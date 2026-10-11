"""Подсчеты в зоне как заготовки записей `CALC` (контракт fact): сколько объектов слоя в зоне, на какую площадь
(длину) зона задевает полосы, участки, кварталы. Формат и правила — как у `measure`: поля без `id`, статус `check`,
слои и даты — в `formula` и `sources`, в `inputs` — записи реестра зоны (например NORM-… буфера).

Точки считаются, если лежат внутри зоны; площади и линии — если пересекают зону на площади (длине) больше нуля,
касание границей не считается. Файл заготовок — `analysis-buffers.calc.yaml` рядом с частью буферов.
"""

import copy

import yaml

from cm_carto import gpkg
from cm_carto.analysis import buffers, data, measure, params as params_mod
from cm_carto.project import Project


def calc_path(project: Project):
    return project.part(buffers.PART).with_suffix(".calc.yaml")


def hits(zone_geom, frame) -> list[dict]:
    """Объекты слоя в зоне: [{object_id, area_m2 | length_m}] по возрастанию object_id."""
    out = []
    for _, row in frame.sort_values("object_id", kind="stable").iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type in ("Point", "MultiPoint"):
            if zone_geom.contains(geom):
                out.append({"object_id": row["object_id"]})
        elif geom.geom_type in ("Polygon", "MultiPolygon"):
            area = zone_geom.intersection(geom).area
            if area > 0:
                out.append({"object_id": row["object_id"], "area_m2": area})
        else:
            length = zone_geom.intersection(geom).length
            if length > 0:
                out.append({"object_id": row["object_id"], "length_m": length})
    return out


def found(project: Project, zone_id: str, layer: str, where: str | None):
    zone = data.one(project, zone_id)
    frame = data.layer(project, layer, where=where, bbox=tuple(zone.geometry.bounds))
    return zone, frame, hits(zone.geometry, frame)


def source_list(project: Project, zone, frame, hit_ids: set, layer: str) -> list[dict]:
    """Слои зоны и подсчитанных объектов: подписи и даты их источников, без повторов."""
    seen, out = set(), []
    pairs = [(zone["object_id"].split(":")[0], zone["source_label"], zone["source_date"])]
    pairs += sorted({(layer, r["source_label"], r["source_date"]) for _, r in frame.iterrows() if r["object_id"] in hit_ids})
    for name, label, date in pairs:
        for s in measure.layer_source(project, name, label, date):
            if (s["doc_id"], s["label"]) not in seen:
                seen.add((s["doc_id"], s["label"]))
                out.append(s)
    return out


def number(value: float) -> float:
    return float(round(value, 2))


def drafts(project: Project, zone_id: str, layer: str, where: str | None = None) -> list[dict]:
    limit = params_mod.load()["count"]["list_limit"]
    zone, frame, found_ = found(project, zone_id, layer, where)
    ids = [h["object_id"] for h in found_]
    sources = source_list(project, zone, frame, set(ids), layer)
    scope = f"слой {layer}" + (f", отбор «{where}»" if where else "")
    listed = ", ".join(ids[:limit]) + (f" и еще {len(ids) - limit}" if len(ids) > limit else "") if ids else "нет"
    inputs = list(zone["fact_ids"]) if "fact_ids" in zone and zone["fact_ids"] is not None else []

    def draft(what: str, value: float, unit: str, formula: str) -> dict:
        return {"kind": "calc", "subject": f"{zone_id}, {what}, {scope}", "value": value, "unit": unit,
                "status": "check", "inputs": list(inputs), "formula": f"{formula}; зона {zone_id}; {scope}",
                "sources": copy.deepcopy(sources)}

    kinds = {gpkg.layers()[layer][0]} if layer in gpkg.layers() else set()
    out = [draft("число объектов", len(ids), "шт.",
                 f"число объектов слоя {layer}: точки внутри зоны, площади и линии — пересекающие зону; объекты: {listed}")]
    if kinds & {"Polygon", "MultiPolygon"}:
        areas = [h for h in found_ if "area_m2" in h]
        detail = ", ".join(f"{h['object_id']} {h['area_m2'] / 1e4:.2f} га" for h in areas[:limit])
        out.append(draft("площадь пересечения", number(sum(h["area_m2"] for h in areas) / 1e4), "га",
                         f"сумма площадей пересечения зоны с объектами слоя {layer}, м2 / 10000; {detail or 'нет'}"
                         + (f" и еще {len(areas) - limit}" if len(areas) > limit else "")))
    if kinds & {"LineString", "MultiLineString"}:
        lines = [h for h in found_ if "length_m" in h]
        out.append(draft("длина в зоне", number(sum(h["length_m"] for h in lines) / 1000), "км",
                         f"сумма длин частей объектов слоя {layer} внутри зоны, м / 1000"))
    return out


def save(project: Project, zone_id: str, layer: str, where: str | None = None) -> tuple:
    """Заготовки в файл рядом с частью; прежние с тем же subject заменяются."""
    new = drafts(project, zone_id, layer, where)
    path = calc_path(project)
    old = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    replaced = {d["subject"] for d in new}
    items = sorted([d for d in old if d["subject"] not in replaced] + new, key=lambda d: d["subject"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return path, new
