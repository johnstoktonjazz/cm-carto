"""Общее для источников fetch: правила, охват, ключи объектов, таблицы слоев, кварталы, запись части геопакета."""

import hashlib
import re
from functools import cache
from pathlib import Path

import geopandas as gpd
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


class Names:
    """Названия на языке проекта: Overture `names.common[язык]`, OSM `name:<язык>`; иначе основное название.
    Составные «А / Б» без названия на языке проекта остаются как есть и считаются в `composite`."""

    def __init__(self, language: str):
        self.language = language
        self.composite = 0

    def pick(self, local: str | None, primary: str | None) -> str | None:
        if local:
            return local
        if primary and any(sep in primary for sep in rules()["names"]["composite_separators"]):
            self.composite += 1
        return primary

    def overture(self, names) -> str | None:
        if names is None:
            return None
        return self.pick(dict(names["common"] or []).get(self.language), names["primary"])

    def osm(self, row: dict) -> str | None:
        local, primary = row.get(f"name:{self.language}"), row.get("name")
        return self.pick(local if local == local else None, primary if primary == primary else None)


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


def clip_areas(frame: gpd.GeoDataFrame, area) -> gpd.GeoDataFrame:
    """Площадные объекты по охвату: целиком внутри остаются как есть, пересекающие границу обрезаются."""
    frame = frame.copy()
    frame["geometry"] = frame.geometry.make_valid()
    frame = frame[frame.geometry.intersects(area)].copy()
    inside = frame.geometry.within(area)
    frame["geometry"] = [g if i else shapely.intersection(g, area) for g, i in zip(frame.geometry, inside)]
    frame["geometry"] = [shapely.MultiPolygon([p for p in shapely.get_parts(g) if p.geom_type == "Polygon" and p.area > 0.01])
                         for g in frame.geometry]
    return frame[~frame.geometry.is_empty]


def points(frame: gpd.GeoDataFrame, area) -> gpd.GeoDataFrame:
    """Точки внутри охвата; площадной объект заменяется точкой на его поверхности."""
    frame = frame.copy()
    frame["geometry"] = [g if g.geom_type == "Point" else g.representative_point() for g in frame.geometry]
    return frame[frame.geometry.within(area)]


def disjoint(frame: gpd.GeoDataFrame, column: str, value: str) -> gpd.GeoDataFrame:
    """Объекты со значением column == value не перекрывают друг друга: от более крупного отнимаются более мелкие
    (море, залив и лиман в источнике лежат друг на друге)."""
    frame = frame.copy()
    taken = None
    for index in frame[frame[column] == value].geometry.area.sort_values().index:
        geom = frame.geometry[index]
        frame.loc[index, "geometry"] = geom if taken is None else geom.difference(taken)
        taken = geom if taken is None else taken.union(geom)
    return frame[~frame.geometry.is_empty]


def lines_of(geom):
    """Линейные части результата обрезки в одну MultiLineString (точки и пустое отбрасываются)."""
    parts = [g for g in shapely.get_parts(geom) if g.geom_type == "LineString" and g.length > 0.01]
    return shapely.MultiLineString(parts) if parts else None


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
