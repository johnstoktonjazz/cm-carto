"""Открытые реестры: ОКН (heritage), ДТП (accidents), учреждения bus.gov.ru и гостиницы (для сверки), Росстат (таблица).

Порталы закрыты из облака: выгрузку кладет в кеш человек (`--file`, `--date`), дальше все работает от кеша. Имена
столбцов выгрузок — в rules/registries.yaml.
"""

import json
import shutil
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
import yaml

from cm_carto.fetch import common
from cm_carto.project import Project

RULES = Path(__file__).resolve().parents[2] / "rules" / "registries.yaml"


def rules() -> dict:
    return yaml.safe_load(RULES.read_text(encoding="utf-8"))


def cache_dir(project: Project, registry: str) -> Path:
    return project.inputs / "cache" / "registries" / registry


def download(project: Project, registry: str, file: Path | None = None, date: str | None = None) -> Path:
    """Выгрузка реестра в кеш: файл data.csv и SOURCE.yaml. Готовый кеш не трогается."""
    folder = cache_dir(project, registry)
    marker = folder / "SOURCE.yaml"
    if marker.exists():
        return folder
    if file is None or date is None:
        raise ValueError(f"{registry}: кеша нет; выгрузка реестра дается файлом и датой: --file F.csv --date ГГГГ-ММ-ДД")
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(file, folder / "data.csv")
    info = {"source": rules()[registry]["title"], "date": date, "file": "data.csv",
            "license": rules()[registry]["license"], "original_name": file.name}
    marker.write_text(yaml.safe_dump(info, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return folder


def info(project: Project, registry: str) -> dict:
    return yaml.safe_load((cache_dir(project, registry) / "SOURCE.yaml").read_text(encoding="utf-8"))


def read(project: Project, registry: str) -> pd.DataFrame:
    """Таблица реестра со столбцами под каноническими именами из rules (columns); все значения — строки."""
    rule = rules()[registry]
    raw = pd.read_csv(cache_dir(project, registry) / "data.csv", sep=rule["delimiter"], dtype=str,
                      encoding="utf-8-sig", keep_default_na=False)
    missing = [c for c in rule["columns"].values() if c not in raw.columns]
    if missing:
        raise ValueError(f"{registry}: в выгрузке нет столбцов {missing}; поправить rules/registries.yaml")
    return raw.rename(columns={v: k for k, v in rule["columns"].items()})[list(rule["columns"])]


def locate(frame: pd.DataFrame, project: Project, area) -> list:
    """Геометрия записей в системе координат проекта по lon/lat или GeoJSON-столбцу geo; нет координат — None."""
    if "geo" in frame:
        coordinates = [json.loads(v)["coordinates"] if v else None for v in frame["geo"]]
    else:
        coordinates = [[float(lon), float(lat)] if lon and lat else None for lon, lat in zip(frame["lon"], frame["lat"])]
    points = gpd.GeoSeries([shapely.Point(c) if c else shapely.Point() for c in coordinates], crs="EPSG:4326")
    projected = points.to_crs(project.crs)
    return [None if c is None or not g.within(area) else g for c, g in zip(coordinates, projected)]


def stamp(project: Project, registry: str) -> tuple[str, str, str]:
    """Подпись, дата и лицензия записей реестра."""
    meta = info(project, registry)
    return rules()[registry]["label"].format(date=meta["date"]), str(meta["date"]), meta["license"]


def heritage(project: Project) -> gpd.GeoDataFrame:
    """ОКН в охвате: слой heritage. Записи без координат в охват не попадают."""
    rule = rules()["okn"]
    label, date, license = stamp(project, "okn")
    frame = read(project, "okn")
    area = common.extent(project)
    rows = []
    for record, geom in zip(frame.to_dict("records"), locate(frame, project, area)):
        if geom is None:
            continue
        rows.append({"key": rule["key_prefix"] + record["registry_number"], "name": record["name"],
                     "kind": f"объект культурного наследия: {record['kind'].lower()}", "label": label, "date": date,
                     "license": license, "geometry": geom, "registry_number": record["registry_number"],
                     "category": rule["category"][record["category"]]})
    return common.table("heritage", rows, project.crs)


def severity(dead: str, injured: str) -> str:
    if int(dead or 0) > 0:
        return "fatal"
    return "injury" if int(injured or 0) > 0 else "damage"


def accidents(project: Project) -> gpd.GeoDataFrame:
    """ДТП в охвате: слой accidents — дата, тяжесть, место; участников нет."""
    rule = rules()["dtp"]
    label, date, license = stamp(project, "dtp")
    frame = read(project, "dtp")
    area = common.extent(project)
    rows = []
    for record, geom in zip(frame.to_dict("records"), locate(frame, project, area)):
        if geom is None:
            continue
        day = pd.to_datetime(record["date"], format=rule["date_format"]).date().isoformat()
        rows.append({"key": rule["key_prefix"] + record["id"], "kind": "дорожно-транспортное происшествие",
                     "label": label, "date": date, "license": license, "geometry": geom, "accident_date": day,
                     "severity": severity(record["dead"], record["injured"])})
    frame = common.table("accidents", rows, project.crs)
    return frame.rename(columns={"accident_date": "date"}) if rows else frame


def institutions(project: Project, registry: str) -> list[dict]:
    """Учреждения реестра для сверки: слой, poi_type, kind, ключ, название, вместимость, геометрия или None.
    Записи вне групп учреждений (типы не из rules) в сверку не идут."""
    rule = rules()[registry]
    frame = read(project, registry)
    label, date, license = stamp(project, registry)
    area = common.extent(project)
    out = []
    for record, geom in zip(frame.to_dict("records"), locate(frame, project, area)):
        if registry == "hotels":
            layer, poi_type, kind = rule["layer"]
        else:
            text = f"{record['type']} {record['name']}".lower()
            found = [v for k, v in rule["types"].items() if k in text]
            if not found:
                continue
            layer, poi_type, kind = found[0]
        out.append({"key": rule["key_prefix"] + record["id"], "name": record["name"], "layer": layer,
                    "poi_type": poi_type, "kind": kind, "address": record["address"],
                    "capacity": int(record["capacity"]) if record["capacity"] else None, "geometry": geom,
                    "label": label, "date": date, "license": license,
                    "inside": geom is not None or not (record["lon"] and record["lat"])})
    return [r for r in out if r["inside"]]


def covered(registry: str) -> set[tuple[str, str]]:
    """Группы (слой, poi_type), которые реестр покрывает: учреждения OSM этих групп без пары в реестре не подтверждены."""
    rule = rules()[registry]
    if registry == "hotels":
        return {tuple(rule["layer"][:2])}
    return {tuple(v[:2]) for v in rule["types"].values()}


def rosstat(project: Project) -> pd.DataFrame:
    """Показатели Росстата: показатель, единица, год, значение, ОКТМО; значение — число."""
    frame = read(project, "rosstat")
    frame["value"] = pd.to_numeric(frame["value"].str.replace(",", ".").str.replace(r"\s", "", regex=True))
    frame["year"] = frame["year"].astype(int)
    return frame
