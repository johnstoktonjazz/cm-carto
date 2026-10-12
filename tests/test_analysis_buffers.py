"""Буферы по норме реестра: расстояние из записи NRM, ошибка без записи, норма по классу опасности, категории дороги
(в том числе по допущению ASM) и длине водотока из атрибутов автора, лицензия только у внешних источников, статус и
подпись, повтор байт в байт, в правилах и коде нет чисел норм."""

import ast
import copy
import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from analysis_fixtures import run
from buffer_fixtures import (ASSUMPTIONS, REGISTRY, edit_registry, parcel, poi, project_with_registry, rect, road,
                            water, write_layers)

from cm_carto import gpkg, paths
from cm_carto.analysis import buffers, norms, params

PARCEL = "parcels:p1"


def world(tmp_path):
    project = project_with_registry(tmp_path)
    write_layers(project, parcels=[parcel("p1", rect(0, 0, 100, 100), hazard_class="V"),
                                   parcel("p2", rect(500, 0, 600, 100), hazard_class="IV")],
                 water=[water("short", rect(0, 1000, 5000, 1020), "river", 5),
                        water("middle", rect(0, 2000, 20000, 2020), "river", 20),
                        water("long", rect(0, 3000, 80000, 3020), "river", 80),
                        water("cut", rect(0, 5000, 3000, 5020), "river", 120),   # длиннее охвата: геометрия обрезана
                        water("lake", rect(0, 4000, 300, 4300), "lake")],
                 poi_other=[poi("a1", 0, 0, "котельная"), poi("a2", 900, 0, "аптека"),
                            poi("fac", 2000, 0, "предприятие", "IV")],
                 roads=[road("cat", 100, "local", "IБ"), road("main", 200, "main"), road("dist", 300, "district"),
                        road("loc", 400, "local")])
    return project


def only(project, rule, origin=None):
    (row,) = buffers.rows(project, rule, origin)
    return row


def test_distance_comes_from_the_norm_and_follows_the_registry(tmp_path):
    project = world(tmp_path)
    row = only(project, "szz-industry", [PARCEL])
    assert row["distance_m"] == 50 and row["fact_ids"] == ["NRM-90002"] and row["origin_id"] == PARCEL   # p1: класс V
    minx, miny, maxx, maxy = row["geometry"].bounds
    assert (maxx - minx) == pytest.approx(200)
    edit_registry(project, "NRM-90002", value=75)       # код и правила те же, норма другая
    wider = only(project, "szz-industry", [PARCEL])
    assert wider["distance_m"] == 75 and wider["geometry"].bounds[2] - wider["geometry"].bounds[0] == pytest.approx(250)
    assert "75 м по NRM-90002" in wider["source_label"]


def test_status_and_label_follow_the_source(tmp_path):
    project = world(tmp_path)
    cadastral = only(project, "szz-industry", [PARCEL])
    assert cadastral["status"] == "calc" and cadastral["source_label"] == "буфер 50 м по NRM-90002, от тестовая КПТ"
    osm = only(project, "szz-boiler")
    assert cadastral["fact_ids"] == ["NRM-90002"]
    assert osm["status"] == "calc" and osm["source_license"] == "ODbL 1.0" and osm["fact_ids"] == ["NRM-90001"]
    assert osm["source_label"].startswith("буфер 50 м по NRM-90001, от тестовые улицы")
    assert osm["buffer_type"] == "sanitary"


def test_missing_norm_is_an_error(tmp_path):
    project = world(tmp_path)
    edit_registry(project, "NRM-90002", drop=True)
    with pytest.raises(ValueError, match="NRM-90002.*нет в реестре"):
        buffers.rows(project, "szz-industry", [PARCEL])


def test_norm_without_meters_is_an_error(tmp_path):
    project = world(tmp_path)
    edit_registry(project, "NRM-90002", value="по расчету")
    with pytest.raises(ValueError, match="число в метрах"):
        buffers.rows(project, "szz-industry", [PARCEL])


def test_bad_registry_record_is_refused(tmp_path):
    project = world(tmp_path)
    path = project.root / "registry" / "norms.yaml"
    records = yaml.safe_load(path.read_text(encoding="utf-8"))
    del records[0]["sources"]
    path.write_text(yaml.safe_dump(records, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="реестр не проходит"):
        norms.load(project)


def test_norm_by_river_length_attribute_not_geometry(tmp_path):
    project = world(tmp_path)
    rows = {r["origin_id"]: r["distance_m"] for r in buffers.rows(project, "water-protection-river")}
    assert rows == {"water:short": 50, "water:middle": 100, "water:long": 200, "water:cut": 200}


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
        buffers.rows(project, "szz-industry")                 # правило без подбора
    with pytest.raises(ValueError, match="слоя parcels"):
        buffers.rows(project, "szz-industry", ["water:lake"])
    with pytest.raises(ValueError, match="объектов нет"):
        buffers.rows(project, "szz-industry", ["parcels:nope"])
    with pytest.raises(ValueError, match="правила буфера"):
        buffers.rows(project, "no-such-rule")


def test_save_checks_the_contract_and_repeats_bytes(tmp_path):
    project = world(tmp_path)
    path = buffers.save(project, buffers.rows(project, "szz-industry", [PARCEL, "parcels:p2"]))
    assert gpkg.check(path) == []
    first = path.read_bytes()
    buffers.save(project, buffers.rows(project, "szz-industry", [PARCEL, "parcels:p2"]))
    assert path.read_bytes() == first
    buffers.save(project, buffers.rows(project, "szz-boiler"))      # другое правило добавляет, не стирает
    assert sorted(gpkg.read(path)["buffers"]["object_id"]) == [
        "buffers:szz-boiler-poi_other-a1", "buffers:szz-industry-parcels-p1", "buffers:szz-industry-parcels-p2"]


def test_cli_buffer(tmp_path):
    project = world(tmp_path)
    assert run(["buffer", str(project.root / "project.yaml"), "--rule", "szz-industry", "--from", PARCEL]) == 0
    assert project.part("analysis-buffers").exists()


def test_norm_by_hazard_class_of_parcels_and_poi(tmp_path):
    project = world(tmp_path)
    got = {r["origin_id"]: r for r in buffers.rows(project, "szz-industry", ["parcels:p1", "parcels:p2"])}
    assert got["parcels:p1"]["distance_m"] == 50 and got["parcels:p1"]["fact_ids"] == ["NRM-90002"]
    assert got["parcels:p2"]["distance_m"] == 100 and got["parcels:p2"]["fact_ids"] == ["NRM-90003"]
    assert got["parcels:p2"]["kind"] == "санитарно-защитная зона предприятия класса IV"
    assert got["parcels:p1"]["kind"].endswith("класса V")
    fac = only(project, "szz-industry-poi", ["poi_other:fac"])
    assert fac["distance_m"] == 100 and fac["fact_ids"] == ["NRM-90003"] and fac["source_license"] == "ODbL 1.0"


def test_object_without_hazard_class_is_an_error_with_ids(tmp_path):
    project = world(tmp_path)
    write_layers(project, parcels=[parcel("p1", rect(0, 0, 100, 100), hazard_class="V"),
                                   parcel("n1", rect(300, 0, 400, 100)), parcel("n2", rect(700, 0, 800, 100))],
                 poi_other=[poi("a2", 900, 0, "аптека")])
    with pytest.raises(ValueError, match=r"класс опасности вводит автор проекта: атрибут hazard_class.*n1.*n2") as error:
        buffers.rows(project, "szz-industry", ["parcels:p1", "parcels:n1", "parcels:n2"])
    assert "parcels:p1" not in str(error.value)
    with pytest.raises(ValueError, match="poi_other:a2"):                  # у аптеки класса нет
        buffers.rows(project, "szz-industry-poi", ["poi_other:a2"])


def test_hazard_class_without_a_norm_is_an_error(tmp_path):
    project = world(tmp_path)
    write_layers(project, parcels=[parcel("i1", rect(0, 0, 100, 100), hazard_class="III")])
    with pytest.raises(ValueError, match=r"нет нормы для значения hazard_class = III \(есть: IV, V\).*parcels:i1"):
        buffers.rows(project, "szz-industry", ["parcels:i1"])


def test_roadside_by_road_category(tmp_path):
    project = world(tmp_path)
    row = only(project, "roadside", ["roads:cat"])                           # категория IБ введена автором
    assert row["distance_m"] == 75 and row["fact_ids"] == ["NRM-90009"]
    assert row["kind"] == "придорожная полоса дороги категории IБ"


def test_roadside_by_street_class_goes_through_the_assumption(tmp_path):
    project = world(tmp_path)
    row = only(project, "roadside", ["roads:main"])                          # категории нет: main -> II по ASM-9003
    assert row["distance_m"] == 50 and row["fact_ids"] == ["NRM-90010", "ASM-9003"]
    assert row["kind"].endswith("категории II")
    both = {r["origin_id"]: r["fact_ids"] for r in buffers.rows(project, "roadside", ["roads:cat", "roads:dist"])}
    assert both == {"roads:cat": ["NRM-90009"], "roads:dist": ["NRM-90011", "ASM-9003"]}
    edit_registry(project, "ASM-9003", value="express: IБ; main: III; district: III", file="assumptions.yaml")
    assert only(project, "roadside", ["roads:main"])["fact_ids"] == ["NRM-90011", "ASM-9003"]   # таблица — в данных


def test_roadside_errors_name_the_assumption_and_ids(tmp_path):
    project = world(tmp_path)
    with pytest.raises(ValueError, match=r"допущении ASM-9003 нет соответствия для road_class = local.*roads:loc"):
        buffers.rows(project, "roadside", ["roads:loc"])
    edit_registry(project, "ASM-9003", value="express IБ", file="assumptions.yaml")
    with pytest.raises(ValueError, match="ASM-9003: в value ждем пары"):
        buffers.rows(project, "roadside", ["roads:main"])
    edit_registry(project, "ASM-9003", drop=True, file="assumptions.yaml")
    with pytest.raises(ValueError, match="допущения ASM-9003 нет в реестре"):
        buffers.rows(project, "roadside", ["roads:main"])
    assert only(project, "roadside", ["roads:cat"])["fact_ids"] == ["NRM-90009"]   # с категорией допущение не нужно


def test_river_without_length_attribute_is_an_error_with_ids(tmp_path):
    project = project_with_registry(tmp_path)
    write_layers(project, water=[water("known", rect(0, 0, 5000, 20), "river", 30),
                                 water("unknown", rect(0, 1000, 5000, 1020), "river"),
                                 water("unknown2", rect(0, 2000, 5000, 2020), "river")])
    with pytest.raises(ValueError, match=r"длина водотока вводит автор проекта: атрибут length_km.*unknown.*unknown2") as error:
        buffers.rows(project, "water-protection-river")
    assert "water:known" not in str(error.value)


def test_license_only_from_external_source(tmp_path):
    project = world(tmp_path)
    kpt = only(project, "szz-industry", [PARCEL])
    osm = only(project, "szz-boiler")
    assert "source_license" not in kpt and osm["source_license"] == "ODbL 1.0"
    path = buffers.save(project, [kpt, osm])
    assert gpkg.check(path) == []
    saved = gpkg.read(path)["buffers"].set_index("object_id")["source_license"]
    assert saved.isna()["buffers:szz-industry-parcels-p1"] and saved["buffers:szz-boiler-poi_other-a1"] == "ODbL 1.0"


def test_inapplicable_norm_is_an_error_and_unchecked_is_allowed(tmp_path):
    project = world(tmp_path)
    assert only(project, "szz-industry", [PARCEL])["distance_m"] == 50      # unchecked в синтетическом реестре
    edit_registry(project, "NRM-90002", applicability="not_applicable")
    with pytest.raises(ValueError, match="NRM-90002.*неприменима к объекту проекта"):
        buffers.rows(project, "szz-industry", [PARCEL])
    edit_registry(project, "NRM-90002", applicability="applicable")
    assert only(project, "szz-industry", [PARCEL])["distance_m"] == 50


def test_registry_must_pass_fact_1_1_0(tmp_path):
    project = world(tmp_path)
    path = project.root / "registry" / "norms.yaml"
    records = yaml.safe_load(path.read_text(encoding="utf-8"))
    records[0]["applicability"] = "строкой, как в fact 1.0.0"
    records[1]["id"] = "NR-90002"
    path.write_text(yaml.safe_dump(records, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="реестр не проходит"):
        norms.load(project)


def numbers(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from ([] if key == "below_km" else numbers(value))
    elif isinstance(node, list):
        for value in node:
            yield from numbers(value)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield node


def references(rules: dict) -> tuple[set, set]:
    """Записи NRM и ASM, на которые ссылаются правила."""
    norms_, assumptions = set(), set()
    for rule in rules.values():
        if "norm" in rule:
            norms_.add(rule["norm"])
        if "norm_by_attr" in rule:
            norms_ |= set(rule["norm_by_attr"]["norms"].values())
            if "if_missing" in rule["norm_by_attr"]:
                assumptions.add(rule["norm_by_attr"]["if_missing"]["assumption_id"])
        if "norm_by_length" in rule:
            norms_ |= {band["norm"] for band in rule["norm_by_length"]["bands"]}
    return norms_, assumptions


def test_rules_have_no_distances():
    rules = params.load()["buffers"]["rules"]
    assert list(numbers(rules)) == []
    norm_ids, assumption_ids = references(rules)
    assert norm_ids <= {r["id"] for r in yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))}
    assert assumption_ids <= {r["id"] for r in yaml.safe_load(ASSUMPTIONS.read_text(encoding="utf-8"))}
    assert "own_data_license" not in params.load()["buffers"]


def test_rule_attributes_and_categories_are_in_the_geopackage_contract():
    """Атрибуты автора и их значения в правилах — те, что перечислены в контракте geopackage."""
    layers = gpkg.layers()
    for name, rule in params.load()["buffers"]["rules"].items():
        spec = rule.get("norm_by_attr") or rule.get("norm_by_length")
        if spec is None:
            continue
        attrs = layers[rule["layer"]][1]
        assert spec["attr"] in attrs, f"{name}: в слое {rule['layer']} нет атрибута {spec['attr']}"
        if "norm_by_attr" in rule:
            assert set(spec["norms"]) <= set(attrs[spec["attr"]]["enum"]), name
            if "if_missing" in spec:
                assert spec["if_missing"]["attr"] in attrs, name


def test_asm_table_is_a_valid_correspondence_to_contract_values():
    assumptions = {r["id"]: r for r in yaml.safe_load(ASSUMPTIONS.read_text(encoding="utf-8"))}
    table = norms.correspondence(assumptions, "ASM-9003")
    roads = gpkg.layers()["roads"][1]
    assert set(table) <= set(roads["road_class"]["enum"]) and set(table.values()) <= set(roads["road_category"]["enum"])


def test_code_has_no_norm_numbers():
    allowed = {0, 1, 2, 120, 1000, 10000}      # 120 — ширина строки в yaml
    for name in ("buffers", "norms", "count"):
        tree = ast.parse((Path(buffers.__file__).parent / f"{name}.py").read_text(encoding="utf-8"))
        found = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
                 and not isinstance(n.value, bool)}
        assert found <= allowed, f"{name}.py: числа {sorted(found - allowed)}"


def test_synthetic_registries_pass_fact_1_1_0():
    schema = json.loads(paths.schema("fact").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    records = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    assert len(records) == 13
    for record in records:
        assert list(validator.iter_errors(record)) == []
        assert record["id"].startswith("NRM-9") and record["kind"] == "norm" and record["status"] == "check"
        assert record["applicability"]["status"] == "unchecked" and record["applicability"]["condition"]
        assert record["sources"][0]["label"] == "синтетический образец, не норма"
        assert record["sources"][0]["doc_class"] == "C"
    for record in yaml.safe_load(ASSUMPTIONS.read_text(encoding="utf-8")):
        assert list(validator.iter_errors(record)) == [] and record["id"].startswith("ASM-9")


def test_rules_accept_only_nrm():
    """Переход закончен: правила анализа принимают нормы только `NRM-NNNNN`; NORM- и иной префикс — ошибка схемы."""
    schema = json.loads((paths.ROOT / "schema" / "analysis.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for place in ("norm", "norm_by_attr", "norm_by_length"):
        for norm, valid in (("NRM-90001", True), ("NORM-90001", False), ("NR-90001", False)):
            rules = copy.deepcopy(yaml.safe_load((paths.ROOT / "rules" / "analysis.yaml").read_text(encoding="utf-8")))
            rule = next(r for r in rules["buffers"]["rules"].values() if place in r)
            if place == "norm":
                rule["norm"] = norm
            elif place == "norm_by_attr":
                rule["norm_by_attr"]["norms"] = {"IV": norm}
            else:
                rule["norm_by_length"]["bands"][0]["norm"] = norm
            assert (list(validator.iter_errors(rules)) == []) is valid, (place, norm)


def test_no_transitional_prefix_in_the_analysis_module():
    texts = [(paths.ROOT / "rules" / "analysis.yaml")] + sorted((Path(buffers.__file__).parent).glob("*.py"))
    assert [str(p) for p in texts if "NORM" in p.read_text(encoding="utf-8")] == []


def test_hazard_class_from_author_attributes_file(tmp_path):
    """Класс опасности вводит автор в файле carto.attributes: участок части без атрибута получает норму класса."""
    project = project_with_registry(tmp_path)
    write_layers(project, parcels=[parcel("a1", rect(0, 0, 100, 100))])
    (tmp_path / "attributes.yaml").write_text("parcels:a1: {hazard_class: IV}\n", encoding="utf-8")
    row = only(project, "szz-industry", ["parcels:a1"])
    assert row["distance_m"] == 100 and row["fact_ids"] == ["NRM-90003"]
