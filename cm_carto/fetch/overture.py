"""Выгрузка Overture Maps по охвату проекта: кеш сырых выборок (GeoParquet) и раскладка в слои контракта.

Сеть нужна один раз: пока в кеше `<inputs>/cache/overture-<выпуск>/` лежит SOURCE.yaml, повторный запуск ничего
не качает. Раскладка (`layout`) читает только кеш и детерминирована.
"""

import json
import os
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as fs
import pyarrow.parquet as pq
import shapely
import yaml
from shapely.ops import substring

from cm_carto.fetch import common
from cm_carto.fetch.common import rules
from cm_carto.project import Project

BUCKET = "overturemaps-us-west-2/release/"
GEO_METADATA = {"version": "1.0.0", "primary_column": "geometry",
                "columns": {"geometry": {"encoding": "WKB", "geometry_types": []}}}


def release_of(project: Project, release: str | None) -> str:
    """Выпуск Overture: аргумент команды или `fetch.overture_release` в project.yaml."""
    if release is not None:
        return release
    return project.data["fetch"]["overture_release"]


def cache_dir(project: Project, release: str) -> Path:
    return project.root / project.data["inputs"] / "cache" / f"overture-{release}"


def release_date(release: str) -> str:
    """2026-09-23.0 -> 2026-09-23."""
    return release.split(".")[0]


def _s3() -> fs.S3FileSystem:
    return fs.S3FileSystem(anonymous=True, region="us-west-2", proxy_options=os.environ.get("HTTPS_PROXY"),
                           tls_ca_file_path=os.environ.get("SSL_CERT_FILE"))


def _select(release: str, source: str, columns: list[str], bounds) -> pa.Table:
    theme, type_ = source.split("/")
    xmin, ymin, xmax, ymax = bounds
    where = ((pc.field("bbox", "xmin") < xmax) & (pc.field("bbox", "xmax") > xmin)
             & (pc.field("bbox", "ymin") < ymax) & (pc.field("bbox", "ymax") > ymin))
    dataset = ds.dataset(f"{BUCKET}{release}/theme={theme}/type={type_}", filesystem=_s3(), format="parquet")
    return dataset.to_table(filter=where, columns=[*columns, "geometry"])


def download(project: Project, release: str) -> Path:
    """Сырые выборки по охвату в кеш; при готовом кеше сеть не трогается."""
    folder = cache_dir(project, release)
    marker = folder / "SOURCE.yaml"
    bounds = list(project.extent_wgs84)
    if marker.exists():
        cached = yaml.safe_load(marker.read_text(encoding="utf-8"))
        if cached["extent_wgs84"] != bounds:
            raise ValueError(f"{marker}: кеш снят на охват {cached['extent_wgs84']}, в project.yaml {bounds}; удалить кеш")
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    spec = rules()["overture"]["datasets"]
    for source, item in spec.items():
        target = folder / (source.replace("/", "-") + ".parquet")
        if target.exists():
            continue
        table = _select(release, source, item["columns"], bounds)
        table = table.replace_schema_metadata({"geo": json.dumps(GEO_METADATA)})
        pq.write_table(table, target, compression="zstd")
        print(f"overture {release}: {source}: {table.num_rows} записей")
    info = {"source": "Overture Maps Foundation", "release": release, "downloaded": date.today().isoformat(),
            "extent_wgs84": bounds,
            "licenses": {s: i["license"] for s, i in spec.items()},
            "datasets": {s: (folder / (s.replace("/", "-") + ".parquet")).name for s in spec}}
    marker.write_text(yaml.safe_dump(info, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return folder


def read(folder: Path, source: str, project: Project) -> gpd.GeoDataFrame:
    """Сырая выборка из кеша в системе координат проекта (без обрезки по охвату); столбец class называется class_."""
    frame = pq.read_table(folder / (source.replace("/", "-") + ".parquet")).to_pandas()
    geometry = gpd.GeoSeries.from_wkb(frame.pop("geometry"), crs="EPSG:4326").to_crs(project.crs)
    return gpd.GeoDataFrame(frame.rename(columns={"class": "class_"}), geometry=geometry)


def primary_name(names) -> str | None:
    return names["primary"] if names is not None else None


def source_key(source, gers_id: str) -> str:
    """Ключ object_id: номер элемента OSM из record_id, иначе идентификатор GERS."""
    if source["dataset"] == "OpenStreetMap" and source["record_id"] is not None:
        return common.osm_key(source["record_id"])
    return f"ovt-{gers_id}"


def origin(sources, gers_id: str, release: str) -> tuple[str, str, str]:
    """Ключ object_id, подпись и лицензия записи по первому источнику."""
    first = sources[0]
    label = "osm_overture" if first["dataset"] == "OpenStreetMap" else "other_overture"
    return (source_key(first, gers_id), rules()["labels"][label].format(dataset=first["dataset"], release=release),
            common.license_name(first["license"]))


def covers(between, at: float) -> bool:
    return between is None or between[0] <= at <= between[1]


def pieces(segment, flags, sources):
    """Участки сегмента по границам источников (`between`) и флагов: (источник, под землей, линия).
    Заброшенные и строящиеся участки пропускаются."""
    flags = flags if flags is not None else []
    cuts = {0.0, 1.0}
    for item in [*sources, *flags]:
        if item["between"] is not None:
            cuts.update(float(x) for x in item["between"])
    cuts = sorted(cuts)
    for start, end in zip(cuts, cuts[1:]):
        middle = (start + end) / 2
        values = {v for f in flags if covers(f["between"], middle) for v in f["values"]}
        if values & set(rules()["flags"]["skip"]):
            continue
        owner = [s for s in sources if covers(s["between"], middle)]
        if not owner:
            raise ValueError(f"сегмент {segment.id}: участок {start:.4f}-{end:.4f} без источника")
        line = substring(segment.geometry, start, end, normalized=True)
        if line.geom_type == "LineString" and line.length > 0.01:
            yield owner[0], bool(values & set(rules()["flags"]["tunnel"])), line


def lines(segments: gpd.GeoDataFrame, subtype: str, layer: str, attr: str, release: str, area, date: str):
    """Слои roads и rail: сегменты Overture режутся по исходным элементам OSM и тоннелям, участки одного элемента
    собираются обратно в одну запись `слой:osm-w123` (тоннельный участок — с суффиксом -t)."""
    rule = rules()[layer]
    flags_column = "road_flags" if layer == "roads" else "rail_flags"
    groups = {}
    for segment in segments[segments["subtype"] == subtype].itertuples():
        if segment.class_ not in rule["classes"]:
            raise ValueError(f"{layer}: класс Overture {segment.class_} не описан в rules/fetch.yaml")
        value = rule["classes"][segment.class_]
        for source, tunnel, line in pieces(segment, getattr(segment, flags_column), segment.sources):
            key = source_key(source, segment.id)
            group = groups.setdefault((key, tunnel), {"value": value, "lines": [], "name": primary_name(segment.names),
                                                       "license": common.license_name(source["license"]),
                                                       "label": origin([source], segment.id, release)[1]})
            if group["value"] != value:
                raise ValueError(f"{layer}: у {key} два класса — {group['value']} и {value}")
            group["lines"].append(line)
    rows = []
    for (key, tunnel), group in groups.items():
        geom = common.lines_of(shapely.intersection(shapely.line_merge(shapely.MultiLineString(group["lines"])), area))
        if geom is None:
            continue
        rows.append({"key": key + ("-t" if tunnel else ""), "name": group["name"], "kind": rule["kind"][group["value"]],
                     "label": group["label"], "date": date, "license": group["license"], "geometry": geom,
                     attr: group["value"], "tunnel": tunnel})
    return common.table(layer, rows, segments.crs)


def polygons(frame: gpd.GeoDataFrame, layer: str, release: str, area, date: str, attrs) -> gpd.GeoDataFrame:
    """Слой площадных объектов: ключ и подпись — из источника записи, attrs(record) — kind и атрибуты слоя."""
    rows = []
    for record in common.clip_areas(frame, area).itertuples():
        key, label, license = origin(record.sources, record.id, release)
        rows.append({"key": key, "name": primary_name(record.names), "label": label, "date": date, "license": license,
                     "geometry": record.geometry, **attrs(record)})
    return common.table(layer, rows, frame.crs)


def pick(frame: gpd.GeoDataFrame, mapping: dict, geometry: list[str]) -> gpd.GeoDataFrame:
    """Записи с классом из mapping и геометрией нужного вида."""
    return frame[frame["class_"].isin(mapping) & frame.geometry.geom_type.isin(geometry)]


def stops(infrastructure: gpd.GeoDataFrame, release: str, area, date: str) -> gpd.GeoDataFrame:
    rule = rules()["transit_stops"]
    found = common.points(infrastructure[(infrastructure["subtype"] == "transit")
                                  & infrastructure["class_"].isin(rule["classes"])], area)
    rows = []
    for record in found.itertuples():
        key, label, license = origin(record.sources, record.id, release)
        mode = rule["classes"][record.class_]
        rows.append({"key": key, "name": primary_name(record.names), "kind": rule["kind"][mode], "label": label,
                     "date": date, "license": license, "geometry": record.geometry, "mode": mode})
    return common.table("transit_stops", rows, found.crs)


def poi(places: gpd.GeoDataFrame, land_use: gpd.GeoDataFrame, release: str, area, date: str) -> dict:
    """Учреждения по группам из places (taxonomy.primary) и land_use (класс). Точка places внутри площадного объекта
    land_use той же группы — то же учреждение, остается объект land_use (в нем название и номер элемента OSM)."""
    rule = rules()["poi"]
    layers = {}
    found = common.points(pick(land_use, rule["land_use"], ["Polygon", "MultiPolygon"]), area)
    rows = {name: [] for name in ["poi_education", "poi_health", "poi_culture", "poi_hotels", "poi_other"]}
    areas = {name: [] for name in rows}
    for record in found.itertuples():
        layer, poi_type, kind = rule["land_use"][record.class_]
        key, label, license = origin(record.sources, record.id, release)
        rows[layer].append({"key": key, "name": primary_name(record.names), "kind": kind, "label": label, "date": date,
                            "license": license, "geometry": record.geometry, "poi_type": poi_type, "confirmed_by": []})
        areas[layer].append(land_use.geometry[record.Index])
    minimum = rules()["overture"]["places_min_confidence"]
    places = places[(places["operating_status"] != "closed") & (places["confidence"] >= minimum)]
    primary = places["taxonomy"].map(lambda t: t["primary"] if t is not None else None)
    places = common.points(places[primary.isin(rule["places"])], area)
    label = rules()["labels"]["places"].format(release=release)
    for record in places.itertuples():
        layer, poi_type, kind = rule["places"][record.taxonomy["primary"]]
        if any(a.contains(record.geometry) for a in areas[layer]):
            continue
        rows[layer].append({"key": f"ovt-{record.id}", "name": primary_name(record.names), "kind": kind, "label": label,
                            "date": date, "license": rules()["overture"]["places_license"],
                            "geometry": record.geometry, "poi_type": poi_type, "confirmed_by": []})
    return {layer: common.table(layer, items, places.crs) for layer, items in rows.items()}


def layout(project: Project, release: str) -> dict[str, gpd.GeoDataFrame]:
    """Слои контракта из кеша Overture: тот же кеш и те же правила дают те же слои."""
    folder = cache_dir(project, release)
    area = common.extent(project)
    date = release_date(release)
    rule = rules()

    segments = read(folder, "transportation/segment", project)
    frames = {"roads": lines(segments, "road", "roads", "road_class", release, area, date),
              "rail": lines(segments, "rail", "rail", "rail_class", release, area, date)}
    frames["transit_stops"] = stops(read(folder, "base/infrastructure", project), release, area, date)
    frames["buildings_osm"] = polygons(
        read(folder, "buildings/building", project), "buildings_osm", release, area, date,
        lambda r: {"kind": rule["buildings"]["kind"], "building_use": r.subtype,
                   "floors": None if r.num_floors != r.num_floors else int(r.num_floors)})

    green = rule["green"]["classes"]
    land_use, land = read(folder, "base/land_use", project), read(folder, "base/land", project)
    frames["green"] = polygons(
        pd.concat([pick(land_use, green, ["Polygon", "MultiPolygon"]), pick(land, green, ["Polygon", "MultiPolygon"])]),
        "green", release, area, date,
        lambda r: {"green_class": green[r.class_], "kind": rule["green"]["kind"][green[r.class_]]})
    water = rule["water"]["classes"]
    frames["water"] = common.disjoint(polygons(
        pick(read(folder, "base/water", project), water, ["Polygon", "MultiPolygon"]), "water", release, area, date,
        lambda r: {"water_class": water[r.class_], "kind": rule["water"]["kind"][water[r.class_]]}), "water_class", "sea")

    frames |= poi(read(folder, "places/place", project), land_use, release, area, date)

    sea = shapely.union_all(frames["water"].loc[frames["water"]["water_class"] == "sea", "geometry"].values)
    streets = frames["roads"][~frames["roads"]["tunnel"]]
    frames["blocks"] = common.blocks(streets, sea, area, rule["labels"]["blocks_overture"].format(release=release),
                                     date, streets["source_license"].iloc[0])
    return frames


def run(project: Project, release: str | None = None) -> Path:
    """cm-carto fetch <project.yaml> overture: выгрузка в кеш (если его нет) и часть fetch-overture.gpkg."""
    release = release_of(project, release)
    folder = download(project, release)
    frames = layout(project, release)
    path = common.write_part(project, "fetch-overture", frames, folder / "SOURCE.yaml")
    for name in sorted(frames):
        print(f"{name}: {len(frames[name])}")
    return path
