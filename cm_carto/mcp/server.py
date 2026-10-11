"""MCP-сервер картографа (FastMCP, stdio): те же функции, что у командной строки.

Инструмент = подкоманда `cm-carto` с теми же полями; вызывается та же функция `run` модуля команды, ответ — ее
вывод: пути результатов и короткий отчет. Плюс `qgis_project` — проект .qgz для проверки глазами (cm_carto/qgis/project.py).
Относительные пути — от папки проекта: переменная CM_CARTO_PROJECT, без нее — текущая папка сервера.
QGIS поднимается один раз на процесс (render.build.start), без окна; QT_HASH_SEED=0 ставится до загрузки Qt.

Инструменты синхронные внутри async-функций: PyQGIS работает в главном потоке процесса.
"""

import contextlib
import io
import os
from argparse import Namespace
from pathlib import Path
from typing import Literal

os.environ.setdefault("QT_HASH_SEED", "0")
if os.environ["QT_HASH_SEED"] != "0":
    raise RuntimeError("QT_HASH_SEED должен быть 0 до загрузки Qt (порядок атрибутов SVG, Э-006)")

from fastmcp import FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from cm_carto import cli  # noqa: E402
from cm_carto.analysis import cli as analysis_cli  # noqa: E402
from cm_carto.fetch import cli as fetch_cli  # noqa: E402
from cm_carto.ingest import cli as ingest_cli  # noqa: E402
from cm_carto.render import cli as render_cli  # noqa: E402

mcp = FastMCP("cm-carto", instructions=(
    "Картограф CM: геопакет проекта (ingest, fetch), карты под места макетов (render), анализ (isochrone, measure, "
    "buffer, count), проверка окружения (doctor), проект QGIS для проверки глазами (qgis_project). Относительные "
    "пути — от папки проекта. Выпуск собирает только render из описания; правки в QGIS в выпуск не идут."))


def at(value: str | Path) -> Path:
    return Path(os.environ.get("CM_CARTO_PROJECT", ".")).resolve() / value


def call(func, **fields) -> str:
    """Функция команды с полями аргументов; ненулевой код возврата — ошибка инструмента с ее выводом."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = func(Namespace(**fields))
    if code:
        raise ToolError(out.getvalue().strip())
    return out.getvalue().strip()


@mcp.tool
async def doctor(out: str | None = None) -> str:
    """Проверка окружения: QGIS без окна, шрифты, контракты, пробная карта и повтор байт в байт. out — папка пробной карты."""
    return call(cli.doctor, out=at(out) if out else None)


@mcp.tool
async def ingest(project: str, input: str) -> str:
    """DXF, КПТ (XML), SHP, GPKG → часть геопакета и отчет импорта. project — project.yaml, input — файл или папка."""
    return call(ingest_cli.run, project=at(project), input=at(input))


@mcp.tool(description="Внешние источники → часть геопакета и кеш: " + "; ".join(f"{k} — {v}" for k, v in fetch_cli.SOURCES.items()))
async def fetch(project: str, source: Literal[tuple(fetch_cli.SOURCES)], release: str | None = None,
                file: str | None = None, date: str | None = None, base: Literal["overture", "osm"] = "overture") -> str:
    return call(fetch_cli.run, project=at(project), source=source, release=release, file=at(file) if file else None,
                date=date, base=base)


@mcp.tool
async def render(specs: list[str], preview: bool = False) -> str:
    """Карты по описаниям (schema/map.schema.json): подложка PNG, provenance, overlay.json; preview — еще превью."""
    return call(render_cli.run, specs=[at(s) for s in specs], preview=preview)


@mcp.tool
async def isochrone(project: str, origin: str, mode: Literal["walk", "drive"], minutes: list[float]) -> str:
    """Изохроны от объекта (origin — object_id точки или контура) по графу улиц → слой isochrones части геопакета."""
    return call(analysis_cli.run_isochrone, project=at(project), origin=origin, mode=mode, minutes=minutes)


@mcp.tool
async def measure(project: str, object_id: str) -> str:
    """Площадь и длина достижимой сети изохроны → заготовки CALC."""
    return call(analysis_cli.run_measure, project=at(project), object_id=object_id)


@mcp.tool
async def buffer(project: str, rule: str, origin: list[str] | None = None) -> str:
    """Буферы по норме реестра (rules/analysis.yaml, buffers.rules) → слой buffers; origin — object_id источников."""
    return call(analysis_cli.run_buffer, project=at(project), rule=rule, origin=origin)


@mcp.tool
async def count(project: str, zone: str, layer: str, where: str | None = None) -> str:
    """Число объектов слоя в зоне и площадь пересечения → заготовки CALC; where — фильтр OGR."""
    return call(analysis_cli.run_count, project=at(project), zone=zone, layer=layer, where=where)


@mcp.tool
async def qgis_project(spec: str, out: str | None = None) -> str:
    """Проект .qgz по описанию карты для проверки глазами (слои и стили верстки, геопакет только на чтение). Не выпуск."""
    from cm_carto.qgis import project

    return str(project.build_project(at(spec), at(out) if out else None))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
