#!/usr/bin/env python3
"""Real Sound Cleanup Gate v1 — diagnostics (fail-open, JSON + MD)."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from utils.storage_paths import get_sv_cache  # type: ignore[attr-defined]
except Exception:

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")


STATEVERGE = Path.home() / "StateVerge"
CONTROL_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"
OUT_JSON = CONTROL_LOGS / "real_sound_gate_diagnose.json"
OUT_MD = CONTROL_LOGS / "real_sound_gate_diagnose.md"
GATE_SCRIPT = STATEVERGE / "scripts" / "real_sound_cleanup_gate.py"
LONG_QUEUE = STATEVERGE / "scripts" / "nyc_auto" / "auto_publish_queue.py"
SHORTS_QUEUE = STATEVERGE / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py"
SHORTS_WORKER = STATEVERGE / "scripts" / "jobs" / "shorts_cut_upload_job.py"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".diag_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _grep_file(path: Path, pattern: str) -> bool:
    try:
        return bool(re.search(pattern, path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


def _grep_gate_script() -> dict[str, bool]:
    if not GATE_SCRIPT.is_file():
        return {}
    txt = GATE_SCRIPT.read_text(encoding="utf-8", errors="replace")
    return {
        "real_sound_gate_has_vfr_safety": bool(
            re.search(r"detect_video_copy_safety|input_is_raw_iphone_or_vfr|input_is_cfr_safe", txt)
        ),
        "detects_raw_inbox_paths": bool(re.search(r"is_raw_inbox_path|SV_CACHE/inbox|00_INBOX/iphone", txt)),
        "cfr_reencode_branch_present": bool(
            re.search(r"h264_videotoolbox|libx264|fps=30,format=yuv420p", txt)
        ),
        "c_v_copy_branch_guarded": bool(re.search(r"_mux_argv_copy", txt)) and bool(
            re.search(r"use_copy|input_is_cfr_safe", txt)
        ),
        "dry_run_does_not_encode_full_video": bool(re.search(r"if dry_run:", txt))
        and bool(re.search(r"return video, rep", txt)),
        "report_fields_present": bool(re.search(r"input_duration_sec|output_duration_sec|duration_delta_sec", txt)),
    }


def _latest_under(root: Path, glob_pat: str, limit: int = 50) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    try:
        for p in root.rglob(glob_pat):
            if p.is_file():
                out.append(p)
    except OSError:
        return []
    out.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
    return out[:limit]


def main() -> int:
    warnings: list[str] = []
    critical: list[str] = []
    status = "ok"

    primary_jobs = get_sv_cache(verbose=False) / "audio_processing" / "real_sound_jobs"
    fb_jobs = Path.home() / "StateVerge" / "data" / "audio_runtime" / "real_sound_jobs"

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")

    if not ffmpeg:
        critical.append("ffmpeg_missing")
    if not ffprobe:
        critical.append("ffprobe_missing")
    if not GATE_SCRIPT.is_file():
        critical.append("real_sound_cleanup_gate.py_missing")

    primary_ok = _path_writable(primary_jobs.parent)
    fb_ok = _path_writable(fb_jobs)

    if not primary_ok and not fb_ok:
        critical.append("real_sound_job_roots_unwritable")
        status = "critical"
    elif not primary_ok:
        warnings.append("sv_cache_audio_processing_unwritable_using_fallback")
        status = "warning" if status == "ok" else status

    gate_checks = _grep_gate_script()
    long_hooked = _grep_file(LONG_QUEUE, r"run_real_sound_cleanup|real_sound_cleanup_gate")
    long_dry_gate = _grep_file(LONG_QUEUE, r"dry_run=True")
    shorts_queue_hooked = _grep_file(SHORTS_QUEUE, r"no.real.sound.gate|no_real_sound_gate")
    shorts_worker_hooked = _grep_file(SHORTS_WORKER, r"run_real_sound_cleanup|real_sound_cleanup_gate")

    reports = _latest_under(primary_jobs, "real_sound_quality_report.json") + _latest_under(
        fb_jobs, "real_sound_quality_report.json"
    )
    finals = _latest_under(primary_jobs, "final_real_sound_video.mp4") + _latest_under(
        fb_jobs, "final_real_sound_video.mp4"
    )
    reports.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    finals.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    latest_report: dict[str, Any] | None = None
    if reports:
        try:
            latest_report = json.loads(reports[0].read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            latest_report = {"error": "unreadable", "path": str(reports[0])}

    payload: dict[str, Any] = {
        "status": status,
        "generated_at": _utc(),
        "ffmpeg": bool(ffmpeg),
        "ffprobe": bool(ffprobe),
        "gate_script_exists": GATE_SCRIPT.is_file(),
        "sv_cache_real_sound_jobs_writable": primary_ok,
        "fallback_real_sound_jobs_writable": fb_ok,
        "primary_jobs_root": str(primary_jobs),
        "fallback_jobs_root": str(fb_jobs),
        "latest_report_path": str(reports[0]) if reports else "",
        "latest_final_video_path": str(finals[0]) if finals else "",
        "latest_report_summary": latest_report,
        "long_queue_integrated": long_hooked,
        "long_queue_dry_run_gate": long_dry_gate,
        "shorts_queue_integrated": shorts_queue_hooked,
        "shorts_worker_integrated": shorts_worker_hooked,
        "long_queue_gate_connected": long_hooked and long_dry_gate,
        "shorts_worker_gate_connected": shorts_worker_hooked,
        "real_sound_gate_has_vfr_safety": gate_checks.get("real_sound_gate_has_vfr_safety", False),
        "detects_raw_inbox_paths": gate_checks.get("detects_raw_inbox_paths", False),
        "cfr_reencode_branch_present": gate_checks.get("cfr_reencode_branch_present", False),
        "c_v_copy_branch_guarded": gate_checks.get("c_v_copy_branch_guarded", False),
        "dry_run_does_not_encode_full_video": gate_checks.get("dry_run_does_not_encode_full_video", False),
        "report_fields_present": gate_checks.get("report_fields_present", False),
        "warnings": warnings,
        "critical": critical,
    }

    if critical:
        payload["status"] = "critical"
    elif warnings or not long_hooked or not (shorts_worker_hooked and shorts_queue_hooked):
        if payload["status"] != "critical":
            payload["status"] = "warning"
        if not long_hooked:
            warnings.append("long_queue_gate_not_detected")
        if not long_dry_gate:
            warnings.append("long_queue_dry_run_gate_not_detected")
        if not shorts_worker_hooked:
            warnings.append("shorts_worker_gate_not_detected")
        if not shorts_queue_hooked:
            warnings.append("shorts_queue_no_real_sound_flag_not_detected")

    for k, v in gate_checks.items():
        if not v and k != "dry_run_does_not_encode_full_video":
            warnings.append(f"gate_check_failed:{k}")

    CONTROL_LOGS.mkdir(parents=True, exist_ok=True)
    try:
        OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    md_lines = [
        "# Real Sound Cleanup Gate v1 — diagnose",
        "",
        f"- **status**: {payload['status']}",
        f"- **ffmpeg / ffprobe**: {payload['ffmpeg']} / {payload['ffprobe']}",
        f"- **gate script**: {payload['gate_script_exists']}",
        f"- **SV_CACHE jobs writable**: {primary_ok}",
        f"- **fallback writable**: {fb_ok}",
        f"- **long queue integrated**: {long_hooked}",
        f"- **long dry-run gate**: {long_dry_gate}",
        f"- **shorts queue flag**: {shorts_queue_hooked}",
        f"- **shorts worker integrated**: {shorts_worker_hooked}",
        "",
        "## VFR safety patch checks",
        "",
        "```json",
        json.dumps(
            {
                k: payload[k]
                for k in (
                    "real_sound_gate_has_vfr_safety",
                    "detects_raw_inbox_paths",
                    "cfr_reencode_branch_present",
                    "c_v_copy_branch_guarded",
                    "long_queue_gate_connected",
                    "shorts_worker_gate_connected",
                    "dry_run_does_not_encode_full_video",
                    "report_fields_present",
                )
            },
            indent=2,
        ),
        "```",
        "",
        "## Latest artifacts",
        "",
        f"- report: `{payload.get('latest_report_path') or 'none'}`",
        f"- final: `{payload.get('latest_final_video_path') or 'none'}`",
        "",
        "## Critical / warnings",
        "",
        "```json",
        json.dumps({"critical": critical, "warnings": warnings}, indent=2),
        "```",
        "",
    ]
    try:
        OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")
    except OSError:
        pass

    print(json.dumps({"ok": True, "status": payload["status"], "wrote_json": str(OUT_JSON), "wrote_md": str(OUT_MD)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
