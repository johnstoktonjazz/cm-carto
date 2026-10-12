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
import shapely
import yaml
from PIL import Image
from pyproj import CRS
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box

from cm_carto import gpkg, paths, tokens
from cm_carto.render import build, overlay, spec as spec_file

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
        # Магазины выше нижнего края на 18 мм: левый нижний угол свободен для линейки (правый занят изохроной, Д-054).
        "poi_other": rows("poi_other", [(f"o{i}", Point(X - 400 + i * 120, Y - 260), {**OSM, "poi_type": "shop",
                                                                                     "name": f"Магазин {i}"})
                                        for i in range(6)]),
        "zouit": rows("zouit", [("z1", box(X + 300, Y + 300, X + 600, Y + 600), {**OFFICIAL, "zouit_type": "sanitary"})]),
        "isochrones": rows("isochrones", [("walk-10", CIRCLE, {**OSM, "mode": "walk", "minutes": 10.0,
                                                              "source_label": "синтетический расчет"})]),
        # Зоны ПЗЗ: Ж — большая, через нее идет улица main; остальные категории — полосой сверху.
        "zones_pzz": rows("zones_pzz", [("zh", box(X - 400, Y - 200, X + 400, Y + 150),
                                         {**OFFICIAL, "zone": "Ж", "code": "Ж-1", "fact_ids": ["F-000530"]})]
                          + [(f"z{i}", box(X - 420 + i * 120, Y + 250, X - 320 + i * 120, Y + 320),
                              {**OFFICIAL, "zone": zone, "code": f"{zone}-1"})
                             for i, zone in enumerate(["ОЖ", "ОД", "П", "ИТ", "Р", "СН"])]),
    }


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("project")
    (root / "project.yaml").write_text(yaml.safe_dump(
        {"name": "синтетический проект", "crs": {"name": CRS_NAME, "definition": CRS_NAME},
         "carto": {"extent": [X - 5000, Y - 5000, X + 5000, Y + 5000], "geo": "geo"}}, allow_unicode=True), encoding="utf-8")
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
    """Подложка — в размер места, без привязки и метаданных; слои данных на ней не нарисованы: в точке контура
    территории (overlay) на подложке нет цвета границы, а в превью overlay он есть (граница — цвет text, К-05 верстки,
    поэтому по цвету всей картинки не отличить: подписи подложки того же цвета)."""
    build.render(describe(project), preview=True)
    width, height = tokens.place_size_mm("square_small")
    image = Image.open(project / "maps" / "map.png")
    assert abs(image.width - width / 25.4 * 300) <= 1
    assert abs(image.height - height / 25.4 * 300) <= 1
    assert "dpi" in image.info and set(image.info) <= {"dpi"}
    std, style = tokens.load("map"), tokens.load("style")
    name = std["layers"]["territory"]["stroke"]["color"]
    hexa = std["palette"].get(name) or style["colors"][name]
    color = tuple(int(hexa[i:i + 2], 16) for i in (0, 2, 4))
    x, y = layer(side(project, "map", ".overlay.json"), "territory")["features"][0]["rings"][0][0]
    pixel = (round(x / 25.4 * 300), round(y / 25.4 * 300))
    preview = Image.open(project / "maps" / "map.preview.png").convert("RGB")
    assert image.convert("RGB").getpixel(pixel) != color
    assert preview.getpixel(pixel) == color


def test_provenance_describes_basemap(project):
    build.render(describe(project))
    doc = side(project, "map", ".provenance.json")
    jsonschema.Draft202012Validator(json.loads(paths.schema("image").read_text(encoding="utf-8"))).validate(doc)
    assert doc["scale"] == "1:10000" and doc["status"] == "official" and doc["date"] == "2021-06-01"
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
    assert [f["name"] for f in layer(doc, "point_1")["features"]] == ["Школа №\u00a01", "Школа №\u00a02"]  # имя фигуры — как подпись
    labels = layer(doc, "labels_point_1")["features"]
    assert [f["text"] for f in labels] == ["Школа №\u00a01", "Школа №\u00a02"] and {f["size_role"] for f in labels} == {"label"}
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


def test_reference_basemap_passes_image(project):
    """Подложка из OSM — reference: в image 1.1.0 статус есть, provenance выпускается и проходит схему."""
    layers = yaml.safe_load(describe(project).read_text(encoding="utf-8"))["layers"]
    layers["road_main"] = {"layer": "roads", "where": "road_class = 'main'"}
    out = build.render(describe(project, "osm", layers=layers))
    provenance = next(p for p in out if p.name.endswith(".provenance.json"))
    assert json.loads(provenance.read_text(encoding="utf-8"))["status"] == "reference"


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
    assert side(project, "extent", ".provenance.json")["scale"] == "1:6400"  # 1200 м на 187,68 мм — 1:6394, вверх
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


def test_compare_group_shares_role_sources(project):
    """Сравнение: у школ на a один источник, на b (школы и сады) — два; source_label роли в обоих overlay один и тот
    же, источники в порядке путей карт, затем объектов."""
    a, b = compare_pair(project, "src-")
    raw = yaml.safe_load(b.read_text(encoding="utf-8"))
    raw["layers"]["point_1"]["where"] = "poi_type IN ('school', 'kindergarten')"
    b.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    for path in (a, b):
        build.render(path)
    docs = {name: side(project, f"src-{name}", ".overlay.json") for name in "ab"}
    found = {name: [(item["id"], item["source_label"], len(item["features"])) for item in doc["layers"]
                    if item["role"] == "point_1"] for name, doc in docs.items()}
    assert [x[:2] for x in found["a"]] == [x[:2] for x in found["b"]] == [
        ("point_1_1", "синтетическая выгрузка OSM, ODbL 1.0"), ("point_1_2", "другой источник садов, ODbL 1.0")]
    assert found["a"][1][2] == 0 and found["b"][1][2] > 0  # у a садов этого источника нет — слой без фич


def test_role_with_two_sources_gives_layer_per_source(project):
    """Школы из двух источников: два слоя overlay с role point_1, у каждого свой source_label без «; », status и
    fact_ids; строка легенды одна; overlay по контракту."""
    raw = yaml.safe_load(describe(project, "two").read_text(encoding="utf-8"))
    raw["layers"]["point_1"]["where"] = "poi_type IN ('school', 'kindergarten')"
    (project / "two.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    build.render(project / "two.yaml")
    doc = side(project, "two", ".overlay.json")
    jsonschema.Draft202012Validator(json.loads(paths.schema("overlay").read_text(encoding="utf-8"))).validate(doc)
    found = [item for item in doc["layers"] if item["role"] == "point_1"]
    assert [item["id"] for item in found] == ["point_1_1", "point_1_2"]
    assert sorted(item["source_label"] for item in found) == ["другой источник садов, ODbL 1.0",
                                                               "синтетическая выгрузка OSM, ODbL 1.0"]
    assert all(item["features"] and "status" in item and "fact_ids" in item for item in found)
    assert [row["text"] for row in layer(doc, "legend")["legend"]].count("Школы") == 1


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


def test_style_color_changes_basemap_and_preview_not_overlay(project, tmp_path, monkeypatch):
    """Приемка, п. 3, для style.yaml: роль text (map.yaml: подписи улиц подложки, знаки и подписи данных, легенда)
    другого цвета — стили их же командой — подписи улиц на подложке и знаки в превью нового цвета; overlay.json
    цветов не несет и не меняется байт в байт."""
    design = tmp_path / "cm-design"
    for folder in ("tokens", "design", "cm_deck", "input/fonts"):
        shutil.copytree(paths.design() / folder, design / folder)
    tokens_file = design / "tokens" / "style.yaml"
    color = tokens.load("style")["colors"]["text"]
    text = tokens_file.read_text(encoding="utf-8")
    assert f"  text: {color}\n" in text
    tokens_file.write_text(text.replace(f"  text: {color}\n", "  text: 1F5FA0\n"), encoding="utf-8")
    subprocess.run([sys.executable, "-m", "cm_deck.qgis"], cwd=design, check=True,
                   env={**os.environ, "PYTHONPATH": str(design)})
    assert "31,95,160" in (design / "design" / "qgis" / "road_main.qml").read_text(encoding="utf-8")

    data = frames()
    data["roads"][["status", "source_label", "source_date"]] = ["official", "синтетический чертеж, лист 2", "2021-06-01"]
    gpkg.write(project / "geo" / "streets.gpkg", data, "синтетический проект", CRS.from_user_input(CRS_NAME),
               CRS_NAME, "sha256:" + "0" * 64)
    layers = yaml.safe_load(describe(project).read_text(encoding="utf-8"))["layers"]
    layers["road_main"] = {"layer": "roads", "where": "road_class = 'main'"}
    build.render(describe(project, "style-before", layers=layers, data="geo/streets.gpkg"), preview=True)
    monkeypatch.setenv("CM_DESIGN", str(design))
    build.render(describe(project, "style-after", layers=layers, data="geo/streets.gpkg"), preview=True)

    def colors(suffix: str) -> dict:
        return {name: {c for _, c in Image.open(project / "maps" / f"style-{name}{suffix}").convert("RGB")
                       .getcolors(1 << 24)} for name in ("before", "after")}
    new = (31, 95, 160)
    old = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    basemap, shown = colors(".png"), colors(".preview.png")
    assert old in basemap["before"] and new not in basemap["before"]  # подпись «Главная улица» цвета text
    assert new in basemap["after"] and old not in basemap["after"]
    assert old in shown["before"] and new not in shown["before"]
    assert new in shown["after"] and old not in shown["after"]
    before = (project / "maps" / "style-before.overlay.json").read_text(encoding="utf-8")
    after = (project / "maps" / "style-after.overlay.json").read_text(encoding="utf-8")
    assert before.replace('"style-before.png"', '"style-after.png"') == after


def streets_gpkg(project, name: str, isochrone=None) -> str:
    """Геопакет с улицей main «Четвёртая линия» через зону Ж (y = Y − 10) и, если дано, своей изохроной."""
    data = frames()
    data["roads"].loc[0, "name"] = "Четвёртая линия"
    if isochrone is not None:
        data["isochrones"] = data["isochrones"].set_geometry([isochrone], crs=data["isochrones"].crs)
    gpkg.write(project / "geo" / f"{name}.gpkg", data, "синтетический проект", CRS.from_user_input(CRS_NAME),
               CRS_NAME, "sha256:" + "0" * 64)
    return f"geo/{name}.gpkg"


def test_street_cut_from_zone(project):
    """Улица main (полуширина 14 м — rules/fetch.yaml: blocks.half_width_m, как у кварталов подложки) идет через зону Ж:
    в overlay в зоне полоса без заливки шириной 2 × 1,4 мм на 1:10 000; граница территории (без заливки) не режется."""
    widths = yaml.safe_load(build.FETCH_RULES.read_text(encoding="utf-8"))["blocks"]["half_width_m"]
    half = widths["main"] * 1000 / 10000
    layers = {"zoning": {"layer": "zones_pzz"}, "territory": {"layer": "boundary_planning"},
              "road_main": {"layer": "roads", "where": "road_class = 'main'"}, "blocks": {}}
    build.render(describe(project, "cut", layers=layers, data=streets_gpkg(project, "cut")))
    doc = side(project, "cut", ".overlay.json")
    zh = layer(doc, "zoning_zh")
    assert zh["role"] == "zoning" and {f["category"] for f in zh["features"]} == {"Ж"}
    assert len(zh["features"]) == 2 and {f["id"] for f in zh["features"]} == {"zones_pzz:zh"}  # две части, один id
    assert all(f["status"] == "official" and f["fact_ids"] == ["F-000530"] for f in zh["features"])
    zone = shapely.union_all([Polygon(f["rings"][0], f["rings"][1:]) for f in zh["features"]])
    axis = (444.45 + 10) / 10  # y улицы в мм места: (верх охвата − (Y − 10)) / 10
    tolerance = overlay.SIMPLIFY_MM + overlay.ROUND_MM
    for x in (20.0, 45.0, 70.0):
        assert not zone.intersects(LineString([(x, axis - half + tolerance), (x, axis + half - tolerance)]))
        assert zone.contains(Point(x, axis - half - tolerance)) and zone.contains(Point(x, axis + half + tolerance))
    territory = layer(doc, "territory")["features"]
    assert len(territory) == 1 and len(territory[0]["rings"]) == 1


def test_basemap_labels_avoid_overlay_lines(project, monkeypatch):
    """Подпись улицы подложки не пересекает линию изохроны: контуры без заливки — места, которые QGIS закрывает для
    подписей подложки. Изохрона — узкая полоса поперек улицы посередине карты, где подпись встала бы без запрета."""
    strip = box(X - 20, Y - 300, X + 20, Y + 300)
    seen = []
    labels = build.basemap_labels
    monkeypatch.setattr(build, "basemap_labels", lambda *a: seen.append(labels(*a)) or seen[-1])
    layers = {"reach_walk": {"layer": "isochrones"}, "road_main": {"layer": "roads", "where": "road_class = 'main'"},
              "blocks": {}}
    build.render(describe(project, "avoid", layers=layers, data=streets_gpkg(project, "avoid", strip)))
    ring = layer(side(project, "avoid", ".overlay.json"), "reach_walk")["features"][0]["rings"][0]
    edge = LineString(ring)
    assert seen and seen[0], "подпись улицы не встала"
    assert not any(box(*r).intersects(edge) for r in seen[0])


def test_basemap_label_follows_text_rules(project):
    """«Четвёртая линия» в геопакете — «Четвертая линия» в подписи подложки: QGIS подписывает копию слоя в памяти."""
    layers = {"road_main": {"layer": "roads", "where": "road_class = 'main'"}, "blocks": {}}
    path = describe(project, "yo", layers=layers, data=streets_gpkg(project, "yo"))
    spec = spec_file.load(path)
    build.start()
    role = next(r for r in spec.roles if r.key == "road_main")
    d = build.vector(spec, tokens.load("map"), role, build.frame(spec))
    assert [f["name"] for f in d.layer.getFeatures()] == ["Четвертая линия"]
    assert d.layer.labelsEnabled() and d.rows[0]["name"] == "Четвёртая линия"  # данные — как в источнике


def test_caption_merges_one_provider():
    """Подпись карты: один поставщик и лицензия — одной записью без перечня слоев (он в layers provenance);
    уточнение источника слоя («кварталы по улицам») — тот же поставщик."""
    from types import SimpleNamespace as NS

    osm = "OpenStreetMap через Overture Maps 2026-09-23.0"
    drawn = [NS(role=NS(title="Магистральная улица", layer="roads"), rows=[{"source_label": osm, "source_license": "ODbL 1.0"}]),
             NS(role=NS(title="Улица районного значения", layer="roads"),
                rows=[{"source_label": osm, "source_license": "ODbL 1.0"}]),
             NS(role=NS(title="Железная дорога", layer="rail"), rows=[{"source_label": osm, "source_license": "ODbL 1.0"}]),
             NS(role=NS(title="Кварталы", layer="blocks"), rows=[{"source_label": f"{osm}, кварталы по улицам",
                                                                  "source_license": "ODbL 1.0"}]),
             NS(role=NS(title="Водные объекты", layer="water_ppt"),
                rows=[{"source_label": "чертеж ППТ, лист 2", "source_license": None}])]
    assert build.caption(drawn) == f"{osm}, ODbL 1.0; чертеж ППТ, лист 2"


def test_legend_groups_from_nine_rows(project):
    """11 строк (зоны — по строке на категорию) — группы по уровням map.yaml: levels, строка kind: group."""
    layers = {"point_1": {"layer": "poi_education", "where": "poi_type = 'school'", "title": "Школы"},
              "reach_walk": {"layer": "isochrones", "title": "10 минут пешком"},
              "territory": {"layer": "boundary_planning"}, "zone_sanitary": {"layer": "zouit"},
              "zoning": {"layer": "zones_pzz"}, "blocks": {}}
    build.render(describe(project, "groups", layers=layers))
    doc = side(project, "groups", ".overlay.json")
    rows = layer(doc, "legend")["legend"]
    std = tokens.load("map")
    groups = [r for r in rows if r["kind"] == "group"]
    assert [(r["role"], r["text"]) for r in groups] == [(k, std["levels"][k]["title"]) for k in ("analysis", "boundaries")]
    assert len(rows) - len(groups) == 11 and rows[0]["kind"] == "group"
    assert [r["category"] for r in rows if r["role"] == "zoning"] == ["Ж", "ОЖ", "ОД", "П", "ИТ", "Р", "СН"]


# Угол линейки (Д-054, map.yaml: scale_bar.position): правый нижний, при касании данных — левый нижний без легенды внутри.

BAR_RIGHT = (300.0, 190.0, 340.0, 200.0)  # плашка в правом нижнем углу места шириной 345 мм
BAR_WIDTH = 345.0


def ring_layer(role: str, rect: tuple) -> dict:
    x0, y0, x1, y1 = rect
    return {"id": role, "role": role, "kind": "polygon",
            "features": [{"id": "o1", "rings": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}]}


def test_scale_bar_moves_left_when_contour_touches_right():
    std = tokens.load("map")
    reach = ring_layer("reach_walk", (200.0, 100.0, 320.0, 195.0))  # контур изохроны проходит под правой плашкой
    corner, rect = overlay.choose_corner(std, BAR_WIDTH, BAR_RIGHT, [reach], None)
    assert corner == "bottom_left" and rect == (5.0, 190.0, 45.0, 200.0)
    filled = ring_layer("green", (200.0, 100.0, 320.0, 195.0))
    assert overlay.choose_corner(std, BAR_WIDTH, BAR_RIGHT, [filled], None)[0] == "bottom_right"  # край заливки — не препятствие


def test_scale_bar_stays_right_with_legend_inside():
    std = tokens.load("map")
    legend = (14.0, 150.0, 80.0, 198.0)
    assert overlay.choose_corner(std, BAR_WIDTH, BAR_RIGHT, [], legend) == ("bottom_right", BAR_RIGHT)


def test_scale_bar_both_corners_busy_is_error():
    std = tokens.load("map")
    reach = ring_layer("reach_walk", (200.0, 100.0, 320.0, 195.0))
    with pytest.raises(ValueError, match="сдвинуть охват"):
        overlay.choose_corner(std, BAR_WIDTH, BAR_RIGHT, [reach], (14.0, 150.0, 80.0, 198.0))
    point = {"id": "stop", "role": "stop", "kind": "point", "features": [{"id": "s1", "at": [20.0, 195.0]}]}
    with pytest.raises(ValueError, match="левый нижний угол — stop:s1"):
        overlay.choose_corner(std, BAR_WIDTH, BAR_RIGHT, [reach, point], None)


def zone_gpkg(project, name: str, zone: str) -> str:
    """Копия геопакета, где зона zones_pzz:zh — категории zone; значение пишется в обход схемы (как придут данные
    версии контракта, которой стиль верстки еще не знает)."""
    data = project / "geo" / f"{name}.gpkg"
    shutil.copy(project / "geo" / "project.gpkg", data)
    from osgeo import ogr  # GDAL: триггеры GPKG вызывают его функции SQL
    ogr.UseExceptions()
    source = ogr.Open(str(data), 1)
    source.ExecuteSQL(f"UPDATE zones_pzz SET zone = '{zone}' WHERE object_id = 'zones_pzz:zh'")
    source = None
    return f"geo/{name}.gpkg"


def test_zone_category_missing_from_style_is_refused(project):
    """Категория зоны, которой нет в стиле верстки, не пропадает молча: сборка падает с именем категории."""
    with pytest.raises(ValueError, match="категорий ХХ нет в стиле верстки"):
        build.render(describe(project, "unknown-zone", layers={"zoning": {"layer": "zones_pzz"}, "blocks": {}},
                              data=zone_gpkg(project, "unknown-zone", "ХХ")))


def test_agricultural_zone_is_drawn(project):
    """СХ (geopackage 1.2.0) есть в стиле верстки (map.yaml: zoning.categories, zoning.qml): зона в overlay и легенде."""
    build.render(describe(project, "agro", layers={"zoning": {"layer": "zones_pzz"}, "blocks": {}},
                          data=zone_gpkg(project, "agro", "СХ")))
    doc = side(project, "agro", ".overlay.json")
    assert {f["category"] for l in doc["layers"] if l["role"] == "zoning" for f in l["features"]} >= {"СХ"}