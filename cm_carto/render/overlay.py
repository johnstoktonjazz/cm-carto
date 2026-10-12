"""Слои данных карты по контракту overlay 1.1.0 (cm-contracts/contracts/overlay): все, что на карте кроме подложки,
в миллиметрах места макета — верстка рисует это фигурами и текстом PowerPoint.

Координаты — мм от левого верхнего угла места, y вниз. Контуры обрезаны по рамке места, упрощены (Дуглас — Пекер,
SIMPLIFY_MM) и привязаны к сетке ROUND_MM; кольца с дырами сохраняются; составной объект — по фиче на часть с одним
id (object_id); у фичи — status и fact_ids объекта из геопакета. Цветов, толщин и кеглей в файле нет: стиль — по
role из map.yaml верстки.

Как имена совпадают с map.yaml: role слоя данных — ключ map.yaml: layers; зоны ПЗЗ — слой на категорию, role
«zoning», category у фичи и строки легенды, id «zoning_<транслит>»; один слой данных — один источник
(source_label, status, fact_ids): у роли из нескольких источников слои «<id>_<n>» с той же role, строка легенды одна; size_role подписи — ключ map.yaml: text.styles;
symbol — map.yaml: layers.<роль>.marker.shape; служебные слои — role legend, scale_bar, north_arrow; place.name —
ключ map.yaml: places. Легенда с map.yaml: legend.groups_from строк — группами по уровням (строка kind: group).

Площадные слои с заливкой или штриховкой уровня boundaries (зоны ПЗЗ, ограничения) — без улиц подложки: из них
вырезается полоса по классу улицы (cut, строится в build.py) до упрощения. Линии и контуры без заливки, знаки,
линейка, легенда внутри и север — места, которые подписи подложки обходят (blockers: build.py передает их движку
подписей QGIS до экспорта подложки).

Подписи расставляет картограф (верстка их не двигает): по правилу роли (Role.label: правило стандарта map.yaml и
`label` описания карты — поле и стиль; max_features, min_size), позиции вокруг знака в порядке стилей верстки (справа
сверху, слева сверху, справа снизу, слева снизу, справа, слева) с отступом text.placement.distance от края знака;
подпись не задевает другие подписи, подписи подложки, знаки, границу территории, линейку, север, легенду внутри и край
места. Не встала у знака — отнесена с выноской (leader_to); подпись площади — в центре внутри площади целиком, не
встала — рядом вне площади на плашке с выноской от точки на объекте (второй проход, когда все подписи внутри уже
стоят); не встала и так — в лог. Роль подложки с `label` описания (кварталы) подписывается так же: растр ее не
подписывает.
Размер текста — по метрике шрифта из style.yaml: font.files (кегль text.styles, перенос text.wrap_chars).
Текст подписи — по TEXT.md верстки (labels.clean, правила rules/labels.yaml); перенос не отрывает предлог, сокращение
и слово из rules/labels.yaml: keep_with_next от следующего слова (text.yaml: nbsp верстки).
"""

import itertools
import json
import math
import re
from functools import cache

import jsonschema
import pyogrio
import shapely
from PIL import ImageFont
from shapely import affinity
from shapely.geometry import LineString, Point, box

from cm_carto import paths, tokens
from cm_carto.render import labels as label_rules
from cm_carto.render.labels import NBSP, clean as clean_label

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
BLOCK_STEP_MM = 2.0       # линии overlay для подписей подложки — куски не длиннее (как line_obstacles верстки)
BLOCK_MARGIN_MM = 0.4     # запас вокруг линии сверх половины толщины
GLUE = "\u2060"           # временная склейка слов при переносе (WORD JOINER), в текст не попадает
# Статусы геометрии от надежного к слабому: сводный статус карты и слоя — самый слабый из встреченных.
STATUSES = ["official", "cadastral", "concept", "digitized", "reference", "calc"]
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "iu", "ia"]))


@cache
def weakest(statuses) -> str:
    return max(statuses, key=STATUSES.index)


def source_text(row: dict) -> str:
    """Подпись источника объекта: «подпись, лицензия»."""
    return ", ".join(v for v in (row["source_label"], row["source_license"]) if v)


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
def text_tokens() -> dict:
    return tokens.load("text")


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
        """Перенос по словам, строка не длиннее text.wrap_chars знаков (как autoWrapLength стилей верстки). Не делят
        строку: неразрывный пробел («№ 20»), пробел после предлога, союза и сокращения (text.yaml: nbsp.after,
        abbreviations), перед тире (nbsp.before) и после слова rules/labels.yaml: keep_with_next («имени Пушкина»)."""
        limit, lines = self.std["text"]["wrap_chars"], []
        nbsp = text_tokens()["nbsp"]
        after = {w.casefold() for w in [*nbsp["after"], *nbsp["abbreviations"],
                                        *label_rules.rules().get("keep_with_next", [])]}
        before = set(nbsp["before"])
        words = []
        for word in text.split(" "):
            if words and (re.split(f"[{NBSP}{GLUE}]", words[-1])[-1].casefold() in after or word in before):
                words[-1] += GLUE + word
            else:
                words.append(word)
        for word in words:
            if lines and len(lines[-1]) + 1 + len(word) <= limit:
                lines[-1] += " " + word
            else:
                lines.append(word)
        return "\n".join(lines).replace(GLUE, " ")

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


CORNERS = {"bottom_right": "правый нижний", "bottom_left": "левый нижний"}  # углы линейки (map.yaml: scale_bar)


def mirror(rect, width: float) -> tuple[float, float, float, float]:
    """Прямоугольник, отраженный по ширине места: из правого угла в левый с тем же отступом от края."""
    x0, y0, x1, y1 = rect
    return width - x1, y0, width - x0, y1


def data_hit(rect, layers: list[dict], std: dict) -> str | None:
    """Первый объект «<слой>:<id>» слоев данных overlay, которого касается прямоугольник, как у верстки
    (cm_deck/overlay_make.py: data_hit): линия и контур площади без заливки и штриховки — с запасом половины толщины
    линии (края залитых зон не в счет); знак точки — кругом или квадратом из токенов (размер и обводка). None — свободно."""
    plate = box(*rect)
    for layer in layers:
        kind, role = layer["kind"], layer["role"]
        if kind not in ("polygon", "line", "point") or role not in std["layers"]:
            continue
        spec = std["layers"][role]
        if kind == "polygon" and (role == "zoning" or {"fill", "hatch"} & set(spec)):
            continue
        for feature in layer["features"]:
            if kind == "point":
                marker = spec["marker"]
                r = (marker["size"] + marker["stroke"]["width"]) / 2
                x, y = feature["at"]
                hit = (plate.distance(Point(x, y)) < r if marker["shape"] == "circle"
                       else plate.intersects(box(x - r, y - r, x + r, y + r)))
            else:
                paths = feature["rings"] if kind == "polygon" else [feature["path"]]
                grown = box(*plate.buffer(spec["stroke"]["width"] / 2, join_style="mitre").bounds)
                hit = any(grown.intersects(LineString(path)) for path in paths)
            if hit:
                return f"{layer['id']}:{feature['id']}"
    return None


def choose_corner(std: dict, width: float, right, layers: list[dict], legend) -> tuple[str, tuple]:
    """Угол линейки по map.yaml: scale_bar.position (Д-054): первый угол из списка, где плашка не касается объектов
    слоев данных (data_hit); при легенде внутри карты (`legend` — ее плашка) левый нижний угол занят легендой.
    `right` — плашка в правом нижнем углу по компоновке, левая — ее отражение. Все углы заняты — ValueError: сдвинуть
    охват (линейку не двигают). Возвращает (угол, плашка)."""
    order = std["scale_bar"]["position"]
    if not (isinstance(order, list) and order and len(set(order)) == len(order) and set(order) <= set(CORNERS)):
        raise ValueError(f"map.yaml: scale_bar.position — список углов без повторов из {', '.join(CORNERS)}, "
                         f"дано {order!r}")
    reasons = []
    for corner in order:
        rect = right if corner == "bottom_right" else mirror(right, width)
        hit = data_hit(rect, layers, std)
        if hit is None and legend is not None and (corner == "bottom_left" or box(*rect).intersects(box(*legend))):
            hit = "легенда"
        if hit is None:
            return corner, rect
        reasons.append(f"{CORNERS[corner]} угол — {hit}")
    raise ValueError(f"линейка не помещается: {'; '.join(reasons)}; сдвинуть охват карты "
                     f"(линейку не двигают, map.yaml: scale_bar.position)")


class Overlay:
    def __init__(self, spec, std: dict, style: dict, layout, rect):
        self.spec, self.std, self.style, self.layout = spec, std, style, layout
        self.rect = rect
        self.matrix = to_mm(spec.scale, rect)
        self.frame = box(0, 0, *spec.size_mm)
        self.measure = Measure(std, style)
        self.plate = (std["text"]["plate"]["padding_x"], std["text"]["plate"]["padding_y"])
        self.obstacles: list = []      # знаки, граница территории, линейка, север, легенда внутри
        self.blockers: list = []       # куски линий и контуров без заливки: их обходят подписи подложки
        self.placed: list = []         # подписи
        self.unplaced: list[str] = []
        self.legend_box = None         # плашка легенды внутри карты (x0, y0, x1, y1) или None
        self.corner = "bottom_right"   # угол линейки и севера

    # Слои данных

    def data_layer(self, layer_id: str, role: str, kind: str, rows: list[dict], source: str, spec_role: dict,
                   category: str | None = None) -> dict:
        """Слой одного источника: status — самый слабый у его объектов (нет объектов — нет и status)."""
        features = []
        for row in sorted(rows, key=lambda r: r["object_id"]):
            for part in parts(row["mm"]):
                feature = {"id": row["object_id"]}
                if row.get("name"):
                    feature["name"] = clean_label(str(row["name"]))  # имя фигуры PowerPoint — как текст подписи
                feature |= shape(part)
                if kind == "point":
                    feature["symbol"] = spec_role["marker"]["shape"]
                if category is not None:
                    feature["category"] = category
                feature["status"] = row["status"]
                feature["fact_ids"] = sorted(json.loads(row["fact_ids"] or "[]"))
                features.append(feature)
        layer = {"id": layer_id, "role": role, "kind": kind, "source_label": source}
        if rows:
            layer["status"] = weakest(row["status"] for row in rows)
        layer["fact_ids"] = sorted({f for row in rows for f in json.loads(row["fact_ids"] or "[]")})
        if kind != "point":
            layer["simplify_mm"] = SIMPLIFY_MM
        return layer | {"features": features}

    def source_layers(self, layer_id: str, role: str, kind: str, rows: list[dict], entry: dict, spec_role: dict,
                      category: str | None = None) -> list[dict]:
        """Слой на источник роли (entry["sources"], у карт сравнения — общие на группу) в порядке источников: один —
        id layer_id, несколько — «<layer_id>_<n>»; источник без объектов на этой карте — слой без фич (набор слоев
        роли у карт группы один и тот же). Подвал верстки берет подписи источников из слоев: склейка давала повторы."""
        sources = entry["sources"]
        stray = sorted({source_text(row) for row in rows} - set(sources))
        if stray:
            raise ValueError(f"роль {role}: источников {stray} нет в перечне роли {sources}")
        return [self.data_layer(layer_id if len(sources) == 1 else f"{layer_id}_{n}", role, kind,
                                [row for row in rows if source_text(row) == source], source, spec_role, category)
                for n, source in enumerate(sources, 1)]

    def data_layers(self, drawn: list, entries: dict, cut=None) -> list[dict]:
        """Слои ролей уровней analysis, objects, boundaries снизу вверх (обратный порядок стандарта). cut — полосы улиц
        подложки в системе проекта: вычитаются из площадей с заливкой или штриховкой (cuts_streets) до упрощения."""
        layers = []
        for d in reversed(drawn):
            carve = cut is not None and not cut.is_empty and cuts_streets(d.spec)
            for row in d.rows:
                geom = row["geom"].difference(cut) if carve else row["geom"]
                row["mm"] = geometry_mm(geom, self.matrix, self.frame)
            rows = [row for row in d.rows if not row["mm"].is_empty]
            kind = d.spec["geometry"]
            if d.role.key == "zoning":
                field = self.std["zoning"]["field"]
                for value, _ in d.categories():
                    part = [row for row in rows if row[field] == value]
                    layers += self.source_layers(ident(f"zoning_{value}"), "zoning", kind, part,
                                                 entries[d.role.key], d.spec, category=value)
            else:
                layers += self.source_layers(d.role.key, d.role.key, kind, rows, entries[d.role.key], d.spec)
            if kind == "point":
                r = d.spec["marker"]["size"] / 2
                self.obstacles += [box(row["mm"].x - r, row["mm"].y - r, row["mm"].x + r, row["mm"].y + r)
                                   for row in rows]
            if d.role.key == "territory":
                self.obstacles += [LineString(ring.coords) for row in rows for p in parts(row["mm"])
                                   for ring in [p.exterior, *p.interiors]]
            if "stroke" in d.spec and (kind == "line" or not {"fill", "hatch", "categories"} & set(d.spec)):
                self.blockers += line_blocks(rows, d.spec["stroke"]["width"])
        return layers

    # Служебные слои

    def legend_layer(self, items: list, entries: dict) -> dict:
        """Строки легенды: items — (роль, роль map.yaml, категории) в порядке стандарта (build.legend: у карт
        сравнения — общие на группу); у роли с категориями — строка на категорию."""
        rows = []
        for role, spec_role, categories in items:
            kind = spec_role["geometry"]
            if role.key == "zoning":
                rows += [{"role": "zoning", "kind": kind, "category": value, "text": title, "level": role.level}
                         for value, title in categories]
                continue
            row = {"role": role.key, "kind": kind, "text": role.title, "level": role.level}
            if kind == "point":
                row["symbol"] = spec_role["marker"]["shape"]
            rows.append(row)
        rule = self.std["legend"]
        if not rows:
            raise ValueError("в легенде нет строк: на карте только подложка")
        if len(rows) > rule["max_items"]:
            raise ValueError(f"в легенде {len(rows)} строк, предел {rule['max_items']}: делить карту (map.yaml: legend)")
        grouped = len(rows) >= rule["groups_from"]  # MAPS.md, 6: с groups_from строк — группы по уровням
        out, level = [], None
        for row in rows:
            if grouped and row["level"] != level:
                level = row["level"]
                out.append({"role": level, "kind": "group", "text": self.std["levels"][level]["title"]})
            out.append({k: v for k, v in row.items() if k != "level"})
        rows = out
        sources = []
        for entry in entries.values():
            sources += [s for s in entry["sources"] if s not in sources]
        layer = {"id": "legend", "role": "legend", "kind": "legend", "source_label": "; ".join(sources), "legend": rows}
        if self.spec.legend == "inside":  # левый верхний угол плашки; низ — по точке элемента legend компоновки
            inside = rule["inside"]
            # Раскладка как у верстки (cm_deck/overlay.py: _legend_items): строка — max(интерлиньяж, образец),
            # заголовок группы — интерлиньяж своего кегля, между группами group_gap.
            leading = {key: self.style["leading"][self.std["text"]["styles"][rule[key]]["size"]] / 72 * 25.4
                       for key in ("text", "group_text")}
            height, width = 2 * inside["padding"] - rule["item_gap"], 0.0
            for n, row in enumerate(rows):
                if row["kind"] == "group":
                    height += (rule["group_gap"] if n else 0) + leading["group_text"] + rule["item_gap"]
                    width = max(width, self.measure.size(row["text"], rule["group_text"])[0])
                else:
                    height += max(leading["text"], rule["swatch"][1]) + rule["item_gap"]
                    width = max(width, rule["swatch"][0] + rule["swatch_text_gap"]
                                + self.measure.size(row["text"], rule["text"])[0])
            width += 2 * inside["padding"]
            x0, y0, x1, y1 = element_box(self.layout.itemById("legend"), (width, height))
            layer["legend_at"] = xy((x0, y0))
            self.legend_box = (x0, y0, x1, y1)
            self.obstacles.append(box(x0, y0, x1, y1))
        return layer

    def scale_bar_layer(self, layers: list[dict]) -> dict:
        """Линейка: at — левый конец линии; плашка — по точке элемента scalebar компоновки (правый нижний угол), угол —
        по choose_corner (map.yaml: scale_bar.position) с препятствиями из слоев данных `layers` и легенды внутри."""
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
        right = element_box(self.layout.itemById("scalebar"), (width, height))
        self.corner, (x0, y0, x1, y1) = choose_corner(self.std, self.spec.size_mm[0], right, layers, self.legend_box)
        self.obstacles.append(box(x0, y0, x1, y1))
        at = (x0 + pad + first[0] / 2, y1 - pad - rule["line"]["width"] / 2)
        return {"id": "scale_bar", "role": "scale_bar", "kind": "scalebar",
                "source_label": f"масштаб 1:{number(self.spec.scale, self.style)}",
                "scalebar": {"at": xy(at), "segments_m": segments, "segment_mm": mm(segment)}}

    def north_layer(self) -> dict:
        """Стрелка севера: at — правый нижний угол знака размера map.yaml: north_arrow.size (overlay 1.1.0); над
        линейкой, край по краю ее плашки в том же углу (в левом — зеркально элементу north компоновки)."""
        box_ = element_box(self.layout.itemById("north"), self.std["north_arrow"]["size"])
        x0, y0, x1, y1 = mirror(box_, self.spec.size_mm[0]) if self.corner == "bottom_left" else box_
        self.obstacles.append(box(x0, y0, x1, y1))
        return {"id": "north_arrow", "role": "north_arrow", "kind": "north", "source_label": "север вверху",
                "features": [{"id": "north_arrow", "at": xy((x1, y1))}]}

    # Подписи

    def free(self, shape_) -> bool:
        return (self.frame.contains(shape_) and not any(shape_.intersects(o) for o in self.obstacles)
                and not any(shape_.intersects(p) for p in self.placed))

    def placement(self, d, rule: dict) -> str:
        return rule.get("placement", self.std["text"]["placement"][d.spec["geometry"]])

    def plate_of(self, rule: dict) -> tuple[float, float]:
        return (0, 0) if rule["style"] in self.std["text"]["plate"]["except_styles"] else self.plate

    def place(self, d, row: dict, text: str, rule: dict):
        """Место подписи одного объекта у знака или внутри площади: (at, anchor, leader_to или None) или None, если не
        встала. Подпись площади — в центре, внутри площади целиком (MAPS.md, раздел 5). Подпись точки, не встав рядом, уходит с выноской (LEADER_STEPS); подпись площади, не встав внутри, — в
        place_outside вторым проходом, когда все подписи внутри уже расставлены."""
        size = self.measure.size(text, rule["style"])
        plate = self.plate_of(rule)
        placement = self.placement(d, rule)
        geom = row["mm"]
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
            if self.free(shape_) and (placement != "centroid" or geom.contains(text_box(at, anchor, size, (0, 0)))):
                self.placed.append(shape_)
                return at, anchor, leader
        return None

    def place_outside(self, d, row: dict, text: str, rule: dict):
        """Площадь, которой не нашлось места внутри (MAPS.md, раздел 5: выноска — точка на объекте, прямая линия, подпись
        у конца): подпись рядом вне объекта на плашке (якорь не «c»), выноска от точки на поверхности объекта.
        Подпись стоит за границей объекта по лучу из этой точки в одну из сторон AROUND, на text.placement.distance плюс
        относ LEADER_STEPS; не задевает объект, другие подписи и их выноски, знаки и край места, выноска — другие подписи
        и знаки. (at, anchor, leader_to) или None."""
        size = self.measure.size(text, rule["style"])
        geom = row["mm"]
        point = geom.point_on_surface()
        reach = math.hypot(*self.frame.bounds[2:])
        gap = self.std["text"]["placement"]["distance"]
        for extra in LEADER_STEPS:
            for (dx, dy), anchor in AROUND:
                norm = math.hypot(dx, dy)
                ray = LineString([point, (point.x + dx / norm * reach, point.y + dy / norm * reach)])
                inner = min((p for p in parts(geom.intersection(ray)) if p.geom_type == "LineString"),
                            key=lambda p: p.distance(point))
                edge = max(inner.coords, key=lambda c: math.hypot(c[0] - point.x, c[1] - point.y))
                at = (edge[0] + dx * (gap + extra), edge[1] + dy * (gap + extra))
                shape_ = text_box(at, anchor, size, self.plate)
                leader = LineString([point, at])
                if not shape_.intersects(geom) and self.free(shape_) and self.free(leader):
                    self.placed += [shape_, leader]
                    return at, anchor, (point.x, point.y)
        return None

    def label_rows(self, d) -> list[dict]:
        """Объекты роли с подписью, по object_id. У роли подложки строки без геометрии (build.vector читает ее у данных и
        улиц): она берется из геопакета по охвату карты."""
        if d.role.level == "basemap":
            found = pyogrio.read_dataframe(self.spec.data, layer=d.role.layer, columns=["object_id"], bbox=self.rect)
            geoms = dict(zip(found["object_id"], found.geometry))
            for row in d.rows:
                row["mm"] = geometry_mm(geoms[row["object_id"]], self.matrix, self.frame)
        return sorted((row for row in d.rows if not row["mm"].is_empty), key=lambda r: r["object_id"])

    def label_layers(self, drawn: list, entries: dict) -> list[dict]:
        """Подписи по правилу роли (Role.label: стандарт верстки и описание карты); drawn — роли с правилом. Роли — от
        верхней роли стандарта, внутри роли — по object_id. Первый проход: точки у знака (при тесноте с выноской) и
        площади внутри; второй: площади, не вставшие внутри, — рядом с выноской. Не встала — в лог (unplaced)."""
        layers, pending = [], []
        for d in drawn:
            rule = d.role.label
            rows = self.label_rows(d)
            if len(rows) > rule.get("max_features", len(rows)):
                continue
            found = {}
            for n, row in enumerate(rows):
                text = row.get(rule["field"])
                if text in (None, ""):
                    continue
                if "min_size" in rule and row["mm"].area < rule["min_size"] ** 2:  # как minFeatureSize QGIS у площадей
                    continue
                text = self.measure.wrap(clean_label(str(text)))
                spot = self.place(d, row, text, rule)
                if spot is not None:
                    found[n] = self.label_feature(row, text, rule, spot)
                elif self.placement(d, rule) == "centroid":
                    pending.append((found, n, d, row, text, rule))
                else:
                    self.unplaced.append(f"{d.role.key} {row['object_id']} «{text}»")
            layers.append((d, found))
        for found, n, d, row, text, rule in pending:
            spot = self.place_outside(d, row, text, rule)
            if spot is None:
                self.unplaced.append(f"{d.role.key} {row['object_id']} «{text}»")
            else:
                found[n] = self.label_feature(row, text, rule, spot)
        out = []
        for d, found in layers:
            if found:
                source = entries[d.role.key]["source"] if d.role.key in entries \
                    else "; ".join(dict.fromkeys(source_text(row) for row in d.rows))
                out.append({"id": f"labels_{d.role.key}", "role": "label", "kind": "label", "source_label": source,
                            "features": [found[n] for n in sorted(found)]})
        return list(reversed(out))

    @staticmethod
    def label_feature(row: dict, text: str, rule: dict, spot) -> dict:
        at, anchor, leader = spot
        feature = {"id": row["object_id"], "text": text, "at": xy(at), "anchor": anchor, "size_role": rule["style"]}
        if leader:
            feature["leader_to"] = xy(leader)
        return feature


def cuts_streets(spec_role: dict) -> bool:
    """Из площади вырезаются улицы подложки: заливка или штриховка уровня boundaries (зоны ПЗЗ, ограничения)."""
    return spec_role["geometry"] == "polygon" and spec_role["level"] == "boundaries" \
        and bool({"fill", "hatch", "categories"} & set(spec_role))


def line_blocks(rows: list[dict], width: float) -> list:
    """Прямоугольники вдоль линий и контуров, мм: куски до BLOCK_STEP_MM с запасом половины толщины и BLOCK_MARGIN_MM
    (как line_obstacles верстки): подписи подложки не идут под изохрону и границу."""
    half = width / 2 + BLOCK_MARGIN_MM
    out = []
    for row in rows:
        for part in parts(row["mm"]):
            lines = [part.exterior, *part.interiors] if part.geom_type == "Polygon" else [part]
            for line in lines:
                for (x0, y0), (x1, y1) in itertools.pairwise(line.coords):
                    n = max(1, math.ceil(math.hypot(x1 - x0, y1 - y0) / BLOCK_STEP_MM))
                    for k in range(n):
                        ax, ay = x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n
                        bx, by = x0 + (x1 - x0) * (k + 1) / n, y0 + (y1 - y0) * (k + 1) / n
                        out.append(box(min(ax, bx) - half, min(ay, by) - half, max(ax, bx) + half, max(ay, by) + half))
    return out


def build(spec, std: dict, style: dict, drawn: list, legend_items: list, entries: dict, north: bool, layout, rect,
          basemap: str, raster, cut=None) -> tuple[dict, list[str]]:
    """overlay.json по контракту и подписи, которые не встали без наездов. legend_items — строки легенды
    (build.legend); cut — полосы улиц (вычитаются из зон). raster(blockers) рисует подложку, обходя подписями
    blockers (рамки x0, y0, x1, y1 в мм: линии без заливки, знаки, линейка, север, легенда внутри), и возвращает
    рамки своих подписей в мм — подписи overlay их не задевают."""
    data = [d for d in drawn if d.role.level != "basemap"]
    o = Overlay(spec, std, style, layout, rect)
    layers = o.data_layers(data, entries, cut)
    legend = o.legend_layer(legend_items, entries)
    service = [o.scale_bar_layer(layers)] + ([o.north_layer()] if north else [])
    blockers = [b.bounds for b in o.blockers + [x for x in o.obstacles if x.geom_type == "Polygon"]]
    o.obstacles += [box(*r) for r in raster(sorted(blockers))]
    labels = o.label_layers([d for d in drawn if d.role.label], entries)
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
