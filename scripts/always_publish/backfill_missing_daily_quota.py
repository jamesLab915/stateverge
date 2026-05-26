#!/usr/bin/env python3
"""Backfill missed daily long/shorts quota without exceeding daily maximum."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1]
_REPO = _SCRIPTS.parent
_PY = _REPO / ".venv_audio" / "bin" / "python3"
_LONG_QUEUE = _SCRIPTS / "nyc_auto" / "auto_publish_queue.py"
_SHORTS_QUEUE = _SCRIPTS / "nyc_auto" / "auto_publish_queue_shorts.py"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from always_publish.daily_delivery_state import (  # noqa: E402
    DAILY_LONG_REQUIRED,
    DAILY_SHORTS_REQUIRED,
    load_delivery_state,
)
from always_publish.quota_guard import check_quota  # noqa: E402
from always_publish.scheduler import load_config  # noqa: E402


def _run(cmd: list[str], *, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"dry_run": True, "cmd": cmd}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=False, cwd=str(_REPO))
        return {
            "returncode": r.returncode,
            "stdout_tail": (r.stdout or "")[-1200:],
            "stderr_tail": (r.stderr or "")[-800:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": repr(exc)}


def build_backfill_plan(*, dry_run: bool = True) -> dict[str, Any]:
    cfg = load_config()
    day = load_delivery_state().get("date") or ""
    quota = check_quota(str(day), cfg)
    state = load_delivery_state()
    long_remaining = int(state.get("long_remaining") or 0)
    shorts_remaining = int(state.get("shorts_remaining") or 0)
    actions: list[dict[str, Any]] = []
    py = str(_PY if _PY.is_file() else Path(sys.executable))

    if long_remaining > 0 and int(quota.get("allow_long") or 0) > 0:
        actions.append(
            {
                "kind": "long",
                "remaining": long_remaining,
                "cmd": [
                    py,
                    str(_LONG_QUEUE),
                    "--upload",
                    "--privacy-status",
                    "unlisted",
                    "--max-count",
                    "1",
                ],
                "result": _run(
                    [py, str(_LONG_QUEUE), "--upload", "--privacy-status", "unlisted", "--max-count", "1"],
                    dry_run=dry_run,
                ),
            }
        )

    shorts_to_run = min(shorts_remaining, int(quota.get("allow_shorts") or 0))
    for i in range(shorts_to_run):
        actions.append(
            {
                "kind": "shorts",
                "slot": i + 1,
                "remaining_before": shorts_remaining - i,
                "cmd": [
                    py,
                    str(_SHORTS_QUEUE),
                    "--upload",
                    "--privacy-status",
                    "unlisted",
                    "--max-count",
                    "1",
                ],
                "result": _run(
                    [py, str(_SHORTS_QUEUE), "--upload", "--privacy-status", "unlisted", "--max-count", "1"],
                    dry_run=dry_run,
                ),
            }
        )

    return {
        "ok": True,
        "version": "backfill_missing_daily_quota_v2",
        "dry_run": dry_run,
        "date": day,
        "long_required": DAILY_LONG_REQUIRED,
        "shorts_required": DAILY_SHORTS_REQUIRED,
        "long_remaining": long_remaining,
        "shorts_remaining": shorts_remaining,
        "quota": quota,
        "delivery_state": state,
        "actions": actions,
        "actions_planned": len(actions),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backfill missing daily Always Deliver quota")
    p.add_argument("--dry-run", action="store_true", help="Plan only; do not invoke queues")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    plan = build_backfill_plan(dry_run=bool(args.dry_run))
    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        print(
            f"backfill date={plan.get('date')} long_remaining={plan.get('long_remaining')} "
            f"shorts_remaining={plan.get('shorts_remaining')} actions={plan.get('actions_planned')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
