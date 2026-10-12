"""Форма анкеты: общие поля базы наверху, поля картографа в разделе carto (К-045)."""

import pytest
import yaml

from cm_carto import project as project_mod

CRS = {"name": "EPSG:32637", "definition": "EPSG:32637"}
CARTO = {"extent": [486360, 5226270, 498490, 5236300], "geo": "geo", "inputs": "input", "language": "ru"}


def write(folder, **data):
    path = folder / "project.yaml"
    path.write_text(yaml.safe_dump({"name": "п", "crs": CRS, **data}, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("field", project_mod.CARTO_FIELDS)
def test_carto_field_at_top_level_is_error(tmp_path, field):
    path = write(tmp_path, carto=CARTO, **{field: CARTO.get(field, "x")})
    with pytest.raises(ValueError, match=rf"поле {field} — в разделе carto анкеты \(верхний уровень — поля базы, К-045\)"):
        project_mod.load(path)


def test_base_field_at_top_level_is_ignored(tmp_path):
    project = project_mod.load(write(tmp_path, carto=CARTO, stage="концепция"))
    assert project.data["stage"] == "концепция" and project.extent == (486360, 5226270, 498490, 5236300)


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
