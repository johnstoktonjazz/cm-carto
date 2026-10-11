"""Граф улиц из слоя roads: узлы — перекрестки и концы линий, ребра — части линий между узлами с длиной в метрах
системы проекта и временем прохода по скорости класса.

Объект слоя roads — целая улица (элемент OSM), а не отрезок между перекрестками. Перекресток — вершина, общая у
двух разных линий (вершины ближе `snap_tolerance_m` считаются одной точкой); так устроена топология OSM. Линии, которые
пересекаются без общей вершины (мост, путепровод, тоннель), не соединяются. Каждая линия режется в своих перекрестках
и концах. Параллельные ребра между теми же узлами не склеиваются.

Класс улицы без скорости в `rules/analysis.yaml` (null) в графе режима не участвует. Однонаправленность улиц в слое
roads не хранится: граф неориентированный.
"""

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import LineString


def build(roads: gpd.GeoDataFrame, mode: str, params: dict) -> nx.MultiGraph:
    if roads.crs.axis_info[0].unit_name != "metre":
        raise ValueError(f"система координат {roads.crs.name} не в метрах: длины графа считаются в ее единицах")
    speeds = params["modes"][mode]["speed_kmh"]
    roads = roads.assign(speed=roads["road_class"].map(speeds)).sort_values("object_id", kind="stable")
    roads = roads[roads["speed"].notna()]
    lines = [(part, speed) for geom, speed in zip(roads.geometry, roads["speed"]) for part in geom.geoms]

    coords, owner = shapely.get_coordinates(np.array([line for line, _ in lines], dtype=object), return_index=True)
    changes = owner[1:] != owner[:-1]
    first, last = np.r_[True, changes], np.r_[changes, True]

    # вершины в допуске — одна точка; точка с вершинами двух разных линий — перекресток
    points = shapely.points(coords)
    pairs = STRtree(points).query(points, predicate="dwithin", distance=params["graph"]["snap_tolerance_m"])
    near = nx.Graph()
    near.add_edges_from(pairs[:, pairs[0] != pairs[1]].T.tolist())
    point = np.arange(len(coords))
    for component in nx.connected_components(near):
        point[list(component)] = min(component)
    lines_at = pd.Series(owner).groupby(point).nunique()
    is_node = first | last | (lines_at.loc[point].to_numpy() >= 2)

    node_of = {p: n for n, p in enumerate(np.unique(point[is_node]))}
    graph = nx.MultiGraph()
    for p, n in node_of.items():
        graph.add_node(n, pos=tuple(coords[point == p].mean(axis=0)))

    for k, (_, speed) in enumerate(lines):
        at = np.flatnonzero((owner == k) & is_node)
        per_minute = speed * 1000 / 60
        for a, b in zip(at, at[1:]):
            part = LineString(coords[a:b + 1])
            if part.length > 0:
                graph.add_edge(node_of[point[a]], node_of[point[b]], line=part, length=part.length,
                               speed_m_min=per_minute, minutes=part.length / per_minute)
    return graph
