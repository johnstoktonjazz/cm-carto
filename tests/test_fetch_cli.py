"""Команда fetch: разбор аргументов и сквозной прогон на синтетическом кеше (Overture + bus.gov.ru + сверка)."""

import argparse

import yaml
from fetch_fixtures import RELEASE, overture_cache, project_file

from cm_carto import gpkg, project
from cm_carto.fetch import cli


def parse(*argv):
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(argv)


def test_parser():
    args = parse("fetch", "p.yaml", "reconcile", "--base", "osm")
    assert (args.command, args.source, args.base, args.func) == ("fetch", "reconcile", "osm", cli.run)


def test_overture_then_reconcile_with_bus_gov(tmp_path):
    path = project_file(tmp_path, fetch={"overture_release": RELEASE})
    overture_cache(project.load(path))
    registry = tmp_path / "bus.csv"
    registry.write_text("Идентификатор;Наименование;Тип учреждения;Адрес;Вместимость;Долгота;Широта\n"
                        "10;МБОУ СОШ №5;общеобразовательное;ул. Первая, 1;800;;\n"
                        "11;МБОУ СОШ №9;общеобразовательное;ул. Вторая, 2;300;;\n", encoding="utf-8")
    assert cli.run(parse("fetch", str(path), "overture")) == 0
    assert cli.run(parse("fetch", str(path), "bus_gov", "--file", str(registry), "--date", "2026-10-01")) == 0
    assert cli.run(parse("fetch", str(path), "reconcile")) == 0
    proj = project.load(path)
    part = proj.part("fetch-overture")
    assert gpkg.check(part) == []
    school = gpkg.read(part)["poi_education"]
    assert list(school["object_id"]) == ["poi_education:busgov-10"]
    assert school["confirmed_by"].iloc[0] == ["osm-w60", "busgov-10"] and school["capacity"].iloc[0] == 800
    report = yaml.safe_load(part.with_suffix(".reconcile.yaml").read_text(encoding="utf-8"))
    assert report["matched"] == 1 and [u["key"] for u in report["unplaced"]] == ["busgov-11"]
    first = part.read_bytes()
    cli.run(parse("fetch", str(path), "reconcile"))      # повторная сверка из того же кеша — тот же файл
    assert part.read_bytes() == first
