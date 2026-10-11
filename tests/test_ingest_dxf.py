"""ingest DXF: правила раскладки, починка контуров, номера и ключи, мусорные слои, повтор байт в байт."""

import math

import pytest
from shapely.geometry import Point, box

from cm_carto import gpkg
from cm_carto.ingest import dxf, ids
from test_ingest_support import SAMPLES, ingest, label_at, make_dxf, origin, project_in, square, write_source


def run(tmp_path, draw):
    project = project_in(tmp_path)
    x, y = origin(project)
    file = make_dxf(tmp_path / "plan.dxf", lambda msp, doc: draw(msp, doc, x, y))
    out, report = ingest(project, file)
    return gpkg.read(out), report, (x, y)


def lines_ring(msp, pts, layer="Кварталы", gap=0.0):
    for i in range(4):
        p, q = pts[i], pts[(i + 1) % 4]
        if gap and i == 3:
            d = math.dist(p, q)
            q = (q[0] + (p[0] - q[0]) * gap / d, q[1] + (p[1] - q[1]) * gap / d)
        msp.add_line(p, q, dxfattribs={"layer": layer})


def test_gap_within_tolerance_is_closed(tmp_path):
    def draw(msp, doc, x, y):
        lines_ring(msp, square(x, y), gap=0.30)
        label_at(msp, "7", (x + 50, y + 50))
    frames, report, _ = run(tmp_path, draw)
    assert frames["blocks"].object_id.tolist() == ["blocks:7"]
    assert frames["blocks"].geometry.iloc[0].area == pytest.approx(10000, rel=1e-3)
    assert any("замкнут разрыв 0,30 м" in line for line in report.fixed)


def test_gap_beyond_tolerance_goes_to_report(tmp_path):
    def draw(msp, doc, x, y):
        lines_ring(msp, square(x, y), gap=2.0)              # разрыв 2 м при допуске 0,5 м
        msp.add_lwpolyline(square(x + 200, y), close=True, dxfattribs={"layer": "Кварталы"})
    frames, report, _ = run(tmp_path, draw)
    assert len(frames["blocks"]) == 1                      # распознан только замкнутый
    assert any("не замкнут, разрыв 2,00 м" in line for line in report.unrecognized)


def test_trash_layers_are_ignored(tmp_path):
    def draw(msp, doc, x, y):
        msp.add_lwpolyline(square(x, y), close=True, dxfattribs={"layer": "Кварталы"})
        msp.add_lwpolyline(square(x - 50, y - 50, 300), close=True, dxfattribs={"layer": "Рамка"})
        msp.add_lwpolyline(square(x, y, 10), close=True, dxfattribs={"layer": "Штамп"})
        msp.add_line((x, y), (x + 5, y), dxfattribs={"layer": "0"})
        msp.add_point((x, y), dxfattribs={"layer": "Defpoints"})
        msp.add_line((x, y), (x + 5, y), dxfattribs={"layer": "Прочее"})
    frames, report, _ = run(tmp_path, draw)
    assert set(frames) == {"blocks"} and len(frames["blocks"]) == 1
    assert {"«0»", "«Defpoints»", "«Рамка»", "«Штамп»"} <= {s.split(":")[0].split()[-1] for s in report.ignored}
    assert len(report.unrecognized) == 1 and "«Прочее»" in report.unrecognized[0]   # слой без правила — не молчок


def test_all_linework_types_make_one_contour(tmp_path):
    """Отрезки, полилиния и дуга вперемешку собираются в один контур."""
    def draw(msp, doc, x, y):
        msp.add_line((x, y), (x + 100, y), dxfattribs={"layer": "Кварталы"})
        msp.add_lwpolyline([(x + 100, y), (x + 100, y + 100)], dxfattribs={"layer": "Кварталы"})
        msp.add_polyline2d([(x + 100, y + 100), (x + 10, y + 100)], dxfattribs={"layer": "Кварталы"})
        msp.add_arc((x + 10, y + 90), 10, 90, 180, dxfattribs={"layer": "Кварталы"})   # скругленный угол
        msp.add_line((x, y + 90), (x, y), dxfattribs={"layer": "Кварталы"})
    frames, report, _ = run(tmp_path, draw)
    block = frames["blocks"].geometry.iloc[0]
    assert len(frames["blocks"]) == 1
    assert block.area == pytest.approx(100 * 100 - (100 - math.pi * 100 / 4), rel=2e-3)
    assert not report.fixed or all("меньше 1 см" in line for line in report.fixed)


def test_self_intersection_is_fixed(tmp_path):
    def draw(msp, doc, x, y):
        msp.add_lwpolyline([(x, y), (x + 100, y + 100), (x + 100, y), (x, y + 100)], close=True,
                           dxfattribs={"layer": "Зоны_размещения_ОКС"})      # восьмерка
        label_at(msp, "4", (x + 85, y + 50), "Номера_зон")
    frames, report, _ = run(tmp_path, draw)
    site = frames["sites"].geometry.iloc[0]
    assert frames["sites"].object_id.tolist() == ["sites:4"]
    assert site.is_valid and len(site.geoms) == 2
    assert any("самопересечение исправлено" in line for line in report.fixed)
    assert any("сверить с чертежом" in line for line in report.confirm)


def test_number_from_text_and_geometry_key(tmp_path):
    def draw(msp, doc, x, y):
        msp.add_lwpolyline(square(x, y), close=True, dxfattribs={"layer": "Кварталы"})
        label_at(msp, "12а", (x + 50, y + 50))
        msp.add_lwpolyline(square(x + 200, y), close=True, dxfattribs={"layer": "Кварталы"})      # без номера
        msp.add_lwpolyline(square(x + 400, y), close=True, dxfattribs={"layer": "Кварталы"})      # два номера
        label_at(msp, "3", (x + 430, y + 50))
        label_at(msp, "4", (x + 470, y + 50))
        label_at(msp, "Проезд", (x + 450, y + 80))                                                  # не номер
    frames, report, _ = run(tmp_path, draw)
    blocks = frames["blocks"].set_index("object_id")
    assert "blocks:12a" in blocks.index and blocks.loc["blocks:12a", "num"] == "12а"
    hashed = [i for i in blocks.index if i != "blocks:12a"]
    assert len(hashed) == 2 and all(i.startswith("blocks:g") and len(i) == len("blocks:g") + 12 for i in hashed)
    assert any("нет номера" in line for line in report.confirm)
    assert any("несколько номеров (3, 4)" in line for line in report.confirm)
    assert any("«Проезд»" in line and "не номер" in line for line in report.unrecognized)


def test_geometry_key_rounds_to_one_centimeter():
    base = box(100.0, 200.0, 150.0, 260.0)
    assert ids.geometry_key(base) == ids.geometry_key(box(100.001, 200.0, 150.0, 260.0))
    assert ids.geometry_key(base) != ids.geometry_key(box(100.02, 200.0, 150.0, 260.0))
    assert ids.geometry_key(base) == ids.geometry_key(box(150.0, 260.0, 100.0, 200.0))     # порядок вершин не важен


def test_block_insert_with_layer_zero_and_rotation(tmp_path):
    def draw(msp, doc, x, y):
        block = doc.blocks.new("ТИП")
        block.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True, dxfattribs={"layer": "0"})
        msp.add_blockref("ТИП", (x, y), dxfattribs={"layer": "Кварталы", "rotation": 30})
    frames, _, (x, y) = run(tmp_path, draw)
    geom = frames["blocks"].geometry.iloc[0]
    assert geom.area == pytest.approx(6000, rel=1e-6)
    assert geom.contains(Point(x + 50 * math.cos(math.radians(30)) - 30 * math.sin(math.radians(30)),
                               y + 50 * math.sin(math.radians(30)) + 30 * math.cos(math.radians(30))))


def test_red_lines_state_from_layer_and_merge(tmp_path):
    def draw(msp, doc, x, y):
        msp.add_line((x, y), (x + 100, y), dxfattribs={"layer": "Красные_линии_существующие"})
        msp.add_lwpolyline([(x + 100.2, y), (x + 100, y + 100)], dxfattribs={"layer": "Красные_линии_существующие"})
        msp.add_line((x, y + 300), (x + 100, y + 300), dxfattribs={"layer": "Красные_линии_устанавливаемые"})
    frames, report, _ = run(tmp_path, draw)
    red = frames["red_lines"].set_index("state")
    assert len(red.loc[["existing"]]) == 1 and len(red.loc[["planned"]]) == 1    # отрезок и полилиния с разрывом 0,2 м — одна линия
    assert red.loc["existing"].geometry.geom_type == "MultiLineString" and len(red.loc["existing"].geometry.geoms) == 1
    assert any("замкнут разрыв 0,20 м" in line for line in report.fixed)


def test_drawing_outside_project_extent_fails(tmp_path):
    project = project_in(tmp_path)
    file = make_dxf(tmp_path / "local.dxf", lambda msp, doc: msp.add_lwpolyline(square(0, 0), close=True, dxfattribs={"layer": "Кварталы"}))
    with pytest.raises(ValueError, match="вне охвата проекта"):
        ingest(project, file)


def test_missing_source_passport_fails(tmp_path):
    project = project_in(tmp_path)
    file = make_dxf(tmp_path / "plan.dxf", lambda msp, doc: None)
    (tmp_path / "plan.dxf.source.yaml").unlink()
    with pytest.raises(FileNotFoundError, match="нет паспорта источника"):
        ingest(project, file)


def test_nothing_recognized_fails_with_report(tmp_path):
    project = project_in(tmp_path)
    file = make_dxf(tmp_path / "plan.dxf", lambda msp, doc: msp.add_line((0, 0), (1, 1), dxfattribs={"layer": "Прочее"}))
    with pytest.raises(ValueError, match="ничего не распознано"):
        ingest(project, file)
    assert "нет правила раскладки" in (tmp_path / "geo/parts/ingest-plan.report.md").read_text(encoding="utf-8")


def test_sample_dxf(tmp_path):
    """Образец ППТ: что распознано, что исправлено, повтор дает тот же файл, статусы и источники на месте."""
    project = project_in(tmp_path)
    file = SAMPLES / "dxf" / "ppt-sample.dxf"
    out, report = ingest(project, file)
    first = out.read_bytes()
    out, again = ingest(project, file)
    assert out.read_bytes() == first
    assert again.changes == ["исчезнувших нет", "новых нет"]
    assert gpkg.check(out) == []
    frames = gpkg.read(out)
    assert {k: len(v) for k, v in frames.items()} == {"boundary_planning": 1, "red_lines": 18, "blocks": 15, "sites": 6}
    assert set(frames["blocks"].num) >= {"1", "8а", "11", "15"}
    for frame in frames.values():
        assert set(frame.status) == {"official"} and frame.source_label.str.startswith("синтетический образец").all()
        assert (frame.source_date == "2026-10-11").all()
    assert any("замкнут разрыв 0,30 м" in line for line in report.fixed)
    assert any("самопересечение" in line for line in report.fixed)
    assert [line.split(":")[0] for line in report.unrecognized] == ["слой «Инженерные_сети»"]


def test_report_lists_vanished_and_new_ids(tmp_path):
    project = project_in(tmp_path)
    x, y = origin(project)
    file = tmp_path / "plan.dxf"
    make_dxf(file, lambda msp, doc: (msp.add_lwpolyline(square(x, y), close=True, dxfattribs={"layer": "Кварталы"}),
                                     label_at(msp, "1", (x + 50, y + 50))))
    ingest(project, file)
    make_dxf(file, lambda msp, doc: (msp.add_lwpolyline(square(x, y), close=True, dxfattribs={"layer": "Кварталы"}),
                                     label_at(msp, "2", (x + 50, y + 50))))
    _, report = ingest(project, file)
    assert report.changes == ["исчезли (1): blocks:1", "новые (1): blocks:2"]


def test_same_number_in_two_contours_makes_one_object(tmp_path):
    def draw(msp, doc, x, y):
        for dx in (0, 300):
            msp.add_lwpolyline(square(x + dx, y), close=True, dxfattribs={"layer": "Кварталы"})
            label_at(msp, "5", (x + dx + 50, y + 50))
    frames, report, _ = run(tmp_path, draw)
    assert frames["blocks"].object_id.tolist() == ["blocks:5"]
    assert len(frames["blocks"].geometry.iloc[0].geoms) == 2
    assert any("2 контура с ключом 5 объединены" in line for line in report.fixed)


def test_empty_source_label_is_refused(tmp_path):
    project = project_in(tmp_path)
    x, y = origin(project)
    file = make_dxf(tmp_path / "plan.dxf", lambda msp, doc: msp.add_lwpolyline(square(x, y), close=True, dxfattribs={"layer": "Кварталы"}))
    write_source(file, label="")
    with pytest.raises(ValueError, match="source_label"):
        ingest(project, file)
