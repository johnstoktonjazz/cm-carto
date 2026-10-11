"""Команда cm-carto render <описание.yaml> [...]: карта PNG в размер места, легенда SVG, *.provenance.json."""

from pathlib import Path


def run(args) -> int:
    from cm_carto.render import build

    for spec in args.specs:
        for path in build.render(spec):
            print(f"{spec.name}: {path}")
    return 0


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("render", help="карта по описанию: PNG в размер места, легенда, provenance")
    p.add_argument("specs", nargs="+", type=Path, help="описания карт .yaml (schema/map.schema.json)")
    p.set_defaults(func=run)
