#!/usr/bin/env bash
# Контрольная точка К1 на полигоне, полный путь с чистого клона: выгрузка Overture (если части нет; из кеша
# inputs/cache, без кеша — с сети) → изохроны, которые называют описания карт (если части нет или ее input_hash
# устарел) → project.gpkg из частей → четыре карты (подложка PNG + provenance по контракту image + overlay.json по
# контракту overlay + превью) → три листа cm-deck → линтер верстки → рендер
# PNG/PDF → контактный лист → копия листов, рендера и отчета линтера в examples/taganrog/sheets/out/.
# Части ingest-* — в git (К-023); части fetch-* и analysis-* — нет, их собирает этот скрипт.
# Запуск из любого места: scripts/build_k1.sh
#
# Верстка (cm-design, CM_DESIGN) только читается: ее пакеты cm_deck, qa, review работают из зеркала
# _build/cm-design (копия нужных папок), потому что cm-deck пишет кеш в _build/ своего корня. Зависимости cm-deck
# (python-pptx) — в окружении картографа; LibreOffice (soffice) и pdftoppm — системные.
# Контракты для рендера и линтера — CM_CONTRACTS, иначе подмодуль cm-contracts (закреплен на main; соседний клон может
# стоять на ветке с неслитыми версиями).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESIGN="${CM_DESIGN:-$ROOT/../cm-design}"
RUN="$ROOT/scripts/run.sh"
EX="$ROOT/examples/taganrog"
OUT="$ROOT/_build/k1"
MIRROR="$ROOT/_build/cm-design"
cd "$ROOT"
if [ -z "${CM_CONTRACTS:-}" ]; then
  CM_CONTRACTS="$ROOT/cm-contracts"
fi
export CM_CONTRACTS

ls "$EX"/geo/parts/ingest-*.gpkg >/dev/null

echo "== выгрузка Overture"
if [ -f "$EX/geo/parts/fetch-overture.gpkg" ]; then
  echo "$EX/geo/parts/fetch-overture.gpkg — есть"
else
  "$RUN" cm-carto fetch "$EX/project.yaml" overture
fi

echo "== изохроны из описаний карт"
# Изохроны, которые карты называют (within, center, extent, where по object_id). Часть готова, если в ней все они и
# ее input_hash совпадает с пересчетом по тем же входам, что пишет isochrone.save (правила, система координат,
# файлы со слоем roads, object_id).
"$RUN" python - <<PY | while read -r origin mode minutes; do "$RUN" cm-carto isochrone "$EX/project.yaml" --from "$origin" --mode "$mode" --minutes $minutes </dev/null; done
import re
import sys
from collections import defaultdict
from pathlib import Path
from cm_carto import gpkg, project
from cm_carto.analysis import data, isochrone, params
p = project.load(Path("$EX/project.yaml"))
ids = sorted({i for f in Path("$EX/maps").glob("k1-*.yaml") for i in re.findall(r"isochrones:[\w.-]+", f.read_text(encoding="utf-8"))})
part = p.part("analysis-isochrones")
if part.exists():
    have = sorted(gpkg.read(part)["isochrones"]["object_id"])
    fresh = gpkg.meta(part)["input_hash"] == isochrone.part_hash(p, have)
    if fresh and set(ids) <= set(have):
        print(part, "— готова:", ", ".join(ids), file=sys.stderr)
        sys.exit()
todo = defaultdict(list)
for i in ids:
    mode, minutes, layer, key = re.fullmatch(r"isochrones:(walk|drive)-([\d.]+)-([a-z0-9_]+)-(.+)", i).groups()
    origin = f"{layer}:{key}"
    if isochrone.object_id(origin, mode, float(minutes)) != i:
        raise ValueError(f"{i}: не разобрать на источник, режим и срок")
    todo[origin, mode].append(minutes)
for (origin, mode), minutes in sorted(todo.items()):
    print(origin, mode, " ".join(minutes))
PY

echo "== геопакет проекта из частей"
"$RUN" python - <<PY
from pathlib import Path
from cm_carto import gpkg, project
p = project.load(Path("$EX/project.yaml"))
parts = sorted(p.geo.glob("parts/*.gpkg"))
print("части:", ", ".join(x.name for x in parts))
# Слияние с проверкой контракта — больше минуты на полигоне; те же части (input_hash) — файл уже собран.
if p.gpkg.is_file() and gpkg.meta(p.gpkg)["input_hash"] == gpkg.merge_hash(parts, p.attributes):
    print(p.gpkg, "— части не менялись")
else:
    gpkg.merge(parts, p.gpkg, p.name, p.crs, p.crs_name, p.attributes)
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

echo "== зеркало верстки"
rm -rf "$MIRROR" && mkdir -p "$MIRROR/input"
for d in cm_deck qa review tokens schema template design input/fonts; do
  cp -a "$DESIGN/$d" "$MIRROR/$d"
done
find "$MIRROR" -name __pycache__ -prune -exec rm -rf {} +
cp "$DESIGN/pyproject.toml" "$MIRROR/"
DECK=(env PYTHONPATH="$MIRROR" "$RUN" python -m cm_deck.cli)

echo "== листы"
mkdir -p "$OUT"
"${DECK[@]}" build "$EX/sheets" -o "$OUT/k1.pptx"
echo "== линтер верстки (CM_CONTRACTS=$CM_CONTRACTS)"
# Ошибки линтера не останавливают рендер и контактный лист (они показывают ошибки на листах); код выхода — в конце.
# Путь к pptx — от корня репозитория: линтер пишет его в lint.json как передан, а отчет лежит в git.
lint=0
"${DECK[@]}" lint "${OUT#$ROOT/}/k1.pptx" --slides "$EX/sheets" -o "$OUT/lint.json" || lint=$?
echo "== рендер листов"
"${DECK[@]}" render "$OUT/k1.pptx" -o "$OUT/render"
echo "== контактный лист"
"${DECK[@]}" review "$OUT/k1.pptx" "$OUT/render" -o "$OUT/review/index.html" --lint "$OUT/lint.json"
echo "== образцы в $EX/sheets/out"
mkdir -p "$EX/sheets/out"
cp "$OUT/k1.pptx" "$OUT/lint.json" "$OUT"/render/k1.pdf "$OUT"/render/k1-*.png "$EX/sheets/out/"
echo "К1: карты $EX/maps/images/; листы $OUT/k1.pptx, $OUT/render/, $OUT/review/index.html; линтер: $([ $lint = 0 ] && echo 0 ошибок || echo есть ошибки, $OUT/lint.json)"
exit $lint
