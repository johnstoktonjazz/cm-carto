"""Рендер карты по описанию на синтетическом геопакете: подложка PNG в размер места без слоев данных, provenance
по контракту image, overlay по контракту overlay (мм места, упрощение, подписи без наездов), повтор байт в байт,
отбор where, ошибки описания и печати, смена токена верстки меняет карту без правок кода."""

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
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box

from cm_carto import gpkg, paths, tokens
from cm_carto.render import build, overlay

CRS_NAME = "EPSG:32637"
X, Y = 500000, 5000000
COMMON = {"fact_ids": [], "source_date": "2026-10-01"}
OFFICIAL = {**COMMON, "status": "official", "source_label": "синтетический чертеж, лист 1", "source_date": "2021-06-01"}
OSM = {**COMMON, "status": "reference", "source_label": "синтетическая выгрузка OSM", "source_license": "ODbL 1.0"}
CIRCLE = Point(X + 100, Y - 100).buffer(250, quad_segs=64)  # изохрона: много вершин для проверки упрощения


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
        "poi_other": rows("poi_other", [(f"o{i}", Point(X - 400 + i * 120, Y - 380), {**OSM, "poi_type": "shop",
                                                                                     "name": f"Магазин {i}"})
                                        for i in range(6)]),
        "zouit": rows("zouit", [("z1", box(X + 300, Y + 300, X + 600, Y + 600), {**OFFICIAL, "zouit_type": "sanitary"})]),
        "isochrones": rows("isochrones", [("walk-10", CIRCLE, {**OSM, "mode": "walk", "minutes": 10.0,
                                                              "source_label": "синтетический расчет"})]),
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
                       "reach_walk": {"layer": "isochrones", "title": "10 минут пешком"},
                       "point_3": {"layer": "poi_other", "title": "Магазины"},
                       "blocks": {}},
            "title": "Школы у границы", "out": f"maps/{name}.png"}
    spec.update(changes)
    path = folder / f"{name}.yaml"
    path.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    return path


def side(project, name: str, suffix: str) -> dict:
    return json.loads((project / "maps" / f"{name}{suffix}").read_text(encoding="utf-8"))


def layer(doc: dict, layer_id: str) -> dict:
    return next(item for item in doc["layers"] if item["id"] == layer_id)


def test_basemap_png_matches_place_and_has_no_data(project):
    """Подложка — в размер места, без привязки и метаданных; слои данных на ней не нарисованы (нет цвета accent
    границы территории), а в превью overlay граница есть."""
    build.render(describe(project), preview=True)
    width, height = tokens.place_size_mm("square_small")
    image = Image.open(project / "maps" / "map.png")
    assert abs(image.width - width / 25.4 * 300) <= 1
    assert abs(image.height - height / 25.4 * 300) <= 1
    assert "dpi" in image.info and set(image.info) <= {"dpi"}
    accent = tuple(int(tokens.load("style")["colors"]["accent"][i:i + 2], 16) for i in (0, 2, 4))
    assert accent not in {c for _, c in image.convert("RGB").getcolors(1 << 24)}
    preview = Image.open(project / "maps" / "map.preview.png").convert("RGB")
    assert accent in {c for _, c in preview.getcolors(1 << 24)}


def test_provenance_describes_basemap(project):
    build.render(describe(project))
    doc = side(project, "map", ".provenance.json")
    jsonschema.Draft202012Validator(json.loads(paths.schema("image").read_text(encoding="utf-8"))).validate(doc)
    assert doc["scale"] == "1:10 000" and doc["status"] == "official" and doc["date"] == "2021-06-01"
    assert [entry["name"] for entry in doc["layers"]] == ["Кварталы"]
    assert "legend_file" not in doc


def test_overlay_passes_contract_and_names_follow_map_yaml(project):
    build.render(describe(project))
    doc = side(project, "map", ".overlay.json")
    jsonschema.Draft202012Validator(json.loads(paths.schema("overlay").read_text(encoding="utf-8"))).validate(doc)
    assert [item["id"] for item in doc["layers"]] == [
        "zone_sanitary", "territory", "reach_walk", "point_3", "point_1", "labels_point_1", "legend", "scale_bar"]
    assert doc["basemap"] == "map.png" and doc["place"]["name"] == "square_small"
    assert [row["role"] for row in layer(doc, "legend")["legend"]] == [
        "point_1", "point_3", "reach_walk", "territory", "zone_sanitary"]
    assert layer(doc, "scale_bar")["scalebar"]["segments_m"] == [0, 100, 200]


def test_known_object_lands_in_expected_mm(project):
    """Угол территории (X − 200, Y + 190) при центре (X, Y), 1:10 000, место 91,37 × 88,9 мм: x = (456,85 − 200) / 10,
    y = (444,5 − 190) / 10."""
    build.render(describe(project))
    ring = layer(side(project, "map", ".overlay.json"), "territory")["features"][0]["rings"][0]
    assert any(abs(x - 25.685) <= 0.05 and abs(y - 25.45) <= 0.05 for x, y in ring)


def test_simplification_within_tolerance(project):
    build.render(describe(project))
    doc = side(project, "map", ".overlay.json")
    rings = layer(doc, "reach_walk")["features"][0]["rings"]
    xmin, _, _, ymax = doc["map"]["extent"]
    exact = affinity.affine_transform(CIRCLE, overlay.to_mm(10000, (xmin, 0, 0, ymax)))
    simple = Polygon(rings[0], rings[1:])
    assert len(rings[0]) < len(CIRCLE.exterior.coords)
    assert exact.boundary.hausdorff_distance(simple.boundary) <= overlay.SIMPLIFY_MM + overlay.ROUND_MM


def test_where_and_labels_follow_rules(project, capsys):
    """Отбор where: сада и школы вне охвата нет; подписи — только у роли не больше max_features объектов
    (школ 2 из 5 — подписаны, магазинов 6 — нет); подписи не задевают друг друга и знаки."""
    build.render(describe(project))
    assert "point_1 ← poi_education (poi_type = 'school'): в охвате 2" in capsys.readouterr().out
    doc = side(project, "map", ".overlay.json")
    assert [f["id"] for f in layer(doc, "point_1")["features"]] == ["poi_education:s1", "poi_education:s2"]
    labels = layer(doc, "labels_point_1")["features"]
    assert [f["text"] for f in labels] == ["Школа 1", "Школа 2"] and {f["size_role"] for f in labels} == {"label"}
    assert "labels_point_3" not in [item["id"] for item in doc["layers"]]
    measure = overlay.Measure(tokens.load("map"), tokens.load("style"))
    boxes = [overlay.text_box(f["at"], f["anchor"], measure.size(f["text"], f["size_role"]), (0, 0)) for f in labels]
    marks = [Point(f["at"]).buffer(1.25) for item in doc["layers"] if item["kind"] == "point" for f in item["features"]]
    assert not any(a.intersects(b) for i, a in enumerate(boxes) for b in boxes[i + 1:])
    assert not any(a.intersects(m) for a in boxes for m in marks)


RENDER = "import sys; from pathlib import Path; from cm_carto.render import build; " \
         "build.CACHE = Path(sys.argv[1]); build.render(Path(sys.argv[2]))"


def test_repeat_is_byte_identical(project, tmp_path, capsys):
    """Две сборки в разных процессах — те же PNG и overlay; повтор — из кеша."""
    for name in ("a", "b"):
        subprocess.run([sys.executable, "-c", RENDER, str(tmp_path / "cache"), str(describe(project, name))],
                       check=True)
    maps = project / "maps"
    assert (maps / "a.png").read_bytes() == (maps / "b.png").read_bytes()
    a = (maps / "a.overlay.json").read_text(encoding="utf-8")
    assert a.replace('"a.png"', '"b.png"') == (maps / "b.overlay.json").read_text(encoding="utf-8")
    build.render(describe(project, "a"))
    assert "из кеша" in capsys.readouterr().out


def test_reference_basemap_is_refused(project):
    """Подложка из OSM — reference, а в image: status его нет: provenance не выпускается (вопрос к контракту)."""
    layers = yaml.safe_load(describe(project).read_text(encoding="utf-8"))["layers"]
    layers["road_main"] = {"layer": "roads", "where": "road_class = 'main'"}
    with pytest.raises(ValueError, match="статус подложки reference"):
        build.render(describe(project, "osm", layers=layers))


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
    data["poi_education"].loc[0, "source_license"] = "условия использования Яндекс Карт"
    gpkg.write(project / "geo" / "forbidden.gpkg", data, "синтетический проект", CRS.from_user_input(CRS_NAME),
               CRS_NAME, "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="запрещенные к печати"):
        build.render(describe(project, "forbidden", data="geo/forbidden.gpkg"))


def test_token_color_changes_map(project, tmp_path, monkeypatch):
    """Приемка, п. 3: другой цвет кварталов в map.yaml верстки — стили их же командой — другая подложка, код тот же."""
    design = tmp_path / "cm-design"
    for folder in ("tokens", "design", "cm_deck", "input/fonts"):
        shutil.copytree(paths.design() / folder, design / folder)
    tokens_file = design / "tokens" / "map.yaml"
    color = tokens.load("map")["palette"]["block"]
    text = tokens_file.read_text(encoding="utf-8")
    assert f"block: {color}" in text
    tokens_file.write_text(text.replace(f"block: {color}", "block: D8E4F0"), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "cm_deck.qgis"], cwd=design, check=True,
                   env={**os.environ, "PYTHONPATH": str(design)})
    assert "216,228,240" in (design / "design" / "qgis" / "blocks.qml").read_text(encoding="utf-8")

    build.render(describe(project, "before"))
    monkeypatch.setenv("CM_DESIGN", str(design))
    build.render(describe(project, "after"))
    before = Image.open(project / "maps" / "before.png").convert("RGB")
    after = Image.open(project / "maps" / "after.png").convert("RGB")
    assert before.size == after.size and before.tobytes() != after.tobytes()
    assert (216, 228, 240) in {c for _, c in after.getcolors(1 << 24)}


def test_extent_fits_scale_and_inside_legend(project):
    spec = yaml.safe_load(describe(project, "extent").read_text(encoding="utf-8"))
    del spec["center"], spec["scale"]
    spec |= {"place": "landscape", "legend": "inside", "extent": [X - 600, Y - 600, X + 600, Y + 600]}
    (project / "extent.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    build.render(project / "extent.yaml")
    assert side(project, "extent", ".provenance.json")["scale"] == "1:6 400"  # 1200 м на 187,68 мм — 1:6394, вверх
    legend = layer(side(project, "extent", ".overlay.json"), "legend")
    x, y = legend["legend_at"]
    assert 0 < x < 251.88 and 0 < y < 187.68


def test_within_keeps_only_objects_inside_contour(project, capsys):
    """within: школы — только внутри изохроны (s1 внутри, s2 вне круга, хотя в охвате карты); площадь, пересекающая
    контур, остается. В счете и на карте — только они."""
    spec = yaml.safe_load(describe(project).read_text(encoding="utf-8"))
    spec["layers"]["point_1"]["within"] = "isochrones:walk-10"
    spec["layers"]["territory"]["within"] = "isochrones:walk-10"
    spec["center"] = "isochrones:walk-10"
    (project / "within.yaml").write_text(yaml.safe_dump(spec | {"out": "maps/within.png"}, allow_unicode=True),
                                         encoding="utf-8")
    build.render(project / "within.yaml")
    assert "point_1 ← poi_education (poi_type = 'school') внутри isochrones:walk-10: в охвате 1" \
        in capsys.readouterr().out
    doc = side(project, "within", ".overlay.json")
    assert [f["id"] for f in layer(doc, "point_1")["features"]] == ["poi_education:s1"]
    assert [f["id"] for f in layer(doc, "territory")["features"]] == ["boundary_planning:t"]


def test_within_unknown_object_is_refused(project):
    layers = yaml.safe_load(describe(project).read_text(encoding="utf-8"))["layers"]
    layers["point_1"]["within"] = "isochrones:walk-99"
    with pytest.raises(ValueError, match="объект isochrones:walk-99: в project.gpkg найдено 0, нужен один"):
        build.render(describe(project, "unknown", layers=layers))


def compare_pair(project, prefix: str, **changes_b) -> tuple:
    """Две карты сравнения: у a сады в охвате есть, у b отбор садов пустой; compare_with друг на друга."""
    layers = yaml.safe_load(describe(project).read_text(encoding="utf-8"))["layers"]
    layers["point_2"] = {"layer": "poi_education", "where": "poi_type = 'kindergarten'", "title": "Детские сады"}
    empty = {**layers, "point_2": {**layers["point_2"], "where": "poi_type = 'kindergarten' AND name = 'нет'"}}
    a = describe(project, f"{prefix}a", layers=layers, compare_with=[f"{prefix}b.yaml"])
    b = describe(project, f"{prefix}b", layers=empty, compare_with=[f"{prefix}a.yaml"], **changes_b)
    return a, b


def test_compare_group_shares_legend(project):
    """Сравнение: у b садов 0, но строка «Детские сады» в легенде обеих карт; строки легенды — байт в байт."""
    for path in compare_pair(project, "cmp-"):
        build.render(path)
    a, b = side(project, "cmp-a", ".overlay.json"), side(project, "cmp-b", ".overlay.json")
    assert "point_2" in [item["id"] for item in a["layers"]]
    assert "point_2" not in [item["id"] for item in b["layers"]]
    assert "Детские сады" in [row["text"] for row in layer(b, "legend")["legend"]]
    text = {name: (project / "maps" / f"cmp-{name}.overlay.json").read_text(encoding="utf-8") for name in "ab"}
    legend = {name: text[name][text[name].index('"legend": ['):].split("]", 1)[0] for name in "ab"}
    assert legend["a"] == legend["b"]


def test_compare_group_scale_mismatch_is_refused(project):
    a, _ = compare_pair(project, "scale-", scale=20000)
    with pytest.raises(ValueError, match="сравнение с scale-b.yaml: масштаб 10000 против 20000"):
        build.render(a)


def test_compare_group_repeat_is_byte_identical_and_follows_neighbor(project, tmp_path, capsys, monkeypatch):
    """Карта группы в двух процессах — байт в байт; смена описания соседа — новая сборка, не кеш."""
    a, b = compare_pair(project, "rep-")
    outputs = []
    for run in ("1", "2"):
        subprocess.run([sys.executable, "-c", RENDER, str(tmp_path / f"cache{run}"), str(a)], check=True)
        outputs.append([(project / "maps" / f"rep-a{suffix}").read_bytes() for suffix in (".png", ".overlay.json")])
    assert outputs[0] == outputs[1]
    monkeypatch.setattr(build, "CACHE", tmp_path / "cache1")
    build.render(a)
    assert "из кеша" in capsys.readouterr().out
    b.write_text(b.read_text(encoding="utf-8").replace("Школы у границы", "Школы у границы, вариант"),
                 encoding="utf-8")
    build.render(a)
    assert "из кеша" not in capsys.readouterr().out
