#!/usr/bin/env bash
# LaunchAgent helper: waits for StateVerge SSD, then serves the Flask vol dashboard.
# Default URL: http://127.0.0.1:8767 (SV_VOL_DASHBOARD_PORT overrides).

set -euo pipefail

SSD="${STATEVERGE_VOL:-/Volumes/StateVerge}"
PY="${SSD}/07_AUTOMATION/scripts/dashboard.py"
VENV_ACTIVATE="${HOME}/StateVerge/.venv/bin/activate"

until [[ -f "$PY" ]]; do
  sleep 15
done

# shellcheck disable=SC1090
source "$VENV_ACTIVATE"
export SV_VOL_DASHBOARD_PORT="${SV_VOL_DASHBOARD_PORT:-8767}"

exec python3 "$PY"
