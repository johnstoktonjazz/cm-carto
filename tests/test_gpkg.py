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
    out = gpkg.merge([a, b], tmp_path / "p.gpkg", doc["project"], CRS.from_user_input(doc["crs"]), doc["crs"])
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
