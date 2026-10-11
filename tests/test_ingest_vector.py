"""ingest SHP и GPKG третьих лиц: слой источника → слой контракта, пересчет в проектную систему, лицензия."""

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon

from cm_carto import gpkg
from test_ingest_support import ingest, origin, project_in, write_source


def lonlat(project, dx=0.0, dy=0.0):
    """Точки в градусах: центр охвата проекта со смещением в метрах."""
    x, y = origin(project)
    return Transformer.from_crs(project.crs, 4326, always_xy=True).transform(x + dx, y + dy)


def third_party(tmp_path, project, name="roads", kind="gpkg"):
    a, b = lonlat(project), lonlat(project, 300, 100)
    frame = gpd.GeoDataFrame({"id": [101, 102], "name": ["улица Первая", None], "class": ["main", "local"]},
                             geometry=[LineString([a, b]), LineString([b, lonlat(project, 300, 400)])], crs=4326)
    file = tmp_path / f"{name}.{kind}"
    frame.to_file(file, layer=name, driver="GPKG" if kind == "gpkg" else "ESRI Shapefile")
    return file


def test_gpkg_of_third_party_is_laid_out_and_reprojected(tmp_path):
    project = project_in(tmp_path)
    file = third_party(tmp_path, project)
    write_source(file, rules="vector", label="выгрузка третьих лиц, тест", license="CC0 тест")
    out, report = ingest(project, file)
    roads = gpkg.read(out)["roads"].set_index("object_id")
    assert list(roads.index) == ["roads:101", "roads:102"]
    assert roads.loc["roads:101", "road_class"] == "main" and roads.loc["roads:101", "name"] == "улица Первая"
    assert set(roads.status) == {"reference"} and set(roads.source_license) == {"CC0 тест"}
    x, y = origin(project)
    assert roads.geometry.iloc[0].geoms[0].coords[0] == pytest.approx((x, y), abs=0.01)           # пересчитано из градусов
    assert any("EPSG:4326" in line for line in report.confirm)


def test_shapefile_is_read_by_file_name(tmp_path):
    project = project_in(tmp_path)
    file = third_party(tmp_path, project, name="roads", kind="shp")
    write_source(file, rules="vector", label="выгрузка третьих лиц, тест", license="CC0 тест")
    out, _ = ingest(project, file)
    assert len(gpkg.read(out)["roads"]) == 2


def test_reference_without_license_is_refused(tmp_path):
    project = project_in(tmp_path)
    file = third_party(tmp_path, project)
    write_source(file, rules="vector", label="выгрузка третьих лиц, тест")
    with pytest.raises(ValueError, match="source_license"):
        ingest(project, file)


def test_layer_without_rule_is_reported(tmp_path):
    project = project_in(tmp_path)
    file = third_party(tmp_path, project)
    gpd.GeoDataFrame({"id": [1]}, geometry=[Point(lonlat(project))], crs=4326).to_file(file, layer="заметки", driver="GPKG")
    write_source(file, rules="vector", label="выгрузка третьих лиц, тест", license="CC0 тест")
    _, report = ingest(project, file)
    assert report.unrecognized == ["слой «заметки»: нет правила раскладки"]


def test_wrong_rules_type_for_file(tmp_path):
    project = project_in(tmp_path)
    file = third_party(tmp_path, project)
    write_source(file, rules="ppt", label="тест")
    with pytest.raises(ValueError, match="нужны vector"):
        ingest(project, file)
