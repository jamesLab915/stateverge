#!/usr/bin/env python3
"""Diagnose Agent Production Assist v1."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATEVERGE = Path.home() / "StateVerge"
CONTROL = Path.home() / "StateVerge_Control_Center"
PERMS = STATEVERGE / "config" / "agent_permissions.json"
YU = STATEVERGE / "scripts" / "nyc_auto" / "youtube_upload.py"
LONGQ = STATEVERGE / "scripts" / "nyc_auto" / "auto_publish_queue.py"
SHORTQ = STATEVERGE / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py"
SHORTJOB = STATEVERGE / "scripts" / "jobs" / "shorts_cut_upload_job.py"
IDLE = STATEVERGE / "scripts" / "agent_idle_asset_organizer.py"
MAIN = CONTROL / "backend" / "main.py"
PAGE = CONTROL / "frontend" / "src" / "app" / "server-mode" / "page.tsx"
ALLOWED = CONTROL / "remote_agent" / "allowed_tasks.json"
MOBILE = CONTROL / "remote_agent" / "mobile_command_agent.py"
OUTJ = CONTROL / "logs" / "agent_production_assist_diagnose.json"
OUTM = CONTROL / "logs" / "agent_production_assist_diagnose.md"


def _has(s: Path, needle: str) -> bool:
    return s.is_file() and needle in s.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    rows: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "checks": {}}
    ok = True
    rows["checks"]["perms"] = PERMS.is_file()
    if PERMS.is_file():
        data = json.loads(PERMS.read_text(encoding="utf-8"))
        p = data.get("permissions") if isinstance(data, dict) else {}
        rows["checks"]["real_upload_private"] = bool((p or {}).get("real_upload_private"))
        rows["checks"]["real_upload_public_false"] = not bool((p or {}).get("real_upload_public"))
        if not rows["checks"]["real_upload_private"] or not rows["checks"]["real_upload_public_false"]:
            ok = False
    else:
        ok = False
    for label, p, needle in (
        ("youtube_upload_agent", YU, "--agent-upload"),
        ("long_queue_agent", LONGQ, "--agent-private-upload"),
        ("shorts_queue_agent", SHORTQ, "--agent-private-upload"),
        ("shorts_job_agent", SHORTJOB, "--agent-private-upload"),
        ("idle_organizer", IDLE, "run-once"),
    ):
        rows["checks"][label] = _has(Path(p), needle)
        if not rows["checks"][label]:
            ok = False
    rq = Path("/Volumes/SV_TRANSFER/publish_pack/review_queue")
    fb = Path.home() / "StateVerge/data/review_queue"
    rows["checks"]["review_queue_writable"] = False
    for d in (rq, fb):
        try:
            d.mkdir(parents=True, exist_ok=True)
            rows["checks"]["review_queue_writable"] = rows["checks"]["review_queue_writable"] or os.access(
                d, os.W_OK
            )
        except OSError:
            continue
    rows["checks"]["backend_review"] = _has(MAIN, "/api/review-queue")
    rows["checks"]["backend_private_upload"] = _has(MAIN, "/api/agent/private-upload/run")
    rows["checks"]["frontend_review_panel"] = _has(PAGE, "Private Upload Review Queue")
    rows["checks"]["frontend_idle_panel"] = _has(PAGE, "Idle Asset Organizer")
    if ALLOWED.is_file():
        t = ALLOWED.read_text(encoding="utf-8", errors="replace")
        rows["checks"]["mobile_dry_run_long_safe"] = "dry_run_long_safe" in t
        if not rows["checks"]["mobile_dry_run_long_safe"]:
            ok = False
    else:
        ok = False
    rows["checks"]["mobile_agent_handlers"] = _has(MOBILE, "def _execute_dry_run_long")
    if not rows["checks"]["mobile_agent_handlers"]:
        ok = False
    rows["checks"]["plist_idle"] = (CONTROL / "launchagents/com.stateverge.idle.asset.organizer.plist").is_file()
    rows["ok"] = ok
    OUTJ.parent.mkdir(parents=True, exist_ok=True)
    OUTJ.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    OUTM.write_text(f"# agent_production_assist diagnose\n\nok={ok}\n", encoding="utf-8")
    print(json.dumps({"ok": ok}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
