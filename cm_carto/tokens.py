"""Токены верстки: читаются из cm-design при каждом запуске, к себе не копируются (контракт tokens)."""

import yaml

from cm_carto import paths


def load(name: str) -> dict:
    return yaml.safe_load((paths.design() / "tokens" / f"{name}.yaml").read_text(encoding="utf-8"))


def place_size_mm(place: str) -> tuple[float, float]:
    """Размер места карты в мм: map.yaml: places.<место>.image."""
    places = load("map")["places"]
    if place not in places:
        raise ValueError(f"места {place} нет в map.yaml: places ({', '.join(places)})")
    width, height = places[place]["image"]
    return float(width), float(height)
