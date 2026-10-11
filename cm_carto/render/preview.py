"""Превью карты для проверки полноты: overlay.json поверх подложки простым рисованием Pillow. Не выпуск: в альбом
идут подложка и overlay, рисует их верстка. Цвета, толщины, знаки и кегли — из токенов верстки по role.

Отдельная легенда (место 59/91/123 мм) рисуется справа от карты на белом поле — чтобы видеть ее рядом.
"""

import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

from cm_carto import paths, tokens
from cm_carto.render import spec as spec_file

PT_MM = 25.4 / 72


class Canvas:
    def __init__(self, image: Image.Image, dpi: int, std: dict, style: dict):
        self.image, self.k, self.std, self.style = image, dpi / 25.4, std, style
        self.draw = ImageDraw.Draw(image)

    def px(self, xy) -> tuple[float, float]:
        return xy[0] * self.k, xy[1] * self.k

    def color(self, name: str) -> tuple[int, int, int]:
        hexa = self.std["palette"].get(name) or self.style["colors"][name]
        return tuple(int(str(hexa)[i:i + 2], 16) for i in (0, 2, 4))

    def width(self, mm: float) -> int:
        return max(1, round(mm * self.k))

    def font(self, style_key: str) -> ImageFont.FreeTypeFont:
        text = self.std["text"]["styles"][style_key]
        path = paths.design() / self.style["font"]["files"][text["weight"].lower()]
        return ImageFont.truetype(str(path), round(text["size"] * PT_MM * self.k))

    def line(self, coords, stroke: dict) -> None:
        pts = [self.px(c) for c in coords]
        pattern = [v * self.k for v in self.std["line_types"][stroke.get("type", "solid")]]
        color, width = self.color(stroke["color"]), self.width(stroke["width"])
        if not pattern:
            self.draw.line(pts, fill=color, width=width, joint="curve")
            return
        on, index, left = True, 0, pattern[0]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            length, done = math.hypot(x1 - x0, y1 - y0), 0.0
            while done < length:
                step = min(left, length - done)
                a = (x0 + (x1 - x0) * done / length, y0 + (y1 - y0) * done / length)
                b = (x0 + (x1 - x0) * (done + step) / length, y0 + (y1 - y0) * (done + step) / length)
                if on:
                    self.draw.line([a, b], fill=color, width=width)
                done, left = done + step, left - step
                if left <= 0:
                    index = (index + 1) % len(pattern)
                    on, left = not on, pattern[index]

    def polygon(self, rings, layer: dict, fill: str | None = None) -> None:
        mask = Image.new("L", self.image.size, 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.polygon([self.px(c) for c in rings[0]], fill=255)
        for hole in rings[1:]:
            mdraw.polygon([self.px(c) for c in hole], fill=0)
        fill = fill or layer.get("fill", {}).get("color")
        if fill:
            self.image.paste(self.color(fill), mask=mask)
        if "hatch" in layer:
            hatch = layer["hatch"]
            lines = Image.new("L", self.image.size, 0)
            ldraw = ImageDraw.Draw(lines)
            w, h = self.image.size
            angle, step = math.radians(hatch["angle"]), hatch["step"] * self.k
            dx, dy = math.cos(angle), -math.sin(angle)
            nx, ny = -dy, dx
            reach = math.hypot(w, h)
            for i in range(-int(reach / step), int(reach / step) + 1):
                cx, cy = w / 2 + nx * i * step, h / 2 + ny * i * step
                ldraw.line([(cx - dx * reach, cy - dy * reach), (cx + dx * reach, cy + dy * reach)], fill=255,
                           width=self.width(hatch["width"]))
            self.image.paste(self.color(hatch["color"]), mask=ImageChops.darker(mask, lines))
        if "stroke" in layer:
            for ring in rings:
                self.line(ring, layer["stroke"])

    def marker(self, xy, marker: dict) -> None:
        x, y = self.px(xy)
        r = marker["size"] / 2 * self.k
        stroke = marker.get("stroke")
        box = [x - r, y - r, x + r, y + r]
        args = {"fill": self.color(marker["fill"])}
        if stroke:
            args |= {"outline": self.color(stroke["color"]), "width": self.width(stroke["width"])}
        (self.draw.ellipse if marker["shape"] == "circle" else self.draw.rectangle)(box, **args)

    def text(self, anchor, text: str, style_key: str, align: str, plate: bool = True, angle: float = 0) -> tuple:
        font = self.font(style_key)
        color = self.color(self.std["text"]["styles"][style_key]["color"])
        x, y = self.px(anchor)
        left, top, right, bottom = self.draw.textbbox((0, 0), text, font=font, anchor="ls")
        w, h = right - left, bottom - top
        vertical, horizontal = (align.split("-") + ["center"])[:2] if "-" in align else ("middle", "center")
        x0 = {"left": x, "center": x - w / 2, "right": x - w}[horizontal]
        y0 = {"top": y, "middle": y - h / 2, "bottom": y - h}[vertical]
        if angle:  # подпись вдоль линии: отдельный слой с поворотом
            layer = Image.new("RGBA", (int(w + 4 * self.k), int(h + 2 * self.k)), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            pad = self.std["text"]["plate"]
            d.rectangle([0, 0, layer.width, layer.height], fill=self.color(pad["color"]))
            d.text((2 * self.k - left, self.k - top), text, font=font, fill=color)
            layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
            self.image.paste(layer, (int(x - layer.width / 2), int(y - layer.height / 2)), layer)
            return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        if plate:
            pad = self.std["text"]["plate"]
            px_, py_ = pad["padding_x"] * self.k, pad["padding_y"] * self.k
            self.draw.rectangle([x0 - px_, y0 - py_, x0 + w + px_, y0 + h + py_], fill=self.color(pad["color"]))
        self.draw.text((x0 - left, y0 - top), text, font=font, fill=color)
        return (x0, y0, x0 + w, y0 + h)


def rings_of(geometry: dict) -> list:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    return []


def lines_of(geometry: dict) -> list:
    return {"LineString": [geometry["coordinates"]], "MultiLineString": geometry["coordinates"]}.get(geometry["type"], [])


def feature(canvas: Canvas, item: dict) -> None:
    std = canvas.std
    layer = spec_file.role_spec(std, item["role"])
    geometry = item["geometry"]
    if item["role"] == "zoning":
        category = std["zoning"]["categories"][item[std["zoning"]["field"]]]
        for rings in rings_of(geometry):
            canvas.polygon(rings, {"stroke": std["zoning"]["stroke"]}, fill=category["fill"])
    elif geometry["type"] == "Point":
        canvas.marker(geometry["coordinates"], layer["marker"])
    elif layer["geometry"] == "polygon":
        for rings in rings_of(geometry):
            canvas.polygon(rings, layer)
    else:
        for coords in lines_of(geometry):
            canvas.line(coords, layer["stroke"])


def legend_rows(std: dict, data: dict):
    """Строки легенды с вертикальным смещением, мм: (строка, y от верха легенды); и высота легенды."""
    rule = std["legend"]
    y, out = 0.0, []
    for row in data["legend"]["rows"]:
        if row["type"] == "group" and out:
            y += rule["group_gap"]
        out.append((row, y))
        y += rule["swatch"][1] + rule["item_gap"]
    return out, y - rule["item_gap"]


def legend(canvas: Canvas, data: dict, origin: tuple[float, float]) -> None:
    """Строки сверху вниз от origin (мм): образец и промежутки — map.yaml: legend."""
    std, rule = canvas.std, canvas.std["legend"]
    sw, sh = rule["swatch"]
    rows, _ = legend_rows(std, data)
    for row, dy in rows:
        x, y = origin[0], origin[1] + dy
        if row["type"] != "item":
            canvas.text((x, y + sh / 2), row["text"], rule["group_text"], "middle-left", plate=False)
            continue
        layer = spec_file.role_spec(std, row["role"])
        square = [[[x, y], [x + sw, y], [x + sw, y + sh], [x, y + sh], [x, y]]]
        if row["role"] == "zoning":
            canvas.polygon(square, {"stroke": std["zoning"]["stroke"]}, fill=std["zoning"]["categories"][row["category"]]["fill"])
        elif layer["geometry"] == "point":
            canvas.marker((x + sw / 2, y + sh / 2), layer["marker"])
        elif layer["geometry"] == "polygon":
            canvas.polygon(square, layer)
        else:
            canvas.line([[x, y + sh / 2], [x + sw, y + sh / 2]], layer["stroke"])
        canvas.text((x + sw + rule["swatch_text_gap"], y + sh / 2), row["text"], rule["text"], "middle-left", plate=False)


def anchored(position: dict, size: tuple[float, float]) -> tuple[float, float]:
    """Левый верхний угол элемента размера size по точке и привязке из компоновки."""
    vertical, horizontal = position["align"].split("-") if "-" in position["align"] else ("middle", "center")
    x = {"left": position["x"], "center": position["x"] - size[0] / 2, "right": position["x"] - size[0]}[horizontal]
    y = {"top": position["y"], "middle": position["y"] - size[1] / 2, "bottom": position["y"] - size[1]}[vertical]
    return x, y


def scale_bar(canvas: Canvas, bar: dict) -> None:
    rule = canvas.std["scale_bar"]
    text_h = canvas.std["text"]["styles"][rule["text"]]["size"] * PT_MM
    width, height = bar["length_mm"] + 2 * rule["plate_padding"] + 6, rule["tick_height"] + rule["label_gap"] + text_h + 2 * rule["plate_padding"]
    x, y = anchored(bar["position"], (width, height))
    canvas.draw.rectangle([*canvas.px((x, y)), *canvas.px((x + width, y + height))], fill=canvas.color("background"))
    x0, base = x + rule["plate_padding"], y + height - rule["plate_padding"]
    stroke = {"color": rule["line"]["color"], "width": rule["line"]["width"]}
    canvas.line([[x0, base], [x0 + bar["length_mm"], base]], stroke)
    for i, label in enumerate(bar["labels"]):
        tx = x0 + i * bar["segment_mm"]
        canvas.line([[tx, base], [tx, base - rule["tick_height"]]], stroke)
        canvas.text((tx, base - rule["tick_height"] - rule["label_gap"]), label, rule["text"],
                    "bottom-left" if i == len(bar["labels"]) - 1 else "bottom-center", plate=False)


def north(canvas: Canvas, data: dict) -> None:
    rule = canvas.std["north_arrow"]
    w, h = rule["size"]
    x, y = anchored(data["north"]["position"], (w, h))
    canvas.draw.polygon([canvas.px((x + w / 2, y + h * 0.3)), canvas.px((x, y + h)), canvas.px((x + w, y + h))],
                        fill=canvas.color(rule["color"]))
    canvas.text((x + w / 2, y), "С", rule["text"], "top-center", plate=False)


def render(overlay_path: Path, out: Path) -> Path:
    data = json.loads(overlay_path.read_text(encoding="utf-8"))
    std, style = tokens.load("map"), tokens.load("style")
    dpi = std["export"]["dpi"]
    k = dpi / 25.4
    width, height = data["size_mm"]["width"], data["size_mm"]["height"]
    size = (round(width * k), round(height * k))
    extra = data["legend"]["place"] if data["legend"]["place"] != "inside" else 0
    image = Image.new("RGB", (size[0] + round((extra + 10) * k) if extra else size[0], size[1]), (255, 255, 255))
    canvas = Canvas(image, dpi, std, style)
    for item in data["stack"]:
        if item["kind"] == "basemap":
            layer = Image.open(overlay_path.with_name(item["file"])).convert("RGBA")
            image.paste(layer, (0, 0), layer)
        else:
            for f in (f for f in data["features"] if f["role"] in item["roles"]):
                feature(canvas, f)
    boxes = []
    for label in data["labels"]:
        rule = spec_file.role_spec(std, label["role"])["label"]
        plate = rule["style"] not in std["text"]["plate"]["except_styles"]
        boxes.append((label["object_id"], canvas.text(label["anchor"], label["text"], label["style"], label["align"],
                                                      plate, label.get("angle", 0))))
    scale_bar(canvas, data["scale_bar"])
    if data["north"]["visible"]:
        north(canvas, data)
    if data["legend"]["place"] == "inside":  # на белой плашке с полем inside.padding, низом в точку компоновки
        pad = std["legend"]["inside"]["padding"]
        _, height_mm = legend_rows(std, data)
        x, bottom = data["legend"]["position"]["x"], data["legend"]["position"]["y"]
        canvas.draw.rectangle([*canvas.px((x, bottom - height_mm - 2 * pad)), *canvas.px((x + 2 * pad + 60, bottom))],
                              fill=canvas.color("background"))
        legend(canvas, data, (x + pad, bottom - pad - height_mm))
    else:
        legend(canvas, data, (width + 10, 10))
    image.save(out, optimize=False)
    for (a, ba), (b, bb) in ((x, y) for i, x in enumerate(boxes) for y in boxes[i + 1:]):
        if ba[0] < bb[2] and bb[0] < ba[2] and ba[1] < bb[3] and bb[1] < ba[3]:
            print(f"{overlay_path.name}: подписи пересекаются: {a} и {b}")
    return out
