#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


HOME = Path.home()
CC = HOME / "StateVerge_Control_Center"
RA = CC / "remote_agent"
SV = HOME / "StateVerge"
LOG_DIR = CC / "logs"
OUT_JSON = LOG_DIR / "mobile_command_bridge_diagnose.json"
OUT_MD = LOG_DIR / "mobile_command_bridge_diagnose.md"

MOBILE_AGENT_SCRIPT = RA / "mobile_command_agent.py"
SUBMIT_COMMAND_SCRIPT = RA / "submit_mobile_command.py"
ALLOWED_TASKS = RA / "allowed_tasks.json"
MOBILE_AGENT_PLIST = CC / "launchagents" / "com.stateverge.mobile.command.agent.plist"
INSTALL_SCRIPT = CC / "install_mobile_command_agent.sh"
BACKEND_MAIN = CC / "backend" / "main.py"
FRONTEND_PAGE = CC / "frontend" / "src" / "app" / "mobile-command" / "page.tsx"
DIAGNOSE_SCRIPT = SV / "scripts" / "diagnose_mobile_command_bridge.py"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), body
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return int(e.code), body
    except Exception as exc:
        return -1, repr(exc)


def _launchctl_list() -> str:
    try:
        r = subprocess.run(
            ["launchctl", "list", "com.stateverge.mobile.command.agent"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return repr(exc)


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    queue_n = len(list((RA / "command_queue").glob("*.queued.json"))) if (RA / "command_queue").is_dir() else 0
    result_n = len(list((RA / "command_results").glob("*.json"))) if (RA / "command_results").is_dir() else 0

    latest_result_path = ""
    latest_result_mtime = 0.0
    if (RA / "command_results").is_dir():
        for p in (RA / "command_results").glob("*.json"):
            try:
                mt = float(p.stat().st_mtime)
                if mt > latest_result_mtime:
                    latest_result_mtime = mt
                    latest_result_path = str(p)
            except OSError:
                continue

    code, body = _http_get("http://127.0.0.1:8765/api/mobile-command/status")
    backend_status_ok = code == 200

    backend_patched = False
    if BACKEND_MAIN.is_file():
        try:
            t = BACKEND_MAIN.read_text(encoding="utf-8", errors="replace")
            backend_patched = "/api/mobile-command/status" in t
        except OSError:
            backend_patched = False

    report: dict[str, object] = {
        "created_at": _utc(),
        "remote_agent_dir_exists": RA.is_dir(),
        "allowed_tasks_exists": ALLOWED_TASKS.is_file(),
        "mobile_command_agent_exists": MOBILE_AGENT_SCRIPT.is_file(),
        "submit_exists": SUBMIT_COMMAND_SCRIPT.is_file(),
        "plist_exists": MOBILE_AGENT_PLIST.is_file(),
        "install_script_exists": INSTALL_SCRIPT.is_file(),
        "frontend_page_exists": FRONTEND_PAGE.is_file(),
        "backend_main_has_mobile_routes": backend_patched,
        "queue_queued_count": queue_n,
        "command_results_count": result_n,
        "latest_result_path": latest_result_path or None,
        "curl_mobile_command_status_http_code": code,
        "curl_mobile_command_status_ok": backend_status_ok,
        "launchctl_list_mobile_command_agent": _launchctl_list(),
    }

    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Mobile Command Bridge diagnose",
        "",
        f"- generated_at: `{report['created_at']}`",
        f"- remote_agent: `{RA}`",
        f"- queue (.queued.json): **{queue_n}**",
        f"- results (.json): **{result_n}**",
        f"- latest_result: `{latest_result_path or '(none)'}`",
        f"- GET /api/mobile-command/status: HTTP **{code}**",
        "",
        "## launchctl",
        "",
        "```",
        str(report.get("launchctl_list_mobile_command_agent") or ""),
        "```",
        "",
        "## Full JSON",
        "",
        "```",
        json.dumps(report, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")

    test_cmd_status = "skipped_run_submit_manually"

    print(f"MOBILE_AGENT_SCRIPT={MOBILE_AGENT_SCRIPT}")
    print(f"SUBMIT_COMMAND_SCRIPT={SUBMIT_COMMAND_SCRIPT}")
    print(f"ALLOWED_TASKS={ALLOWED_TASKS}")
    print(f"MOBILE_AGENT_PLIST={MOBILE_AGENT_PLIST}")
    print(f"INSTALL_SCRIPT={INSTALL_SCRIPT}")
    print(f"BACKEND_PATCHED={'true' if backend_patched else 'false'}")
    print(f"FRONTEND_PAGE={FRONTEND_PAGE}")
    print(f"DIAGNOSE_SCRIPT={DIAGNOSE_SCRIPT}")
    print(f"DIAGNOSE_JSON={OUT_JSON}")
    print(f"DIAGNOSE_MD={OUT_MD}")
    print(f"LAUNCHCTL_STATUS={report.get('launchctl_list_mobile_command_agent')!r}")
    print(f"TEST_COMMAND_STATUS={test_cmd_status}")
    print(f"LATEST_RESULT={latest_result_path}")
    next_step = "LAN only: open Control Center on http://127.0.0.1:3000/mobile-command after start_server_mode.sh; do not expose ports publicly."
    if not backend_status_ok:
        next_step = "Start backend on :8765 (e.g. start_server_mode.sh) and re-run this diagnose."
    elif not queue_n and not latest_result_path:
        next_step = "Run submit_mobile_command.py check_storage or use the Mobile Command page to enqueue a task."
    print(f"NEXT_STEP={next_step}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
