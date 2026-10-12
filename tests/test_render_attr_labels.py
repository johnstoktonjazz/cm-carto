"""Подписи объектов из атрибута слоя (`label` в описании карты): номер квартала, индекс зоны, номер участка; проверка поля
при загрузке описания; площадь, которой нет места внутри, — подпись рядом с выноской (leader_to) на объекте."""

import json

import jsonschema
import pytest
from pyproj import CRS
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from test_render_map import CRS_NAME, OFFICIAL, X, Y, cache, describe, frames, layer, project, rows, side  # noqa: F401

from cm_carto import gpkg, paths, tokens
from cm_carto.render import build, overlay, spec as spec_file

BASE = {"territory": {"layer": "boundary_planning"}, "blocks": {}}
BLOCKS = [(f"b{i}{j}", box(X + i * 220, Y + j * 220, X + i * 220 + 200, Y + j * 220 + 200),
           {**OFFICIAL, "num": f"{i + 4}{j + 4}"}) for i in range(-2, 2) for j in range(-2, 2)]
# Участки: два крупных (20 × 20 мм на 1:10 000) и ряд из четырех тесных (3 × 3 мм через 0,5 мм): в тесном ряду подпись
# «10» кегля 10 pt шире и выше участка.
BIG = [(f"big{k}", box(X - 380 + k * 250, Y + 60, X - 180 + k * 250, Y + 260), {**OFFICIAL, "num": f"{k + 1}"})
       for k in range(2)]
TIGHT = [(f"tight{k}", box(X - 300 + k * 35, Y - 200, X - 270 + k * 35, Y - 170), {**OFFICIAL, "num": f"{k + 10}"})
         for k in range(4)]


@pytest.fixture(scope="module")
def attr_project(project):
    """Проект test_render_map с геопакетом, где кварталы с номерами и добавлены участки (слой sites)."""
    (project / "geo" / "project.gpkg").unlink()
    data = frames() | {"blocks": rows("blocks", BLOCKS), "sites": rows("sites", BIG + TIGHT)}
    gpkg.write(project / "geo" / "project.gpkg", data, "синтетический проект", CRS.from_user_input(CRS_NAME), CRS_NAME,
               "sha256:" + "0" * 64)
    return project


def render(root, name: str, layers: dict) -> dict:
    build.render(describe(root, name, layers=layers))
    doc = side(root, name, ".overlay.json")
    jsonschema.Draft202012Validator(json.loads(paths.schema("overlay").read_text(encoding="utf-8"))).validate(doc)
    return doc


def polygon(feature: dict) -> Polygon:
    return Polygon(feature["rings"][0], feature["rings"][1:])


def test_block_numbers_are_labels_inside_their_blocks(attr_project):
    """`label: num` у кварталов (стандарт правила подписи кварталов не задает — стиль `label_style`): подпись на слое
    labels_blocks, текст — номер квартала, подпись в центре своего квартала (якорь c, без выноски)."""
    doc = render(attr_project, "blocks", BASE | {"blocks": {"label": "num", "label_style": "label_major"}})
    labels = layer(doc, "labels_blocks")
    assert labels["role"] == "label" and labels["kind"] == "label"
    nums = {f"blocks:{key}": attrs["num"] for key, _, attrs in BLOCKS}
    assert len(labels["features"]) >= 4
    xmin, _, _, ymax = doc["map"]["extent"]
    shapes = {key: affinity.affine_transform(geom, overlay.to_mm(10000, (xmin, 0, 0, ymax)))
              for key, geom, _ in BLOCKS}
    for f in labels["features"]:
        assert f["text"] == nums[f["id"]] and f["size_role"] == "label_major" and f["anchor"] == "c"
        assert "leader_to" not in f
        assert shapes[f["id"].split(":")[1]].contains(Point(f["at"]))
    assert [item["id"] for item in doc["layers"]].index("labels_blocks") > [item["id"] for item in doc["layers"]].index("territory")


def test_label_field_must_be_in_contract_layer(attr_project):
    """Поле не из контракта слоя, неподходящего типа, стиль без правила стандарта и дубль растровой подписи — ошибки
    при загрузке описания: с путем описания и ролью."""
    def load(layers: dict) -> None:
        spec_file.load(describe(attr_project, "bad", layers=BASE | layers))

    with pytest.raises(ValueError, match=r"bad\.yaml: роль blocks: label height: в слое blocks нет такого атрибута"):
        load({"blocks": {"label": "height", "label_style": "label_major"}})
    with pytest.raises(ValueError, match=r"роль point_3: label confirmed_by: атрибут типа array"):
        spec_file.load(describe(attr_project, "bad", layers=BASE | {"point_3": {"layer": "poi_other",
                                                                          "label": "confirmed_by"}}))
    with pytest.raises(ValueError, match=r"роль blocks: в map.yaml у роли нет правила подписи, стиль .*label_style"):
        load({"blocks": {"label": "num"}})
    with pytest.raises(ValueError, match=r"роль blocks: label_style label_huge: нет в map.yaml: text.styles"):
        load({"blocks": {"label": "num", "label_style": "label_huge"}})
    with pytest.raises(ValueError, match=r"роль blocks: label_style без label"):
        load({"blocks": {"label_style": "label_major"}})
    with pytest.raises(ValueError, match=r"роль road_main: подписи роли уровня подложки рисует растр"):
        load({"road_main": {"layer": "roads", "label": "name"}})
    load({"blocks": {"label": "name", "label_style": "label_major"}})  # общее поле name допустимо


def test_label_replaces_standard_field_and_keeps_rule(attr_project):
    """Индекс зоны: правило стандарта (map.yaml: zoning.label — стиль label_minor, min_size 12, repeat 45) остается,
    `label` заменяет только поле. Малые зоны (меньше 12 × 12 мм) не подписываются, как и без `label` в описании."""
    layers = BASE | {"zoning": {"layer": "zones_pzz", "label": "code"}}
    standard = spec_file.load(describe(attr_project, "std", layers=BASE | {"zoning": {"layer": "zones_pzz"}}))
    zoning = next(r for r in spec_file.load(describe(attr_project, "zone", layers=layers)).roles if r.key == "zoning")
    assert zoning.label == next(r for r in standard.roles if r.key == "zoning").label == tokens.load("map")["zoning"]["label"]
    doc = render(attr_project, "zone", layers)
    labels = layer(doc, "labels_zoning")["features"]
    assert [(f["id"], f["text"], f["size_role"]) for f in labels] == [("zones_pzz:zh", "Ж-1", "label_minor")]
    other = render(attr_project, "zone-category", BASE | {"zoning": {"layer": "zones_pzz", "label": "zone"}})
    assert [f["text"] for f in layer(other, "labels_zoning")["features"]] == ["Ж"]


def test_tight_areas_get_leader_on_the_object(attr_project, capsys):
    """Тесный ряд участков: подпись «10» шире участка 3 × 3 мм, поэтому она не встает внутрь; крупный участок
    подписан в центре. Тесные участки получают подпись рядом вне участка (якорь не c) с выноской на объект; подписи не
    пересекаются друг с другом, объектами и выносками."""
    layers = BASE | {"parcels": {"layer": "sites", "label": "num"}}
    doc = render(attr_project, "tight", layers)
    labels = {f["id"]: f for f in layer(doc, "labels_parcels")["features"]}
    sites = {f["id"]: polygon(f) for f in layer(doc, "parcels")["features"]}
    assert set(labels) == set(sites) and not [line for line in capsys.readouterr().out.splitlines() if "не встала" in line]
    big = [f for key, f in labels.items() if key.startswith("sites:big")]
    tight = [f for key, f in labels.items() if key.startswith("sites:tight")]
    assert all(f["anchor"] == "c" and "leader_to" not in f for f in big)
    assert tight and all("leader_to" in f and f["anchor"] != "c" for f in tight)
    tolerance = overlay.ROUND_MM
    assert all(sites[f["id"]].buffer(tolerance).intersects(Point(f["leader_to"])) for f in tight)
    std, style = tokens.load("map"), tokens.load("style")
    measure = overlay.Measure(std, style)
    plate = (std["text"]["plate"]["padding_x"], std["text"]["plate"]["padding_y"])
    boxes = {key: overlay.text_box(f["at"], f["anchor"], measure.size(f["text"], f["size_role"]),
                                   (0, 0) if f["anchor"] == "c" else plate) for key, f in labels.items()}
    assert not any(a.intersects(b) for i, a in enumerate(boxes.values()) for b in list(boxes.values())[i + 1:])
    for key, f in labels.items():
        if "leader_to" in f:
            assert not boxes[key].intersects(sites[key])  # подпись вне своего участка
            line = overlay.LineString([f["leader_to"], f["at"]])
            assert not any(line.intersects(b) for other, b in boxes.items() if other != key)  # выноска не задевает чужие подписи
