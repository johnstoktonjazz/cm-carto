"""Командная строка: cm-carto ingest | fetch | render | isochrone | measure | doctor."""

import argparse
import sys
from pathlib import Path


def doctor(args) -> int:
    from cm_carto import doctor as check

    errors = check.run(args.out)
    for error in errors:
        print(f"ошибка: {error}")
    print("окружение годно" if not errors else f"ошибок: {len(errors)}")
    return 1 if errors else 0


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="cm-carto", description="картограф инструмента CM")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("doctor", help="проверка окружения: QGIS без окна, шрифты, контракты, пробный рендер")
    p.add_argument("-o", "--out", type=Path, help="папка для пробной карты")
    p.set_defaults(func=doctor)
    from cm_carto.ingest import cli as ingest_cli
    from cm_carto.render import cli as render_cli

    ingest_cli.add_parser(commands)
    render_cli.add_parser(commands)
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
