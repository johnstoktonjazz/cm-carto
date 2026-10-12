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
    write_source(file, rules="vector", label="выгрузка третьих лиц, тест", status="reference", license="CC0 тест")
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
    write_source(file, rules="vector", label="выгрузка третьих лиц, тест", status="reference", license="CC0 тест")
    out, _ = ingest(project, file)
    assert len(gpkg.read(out)["roads"]) == 2


def test_reference_without_license_is_refused(tmp_path):
    project = project_in(tmp_path)
    file = third_party(tmp_path, project)
    write_source(file, rules="vector", label="выгрузка третьих лиц, тест", status="reference")
    with pytest.raises(ValueError, match="reference требует license"):
        ingest(project, file)


def test_layer_without_rule_is_reported(tmp_path):
    project = project_in(tmp_path)
    file = third_party(tmp_path, project)
    gpd.GeoDataFrame({"id": [1]}, geometry=[Point(lonlat(project))], crs=4326).to_file(file, layer="заметки", driver="GPKG")
    write_source(file, rules="vector", label="выгрузка третьих лиц, тест", status="reference", license="CC0 тест")
    _, report = ingest(project, file)
    assert report.unrecognized == ["слой «заметки»: нет правила раскладки"]


def test_status_comes_from_passport_not_rules(tmp_path):
    """Оцифровка — digitized с digitized_from из паспорта; те же правила vector, статуса в них нет."""
    project = project_in(tmp_path)
    file = third_party(tmp_path, project)
    write_source(file, rules="vector", label="оцифровка слайда, тест", status="digitized",
                 digitized_from="альбом концепции ред. 5, слайд 12; картограф, 09.10.2026")
    out, report = ingest(project, file)
    roads = gpkg.read(out)["roads"]
    assert set(roads.status) == {"digitized"} and set(roads.digitized_from) == {"альбом концепции ред. 5, слайд 12; картограф, 09.10.2026"}
    assert "Статус: digitized (паспорт источника)" in report.header
    assert gpkg.check(out) == []


def test_digitized_without_origin_is_refused(tmp_path):
    project = project_in(tmp_path)
    file = third_party(tmp_path, project)
    write_source(file, rules="vector", label="оцифровка слайда, тест", status="digitized")
    with pytest.raises(ValueError, match="digitized требует digitized_from"):
        ingest(project, file)


def test_vector_passport_without_status_is_refused(tmp_path):
    project = project_in(tmp_path)
    file = third_party(tmp_path, project)
    write_source(file, rules="vector", label="выгрузка третьих лиц, тест", status=None, license="CC0 тест")
    with pytest.raises(ValueError, match="нет полей \\['status'\\]"):
        ingest(project, file)


def test_wrong_rules_type_for_file(tmp_path):
    project = project_in(tmp_path)
    file = third_party(tmp_path, project)
    write_source(file, rules="ppt", label="тест")
    with pytest.raises(ValueError, match="нужны vector"):
        ingest(project, file)
