# Как заказать карту

Карта — это описание (`.yaml`) и команда. Картограф строит подложку PNG строго в размер места макета верстки, рядом кладет `provenance.json` и `overlay.json`. Та же команда на тех же данных дает тот же файл (байт в байт). Стандарт карт — `cm-design/MAPS.md`; стили, места и роли — токены `cm-design/tokens/map.yaml`. Цветов и толщин в описании нет.

## 1. Описание карты

Схема — `schema/map.schema.json`; описание проверяется один раз при загрузке, ошибка называет поле.

| Поле | Что задает |
|---|---|
| `place` | место макета: `sheet`, `wide`, `landscape`, `landscape_wide`, `square_large`, `square`, `portrait`, `square_small`, `thumb`; размер PNG равен размеру места |
| `project` | `project.yaml` проекта, путь от папки описания (система координат `territory.crs`, папка геоданных `carto.geo`) |
| `data` | геопакет, если не `<carto.geo>/project.gpkg` |
| `center` + `scale` | центр (`[x, y]` или `object_id`) и знаменатель масштаба |
| `extent` | охват вместо центра и масштаба: `[xmin, ymin, xmax, ymax]` или `object_id`; масштаб подбирается сам |
| `legend` | ширина места легенды, мм (`59`, `91`, `123`) или `inside` — легенда внутри карты |
| `layers` | роли карты (см. ниже) |
| `title`, `fact_ids` | подпись картинки; записи реестра, которые карта показывает |
| `compare_with` | описания соседних карт сравнения |
| `out` | подложка PNG, путь от папки описания |

### Роли `layers`

Ключ — роль из `map.yaml` (стиль уже в верстке), значение — откуда брать объекты:

```yaml
layers:
  point_1: {layer: poi_education, where: "poi_type = 'school'", within: isochrones:<КЛЮЧ>, title: Школы}
  reach_walk: {layer: isochrones, where: "object_id = 'isochrones:<КЛЮЧ>'"}
  blocks: {}          # слой геопакета с именем роли, без отбора
```

- `layer` — слой геопакета (по умолчанию имя роли); геометрия слоя должна подходить роли (точка, линия, площадь).
- `where` — отбор по атрибутам (синтаксис OGR SQL).
- `within` — `object_id` контура: точки остаются только внутри контура, линии и площади — пересекающие его. Именно так считаются «школы в изохроне»: на карте и в счете одни и те же объекты.
- `title` — строка легенды вместо стандартной.
- `label` — атрибут слоя геопакета (атрибут контракта или общее поле `name`; строка или целое), по которому подписываются объекты роли: номер квартала, индекс зоны, номер участка. Заменяет поле подписи стандарта, остальное правило стандарта (`max_features`, `min_size`) действует; поле не из контракта слоя — ошибка при загрузке. Подпись — `kind: label` в `overlay.json`: площадь подписана в центре внутри себя, при тесноте — рядом вне объекта на плашке с выноской (`leader_to` на объекте); не встала и так — строка в логе.
- `label_style` — стиль подписи, ключ `map.yaml: text.styles` (`label_major`, `label_minor`, `label`). Нужен вместе с `label` у роли, которой в стандарте не задано правило подписи (`blocks`); у роли с правилом (`parcels`, `zoning`) заменяет его стиль. Роль подложки с подписью в растре (улицы, вода, зелень) `label` не принимает.

```yaml
  blocks: {label: num, label_style: label_major}     # номера кварталов
  zoning: {layer: zones_pzz, label: code}            # индексы зон (стиль и min_size — из стандарта)
  parcels: {layer: sites, label: num}                # номера участков
```

Роли: `point_1`, `point_2`, `point_3` (группы объектов анализа), `photo_point`, `reach_walk`, `reach_drive`, `arrow`, `arrow_accent`, `leader`, `main_object`, `stop`, `buildings_proposed`, `territory`, `outside_owned`, `parcels`, `zone_sanitary`, `zone_water_protection`, `zone_roadside`, `zone_right_of_way`, `zoning` (зоны ПЗЗ, категории из поля `zone`), подложка: `road_express`, `road_main`, `road_district`, `road_local`, `road_pedestrian`, `railway`, `buildings_existing`, `green`, `water`, `blocks`. Незнакомая роль — ошибка со списком допустимых.

Подложка нужна всегда: без роли уровня подложки (`blocks`, `road_*` и др.) в охвате карта не собирается.

### Сравнение

Две карты с `compare_with`, названными друг другом: одно место, один масштаб, одна ширина легенды, один набор ролей. Легенда у обеих одна: строки по обеим картам.

```yaml
# а.yaml                        # б.yaml
compare_with: [b.yaml]          compare_with: [a.yaml]
```

## 2. Что вернется

Для `out: images/k1.png`:

| Файл | Что это |
|---|---|
| `images/k1.png` | подложка в размер места, 300 dpi, без привязки и метаданных; только слои подложки |
| `images/k1.provenance.json` | происхождение подложки по контракту `image`: источник, дата, статус, масштаб, слои |
| `images/k1.overlay.json` | остальное по контракту `overlay`: объекты данных, подписи, легенда, масштабная линейка в мм места; верстка ставит это редактируемыми объектами PowerPoint |
| `images/k1.preview.png` | только с `--preview`: overlay поверх подложки, для проверки глазами, не выпуск |

В консоль идет отчет: сколько объектов роли попало в охват, какие подписи не встали без наездов. Данные с запретом на печать (Яндекс, 2ГИС, Google) и подложка со статусом, которого нет в контракте `image`, останавливают сборку с понятным текстом. Повтор с теми же входами берется из кеша (`_build/cache`).

## 3. Типовые карты (рецепты)

Шаблоны в `examples/recipes/`. Скопируйте нужный в `maps/` проекта, замените `<КЛЮЧ_…>` ключами `object_id` из геопакета (список слоя: `ogrinfo -al geo/project.gpkg <слой>` или QGIS), запустите шаги из заголовка файла.

| Рецепт | Файл | Цепочка |
|---|---|---|
| Транспортная доступность | `transport-access.yaml` | `isochrone` от остановки (5, 10, 15 минут) → `measure` → `render` |
| Обеспеченность школами и садами | `school-kindergarten-provision.yaml` | `isochrone` от границы → `count` школ и садов в изохроне → `render` |
| Ограничения территории | `territory-restrictions.yaml` | `buffer` по правилам СЗЗ и водоохранных зон → `count` участков в зоне → `render` |

Размеры зон берутся из записей NRM реестра проекта (`rules/analysis.yaml`, `buffers.rules`), числа расстояний в описаниях и коде не пишутся. Класс опасности (`hazard_class` участка и объекта), категорию дороги (`road_category`) и длину водотока (`length_km`) вводит автор проекта атрибутами слоя; без них правило останавливается с перечнем `object_id`. Категория дороги без атрибута берется по классу улицы из допущения ASM (`assumptions.yaml` рядом с реестром норм), номер допущения попадает в `fact_ids` буфера. Лицензия буфера — лицензия источника, если она у него есть (OSM); у буфера от КПТ и ППТ ее нет. Измерения (`measure`, `count`) выдаются заготовками `CALC` со статусом `check`: формула и слои-входы записаны, регистрирует их база. В подписи карты идет число из реестра, если оно там есть, а не измерение.

## 4. Команды

```bash
scripts/run.sh cm-carto doctor                                   # окружение годно?
scripts/run.sh cm-carto ingest examples/taganrog/project.yaml input/taganrog/dxf
scripts/run.sh cm-carto fetch  examples/taganrog/project.yaml osm --file выгрузка.osm --date 2026-10-01
scripts/run.sh cm-carto isochrone project.yaml --from transit_stops:<КЛЮЧ> --mode walk --minutes 5 10 15
scripts/run.sh cm-carto measure project.yaml isochrones:<КЛЮЧ>
scripts/run.sh cm-carto buffer  project.yaml --rule <ПРАВИЛО>
scripts/run.sh cm-carto count   project.yaml --zone buffers:<КЛЮЧ> --layer parcels
scripts/run.sh cm-carto render maps/k1.yaml --preview
```

## 5. MCP

`cm-carto-mcp` (FastMCP, stdio) — те же функции, что у командной строки, с теми же полями: `doctor`, `ingest`, `fetch`, `render`, `isochrone`, `measure`, `buffer`, `count`; плюс `qgis_project` — проект `.qgz` по описанию карты для проверки глазами (слои и стили верстки, геопакет только на чтение). Ответ — вывод команды: пути результатов и короткий отчет; ошибка команды — ошибка инструмента с тем же текстом.

Относительные пути считаются от папки проекта (`CM_CARTO_PROJECT`, по умолчанию — текущая папка сервера). Подключение — `.mcp.json` в корне репозитория; интерактивная работа в QGIS — `docs/QGIS_MCP.md`. Запрос агента: «собери карту по `maps/k1.yaml`» = вызов `render` с `specs: ["maps/k1.yaml"]`.
