#!/usr/bin/env bash
# Контрольная точка К1 на полигоне: части геопакета → project.gpkg → три карты → листы верстки → линтер →
# рендер листов (PDF, PNG) → контактный лист. Запуск из любого места: scripts/build_k1.sh
#
# Верстка (cm-design, CM_DESIGN) только читается: ее пакеты cm_deck, qa, review работают из зеркала
# _build/cm-design (копия нужных папок), потому что cm-deck пишет кеш в _build/ своего корня. Зависимости cm-deck
# (python-pptx) — в окружении картографа; LibreOffice (soffice) и pdftoppm — системные.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESIGN="${CM_DESIGN:-$ROOT/../cm-design}"
RUN="$ROOT/scripts/run.sh"
EX="$ROOT/examples/taganrog"
OUT="$ROOT/_build/k1"
MIRROR="$ROOT/_build/cm-design"
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

echo "== карты"
"$RUN" python - <<PY
from pathlib import Path
from cm_carto.render import build
for name in ("k1-analysis", "k1-zoning", "k1-compare-a", "k1-compare-b"):
    for path in build.render(Path("$EX/maps") / f"{name}.yaml"):
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
echo "== линтер верстки"
# Ошибки линтера не останавливают рендер и контактный лист (они показывают ошибки на листах); код выхода — в конце.
lint=0
"${DECK[@]}" lint "$OUT/k1.pptx" --slides "$EX/sheets" -o "$OUT/lint.json" || lint=$?
echo "== рендер листов"
"${DECK[@]}" render "$OUT/k1.pptx" -o "$OUT/render"
echo "== контактный лист"
"${DECK[@]}" review "$OUT/k1.pptx" "$OUT/render" -o "$OUT/review/index.html" --lint "$OUT/lint.json"
echo "К1: $OUT/k1.pptx, $OUT/render/, $OUT/review/index.html; линтер: $([ $lint = 0 ] && echo 0 ошибок || echo есть ошибки, $OUT/lint.json)"
exit $lint
