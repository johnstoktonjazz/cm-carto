"""Сборка карты по описанию через PyQGIS без окна: PNG в размер места, легенда SVG, provenance по контракту image.

Компоновка — design/qgis/map_<место>.qpt верстки, стили ролей — design/qgis/<роль>.qml, легенда отдельным файлом —
legend_<ширина>.qpt; все без правок (DECISIONS.md, К-004). Слой роли — слой геопакета с отбором where и объектами,
пересекающими охват карты: подписи «до пяти объектов» (max_features в стилях) и счет в provenance считают то, что
на карте. Кеш — по хэшу описания, анкеты, геопакета, стилей и токенов верстки и кода рендера: повтор не рендерит.
"""

import atexit
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Обрезку SVG по содержимому QGIS делает через QDomDocument; атрибуты элемента там в QHash, а затравка QHash в Qt 5
# своя у каждого процесса: порядок атрибутов в SVG легенды менялся от запуска к запуску. Постоянная затравка — тот же
# файл байт в байт. Действует, только если Qt еще не загружен в процесс: модуль импортируется раньше qgis.
if "qgis.core" in sys.modules and os.environ.get("QT_HASH_SEED") != "0":
    raise RuntimeError("cm_carto.render.build импортирован после qgis без QT_HASH_SEED=0: легенда SVG не будет "
                       "воспроизводимой; импортировать модуль раньше qgis или задать QT_HASH_SEED=0 в окружении")
os.environ["QT_HASH_SEED"] = "0"

import jsonschema  # noqa: E402
import yaml  # noqa: E402
from osgeo import gdal  # noqa: E402
from qgis.core import (  # noqa: E402
    NULL,
    Qgis,
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsFeatureRequest,
    QgsLayerTreeGroup,
    QgsLayoutExporter,
    QgsMapLayerLegendUtils,
    QgsMargins,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtXml import QDomDocument  # noqa: E402

from cm_carto import paths, tokens  # noqa: E402
from cm_carto.render import spec as spec_file  # noqa: E402
from cm_carto.render.spec import Role, Spec  # noqa: E402

CACHE = paths.ROOT / "_build" / "cache"
CODE = [*sorted(Path(__file__).parent.glob("*.py")), Path(__file__).with_name("print.yaml"), spec_file.SCHEMA]
SOURCE_FIELDS = ["status", "source_label", "source_license", "source_date"]
# Статусы геометрии от надежного к слабому: сводный статус карты и слоя — самый слабый из встреченных.
STATUSES = ["official", "cadastral", "concept", "digitized", "reference"]


@cache
def start() -> QgsApplication:
    app = QgsApplication([], False)
    app.initQgis()
    atexit.register(app.exitQgis)
    return app


@cache
def forbidden() -> list[str]:
    data = yaml.safe_load(Path(__file__).with_name("print.yaml").read_text(encoding="utf-8"))
    return [word.casefold() for word in data["forbidden"]]


@dataclass
class Drawn:
    role: Role
    layer: QgsVectorLayer
    rows: list[dict]          # поля источника объектов в охвате
    legend_nodes: list[int]   # строки легенды слоя: для категорий — только встреченные на карте


def value(v):
    return None if v == NULL else v


def vector(spec: Spec, role: Role, rect: QgsRectangle) -> Drawn:
    # Только чтение: открытый на запись GPKG QGIS переводит в журнал WAL (байты 18–19 заголовка), файл данных
    # меняется от рендера, и хэш кеша не совпадает при повторе (найдено при повторной сборке).
    options = QgsVectorLayer.LayerOptions(QgsProject.instance().transformContext())
    options.forceReadOnly = True
    layer = QgsVectorLayer(f"{spec.data}|layername={role.layer}", role.title, "ogr", options)
    if not layer.isValid():
        raise ValueError(f"{spec.data}: слой {role.layer} не открывается")
    if role.where and not layer.setSubsetString(role.where):
        raise ValueError(f"{spec.path}: роль {role.key}: отбор «{role.where}» не применяется к слою {role.layer}")
    message, ok = layer.loadNamedStyle(str(paths.qgis_styles() / f"{role.key}.qml"))
    if not ok:
        raise ValueError(f"{role.key}.qml: {message}")
    renderer = layer.renderer()
    category = renderer.classAttribute() if isinstance(renderer, QgsCategorizedSymbolRenderer) else None
    request = QgsFeatureRequest().setFilterRect(rect).setFlags(QgsFeatureRequest.Flag.ExactIntersect)
    # Столбец, которого нет ни у одного объекта слоя (source_license у официальных), в файле не пишется.
    fields = [k for k in SOURCE_FIELDS + ([category] if category else []) if k in layer.fields().names()]
    found = sorted(((f.id(), {k: value(f[k]) if k in fields else None for k in SOURCE_FIELDS + [category]})
                    for f in layer.getFeatures(request)), key=lambda item: item[0])
    ids = ",".join(str(fid) for fid, _ in found) or "-1"
    layer.setSubsetString(f"({role.where}) AND fid IN ({ids})" if role.where else f"fid IN ({ids})")
    rows = [row for _, row in found]
    if category:
        present = {row[category] for row in rows}
        nodes = [i for i, c in enumerate(renderer.categories()) if c.value() in present]
    else:
        nodes = list(range(len(renderer.legendSymbolItems())))
    return Drawn(role, layer, rows, nodes)


def check_print(spec: Spec, drawn: list[Drawn]) -> None:
    """Правило печати: на карте нет объектов источников, запрещенных к печати (print.yaml)."""
    errors = set()
    for d in drawn:
        for row in d.rows:
            for field in ("source_license", "source_label"):
                text = (row[field] or "").casefold()
                for word in forbidden():
                    if word in text:
                        errors.add(f"роль {d.role.key} (слой {d.role.layer}): {field} «{row[field]}»")
    if errors:
        raise ValueError(f"{spec.path}: на карте данные, запрещенные к печати (cm_carto/render/print.yaml):\n  "
                         + "\n  ".join(sorted(errors)))


def template(project: QgsProject, path: Path) -> QgsPrintLayout:
    layout = QgsPrintLayout(project)
    doc = QDomDocument()
    doc.setContent(path.read_text(encoding="utf-8"))
    layout.loadFromTemplate(doc, QgsReadWriteContext())
    return layout


def draft(layout: QgsPrintLayout) -> None:
    """Черновая отрисовка: после нее QGIS знает размеры элементов (легенда); refresh — как у верстки (qgis_render)."""
    QgsLayoutExporter(layout).renderPageToImage(0)
    layout.refresh()


def fill_legend(legend, std: dict, drawn: list[Drawn]) -> None:
    """Строки легенды: роли уровней с legend: true в порядке стандарта; с groups_from строк — группы уровней."""
    legend.setAutoUpdateModel(False)
    model = legend.model()
    root = model.rootGroup()
    root.removeAllChildren()
    shown = [d for d in drawn if std["levels"][d.role.level]["legend"]]
    rows = sum(len(d.legend_nodes) for d in shown)
    if rows == 0:
        raise ValueError("в легенде нет строк: на карте только подложка")
    if rows > std["legend"]["max_items"]:
        raise ValueError(f"в легенде {rows} строк, предел {std['legend']['max_items']}: делить карту (map.yaml: legend)")
    groups = {}
    for d in shown:
        parent = root
        if rows >= std["legend"]["groups_from"]:
            if d.role.level not in groups:
                groups[d.role.level] = root.addChildNode(QgsLayerTreeGroup(std["levels"][d.role.level]["title"]))
            parent = groups[d.role.level]
        node = parent.addLayer(d.layer)
        QgsMapLayerLegendUtils.setLegendNodeOrder(node, d.legend_nodes)
        model.refreshLayerLegend(node)
    legend.updateLegend()
    draft(legend.layout())
    legend.adjustBoxSize()


def export_png(layout: QgsPrintLayout, out: Path, dpi: int) -> None:
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = dpi
    settings.appendGeoreference = False  # картинка для слайда; привязка — в provenance
    settings.exportMetadata = False
    # QGIS после записи PNG открывает его через GDAL на запись привязки, GDAL печатает «PNG driver does not support
    # update access»; картинка записана, итог экспорта проверяется ниже (так же у верстки и в doctor).
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    result = QgsLayoutExporter(layout).exportToImage(str(out), settings)
    gdal.PopErrorHandler()
    if result != QgsLayoutExporter.Success:
        raise RuntimeError(f"{out}: экспорт не удался ({result})")


def export_legend(layout: QgsPrintLayout, out: Path, std: dict) -> None:
    rule = std["export"]["legend"]
    settings = QgsLayoutExporter.SvgExportSettings()
    settings.cropToContents = rule["crop_to_contents"]
    settings.cropMargins = QgsMargins(0, 0, rule["crop_margin"], 0)  # мм
    settings.textRenderFormat = Qgis.TextRenderFormat.AlwaysOutlines
    settings.exportMetadata = False  # иначе в SVG дата экспорта: файл различается при каждой сборке
    result = QgsLayoutExporter(layout).exportToSvg(str(out), settings)
    if result != QgsLayoutExporter.Success:
        raise RuntimeError(f"{out}: экспорт легенды не удался ({result})")


def weakest(statuses) -> str:
    return max(statuses, key=STATUSES.index)


def sources(rows: list[dict]) -> list[str]:
    """Подписи источников объектов в порядке появления: «подпись, лицензия»."""
    out = []
    for row in rows:
        text = ", ".join(v for v in (row["source_label"], row["source_license"]) if v)
        if text not in out:
            out.append(text)
    return out


def provenance(spec: Spec, std: dict, style: dict, drawn: list[Drawn]) -> dict:
    """Описание по контракту image. Источник, дата и статус — из объектов в охвате карты (README модуля)."""
    layers, all_sources = [], []
    for d in drawn:
        layer_sources = sources(d.rows)
        all_sources += [s for s in layer_sources if s not in all_sources]
        layers.append({"name": d.role.title, "source": "; ".join(layer_sources),
                       "status": weakest(row["status"] for row in d.rows),
                       "date": max(row["source_date"] for row in d.rows)})
    subject = [entry["status"] for d, entry in zip(drawn, layers)
               if std["levels"][d.role.level]["legend"] and entry["status"] != "reference"]
    if not subject:
        raise ValueError(f"{spec.path}: статус карты не выражается контрактом image: слои легенды только reference, "
                         f"а в image: status нет reference (вопрос верстке, доклад render-dev)")
    scale = f"{spec.scale:,}".replace(",", style["number_format"]["thousands"])
    doc = {"file": spec.out.name, "kind": "map"}
    if spec.title:
        doc["title"] = spec.title
    doc |= {"source": "; ".join(all_sources), "date": max(entry["date"] for entry in layers),
            "status": weakest(subject), "size_mm": {"width": spec.size_mm[0], "height": spec.size_mm[1]},
            "dpi": std["export"]["dpi"], "crs": spec.project.crs_name, "scale": f"1:{scale}", "layers": layers}
    if spec.legend != "inside":
        doc["legend_file"] = spec.legend_out.name
    if spec.fact_ids:
        doc["fact_ids"] = spec.fact_ids
    errors = [e.message for e in jsonschema.Draft202012Validator(image_schema()).iter_errors(doc)]
    if errors:
        raise ValueError(f"{spec.provenance_out.name}: не проходит контракт image:\n  " + "\n  ".join(errors))
    return doc


@cache
def image_schema() -> dict:
    return json.loads(paths.schema("image").read_text(encoding="utf-8"))


def draw(spec: Spec, folder: Path) -> None:
    """Рендер в папку кеша: карта, легенда (если отдельным файлом), provenance."""
    start()
    std, style = tokens.load("map"), tokens.load("style")
    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem.fromWkt(spec.project.crs.to_wkt()))
    layout = template(project, paths.qgis_styles() / f"map_{spec.place}.qpt")
    item = layout.itemById("map")
    cx, cy = spec.center
    half_w, half_h = spec.size_mm[0] * spec.scale / 2000, spec.size_mm[1] * spec.scale / 2000
    rect = QgsRectangle(cx - half_w, cy - half_h, cx + half_w, cy + half_h)

    drawn = []
    for role in spec.roles:
        d = vector(spec, role, rect)
        where = f" ({role.where})" if role.where else ""
        print(f"{spec.path.name}: {role.key} ← {role.layer}{where}: в охвате {len(d.rows)}")
        if d.rows:
            drawn.append(d)
            project.addMapLayer(d.layer, False)
    if not drawn:
        raise ValueError(f"{spec.path}: в охвате нет объектов ни одной роли")
    check_print(spec, drawn)

    north = not any(d.role.level == "basemap" for d in drawn)
    if spec.north is not None and spec.north != north:
        raise ValueError(f"{spec.path}: north: {spec.north}, по правилу MAPS.md, раздел 7 — {north} "
                         f"(север вверху; подложка в охвате {'есть' if not north else 'нет'})")
    item.setLayers([d.layer for d in drawn])
    item.setExtent(rect)
    layout.itemById("north").setVisibility(north)
    inside = layout.itemById("legend")
    if inside is not None:
        inside.setVisibility(spec.legend == "inside")
    if spec.legend == "inside":
        fill_legend(inside, std, drawn)
    layout.refresh()
    export_png(layout, folder / spec.out.name, std["export"]["dpi"])

    if spec.legend != "inside":
        legend_layout = template(project, paths.qgis_styles() / f"legend_{spec.legend}.qpt")
        fill_legend(legend_layout.itemById("legend"), std, drawn)
        export_legend(legend_layout, folder / spec.legend_out.name, std)

    doc = provenance(spec, std, style, drawn)
    (folder / spec.provenance_out.name).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
    project.clear()


def cache_key(spec: Spec) -> str:
    """Хэш всего, от чего зависит результат: описание, анкета, геопакет, токены и стили верстки, контракт, код."""
    digest = hashlib.sha256()
    design = [paths.design() / "tokens" / "map.yaml", paths.design() / "tokens" / "style.yaml",
              *sorted(p for p in paths.qgis_styles().iterdir() if p.is_file())]
    for path in [spec.path, spec.project.root / "project.yaml", spec.data, *design, paths.schema("image"), *CODE]:
        digest.update(path.name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    digest.update(Qgis.version().encode())
    return digest.hexdigest()


def render(path: Path) -> list[Path]:
    """Карта по описанию; возвращает записанные файлы. Повтор с теми же входами берется из кеша без рендера."""
    spec = spec_file.load(path)
    folder = CACHE / cache_key(spec)
    if folder.is_dir():
        print(f"{spec.path.name}: из кеша {folder.name[:12]}")
    else:
        work = folder.with_name(folder.name + ".work")
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        draw(spec, work)
        work.rename(folder)
    spec.out.parent.mkdir(parents=True, exist_ok=True)
    written = []
    for name in sorted(p.name for p in folder.iterdir()):
        target = spec.out.with_name(name)
        shutil.copyfile(folder / name, target)
        written.append(target)
    return written
