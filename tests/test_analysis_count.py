"""Подсчеты в зоне: точки внутри, площади и линии с пересечением, отбор where, заготовки CALC по схеме fact,
повтор байт в байт."""

import json

import geopandas as gpd
import jsonschema
import pytest
import yaml
from analysis_fixtures import X0, Y0, run, street
from buffer_fixtures import parcel, poi, project_with_registry, rect, write_layers

from cm_carto import gpkg, paths
from cm_carto.analysis import buffers, count

ZONE = "buffers:szz-industry-v-parcels-z"


def world(tmp_path):
    """Участок z 100x100 м, буфер 50 м по NORM-90002: зона x и y от -50 до 150 (с закруглением углов)."""
    project = project_with_registry(tmp_path)
    write_layers(
        project,
        parcels=[parcel("z", rect(0, 0, 100, 100)), parcel("half", rect(100, 0, 300, 100), "Магазины"),
                 parcel("touch", rect(150, 0, 250, 100)), parcel("far", rect(400, 0, 500, 100))],
        poi_other=[poi("in1", 10, 10), poi("in2", 120, 50, "банк"), poi("edge", 150, 50), poi("out", 300, 300)],
        roads=[street("cross", [(X0 - 100, Y0 + 50), (X0 + 100, Y0 + 50)]),
               street("away", [(X0 - 100, Y0 + 500), (X0 + 100, Y0 + 500)])])
    buffers.save(project, buffers.rows(project, "szz-industry-v", ["parcels:z"]))
    return project


def by_what(drafts):
    return {d["subject"].split(", ")[1]: d for d in drafts}


def test_points_inside_the_zone_are_counted(tmp_path):
    project = world(tmp_path)
    (draft,) = count.drafts(project, ZONE, "poi_other")
    assert draft["value"] == 2 and draft["unit"] == "шт."                 # in1, in2; edge — на границе, не внутри
    assert "poi_other:in1" in draft["formula"] and "poi_other:in2" in draft["formula"]
    assert "poi_other:out" not in draft["formula"]


def test_areas_count_with_the_intersection_area(tmp_path):
    project = world(tmp_path)
    got = by_what(count.drafts(project, ZONE, "parcels"))
    assert got["число объектов"]["value"] == 1                           # half; touch только касается границы, z — источник
    # half: часть x от 100 до 150 м на всю высоту 100 м (0,5 га)
    assert got["площадь пересечения"]["value"] == pytest.approx(0.5, abs=0.01)
    assert "parcels:half 0.50 га" in got["площадь пересечения"]["formula"]


def test_source_parcel_is_not_counted_in_its_own_zone(tmp_path):
    project = world(tmp_path)
    for draft in count.drafts(project, ZONE, "parcels"):
        assert "parcels:z " not in draft["formula"] and "parcels:z," not in draft["formula"]
        assert "без объекта-источника зоны parcels:z" in draft["subject"] + draft["formula"]
    # слой без участка-источника подсчитывается как раньше
    assert "без объекта-источника" not in count.drafts(project, ZONE, "poi_other")[0]["subject"]


def test_sources_follow_the_part_of_each_object(tmp_path):
    """Одинаковый слой в двух частях: подпись части — только объектов этой части."""
    project = world(tmp_path)
    other = parcel("in-other", rect(100, 0, 140, 100), "Магазины")
    other["source_label"] = "подпись второй части"
    gpkg.write(project.part("second"), {"parcels": gpd.GeoDataFrame([other], geometry="geometry", crs=project.crs)},
               project.name, project.crs, project.crs_name, "sha256:" + "1" * 64)
    drafts = count.drafts(project, ZONE, "parcels")
    parcels = [s for s in drafts[0]["sources"] if s["doc_id"].endswith(":parcels")]
    by_part = {s["doc_id"].split(":")[1]: s["label"] for s in parcels}
    assert set(by_part) == {"fetch-test", "second"}
    assert "подпись второй части" in by_part["second"] and "подпись второй части" not in by_part["fetch-test"]


def test_where_filters_the_counted_layer(tmp_path):
    project = world(tmp_path)
    got = by_what(count.drafts(project, ZONE, "parcels", "permitted_use = 'Магазины'"))
    assert got["число объектов"]["value"] == 1 and "отбор «permitted_use = 'Магазины'»" in got["число объектов"]["subject"]
    assert by_what(count.drafts(project, ZONE, "poi_other", "kind = 'банк'"))["число объектов"]["value"] == 1


def test_lines_give_length_in_the_zone(tmp_path):
    project = world(tmp_path)
    got = by_what(count.drafts(project, ZONE, "roads"))
    assert got["число объектов"]["value"] == 1
    assert got["длина в зоне"]["value"] == pytest.approx(0.15, abs=0.005) and got["длина в зоне"]["unit"] == "км"


def test_empty_zone_gives_zero(tmp_path):
    project = world(tmp_path)
    got = by_what(count.drafts(project, ZONE, "parcels", "permitted_use = 'нет такого'"))
    assert got["число объектов"]["value"] == 0 and got["площадь пересечения"]["value"] == 0


def test_drafts_are_check_calc_with_norm_inputs_and_pass_the_fact_schema(tmp_path):
    project = world(tmp_path)
    validator = jsonschema.Draft202012Validator(json.loads(paths.schema("fact").read_text(encoding="utf-8")))
    drafts = count.drafts(project, ZONE, "parcels") + count.drafts(project, ZONE, "roads")
    for number, draft in enumerate(drafts, start=1):
        assert draft["status"] == "check" and draft["inputs"] == ["NORM-90002"] and "id" not in draft
        assert list(validator.iter_errors({"id": f"CALC-{number:05d}", **draft})) == []
        assert any(validator.iter_errors(draft))
    layers = {s["doc_id"] for d in drafts for s in d["sources"]}
    assert layers == {"geopackage:analysis-buffers:buffers", "geopackage:fetch-test:parcels", "geopackage:fetch-test:roads"}


def test_save_replaces_same_subject_and_repeats_bytes(tmp_path):
    project = world(tmp_path)
    path, _ = count.save(project, ZONE, "parcels")
    count.save(project, ZONE, "poi_other")
    first = path.read_bytes()
    count.save(project, ZONE, "parcels")
    assert path.read_bytes() == first
    subjects = [d["subject"] for d in yaml.safe_load(first.decode())]
    assert len(subjects) == 3 and subjects == sorted(subjects)


def test_list_is_cut_at_the_limit(tmp_path, monkeypatch):
    from cm_carto.analysis import params
    project = world(tmp_path)
    changed = json.loads(json.dumps(params.load()))
    changed["count"]["list_limit"] = 1
    monkeypatch.setattr(params, "load", lambda: changed)
    assert "и еще 1" in count.drafts(project, ZONE, "parcels")[0]["formula"]


def test_cli_count(tmp_path):
    project = world(tmp_path)
    assert run(["count", str(project.root / "project.yaml"), "--zone", ZONE, "--layer", "poi_other",
                "--where", "kind = 'банк'"]) == 0
    assert count.calc_path(project).exists()
