"""Граф улиц из слоя roads: узлы — перекрестки и концы линий, ребра — части линий между узлами с длиной в метрах
системы проекта и временем прохода по скорости класса.

Объект слоя roads — целая улица (элемент OSM), а не отрезок между перекрестками. Перекресток — вершина, общая у
двух разных линий (вершины ближе `snap_tolerance_m` считаются одной точкой); так устроена топология OSM. Линии, которые
пересекаются без общей вершины (мост, путепровод, тоннель), не соединяются. Мост (`bridge`) и тоннель (`tunnel`)
образуют перекрестки только в концах: общая вершина во внутренней точке такой линии с другой улицей (путь под мостом
или над тоннелем) перекрестком не считается и линию не режет. Каждая линия режется в своих перекрестках и концах.
Параллельные ребра между теми же узлами не склеиваются.

Класс улицы без скорости в `rules/analysis.yaml` (null) в графе режима не участвует. Режим с `directed: true` строится
ориентированным (networkx MultiDiGraph): двусторонняя улица — пара дуг, односторонняя (`oneway = true`, движение по
направлению линии) — одна дуга от начала части линии к ее концу. Остальные режимы (пешком: односторонность пешехода
не касается) — неориентированный MultiGraph. Нет значения `oneway` или `bridge` (источник его не сообщает) — улица
двусторонняя и не мост.

Ребро хранит `edge` (номер части линии: две дуги двусторонней улицы имеют один номер), `line` (геометрия в
направлении исходной линии), `start` и `end` — узлы начала и конца этой геометрии (порядок пары в networkx этого не
задает) и `from_start`, `from_end` — с какого конца ребро проходится (у неориентированного оба, у дуги один).
"""

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import LineString


def flag(roads: gpd.GeoDataFrame, column: str) -> pd.Series:
    """Признак улицы из слоя: нет столбца или значения — ложь."""
    return roads[column].isin([True]) if column in roads else pd.Series(False, index=roads.index)


def build(roads: gpd.GeoDataFrame, mode: str, params: dict) -> nx.MultiGraph:
    if roads.crs.axis_info[0].unit_name != "metre":
        raise ValueError(f"система координат {roads.crs.name} не в метрах: длины графа считаются в ее единицах")
    spec = params["modes"][mode]
    roads = roads.assign(speed=roads["road_class"].map(spec["speed_kmh"]), oneway=flag(roads, "oneway"),
                         separated=flag(roads, "bridge") | flag(roads, "tunnel")).sort_values("object_id", kind="stable")
    roads = roads[roads["speed"].notna()]
    lines = [(part, speed, oneway, separated) for geom, speed, oneway, separated
             in zip(roads.geometry, roads["speed"], roads["oneway"], roads["separated"]) for part in geom.geoms]

    coords, owner = shapely.get_coordinates(np.array([line[0] for line in lines], dtype=object), return_index=True)
    changes = owner[1:] != owner[:-1]
    first, last = np.r_[True, changes], np.r_[changes, True]
    # внутренняя вершина моста или тоннеля перекрестка не образует: ни как узел, ни как вторая линия в чужом узле
    inner_separated = np.array([line[3] for line in lines], dtype=bool)[owner] & ~(first | last)

    # вершины в допуске — одна точка; точка с вершинами двух разных линий — перекресток
    points = shapely.points(coords)
    pairs = STRtree(points).query(points, predicate="dwithin", distance=params["graph"]["snap_tolerance_m"])
    near = nx.Graph()
    near.add_edges_from(pairs[:, pairs[0] != pairs[1]].T.tolist())
    point = np.arange(len(coords))
    for component in nx.connected_components(near):
        point[list(component)] = min(component)
    lines_at = pd.Series(owner[~inner_separated]).groupby(point[~inner_separated]).nunique()
    is_node = first | last | (~inner_separated & (lines_at.reindex(point).to_numpy() >= 2))

    node_of = {p: n for n, p in enumerate(np.unique(point[is_node]))}
    graph = nx.MultiDiGraph() if spec["directed"] else nx.MultiGraph()
    for p, n in node_of.items():
        graph.add_node(n, pos=tuple(coords[point == p].mean(axis=0)))

    edge = 0
    for k, (_, speed, oneway, _) in enumerate(lines):
        at = np.flatnonzero((owner == k) & is_node)
        per_minute = speed * 1000 / 60
        for a, b in zip(at, at[1:]):
            part = LineString(coords[a:b + 1])
            if part.length == 0:
                continue
            start, end = node_of[point[a]], node_of[point[b]]
            data = dict(edge=edge, line=part, length=part.length, start=start, end=end,
                        speed_m_min=per_minute, minutes=part.length / per_minute)
            edge += 1
            if not spec["directed"]:
                graph.add_edge(start, end, from_start=True, from_end=True, **data)
                continue
            graph.add_edge(start, end, from_start=True, from_end=False, **data)
            if not oneway:
                graph.add_edge(end, start, from_start=False, from_end=True, **data)
    return graph
