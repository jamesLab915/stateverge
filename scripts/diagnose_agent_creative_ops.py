#!/usr/bin/env python3
"""Diagnose Agent Creative Ops v1."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATEVERGE = Path.home() / "StateVerge"
CONTROL_CENTER = Path.home() / "StateVerge_Control_Center"
PERMS = STATEVERGE / "config" / "agent_permissions.json"
SCRIPT = STATEVERGE / "scripts" / "agent_creative_ops.py"
MAIN = CONTROL_CENTER / "backend" / "main.py"
ALLOWED = CONTROL_CENTER / "remote_agent" / "allowed_tasks.json"
OUT_JSON = CONTROL_CENTER / "logs" / "agent_creative_ops_diagnose.json"
OUT_MD = CONTROL_CENTER / "logs" / "agent_creative_ops_diagnose.md"


def main() -> int:
    rows: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "checks": {}}
    ok = True
    rows["checks"]["agent_permissions"] = PERMS.is_file()
    rows["checks"]["agent_creative_ops_py"] = SCRIPT.is_file()
    if PERMS.is_file():
        data = json.loads(PERMS.read_text(encoding="utf-8"))
        perms = data.get("permissions") if isinstance(data, dict) else {}
        rows["checks"]["dangerous_flags_false"] = not bool(
            (perms or {}).get("real_upload")
            or (perms or {}).get("edit_tokens")
            or (perms or {}).get("bypass_dedupe")
            or (perms or {}).get("bypass_channel_guard")
            or (perms or {}).get("bypass_vfr_safety")
            or (perms or {}).get("delete_raw_media")
            or (perms or {}).get("delete_youtube_video")
            or (perms or {}).get("format_disks")
        )
        if not rows["checks"]["dangerous_flags_false"]:
            ok = False
    else:
        ok = False
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    rows["checks"]["py_compile_creative"] = r.returncode == 0
    if r.returncode != 0:
        ok = False
    rows["checks"]["backend_api_marker"] = MAIN.is_file() and "/api/agent/creative-ops/status" in MAIN.read_text(
        encoding="utf-8", errors="replace"
    )
    if not rows["checks"]["backend_api_marker"]:
        ok = False
    page = CONTROL_CENTER / "frontend" / "src" / "app" / "server-mode" / "page.tsx"
    rows["checks"]["frontend_creative_panel"] = (
        page.is_file() and "Agent Creative Ops" in page.read_text(encoding="utf-8", errors="replace")
    )
    if not rows["checks"]["frontend_creative_panel"]:
        ok = False
    if ALLOWED.is_file():
        t = ALLOWED.read_text(encoding="utf-8", errors="replace")
        need_tasks = (
            "diagnose_creative_ops_safe",
            "propose_shorts_upgrade_safe",
            "propose_long_upgrade_safe",
            "propose_metadata_upgrade_safe",
            "run_creative_ops_report_safe",
        )
        rows["checks"]["mobile_safe_tasks"] = {tid: (tid in t) for tid in need_tasks}
        if not all(rows["checks"]["mobile_safe_tasks"].values()):
            ok = False
    else:
        ok = False
    for d in (
        CONTROL_CENTER / "logs" / "agent_upgrade_reports",
        CONTROL_CENTER / "logs" / "agent_upgrade_backups",
    ):
        d.mkdir(parents=True, exist_ok=True)
        rows["checks"][f"writable_{d.name}"] = os.access(d, os.W_OK)
    rows["ok"] = ok
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    OUT_MD.write_text(f"# agent_creative_ops diagnose\n\nok={ok}\n", encoding="utf-8")
    print(json.dumps({"ok": ok}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
