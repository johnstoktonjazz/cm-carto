#!/usr/bin/env python
"""Синтетические образцы для ingest (DECISIONS.md, К-007): DXF проекта планировки и КПТ в XML.

    scripts/run.sh python scripts/make_samples.py

Геометрия привязана к квартальной сетке исторического центра полигона: две семьи улиц под углом около 73 градусов
(вдоль берега на юго-восток и поперек на северо-восток), перекрестки сняты с улично-дорожной сети Overture с точностью
до десятков метров. Все данные вымышлены: кадастровые номера 00:00:0000000:N, правообладателей нет. Повторный запуск
дает те же байты: случайность только из зерна, метаданные ezdxf зафиксированы.
"""

import math
import random
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf
import yaml
from ezdxf.enums import TextEntityAlignment
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "input" / "taganrog"
DATE = "2026-10-11"
SEED = 20261011

# Локальная косая система: a — вдоль улиц на юго-восток (азимут 133), b — поперек на северо-восток (азимут 60,5).
ORIGIN = (494890.0, 5228857.0)
U = (math.sin(math.radians(133)), math.cos(math.radians(133)))
V = (math.sin(math.radians(60.5)), math.cos(math.radians(60.5)))
TEXT_ROTATION = 90 - 133  # текст вдоль улицы a, град против часовой от востока

A_LINES = [-111, 0, 115, 230, 345, 460, 575]   # оси улиц, пересекающих сетку; колонка 5 вне границы ППТ
B_LINES = [-350, -168, 0, 180]                 # оси улиц вдоль сетки; ряды 0..2 с юго-запада на северо-восток
HALF_STREET = 9.0
WIDENING = 3.0                                 # устанавливаемая красная линия сужает квартал колонки 4 на 3 м
PPT_COLUMNS = 5
BLOCK_NUMS = {8: "8а"}                         # остальные — просто номер

SKETCH = "синтетический образец"


def world(geom):
    return affinity.affine_transform(geom, [U[0], V[0], U[1], V[1], ORIGIN[0], ORIGIN[1]])


def world_xy(a, b):
    return ORIGIN[0] + a * U[0] + b * V[0], ORIGIN[1] + a * U[1] + b * V[1]


def block_num(col: int, row: int) -> int:
    return (2 - row) * PPT_COLUMNS + col + 1


def make_blocks(rnd: random.Random) -> dict[tuple[int, int], dict]:
    """Кварталы колонок 0..5, рядов 0..2: локальные контуры, номера, контур существующей красной линии."""
    blocks = {}
    for row in range(3):
        for col in range(6):
            a0, a1 = A_LINES[col] + HALF_STREET, A_LINES[col + 1] - HALF_STREET
            b0, b1 = B_LINES[row] + HALF_STREET, B_LINES[row + 1] - HALF_STREET
            jit = lambda: round(rnd.uniform(-2.5, 2.5), 2)
            corners = [(a0 + jit(), b0 + jit()), (a1 + jit(), b0 + jit()), (a1 + jit(), b1 + jit()), (a0 + jit(), b1 + jit())]
            old = list(corners)
            if col == PPT_COLUMNS - 1:
                corners[1] = (corners[1][0] - WIDENING, corners[1][1])
                corners[2] = (corners[2][0] - WIDENING, corners[2][1])
            num = block_num(col, row) if col < PPT_COLUMNS else None
            if num == 8:
                x, y = corners[2]
                corners[2:3] = [(x, y - 12), (x - 12, y)]
            blocks[(col, row)] = {"num": BLOCK_NUMS.get(num, str(num)) if num else None, "index": num,
                                  "ring": corners, "old_ring": old}
    return blocks


def shrunk(ring, margin):
    return Polygon(ring).buffer(-margin, join_style=2)


def local_rect(block, ma=20.0, mb=25.0):
    xs = [p[0] for p in block["ring"]]
    ys = [p[1] for p in block["ring"]]
    return box(min(xs) + ma, min(ys) + mb, max(xs) - ma, max(ys) - mb)


def half(poly, axis, lo, hi):
    """Часть полигона между значениями координаты lo и hi вдоль оси (0 — a, 1 — b)."""
    big = 10000
    clip = box(lo, -big, hi, big) if axis == 0 else box(-big, lo, big, hi)
    return poly.intersection(clip)


def parcels_for(blocks) -> list[dict]:
    """Участки КПТ: локальные геометрии (без номеров — они по порядку)."""
    out = []

    def add(col, row, geom, use):
        out.append({"geom": geom, "use": use, "block": blocks[(col, row)]["index"] or 0})

    def poly_of(col, row):
        return shrunk(blocks[(col, row)]["ring"], 2.0)

    uses = ["Для индивидуального жилищного строительства", "Среднеэтажная жилая застройка", "Магазины",
            "Общественное питание", "Здравоохранение", "Культурное развитие", "Бытовое обслуживание",
            "Хранение автотранспорта", "Деловое управление", "Образование и просвещение"]
    k = 0

    def use():
        nonlocal k
        k += 1
        return uses[k % len(uses)]

    for col, row in [(1, 2), (0, 1), (1, 1), (4, 1), (2, 0), (3, 0), (4, 0)]:   # по одному участку на квартал
        add(col, row, poly_of(col, row), use())
    for col, row in [(2, 2), (2, 1)]:                                           # квартал делится на два участка
        p = poly_of(col, row)
        xs = p.bounds
        mid = (xs[1] + xs[3]) / 2
        add(col, row, half(p, 1, xs[1] - 1, mid - 0.5), use())
        add(col, row, half(p, 1, mid + 0.5, xs[3] + 1), use())
    p = poly_of(3, 1)                                                           # участок с внутренним двором (дыра)
    cx, cy = p.centroid.x, p.centroid.y
    add(3, 1, p.difference(box(cx - 12, cy - 9, cx + 12, cy + 9)), use())
    for col in (5,):                                                            # вне границы ППТ, колонка 5
        for row in (0, 1, 2):
            add(col, row, poly_of(col, row), use())
    return out


def two_contour_parcel(blocks):
    """Участок из двух контуров в одном квартале (контур разрезан проездом)."""
    p = shrunk(blocks[(0, 2)]["ring"], 2.0)
    b = p.bounds
    mid = (b[0] + b[2]) / 2
    return {"geom": MultiPolygon([half(p, 0, b[0] - 1, mid - 25), half(p, 0, mid + 25, b[2] + 1)]),
            "use": "Для индивидуального жилищного строительства", "block": blocks[(0, 2)]["index"]}


# ---------- DXF ----------

def lw(msp, pts, layer, close=True):
    return msp.add_lwpolyline([(round(x, 3), round(y, 3)) for x, y in pts], close=close, dxfattribs={"layer": layer})


def lines(msp, pts, layer, closed=True, gap=0.0):
    """Контур отрезками LINE; gap — на сколько последний отрезок не доходит до первой точки (разрыв чертежа)."""
    n = len(pts)
    for i in range(n if closed else n - 1):
        p, q = pts[i], pts[(i + 1) % n]
        if gap and i == n - 1:
            d = math.dist(p, q)
            q = (q[0] + (p[0] - q[0]) * gap / d, q[1] + (p[1] - q[1]) * gap / d)
        msp.add_line((round(p[0], 3), round(p[1], 3)), (round(q[0], 3), round(q[1], 3)), dxfattribs={"layer": layer})


def polyline_old(msp, pts, layer, close=True):
    return msp.add_polyline2d([(round(x, 3), round(y, 3)) for x, y in pts], close=close, dxfattribs={"layer": layer})


def text(msp, s, p, height, layer, rotation=TEXT_ROTATION):
    t = msp.add_text(s, height=height, rotation=rotation, dxfattribs={"layer": layer})
    t.set_placement((round(p[0], 3), round(p[1], 3)), align=TextEntityAlignment.MIDDLE_CENTER)


def mtext(msp, s, p, height, layer):
    m = msp.add_mtext(s, dxfattribs={"layer": layer, "char_height": height, "rotation": TEXT_ROTATION,
                                     "attachment_point": 5})
    m.dxf.insert = (round(p[0], 3), round(p[1], 3))


def rounded_corner_arc(msp, ring, i, radius, layer):
    """Контур отрезками LINE со скругленным дугой ARC углом ring[i]."""
    n = len(ring)
    P = ring[i]
    prev, nxt = ring[i - 1], ring[(i + 1) % n]
    d1 = ((prev[0] - P[0]), (prev[1] - P[1]))
    d2 = ((nxt[0] - P[0]), (nxt[1] - P[1]))
    l1, l2 = math.hypot(*d1), math.hypot(*d2)
    d1, d2 = (d1[0] / l1, d1[1] / l1), (d2[0] / l2, d2[1] / l2)
    phi = math.acos(d1[0] * d2[0] + d1[1] * d2[1])
    t = radius / math.tan(phi / 2)
    t1 = (P[0] + d1[0] * t, P[1] + d1[1] * t)
    t2 = (P[0] + d2[0] * t, P[1] + d2[1] * t)
    bis = (d1[0] + d2[0], d1[1] + d2[1])
    lb = math.hypot(*bis)
    c = (P[0] + bis[0] / lb * radius / math.sin(phi / 2), P[1] + bis[1] / lb * radius / math.sin(phi / 2))
    a1 = math.degrees(math.atan2(t1[1] - c[1], t1[0] - c[0])) % 360
    a2 = math.degrees(math.atan2(t2[1] - c[1], t2[0] - c[0])) % 360
    start, end = (a1, a2) if (a2 - a1) % 360 < 180 else (a2, a1)
    msp.add_arc((round(c[0], 3), round(c[1], 3)), round(radius, 3), round(start, 6), round(end, 6),
                dxfattribs={"layer": layer})
    others = [t2] + [ring[(i + 1 + k) % n] for k in range(0, n - 1)] + [t1]
    for p, q in zip(others, others[1:]):
        msp.add_line((round(p[0], 3), round(p[1], 3)), (round(q[0], 3), round(q[1], 3)), dxfattribs={"layer": layer})


def make_dxf(blocks, rnd) -> Path:
    ezdxf.options.write_fixed_meta_data_for_testing = True
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.M
    msp = doc.modelspace()
    names = ["Граница_ППТ", "Красные_линии_существующие", "Красные_линии_устанавливаемые", "Кварталы",
             "Номера_кварталов", "Зоны_размещения_ОКС", "Номера_зон", "Рамка", "Штамп", "Размеры", "Defpoints",
             "Знаки", "Подписи_улиц", "Инженерные_сети"]
    for i, name in enumerate(names):
        if name not in doc.layers:
            doc.layers.add(name, color=(i % 7) + 1)

    # граница ППТ — по осям крайних улиц, угол срезан
    boundary = [(-111, -350), (115, -352), (460, -350), (460, 150), (430, 180), (170, 181), (-111, 180)]
    boundary = [(a + rnd.uniform(-3, 3), b + rnd.uniform(-3, 3)) for a, b in boundary]
    lw(msp, [world_xy(a, b) for a, b in boundary], "Граница_ППТ")

    insert_rotation = TEXT_ROTATION
    for (col, row), blk in sorted(blocks.items(), key=lambda kv: kv[1]["index"] or 99):
        if blk["index"] is None:
            continue
        pts = [world_xy(*p) for p in blk["ring"]]
        i = blk["index"]
        if i == 11:        # блок-вставка: контур в блоке на слое "0", слой берется у вставки
            p0 = pts[0]
            c, s = math.cos(math.radians(-insert_rotation)), math.sin(math.radians(-insert_rotation))
            local = [((x - p0[0]) * c - (y - p0[1]) * s, (x - p0[0]) * s + (y - p0[1]) * c) for x, y in pts]
            block = doc.blocks.new("ТИП_КВАРТАЛ")
            block.add_lwpolyline([(round(x, 3), round(y, 3)) for x, y in local], close=True, dxfattribs={"layer": "0"})
            msp.add_blockref("ТИП_КВАРТАЛ", (round(p0[0], 3), round(p0[1], 3)),
                             dxfattribs={"layer": "Кварталы", "rotation": insert_rotation})
        elif i == 7:       # незамкнутый контур: разрыв 0,30 м меньше допуска
            lines(msp, pts, "Кварталы", gap=0.30)
        elif i % 4 == 1:
            lw(msp, pts, "Кварталы")
        elif i % 4 == 2:
            lines(msp, pts, "Кварталы")
        elif i % 4 == 3:
            polyline_old(msp, pts, "Кварталы")
        else:              # открытая полилиния и замыкающий отрезок
            lw(msp, pts[:-1] + [pts[-1]], "Кварталы", close=False)
            lines(msp, [pts[-1], pts[0]], "Кварталы", closed=False)
        cx = sum(x for x, _ in pts) / len(pts)
        cy = sum(y for _, y in pts) / len(pts)
        text(msp, blk["num"], (cx, cy), 8, "Номера_кварталов")

    # красные линии: существующие по контурам кварталов, у колонки 4 — прежний край и устанавливаемая линия
    for (col, row), blk in sorted(blocks.items(), key=lambda kv: kv[1]["index"] or 99):
        if blk["index"] is None:
            continue
        ring = [world_xy(*p) for p in blk["old_ring"]]
        for k in range(4):
            p, q = ring[k], ring[(k + 1) % 4]
            if k % 2 == 0:
                msp.add_line((round(p[0], 3), round(p[1], 3)), (round(q[0], 3), round(q[1], 3)),
                             dxfattribs={"layer": "Красные_линии_существующие"})
            else:
                mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
                lw(msp, [p, mid, q], "Красные_линии_существующие", close=False)
        if col == PPT_COLUMNS - 1:
            new = [world_xy(*p) for p in blk["ring"]]
            lw(msp, [new[1], new[2]], "Красные_линии_устанавливаемые", close=False)

    # зоны планируемого размещения ОКС
    site_blocks = [2, 4, 8, 9, 12, 14]
    by_index = {b["index"]: b for b in blocks.values() if b["index"]}
    for n, bi in enumerate(site_blocks, start=1):
        rect = local_rect(by_index[bi])
        a0, b0, a1, b1 = rect.bounds
        ring = [(a0, b0), (a1, b0), (a1, b1), (a0, b1)]
        w = [world_xy(*p) for p in ring]
        label = world_xy((a0 + a1) / 2, (b0 + b1) / 2)
        if n == 1:
            lw(msp, w, "Зоны_размещения_ОКС")
        elif n == 2:
            lines(msp, w, "Зоны_размещения_ОКС")
        elif n == 3:
            rounded_corner_arc(msp, w, 2, 10.0, "Зоны_размещения_ОКС")
        elif n == 4:       # самопересечение: восьмерка
            bow = [world_xy(a0, b0), world_xy(a1, b1), world_xy(a1, b0), world_xy(a0, b1)]
            lw(msp, bow, "Зоны_размещения_ОКС")
            label = world_xy(a1 - 0.2 * (a1 - a0), (b0 + b1) / 2)
        elif n == 5:
            polyline_old(msp, w, "Зоны_размещения_ОКС")
        else:
            lw(msp, w[:3], "Зоны_размещения_ОКС", close=False)
            lines(msp, [w[2], w[3], w[0]], "Зоны_размещения_ОКС", closed=False)
        if n % 2:
            text(msp, str(n), label, 6, "Номера_зон")
        else:
            mtext(msp, "{\\fArial|b1|i0|c204;" + str(n) + "}", label, 6, "Номера_зон")

    # мусорные слои
    xs = [world_xy(a, b) for a, b in [(-111, -350), (575, -350), (575, 180), (-111, 180)]]
    minx, maxx = min(p[0] for p in xs) - 60, max(p[0] for p in xs) + 260
    miny, maxy = min(p[1] for p in xs) - 60, max(p[1] for p in xs) + 60
    lw(msp, [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)], "Рамка")
    sx0, sy0 = maxx - 230, miny + 10
    lw(msp, [(sx0, sy0), (maxx - 10, sy0), (maxx - 10, sy0 + 70), (sx0, sy0 + 70)], "Штамп")
    for k, s in enumerate([f"Проект планировки территории, чертеж красных линий, {SKETCH}",
                           f"Образец для проверки ingest, дата {DATE}", "Данные вымышлены"]):
        t = msp.add_text(s, height=6, dxfattribs={"layer": "Штамп"})
        t.set_placement((sx0 + 6, sy0 + 55 - k * 16), align=TextEntityAlignment.LEFT)
    for p1, p2 in [(world_xy(A_LINES[0] + 9, B_LINES[0] - 20), world_xy(A_LINES[1] - 9, B_LINES[0] - 20)),
                   (world_xy(A_LINES[0] - 20, B_LINES[0] + 9), world_xy(A_LINES[0] - 20, B_LINES[1] - 9))]:
        msp.add_aligned_dim(p1, p2, distance=-12, dxfattribs={"layer": "Размеры"}).render()
    msp.add_line((minx + 10, miny + 10), (minx + 40, miny + 40), dxfattribs={"layer": "0"})
    msp.add_point((minx + 20, maxy - 20), dxfattribs={"layer": "Defpoints"})
    msp.add_point((minx + 30, maxy - 20), dxfattribs={"layer": "Defpoints"})
    north = doc.blocks.new("СЕВЕР")
    north.add_lwpolyline([(0, 0), (6, -20), (0, -12), (-6, -20)], close=True)
    north.add_text("С", dxfattribs={"height": 8}).set_placement((0, 6), align=TextEntityAlignment.MIDDLE_CENTER)
    msp.add_blockref("СЕВЕР", (minx + 40, maxy - 40), dxfattribs={"layer": "Знаки"})
    for name, a, b in [("ул. Петровская", 230, 0), ("ул. Фрунзе", 230, -168)]:
        text(msp, name, world_xy(a, b), 7, "Подписи_улиц")
    for k in range(3):   # инженерные сети: слой без правила раскладки
        p = world_xy(-100 + 60 * k, -50)
        q = world_xy(-60 + 60 * k, 120)
        msp.add_line((round(p[0], 3), round(p[1], 3)), (round(q[0], 3), round(q[1], 3)),
                     dxfattribs={"layer": "Инженерные_сети"})

    path = OUT / "dxf" / "ppt-sample.dxf"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Классы ezdxf собирает из множества типов сущностей: порядок обхода множества строк меняется от запуска
    # к запуску (рандомизация хэшей Python). Классы добавляются заранее и сортируются — файл тот же байт в байт.
    doc.classes.add_required_classes(doc.dxfversion)
    doc.classes.classes = dict(sorted(doc.classes.classes.items()))
    doc.saveas(path)
    (OUT / "dxf" / "ppt-sample.dxf.source.yaml").write_text(yaml.safe_dump({
        "rules": "ppt",
        "label": f"{SKETCH}: проект планировки, чертеж красных линий",
        "date": DATE,
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


# ---------- КПТ ----------

def ordinates(parent, ring):
    ords = ET.SubElement(parent, "ordinates")
    pts = list(ring.coords)
    for n, (e, nn) in enumerate(pts, start=1):
        o = ET.SubElement(ords, "ordinate")
        ET.SubElement(o, "x").text = f"{nn:.2f}"      # Росреестр: x — север
        ET.SubElement(o, "y").text = f"{e:.2f}"       # y — восток
        ET.SubElement(o, "num_geopoint").text = str(n if n < len(pts) else 1)


def contours(parent, geom):
    loc = ET.SubElement(parent, "contours_location")
    cs = ET.SubElement(loc, "contours")
    parts = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    for k, poly in enumerate(parts, start=1):
        c = ET.SubElement(cs, "contour")
        ET.SubElement(c, "number_pp").text = str(k)
        se = ET.SubElement(ET.SubElement(c, "entity_spatial"), "spatials_elements")
        for ring in [poly.exterior, *poly.interiors]:
            ordinates(ET.SubElement(se, "spatial_element"), ring)


def make_kpt(blocks) -> Path:
    parcels = parcels_for(blocks) + [two_contour_parcel(blocks)]
    root = ET.Element("extract_cadastral_plan_territory")
    req = ET.SubElement(ET.SubElement(root, "details_statement"), "group_top_requisites")
    ET.SubElement(req, "organ_registr_rights").text = f"{SKETCH}, не выписка Росреестра, данные вымышлены"
    ET.SubElement(req, "date_formation").text = DATE
    ET.SubElement(req, "registration_number").text = "SYN-KPT-0001"
    ET.SubElement(root, "coord_system").text = "EPSG:32637"
    block = ET.SubElement(ET.SubElement(root, "cadastral_blocks"), "cadastral_block")
    ET.SubElement(block, "cadastral_number").text = "00:00:0000000"
    records = ET.SubElement(ET.SubElement(ET.SubElement(block, "record_data"), "base_data"), "land_records")
    for n, p in enumerate(parcels, start=1):
        geom = world(p["geom"])
        rec = ET.SubElement(records, "land_record")
        ET.SubElement(ET.SubElement(ET.SubElement(rec, "object"), "common_data"), "cad_number").text = f"00:00:0000000:{n}"
        params = ET.SubElement(rec, "params")
        ET.SubElement(ET.SubElement(ET.SubElement(params, "category"), "type"), "value").text = "Земли населенных пунктов"
        ET.SubElement(ET.SubElement(ET.SubElement(params, "permitted_use"), "permitted_use_established"),
                      "by_document").text = p["use"]
        ET.SubElement(ET.SubElement(params, "area"), "value").text = str(round(geom.area))
        contours(rec, geom)
    rec = ET.SubElement(records, "land_record")     # участок без границ: в ЕГРН границы не установлены
    ET.SubElement(ET.SubElement(ET.SubElement(rec, "object"), "common_data"), "cad_number").text = \
        f"00:00:0000000:{len(parcels) + 1}"
    params = ET.SubElement(rec, "params")
    ET.SubElement(ET.SubElement(ET.SubElement(params, "category"), "type"), "value").text = "Земли населенных пунктов"
    ET.SubElement(ET.SubElement(params, "area"), "value").text = "600"

    zones = ET.SubElement(root, "zones_and_borders")

    def zone(reg, boundary_type, zone_type, name, geom, index=None):
        z = ET.SubElement(zones, "zone_and_border")
        ET.SubElement(z, "reg_numb_border").text = reg
        ET.SubElement(ET.SubElement(z, "type_boundary"), "value").text = boundary_type
        ET.SubElement(ET.SubElement(z, "type_zone"), "value").text = zone_type
        ET.SubElement(z, "name_by_doc").text = name
        if index:
            ET.SubElement(z, "index").text = index
        contours(z, world(geom))

    zouit = "Зона с особыми условиями использования территории"
    zone("00:00-6.1", zouit, "Санитарно-защитная зона", f"СЗЗ предприятия, {SKETCH}",
         Polygon([(-130, -360), (130, -360), (150, -150), (40, -120), (-130, -140)]))
    zone("00:00-6.2", zouit, "Охранная зона инженерных коммуникаций", f"Охранная зона газопровода, {SKETCH}",
         LineString([(-111, 0), (460, 0)]).buffer(8, cap_style=2))
    zone("00:00-6.3", zouit, "Зона охраны объекта культурного наследия", f"Охранная зона ОКН, {SKETCH}",
         Polygon([(0, -330), (345, -330), (345, 170), (0, 170)]))
    pzz = "Территориальная зона"
    region = box(-130, -370, 600, 200)
    sea = box(-130, -370, 600, -250)
    rest = region.difference(sea)
    od = rest.intersection(box(-130, -370, 230, 200))
    zh = rest.intersection(box(230, -370, 600, 200))
    zone("00:00-7.1", pzz, "Зона рекреационного назначения", "Зона Р-1", sea, "Р-1")
    zone("00:00-7.2", pzz, "Зона делового, общественного и коммерческого назначения", "Зона ОД-1", od, "ОД-1")
    zone("00:00-7.3", pzz, "Жилая зона", "Зона Ж-3", zh, "Ж-3")

    ET.indent(root, space="  ")
    path = OUT / "kpt" / "kpt-sample.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def main() -> None:
    rnd = random.Random(SEED)
    blocks = make_blocks(rnd)
    print(make_dxf(blocks, rnd))
    print(make_kpt(blocks))


if __name__ == "__main__":
    main()
