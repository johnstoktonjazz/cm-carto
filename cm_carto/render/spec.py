"""Описание карты (YAML): проверка по schema/map.schema.json и по стандарту верстки один раз при загрузке.

Дальше рендер доверяет описанию: роли есть в map.yaml, слои — в геопакете, геометрия слоя подходит роли, место
и ширина легенды — из map.yaml, система координат — из анкеты проекта.
"""

import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import jsonschema
import pyogrio
import shapely
import yaml

from cm_carto import gpkg, paths, project as project_file, tokens

SCHEMA = paths.ROOT / "schema" / "map.schema.json"
# Геометрия слоя геопакета (контракт) → геометрия роли в map.yaml.
GEOMETRY = {"MultiPolygon": "polygon", "MultiLineString": "line", "Point": "point"}
# Поле охвата по объекту (extent: <object_id>), мм места с каждой стороны.
FIELD_MM = 5


@cache
def schema() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def order(std: dict) -> list[str]:
    """Роли сверху вниз, как cm_deck.qgis.order: слои map.yaml и зоны ПЗЗ над слоем zoning.above."""
    keys = list(std["layers"])
    keys.insert(keys.index(std["zoning"]["above"]), "zoning")
    return keys


def role_spec(std: dict, role: str) -> dict:
    return std["zoning"] if role == "zoning" else std["layers"][role]


@dataclass(frozen=True)
class Role:
    key: str           # роль map.yaml
    layer: str         # слой геопакета
    where: str | None  # отбор (subset string)
    title: str         # строка легенды
    level: str         # уровень map.yaml: levels
    within: str | None = None  # object_id контура: на карте только объекты внутри него
    contour: shapely.Geometry | None = None


@dataclass(frozen=True)
class Spec:
    path: Path
    raw: dict
    project: project_file.Project
    data: Path
    place: str
    size_mm: tuple[float, float]
    center: tuple[float, float]
    scale: int
    north: bool | None
    legend: int | str
    roles: list[Role]   # в порядке стандарта сверху вниз
    title: str | None
    fact_ids: list[str]
    out: Path

    @property
    def overlay_out(self) -> Path:
        return self.out.with_name(self.out.stem + ".overlay.json")

    @property
    def preview_out(self) -> Path:
        return self.out.with_name(self.out.stem + ".preview.png")

    @property
    def provenance_out(self) -> Path:
        return self.out.with_name(self.out.stem + ".provenance.json")


def fit_scale(extent: list[float], size_mm: tuple[float, float]) -> int:
    """Наименьший масштаб с двумя значащими цифрами, при котором охват виден целиком на месте size_mm."""
    xmin, ymin, xmax, ymax = extent
    exact = max((xmax - xmin) / size_mm[0], (ymax - ymin) / size_mm[1]) * 1000
    step = 10 ** max(math.floor(math.log10(exact)) - 1, 0)
    return int(math.ceil(exact / step) * step)


def find(path: Path, data: Path, present: set[str], object_id: str) -> shapely.Geometry:
    """Геометрия объекта геопакета по object_id (<слой>:<ключ>): ровно один объект."""
    layer = object_id.split(":", 1)[0]
    if layer not in present:
        raise ValueError(f"{path}: объект {object_id}: слоя {layer} нет в {data.name}")
    frame = pyogrio.read_dataframe(data, layer=layer, columns=["object_id"],
                                   where=f"object_id = '{object_id.replace(chr(39), chr(39) * 2)}'")
    if len(frame) != 1:
        raise ValueError(f"{path}: объект {object_id}: в {data.name} найдено {len(frame)}, нужен один")
    return frame.geometry.iloc[0]


def load(path: Path) -> Spec:
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '.'}: {e.message}"
              for e in jsonschema.Draft202012Validator(schema()).iter_errors(raw)]
    if errors:
        raise ValueError(f"{path}: описание карты не проходит schema/map.schema.json:\n  " + "\n  ".join(errors))
    base = path.parent
    std = tokens.load("map")
    project = project_file.load(base / raw["project"])
    data = (base / raw["data"]).resolve() if "data" in raw else project.gpkg

    if raw["place"] not in std["places"]:
        raise ValueError(f"{path}: места {raw['place']} нет в map.yaml: places ({', '.join(std['places'])})")
    size = tokens.place_size_mm(raw["place"])
    legend = raw["legend"]
    if legend == "inside":
        if raw["place"] not in std["legend"]["inside"]["places"]:
            raise ValueError(f"{path}: у места {raw['place']} нет легенды внутри карты (map.yaml: legend.inside.places)")
    elif legend not in std["legend"]["widths"]:
        raise ValueError(f"{path}: legend {legend}: ширина места легенды из map.yaml: legend.widths "
                         f"({', '.join(map(str, std['legend']['widths']))}) или inside")
    if "crs" in raw and raw["crs"] != project.crs_name:
        raise ValueError(f"{path}: crs {raw['crs']}, у проекта {project.crs_name}")

    if not data.is_file():
        raise FileNotFoundError(f"{path}: нет геопакета {data}")
    info = gpkg.meta(data)
    if info["contract"] != gpkg.CONTRACT:
        raise ValueError(f"{data}: контракт {info['contract']}, нужен {gpkg.CONTRACT}")
    if info["crs"] != project.crs_name:
        raise ValueError(f"{data}: система координат {info['crs']}, у проекта {project.crs_name}")
    present = {name for name, _ in pyogrio.list_layers(data)}

    known = order(std)
    unknown = sorted(set(raw["layers"]) - set(known))
    if unknown:
        raise ValueError(f"{path}: ролей нет в map.yaml: {', '.join(unknown)} (роли: {', '.join(known)})")
    roles = []
    for key in known:
        if key not in raw["layers"]:
            continue
        options = raw["layers"][key] or {}
        layer = options.get("layer", key)
        if layer not in present:
            raise ValueError(f"{path}: роль {key}: слоя {layer} нет в {data.name} ({', '.join(sorted(present))})")
        spec = role_spec(std, key)
        geometry = GEOMETRY[gpkg.layers()[layer][0]]
        if geometry != spec["geometry"]:
            raise ValueError(f"{path}: роль {key} рисует {spec['geometry']}, у слоя {layer} геометрия {geometry}")
        within = options.get("within")
        contour = None
        if within:
            contour = find(path, data, present, within)
            if contour.geom_type not in ("Polygon", "MultiPolygon"):
                raise ValueError(f"{path}: роль {key}: within {within} — не контур ({contour.geom_type})")
        roles.append(Role(key, layer, options.get("where"), options.get("title", spec["title"]), spec["level"],
                          within, contour))

    if "extent" in raw:
        if isinstance(raw["extent"], str):
            bounds = list(find(path, data, present, raw["extent"]).bounds)
            scale = fit_scale(bounds, (size[0] - 2 * FIELD_MM, size[1] - 2 * FIELD_MM))
        else:
            bounds = raw["extent"]
            scale = fit_scale(bounds, size)
        xmin, ymin, xmax, ymax = bounds
        center = ((xmin + xmax) / 2, (ymin + ymax) / 2)
    else:
        if isinstance(raw["center"], str):
            xmin, ymin, xmax, ymax = find(path, data, present, raw["center"]).bounds
            center = ((xmin + xmax) / 2, (ymin + ymax) / 2)
        else:
            center = tuple(raw["center"])
        scale = raw["scale"]
    return Spec(path=path, raw=raw, project=project, data=data, place=raw["place"], size_mm=size, center=center,
                scale=scale, north=raw.get("north"), legend=legend, roles=roles, title=raw.get("title"),
                fact_ids=raw.get("fact_ids", []), out=(base / raw["out"]).resolve())
