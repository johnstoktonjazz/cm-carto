# Отчет cli-dev: doctor по стилям шрифта, понятные ошибки CLI и fetch без сети

## Что изменено

Дефект 1 (`cm_carto/doctor.py`):
- `split_face(face, family)`: имя начертания из `style.yaml: font.faces` -> (семейство, стиль) по правилу. Семейство берется из `faces.regular` («Open Sans»), стиль — остаток имени после семейства, у regular — «Regular». Имя, не начинающееся с семейства, — `ValueError` с именем.
- `font_styles(family)`: `QFontDatabase().styles(family)`.
- `check_fonts`: начертание найдено, если стиль есть среди стилей семейства. Это то, как шрифт берет рендер (стили верстки: `fontFamily="Open Sans"` + `namedStyle="Regular"|"SemiBold"`, проверено grep по `design/qgis`). Сообщения: «нет семейства Open Sans» или «в семействе Open Sans нет стиля SemiBold (есть: ...)».
- На этом контейнере (Linux, fontconfig) у «Open Sans»: Light, Regular, Medium, SemiBold (+ курсивы и Bold), то есть та же схема, что на macOS; отдельные семейства «Open Sans Light» и т.п. проверка больше не использует.

Дефект 4:
- `cm_carto/cli.py: main` — одно место: `ValueError` и `FileNotFoundError` из команды печатаются как «ошибка: <текст>» в stderr, код 1, без трассировки. Прочие исключения идут с трассировкой. Многострочные тексты (списки нарушений контракта: `gpkg`, `spec`, `rules`) выводятся как есть: первая строка «ошибка: ...», дальше список. Ошибки «нет сети» и «объект не найден» — одна строка.
- `cm_carto/fetch/common.py: unreachable(source, host, cache, error)` — формирует `ValueError`: «нет доступа к <хост>: fetch <источник> требует сети или кеша <путь кеша>; <исходный текст одной строкой>».
- `cm_carto/fetch/osm.py: overpass()` — запрос Overpass вынесен в функцию; `requests.RequestException` (отказ соединения, прокси, таймаут, HTTP 4xx/5xx) -> `raise common.unreachable(...) from error`.
- `cm_carto/fetch/overture.py: download` — вокруг `_select` (S3 через pyarrow) `OSError` -> `raise common.unreachable(...) from error` (pyarrow отдает отказ S3 как OSError «AWS Error NETWORK_CONNECTION»; так же выглядит и отсутствующий выпуск).
- Реестры (`okn`, `dtp`, `bus_gov`, `hotels`, `rosstat`) и `reconcile` в сеть не ходят, только файлы (`--file`). `analysis/cli.py` и `render/cli.py` правок не потребовали: перехват в `main` покрывает их.
- MCP-сервер (`cm_carto/mcp/server.py`) не менялся: он вызывает `func(Namespace)`, не `main`; ошибки проверок там пробрасываются как исключения (FastMCP отдает текст), ненулевой код — `ToolError`. Теперь сетевой отказ fetch в MCP тоже приходит как `ValueError` с понятным текстом.

## Как проверено

Новые тесты:
- `tests/test_doctor_fonts.py`: разбор имен по правилу (без QGIS-вызовов), чужое семейство -> ошибка, токены дают одно семейство и стили Light/Regular/Medium/SemiBold, doctor видит шрифты этой машины, эмуляция macOS (monkeypatch `font_styles`: «Open Sans» -> [Light, Regular, Medium, SemiBold], других семейств нет) проходит, без SemiBold — ошибка с именем «Open Sans SemiBold», нет семейства — ошибка.
- `tests/test_cli_errors.py`: `measure` на несуществующую изохрону — одна строка «ошибка: ...», код 1, stdout пуст, без «Traceback»; несуществующий project.yaml — то же; `KeyError` (ошибка кода) не перехватывается.
- `tests/test_fetch_offline.py`: Overpass (`requests.post` бросает `requests.exceptions.ConnectionError`), ответ 504, Overture (`_select` бросает `OSError` как pyarrow) — сообщение с хостом, источником, путем кеша, исходным текстом одной строкой, `__cause__` сохранен, SOURCE.yaml не записан; через `cli.main` — одна строка в stderr, код 1.

Реальный отказ (прокси на закрытый порт, копия анкеты полигона во временной папке без кеша), код 1, без трассировки:
- osm: `ошибка: нет доступа к overpass-api.de: fetch osm требует сети или кеша <папка>/input/cache/osm; HTTPSConnectionPool(host='overpass-api.de', port=443): Max retries exceeded ... ProxyError(...)`
- overture: `ошибка: нет доступа к overturemaps-us-west-2 (S3): fetch overture требует сети или кеша <папка>/input/cache/overture-2026-09-24.0; When getting information for key ... AWS Error NETWORK_CONNECTION ...`

## Вывод doctor

```
QGIS 3.44.14-Solothurn; cm-contracts: /home/user/cm-carto/cm-contracts; cm-design: /home/user/cm-design; пробная карта: /tmp/cm-carto-doctor-pyvn5e8m
окружение годно
```

## pytest

`scripts/run.sh python -m pytest -q`: `381 passed in 170.65s (0:02:50)`.

## Замечания

- Отсутствующий геопакет (`geo/project.gpkg` нет, например, до ingest) дает `pyogrio.errors.DataSourceError`, а не `FileNotFoundError`: трассировка останется. Правка в `cm_carto/gpkg.py` (вне моих файлов): проверять наличие файла и бросать `FileNotFoundError` с подсказкой, какую команду запустить.
- После отказа сети остается пустая папка кеша без SOURCE.yaml (osm: `mkdir` идет до запроса); повторный запуск работает.
- Правка `ValueError` -> «ошибка:» в `main` скрывает трассировку и для `ValueError` из библиотек (pandas, shapely); по заданию это принято.
