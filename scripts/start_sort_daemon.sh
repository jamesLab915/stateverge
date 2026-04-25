#!/usr/bin/env bash
# Background Envato sorter: watch ~/Downloads -> assets/envato
set -euo pipefail
ROOT="/Users/ziweizhang/StateVerge"
cd "$ROOT"
export PYTHONPATH="$PWD"
LOG="$ROOT/logs/envato_sort.log"
mkdir -p "$ROOT/logs"
PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi
nohup "$PY" -m src.utils.sort_envato --watch >>"$LOG" 2>&1 &
echo "envato sort started (pid=$!); log: $LOG"
