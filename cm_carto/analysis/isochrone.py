"""Изохрона от объекта по графу улиц.

Метод:
1. Граф режима (`graph.build`): пешком — все классы улиц, кроме скоростных, неориентированный; на автомобиле — без
   пешеходных улиц и дорожек, скорости по классам, ориентированный: одностороннюю улицу (`roads.oneway`) проходят
   только по направлению линии. Параметры, в том числе какой режим ориентированный (`directed`), — `rules/analysis.yaml`.
2. Старт. Точка привязывается к ближайшему отрезку графа (дальше `point_snap_max_m` — ошибка): время до концов
   отрезка считается вдоль него (по односторонней улице — только до ее конца), а часть самого отрезка вокруг точки
   достижима сразу (по односторонней — только впереди точки). Контур стартует от
   узлов графа не дальше `contour_boundary_tolerance_m` от его границы, время старта 0.
3. Кратчайшее время до узлов — Дейкстра по времени ребер (длина / скорость класса), в ориентированном графе — по дугам.
4. Достижимые части ребер: ребро доходит от каждого конца, с которого его можно пройти, на остаток времени × скорость; на краю изохроны ребро
   достижимо частично. Части, пришедшие с разных концов, объединяются.
5. Полигон: полоса `buffer_m` вокруг достижимых частей (для контура — вместе с самим контуром), внутренние дыры
   закрыты, контур не упрощается: изохроны разных сроков от одного объекта вложены точно. Изохрона — оценка по
   графу улиц, не круг.

Скорость в слое `isochrones` — средняя по достижимой сети: длина / время прохода по ее частям.
"""

from dataclasses import dataclass

import geopandas as gpd
import networkx as nx
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import substring

from cm_carto import gpkg
from cm_carto.analysis import data, graph as graph_mod, params as params_mod
from cm_carto.project import Project

ORIGIN = "origin"


@dataclass(frozen=True)
class Reach:
    pieces: list           # достижимые части отрезков, LineString
    length_m: float
    travel_min: float      # сумма времен прохода по частям


def point_start(g: nx.MultiGraph, point: Point, snap_max: float):
    """Привязка точки к отрезку: время до его концов, номер ребра и положение точки на нем, соединительная линия.
    В ориентированном графе точка выходит на отрезок только в разрешенных направлениях: по односторонней улице —
    только к концу линии."""
    edges = list(g.edges(keys=True, data=True))
    edge = edges[int(STRtree([e[3]["line"] for e in edges]).nearest(point))][3]
    line, speed = edge["line"], edge["speed_m_min"]
    gap = line.distance(point)
    if gap > snap_max:
        raise ValueError(f"точка дальше {snap_max} м от ближайшего отрезка графа ({gap:.0f} м)")
    along = line.project(point)
    same = [e for *_, e in edges if e["edge"] == edge["edge"]]
    from_start, from_end = any(e["from_start"] for e in same), any(e["from_end"] for e in same)
    seeds = {}
    if from_start:     # от начала линии к концу: до конца отрезка
        seeds[edge["end"]] = (edge["length"] - along) / speed
    if from_end:       # от конца линии к началу
        seeds[edge["start"]] = min(seeds.get(edge["start"], float("inf")), along / speed)
    connector = [LineString([point, line.interpolate(along)])] if gap > 0 else []
    return seeds, (edge["edge"], along, from_start, from_end), connector


def contour_start(g: nx.MultiGraph, contour, tolerance: float) -> dict:
    nodes = list(g.nodes)
    near = shapely.dwithin(shapely.points([g.nodes[n]["pos"] for n in nodes]), contour.boundary, tolerance)
    seeds = {n: 0.0 for n, ok in zip(nodes, near) if ok}
    if not seeds:
        raise ValueError(f"у границы контура нет узлов графа в {tolerance} м")
    return seeds


def times(g: nx.MultiGraph, seeds: dict, limit: float) -> dict:
    """Кратчайшее время до узлов, мин, не больше limit. Узел ORIGIN добавляется на время расчета."""
    g.add_weighted_edges_from([(ORIGIN, n, t) for n, t in seeds.items()], weight="minutes")
    dist = nx.single_source_dijkstra_path_length(g, ORIGIN, cutoff=limit, weight="minutes")
    g.remove_node(ORIGIN)
    return dist


def reach(g: nx.MultiGraph, dist: dict, direct, minutes: float) -> Reach:
    spans, shapes = {}, {}
    for u, v, key, edge in g.edges(keys=True, data=True):
        w, speed = edge["length"], edge["speed_m_min"]
        shapes[edge["edge"]] = (edge["line"], speed)
        found = spans.setdefault(edge["edge"], [])
        if edge["from_start"] and edge["start"] in dist and dist[edge["start"]] < minutes:
            found.append((0.0, min(w, (minutes - dist[edge["start"]]) * speed)))
        if edge["from_end"] and edge["end"] in dist and dist[edge["end"]] < minutes:
            found.append((max(0.0, w - (minutes - dist[edge["end"]]) * speed), w))
    if direct is not None:
        number, along, from_start, from_end = direct
        line, speed = shapes[number]
        if from_start:
            spans[number].append((along, min(line.length, along + minutes * speed)))
        if from_end:
            spans[number].append((max(0.0, along - minutes * speed), along))
    pieces, length, travel = [], 0.0, 0.0
    for number, found in spans.items():
        line, speed = shapes[number]
        merged = []
        for start, end in sorted(found):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in merged:
            if end > start:
                pieces.append(substring(line, start, end))
                length += end - start
                travel += (end - start) / speed
    return Reach(pieces, length, travel)


def polygon(r: Reach, extra: list, contour, p: dict) -> MultiPolygon:
    area = shapely.MultiLineString(r.pieces + extra).buffer(p["buffer_m"], quad_segs=p["quad_segs"])
    if contour is not None:
        area = area.union(contour)
    parts = area.geoms if area.geom_type == "MultiPolygon" else [area]
    closed = shapely.union_all([Polygon(part.exterior) for part in parts])
    return closed if closed.geom_type == "MultiPolygon" else MultiPolygon([closed])


def compute(project: Project, origin_id: str, mode: str, minutes: list[float]):
    """Источник, дороги и достижимые части по каждому сроку: (roads, origin, {минуты: (Reach, полигон)})."""
    p = params_mod.load()
    roads = data.layer(project, "roads")
    origin = data.one(project, origin_id)
    g = graph_mod.build(roads, mode, p)
    geom = origin.geometry
    if geom.geom_type == "Point":
        seeds, direct, extra = point_start(g, geom, p["origin"]["point_snap_max_m"])
        contour = None
    elif geom.geom_type in ("Polygon", "MultiPolygon"):
        seeds, direct, extra, contour = contour_start(g, geom, p["origin"]["contour_boundary_tolerance_m"]), None, [], geom
    else:
        raise ValueError(f"{origin_id}: источник изохроны — точка или контур, а не {geom.geom_type}")
    dist = times(g, seeds, max(minutes))
    out = {}
    for m in minutes:
        r = reach(g, dist, direct, m)
        out[m] = (r, polygon(r, extra, contour, p["isochrone"]))
    return roads, origin, out


def single(frame, column: str) -> str:
    values = frame[column].dropna().unique()
    if len(values) != 1:
        raise ValueError(f"улицы из нескольких источников: {column} = {sorted(values)}")
    return str(values[0])


def object_id(origin_id: str, mode: str, minutes: float) -> str:
    return f"isochrones:{mode}-{minutes:g}-{origin_id.replace(':', '-')}"


def rows(project: Project, origin_id: str, mode: str, minutes: list[float]) -> list[dict]:
    p = params_mod.load()
    spec = p["modes"][mode]
    roads, origin, results = compute(project, origin_id, mode, minutes)
    label, date, licence = single(roads, "source_label"), single(roads, "source_date"), single(roads, "source_license")
    out = []
    for m, (r, geom) in results.items():
        speed = round(r.length_m / r.travel_min * 60 / 1000, 1)
        row = {"object_id": object_id(origin_id, mode, m), "kind": spec["kind"].format(minutes=f"{m:g}"),
               "status": "calc",
               "source_label": p["labels"]["source"].format(roads_label=label),
               "source_date": date, "source_license": licence,
               "fact_ids": [spec["assumption_id"]] if spec["assumption_id"] else [],
               "mode": mode, "minutes": float(m), "speed_kmh": speed, "origin_id": origin_id, "geometry": geom}
        if isinstance(origin.get("name"), str):
            row["name"] = origin["name"]
        out.append(row)
    return out


def part_hash(project: Project, object_ids) -> str:
    """Хэш входов части изохрон: правила, система координат, улицы, состав изохрон. Одно место: им пользуются save
    и сборка образцов (scripts/build_k1.sh), чтобы понять, свежа ли часть."""
    return gpkg.input_hash(params_mod.RULES, project.crs_name, *data.locate(project, "roads"), sorted(object_ids))


def save(project: Project, new: list[dict]):
    """Часть analysis-isochrones: новые изохроны заменяют прежние с тем же object_id, остальные сохраняются."""
    part = project.part("analysis-isochrones")
    items = pd.DataFrame(new)
    if part.exists():
        old = gpkg.read(part)["isochrones"]
        items = pd.concat([old[~old["object_id"].isin(items["object_id"])], items], ignore_index=True)
    frame = gpd.GeoDataFrame(items, geometry="geometry", crs=project.crs)
    return gpkg.write(part, {"isochrones": frame}, project.name, project.crs, project.crs_name,
                      part_hash(project, frame["object_id"]))
