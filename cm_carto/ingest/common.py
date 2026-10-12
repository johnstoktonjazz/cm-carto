"""Общее для импорта: паспорт источника, строка объекта, запись части геопакета с отчетом."""

import re
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import yaml

from cm_carto import gpkg
from cm_carto.ingest.report import Report
from cm_carto.project import Project

SOURCE_KEYS = {"rules", "label", "date", "status", "license", "doc", "digitized_from"}
# Статус объектов DXF, SHP, GPKG задает паспорт источника, а не правила раскладки. cadastral — природа выписки ЕГРН
# (правила egrn), calc — расчет картографа: из паспорта ни тот, ни другой не берутся.
SOURCE_STATUSES = ("official", "concept", "digitized", "reference")


@dataclass(frozen=True)
class Source:
    """Паспорт документа: подпись, дата, статус, лицензия — то, чего нет в самом файле."""
    rules: str | None
    label: str
    date: str
    status: str
    license: str | None = None
    doc: str | None = None
    digitized_from: str | None = None


def source_path(file: Path) -> Path:
    return file.with_name(file.name + ".source.yaml")


def load_source(file: Path) -> Source:
    path = source_path(file)
    if not path.exists():
        raise FileNotFoundError(f"{file.name}: нет паспорта источника {path.name} (rules, label, date, status)")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    unknown = set(data) - SOURCE_KEYS
    missing = {"rules", "label", "date", "status"} - set(data)
    if unknown or missing:
        raise ValueError(f"{path}: лишние поля {sorted(unknown)}, нет полей {sorted(missing)}")
    date = str(data["date"])
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}(-[0-9]{2})?", date):
        raise ValueError(f"{path}: date «{date}» — нужна ГГГГ-ММ-ДД или ГГГГ-ММ")
    status = data["status"]
    if status not in SOURCE_STATUSES:
        raise ValueError(f"{path}: status «{status}» — нужен один из {', '.join(SOURCE_STATUSES)}")
    if status == "official" and not data.get("doc"):
        raise ValueError(f"{path}: official — только утвержденный документ: в паспорте нет doc "
                         f"(рабочий, неутвержденный чертеж — status: concept)")
    if status == "digitized" and not data.get("digitized_from"):
        raise ValueError(f"{path}: digitized требует digitized_from (с какого документа или слайда, кем и когда)")
    if status == "reference" and not data.get("license"):
        raise ValueError(f"{path}: reference требует license (лицензия источника, source_license в геопакете)")
    return Source(data["rules"], data["label"], date, status, data.get("license"), data.get("doc"), data.get("digitized_from"))


def row(layer: str, key: str, geom, source: Source, status: str, kind: str, name: str | None = None, **attrs) -> dict:
    out = {"object_id": f"{layer}:{key}", "kind": kind, "status": status, "source_label": source.label,
           "source_date": source.date, "fact_ids": [], "geometry": geom, **attrs}
    for field, value in (("name", name), ("source_license", source.license), ("source_doc", source.doc),
                         ("digitized_from", source.digitized_from)):
        if value:
            out[field] = value
    return out


def frames_of(rows: dict[str, list[dict]], project: Project) -> dict[str, gpd.GeoDataFrame]:
    return {layer: gpd.GeoDataFrame(items, geometry="geometry", crs=project.crs)
            for layer, items in rows.items() if items}


def write_part(project: Project, name: str, frames: dict[str, gpd.GeoDataFrame], report: Report,
               inputs: list[Path]) -> Path:
    """Часть геопакета ingest-<имя>.gpkg и отчет рядом; объекты сверяются с прошлой сборкой этого входа."""
    out = project.part(f"ingest-{name}")
    before = {i for f in gpkg.read(out).values() for i in f["object_id"]} if out.exists() else None
    report_path = out.with_suffix(".report.md")
    report.compare(before, {i for f in frames.values() for i in f["object_id"]})
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.render(), encoding="utf-8")
    if not frames:
        raise ValueError(f"{name}: ничего не распознано, причины — в {report_path}")
    gpkg.write(out, frames, project.name, project.crs, project.crs_name, gpkg.input_hash(*inputs, project.crs_name))
    return out
