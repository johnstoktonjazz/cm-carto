"""Карты по описанию (schema/map.schema.json) через PyQGIS без окна в стилях и компоновках верстки.

    cm-carto render <описание.yaml> [...] [--preview]

Выход: подложка <out>.png и <out>.provenance.json (контракт image), слои данных <out>.overlay.json (контракт overlay),
с --preview — <out>.preview.png для проверки глазами. spec.py — описание карты: проверка по схеме и по стандарту
верстки при загрузке; build.py — рендер подложки, provenance, кеш _build/cache; overlay.py — слои данных в мм места
и расстановка подписей; preview.py — превью; print.yaml — источники, запрещенные к печати; cli.py — команда.
"""
