"""Система координат проекта без параметров перехода к WGS84 (дефект стыка №1: проект в инженерной СК, например МСК
субъекта): анкета не требует охват в WGS84, ingest и render работают в СК проекта, fetch отказывает до сети и кеша."""

import argparse
import dataclasses

import pytest
import yaml
from pyproj import CRS

import test_render_map as render_map
from cm_carto import gpkg, project as project_mod
from cm_carto.fetch import cli as fetch_cli, osm, overture
from cm_carto.render import build
from fetch_fixtures import project_file
from test_ingest_support import EXAMPLE, SAMPLES, ingest, project_in

TMERC = "+proj=tmerc +lat_0=0 +lon_0=39.5 +k=1 +x_0=530000 +y_0=0 +ellps=krass +units=m +no_defs"
TOWGS84 = " +towgs84=23.57,-140.95,-79.8,0,-0.35,-0.79,-0.22"
ENGCRS = ('ENGCRS["Местная СК",EDATUM["Местное начало"],CS[Cartesian,2],AXIS["x",east],AXIS["y",north],'
          'LENGTHUNIT["metre",1]]')
LOCAL = {"эллипсоид Красовского без параметров перехода": TMERC, "инженерная СК": ENGCRS}


def anketa(folder, definition: str, **extra) -> project_mod.Project:
    """Анкета без extent_wgs84: охват только в системе проекта."""
    data = {"name": "синтетический проект", "crs": {"name": "Местная СК", "definition": definition},
            "extent": [486360, 5226270, 498490, 5236300], "geo": "geo", "inputs": "input", "language": "ru", **extra}
    path = folder / "project.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return project_mod.load(path)


def test_anketa_without_extent_wgs84_loads(tmp_path):
    project = anketa(tmp_path, TMERC)
    assert project.extent == (486360, 5226270, 498490, 5236300)
    assert "extent_wgs84" not in project.data


def test_anketa_without_extent_is_error(tmp_path):
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({"name": "п", "crs": {"name": "п", "definition": TMERC}, "geo": "geo",
                                    "extent_wgs84": [38.0, 47.0, 38.1, 47.1]}), encoding="utf-8")
    with pytest.raises(ValueError, match="нет поля extent"):
        project_mod.load(path)


@pytest.mark.parametrize("definition", ["EPSG:32637", TMERC + TOWGS84])
def test_crs_with_transition_passes_fetch_check(tmp_path, definition):
    project = anketa(tmp_path, definition)
    project.require_wgs84()
    west, south, east, north = project.fetch_extent_wgs84
    assert west < east and south < north and 36 < west < 41 and 46 < south < 49


def test_fetch_extent_from_extent_covers_it(tmp_path):
    """Охват fetch из extent накрывает extent целиком: контур сгущен, дуга параллели не срезает угол."""
    project = anketa(tmp_path, "EPSG:32637")
    west, south, east, north = project.fetch_extent_wgs84
    assert (west, south, east, north) == pytest.approx((38.82, 47.19, 38.98, 47.28), abs=0.002)


def test_explicit_extent_wgs84_wins(tmp_path):
    project = anketa(tmp_path, "EPSG:32637", extent_wgs84=[38.0, 47.0, 38.5, 47.5])
    assert project.fetch_extent_wgs84 == (38.0, 47.0, 38.5, 47.5)


@pytest.mark.parametrize("definition", LOCAL.values(), ids=LOCAL)
def test_local_crs_refuses_wgs84(tmp_path, definition):
    project = anketa(tmp_path, definition, extent_wgs84=[38.0, 47.0, 38.5, 47.5])   # явный охват не отменяет отказ
    with pytest.raises(ValueError, match="нужны параметры перехода"):
        project.require_wgs84()
    with pytest.raises(ValueError, match="нужны параметры перехода"):
        project.fetch_extent_wgs84


@pytest.mark.parametrize("source", ["overture", "osm", "okn", "dtp", "bus_gov", "hotels", "rosstat", "reconcile"])
@pytest.mark.parametrize("definition", LOCAL.values(), ids=LOCAL)
def test_fetch_in_local_crs_fails_before_network_and_cache(tmp_path, monkeypatch, source, definition):
    """Отказ первым действием: ни выборки Overture, ни запроса Overpass, ни папки кеша."""
    def fail(*args, **kwargs):
        raise AssertionError("обращение к сети")

    monkeypatch.setattr(overture, "_select", fail)
    monkeypatch.setattr(osm.requests, "post", fail)
    anketa(tmp_path, definition, fetch={"overture_release": "2026-09-23.0"})
    registry = tmp_path / "registry.csv"
    registry.write_text("x\n", encoding="utf-8")
    parser = argparse.ArgumentParser()
    fetch_cli.add_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["fetch", str(tmp_path / "project.yaml"), source, "--file", str(registry), "--date", "2026-10-01"])
    with pytest.raises(ValueError, match="нужны параметры перехода"):
        fetch_cli.run(args)
    assert not (tmp_path / "input").exists() and not (tmp_path / "geo").exists()


def test_taganrog_cache_still_matches_example():
    """Кеш Overture полигона снят на extent_wgs84 анкеты: охват fetch равен ему, а не пересчитанному из extent."""
    project = project_mod.load(EXAMPLE)
    cached = yaml.safe_load((overture.cache_dir(project, "2026-09-23.0") / "SOURCE.yaml").read_text(encoding="utf-8"))
    assert cached["extent_wgs84"] == list(project.fetch_extent_wgs84)


@pytest.mark.parametrize("definition", LOCAL.values(), ids=LOCAL)
def test_ingest_dxf_in_local_crs(tmp_path, definition):
    example = project_in(tmp_path)
    data = {**example.data, "crs": {"name": "Местная СК", "definition": definition}}
    data.pop("extent_wgs84")
    project = dataclasses.replace(example, data=data)
    out, _ = ingest(project, SAMPLES / "dxf" / "ppt-sample.dxf")
    frames = gpkg.read(out)
    assert frames["blocks"] is not None and len(frames["blocks"]) > 0
    assert all(CRS(f.crs) == CRS.from_user_input(definition) for f in frames.values())
    assert gpkg.meta(out)["crs"] == "Местная СК" and gpkg.check(out) == []


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "CACHE", tmp_path / "cache")


@pytest.mark.parametrize("definition", LOCAL.values(), ids=LOCAL)
def test_render_in_local_crs(tmp_path, cache, definition):
    crs = CRS.from_user_input(definition)
    folder = tmp_path / "project"
    folder.mkdir()
    data = {"name": "синтетический проект", "crs": {"name": "Местная СК", "definition": definition},
            "extent": [render_map.X - 5000, render_map.Y - 5000, render_map.X + 5000, render_map.Y + 5000], "geo": "geo"}
    (folder / "project.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    frames = {name: frame.set_crs(crs, allow_override=True) for name, frame in render_map.frames().items()}
    gpkg.write(folder / "geo" / "project.gpkg", frames, "синтетический проект", crs, "Местная СК", "sha256:" + "0" * 64)
    build.render(render_map.describe(folder))
    assert (folder / "maps" / "map.png").stat().st_size > 0
    assert render_map.side(folder, "map", ".provenance.json")["layers"][0]["name"] == "Кварталы"
    overlay = render_map.side(folder, "map", ".overlay.json")
    assert overlay["map"]["crs"] == "Местная СК"
