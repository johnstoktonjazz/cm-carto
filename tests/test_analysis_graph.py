"""Граф улиц: перекрестки по общим вершинам, режимы по классам улиц, проверка параметров."""

import geopandas as gpd
import networkx as nx
import pytest
import yaml
from analysis_fixtures import X0, Y0, lattice, modes, street

from cm_carto import paths
from cm_carto.analysis import graph, params


def frame(rows: list[dict]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:32637")


def test_lattice_is_cut_at_crossings():
    g = graph.build(frame(lattice(range(11), range(11))), "walk", modes())
    assert g.number_of_nodes() == 121
    assert g.number_of_edges() == 220
    assert {round(d["length"]) for *_, d in g.edges(data=True)} == {100}


def test_crossing_without_shared_vertex_is_not_a_junction():
    roads = [street("a", [(X0, Y0), (X0 + 100, Y0 + 100)]), street("b", [(X0, Y0 + 100), (X0 + 100, Y0)])]
    g = graph.build(frame(roads), "walk", modes())
    assert g.number_of_nodes() == 4
    assert nx.number_connected_components(g) == 2


def test_express_is_out_of_walk_graph_but_in_drive_graph():
    left, right = lattice(range(0, 4), range(0, 4), prefix="l"), lattice(range(6, 10), range(0, 4), prefix="r")
    link = street("link", [(X0 + 300, Y0), (X0 + 600, Y0)], "express")
    roads = frame(left + right + [link])
    assert nx.number_connected_components(graph.build(roads, "walk", modes())) == 2
    assert nx.number_weakly_connected_components(graph.build(roads, "drive", modes())) == 1


def test_path_is_out_of_drive_graph():
    roads = frame([street("a", [(X0, Y0), (X0 + 100, Y0)], "path"), street("b", [(X0, Y0), (X0, Y0 + 100)])])
    assert graph.build(roads, "walk", modes()).number_of_edges() == 2
    assert graph.build(roads, "drive", modes()).number_of_edges() == 2      # одна двусторонняя улица b — пара дуг


def test_params_without_a_class_are_refused(tmp_path):
    rules = yaml.safe_load((paths.ROOT / "rules" / "analysis.yaml").read_text(encoding="utf-8"))
    del rules["modes"]["walk"]["speed_kmh"]["path"]
    file = tmp_path / "analysis.yaml"
    file.write_text(yaml.safe_dump(rules), encoding="utf-8")
    with pytest.raises(ValueError, match="path"):
        params.load(file)


def arcs(g) -> set:
    """Дуги ориентированного графа как пары координат (начало, конец) в метрах от угла сетки."""
    pos = lambda n: (round(g.nodes[n]["pos"][0] - X0), round(g.nodes[n]["pos"][1] - Y0))
    return {(pos(u), pos(v)) for u, v in g.edges()}


def test_two_way_street_is_a_pair_of_arcs_one_way_is_one_arc():
    roads = frame([street("two", [(X0, Y0), (X0 + 100, Y0)]),
                   street("one", [(X0, Y0 + 100), (X0 + 100, Y0 + 100)], oneway=True),
                   street("flag-off", [(X0, Y0 + 200), (X0 + 100, Y0 + 200)], oneway=False)])
    g = graph.build(roads, "drive", modes())
    assert isinstance(g, nx.MultiDiGraph)
    assert arcs(g) == {((0, 0), (100, 0)), ((100, 0), (0, 0)), ((0, 100), (100, 100)),
                       ((0, 200), (100, 200)), ((100, 200), (0, 200))}
    assert {d["edge"] for *_, d in g.edges(data=True)} == {0, 1, 2}


def test_one_way_is_ignored_when_mode_is_not_directed():
    roads = frame([street("one", [(X0, Y0), (X0 + 100, Y0)], oneway=True)])
    g = graph.build(roads, "walk", modes())
    assert type(g) is nx.MultiGraph and g.number_of_edges() == 1
    assert nx.has_path(g, 0, 1) and nx.has_path(g, 1, 0)


def test_one_way_follows_the_line_direction():
    """Линия записана с востока на запад: дуга идет так же."""
    roads = frame([street("one", [(X0 + 100, Y0), (X0, Y0)], oneway=True)])
    assert arcs(graph.build(roads, "drive", modes())) == {((100, 0), (0, 0))}


def test_one_way_is_cut_into_arcs_at_junctions():
    roads = frame([street("one", [(X0, Y0), (X0 + 100, Y0), (X0 + 200, Y0)], oneway=True),
                   street("cross", [(X0 + 100, Y0 - 100), (X0 + 100, Y0), (X0 + 100, Y0 + 100)])])
    assert arcs(graph.build(roads, "drive", modes())) >= {((0, 0), (100, 0)), ((100, 0), (200, 0))}


def test_missing_oneway_and_bridge_columns_mean_two_way_no_bridge():
    """Часть без атрибутов (чертеж DXF, старая выгрузка) — улицы двусторонние."""
    roads = frame([street("a", [(X0, Y0), (X0 + 100, Y0)])])
    assert "oneway" not in roads
    assert len(arcs(graph.build(roads, "drive", modes()))) == 2


def test_null_oneway_is_two_way():
    roads = frame([street("a", [(X0, Y0), (X0 + 100, Y0)], oneway=None), street("b", [(X0, Y0 + 100), (X0 + 100, Y0 + 100)], oneway=True)])
    assert len(arcs(graph.build(roads, "drive", modes()))) == 3


def over_street(**attrs):
    """Улица вдоль оси x (с attrs — мост или тоннель) над улицей вдоль оси y; общая вершина — в середине обеих линий."""
    return [street("under", [(X0 + 100, Y0 - 100), (X0 + 100, Y0), (X0 + 100, Y0 + 100)]),
            street("over", [(X0, Y0), (X0 + 100, Y0), (X0 + 200, Y0)], **attrs)]


def test_ordinary_streets_with_shared_mid_vertex_cross():
    g = graph.build(frame(over_street()), "walk", modes())
    assert g.number_of_nodes() == 5 and nx.number_connected_components(g) == 1


@pytest.mark.parametrize("attrs", [{"bridge": True}, {"tunnel": True}])
def test_bridge_and_tunnel_do_not_cross_streets_by_a_mid_vertex(attrs):
    """Общая вершина во внутренней точке моста с улицей под ним перекрестком не считается: мост не режется,
    улица под ним остается целой, концы моста — узлы."""
    g = graph.build(frame(over_street(**attrs)), "walk", modes())
    assert nx.number_connected_components(g) == 2
    assert g.number_of_nodes() == 4 and g.number_of_edges() == 2
    assert sorted(round(d["length"]) for *_, d in g.edges(data=True)) == [200, 200]


def test_bridge_ends_are_junctions_with_ramps():
    """Въезд и съезд: улицы, примыкающие к концам моста, соединяются с ним; улица под мостом — нет."""
    roads = over_street(bridge=True) + [street("in", [(X0 - 100, Y0), (X0, Y0)]), street("out", [(X0 + 200, Y0), (X0 + 300, Y0)])]
    g = graph.build(frame(roads), "walk", modes())
    assert nx.number_connected_components(g) == 2
    assert sorted(len(c) for c in nx.connected_components(g)) == [2, 4]      # улица под мостом; въезд, мост, съезд


def test_two_ordinary_streets_under_a_bridge_still_cross_each_other():
    """Мост над перекрестком: вершина, общая у двух улиц под мостом, остается узлом."""
    roads = [street("a", [(X0 + 100, Y0 - 100), (X0 + 100, Y0), (X0 + 100, Y0 + 100)]),
             street("b", [(X0, Y0), (X0 + 100, Y0), (X0 + 200, Y0)]),
             street("bridge", [(X0, Y0 + 5), (X0 + 100, Y0), (X0 + 200, Y0 + 5)], bridge=True)]
    g = graph.build(frame(roads), "walk", modes())
    assert sorted(g.degree(n) for n in g.nodes) == [1, 1, 1, 1, 1, 1, 4]      # крест 4 ребра + мост из одного ребра


def test_rules_make_drive_directed_and_walk_not():
    """Ориентированность режима — параметр правил (`modes.<режим>.directed`), а не имя режима в коде."""
    rules = params.load()["modes"]
    assert (rules["walk"]["directed"], rules["drive"]["directed"]) == (False, True)
