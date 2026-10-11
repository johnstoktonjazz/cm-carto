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

    def text(self, anchor, text: str, style_key: str, align: str, plate: bool = True) -> None:
        """Текст по точке привязки: align — какой угол рамки текста стоит в anchor; строки — по \\n."""
        font = self.font(style_key)
        color = self.color(self.std["text"]["styles"][style_key]["color"])
        ascent, descent = font.getmetrics()
        lines = text.split("\n")
        w, line_h = max(self.draw.textlength(line, font=font) for line in lines), ascent + descent
        h = line_h * len(lines)
        x, y = self.px(anchor)
        vertical, horizontal = align.split("-") if "-" in align else ("middle", "center")
        x0 = {"left": x, "center": x - w / 2, "right": x - w}[horizontal]
        y0 = {"top": y, "middle": y - h / 2, "bottom": y - h}[vertical]
        if plate:
            pad = self.std["text"]["plate"]
            px_, py_ = pad["padding_x"] * self.k, pad["padding_y"] * self.k
            self.draw.rectangle([x0 - px_, y0 - py_, x0 + w + px_, y0 + h + py_], fill=self.color(pad["color"]))
        for i, line in enumerate(lines):
            self.draw.text((x0, y0 + i * line_h), line, font=font, fill=color, anchor="la")


def role_style(std: dict, role: str) -> tuple[dict, str | None]:
    """Стиль роли overlay: (слой map.yaml, заливка категории); «zoning.<категория>» — категория зон ПЗЗ."""
    if role.startswith("zoning."):
        category = std["zoning"]["categories"][role.split(".", 1)[1]]
        return {"geometry": "polygon", "stroke": std["zoning"]["stroke"]}, category["fill"]
    return spec_file.role_spec(std, role), None


def swatch(canvas: Canvas, role: str, x: float, y: float) -> None:
    std = canvas.std
    layer, fill = role_style(std, role)
    sw, sh = std["legend"]["swatch"]
    if layer["geometry"] == "point":
        canvas.marker((x + sw / 2, y + sh / 2), layer["marker"])
    elif layer["geometry"] == "polygon":
        canvas.polygon([[[x, y], [x + sw, y], [x + sw, y + sh], [x, y + sh], [x, y]]], layer, fill=fill)
    else:
        canvas.line([[x, y + sh / 2], [x + sw, y + sh / 2]], layer["stroke"])


def legend(canvas: Canvas, layer: dict, origin: tuple[float, float]) -> None:
    """Строки сверху вниз: образец, текст 10 pt, промежутки — map.yaml: legend; внутри — на плашке."""
    std, rule = canvas.std, canvas.std["legend"]
    sw, sh = rule["swatch"]
    x, y = origin
    if "legend_at" in layer:
        pad = rule["inside"]["padding"]
        rows = len(layer["legend"])
        height = rows * sh + (rows - 1) * rule["item_gap"] + 2 * pad
        width = sw + rule["swatch_text_gap"] + max(
            canvas.draw.textlength(r["text"], font=canvas.font(rule["text"])) / canvas.k for r in layer["legend"]) + 2 * pad
        canvas.draw.rectangle([*canvas.px((x, y)), *canvas.px((x + width, y + height))], fill=canvas.color("background"))
        x, y = x + pad, y + pad
    for row in layer["legend"]:
        swatch(canvas, row["role"], x, y)
        canvas.text((x + sw + rule["swatch_text_gap"], y + sh / 2), row["text"], rule["text"], "middle-left", plate=False)
        y += sh + rule["item_gap"]


def scale_bar(canvas: Canvas, layer: dict, style: dict) -> None:
    from cm_carto.render.overlay import number

    rule, bar = canvas.std["scale_bar"], layer["scalebar"]
    stroke = {"color": rule["line"]["color"], "width": rule["line"]["width"]}
    x0, base = bar["at"]
    labels = [number(v, style) for v in bar["segments_m"]]
    labels[-1] += style["number_format"]["unit_separator"] + rule["unit_label"]
    font = canvas.font(rule["text"])
    pad = rule["plate_padding"]
    first = canvas.draw.textlength(labels[0], font=font) / canvas.k
    last = canvas.draw.textlength(labels[-1], font=font) / canvas.k
    length = bar["segment_mm"] * (len(labels) - 1)
    ascent, descent = font.getmetrics()
    top = base - rule["tick_height"] - rule["label_gap"] - (ascent + descent) / canvas.k
    canvas.draw.rectangle([*canvas.px((x0 - first / 2 - pad, top - pad)), *canvas.px((x0 + length + last / 2 + pad, base + pad))],
                          fill=canvas.color("background"))
    canvas.line([[x0, base], [x0 + length, base]], stroke)
    for i, label in enumerate(labels):
        tx = x0 + i * bar["segment_mm"]
        canvas.line([[tx, base], [tx, base - rule["tick_height"]]], stroke)
        canvas.text((tx, base - rule["tick_height"] - rule["label_gap"]), label, rule["text"], "bottom-center", plate=False)


def north(canvas: Canvas, at) -> None:
    rule = canvas.std["north_arrow"]
    w, h = rule["size"]
    x, y = at
    canvas.draw.polygon([canvas.px((x + w / 2, y + h * 0.3)), canvas.px((x, y + h)), canvas.px((x + w, y + h))],
                        fill=canvas.color(rule["color"]))
    canvas.text((x + w / 2, y), "С", rule["text"], "top-center", plate=False)


ALIGN = {"nw": "top-left", "n": "top-center", "ne": "top-right", "w": "middle-left", "c": "center",
         "e": "middle-right", "sw": "bottom-left", "s": "bottom-center", "se": "bottom-right"}


def render(overlay_path: Path, out: Path) -> Path:
    data = json.loads(overlay_path.read_text(encoding="utf-8"))
    std, style = tokens.load("map"), tokens.load("style")
    dpi = std["export"]["dpi"]
    k = dpi / 25.4
    width, height = data["place"]["size_mm"]["width"], data["place"]["size_mm"]["height"]
    legend_layer = next(layer for layer in data["layers"] if layer["kind"] == "legend")
    outside = "legend_at" not in legend_layer
    panel = max(std["legend"]["widths"]) + 10 if outside else 0
    image = Image.new("RGB", (round((width + panel) * k), round(height * k)), (255, 255, 255))
    basemap = Image.open(overlay_path.with_name(data["basemap"])).convert("RGB")
    image.paste(basemap, (0, 0))
    canvas = Canvas(image, dpi, std, style)
    for layer in data["layers"]:
        kind = layer["kind"]
        if kind in ("polygon", "line", "point"):
            spec_role, fill = role_style(std, layer["role"])
            for f in layer["features"]:
                if kind == "polygon":
                    canvas.polygon(f["rings"], spec_role, fill=fill)
                elif kind == "line":
                    canvas.line(f["path"], spec_role["stroke"])
                else:
                    canvas.marker(f["at"], spec_role["marker"])
        elif kind == "label":
            for f in layer["features"]:
                if "leader_to" in f:
                    canvas.line([f["leader_to"], f["at"]], std["layers"]["leader"]["stroke"])
                plate = f["size_role"] not in std["text"]["plate"]["except_styles"]
                canvas.text(f["at"], f["text"], f["size_role"], ALIGN[f["anchor"]], plate)
        elif kind == "legend":
            legend(canvas, layer, layer["legend_at"] if not outside else (width + 10, 10))
        elif kind == "scalebar":
            scale_bar(canvas, layer, style)
        elif kind == "north":
            north(canvas, layer["features"][0]["at"])
    image.save(out, optimize=False)
    return out
