"""Правила раскладки rules/<имя>.yaml: проверяются по schema/rules.schema.json и по контракту один раз при загрузке."""

import json
from functools import cache
from pathlib import Path

import jsonschema
import yaml

from cm_carto import gpkg, paths

GEOMETRY = {"polygon": "MultiPolygon", "line": "MultiLineString"}


def path(name: str) -> Path:
    return paths.ROOT / "rules" / f"{name}.yaml"


@cache
def load(name: str) -> dict:
    rules = yaml.safe_load(path(name).read_text(encoding="utf-8"))
    schema = json.loads((paths.ROOT / "schema" / "rules.schema.json").read_text(encoding="utf-8"))
    errors = [f"{'/'.join(str(p) for p in e.absolute_path) or '.'}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema).iter_errors(rules)]
    errors += contract_errors(rules) if not errors else []
    if errors:
        raise ValueError(f"{path(name)}: правила не проходят проверку:\n  " + "\n  ".join(errors))
    return rules


def _attrs(layer: str) -> dict:
    return gpkg.layers()[layer][1]


def contract_errors(rules: dict) -> list[str]:
    """Слои, атрибуты и перечни правил — по контракту geopackage."""
    errors = []

    def check_layer(target: str, attrs: list[str], geometry: str | None = None):
        if target not in gpkg.layers():
            errors.append(f"слоя {target} нет в контракте {gpkg.CONTRACT}")
            return
        errors.extend(f"{target}: атрибута {a} нет в контракте" for a in attrs if a not in _attrs(target))
        if geometry and gpkg.layers()[target][0] != GEOMETRY[geometry]:
            errors.append(f"{target}: слой контракта {gpkg.layers()[target][0]}, правило — {geometry}")

    if rules["type"] == "dxf":
        for rule in rules["layers"]:
            number = rule.get("number", {}).get("attr")
            check_layer(rule["target"], list(rule.get("attrs", {})) + ([number] if number else []), rule["geometry"])
    elif rules["type"] == "vector":
        for rule in rules["layers"]:
            check_layer(rule["target"], list(rule.get("attrs", {})))
    else:
        for target in ("parcels", "zouit", "zones_pzz"):
            check_layer(target, [])
        allowed = _attrs("zouit")["zouit_type"]["enum"]
        errors += [f"zouit_type {r['zouit_type']} не из перечня контракта" for r in rules["zouit_types"]
                   if r["zouit_type"] not in allowed]
        allowed = _attrs("zones_pzz")["zone"]["enum"]
        errors += [f"zone {z['zone']} не из перечня контракта" for z in rules["pzz"]["zones"] if z["zone"] not in allowed]
    return errors
