"""Общее для источников fetch: правила, охват, ключи объектов, таблицы слоев, кварталы, запись части геопакета."""

import hashlib
import re
from functools import cache
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
import yaml
from shapely.ops import unary_union

from cm_carto import gpkg
from cm_carto.project import Project

RULES = Path(__file__).resolve().parents[2] / "rules" / "fetch.yaml"
OSM_ID = re.compile(r"^([nwr])(\d+)(@\d+)?$")


@cache
def rules() -> dict:
    return yaml.safe_load(RULES.read_text(encoding="utf-8"))


def extent(project: Project) -> shapely.Polygon:
    """Охват из project.yaml в системе координат проекта; стороны разбиты, чтобы дуги параллелей не спрямлялись."""
    box = gpd.GeoSeries([shapely.box(*project.extent_wgs84)], crs="EPSG:4326").segmentize(0.001)
    return box.to_crs(project.crs).iloc[0]


def license_name(raw: str) -> str:
    return rules()["licenses"].get(raw, raw)


def osm_key(record_id: str) -> str:
    """Идентификатор записи OSM (w123@4, n7, r9) -> ключ object_id (osm-w123)."""
    found = OSM_ID.match(record_id)
    if found is None:
        raise ValueError(f"не идентификатор OSM: {record_id}")
    return f"osm-{found[1]}{found[2]}"


def geometry_key(geom) -> str:
    """Ключ по геометрии (контракт, object_id): g и 12 знаков SHA-256 геометрии с округлением до 1 см."""
    return "g" + hashlib.sha256(shapely.to_wkb(shapely.set_precision(geom, 0.01))).hexdigest()[:12]


def table(layer: str, rows: list[dict], crs) -> gpd.GeoDataFrame:
    """Слой из записей: key, name, kind, label, date, license, geometry и атрибуты слоя. Статус всех — reference."""
    columns = ["object_id", "name", "kind", "status", "source_label", "source_date", "source_license", "fact_ids"]
    attrs = sorted({k for r in rows for k in r} - {"key", "name", "kind", "label", "date", "license", "geometry"})
    records = []
    for r in rows:
        records.append({"object_id": f"{layer}:{r['key']}", "name": r.get("name") or None, "kind": r["kind"],
                        "status": "reference", "source_label": r["label"], "source_date": r["date"],
                        "source_license": r["license"], "fact_ids": [], **{a: r.get(a) for a in attrs},
                        "geometry": r["geometry"]})
    frame = gpd.GeoDataFrame(records, columns=columns + attrs + ["geometry"], geometry="geometry", crs=crs)
    for a in attrs:      # целые атрибуты с пропусками остаются целыми, а не вещественными
        values = [r.get(a) for r in rows]
        if any(v is not None for v in values) and all(v is None or (isinstance(v, int) and not isinstance(v, bool)) for v in values):
            frame[a] = pd.Series(values, index=frame.index, dtype=object)
    return frame.sort_values("object_id", kind="stable").reset_index(drop=True)


def blocks(streets: gpd.GeoDataFrame, sea, area, label: str, date: str, license: str) -> gpd.GeoDataFrame:
    """Кварталы по улично-дорожной сети: суша охвата (охват без моря) минус полосы улиц по классу. Связные части
    суши между полосами и есть ячейки полигонизации осей улиц с вырезанными улицами. Обрезанные охватом, мелкие
    и слишком крупные части отбрасываются."""
    rule = rules()["blocks"]
    streets = streets[streets["road_class"].isin(rule["half_width_m"])]
    land = area.difference(sea) if sea is not None else area
    strip = unary_union([g.buffer(rule["half_width_m"][c], cap_style="flat")
                         for c, g in zip(streets["road_class"], streets.geometry)])
    edge = area.exterior.buffer(rule["edge_tolerance_m"])
    rows = []
    for part in shapely.get_parts(land.difference(strip)):
        if part.geom_type != "Polygon" or not rule["min_area_m2"] <= part.area <= rule["max_area_ha"] * 1e4:
            continue
        if part.intersects(edge):
            continue
        rows.append({"key": geometry_key(part), "kind": rule["kind"], "label": label, "date": date,
                     "license": license, "geometry": part})
    return table("blocks", rows, streets.crs)


def write_part(project: Project, name: str, frames: dict[str, gpd.GeoDataFrame], *inputs) -> Path:
    """Часть геопакета fetch-<имя>.gpkg: контракт проверяет gpkg.write; хэш входов — файлы источника и правила."""
    return gpkg.write(project.part(name), frames, project.name, project.crs, project.crs_name,
                      gpkg.input_hash(*inputs, RULES))
