"""Команда `cm-carto ingest <project.yaml> <файл или папка>`: часть геопакета и отчет импорта на каждый вход.

Вход — DXF (паспорт `<файл>.source.yaml` с правилами, подписью и датой), выписка КПТ (XML), SHP или GPKG третьих
лиц (паспорт обязателен). Папка — файлы в ней без вложенных папок. Результат — `<geo>/parts/ingest-<имя>.gpkg` и
`ingest-<имя>.report.md`.
"""

from pathlib import Path

from cm_carto import project as project_mod
from cm_carto.ingest import common, dxf, egrn, report as report_mod, rules as rules_mod, vector

SUFFIXES = {".dxf", ".xml", ".shp", ".gpkg"}
RULES_TYPE = {".dxf": "dxf", ".shp": "vector", ".gpkg": "vector"}


def ingest_file(project: project_mod.Project, file: Path) -> tuple[Path, report_mod.Report]:
    suffix = file.suffix.lower()
    report = report_mod.Report(f"Отчет импорта: {file.name}", [f"Проект: {project.name}", f"Система координат: {project.crs_name}"])
    if suffix == ".xml":
        applied = rules_mod.applied("egrn", project)
        report.header += applied.header(project)
        frames = egrn.ingest(file, applied.rules, project, report)
        inputs = [file, *applied.files]
    else:
        source = common.load_source(file)
        applied = rules_mod.applied(source.rules, project)
        rules = applied.rules
        if rules["type"] != RULES_TYPE[suffix]:
            raise ValueError(f"{file.name}: правила {source.rules} типа {rules['type']}, для {suffix} нужны {RULES_TYPE[suffix]}")
        report.header += [f"Источник: {source.label}, {source.date}", *applied.header(project)]
        frames = (dxf if suffix == ".dxf" else vector).ingest(file, source, rules, project, report)
        inputs = [file, common.source_path(file), *applied.files]
    return common.write_part(project, file.stem, frames, report, inputs), report


def run(args) -> int:
    project = project_mod.load(args.project)
    files = [args.input] if args.input.is_file() else sorted(f for f in args.input.iterdir() if f.suffix.lower() in SUFFIXES)
    if not files:
        raise ValueError(f"в {args.input} нет файлов DXF, XML, SHP, GPKG")
    stems = [f.stem for f in files]
    if len(set(stems)) != len(stems):
        raise ValueError(f"имена входов повторяются без учета расширения: {stems}")
    for file in files:
        out, report = ingest_file(project, file)
        total = sum(n for _, dst, n in report.recognized if "." not in dst)
        print(f"{file.name}: объектов {total}, не распознано {len(report.unrecognized)}, исправлено {len(report.fixed)}, "
              f"на подтверждение {len(report.confirm)} → {out}, отчет {out.with_suffix('.report.md')}")
    return 0


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("ingest", help="DXF, КПТ (XML), SHP, GPKG → часть геопакета и отчет импорта")
    p.add_argument("project", type=Path, help="project.yaml проекта")
    p.add_argument("input", type=Path, help="файл или папка с файлами")
    p.set_defaults(func=run)
