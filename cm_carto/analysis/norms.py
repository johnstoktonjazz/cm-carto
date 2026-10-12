"""Реестры на входе анализа: записи NRM (нормы) и ASM (допущения) формата контракта fact 1.1.0.

Реестр норм проекта — `<папка анкеты>/registry/norms.yaml` (`carto.registry` анкеты может указать другой путь): список
записей. Допущения — файл `assumptions.yaml` рядом с реестром норм. Настоящие реестры ведет база; на полигоне —
синтетические образцы. Каждая запись проверяется по схеме fact; записи, которой нет, — ошибка, а не значение по умолчанию.

Норму для объекта правило буфера выбирает тремя способами (`rules/analysis.yaml`, `buffers.rules`): прямая ссылка,
значение атрибута-категории объекта, длина водотока из атрибута. Атрибуты вводит автор проекта (geopackage 1.2.0);
объект без атрибута и значение без нормы — ошибка со списком object_id, а не молчаливый пропуск.
"""

import json
from collections import defaultdict

import jsonschema
import pandas as pd
import yaml

from cm_carto import paths
from cm_carto.project import Project

ASSUMPTIONS = "assumptions.yaml"


def read(path, kind: str, what: str) -> dict[str, dict]:
    """Записи файла, проверенные по схеме fact; в файле только записи вида `kind`."""
    if not path.exists():
        raise FileNotFoundError(f"нет реестра {what} {path}")
    records = yaml.safe_load(path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(json.loads(paths.schema("fact").read_text(encoding="utf-8")))
    errors, out = [], {}
    for number, record in enumerate(records, start=1):
        errors += [f"запись {number}: {e.message}" for e in validator.iter_errors(record)]
        if record.get("kind") != kind:
            errors.append(f"запись {number} ({record.get('id')}): в реестре {what} только kind: {kind}")
        if record.get("id") in out:
            errors.append(f"{record.get('id')} повторяется")
        out[record.get("id")] = record
    if errors:
        raise ValueError(f"{path}: реестр не проходит проверку:\n  " + "\n  ".join(errors))
    return out


def load(project: Project) -> dict[str, dict]:
    return read(project.registry, "norm", "норм")


def load_assumptions(project: Project) -> dict[str, dict]:
    return read(project.registry.with_name(ASSUMPTIONS), "assumption", "допущений")


def get(registry: dict[str, dict], norm_id: str) -> dict:
    record = registry.get(norm_id)
    if record is None:
        raise ValueError(f"нормы {norm_id} нет в реестре: расстояние буфера без записи NRM не берется")
    if record["status"] == "superseded":
        raise ValueError(f"{norm_id} заменена ({record.get('superseded_by', 'записью без номера')}), правило устарело")
    if record["applicability"]["status"] == "not_applicable":
        note = record["applicability"].get("note")
        raise ValueError(f"{norm_id}: норма процитирована верно, но неприменима к объекту проекта "
                         f"(applicability.status: not_applicable{'; ' + note if note else ''}): буфер по ней не строится")
    return record


def distance_m(record: dict) -> float:
    if record.get("unit") != "м" or isinstance(record["value"], bool) or not isinstance(record["value"], (int, float)):
        raise ValueError(f"{record['id']}: для буфера нужно число в метрах, в записи {record['value']!r} {record.get('unit')!r}")
    if record["value"] <= 0:
        raise ValueError(f"{record['id']}: расстояние должно быть больше нуля")
    return float(record["value"])


def correspondence(assumptions: dict[str, dict], assumption_id: str) -> dict[str, str]:
    """Таблица допущения ASM: в `value` записи пары «значение: категория» через «;»."""
    record = assumptions.get(assumption_id)
    if record is None:
        raise ValueError(f"допущения {assumption_id} нет в реестре допущений")
    if record["status"] == "superseded":
        raise ValueError(f"{assumption_id} заменено ({record.get('superseded_by', 'записью без номера')}), правило устарело")
    table = {}
    for pair in str(record["value"]).split(";"):
        key, colon, category = (part.strip() for part in pair.partition(":"))
        if not colon or not key or not category:
            raise ValueError(f"{assumption_id}: в value ждем пары «значение: категория» через «;», а не {record['value']!r}")
        table[key] = category
    return table


def filled(value) -> bool:
    return value is not None and not pd.isna(value) and value != ""


def author_error(spec: dict, layer: str, ids: list[str]) -> str:
    return f"{spec['title']} вводит автор проекта: атрибут {spec['attr']} слоя {layer} не задан у {ids}"


def by_attr(project: Project, registry: dict, rule: dict, frame) -> list[dict]:
    """Норма по значению атрибута-категории; при `if_missing` — по другому атрибуту через допущение ASM."""
    spec = rule["norm_by_attr"]
    fallback = spec.get("if_missing")
    table = None
    empty, no_class, no_norm = [], defaultdict(list), defaultdict(list)
    picks = []
    for _, origin in frame.iterrows():
        value, fact_ids = origin.get(spec["attr"]), []
        if not filled(value) and fallback is not None and filled(origin.get(fallback["attr"])):
            if table is None:
                table = correspondence(load_assumptions(project), fallback["assumption_id"])
            if origin[fallback["attr"]] not in table:
                no_class[origin[fallback["attr"]]].append(origin["object_id"])
                continue
            value, fact_ids = table[origin[fallback["attr"]]], [fallback["assumption_id"]]
        if not filled(value):
            empty.append(origin["object_id"])
        elif value not in spec["norms"]:
            no_norm[value].append(origin["object_id"])
        else:
            record = get(registry, spec["norms"][value])
            picks.append({"norm": record, "fact_ids": [record["id"]] + fact_ids, "value": value})
    errors = [author_error(spec, rule["layer"], empty)] if empty else []
    if fallback is not None:
        errors += [f"в допущении {fallback['assumption_id']} нет соответствия для {fallback['attr']} = {key}: {ids}"
                   for key, ids in no_class.items()]
    errors += [f"в правиле нет нормы для значения {spec['attr']} = {key} (есть: {', '.join(spec['norms'])}): {ids}"
               for key, ids in no_norm.items()]
    if errors:
        raise ValueError("\n".join(errors))
    return picks


def by_length(registry: dict, rule: dict, frame) -> list[dict]:
    """Норма по длине водотока из атрибута: первая полоса, где длина меньше `below_km`."""
    spec = rule["norm_by_length"]
    empty = [o["object_id"] for _, o in frame.iterrows() if not filled(o.get(spec["attr"]))]
    if empty:
        raise ValueError(author_error(spec, rule["layer"], empty))
    picks = []
    for _, origin in frame.iterrows():
        band = next((b for b in spec["bands"] if "below_km" not in b or origin[spec["attr"]] < b["below_km"]), None)
        if band is None:
            raise ValueError("в norm_by_length нет полосы без границы")
        record = get(registry, band["norm"])
        picks.append({"norm": record, "fact_ids": [record["id"]], "value": None})
    return picks


def pick(project: Project, rule: dict, frame) -> list[dict]:
    """Запись нормы для каждого объекта таблицы по правилу, в порядке строк: {norm, fact_ids, value}."""
    registry = load(project)
    if "norm" in rule:
        record = get(registry, rule["norm"])
        return [{"norm": record, "fact_ids": [record["id"]], "value": None} for _ in range(len(frame))]
    if "norm_by_attr" in rule:
        return by_attr(project, registry, rule, frame)
    return by_length(registry, rule, frame)
