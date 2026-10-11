"""Проверка окружения: QGIS без окна, шрифты из токенов, контракты и стили верстки, пробный рендер.

    cm-carto doctor [-o папка]

Пробный рендер: два синтетических полигона (квартал и территория) в стилях верстки, компоновка map_thumb.qpt,
PNG 300 dpi. Проверяется размер в пикселях по месту из map.yaml, что картинка не пустая и что повторная
сборка дает тот же файл.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from osgeo import gdal  # noqa: E402
from qgis.core import (  # noqa: E402
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsLayoutExporter,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QFontDatabase, QImage  # noqa: E402
from qgis.PyQt.QtXml import QDomDocument  # noqa: E402

from cm_carto import paths, tokens  # noqa: E402

PLACE = "thumb"
CRS = "EPSG:3395"  # мировая Меркатора: любая метрическая, пробный рендер не привязан ни к одному проекту
MIN_QGIS = 34400


def check_fonts() -> list[str]:
    faces = tokens.load("style")["font"]["faces"]
    db = QFontDatabase()
    families = set(db.families())
    return [f"шрифт {face} ({role}) не установлен" for role, face in faces.items() if face not in families]


def check_contracts() -> list[str]:
    errors = []
    for name in ("geopackage", "image", "fact"):
        json.loads(paths.schema(name).read_text(encoding="utf-8"))
    for name in ("territory", "blocks"):
        if not (paths.qgis_styles() / f"{name}.qml").is_file():
            errors.append(f"нет стиля верстки {name}.qml")
    if not (paths.qgis_styles() / f"map_{PLACE}.qpt").is_file():
        errors.append(f"нет компоновки верстки map_{PLACE}.qpt")
    return errors


def polygon_layer(name: str, wkt: str) -> QgsVectorLayer:
    layer = QgsVectorLayer(f"MultiPolygon?crs={CRS}", name, "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt(wkt))
    layer.dataProvider().addFeatures([feature])
    message, ok = layer.loadNamedStyle(str(paths.qgis_styles() / f"{name}.qml"))
    if not ok:
        raise RuntimeError(f"{name}.qml: {message}")
    return layer


def render(out: Path) -> Path:
    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem(CRS))
    layers = [
        polygon_layer("territory", "MULTIPOLYGON(((500100 5000100,500300 5000100,500300 5000250,500100 5000250,500100 5000100)))"),
        polygon_layer("blocks", "MULTIPOLYGON(((500000 5000000,500400 5000000,500400 5000400,500000 5000400,500000 5000000)))"),
    ]
    for layer in layers:
        project.addMapLayer(layer, False)
    doc = QDomDocument()
    doc.setContent((paths.qgis_styles() / f"map_{PLACE}.qpt").read_text(encoding="utf-8"))
    layout = QgsPrintLayout(project)
    layout.loadFromTemplate(doc, QgsReadWriteContext())
    item = layout.itemById("map")
    item.setLayers(layers)
    item.setExtent(QgsRectangle(500000, 5000000, 500400, 5000400))
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = 300
    settings.appendGeoreference = False
    settings.exportMetadata = False
    # QGIS после записи PNG открывает его через GDAL на запись привязки, GDAL печатает «PNG driver does not support
    # update access»; картинка при этом записана, итог экспорта проверяется ниже (так же у верстки, qgis_render).
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    result = QgsLayoutExporter(layout).exportToImage(str(out), settings)
    gdal.PopErrorHandler()
    project.clear()
    if result != QgsLayoutExporter.Success:
        raise RuntimeError(f"экспорт пробной карты не удался ({result})")
    return out


def check_render(folder: Path) -> list[str]:
    errors = []
    first, second = render(folder / "doctor-1.png"), render(folder / "doctor-2.png")
    image = QImage(str(first))
    width_mm, height_mm = tokens.place_size_mm(PLACE)
    expected = (int(width_mm / 25.4 * 300), int(height_mm / 25.4 * 300))
    if abs(image.width() - expected[0]) > 1 or abs(image.height() - expected[1]) > 1:
        errors.append(f"пробная карта {image.width()}×{image.height()} px, место {PLACE} — {expected[0]}×{expected[1]}")
    colors = {image.pixel(x, y) for x in range(0, image.width(), 25) for y in range(0, image.height(), 25)}
    if len(colors) < 2:
        errors.append("пробная карта пустая: один цвет")
    if hashlib.sha256(first.read_bytes()).digest() != hashlib.sha256(second.read_bytes()).digest():
        errors.append("повторная сборка пробной карты дала другой файл")
    return errors


def run(folder: Path | None = None) -> list[str]:
    """Список ошибок окружения; пустой — окружение годно."""
    errors = []
    if Qgis.versionInt() < MIN_QGIS:
        errors.append(f"QGIS {Qgis.version()}, нужен 3.44 или новее")
    app = QgsApplication([], False)
    app.initQgis()
    errors += check_contracts()
    errors += check_fonts()
    if folder is None:
        folder = Path(tempfile.mkdtemp(prefix="cm-carto-doctor-"))
    folder.mkdir(parents=True, exist_ok=True)
    errors += check_render(folder)
    app.exitQgis()
    print(f"QGIS {Qgis.version()}; cm-contracts: {paths.contracts()}; cm-design: {paths.design()}; пробная карта: {folder}")
    return errors
