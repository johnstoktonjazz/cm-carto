"""ВРЕМЕННО, вместе с cm_carto/render/deck_overlay.py: перевод overlay по контракту в черновой формат cm-deck.

Образцы К1 (examples/taganrog/maps/images/*.overlay.json) и образец с выноской, подписью в две строки, подписью зоны
и стрелкой севера после перевода проходят cm_deck.overlay.check и рисуются cm_deck.overlay.draw. cm_deck — из клона
верстки (CM_DESIGN или ../cm-design), только чтение: байткод в его дерево не пишется.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

from cm_carto import paths, tokens
from cm_carto.render import deck_overlay

IMAGES = Path(__file__).resolve().parents[1] / "examples" / "taganrog" / "maps" / "images"
SAMPLES = sorted(IMAGES.glob("*.overlay.json"))


@pytest.fixture(scope="module")
def deck():
    write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(paths.design()))
    try:
        from cm_deck import overlay
    except ImportError as e:
        pytest.skip(f"нет cm_deck в клоне верстки: {e}")
    finally:
        sys.path.remove(str(paths.design()))
        sys.dont_write_bytecode = write
    return overlay


def sample(name: str) -> dict:
    return json.loads((IMAGES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", SAMPLES, ids=[p.name for p in SAMPLES])
def test_samples_pass_deck_check(deck, path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = deck_overlay.translate(doc)
    deck.check(out)
    data = [f for layer in doc["layers"] if layer["kind"] in deck_overlay.DATA_KINDS for f in layer["features"]]
    assert len([e for e in out["elements"] if e["kind"] in ("polygon", "polyline", "point")]) == len(data)
    legend = next(layer for layer in doc["layers"] if layer["kind"] == "legend")
    assert [row["label"] for row in out["legend"]] == [row["text"] for row in legend["legend"]
                                                       if row.get("kind") != "group"]
    assert out["legend_inside"] == ("legend_at" in legend)


def test_zoning_and_scalebar(deck):
    out = deck_overlay.translate(sample("k1-zoning.overlay.json"))
    zones = {e["layer"] for e in out["elements"] if e["layer"].startswith("zoning")}
    assert zones and all(z.startswith("zoning:") for z in zones)
    texts = [e for e in out["elements"] if e["kind"] == "text"]
    assert texts and all(e["layer"].startswith("zoning:") for e in texts)  # индекс зоны — на слое своей категории
    bar = sample("k1-zoning.overlay.json")["layers"][-1]["scalebar"]
    assert out["scalebar"]["at_mm"] == bar["at"]
    assert out["scalebar"]["label"] == f"{bar['segments_m'][-1]:g} м"


def test_leader_multiline_north(deck):
    doc = sample("k1-analysis.overlay.json")
    doc = copy.deepcopy(doc)
    labels = next(layer for layer in doc["layers"] if layer["kind"] == "label" and
                  any("\n" in f["text"] for f in layer["features"]))
    two = next(f for f in labels["features"] if "\n" in f["text"])
    point = next(f for layer in doc["layers"] if layer["kind"] == "point" for f in layer["features"]
                 if f["id"] == two["id"])
    two["leader_to"] = point["at"]
    doc["layers"].append({"id": "north_arrow", "role": "north_arrow", "kind": "north", "source_label": "север",
                          "features": [{"id": "north_arrow", "at": [10.0, 10.0]}]})
    out = deck_overlay.translate(doc)
    deck.check(out)
    callout = next(e for e in out["elements"] if e["kind"] == "callout")
    rest = [e for e in out["elements"] if e["kind"] == "text" and e["layer"] == "leader"]
    lines = two["text"].split("\n")
    assert callout["layer"] == "leader" and callout["at_mm"] == point["at"] and callout["text"] == lines[0]
    assert [e["text"] for e in rest] == lines[:0:-1]  # нижняя строка — первой
    std = tokens.load("map")
    step = rest[-1]["at_mm"][1] - callout["to_mm"][1]
    assert step == pytest.approx(std["text"]["styles"][two["size_role"]]["size"] * 1.362 * 25.4 / 72, abs=0.02)
    assert out["north"]["at_mm"] == [10.0, 10.0]  # правый нижний угол знака в обоих форматах

    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    size = out["size_mm"]
    group = deck.draw(slide, out, (0, 0, size["width"] * 72 / 25.4, size["height"] * 72 / 25.4))
    names = [s.name for s in group.shapes]
    assert any(":leader:" in n for n in names) and any(n.endswith(":north:arrow") for n in names)


def test_unknown_kind():
    doc = sample("k1-analysis.overlay.json")
    doc["layers"].append({"id": "x", "role": "x", "kind": "raster", "source_label": "x"})
    with pytest.raises(ValueError, match="вид raster"):
        deck_overlay.translate(doc)


def test_grouped_legend_passes_deck(deck):
    """Легенда с 9 строк — группы: строки kind: group переводчик пропускает, check верстки принимает перевод, а ее
    раскладка ставит заголовки тех же уровней map.yaml: levels, что в overlay."""
    doc = copy.deepcopy(sample("k1-zoning.overlay.json"))
    legend = next(layer for layer in doc["layers"] if layer["kind"] == "legend")
    std, style = tokens.load("map"), tokens.load("style")
    cats = list(std["zoning"]["categories"])
    rows = [{"role": "analysis", "kind": "group", "text": std["levels"]["analysis"]["title"]},
            {"role": "point_1", "kind": "point", "symbol": "circle", "text": "Школы"},
            {"role": "reach_walk", "kind": "polygon", "text": "10 минут пешком"},
            {"role": "boundaries", "kind": "group", "text": std["levels"]["boundaries"]["title"]},
            {"role": "territory", "kind": "polygon", "text": "Граница"}]
    rows += [{"role": "zoning", "kind": "polygon", "category": c, "text": std["zoning"]["categories"][c]["title"]}
             for c in cats]
    legend["legend"] = rows
    out = deck_overlay.translate(doc)
    deck.check(out)
    assert len(out["legend"]) == len(rows) - 2 >= std["legend"]["groups_from"]
    items, _, _ = deck._legend_items(out["legend"], std, style, {})
    assert [i[2] for i in items if i[0] == "group"] == [r["text"] for r in rows if r["kind"] == "group"]
