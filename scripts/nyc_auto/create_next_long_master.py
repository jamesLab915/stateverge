#!/usr/bin/env python3
"""Build next NYC long master from inbox: CFR + crop to 16:9 via concat demuxer + single encode. No upload."""
from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent.parent
_NYC = Path(__file__).resolve().parent
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import diagnose_long_master_supply as lms  # noqa: E402
import auto_publish_queue as apq  # noqa: E402

try:
    from utils.storage_paths import get_sv_cache, get_sv_transfer, get_transfer_ready_to_upload  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")

    def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER/ready_to_upload")


from nyc_long_source_policy import (  # noqa: E402
    LONG_SOURCE_POLICY_VERSION,
    is_allowed_long_video169_candidate_path,
    long_source_pool_origin,
)
from nyc_ferry_prefer_date_v1 import (  # noqa: E402
    append_prefer_manifest_fields,
    count_preferred_ferry_files,
    resolve_prefer_dates,
)
from nyc_common import parse_fraction  # noqa: E402
from nyc_long_schedule_v1 import (  # noqa: E402
    evaluate_publish_schedule_gate,
    get_today_plan,
    min_master_duration_for_kind,
    record_production,
    schedule_metadata_for_job,
)


def _video169_source_violations(sources: list[dict[str, Any]]) -> list[str]:
    bad: list[str] = []
    for ent in sources:
        if not isinstance(ent, dict):
            continue
        p = Path(str(ent.get("path") or "").strip())
        if not p.is_file():
            bad.append(f"missing_or_not_file:{p}")
            continue
        if not is_allowed_long_video169_candidate_path(p):
            bad.append(str(p))
        elif long_source_pool_origin(p) != "raw_video169_source_pool":
            bad.append(str(p))
    return bad


FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
CREATE_LONG_MASTER_LOCK = ".create_long_master.lock"
_LONG_MASTER_MIN_DURATION_SEC = 3600.0
_LOCK_HELD: Path | None = None


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _emit_ferry_may15_status(*, ferry_pref: int, pick_note: str, mono_st: str | None) -> None:
    ready = ferry_pref > 0 and pick_note in ("ok", "dry_run_ok")
    if mono_st != "ferry":
        ready = ferry_pref > 0
    print(f"FERRY_MAY15_FILES_FOUND={ferry_pref}")
    print(f"FERRY_MAY15_PRIORITY_READY={str(ready).lower()}")


def _escape_concat_path(p: Path) -> str:
    s = str(p.resolve() if p.exists() else p).replace("\\", "/")
    return s.replace("'", "'\\''")


def _create_lock_path(xfer: Path) -> Path:
    return xfer / "publish_pack" / "nyc_long_uploads" / CREATE_LONG_MASTER_LOCK


def _release_create_lock() -> None:
    global _LOCK_HELD  # noqa: PLW0603
    if _LOCK_HELD is None:
        return
    apq._release_lock(_LOCK_HELD)  # noqa: SLF001
    _LOCK_HELD = None


def _acquire_create_lock(xfer: Path, warnings: list[str]) -> tuple[bool, str]:
    global _LOCK_HELD  # noqa: PLW0603
    lock_path = _create_lock_path(xfer)
    ok, _reason = apq._try_acquire_lock(  # noqa: SLF001
        lock_path,
        f"clm_{_utc_compact()}",
        warnings,
        mode="create_long_master",
    )
    if not ok:
        return False, "create_long_master_already_running"
    _LOCK_HELD = lock_path
    atexit.register(_release_create_lock)
    return True, ""


def _invalid_probe_quarantine_dir(ready: Path) -> Path:
    return ready / "_invalid_probe" / _utc_compact()


def _quarantine_master(path: Path, *, ready: Path, reason: str, warnings: list[str]) -> Path | None:
    if not path.is_file():
        return None
    qdir = _invalid_probe_quarantine_dir(ready)
    try:
        qdir.mkdir(parents=True, exist_ok=True)
        clips_sub = qdir / "nyc_long_clips"
        clips_sub.mkdir(parents=True, exist_ok=True)
        dst = clips_sub / path.name
        if dst.exists():
            dst = clips_sub / f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}"
        shutil.move(str(path), str(dst))
        man = {
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
            "src": str(path),
            "dst": str(dst),
            "reason": reason,
        }
        (qdir / "quarantine_manifest.json").write_text(
            json.dumps(man, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        warnings.append(f"quarantined_master:{path.name}:{reason}")
        return dst
    except OSError as exc:
        warnings.append(f"quarantine_failed:{path.name}:{exc!r}")
        return None


def _first_stream(probe: dict[str, Any], codec_type: str) -> dict[str, Any] | None:
    for st in probe.get("streams") or []:
        if isinstance(st, dict) and (st.get("codec_type") or "").lower() == codec_type.lower():
            return st
    return None


def _validate_qualified_long_master(
    out_mp4: Path,
    *,
    min_duration_sec: float = _LONG_MASTER_MIN_DURATION_SEC,
    expected_fps: int = 30,
) -> tuple[bool, dict[str, Any]]:
    """Post-encode gate: duration, moov/ffprobe, h264 1920x1080 CFR~30, AAC 48k stereo yuv420p."""
    rep: dict[str, Any] = {"path": str(out_mp4), "min_duration_sec": float(min_duration_sec)}
    probe = apq._ffprobe_json(out_mp4)  # noqa: SLF001
    if not probe:
        rep["ok"] = False
        rep["reason"] = "ffprobe_failed_or_corrupt_moov"
        return False, rep
    dur, has_v = apq._duration_and_has_video(probe)  # noqa: SLF001
    w, h = apq._primary_video_dims(probe)  # noqa: SLF001
    vst = _first_stream(probe, "video")
    ast = _first_stream(probe, "audio")
    rep.update(
        {
            "has_video_stream": bool(has_v),
            "duration_sec": float(dur or 0.0),
            "width": w,
            "height": h,
        }
    )
    if not has_v or dur is None:
        rep["ok"] = False
        rep["reason"] = "no_video_stream"
        return False, rep
    if float(dur) < float(min_duration_sec):
        rep["ok"] = False
        rep["reason"] = "duration_below_min"
        return False, rep
    if w != 1920 or h != 1080:
        rep["ok"] = False
        rep["reason"] = f"resolution_want_1920x1080_got_{w}x{h}"
        return False, rep
    vcodec = str((vst or {}).get("codec_name") or "").lower()
    if vcodec != "h264":
        rep["ok"] = False
        rep["reason"] = f"video_codec_want_h264_got_{vcodec or 'unknown'}"
        return False, rep
    pix = str((vst or {}).get("pix_fmt") or "").lower()
    rep["pix_fmt"] = pix
    if pix and pix != "yuv420p":
        rep["ok"] = False
        rep["reason"] = f"pix_fmt_want_yuv420p_got_{pix}"
        return False, rep
    afr_s = str((vst or {}).get("avg_frame_rate") or "")
    rfr_s = str((vst or {}).get("r_frame_rate") or "")
    rep["avg_frame_rate"] = afr_s
    rep["r_frame_rate"] = rfr_s
    fps = parse_fraction(afr_s) or 0.0
    exp = float(expected_fps)
    if abs(fps - exp) > 0.6 and afr_s != f"{expected_fps}/1":
        rep["ok"] = False
        rep["reason"] = f"avg_frame_rate_want_{expected_fps}_got_{afr_s}"
        return False, rep
    if not ast:
        rep["ok"] = False
        rep["reason"] = "no_audio_stream"
        return False, rep
    acodec = str(ast.get("codec_name") or "").lower()
    try:
        sample_rate = int(ast.get("sample_rate") or 0)
    except (TypeError, ValueError):
        sample_rate = 0
    try:
        channels = int(ast.get("channels") or 0)
    except (TypeError, ValueError):
        channels = 0
    rep["audio_codec"] = acodec
    rep["sample_rate"] = sample_rate
    rep["channels"] = channels
    if acodec != "aac":
        rep["ok"] = False
        rep["reason"] = f"audio_codec_want_aac_got_{acodec or 'unknown'}"
        return False, rep
    if sample_rate != 48000:
        rep["ok"] = False
        rep["reason"] = f"sample_rate_want_48000_got_{sample_rate}"
        return False, rep
    if channels != 2:
        rep["ok"] = False
        rep["reason"] = f"channels_want_2_got_{channels}"
        return False, rep
    rep["ok"] = True
    rep["reason"] = "ok"
    return True, rep


def _emit_long_master_status(
    *,
    ready: bool,
    master_path: str = "",
    duration_sec: float = 0.0,
    block_reason: str = "",
) -> None:
    print(f"LONG_MASTER_READY={'true' if ready else 'false'}")
    print(f"MASTER_PATH={master_path}")
    print(f"DURATION_SEC={duration_sec:.3f}" if duration_sec else "DURATION_SEC=0")
    print(f"BLOCK_REASON={block_reason}")


def _encode_concat_demuxer(sources: list[Path], out_mp4: Path, *, fps: int, timeout_sec: float) -> tuple[bool, str]:
    """Single ffmpeg: concat demuxer → one H.264/AAC encode (CFR + center-crop to 16:9)."""
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    lst = out_mp4.parent / f"_concat_list_{uuid.uuid4().hex[:10]}.txt"
    lines = [f"file '{_escape_concat_path(Path(s))}'" for s in sources]
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    vf = f"fps={fps},scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,format=yuv420p"
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-fflags",
        "+genpts",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lst),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(out_mp4),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        tail = (r.stderr or "")[-8000:]
        try:
            lst.unlink(missing_ok=True)
        except OSError:
            pass
        if r.returncode == 0 and out_mp4.is_file() and out_mp4.stat().st_size > 4096:
            return True, tail
        return False, tail
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, repr(exc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Plan only; write manifest, no ffmpeg output.")
    ap.add_argument("--fps", type=int, choices=(30, 60), default=30)
    ap.add_argument(
        "--schedule-kind",
        choices=("auto", "1h", "3h"),
        default="auto",
        help="Target master length: 1h (3600s) or 3h (10800s); auto uses today's plan (first job).",
    )
    ap.add_argument(
        "--force-schedule",
        action="store_true",
        help="Bypass daily schedule gate (max 1x 1h per calendar day).",
    )
    ap.add_argument("--min-total-sec", type=float, default=None)
    ap.add_argument(
        "--encode-timeout-sec",
        type=float,
        default=float(os.environ.get("NYC_LONG_MASTER_ENCODE_TIMEOUT_SEC", str(8 * 3600))),
        help="Wall-clock cap for the single concat+encode ffmpeg (default 8h).",
    )
    ap.add_argument(
        "--prefer-date",
        default=None,
        help="Prefer ferry sources from this ISO date (also STATEVERGE_PREFER_SOURCE_DATE). Default 2026-05-15.",
    )
    ap.add_argument(
        "--source-type",
        choices=("auto", "driving", "ferry"),
        default="auto",
        help="Mono source pool for master pick (ferry = video169/ferry only).",
    )
    args = ap.parse_args()

    schedule_kind = str(args.schedule_kind or "auto")
    if schedule_kind == "auto":
        plan = get_today_plan()
        jobs = plan.get("jobs") or []
        if jobs and isinstance(jobs[0], dict):
            schedule_kind = str(jobs[0].get("kind") or "1h")
        else:
            schedule_kind = "1h"

    gate = evaluate_publish_schedule_gate(kind=schedule_kind, force=bool(args.force_schedule))
    if not gate.get("allowed") and not args.dry_run:
        br = str(gate.get("reason") or "schedule_gate_blocked")
        print(
            json.dumps(
                {"ok": False, "block_reason": br, "schedule_gate": gate},
                indent=2,
                ensure_ascii=False,
            )
        )
        _emit_long_master_status(ready=False, block_reason=br)
        return 5

    if args.min_total_sec is not None:
        min_total = float(args.min_total_sec)
    else:
        job_dur = None
        for j in (gate.get("today_plan") or {}).get("jobs") or []:
            if isinstance(j, dict) and str(j.get("kind")) == schedule_kind:
                job_dur = j.get("duration_sec")
                break
        min_total = float(job_dur or (10800.0 if schedule_kind == "3h" else lms.MIN_MASTER_SEC))
    min_validate = min_master_duration_for_kind(schedule_kind)

    warnings: list[str] = []
    prefer_dates = resolve_prefer_dates(args.prefer_date)
    mono_st: str | None = None
    src_mode = str(args.source_type or "auto")
    if src_mode == "ferry":
        mono_st = "ferry"
    elif src_mode == "driving":
        mono_st = "driving"
    may15_ferry_only = schedule_kind == "3h" and mono_st == "ferry"

    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)
    ready = get_transfer_ready_to_upload(verbose=False)

    if not args.dry_run:
        lock_ok, lock_reason = _acquire_create_lock(xfer, warnings)
        if not lock_ok:
            rep = {
                "ok": False,
                "status": "blocked",
                "block_reason": lock_reason,
                "warnings": warnings,
            }
            print(json.dumps(rep, indent=2, ensure_ascii=False))
            _emit_long_master_status(ready=False, block_reason=lock_reason)
            return 6

    inbox = lms._scan_inbox_videos(xfer, cache, warnings)  # noqa: SLF001
    ferry_total, ferry_pref = count_preferred_ferry_files(inbox, prefer_dates)

    picked, planned_total, pick_note = lms._pick_master_sources(  # noqa: SLF001
        inbox,
        warnings,
        min_total_sec=float(min_total),
        prefer_dates=prefer_dates,
        mono_source_type=mono_st,
        may15_ferry_only=may15_ferry_only,
    )

    ts = _utc_compact()
    out_suffix = ""
    if schedule_kind == "3h" and mono_st == "ferry":
        out_suffix = "_ferry_3h_may15"
    out_mp4 = ready / "nyc_long_clips" / f"nyc_long_master{out_suffix}_{ts}.mp4"
    out_tmp = out_mp4.with_name(f"{out_mp4.stem}.tmp.mp4")
    required_duration_sec = max(float(_LONG_MASTER_MIN_DURATION_SEC), float(min_total))
    manifest = xfer / "publish_pack" / "nyc_long_uploads" / f"master_generation_{ts}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    ready.mkdir(parents=True, exist_ok=True)
    (ready / "nyc_long_clips").mkdir(parents=True, exist_ok=True)

    base_manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": bool(args.dry_run),
        "fps": int(args.fps),
        "min_total_sec": float(min_total),
        "schedule_kind": schedule_kind,
        "schedule_gate": gate,
        **schedule_metadata_for_job(),
        "pick_note": pick_note,
        "planned_total_sec": round(planned_total, 3),
        "sources": picked,
        "output_path": str(out_mp4),
        "output_tmp_path": str(out_tmp),
        "required_duration_sec": float(required_duration_sec),
        "encode_mode": "concat_demuxer_single_pass_h264_aac",
        "warnings": warnings,
        "source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "master_source_policy": "video169_only",
        "all_sources_video169": True,
        "mixed_non_video169_sources_count": 0,
        "prefer_source_dates": [d.isoformat() for d in prefer_dates],
        "ferry_video169_total": ferry_total,
        "ferry_prefer_date_match_count": ferry_pref,
        "source_type_mode": src_mode,
        "may15_ferry_only_attempted": may15_ferry_only,
    }
    append_prefer_manifest_fields(
        base_manifest,
        prefer_dates=prefer_dates,
        may15_only_attempted=may15_ferry_only,
        may15_only_satisfied=may15_ferry_only and pick_note == "ok" and not any(
            w == "insufficient_may15_ferry_footage" for w in warnings
        ),
        pick_note=pick_note,
        warnings=warnings,
    )

    try:
        from content_routing_v1 import build_content_routing_bundle, infer_content_theme  # noqa: WPS433

        src_paths = [str(x.get("path") or "") for x in picked if isinstance(x, dict)]
        themes = [infer_content_theme({"path": p}) for p in src_paths if p]
        dominant = "unknown"
        for pref in ("ferry", "driving", "skyline_sequence"):
            if pref in themes:
                dominant = pref
                break
        cr_bundle = build_content_routing_bundle(
            {"source_paths": src_paths, "job_id": ts},
            job_id=ts,
        )
        base_manifest["content_routing"] = {
            "dominant_theme": dominant,
            "per_source_themes": dict(zip(src_paths[:40], themes[:40])),
            "audio_mode": cr_bundle.get("audio_mode"),
            "music_required": cr_bundle.get("music_required"),
            "music_disabled_by_default": cr_bundle.get("music_disabled_by_default"),
            "metadata_policy_version": cr_bundle.get("metadata_policy_version"),
            "planned_title": cr_bundle.get("title"),
        }
        try:
            from music_selector import long_music_selection_fields  # noqa: WPS433

            mf = long_music_selection_fields(theme=dominant, xfer=xfer)
            base_manifest.update(mf)
        except Exception as mexc:  # noqa: BLE001
            warnings.append(f"long_music_fields_failed:{mexc!r}")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"content_routing_manifest_failed:{exc!r}")

    if pick_note != "ok" or not picked:
        base_manifest["status"] = "blocked"
        base_manifest["block_reason"] = "insufficient_usable_inbox_for_master"
        base_manifest["all_sources_video169"] = True
        base_manifest["mixed_non_video169_sources_count"] = 0
        manifest.write_text(json.dumps(base_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": False, "manifest": str(manifest), **base_manifest}, indent=2, ensure_ascii=False))
        _emit_ferry_may15_status(ferry_pref=ferry_pref, pick_note=pick_note, mono_st=mono_st)
        _emit_long_master_status(ready=False, block_reason=str(base_manifest.get("block_reason") or "blocked"))
        return 2

    if float(planned_total) < float(required_duration_sec):
        base_manifest["status"] = "blocked"
        base_manifest["block_reason"] = "insufficient_long_source_duration"
        base_manifest["selected_total_duration_sec"] = round(float(planned_total), 3)
        base_manifest["required_duration_sec"] = float(required_duration_sec)
        manifest.write_text(json.dumps(base_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": False, "manifest": str(manifest), **base_manifest}, indent=2, ensure_ascii=False))
        _emit_ferry_may15_status(ferry_pref=ferry_pref, pick_note=pick_note, mono_st=mono_st)
        _emit_long_master_status(ready=False, block_reason="insufficient_long_source_duration")
        return 2

    viol = _video169_source_violations(picked)
    base_manifest["all_sources_video169"] = len(viol) == 0
    base_manifest["mixed_non_video169_sources_count"] = int(len(viol))
    if viol:
        base_manifest["status"] = "blocked"
        base_manifest["block_reason"] = "non_video169_source_in_master_plan"
        base_manifest["non_video169_source_paths"] = viol[:80]
        warnings.append("master_aborted_non_video169_sources")
        manifest.write_text(json.dumps(base_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": False, "manifest": str(manifest), **base_manifest}, indent=2, ensure_ascii=False))
        _emit_ferry_may15_status(ferry_pref=ferry_pref, pick_note=pick_note, mono_st=mono_st)
        _emit_long_master_status(ready=False, block_reason="non_video169_source_in_master_plan")
        return 7

    if args.dry_run:
        base_manifest["status"] = "dry_run_ok"
        manifest.write_text(json.dumps(base_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": True, "manifest": str(manifest), "would_write": str(out_mp4)}, indent=2, ensure_ascii=False))
        _emit_ferry_may15_status(ferry_pref=ferry_pref, pick_note="dry_run_ok", mono_st=mono_st)
        _emit_long_master_status(ready=False, block_reason="dry_run")
        return 0

    try:
        out_tmp.unlink(missing_ok=True)
    except OSError:
        pass

    sources = [Path(str(x["path"])) for x in picked]
    ok, tail = _encode_concat_demuxer(
        sources,
        out_tmp,
        fps=int(args.fps),
        timeout_sec=float(args.encode_timeout_sec),
    )
    base_manifest["ffmpeg_stderr_tail"] = tail[-12000:]
    if not ok:
        base_manifest["status"] = "failed"
        base_manifest["block_reason"] = "concat_encode_failed"
        if out_tmp.is_file():
            _quarantine_master(out_tmp, ready=ready, reason="concat_encode_failed", warnings=warnings)
        manifest.write_text(json.dumps(base_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": False, "manifest": str(manifest)}, indent=2, ensure_ascii=False))
        _emit_long_master_status(ready=False, block_reason="concat_encode_failed")
        return 3

    val_ok, val_rep = _validate_qualified_long_master(
        out_tmp,
        min_duration_sec=max(float(min_validate), float(_LONG_MASTER_MIN_DURATION_SEC)),
        expected_fps=int(args.fps),
    )
    base_manifest["output_validation"] = val_rep
    if not val_ok:
        base_manifest["status"] = "failed"
        base_manifest["block_reason"] = str(val_rep.get("reason") or "output_validation_failed")
        qdst = _quarantine_master(
            out_tmp,
            ready=ready,
            reason=str(base_manifest["block_reason"]),
            warnings=warnings,
        )
        base_manifest["quarantine_path"] = str(qdst) if qdst else ""
        manifest.write_text(json.dumps(base_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": False, "manifest": str(manifest), "validation": val_rep}, indent=2, ensure_ascii=False))
        _emit_long_master_status(ready=False, block_reason=str(base_manifest["block_reason"]))
        return 4

    try:
        out_tmp.replace(out_mp4)
    except OSError as exc:
        base_manifest["status"] = "failed"
        base_manifest["block_reason"] = f"rename_tmp_to_final_failed:{exc!r}"
        _quarantine_master(out_tmp, ready=ready, reason="rename_failed", warnings=warnings)
        manifest.write_text(json.dumps(base_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        _emit_long_master_status(ready=False, block_reason=str(base_manifest["block_reason"]))
        return 4

    base_manifest["status"] = "completed"
    base_manifest["output_size_bytes"] = out_mp4.stat().st_size
    try:
        base_manifest["schedule_production_record"] = record_production(schedule_kind)
    except OSError as exc:
        warnings.append(f"schedule_state_record_failed:{exc!r}")

    gate_job = f"nyc_lm_{ts}"
    py = sys.executable or "python3"
    gate_script = _SCRIPTS / "long_real_sound_noise_cleanup_gate.py"
    noise_report: dict[str, Any] = {}
    report_path: Path | None = None
    if gate_script.is_file():
        rr = subprocess.run(
            [
                py,
                str(gate_script),
                "--video",
                str(out_mp4),
                "--mode",
                "auto",
                "--job-id",
                gate_job,
                "--force",
            ],
            cwd=str(_SCRIPTS.parent),
            capture_output=True,
            text=True,
            timeout=float(os.environ.get("LONG_RSNC_GATE_TIMEOUT_SEC", str(6 * 3600))),
            check=False,
        )
        base_manifest["noise_cleanup_subprocess_returncode"] = int(rr.returncode or -1)
        if (rr.stderr or "").strip():
            base_manifest["noise_cleanup_subprocess_stderr_tail"] = (rr.stderr or "")[-6000:]
        for line in (rr.stdout or "").splitlines():
            if line.startswith("REPORT_JSON="):
                report_path = Path(line.split("=", 1)[1].strip())
                break
        if report_path and report_path.is_file():
            try:
                noise_report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                warnings.append("noise_gate_report_read_failed")
        else:
            warnings.append("noise_gate_report_path_missing")
    else:
        warnings.append("long_real_sound_noise_cleanup_gate_script_missing")

    base_manifest["raw_master_path"] = str(out_mp4)
    base_manifest["noise_cleanup_report"] = str(report_path) if report_path else ""
    base_manifest["noise_cleanup_mode_used"] = noise_report.get("mode_used", "")
    base_manifest["noise_cleanup_status"] = noise_report.get("status", "")
    base_manifest["noise_cleanup_publish_safe"] = bool(noise_report.get("publish_safe"))
    base_manifest["vocal_separation_used"] = False
    base_manifest["demucs_used"] = False

    final_p = Path(str(noise_report.get("final_video") or ""))
    clean_dst = ready / "nyc_long_clips" / f"nyc_long_master_clean_real_sound_{ts}.mp4"
    base_manifest["clean_master_path"] = ""
    if noise_report.get("publish_safe") and final_p.is_file():
        try:
            shutil.copy2(final_p, clean_dst)
            base_manifest["clean_master_path"] = str(clean_dst)
        except OSError as exc:
            warnings.append(f"clean_master_copy_failed:{exc!r}")
    elif noise_report:
        warnings.append("noise_gate_did_not_produce_publish_safe_output")

    base_manifest["warnings"] = warnings
    manifest.write_text(json.dumps(base_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(out_mp4), "manifest": str(manifest)}, indent=2, ensure_ascii=False))
    _emit_ferry_may15_status(ferry_pref=ferry_pref, pick_note=pick_note, mono_st=mono_st)
    fin_dur = float(val_rep.get("duration_sec") or 0.0)
    _emit_long_master_status(ready=True, master_path=str(out_mp4), duration_sec=fin_dur, block_reason="")
    _release_create_lock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
