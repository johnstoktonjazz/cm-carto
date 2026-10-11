"""Слои данных карты в миллиметрах места макета (overlay.json): то, что верстка рисует редактируемым в PowerPoint.

Система координат файла — как у PowerPoint: начало в левом верхнем углу места, x вправо, y вниз, миллиметры
(units, origin, size_mm записаны в файле). Геометрия обрезана по рамке места, упрощена (Дуглас — Пекер, допуск
SIMPLIFY_MM) и привязана к сетке ROUND_MM; кольца с дырами сохраняются. Стиль в файл не пишется: цвет, толщина и
шрифт — по role из токенов верстки (map.yaml), как у стилей QGIS. Формат — schema/overlay.draft.schema.json
(черновик картографа до контракта overlay).
"""

import json
import math
from functools import cache

import jsonschema
import shapely
from shapely import affinity
from shapely.geometry import box, mapping

from cm_carto import paths

SCHEMA = paths.ROOT / "schema" / "overlay.draft.schema.json"
FORMAT = "cm-carto/overlay-draft/0.1"
SIMPLIFY_MM = 0.1
ROUND_MM = 0.01
# Опорная точка элемента компоновки QGIS (referencePoint) → как привязан элемент к точке position.
REFERENCE = {0: "top-left", 1: "top-center", 2: "top-right", 3: "middle-left", 4: "center", 5: "middle-right",
             6: "bottom-left", 7: "bottom-center", 8: "bottom-right"}
NICE = [1, 2, 2.5, 5]


@cache
def schema() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def to_mm(scale: int, rect: tuple[float, float, float, float]) -> list[float]:
    """Аффинное преобразование shapely: система проекта → мм места (y вниз)."""
    xmin, _, _, ymax = rect
    k = 1000 / scale
    return [k, 0, 0, -k, -xmin * k, ymax * k]


def rounded(value):
    if isinstance(value, float):
        return round(value, 2) + 0.0  # + 0.0: -0.0 → 0.0
    if isinstance(value, (list, tuple)):
        return [rounded(v) for v in value]
    return value


def geometry_mm(geom, matrix: list[float], frame) -> object:
    """Геометрия в мм места: обрезка по рамке, упрощение, сетка ROUND_MM (кольца и дыры сохраняются)."""
    clipped = affinity.affine_transform(geom, matrix).intersection(frame)
    if clipped.geom_type != "Point":
        clipped = clipped.simplify(SIMPLIFY_MM, preserve_topology=True)
    return shapely.set_precision(clipped, ROUND_MM)


def geojson(geom) -> dict:
    data = mapping(geom)
    return {"type": data["type"], "coordinates": rounded(data["coordinates"])}


def nice_step(scale: int, width_mm: list[float]) -> float:
    """Деление линейки в метрах: наименьшее круглое (1, 2, 2,5, 5 × 10^n), не короче width_mm[0] мм на листе."""
    least = width_mm[0] * scale / 1000
    power = 10 ** math.floor(math.log10(least))
    for factor in NICE + [10]:
        if factor * power >= least:
            step = factor * power
            break
    if step * 1000 / scale > width_mm[1]:
        raise ValueError(f"деление линейки {step} м шире {width_mm[1]} мм при 1:{scale} (map.yaml: scale_bar)")
    return step


def number(value: float, style: dict) -> str:
    """Число по style.yaml: number_format: разряды и десятичный знак."""
    whole, _, fraction = f"{value:.10g}".partition(".")
    whole = f"{int(whole):,}".replace(",", style["number_format"]["thousands"])
    return whole + (style["number_format"]["decimal"] + fraction if fraction else "")


def position(item) -> dict:
    """Положение элемента компоновки верстки (map_<место>.qpt) в мм места."""
    point = item.positionWithUnits()
    return {"x": rounded(point.x()), "y": rounded(point.y()), "align": REFERENCE[item.referencePoint()]}


def label(role_key: str, spec: dict, std: dict, geom, row: dict, count: int) -> dict | None:
    """Подпись объекта по правилу стиля роли (map.yaml: layers.<роль>.label); None — не подписывается."""
    rule = spec["label"]
    text = row.get(rule["field"])
    if text in (None, "") or count > rule.get("max_features", count):
        return None
    if "min_size" in rule and geom.geom_type in ("Polygon", "MultiPolygon") and geom.area < rule["min_size"] ** 2:
        return None
    if "min_size" in rule and geom.geom_type in ("LineString", "MultiLineString") and geom.length < rule["min_size"]:
        return None
    placement = rule.get("placement", std["text"]["placement"][spec["geometry"]])
    distance = std["text"]["placement"]["distance"]
    out = {"role": role_key, "object_id": row["object_id"], "text": str(text), "style": rule["style"],
           "placement": placement}
    if placement == "around":  # справа сверху от края знака (MAPS.md, раздел 5)
        r = spec["marker"]["size"] / 2
        out |= {"anchor": rounded([geom.x + r + distance, geom.y - r - distance]), "align": "bottom-left"}
    elif placement == "along":
        line = shapely.line_merge(geom) if geom.geom_type == "MultiLineString" else geom
        line = max(line.geoms, key=lambda g: g.length) if line.geom_type == "MultiLineString" else line
        a, b = line.interpolate(0.45, normalized=True), line.interpolate(0.55, normalized=True)
        middle = line.interpolate(0.5, normalized=True)
        angle = math.degrees(math.atan2(-(b.y - a.y), b.x - a.x))
        angle = angle - 180 if angle > 90 else angle + 180 if angle < -90 else angle  # текст не вверх ногами
        out |= {"anchor": rounded([middle.x, middle.y - distance]), "align": "bottom-center",
                "angle": rounded(angle)}
    else:  # center у точки, centroid у площади: в центре, внутри контура
        point = geom if geom.geom_type == "Point" else geom.point_on_surface()
        out |= {"anchor": rounded([point.x, point.y]), "align": "center"}
    return out


def legend(std: dict, drawn: list, spec, layout) -> dict:
    """Строки легенды: роли уровней с legend: true в порядке стандарта; у категорий — встреченные на карте;
    с groups_from строк — заголовки групп уровней; больше max_items — ошибка."""
    shown = [d for d in drawn if std["levels"][d.role.level]["legend"]]
    items = []
    for d in shown:
        categories = d.categories()
        if categories:
            items.append({"type": "heading", "text": d.role.title, "level": d.role.level})
            items += [{"type": "item", "role": d.role.key, "category": value, "text": title, "level": d.role.level}
                      for value, title in categories]
        else:
            items.append({"type": "item", "role": d.role.key, "text": d.role.title, "level": d.role.level})
    rows = sum(1 for i in items if i["type"] == "item")
    if rows == 0:
        raise ValueError("в легенде нет строк: на карте только подложка")
    if rows > std["legend"]["max_items"]:
        raise ValueError(f"в легенде {rows} строк, предел {std['legend']['max_items']}: делить карту (map.yaml: legend)")
    if rows >= std["legend"]["groups_from"]:
        grouped, seen = [], set()
        for i in items:
            if i["level"] not in seen:
                seen.add(i["level"])
                grouped.append({"type": "group", "text": std["levels"][i["level"]]["title"], "level": i["level"]})
            grouped.append(i)
        items = grouped
    out = {"place": spec.legend, "rows": items}
    if spec.legend == "inside":
        out["position"] = position(layout.itemById("legend"))
    return out


def scale_bar(std: dict, style: dict, spec, layout) -> dict:
    rule = std["scale_bar"]
    step = nice_step(spec.scale, rule["segment_width"])
    ticks = [i * step for i in range(rule["segments"] + 1)]
    labels = [number(t, style) for t in ticks]
    labels[-1] += style["number_format"]["unit_separator"] + rule["unit_label"]
    segment = step * 1000 / spec.scale
    return {"segment_mm": rounded(segment), "segments": rule["segments"], "length_mm": rounded(segment * rule["segments"]),
            "labels": labels, "position": position(layout.itemById("scalebar"))}


def build(spec, std: dict, style: dict, drawn: list, stack: list, layers: list, north: bool, layout, rect,
          basemap: str | None) -> dict:
    """overlay.json: стопка слоев, объекты, подписи, легенда, линейка, север."""
    matrix = to_mm(spec.scale, rect)
    width, height = spec.size_mm
    frame = box(0, 0, width, height)
    features, labels = [], []
    for d in reversed([d for d in drawn if d.role.level != "basemap"]):  # снизу вверх, как рисовать
        role = d.role
        spec_role = d.spec
        category = std["zoning"]["field"] if role.key == "zoning" else None
        geoms = [(row, geometry_mm(row["geom"], matrix, frame)) for row in d.rows]
        geoms = [(row, g) for row, g in geoms if not g.is_empty]
        for row, geom in sorted(geoms, key=lambda item: item[0]["object_id"]):
            feature = {"role": role.key, "object_id": row["object_id"], "status": row["status"],
                       "fact_ids": json.loads(row["fact_ids"] or "[]"), "geometry": geojson(geom)}
            if category:
                feature[category] = row[category]
                code = spec_role["label"]["field"]
                if row.get(code) not in (None, ""):
                    feature[code] = row[code]
            if spec_role["geometry"] == "point":
                feature["marker"] = {"shape": spec_role["marker"]["shape"],
                                     "token": f"map.yaml: layers.{role.key}.marker"}
            features.append(feature)
            if "label" in spec_role:
                text = label(role.key, spec_role, std, geom, row, len(d.rows))
                if text:
                    labels.append(text)
    xmin, ymin, xmax, ymax = rect
    doc = {
        "format": FORMAT,
        "units": "mm",
        "origin": "top-left",
        "y_axis": "down",
        "size_mm": {"width": width, "height": height},
        "basemap": basemap,
        "stack": stack,
        "crs": spec.project.crs_name,
        "scale": spec.scale,
        "extent": rounded([xmin, ymin, xmax, ymax]),
        "layers": layers,
        "features": features,
        "labels": labels,
        "legend": legend(std, drawn, spec, layout),
        "scale_bar": scale_bar(std, style, spec, layout),
        "north": {"visible": north, "position": position(layout.itemById("north"))},
    }
    errors = [f"{'/'.join(map(str, e.absolute_path))}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema()).iter_errors(doc)]
    if errors:
        raise ValueError(f"overlay не проходит schema/overlay.draft.schema.json:\n  " + "\n  ".join(errors[:20]))
    return doc


def dumps(doc: dict) -> str:
    """Ключи по алфавиту; элемент списка объектов — одной строкой: файл читается и сравнивается построчно."""
    def compact(value) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))

    lines = []
    for key in sorted(doc):
        value = doc[key]
        if isinstance(value, list) and value and isinstance(value[0], dict):
            items = ",\n".join(f"  {compact(v)}" for v in value)
            lines.append(f' "{key}": [\n{items}\n ]')
        elif isinstance(value, dict) and any(isinstance(v, list) and v and isinstance(v[0], dict) for v in value.values()):
            inner = ",\n".join(f'  "{k}": ' + (("[\n" + ",\n".join(f"   {compact(x)}" for x in v) + "\n  ]")
                                                if isinstance(v, list) and v and isinstance(v[0], dict) else compact(v))
                               for k, v in sorted(value.items()))
            lines.append(f' "{key}": {{\n{inner}\n }}')
        else:
            lines.append(f' "{key}": {compact(value)}')
    text = "{\n" + ",\n".join(lines) + "\n}\n"
    return text
