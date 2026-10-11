"""Анкета проекта project.yaml: все проектное — отсюда, в коде проектных значений нет.

Часть картографа:

    name: <имя проекта>
    crs:
      name: EPSG:32637            # как подписывать; EPSG-код или МСК с зоной
      definition: EPSG:32637      # что понимает PROJ: EPSG-код, строка PROJ или WKT (для МСК — параметры)
    extent_wgs84: [xmin, ymin, xmax, ymax]   # охват выгрузок внешних источников, градусы
    geo: 00_База_знаний/geo                   # папка геоданных проекта от папки анкеты
"""

from dataclasses import dataclass
from pathlib import Path

import yaml
from pyproj import CRS


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
    def extent_wgs84(self) -> tuple[float, float, float, float]:
        xmin, ymin, xmax, ymax = self.data["extent_wgs84"]
        return float(xmin), float(ymin), float(xmax), float(ymax)

    @property
    def geo(self) -> Path:
        return self.root / self.data["geo"]

    def part(self, name: str) -> Path:
        """Часть геопакета от одной команды (ingest, fetch, analysis); сборка проекта сливает части."""
        return self.geo / "parts" / f"{name}.gpkg"

    @property
    def gpkg(self) -> Path:
        return self.geo / "project.gpkg"


def load(path: Path) -> Project:
    path = Path(path).resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("name", "crs", "extent_wgs84", "geo"):
        if key not in data:
            raise ValueError(f"{path}: нет поля {key}")
    return Project(path.parent, data)
