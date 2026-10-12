"""Анкета проекта project.yaml: все проектное — отсюда, в коде проектных значений нет.

Анкету читает cm_core базы, владелец анкеты — база. Верхний уровень — общие поля базы (`name`, `crs`), картограф их
только читает; другие поля базы на верхнем уровне допустимы, картограф их не знает и не трогает. Все остальное,
что читает только картограф, лежит в разделе `carto:`:

    name: <имя проекта>
    crs:
      name: МСК-NN зона N         # как подписывать; EPSG-код или МСК с зоной
      definition: "+proj=tmerc …" # что понимает PROJ: EPSG-код, строка PROJ или WKT (для МСК — параметры)
    carto:
      extent: [xmin, ymin, xmax, ymax]        # охват проекта в системе координат проекта, ее единицы (метры)
      extent_wgs84: [xmin, ymin, xmax, ymax]  # необязательно: охват выгрузок внешних источников, градусы;
                                              # нужен, когда кеш fetch снят именно на него, иначе — extent в WGS84
      geo: 00_База_знаний/geo                 # папка геоданных проекта от папки анкеты
      inputs: 01_Исходные                     # образцы и кеш внешних источников от папки анкеты
      language: ru                            # язык названий объектов из внешних источников
      rules: rules                            # необязательно: папка правил проекта от папки анкеты (ingest)
      registry: registry/norms.yaml           # необязательно: реестр норм (буферы) от папки анкеты
      attributes: attributes.yaml             # необязательно: атрибуты, которые вводит автор (object_id → значения)
      fetch:
        overture_release: 2026-09-23.0        # выпуск Overture Maps для fetch overture

Обязательны `name`, `crs` и в разделе `carto` — `extent`, `geo`. Остальное нужно командам, которые его читают
(`inputs` и `fetch` — fetch, `language` — fetch и сверка, `rules` — ingest, `registry` — буферы).

Система координат проекта может быть любой, в том числе локальной (инженерной) или без параметров перехода к WGS84:
ingest и render работают в ней и WGS84 не трогают. Параметры перехода (`+towgs84` или WKT BOUNDCRS в
`crs.definition`) нужны только fetch: он переводит охват в WGS84 и данные источников в систему проекта.
"""

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import geopandas as gpd
import shapely
import yaml
from pyproj import CRS
from pyproj.transformer import TransformerGroup


CARTO_FIELDS = ("extent", "extent_wgs84", "geo", "inputs", "language", "rules", "registry", "attributes", "fetch")


@dataclass(frozen=True)
class Project:
    root: Path
    data: dict

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def crs_name(self) -> str:
        return self.data["crs"]["name"]

    @property
    def crs(self) -> CRS:
        return CRS.from_user_input(self.data["crs"]["definition"])

    @property
    def carto(self) -> dict:
        """Раздел анкеты `carto`: поля картографа."""
        return self.data["carto"]

    @property
    def extent(self) -> tuple[float, float, float, float]:
        """Охват проекта в системе координат проекта."""
        xmin, ymin, xmax, ymax = self.carto["extent"]
        return float(xmin), float(ymin), float(xmax), float(ymax)

    def require_wgs84(self) -> None:
        """Fetch переводит охват в WGS84, а данные источников — в систему проекта; это возможно только по параметрам
        перехода, а не по оценке «на глаз» (ballpark). Без оценки PROJ не находит ни одной операции для локальной
        СК и для СК без параметров перехода."""
        if not TransformerGroup(self.crs, 4326, always_xy=True, allow_ballpark=False).transformers:
            raise ValueError(f"{self.crs_name}: система координат проекта не переводится в WGS84 — нужны параметры "
                             f"перехода (crs.definition: +towgs84 или WKT BOUNDCRS); без них fetch внешних источников "
                             f"невозможен, ingest и render работают в СК проекта")

    @cached_property
    def fetch_extent_wgs84(self) -> tuple[float, float, float, float]:
        """Охват выгрузок внешних источников в градусах: `extent_wgs84` анкеты, иначе `extent`, переведенный в WGS84
        (по сгущенному контуру, чтобы дуги параллелей не спрямлялись). Только для fetch."""
        self.require_wgs84()
        if "extent_wgs84" in self.carto:
            west, south, east, north = self.carto["extent_wgs84"]
            return float(west), float(south), float(east), float(north)
        xmin, ymin, xmax, ymax = self.extent
        box = shapely.segmentize(shapely.box(xmin, ymin, xmax, ymax), max(xmax - xmin, ymax - ymin) / 100)
        west, south, east, north = gpd.GeoSeries([box], crs=self.crs).to_crs(4326).total_bounds
        return round(west, 6), round(south, 6), round(east, 6), round(north, 6)

    @property
    def language(self) -> str:
        return self.carto["language"]

    @property
    def inputs(self) -> Path:
        """Папка образцов и кеша внешних источников."""
        return self.root / self.carto["inputs"]

    @property
    def overture_release(self) -> str:
        """Выпуск Overture Maps для fetch overture (`carto.fetch.overture_release`)."""
        return self.carto["fetch"]["overture_release"]

    @property
    def geo(self) -> Path:
        return self.root / self.carto["geo"]

    def part(self, name: str) -> Path:
        """Часть геопакета от одной команды (ingest, fetch, analysis); сборка проекта сливает части."""
        return self.geo / "parts" / f"{name}.gpkg"

    @property
    def rules(self) -> Path | None:
        """Папка правил проекта: `<имя>.yaml` в ней берется раньше правил модуля rules/ (ingest)."""
        return self.root / self.carto["rules"] if "rules" in self.carto else None

    @property
    def registry(self) -> Path:
        """Реестр норм проекта (записи NRM для буферов)."""
        return self.root / self.carto.get("registry", "registry/norms.yaml")

    @property
    def attributes(self) -> Path | None:
        """Атрибуты автора проекта (geopackage 1.2.0: класс опасности, категория дороги, длина водотока)."""
        return self.root / self.carto["attributes"] if "attributes" in self.carto else None

    @property
    def gpkg(self) -> Path:
        return self.geo / "project.gpkg"


def load(path: Path) -> Project:
    path = Path(path).resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("name", "crs", "carto"):
        if key not in data:
            raise ValueError(f"{path}: нет поля {key}")
    for key in CARTO_FIELDS:
        if key in data:
            raise ValueError(f"{path}: поле {key} — в разделе carto анкеты (верхний уровень — поля базы, К-045)")
    carto = data["carto"]
    for key in ("extent", "geo"):
        if key not in carto:
            raise ValueError(f"{path}: нет поля carto.{key}")
    for key in ("extent", "extent_wgs84"):
        if key in carto and not (len(carto[key]) == 4 and carto[key][0] < carto[key][2] and carto[key][1] < carto[key][3]):
            raise ValueError(f"{path}: carto.{key} — это [xmin, ymin, xmax, ymax], минимумы меньше максимумов: {carto[key]}")
    return Project(path.parent, data)
