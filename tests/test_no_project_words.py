"""Правило 10 паспорта: в коде, правилах, схемах и скриптах нет проектных значений — названий, координат, систем
координат проектов. Слова — из списка ниже и из анкет project.yaml всех примеров (имя, охват, система координат).
Скрипты полигона (POLYGON) — данные примера, а не код инструмента: генератор образцов и сборка К1 (К-027)."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHECKED = ["cm_carto", "rules", "schema", "scripts"]
POLYGON = {"scripts/make_samples.py", "scripts/build_k1.sh"}
WORDS = ["Дербент", "Derbent", "МСК-05", "Таганрог", "Taganrog"]


def project_values() -> set[str]:
    values = set(WORDS)
    for path in ROOT.glob("examples/*/project.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        values.add(data["name"])
        values.update(str(v) for v in data["extent_wgs84"])
        values.update({data["crs"]["name"], data["crs"]["definition"]})
    return values


def test_no_project_values_in_code():
    found = []
    values = project_values()
    for folder in CHECKED:
        for path in sorted((ROOT / folder).rglob("*")):
            if str(path.relative_to(ROOT)) in POLYGON:
                continue
            if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml", ".json", ".qml", ".qpt", ".sh"}:
                continue
            text = path.read_text(encoding="utf-8")
            for value in values:
                pattern = re.escape(value) if not re.fullmatch(r"[\d.]+", value) else rf"(?<![\d.]){re.escape(value)}(?![\d])"
                if re.search(pattern, text, flags=re.IGNORECASE):
                    found.append(f"{path.relative_to(ROOT)}: {value}")
    assert found == []
