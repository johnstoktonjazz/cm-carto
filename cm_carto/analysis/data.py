"""Чтение слоев геопакета проекта для анализа: части `geo/parts/*.gpkg`, а если частей нет — `geo/project.gpkg`.
Атрибуты автора (carto.attributes) накладываются на объекты при чтении, как при сборке геопакета проекта."""

import geopandas as gpd
import pandas as pd
import pyogrio

from cm_carto import gpkg
from cm_carto.project import Project


def files(project: Project) -> list:
    parts = sorted((project.geo / "parts").glob("*.gpkg"))
    return parts or [project.gpkg]


def locate(project: Project, layer: str) -> list:
    """Файлы, в которых есть слой."""
    return [f for f in files(project) if layer in set(pyogrio.list_layers(f)[:, 0])]


def layer(project: Project, name: str, where: str | None = None, bbox=None) -> gpd.GeoDataFrame:
    """Слой со всех файлов, где он есть (where — фильтр OGR по атрибутам, bbox — рамка); повтор object_id — ошибка."""
    found = locate(project, name)
    if not found:
        raise ValueError(f"слоя {name} нет в {[str(f) for f in files(project)]}")
    frame = pd.concat([pyogrio.read_dataframe(f, layer=name, where=where, bbox=bbox) for f in found], ignore_index=True)
    repeated = sorted(set(frame["object_id"][frame["object_id"].duplicated()]))
    if repeated:
        raise ValueError(f"слой {name}: object_id повторяется в частях: {repeated[:5]}")
    frame = gpd.GeoDataFrame(frame, geometry="geometry", crs=pyogrio.read_info(found[0], layer=name)["crs"])
    return gpkg.with_attributes(name, frame, gpkg.author_attributes(project.attributes))


def one(project: Project, object_id: str) -> gpd.GeoSeries:
    """Объект по object_id: слой — до двоеточия."""
    frame = layer(project, object_id.split(":")[0], where=f"object_id = '{object_id}'")
    if len(frame) != 1:
        raise ValueError(f"объект {object_id} не найден в частях {[f.name for f in files(project)]}")
    return frame.iloc[0]
