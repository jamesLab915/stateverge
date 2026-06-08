#!/usr/bin/env python3
"""Diagnose / repair StateVerge autopublish runtime v1.

Fail-open: always exits 0; details in JSON under Control Center logs.

Default: diagnose only (JSON + 4-line stdout footer). ``--apply`` rewrites official plists
(see install_autopublish_launchagents_v1), ensures log/runtime dirs, touches runtime logs if missing.
``--emit-summary`` appends 5 summary lines after the 4-line footer (use with ``--apply`` to avoid
breaking consumers that parse only the first 4 lines).
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHORTS_LABEL = "com.stateverge.shorts.autopublish"
NYC_LABEL = "com.stateverge.nyc.autopublish"
LEGACY_YOUTUBE_LABEL = "com.stateverge.youtube.publish"

SHORTS_EXPECT_SCHEDULE = {(18, 0), (21, 0), (0, 0), (2, 0), (4, 0), (5, 0)}
NYC_EXPECT_SCHEDULE = {(10, 0), (16, 0)}
EXP_SHORTS_TAIL = ["--upload", "--privacy-status", "unlisted", "--max-count", "1"]
EXP_NYC_TAIL = ["--upload", "--privacy-status", "unlisted", "--max-count", "1"]


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


def _load_plist(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as fh:
            data = plistlib.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, TypeError, ValueError, plistlib.InvalidFileException):
        return None


def _schedule_set(plist: dict[str, Any] | None) -> set[tuple[int, int]]:
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


def _launchctl_print_loaded(label: str) -> bool:
    dom = f"gui/{os.getuid()}"
    target = f"{dom}/{label}"
    try:
        import subprocess

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


def _unified_from_plists(
    plist_shorts: dict[str, Any] | None,
    plist_nyc: dict[str, Any] | None,
    official_py: Path,
) -> tuple[bool, bool]:
    official_exists = official_py.is_file()
    pa_s = plist_shorts.get("ProgramArguments") if plist_shorts else None
    pa_n = plist_nyc.get("ProgramArguments") if plist_nyc else None
    py_s = str(pa_s[0]) if isinstance(pa_s, list) and pa_s else None
    py_n = str(pa_n[0]) if isinstance(pa_n, list) and pa_n else None
    q_s = str(pa_s[1]) if isinstance(pa_s, list) and len(pa_s) > 1 else None
    q_n = str(pa_n[1]) if isinstance(pa_n, list) and len(pa_n) > 1 else None

    def shorts_ok() -> bool:
        if not official_exists or not plist_shorts:
            return False
        if _schedule_set(plist_shorts) != SHORTS_EXPECT_SCHEDULE:
            return False
        if q_s != str(_sv() / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py"):
            return False
        if not py_s or not isinstance(pa_s, list):
            return False
        if [str(x) for x in pa_s[2:]] != EXP_SHORTS_TAIL:
            return False
        try:
            return Path(py_s).resolve() == official_py.resolve()
        except OSError:
            return False

    def long_ok() -> bool:
        if not official_exists or not plist_nyc:
            return False
        if _schedule_set(plist_nyc) != NYC_EXPECT_SCHEDULE:
            return False
        if q_n != str(_sv() / "scripts" / "nyc_auto" / "auto_publish_queue.py"):
            return False
        if not py_n or not isinstance(pa_n, list):
            return False
        if [str(x) for x in pa_n[2:]] != EXP_NYC_TAIL:
            return False
        try:
            return Path(py_n).resolve() == official_py.resolve()
        except OSError:
            return False

    return shorts_ok(), long_ok()


def _stale_lock_report(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    try:
        st = path.stat()
        out["mtime_epoch"] = st.st_mtime
        out["age_seconds"] = max(0.0, time.time() - st.st_mtime)
    except OSError as exc:
        out["stat_error"] = repr(exc)
        return out
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        doc = json.loads(raw)
        if isinstance(doc, dict):
            out["lock_json_pid"] = doc.get("pid")
    except (OSError, json.JSONDecodeError, TypeError):
        out["lock_json_pid"] = None
    age_h = float(out.get("age_seconds") or 0) / 3600.0
    if age_h >= 24.0:
        out["warning"] = "lock_older_than_24h_report_only_v1_no_auto_remove"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Repair autopublish runtime v1 (diagnose default)")
    ap.add_argument("--apply", action="store_true", help="Rewrite plists, mkdir, touch logs")
    ap.add_argument(
        "--disable-legacy-youtube-publish",
        action="store_true",
        help="With --apply: best-effort bootout/unload legacy YouTube publish agent",
    )
    ap.add_argument(
        "--emit-summary",
        action="store_true",
        help="After the 4-line footer, print 5-line official summary (machine-readable)",
    )
    ns = ap.parse_args(argv)
    apply = bool(ns.apply)
    report: dict[str, Any] = {
        "schema": "autopublish_runtime_repair_v1",
        "generated_at": _utc_iso(),
        "apply": apply,
        "disable_legacy_youtube_publish": bool(ns.disable_legacy_youtube_publish),
        "emit_summary": bool(ns.emit_summary),
        "warnings": [],
        "errors": [],
    }

    official_py = _official_python()
    user_shorts = _user_launchagents() / f"{SHORTS_LABEL}.plist"
    user_nyc = _user_launchagents() / f"{NYC_LABEL}.plist"

    plist_s = _load_plist(user_shorts) if user_shorts.is_file() else None
    plist_n = _load_plist(user_nyc) if user_nyc.is_file() else None

    shorts_lock = _sv() / "data" / "shorts_runtime" / ".shorts_cut_upload.global.lock"
    xfer_pack = Path("/Volumes/SV_TRANSFER/publish_pack")
    lock_long_p = xfer_pack / "nyc_long_uploads" / ".nyc_long_upload.lock"
    lock_long_fb = _sv() / "data" / "long_runtime" / "long_uploads" / ".nyc_long_upload.lock"

    report["stale_lock_probe"] = {
        "shorts_global_lock": _stale_lock_report(shorts_lock),
        "nyc_long_lock_primary": _stale_lock_report(lock_long_p),
        "nyc_long_lock_fallback": _stale_lock_report(lock_long_fb),
    }

    su, lu = _unified_from_plists(plist_s, plist_n, official_py)
    report["shorts_runtime_unified"] = su
    report["long_runtime_unified"] = lu

    legacy_loaded_before = _launchctl_print_loaded(LEGACY_YOUTUBE_LABEL)
    report["legacy_youtube_publish_loaded_before"] = legacy_loaded_before

    if apply:
        try:
            from install_autopublish_launchagents_v1 import sync_official_autopublish_plists

            inner: dict[str, Any] = {}
            sync_official_autopublish_plists(
                apply=True,
                disable_legacy_youtube_publish=bool(ns.disable_legacy_youtube_publish),
                report=inner,
            )
            report["install_sync"] = inner
        except Exception as exc:
            report["errors"].append(f"install_sync_import_or_run:{exc!r}")

        # Dirs + empty logs if missing
        try:
            for d in (
                _logs(),
                _sv() / "data" / "shorts_runtime",
                _sv() / "data" / "shorts_runtime" / "jobs",
                _sv() / "data" / "shorts_runtime" / "shorts_uploads",
                _sv() / "data" / "long_runtime" / "long_uploads",
            ):
                d.mkdir(parents=True, exist_ok=True)
            for logf in (_logs() / "shorts_runtime.log", _logs() / "long_runtime.log"):
                if not logf.is_file():
                    logf.write_text("", encoding="utf-8")
            report["dirs_and_logs_ok"] = True
        except Exception as exc:
            report["errors"].append(f"mkdir_touch_logs:{exc!r}")
            report["dirs_and_logs_ok"] = False

        plist_s = _load_plist(user_shorts) if user_shorts.is_file() else None
        plist_n = _load_plist(user_nyc) if user_nyc.is_file() else None
        su, lu = _unified_from_plists(plist_s, plist_n, official_py)
        report["shorts_runtime_unified"] = su
        report["long_runtime_unified"] = lu

    legacy_loaded_after = _launchctl_print_loaded(LEGACY_YOUTUBE_LABEL)
    report["legacy_youtube_publish_loaded_after"] = legacy_loaded_after

    if apply and ns.disable_legacy_youtube_publish:
        if legacy_loaded_after:
            report["LEGACY_YOUTUBE_PUBLISH_AGENT_STATUS"] = "warning_still_loaded"
            report["warnings"].append("legacy_youtube_publish_still_loaded_after_disable_attempt")
        else:
            report["LEGACY_YOUTUBE_PUBLISH_AGENT_STATUS"] = "disabled"
    else:
        report["LEGACY_YOUTUBE_PUBLISH_AGENT_STATUS"] = "loaded" if legacy_loaded_after else "not_loaded"

    out_path = _logs() / "autopublish_runtime_repair_v1.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        report["errors"].append(f"json_write:{exc!r}")

    print("AUTOPUBLISH_RUNTIME_REPAIR_READY=true")
    print(f"APPLY_MODE={'true' if apply else 'false'}")
    print(f"SHORTS_RUNTIME_UNIFIED={'true' if su else 'false'}")
    print(f"LONG_RUNTIME_UNIFIED={'true' if lu else 'false'}")

    if ns.emit_summary:
        leg = str(report.get("LEGACY_YOUTUBE_PUBLISH_AGENT_STATUS") or "unknown")
        print(f"SHORTS_RUNTIME_UNIFIED={'true' if su else 'false'}")
        print(f"LONG_RUNTIME_UNIFIED={'true' if lu else 'false'}")
        print("OFFICIAL_SHORTS_JOB_TYPE=shorts_cut_upload")
        print("OFFICIAL_LONG_JOB_TYPE=nyc_long_upload")
        print(f"OFFICIAL_AUTOPUBLISH_PYTHON={official_py}")
        print(f"LEGACY_YOUTUBE_PUBLISH_AGENT_STATUS={leg}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
