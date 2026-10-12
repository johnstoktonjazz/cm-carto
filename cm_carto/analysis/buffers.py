"""Буферы вокруг объектов слоя по записи NORM: санитарно-защитные, водоохранные, береговые, придорожные зоны.

Правило (`rules/analysis.yaml`, `buffers.rules`) называет слой, подбор объектов, тип буфера и норму реестра;
расстояние берется из `value` записи NORM, в правилах и коде чисел расстояний нет. Статус буфера — `calc` (geopackage 1.1.0), `source_license` — лицензия геометрии источника,
`fact_ids` — [NORM-…], подпись — из правила `label`.
"""

import geopandas as gpd
import pandas as pd

from cm_carto import gpkg
from cm_carto.analysis import data, norms, params as params_mod
from cm_carto.project import Project

PART = "analysis-buffers"
CARRIED = ("name", "source_doc")


def rule_of(name: str) -> dict:
    rules = params_mod.load()["buffers"]["rules"]
    if name not in rules:
        raise ValueError(f"правила буфера {name} нет; есть: {', '.join(sorted(rules))}")
    return rules[name]


def sources(project: Project, rule: dict, origin_ids: list[str] | None):
    """Объекты-источники: названные явно (из слоя правила) или подобранные по `match`."""
    layer = rule["layer"]
    if origin_ids:
        wrong = [i for i in origin_ids if i.split(":")[0] != layer]
        if wrong:
            raise ValueError(f"правило ждет объекты слоя {layer}, а названы {wrong}")
        quoted = ", ".join("'" + i.replace("'", "''") + "'" for i in origin_ids)
        frame = data.layer(project, layer, where=f"object_id IN ({quoted})")
        missing = sorted(set(origin_ids) - set(frame["object_id"]))
        if missing:
            raise ValueError(f"объектов нет в слое {layer}: {missing}")
    else:
        if "match" not in rule:
            raise ValueError("в правиле нет подбора объектов (`match`): назовите их через --from")
        frame = data.layer(project, layer)
        for column, wanted in rule["match"].items():
            if column not in frame:
                raise ValueError(f"в слое {layer} нет столбца {column} для подбора по правилу")
            frame = frame[frame[column].isin([wanted] if isinstance(wanted, str) else wanted)]
        if frame.empty:
            raise ValueError(f"в слое {layer} нет объектов под правило {rule['match']}: назовите объекты через --from")
    return frame.sort_values("object_id", kind="stable")


def object_id(rule_name: str, origin_id: str) -> str:
    return f"buffers:{rule_name}-{origin_id.replace(':', '-')}"


def distance_text(distance: float) -> str:
    return f"{distance:g}".replace(".", ",")


def licence_of(origin, p: dict) -> str:
    """Лицензия исходных данных: у внешнего источника своя, у данных заказчика (КПТ, ППТ) — запись правил."""
    value = origin.get("source_license")
    return value if isinstance(value, str) and value else p["own_data_license"]


def rows(project: Project, rule_name: str, origin_ids: list[str] | None = None) -> list[dict]:
    p = params_mod.load()["buffers"]
    rule = rule_of(rule_name)
    registry = norms.load(project)
    out = []
    for _, origin in sources(project, rule, origin_ids).iterrows():
        record = norms.pick(registry, rule, origin.geometry)
        distance = norms.distance_m(record)
        zone = origin.geometry.buffer(distance, quad_segs=p["quad_segs"])
        if rule.get("exclude_source"):
            zone = zone.difference(origin.geometry)
        row = {"object_id": object_id(rule_name, origin["object_id"]), "kind": rule["kind"], "status": "calc",
               "source_label": p["label"].format(distance=distance_text(distance), norm=record["id"],
                                                 origin=origin["source_label"]),
               "source_date": origin["source_date"],
               "source_license": licence_of(origin, p), "fact_ids": [record["id"]], "buffer_type": rule["buffer_type"],
               "distance_m": distance, "origin_id": origin["object_id"], "geometry": zone}
        row.update({k: origin[k] for k in CARRIED if k in origin and isinstance(origin[k], str)})
        out.append(row)
    return out


def save(project: Project, new: list[dict]):
    """Часть analysis-buffers: новые буферы заменяют прежние с тем же object_id, остальные сохраняются."""
    part = project.part(PART)
    items = pd.DataFrame(new)
    if part.exists():
        old = gpkg.read(part)["buffers"]
        items = pd.concat([old[~old["object_id"].isin(items["object_id"])], items], ignore_index=True)
    frame = gpd.GeoDataFrame(items, geometry="geometry", crs=project.crs)
    layers = sorted({i.split(":")[0] for i in frame["origin_id"]})
    digest = gpkg.input_hash(params_mod.RULES, project.registry, project.crs_name,
                             *[f for layer in layers for f in data.locate(project, layer) if f != part],
                             sorted(frame["object_id"]))
    return gpkg.write(part, {"buffers": frame}, project.name, project.crs, project.crs_name, digest)
