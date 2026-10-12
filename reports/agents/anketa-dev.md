# Отчет anketa-dev: поля картографа в разделе `carto` анкеты

## Что изменено

Код (`cm_carto/`)
- `project.py`: докстринг анкеты в новой форме; `CARTO_FIELDS`; свойство `carto` (dict раздела), `extent`, `fetch_extent_wgs84`, `language`, `geo`, `rules` читают раздел; новые свойства `inputs`, `overture_release`, `registry`. Загрузка: обязательны `name`, `crs`, `carto`, `carto.extent`, `carto.geo`; поле картографа на верхнем уровне - ValueError «<путь>: поле <имя> - в разделе carto анкеты (верхний уровень - поля базы, К-045)»; формат охватов проверяется как раньше (в тексте ошибки `carto.extent`). Посторонние поля базы на верхнем уровне допустимы.
- `fetch/osm.py`, `fetch/overture.py`, `fetch/registries.py`: прямые `project.data[...]` заменены на `project.inputs`, `project.overture_release`. `analysis/norms.py`: функция `registry_path` удалена, везде `project.registry` (`analysis/buffers.py` переведен). После правки в `cm_carto/` нет обращений к `project.data` вне `project.py`.
- Тексты: `fetch/cli.py`, `fetch/overture.py`, `ingest/rules.py`, `analysis/norms.py` (упоминания полей анкеты -> `carto.*`).

Анкета и тесты
- `examples/taganrog/project.yaml` - в новой форме, комментарии сохранены.
- Фикстуры в новой форме: `tests/fetch_fixtures.py`, `test_render_map.py`, `test_mcp.py`, `test_project_crs.py` (anketa, тест без extent, локальные СК), `test_ingest_project_rules.py`, `test_no_project_words.py`.
- Новый `tests/test_project_anketa.py` (13 проверок): каждое из 8 полей картографа на верхнем уровне - ошибка с текстом про раздел carto; `stage: концепция` на верхнем уровне - грузится; нет `carto` - ошибка; нет `carto.extent`/`carto.geo` - ошибка; перевернутый охват - ошибка.
- `analysis_fixtures.py`, `buffer_fixtures.py`, `test_render_attr_labels.py`, `test_ingest_*` (кроме указанных) анкету не пишут (копируют анкету полигона) - не менялись.

Документы
- `docs/ПЕРЕНОС.md`: блок анкеты в новой форме, подразделы «Верхний уровень: поля базы» и «Раздел картографа `carto`» с таблицей (что значит каждое поле, что обязательно).
- `README.md`, `MAPS_USAGE.md`, `ПОДКЛЮЧЕНИЯ.md`, `rules/analysis.yaml` (комментарий), `schema/map.schema.json` (описание `data`) - упоминания полей анкеты переведены на `carto.*`.
- `DECISIONS.md`, `tasks.json` не тронуты.

Не читают полей анкеты и не менялись: `examples/recipes/*.yaml` (только `project: ../project.yaml` и `extent` описания карты - это не поле анкеты), `scripts/build_k1.sh` (только путь анкеты и `project.load`), `scripts/make_samples.py` (`rules` там - поле паспорта `.source.yaml`, не анкеты).

## Как проверено
- `scripts/run.sh python -m pytest -q`: **285 passed** (272 прежних + 13 новых), 7 предупреждений сторонних библиотек; прогон повторен после правок документов и схемы.
- `scripts/build_k1.sh`: **rc 0**, 5 мин 20 с; линтер верстки: 0 ошибок, 0 предупреждений (справка T19 про заголовок слайда 3 - прежняя).
- `git status` в `examples/` после сборки: изменена только `examples/taganrog/project.yaml`; `k1.pdf` показал изменение (дата создания LibreOffice) - возвращен `git checkout`. Отслеживаемые геопакеты, карты и листы не изменились.
- `input_hash` частей не изменился: хэш считается по байтам правил, реестра, данных и `crs_name`, текст анкеты в него не входит (`gpkg.input_hash`); отслеживаемые части ingest совпали с индексом.

## Что осталось
- Старая форма анкеты не поддерживается (одна форма). Анкеты других проектов, если они есть вне репозитория, нужно перенести в раздел `carto` перед следующим запуском: иначе ValueError с указанием поля.
- Сообщения ошибок кеша fetch (`в project.yaml {bounds}`) не менялись - охват там итоговый, не поле анкеты.
- Правки не закоммичены (коммитит оркестратор). Новый файл: `tests/test_project_anketa.py`.

## Анкета базы: project, territory, 12.10

Решение базы (cm_core 0.4.2): имя проекта - `project.name`, система координат - `territory.crs` объектом `{name, definition}`; картограф читает их и свое берет из `carto:`. Другие поля разделов `project` и `territory` и другие разделы он не знает и не трогает.

### Что изменено
- `cm_carto/project.py`: `Project.name` читает `project.name`, `crs_name` и `crs` - `territory.crs.name` и `territory.crs.definition`. `load` требует `project.name`, `territory.crs.name`, `territory.crs.definition`, `carto.extent`, `carto.geo` (ошибка «нет поля <путь>»). Верхнеуровневые `name` и `crs` - ошибка «поле <имя> на верхнем уровне - имя проекта - project.name, система координат - territory.crs (анкета базы, cm_core 0.4.2)» (даже если рядом есть новая форма: одна форма, без поддержки старой). Поля картографа на верхнем уровне - ошибка, текст прежний. Докстринг анкеты переписан в новой форме; отказ fetch теперь «нужны параметры перехода (territory.crs.definition: +towgs84 или WKT BOUNDCRS)» (начало фразы «нужны параметры перехода» прежнее, тесты на него не менялись).
- `examples/taganrog/project.yaml` - в новой форме, комментарии сохранены.
- Фикстуры анкет: `tests/fetch_fixtures.py`, `tests/test_render_map.py`, `tests/test_mcp.py`, `tests/test_project_crs.py` (4 места, включая подмену `crs` в `example.data`), `tests/test_no_project_words.py` (читает `project.name`, `territory.crs.*` примеров), `tests/test_project_anketa.py`. `test_render_attr_labels.py`, `test_ingest_project_rules.py` анкету не пишут (берут из примера, меняют только `carto`) - не менялись. Чужие тестовые файлы не правились; строк анкеты в них, кроме `tests/fetch_fixtures.py` (общая фикстура fetch и doctor - правка только строки анкеты), нет.
- Новые и переписанные тесты `tests/test_project_anketa.py`: старая форма (`name`, `crs` по отдельности) - ошибка с подсказкой; нет `project.name` / `territory.crs.name` / `territory.crs.definition` - ошибка «нет поля ...»; нет разделов `project` или `territory` - ошибка; посторонние поля в `project`, `territory` и посторонние разделы грузятся и не трогаются, `name`/`crs_name`/`crs` читаются из новых полей.
- Документы: `docs/ПЕРЕНОС.md` (блок анкеты, раздел «Поля базы: `project.name`, `territory.crs`», список обязательного, `territory.crs.definition` в абзаце про СК), `README.md`, `MAPS_USAGE.md`, `schema/map.schema.json` (описание `crs`: «совпасть с territory.crs.name анкеты»). `ПОДКЛЮЧЕНИЯ.md` полей `crs`/`name` анкеты не упоминает - не менялся. `scripts/` и `examples/recipes/` полей анкеты не читают (только `p.name`, `p.crs`, `p.crs_name` через `Project` и путь анкеты) - не менялись. Остальной код (`gpkg`, ingest, fetch, analysis, render) работает через свойства `Project` - не менялся.

### Как проверено
- `scripts/run.sh python -m pytest -q`: **381 passed**, 7 предупреждений сторонних библиотек. Первый прогон дал 4 падения в `tests/test_ingest_*.py` (dxf, project_rules, vector) - работа параллельного исполнителя ingest в момент прогона; после повторного прогона этих файлов и полного набора все проходят.
- Результат сборки не меняется: в геопакет и provenance идут те же `project.name` и `territory.crs.name` (значения прежние), `input_hash` от текста анкеты не зависит. `scripts/build_k1.sh` не запускался (оркестратор).

### Для сведения оркестратору
- Исполнителям ingest и doctor/cli: их файлы по анкете не правились; общий `tests/fetch_fixtures.py` теперь пишет анкету в новой форме.
- Правки не закоммичены.
