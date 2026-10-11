"""Рецепты MAPS_USAGE.md (examples/recipes/*.yaml): описание проходит схему карты, место, ширина легенды, роли, слои
геопакета и геометрия слоев согласованы со стандартом верстки; в шаблонах нет проектных значений, только <…>."""

import re
from pathlib import Path

import jsonschema
import pytest
import yaml

from cm_carto import gpkg, tokens
from cm_carto.render import spec as spec_file

RECIPES = sorted((Path(__file__).resolve().parent.parent / "examples" / "recipes").glob("*.yaml"))


def test_recipes_exist():
    assert [p.stem for p in RECIPES] == ["school-kindergarten-provision", "territory-restrictions", "transport-access"]


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: p.stem)
def test_recipe_follows_schema_and_standard(path):
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = [e.message for e in jsonschema.Draft202012Validator(spec_file.schema()).iter_errors(raw)]
    assert not errors
    std = tokens.load("map")
    assert raw["place"] in std["places"]
    assert raw["legend"] == "inside" or raw["legend"] in std["legend"]["widths"]
    for key, options in raw["layers"].items():
        assert key in spec_file.order(std)
        layer = (options or {}).get("layer", key)
        assert spec_file.GEOMETRY[gpkg.layers()[layer][0]] == spec_file.role_spec(std, key)["geometry"], key


@pytest.mark.parametrize("path", RECIPES, ids=lambda p: p.stem)
def test_recipe_has_only_placeholders(path):
    text = path.read_text(encoding="utf-8")
    ids = re.findall(r"[a-z_]+:[^\s,'\"}]+", "\n".join(line for line in text.splitlines() if not line.startswith("#")))
    assert all("<" in i for i in ids if i.split(":")[0] in gpkg.layers()), ids
