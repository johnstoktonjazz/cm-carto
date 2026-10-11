"""Сборка карты по описанию через PyQGIS без окна, одним проходом — два выхода (решение Романа: все, кроме подложки,
редактируемо в PowerPoint):

- <out>.png — подложка: роли уровня basemap (map.yaml) со своими подписями, компоновка design/qgis/map_<место>.qpt
  верстки без линейки, севера и легенды, в размер места, 300 dpi; <out>.provenance.json по контракту image;
- <out>.overlay.json — остальное по контракту overlay (overlay.py): объекты, подписи, легенда, линейка, север.

Overlay лежит поверх всей подложки: зоны ПЗЗ, которые по стандарту стоят под улицами (zoning.above), закрывают улицы
внутри зон — в overlay 1.0.0 нет места слою между частями подложки (вопрос к контракту, доклад render-dev).
Стили ролей — design/qgis/<роль>.qml без правок (К-004). Слой роли — слой геопакета с отбором where и объектами,
пересекающими охват карты. Кеш — по хэшу описания, анкеты, геопакета, стилей и токенов верстки, контрактов и кода.
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
import shapely  # noqa: E402
import yaml  # noqa: E402
from osgeo import gdal  # noqa: E402
from qgis.core import (  # noqa: E402
    NULL,
    Qgis,
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsFeatureRequest,
    QgsLayoutExporter,
    QgsMapRendererSequentialJob,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSizeF  # noqa: E402
from qgis.PyQt.QtXml import QDomDocument  # noqa: E402

from cm_carto import paths, tokens  # noqa: E402
from cm_carto.render import overlay, preview as preview_file, spec as spec_file  # noqa: E402
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
    spec: dict                # роль в map.yaml
    layer: QgsVectorLayer
    rows: list[dict]          # объекты в охвате: поля слоя; у ролей данных — и геометрия (geom, shapely)

    def categories(self) -> list[tuple[str, str]]:
        """Категории стиля, встреченные на карте: (значение, подпись легенды); пусто у стиля без категорий."""
        renderer = self.layer.renderer()
        if not isinstance(renderer, QgsCategorizedSymbolRenderer):
            return []
        present = {row[renderer.classAttribute()] for row in self.rows}
        return [(c.value(), c.label()) for c in renderer.categories() if c.value() in present]


def value(v):
    return None if v == NULL else v


def vector(spec: Spec, std: dict, role: Role, rect: QgsRectangle) -> Drawn:
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
    data = role.level != "basemap"
    request = QgsFeatureRequest().setFilterRect(rect).setFlags(QgsFeatureRequest.Flag.ExactIntersect)
    names = layer.fields().names()
    found = []
    for f in layer.getFeatures(request):
        # Столбец, которого нет ни у одного объекта слоя (source_license у официальных), в файле не пишется.
        row = {k: None for k in SOURCE_FIELDS} | {k: value(f[k]) for k in names}
        if data:
            row["geom"] = shapely.from_wkb(bytes(f.geometry().asWkb()))
        found.append((f.id(), row))
    found.sort(key=lambda item: item[0])
    ids = ",".join(str(fid) for fid, _ in found) or "-1"
    layer.setSubsetString(f"({role.where}) AND fid IN ({ids})" if role.where else f"fid IN ({ids})")
    return Drawn(role, spec_file.role_spec(std, role.key), layer, [row for _, row in found])


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


def export_png(layout: QgsPrintLayout, out: Path, dpi: int) -> None:
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = dpi
    settings.appendGeoreference = False  # картинка для слайда; привязка — в overlay и provenance
    settings.exportMetadata = False
    # QGIS после записи PNG открывает его через GDAL на запись привязки, GDAL печатает «PNG driver does not support
    # update access»; картинка записана, итог экспорта проверяется ниже (так же у верстки и в doctor).
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    result = QgsLayoutExporter(layout).exportToImage(str(out), settings)
    gdal.PopErrorHandler()
    if result != QgsLayoutExporter.Success:
        raise RuntimeError(f"{out}: экспорт не удался ({result})")


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


def layer_entry(d: Drawn) -> dict:
    """Слой на карте: строка легенды, источники, самый слабый статус и самая поздняя дата объектов в охвате."""
    return {"name": d.role.title, "source": "; ".join(sources(d.rows)),
            "status": weakest(row["status"] for row in d.rows), "date": max(row["source_date"] for row in d.rows)}


def basemap_labels(item, rect: QgsRectangle, spec: Spec, dpi: int) -> list[tuple[float, float, float, float]]:
    """Рамки подписей подложки (улицы, вода, зелень) в мм места: тот же движок подписей QGIS с теми же настройками
    карты, что у экспорта; подписи overlay их не задевают."""
    size = QSizeF(spec.size_mm[0] / 25.4 * dpi, spec.size_mm[1] / 25.4 * dpi)
    job = QgsMapRendererSequentialJob(item.mapSettings(rect, size, dpi, True))
    job.start()
    job.waitForFinished()
    k = 1000 / spec.scale
    out = []
    for label in job.takeLabelingResults().allLabels():
        if label.isUnplaced:
            continue
        r = label.labelGeometry.boundingBox() if hasattr(label, "labelGeometry") else label.labelRect
        out.append(((r.xMinimum() - rect.xMinimum()) * k, (rect.yMaximum() - r.yMaximum()) * k,
                    (r.xMaximum() - rect.xMinimum()) * k, (rect.yMaximum() - r.yMinimum()) * k))
    return sorted(out)


def provenance(spec: Spec, std: dict, style: dict, basemap: list[Drawn]) -> dict:
    """Описание подложки по контракту image: слои, источник, дата и статус — только подложки. Статус — самый слабый
    из слоев подложки; reference в image: status нет — тогда ошибка (вопрос к контракту, доклад render-dev)."""
    layers = [layer_entry(d) for d in basemap]
    all_sources = []
    for d in basemap:
        all_sources += [s for s in sources(d.rows) if s not in all_sources]
    status = weakest(entry["status"] for entry in layers)
    if status not in image_schema()["properties"]["status"]["enum"]:
        raise ValueError(f"{spec.path}: статус подложки {status}, в контракте image: status его нет — provenance "
                         f"не выпускается (в подложке: {', '.join(e['name'] for e in layers if e['status'] == status)})")
    doc = {"file": spec.out.name, "kind": "map"}
    if spec.title:
        doc["title"] = spec.title
    doc |= {"source": "; ".join(all_sources), "date": max(entry["date"] for entry in layers), "status": status,
            "size_mm": {"width": spec.size_mm[0], "height": spec.size_mm[1]}, "dpi": std["export"]["dpi"],
            "crs": spec.project.crs_name, "scale": f"1:{overlay.number(spec.scale, style)}", "layers": layers}
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
    """Рендер в папку кеша: подложка PNG, provenance подложки, overlay.json."""
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
        d = vector(spec, std, role, rect)
        where = f" ({role.where})" if role.where else ""
        print(f"{spec.path.name}: {role.key} ← {role.layer}{where}: в охвате {len(d.rows)}")
        if d.rows:
            drawn.append(d)
            project.addMapLayer(d.layer, False)
    check_print(spec, drawn)
    basemap = [d for d in drawn if d.role.level == "basemap"]
    if not basemap:  # MAPS.md, раздел 7 допускает карту без подложки, контракт overlay — нет (basemap обязателен)
        raise ValueError(f"{spec.path}: в охвате нет подложки, а overlay 1.0.0 требует basemap (вопрос к контракту)")
    north = False  # подложка есть, север вверху: стрелки нет (MAPS.md, раздел 7)
    if spec.north is not None and spec.north != north:
        raise ValueError(f"{spec.path}: north: {spec.north}, по правилу MAPS.md, раздел 7 — {north} "
                         f"(север вверху, подложка в охвате есть)")

    for element in ("scalebar", "north", "legend"):  # служебные знаки — в overlay
        if layout.itemById(element) is not None:
            layout.itemById(element).setVisibility(False)
    item.setLayers([d.layer for d in basemap])
    item.setExtent(rect)
    layout.refresh()
    export_png(layout, folder / spec.out.name, std["export"]["dpi"])
    doc = provenance(spec, std, style, basemap)
    (folder / spec.provenance_out.name).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")

    entries = {d.role.key: layer_entry(d) for d in drawn if d.role.level != "basemap"}
    bounds = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
    obstacles = basemap_labels(item, rect, spec, std["export"]["dpi"])
    data, unplaced = overlay.build(spec, std, style, drawn, entries, north, layout, bounds, spec.out.name, obstacles)
    for text in unplaced:
        print(f"{spec.path.name}: подпись не встала без наездов: {text}")
    (folder / spec.overlay_out.name).write_text(overlay.dumps(data), encoding="utf-8")
    project.clear()


def cache_key(spec: Spec) -> str:
    """Хэш всего, от чего зависит результат: описание, анкета, геопакет, токены и стили верстки, контракт, код."""
    digest = hashlib.sha256()
    design = [paths.design() / "tokens" / "map.yaml", paths.design() / "tokens" / "style.yaml",
              *sorted(p for p in paths.qgis_styles().iterdir() if p.is_file())]
    for path in [spec.path, spec.project.root / "project.yaml", spec.data, *design, paths.schema("image"),
                 paths.schema("overlay"), *CODE]:
        digest.update(path.name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    digest.update(Qgis.version().encode())
    return digest.hexdigest()


def render(path: Path, preview: bool = False) -> list[Path]:
    """Карта по описанию; возвращает записанные файлы. Повтор с теми же входами берется из кеша без рендера.
    preview — еще <out>.preview.png: overlay поверх подложки для проверки глазами (preview.py), не выпуск."""
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
    if preview:
        written.append(preview_file.render(spec.overlay_out, spec.preview_out))
    return written
