"""Правило 10 паспорта: в коде, правилах и схемах нет проектных значений — названий, координат, систем координат
проектов. Слова — из списка ниже и из анкет project.yaml всех примеров."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHECKED = ["cm_carto", "rules", "schema"]
WORDS = ["Дербент", "Derbent", "МСК-05", "Таганрог", "Taganrog"]


def project_values() -> set[str]:
    values = set(WORDS)
    for path in ROOT.glob("examples/*/project.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        values.add(data["name"])
        values.update(str(v) for v in data["extent_wgs84"])
    return values


def test_no_project_values_in_code():
    found = []
    values = project_values()
    for folder in CHECKED:
        for path in sorted((ROOT / folder).rglob("*")):
            if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml", ".json", ".qml", ".qpt"}:
                continue
            text = path.read_text(encoding="utf-8")
            for value in values:
                pattern = re.escape(value) if not re.fullmatch(r"[\d.]+", value) else rf"(?<![\d.]){re.escape(value)}(?![\d])"
                if re.search(pattern, text, flags=re.IGNORECASE):
                    found.append(f"{path.relative_to(ROOT)}: {value}")
    assert found == []
