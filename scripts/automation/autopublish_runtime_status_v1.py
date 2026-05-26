#!/usr/bin/env python3
"""Probe StateVerge autopublish runtime (LaunchAgents, paths, locks, logs) — v1.

Fail-open: always exits 0. Writes JSON to Control Center logs and prints a 4-line stdout footer.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHORTS_LABEL = "com.stateverge.shorts.autopublish"
NYC_LABEL = "com.stateverge.nyc.autopublish"
LEGACY_YOUTUBE_LABEL = "com.stateverge.youtube.publish"

SHORTS_EXPECT_SCHEDULE = {(9, 30), (13, 30), (17, 30), (21, 30)}
NYC_EXPECT_SCHEDULE = {(10, 0), (16, 0)}

LEGACY_AUTOMATION_NEEDLE = "/Volumes/StateVerge/07_AUTOMATION"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _home() -> Path:
    return Path.home()


def _sv() -> Path:
    return _home() / "StateVerge"


def _cc() -> Path:
    return _home() / "StateVerge_Control_Center"


def _logs() -> Path:
    return _cc() / "logs"


def _official_python() -> Path:
    return _sv() / ".venv_audio" / "bin" / "python3"


def _user_launchagents() -> Path:
    return _home() / "Library" / "LaunchAgents"


def _safe_read_json(path: Path) -> Any | None:
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _load_plist(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as fh:
            data = plistlib.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, TypeError, ValueError, plistlib.InvalidFileException):
        return None


def _launchctl_print_loaded(label: str) -> bool:
    dom = f"gui/{os.getuid()}"
    target = f"{dom}/{label}"
    try:
        r = subprocess.run(
            ["launchctl", "print", target],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _extract_schedule(plist: dict[str, Any] | None) -> list[str]:
    if not plist:
        return []
    sci = plist.get("StartCalendarInterval")
    out: list[str] = []
    if isinstance(sci, dict):
        sci = [sci]
    if not isinstance(sci, list):
        return []
    for block in sci:
        if not isinstance(block, dict):
            continue
        try:
            h = int(block.get("Hour", 0))
            m = int(block.get("Minute", 0))
            out.append(f"{h:02d}:{m:02d}")
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _schedule_set_from_plist(plist: dict[str, Any] | None) -> set[tuple[int, int]]:
    if not plist:
        return set()
    sci = plist.get("StartCalendarInterval")
    if isinstance(sci, dict):
        sci = [sci]
    if not isinstance(sci, list):
        return set()
    s: set[tuple[int, int]] = set()
    for block in sci:
        if not isinstance(block, dict):
            continue
        try:
            s.add((int(block.get("Hour", -1)), int(block.get("Minute", -1))))
        except (TypeError, ValueError):
            continue
    return s


def _newest_under(root: Path, pattern: str) -> str | None:
    try:
        if not root.is_dir():
            return None
        best: tuple[float, Path] | None = None
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if best is None or mt > best[0]:
                best = (mt, p)
        return str(best[1]) if best else None
    except OSError:
        return None


def _newest_child_job_json(jobs_dir: Path) -> str | None:
    try:
        if not jobs_dir.is_dir():
            return None
        best: tuple[float, Path] | None = None
        for child in jobs_dir.iterdir():
            if not child.is_dir():
                continue
            jp = child / "job.json"
            if not jp.is_file():
                continue
            try:
                mt = jp.stat().st_mtime
            except OSError:
                continue
            if best is None or mt > best[0]:
                best = (mt, jp)
        return str(best[1]) if best else None
    except OSError:
        return None


def _last_csv_row(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            if line.strip():
                return line.strip()[:500]
        return None
    except OSError:
        return None


def _shorts_worker_script() -> str:
    return str(_sv() / "scripts" / "jobs" / "shorts_cut_upload_job.py")


def _long_worker_script() -> str:
    return str(_sv() / "scripts" / "jobs" / "nyc_cut_upload_job.py")


def _scan_legacy_automation_plists() -> list[str]:
    hits: list[str] = []
    la = _user_launchagents()
    try:
        if not la.is_dir():
            return hits
        for p in la.glob("com.stateverge*.plist"):
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if LEGACY_AUTOMATION_NEEDLE in raw:
                hits.append(str(p))
    except OSError:
        pass
    return sorted(set(hits))


def _emergency_paths() -> list[Path]:
    xfer = Path("/Volumes/SV_TRANSFER")
    return [
        xfer / "publish_pack" / "nyc_long_uploads" / "long_autopublish_emergency.json",
        _sv() / "data" / "long_runtime" / "long_uploads" / "long_autopublish_emergency.json",
    ]


def _doctor_paths() -> list[Path]:
    return [
        _sv() / "logs" / "stateverge_doctor_report.json",
        _logs() / "stateverge_doctor_report.json",
    ]


def _shorts_schedule_completion_paths() -> list[Path]:
    return [
        Path("/Volumes/SV_CACHE/review_reports/shorts_schedule_completion_report.json"),
        _sv() / "logs" / "shorts_schedule_completion_report.json",
        _logs() / "shorts_schedule_completion_report.json",
    ]


def _shorts_schedule_completion_summary() -> dict[str, Any] | None:
    for p in _shorts_schedule_completion_paths():
        doc = _safe_read_json(p)
        if not isinstance(doc, dict) or doc.get("schema") != "shorts_schedule_completion_v1":
            continue
        return {
            "report_path": str(p),
            "date_local": doc.get("date_local"),
            "expected_today": doc.get("expected_today"),
            "uploaded_today": doc.get("uploaded_today"),
            "missing_count": doc.get("missing_count"),
            "backfill_uploaded": doc.get("backfill_uploaded"),
            "unresolved_block_reason": doc.get("unresolved_block_reason") or "",
            "generated_at": doc.get("generated_at"),
        }
    return None


def main(argv: list[str] | None = None) -> int:
    _ = argv
    warnings: list[str] = []
    errors: list[str] = []

    official_py = _official_python()
    official_python_exists = official_py.is_file()

    user_shorts_plist = _user_launchagents() / f"{SHORTS_LABEL}.plist"
    user_nyc_plist = _user_launchagents() / f"{NYC_LABEL}.plist"
    legacy_plist = _user_launchagents() / f"{LEGACY_YOUTUBE_LABEL}.plist"

    plist_shorts = _load_plist(user_shorts_plist) if user_shorts_plist.is_file() else None
    plist_nyc = _load_plist(user_nyc_plist) if user_nyc_plist.is_file() else None

    shorts_loaded = _launchctl_print_loaded(SHORTS_LABEL)
    nyc_loaded = _launchctl_print_loaded(NYC_LABEL)
    legacy_loaded = _launchctl_print_loaded(LEGACY_YOUTUBE_LABEL)

    pa_s = plist_shorts.get("ProgramArguments") if plist_shorts else None
    pa_n = plist_nyc.get("ProgramArguments") if plist_nyc else None
    py_shorts = str(pa_s[0]) if isinstance(pa_s, list) and pa_s else None
    py_nyc = str(pa_n[0]) if isinstance(pa_n, list) and pa_n else None
    q_shorts = str(pa_s[1]) if isinstance(pa_s, list) and len(pa_s) > 1 else None
    q_nyc = str(pa_n[1]) if isinstance(pa_n, list) and len(pa_n) > 1 else None

    runtime_root = _sv() / "data" / "shorts_runtime"
    lock_path = runtime_root / ".shorts_cut_upload.global.lock"
    lock_exists = lock_path.is_file()

    jobs_dir = runtime_root / "jobs"
    shorts_uploads = runtime_root / "shorts_uploads"
    upload_csv = _sv() / "data" / "youtube" / "upload_history.csv"

    last_job = _newest_child_job_json(jobs_dir)
    last_upload = _newest_under(shorts_uploads, "**/shorts_job_result.json") or _last_csv_row(upload_csv)

    logs_dir = _logs()
    shorts_stdout = logs_dir / "launchd_shorts_stdout.log"
    shorts_stderr = logs_dir / "launchd_shorts_stderr.log"
    shorts_runtime_log = logs_dir / "shorts_runtime.log"

    logs_exist = {
        "launchd_shorts_stdout": shorts_stdout.is_file(),
        "launchd_shorts_stderr": shorts_stderr.is_file(),
        "shorts_runtime_log": shorts_runtime_log.is_file(),
    }

    xfer_ready = Path("/Volumes/SV_TRANSFER/ready_to_upload")
    xfer_pack = Path("/Volumes/SV_TRANSFER/publish_pack")
    home_long_uploads = _sv() / "data" / "long_runtime" / "long_uploads"
    lock_primary = xfer_pack / "nyc_long_uploads" / ".nyc_long_upload.lock"
    lock_fb = home_long_uploads / ".nyc_long_upload.lock"
    lock_long_exists = lock_primary.is_file() or lock_fb.is_file()

    emergency_snippet: dict[str, Any] | None = None
    emergency_path_used: str | None = None
    for ep in _emergency_paths():
        doc = _safe_read_json(ep)
        if isinstance(doc, dict):
            emergency_path_used = str(ep)
            emergency_snippet = {
                k: doc.get(k)
                for k in ("LONG_AUTOPUBLISH_DISABLED", "block_reason", "updated_at", "SAFE_TO_ENABLE_LONG_UPLOAD")
                if k in doc
            }
            break

    doctor_gate: str | None = None
    upload_allowed: Any = None
    doctor_path_used: str | None = None
    for dp in _doctor_paths():
        doc = _safe_read_json(dp)
        if isinstance(doc, dict):
            doctor_path_used = str(dp)
            doctor_gate = str(doc.get("doctor_gate") or "") or None
            upload_allowed = doc.get("upload_allowed")
            break

    long_last = _newest_under(home_long_uploads, "**/long_job_result.json") or _last_csv_row(upload_csv)

    long_stdout = logs_dir / "launchd_nyc_long_stdout.log"
    long_stderr = logs_dir / "launchd_nyc_long_stderr.log"
    long_runtime_log = logs_dir / "long_runtime.log"
    long_logs_exist = {
        "launchd_nyc_long_stdout": long_stdout.is_file(),
        "launchd_nyc_long_stderr": long_stderr.is_file(),
        "long_runtime_log": long_runtime_log.is_file(),
    }

    legacy_paths = _scan_legacy_automation_plists()

    sched_s = _schedule_set_from_plist(plist_shorts)
    sched_n = _schedule_set_from_plist(plist_nyc)
    if plist_shorts and sched_s != SHORTS_EXPECT_SCHEDULE:
        warnings.append("shorts_schedule_mismatch")
    if plist_nyc and sched_n != NYC_EXPECT_SCHEDULE:
        warnings.append("nyc_schedule_mismatch")
    if py_shorts:
        try:
            if Path(py_shorts).resolve() != official_py.resolve():
                warnings.append("shorts_plist_python_not_official_venv")
        except OSError:
            warnings.append("shorts_plist_python_resolve_failed")
    if py_nyc:
        try:
            if Path(py_nyc).resolve() != official_py.resolve():
                warnings.append("nyc_plist_python_not_official_venv")
        except OSError:
            warnings.append("nyc_plist_python_resolve_failed")

    exp_shorts_args = ["--upload", "--privacy-status", "unlisted", "--max-count", "1"]
    exp_nyc_args = ["--upload", "--privacy-status", "unlisted", "--max-count", "1"]

    def _unified_shorts() -> bool:
        if not official_python_exists or not plist_shorts:
            return False
        if sched_s != SHORTS_EXPECT_SCHEDULE:
            return False
        exp_q = str(_sv() / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py")
        if q_shorts != exp_q or not py_shorts:
            return False
        if not isinstance(pa_s, list) or [str(x) for x in pa_s[2:]] != exp_shorts_args:
            return False
        try:
            return Path(py_shorts).resolve() == official_py.resolve()
        except OSError:
            return False

    def _unified_long() -> bool:
        if not official_python_exists or not plist_nyc:
            return False
        if sched_n != NYC_EXPECT_SCHEDULE:
            return False
        exp_q = str(_sv() / "scripts" / "nyc_auto" / "auto_publish_queue.py")
        if q_nyc != exp_q or not py_nyc:
            return False
        if not isinstance(pa_n, list) or [str(x) for x in pa_n[2:]] != exp_nyc_args:
            return False
        try:
            return Path(py_nyc).resolve() == official_py.resolve()
        except OSError:
            return False

    su = _unified_shorts()
    lu = _unified_long()

    report: dict[str, Any] = {
        "schema": "autopublish_runtime_status_v1",
        "generated_at": _utc_iso(),
        "official_python": str(official_py),
        "official_python_exists": official_python_exists,
        "shorts": {
            "agent_loaded": shorts_loaded,
            "plist_path": str(user_shorts_plist),
            "python_path": py_shorts,
            "schedule_times": _extract_schedule(plist_shorts),
            "queue_script": q_shorts,
            "worker_script": _shorts_worker_script(),
            "official_job_type": "shorts_cut_upload",
            "runtime_root": str(runtime_root),
            "runtime_unified": su,
            "lock_path": str(lock_path),
            "lock_exists": lock_exists,
            "last_job": last_job,
            "last_upload": last_upload,
            "logs_exist": logs_exist,
            "schedule_completion": _shorts_schedule_completion_summary(),
        },
        "long": {
            "official_agent_loaded": nyc_loaded,
            "legacy_agent_loaded": legacy_loaded,
            "official_plist_path": str(user_nyc_plist),
            "legacy_plist_path": str(legacy_plist),
            "python_path": py_nyc,
            "schedule_times": _extract_schedule(plist_nyc),
            "queue_script": q_nyc,
            "worker_script": _long_worker_script(),
            "runtime_unified": lu,
            "ready_pool": str(xfer_ready),
            "publish_pack": str(xfer_pack),
            "lock_exists": lock_long_exists,
            "lock_paths_checked": [str(lock_primary), str(lock_fb)],
            "emergency_gate": {
                "path": emergency_path_used,
                "exists": bool(emergency_path_used),
                "snippet": emergency_snippet,
            },
            "doctor_gate": {
                "report_path": doctor_path_used,
                "doctor_gate": doctor_gate,
                "upload_allowed": upload_allowed,
            },
            "last_job": long_last,
            "last_upload": long_last,
            "logs_exist": long_logs_exist,
        },
        "global": {
            "legacy_paths_detected": legacy_paths,
            "warnings": warnings,
            "errors": errors,
        },
    }

    out_path = _logs() / "autopublish_runtime_status_v1.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        errors.append(f"json_write:{exc!r}")
        report["global"]["errors"] = errors

    # Strict stdout footer (must be last 4 lines on stdout)
    print("AUTOPUBLISH_RUNTIME_STATUS_READY=true")
    print(f"SHORTS_AGENT_LOADED={'true' if shorts_loaded else 'false'}")
    print(f"LONG_AGENT_LOADED={'true' if nyc_loaded else 'false'}")
    print(f"LEGACY_YOUTUBE_AGENT_LOADED={'true' if legacy_loaded else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
