#!/usr/bin/env bash
# Background Envato sorter: watch ~/Downloads -> assets/envato
#
# DISABLED on 2026-04-25 per user request.
# This script no longer launches the watcher automatically.
#
# Manual one-shot scan:
#   cd ~/StateVerge && export PYTHONPATH="$PWD" && python -m src.utils.sort_envato --once
#
# Manual foreground watcher:
#   cd ~/StateVerge && export PYTHONPATH="$PWD" && python -m src.utils.sort_envato --watch
#
# To re-enable auto start, restore the old nohup command manually.

set -euo pipefail

echo "[start_sort_daemon] DISABLED — Envato watcher is not started." >&2
exit 0
