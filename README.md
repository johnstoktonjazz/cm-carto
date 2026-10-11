# cm-carto — картограф инструмента CM

Единственный источник геометрии в инструменте CM: геопакет проекта по контракту `geopackage`, карты под места макетов верстки в ее стилях, пространственный анализ. Паспорт — `ЗАДАНИЕ_КАРТОГРАФ.md` у Романа; решения — `DECISIONS.md`; журнал ошибок — `experiences.md`; план — `tasks.json`; доклады — `reports/`.

## Рядом

| Что | Где | Зачем |
|---|---|---|
| `cm-contracts` | подмодуль `cm-contracts/` или `CM_CONTRACTS` | схемы `geopackage`, `image`, `fact` |
| `cm-design` | соседний клон `../cm-design` или `CM_DESIGN`, только чтение | токены `tokens/map.yaml`, `style.yaml`; стили и компоновки `design/qgis/`; шрифты `input/fonts/` |

## Установка

Облако (Linux):

```bash
git clone --recurse-submodules <cm-carto> && git clone <cm-design> cm-design   # рядом
cm-carto/scripts/setup_env.sh            # micromamba, QGIS 3.44 и геостек, шрифты, пакет, doctor
```

Команды — в окружении: `scripts/run.sh cm-carto doctor`, `scripts/run.sh python -m pytest`.

## Команды

| Команда | Что делает | Состояние |
|---|---|---|
| `cm-carto doctor` | QGIS без окна, шрифты из токенов, контракты и стили верстки, пробная карта и повтор байт в байт | готово |
| `cm-carto ingest <project.yaml> <файл или папка>` | DXF, КПТ, SHP, GPKG → часть геопакета и отчет импорта; рядом с DXF, SHP, GPKG — паспорт `<файл>.source.yaml` (правила, подпись, дата, лицензия) | готово |
| `cm-carto fetch <project.yaml> <источник>` | Overture, OSM напрямую, реестры (файлом) → часть геопакета, кеш с датой и лицензией, сверка; подключения — `ПОДКЛЮЧЕНИЯ.md` | готово; OSM напрямую и реестры проверены только на синтетике |
| `cm-carto render <описание.yaml> [--preview]` | карта по описанию (`schema/map.schema.json`): подложка PNG в размер места с `provenance.json` (контракт `image`), остальное — `overlay.json` в мм места (контракт `overlay`), превью для проверки, кеш | готово; образцы и листы К1 — `scripts/build_k1.sh` |
| `cm-carto isochrone`, `measure` | изохроны пешком и на автомобиле по графу улиц, измерения как заготовки `CALC` | готово |
| `cm-carto buffer`, `count` | буферы ограничений с расстоянием из записи `NORM` реестра; объекты, площадь и длина в зоне — заготовки `CALC` | готово; реестр полигона синтетический |
| `cm-carto-mcp` | MCP-сервер (FastMCP) с теми же инструментами плюс `qgis_project` (`.qgz` для проверки глазами); `.mcp.json` с серверами `cm-carto` и `qgis` | готово; qgis-mcp — на Mac, `docs/QGIS_MCP.md` |

## Документы

- `MAPS_USAGE.md` — как заказать карту, шаблоны карт рецептов `examples/recipes/`.
- `ПОДКЛЮЧЕНИЯ.md` — внешние источники.
- `docs/ПЕРЕНОС.md` — установка и перенос на Mac; `docs/QGIS_MCP.md` — интерактивная проверка в QGIS.

## Устройство

- `cm_carto/project.py` — анкета `project.yaml` (система координат, охват, папки).
- `cm_carto/gpkg.py` — запись, чтение, проверка и слияние геопакета по контракту; части `geo/parts/*.gpkg` → `geo/project.gpkg`.
- `cm_carto/tokens.py`, `paths.py` — токены и стили верстки, контракты.
- `examples/taganrog/` — город-полигон (К-001); `input/taganrog/` — образцы и кеш внешних источников.
