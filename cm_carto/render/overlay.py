"""Слои данных карты по контракту overlay 1.0.0 (cm-contracts/contracts/overlay): все, что на карте кроме подложки,
в миллиметрах места макета — верстка рисует это фигурами и текстом PowerPoint.

Координаты — мм от левого верхнего угла места, y вниз. Контуры обрезаны по рамке места, упрощены (Дуглас — Пекер,
SIMPLIFY_MM) и привязаны к сетке ROUND_MM; кольца с дырами сохраняются; составной объект — по фиче на часть с одним
id (object_id). Цветов, толщин и кеглей в файле нет: стиль — по role из map.yaml верстки.

Как имена совпадают с map.yaml (confirm 1 контракта): role слоя данных — ключ map.yaml: layers; зоны ПЗЗ — слой на
категорию, role «zoning.<категория>», id «zoning_<транслит>»; size_role подписи — ключ map.yaml: text.styles;
symbol — map.yaml: layers.<роль>.marker.shape; служебные слои — role legend, scale_bar, north_arrow.

Подписи расставляет картограф (верстка их не двигает): по правилу стиля роли (max_features, min_size), позиции
вокруг знака в порядке стилей верстки (справа сверху, слева сверху, справа снизу, слева снизу, справа, слева) с
отступом text.placement.distance от края знака; подпись не задевает другие подписи, подписи подложки, знаки,
границу территории, линейку, север, легенду внутри и край места. Не встала — отнесена с выноской (leader_to); не встала и так — в лог.
Размер текста — по метрике шрифта из style.yaml: font.files (кегль text.styles, перенос text.wrap_chars).
"""

import json
import math
from functools import cache

import jsonschema
import shapely
from PIL import ImageFont
from shapely import affinity
from shapely.geometry import LineString, Point, box

from cm_carto import paths

SIMPLIFY_MM = 0.1
ROUND_MM = 0.01
PT_MM = 25.4 / 72
METRIC_PX = 1000  # кегль шрифта при замере: ширина в мм = замер / METRIC_PX × кегль в мм
# Опорная точка элемента компоновки QGIS (referencePoint): доли ширины и высоты от левого верхнего угла.
REFERENCE = {0: (0, 0), 1: (0.5, 0), 2: (1, 0), 3: (0, 0.5), 4: (0.5, 0.5), 5: (1, 0.5), 6: (0, 1), 7: (0.5, 1),
             8: (1, 1)}
NICE = [1, 2, 2.5, 5, 10]
# Позиции подписи у точки, как predefinedPositionOrder стилей верстки (TR, TL, BR, BL, R, L): направление от знака
# и какой угол текста стоит в точке привязки.
AROUND = [((1, -1), "sw"), ((-1, -1), "se"), ((1, 1), "nw"), ((-1, 1), "ne"), ((1, 0), "w"), ((-1, 0), "e")]
ANCHOR = {"nw": (0, 0), "n": (0.5, 0), "ne": (1, 0), "w": (0, 0.5), "c": (0.5, 0.5), "e": (1, 0.5),
          "sw": (0, 1), "s": (0.5, 1), "se": (1, 1)}
LEADER_STEPS = [3.0, 6.0]  # относ подписи с выноской, мм сверх обычного отступа
CENTROID_GRID_MM = 2.0    # шаг запасных точек подписи площади внутри контура
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "iu", "ia"]))


@cache
def schema() -> dict:
    return json.loads(paths.schema("overlay").read_text(encoding="utf-8"))


def ident(text: str) -> str:
    """Имя слоя по шаблону контракта ^[a-z][a-z0-9_]*$: транслит кириллицы, прочее — «_»."""
    return "".join(TRANSLIT.get(c, "_") if not c.isascii() else (c if c.isalnum() else "_") for c in text.lower())


def mm(value: float) -> float:
    return round(value, 2) + 0.0  # + 0.0: -0.0 → 0.0


def xy(point) -> list[float]:
    return [mm(point[0]), mm(point[1])]


def to_mm(scale: int, rect) -> list[float]:
    """Аффинное преобразование shapely: система проекта → мм места (y вниз)."""
    xmin, _, _, ymax = rect
    k = 1000 / scale
    return [k, 0, 0, -k, -xmin * k, ymax * k]


def geometry_mm(geom, matrix: list[float], frame):
    """Геометрия в мм места: обрезка по рамке, упрощение, сетка ROUND_MM (кольца и дыры сохраняются)."""
    clipped = affinity.affine_transform(geom, matrix).intersection(frame)
    if clipped.geom_type not in ("Point", "MultiPoint"):
        clipped = clipped.simplify(SIMPLIFY_MM, preserve_topology=True)
    return shapely.set_precision(clipped, ROUND_MM)


def parts(geom) -> list:
    if geom.is_empty:
        return []
    if geom.geom_type.startswith("Multi") or geom.geom_type == "GeometryCollection":
        return [p for g in geom.geoms for p in parts(g)]
    return [geom]


def shape(part) -> dict:
    """Часть геометрии → поля фичи контракта: rings, path или at."""
    if part.geom_type == "Polygon":
        return {"rings": [[xy(c) for c in ring.coords] for ring in [part.exterior, *part.interiors]]}
    if part.geom_type == "LineString":
        return {"path": [xy(c) for c in part.coords]}
    return {"at": xy((part.x, part.y))}


@cache
def font(path: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, METRIC_PX)


class Measure:
    """Размер текста в мм по метрике шрифта верстки (style.yaml: font.files) и кеглю роли (map.yaml: text.styles)."""

    def __init__(self, std: dict, style: dict):
        self.std, self.style = std, style

    def font(self, style_key: str):
        text = self.std["text"]["styles"][style_key]
        path = paths.design() / self.style["font"]["files"][text["weight"].lower()]
        return font(str(path)), text["size"] * PT_MM

    def wrap(self, text: str) -> str:
        """Перенос по словам, строка не длиннее text.wrap_chars знаков (как autoWrapLength стилей верстки)."""
        limit, lines = self.std["text"]["wrap_chars"], []
        for word in text.split():
            if lines and len(lines[-1]) + 1 + len(word) <= limit:
                lines[-1] += " " + word
            else:
                lines.append(word)
        return "\n".join(lines)

    def size(self, text: str, style_key: str) -> tuple[float, float]:
        f, size = self.font(style_key)
        ascent, descent = f.getmetrics()
        lines = text.split("\n")
        width = max(f.getlength(line) for line in lines) / METRIC_PX * size
        return width, len(lines) * (ascent + descent) / METRIC_PX * size


def text_box(at, anchor: str, size, plate):
    ax, ay = ANCHOR[anchor]
    x0, y0 = at[0] - ax * size[0], at[1] - ay * size[1]
    return box(x0 - plate[0], y0 - plate[1], x0 + size[0] + plate[0], y0 + size[1] + plate[1])


def element_box(item, size) -> tuple[float, float, float, float]:
    """Прямоугольник элемента компоновки map_<место>.qpt размера size, мм: (x0, y0, x1, y1)."""
    point, (rx, ry) = item.positionWithUnits(), REFERENCE[item.referencePoint()]
    x0, y0 = point.x() - rx * size[0], point.y() - ry * size[1]
    return x0, y0, x0 + size[0], y0 + size[1]


def nice_step(scale: int, width_mm: list[float]) -> float:
    """Деление линейки в метрах: наименьшее круглое (1, 2, 2,5, 5 × 10^n) не короче width_mm[0] мм на листе."""
    least = width_mm[0] * scale / 1000
    power = 10 ** math.floor(math.log10(least))
    step = next(f * power for f in NICE if f * power >= least)
    if step * 1000 / scale > width_mm[1]:
        raise ValueError(f"деление линейки {step} м шире {width_mm[1]} мм при 1:{scale} (map.yaml: scale_bar)")
    return step


def number(value: float, style: dict) -> str:
    """Число по style.yaml: number_format: разряды и десятичный знак."""
    whole, _, fraction = f"{value:.10g}".partition(".")
    whole = f"{int(whole):,}".replace(",", style["number_format"]["thousands"])
    return whole + (style["number_format"]["decimal"] + fraction if fraction else "")


class Overlay:
    def __init__(self, spec, std: dict, style: dict, layout, rect):
        self.spec, self.std, self.style, self.layout = spec, std, style, layout
        self.matrix = to_mm(spec.scale, rect)
        self.frame = box(0, 0, *spec.size_mm)
        self.measure = Measure(std, style)
        self.plate = (std["text"]["plate"]["padding_x"], std["text"]["plate"]["padding_y"])
        self.obstacles: list = []      # знаки, граница территории, линейка, север, легенда внутри
        self.placed: list = []         # подписи
        self.unplaced: list[str] = []

    # Слои данных

    def data_layer(self, layer_id: str, role: str, kind: str, rows: list[dict], entry: dict, spec_role: dict) -> dict:
        features = []
        for row in sorted(rows, key=lambda r: r["object_id"]):
            for part in parts(row["mm"]):
                feature = {"id": row["object_id"]}
                if row.get("name"):
                    feature["name"] = str(row["name"])
                feature |= shape(part)
                if kind == "point":
                    feature["symbol"] = spec_role["marker"]["shape"]
                features.append(feature)
        layer = {"id": layer_id, "role": role, "kind": kind, "source_label": entry["source"], "status": entry["status"],
                 "fact_ids": sorted({f for row in rows for f in json.loads(row["fact_ids"] or "[]")})}
        if kind != "point":
            layer["simplify_mm"] = SIMPLIFY_MM
        return layer | {"features": features}

    def data_layers(self, drawn: list, entries: dict) -> list[dict]:
        """Слои ролей уровней analysis, objects, boundaries снизу вверх (обратный порядок стандарта)."""
        layers = []
        for d in reversed(drawn):
            for row in d.rows:
                row["mm"] = geometry_mm(row["geom"], self.matrix, self.frame)
            rows = [row for row in d.rows if not row["mm"].is_empty]
            kind = d.spec["geometry"]
            if d.role.key == "zoning":
                field = self.std["zoning"]["field"]
                for value, _ in d.categories():
                    part = [row for row in rows if row[field] == value]
                    layers.append(self.data_layer(ident(f"zoning_{value}"), f"zoning.{value}", kind, part,
                                                  entries[d.role.key], d.spec))
            else:
                layers.append(self.data_layer(d.role.key, d.role.key, kind, rows, entries[d.role.key], d.spec))
            if kind == "point":
                r = d.spec["marker"]["size"] / 2
                self.obstacles += [box(row["mm"].x - r, row["mm"].y - r, row["mm"].x + r, row["mm"].y + r)
                                   for row in rows]
            if d.role.key == "territory":
                self.obstacles += [LineString(ring.coords) for row in rows for p in parts(row["mm"])
                                   for ring in [p.exterior, *p.interiors]]
        return layers

    # Служебные слои

    def legend_layer(self, items: list, entries: dict) -> dict:
        """Строки легенды: items — (роль, роль map.yaml, категории) в порядке стандарта (build.legend: у карт
        сравнения — общие на группу); у роли с категориями — строка на категорию."""
        rows = []
        for role, spec_role, categories in items:
            kind = spec_role["geometry"]
            if role.key == "zoning":
                rows += [{"role": f"zoning.{value}", "kind": kind, "text": title} for value, title in categories]
                continue
            row = {"role": role.key, "kind": kind, "text": role.title}
            if kind == "point":
                row["symbol"] = spec_role["marker"]["shape"]
            rows.append(row)
        rule = self.std["legend"]
        if not rows:
            raise ValueError("в легенде нет строк: на карте только подложка")
        if len(rows) > rule["max_items"]:
            raise ValueError(f"в легенде {len(rows)} строк, предел {rule['max_items']}: делить карту (map.yaml: legend)")
        if len(rows) >= rule["groups_from"]:
            raise ValueError(f"в легенде {len(rows)} строк: с {rule['groups_from']} строк — группы по уровням, "
                             f"а у overlay 1.0.0 нет строки-заголовка группы (вопрос к контракту)")
        sources = []
        for entry in entries.values():
            sources += [s for s in entry["source"].split("; ") if s not in sources]
        layer = {"id": "legend", "role": "legend", "kind": "legend", "source_label": "; ".join(sources), "legend": rows}
        if self.spec.legend == "inside":  # левый верхний угол плашки; низ — по точке элемента legend компоновки
            inside = rule["inside"]
            height = len(rows) * rule["swatch"][1] + (len(rows) - 1) * rule["item_gap"] + 2 * inside["padding"]
            width = rule["swatch"][0] + rule["swatch_text_gap"] + max(
                self.measure.size(row["text"], rule["text"])[0] for row in rows) + 2 * inside["padding"]
            x0, y0, x1, y1 = element_box(self.layout.itemById("legend"), (width, height))
            layer["legend_at"] = xy((x0, y0))
            self.obstacles.append(box(x0, y0, x1, y1))
        return layer

    def scale_bar_layer(self) -> dict:
        """Линейка: at — левый конец линии; плашка — по точке элемента scalebar компоновки (map.yaml: scale_bar)."""
        rule = self.std["scale_bar"]
        step = nice_step(self.spec.scale, rule["segment_width"])
        segments = [i * step for i in range(rule["segments"] + 1)]
        labels = [number(s, self.style) for s in segments]
        labels[-1] += self.style["number_format"]["unit_separator"] + rule["unit_label"]
        segment = step * 1000 / self.spec.scale
        first = self.measure.size(labels[0], rule["text"])
        last = self.measure.size(labels[-1], rule["text"])
        pad = rule["plate_padding"]
        width = first[0] / 2 + segment * rule["segments"] + last[0] / 2 + 2 * pad
        height = 2 * pad + last[1] + rule["label_gap"] + rule["tick_height"] + rule["line"]["width"]
        x0, y0, x1, y1 = element_box(self.layout.itemById("scalebar"), (width, height))
        self.obstacles.append(box(x0, y0, x1, y1))
        at = (x0 + pad + first[0] / 2, y1 - pad - rule["line"]["width"] / 2)
        return {"id": "scale_bar", "role": "scale_bar", "kind": "scalebar",
                "source_label": f"масштаб 1:{number(self.spec.scale, self.style)}",
                "scalebar": {"at": xy(at), "segments_m": segments, "segment_mm": mm(segment)}}

    def north_layer(self) -> dict:
        """Стрелка севера: at — левый верхний угол знака размера map.yaml: north_arrow.size."""
        x0, y0, x1, y1 = element_box(self.layout.itemById("north"), self.std["north_arrow"]["size"])
        self.obstacles.append(box(x0, y0, x1, y1))
        return {"id": "north_arrow", "role": "north_arrow", "kind": "north", "source_label": "север вверху",
                "features": [{"id": "north_arrow", "at": xy((x0, y0))}]}

    # Подписи

    def free(self, shape_) -> bool:
        return (self.frame.contains(shape_) and not any(shape_.intersects(o) for o in self.obstacles)
                and not any(shape_.intersects(p) for p in self.placed))

    def place(self, d, row: dict, text: str, style_key: str):
        """Место подписи одного объекта: (at, anchor, leader_to или None) или None, если не встала."""
        size = self.measure.size(text, style_key)
        plate = (0, 0) if style_key in self.std["text"]["plate"]["except_styles"] else self.plate
        rule = d.spec["label"]
        placement = rule.get("placement", self.std["text"]["placement"][d.spec["geometry"]])
        geom = row["mm"]
        if placement == "along":
            raise ValueError(f"роль {d.role.key}: подпись вдоль линии overlay 1.0.0 не выражает (нет угла)")
        if placement == "center":  # номер в знаке точки
            candidates = [((geom.x, geom.y), "c", None)]
        elif placement == "centroid":  # внутри контура: точка на поверхности, затем ближайшие к ней узлы сетки
            point = geom.point_on_surface()
            x0, y0, x1, y1 = geom.bounds
            grid = [Point(x0 + i * CENTROID_GRID_MM, y0 + j * CENTROID_GRID_MM)
                    for i in range(int((x1 - x0) / CENTROID_GRID_MM) + 1)
                    for j in range(int((y1 - y0) / CENTROID_GRID_MM) + 1)]
            inside = sorted((p for p in grid if geom.contains(p)), key=lambda p: (p.distance(point), p.x, p.y))
            candidates = [((p.x, p.y), "c", None) for p in [point, *inside]]
        else:  # around
            step = d.spec["marker"]["size"] / 2 + self.std["text"]["placement"]["distance"]
            candidates = [((geom.x + dx * step, geom.y + dy * step), anchor, None) for (dx, dy), anchor in AROUND]
            candidates += [((geom.x + dx * (step + extra), geom.y + dy * (step + extra)), anchor, (geom.x, geom.y))
                           for extra in LEADER_STEPS for (dx, dy), anchor in AROUND]
        for at, anchor, leader in candidates:
            shape_ = text_box(at, anchor, size, plate)
            if self.free(shape_):
                self.placed.append(shape_)
                return at, anchor, leader
        return None

    def label_layers(self, drawn: list, entries: dict) -> list[dict]:
        """Подписи по правилам стилей ролей; расставляются от верхней роли стандарта, внутри роли — по object_id."""
        layers = []
        for d in (d for d in drawn if "label" in d.spec):
            rule = d.spec["label"]
            rows = sorted((row for row in d.rows if not row["mm"].is_empty), key=lambda r: r["object_id"])
            if len(rows) > rule.get("max_features", len(rows)):
                continue
            features = []
            for row in rows:
                text, geom = row.get(rule["field"]), row["mm"]
                if text in (None, ""):
                    continue
                if "min_size" in rule and geom.area < rule["min_size"] ** 2:  # как minFeatureSize QGIS у площадей
                    continue
                text = self.measure.wrap(str(text))
                spot = self.place(d, row, text, rule["style"])
                if spot is None:
                    self.unplaced.append(f"{d.role.key} {row['object_id']} «{text}»")
                    continue
                at, anchor, leader = spot
                feature = {"id": row["object_id"], "text": text, "at": xy(at), "anchor": anchor,
                           "size_role": rule["style"]}
                if leader:
                    feature["leader_to"] = xy(leader)
                features.append(feature)
            if features:
                layers.append({"id": f"labels_{d.role.key}", "role": d.role.key, "kind": "label",
                               "source_label": entries[d.role.key]["source"], "features": features})
        return list(reversed(layers))


def build(spec, std: dict, style: dict, drawn: list, legend_items: list, entries: dict, north: bool, layout, rect,
          basemap: str, basemap_labels: list) -> tuple[dict, list[str]]:
    """overlay.json по контракту и подписи, которые не встали без наездов. legend_items — строки легенды
    (build.legend); basemap_labels — рамки подписей подложки в мм: подписи overlay их не задевают."""
    data = [d for d in drawn if d.role.level != "basemap"]
    o = Overlay(spec, std, style, layout, rect)
    o.obstacles += [box(*r) for r in basemap_labels]
    layers = o.data_layers(data, entries)
    service = [o.scale_bar_layer()] + ([o.north_layer()] if north else [])
    legend = o.legend_layer(legend_items, entries)
    labels = o.label_layers(data, entries)
    xmin, ymin, xmax, ymax = rect
    doc = {"basemap": basemap,
           "place": {"name": spec.place, "size_mm": {"width": spec.size_mm[0], "height": spec.size_mm[1]}},
           "map": {"crs": spec.project.crs_name, "extent": [mm(xmin), mm(ymin), mm(xmax), mm(ymax)],
                   "scale": spec.scale, "rotation_deg": 0},
           "layers": layers + labels + [legend] + service}
    errors = [f"{'/'.join(map(str, e.absolute_path))}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema()).iter_errors(doc)]
    if errors:
        raise ValueError("overlay не проходит контракт overlay:\n  " + "\n  ".join(errors[:20]))
    return doc, o.unplaced


def dumps(doc: dict) -> str:
    """Ключи по алфавиту; слой — блоком, фича и строка легенды — одной строкой: файл сравнивается построчно."""
    def compact(value) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))

    def layer(item: dict) -> str:
        fields = []
        for key in sorted(item):
            if key in ("features", "legend"):
                fields.append(f'   "{key}": [\n' + ",\n".join(f"    {compact(v)}" for v in item[key]) + "\n   ]")
            else:
                fields.append(f'   "{key}": {compact(item[key])}')
        return "  {\n" + ",\n".join(fields) + "\n  }"

    head = [f' "{key}": {compact(doc[key])}' for key in sorted(doc) if key != "layers"]
    return "{\n" + ",\n".join(head) + ',\n "layers": [\n' + ",\n".join(layer(i) for i in doc["layers"]) + "\n ]\n}\n"
