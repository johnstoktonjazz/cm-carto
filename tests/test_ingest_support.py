"""Общее для тестов ingest: проект во временной папке, сборка DXF и паспорта источника. Тестов здесь нет."""

import dataclasses
from pathlib import Path

import ezdxf
import yaml

from cm_carto import project as project_mod
from cm_carto.ingest import cli

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "input" / "taganrog"
EXAMPLE = ROOT / "examples" / "taganrog" / "project.yaml"


def project_in(tmp_path: Path) -> project_mod.Project:
    """Анкета примера, геопакет — во временной папке."""
    return dataclasses.replace(project_mod.load(EXAMPLE), root=tmp_path)


def origin(project) -> tuple[float, float]:
    """Точка в охвате проекта, в его системе координат: от нее строятся тестовые чертежи и выписки."""
    xmin, ymin, xmax, ymax = project.extent
    return (xmin + xmax) / 2, (ymin + ymax) / 2


def square(x, y, size=100.0):
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]


def write_source(path: Path, rules="ppt", label="тестовый образец", date="2026-10-11", **extra) -> None:
    data = {"rules": rules, "label": label, "date": date, **extra}
    (path.parent / (path.name + ".source.yaml")).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def make_dxf(path: Path, draw) -> Path:
    """DXF с типовыми слоями ППТ; draw(msp) рисует содержимое."""
    ezdxf.options.write_fixed_meta_data_for_testing = True
    doc = ezdxf.new("R2010")
    for name in ["Граница_ППТ", "Красные_линии_существующие", "Красные_линии_устанавливаемые", "Кварталы",
                 "Номера_кварталов", "Зоны_размещения_ОКС", "Номера_зон", "Рамка", "Штамп", "Размеры", "Defpoints",
                 "Прочее"]:
        if name not in doc.layers:
            doc.layers.add(name)
    draw(doc.modelspace(), doc)
    doc.saveas(path)
    write_source(path)
    return path


def label_at(msp, text, point, layer="Номера_кварталов"):
    t = msp.add_text(text, height=5, dxfattribs={"layer": layer})
    t.set_placement(point, align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)


def ingest(project, file: Path):
    out, report = cli.ingest_file(project, file)
    return out, report
