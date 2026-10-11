"""Подписи объектов по TEXT.md верстки (cm_carto/render/labels.py, правила rules/labels.yaml) на примерах из образцов К1."""

import pytest

from cm_carto.render.labels import NBSP, clean, quotes
from cm_carto.render.overlay import Measure


@pytest.mark.parametrize("raw, label", [
    ('МБДОУ д/с 20 "Красная Шапочка"', f"Детский сад №{NBSP}20 «Красная Шапочка»"),
    ("Детский сад №68 «Светлячок»", f"Детский сад №{NBSP}68 «Светлячок»"),
    ("Школа №9", f"Школа №{NBSP}9"),
    ("Лицей №4", f"Лицей №{NBSP}4"),
    ("школа №22", f"Школа №{NBSP}22"),
    ("  Школа   № 23 ", f"Школа №{NBSP}23"),
    ("Гимназия 2 им. Чехова", f"Гимназия №{NBSP}2 имени Чехова"),
    ("Средняя Общеобразовательная школа 21", f"Средняя общеобразовательная школа №{NBSP}21"),
    ("МБОУ СОШ № 5", f"Средняя общеобразовательная школа №{NBSP}5"),
    ("Городская больница 1-приёмный покой", "Городская больница 1-приемный покой"),
    ("Таганрогская поликлиника ЮОМЦ ФМБА России", "Таганрогская поликлиника ЮОМЦ ФМБА России"),
    ('ООО "Школа "Радуга""', "«Школа „Радуга“»"),
    ('ГБУЗ " Больница "', "«Больница»"),
    ("Таганрог-II", "Таганрог-II"),
    ("Ж-3", "Ж-3"),
])
def test_clean(raw, label):
    assert clean(raw) == label


def test_quotes_nested():
    assert quotes('“Парк „Дубки“”') == "«Парк „Дубки“»"
    assert quotes('"а" и "б"') == "«а» и «б»"


def test_name_in_quotes_keeps_case():
    assert clean('Детский сад "Школа радости"') == "Детский сад «Школа радости»"


def test_wrap_keeps_number_with_sign():
    from cm_carto import tokens

    measure = Measure(tokens.load("map"), tokens.load("style"))
    wrapped = measure.wrap(clean('МБДОУ д/с 20 "Красная Шапочка"'))
    assert f"№{NBSP}20" in wrapped.split("\n")[0]
