"""MCP-сервер: список инструментов равен подкомандам CLI (плюс qgis_project), doctor и render через клиент FastMCP
в памяти дают те же файлы, что CLI, байт в байт; проект .qgz собирается из описания карты."""

import asyncio
import contextlib
import io
import os
import re
import subprocess
import sys

import pytest
import yaml
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pyproj import CRS
from shapely.geometry import box

from cm_carto import cli, gpkg
from cm_carto.mcp import server
from cm_carto.render import build
from test_render_map import CRS_NAME, OFFICIAL, X, Y, rows

ENV = {**os.environ, "QT_HASH_SEED": "0"}


def tool(name: str, **arguments):
    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(run())


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("mcp")
    (root / "project.yaml").write_text(yaml.safe_dump(
        {"name": "синтетический проект", "crs": {"name": CRS_NAME, "definition": CRS_NAME},
         "extent_wgs84": [0, 0, 1, 1], "geo": "geo"}, allow_unicode=True), encoding="utf-8")
    blocks = [(f"b{i}", box(X + i * 220, Y, X + i * 220 + 200, Y + 200), {**OFFICIAL, "num": str(i)}) for i in range(-2, 2)]
    frames = {"blocks": rows("blocks", blocks),
              "boundary_planning": rows("boundary_planning", [("t", box(X - 200, Y - 100, X + 190, Y + 190), OFFICIAL)])}
    gpkg.write(root / "geo" / "project.gpkg", frames, "синтетический проект", CRS.from_user_input(CRS_NAME), CRS_NAME,
               "sha256:" + "0" * 64)
    return root


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "CACHE", tmp_path / "cache")


def describe(folder, name: str) -> str:
    spec = {"place": "square_small", "project": "project.yaml", "center": [X, Y], "scale": 10000, "legend": 91,
            "layers": {"territory": {"layer": "boundary_planning", "title": "Граница проекта планировки"}, "blocks": {}},
            "title": "Кварталы", "out": f"maps/{name}.png"}
    (folder / f"{name}.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    return f"{name}.yaml"


def test_tools_match_cli_commands():
    help_text = io.StringIO()
    with contextlib.redirect_stdout(help_text), pytest.raises(SystemExit):
        cli.main(["--help"])
    commands = set(re.search(r"\{([a-z_,]+)\}", help_text.getvalue()).group(1).split(","))
    async def listing():
        async with Client(server.mcp) as client:
            return {t.name for t in await client.list_tools()}
    assert asyncio.run(listing()) == commands | {"qgis_project"}
    assert commands == {"doctor", "ingest", "fetch", "render", "isochrone", "measure", "buffer", "count"}


def test_doctor_matches_cli(tmp_path):
    subprocess.run([sys.executable, "-m", "cm_carto.cli", "doctor", "-o", str(tmp_path / "cli")], check=True, env=ENV)
    text = tool("doctor", out=str(tmp_path / "mcp")).content[0].text
    assert "окружение годно" in text
    assert (tmp_path / "cli" / "doctor-1.png").read_bytes() == (tmp_path / "mcp" / "doctor-1.png").read_bytes()


def test_render_matches_cli(project, monkeypatch):
    monkeypatch.setenv("CM_CARTO_PROJECT", str(project))
    cli_spec, mcp_spec = describe(project, "by-cli"), describe(project, "by-mcp")
    subprocess.run([sys.executable, "-c", "import sys; from pathlib import Path; from cm_carto.render import build; "
                    "from cm_carto import cli; build.CACHE = Path(sys.argv[1]); cli.main(['render', sys.argv[2]])",
                    str(project / "cli-cache"), str(project / cli_spec)], check=True, env=ENV)
    text = tool("render", specs=[mcp_spec]).content[0].text
    maps = project / "maps"
    assert str(maps / "by-mcp.png") in text
    assert (maps / "by-cli.png").read_bytes() == (maps / "by-mcp.png").read_bytes()


def test_failed_command_is_tool_error(project, monkeypatch):
    monkeypatch.setenv("CM_CARTO_PROJECT", str(project))
    with pytest.raises(ToolError, match="ролей нет в map.yaml"):
        spec = yaml.safe_load((project / describe(project, "bad")).read_text(encoding="utf-8"))
        spec["layers"] = {"schools": {}}
        (project / "bad.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
        tool("render", specs=["bad.yaml"])


def test_qgis_project_has_layers_in_order_and_layout(project, monkeypatch):
    from qgis.core import QgsProject

    monkeypatch.setenv("CM_CARTO_PROJECT", str(project))
    path = tool("qgis_project", spec=describe(project, "view")).content[0].text
    assert path == str(project / "maps" / "view.qgz")
    loaded = QgsProject()
    assert loaded.read(path)
    assert [layer.name() for layer in loaded.layerTreeRoot().layerOrder()] == ["Граница проекта планировки", "Кварталы"]
    assert [layout.name() for layout in loaded.layoutManager().layouts()] == ["view"]
    assert all(layer.readOnly() for layer in loaded.mapLayers().values())  # выпускной геопакет только на чтение (Э-005)


def test_qgis_project_basemap_layers_are_links_with_features(project, monkeypatch):
    """Подложка в .qgz — ссылка на геопакет, а не копия в памяти: после открытия объекты на месте, «ё» в подписи заменена."""
    from qgis.core import QgsProject
    from test_render_map import streets_gpkg

    monkeypatch.setenv("CM_CARTO_PROJECT", str(project))
    layers = {"road_main": {"layer": "roads", "where": "road_class = 'main'"}, "blocks": {}}
    name = describe(project, "links")
    spec = yaml.safe_load((project / name).read_text(encoding="utf-8"))
    spec.update(layers=layers, data=str(streets_gpkg(project, "links")))
    (project / name).write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    path = tool("qgis_project", spec=name).content[0].text
    loaded = QgsProject()
    assert loaded.read(path)
    roads = next(l for l in loaded.mapLayers().values() if l.name() != "Кварталы")
    assert roads.providerType() == "ogr" and roads.featureCount() > 0
    assert all(l.providerType() != "memory" and l.featureCount() > 0 for l in loaded.mapLayers().values())
    assert roads.labeling().settings().isExpression
