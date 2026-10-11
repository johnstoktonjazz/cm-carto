"""DXF → слои геопакета по правилам раскладки (rules/*.yaml, type: dxf).

Слой чертежа относится к правилу по шаблону имени. Контуры собираются из любых линий: замкнутые полилинии, дуги,
отрезки; незамкнутые концы в допуске замыкаются, самопересечения чинятся; каждое исправление — строка отчета.
Номер объекта — текст из слоя номеров внутри контура. Система координат чертежа — проектная (в DXF ее нет).
"""

import fnmatch
import math
import re
from collections import Counter, defaultdict

import ezdxf
import geopandas as gpd
from ezdxf import path as dxfpath
from shapely import STRtree
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import linemerge, nearest_points, polygonize, unary_union

from cm_carto.ingest import common, geom as geomlib, ids
from cm_carto.ingest.report import Report, at, num
from cm_carto.project import Project

LINEWORK = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
TEXTS = {"TEXT", "MTEXT"}
GEOMETRY_ROUND = 6   # знаков после запятой: шум чертежа ниже микрометра склеивает совпадающие вершины
NOISE_M = 0.01       # разрыв меньше 1 см — шум округления дуг и координат, в отчете одной строкой на слой


def norm(name: str) -> str:
    return name.casefold().replace("_", " ")


def matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(norm(name), norm(p)) for p in patterns)


def classify(layer: str, rules: dict) -> tuple[str, int | None]:
    """Слой чертежа → ('draw', правило) | ('number', правило) | ('ignore', None) | ('none', None)."""
    for i, rule in enumerate(rules["layers"]):
        if matches(layer, rule["drawing"]):
            return "draw", i
    for i, rule in enumerate(rules["layers"]):
        if "number" in rule and matches(layer, rule["number"]["layers"]):
            return "number", i
    return ("ignore", None) if matches(layer, rules["ignore"]) else ("none", None)


def expand(entity, layer: str):
    """Сущности вставки блока с их слоем: вложенные вставки раскрываются, слой "0" в блоке — слой вставки."""
    if entity.dxftype() == "INSERT":
        for sub in entity.virtual_entities():
            yield from expand(sub, layer if sub.dxf.layer == "0" else sub.dxf.layer)
    else:
        yield layer, entity


def entities(modelspace, rules: dict):
    """Сущности чертежа с эффективным слоем; раскрываются только вставки на слоях с правилами."""
    for entity in modelspace:
        layer = entity.dxf.layer
        if entity.dxftype() == "INSERT" and classify(layer, rules)[0] in ("draw", "number"):
            yield from expand(entity, layer)
        else:
            yield layer, entity


def coords_of(entity, tolerance: float) -> list[list[tuple[float, float]]]:
    """Ломаные сущности; дуги, окружности и сплайны заменяются ломаной с допуском tolerance."""
    return [[(round(v.x, GEOMETRY_ROUND), round(v.y, GEOMETRY_ROUND)) for v in sub.flattening(tolerance)]
            for sub in dxfpath.make_path(entity).sub_paths()]


def text_of(entity) -> tuple[str, tuple[float, float]]:
    if entity.dxftype() == "MTEXT":
        text, point = entity.plain_text(), entity.dxf.insert
    else:
        text, point = entity.dxf.text, entity.get_placement()[1]
    return " ".join(text.split()), (point.x, point.y)


def close_gaps(lines: list[LineString], tolerance: float):
    """Узлует линии и замыкает свободные концы: конец — к ближайшему свободному концу, иначе к линии, если ближе
    допуска. Возвращает линии с перемычками, перемычки (от, до, длина) и оставшиеся свободные концы."""
    noded = unary_union(lines)
    segments = list(getattr(noded, "geoms", [noded]))
    degree = Counter()
    for s in segments:
        degree[s.coords[0]] += 1
        degree[s.coords[-1]] += 1
    free = sorted(p for p, d in degree.items() if d == 1)
    bridges, used = [], set()
    pairs = sorted((math.dist(p, q), i, j) for i, p in enumerate(free) for j, q in enumerate(free) if i < j
                   and math.dist(p, q) <= tolerance)
    for dist, i, j in pairs:
        if i not in used and j not in used:
            used |= {i, j}
            bridges.append((free[i], free[j], dist))
    for i, p in enumerate(free):
        if i in used:
            continue
        others = [s for s in segments if p not in (s.coords[0], s.coords[-1])]
        if not others:
            continue
        nearest = min(others, key=lambda s: s.distance(Point(p)))
        q = nearest_points(Point(p), nearest)[1]
        if q.distance(Point(p)) <= tolerance:
            used.add(i)
            bridges.append((p, (q.x, q.y), q.distance(Point(p))))
    left = [p for i, p in enumerate(free) if i not in used]
    return segments + [LineString([a, b]) for a, b, _ in bridges], bridges, left


def report_bridges(bridges: list, layer: str, report: Report) -> None:
    for a, b, dist in sorted(bridges):
        if dist >= NOISE_M:
            report.fixed.append(f"слой «{layer}»: замкнут разрыв {num(dist)} м {at(*a)}")
    noise = sum(1 for _, _, dist in bridges if dist < NOISE_M)
    if noise:
        report.fixed.append(f"слой «{layer}»: замкнуто разрывов меньше 1 см (округление дуг и координат): {noise}")


def unclosed_report(left: list[tuple[float, float]], tolerance: float, layer: str, report: Report) -> None:
    """Свободные концы, которые не замкнулись: попарно с ближайшим другим концом."""
    seen = set()
    for i, p in enumerate(left):
        if i in seen:
            continue
        others = [(math.dist(p, q), j) for j, q in enumerate(left) if j != i and j not in seen]
        if others:
            dist, j = min(others)
            seen |= {i, j}
            report.unrecognized.append(f"слой «{layer}»: контур не замкнут, разрыв {num(dist)} м {at(*p)}, допуск {num(tolerance)} м")
        else:
            seen.add(i)
            report.unrecognized.append(f"слой «{layer}»: свободный конец линии {at(*p)}, контур не замкнут")


def polygons_from(paths: list[list[tuple[float, float]]], tolerance: float, layer: str, report: Report) -> list:
    """Замкнутые ломаные — сразу полигоны; незамкнутые собираются в контуры с замыканием разрывов в допуске."""
    polygons, opened = [], []
    for c in paths:
        if len(c) >= 4 and c[0] == c[-1]:
            polygons.append(Polygon(c))
        else:
            opened.append(LineString(c))
    if opened:
        segments, bridges, left = close_gaps(opened, tolerance)
        report_bridges(bridges, layer, report)
        unclosed_report(left, tolerance, layer, report)
        polygons += list(polygonize(unary_union(segments)))
    result = []
    for poly in polygons:
        if poly.area == 0:
            report.unrecognized.append(f"слой «{layer}»: контур нулевой площади {at(poly.centroid.x, poly.centroid.y)}")
            continue
        fixed, changed = geomlib.fix_polygon(poly)
        if changed:
            center = poly.envelope.centroid
            report.fixed.append(f"слой «{layer}»: самопересечение исправлено {at(center.x, center.y)}, "
                                f"частей: {len(geomlib.polygons_of(fixed))}")
            report.confirm.append(f"слой «{layer}»: контур {at(center.x, center.y)} изменен при исправлении самопересечения, сверить с чертежом")
        result.append(fixed)
    return result


def lines_from(paths: list[list[tuple[float, float]]], tolerance: float, layer: str, report: Report) -> list[LineString]:
    segments, bridges, _ = close_gaps([LineString(c) for c in paths], tolerance)
    report_bridges(bridges, layer, report)
    merged = linemerge(unary_union(segments))
    return list(getattr(merged, "geoms", [merged]))


def numbered(geoms: list, texts: list[tuple[str, tuple[float, float]]], rule: dict, layer: str, report: Report) -> list:
    """Контуры с номерами: номер — единственный текст-номер внутри контура."""
    pattern = re.compile(rule["number"]["pattern"])
    numbers = []
    for text, point in texts:
        if pattern.fullmatch(text):
            numbers.append((text, Point(point)))
        else:
            report.unrecognized.append(f"слой «{layer}»: текст «{text}» {at(*point)} не номер (шаблон {rule['number']['pattern']})")
    tree = STRtree([p for _, p in numbers])
    out = []
    for geom in geoms:
        inside = sorted({numbers[i][0] for i in tree.query(geom, predicate="intersects")})
        if len(inside) > 1:
            report.confirm.append(f"{rule['target']}: в контуре {at(geom.centroid.x, geom.centroid.y)} несколько номеров "
                                  f"({', '.join(inside)}), ключ по геометрии")
        elif not inside:
            report.confirm.append(f"{rule['target']}: у контура {at(geom.centroid.x, geom.centroid.y)} нет номера, ключ по геометрии")
        out.append((geom, inside[0] if len(inside) == 1 else None))
    return out


def ingest(path, source: common.Source, rules: dict, project: Project, report: Report) -> dict[str, gpd.GeoDataFrame]:
    doc = ezdxf.readfile(path)
    status = rules["status"]
    tolerance, arc_tolerance = rules["tolerance_m"], rules["arc_tolerance_m"]

    drawn = defaultdict(lambda: defaultdict(list))     # правило → слой чертежа → ломаные
    texts = defaultdict(lambda: defaultdict(list))     # правило → слой номеров → (текст, точка)
    skipped = Counter()                                # (слой, тип) без разбора на слоях с правилом
    other = defaultdict(Counter)                       # слой без правила → типы
    ignored = Counter()
    for layer, entity in entities(doc.modelspace(), rules):
        role, i = classify(layer, rules)
        kind = entity.dxftype()
        if role == "ignore":
            ignored[layer] += 1
        elif role == "none":
            other[layer][kind] += 1
        elif role == "draw" and kind in LINEWORK:
            drawn[i][layer] += [c for c in coords_of(entity, arc_tolerance) if len(c) >= 2]
        elif role == "number" and kind in TEXTS:
            texts[i][layer].append(text_of(entity))
        else:
            skipped[(layer, kind)] += 1

    rows = defaultdict(list)
    for i, rule in enumerate(rules["layers"]):
        for layer, paths in drawn[i].items():
            if rule["geometry"] == "polygon":
                geoms = polygons_from(paths, tolerance, layer, report)
                if "number" in rule:
                    layer_texts = [t for tl in texts[i].values() for t in tl]
                    items = numbered(geoms, layer_texts, rule, layer, report)
                else:
                    items = [(g, None) for g in geoms]
            else:
                items = [(g, None) for g in lines_from(paths, tolerance, layer, report)]
            grouped = defaultdict(list)
            for geom, number in items:
                key = ids.key_from_text(number) if number else ids.geometry_key(geom)
                grouped[key].append((geom, number))
            for key, parts in grouped.items():
                geom = parts[0][0] if len(parts) == 1 else unary_union([g for g, _ in parts])
                if len(parts) > 1:
                    report.fixed.append(f"слой «{layer}»: {len(parts)} контура с ключом {key} объединены в один объект")
                attrs = dict(rule.get("attrs", {}))
                name = None
                if parts[0][1]:
                    attrs[rule["number"]["attr"]] = parts[0][1]
                    name = rule.get("name", "{num}").format(num=parts[0][1])
                rows[rule["target"]].append(common.row(rule["target"], key, geom, source, status, rule["kind"], name, **attrs))
            report.recognized.append((f"«{layer}»", rule["target"], len(grouped)))
        if "number" in rule:
            for layer, items in texts[i].items():
                report.recognized.append((f"«{layer}» (номера, {len(items)})", f"{rule['target']}.{rule['number']['attr']}", len(items)))

    for (layer, kind), n in sorted(skipped.items()):
        report.unrecognized.append(f"слой «{layer}»: {n} объектов типа {kind} правило не разбирает")
    for layer, kinds in sorted(other.items()):
        report.unrecognized.append(f"слой «{layer}»: нет правила раскладки, {sum(kinds.values())} объектов "
                                   f"({', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))})")
    report.ignored += [f"слой «{layer}»: {n} объектов" for layer, n in sorted(ignored.items())]
    report.confirm.append(f"система координат чертежа принята проектной ({project.crs_name}): в DXF ее нет")
    frames = common.frames_of(rows, project)
    geomlib.check_extent(project, frames)
    return frames
