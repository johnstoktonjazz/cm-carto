"""Команды `cm-carto isochrone` и `cm-carto measure`.

isochrone <project.yaml> --from <object_id> --mode walk|drive --minutes 5 10 15
    изохроны от объекта (точка или контур) → слой isochrones части `analysis-isochrones.gpkg`;
measure <project.yaml> <object_id изохроны>
    площадь и длина достижимой сети → заготовки CALC в `analysis-isochrones.calc.yaml` рядом с частью.
"""

from pathlib import Path

from cm_carto import project as project_mod
from cm_carto.analysis import isochrone, measure


def run_isochrone(args) -> int:
    project = project_mod.load(args.project)
    rows = isochrone.rows(project, args.origin, args.mode, args.minutes)
    out = isochrone.save(project, rows)
    for row in rows:
        print(f"{row['object_id']}: {row['geometry'].area / 1e4:.1f} га, {row['speed_kmh']:g} км/ч → {out}")
    return 0


def run_measure(args) -> int:
    project = project_mod.load(args.project)
    path, drafts = measure.save(project, args.object_id)
    for d in drafts:
        print(f"{d['subject']}: {d['value']} {d['unit']} → {path}")
    return 0


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("isochrone", help="изохроны пешком и на автомобиле по графу улиц → часть геопакета")
    p.add_argument("project", type=Path, help="project.yaml проекта")
    p.add_argument("--from", dest="origin", required=True, help="object_id источника: точка или контур")
    p.add_argument("--mode", required=True, choices=["walk", "drive"])
    p.add_argument("--minutes", required=True, nargs="+", type=float, help="сроки, мин")
    p.set_defaults(func=run_isochrone)
    p = subparsers.add_parser("measure", help="площадь и длина сети изохроны → заготовки CALC")
    p.add_argument("project", type=Path, help="project.yaml проекта")
    p.add_argument("object_id", help="object_id изохроны")
    p.set_defaults(func=run_measure)
