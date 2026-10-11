#!/usr/bin/env bash
# Команда в окружении картографа: scripts/run.sh cm-carto doctor
set -euo pipefail
ENV=${CM_CARTO_ENV:-/opt/mm/envs/carto}
export MAMBA_ROOT_PREFIX=${MAMBA_ROOT_PREFIX:-/opt/mm/root}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/tmp/runtime-$(id -u)}
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
exec "${MM:-/opt/mm/bin/micromamba}" run -p "$ENV" "$@"
