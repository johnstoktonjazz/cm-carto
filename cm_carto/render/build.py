"""Сборка карты по описанию через PyQGIS без окна, одним проходом — два выхода (решение Романа: все, кроме подложки,
редактируемо в PowerPoint):

- <out>.png — подложка: роли уровня basemap (map.yaml) со своими подписями, компоновка design/qgis/map_<место>.qpt
  верстки без линейки, севера и легенды, в размер места, 300 dpi; <out>.provenance.json по контракту image;
- <out>.overlay.json — остальное по контракту overlay (overlay.py): объекты, подписи, легенда, линейка, север.

Overlay лежит поверх всей подложки (второго растра нет, К-028): улицы подложки вырезаются из зон ПЗЗ и ограничений
полосой по классу улицы — полуширины те же, что у кварталов подложки (rules/fetch.yaml: blocks.half_width_m);
подписи подложки обходят линии и контуры overlay, знаки, линейку и легенду внутри (label blocking items QGIS, прием
верстки в cm_deck/qgis_render.py). Подписи подложки — по TEXT.md (labels.name) из копии слоя в памяти: геопакет
хранит name как в источнике.
Стили ролей — design/qgis/<роль>.qml без правок (К-004). Слой роли — слой геопакета с отбором where и объектами,
пересекающими охват карты, и, если задан within, — внутри контура. Кеш — по хэшу описания, анкеты, геопакета, стилей и токенов верстки, контрактов и кода.
"""

import atexit
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from functools import cache
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
    QgsFillSymbol,
    QgsLayoutExporter,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsMapRendererSequentialJob,
    QgsPrintLayout,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QSizeF  # noqa: E402
from qgis.PyQt.QtXml import QDomDocument  # noqa: E402

from cm_carto import paths, tokens  # noqa: E402
from cm_carto.render import labels as label_rules, overlay, preview as preview_file, spec as spec_file  # noqa: E402
from cm_carto.render.spec import Role, Spec  # noqa: E402

CACHE = paths.ROOT / "_build" / "cache"
FETCH_RULES = paths.ROOT / "rules" / "fetch.yaml"
CODE = [*sorted(Path(__file__).parent.glob("*.py")), Path(__file__).with_name("print.yaml"), spec_file.SCHEMA,
        label_rules.RULES, FETCH_RULES]
SOURCE_FIELDS = ["status", "source_label", "source_license", "source_date"]
STREET_ROLES = ("road_express", "road_main", "road_district", "road_local", "road_pedestrian")


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
        missing = sorted(present - {c.value() for c in renderer.categories()})
        if missing:
            raise ValueError(f"роль {self.role.key}: категорий {', '.join(map(str, missing))} нет в стиле верстки "
                             f"({renderer.classAttribute()}): объекты не нарисовались бы; категория — в map.yaml и .qml верстки")
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
    data = role.level != "basemap" or role.key in STREET_ROLES  # улицы — для вырезки из зон
    request = QgsFeatureRequest().setFilterRect(rect).setFlags(QgsFeatureRequest.Flag.ExactIntersect)
    names = layer.fields().names()
    if role.contour is not None:
        shapely.prepare(role.contour)
        # Точки — внутри контура, линии и площади — пересекающие его.
        inside = role.contour.covers if spec_file.role_spec(std, role.key)["geometry"] == "point" \
            else role.contour.intersects
    found = []
    for f in layer.getFeatures(request):
        geom = shapely.from_wkb(bytes(f.geometry().asWkb())) if data or role.contour is not None else None
        if role.contour is not None and not inside(geom):
            continue
        # Столбец, которого нет ни у одного объекта слоя (source_license у официальных), в файле не пишется.
        row = {k: None for k in SOURCE_FIELDS} | {k: value(f[k]) for k in names}
        if data:
            row["geom"] = geom
        found.append((f.id(), row))
    found.sort(key=lambda item: item[0])
    ids = ",".join(str(fid) for fid, _ in found) or "-1"
    layer.setSubsetString(f"({role.where}) AND fid IN ({ids})" if role.where else f"fid IN ({ids})")
    spec = spec_file.role_spec(std, role.key)
    if role.level == "basemap" and "label" in spec:
        layer = basemap_copy(layer, spec["label"]["field"])
    return Drawn(role, spec, layer, [row for _, row in found])


def basemap_copy(layer: QgsVectorLayer, field: str) -> QgsVectorLayer:
    """Слой подложки с подписями — копия в памяти, подпись по TEXT.md (labels.name): «Четвёртая линия» →
    «Четвертая линия». Стиль и подписи — тот же qml; геопакет не меняется."""
    copy = layer.materialize(QgsFeatureRequest())
    index = copy.fields().indexOf(field)
    changes = {}
    for f in copy.getFeatures():
        text = value(f[field])
        if text is not None and label_rules.name(str(text)) != text:
            changes[f.id()] = {index: label_rules.name(str(text))}
    if changes and not copy.dataProvider().changeAttributeValues(changes):
        raise RuntimeError(f"{layer.name()}: подписи подложки не записаны в копию слоя")
    copy.setName(layer.name())
    copy.setRenderer(layer.renderer().clone())
    copy.setLabeling(QgsVectorLayerSimpleLabeling(layer.labeling().settings()))
    copy.setLabelsEnabled(layer.labelsEnabled())
    return copy


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


def caption(basemap: list[Drawn]) -> str:
    """Подпись карты (source в provenance): по записи на поставщика и лицензию — «OpenStreetMap через Overture Maps
    2026-09-23.0, ODbL 1.0». Слои подложки перечислены в provenance (layers), в подписи их нет: подпись под картой
    сравнения шириной в три колонки не вмещала перечень (К-036). Подпись источника, которая продолжает другую через
    запятую («…2026-09-23.0, кварталы по улицам»), — тот же поставщик."""
    found = [(row["source_label"] or "", row["source_license"] or "") for d in basemap for row in d.rows]
    labels = set(found)
    groups: list[tuple[str, str]] = []
    for label, lic in found:
        bases = [b for b, other in labels if other == lic and label.startswith(b + ", ")]
        key = (min(bases, key=len) if bases else label, lic)
        if key not in groups:
            groups.append(key)
    return "; ".join(", ".join(x for x in key if x) for key in groups)


def sources(rows: list[dict]) -> list[str]:
    """Подписи источников объектов в порядке появления: «подпись, лицензия»."""
    out = []
    for row in rows:
        text = overlay.source_text(row)
        if text not in out:
            out.append(text)
    return out


def layer_entry(d: Drawn) -> dict:
    """Слой на карте: строка легенды, источники, самый слабый статус и самая поздняя дата объектов в охвате."""
    return {"name": d.role.title, "source": "; ".join(sources(d.rows)),
            "status": overlay.weakest(row["status"] for row in d.rows), "date": max(row["source_date"] for row in d.rows)}


def blockers(layout: QgsPrintLayout, rects: list) -> None:
    """Места, которые подписи подложки обходят (мм места = мм компоновки: карта в углу страницы): прямоугольники
    без краски — label blocking items карты, как у верстки (cm_deck/qgis_render.py: blockers)."""
    item = layout.itemById("map")
    for x0, y0, x1, y1 in rects:
        shape = QgsLayoutItemShape(layout)
        shape.setShapeType(QgsLayoutItemShape.Rectangle)
        shape.attemptMove(QgsLayoutPoint(x0, y0, Qgis.LayoutUnit.Millimeters))
        shape.attemptResize(QgsLayoutSize(max(x1 - x0, 0.1), max(y1 - y0, 0.1), Qgis.LayoutUnit.Millimeters))
        shape.setSymbol(QgsFillSymbol.createSimple({"style": "no", "outline_style": "no"}))
        layout.addLayoutItem(shape)
        item.addLabelBlockingItem(shape)


def street_cut(std: dict, drawn: list[Drawn]):
    """Полосы улиц подложки в системе проекта: ось ± полуширина класса (rules/fetch.yaml: blocks.half_width_m — те же,
    что у кварталов подложки); класса нет в правилах — ошибка."""
    widths = yaml.safe_load(FETCH_RULES.read_text(encoding="utf-8"))["blocks"]["half_width_m"]
    strips = []
    for d in drawn:
        if d.role.key not in STREET_ROLES:
            continue
        for row in d.rows:
            if row["road_class"] not in widths:
                raise ValueError(f"улица {row['object_id']}: класса {row['road_class']} нет в {FETCH_RULES.name}: "
                                 f"blocks.half_width_m — ширина полосы для вырезки из зон")
            strips.append(row["geom"].buffer(widths[row["road_class"]], cap_style="flat"))
    return shapely.union_all(strips) if strips else None


def basemap_labels(item, rect: QgsRectangle, spec: Spec, dpi: int) -> list[tuple[float, float, float, float]]:
    """Рамки подписей подложки (улицы, вода, зелень) в мм места: тот же движок подписей QGIS с теми же настройками
    карты (и местами blockers), что у экспорта; подписи overlay их не задевают."""
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
    """Описание подложки по контракту image 1.1.0: слои, источник (caption), дата и статус — только подложки. Статус —
    самый слабый из слоев подложки; статуса нет в image: status — ошибка."""
    layers = [layer_entry(d) for d in basemap]
    status = overlay.weakest(entry["status"] for entry in layers)
    if status not in image_schema()["properties"]["status"]["enum"]:
        raise ValueError(f"{spec.path}: статус подложки {status}, в контракте image: status его нет — provenance "
                         f"не выпускается (в подложке: {', '.join(e['name'] for e in layers if e['status'] == status)})")
    doc = {"file": spec.out.name, "kind": "map"}
    if spec.title:
        doc["title"] = spec.title
    doc |= {"source": caption(basemap), "date": max(entry["date"] for entry in layers), "status": status,
            "size_mm": {"width": spec.size_mm[0], "height": spec.size_mm[1]}, "dpi": std["export"]["dpi"],
            "crs": spec.project.crs_name, "scale": f"1:{spec.scale}", "layers": layers}  # image 1.1.0: ^1:[0-9]+$
    if "overlay_file" in image_schema()["properties"]:  # image 1.1.0: ссылка подложки на overlay.json рядом
        doc["overlay_file"] = spec.overlay_out.name
    if spec.fact_ids:
        doc["fact_ids"] = spec.fact_ids
    errors = [e.message for e in jsonschema.Draft202012Validator(image_schema()).iter_errors(doc)]
    if errors:
        raise ValueError(f"{spec.provenance_out.name}: не проходит контракт image:\n  " + "\n  ".join(errors))
    return doc


@cache
def image_schema() -> dict:
    return json.loads(paths.schema("image").read_text(encoding="utf-8"))


def frame(spec: Spec) -> QgsRectangle:
    """Охват карты в системе координат проекта: центр, масштаб и размер места."""
    cx, cy = spec.center
    half_w, half_h = spec.size_mm[0] * spec.scale / 2000, spec.size_mm[1] * spec.scale / 2000
    return QgsRectangle(cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def neighbours(spec: Spec, std: dict) -> list[tuple[Spec, Drawn]]:
    """Роли уровней с legend: true, найденные на соседних картах сравнения (compare_with): тот же отбор по их охвату,
    без отрисовки. Пары (карта, роль) в порядке карт группы и стандарта."""
    out = []
    for other in spec.group:
        rect = frame(other)
        found = [vector(other, std, role, rect) for role in other.roles if std["levels"][role.level]["legend"]]
        out += [(other, d) for d in found if d.rows]
    return out


def group_entries(spec: Spec, drawn: list[Drawn], others: list[tuple[Spec, Drawn]]) -> dict:
    """Слои данных карты (layer_entry). У роли, найденной и на соседях по сравнению, источник — объединение
    источников роли по картам группы в порядке путей карт, затем объектов: строки у карт группы одни и те же
    (К-026: одна легенда на группу — и один перечень источников). sources — тот же перечень списком: по слою overlay
    на источник."""
    entries = {}
    for d in drawn:
        if d.role.level == "basemap":
            continue
        entry = layer_entry(d)
        same = [(s, o) for s, o in others if o.role.key == d.role.key]
        if same:
            found = []
            for _, o in sorted([(spec, d), *same], key=lambda pair: str(pair[0].path.resolve())):
                found += [text for text in sources(o.rows) if text not in found]
            entry |= {"source": "; ".join(found), "sources": found}
        else:
            entry["sources"] = sources(d.rows)
        entries[d.role.key] = entry
    return entries


def legend(spec: Spec, std: dict, drawn: list[Drawn], others: list[tuple[Spec, Drawn]]
           ) -> list[tuple[Role, dict, list[tuple[str, str]]]]:
    """Строки легенды: роли уровней с legend: true, нарисованные на этой карте или на соседней карте сравнения
    (compare_with, others — из neighbours), в порядке стандарта; у ролей с категориями — категории, встреченные на
    картах группы, в порядке стиля. Строки у карт группы одни и те же."""
    shown = [d for d in drawn if std["levels"][d.role.level]["legend"]] + [d for _, d in others]
    rows = []
    for role in spec.roles:
        mine = [d for d in shown if d.role.key == role.key]
        if not mine:
            continue
        values = {value for d in mine for value, _ in d.categories()}
        renderer = mine[0].layer.renderer()
        categories = [(c.value(), c.label()) for c in renderer.categories() if c.value() in values] if values else []
        rows.append((role, mine[0].spec, categories))
    return rows


def draw(spec: Spec, folder: Path) -> None:
    """Рендер в папку кеша: подложка PNG, provenance подложки, overlay.json."""
    start()
    std, style = tokens.load("map"), tokens.load("style")
    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem.fromWkt(spec.project.crs.to_wkt()))
    layout = template(project, paths.qgis_styles() / f"map_{spec.place}.qpt")
    item = layout.itemById("map")
    rect = frame(spec)

    drawn = []
    for role in spec.roles:
        d = vector(spec, std, role, rect)
        where = f" ({role.where})" if role.where else ""
        within = f" внутри {role.within}" if role.within else ""
        print(f"{spec.path.name}: {role.key} ← {role.layer}{where}{within}: в охвате {len(d.rows)}")
        if d.rows:
            drawn.append(d)
            project.addMapLayer(d.layer, False)
    check_print(spec, drawn)
    basemap = [d for d in drawn if d.role.level == "basemap"]
    if not basemap:  # MAPS.md, раздел 7 допускает карту без подложки, контракт overlay — нет (basemap обязателен)
        raise ValueError(f"{spec.path}: в охвате нет подложки, а overlay 1.1.0 требует basemap")
    north = False  # подложка есть, север вверху: стрелки нет (MAPS.md, раздел 7)
    if spec.north is not None and spec.north != north:
        raise ValueError(f"{spec.path}: north: {spec.north}, по правилу MAPS.md, раздел 7 — {north} "
                         f"(север вверху, подложка в охвате есть)")

    for element in ("scalebar", "north", "legend"):  # служебные знаки — в overlay
        if layout.itemById(element) is not None:
            layout.itemById(element).setVisibility(False)
    item.setLayers([d.layer for d in basemap])
    item.setExtent(rect)

    def raster(rects: list) -> list:
        """Подложка в PNG с подписями в обход rects (мм); рамки подписей подложки, мм."""
        blockers(layout, rects)
        layout.refresh()
        export_png(layout, folder / spec.out.name, std["export"]["dpi"])
        return basemap_labels(item, rect, spec, std["export"]["dpi"])

    doc = provenance(spec, std, style, basemap)
    (folder / spec.provenance_out.name).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
    others = neighbours(spec, std)
    entries = group_entries(spec, drawn, others)
    bounds = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
    data, unplaced = overlay.build(spec, std, style, drawn, legend(spec, std, drawn, others), entries, north, layout,
                                   bounds, spec.out.name, raster, street_cut(std, drawn))
    for text in unplaced:
        print(f"{spec.path.name}: подпись не встала без наездов: {text}")
    (folder / spec.overlay_out.name).write_text(overlay.dumps(data), encoding="utf-8")
    project.clear()


def cache_key(spec: Spec) -> str:
    """Хэш всего, от чего зависит результат: описание, анкета, геопакет (и у соседних карт сравнения — их легенда
    зависит от соседей), токены и стили верстки, контракт, код."""
    digest = hashlib.sha256()
    design = [paths.design() / "tokens" / "map.yaml", paths.design() / "tokens" / "style.yaml",
              *sorted(p for p in paths.qgis_styles().iterdir() if p.is_file())]
    inputs = [path for s in [spec, *spec.group] for path in (s.path, s.project.root / "project.yaml", s.data)]
    for path in [*inputs, *design, paths.schema("image"), paths.schema("overlay"), *CODE]:
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
