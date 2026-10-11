"""Геопакет по контракту geopackage: запись, чтение, проверка, слияние частей.

Слой — GeoDataFrame со столбцами общих полей (FIELDS) и атрибутов слоя из схемы контракта; списки (fact_ids,
confirmed_by) — списками Python, в файле они JSON-текстом. Запись проверяет данные по схеме один раз; дальше
код доверяет файлу. Файл воспроизводим: объекты по object_id, даты в gpkg_contents постоянные, времени сборки нет.
"""

import hashlib
import json
import sqlite3
from functools import cache
from pathlib import Path

import geopandas as gpd
import jsonschema
import pandas as pd
import pyogrio
from pyproj import CRS
from shapely.geometry import MultiLineString, MultiPolygon

from cm_carto import paths

CONTRACT = "geopackage/1.1.0"
FIELDS = ["object_id", "name", "kind", "status", "source_doc", "source_label", "source_date", "source_license",
          "fact_ids", "digitized_from"]
LISTS = {"fact_ids", "confirmed_by"}
# Дата изменения в gpkg_contents: постоянная, иначе файл различается при каждой сборке (контракт, «Хранение»).
FIXED_DATE = "2000-01-01T00:00:00.000Z"


@cache
def schema() -> dict:
    return json.loads(paths.schema("geopackage").read_text(encoding="utf-8"))


@cache
def layers() -> dict[str, tuple[str, dict]]:
    """Слой → (геометрия, атрибуты со схемами) из allOf контракта."""
    items = schema()["properties"]["layers"]["items"]
    out = {}
    for rule in items["allOf"]:
        name = rule["if"]["properties"]["name"]["const"]
        then = rule["then"]["properties"]
        attrs = then["features"]["items"]["properties"]["attrs"]["properties"]
        out[name] = (then["geometry"]["const"], attrs)
    return out


def _multi(geom, kind: str):
    if kind == "MultiPolygon" and geom.geom_type == "Polygon":
        return MultiPolygon([geom])
    if kind == "MultiLineString" and geom.geom_type == "LineString":
        return MultiLineString([geom])
    if geom.geom_type != kind:
        raise ValueError(f"геометрия {geom.geom_type}, слой требует {kind}")
    return geom


def _feature(row: dict, attrs: dict) -> dict:
    feature = {k: row[k] for k in FIELDS if k in row and _present(row[k])}
    values = {k: row[k] for k in attrs if k in row and _present(row[k])}
    if values:
        feature["attrs"] = {k: (v.item() if hasattr(v, "item") else v) for k, v in values.items()}
    return feature


def _present(value) -> bool:
    if isinstance(value, list):
        return True
    return not pd.isna(value)


def dump(frames: dict[str, gpd.GeoDataFrame], project_name: str, crs_name: str, input_hash: str) -> dict:
    """JSON-выгрузка по контракту (без геометрии): ее проверяет schema.json."""
    out = {"contract": CONTRACT, "crs": crs_name, "project": project_name, "input_hash": input_hash, "layers": []}
    for name in sorted(frames):
        kind, attrs = layers()[name]
        rows = frames[name].drop(columns="geometry").to_dict("records")
        out["layers"].append({"name": name, "geometry": kind, "features": [_feature(r, attrs) for r in rows]})
    return out


def validate(document: dict) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema())
    errors = []
    for error in validator.iter_errors(document):
        where = "/".join(str(p) for p in error.absolute_path)
        if len(error.absolute_path) >= 4:
            layer = document["layers"][error.absolute_path[1]]
            feature = layer["features"][error.absolute_path[3]]
            where = f"{layer['name']}: {feature.get('object_id', '?')}"
        errors.append(f"{where}: {error.message}")
    ids = [f["object_id"] for layer in document["layers"] for f in layer["features"]]
    errors += [f"object_id повторяется: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    errors += [f"{layer['name']}: object_id {f['object_id']} не из этого слоя" for layer in document["layers"]
               for f in layer["features"] if not f["object_id"].startswith(layer["name"] + ":")]
    return errors


def input_hash(*parts) -> str:
    """Хэш входов сборки: байты файлов (Path) и параметры (все остальное через json)."""
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, Path):
            digest.update(part.read_bytes())
        else:
            digest.update(json.dumps(part, ensure_ascii=False, sort_keys=True, default=str).encode())
    return "sha256:" + digest.hexdigest()


def write(path: Path, frames: dict[str, gpd.GeoDataFrame], project_name: str, crs: CRS, crs_name: str,
          input_hash: str) -> Path:
    """Пишет геопакет; падает со списком нарушений контракта до записи."""
    prepared = {}
    for name, frame in frames.items():
        if name not in layers():
            raise ValueError(f"слоя {name} нет в контракте {CONTRACT}")
        if frame.crs is None or CRS(frame.crs) != crs:
            raise ValueError(f"{name}: система координат {frame.crs}, проект — {crs_name}")
        kind, attrs = layers()[name]
        frame = frame.copy()
        frame["geometry"] = frame.geometry.apply(lambda g: _multi(g, kind))
        # Целые атрибуты с пропусками приходят вещественными (5.0): в файл — целыми по типу схемы.
        # Дробное значение в целом поле — ошибка приведения, а не тихое округление.
        for column, spec in attrs.items():
            if spec.get("type") == "integer" and column in frame:
                frame[column] = frame[column].astype("Int64")
        prepared[name] = frame.sort_values("object_id", kind="stable").reset_index(drop=True)
    errors = validate(dump(prepared, project_name, crs_name, input_hash))
    if errors:
        raise ValueError(f"{path}: геопакет не проходит контракт {CONTRACT}:\n  " + "\n  ".join(errors[:50]))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    pyogrio.set_gdal_config_options({"OGR_CURRENT_DATE": FIXED_DATE})
    for name in sorted(prepared):
        frame = prepared[name]
        kind, attrs = layers()[name]
        columns = [c for c in FIELDS + list(attrs) if c in frame.columns]
        frame = frame[columns + ["geometry"]].copy()
        for column in LISTS & set(columns):
            frame[column] = frame[column].apply(lambda v: json.dumps(list(v), ensure_ascii=False))
        frame = frame.rename_geometry("geom")
        pyogrio.write_dataframe(frame, path, layer=name, driver="GPKG", geometry_type=kind,
                                layer_options={"FID": "fid", "SPATIAL_INDEX": "YES"})
    meta = pd.DataFrame({"key": ["contract", "project", "crs", "input_hash"],
                         "value": [CONTRACT, project_name, crs_name, input_hash]})
    pyogrio.write_dataframe(meta, path, layer="cm_meta", driver="GPKG")
    with sqlite3.connect(path) as db:
        for name in prepared:
            db.execute(f'CREATE UNIQUE INDEX "{name}_object_id" ON "{name}" (object_id)')
        db.execute("UPDATE gpkg_contents SET last_change = ?", (FIXED_DATE,))
    with sqlite3.connect(path) as db:
        db.execute("VACUUM")
    return path


def meta(path: Path) -> dict[str, str]:
    frame = pyogrio.read_dataframe(path, layer="cm_meta")
    return dict(zip(frame["key"], frame["value"]))


def read(path: Path) -> dict[str, gpd.GeoDataFrame]:
    frames = {}
    for name, _ in pyogrio.list_layers(path):
        if name == "cm_meta":
            continue
        frame = pyogrio.read_dataframe(path, layer=name)
        for column in LISTS & set(frame.columns):
            frame[column] = frame[column].apply(json.loads)
        frames[name] = frame
    return frames


def check(path: Path) -> list[str]:
    """Проверка готового файла: контракт, одна система координат, метаданные."""
    info = meta(path)
    if info.get("contract") != CONTRACT:
        return [f"{path}: контракт {info.get('contract')}, нужен {CONTRACT}"]
    frames = read(path)
    crs = {CRS(f.crs).to_wkt() for f in frames.values()}
    errors = [f"{path}: систем координат {len(crs)}, нужна одна"] if len(crs) > 1 else []
    return errors + validate(dump(frames, info["project"], info["crs"], info["input_hash"]))


def merge(parts: list[Path], out: Path, project_name: str, crs: CRS, crs_name: str) -> Path:
    """Сборка геопакета проекта из частей: слои одного имени складываются, повтор object_id — ошибка."""
    collected: dict[str, list[gpd.GeoDataFrame]] = {}
    for part in sorted(parts):
        for name, frame in read(part).items():
            collected.setdefault(name, []).append(frame)
    frames = {name: gpd.GeoDataFrame(pd.concat(items, ignore_index=True), crs=items[0].crs)
              for name, items in collected.items()}
    return write(out, frames, project_name, crs, crs_name, input_hash(*sorted(parts)))
