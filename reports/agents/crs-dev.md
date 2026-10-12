# crs-dev: СК проекта без параметров перехода (дефект стыка №1)

## Что изменено

- `cm_carto/project.py`: обязательное поле анкеты теперь `extent` (охват в СК проекта), `extent_wgs84` необязателен
  (явный охват выгрузок внешних источников). Новое: свойство `extent`; метод `require_wgs84()` (одна явная проверка:
  `TransformerGroup(crs, 4326, allow_ballpark=False).transformers` пуст, значит перехода по параметрам нет, и
  `ValueError` «<имя СК анкеты>: система координат проекта не переводится в WGS84 — нужны параметры перехода ...»);
  `cached_property fetch_extent_wgs84` (проверка, затем `extent_wgs84` анкеты, иначе `extent` по контуру,
  сгущенному на 1/100 стороны, переведенному в WGS84, округление до 6 знаков). Старое свойство `extent_wgs84`
  удалено. Загрузка проверяет `extent` и `extent_wgs84` (4 числа, минимумы меньше максимумов). Докстринг анкеты обновлен.
- `cm_carto/fetch/common.py`, `osm.py`, `overture.py`: `project.extent_wgs84` -> `project.fetch_extent_wgs84`
  (охват, запрос Overpass, маркеры кеша). `registries.py` охват берет через `common.extent` (правки не потребовалось).
- `cm_carto/fetch/cli.py`: `project.require_wgs84()` первой строкой после загрузки анкеты, до любого источника:
  отказ раньше сети, кеша и копирования файлов (в том числе для реестров и Росстата).
- `examples/taganrog/project.yaml`: добавлен `extent: [486360, 5226270, 498490, 5236300]` (EPSG:32637, от
  extent_wgs84 по сгущенному контуру, наружу до 10 м); `extent_wgs84` оставлен, кеш Таганрога годен.
- `cm_carto/ingest/geom.py` (файл исполнителя правил, правка минимальная, 2 строки в `check_extent`): охват проверки —
  `box(*project.extent)` без перевода из WGS84; текст ошибки «extent». Без этого ingest в такой СК не работает.
  Если исполнитель правил перезапишет файл целиком, нужно сохранить эту замену.
- `render/build.py`: правок не потребовалось: `fromWkt(crs.to_wkt())` принимает ENGCRS и tmerc без towgs84,
  QGIS-проект, подложка, provenance и overlay (`crs` = `crs.name` анкеты) проверены тестом.
- Тесты: новый `tests/test_project_crs.py` (29 прогонов); фикстуры под обязательный `extent`:
  `tests/fetch_fixtures.py` (EXTENT в UTM, `fetch_extent_wgs84`), `tests/test_render_map.py` и `tests/test_mcp.py`
  (`extent` вместо `extent_wgs84: [0,0,1,1]`), `tests/test_ingest_support.py` (`origin` по `project.extent`,
  без Transformer; файл исполнителя ingest-тестов, правка в одной функции), `tests/test_no_project_words.py`
  (значения `extent` примеров тоже проверяются как проектные).

## Поведение pyproj 3.8 / PROJ 9.9 (проверено)

`Transformer.accuracy` равен -1 и у ballpark, и у перехода по towgs84, различить по нему нельзя. Различает
`allow_ballpark=False`: `TransformerGroup(...).transformers` пуст для tmerc на Красовском без towgs84 и для ENGCRS/LOCAL_CS
(у `Transformer.from_crs` в этом режиме для них ProjError); непуст для EPSG:32637, EPSG:28407 и tmerc+towgs84.

## Как проверено

- `tests/test_project_crs.py`: анкета без extent_wgs84 грузится, без extent — ошибка; EPSG:32637 и tmerc+towgs84 проходят
  проверку (и охват fetch из extent накрывает город); tmerc без towgs84 и ENGCRS отказывают, в том числе при явном
  extent_wgs84; fetch всех 8 источников в такой СК падает «нужны параметры перехода» при заблокированных
  `overture._select` и `requests.post`, без создания папок кеша и геопакета; ingest DXF-образца в обеих локальных СК
  дает геопакет в этой СК (CRS слоев, `cm_meta.crs`, `gpkg.check`); render карты проходит (PNG, provenance, overlay
  `map.crs` = имя СК анкеты); кеш Overture Таганрога совпадает с `fetch_extent_wgs84` примера.
- `grep -rn "4326\|WGS" cm_carto/ingest cm_carto/render cm_carto/analysis cm_carto/gpkg.py cm_carto/mcp cm_carto/qgis`:
  пусто (WGS84 остался только в `project.py` и `fetch/`).
- `cm-carto fetch project.yaml overture` на копии анкеты Таганрога при недоступной сети (HTTPS_PROXY на закрытый порт):
  отработал по кешу, `fetch-overture.gpkg` совпадает с лежащим в `examples/taganrog/geo/parts` байт в байт.
- Полный набор: `267 passed, 7 warnings in 128.53s` (`scripts/run.sh python -m pytest -q`).

## Что осталось

- `docs/ПЕРЕНОС.md:48` описывает анкету с `extent_wgs84` как обязательным: заменить на `extent` (обязательно) и
  `extent_wgs84` (необязательно); docs не мои. `DECISIONS.md:72` («вне `extent_wgs84`») устарело, запись 232 уже новая.
- Проект МСК-05 на Mac: добавить в анкету `extent` в МСК; для fetch понадобятся `+towgs84` или BOUNDCRS в `crs.definition`.
