#!/usr/bin/env bash
# Контрольная точка К1 на полигоне: части геопакета → project.gpkg → три карты (четыре файла: сравнение — две карты)
# в новом виде — подложка PNG + provenance (контракт image) + overlay.json (контракт overlay) + превью для проверки.
# Запуск из любого места: scripts/build_k1.sh
#
# Сборка листов cm-deck (build, lint, render, review из копии верстки в _build/cm-design, заход 2) снята до поддержки
# overlay в cm-deck; как ее вернуть — reports/agents/render-dev.md, «Заход 2».
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/scripts/run.sh"
EX="$ROOT/examples/taganrog"
cd "$ROOT"

ls "$EX"/geo/parts/fetch-overture.gpkg "$EX"/geo/parts/ingest-*.gpkg >/dev/null

echo "== геопакет проекта из частей"
"$RUN" python - <<PY
from pathlib import Path
from cm_carto import gpkg, project
p = project.load(Path("$EX/project.yaml"))
parts = sorted(p.geo.glob("parts/*.gpkg"))
print("части:", ", ".join(x.name for x in parts))
# Слияние с проверкой контракта — больше минуты на полигоне; те же части (input_hash) — файл уже собран.
if p.gpkg.is_file() and gpkg.meta(p.gpkg)["input_hash"] == gpkg.input_hash(*parts):
    print(p.gpkg, "— части не менялись")
else:
    gpkg.merge(parts, p.gpkg, p.name, p.crs, p.crs_name)
    print(p.gpkg)
PY

echo "== карты: подложка PNG, provenance, overlay.json, превью"
"$RUN" python - <<PY
from pathlib import Path
from cm_carto.render import build
for name in ("k1-analysis", "k1-zoning", "k1-compare-a", "k1-compare-b"):
    for path in build.render(Path("$EX/maps") / f"{name}.yaml", preview=True):
        print(path.relative_to(Path("$ROOT")))
PY

# Листы cm-deck — когда верстка научится ставить overlay.json (контракт overlay 1.0.0): сейчас cm-deck вставляет
# картинку целиком, а легенда, подписи и линейка теперь в overlay. Описания листов — examples/taganrog/sheets/.
echo "К1: карты и превью — $EX/maps/images/"
