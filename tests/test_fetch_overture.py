"""fetch overture: правила раскладки классов, разбор record_id, повторная раскладка из кеша байт в байт."""

import pytest
from fetch_fixtures import RELEASE, overture_cache, project_file

from cm_carto import gpkg, project
from cm_carto.fetch import common, overture


@pytest.fixture
def cached(tmp_path):
    proj = project.load(project_file(tmp_path, fetch={"overture_release": RELEASE}))
    overture_cache(proj)
    return proj


@pytest.mark.parametrize("record_id, key", [("w123@4", "osm-w123"), ("n7", "osm-n7"), ("r9@12", "osm-r9")])
def test_osm_record_id(record_id, key):
    assert common.osm_key(record_id) == key


def test_bad_record_id_is_error():
    with pytest.raises(ValueError, match="не идентификатор OSM"):
        common.osm_key("not-osm")


def test_release_from_argument_or_project(cached):
    assert overture.release_of(cached, None) == RELEASE
    assert overture.release_of(cached, "2027-01-01.0") == "2027-01-01.0"
    assert overture.release_date(RELEASE) == "2026-09-23"


def test_roads_split_by_osm_element_and_tunnel(cached):
    roads = overture.layout(cached, RELEASE)["roads"].set_index("object_id")
    # сегмент Overture из двух элементов OSM режется по ним; участок под землей — отдельная запись с суффиксом
    assert set(roads.index) == {"roads:osm-w1", "roads:osm-w2", "roads:osm-w2-t", "roads:osm-w3", "roads:osm-w4",
                                "roads:osm-w5", "roads:osm-w6", "roads:ovt-seg-6"}
    assert roads.loc["roads:osm-w1", "road_class"] == "main"
    assert not roads.loc["roads:osm-w2", "tunnel"] and roads.loc["roads:osm-w2-t", "tunnel"]
    assert roads.loc["roads:osm-w3", "road_class"] == "local" and roads.loc["roads:osm-w4", "road_class"] == "district"
    assert roads.loc["roads:osm-w6", "road_class"] == "service" and roads.loc["roads:ovt-seg-6", "road_class"] == "path"
    assert roads.loc["roads:osm-w1", "source_label"] == f"OpenStreetMap через Overture Maps {RELEASE}"
    assert roads.loc["roads:ovt-seg-6", "source_label"] == f"Esri Community Maps через Overture Maps {RELEASE}"
    assert roads.loc["roads:ovt-seg-6", "source_license"] == "CC-BY-4.0"
    assert set(roads["source_date"]) == {"2026-09-23"} and set(roads["status"]) == {"reference"}


def test_unknown_road_class_is_error(cached, monkeypatch):
    original = overture.read

    def read(folder, source, proj):
        frame = original(folder, source, proj)
        if source == "transportation/segment":
            frame.loc[0, "class_"] = "bobsled"
        return frame

    monkeypatch.setattr(overture, "read", read)
    with pytest.raises(ValueError, match="bobsled"):
        overture.layout(cached, RELEASE)


def test_rail_classes(cached):
    rail = overture.layout(cached, RELEASE)["rail"].set_index("object_id")
    assert rail.loc["rail:osm-w7", "rail_class"] == "main" and rail.loc["rail:osm-w8", "rail_class"] == "tram"


def test_sea_polygons_do_not_overlap_and_pool_is_not_water(cached):
    water = overture.layout(cached, RELEASE)["water"]
    assert sorted(water["water_class"]) == ["lake", "sea", "sea"]
    sea = water[water["water_class"] == "sea"].geometry
    assert sea.iloc[0].intersection(sea.iloc[1]).area < 1e-6


def test_stops_buildings_green(cached):
    frames = overture.layout(cached, RELEASE)
    assert list(frames["transit_stops"]["mode"]) == ["bus"]
    buildings = frames["buildings_osm"].set_index("object_id")
    assert buildings.loc["buildings_osm:osm-w80", "floors"] == 5
    assert buildings.loc["buildings_osm:osm-w80", "building_use"] == "residential"
    assert any(i.startswith("buildings_osm:ovt-") for i in buildings.index)
    assert sorted(frames["green"]["green_class"]) == ["forest", "park"]


def test_poi_places_and_land_use(cached):
    frames = overture.layout(cached, RELEASE)
    school = frames["poi_education"]
    # точка Places внутри площадного объекта land_use того же слоя — то же учреждение: остается объект OSM
    assert list(school["object_id"]) == ["poi_education:osm-w60"] and school["poi_type"].iloc[0] == "school"
    health = frames["poi_health"]
    # уверенность ниже порога, закрытое и неописанное правилами не берутся
    assert list(health["object_id"]) == ["poi_health:ovt-pl-2"]
    assert health["source_label"].iloc[0] == f"Overture Maps Places {RELEASE}"
    assert health["source_license"].iloc[0] == "CDLA-Permissive-2.0"
    assert len(frames["poi_other"]) == 0


def test_blocks_between_streets(cached):
    blocks = overture.layout(cached, RELEASE)["blocks"]
    # центральный квартал между четырьмя улицами; остальные подходят к границе охвата и отбрасываются
    assert len(blocks) == 1 and blocks["object_id"].iloc[0].startswith("blocks:g")
    assert blocks["kind"].iloc[0] == "квартал по улично-дорожной сети"


def test_part_passes_contract_and_rebuilds_byte_for_byte(cached):
    first = overture.run(cached).read_bytes()
    assert gpkg.check(cached.part("fetch-overture")) == []
    assert overture.run(cached).read_bytes() == first


def test_cache_present_does_not_touch_network(cached, monkeypatch):
    def fail(*args):
        raise AssertionError("сеть при готовом кеше")

    monkeypatch.setattr(overture, "_select", fail)
    assert overture.download(cached, RELEASE) == overture.cache_dir(cached, RELEASE)


def test_cache_for_other_extent_is_error(cached):
    other = project.load(project_file(cached.root, extent_wgs84=[38.0, 47.0, 38.5, 47.5]))
    with pytest.raises(ValueError, match="кеш снят на охват"):
        overture.download(other, RELEASE)


def test_names_in_project_language_and_composite_count():
    names = common.Names("ru")
    both = {"primary": "Таганрогский залив / Таганрозька затока", "common": [("uk", "Таганрозька затока"), ("ru", "Таганрогский залив")]}
    assert names.overture(both) == "Таганрогский залив" and names.composite == 0
    # нет названия на языке проекта: основное остается как есть, составное считается
    assert names.overture({"primary": "Азовське море - Азовское море", "common": [("uk", "Азовське море")]}) \
        == "Азовське море - Азовское море"
    assert names.overture({"primary": "Школа 5", "common": None}) == "Школа 5" and names.composite == 1
    assert names.overture(None) is None
    assert names.osm({"name": "A / Б", "name:ru": float("nan")}) == "A / Б" and names.composite == 2
    assert names.osm({"name": "Б", "name:ru": "В"}) == "В"


def test_block_is_whole_cell_between_street_strips():
    # сетка улиц 200 м: квартал в середине — вся ячейка за вычетом полос улиц, без потерь внутри (проверка по геометрии)
    import geopandas as gpd
    import shapely

    half = common.rules()["blocks"]["half_width_m"]
    xs = [0, 200, 400, 600]
    lines = [shapely.LineString([(x, -100), (x, 700)]) for x in xs] + [shapely.LineString([(-100, y), (700, y)]) for y in xs]
    streets = gpd.GeoDataFrame({"road_class": ["local", "main"] * 4}, geometry=lines, crs="EPSG:32637")
    area = shapely.box(-300, -300, 900, 900)
    found = common.blocks(streets, None, area, "метка", "2026-01-01", "лицензия")
    assert len(found) == 9
    # ячейка 200..400: слева улица main (x=200), справа local (x=400); то же по y
    cell = found[found.contains(shapely.Point(300, 300))].geometry.iloc[0]
    expected = (200 - half["main"] - half["local"]) ** 2
    assert abs(cell.area - expected) < 1e-6
