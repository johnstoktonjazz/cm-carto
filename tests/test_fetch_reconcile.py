"""Сверка учреждений: названия и расстояние, одна запись с двумя источниками, пометка «не подтверждено»."""

import pytest
import shapely
from pyproj import CRS

from cm_carto.fetch import common, reconcile

CRS_ = CRS.from_epsg(32637)
BASE_LABEL = "OpenStreetMap через Overture Maps 2026-09-23.0"


def base(items):
    rows = [{"key": key, "name": name, "kind": "школа", "label": BASE_LABEL, "date": "2026-09-23", "license": "ODbL 1.0",
             "geometry": shapely.Point(x, y), "poi_type": poi_type, "confirmed_by": []}
            for key, name, x, y, poi_type in items]
    frames = {name: common.table(name, [], CRS_) for name in
              ["poi_education", "poi_health", "poi_culture", "poi_hotels", "poi_other"]}
    frames["poi_education"] = common.table("poi_education", rows, CRS_)
    return frames


def registry(key, name, x=None, y=None, poi_type="school", capacity=None):
    return {"key": key, "name": name, "layer": "poi_education", "poi_type": poi_type, "kind": "школа",
            "address": "ул. Первая, 1", "capacity": capacity, "geometry": None if x is None else shapely.Point(x, y),
            "label": "bus.gov.ru, выгрузка 2026-10-01", "date": "2026-10-01", "license": "условия bus.gov.ru"}


@pytest.fixture(autouse=True)
def covered(monkeypatch):
    monkeypatch.setattr(reconcile.registries, "covered", lambda registry: {("poi_education", "school")})


def test_names_normalization():
    assert reconcile.tokens("МБОУ СОШ № 5 им. Чехова") == {"5", "чехова"}
    assert reconcile.tokens("Школа №5") == {"5"}
    assert reconcile.similarity("Школа №5", "МБОУ «Средняя школа» №5") == 1.0
    assert reconcile.similarity("Школа №5", "Школа №6") == 0.0
    assert reconcile.similarity("Лицей Чехова", "Лицей Пушкина") == 0.5     # ниже порога 0.6
    assert reconcile.similarity("Гимназия Чехова", "МБОУ Гимназия имени А. П. Чехова") == 1.0


def test_same_object_gets_one_record_with_both_sources():
    frames, report = reconcile.apply(base([("osm-w1", "Школа №5", 500000, 5200000, "school")]),
                                     {"bus_gov": [registry("busgov-10", "МБОУ СОШ №5", 500050, 5200000, capacity=800)]})
    school = frames["poi_education"]
    assert len(school) == 1 and report["matched"] == 1
    row = school.iloc[0]
    assert row["object_id"] == "poi_education:busgov-10" and row["confirmed_by"] == ["osm-w1", "busgov-10"]
    assert row["name"] == "МБОУ СОШ №5" and row["capacity"] == 800 and row["status"] == "reference"
    assert BASE_LABEL in row["source_label"] and "bus.gov.ru" in row["source_label"]
    assert row["source_license"] == "ODbL 1.0; условия bus.gov.ru" and row["source_date"] == "2026-10-01"
    assert row.geometry.x == 500000     # геометрия остается из OSM


def test_only_in_osm_is_marked_unconfirmed():
    frames, report = reconcile.apply(base([("osm-w1", "Школа №5", 500000, 5200000, "school")]),
                                     {"bus_gov": [registry("busgov-10", "Школа №9", 500050, 5200000)]})
    labels = dict(zip(frames["poi_education"]["object_id"], frames["poi_education"]["source_label"]))
    assert labels["poi_education:osm-w1"] == f"{BASE_LABEL} — не подтверждено реестром"
    assert labels["poi_education:busgov-10"].startswith("bus.gov.ru")      # только в реестре, с координатами
    assert report == {"matched": 0, "registry_only": 1, "unplaced": []}


def test_far_object_is_not_matched():
    frames, report = reconcile.apply(base([("osm-w1", "Школа №5", 500000, 5200000, "school")]),
                                     {"bus_gov": [registry("busgov-10", "Школа №5", 502000, 5200000)]})
    assert report["matched"] == 0 and len(frames["poi_education"]) == 2


def test_other_type_is_not_matched():
    frames, report = reconcile.apply(base([("osm-w1", "Детский сад №5", 500000, 5200000, "kindergarten")]),
                                     {"bus_gov": [registry("busgov-10", "Школа №5", 500000, 5200000)]})
    assert report["matched"] == 0


def test_no_coordinates_match_by_unique_name_only():
    items = [("osm-w1", "Школа №5", 500000, 5200000, "school"), ("osm-w2", "Школа №6", 501000, 5200000, "school")]
    frames, report = reconcile.apply(base(items), {"bus_gov": [registry("busgov-10", "МБОУ СОШ №5"),
                                                                registry("busgov-11", "МБОУ СОШ №8")]})
    assert report["matched"] == 1
    assert [u["key"] for u in report["unplaced"]] == ["busgov-11"]
    assert "poi_education:busgov-10" in set(frames["poi_education"]["object_id"])


def test_ambiguous_name_without_coordinates_is_not_matched():
    items = [("osm-w1", "Гимназия Чехова", 500000, 5200000, "school"),
             ("osm-w2", "Лицей Чехова", 501000, 5200000, "school")]
    _, report = reconcile.apply(base(items), {"bus_gov": [registry("busgov-10", "Чехова")]})
    assert report["matched"] == 0 and len(report["unplaced"]) == 1


def test_one_osm_object_serves_one_record():
    items = [("osm-w1", "Школа №5", 500000, 5200000, "school")]
    _, report = reconcile.apply(base(items), {"bus_gov": [registry("busgov-10", "Школа №5", 500000, 5200000),
                                                          registry("busgov-11", "Школа №5", 500010, 5200000)]})
    assert report["matched"] == 1 and report["registry_only"] == 1
