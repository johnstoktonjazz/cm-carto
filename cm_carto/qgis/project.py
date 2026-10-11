"""Проект QGIS (.qgz) для проверки карты глазами: те же слои и стили, что у `render`, без обрезки по охвату.

    project.build(описание.yaml) → <out карты>.qgz рядом с картой

Слои — роли описания в порядке стандарта (сверху вниз): слой геопакета проекта с отбором `where`, `within` и стилем
верстки `design/qgis/<роль>.qml`; компоновка места `map_<место>.qpt` с охватом карты. Геопакет открыт только на чтение
(Э-005). Проект — инструмент проверки: правки в нем в выпуск не идут, выпуск собирает `render` из описания.
"""

from pathlib import Path

from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsProject, QgsReferencedRectangle, QgsRectangle,
                       QgsVectorLayer)

from cm_carto import paths, tokens
from cm_carto.render import build, spec as spec_file

EVERYWHERE = QgsRectangle(-1e9, -1e9, 1e9, 1e9)


def linked(spec, role, copy: QgsVectorLayer, subset: str) -> QgsVectorLayer:
    """Подложка в проекте — ссылка на слой геопакета (а не копия в памяти, которая после открытия .qgz пуста): тот же
    тот же итоговый отбор объектов (`subset` исходного слоя рендера), стиль и подпись. Подпись — выражение QGIS: замена «ё» на «е»;
    остальные правила `labels.name` (кавычки, «№», пробелы) выражением не повторяются, на подложке они видны только
    в рендере."""
    options = QgsVectorLayer.LayerOptions(QgsProject.instance().transformContext())
    options.forceReadOnly = True
    layer = QgsVectorLayer(f"{spec.data}|layername={role.layer}", copy.name(), "ogr", options)
    layer.setSubsetString(subset)
    layer.setRenderer(copy.renderer().clone())
    labeling = copy.labeling()
    if labeling is not None:
        settings = labeling.settings()
        field = settings.fieldName
        settings.fieldName = f"replace(replace(\"{field}\", 'ё', 'е'), 'Ё', 'Е')"
        settings.isExpression = True
        layer.setLabeling(type(labeling)(settings))
        layer.setLabelsEnabled(copy.labelsEnabled())
    return layer


def build_project(path: Path, out: Path | None = None) -> Path:
    spec = spec_file.load(path)
    build.start()
    std = tokens.load("map")
    project = QgsProject.instance()
    project.clear()
    crs = QgsCoordinateReferenceSystem.fromWkt(spec.project.crs.to_wkt())
    project.setCrs(crs)
    project.setTitle(spec.title or spec.path.stem)
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    layers = []
    for role in spec.roles:
        subsets = []  # итоговый отбор исходного слоя: рендер подменяет его копией в памяти (fid копии другие)
        original = build.basemap_copy
        build.basemap_copy = lambda src, field: subsets.append(src.subsetString()) or original(src, field)
        try:
            layer = build.vector(spec, std, role, EVERYWHERE).layer
        finally:
            build.basemap_copy = original
        if layer.providerType() == "memory":
            layer = linked(spec, role, layer, subsets[0])
        layer.setReadOnly(True)  # в проекте слой не редактируется: правки в QGIS в выпуск не идут
        project.addMapLayer(layer, False)
        project.layerTreeRoot().addLayer(layer)
        layers.append(layer)
    rect = build.frame(spec)
    project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(rect, crs))
    layout = build.template(project, paths.qgis_styles() / f"map_{spec.place}.qpt")
    layout.setName(spec.out.stem)
    item = layout.itemById("map")
    item.setLayers(layers)
    item.setExtent(rect)
    project.layoutManager().addLayout(layout)
    target = Path(out) if out else spec.out.with_suffix(".qgz")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not project.write(str(target)):
        raise RuntimeError(f"{target}: проект не записан")
    project.clear()
    return target
