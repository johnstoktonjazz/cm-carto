"""Измерения по изохроне как заготовки записей `CALC` (контракт fact): значение, формула, слои-входы с датами,
статус `check`. Номер `CALC-…` присваивает база; в заготовке поля `id` нет.

Входы записи реестра (`inputs`) — идентификаторы других записей, а слои геопакета записями не являются: слои и их даты
названы в формуле и в `sources`. В `inputs` попадает только номер допущения скорости, если он задан в правилах.
"""

import copy
import json

import yaml

from cm_carto.analysis import data, isochrone, params as params_mod
from cm_carto.project import Project

ISOCHRONES = "analysis-isochrones"


def calc_path(project: Project):
    return project.part(ISOCHRONES).with_suffix(".calc.yaml")


def layer_source(project: Project, layer: str, label: str, date: str) -> list[dict]:
    return [{"doc_id": f"geopackage:{f.stem}:{layer}", "label": f"слой {layer}, {label}, выгрузка {date}",
             "doc_status": "reference"} for f in data.locate(project, layer)]


def drafts(project: Project, object_id: str) -> list[dict]:
    p = params_mod.load()
    iso = data.one(project, object_id)
    roads, origin, results = isochrone.compute(project, iso["origin_id"], iso["mode"], [iso["minutes"]])
    reach, fresh = results[iso["minutes"]]
    if fresh.symmetric_difference(iso.geometry).area > 0.01:
        raise ValueError(f"{object_id}: изохрона в части не совпадает с расчетом по текущим данным и правилам, "
                         "нужен `cm-carto isochrone`")
    layer = iso["origin_id"].split(":")[0]
    roads_label, roads_date = isochrone.single(roads, "source_label"), isochrone.single(roads, "source_date")
    sources = (layer_source(project, "roads", roads_label, roads_date)
               + layer_source(project, layer, origin["source_label"], origin["source_date"]))
    speed = f"{iso['speed_kmh']:g}".replace(".", ",")
    method = (f"{iso['kind']} от {iso['origin_id']} по графу улиц (слой roads), средняя скорость {speed} км/ч, "
              f"полоса {p['isochrone']['buffer_m']:g} м вокруг достижимых частей отрезков")
    inputs = json.loads(iso["fact_ids"])

    def draft(what: str, value: float, unit: str, formula: str) -> dict:
        return {"kind": "calc", "subject": f"{object_id}, {what}", "value": float(round(value, 2)), "unit": unit,
                "status": "check", "inputs": list(inputs), "formula": f"{formula}; {method}",
                "sources": copy.deepcopy(sources)}

    return [
        draft("площадь", iso.geometry.area / 1e4, "га", "площадь полигона изохроны, м2 / 10000"),
        draft("длина достижимой сети", reach.length_m / 1000, "км",
              f"сумма длин частей отрезков слоя roads, достижимых за {iso['minutes']:g} мин, м / 1000"),
    ]


def save(project: Project, object_id: str) -> tuple:
    """Заготовки по изохроне в файл рядом с частью; прежние заготовки этой изохроны заменяются."""
    new = drafts(project, object_id)
    path = calc_path(project)
    old = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    kept = [d for d in old if not d["subject"].startswith(object_id + ", ")]
    items = sorted(kept + new, key=lambda d: d["subject"])
    path.write_text(yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return path, new
