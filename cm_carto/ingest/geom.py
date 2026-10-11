"""Общая геометрия импорта: починка полигонов, проверка охвата."""

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, box

from cm_carto.project import Project


def polygons_of(geom) -> list[Polygon]:
    if geom.geom_type == "Polygon":
        return [geom]
    return [p for part in getattr(geom, "geoms", []) for p in polygons_of(part)]


def fix_polygon(poly: Polygon | MultiPolygon) -> tuple[Polygon | MultiPolygon, bool]:
    """Самопересечения и прочая невалидность → make_valid, только площади. Второй результат: чинили ли."""
    if poly.is_valid:
        return poly, False
    parts = polygons_of(make_valid(poly))
    return (parts[0] if len(parts) == 1 else MultiPolygon(parts)), True


def check_extent(project: Project, frames: dict[str, gpd.GeoDataFrame]) -> None:
    """Все объекты должны лежать в охвате проекта: иначе чертеж или выписка не в проектной системе координат."""
    area = gpd.GeoSeries([box(*project.extent_wgs84)], crs=4326).to_crs(project.crs).iloc[0]
    outside = [f"{layer}:{oid}" for layer, frame in frames.items()
               for oid, geom in zip(frame["object_id"], frame.geometry) if not area.intersects(geom)]
    if outside:
        raise ValueError(f"объектов вне охвата проекта (extent_wgs84): {len(outside)}, например {outside[:3]}; "
                         f"источник не в системе координат проекта {project.crs_name} или в других единицах")
