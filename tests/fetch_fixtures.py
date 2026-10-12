"""Синтетические данные для тестов fetch: проект с охватом в долях градуса и маленький .osm."""

from pathlib import Path

import yaml

WEST, SOUTH, EAST, NORTH = 38.000, 47.000, 38.010, 47.009
EXTENT = [423900, 5205600, 424800, 5206700]   # те же места в UTM 37N, метры


def project_file(folder: Path, **extra) -> Path:
    carto = {"extent": EXTENT, "extent_wgs84": [WEST, SOUTH, EAST, NORTH], "geo": "geo", "inputs": "input", "language": "ru",
             **extra}
    data = {"name": "синтетический проект", "crs": {"name": "EPSG:32637", "definition": "EPSG:32637"}, "carto": carto}
    path = folder / "project.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def osm_xml() -> str:
    """Сетка улиц 2×2 вокруг центрального квартала, ж/д, остановка, школа, здание, парк, пруд, береговая линия
    (вода севернее линии: суша слева при движении на запад)."""
    nodes, ways = [], []
    counter = iter(range(1, 10000))

    def node(lon, lat, tags=None):
        i = next(counter)
        inner = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in (tags or {}).items())
        nodes.append(f'<node id="{i}" version="1" lat="{lat}" lon="{lon}">{inner}</node>')
        return i

    def way(points, tags, closed=False):
        i = next(counter)
        refs = [node(lon, lat) for lon, lat in points]
        if closed:
            refs.append(refs[0])
        inner = "".join(f'<nd ref="{r}"/>' for r in refs) + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
        ways.append(f'<way id="{i}" version="1">{inner}</way>')
        return i

    w, e, s = WEST, EAST, SOUTH
    way([(w, 47.002), (e, 47.002)], {"highway": "secondary", "name": "Первая улица"})
    way([(w, 47.006), (e, 47.006)], {"highway": "residential"})
    way([(38.002, s), (38.002, 47.0085)], {"highway": "tertiary"})
    way([(38.008, s), (38.008, 47.0085)], {"highway": "residential"})
    way([(38.009, 47.003), (38.009, 47.004)], {"highway": "residential", "tunnel": "yes"})
    way([(38.004, 47.003), (38.004, 47.005)], {"highway": "service"})
    way([(w, 47.001), (e, 47.001)], {"railway": "rail"})
    way([(w, 47.0015), (e, 47.0015)], {"railway": "rail", "service": "siding"})
    way([(e + 0.01, 47.0085), (w - 0.01, 47.0085)], {"natural": "coastline"})
    way([(38.003, 47.003), (38.0035, 47.003), (38.0035, 47.0035), (38.003, 47.0035)],
        {"building": "apartments", "building:levels": "5"}, closed=True)
    way([(38.005, 47.003), (38.0055, 47.003), (38.0055, 47.0035), (38.005, 47.0035)],
        {"amenity": "school", "building": "school", "name": "Школа №5"}, closed=True)
    way([(38.006, 47.004), (38.0065, 47.004), (38.0065, 47.0045), (38.006, 47.0045)], {"leisure": "park"}, closed=True)
    way([(38.0045, 47.004), (38.0048, 47.004), (38.0048, 47.0042), (38.0045, 47.0042)],
        {"natural": "water", "water": "pond"}, closed=True)
    node(38.0042, 47.0021, {"highway": "bus_stop", "name": "Перша вулиця", "name:ru": "Первая улица"})
    node(38.0052, 47.0041, {"amenity": "hospital", "name": "Больница"})
    return ('<?xml version="1.0" encoding="UTF-8"?><osm version="0.6" generator="test">'
            + "".join(nodes) + "".join(ways) + "</osm>")


RELEASE = "2026-09-23.0"
OSM_SOURCE = {"property": "", "dataset": "OpenStreetMap", "license": "ODbL-1.0", "record_id": None, "update_time": None,
              "confidence": None, "between": None, "provider": "osm", "resource": "planet", "version": "2026-09-09"}


def source(record_id=None, between=None, dataset="OpenStreetMap", license="ODbL-1.0"):
    return {**OSM_SOURCE, "dataset": dataset, "license": license, "record_id": record_id, "between": between}


def overture_cache(project) -> Path:
    """Кеш Overture на синтетической сетке (те же улицы, что в osm_xml): записи в формате выборок Overture."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    import shapely

    from cm_carto.fetch import overture

    folder = overture.cache_dir(project, RELEASE)
    folder.mkdir(parents=True)

    def save(name, columns, rows):
        data = {c: [r.get(c) for r in rows] for c in columns}
        data["geometry"] = [shapely.to_wkb(r["geometry"]) for r in rows]
        pq.write_table(pa.table(data), folder / (name + ".parquet"))

    def line(*points):
        return shapely.LineString(points)

    def rect(x0, y0, x1, y1):
        return shapely.box(x0, y0, x1, y1)

    w, e, s = WEST, EAST, SOUTH
    flags = [{"values": ["is_tunnel"], "between": [0.9, 1.0]}]
    cols = overture.rules()["overture"]["datasets"]
    save("transportation-segment", cols["transportation/segment"]["columns"], [
        {"id": "seg-1", "names": {"primary": "Первая улица", "common": None}, "subtype": "road", "class_": None, "class": "secondary",
         "road_flags": flags, "geometry": line((w, 47.002), (e, 47.002)),
         "sources": [source("w1@3", [0.0, 0.5]), source("w2@1", [0.5, 1.0])]},
        {"id": "seg-2", "subtype": "road", "class": "residential", "geometry": line((w, 47.006), (e, 47.006)),
         "sources": [source("w3@1")]},
        {"id": "seg-3", "subtype": "road", "class": "tertiary", "geometry": line((38.002, s), (38.002, 47.0085)),
         "sources": [source("w4@1")]},
        {"id": "seg-4", "subtype": "road", "class": "residential", "geometry": line((38.008, s), (38.008, 47.0085)),
         "sources": [source("w5@1")]},
        {"id": "seg-5", "subtype": "road", "class": "service", "geometry": line((38.004, 47.003), (38.004, 47.005)),
         "sources": [source("w6@1")]},
        {"id": "seg-6", "subtype": "road", "class": "footway", "geometry": line((38.0041, 47.003), (38.0041, 47.005)),
         "sources": [source(None, dataset="Esri Community Maps", license="CC-BY-4.0")]},
        {"id": "seg-7", "subtype": "rail", "class": "standard_gauge", "geometry": line((w, 47.001), (e, 47.001)),
         "sources": [source("w7@1")]},
        {"id": "seg-8", "subtype": "rail", "class": "tram", "geometry": line((w, 47.0015), (e, 47.0015)),
         "sources": [source("w8@1")]},
    ])
    save("base-water", cols["base/water"]["columns"], [
        {"id": "water-1", "subtype": "physical", "class": "sea", "geometry": rect(w - 0.02, 47.0085, e + 0.02, 47.02),
         "sources": [source("r100@1")]},
        {"id": "water-2", "subtype": "physical", "class": "bay", "geometry": rect(38.003, 47.0085, 38.006, 47.015),
         "sources": [source("r101@1")]},
        {"id": "water-3", "subtype": "pond", "class": "pond", "geometry": rect(38.0045, 47.004, 38.0048, 47.0042),
         "sources": [source("w50@1")]},
        {"id": "water-4", "subtype": "human_made", "class": "swimming_pool",
         "geometry": rect(38.006, 47.003, 38.0062, 47.0032), "sources": [source("w51@1")]},
    ])
    save("base-land", cols["base/land"]["columns"], [
        {"id": "land-1", "subtype": "forest", "class": "wood", "geometry": rect(38.0092, 47.004, 38.0098, 47.005),
         "sources": [source("w52@1")]},
    ])
    save("base-land_use", cols["base/land_use"]["columns"], [
        {"id": "lu-1", "names": {"primary": "Школа №5", "common": None}, "subtype": "education", "class": "school",
         "geometry": rect(38.005, 47.003, 38.0055, 47.0035), "sources": [source("w60@1")]},
        {"id": "lu-2", "subtype": "park", "class": "park", "geometry": rect(38.006, 47.004, 38.0065, 47.0045),
         "sources": [source("w61@1")]},
    ])
    save("base-infrastructure", cols["base/infrastructure"]["columns"], [
        {"id": "inf-1", "names": {"primary": "Первая улица", "common": None}, "subtype": "transit", "class": "bus_stop",
         "geometry": shapely.Point(38.0042, 47.0021), "sources": [source("n70@2")]},
        {"id": "inf-2", "subtype": "transit", "class": "platform", "geometry": shapely.Point(38.0043, 47.0021),
         "sources": [source("n71@1")]},
    ])
    save("buildings-building", cols["buildings/building"]["columns"], [
        {"id": "b-1", "subtype": "residential", "class": "apartments", "num_floors": 5.0,
         "geometry": rect(38.003, 47.003, 38.0035, 47.0035), "sources": [source("w80@1")]},
        {"id": "b-2", "subtype": None, "class": None, "num_floors": None,
         "geometry": rect(38.007, 47.003, 38.0072, 47.0032),
         "sources": [source(None, dataset="Microsoft ML Buildings")]},
    ])
    place = {"operating_status": None, "basic_category": "x", "addresses": None}
    google = [source("abc=", dataset="meta", license="CDLA-Permissive-2.0")]
    save("places-place", cols["places/place"]["columns"], [
        {**place, "id": "pl-1", "names": {"primary": "Школа 5", "common": None}, "taxonomy": {"primary": "school"}, "confidence": 0.9,
         "geometry": shapely.Point(38.00525, 47.00325), "sources": google},
        {**place, "id": "pl-2", "names": {"primary": "Больница", "common": None}, "taxonomy": {"primary": "hospital"},
         "confidence": 0.9, "geometry": shapely.Point(38.0052, 47.0041), "sources": google},
        {**place, "id": "pl-3", "names": {"primary": "Редкая", "common": None}, "taxonomy": {"primary": "hospital"},
         "confidence": 0.1, "geometry": shapely.Point(38.0053, 47.0041), "sources": google},
        {**place, "id": "pl-4", "names": {"primary": "Закрытая", "common": None}, "taxonomy": {"primary": "hospital"},
         "confidence": 0.9, "geometry": shapely.Point(38.0054, 47.0041), "sources": google,
         "operating_status": "closed"},
        {**place, "id": "pl-5", "names": {"primary": "Кафе", "common": None}, "taxonomy": {"primary": "cafe"}, "confidence": 0.9,
         "geometry": shapely.Point(38.0055, 47.0041), "sources": google},
    ])
    import yaml
    (folder / "SOURCE.yaml").write_text(yaml.safe_dump({"release": RELEASE, "extent_wgs84": list(project.fetch_extent_wgs84)}),
                                        encoding="utf-8")
    return folder
