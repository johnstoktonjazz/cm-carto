"""Сверка учреждений OSM/Overture с реестрами (bus.gov.ru, гостиницы).

Учреждение есть в реестре и в OSM — одна запись: геометрия из OSM, название, вместимость и ключ из реестра,
`confirmed_by` и подпись с обоими источниками. Только в OSM — остается `reference`, в подписи пометка «не подтверждено»
(для групп, которые реестр покрывает). Только в реестре и с координатами — запись реестра; без координат — в отчет.
"""

import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import yaml

from cm_carto.fetch import common, osm, overture, registries
from cm_carto.fetch.common import rules
from cm_carto.project import Project

POI_REGISTRIES = ["bus_gov", "hotels"]


def tokens(name: str | None) -> set[str]:
    """Различающие слова названия: нижний регистр, "е с двумя точками" как "е", без родовых слов; цифры остаются."""
    words = re.findall(r"[a-zа-я]+|\d+", (name or "").lower().replace("\u0451", "е"))
    return {w for w in words if w not in set(rules()["reconcile"]["noise_words"])}


def similarity(a: str | None, b: str | None) -> float:
    """Доля общих различающих слов по меньшему названию; разные номера — 0; оба названия пустые — 1."""
    ta, tb = tokens(a), tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    digits_a, digits_b = {w for w in ta if w.isdigit()}, {w for w in tb if w.isdigit()}
    if digits_a and digits_b and digits_a != digits_b:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def find_match(record: dict, candidates: gpd.GeoDataFrame):
    """Индекс подходящего объекта OSM или None. Есть координаты — ближайший по расстоянию из похожих по названию;
    нет — единственный похожий по названию."""
    rule = rules()["reconcile"]
    candidates = candidates[candidates["poi_type"] == record["poi_type"]]
    found = [(similarity(record["name"], name if isinstance(name, str) else None), index)
             for index, name in candidates["name"].items()]
    found = [(sim, index) for sim, index in found if sim >= rule["name_similarity"]]
    if record["geometry"] is None:
        return found[0][1] if len(found) == 1 else None
    near = [(candidates.geometry[index].distance(record["geometry"]), index) for _, index in found]
    near = [(d, index) for d, index in near if d <= rule["distance_m"]]
    return min(near)[1] if near else None


def apply(frames: dict[str, gpd.GeoDataFrame], institutions: dict[str, list[dict]]) -> tuple[dict, dict]:
    """Сверка слоев poi_* с учреждениями реестров. Возвращает слои и отчет (число сведенных, добавленных, без места)."""
    frames = {k: v.copy() for k, v in frames.items()}
    note = rules()["reconcile"]["unconfirmed_note"]
    covered = {pair for registry in institutions for pair in registries.covered(registry)}
    taken = {layer: set() for layer in frames}
    for layer, frame in frames.items():
        if layer.startswith("poi_") and "capacity" not in frame:
            frame["capacity"] = float("nan")
    added: dict[str, list[dict]] = {}
    report = {"matched": 0, "registry_only": 0, "unplaced": []}
    for registry, items in institutions.items():
        for record in sorted(items, key=lambda r: r["key"]):
            frame = frames[record["layer"]]
            index = find_match(record, frame[~frame.index.isin(taken[record["layer"]])])
            if index is not None:
                taken[record["layer"]].add(index)
                base = frame.loc[index]
                frame.at[index, "object_id"] = f"{record['layer']}:{record['key']}"
                frame.at[index, "name"] = record["name"]
                frame.at[index, "confirmed_by"] = [base["object_id"].split(":", 1)[1], record["key"]]
                frame.at[index, "source_label"] = f"{base['source_label']}; {record['label']}"
                frame.at[index, "source_license"] = f"{base['source_license']}; {record['license']}"
                frame.at[index, "source_date"] = max(base["source_date"], record["date"])
                if record["capacity"] is not None:
                    frame.at[index, "capacity"] = record["capacity"]
                report["matched"] += 1
            elif record["geometry"] is not None:
                added.setdefault(record["layer"], []).append(
                    {"key": record["key"], "name": record["name"], "kind": record["kind"], "label": record["label"],
                     "date": record["date"], "license": record["license"], "geometry": record["geometry"],
                     "poi_type": record["poi_type"], "capacity": record["capacity"], "confirmed_by": [record["key"]]})
                report["registry_only"] += 1
            else:
                report["unplaced"].append({"registry": registry, "key": record["key"], "name": record["name"],
                                           "address": record["address"]})
    for layer, frame in frames.items():
        if not layer.startswith("poi_"):
            continue
        for index in frame.index[~frame.index.isin(taken[layer])]:
            if (layer, frame.at[index, "poi_type"]) in covered:
                frame.at[index, "source_label"] = f"{frame.at[index, 'source_label']} — {note}"
        if layer in added:
            frames[layer] = pd.concat([frame, common.table(layer, added[layer], frame.crs)], ignore_index=True)
    return frames, report


def run(project: Project, base: str) -> Path:
    """cm-carto fetch <project.yaml> reconcile --base overture|osm: слои базы заново из кеша, сверка со всеми
    реестрами учреждений, лежащими в кеше, и перезапись части fetch-<base>.gpkg; отчет — рядом, .yaml."""
    loaded = [r for r in POI_REGISTRIES if (registries.cache_dir(project, r) / "SOURCE.yaml").exists()]
    if not loaded:
        raise ValueError("в кеше нет выгрузок bus_gov и hotels: сначала cm-carto fetch <project.yaml> bus_gov --file ...")
    if base == "overture":
        release = overture.release_of(project, None)
        frames, inputs = overture.layout(project, release), [overture.cache_dir(project, release) / "SOURCE.yaml"]
    else:
        frames, inputs = osm.layout(project), [osm.cache_dir(project) / "SOURCE.yaml"]
    frames, report = apply(frames, {r: registries.institutions(project, r) for r in loaded})
    inputs += [registries.cache_dir(project, r) / "data.csv" for r in loaded]
    path = common.write_part(project, f"fetch-{base}", frames, *inputs)
    report_path = path.with_suffix(".reconcile.yaml")
    report_path.write_text(yaml.safe_dump({"registries": loaded, **report}, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")
    print(f"сведено {report['matched']}, только в реестре {report['registry_only']}, без места {len(report['unplaced'])}")
    return path
