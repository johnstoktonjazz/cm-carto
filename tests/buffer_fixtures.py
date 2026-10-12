"""Общее для тестов буферов и подсчетов: синтетические реестры норм и допущений и слои во временном проекте. Тестов здесь нет."""

import shutil
from pathlib import Path

import geopandas as gpd
import yaml
from analysis_fixtures import SOURCE, X0, Y0, project_in, street
from shapely.geometry import MultiPolygon, Point, box

from cm_carto import gpkg

REGISTRIES = Path(__file__).resolve().parent.parent / "examples" / "taganrog" / "registry"
REGISTRY = REGISTRIES / "norms.yaml"
ASSUMPTIONS = REGISTRIES / "assumptions.yaml"
CADASTRE = {"status": "cadastral", "source_label": "тестовая КПТ", "source_date": "2026-10-11", "fact_ids": []}


def rect(x0: float, y0: float, x1: float, y1: float) -> MultiPolygon:
    """Прямоугольник в метрах от начала решетки."""
    return MultiPolygon([box(X0 + x0, Y0 + y0, X0 + x1, Y0 + y1)])


def project_with_registry(tmp_path):
    project = project_in(tmp_path)
    (tmp_path / "registry").mkdir()
    shutil.copy(REGISTRY, tmp_path / "registry" / "norms.yaml")
    shutil.copy(ASSUMPTIONS, tmp_path / "registry" / "assumptions.yaml")
    return project


def edit_registry(project, norm_id: str, value=None, drop: bool = False, applicability: str | None = None,
                  file: str = "norms.yaml"):
    """Правка записи реестра проекта: значение, удаление, статус применимости (`applicability.status`)."""
    path = project.root / "registry" / file
    records = yaml.safe_load(path.read_text(encoding="utf-8"))
    records = [r for r in records if not (drop and r["id"] == norm_id)]
    for r in records:
        if r["id"] == norm_id and value is not None:
            r["value"] = value
        if r["id"] == norm_id and applicability is not None:
            r["applicability"]["status"] = applicability
    path.write_text(yaml.safe_dump(records, allow_unicode=True, sort_keys=False), encoding="utf-8")


def parcel(key: str, geometry, use: str = "Склады", hazard_class: str | None = None) -> dict:
    return {"object_id": f"parcels:{key}", "kind": "земельный участок", "permitted_use": use,
            **({"hazard_class": hazard_class} if hazard_class else {}), **CADASTRE, "geometry": geometry}


def water(key: str, geometry, water_class: str, length_km: float | None = None) -> dict:
    return {"object_id": f"water:{key}", "kind": "водный объект", "water_class": water_class,
            **({"length_km": length_km} if length_km is not None else {}), **SOURCE, "geometry": geometry}


def poi(key: str, x: float, y: float, kind: str = "аптека", hazard_class: str | None = None) -> dict:
    return {"object_id": f"poi_other:{key}", "kind": kind, "poi_type": "pharmacy",
            **({"hazard_class": hazard_class} if hazard_class else {}), **SOURCE, "geometry": Point(X0 + x, Y0 + y)}


def road(key: str, y: float, road_class: str, road_category: str | None = None) -> dict:
    """Улица вдоль оси x на высоте y от начала решетки; категорию дороги вводит автор."""
    return {**street(key, [(X0, Y0 + y), (X0 + 1000, Y0 + y)], road_class),
            **({"road_category": road_category} if road_category else {})}


def write_layers(project, **layers):
    """Часть fetch-test со слоями `имя=[строки]`."""
    frames = {name: gpd.GeoDataFrame(rows, geometry="geometry", crs=project.crs) for name, rows in layers.items()}
    gpkg.write(project.part("fetch-test"), frames, project.name, project.crs, project.crs_name, "sha256:" + "0" * 64)
