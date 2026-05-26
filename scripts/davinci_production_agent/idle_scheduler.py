#!/usr/bin/env python3
"""Idle scheduler — night autobuild when CPU low, no active jobs, volumes online, doctor OK."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1]
_REPO = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from davinci_production_agent.config import load_config
from davinci_production_agent.paths import REQUIRED_VOLUMES

DOCTOR_REPORT_PATHS = (
    Path("/Volumes/SV_CACHE/logs/stateverge_doctor_report.json"),
    _REPO / "logs/stateverge_doctor_report.json",
    Path.home() / "StateVerge_Control_Center/logs/stateverge_doctor_report.json",
)

NIGHT_PLIST_NAME = "com.stateverge.davinci.inventory.night"
NIGHT_PLIST_PATH = Path.home() / "Library/LaunchAgents" / f"{NIGHT_PLIST_NAME}.plist"


def _cpu_percent() -> float | None:
    try:
        import psutil  # type: ignore

        return float(psutil.cpu_percent(interval=0.25))
    except Exception:
        try:
            load1, _, _ = os.getloadavg()
            cores = float(os.cpu_count() or 4)
            return 100.0 * load1 / cores
        except OSError:
            return None


def _proc_count(pattern: str) -> int:
    try:
        r = subprocess.run(
            ["/bin/ps", "-ax", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        n = 0
        for line in (r.stdout or "").splitlines():
            if pattern in line and "grep" not in line:
                n += 1
        return n
    except (OSError, subprocess.TimeoutExpired):
        return 0


def volumes_online() -> dict[str, Any]:
    missing: list[str] = []
    for vol in REQUIRED_VOLUMES:
        if not vol.exists():
            missing.append(str(vol))
    return {"ok": not missing, "missing": missing}


def doctor_blocks_upload() -> dict[str, Any]:
    for p in DOCTOR_REPORT_PATHS:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        block = str(data.get("block_upload") or data.get("BLOCK_UPLOAD") or "").lower()
        status = str(data.get("status") or data.get("overall") or "").upper()
        if block in ("true", "1", "yes") or status == "BLOCK_UPLOAD" or "BLOCK" in status:
            return {"blocked": True, "source": str(p), "detail": data}
        return {"blocked": False, "source": str(p), "detail": data}
    return {"blocked": False, "source": None, "detail": None}


def active_processes() -> dict[str, int]:
    return {
        "youtube_upload": _proc_count("youtube_upload"),
        "ffmpeg": _proc_count("ffmpeg"),
        "davinci_resolve": _proc_count("Resolve") + _proc_count("DaVinci Resolve"),
        "davinci_render": _proc_count("davinci_production_agent"),
    }


def idle_conditions(*, cpu_max: float | None = None) -> dict[str, Any]:
    cfg = load_config()
    max_cpu = float(cpu_max if cpu_max is not None else cfg.get("idle_cpu_max_percent") or 40)
    cpu = _cpu_percent()
    procs = active_processes()
    vols = volumes_online()
    doctor = doctor_blocks_upload()

    reasons: list[str] = []
    if cpu is not None and cpu >= max_cpu:
        reasons.append(f"cpu_high:{cpu:.1f}>={max_cpu}")
    if procs["youtube_upload"] > 0:
        reasons.append("active_youtube_upload")
    if procs["ffmpeg"] > 2:
        reasons.append("active_ffmpeg")
    if procs["davinci_resolve"] > 0 or procs["davinci_render"] > 0:
        reasons.append("active_davinci")
    if not vols["ok"]:
        reasons.append("volumes_offline")
    if doctor.get("blocked"):
        reasons.append("doctor_block_upload")

    safe_flag = Path.home() / "StateVerge_Control_Center/logs/STATEVERGE_SAFE_MODE.flag"
    if safe_flag.is_file():
        reasons.append("safe_mode_flag")

    return {
        "idle_ok": not reasons,
        "cpu_percent": cpu,
        "cpu_max": max_cpu,
        "processes": procs,
        "volumes": vols,
        "doctor": doctor,
        "reasons": reasons,
        "future_inventory_enabled": bool(cfg.get("future_inventory_enabled")),
    }


def can_run_future_inventory() -> bool:
    cond = idle_conditions()
    cfg = load_config()
    return bool(cond["idle_ok"]) and bool(cfg.get("future_inventory_enabled"))


def run_night_inventory_build() -> dict[str, Any]:
    """Entry point for launchd/cron: build inventory when idle."""
    cond = idle_conditions()
    if not can_run_future_inventory():
        return {"ok": False, "idle": cond, "block_reason": "idle_conditions_not_met"}
    from davinci_production_agent.agent_v1 import build_inventory_idle  # noqa: WPS433

    return build_inventory_idle()


def night_plist_documentation() -> dict[str, str]:
    """Document optional launchd plist (install manually)."""
    python = Path.home() / "StateVerge/.venv_audio/bin/python3"
    return {
        "plist_path": str(NIGHT_PLIST_PATH),
        "label": NIGHT_PLIST_NAME,
        "program_arguments": (
            f"{python} -c \"from davinci_production_agent.idle_scheduler import run_night_inventory_build; "
            "import json; print(json.dumps(run_night_inventory_build()))\""
        ),
        "note": "Run at 02:30 local; StartCalendarInterval Hour=2 Minute=30",
        "install": (
            f"See StateVerge/scripts/launchd/{NIGHT_PLIST_NAME}.plist — "
            f"launchctl load ~/Library/LaunchAgents/{NIGHT_PLIST_NAME}.plist"
        ),
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Idle scheduler for DaVinci inventory autobuild")
    ap.add_argument("--run", action="store_true", help="Run night inventory build now")
    ap.add_argument("--status", action="store_true", help="Print idle conditions JSON")
    ap.add_argument("--plist-doc", action="store_true", help="Print launchd plist documentation")
    args = ap.parse_args()
    if args.plist_doc:
        print(json.dumps(night_plist_documentation(), indent=2))
        return 0
    if args.run:
        print(json.dumps(run_night_inventory_build(), indent=2))
        return 0
    print(json.dumps(idle_conditions(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
