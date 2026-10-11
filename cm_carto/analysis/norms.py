"""Реестр норм на входе анализа: записи NORM формата контракта fact, расстояния буферов берутся отсюда.

Реестр проекта — `<папка анкеты>/registry/norms.yaml` (ключ `registry` анкеты может указать другой путь): список
записей. Настоящий реестр ведет база; на полигоне — синтетический образец. Каждая запись проверяется по схеме fact;
запись, которой нет, — ошибка, а не расстояние по умолчанию.
"""

import json
from pathlib import Path

import jsonschema
import yaml

from cm_carto import paths
from cm_carto.project import Project


def registry_path(project: Project) -> Path:
    return project.root / project.data.get("registry", "registry/norms.yaml")


def load(project: Project) -> dict[str, dict]:
    path = registry_path(project)
    if not path.exists():
        raise FileNotFoundError(f"нет реестра норм {path}")
    records = yaml.safe_load(path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(json.loads(paths.schema("fact").read_text(encoding="utf-8")))
    errors, out = [], {}
    for number, record in enumerate(records, start=1):
        errors += [f"запись {number}: {e.message}" for e in validator.iter_errors(record)]
        if record.get("kind") != "norm":
            errors.append(f"запись {number} ({record.get('id')}): в реестре норм только kind: norm")
        if record.get("id") in out:
            errors.append(f"{record.get('id')} повторяется")
        out[record.get("id")] = record
    if errors:
        raise ValueError(f"{path}: реестр не проходит проверку:\n  " + "\n  ".join(errors))
    return out


def get(registry: dict[str, dict], norm_id: str) -> dict:
    record = registry.get(norm_id)
    if record is None:
        raise ValueError(f"нормы {norm_id} нет в реестре: расстояние буфера без записи NORM не берется")
    if record["status"] == "superseded":
        raise ValueError(f"{norm_id} заменена ({record.get('superseded_by', 'записью без номера')}), правило устарело")
    return record


def distance_m(record: dict) -> float:
    if record.get("unit") != "м" or isinstance(record["value"], bool) or not isinstance(record["value"], (int, float)):
        raise ValueError(f"{record['id']}: для буфера нужно число в метрах, в записи {record['value']!r} {record.get('unit')!r}")
    if record["value"] <= 0:
        raise ValueError(f"{record['id']}: расстояние должно быть больше нуля")
    return float(record["value"])


def length_km(geom) -> float:
    """Длина объекта, км: линия — ее длина, контур — длинная сторона минимального описанного прямоугольника."""
    if geom.geom_type in ("LineString", "MultiLineString"):
        return geom.length / 1000
    rect = geom.minimum_rotated_rectangle.exterior.coords
    sides = [((rect[i][0] - rect[i + 1][0]) ** 2 + (rect[i][1] - rect[i + 1][1]) ** 2) ** 0.5 for i in range(2)]
    return max(sides) / 1000


def pick(registry: dict[str, dict], rule: dict, geom) -> dict:
    """Запись нормы для объекта по правилу: прямая ссылка или полоса по длине."""
    if "norm" in rule:
        return get(registry, rule["norm"])
    length = length_km(geom)
    for band in rule["norm_by_length"]:
        if "below_km" not in band or length < band["below_km"]:
            return get(registry, band["norm"])
    raise ValueError("в norm_by_length нет полосы без границы")
