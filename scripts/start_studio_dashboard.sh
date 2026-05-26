#!/usr/bin/env bash
# Start the StateVerge Studio Dashboard locally.
#
# Default URL: http://127.0.0.1:8765
# If 8765 is taken, the next free port in 8766–8790 is used (unless you set
# SV_DASHBOARD_PORT explicitly — then the script exits with lsof detail if busy).
#
# Override:
#   SV_DASHBOARD_HOST=0.0.0.0 SV_DASHBOARD_PORT=9000 ./scripts/start_studio_dashboard.sh

set -euo pipefail

# Resolve project root from this script's location.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# Activate the project virtualenv (must already be created).
if [ ! -f ".venv/bin/activate" ]; then
  echo "[start_studio_dashboard] error: .venv not found at $PROJECT_ROOT/.venv" >&2
  echo "  create it first:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Sanity-check key deps.
python -c "import fastapi, uvicorn, jinja2" >/dev/null 2>&1 || {
  echo "[start_studio_dashboard] missing deps; installing from requirements.txt..." >&2
  pip install -r requirements.txt
}

HOST="${SV_DASHBOARD_HOST:-127.0.0.1}"

# True if something is listening on TCP port $1 (macOS lsof).
_port_in_use() {
  lsof -i ":$1" -P -sTCP:LISTEN -t >/dev/null 2>&1
}

if [ -n "${SV_DASHBOARD_PORT:-}" ]; then
  PORT="${SV_DASHBOARD_PORT}"
  if _port_in_use "${PORT}"; then
    echo "[start_studio_dashboard] error: port ${PORT} already in use (Address already in use)." >&2
    echo "  either:  kill the process below, or use another port:" >&2
    echo "    SV_DASHBOARD_PORT=8766 ./scripts/start_studio_dashboard.sh" >&2
    echo "  listening on ${PORT}:" >&2
    lsof -i ":${PORT}" -P -sTCP:LISTEN >&2 || true
    exit 1
  fi
else
  # No port requested — pick the first free slot in a small range so copy-paste
  # docs still work most of the time, but we don't fail fatally on 8765 alone.
  PORT=""
  try=8765
  while [ "${try}" -le 8790 ]; do
    if ! _port_in_use "${try}"; then
      PORT="${try}"
      break
    fi
    try=$((try + 1))
  done
  if [ -z "${PORT}" ]; then
    echo "[start_studio_dashboard] error: no free TCP port in 8765-8790" >&2
    exit 1
  fi
  if [ "${PORT}" != "8765" ]; then
    echo "[start_studio_dashboard] note: 8765 is busy — using http://${HOST}:${PORT}" >&2
  fi
fi

echo "[start_studio_dashboard] http://${HOST}:${PORT}"
exec uvicorn src.studio_dashboard.app:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --reload
