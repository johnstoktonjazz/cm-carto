"""Буферы по норме реестра: расстояние из записи NORM, ошибка без записи, полосы по длине, статус и подпись от
источника, повтор байт в байт, в правилах и коде нет чисел норм."""

import ast
import copy
import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from analysis_fixtures import run
from buffer_fixtures import REGISTRY, edit_registry, parcel, poi, project_with_registry, rect, water, write_layers

from cm_carto import gpkg, paths
from cm_carto.analysis import buffers, norms, params

PARCEL = "parcels:p1"


def world(tmp_path):
    project = project_with_registry(tmp_path)
    write_layers(project, parcels=[parcel("p1", rect(0, 0, 100, 100)), parcel("p2", rect(500, 0, 600, 100))],
                 water=[water("short", rect(0, 1000, 5000, 1020), "river"),
                        water("middle", rect(0, 2000, 20000, 2020), "river"),
                        water("long", rect(0, 3000, 80000, 3020), "river"),
                        water("lake", rect(0, 4000, 300, 4300), "lake")],
                 poi_other=[poi("a1", 0, 0, "котельная"), poi("a2", 900, 0, "аптека")])
    return project


def only(project, rule, origin=None):
    (row,) = buffers.rows(project, rule, origin)
    return row


def test_distance_comes_from_the_norm_and_follows_the_registry(tmp_path):
    project = world(tmp_path)
    row = only(project, "szz-industry-v", [PARCEL])
    assert row["distance_m"] == 50 and row["fact_ids"] == ["NORM-90002"] and row["origin_id"] == PARCEL
    minx, miny, maxx, maxy = row["geometry"].bounds
    assert (maxx - minx) == pytest.approx(200)
    edit_registry(project, "NORM-90002", value=75)       # код и правила те же, норма другая
    wider = only(project, "szz-industry-v", [PARCEL])
    assert wider["distance_m"] == 75 and wider["geometry"].bounds[2] - wider["geometry"].bounds[0] == pytest.approx(250)
    assert "75 м по NORM-90002" in wider["source_label"]


def test_status_and_label_follow_the_source(tmp_path):
    project = world(tmp_path)
    cadastral = only(project, "szz-industry-v", [PARCEL])
    assert cadastral["status"] == "calc" and cadastral["source_label"] == "буфер 50 м по NORM-90002, от тестовая КПТ"
    osm = only(project, "szz-boiler")
    assert cadastral["source_license"] == "данные заказчика, внешней лицензии нет" and cadastral["fact_ids"] == ["NORM-90002"]
    assert osm["status"] == "calc" and osm["source_license"] == "ODbL 1.0" and osm["fact_ids"] == ["NORM-90001"]
    assert osm["source_label"].startswith("буфер 50 м по NORM-90001, от тестовые улицы")
    assert osm["buffer_type"] == "sanitary"


def test_missing_norm_is_an_error(tmp_path):
    project = world(tmp_path)
    edit_registry(project, "NORM-90002", drop=True)
    with pytest.raises(ValueError, match="NORM-90002.*нет в реестре"):
        buffers.rows(project, "szz-industry-v", [PARCEL])


def test_norm_without_meters_is_an_error(tmp_path):
    project = world(tmp_path)
    edit_registry(project, "NORM-90002", value="по расчету")
    with pytest.raises(ValueError, match="число в метрах"):
        buffers.rows(project, "szz-industry-v", [PARCEL])


def test_bad_registry_record_is_refused(tmp_path):
    project = world(tmp_path)
    path = project.root / "registry" / "norms.yaml"
    records = yaml.safe_load(path.read_text(encoding="utf-8"))
    del records[0]["sources"]
    path.write_text(yaml.safe_dump(records, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="реестр не проходит"):
        norms.load(project)


def test_norm_by_river_length(tmp_path):
    project = world(tmp_path)
    rows = {r["origin_id"]: r["distance_m"] for r in buffers.rows(project, "water-protection-river")}
    assert rows == {"water:short": 50, "water:middle": 100, "water:long": 200}


def test_water_zone_excludes_the_water_itself(tmp_path):
    project = world(tmp_path)
    row = only(project, "coastal-strip", ["water:lake"])
    assert row["distance_m"] == 20 and row["geometry"].disjoint(rect(0, 4000, 300, 4300).buffer(-0.01)) is True
    assert row["geometry"].area == pytest.approx((340 * 340 - 300 * 300) - 4 * (20 ** 2) * (1 - 3.14159 / 4), rel=0.01)


def test_objects_are_named_or_matched(tmp_path):
    project = world(tmp_path)
    with pytest.raises(ValueError, match="назовите объекты через --from"):
        buffers.rows(project, "szz-fuel")                       # автозаправок в слое нет
    with pytest.raises(ValueError, match="назовите"):
        buffers.rows(project, "szz-industry-v")                 # правило без подбора
    with pytest.raises(ValueError, match="слоя parcels"):
        buffers.rows(project, "szz-industry-v", ["water:lake"])
    with pytest.raises(ValueError, match="объектов нет"):
        buffers.rows(project, "szz-industry-v", ["parcels:nope"])
    with pytest.raises(ValueError, match="правила буфера"):
        buffers.rows(project, "no-such-rule")


def test_save_checks_the_contract_and_repeats_bytes(tmp_path):
    project = world(tmp_path)
    path = buffers.save(project, buffers.rows(project, "szz-industry-v", [PARCEL, "parcels:p2"]))
    assert gpkg.check(path) == []
    first = path.read_bytes()
    buffers.save(project, buffers.rows(project, "szz-industry-v", [PARCEL, "parcels:p2"]))
    assert path.read_bytes() == first
    buffers.save(project, buffers.rows(project, "szz-boiler"))      # другое правило добавляет, не стирает
    assert sorted(gpkg.read(path)["buffers"]["object_id"]) == [
        "buffers:szz-boiler-poi_other-a1", "buffers:szz-industry-v-parcels-p1", "buffers:szz-industry-v-parcels-p2"]


def test_cli_buffer(tmp_path):
    project = world(tmp_path)
    assert run(["buffer", str(project.root / "project.yaml"), "--rule", "szz-industry-iv", "--from", PARCEL]) == 0
    assert project.part("analysis-buffers").exists()


def numbers(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from ([] if key == "below_km" else numbers(value))
    elif isinstance(node, list):
        for value in node:
            yield from numbers(value)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield node


def test_rules_have_no_distances():
    rules = params.load()["buffers"]["rules"]
    assert list(numbers(rules)) == []
    refs = {rule["norm"] for rule in rules.values() if "norm" in rule}
    refs |= {band["norm"] for rule in rules.values() for band in rule.get("norm_by_length", [])}
    assert refs <= {r["id"] for r in yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))}


def test_code_has_no_norm_numbers():
    allowed = {0, 1, 2, 120, 1000, 10000}      # 120 — ширина строки в yaml
    for name in ("buffers", "norms", "count"):
        tree = ast.parse((Path(buffers.__file__).parent / f"{name}.py").read_text(encoding="utf-8"))
        found = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
                 and not isinstance(n.value, bool)}
        assert found <= allowed, f"{name}.py: числа {sorted(found - allowed)}"


def test_synthetic_registry_is_marked_and_valid():
    schema = json.loads(paths.schema("fact").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    records = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    assert len(records) == 13
    for record in records:
        assert list(validator.iter_errors(record)) == []
        assert record["id"].startswith("NORM-9") and record["kind"] == "norm" and record["status"] == "check"
        assert record["sources"][0]["label"] == "синтетический образец, не норма"
        assert record["sources"][0]["doc_class"] == "C"
