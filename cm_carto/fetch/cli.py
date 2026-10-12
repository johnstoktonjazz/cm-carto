"""Команда cm-carto fetch <project.yaml> <источник>: внешние источники -> часть геопакета и кеш."""

from pathlib import Path

from cm_carto import project as project_module
from cm_carto.fetch import common, osm, overture, reconcile, registries

SOURCES = {
    "overture": "Overture Maps (OSM и Places) по охвату: кеш, слои, часть fetch-overture.gpkg",
    "osm": "прямая выгрузка OSM (Overpass или --file .osm): кеш, слои, часть fetch-osm.gpkg",
    "okn": "реестр ОКН (--file, --date): часть fetch-okn.gpkg, слой heritage",
    "dtp": "ДТП ГИБДД (--file, --date): часть fetch-dtp.gpkg, слой accidents",
    "bus_gov": "учреждения bus.gov.ru (--file, --date): в кеш; в геопакет — командой reconcile",
    "hotels": "реестр средств размещения (--file, --date): в кеш; в геопакет — командой reconcile",
    "rosstat": "показатели Росстата (--file, --date): таблица geo/rosstat.csv",
    "reconcile": "сверка учреждений базы (--base overture|osm) с реестрами bus_gov и hotels, перезапись части",
}


def registry(args, project) -> Path:
    registries.download(project, args.source, args.file, args.date)
    if args.source == "okn":
        return common.write_part(project, "fetch-okn", {"heritage": registries.heritage(project)},
                                 registries.cache_dir(project, "okn") / "data.csv")
    if args.source == "dtp":
        return common.write_part(project, "fetch-dtp", {"accidents": registries.accidents(project)},
                                 registries.cache_dir(project, "dtp") / "data.csv")
    if args.source == "rosstat":
        path = project.geo / "rosstat.csv"
        registries.rosstat(project).to_csv(path, index=False)
        return path
    return registries.cache_dir(project, args.source)


def run(args) -> int:
    project = project_module.load(args.project)
    project.require_wgs84()
    if args.source == "overture":
        path = overture.run(project, args.release)
    elif args.source == "osm":
        path = osm.run(project, args.file, args.date)
    elif args.source == "reconcile":
        path = reconcile.run(project, args.base)
    else:
        path = registry(args, project)
    print(path)
    return 0


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("fetch", help="внешние источники: Overture, OSM, реестры, сверка")
    p.add_argument("project", type=Path, help="project.yaml")
    p.add_argument("source", choices=list(SOURCES), help="; ".join(f"{k} — {v}" for k, v in SOURCES.items()))
    p.add_argument("--release", help="выпуск Overture (иначе carto.fetch.overture_release из project.yaml)")
    p.add_argument("--file", type=Path, help="файл выгрузки (osm: .osm; реестры: .csv)")
    p.add_argument("--date", help="дата выгрузки файла, ГГГГ-ММ-ДД")
    p.add_argument("--base", choices=["overture", "osm"], default="overture", help="для reconcile: чьи слои сверять")
    p.set_defaults(func=run)
