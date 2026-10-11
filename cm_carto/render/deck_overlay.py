"""ВРЕМЕННО, до перехода cm-deck на контракт overlay; удалить вместе с вызовом (scripts/build_k1.sh).

Переводчик <карта>.overlay.json (контракт overlay 1.1.0) → <карта>.deck-overlay.json — черновой формат, который
разбирает cm_deck/overlay.py верстки (check, draw; описание — в шапке того модуля). Наш overlay.json остается
по контракту, переводчик его не меняет.

Соответствие:
- слой данных: роль → layer; зона (role zoning, category Ж) → «zoning:Ж»; polygon (rings: первое — внешнее, остальные — дыры) →
  polygon points_mm/holes_mm; line (path) → polyline; point (at) → point at_mm;
- подпись (kind label): строка — элемент text (у верстки текст в одну строку: середина строки на at_mm, по
  горизонтали по anchor l/c/r), положение строк — как расставил картограф, нижняя строка — первой (плашки); подпись с выноской (leader_to) — callout
  на слое leader (точка на объекте — at_mm, у конца выноски — первая строка), остальные строки — text на том же
  слое; кегль — style = size_role; подпись зон — слой категории своей зоны;
- легенда: строки {layer, label}; строки kind: group пропускаются — группы по уровням с 9 строк верстка ставит
  сама (map.yaml: legend.groups_from, те же заголовки map.yaml: levels); legend_inside — есть legend_at;
- scale_bar → scalebar {at_mm, length_mm, label}: at — левый конец линии в обоих форматах; north_arrow: at — правый
  нижний угол знака в обоих форматах;
- id элемента — object_id, «:» → «.»; части одного объекта — «-2», «-3».

    scripts/run.sh python -m cm_carto.render.deck_overlay <карта>.overlay.json [...]
"""

import json
import sys
from pathlib import Path

from cm_carto import tokens
from cm_carto.render.overlay import ANCHOR, Measure, number

DATA_KINDS = ("polygon", "line", "point")
SUFFIX = ".deck-overlay.json"


def layer_key(role: str, category: str | None = None) -> str:
    """Роль и категория контракта → слой верстки: zoning, Ж → «zoning:Ж»."""
    return f"{role}:{category}" if category else role


def element_id(object_id: str, seen: set, layer: str) -> str:
    base = object_id.replace(":", ".")
    ident, n = base, 1
    while (layer, ident) in seen:
        n += 1
        ident = f"{base}-{n}"
    seen.add((layer, ident))
    return ident


def lines_of(feature: dict, measure: Measure, std: dict) -> list[tuple[str, float, float, str]]:
    """Строки подписи: (текст, x, y середины строки, anchor l/c/r) — по рамке, которую поставил картограф."""
    text, style = feature["text"], feature.get("size_role", "label")
    width, height = measure.size(text, style)
    ax, ay = ANCHOR[feature["anchor"]]
    x0, y0 = feature["at"][0] - ax * width, feature["at"][1] - ay * height
    rows = text.split("\n")
    step = height / len(rows)
    horizontal = {0: ("l", x0), 0.5: ("c", x0 + width / 2), 1: ("r", x0 + width)}[ax]
    return [(row, horizontal[1], y0 + (i + 0.5) * step, horizontal[0]) for i, row in enumerate(rows)]


def translate(doc: dict, std: dict | None = None, style: dict | None = None) -> dict:
    """overlay по контракту → формат cm_deck.overlay. Неизвестный вид слоя или несовпадение делений линейки —
    ValueError."""
    std = std or tokens.load("map")
    style = style or tokens.load("style")
    measure = Measure(std, style)
    data = [layer for layer in doc["layers"] if layer["kind"] in DATA_KINDS]
    # Подпись зоны — на слое категории своей зоны: object_id → слой верстки.
    owner = {(layer["role"], f["id"]): layer_key(layer["role"], f.get("category"))
             for layer in data for f in layer["features"]}
    elements, seen = [], set()
    out = {"place": doc["place"]["name"],
           "size_mm": dict(doc["place"]["size_mm"]),
           "crs": doc["map"]["crs"],
           "scale": f"1:{doc['map']['scale']}",
           "elements": elements,
           "legend": [],
           "legend_inside": False,
           "layers": [],
           "fact_ids": sorted({f for layer in doc["layers"] for f in layer.get("fact_ids", [])})}
    for layer in doc["layers"]:
        kind = layer["kind"]
        if kind in DATA_KINDS:
            keys = []
            for f in layer["features"]:
                key = layer_key(layer["role"], f.get("category"))
                if key not in keys:
                    keys.append(key)
                el = {"id": element_id(f["id"], seen, key), "layer": key}
                if kind == "polygon":
                    el |= {"kind": "polygon", "points_mm": f["rings"][0]}
                    if len(f["rings"]) > 1:
                        el["holes_mm"] = f["rings"][1:]
                elif kind == "line":
                    el |= {"kind": "polyline", "points_mm": f["path"]}
                else:
                    el |= {"kind": "point", "at_mm": f["at"]}
                elements.append(el)
            out["layers"] += [{"name": key, "source": layer["source_label"], "status": layer.get("status", "")}
                              for key in keys]
        elif kind == "label":
            for f in layer["features"]:
                key = owner.get((layer["role"], f["id"]), layer_key(layer["role"]))
                style_key = f.get("size_role", "label")
                rows, callout = lines_of(f, measure, std), []
                if "leader_to" in f:
                    key = "leader"
                    text, x, y, anchor = rows[0]
                    gap = std["text"]["placement"]["distance"]
                    if anchor == "c":
                        to = [x, y + measure.size(text, style_key)[1] / 2 + gap]
                    else:
                        to = [x - gap if anchor == "l" else x + gap, y]
                    callout = [{"kind": "callout", "id": element_id(f["id"], seen, key), "layer": key,
                                "at_mm": f["leader_to"], "to_mm": [round(v, 2) for v in to], "text": text,
                                "anchor": anchor, "style": style_key}]
                    rows = rows[1:]
                # Нижняя строка — первой: у каждой строки своя плашка, и плашка верхней строки ложится на поле над
                # буквами нижней, а не на низ букв верхней (верстка рисует подписи одного слоя в порядке элементов).
                for text, x, y, anchor in reversed(rows):
                    elements.append({"kind": "text", "id": element_id(f["id"], seen, key), "layer": key,
                                     "at_mm": [round(x, 2), round(y, 2)], "text": text, "anchor": anchor,
                                     "style": style_key})
                elements += callout  # первая строка с выноской — последней, поверх плашек нижних строк
        elif kind == "legend":
            out["legend"] = [{"layer": layer_key(row["role"], row.get("category")), "label": row["text"]}
                             for row in layer["legend"] if row.get("kind") != "group"]
            out["legend_inside"] = "legend_at" in layer
        elif kind == "scalebar":
            bar = layer["scalebar"]
            segments = len(bar["segments_m"]) - 1
            if segments != std["scale_bar"]["segments"]:
                raise ValueError(f"линейка: {segments} деления, у верстки map.yaml: scale_bar.segments = "
                                 f"{std['scale_bar']['segments']}")
            label = number(bar["segments_m"][-1], style) + style["number_format"]["unit_separator"] \
                + std["scale_bar"]["unit_label"]
            out["scalebar"] = {"at_mm": bar["at"], "length_mm": round(bar["segment_mm"] * segments, 2),
                               "label": label}
        elif kind == "north":
            out["north"] = {"at_mm": layer["features"][0]["at"]}
        else:
            raise ValueError(f"слой {layer['id']}: вид {kind} переводчик не знает")
    return out


def write(path: Path) -> Path:
    """<карта>.overlay.json → <карта>.deck-overlay.json рядом."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    target = path.with_name(path.name.removesuffix(".overlay.json") + SUFFIX)
    target.write_text(json.dumps(translate(doc), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(write(Path(arg)))
