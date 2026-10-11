"""Измерения по изохроне как заготовки записей `CALC` (контракт fact): значение, формула, слои-входы с датами,
статус `check`. Номер `CALC-…` присваивает база; в заготовке поля `id` нет.

Входы записи реестра (`inputs`) — идентификаторы других записей, а слои геопакета записями не являются: слои и их даты
названы в формуле и в `sources`. В `inputs` попадает только номер допущения скорости, если он задан в правилах.
"""

import copy
import json

import pyogrio
import yaml

from cm_carto.analysis import data, isochrone, params as params_mod
from cm_carto.project import Project

ISOCHRONES = "analysis-isochrones"


def calc_path(project: Project):
    return project.part(ISOCHRONES).with_suffix(".calc.yaml")


def layer_source(project: Project, layer: str, objects) -> list[dict]:
    """Источники слоя по частям: в подпись части попадают подписи только тех объектов, что лежат в этой части.

    `objects` — объекты слоя, попавшие в расчет (таблица со столбцами object_id, source_label, source_date).
    """
    wanted = {}
    for _, o in objects.iterrows():
        wanted.setdefault(o["object_id"], (o["source_label"], o["source_date"]))
    out = []
    for f in data.locate(project, layer):
        ids = set(pyogrio.read_dataframe(f, layer=layer, columns=[], read_geometry=False).index.map(str)) \
            if False else set(pyogrio.read_dataframe(f, layer=layer, read_geometry=False)["object_id"])
        for label, date in sorted({wanted[i] for i in ids if i in wanted}):
            out.append({"doc_id": f"geopackage:{f.stem}:{layer}", "label": f"слой {layer}, {label}, выгрузка {date}",
                        "doc_status": "reference"})
    return out


def drafts(project: Project, object_id: str) -> list[dict]:
    p = params_mod.load()
    iso = data.one(project, object_id)
    roads, origin, results = isochrone.compute(project, iso["origin_id"], iso["mode"], [iso["minutes"]])
    reach, fresh = results[iso["minutes"]]
    if fresh.symmetric_difference(iso.geometry).area > 0.01:
        raise ValueError(f"{object_id}: изохрона в части не совпадает с расчетом по текущим данным и правилам, "
                         "нужен `cm-carto isochrone`")
    layer = iso["origin_id"].split(":")[0]
    isochrone.single(roads, "source_label"), isochrone.single(roads, "source_date")   # улицы из одного источника
    sources = (layer_source(project, "roads", roads) + layer_source(project, layer, origin.to_frame().T))
    speed = f"{iso['speed_kmh']:g}".replace(".", ",")
    method = (f"{iso['kind']} от {iso['origin_id']} по графу улиц (слой roads), средняя скорость {speed} км/ч, "
              f"полоса {p['isochrone']['buffer_m']:g} м вокруг достижимых частей отрезков")
    inputs = json.loads(iso["fact_ids"])  # допущение скорости ASM-… из правил, если задано

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
