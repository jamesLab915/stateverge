#!/usr/bin/env python3
"""Diagnose Agent Operations Monitor v1."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATEVERGE = Path.home() / "StateVerge"
CONTROL_CENTER = Path.home() / "StateVerge_Control_Center"
OUT_JSON = CONTROL_CENTER / "logs" / "agent_ops_monitor_diagnose.json"
OUT_MD = CONTROL_CENTER / "logs" / "agent_ops_monitor_diagnose.md"
MONITOR = STATEVERGE / "scripts" / "agent_ops_monitor.py"
MAIN_PY = CONTROL_CENTER / "backend" / "main.py"
PLIST = CONTROL_CENTER / "launchagents" / "com.stateverge.agent.monitor.plist"
ALLOWED = CONTROL_CENTER / "remote_agent" / "allowed_tasks.json"


def _py_compile(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing"
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return r.returncode == 0, (r.stderr or r.stdout or "")[-2000:]


def main() -> int:
    rows: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "checks": {}}
    ok_all = True
    rows["checks"]["agent_ops_monitor_py"] = MONITOR.is_file()
    rows["checks"]["py_compile_monitor"], err = _py_compile(MONITOR)
    if not rows["checks"]["py_compile_monitor"]:
        ok_all = False
    rows["checks"]["py_compile_error_tail"] = err

    py = STATEVERGE / ".venv_audio" / "bin" / "python3"
    exe = str(py) if py.is_file() else sys.executable
    r = subprocess.run(
        [exe, str(MONITOR), "--mode", "monitor", "--safe-repair"],
        cwd=str(STATEVERGE),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    rows["checks"]["monitor_run_rc"] = r.returncode
    rows["checks"]["monitor_stdout_tail"] = (r.stdout or "")[-4000:]
    latest = CONTROL_CENTER / "logs" / "agent_ops_monitor_latest.json"
    rows["checks"]["latest_json_exists"] = latest.is_file()

    rows["checks"]["backend_has_agent_route"] = False
    if MAIN_PY.is_file():
        t = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        rows["checks"]["backend_has_agent_route"] = "/api/agent/status" in t

    page = CONTROL_CENTER / "frontend" / "src" / "app" / "server-mode" / "page.tsx"
    rows["checks"]["frontend_agent_panel"] = page.is_file() and "Agent Operations Monitor" in page.read_text(
        encoding="utf-8", errors="replace"
    )
    if not rows["checks"]["frontend_agent_panel"]:
        ok_all = False

    rows["checks"]["plist_exists"] = PLIST.is_file()
    if ALLOWED.is_file():
        raw = json.loads(ALLOWED.read_text(encoding="utf-8"))
        tasks = raw.get("tasks") if isinstance(raw, dict) else []
        ids = {str(t.get("id")) for t in tasks if isinstance(t, dict)}
        need = {
            "diagnose_agent_ops_monitor",
            "run_agent_ops_monitor_safe",
            "restart_server_mode_safe",
            "clean_stale_shorts_lock_safe",
            "run_final_health_check_safe",
            "diagnose_auto_publish_v2_safe",
            "diagnose_shorts_safe",
        }
        rows["checks"]["allowed_tasks_has_safe_ops"] = need.issubset(ids)
        rows["checks"]["missing_safe_tasks"] = sorted(need - ids)
        if not need.issubset(ids):
            ok_all = False
    else:
        ok_all = False
        rows["checks"]["allowed_tasks_has_safe_ops"] = False

    rows["ok"] = ok_all
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    OUT_MD.write_text(
        "\n".join(
            [
                "# diagnose_agent_ops_monitor",
                "",
                f"- ok: **{rows['ok']}**",
                f"- latest_json: {latest.is_file()}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"ok": ok_all}, indent=2))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
