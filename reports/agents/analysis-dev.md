# analysis-dev: T4.1 изохроны и измерения

11.10.2026. Сохранено оркестратором из ответа исполнителя (запись файла исполнителю была запрещена).

## Что сделано

- `rules/analysis.yaml`, `schema/analysis.schema.json`, `cm_carto/analysis/params.py` — параметры: допуск узлов 0,5 м, скорости по классам для walk и drive (null — класс исключен), `assumption_id`, привязка точки до 100 м, допуск границы контура 25 м, буфер 40 м.
- `cm_carto/analysis/data.py` — слои из `geo/parts/*.gpkg` (или `geo/project.gpkg`), поиск по object_id.
- `cm_carto/analysis/graph.py`, `isochrone.py`, `measure.py`, `cli.py` (`isochrone`, `measure`).
- Тесты `tests/test_analysis_*.py`, `tests/analysis_fixtures.py` — 21 тест на решетке 100 м.
- `examples/taganrog/geo/parts/analysis-isochrones.gpkg` — 11 изохрон; `analysis-isochrones.calc.yaml` — 8 заготовок CALC.

## Команды

```
scripts/run.sh cm-carto isochrone examples/taganrog/project.yaml --from transit_stops:osm-n1501726533 --mode walk --minutes 5 10 15
scripts/run.sh cm-carto measure examples/taganrog/project.yaml isochrones:walk-10-transit_stops-osm-n1501726533
```

## Метод

1. Граф: слой roads хранит целые улицы (элементы OSM); перекресток — вершина, общая у двух линий (допуск 0,5 м); линии режутся в перекрестках и концах; мосты и тоннели без общей вершины не соединяются. Пешеходный граф 10 926 узлов, 1 154 из 1 178 км в одной компоненте; автомобильный 7 796 узлов, 1 024 из 1 050 км.
2. Пешком — все классы, кроме express, 4,8 км/ч; на автомобиле — без pedestrian и path, 80/50/40/30/20 км/ч (express/main/district/local/service). Граф неориентированный.
3. Старт: точка — по ближайшему отрезку (до 100 м), контур — от узлов не дальше 25 м от границы.
4. Дейкстра по времени; на краю частичные ребра; полигон — буфер 40 м вокруг достижимых частей, дыры закрыты, без упрощения (упрощение ломало вложенность).
5. Слой isochrones: `speed_kmh` — средняя по достижимой сети; object_id `isochrones:<режим>-<минуты>-<origin_id>`.
6. Измерение: площадь по геометрии, длина достижимой сети по графу; изохрона, не совпадающая с пересчетом, — ошибка.

## Образцы К1 compare

10 минут пешком от двух станций: «Таганрог-II» (`transit_stops:osm-n1501726533`) и «Таганрог-Пассажирский» (`transit_stops:osm-n9683398095`) — зоны сравнимого размера в одном масштабе 1:20 000. Пешком против автомобиля в один масштаб не ложится (4 566 га против 87–133 га).

| Источник | Режим | Мин | Площадь, га | Скорость, км/ч | Сеть, км |
|---|---|---|---|---|---|
| Таганрог-II | пешком | 5 / 10 / 15 | 18,9 / 87,3 / 241,8 | 4,8 | 13,22 (10 мин) |
| Таганрог-Пассажирский | пешком | 5 / 10 / 15 | 29,7 / 132,7 / 294,0 | 4,8 | 26,53 (10 мин) |
| контур ППТ | пешком | 5 / 10 / 15 | 117,2 / 268,6 / 445,9 | 4,8 | 57,55 (10 мин) |
| контур ППТ | на автомобиле | 5 / 10 / 15 | 1 527,8 / 4 566,4 / 7 037,9 | 29,9 / 27,2 / 26,2 | 686,7 (10 мин) |

## Тесты

21 passed (analysis); весь набор без `test_render_map.py` — 107 passed. Повтор на полигоне — тот же файл (sha256).

## Расхождение с заданием

`inputs` в заготовке CALC — идентификаторы записей по контракту fact, слои геопакета ими не являются: слои и даты — в `formula` и `sources` (`doc_id: geopackage:<часть>:<слой>`), `inputs` — [] или номер допущения скорости.

## Вопросы Роману

1. Два варианта на compare — две станции, 10 минут пешком? — Да. — Равный размер, один масштаб.
2. Скорости 4,8 км/ч пешком и 80/50/40/30/20 км/ч на автомобиле — допущения расчета с записями ASM? — Да, номер вписать в `assumption_id`. — Это не нормы и не данные документа.
3. `speed_kmh` на автомобиле — средняя по достижимой сети? — Да. — Контракт допускает одно число.
4. Как база регистрирует слой-вход в `sources` CALC? — `doc_id: geopackage:<часть>:<слой>`, `doc_status: reference`. — Нужен ответ базы.
5. Атрибуты `oneway` и `bridge` в слое roads (geopackage 1.2.0)? — Да. — Без них автомобильная изохрона завышена.

## Замечания

Пешеходный граф включает service и path — у дворов изохроны могут быть завышены; исключение классов — параметр. Время расчета: около 3–4 с на объект.

## Поправка 11.10.2026

Площади в таблице «Образцы К1 compare» выше — до исправления ориентации ребер (Э-008). Действующие значения — в `examples/taganrog/geo/parts/analysis-isochrones.calc.yaml`: станция Таганрог-II 17,2 / 84,9 / 229,8 га; Таганрог-Пассажирский 27,6 / 126,5 / 282,5 га; контур ППТ пешком 114,6 / 254,5 / 436,7 га; на автомобиле 1 512,5 / 4 545,5 / 7 033,4 га. Изохроны и буферы — статус `calc` (geopackage 1.1.0).

## D8a, 12.10

Задача: реестр норм и буферы под fact 1.1.0 и geopackage 1.2.0 (решения Романа К-039, К-044, К-045). Исполнитель — Sonnet, усилие обычное.

### Что изменено по файлам

- `examples/taganrog/registry/norms.yaml`: id `NORM-9xxxx` -> `NRM-9xxxx`; `applicability` — объект (`condition` — прежний текст, `status: unchecked`, `note`); у норм придорожной полосы условие теперь по категориям дороги (NRM-90009 — IА, IБ, IВ; NRM-90010 — II; NRM-90011 — III), а не «по классу улицы». `doc_status: reference` уже соответствовал 1.1.0.
- `examples/taganrog/registry/assumptions.yaml`: добавлено `ASM-9003` (временный номер) — «соответствие класса улицы категории автомобильной дороги»; таблица в `value`: `express: IБ; main: II; district: III`. ASM-9001 и ASM-9002 проверены по fact 1.1.0, правок не нужно.
- `rules/analysis.yaml`: все ссылки на `NRM-9xxxx`; убран `buffers.own_data_license`. Способы выбора нормы: `norm` (прямо), `norm_by_attr` (одна форма на все атрибуты-категории: `attr`, `title` для сообщения об ошибке, `norms` значение -> запись, необязательное `if_missing: {attr, assumption_id}`), `norm_by_length` (теперь `{attr, title, bands}`). Правила `szz-industry-iv` и `szz-industry-v` слиты в `szz-industry` (участки по `hazard_class`), добавлено `szz-industry-poi` (объекты `poi_other` по `hazard_class`; таблица общая через якорь YAML); `roadside-express/main/district` слиты в `roadside` (по `road_category`, при отсутствии — `road_class` через ASM-9003); `water-protection-river` берет длину из `water.length_km`. В `kind` правил с `norm_by_attr` есть `{value}` — значение категории («класса IV», «категории II»). `object_id` буфера теперь `buffers:szz-industry-parcels-…` (было `szz-industry-iv-…`).
- `schema/analysis.schema.json`: только `NRM-NNNNN` (NORM- не принимается нигде); `own_data_license` убран; описаны `norm_by_attr` и `norm_by_length`; `oneOf` из трех способов.
- `cm_carto/analysis/norms.py`: переписан. `read/load/load_assumptions` (допущения — `assumptions.yaml` рядом с реестром норм), `get` (запись `applicability.status: not_applicable` — ошибка «норма процитирована верно, но неприменима к объекту проекта»; `unchecked` и `applicable` проходят), `correspondence` (таблица допущения из `value`), `pick(project, rule, frame)` — норма для каждого объекта; ошибки собираются по всем объектам с перечнем `object_id`: «класс опасности вводит автор проекта: атрибут hazard_class слоя parcels не задан у [...]», «в правиле нет нормы для значения hazard_class = III (есть: IV, V): [...]», «в допущении ASM-9003 нет соответствия для road_class = local: [...]». `length_km` по геометрии удалена (не нужна). Допущение читается лениво — только если у какого-то объекта нет категории.
- `cm_carto/analysis/buffers.py`: норма и `fact_ids` — из `norms.pick` (при ASM-соответствии `fact_ids` = [NRM, ASM]); `source_license` копируется только если он есть у источника (`CARRIED`), `licence_of` убрана; docstring под NRM и geopackage 1.2.0.
- `cm_carto/analysis/count.py`: только комментарий NORM -> NRM (подсчеты лицензию не пишут, `inputs` — `fact_ids` зоны). `cm_carto/analysis/cli.py`: в строке отчета buffer — все `fact_ids`.
- `tests/buffer_fixtures.py`: `parcel(..., hazard_class)`, `water(..., length_km)`, `poi(..., hazard_class)`, `road(key, y, road_class, road_category)`, `edit_registry(..., applicability, file)`, копируется и `assumptions.yaml`.
- `tests/test_analysis_buffers.py`, `tests/test_analysis_count.py`: переписаны под новые правила (список ниже).
- `MAPS_USAGE.md` (абзац о нормах), `examples/recipes/territory-restrictions.yaml` (комментарий): NORM -> NRM, описан ввод атрибутов автора.
- `examples/taganrog/geo/parts/analysis-buffers.calc.yaml`: пересобран (см. «Что изменится в examples/»).

### Как проверено

Тесты на правила: норма по классу опасности (участок IV -> 100 м NRM-90003, V -> 50 м NRM-90002, `poi_other` — то же); объект без `hazard_class` — ошибка с перечнем id, id участка с классом в сообщение не попадает; класс III без нормы — ошибка; придорожная по `road_category` (IБ -> NRM-90009) и по ASM (`main` -> NRM-90010, `fact_ids` = [NRM-90010, ASM-9003]; смена таблицы в записи ASM меняет норму без правки кода; класс `local` без соответствия, битая запись, нет записи — ошибки); река по `length_km` (геометрия 3 км при `length_km` 120 -> 200 м) и без него — ошибка с id; лицензия: у буфера от КПТ поля нет, от OSM — ODbL 1.0, геопакет проходит `gpkg.check`, в слое NULL у КПТ; `not_applicable` — ошибка, `unchecked` и `applicable` — проходят; реестр с `applicability` строкой и id `NR-…` не проходит fact 1.1.0; синтетические реестры норм и допущений проходят fact 1.1.0, все id `NRM-9` / `ASM-9`; схема правил принимает только `NRM-` (в трех местах), `NORM-` — отказ; атрибуты правил и значения категорий сверены с перечнями контракта geopackage (`IА` — кириллица), таблица ASM — со значениями `road_class` и `road_category` контракта; в `rules/analysis.yaml` и `cm_carto/analysis/*.py` нет `NORM`; `test_rules_have_no_distances`, `test_code_has_no_norm_numbers`, `test_no_project_words` проходят. Пересборка заготовок CALC буферов К1 в копии полигона — см. ниже.

Итоговая строка полного набора: `20 failed, 310 passed` (2:13). Все 20 — `tests/test_analysis_isochrone.py` (12) и `tests/test_analysis_measure.py` (8), `KeyError: 'directed'`: другой исполнитель (граф, односторонние режимы) ждет в `rules/analysis.yaml` параметр `modes.<режим>.directed`, а в схеме и правилах его еще нет. Файл правил мой, но параметр описывает тот исполнитель; я его не добавлял, чтобы не угадывать значения и не разойтись с его схемой. `tests/test_analysis_buffers.py`, `tests/test_analysis_count.py`, `tests/test_no_project_words.py`, `tests/test_recipes.py`, `tests/test_mcp.py` — 52 passed. До правок падали 21 тест буферов и подсчетов — теперь проходят.

### Что изменится в examples/

`scripts/build_k1.sh` буферы и подсчеты не делает (изохроны, `ingest`, `fetch overture`, слияние частей, карты, листы), карты `maps/k1-*.yaml` слой `buffers` не используют (в `k1-zoning.yaml` — `zouit`, а не `buffers`), значит сборка К1 новыми правилами не затронута. Часть `analysis-buffers.gpkg` в `.gitignore`, в чистом клоне ее нет; если она осталась локально от прошлых сборок, `build_k1.sh` вольет ее в `project.gpkg` (`parts/*.gpkg`) — она старая (объекты `szz-industry-iv-…`) и лежит с 11.10, ее надо удалить или пересобрать командами ниже.

Изменены:
1. `examples/taganrog/registry/norms.yaml` и `assumptions.yaml` — см. выше.
2. `examples/taganrog/geo/parts/analysis-buffers.calc.yaml` (в git; заготовки CALC подсчетов в зонах буферов): `inputs` с `NORM-90003` / `NORM-90005` -> `NRM-…`, `subject` и `formula` — `szz-industry-parcels-…` вместо `szz-industry-iv-parcels-…`, подписи источников — «по NRM-…». Значения те же (55 улиц / 2,51 км; 4 квартала / 4,08 га; 5 участков / 3,94 га; 2 объекта POI; река — 0). Пересобран настоящими командами в копии полигона вне репозитория (`buffer --rule szz-industry --from parcels:00-00-0000000-7`, `buffer --rule water-protection-river --from water:osm-w390570032`, затем `count` по тем же слоям и отбору, что были).
3. Для пересборки в копии пришлось задать атрибуты автора, как их задал бы автор в QGIS: `hazard_class = IV` у участка `parcels:00-00-0000000-7` (в `ingest-kpt-sample.gpkg`) и `length_km = 8` у реки `water:osm-w390570032` (в `fetch-overture.gpkg`). В репозитории эти данные не менялись, поэтому в чистом клоне те же команды остановятся с перечнем id «класс опасности вводит автор проекта» и «длина водотока вводит автор проекта» — это ожидаемое поведение. Если оркестратор хочет, чтобы образец воспроизводился с чистого клона, атрибуты надо добавить в `ingest-kpt-sample.gpkg` (его пересобирает ingest из КПТ-образца, правила `rules/egrn.yaml` — у оркестратора) и в источник реки; решение за ним.

### Замечания и вопросы

- `cm_carto/project.py` (docstring `Project.registry`) и `docs/ПЕРЕНОС.md` (две строки), `README.md` (строка про `buffer`/`count`) еще говорят «записи NORM» — не мои файлы, правка в одно слово.
- Допущения читаются из `assumptions.yaml` рядом с реестром норм (в анкете отдельного поля нет). Если база отдаст допущения иначе (один общий реестр), поменяется только `norms.load_assumptions`.
- Формат таблицы допущения — строка `класс: категория; …` в `value` (схема fact 1.1.0 не допускает других полей); это согласовать с базой при выпуске настоящих ASM.
- Правила `szz-industry`, `szz-industry-poi`, `roadside` без `match`: объекты называют через `--from` (как и раньше у `szz-industry-iv/-v`); подбор по `match` для придорожной полосы всех улиц слоя был бы неверен.
- Классы IV и V дороги и классы опасности I–III норм в синтетическом реестре не имеют: такой объект останавливает буфер ошибкой «в правиле нет нормы для значения … (есть: …)».
