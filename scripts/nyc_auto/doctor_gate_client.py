"""Read StateVerge Doctor report and evaluate upload gates (fail-open, minimal deps)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HOME = Path.home()
_DOCTOR_JSON_CANDIDATES: tuple[Path, ...] = (
    Path("/Volumes/SV_CACHE/logs/stateverge_doctor_report.json"),
    _HOME / "StateVerge" / "logs" / "stateverge_doctor_report.json",
    _HOME / "StateVerge_Control_Center" / "logs" / "stateverge_doctor_report.json",
)
_STATEVERGE = _HOME / "StateVerge"
_SV_TRANSFER = Path("/Volumes/SV_TRANSFER")
_MIN_SELECTED_DURATION_S = 5.0
_MIN_SELECTED_BYTES = 1024 * 1024
_RECENT_LOCK_SECONDS = 2 * 3600
_STALE_DOCTOR_REPORT_SECONDS = 2 * 3600


@dataclass
class UploadSafetySelectedFileResult:
    upload_allowed: bool
    block_reasons: list[str]
    warning_reasons: list[str]
    selected_file_check: dict[str, Any]


def resolve_doctor_report_json_paths() -> list[Path]:
    return list(_DOCTOR_JSON_CANDIDATES)


def load_doctor_report_json() -> tuple[dict[str, Any] | None, Path | None, list[str]]:
    """Return (report, path_used, warnings). Never raises."""
    warns: list[str] = []
    for p in _DOCTOR_JSON_CANDIDATES:
        try:
            if not p.is_file():
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data, p, warns
            warns.append(f"doctor_report_not_dict:{p}")
        except (OSError, json.JSONDecodeError) as exc:
            warns.append(f"doctor_report_read_failed:{p}:{exc!r}")
            continue
    warns.append("doctor_report_missing_all_candidates")
    return None, None, warns


def is_upload_blocked_by_doctor_gate(report: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
    """Interpret doctor_gate from report; fail-open: missing report does not block."""
    if not report:
        return False, {"reason": "no_report", "doctor_gate": None}
    gate = str(report.get("doctor_gate") or "").strip().upper()
    if gate == "BLOCK_UPLOAD":
        return True, {
            "reason": "doctor_gate_block_upload",
            "doctor_gate": gate,
            "gate_reason": str(report.get("gate_reason") or ""),
            "upload_allowed": bool(report.get("upload_allowed", False)),
        }
    return False, {
        "reason": "ok_or_warning",
        "doctor_gate": gate or str(report.get("doctor_gate") or ""),
        "gate_reason": str(report.get("gate_reason") or ""),
        "upload_allowed": bool(report.get("upload_allowed", True)),
    }


def _read_refresh_token(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("refresh_token", "refreshToken", "_refresh_token"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _token_context(mode: str) -> tuple[list[str], list[str], dict[str, Any]]:
    block: list[str] = []
    warn: list[str] = []
    long_path = _STATEVERGE / "data" / "youtube" / "token.json"
    shorts_path = _STATEVERGE / "data" / "youtube" / "token_shorts.json"
    long_present = long_path.is_file()
    shorts_present = shorts_path.is_file()
    long_refresh = _read_refresh_token(long_path)
    shorts_refresh = _read_refresh_token(shorts_path)
    detail = {
        "long_token_path": str(long_path),
        "shorts_token_path": str(shorts_path),
        "long_token_present": long_present,
        "shorts_token_present": shorts_present,
        "channel_isolation_ok": bool(long_refresh and shorts_refresh and long_refresh != shorts_refresh),
    }
    if mode == "long" and not long_present:
        block.append("long_upload_token_missing")
    if mode == "shorts" and not shorts_present:
        block.append("shorts_upload_token_missing")
    if long_present and shorts_present:
        if long_refresh and shorts_refresh and long_refresh == shorts_refresh:
            block.append("channel_tokens_duplicated")
        elif not long_refresh or not shorts_refresh:
            warn.append("token_refresh_field_unreadable")
    elif long_present != shorts_present:
        warn.append("one_upload_token_missing_non_selected_mode")
    return block, warn, detail


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _extract_pid(text: str) -> int | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            pid = data.get("pid") or data.get("process_id")
            if pid is not None:
                return int(pid)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    for token in raw.replace("=", " ").replace(":", " ").split():
        if token.isdigit():
            try:
                return int(token)
            except ValueError:
                return None
    return None


def _inspect_upload_locks() -> tuple[list[str], list[str], list[dict[str, Any]]]:
    block: list[str] = []
    warn: list[str] = []
    lock_paths = (
        _HOME / "StateVerge" / "data" / "shorts_runtime" / ".shorts_cut_upload.global.lock",
        _SV_TRANSFER / "publish_pack" / "nyc_long_uploads" / ".nyc_long_upload.lock",
        _HOME / "StateVerge" / "data" / "long_runtime" / "long_uploads" / ".nyc_long_upload.lock",
        Path("/Volumes/SV_TRANSFER/publish_pack/shorts_uploads/shorts_used_assets.json.shorts_used_assets.fcntl.lock"),
    )
    rows: list[dict[str, Any]] = []
    for path in lock_paths:
        row: dict[str, Any] = {"path": str(path), "exists": False, "recent": False, "pid": None, "process_alive": False}
        try:
            if not path.is_file():
                rows.append(row)
                continue
            row["exists"] = True
            age = max(0.0, time.time() - path.stat().st_mtime)
            row["age_seconds"] = round(age, 1)
            row["recent"] = age <= _RECENT_LOCK_SECONDS
            pid: int | None = None
            text = path.read_text(encoding="utf-8", errors="replace")[:1000]
            pid = _extract_pid(text)
            if pid is not None:
                row["pid"] = pid
                row["process_alive"] = _pid_alive(pid)
            if row["recent"] and row["process_alive"] and pid == os.getpid():
                row["self_process"] = True
            elif row["recent"] and row["process_alive"]:
                block.append(f"upload_lock_active_process_alive:{path}")
            elif row["recent"]:
                warn.append(f"upload_lock_recent_process_unverified:{path}")
            else:
                warn.append(f"stale_upload_lock_process_unverified:{path}")
        except OSError as exc:
            warn.append(f"upload_lock_inspect_failed:{path}:{exc!r}")
        rows.append(row)
    return block, warn, rows


def _ffprobe_duration(path: Path) -> tuple[float | None, str | None]:
    exe = shutil.which("ffprobe")
    if not exe:
        return None, "ffprobe_not_on_path"
    cmd = [
        exe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if r.returncode != 0:
            err = (r.stderr or "").strip().splitlines()
            return None, f"ffprobe_failed:{r.returncode}:{err[-1] if err else ''}"
        dur = float((r.stdout or "").strip() or "0")
        return (dur if dur > 0 else None), None
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return None, repr(exc)


def _target_writable(path: Path | None) -> tuple[bool, str | None]:
    if path is None:
        return True, None
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".upload_gate_write_probe_{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, None
    except OSError as exc:
        return False, repr(exc)


def check_upload_safety_selected_file(
    path: Path,
    *,
    mode: str,
    dry_run: bool,
    public_upload_requested: bool,
    explicit_public_confirmation: bool,
    target_upload_dir: Path | None,
    dedupe_hit: bool,
    already_uploaded: bool,
) -> dict[str, Any]:
    """Gate a real upload on the selected file only; dry-run never blocks."""
    mode_norm = "shorts" if str(mode).lower() == "shorts" else "long"
    block_reasons: list[str] = []
    warning_reasons: list[str] = []
    selected: dict[str, Any] = {
        "status": "checked",
        "mode": mode_norm,
        "path": str(path),
        "exists": False,
        "ffprobe_ok": False,
        "duration_s": None,
        "size_bytes": None,
        "checks": {},
    }

    if not path.is_file():
        block_reasons.append("selected_file_missing")
        selected["status"] = "missing"
    else:
        selected["exists"] = True
        try:
            size = int(path.stat().st_size)
            selected["size_bytes"] = size
            selected["checks"]["size_at_least_1mb"] = size >= _MIN_SELECTED_BYTES
            if size < _MIN_SELECTED_BYTES:
                block_reasons.append("selected_file_size_below_1mb")
        except OSError as exc:
            block_reasons.append("selected_file_stat_failed")
            selected["stat_error"] = repr(exc)
        duration, ff_err = _ffprobe_duration(path)
        selected["duration_s"] = duration
        selected["ffprobe_ok"] = ff_err is None and duration is not None
        selected["checks"]["duration_at_least_5s"] = bool(duration is not None and duration >= _MIN_SELECTED_DURATION_S)
        if ff_err is not None or duration is None:
            block_reasons.append("selected_file_ffprobe_failed")
            selected["ffprobe_error"] = ff_err
        elif duration < _MIN_SELECTED_DURATION_S:
            block_reasons.append("selected_file_duration_below_5s")

    if already_uploaded:
        block_reasons.append("selected_file_already_uploaded")
    if dedupe_hit:
        block_reasons.append("selected_file_dedupe_hit")
    selected["checks"]["already_uploaded"] = bool(already_uploaded)
    selected["checks"]["dedupe_hit"] = bool(dedupe_hit)

    target_ok, target_err = _target_writable(target_upload_dir)
    selected["checks"]["target_upload_dir_writable"] = target_ok
    if target_upload_dir is not None:
        selected["target_upload_dir"] = str(target_upload_dir)
    if not target_ok:
        block_reasons.append("target_upload_dir_not_writable")
        selected["target_upload_dir_error"] = target_err

    if public_upload_requested and not explicit_public_confirmation:
        block_reasons.append("public_upload_without_explicit_confirmation")
    selected["checks"]["public_upload_confirmed_if_requested"] = (
        (not public_upload_requested) or explicit_public_confirmation
    )

    tb, tw, token_detail = _token_context(mode_norm)
    block_reasons.extend(tb)
    warning_reasons.extend(tw)
    selected["token_check"] = token_detail

    lb, lw, locks = _inspect_upload_locks()
    block_reasons.extend(lb)
    warning_reasons.extend(lw)
    selected["lock_check"] = locks

    raw_block_reasons = list(dict.fromkeys(block_reasons))
    warning_reasons = list(dict.fromkeys(warning_reasons))
    if dry_run:
        for reason in raw_block_reasons:
            warning_reasons.append(f"dry_run_bypassed_block:{reason}")
        block_reasons = []
        selected["status"] = "dry_run_checked"
    else:
        block_reasons = raw_block_reasons
        selected["status"] = "blocked" if block_reasons else "ok"

    result = UploadSafetySelectedFileResult(
        upload_allowed=bool(dry_run or not block_reasons),
        block_reasons=block_reasons,
        warning_reasons=list(dict.fromkeys(warning_reasons)),
        selected_file_check=selected,
    )
    return asdict(result)


def check_real_upload_allowed_by_doctor(*, dry_run: bool) -> tuple[bool, dict[str, Any], list[str]]:
    """Compatibility report gate. Dry-run never blocks; report-level gate is fail-open."""
    warns: list[str] = []
    if dry_run:
        return True, {"skipped": "dry_run"}, warns
    rep, path_used, w2 = load_doctor_report_json()
    warns.extend(w2)
    if path_used:
        try:
            age = max(0.0, time.time() - path_used.stat().st_mtime)
            if age > _STALE_DOCTOR_REPORT_SECONDS:
                warns.append(f"old_doctor_report_warning_only:{int(age)}s")
                return True, {
                    "reason": "old_report_warning_only",
                    "doctor_report_path": str(path_used),
                    "doctor_report_age_seconds": int(age),
                }, warns
        except OSError as exc:
            warns.append(f"doctor_report_stat_failed:{path_used}:{exc!r}")
    blocked, detail = is_upload_blocked_by_doctor_gate(rep)
    if path_used:
        detail = {**detail, "doctor_report_path": str(path_used)}
    return not blocked, detail, warns


def log_doctor_gate_block_upload() -> None:
    msg = "DOCTOR_GATE_BLOCK_UPLOAD"
    logger.error(msg)
    print(msg, flush=True)
