"""Прямая выгрузка OSM (Overpass или готовый файл .osm) в те же слои контракта, что и fetch overture.

Источник один: файл `<inputs>/cache/osm/data.osm` с SOURCE.yaml (дата, охват). Его делает `download`: запрос Overpass
по охвату или файл, данный в `--file` (из .osm.pbf Geofabrik: `osmium extract -b <охват> region.osm.pbf -o data.osm`).
Раскладка (`layout`) читает только этот файл через osmnx.
"""

import shutil
from datetime import date as today
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import requests
import shapely
import yaml
from shapely.ops import polygonize, unary_union

from cm_carto.fetch import common
from cm_carto.fetch.common import rules
from cm_carto.project import Project

ELEMENT = {"node": "n", "way": "w", "relation": "r"}


def cache_dir(project: Project) -> Path:
    return project.inputs / "cache" / "osm"


def query(project: Project) -> str:
    west, south, east, north = project.fetch_extent_wgs84
    keys = "".join(f"nwr[{k}];" for k in rules()["osm"]["tag_keys"] + ["natural"])
    return (f"[out:xml][timeout:{rules()['osm']['overpass_timeout_s']}][bbox:{south},{west},{north},{east}];"
            f"({keys});(._;>>;);out meta;")


def download(project: Project, file: Path | None = None, date: str | None = None) -> Path:
    """Кеш выгрузки: файл data.osm и SOURCE.yaml. Готовый кеш сеть не трогает."""
    folder = cache_dir(project)
    marker = folder / "SOURCE.yaml"
    bounds = list(project.fetch_extent_wgs84)
    if marker.exists():
        cached = yaml.safe_load(marker.read_text(encoding="utf-8"))
        if cached["extent_wgs84"] != bounds:
            raise ValueError(f"{marker}: кеш снят на охват {cached['extent_wgs84']}, в project.yaml {bounds}; удалить кеш")
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    if file is not None:
        if date is None:
            raise ValueError("файл .osm дается вместе с датой выгрузки: --date ГГГГ-ММ-ДД")
        shutil.copyfile(file, folder / "data.osm")
        origin = f"файл {file.name}"
    else:
        answer = requests.post(rules()["osm"]["overpass_url"], data={"data": query(project)},
                               timeout=rules()["osm"]["overpass_timeout_s"])
        answer.raise_for_status()
        (folder / "data.osm").write_bytes(answer.content)
        date, origin = today.today().isoformat(), rules()["osm"]["overpass_url"]
    info = {"source": "OpenStreetMap contributors", "origin": origin, "date": date, "extent_wgs84": bounds,
            "license": rules()["osm"]["license"], "file": "data.osm"}
    marker.write_text(yaml.safe_dump(info, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return folder


def tag(row: dict, key: str):
    value = row.get(key)
    return None if value is None or value != value else value


def find(row: dict, mapping: dict):
    """Значение первого правила `тег=значение`, подошедшего записи."""
    for rule, value in mapping.items():
        key, expected = rule.split("=")
        if tag(row, key) == expected:
            return value
    return None


def records(features: gpd.GeoDataFrame):
    """(ключ object_id, теги, геометрия) по элементам OSM."""
    for (element, number), row, geom in zip(features.index, features.drop(columns="geometry").to_dict("records"),
                                            features.geometry):
        yield f"osm-{ELEMENT[element]}{number}", row, geom


def travel(row: dict) -> str | None:
    """Движение по улице OSM относительно направления линии: along, against или None (двустороннее). Значение тега
    oneway — по таблице правил; без тега — улицы, которые OSM считает односторонними сама (implied)."""
    value = tag(row, "oneway")
    if value is not None:
        return rules()["osm"]["oneway"]["values"].get(value)
    if any(tag(row, k) == v for k, v in (rule.split("=") for rule in rules()["osm"]["oneway"]["implied"])):
        return "along"
    return None


def lines(features: gpd.GeoDataFrame, layer: str, column: str, classes: dict, label: str, date: str, area,
          license: str, names: common.Names) -> gpd.GeoDataFrame:
    """Линейные слои roads (highway) и rail (railway): класс по таблице, тоннель по тегам tunnel и covered. У улиц еще
    мост (тег bridge, кроме no) и односторонность (`travel`): линия односторонней улицы пишется по направлению
    движения, поэтому при движении против линии OSM (oneway=-1) она разворачивается."""
    rule = rules()[layer]
    attr = "road_class" if layer == "roads" else "rail_class"
    rows = []
    for key, row, geom in records(features):
        value = classes.get(tag(row, column))
        if value is None and column == "highway" and (tag(row, column) or "").endswith("_link"):
            value = classes.get(tag(row, column)[:-5])
        if value is None or geom.geom_type not in ("LineString", "MultiLineString"):
            continue
        if layer == "rail" and value == "main" and tag(row, "service") in rules()["rail"]["siding_service"]:
            value = "siding"
        direction = travel(row) if layer == "roads" else None
        geom = common.lines_of(shapely.intersection(shapely.reverse(geom) if direction == "against" else geom, area))
        if geom is None:
            continue
        tunnel = tag(row, "tunnel") not in (None, "no") or tag(row, "covered") == "yes"
        item = {"key": key, "name": names.osm(row), "kind": rule["kind"][value], "label": label, "date": date,
                "license": license, "geometry": geom, attr: value, "tunnel": tunnel}
        if layer == "roads":
            item |= {"bridge": tag(row, "bridge") not in (None, "no"), "oneway": direction is not None}
        rows.append(item)
    return common.table(layer, rows, features.crs)


def levels(row: dict):
    value = tag(row, "building:levels")
    return None if value is None else int(float(value))


def sea(features: gpd.GeoDataFrame, area):
    """Море как грани охвата справа от береговой линии OSM (суша слева, вода справа). Линии берутся целиком:
    пересечение с границей охвата находит полигонизация, а не обрезка."""
    coast = features[features["natural"] == "coastline"] if "natural" in features else features.iloc[0:0]
    full = [g.exterior if g.geom_type == "Polygon" else g for g in coast.geometry]
    pieces = [p for line in full for p in shapely.get_parts(shapely.intersection(line, area))
              if p.geom_type == "LineString"]
    if not pieces:
        return None
    faces = list(polygonize(unary_union([*full, area.exterior])))
    water = []
    for piece in pieces:
        (x0, y0), (x1, y1) = piece.coords[0], piece.coords[1]
        length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        point = shapely.Point((x0 + x1) / 2 + (y1 - y0) / length, (y0 + y1) / 2 - (x1 - x0) / length)
        water += [f for f in faces if f.contains(point)]
    return unary_union(water).intersection(area) if water else None


def layout(project: Project) -> dict[str, gpd.GeoDataFrame]:
    """Слои контракта из кеша OSM."""
    folder = cache_dir(project)
    info = yaml.safe_load((folder / "SOURCE.yaml").read_text(encoding="utf-8"))
    date, license = str(info["date"]), common.license_name(info["license"])
    label = rules()["labels"]["osm_direct"].format(date=date)
    area = common.extent(project)
    rule = rules()
    names = common.Names(project.language)
    tags = {k: True for k in rule["osm"]["tag_keys"] + ["natural"]}
    features = ox.features.features_from_xml(folder / "data.osm", tags=tags).to_crs(project.crs)
    features = features[~features.geometry.is_empty]

    frames = {"roads": lines(features, "roads", "highway", rule["roads"]["classes"], label, date, area, license, names),
              "rail": lines(features, "rail", "railway", rule["rail"]["classes"], label, date, area, license, names)}

    stops = []
    for key, row, geom in records(features[features.geometry.geom_type == "Point"]):
        mode = find(row, rule["osm"]["stops"])
        if mode == "rail" and (tag(row, "station") == "subway" or tag(row, "subway") == "yes"):
            mode = "subway"
        if mode == "bus" and tag(row, "trolleybus") == "yes" and tag(row, "bus") != "yes":
            mode = "trolleybus"
        if mode is not None and geom.within(area):
            stops.append({"key": key, "name": names.osm(row), "kind": rule["transit_stops"]["kind"][mode],
                          "label": label, "date": date, "license": license, "geometry": geom, "mode": mode})
    frames["transit_stops"] = common.table("transit_stops", stops, features.crs)

    areal = common.clip_areas(features[features.geometry.geom_type.isin(["Polygon", "MultiPolygon"])], area)
    buildings, green, water = [], [], []
    for key, row, geom in records(areal):
        base = {"key": key, "name": names.osm(row), "label": label, "date": date, "license": license,
                "geometry": geom}
        if tag(row, "building") not in (None, "no"):
            buildings.append({**base, "kind": rule["buildings"]["kind"], "floors": levels(row),
                              "building_use": rule["osm"]["building_use"].get(tag(row, "building"))})
        green_class = find(row, rule["osm"]["green"])
        if green_class is not None:
            green.append({**base, "kind": rule["green"]["kind"][green_class], "green_class": green_class})
        water_class = find(row, rule["osm"]["water"])
        if water_class is not None:
            water.append({**base, "kind": rule["water"]["kind"][water_class], "water_class": water_class})
    frames["buildings_osm"] = common.table("buildings_osm", buildings, features.crs)
    frames["green"] = common.table("green", green, features.crs)
    shore = sea(features, area)
    if shore is not None:
        water.append({"key": "coastline", "name": None, "kind": rule["water"]["kind"]["sea"], "label": label,
                      "date": date, "license": license, "geometry": shore, "water_class": "sea"})
    frames["water"] = common.table("water", water, features.crs)

    pois = {name: [] for name in ["poi_education", "poi_health", "poi_culture", "poi_hotels", "poi_other"]}
    for key, row, geom in records(common.points(features[features.geometry.geom_type.isin(["Point", "Polygon", "MultiPolygon"])], area)):
        found = find(row, rule["poi"]["osm"])
        if found is not None:
            layer, poi_type, kind = found
            pois[layer].append({"key": key, "name": names.osm(row), "kind": kind, "label": label, "date": date,
                                "license": license, "geometry": geom, "poi_type": poi_type, "confirmed_by": []})
    frames |= {layer: common.table(layer, items, features.crs) for layer, items in pois.items()}

    streets = frames["roads"][~frames["roads"]["tunnel"]]
    frames["blocks"] = common.blocks(streets, shore, area, rule["labels"]["osm_direct"].format(date=date), date,
                                     license)
    print(f"названия без названия на языке {project.language}, составные: {names.composite}")
    return frames


def run(project: Project, file: Path | None = None, date: str | None = None) -> Path:
    """cm-carto fetch <project.yaml> osm: выгрузка в кеш (если его нет) и часть fetch-osm.gpkg."""
    folder = download(project, file, date)
    frames = layout(project)
    path = common.write_part(project, "fetch-osm", frames, folder / "SOURCE.yaml", folder / "data.osm")
    for name in sorted(frames):
        print(f"{name}: {len(frames[name])}")
    return path
