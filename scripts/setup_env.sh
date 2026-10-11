#!/usr/bin/env bash
# Окружение картографа в облаке (Linux) с нуля: micromamba, QGIS 3.44 и геостек, шрифты из токенов верстки, пакет.
#   scripts/setup_env.sh [папка окружения]      по умолчанию /opt/mm/envs/carto
# Сеть: github.com (micromamba), conda.anaconda.org (пакеты). На Mac — тот же environment.yml, см. README.
set -euo pipefail
ENV=${1:-/opt/mm/envs/carto}
MM=${MM:-/opt/mm/bin/micromamba}
export MAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX:-/opt/mm/root}
REPO=$(cd "$(dirname "$0")/.." && pwd)
DESIGN=${CM_DESIGN:-$REPO/../cm-design}

if [ ! -x "$MM" ]; then
  mkdir -p "$(dirname "$MM")"
  curl -sSL https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64 -o "$MM"
  chmod +x "$MM"
fi
"$MM" create -y -p "$ENV" -f "$REPO/environment.yml"
# Плагины PDAL для HDF собраны в conda-forge под другую версию hdf5 и печатают ошибку при каждом старте QGIS;
# облака точек картографу не нужны (experiences.md, Э-002).
"$MM" remove -y --force -p "$ENV" libpdal-hdf libpdal-icebridge
"$MM" run -p "$ENV" pip install -q -e "$REPO"

# Шрифты — файлы верстки (style.yaml: font.files), в систему пользователя.
mkdir -p "$HOME/.local/share/fonts"
cp "$DESIGN"/input/fonts/OpenSans-*.ttf "$HOME/.local/share/fonts/"
fc-cache -f > /dev/null
"$REPO/scripts/run.sh" cm-carto doctor
