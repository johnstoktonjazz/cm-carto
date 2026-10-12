"""Правила проекта (поле rules анкеты): имена слоев чертежей проекта без правки rules/ модуля."""

import dataclasses
import re

import ezdxf
import pytest
import yaml

from cm_carto import gpkg
from cm_carto.ingest import rules
from test_ingest_egrn import ZOUIT, kpt, ring, zone
from test_ingest_support import ingest, label_at, origin, project_in, square, write_source

CUSTOM = ["ПЛАН_КВАРТАЛ", "ПЛАН_НОМЕР", "ПЛАН_КРАСНАЯ", "ПЛАН_ШУМ"]
OWN_LAYERS = [
    {"target": "blocks", "drawing": ["ПЛАН_КВАРТАЛ*"], "geometry": "polygon", "kind": "квартал проекта", "name": "квартал {num}",
     "number": {"layers": ["ПЛАН_НОМЕР*"], "pattern": "[0-9]+", "attr": "num"}},
    {"target": "red_lines", "drawing": ["ПЛАН_КРАСНАЯ*"], "geometry": "line", "kind": "красная линия проекта",
     "attrs": {"state": "planned"}},
]


@pytest.fixture(autouse=True)
def fresh_cache():
    rules._applied.cache_clear()
    yield
    rules._applied.cache_clear()


def project_with_rules(tmp_path, files: dict[str, dict]):
    """Проект во временной папке с папкой правил rules/ и файлами правил проекта."""
    folder = tmp_path / "rules"
    folder.mkdir()
    for name, data in files.items():
        (folder / f"{name}.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    project = project_in(tmp_path)
    return dataclasses.replace(project, data={**project.data, "carto": {**project.carto, "rules": "rules"}}), folder


def custom_dxf(tmp_path, project, name="plan.dxf", passport="ppt", typical=True):
    """Чертеж с нетиповыми именами слоев проекта и, если нужно, одним типовым слоем «Кварталы»."""
    x, y = origin(project)
    doc = ezdxf.new("R2010")
    for layer in CUSTOM + ["Кварталы", "Номера_кварталов"]:
        doc.layers.add(layer)
    msp = doc.modelspace()
    msp.add_lwpolyline(square(x, y), close=True, dxfattribs={"layer": "ПЛАН_КВАРТАЛ"})
    label_at(msp, "5", (x + 50, y + 50), layer="ПЛАН_НОМЕР")
    msp.add_line((x, y + 300), (x + 100, y + 300), dxfattribs={"layer": "ПЛАН_КРАСНАЯ"})
    msp.add_line((x, y + 500), (x + 10, y + 500), dxfattribs={"layer": "ПЛАН_ШУМ"})
    if typical:
        msp.add_lwpolyline(square(x + 300, y), close=True, dxfattribs={"layer": "Кварталы"})
        label_at(msp, "9", (x + 350, y + 50))
    file = tmp_path / name
    doc.saveas(file)
    write_source(file, rules=passport)
    return file


def test_without_project_rules_custom_layers_are_unrecognized(tmp_path):
    project = project_in(tmp_path)
    out, report = ingest(project, custom_dxf(tmp_path, project))
    assert set(gpkg.read(out)) == {"blocks"}
    for layer in ("ПЛАН_КВАРТАЛ", "ПЛАН_КРАСНАЯ", "ПЛАН_ШУМ"):
        assert any(f"«{layer}»" in line for line in report.unrecognized)
    assert report.header[-1] == "Правила: rules/ppt.yaml"


def test_extends_adds_project_layers_and_keeps_module_rules(tmp_path):
    project, folder = project_with_rules(tmp_path, {"ppt": {"extends": "ppt", "layers": OWN_LAYERS, "ignore": ["ПЛАН_ШУМ"]}})
    out, report = ingest(project, custom_dxf(tmp_path, project))
    frames = gpkg.read(out)
    assert sorted(frames["blocks"].object_id) == ["blocks:5", "blocks:9"]      # проектный слой и модульный «Кварталы»
    assert set(frames["blocks"].kind) == {"квартал проекта", "квартал ППТ"}
    assert frames["red_lines"].state.tolist() == ["planned"]
    assert report.unrecognized == [] and any("«ПЛАН_ШУМ»" in line for line in report.ignored)
    assert report.header[-2:] == ["Правила проекта: rules/ppt.yaml", "Правила модуля: rules/ppt.yaml"]
    assert gpkg.check(out) == []


def test_project_rule_wins_over_module_rule(tmp_path):
    """Шаблон проекта совпал с модульным «Кварталы*»: первое совпадение — у проекта."""
    own = [{"target": "sites", "drawing": ["Кварталы*"], "geometry": "polygon", "kind": "зона проекта"}]
    project, _ = project_with_rules(tmp_path, {"ppt": {"extends": "ppt", "layers": own}})
    out, _ = ingest(project, custom_dxf(tmp_path, project))
    frames = gpkg.read(out)
    assert set(frames) == {"sites"} and frames["sites"].kind.tolist() == ["зона проекта"]


def test_standalone_project_rules_without_extends(tmp_path):
    standalone = {"type": "dxf", "status": "concept", "tolerance_m": 0.5, "arc_tolerance_m": 0.05, "ignore": ["0"],
                  "layers": OWN_LAYERS}
    project, _ = project_with_rules(tmp_path, {"layout": standalone})
    out, report = ingest(project, custom_dxf(tmp_path, project, passport="layout"))
    frames = gpkg.read(out)
    assert sorted(frames["blocks"].object_id) == ["blocks:5"] and set(frames["blocks"].status) == {"concept"}
    assert any("«Кварталы»" in line for line in report.unrecognized)          # типовых правил в самостоятельном файле нет
    assert report.header[-1] == "Правила проекта: rules/layout.yaml"


def test_project_layer_outside_contract_names_project_file(tmp_path):
    own = [{"target": "no_such_layer", "drawing": ["ПЛАН*"], "geometry": "polygon", "kind": "x"}]
    project, folder = project_with_rules(tmp_path, {"ppt": {"extends": "ppt", "layers": own}})
    with pytest.raises(ValueError, match=re.escape(str(folder / "ppt.yaml")) + r".*\n.*no_such_layer нет в контракте"):
        ingest(project, custom_dxf(tmp_path, project))
    with pytest.raises(ValueError, match="rules/ppt.yaml: правила не проходят"):
        rules.load("ppt", project)


def test_extends_unknown_module_rules_or_other_type_is_refused(tmp_path):
    project, folder = project_with_rules(tmp_path, {"a": {"extends": "no_such_rules"}, "b": {"extends": "egrn", "type": "dxf"}})
    with pytest.raises(ValueError, match="в модуле нет файла"):
        rules.load("a", project)
    with pytest.raises(ValueError, match="а правила модуля egrn.yaml типа egrn"):
        rules.load("b", project)


def test_changing_project_rules_changes_input_hash(tmp_path):
    project, folder = project_with_rules(tmp_path, {"ppt": {"extends": "ppt", "layers": OWN_LAYERS}})
    file = custom_dxf(tmp_path, project)
    first = gpkg.meta(ingest(project, file)[0])["input_hash"]
    assert gpkg.meta(ingest(project, file)[0])["input_hash"] == first
    (folder / "ppt.yaml").write_text(yaml.safe_dump({"extends": "ppt", "layers": OWN_LAYERS[:1]}, allow_unicode=True), encoding="utf-8")
    rules._applied.cache_clear()
    assert gpkg.meta(ingest(project, file)[0])["input_hash"] != first


def test_project_without_rules_folder_uses_module_rules(tmp_path):
    project = project_in(tmp_path)
    assert project.rules is None and rules.load("ppt", project) is rules.load("ppt")


def test_egrn_project_zones_and_zouit_come_before_module(tmp_path):
    own = {"extends": "egrn",
           "zouit_types": [{"contains": ["затопления"], "zouit_type": "water_protection", "kind": "зона затопления"}],
           "pzz": {"zones": [{"zone": "Р", "index": ["Л"]}, {"zone": "Ж", "index": ["ОЖ"]}]}}
    project, _ = project_with_rules(tmp_path, {"egrn": own})
    x, y = origin(project)
    zones = (zone("00:00-1.1", ZOUIT, "Зона затопления", ring(x, y))
             + zone("00:00-2.1", "Территориальная зона", "Зона лесов", ring(x + 200, y), index="Л-1")
             + zone("00:00-2.2", "Территориальная зона", "Зона застройки", ring(x + 400, y), index="ОЖ-2")
             + zone("00:00-2.3", "Территориальная зона", "Производственная зона", ring(x + 600, y)))
    file = tmp_path / "kpt.xml"
    file.write_text(kpt(project, zones=zones), encoding="utf-8")
    out, report = ingest(project, file)
    frames = gpkg.read(out)
    assert frames["zouit"].zouit_type.tolist() == ["water_protection"]
    pzz = frames["zones_pzz"].set_index("object_id").zone
    assert pzz["zones_pzz:00-00-2.1"] == "Р"        # индекса нет в модульных правилах
    assert pzz["zones_pzz:00-00-2.2"] == "Ж"        # проектный «ОЖ» раньше модульного
    assert pzz["zones_pzz:00-00-2.3"] == "П"        # модульные правила работают
    assert report.header[2:4] == ["Правила проекта: rules/egrn.yaml", "Правила модуля: rules/egrn.yaml"]
