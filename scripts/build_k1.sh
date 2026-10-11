#!/usr/bin/env bash
# Контрольная точка К1 на полигоне, полный путь: части геопакета → project.gpkg → четыре карты (подложка PNG +
# provenance по контракту image + overlay.json по контракту overlay + превью) → временный перевод overlay для cm-deck →
# три листа cm-deck → линтер верстки → рендер PNG/PDF → контактный лист.
# Запуск из любого места: scripts/build_k1.sh
#
# Верстка (cm-design, CM_DESIGN) только читается: ее пакеты cm_deck, qa, review работают из зеркала
# _build/cm-design (копия нужных папок), потому что cm-deck пишет кеш в _build/ своего корня. Зависимости cm-deck
# (python-pptx) — в окружении картографа; LibreOffice (soffice) и pdftoppm — системные.
# Контракты для рендера и линтера (K02) — CM_CONTRACTS или подмодуль cm-contracts.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESIGN="${CM_DESIGN:-$ROOT/../cm-design}"
RUN="$ROOT/scripts/run.sh"
EX="$ROOT/examples/taganrog"
OUT="$ROOT/_build/k1"
MIRROR="$ROOT/_build/cm-design"
cd "$ROOT"
export CM_CONTRACTS="${CM_CONTRACTS:-$ROOT/cm-contracts}"

ls "$EX"/geo/parts/fetch-overture.gpkg "$EX"/geo/parts/ingest-*.gpkg >/dev/null

# Подложка из OSM — статус reference, а в контракте image 1.0.0 его нет: provenance подложки не проходит схему,
# и рендер падает (К-025). Для просмотра образцов до image 1.1.0 — K1_TRIAL_IMAGE=1: копия контрактов в _build,
# в image: status добавлен reference, больше ничего. Такие образцы в альбом не идут.
if [ "${K1_TRIAL_IMAGE:-0}" = 1 ]; then
  TRIAL="$ROOT/_build/contracts-image-reference"
  rm -rf "$TRIAL" && mkdir -p "$TRIAL" && cp -r "$CM_CONTRACTS/contracts" "$TRIAL/"
  "$RUN" python - "$TRIAL/contracts/image/schema.json" <<'PY'
import json, sys
path = sys.argv[1]
schema = json.load(open(path, encoding="utf-8"))
schema["properties"]["status"]["enum"].append("reference")
schema["description"] = "ПРОБА: image 1.0.0 + reference в status (предложение к 1.1.0), не контракт"
json.dump(schema, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
  export CM_CONTRACTS="$TRIAL"
  echo "== пробная схема image с reference: $TRIAL"
fi

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

# ВРЕМЕННО, до перехода cm-deck на контракт overlay: перевод в черновой формат cm_deck/overlay.py;
# удалить вместе с cm_carto/render/deck_overlay.py.
echo "== перевод overlay для cm-deck (временно)"
"$RUN" python -m cm_carto.render.deck_overlay "$EX"/maps/images/k1-*.overlay.json

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
lint=0
"${DECK[@]}" lint "$OUT/k1.pptx" --slides "$EX/sheets" -o "$OUT/lint.json" || lint=$?
echo "== рендер листов"
"${DECK[@]}" render "$OUT/k1.pptx" -o "$OUT/render"
echo "== контактный лист"
"${DECK[@]}" review "$OUT/k1.pptx" "$OUT/render" -o "$OUT/review/index.html" --lint "$OUT/lint.json"
echo "К1: карты $EX/maps/images/; листы $OUT/k1.pptx, $OUT/render/, $OUT/review/index.html; линтер: $([ $lint = 0 ] && echo 0 ошибок || echo есть ошибки, $OUT/lint.json)"
exit $lint
