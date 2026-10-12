"""Форма анкеты (база, cm_core 0.4.2): project.name и territory.crs — поля базы, поля картографа — в разделе carto (К-045)."""

import pytest
import yaml

from cm_carto import project as project_mod

BASE = {"project": {"name": "п"}, "territory": {"crs": {"name": "EPSG:32637", "definition": "EPSG:32637"}}}
CARTO = {"extent": [486360, 5226270, 498490, 5236300], "geo": "geo", "inputs": "input", "language": "ru"}


def write(folder, **data):
    path = folder / "project.yaml"
    path.write_text(yaml.safe_dump({**BASE, **data}, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("field", project_mod.CARTO_FIELDS)
def test_carto_field_at_top_level_is_error(tmp_path, field):
    path = write(tmp_path, carto=CARTO, **{field: CARTO.get(field, "x")})
    with pytest.raises(ValueError, match=rf"поле {field} — в разделе carto анкеты \(верхний уровень — поля базы, К-045\)"):
        project_mod.load(path)


def test_foreign_fields_and_sections_are_ignored(tmp_path):
    """Другие поля project и territory и другие разделы базы картограф не знает и не трогает."""
    base = {"project": {**BASE["project"], "stage": "концепция"},
            "territory": {**BASE["territory"], "region": "Ростовская область"}}
    project = project_mod.load(write(tmp_path, carto=CARTO, **base, economics={"budget": 1}, schedule=[]))
    assert project.data["project"]["stage"] == "концепция" and project.data["territory"]["region"] == "Ростовская область"
    assert project.data["economics"] == {"budget": 1} and project.extent == (486360, 5226270, 498490, 5236300)
    assert project.name == "п" and project.crs_name == "EPSG:32637" and project.crs.to_epsg() == 32637


@pytest.mark.parametrize("field, value", [("name", "п"), ("crs", {"name": "EPSG:32637", "definition": "EPSG:32637"})])
def test_old_form_is_error_with_hint(tmp_path, field, value):
    """Прежние верхнеуровневые name и crs не читаются, даже когда новая форма тоже есть."""
    path = write(tmp_path, carto=CARTO, **{field: value})
    with pytest.raises(ValueError, match=rf"поле {field} на верхнем уровне — имя проекта — project\.name, "
                                         r"система координат — territory\.crs \(анкета базы, cm_core 0\.4\.2\)"):
        project_mod.load(path)


@pytest.mark.parametrize("field", ["project.name", "territory.crs.name", "territory.crs.definition"])
def test_base_fields_are_required(tmp_path, field):
    section, *path = field.split(".")
    base = {"project": dict(BASE["project"]), "territory": {"crs": dict(BASE["territory"]["crs"])}}
    node = base[section]
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    with pytest.raises(ValueError, match=rf"нет поля {field.replace('.', r'\.')}"):
        project_mod.load(write(tmp_path, carto=CARTO, **base))


@pytest.mark.parametrize("missing", ["project", "territory"])
def test_base_section_is_required(tmp_path, missing):
    data = {k: v for k, v in BASE.items() if k != missing}
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({**data, "carto": CARTO}, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="нет поля"):
        project_mod.load(path)


def test_anketa_without_carto_is_error(tmp_path):
    with pytest.raises(ValueError, match="нет поля carto"):
        project_mod.load(write(tmp_path))


@pytest.mark.parametrize("field", ["extent", "geo"])
def test_carto_requires_extent_and_geo(tmp_path, field):
    carto = {k: v for k, v in CARTO.items() if k != field}
    with pytest.raises(ValueError, match=f"нет поля carto.{field}"):
        project_mod.load(write(tmp_path, carto=carto))


def test_extent_must_be_ordered(tmp_path):
    with pytest.raises(ValueError, match="carto.extent — это"):
        project_mod.load(write(tmp_path, carto={**CARTO, "extent": [10, 0, 5, 1]}))
