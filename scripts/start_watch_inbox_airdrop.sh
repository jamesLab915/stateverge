#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/StateVerge" || exit 1
SV_ROOT="${STATEVERGE_VOL:-/Volumes/StateVerge}"
LOG_VOLUME="${SV_ROOT}/07_AUTOMATION/logs/watch_inbox_airdrop.log"
LOG_FALLBACK="${HOME}/Library/Logs/StateVerge/watch_inbox_airdrop_launchd.log"
if [[ -d "${SV_ROOT}" ]]; then
  LOG="${LOG_VOLUME}"
else
  LOG="${LOG_FALLBACK}"
fi
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
echo "===== $(date -u +"%Y-%m-%dT%H:%M:%SZ") start_watch_inbox_airdrop ====="
exec python3 "${HOME}/StateVerge/scripts/watch_inbox_airdrop.py"
