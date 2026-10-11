"""Ключи object_id: из текста источника и из геометрии (контракт geopackage, «object_id»)."""

import hashlib
import re

import shapely

TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h",
    "ц": "c", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def key_from_text(text: str) -> str:
    """Номер из документа → ключ: кириллица латиницей, недопустимые знаки (в том числе `:`) → `-`."""
    key = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip().lower().translate(TRANSLIT)).strip("-")
    if not key:
        raise ValueError(f"из текста «{text}» нельзя получить ключ object_id")
    return key


def geometry_key(geom) -> str:
    """`g` и 12 знаков SHA-256 геометрии, округленной до 1 см; порядок вершин и частей не важен."""
    rounded = shapely.normalize(shapely.set_precision(geom, 0.01, mode="pointwise"))
    return "g" + hashlib.sha256(shapely.to_wkb(rounded, output_dimension=2, byte_order=1)).hexdigest()[:12]
