#!/usr/bin/env python3
"""Shorts schedule completion guard — diagnose today's 4 slots + optional backfill.

Schedule (America/New_York or system local): 09:30, 13:30, 17:30, 21:30.
Writes JSON to SV_CACHE review_reports (fallback StateVerge logs) and MD to Control Center logs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_REPO = Path(__file__).resolve().parent.parent
_CC_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"
_SHORTS_STDOUT = _CC_LOGS / "launchd_shorts_stdout.log"
_SHORTS_STDERR = _CC_LOGS / "launchd_shorts_stderr.log"
_QUEUE = _REPO / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py"
_PY = _REPO / ".venv_audio" / "bin" / "python3"

_SLOTS: tuple[tuple[str, int, int], ...] = (
    ("slot_0930", 9, 30),
    ("slot_1330", 13, 30),
    ("slot_1730", 17, 30),
    ("slot_2130", 21, 30),
)

_LOG_TS = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
_DUPLICATE_BLOCK = re.compile(
    r"shorts_output_quick_hash_already_uploaded|output_video_path_already_uploaded|"
    r"shorts_segment_already_reserved|shorts_image_bundle_already_reserved|duplicate",
    re.I,
)


def _local_tz() -> Any:
    try:
        return ZoneInfo("America/New_York")
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _report_json_path() -> Path:
    cache = Path("/Volumes/SV_CACHE/review_reports")
    if cache.parent.is_dir():
        try:
            cache.mkdir(parents=True, exist_ok=True)
            return cache / "shorts_schedule_completion_report.json"
        except OSError:
            pass
    for fb in (_REPO / "logs", _CC_LOGS):
        try:
            fb.mkdir(parents=True, exist_ok=True)
            return fb / "shorts_schedule_completion_report.json"
        except OSError:
            continue
    return _CC_LOGS / "shorts_schedule_completion_report.json"


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _parse_dt(value: str, tz: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz)
    except ValueError:
        return None


def _slot_datetimes(today: date, hour: int, minute: int, tz: Any) -> datetime:
    return datetime.combine(today, time(hour, minute), tzinfo=tz)


def _in_window(dt: datetime, center: datetime, margin_min: int = 15) -> bool:
    lo = center - timedelta(minutes=margin_min)
    hi = center + timedelta(minutes=margin_min)
    return lo <= dt <= hi


def _parse_log_timestamps(text: str, tz: Any) -> list[datetime]:
    out: list[datetime] = []
    for m in _LOG_TS.finditer(text):
        try:
            naive = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            out.append(naive.replace(tzinfo=tz))
        except ValueError:
            continue
    return out


def _upload_roots() -> list[Path]:
    return [
        _REPO / "data" / "shorts_runtime" / "shorts_uploads",
        Path("/Volumes/SV_TRANSFER/publish_pack/shorts_uploads"),
    ]


def _job_roots() -> list[Path]:
    return [
        _REPO / "data" / "shorts_runtime" / "jobs",
        Path("/Volumes/SV_CACHE/jobs/shorts"),
    ]


def _is_success_unlisted_upload(doc: dict[str, Any], up: dict[str, Any] | None) -> bool:
    if up:
        if up.get("ok") is True and str(up.get("status") or "").lower() in ("success", "uploaded", "ok"):
            priv = str(up.get("privacy_used") or up.get("privacy") or "").lower()
            if priv == "public":
                return False
            return priv in ("", "unlisted", "private") and bool(up.get("video_id"))
    if doc.get("uploaded") is True:
        priv = str(doc.get("privacy_status") or doc.get("privacy") or "unlisted").lower()
        if priv == "public":
            return False
        return priv in ("unlisted", "private", "")
    st = str(doc.get("status") or "").lower()
    if st == "uploaded" and doc.get("youtube_video_id"):
        priv = str(doc.get("privacy_status") or "unlisted").lower()
        return priv != "public"
    return False


def _collect_uploads_today(today: date, tz: Any) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for root in _upload_roots():
        if not root.is_dir():
            continue
        for result_path in root.glob("*/shorts_job_result.json"):
            job_dir = result_path.parent
            job_id = job_dir.name
            doc = _safe_read_json(result_path)
            up_path = job_dir / "upload_result.json"
            up = _safe_read_json(up_path) if up_path.is_file() else None
            created = _parse_dt(str(doc.get("created_at") or ""), tz)
            if created and created.date() != today:
                continue
            if not _is_success_unlisted_upload(doc, up if up else None):
                continue
            vid = str(
                (up or {}).get("video_id")
                or doc.get("youtube_video_id")
                or ""
            ).strip()
            if vid and vid in seen:
                continue
            if vid:
                seen.add(vid)
            rows.append(
                {
                    "job_id": job_id,
                    "video_id": vid,
                    "created_at": doc.get("created_at"),
                    "privacy": str(
                        (up or {}).get("privacy_used")
                        or (up or {}).get("privacy")
                        or doc.get("privacy_status")
                        or "unlisted"
                    ),
                    "shorts_job_result": str(result_path),
                    "upload_result": str(up_path) if up_path.is_file() else "",
                }
            )
    rows.sort(key=lambda r: str(r.get("created_at") or ""))
    return rows


def _iter_jobs_today(today: date, tz: Any) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for root in _job_roots():
        if not root.is_dir():
            continue
        for jp in root.glob("*/job.json"):
            doc = _safe_read_json(jp)
            created = _parse_dt(str(doc.get("created_at") or ""), tz)
            if not created or created.date() != today:
                continue
            result = doc.get("result") if isinstance(doc.get("result"), dict) else {}
            jobs.append(
                {
                    "job_id": str(doc.get("job_id") or jp.parent.name),
                    "job_path": str(jp),
                    "created_at": doc.get("created_at"),
                    "created_local": created.isoformat(),
                    "status": str(doc.get("status") or result.get("status") or ""),
                    "uploaded": bool(result.get("uploaded")),
                    "block_reason": str(result.get("block_reason") or ""),
                    "warnings": list(result.get("warnings") or doc.get("warnings") or []),
                    "upload_result_path": str(result.get("upload_result_path") or ""),
                    "result": result,
                }
            )
    jobs.sort(key=lambda j: str(j.get("created_at") or ""))
    return jobs


def _log_blob() -> str:
    return _safe_read_text(_SHORTS_STDOUT) + "\n" + _safe_read_text(_SHORTS_STDERR)


def _slot_triggered(
    slot_center: datetime,
    log_times: list[datetime],
    jobs: list[dict[str, Any]],
    log_text: str,
) -> tuple[bool, str]:
    for lt in log_times:
        if lt.date() == slot_center.date() and _in_window(lt, slot_center):
            return True, "launchd_log_timestamp"
    for job in jobs:
        created = _parse_dt(str(job.get("created_at") or ""), slot_center.tzinfo)
        if created and _in_window(created, slot_center):
            return True, "job_created_at"
    # Untimestamped doctor_gate lines indicate a launchd run (no wall-clock in line).
    if "doctor_gate_warnings" in log_text and slot_center <= datetime.now(slot_center.tzinfo):
        # Weak signal only when no better evidence; prefer false unless slot time passed.
        pass
    return False, ""


def _log_excerpt_for_window(log_text: str, center: datetime, margin_min: int = 15) -> str:
    lines: list[str] = []
    tz = center.tzinfo
    for line in log_text.splitlines():
        m = _LOG_TS.search(line)
        if m:
            try:
                dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
                if _in_window(dt, center, margin_min):
                    lines.append(line)
            except ValueError:
                continue
    return "\n".join(lines)


def _checks_for_job(
    job: dict[str, Any] | None,
    log_text: str,
    slot_center: datetime,
) -> dict[str, Any]:
    result = (job or {}).get("result") if job else {}
    if not isinstance(result, dict):
        result = {}
    warnings = list((job or {}).get("warnings") or result.get("warnings") or [])
    warn_text = " ".join(str(w) for w in warnings).lower()
    block = str((job or {}).get("block_reason") or result.get("block_reason") or "").lower()
    log_slice = _log_excerpt_for_window(log_text, slot_center)

    ffmpeg_missing = bool(
        re.search(r"FileNotFoundError.*ffmpeg|ffmpeg.*not found|No such file.*ffmpeg", log_slice, re.I)
        or ("filenotfounderror" in log_slice.lower() and "ffmpeg" in log_slice.lower())
    )
    image_dim_probe_timeout = "image_dim_probe_timeout" in warn_text or "image_dim_probe_timeout" in log_slice
    global_lock_busy = (
        "shorts_worker_global_lock_busy" in log_slice
        or "global_lock_busy" in block
    )
    duplicate_block = bool(_DUPLICATE_BLOCK.search(block) or _DUPLICATE_BLOCK.search(log_slice))
    doctor_warning_only = bool(
        "doctor_gate_warnings" in log_slice and "blocked_by_doctor_gate" not in log_slice
    )
    doctor_blocked = "blocked_by_doctor_gate" in log_slice or "doctor_gate_block_upload" in block
    upload_result_path = str(result.get("upload_result_path") or (job or {}).get("upload_result_path") or "")
    missing_upload_result = bool(
        result.get("upload_attempted") and not Path(upload_result_path).is_file()
    ) if result else False

    return {
        "ffmpeg_missing": ffmpeg_missing,
        "image_dim_probe_timeout": image_dim_probe_timeout,
        "global_lock_busy": global_lock_busy,
        "duplicate_block": duplicate_block,
        "doctor_warning_only": doctor_warning_only,
        "doctor_blocked": doctor_blocked,
        "missing_upload_result": missing_upload_result,
    }


def _match_job_to_slot(
    slot_center: datetime,
    jobs: list[dict[str, Any]],
    uploads: list[dict[str, Any]],
    log_times: list[datetime],
) -> dict[str, Any] | None:
    tz = slot_center.tzinfo
    best: tuple[float, dict[str, Any]] | None = None
    for job in jobs:
        created = _parse_dt(str(job.get("created_at") or ""), tz)
        if not created or not _in_window(created, slot_center):
            continue
        delta = abs((created - slot_center).total_seconds())
        if best is None or delta < best[0]:
            best = (delta, job)
    if best:
        return best[1]
    # Match successful upload by created time
    for up in uploads:
        created = _parse_dt(str(up.get("created_at") or ""), tz)
        if created and _in_window(created, slot_center):
            return {
                "job_id": up.get("job_id"),
                "uploaded": True,
                "block_reason": "",
                "warnings": [],
                "result": {"uploaded": True},
                "from_upload_scan": True,
            }
    # Log-based upload in window
    for lt in log_times:
        if lt.date() == slot_center.date() and _in_window(lt, slot_center) and "OK video_id" in _log_blob():
            return {
                "job_id": "",
                "uploaded": "OK video_id" in _log_blob(),
                "block_reason": "",
                "warnings": [],
                "result": {},
                "from_log_only": True,
            }
    return None


def _failure_reason(
    job: dict[str, Any] | None,
    uploaded: bool,
    checks: dict[str, Any],
    triggered: bool,
    *,
    pending: bool = False,
) -> str:
    if uploaded:
        return ""
    if pending:
        return "slot_pending"
    if not triggered:
        return "schedule_not_triggered"
    if checks.get("global_lock_busy"):
        return "global_lock_busy"
    if checks.get("duplicate_block"):
        return "duplicate_block"
    if checks.get("doctor_blocked"):
        return "doctor_gate_block"
    if checks.get("ffmpeg_missing"):
        return "ffmpeg_missing"
    if checks.get("image_dim_probe_timeout"):
        return "image_dim_probe_timeout"
    if checks.get("missing_upload_result"):
        return "missing_upload_result"
    if job:
        br = str(job.get("block_reason") or "").strip()
        if br:
            return br
        st = str(job.get("status") or "").strip()
        if st and st not in ("uploaded", "completed"):
            return st
    return "upload_not_completed"


def diagnose() -> dict[str, Any]:
    tz = _local_tz()
    today = datetime.now(tz).date()
    log_text = _log_blob()
    log_times = _parse_log_timestamps(log_text, tz)
    jobs_today = _iter_jobs_today(today, tz)
    uploads_today = _collect_uploads_today(today, tz)
    uploaded_count = len(uploads_today)
    expected = 4
    missing_count = max(0, expected - uploaded_count)

    slots_out: list[dict[str, Any]] = []
    for label, hour, minute in _SLOTS:
        center = _slot_datetimes(today, hour, minute, tz)
        triggered, trigger_evidence = _slot_triggered(center, log_times, jobs_today, log_text)
        now = datetime.now(tz)
        pending = now < center - timedelta(minutes=15)
        if pending:
            triggered = False
            trigger_evidence = "slot_not_yet_due"
        job = _match_job_to_slot(center, jobs_today, uploads_today, log_times)
        job_id = str((job or {}).get("job_id") or "")
        uploaded = bool((job or {}).get("uploaded")) if job else False
        if not uploaded and job_id:
            for u in uploads_today:
                if u.get("job_id") == job_id:
                    uploaded = True
                    break
        if not uploaded:
            for u in uploads_today:
                c = _parse_dt(str(u.get("created_at") or ""), tz)
                if c and _in_window(c, center):
                    uploaded = True
                    job_id = job_id or str(u.get("job_id") or "")
                    break
        checks = _checks_for_job(job, log_text, center)
        failure = _failure_reason(job, uploaded, checks, triggered, pending=pending)
        slots_out.append(
            {
                "slot": label,
                "local_time": f"{hour:02d}:{minute:02d}",
                "slot_center_local": center.isoformat(),
                "slot_pending": pending,
                "schedule_triggered": triggered,
                "trigger_evidence": trigger_evidence,
                "job_id": job_id,
                "uploaded": uploaded,
                "failure_reason": failure,
                "checks": checks,
            }
        )

    return {
        "schema": "shorts_schedule_completion_v1",
        "generated_at": _utc_iso(),
        "timezone": str(tz),
        "date_local": today.isoformat(),
        "expected_today": expected,
        "uploaded_today": uploaded_count,
        "missing_count": missing_count,
        "uploads_today": uploads_today,
        "slots": slots_out,
        "log_paths": {
            "stdout": str(_SHORTS_STDOUT),
            "stderr": str(_SHORTS_STDERR),
        },
        "backfill_attempted": 0,
        "backfill_uploaded": 0,
        "backfill_results": [],
        "unresolved_block_reason": "",
    }


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    path = env.get("PATH", "")
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        if prefix not in path.split(":"):
            path = f"{prefix}:{path}" if path else prefix
    env["PATH"] = path
    if not env.get("FFMPEG_BIN"):
        fb = shutil.which("ffmpeg", path=env["PATH"])
        if fb:
            env["FFMPEG_BIN"] = fb
    if not env.get("FFPROBE_BIN"):
        fp = shutil.which("ffprobe", path=env["PATH"])
        if fp:
            env["FFPROBE_BIN"] = fp
    return env


def _parse_backfill_output(stdout: str, stderr: str, code: int) -> dict[str, Any]:
    blob = stdout + "\n" + stderr
    out: dict[str, Any] = {"exit_code": code, "stdout_tail": stdout[-4000:], "stderr_tail": stderr[-4000:]}
    for line in reversed(blob.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                doc = json.loads(line)
                if isinstance(doc, dict):
                    out["json_line"] = doc
                    break
            except json.JSONDecodeError:
                continue
    if "shorts_worker_global_lock_busy" in blob:
        out["stop_reason"] = "global_lock_busy"
    elif out.get("json_line", {}).get("blocked_by_doctor_gate"):
        out["stop_reason"] = "doctor_gate_block_upload"
    elif out.get("json_line", {}).get("block_reason") == "channel_guard_failed":
        out["stop_reason"] = "channel_guard_failed"
    elif _DUPLICATE_BLOCK.search(blob):
        out["stop_reason"] = "duplicate_block"
    elif code == 14:
        out["stop_reason"] = "global_lock_busy"
    elif code == 8:
        out["stop_reason"] = "doctor_gate_block_upload"
    elif code == 7:
        out["stop_reason"] = "channel_guard_failed"
    uploaded = False
    jl = out.get("json_line") if isinstance(out.get("json_line"), dict) else {}
    if jl.get("uploaded") is True or jl.get("status") == "uploaded":
        uploaded = True
    if "OK video_id" in blob:
        uploaded = True
    out["uploaded"] = uploaded
    return out


def backfill(report: dict[str, Any]) -> dict[str, Any]:
    missing = int(report.get("missing_count") or 0)
    if missing <= 0:
        return report

    py = str(_PY if _PY.is_file() else (shutil.which("python3") or "python3"))
    env = _child_env()
    attempted = 0
    uploaded_n = 0
    results: list[dict[str, Any]] = []
    unresolved = ""

    for _ in range(missing):
        attempted += 1
        cmd = [
            py,
            str(_QUEUE),
            "--upload",
            "--privacy-status",
            "unlisted",
            "--max-count",
            "1",
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(_REPO),
                capture_output=True,
                text=True,
                env=env,
                timeout=3600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append({"attempt": attempted, "error": repr(exc), "uploaded": False})
            unresolved = str(exc)
            break

        parsed = _parse_backfill_output(proc.stdout or "", proc.stderr or "", proc.returncode)
        parsed["attempt"] = attempted
        results.append(parsed)

        if parsed.get("uploaded"):
            uploaded_n += 1
        stop = str(parsed.get("stop_reason") or "")
        if stop == "global_lock_busy":
            unresolved = "global_lock_busy"
            break
        if stop in ("duplicate_block", "doctor_gate_block_upload", "channel_guard_failed"):
            unresolved = stop
            break
        if not parsed.get("uploaded") and proc.returncode != 0:
            unresolved = str(
                (parsed.get("json_line") or {}).get("block_reason")
                or f"exit_{proc.returncode}"
            )
            break

    report["backfill_attempted"] = attempted
    report["backfill_uploaded"] = uploaded_n
    report["backfill_results"] = results
    report["unresolved_block_reason"] = unresolved

    # Refresh counts after backfill
    fresh = diagnose()
    for k in (
        "uploaded_today",
        "missing_count",
        "uploads_today",
        "slots",
    ):
        report[k] = fresh[k]
    report["backfill_attempted"] = attempted
    report["backfill_uploaded"] = uploaded_n
    report["backfill_results"] = results
    report["unresolved_block_reason"] = unresolved
    return report


def _write_md(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Shorts schedule completion",
        "",
        f"**date (local)**: `{report.get('date_local')}`",
        f"**timezone**: `{report.get('timezone')}`",
        f"**generated_at**: `{report.get('generated_at')}`",
        "",
        "## Summary",
        "",
        f"- expected_today: **{report.get('expected_today')}**",
        f"- uploaded_today: **{report.get('uploaded_today')}**",
        f"- missing_count: **{report.get('missing_count')}**",
        f"- backfill_attempted: **{report.get('backfill_attempted', 0)}**",
        f"- backfill_uploaded: **{report.get('backfill_uploaded', 0)}**",
    ]
    if report.get("unresolved_block_reason"):
        lines.append(f"- unresolved_block_reason: `{report.get('unresolved_block_reason')}`")
    lines += ["", "## Slots", ""]
    for slot in report.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        lines.append(
            f"### {slot.get('slot')} ({slot.get('local_time')}) — "
            f"triggered={slot.get('schedule_triggered')} uploaded={slot.get('uploaded')}"
        )
        if slot.get("failure_reason"):
            lines.append(f"- failure: `{slot.get('failure_reason')}`")
        if slot.get("job_id"):
            lines.append(f"- job_id: `{slot.get('job_id')}`")
        chk = slot.get("checks") or {}
        flags = [k for k, v in chk.items() if v]
        if flags:
            lines.append(f"- checks: {', '.join(f'`{f}`' for f in flags)}")
        lines.append("")
    if report.get("backfill_results"):
        lines += ["## Backfill results", "", "```json"]
        lines.append(json.dumps(report["backfill_results"], indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _print_footer(report: dict[str, Any], ready: bool) -> None:
    print(f"SHORTS_SCHEDULE_COMPLETION_GUARD_READY={'true' if ready else 'false'}")
    print(f"EXPECTED_TODAY={report.get('expected_today', 4)}")
    print(f"UPLOADED_TODAY={report.get('uploaded_today', 0)}")
    print(f"MISSING_COUNT={report.get('missing_count', 0)}")
    print(f"BACKFILL_UPLOADED={report.get('backfill_uploaded', 0)}")
    reason = report.get("unresolved_block_reason") or ""
    print(f"UNRESOLVED_REASON={reason if reason else 'none'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Shorts schedule completion diagnose + optional backfill.")
    ap.add_argument(
        "--backfill-missing",
        action="store_true",
        help="Run up to missing_count unlisted uploads (stops on lock/duplicate/doctor block).",
    )
    args = ap.parse_args(argv)

    report = diagnose()
    ready = True

    if args.backfill_missing and int(report.get("missing_count") or 0) > 0:
        report = backfill(report)

    json_path = _report_json_path()
    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        ready = False
        report["json_write_error"] = repr(exc)

    md_path = _CC_LOGS / "shorts_schedule_completion_report.md"
    try:
        _write_md(report, md_path)
        report["report_json_path"] = str(json_path)
        report["report_md_path"] = str(md_path)
    except OSError as exc:
        ready = False
        report["md_write_error"] = repr(exc)

    _print_footer(report, ready)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
