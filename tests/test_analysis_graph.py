"""Граф улиц: перекрестки по общим вершинам, режимы по классам улиц, проверка параметров."""

import geopandas as gpd
import networkx as nx
import pytest
import yaml
from analysis_fixtures import X0, Y0, lattice, street

from cm_carto import paths
from cm_carto.analysis import graph, params


def frame(rows: list[dict]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:32637")


def test_lattice_is_cut_at_crossings():
    g = graph.build(frame(lattice(range(11), range(11))), "walk", params.load())
    assert g.number_of_nodes() == 121
    assert g.number_of_edges() == 220
    assert {round(d["length"]) for *_, d in g.edges(data=True)} == {100}


def test_crossing_without_shared_vertex_is_not_a_junction():
    roads = [street("a", [(X0, Y0), (X0 + 100, Y0 + 100)]), street("b", [(X0, Y0 + 100), (X0 + 100, Y0)])]
    g = graph.build(frame(roads), "walk", params.load())
    assert g.number_of_nodes() == 4
    assert nx.number_connected_components(g) == 2


def test_express_is_out_of_walk_graph_but_in_drive_graph():
    left, right = lattice(range(0, 4), range(0, 4), prefix="l"), lattice(range(6, 10), range(0, 4), prefix="r")
    link = street("link", [(X0 + 300, Y0), (X0 + 600, Y0)], "express")
    roads = frame(left + right + [link])
    assert nx.number_connected_components(graph.build(roads, "walk", params.load())) == 2
    assert nx.number_connected_components(graph.build(roads, "drive", params.load())) == 1


def test_path_is_out_of_drive_graph():
    roads = frame([street("a", [(X0, Y0), (X0 + 100, Y0)], "path"), street("b", [(X0, Y0), (X0, Y0 + 100)])])
    assert graph.build(roads, "walk", params.load()).number_of_edges() == 2
    assert graph.build(roads, "drive", params.load()).number_of_edges() == 1


def test_params_without_a_class_are_refused(tmp_path):
    rules = yaml.safe_load((paths.ROOT / "rules" / "analysis.yaml").read_text(encoding="utf-8"))
    del rules["modes"]["walk"]["speed_kmh"]["path"]
    file = tmp_path / "analysis.yaml"
    file.write_text(yaml.safe_dump(rules), encoding="utf-8")
    with pytest.raises(ValueError, match="path"):
        params.load(file)
