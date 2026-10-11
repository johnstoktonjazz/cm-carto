"""Где лежат соседние репозитории: контракты и дизайн-система.

cm-contracts — подмодуль в корне репозитория или путь из CM_CONTRACTS.
cm-design — соседний клон (../cm-design) или путь из CM_DESIGN; картограф его только читает.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _dir(env: str, default: Path, marker: str) -> Path:
    path = Path(os.environ.get(env, default)).resolve()
    if not (path / marker).exists():
        raise FileNotFoundError(f"{env}: нет {path / marker}; задать {env} или склонировать репозиторий в {default}")
    return path


def contracts() -> Path:
    return _dir("CM_CONTRACTS", ROOT / "cm-contracts", "contracts")


def design() -> Path:
    return _dir("CM_DESIGN", ROOT.parent / "cm-design", "tokens/map.yaml")


def qgis_styles() -> Path:
    """Стили и компоновки верстки (этап 6 верстки сдан, Д-045): берутся без правок."""
    return design() / "design" / "qgis"


def schema(name: str) -> Path:
    return contracts() / "contracts" / name / "schema.json"
