"""Общее для тестов буферов и подсчетов: синтетический реестр норм и слои во временном проекте. Тестов здесь нет."""

import shutil
from pathlib import Path

import geopandas as gpd
import yaml
from analysis_fixtures import SOURCE, X0, Y0, project_in
from shapely.geometry import MultiPolygon, Point, box

from cm_carto import gpkg

REGISTRY = Path(__file__).resolve().parent.parent / "examples" / "taganrog" / "registry" / "norms.yaml"
CADASTRE = {"status": "cadastral", "source_label": "тестовая КПТ", "source_date": "2026-10-11", "fact_ids": []}


def rect(x0: float, y0: float, x1: float, y1: float) -> MultiPolygon:
    """Прямоугольник в метрах от начала решетки."""
    return MultiPolygon([box(X0 + x0, Y0 + y0, X0 + x1, Y0 + y1)])


def project_with_registry(tmp_path):
    project = project_in(tmp_path)
    (tmp_path / "registry").mkdir()
    shutil.copy(REGISTRY, tmp_path / "registry" / "norms.yaml")
    return project


def edit_registry(project, norm_id: str, value=None, drop: bool = False):
    path = project.root / "registry" / "norms.yaml"
    records = yaml.safe_load(path.read_text(encoding="utf-8"))
    records = [r for r in records if not (drop and r["id"] == norm_id)]
    for r in records:
        if r["id"] == norm_id and value is not None:
            r["value"] = value
    path.write_text(yaml.safe_dump(records, allow_unicode=True, sort_keys=False), encoding="utf-8")


def parcel(key: str, geometry, use: str = "Склады") -> dict:
    return {"object_id": f"parcels:{key}", "kind": "земельный участок", "permitted_use": use, **CADASTRE,
            "geometry": geometry}


def water(key: str, geometry, water_class: str) -> dict:
    return {"object_id": f"water:{key}", "kind": "водный объект", "water_class": water_class, **SOURCE,
            "geometry": geometry}


def poi(key: str, x: float, y: float, kind: str = "аптека") -> dict:
    return {"object_id": f"poi_other:{key}", "kind": kind, "poi_type": "pharmacy", **SOURCE,
            "geometry": Point(X0 + x, Y0 + y)}


def write_layers(project, **layers):
    """Часть fetch-test со слоями `имя=[строки]`."""
    frames = {name: gpd.GeoDataFrame(rows, geometry="geometry", crs=project.crs) for name, rows in layers.items()}
    gpkg.write(project.part("fetch-test"), frames, project.name, project.crs, project.crs_name, "sha256:" + "0" * 64)
