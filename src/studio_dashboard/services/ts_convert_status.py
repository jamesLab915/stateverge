"""
Read TS→MP4 batch progress written by scripts/convert_no_name_ts_to_stateverge_nyc.py.

Progress file: <repo>/logs/ts_convert_progress.json
PID lock:      <repo>/logs/ts_convert.pid
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def repo_logs_dir(repo_root: Path) -> Path:
    return repo_root / "logs"


def progress_path(repo_root: Path) -> Path:
    return repo_logs_dir(repo_root) / "ts_convert_progress.json"


def pid_path(repo_root: Path) -> Path:
    return repo_logs_dir(repo_root) / "ts_convert.pid"


def pid_alive(repo_root: Path) -> bool:
    p = pid_path(repo_root)
    if not p.is_file():
        return False
    try:
        pid = int(p.read_text(encoding="utf-8").strip())
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_pid_value(repo_root: Path) -> int | None:
    p = pid_path(repo_root)
    if not p.is_file():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def read_progress(repo_root: Path, *, stale_after_sec: float = 180.0) -> dict:
    """Return UI-friendly snapshot; never raises."""
    root = repo_root.resolve()
    path = progress_path(root)
    base: dict = {
        "ok": True,
        "idle": True,
        "pid_alive": pid_alive(root),
        "pid": read_pid_value(root),
        "progress_path": str(progress_path(root)),
    }
    if not path.is_file():
        base["message"] = "尚无转换记录（从未运行或日志已清空）"
        return base

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        base["message"] = "进度文件损坏或不可读"
        return base

    if not isinstance(data, dict):
        base["message"] = "进度格式异常"
        return base

    running = bool(data.get("running"))
    updated_at = data.get("updated_at")
    stale = False
    if running and isinstance(updated_at, str):
        try:
            u = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if u.tzinfo is None:
                u = u.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - u).total_seconds()
            if age > stale_after_sec and not base["pid_alive"]:
                stale = True
        except ValueError:
            stale = True

    total = int(data.get("total_ts") or 0)
    processed = int(data.get("processed") or 0)
    pct = round(100.0 * processed / total, 1) if total > 0 else 0.0

    return {
        "ok": True,
        "idle": not running,
        "stale": stale,
        "pid_alive": base["pid_alive"],
        "pid": base["pid"],
        "progress_path": str(path),
        "running": running,
        "phase": data.get("phase", ""),
        "started_at": data.get("started_at"),
        "updated_at": updated_at,
        "input_root": data.get("input_root", ""),
        "output_root": data.get("output_root", ""),
        "total_ts": total,
        "processed": processed,
        "percent": pct,
        "converted_ok": int(data.get("converted_ok") or 0),
        "deleted_ts": int(data.get("deleted_ts") or 0),
        "failed": int(data.get("failed") or 0),
        "current_file": data.get("current_file") or "",
        "exit_code": data.get("exit_code"),
        "preflight_error": data.get("preflight_error") or "",
        "failures_recent": data.get("failures_recent") or [],
        "failures_full_count": int(data.get("failures_full_count") or 0),
    }
