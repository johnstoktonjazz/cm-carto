"""Выписка ЕГРН «кадастровый план территории» (XML) → parcels, zouit, zones_pzz (rules/egrn.yaml).

Берутся только номер, границы, категория, вид использования, площадь, вид и реестровый номер зоны: правообладатели
и прочие сведения в слои не попадают. Ординаты Росреестра: x — север, y — восток; система координат выписки
(coord_system) должна совпасть с проектной.
"""

import re
import xml.etree.ElementTree as ET
from collections import defaultdict

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon

from cm_carto.ingest import common, geom as geomlib, ids
from cm_carto.ingest.report import Report, at, num
from cm_carto.project import Project

ROOT_TAG = "extract_cadastral_plan_territory"


def need(el: ET.Element, path: str, where: str) -> str:
    text = (el.findtext(path) or "").strip()
    if not text:
        raise ValueError(f"{where}: нет {path}")
    return text


def contours(el: ET.Element, where: str, report: Report):
    """Контуры объекта: в каждом первое кольцо — внешнее, остальные — дыры; None, если границ нет."""
    parts = []
    for contour in el.iter("contour"):
        rings = [[(float(need(o, "y", where)), float(need(o, "x", where))) for o in se.iter("ordinate")]
                 for se in contour.iter("spatial_element")]
        if not rings or not rings[0]:
            continue
        poly, changed = geomlib.fix_polygon(Polygon(rings[0], rings[1:]))
        if changed:
            report.fixed.append(f"{where}: контур невалиден, исправлен {at(poly.centroid.x, poly.centroid.y)}")
        parts += geomlib.polygons_of(poly)
    return None if not parts else parts[0] if len(parts) == 1 else MultiPolygon(parts)


def first_match(text: str, entries: list[dict], field: str) -> dict | None:
    return next((e for e in entries if any(w in text.lower() for w in e[field])), None)


def pzz_zone(index: str | None, type_name: str, zones: list[dict]) -> str | None:
    letters = re.match(r"[А-Яа-я]+", index or "")
    by_index = next((z["zone"] for z in zones if letters and letters.group().upper() in z["index"]), None)
    by_name = next((z["zone"] for z in zones if any(w in type_name.lower() for w in z.get("name_contains", []))), None)
    return by_index or by_name


def ingest(path, rules: dict, project: Project, report: Report) -> dict[str, gpd.GeoDataFrame]:
    root = ET.parse(path).getroot()
    if root.tag != ROOT_TAG:
        raise ValueError(f"{path}: корень {root.tag}, нужен {ROOT_TAG}")
    system = need(root, "coord_system", path.name)
    if system != project.crs_name:
        raise ValueError(f"{path.name}: система координат выписки {system}, проекта — {project.crs_name}; "
                         f"пересчет выписки в проектную система не делает")
    requisites = "details_statement/group_top_requisites"
    source = common.Source(None, rules["source_label"].format(
        registration_number=need(root, f"{requisites}/registration_number", path.name),
        organ=need(root, f"{requisites}/organ_registr_rights", path.name)),
        need(root, f"{requisites}/date_formation", path.name), rules["status"])
    status = source.status
    rows = defaultdict(list)

    for rec in root.iter("land_record"):
        cad = need(rec, ".//cad_number", "land_record")
        where = f"участок {cad}"
        geom = contours(rec, where, report)
        if geom is None:
            report.unrecognized.append(f"{where}: границы в выписке не установлены")
            continue
        attrs = {"cadastral_number": cad}
        for field, path_ in (("land_category", ".//params/category/type/value"),
                             ("permitted_use", ".//params/permitted_use/permitted_use_established/by_document")):
            value = (rec.findtext(path_) or "").strip()
            if value:
                attrs[field] = value
        declared = float(need(rec, ".//params/area/value", where))
        if abs(geom.area - declared) > rules["area_tolerance"] * declared:
            report.confirm.append(f"{where}: площадь по выписке {num(declared, 0)} м2, по контуру {num(geom.area, 0)} м2")
        rows["parcels"].append(common.row("parcels", ids.key_from_text(cad), geom, source, status,
                                          rules["parcels"]["kind"], **attrs))

    for zone in root.iter("zone_and_border"):
        reg = need(zone, "reg_numb_border", "zone_and_border")
        where = f"граница {reg}"
        boundary = need(zone, "type_boundary/value", where)
        type_name = need(zone, "type_zone/value", where)
        name = (zone.findtext("name_by_doc") or "").strip() or None
        geom = contours(zone, where, report)
        if geom is None:
            report.unrecognized.append(f"{where}: границ нет")
        elif any(w in boundary.lower() for w in rules["boundary_types"]["zouit"]):
            entry = first_match(type_name, rules["zouit_types"], "contains")
            if entry is None:
                report.confirm.append(f"{where}: вид ЗОУИТ «{type_name}» не в словаре, записан как other")
            rows["zouit"].append(common.row("zouit", ids.key_from_text(reg), geom, source, status,
                                            entry["kind"] if entry else rules["zouit_other_kind"], name,
                                            zouit_type=entry["zouit_type"] if entry else "other",
                                            registry_number=reg))
        elif any(w in boundary.lower() for w in rules["boundary_types"]["territorial_zone"]):
            index = (zone.findtext("index") or "").strip() or None
            category = pzz_zone(index, type_name, rules["pzz"]["zones"])
            if category is None:
                report.unrecognized.append(f"{where}: категория зоны не определена по индексу «{index}» и названию «{type_name}»")
                continue
            attrs = {"zone": category, **({"code": index} if index else {})}
            rows["zones_pzz"].append(common.row("zones_pzz", ids.key_from_text(reg), geom, source, status,
                                                rules["pzz"]["kind"], name, **attrs))
        else:
            report.unrecognized.append(f"{where}: тип границы «{boundary}» не из правил")

    for layer, items in rows.items():
        report.recognized.append((f"{ROOT_TAG}", layer, len(items)))
    report.header.append(f"Источник: {source.label}, {source.date}")
    frames = common.frames_of(rows, project)
    geomlib.check_extent(project, frames)
    return frames
