# Журнал ошибок картографа

Что сломалось, почему, что сделано. Тест на правило — в `tests/`, если ошибка может повториться.

## Э-001. PyQGIS не импортируется из python окружения напрямую

11.10.2026. `/opt/mm/envs/carto/bin/python -c "import qgis"` — `ModuleNotFoundError`: пакет лежит в `share/qgis/python`, путь добавляет скрипт активации `etc/conda/activate.d/qgis-activate.sh`. Запуск — только через `micromamba run` (`scripts/run.sh`).

## Э-002. PDAL печатает ошибку при каждом старте QGIS

11.10.2026. `libpdal_plugin_reader_hdf.so` и `..._icebridge.so` собраны под `libhdf5_cpp.so.320`, в окружении `.310`. Облака точек картографу не нужны: пакеты `libpdal-hdf` и `libpdal-icebridge` удаляются при установке (`scripts/setup_env.sh`). После — вывод QGIS чистый.

## Э-003. GDAL печатает «PNG driver does not support update access» при экспорте компоновки

11.10.2026. QGIS после записи PNG открывает файл через GDAL на запись привязки. Картинка записана; сообщение глушится на время экспорта, итог экспорта проверяется по коду возврата (так же у верстки, `cm_deck/qgis_render.py`).

## Э-004. Хосты внешних источников закрыты сетью облака

11.10.2026. 403 от прокси: Overpass (все зеркала), Geofabrik, BBBike, openstreetmap.org, ФГИС ТП, НСПД, bus.gov.ru, opendata.mkrf.ru, data.gov.ru, openrouteservice, HuggingFace, Zenodo. Открыты: GitHub (включая raw), S3 Overture Maps, conda-forge, PyPI. Решение — К-003.
