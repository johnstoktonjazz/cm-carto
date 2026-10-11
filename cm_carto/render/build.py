"""Сборка карты по описанию через PyQGIS без окна, одним проходом — два выхода (решение Романа: все, кроме подложки,
редактируемо в PowerPoint):

- <out>.png — подложка: роли уровня basemap (map.yaml) со своими подписями, компоновка design/qgis/map_<место>.qpt
  верстки без линейки, севера и легенды, в размер места, 300 dpi; <out>.provenance.json по контракту image;
- <out>.overlay.json — остальное в мм места (overlay.py): объекты, подписи, легенда, линейка, север.

Если роль данных стоит в порядке стандарта между ролями подложки (зоны ПЗЗ над застройкой, под улицами), подложка
делится по порядку: нижняя часть — <out>.png, верхние — <out>-2.png … с прозрачным фоном; порядок — overlay: stack.
Стили ролей — design/qgis/<роль>.qml без правок (К-004). Слой роли — слой геопакета с отбором where и объектами,
пересекающими охват карты. Кеш — по хэшу описания, анкеты, геопакета, стилей и токенов верстки и кода рендера.
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
    QgsFillSymbol,
    QgsFeatureRequest,
    QgsLayoutExporter,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtXml import QDomDocument  # noqa: E402

from cm_carto import paths, tokens  # noqa: E402
from cm_carto.render import overlay, spec as spec_file  # noqa: E402
from cm_carto.render.spec import Role, Spec  # noqa: E402

CACHE = paths.ROOT / "_build" / "cache"
CODE = [*sorted(Path(__file__).parent.glob("*.py")), Path(__file__).with_name("print.yaml"), spec_file.SCHEMA,
        overlay.SCHEMA]
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


def map_status(spec: Spec, std: dict, drawn: list[Drawn]) -> str:
    """Сводный статус карты целиком (подложка и overlay): самый слабый среди ролей легенды, кроме reference."""
    statuses = [weakest(row["status"] for row in d.rows) for d in drawn if std["levels"][d.role.level]["legend"]]
    subject = [s for s in statuses if s != "reference"]
    if not subject:
        raise ValueError(f"{spec.path}: статус карты не выражается контрактом image: слои легенды только reference, "
                         f"а в image: status нет reference (вопрос верстке, доклад render-dev)")
    return weakest(subject)


def layer_entry(d: Drawn) -> dict:
    """Слой на карте: строка легенды, источники, самый слабый статус и самая поздняя дата объектов в охвате."""
    return {"name": d.role.title, "source": "; ".join(sources(d.rows)),
            "status": weakest(row["status"] for row in d.rows), "date": max(row["source_date"] for row in d.rows)}


def provenance(spec: Spec, std: dict, style: dict, drawn: list[Drawn]) -> dict:
    """Описание подложки по контракту image: слои, источник и дата — только подложки; статус — карты целиком."""
    basemap = [d for d in drawn if d.role.level == "basemap"]
    layers = [layer_entry(d) for d in basemap]
    all_sources = []
    for d in basemap:
        all_sources += [s for s in sources(d.rows) if s not in all_sources]
    scale = overlay.number(spec.scale, style)
    doc = {"file": spec.out.name, "kind": "map"}
    if spec.title:
        doc["title"] = spec.title
    doc |= {"source": "; ".join(all_sources), "date": max(entry["date"] for entry in layers),
            "status": map_status(spec, std, drawn), "size_mm": {"width": spec.size_mm[0], "height": spec.size_mm[1]},
            "dpi": std["export"]["dpi"], "crs": spec.project.crs_name, "scale": f"1:{scale}", "layers": layers}
    if spec.fact_ids:
        doc["fact_ids"] = spec.fact_ids
    errors = [e.message for e in jsonschema.Draft202012Validator(image_schema()).iter_errors(doc)]
    if errors:
        raise ValueError(f"{spec.provenance_out.name}: не проходит контракт image:\n  " + "\n  ".join(errors))
    return doc


@cache
def image_schema() -> dict:
    return json.loads(paths.schema("image").read_text(encoding="utf-8"))


def stack(drawn: list[Drawn], spec: Spec) -> tuple[list[list[Drawn]], list[dict]]:
    """Стопка снизу вверх: подряд идущие в порядке стандарта роли подложки — одна картинка, роли данных — overlay."""
    runs, items = [], []
    for d in reversed(drawn):
        kind = "basemap" if d.role.level == "basemap" else "overlay"
        if items and items[-1]["kind"] == kind:
            items[-1]["roles"].append(d.role.key)
            if kind == "basemap":
                runs[-1].append(d)
        else:
            items.append({"kind": kind, "roles": [d.role.key]})
            if kind == "basemap":
                runs.append([d])
    names = [spec.out.name] + [f"{spec.out.stem}-{k}.png" for k in range(2, len(runs) + 1)]
    for item in (i for i in items if i["kind"] == "basemap"):
        item["file"] = names.pop(0)
    return runs, items


def draw(spec: Spec, folder: Path) -> None:
    """Рендер в папку кеша: подложка PNG, provenance, overlay.json."""
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
    if not drawn:
        raise ValueError(f"{spec.path}: в охвате нет объектов ни одной роли")
    check_print(spec, drawn)
    north = not any(d.role.level == "basemap" for d in drawn)
    if spec.north is not None and spec.north != north:
        raise ValueError(f"{spec.path}: north: {spec.north}, по правилу MAPS.md, раздел 7 — {north} "
                         f"(север вверху; подложка в охвате {'есть' if not north else 'нет'})")

    runs, items = stack(drawn, spec)
    for element in ("scalebar", "north", "legend"):  # служебные знаки — в overlay
        if layout.itemById(element) is not None:
            layout.itemById(element).setVisibility(False)
    item.setExtent(rect)
    page = layout.pageCollection().page(0)
    white = page.pageStyleSymbol().clone()
    for run, entry in zip(runs, (i for i in items if i["kind"] == "basemap")):
        item.setLayers([d.layer for d in reversed(run)])
        # Нижняя часть — на белом листе компоновки; верхние — на прозрачном, поверх слоев overlay.
        page.setPageStyleSymbol(white if entry["file"] == spec.out.name
                                else QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_style": "no"}))
        layout.refresh()
        export_png(layout, folder / entry["file"], std["export"]["dpi"])

    basemap = None
    if runs:  # карта без подложки (MAPS.md, раздел 7: тогда стрелка севера) — только overlay на белом
        basemap = spec.out.name
        doc = provenance(spec, std, style, drawn)
        (folder / spec.provenance_out.name).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                                                       encoding="utf-8")
    else:
        map_status(spec, std, drawn)
    bounds = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
    layers = [{"role": d.role.key} | layer_entry(d) for d in drawn if d.role.level != "basemap"]
    data = overlay.build(spec, std, style, drawn, items, layers, north, layout, bounds, basemap)
    (folder / spec.overlay_out.name).write_text(overlay.dumps(data), encoding="utf-8")
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
