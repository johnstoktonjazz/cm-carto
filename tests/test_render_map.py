"""Рендер карты по описанию на синтетическом геопакете: размер под место, provenance по контракту image,
повтор байт в байт, отбор where, ошибки описания и печати, смена токена верстки меняет карту без правок кода."""

import json
import os
import shutil
import subprocess
import sys

import geopandas as gpd
import jsonschema
import pytest
import yaml
from PIL import Image
from pyproj import CRS
from shapely.geometry import LineString, Point, box

from cm_carto import gpkg, paths, tokens
from cm_carto.render import build

CRS_NAME = "EPSG:32637"
X, Y = 500000, 5000000
COMMON = {"fact_ids": [], "source_date": "2026-10-01"}
OFFICIAL = {**COMMON, "status": "official", "source_label": "синтетический чертеж, лист 1", "source_date": "2021-06-01"}
OSM = {**COMMON, "status": "reference", "source_label": "синтетическая выгрузка OSM", "source_license": "ODbL 1.0"}


def rows(layer: str, items: list[tuple[str, object, dict]]) -> gpd.GeoDataFrame:
    data = [{"object_id": f"{layer}:{key}", "kind": layer, **attrs} for key, _, attrs in items]
    return gpd.GeoDataFrame(data, geometry=[g for _, g, _ in items], crs=CRS.from_user_input(CRS_NAME))


def frames() -> dict[str, gpd.GeoDataFrame]:
    blocks = [(f"b{i}{j}", box(X + i * 220, Y + j * 220, X + i * 220 + 200, Y + j * 220 + 200), {**OFFICIAL, "num": f"{i}{j}"})
              for i in range(-3, 3) for j in range(-3, 3)]
    return {
        "blocks": rows("blocks", blocks),
        "boundary_planning": rows("boundary_planning", [("t", box(X - 200, Y - 200, X + 190, Y + 190), OFFICIAL)]),
        "roads": rows("roads", [
            ("main", LineString([(X - 800, Y - 10), (X + 800, Y - 10)]), {**OSM, "road_class": "main", "name": "Главная улица"}),
            ("local", LineString([(X - 10, Y - 800), (X - 10, Y + 800)]), {**OSM, "road_class": "local"}),
        ]),
        "poi_education": rows("poi_education", [
            ("s1", Point(X + 100, Y + 100), {**OSM, "poi_type": "school", "name": "Школа 1"}),
            ("s2", Point(X - 300, Y + 250), {**OSM, "poi_type": "school", "name": "Школа 2"}),
            ("k1", Point(X + 250, Y - 300), {**OSM, "poi_type": "kindergarten", "name": "Сад 1",
                                             "source_label": "другой источник садов"}),
            ("far", Point(X + 9000, Y + 9000), {**OSM, "poi_type": "school", "name": "Далекая школа",
                                                "source_label": "источник вне охвата"}),
        ]),
        "zouit": rows("zouit", [("z1", box(X + 300, Y + 300, X + 600, Y + 600), {**OFFICIAL, "zouit_type": "sanitary"})]),
    }


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("project")
    (root / "project.yaml").write_text(yaml.safe_dump(
        {"name": "синтетический проект", "crs": {"name": CRS_NAME, "definition": CRS_NAME},
         "extent_wgs84": [0, 0, 1, 1], "geo": "geo"}, allow_unicode=True), encoding="utf-8")
    gpkg.write(root / "geo" / "project.gpkg", frames(), "синтетический проект", CRS.from_user_input(CRS_NAME),
               CRS_NAME, "sha256:" + "0" * 64)
    return root


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "CACHE", tmp_path / "cache")


def describe(folder, name="map", **changes) -> object:
    spec = {"place": "square_small", "project": "project.yaml", "center": [X, Y], "scale": 10000, "legend": 91,
            "layers": {"point_1": {"layer": "poi_education", "where": "poi_type = 'school'", "title": "Школы"},
                       "zone_sanitary": {"layer": "zouit", "where": "zouit_type = 'sanitary'"},
                       "territory": {"layer": "boundary_planning", "title": "Граница проекта планировки"},
                       "road_main": {"layer": "roads", "where": "road_class = 'main'"},
                       "blocks": {}},
            "title": "Школы у границы", "out": f"maps/{name}.png"}
    spec.update(changes)
    path = folder / f"{name}.yaml"
    path.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    return path


def provenance(png) -> dict:
    return json.loads(png.with_name(png.stem + ".provenance.json").read_text(encoding="utf-8"))


def test_png_size_matches_place(project):
    png = build.render(describe(project))[1]
    width, height = tokens.place_size_mm("square_small")
    image = Image.open(png)
    assert abs(image.width - width / 25.4 * 300) <= 1
    assert abs(image.height - height / 25.4 * 300) <= 1
    assert "dpi" in image.info and set(image.info) <= {"dpi"}  # без привязки и метаданных


def test_provenance_passes_image_contract(project):
    png = build.render(describe(project))[1]
    doc = provenance(png)
    schema = json.loads(paths.schema("image").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(doc)
    assert (png.parent / doc["legend_file"]).is_file()
    assert doc["scale"] == "1:10 000"
    assert doc["status"] == "official"  # слои reference (школы, улица) в сводный статус не входят
    assert doc["date"] == "2026-10-01"
    assert [layer["name"] for layer in doc["layers"]] == [
        "Школы", "Граница проекта планировки", "Санитарно-защитная зона", "Магистральная улица", "Кварталы"]


def test_where_selects_objects(project, capsys):
    doc = provenance(build.render(describe(project))[1])
    schools = doc["layers"][0]
    assert schools["source"] == "синтетическая выгрузка OSM, ODbL 1.0"  # без сада (другой отбор) и школы вне охвата
    assert "point_1 ← poi_education (poi_type = 'school'): в охвате 2" in capsys.readouterr().out


RENDER = "import sys; from pathlib import Path; from cm_carto.render import build; " \
         "build.CACHE = Path(sys.argv[1]); build.render(Path(sys.argv[2]))"


def test_repeat_is_byte_identical(project, tmp_path, capsys):
    """Две сборки в разных процессах (затравка QHash у каждого своя) — те же PNG и SVG; повтор — из кеша."""
    for name in ("a", "b"):
        subprocess.run([sys.executable, "-c", RENDER, str(tmp_path / "cache"), str(describe(project, name))],
                       check=True)
    maps = project / "maps"
    assert (maps / "a.png").read_bytes() == (maps / "b.png").read_bytes()
    assert (maps / "a-legend.svg").read_bytes() == (maps / "b-legend.svg").read_bytes()
    build.render(describe(project, "a"))
    assert "из кеша" in capsys.readouterr().out


def test_unknown_role_is_refused(project):
    with pytest.raises(ValueError, match="ролей нет в map.yaml: schools"):
        build.render(describe(project, layers={"schools": {"layer": "poi_education"}}))


def test_wrong_geometry_is_refused(project):
    with pytest.raises(ValueError, match="роль point_1 рисует point, у слоя blocks геометрия polygon"):
        build.render(describe(project, layers={"point_1": {"layer": "blocks"}}))


def test_north_against_rule_is_refused(project):
    with pytest.raises(ValueError, match="north: True, по правилу"):
        build.render(describe(project, north=True))


def test_forbidden_license_is_refused(project):
    data = frames()
    data["roads"].loc[0, "source_license"] = "условия использования Яндекс Карт"
    gpkg.write(project / "geo" / "forbidden.gpkg", data, "синтетический проект", CRS.from_user_input(CRS_NAME),
               CRS_NAME, "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="запрещенные к печати"):
        build.render(describe(project, "forbidden", data="geo/forbidden.gpkg"))


def test_token_color_changes_map(project, tmp_path, monkeypatch):
    """Приемка, п. 3: другой цвет кварталов в map.yaml верстки — стили их же командой — другая карта, код тот же."""
    design = tmp_path / "cm-design"
    for folder in ("tokens", "design", "cm_deck"):
        shutil.copytree(paths.design() / folder, design / folder)
    tokens_file = design / "tokens" / "map.yaml"
    color = tokens.load("map")["palette"]["block"]
    text = tokens_file.read_text(encoding="utf-8")
    assert f"block: {color}" in text
    tokens_file.write_text(text.replace(f"block: {color}", "block: D8E4F0"), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "cm_deck.qgis"], cwd=design, check=True,
                   env={**os.environ, "PYTHONPATH": str(design)})
    assert "216,228,240" in (design / "design" / "qgis" / "blocks.qml").read_text(encoding="utf-8")

    before = Image.open(build.render(describe(project, "before"))[1]).convert("RGB")
    monkeypatch.setenv("CM_DESIGN", str(design))
    after = Image.open(build.render(describe(project, "after"))[1]).convert("RGB")
    assert before.size == after.size
    assert before.tobytes() != after.tobytes()
    assert (216, 228, 240) in {after.getpixel((x, y)) for x in range(0, after.width, 10) for y in range(0, after.height, 10)}


def test_extent_fits_scale_and_inside_legend(project):
    spec = yaml.safe_load(describe(project, "extent").read_text(encoding="utf-8"))
    del spec["center"], spec["scale"]
    spec |= {"place": "landscape", "legend": "inside", "extent": [X - 600, Y - 600, X + 600, Y + 600]}
    path = project / "extent.yaml"
    path.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    written = build.render(path)
    doc = provenance(written[0])
    assert doc["scale"] == "1:6 400"  # 1200 м на 187,68 мм — 1:6394, вверх до двух значащих цифр
    assert "legend_file" not in doc and len(written) == 2
