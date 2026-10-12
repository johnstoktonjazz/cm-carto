"""Геопакет: пример контракта записывается, проверяется, повторная запись дает тот же файл; нарушения не пишутся."""

import json

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import LineString, Point, box

from cm_carto import gpkg, paths

GEOMETRY = {"MultiPolygon": box(500000, 5000000, 500100, 5000100),
            "MultiLineString": LineString([(500000, 5000000), (500100, 5000100)]),
            "Point": Point(500050, 5000050)}


def example_frames() -> tuple[dict, dict]:
    doc = json.loads((paths.contracts() / "contracts/geopackage/example.json").read_text(encoding="utf-8"))
    crs = CRS.from_user_input(doc["crs"])
    frames = {}
    for layer in doc["layers"]:
        rows = [{**{k: v for k, v in f.items() if k != "attrs"}, **f.get("attrs", {})} for f in layer["features"]]
        frames[layer["name"]] = gpd.GeoDataFrame(rows, geometry=[GEOMETRY[layer["geometry"]]] * len(rows), crs=crs)
    return doc, frames


def write(path, frames, doc):
    return gpkg.write(path, frames, doc["project"], CRS.from_user_input(doc["crs"]), doc["crs"], doc["input_hash"])


def test_example_round_trip(tmp_path):
    doc, frames = example_frames()
    first = write(tmp_path / "a.gpkg", frames, doc)
    second = write(tmp_path / "b.gpkg", frames, doc)
    assert gpkg.check(first) == []
    assert first.read_bytes() == second.read_bytes()
    assert gpkg.read(first)["blocks"]["fact_ids"][0] == ["F-000520"]


def test_reference_without_license_is_refused(tmp_path):
    doc, frames = example_frames()
    frames["roads"] = frames["roads"].drop(columns="source_license")
    with pytest.raises(ValueError, match="source_license"):
        write(tmp_path / "x.gpkg", frames, doc)


def test_duplicate_object_id_is_refused(tmp_path):
    doc, frames = example_frames()
    frames["blocks"] = gpd.GeoDataFrame(gpd.pd.concat([frames["blocks"]] * 2), crs=frames["blocks"].crs)
    with pytest.raises(ValueError, match="повторяется"):
        write(tmp_path / "x.gpkg", frames, doc)


def test_merge_parts(tmp_path):
    doc, frames = example_frames()
    a = write(tmp_path / "a.gpkg", {k: v for k, v in frames.items() if k in ("blocks", "roads")}, doc)
    b = write(tmp_path / "b.gpkg", {k: v for k, v in frames.items() if k not in ("blocks", "roads")}, doc)
    out = gpkg.merge([a, b], tmp_path / "p.gpkg", doc["project"], CRS.from_user_input(doc["crs"]), doc["crs"], None)
    assert gpkg.check(out) == []
    assert set(gpkg.read(out)) == set(frames)


def test_custom_transverse_mercator(tmp_path):
    """Система координат проекта строкой PROJ, как у МСК субъекта (К-001): пишется и проверяется."""
    doc, frames = example_frames()
    msk = CRS.from_user_input("+proj=tmerc +lat_0=0 +lon_0=45 +k=1 +x_0=1300000 +y_0=-4500000 +ellps=krass "
                              "+towgs84=23.57,-140.95,-79.8,0,0.35,0.79,-0.22 +units=m +no_defs")
    frames = {k: v.set_crs(msk, allow_override=True) for k, v in frames.items()}
    out = gpkg.write(tmp_path / "m.gpkg", frames, doc["project"], msk, "МСК синтетическая", doc["input_hash"])
    assert gpkg.check(out) == []
    assert CRS(gpkg.read(out)["blocks"].crs) == msk


def test_integer_attrs_with_gaps_are_written_as_integers(tmp_path):
    """floors с пропуском приходит вещественным (5.0, NaN): в файле целое и пусто (отчет fetch-dev)."""
    doc, frames = example_frames()
    poi = frames["poi_education"]
    frames["poi_education"] = gpd.GeoDataFrame(
        gpd.pd.concat([poi, poi.assign(object_id="poi_education:osm-n1", capacity=float("nan"))]), crs=poi.crs)
    frames["poi_education"]["capacity"] = frames["poi_education"]["capacity"].astype(float)
    out = write(tmp_path / "i.gpkg", frames, doc)
    info = gpkg.pyogrio.read_info(out, layer="poi_education")
    assert dict(zip(info["fields"], info["dtypes"]))["capacity"].startswith("int")


def test_merge_refuses_part_changed_while_reading(tmp_path, monkeypatch):
    """Часть переписали во время слияния — ошибка, а не геопакет с хэшем новой части (Э-014)."""
    doc, frames = example_frames()
    a = write(tmp_path / "a.gpkg", {k: v for k, v in frames.items() if k in ("blocks", "roads")}, doc)
    b = write(tmp_path / "b.gpkg", {k: v for k, v in frames.items() if k not in ("blocks", "roads")}, doc)
    real = gpkg.read

    def read_and_touch(path):
        out = real(path)
        if path == b:
            b.write_bytes(b.read_bytes() + b"\0")
        return out

    monkeypatch.setattr(gpkg, "read", read_and_touch)
    with pytest.raises(RuntimeError, match="менялись во время слияния"):
        gpkg.merge([a, b], tmp_path / "p.gpkg", doc["project"], CRS.from_user_input(doc["crs"]), doc["crs"], None)


def test_merge_applies_author_attributes(tmp_path):
    """Атрибуты автора (geopackage 1.2.0) из файла carto.attributes накладываются на объекты частей и входят в хэш."""
    doc, frames = example_frames()
    a = write(tmp_path / "a.gpkg", frames, doc)
    given = tmp_path / "attributes.yaml"
    given.write_text("roads:osm-w100200300: {road_category: II}\nparcels:00-00-000001-12: {hazard_class: IV}\n",
                     encoding="utf-8")
    crs = CRS.from_user_input(doc["crs"])
    out = gpkg.merge([a], tmp_path / "p.gpkg", doc["project"], crs, doc["crs"], given)
    merged = gpkg.read(out)
    assert merged["roads"].set_index("object_id").loc["roads:osm-w100200300", "road_category"] == "II"
    assert merged["parcels"].set_index("object_id").loc["parcels:00-00-000001-12", "hazard_class"] == "IV"
    assert gpkg.meta(out)["input_hash"] == gpkg.merge_hash([a], given) != gpkg.merge_hash([a], None)


@pytest.mark.parametrize("text, message", [
    ("roads:osm-w100200300: {road_class: main}\n", "не атрибут автора"),        # атрибут источника, не автора
    ("parcels:00-00-000001-12: {hazard_class: VI}\n", "hazard_class = 'VI'"),  # не из перечня контракта
    ("water:osm-w1: {length_km: 8}\n", "объектов нет в частях"),
])
def test_author_attributes_are_checked(tmp_path, text, message):
    doc, frames = example_frames()
    a = write(tmp_path / "a.gpkg", frames, doc)
    given = tmp_path / "attributes.yaml"
    given.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        gpkg.merge([a], tmp_path / "p.gpkg", doc["project"], CRS.from_user_input(doc["crs"]), doc["crs"], given)
