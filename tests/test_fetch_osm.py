"""fetch osm: раскладка маленького синтетического .osm (osmnx читает XML); на живых данных не проверялась."""

import pytest
from fetch_fixtures import osm_xml, project_file

from cm_carto import gpkg, project
from cm_carto.fetch import osm


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "data.osm").write_text(osm_xml(), encoding="utf-8")
    return project.load(project_file(tmp_path))


def test_layers_from_xml(proj, tmp_path):
    osm.download(proj, tmp_path / "data.osm", "2026-10-01")
    frames = osm.layout(proj)
    roads = frames["roads"].set_index("object_id")
    assert roads.loc["roads:osm-w1", "road_class"] == "main" and roads.loc["roads:osm-w1", "name"] == "Первая улица"
    assert set(roads["road_class"]) == {"main", "local", "district", "service"}
    assert roads["tunnel"].sum() == 1
    rail = frames["rail"].set_index("object_id")
    assert sorted(rail["rail_class"]) == ["main", "siding"]
    assert list(frames["transit_stops"]["mode"]) == ["bus"]
    buildings = frames["buildings_osm"].set_index("object_id")
    assert sorted(buildings["building_use"]) == ["education", "residential"]
    assert buildings["floors"].dropna().tolist() == [5]
    assert list(frames["green"]["green_class"]) == ["park"]
    assert list(frames["poi_education"]["poi_type"]) == ["school"]
    assert list(frames["poi_health"]["poi_type"]) == ["hospital"]
    assert frames["roads"]["source_label"].iloc[0] == "OpenStreetMap, выгрузка 2026-10-01"
    assert set(frames["roads"]["source_license"]) == {"ODbL 1.0"}


def test_sea_is_water_right_of_coastline(proj, tmp_path):
    """Береговая линия идет на запад: суша слева (юг), вода справа (север) — море есть, оно севернее линии."""
    osm.download(proj, tmp_path / "data.osm", "2026-10-01")
    water = osm.layout(proj)["water"].set_index("water_class")
    sea, area = water.loc["sea", "geometry"], osm.common.extent(proj)
    assert 0 < sea.area < area.area / 5
    assert sea.bounds[1] > area.bounds[1] + (area.bounds[3] - area.bounds[1]) * 0.9
    assert "lake" in water.index


def test_central_block(proj, tmp_path):
    osm.download(proj, tmp_path / "data.osm", "2026-10-01")
    assert len(osm.layout(proj)["blocks"]) == 1


def test_part_passes_contract_and_is_reproducible(proj, tmp_path):
    first = osm.run(proj, tmp_path / "data.osm", "2026-10-01").read_bytes()
    assert gpkg.check(proj.part("fetch-osm")) == []
    assert osm.run(proj).read_bytes() == first


def test_file_needs_date(proj, tmp_path):
    with pytest.raises(ValueError, match="--date"):
        osm.download(proj, tmp_path / "data.osm", None)


def test_name_in_project_language(proj, tmp_path):
    osm.download(proj, tmp_path / "data.osm", "2026-10-01")
    # в данных основное название на другом языке, `name:ru` есть — берется оно; без него остается `name`
    assert list(osm.layout(proj)["transit_stops"]["name"]) == ["Первая улица"]
    assert osm.layout(proj)["roads"].set_index("object_id").loc["roads:osm-w1", "name"] == "Первая улица"
