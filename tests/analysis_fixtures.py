"""Общее для тестов анализа: решетка улиц во временном проекте. Тестов здесь нет."""

import argparse
import copy
import shutil
from pathlib import Path

import geopandas as gpd
from shapely.geometry import MultiLineString, MultiPolygon, Point, box

from cm_carto import gpkg, project as project_mod
from cm_carto.analysis import cli, params

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "taganrog" / "project.yaml"
X0, Y0, STEP = 500000.0, 5000000.0, 100.0
STOP = "transit_stops:osm-n1"
SOURCE = {"status": "reference", "source_label": "тестовые улицы", "source_date": "2026-10-11",
          "source_license": "ODbL 1.0", "fact_ids": []}


def project_in(tmp_path: Path) -> project_mod.Project:
    """Анкета примера во временной папке: `tmp_path/project.yaml` и атрибуты автора рядом, геопакет — `tmp_path/geo`."""
    shutil.copy(EXAMPLE, tmp_path / "project.yaml")
    shutil.copy(EXAMPLE.parent / "attributes.yaml", tmp_path / "attributes.yaml")
    return project_mod.load(tmp_path / "project.yaml")


def modes(walk: bool = False, drive: bool = True) -> dict:
    """Параметры анализа с явно заданным `directed` режимов: тесты не зависят от значений в rules/analysis.yaml."""
    changed = copy.deepcopy(params.load())
    changed["modes"]["walk"]["directed"], changed["modes"]["drive"]["directed"] = walk, drive
    return changed


def street(key: str, coords: list[tuple[float, float]], road_class: str = "local", **attrs) -> dict:
    """Улица слоя roads; attrs — oneway, bridge, tunnel (по умолчанию tunnel = False, остальных нет, как у источника без них)."""
    return {"object_id": f"roads:{key}", "kind": "улица", "road_class": road_class, "tunnel": False, **SOURCE,
            **attrs, "geometry": MultiLineString([coords])}


def lattice(columns: range, rows: range, road_class: str = "local", prefix: str = "") -> list[dict]:
    """Улицы по линиям решетки, вершины — во всех перекрестках, как у элементов OSM."""
    x = lambda i: X0 + i * STEP
    y = lambda j: Y0 + j * STEP
    out = [street(f"{prefix}h{j}", [(x(i), y(j)) for i in columns], road_class) for j in rows]
    return out + [street(f"{prefix}v{i}", [(x(i), y(j)) for j in rows], road_class) for i in columns]


def stop(key: str, i: float, j: float) -> dict:
    return {"object_id": f"transit_stops:{key}", "kind": "остановка", "mode": "bus", **SOURCE,
            "geometry": Point(X0 + i * STEP, Y0 + j * STEP)}


def write_world(project: project_mod.Project, roads: list[dict], stops: list[dict] = (), contours: list[dict] = ()):
    """Часть геопакета fetch-test: улицы, остановки и контуры (слой boundary_planning)."""
    frames = {"roads": gpd.GeoDataFrame(roads, geometry="geometry", crs=project.crs)}
    if stops:
        frames["transit_stops"] = gpd.GeoDataFrame(list(stops), geometry="geometry", crs=project.crs)
    if contours:
        frames["boundary_planning"] = gpd.GeoDataFrame(
            [{"object_id": f"boundary_planning:{k}", "kind": "граница", **SOURCE, "geometry": MultiPolygon([g])}
             for k, g in contours], geometry="geometry", crs=project.crs)
    gpkg.write(project.part("fetch-test"), frames, project.name, project.crs, project.crs_name, "sha256:" + "0" * 64)


def square(i0: float, j0: float, i1: float, j1: float):
    return box(X0 + i0 * STEP, Y0 + j0 * STEP, X0 + i1 * STEP, Y0 + j1 * STEP)


def run(command: list[str]) -> int:
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(command)
    return args.func(args)
