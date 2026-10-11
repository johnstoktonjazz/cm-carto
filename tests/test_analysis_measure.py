"""Заготовки CALC: значение, формула, слои-входы, статус check; запись проходит схему fact при подстановке id."""

import copy
import json

import jsonschema
import pytest
import yaml
from analysis_fixtures import STOP, lattice, project_in, run, stop, write_world

from cm_carto import paths
from cm_carto.analysis import isochrone, measure, params

ISO = "isochrones:walk-3-transit_stops-osm-n1"


def project_with_isochrone(tmp_path):
    project = project_in(tmp_path)
    write_world(project, lattice(range(11), range(11)), [stop("osm-n1", 5, 5)])
    isochrone.save(project, isochrone.rows(project, STOP, "walk", [3]))
    return project


def test_drafts_are_check_calc_without_id(tmp_path):
    project = project_with_isochrone(tmp_path)
    area, length = measure.drafts(project, ISO)
    for draft in (area, length):
        assert draft["kind"] == "calc" and draft["status"] == "check" and "id" not in draft
        assert all(word in draft["formula"] for word in ("roads", STOP, "4,8 км/ч", "3 мин"))
    assert (area["unit"], length["unit"]) == ("га", "км")
    assert area["value"] > 0 and length["value"] > 0
    layers = {s["doc_id"] for s in area["sources"]}
    assert layers == {"geopackage:fetch-test:roads", "geopackage:fetch-test:transit_stops"}
    assert all("2026-10-11" in s["label"] for s in area["sources"])


def test_drafts_take_the_speed_assumption_as_input(tmp_path):
    project = project_with_isochrone(tmp_path)
    assert all(d["inputs"] == ["ASM-9001"] for d in measure.drafts(project, ISO))


def test_layer_source_is_per_part_and_label(tmp_path):
    """Один слой в двух частях с разными подписями: каждая часть получает подписи своих объектов."""
    import geopandas as gpd
    from cm_carto import gpkg
    project = project_with_isochrone(tmp_path)
    second = stop("osm-n2", 7, 7)
    second["source_label"] = "подпись второй части"
    gpkg.write(project.part("second"), {"transit_stops": gpd.GeoDataFrame([second], geometry="geometry", crs=project.crs)},
               project.name, project.crs, project.crs_name, "sha256:" + "1" * 64)
    objects = gpd.GeoDataFrame([stop("osm-n1", 5, 5), second], geometry="geometry", crs=project.crs)
    got = {s["doc_id"]: s["label"] for s in measure.layer_source(project, "transit_stops", objects)}
    assert set(got) == {"geopackage:fetch-test:transit_stops", "geopackage:second:transit_stops"}
    assert "тестовые" not in got["geopackage:second:transit_stops"] and "подпись второй части" in got["geopackage:second:transit_stops"]
    assert "подпись второй части" not in got["geopackage:fetch-test:transit_stops"]


def test_length_is_the_reachable_network(tmp_path):
    """3 мин = 240 м от узла решетки: точки улиц с |x| + |y| <= 240 — по 1200 м вдоль каждого направления улиц."""
    project = project_with_isochrone(tmp_path)
    assert measure.drafts(project, ISO)[1]["value"] == pytest.approx(2.4)


def test_drafts_pass_the_fact_schema_with_a_temporary_id(tmp_path):
    project = project_with_isochrone(tmp_path)
    schema = json.loads(paths.schema("fact").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for number, draft in enumerate(measure.drafts(project, ISO), start=1):
        assert list(validator.iter_errors({"id": f"CALC-{number:05d}", **draft})) == []
        assert any(validator.iter_errors(draft))  # без id запись контракта не проходит: id присваивает база


def test_save_replaces_drafts_of_the_isochrone_and_repeats_bytes(tmp_path):
    project = project_with_isochrone(tmp_path)
    path, _ = measure.save(project, ISO)
    first = path.read_bytes()
    measure.save(project, ISO)
    assert path.read_bytes() == first
    assert [d["subject"] for d in yaml.safe_load(first.decode())] == [f"{ISO}, длина достижимой сети", f"{ISO}, площадь"]


def test_stale_isochrone_is_refused(tmp_path, monkeypatch):
    project = project_with_isochrone(tmp_path)
    changed = copy.deepcopy(params.load())
    changed["isochrone"]["buffer_m"] = 10
    monkeypatch.setattr(params, "load", lambda: changed)
    with pytest.raises(ValueError, match="не совпадает"):
        measure.drafts(project, ISO)


def test_cli_measure(tmp_path):
    project = project_with_isochrone(tmp_path)
    assert run(["measure", str(project.root / "project.yaml"), ISO]) == 0
    assert measure.calc_path(project).exists()
