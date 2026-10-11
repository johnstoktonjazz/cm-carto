"""Реестры: разбор выгрузок по rules/registries.yaml на синтетических фрагментах; на живых данных не проверялись."""

import json

import pytest
from fetch_fixtures import project_file

from cm_carto import gpkg, project
from cm_carto.fetch import registries


@pytest.fixture
def proj(tmp_path):
    return project.load(project_file(tmp_path))


def put(proj, tmp_path, registry, text, date="2026-10-01"):
    file = tmp_path / f"{registry}.csv"
    file.write_text(text, encoding="utf-8")
    registries.download(proj, registry, file, date)


def okn_row(number, category, lon, lat):
    geo = json.dumps({"type": "Point", "coordinates": [lon, lat]}) if lon else ""
    return f'{number},Дом,Памятник,{category},"{geo.replace(chr(34), chr(34) * 2)}"\n'


OKN_HEAD = "Номер в реестре,Объект,Вид объекта,Категория историко-культурного значения,На карте\n"


def test_okn_heritage(proj, tmp_path):
    put(proj, tmp_path, "okn", OKN_HEAD + okn_row("1001", "Федерального значения", 38.005, 47.004)
        + okn_row("1002", "Выявленный объект культурного наследия", 38.006, 47.004)
        + okn_row("1003", "Регионального значения", None, None)
        + okn_row("1004", "Регионального значения", 39.5, 47.004))
    frame = registries.heritage(proj)
    # без координат и вне охвата записи не попадают в слой
    assert sorted(frame["object_id"]) == ["heritage:1001", "heritage:1002"]
    assert dict(zip(frame["registry_number"], frame["category"])) == {"1001": "federal", "1002": "identified"}
    assert set(frame["status"]) == {"reference"} and frame["source_label"].iloc[0].startswith("ЕГРОКН")
    path = registries.common.write_part(proj, "fetch-okn", {"heritage": frame})
    assert gpkg.check(path) == []


def test_okn_unknown_category_is_error(proj, tmp_path):
    put(proj, tmp_path, "okn", OKN_HEAD + okn_row("1", "Странной категории", 38.005, 47.004))
    with pytest.raises(KeyError, match="Странной"):
        registries.heritage(proj)


def test_missing_columns_are_named(proj, tmp_path):
    put(proj, tmp_path, "okn", "Объект\nДом\n")
    with pytest.raises(ValueError, match="нет столбцов"):
        registries.read(proj, "okn")


def test_no_cache_and_no_file_is_error(proj):
    with pytest.raises(ValueError, match="кеша нет"):
        registries.download(proj, "okn")


def test_dtp_severity_and_no_personal_data(proj, tmp_path):
    put(proj, tmp_path, "dtp", "Номер карточки;Дата;Погибло;Ранено;Долгота;Широта\n"
        "1;05.03.2025;1;2;38.005;47.004\n2;06.03.2025;0;1;38.006;47.004\n3;07.03.2025;0;0;38.007;47.004\n"
        "4;08.03.2025;0;0;;\n")
    frame = registries.accidents(proj).set_index("object_id")
    assert frame["severity"].to_dict() == {"accidents:dtp-1": "fatal", "accidents:dtp-2": "injury",
                                           "accidents:dtp-3": "damage"}
    assert frame.loc["accidents:dtp-1", "date"] == "2025-03-05"
    path = registries.common.write_part(proj, "fetch-dtp", {"accidents": registries.accidents(proj)})
    assert gpkg.check(path) == []


def test_bus_gov_groups_and_missing_coordinates(proj, tmp_path):
    put(proj, tmp_path, "bus_gov", "Идентификатор;Наименование;Тип учреждения;Адрес;Вместимость;Долгота;Широта\n"
        "10;МБОУ СОШ №5;общеобразовательное учреждение;ул. Первая, 1;800;38.005;47.003\n"
        "11;Детский сад №7;дошкольное образовательное учреждение;ул. Вторая, 2;120;;\n"
        "12;Городская поликлиника №1;поликлиника;ул. Третья, 3;;;\n"
        "13;Дом культуры;культурно-досуговое;ул. Четвертая, 4;;;\n")
    found = {r["key"]: r for r in registries.institutions(proj, "bus_gov")}
    assert set(found) == {"busgov-10", "busgov-11", "busgov-12"}
    assert found["busgov-10"]["poi_type"] == "school" and found["busgov-10"]["capacity"] == 800
    assert found["busgov-11"]["layer"] == "poi_education" and found["busgov-11"]["geometry"] is None
    assert found["busgov-12"]["poi_type"] == "clinic" and found["busgov-12"]["capacity"] is None


def test_hotels(proj, tmp_path):
    put(proj, tmp_path, "hotels", "Регистрационный номер;Наименование;Категория;Адрес;Количество мест;Долгота;Широта\n"
        "77;Гостиница Приморская;4 звезды;ул. Первая, 9;200;38.005;47.004\n")
    (record,) = registries.institutions(proj, "hotels")
    assert (record["layer"], record["poi_type"], record["capacity"]) == ("poi_hotels", "hotel", 200)


def test_rosstat_numbers(proj, tmp_path):
    put(proj, tmp_path, "rosstat", "Показатель;Единица измерения;Год;Значение;ОКТМО\n"
        "Численность населения;человек;2024;245 123,5;60000000\n")
    frame = registries.rosstat(proj)
    assert frame["value"].iloc[0] == 245123.5 and frame["year"].iloc[0] == 2024
