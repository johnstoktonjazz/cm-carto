"""Синтетические данные для тестов fetch: проект с охватом в долях градуса и маленький .osm."""

from pathlib import Path

import yaml

WEST, SOUTH, EAST, NORTH = 38.000, 47.000, 38.010, 47.009


def project_file(folder: Path, **extra) -> Path:
    data = {"name": "синтетический проект", "crs": {"name": "EPSG:32637", "definition": "EPSG:32637"},
            "extent_wgs84": [WEST, SOUTH, EAST, NORTH], "geo": "geo", "inputs": "input", **extra}
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

    w, e, s, n = WEST, EAST, SOUTH, NORTH
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
    node(38.0042, 47.0021, {"highway": "bus_stop", "name": "Первая улица"})
    node(38.0052, 47.0041, {"amenity": "hospital", "name": "Больница"})
    return ('<?xml version="1.0" encoding="UTF-8"?><osm version="0.6" generator="test">'
            + "".join(nodes) + "".join(ways) + "</osm>")
