"""Правила раскладки `<имя>.yaml`: проверяются по schema/rules.schema.json и по контракту один раз при загрузке.

Откуда берутся правила. Типовые лежат в `rules/` модуля. Проектные — в папке, которую анкета проекта называет
полем `carto.rules` (от папки анкеты); файл `<имя>.yaml` там берется вместо модульного. Модуль проектных значений не
содержит, поэтому имена слоев чертежей конкретного проекта задаются только в папке проекта.

Файл проекта бывает двух видов.

1. Самостоятельный: полный файл правил того же вида, что в `rules/` (так задается раскладка, которой в модуле нет).
2. Дополнение: первая строка `extends: <имя правил модуля>`. Файл содержит только то, что отличается:
   - `layers` (чертеж, векторный файл) и `zouit_types`, `pzz.zones` (выписка) проверяются раньше модульных,
     первое совпадение выигрывает, поэтому правило проекта сильнее модульного;
   - `ignore` добавляется к модульному;
   - остальные ключи (`tolerance_m`, `pzz.kind` и т.п.) заменяют модульные.
   Ключа `status` в правилах чертежа и векторного файла нет: статус задает паспорт источника (схема его отвергает).

Пример дополнения для чертежа (`<папка правил>/ppt.yaml`):

    extends: ppt
    layers:
      - {target: blocks, drawing: ["КВАРТАЛ*"], geometry: polygon, kind: квартал ППТ}

Итог (дополнение + модульный файл) проверяется схемой и контрактом один раз; ошибка называет оба файла. Какие файлы
применены, пишет шапка отчета импорта; оба файла входят в `input_hash` части.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import jsonschema
import yaml

from cm_carto import gpkg, paths
from cm_carto.project import Project

GEOMETRY = {"polygon": "MultiPolygon", "line": "MultiLineString"}


def path(name: str) -> Path:
    return paths.ROOT / "rules" / f"{name}.yaml"


@dataclass(frozen=True)
class Applied:
    """Итоговые правила и файлы, из которых они сложены."""
    rules: dict
    project_file: Path | None
    module_file: Path | None

    @property
    def files(self) -> list[Path]:
        return [f for f in (self.project_file, self.module_file) if f]

    def header(self, project: Project) -> list[str]:
        if not self.project_file:
            return [f"Правила: rules/{self.module_file.name}"]
        lines = [f"Правила проекта: {self.project_file.relative_to(project.root).as_posix()}"]
        if self.module_file:
            lines.append(f"Правила модуля: rules/{self.module_file.name}")
        return lines


def merge(base: dict, own: dict) -> dict:
    """Дополнение проекта поверх модульных правил: списки правил проекта идут первыми, ignore — после модульного."""
    merged = {**base, **{k: v for k, v in own.items() if k != "pzz"}}
    for key in ("layers", "zouit_types"):
        if key in own:
            merged[key] = own[key] + base.get(key, [])
    if "ignore" in own:
        merged["ignore"] = base["ignore"] + [p for p in own["ignore"] if p not in base["ignore"]]
    if "pzz" in own:
        zones = own["pzz"].get("zones", []) + base.get("pzz", {}).get("zones", [])
        merged["pzz"] = {**base.get("pzz", {}), **own["pzz"], "zones": zones}
    return merged


@cache
def _applied(name: str, folder: Path | None) -> Applied:
    own_path = folder / f"{name}.yaml" if folder and (folder / f"{name}.yaml").exists() else None
    own = yaml.safe_load(own_path.read_text(encoding="utf-8")) if own_path else None
    if own is not None and "extends" in own:
        module_path = path(own["extends"])
        if not module_path.exists():
            raise ValueError(f"{own_path}: extends {own['extends']} — в модуле нет файла {module_path}")
        base = yaml.safe_load(module_path.read_text(encoding="utf-8"))
        if own.get("type", base["type"]) != base["type"]:
            raise ValueError(f"{own_path}: type {own['type']}, а правила модуля {module_path.name} типа {base['type']}")
        rules = merge(base, own)
        applied = Applied(rules, own_path, module_path)
    elif own is not None:
        applied = Applied(own, own_path, None)
    else:
        applied = Applied(yaml.safe_load(path(name).read_text(encoding="utf-8")), None, path(name))
    schema = json.loads((paths.ROOT / "schema" / "rules.schema.json").read_text(encoding="utf-8"))
    errors = [f"{'/'.join(str(p) for p in e.absolute_path) or '.'}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema).iter_errors(applied.rules)]
    errors += contract_errors(applied.rules) if not errors else []
    if errors:
        raise ValueError(f"{', '.join(str(f) for f in applied.files)}: правила не проходят проверку:\n  " + "\n  ".join(errors))
    return applied


def applied(name: str, project: Project | None = None) -> Applied:
    return _applied(name, project.rules if project else None)


def load(name: str, project: Project | None = None) -> dict:
    return applied(name, project).rules


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
