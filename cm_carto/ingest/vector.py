"""Векторные файлы третьих лиц (SHP, GPKG) → слои геопакета по rules/vector.yaml.

Слой или файл источника по шаблону имени → слой контракта; поля источника → атрибуты слоя. Статус — из паспорта источника. Геометрия пересчитывается
в систему координат проекта один раз здесь; система координат файла обязательна.
"""

import fnmatch
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely

from cm_carto.ingest import common, geom as geomlib, ids
from cm_carto.ingest.report import Report, at
from cm_carto.project import Project


def source_layers(path: Path) -> list[str]:
    return [name for name, _ in pyogrio.list_layers(path)]


def ingest(path: Path, source: common.Source, rules: dict, project: Project, report: Report) -> dict[str, gpd.GeoDataFrame]:
    status = source.status
    rows = defaultdict(list)
    systems = set()
    for name in source_layers(path):
        rule = next((r for r in rules["layers"] if any(fnmatch.fnmatchcase(name.casefold(), p.casefold()) for p in r["source"])), None)
        if rule is None:
            report.unrecognized.append(f"слой «{name}»: нет правила раскладки")
            continue
        frame = pyogrio.read_dataframe(path, layer=name)
        if frame.crs is None:
            raise ValueError(f"{path.name}, слой {name}: нет системы координат")
        systems.add(frame.crs.to_string())
        frame = frame.to_crs(project.crs)
        columns = [c for c in [rule.get("key"), rule.get("name"), *rule.get("attrs", {}).values()] if c]
        missing = [c for c in columns if c not in frame.columns]
        if missing:
            raise ValueError(f"{path.name}, слой {name}: нет полей {missing}, правило ждет их")
        empty = frame.geometry.isna() | frame.geometry.is_empty
        if empty.any():
            report.unrecognized.append(f"слой «{name}»: объектов без геометрии {int(empty.sum())}")
        count = 0
        for _, item in frame[~empty].iterrows():
            geom = shapely.force_2d(item.geometry)
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                geom, changed = geomlib.fix_polygon(geom)
                if changed:
                    report.fixed.append(f"слой «{name}»: невалидный контур исправлен {at(geom.centroid.x, geom.centroid.y)}")
            key_value = item[rule["key"]] if "key" in rule else None
            key = ids.key_from_text(str(key_value)) if key_value is not None and not pd.isna(key_value) else ids.geometry_key(geom)
            attrs = {a: item[f] for a, f in rule.get("attrs", {}).items() if not pd.isna(item[f])}
            title = item[rule["name"]] if "name" in rule and not pd.isna(item[rule["name"]]) else None
            rows[rule["target"]].append(common.row(rule["target"], key, geom, source, status, rule["kind"],
                                                   str(title) if title else None, **attrs))
            count += 1
        report.recognized.append((f"«{name}»", rule["target"], count))
    report.confirm.append(f"пересчет из {', '.join(sorted(systems))} в {project.crs_name} сделан PROJ при импорте; "
                          f"для МСК сверить параметры пересчета")
    frames = common.frames_of(rows, project)
    geomlib.check_extent(project, frames)
    return frames
