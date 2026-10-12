"""Проверка шрифтов doctor: начертание — стиль семейства (как берет рендер QGIS), а не отдельное семейство."""

import pytest

from cm_carto import doctor, tokens
from cm_carto.render import build

STYLES = ["Light", "Regular", "Medium", "SemiBold"]


def test_split_face_by_rule():
    assert doctor.split_face("Open Sans", "Open Sans") == ("Open Sans", "Regular")
    assert doctor.split_face("Open Sans Light", "Open Sans") == ("Open Sans", "Light")
    assert doctor.split_face("Open Sans SemiBold", "Open Sans") == ("Open Sans", "SemiBold")


def test_split_face_rejects_foreign_family():
    with pytest.raises(ValueError, match="Roboto Light.*Open Sans"):
        doctor.split_face("Roboto Light", "Open Sans")
    with pytest.raises(ValueError, match="Open SansLight"):
        doctor.split_face("Open SansLight", "Open Sans")


def test_faces_in_tokens_split_into_one_family():
    faces = tokens.load("style")["font"]["faces"]
    parts = {role: doctor.split_face(face, faces["regular"]) for role, face in faces.items()}
    assert {family for family, _ in parts.values()} == {faces["regular"]}
    assert {style for _, style in parts.values()} == set(STYLES)


def test_fonts_of_this_machine():
    build.start()
    assert doctor.check_fonts() == []


def test_macos_answer_family_with_styles_only(monkeypatch):
    """CoreText: одно семейство «Open Sans» со стилями, отдельных «Open Sans Light» нет."""
    monkeypatch.setattr(doctor, "font_styles", lambda family: STYLES if family == "Open Sans" else [])
    assert doctor.check_fonts() == []


def test_missing_style_is_named(monkeypatch):
    monkeypatch.setattr(doctor, "font_styles", lambda family: [s for s in STYLES if s != "SemiBold"] if family == "Open Sans" else [])
    errors = doctor.check_fonts()
    assert len(errors) == 1 and "Open Sans SemiBold" in errors[0] and "SemiBold" in errors[0] and "нет стиля" in errors[0]


def test_missing_family_is_named(monkeypatch):
    monkeypatch.setattr(doctor, "font_styles", lambda family: [])
    errors = doctor.check_fonts()
    assert len(errors) == 4 and "нет семейства Open Sans" in errors[0]
