"""Параметры анализа rules/analysis.yaml: проверка по schema/analysis.schema.json один раз при загрузке."""

import json
from functools import cache
from pathlib import Path

import jsonschema
import yaml

from cm_carto import paths

RULES = paths.ROOT / "rules" / "analysis.yaml"


@cache
def load(path: Path = RULES) -> dict:
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema = json.loads((paths.ROOT / "schema" / "analysis.schema.json").read_text(encoding="utf-8"))
    errors = [f"{'/'.join(str(p) for p in e.absolute_path) or '.'}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema).iter_errors(rules)]
    if errors:
        raise ValueError(f"{path}: параметры не проходят проверку:\n  " + "\n  ".join(errors))
    return rules
