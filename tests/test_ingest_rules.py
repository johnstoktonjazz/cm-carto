"""Правила раскладки: проверка по схеме и контракту при загрузке; команда ingest."""

import argparse

import pytest
import yaml

from cm_carto import gpkg, paths
from cm_carto.ingest import cli, rules
from test_ingest_support import SAMPLES, project_in, EXAMPLE


@pytest.fixture
def custom_rules(tmp_path, monkeypatch):
    """Правила из временной папки: подменяем путь загрузки и сбрасываем кеш."""
    def make(name, base, change):
        data = yaml.safe_load(rules.path(base).read_text(encoding="utf-8"))
        change(data)
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    original = rules.path
    monkeypatch.setattr(rules, "path", lambda n: tmp_path / f"{n}.yaml" if (tmp_path / f"{n}.yaml").exists() else original(n))
    rules._applied.cache_clear()
    yield make
    rules._applied.cache_clear()


@pytest.mark.parametrize("name", ["ppt", "egrn", "vector"])
def test_repository_rules_are_valid(name):
    assert rules.load(name)["type"] in {"dxf", "egrn", "vector"}


def test_ppt_rules_cover_contract_layers():
    targets = {r["target"] for r in rules.load("ppt")["layers"]}
    assert targets == {"boundary_planning", "red_lines", "blocks", "sites"} <= set(gpkg.layers())


def test_unknown_layer_is_refused(custom_rules):
    custom_rules("bad", "ppt", lambda d: d["layers"][0].update(target="no_such_layer"))
    with pytest.raises(ValueError, match="no_such_layer нет в контракте"):
        rules.load("bad")


def test_unknown_attribute_is_refused(custom_rules):
    custom_rules("bad", "ppt", lambda d: d["layers"][1]["attrs"].update(colour="red"))
    with pytest.raises(ValueError, match="red_lines: атрибута colour нет в контракте"):
        rules.load("bad")


def test_geometry_must_match_contract(custom_rules):
    custom_rules("bad", "ppt", lambda d: d["layers"][0].update(geometry="line"))
    with pytest.raises(ValueError, match="слой контракта MultiPolygon, правило — line"):
        rules.load("bad")


def test_schema_violation_is_refused(custom_rules):
    custom_rules("bad", "ppt", lambda d: d.pop("tolerance_m"))
    with pytest.raises(ValueError, match="tolerance_m"):
        rules.load("bad")


@pytest.mark.parametrize("base", ["ppt", "vector"])
def test_status_in_dxf_and_vector_rules_is_refused(custom_rules, base):
    custom_rules("bad", base, lambda d: d.update(status="official"))
    with pytest.raises(ValueError, match="status"):
        rules.load("bad")


@pytest.mark.parametrize("name", ["ppt", "vector"])
def test_repository_dxf_and_vector_rules_have_no_status(name):
    assert "status" not in rules.load(name)
    assert rules.load("egrn")["status"] == "cadastral"       # природа выписки — в правилах


def test_zouit_type_must_be_from_contract(custom_rules):
    custom_rules("bad", "egrn", lambda d: d["zouit_types"][0].update(zouit_type="radioactive"))
    with pytest.raises(ValueError, match="zouit_type radioactive не из перечня"):
        rules.load("bad")


def test_unknown_layer_is_a_warning_not_an_error(tmp_path, capsys):
    """Слой чертежа без правила: строка предупреждения с именем и числом объектов, часть записана, код 0."""
    project_path = tmp_path / "project.yaml"
    project_path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["ingest", str(project_path), str(SAMPLES / "dxf" / "ppt-sample.dxf")])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "предупреждение: слой «Инженерные_сети» не распознан, 3 объектов — в ignore или правило проекта" in out.splitlines()
    assert (tmp_path / "geo" / "parts" / "ingest-ppt-sample.gpkg").exists()
    assert "слой «Инженерные_сети»: нет правила раскладки" in (tmp_path / "geo/parts/ingest-ppt-sample.report.md").read_text(encoding="utf-8")


def test_command_ingests_folder_and_file(tmp_path, capsys):
    project_path = tmp_path / "project.yaml"
    project_path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers(dest="command", required=True))
    for target in (SAMPLES / "dxf", SAMPLES / "kpt" / "kpt-sample.xml"):
        args = parser.parse_args(["ingest", str(project_path), str(target)])
        assert args.func(args) == 0
    parts = tmp_path / "geo" / "parts"
    assert sorted(p.name for p in parts.iterdir()) == ["ingest-kpt-sample.gpkg", "ingest-kpt-sample.report.md",
                                                       "ingest-ppt-sample.gpkg", "ingest-ppt-sample.report.md"]
    assert "ppt-sample.dxf: объектов 40, не распознано 1" in capsys.readouterr().out
    merged = gpkg.merge(sorted(parts.glob("*.gpkg")), tmp_path / "project.gpkg", "тест", project_in(tmp_path).crs, "EPSG:32637", None)
    assert gpkg.check(merged) == []                 # части разных входов сливаются без повтора object_id
