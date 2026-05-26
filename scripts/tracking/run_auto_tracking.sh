#!/bin/sh
# StateVerge daily auto-tracking entry point. Runs once per launchd trigger.
#
# Non-invasive:
#   * no keyboard / screen monitoring
#   * no reading of personal folders
#   * only writes inside the StateVerge repo tree
#
# Each step is wrapped with `|| true` so a transient failure in one tracker
# never aborts the others.

REPO_ROOT="$(cd -- "$(dirname -- "$0")/../.." >/dev/null 2>&1 && pwd)"
cd "$REPO_ROOT" || exit 0

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "[run_auto_tracking] python not found; aborting."
    exit 0
fi

mkdir -p logs/tracking

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

echo "[run_auto_tracking] $(ts) start cwd=$REPO_ROOT"

"$PY" scripts/tracking/track_stateverge_activity.py --mode manual || true
"$PY" scripts/tracking/project_progress_tracker.py || true
"$PY" scripts/tracking/tool_usage_tracker.py || true
"$PY" scripts/tracking/dev_time_tracker.py --mode auto || true

echo "[run_auto_tracking] $(ts) done"
exit 0
