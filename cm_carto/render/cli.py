"""Команда cm-carto render <описание.yaml> [...] [--preview]: подложка PNG и provenance, overlay.json, превью."""

from pathlib import Path


def run(args) -> int:
    from cm_carto.render import build

    for spec in args.specs:
        for path in build.render(spec, args.preview):
            print(f"{spec.name}: {path}")
    return 0


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("render", help="карта по описанию: подложка PNG в размер места, provenance, overlay.json")
    p.add_argument("specs", nargs="+", type=Path, help="описания карт .yaml (schema/map.schema.json)")
    p.add_argument("--preview", action="store_true",
                   help="еще <out>.preview.png: overlay поверх подложки для проверки глазами, не выпуск")
    p.set_defaults(func=run)
