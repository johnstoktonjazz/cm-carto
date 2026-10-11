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
| `cm-carto ingest` | DXF, КПТ, SHP, GPKG → часть геопакета и отчет импорта | этап 1 |
| `cm-carto fetch` | внешние источники → часть геопакета и кеш с датой и лицензией | этап 2 |
| `cm-carto render` | карта по описанию: PNG в размер места, легенда, `provenance.json` | этап 3 |
| `cm-carto isochrone`, `measure` | изохроны, буферы, измерения как заготовки `CALC` | этап 4 |

## Устройство

- `cm_carto/project.py` — анкета `project.yaml` (система координат, охват, папки).
- `cm_carto/gpkg.py` — запись, чтение, проверка и слияние геопакета по контракту; части `geo/parts/*.gpkg` → `geo/project.gpkg`.
- `cm_carto/tokens.py`, `paths.py` — токены и стили верстки, контракты.
- `examples/taganrog/` — город-полигон (К-001); `input/taganrog/` — образцы и кеш внешних источников.
