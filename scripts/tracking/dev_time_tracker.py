#!/usr/bin/env python3
"""
Development time tracker: session-based and auto-estimated.
Non-invasive: no keyboard monitoring, no screenshots.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

_PR = Path(__file__).resolve().parent
if str(_PR) not in sys.path:
    sys.path.insert(0, str(_PR))
from _paths import REPO_ROOT, LOGS, TRACK

SESSION_FILE = LOGS / "dev_time.json"
SUMMARY_CSV = TRACK / "dev_time_summary.csv"
CSV_HEADERS = ["Date", "Project", "Start Time", "End Time", "Duration Hours", "Mode", "Description"]


def _session_start() -> None:
    """Start a development session."""
    LOGS.mkdir(parents=True, exist_ok=True)
    session = {"start": datetime.now().isoformat()}
    with open(SESSION_FILE, "w") as f:
        json.dump(session, f)
    print(f"[dev_time_tracker] session started at {session['start']}")


def _session_end() -> None:
    """End a development session and log duration."""
    if not SESSION_FILE.is_file():
        print("[dev_time_tracker] no session in progress")
        return

    try:
        with open(SESSION_FILE, "r") as f:
            session = json.load(f)
    except Exception:
        print("[dev_time_tracker] error reading session file")
        return

    start_time = datetime.fromisoformat(session.get("start", ""))
    end_time = datetime.now()
    duration_hours = (end_time - start_time).total_seconds() / 3600

    record = {
        "Date": start_time.date().isoformat(),
        "Project": "StateVerge",
        "Start Time": start_time.isoformat(),
        "End Time": end_time.isoformat(),
        "Duration Hours": f"{duration_hours:.2f}",
        "Mode": "session",
        "Description": "Development session",
    }

    _append_to_csv(record)
    SESSION_FILE.unlink()
    print(f"[dev_time_tracker] session ended, duration: {duration_hours:.2f} hours")


def _estimate_from_git(days: int = 1) -> float:
    """Estimate development time from git activity."""
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        result = subprocess.run(
            ["git", "log", f"--since={since}", "--format=%H"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=REPO_ROOT,
        )
        commits = len([l for l in result.stdout.strip().split("\n") if l])
        # Rough estimate: 10 minutes per commit
        hours = (commits * 10) / 60
        return hours
    except Exception:
        return 0.0


def _append_to_csv(record: dict) -> None:
    """Append record to CSV."""
    TRACK.mkdir(parents=True, exist_ok=True)

    records = []
    if SUMMARY_CSV.is_file():
        try:
            with open(SUMMARY_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                records = list(reader)
        except Exception:
            pass

    records.append(record)

    try:
        with open(SUMMARY_CSV, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()
            for r in records:
                writer.writerow(r)
    except Exception as e:
        print(f"[dev_time_tracker] error writing CSV: {e}")


def _auto_estimate() -> None:
    """Auto-estimate development time from git."""
    hours = _estimate_from_git(days=1)
    if hours > 0:
        record = {
            "Date": datetime.now().date().isoformat(),
            "Project": "StateVerge",
            "Start Time": "",
            "End Time": "",
            "Duration Hours": f"{hours:.2f}",
            "Mode": "auto",
            "Description": "Auto-estimated from git activity",
        }
        _append_to_csv(record)
        print(f"[dev_time_tracker] estimated {hours:.2f} hours from git activity")
    else:
        print("[dev_time_tracker] no recent git activity found")


def _summary() -> None:
    """Print summary of logged time."""
    if not SUMMARY_CSV.is_file():
        print("[dev_time_tracker] no time records found")
        return

    try:
        with open(SUMMARY_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            records = list(reader)
    except Exception:
        print("[dev_time_tracker] error reading CSV")
        return

    total_hours = sum(float(r.get("Duration Hours", 0)) for r in records)
    session_count = len([r for r in records if r.get("Mode") == "session"])
    auto_count = len([r for r in records if r.get("Mode") == "auto"])

    print(f"[dev_time_tracker] total: {total_hours:.2f} hours")
    print(f"[dev_time_tracker] sessions: {session_count}, auto-estimates: {auto_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Track development time")
    parser.add_argument("--mode", choices=["session", "auto", "summary"], help="Tracking mode")
    parser.add_argument("--start", action="store_true", help="Start session (--mode session)")
    parser.add_argument("--end", action="store_true", help="End session (--mode session)")
    args = parser.parse_args()

    if args.mode == "session":
        if args.start:
            _session_start()
        elif args.end:
            _session_end()
    elif args.mode == "auto":
        _auto_estimate()
    elif args.mode == "summary":
        _summary()


if __name__ == "__main__":
    main()
