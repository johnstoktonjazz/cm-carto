"""Изохроны на решетке улиц 100 м (пешком 80 м/мин): рост и вложенность, частичное ребро на краю, express,
контур, повтор байт в байт, устойчивость object_id."""

import copy

import pytest
import shapely
from shapely.geometry import Point
from analysis_fixtures import STEP, STOP, lattice, project_in, run, square, stop, street, write_world, X0, Y0

from cm_carto import gpkg
from cm_carto.analysis import isochrone, params


def world(tmp_path, contours=()):
    project = project_in(tmp_path)
    write_world(project, lattice(range(11), range(11)), [stop("osm-n1", 5, 5)], contours)
    return project


def test_isochrones_grow_with_minutes_and_nest(tmp_path):
    project = world(tmp_path)
    rows = isochrone.rows(project, STOP, "walk", [2, 5, 8])
    areas = [r["geometry"].area for r in rows]
    assert areas == sorted(areas) and len(set(areas)) == 3
    for small, big in zip(rows, rows[1:]):
        assert small["geometry"].difference(big["geometry"]).area < 0.01


def test_edge_is_reached_partially_at_the_border(tmp_path):
    """1,5 мин = 120 м: 4 улицы по 100 м от узла и по 20 м на трех продолжениях у каждой."""
    project = world(tmp_path)
    _, _, results = isochrone.compute(project, STOP, "walk", [1.5])
    assert results[1.5][0].length_m == pytest.approx(4 * 100 + 4 * 3 * 20)


def test_point_off_the_streets_is_refused(tmp_path):
    project = project_in(tmp_path)
    write_world(project, lattice(range(3), range(3)), [stop("osm-n1", 5, 5)])
    with pytest.raises(ValueError, match="дальше"):
        isochrone.rows(project, STOP, "walk", [5])


def test_walk_does_not_cross_express_drive_does(tmp_path):
    project = project_in(tmp_path)
    roads = (lattice(range(0, 4), range(0, 4), prefix="l") + lattice(range(6, 10), range(0, 4), prefix="r")
             + [street("link", [(X0 + 300, Y0), (X0 + 600, Y0)], "express")])
    write_world(project, roads, [stop("osm-n1", 1, 1)])
    walk = isochrone.rows(project, STOP, "walk", [30])[0]
    drive = isochrone.rows(project, STOP, "drive", [10])[0]
    assert walk["geometry"].bounds[2] < X0 + 400
    assert drive["geometry"].bounds[2] > X0 + 900
    assert drive["speed_kmh"] == pytest.approx(30.0, abs=3)


def test_contour_starts_from_boundary_nodes_and_is_inside_the_isochrone(tmp_path):
    contour = square(3, 3, 6, 6)
    project = world(tmp_path, [("c1", contour)])
    row = isochrone.rows(project, "boundary_planning:c1", "walk", [3])[0]
    assert row["geometry"].buffer(0.5).contains(contour)
    # от границы 3 мин = 240 м: полоса вокруг контура не уже 240 м по улицам
    assert row["geometry"].bounds[0] <= contour.bounds[0] - 200


def test_isochrone_row_follows_the_contract(tmp_path):
    project = world(tmp_path)
    row = isochrone.rows(project, STOP, "walk", [10])[0]
    assert row["object_id"] == "isochrones:walk-10-transit_stops-osm-n1"
    assert (row["mode"], row["minutes"], row["speed_kmh"], row["origin_id"]) == ("walk", 10.0, 4.8, STOP)
    assert row["status"] == "calc" and row["source_license"] == "ODbL 1.0" and row["fact_ids"] == ["ASM-9001"]
    assert row["source_label"] == "расчет картографа по графу тестовые улицы от 2026-10-11, 4,8 км/ч"
    isochrone.save(project, [row])
    assert gpkg.check(project.part("analysis-isochrones")) == []


def test_assumption_id_goes_to_fact_ids(tmp_path, monkeypatch):
    project = world(tmp_path)
    changed = copy.deepcopy(params.load())
    changed["modes"]["walk"]["assumption_id"] = "ASM-0101"
    monkeypatch.setattr(params, "load", lambda: changed)
    assert isochrone.rows(project, STOP, "walk", [5])[0]["fact_ids"] == ["ASM-0101"]


def test_repeat_gives_the_same_file_and_ids(tmp_path):
    project = world(tmp_path)
    path = isochrone.save(project, isochrone.rows(project, STOP, "walk", [5, 10]))
    first = path.read_bytes()
    ids = list(gpkg.read(path)["isochrones"]["object_id"])
    isochrone.save(project, isochrone.rows(project, STOP, "walk", [5, 10]))
    assert path.read_bytes() == first
    assert list(gpkg.read(path)["isochrones"]["object_id"]) == ids == [
        "isochrones:walk-10-transit_stops-osm-n1", "isochrones:walk-5-transit_stops-osm-n1"]


def test_new_isochrones_are_added_to_the_part(tmp_path):
    project = world(tmp_path)
    isochrone.save(project, isochrone.rows(project, STOP, "walk", [5]))
    path = isochrone.save(project, isochrone.rows(project, STOP, "drive", [5]))
    assert list(gpkg.read(path)["isochrones"]["object_id"]) == [
        "isochrones:drive-5-transit_stops-osm-n1", "isochrones:walk-5-transit_stops-osm-n1"]


def test_cli_isochrone_and_unknown_origin(tmp_path):
    project = world(tmp_path)
    command = ["isochrone", str(project.root / "project.yaml"), "--mode", "walk", "--minutes", "5", "10"]
    assert run(command + ["--from", STOP]) == 0
    assert len(gpkg.read(project.part("analysis-isochrones"))["isochrones"]) == 2
    with pytest.raises(ValueError, match="не найден"):
        run(command + ["--from", "transit_stops:osm-n404"])


@pytest.mark.parametrize("reverse", [False, True])
def test_edge_piece_starts_from_the_node_it_was_reached_from(tmp_path, reverse):
    """Линия ребра идет не обязательно от меньшего узла к большему: куски на краю растут от достигнутого конца."""
    roads = lattice(range(11), range(11))
    if reverse:
        for road in roads:
            road["geometry"] = shapely.reverse(road["geometry"])
    project = project_in(tmp_path)
    write_world(project, roads, [stop("osm-n1", 5, 5)])
    _, _, results = isochrone.compute(project, STOP, "walk", [1.5])
    reach, geom = results[1.5]
    centre = Point(X0 + 5 * STEP, Y0 + 5 * STEP)
    assert reach.length_m == pytest.approx(640)
    for piece in reach.pieces:
        for x, y in piece.coords:
            assert abs(x - centre.x) + abs(y - centre.y) <= 120 + 1e-6
    assert len(geom.geoms) == 1 and geom.area == pytest.approx(
        isochrone.polygon(reach, [], None, params.load()["isochrone"]).area)


def test_rules_name_temporary_assumptions_for_both_modes():
    modes = params.load()["modes"]
    assert (modes["walk"]["assumption_id"], modes["drive"]["assumption_id"]) == ("ASM-9001", "ASM-9002")
