#!/usr/bin/env python3
"""Orchestrate DaVinci Folder Studio v1: scan → timeline → resolve/manifest → render.

Never deletes or overwrites source clips. No upload, no token access.
Stdout ends with DAVINCI_FOLDER_STUDIO_V1_READY=true on completion (fail-open).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from audio_modes import (  # noqa: E402
    DEFAULT_MUSIC_VOLUME,
    DEFAULT_ORIGINAL_AUDIO_VOLUME,
    LOUDNORM_FILTER,
    build_audio_metadata,
    canonical_mode,
    needs_bgm_mix,
    normalize_mode,
    per_clip_audio_filter,
    resolve_ferry_defaults,
)
from folder_scan import clips_payload_from_paths, scan_folder_dict  # noqa: E402
from music_selector import pick_music  # noqa: E402
from paths import (  # noqa: E402
    MARKER_READY,
    NORMALIZE_CACHE_ROOT,
    OUTPUT_AUDIO_BITRATE,
    OUTPUT_AUDIO_CODEC,
    OUTPUT_AUDIO_RATE,
    OUTPUT_FPS,
    OUTPUT_PIX_FMT,
    OUTPUT_VIDEO_CODEC,
    RENDERS_ROOT,
)
from report_writer import write_report  # noqa: E402
from resolve_bridge import bridge_plan, probe_resolve_api  # noqa: E402
from timeline_builder import build_timeline_plan, write_timeline_plan  # noqa: E402

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def compute_render_status(
    *,
    dry_run: bool,
    ok: bool,
    audio_status: str | None = None,
    has_skipped_clips: bool = False,
) -> str:
    """Top-level render_status for report.json."""
    if dry_run:
        return "dry_run"
    if has_skipped_clips and ok:
        return "ok_with_skipped_clips"
    if audio_status == "partial_bgm_failed":
        return "partial"
    if ok:
        return "completed"
    return "failed"


def _escape_concat_path(p: Path) -> str:
    s = str(p.resolve() if p.exists() else p).replace("\\", "/")
    return s.replace("'", "'\\''")


def probe_duration_sec(path: Path) -> float:
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        if r.returncode == 0 and (r.stdout or "").strip():
            return max(0.1, float((r.stdout or "").strip()))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return 5.0


def _enrich_clip_durations(plan: dict[str, Any]) -> None:
    for clip in plan.get("clips") or []:
        p = Path(str(clip.get("path") or ""))
        if p.is_file():
            clip["duration_sec"] = probe_duration_sec(p)


def normalize_timeout_sec(duration_sec: float, *, encode_timeout_sec: float) -> float:
    """Per-clip ffmpeg normalize timeout — scales with source duration (long ferry clips)."""
    duration_sec = max(0.1, float(duration_sec))
    scaled = max(450.0, duration_sec * 2.0)
    return min(float(encode_timeout_sec), scaled)


def _valid_normalized_segment(path: Path, *, min_bytes: int = 1024) -> bool:
    try:
        return path.is_file() and path.stat().st_size > min_bytes
    except OSError:
        return False


def _source_normalize_cache_key(src: Path, *, audio_filter: str | None) -> str:
    """Stable cache id from source identity + mtime + encode/audio policy."""
    st = src.stat()
    payload = "|".join(
        [
            str(src.resolve()),
            str(st.st_size),
            str(int(st.st_mtime_ns)),
            audio_filter or "",
            str(OUTPUT_FPS),
            OUTPUT_PIX_FMT,
            OUTPUT_VIDEO_CODEC,
            OUTPUT_AUDIO_CODEC,
            OUTPUT_AUDIO_BITRATE,
            OUTPUT_AUDIO_RATE,
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _normalize_cache_path(cache_key: str) -> Path:
    return NORMALIZE_CACHE_ROOT / f"{cache_key}.mp4"


def _materialize_cached_segment(cached: Path, seg: Path) -> bool:
    """Copy cached normalize output into this job's timeline segment slot."""
    seg.parent.mkdir(parents=True, exist_ok=True)
    if seg.resolve() == cached.resolve():
        return _valid_normalized_segment(seg)
    try:
        shutil.copy2(cached, seg)
        return _valid_normalized_segment(seg)
    except OSError:
        return False


def _store_normalize_cache(seg: Path, cached: Path) -> bool:
    """Persist a successful normalize into shared cache (no overwrite if entry exists)."""
    if not _valid_normalized_segment(seg):
        return False
    try:
        NORMALIZE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        if _valid_normalized_segment(cached):
            return True
        shutil.copy2(seg, cached)
        return _valid_normalized_segment(cached)
    except OSError:
        return False


def _normalize_clip(
    src: Path,
    dst: Path,
    *,
    audio_filter: str | None,
    timeout_sec: float,
) -> tuple[bool, str]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = f"fps={OUTPUT_FPS},format={OUTPUT_PIX_FMT}"
    cmd: list[str] = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(src),
        "-vf",
        vf,
        "-c:v",
        OUTPUT_VIDEO_CODEC,
        "-preset",
        "medium",
        "-crf",
        "20",
        "-map",
        "0:v:0",
    ]
    if audio_filter:
        cmd.extend(["-af", audio_filter, "-map", "0:a:0?"])
    else:
        cmd.extend(["-map", "0:a:0?"])
    cmd.extend(
        [
            "-c:a",
            OUTPUT_AUDIO_CODEC,
            "-b:a",
            OUTPUT_AUDIO_BITRATE,
            "-ar",
            OUTPUT_AUDIO_RATE,
            "-ac",
            "2",
            "-shortest",
            str(dst),
        ]
    )
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        tail = (r.stderr or "")[-4000:]
        ok = r.returncode == 0 and _valid_normalized_segment(dst)
        return ok, tail
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, repr(exc)


def _concat_clips(sources: list[Path], dst: Path, *, timeout_sec: float) -> tuple[bool, str]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    lst = dst.parent / f"_concat_{uuid.uuid4().hex[:8]}.txt"
    lst.write_text(
        "\n".join(f"file '{_escape_concat_path(p)}'" for p in sources) + "\n",
        encoding="utf-8",
    )
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lst),
        "-c",
        "copy",
        str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        try:
            lst.unlink(missing_ok=True)
        except OSError:
            pass
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 4096:
            return True, (r.stderr or "")[-2000:]
        cmd2 = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c:v",
            OUTPUT_VIDEO_CODEC,
            "-preset",
            "fast",
            "-crf",
            "20",
            "-c:a",
            OUTPUT_AUDIO_CODEC,
            "-b:a",
            OUTPUT_AUDIO_BITRATE,
            "-movflags",
            "+faststart",
            str(dst),
        ]
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=timeout_sec, check=False)
        ok = r2.returncode == 0 and dst.is_file() and dst.stat().st_size > 4096
        return ok, (r2.stderr or "")[-4000:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, repr(exc)


def _mix_bgm(
    video: Path,
    music: Path,
    dst: Path,
    *,
    mode: str,
    original_audio_volume: float,
    music_volume: float,
    use_loudnorm: bool = False,
    timeout_sec: float,
) -> tuple[bool, str]:
    """Light BGM mix — fades only; optional loudnorm I=-16 (never -14)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    m = canonical_mode(mode)
    dur = probe_duration_sec(video)
    fade_out_start = max(0.0, dur - 3.0)

    if m == "add_envato_music":
        fc = (
            f"[1:a]aloop=loop=-1:size=2e+09,volume={music_volume:.2f},"
            f"afade=t=in:st=0:d=3,afade=t=out:st={fade_out_start:.3f}:d=3[a1]"
        )
        maps = ["-map", "0:v:0", "-map", "[a1]"]
    else:
        fc = (
            f"[0:a]volume={original_audio_volume:.2f}[a0];"
            f"[1:a]aloop=loop=-1:size=2e+09,volume={music_volume:.2f},"
            f"afade=t=in:st=0:d=3,afade=t=out:st={fade_out_start:.3f}:d=3[a1];"
            "[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        maps = ["-map", "0:v:0", "-map", "[aout]"]

    cmd: list[str] = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(video),
        "-i",
        str(music),
        "-filter_complex",
        fc,
        *maps,
        "-c:v",
        "copy",
    ]
    if use_loudnorm:
        cmd.extend(["-af", LOUDNORM_FILTER])
    cmd.extend(
        [
            "-c:a",
            OUTPUT_AUDIO_CODEC,
            "-b:a",
            OUTPUT_AUDIO_BITRATE,
            "-ar",
            OUTPUT_AUDIO_RATE,
            "-shortest",
            "-movflags",
            "+faststart",
            str(dst),
        ]
    )
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        ok = r.returncode == 0 and dst.is_file() and dst.stat().st_size > 4096
        return ok, (r.stderr or "")[-4000:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, repr(exc)


def _try_resolve_render(plan: dict[str, Any], output_path: Path) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    status = probe_resolve_api()
    if not status.get("resolve_connected"):
        warnings.append("resolve_render_skipped_not_connected")
        return False, warnings
    try:
        import DaVinciResolveScript as dvr  # type: ignore[import-not-found]

        resolve = dvr.scriptapp("Resolve")
        pm = resolve.GetProjectManager()
        proj = pm.GetCurrentProject()
        if not proj:
            warnings.append("resolve_render_no_current_project")
            return False, warnings
        project = proj
        timeline = project.GetCurrentTimeline()
        if not timeline:
            warnings.append("resolve_render_no_timeline")
            return False, warnings
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(project, "SetRenderSettings") and hasattr(project, "AddRenderJob"):
            project.SetRenderSettings(
                {
                    "TargetDir": str(output_path.parent),
                    "CustomName": output_path.stem,
                    "Format": "mp4",
                    "VideoCodec": "H264",
                    "AudioCodec": "AAC",
                }
            )
            job_id = project.AddRenderJob()
            if job_id and hasattr(project, "StartRendering"):
                project.StartRendering(job_id)
                warnings.append("resolve_render_started_poll_not_implemented_v1")
                return False, warnings
        warnings.append("resolve_render_api_surface_incomplete_v1")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"resolve_render_exception:{exc!r}")
    return False, warnings


def ffmpeg_render(
    plan: dict[str, Any],
    output_path: Path,
    *,
    dry_run: bool,
    encode_timeout_sec: float,
    add_music: bool = False,
    original_audio_volume: float = DEFAULT_ORIGINAL_AUDIO_VOLUME,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
    use_loudnorm: bool = False,
    fail_on_clip_error: bool = False,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "ffmpeg_fallback": True,
        "warnings": [],
        "segments": [],
        "audio_status": "pending",
        "skipped_clips": [],
        "failed_normalize_count": 0,
        "failed_normalize_paths": [],
        "fail_on_clip_error": bool(fail_on_clip_error),
    }
    mode = str(plan.get("audio_mode") or "preserve_raw")
    add_music = bool(plan.get("add_music", add_music))
    audio_meta = plan.get("audio_policy") or build_audio_metadata(
        mode,
        add_music=add_music,
        clip_paths=[str(c.get("path") or "") for c in plan.get("clips") or []],
        original_audio_volume=original_audio_volume,
        music_volume=music_volume,
        use_loudnorm=use_loudnorm,
    )
    af = per_clip_audio_filter(
        mode,
        original_audio_volume=float(audio_meta.get("original_audio_volume", original_audio_volume)),
    )
    meta["audio_policy"] = audio_meta
    meta["per_clip_audio_filter"] = af
    meta["audio_filter_chain"] = audio_meta.get("audio_filter_chain")

    if dry_run:
        meta["dry_run"] = True
        meta["would_render_to"] = str(output_path)
        meta["segment_count"] = len(plan.get("clips") or [])
        meta["audio_status"] = "dry_run"
        meta["ok"] = True
        return meta

    work = Path(plan["project_dir"]) / "work"
    norm_dir = work / "normalized"
    norm_dir.mkdir(parents=True, exist_ok=True)
    normalized: list[Path] = []
    skipped_clips: list[str] = []
    failed_normalize_paths: list[str] = []
    normalize_failures: list[dict[str, str]] = []
    normalize_cache_hits = 0

    for i, clip in enumerate(plan.get("clips") or []):
        src = Path(str(clip["path"]))
        seg = norm_dir / f"seg_{i:04d}.mp4"
        seg_info: dict[str, Any] = {"src": str(src), "dst": str(seg), "index": i}
        if not src.is_file():
            seg_info["ok"] = False
            meta["segments"].append(seg_info)
            meta["warnings"].append(f"normalize_failed:{src.name}")
            skipped_clips.append(src.name)
            failed_normalize_paths.append(str(src))
            normalize_failures.append({"src": str(src), "tail": "source_missing"})
            if fail_on_clip_error:
                meta["error"] = f"normalize_failed:{src.name}"
                meta["skipped_clips"] = skipped_clips
                meta["failed_normalize_count"] = len(skipped_clips)
                meta["failed_normalize_paths"] = failed_normalize_paths
                meta["audio_status"] = "failed"
                meta["fallback_recommended"] = "preserve_raw"
                meta["ok"] = False
                return meta
            continue

        cache_key = _source_normalize_cache_key(src, audio_filter=af)
        cached = _normalize_cache_path(cache_key)
        seg_info["cache_key"] = cache_key

        if _valid_normalized_segment(cached) and _materialize_cached_segment(cached, seg):
            seg_info["ok"] = True
            seg_info["from_cache"] = True
            meta["segments"].append(seg_info)
            normalize_cache_hits += 1
            normalized.append(seg)
            continue

        dur = float(clip.get("duration_sec") or probe_duration_sec(src))
        per_clip_timeout = normalize_timeout_sec(dur, encode_timeout_sec=encode_timeout_sec)
        seg_info["duration_sec"] = dur
        seg_info["normalize_timeout_sec"] = per_clip_timeout

        ok, tail = _normalize_clip(src, seg, audio_filter=af, timeout_sec=per_clip_timeout)
        seg_info["ok"] = ok
        if ok:
            _store_normalize_cache(seg, cached)
            seg_info["cached"] = _valid_normalized_segment(cached)
        meta["segments"].append(seg_info)
        if not ok:
            meta["warnings"].append(f"normalize_failed:{src.name}")
            skipped_clips.append(src.name)
            failed_normalize_paths.append(str(src))
            normalize_failures.append({"src": str(src), "tail": tail})
            if fail_on_clip_error:
                meta["error"] = f"normalize_failed:{src.name}"
                meta["normalize_tail"] = tail
                meta["skipped_clips"] = skipped_clips
                meta["failed_normalize_count"] = len(skipped_clips)
                meta["failed_normalize_paths"] = failed_normalize_paths
                meta["audio_status"] = "failed"
                meta["fallback_recommended"] = "preserve_raw"
                meta["ok"] = False
                return meta
            continue
        normalized.append(seg)

    meta["normalize_cache_hits"] = normalize_cache_hits
    meta["normalize_cache_root"] = str(NORMALIZE_CACHE_ROOT)

    meta["skipped_clips"] = skipped_clips
    meta["failed_normalize_count"] = len(skipped_clips)
    meta["failed_normalize_paths"] = failed_normalize_paths
    if normalize_failures:
        meta["normalize_failures"] = normalize_failures
        meta["suggested_action"] = "Run ffprobe on failed clip or quarantine this source."

    if len(normalized) < 1:
        meta["warnings"].append("all_clips_normalize_failed")
        meta["error"] = "all_clips_normalize_failed"
        meta["audio_status"] = "failed"
        meta["fallback_recommended"] = "preserve_raw"
        meta["ok"] = False
        return meta

    concat_out = work / "concat.mp4"
    ok_concat, tail_concat = _concat_clips(normalized, concat_out, timeout_sec=encode_timeout_sec)
    if not ok_concat:
        meta["warnings"].append("concat_failed")
        meta["concat_tail"] = tail_concat
        meta["audio_status"] = "failed"
        meta["fallback_recommended"] = "preserve_raw"
        meta["ok"] = False
        return meta

    final_src = concat_out
    if needs_bgm_mix(mode, add_music=add_music):
        track, mw = pick_music(mode, seed=str(plan.get("plan_id") or ""), add_music=add_music)
        meta["warnings"].extend(mw)
        if track:
            mixed = work / "with_bgm.mp4"
            ov = float(audio_meta.get("original_audio_volume", original_audio_volume))
            mv = float(audio_meta.get("music_volume", music_volume))
            ln = bool(audio_meta.get("loudnorm_used", use_loudnorm))
            ok_mix, tail_mix = _mix_bgm(
                concat_out,
                track,
                mixed,
                mode=mode,
                original_audio_volume=ov,
                music_volume=mv,
                use_loudnorm=ln,
                timeout_sec=encode_timeout_sec,
            )
            if ok_mix:
                final_src = mixed
                meta["bgm_track"] = str(track)
            else:
                meta["warnings"].append("bgm_mix_failed_using_concat")
                meta["mix_tail"] = tail_mix
                meta["audio_status"] = "partial_bgm_failed"
        else:
            meta["warnings"].append("bgm_track_missing_using_concat")

    if not final_src.is_file():
        meta["audio_status"] = "failed"
        meta["fallback_recommended"] = "preserve_raw"
        meta["ok"] = False
        return meta

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_file():
        meta["warnings"].append("output_exists_not_overwriting_fail_open")
        meta["audio_status"] = "failed"
        meta["fallback_recommended"] = "preserve_raw"
        meta["ok"] = False
        meta["error"] = "output_exists"
        return meta

    try:
        shutil.copy2(final_src, output_path)
    except OSError as exc:
        meta["warnings"].append(f"copy_failed:{exc!r}")
        meta["audio_status"] = "failed"
        meta["fallback_recommended"] = "preserve_raw"
        meta["ok"] = False
        return meta

    meta["audio_status"] = "ok"
    meta["ok"] = output_path.is_file() and output_path.stat().st_size > 4096
    meta["render_path"] = str(output_path)
    return meta


def run_job(
    *,
    input_folder: Path,
    output_name: str,
    audio_mode: str,
    add_music: bool,
    dry_run: bool = False,
    encode_timeout_sec: float = 7200.0,
    clip_paths: list[str] | None = None,
    original_audio_volume: float = DEFAULT_ORIGINAL_AUDIO_VOLUME,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
    use_loudnorm: bool = False,
    fail_on_clip_error: bool = False,
    render_output_path: Path | None = None,
    folder_mode: str = "",
    youtube_preset: str = "",
) -> dict[str, Any]:
    warnings: list[str] = []
    if clip_paths:
        scan = clips_payload_from_paths(clip_paths, input_folder=input_folder)
        if not scan.get("clips"):
            return {
                "ok": False,
                "error": "no_valid_clip_paths",
                "input_folder": str(input_folder),
                "clip_paths": clip_paths,
                "warnings": warnings,
                "audio_status": "failed",
                "fallback_recommended": "preserve_raw",
            }
    else:
        scan = scan_folder_dict(input_folder)
        if not scan.get("clips"):
            return {
                "ok": False,
                "error": "no_clips_found",
                "input_folder": str(input_folder),
                "warnings": warnings,
                "audio_status": "failed",
                "fallback_recommended": "preserve_raw",
            }

    clip_paths_for_policy = [str(c.get("path") or "") for c in scan.get("clips") or []]
    mode, add_music, ferry_warn = resolve_ferry_defaults(
        audio_mode=audio_mode,
        add_music=add_music,
        clip_paths=clip_paths_for_policy,
    )
    warnings.extend(ferry_warn)
    mode = normalize_mode(mode, add_music=add_music)
    audio_meta = build_audio_metadata(
        mode,
        add_music=add_music,
        clip_paths=clip_paths_for_policy,
        original_audio_volume=original_audio_volume,
        music_volume=music_volume,
        use_loudnorm=use_loudnorm,
        extra_warnings=ferry_warn,
    )

    plan = build_timeline_plan(
        input_folder=input_folder,
        output_name=output_name or input_folder.name,
        audio_mode=mode,
        add_music=add_music,
        clips_payload=scan,
        audio_metadata=audio_meta,
    )
    _enrich_clip_durations(plan)
    write_timeline_plan(plan)
    plan_path = Path(plan["project_dir"]) / "timeline_plan.json"

    bridge = bridge_plan(plan)
    warnings.extend(bridge.get("resolve_result", {}).get("warnings") or [])

    slug = plan["output_name"]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    render_path = (
        render_output_path.expanduser().resolve()
        if render_output_path is not None
        else RENDERS_ROOT / f"{slug}_{ts}.mp4"
    )

    resolve_used = bool(bridge.get("resolve_result", {}).get("resolve_used"))
    render_meta: dict[str, Any] = {"resolve_used": resolve_used}

    if dry_run:
        render_meta = ffmpeg_render(
            plan,
            render_path,
            dry_run=True,
            encode_timeout_sec=encode_timeout_sec,
            add_music=add_music,
            original_audio_volume=original_audio_volume,
            music_volume=music_volume,
            use_loudnorm=use_loudnorm,
            fail_on_clip_error=fail_on_clip_error,
        )
    else:
        rr_ok, rr_warn = _try_resolve_render(plan, render_path)
        warnings.extend(rr_warn)
        if not rr_ok or not render_path.is_file():
            render_meta = ffmpeg_render(
                plan,
                render_path,
                dry_run=False,
                encode_timeout_sec=encode_timeout_sec,
                add_music=add_music,
                original_audio_volume=original_audio_volume,
                music_volume=music_volume,
                use_loudnorm=use_loudnorm,
                fail_on_clip_error=fail_on_clip_error,
            )
            render_meta["resolve_render_attempted"] = True
        else:
            render_meta["ok"] = True
            render_meta["render_path"] = str(render_path)
            render_meta["audio_status"] = "ok"

    skipped_clips = list(render_meta.get("skipped_clips") or [])
    failed_normalize_count = int(render_meta.get("failed_normalize_count") or 0)
    failed_normalize_paths = list(render_meta.get("failed_normalize_paths") or [])
    suggested_action = str(render_meta.get("suggested_action") or "")
    if skipped_clips and not suggested_action:
        suggested_action = "Run ffprobe on failed clip or quarantine this source."
    if skipped_clips:
        warnings.append("Some clips were skipped due to normalize failure.")

    audio_status = str(render_meta.get("audio_status") or ("ok" if render_meta.get("ok") else "failed"))
    job_ok = bool(render_meta.get("ok")) if not dry_run else True
    has_skipped_clips = failed_normalize_count > 0 and job_ok
    render_status = compute_render_status(
        dry_run=dry_run,
        ok=job_ok,
        audio_status=audio_status,
        has_skipped_clips=has_skipped_clips,
    )
    job_report = {
        "ok": job_ok,
        "dry_run": dry_run,
        "render_status": render_status,
        "input_folder": str(input_folder),
        "clip_paths": clip_paths or [],
        "selection_mode": scan.get("selection_mode") or "folder_scan",
        "output_name": slug,
        "audio_mode": mode,
        "add_music": add_music,
        "original_audio_volume": audio_meta.get("original_audio_volume"),
        "music_volume": audio_meta.get("music_volume"),
        "audio_filter_chain": audio_meta.get("audio_filter_chain"),
        "audio_protection_policy": audio_meta.get("audio_protection_policy"),
        "audio_was_heavily_processed": audio_meta.get("audio_was_heavily_processed"),
        "denoise_used": audio_meta.get("denoise_used"),
        "demucs_used": audio_meta.get("demucs_used"),
        "voice_isolation_used": audio_meta.get("voice_isolation_used"),
        "loudnorm_used": audio_meta.get("loudnorm_used"),
        "loudnorm_target": audio_meta.get("loudnorm_target"),
        "audio_status": audio_status,
        "fallback_recommended": render_meta.get("fallback_recommended"),
        "audio_policy": audio_meta,
        "clip_count": plan.get("clip_count", 0),
        "timeline_plan_path": str(plan_path),
        "bridge": bridge,
        "render_path": render_meta.get("render_path") or (str(render_path) if dry_run else ""),
        "resolve_used": resolve_used,
        "ffmpeg_fallback": bool(render_meta.get("ffmpeg_fallback")),
        "warnings": warnings + list(render_meta.get("warnings") or []),
        "error": render_meta.get("error", ""),
        "fail_on_clip_error": bool(fail_on_clip_error),
        "skipped_clips": skipped_clips,
        "failed_normalize_count": failed_normalize_count,
        "failed_normalize_paths": failed_normalize_paths,
        "suggested_action": suggested_action,
        "normalize_cache_hits": int(render_meta.get("normalize_cache_hits") or 0),
        "normalize_cache_root": render_meta.get("normalize_cache_root", ""),
        "render_meta": render_meta,
        "timestamp": _utc_iso(),
        "folder_mode": folder_mode or None,
        "youtube_preset": youtube_preset or None,
    }
    paths = write_report(job_report)
    job_report["report_paths"] = paths
    return job_report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-folder", type=Path, required=True)
    ap.add_argument("--output-name", default="")
    ap.add_argument("--audio-mode", default="preserve_raw")
    ap.add_argument("--add-music", action="store_true")
    ap.add_argument("--original-audio-volume", type=float, default=DEFAULT_ORIGINAL_AUDIO_VOLUME)
    ap.add_argument("--music-volume", type=float, default=DEFAULT_MUSIC_VOLUME)
    ap.add_argument("--use-loudnorm", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--encode-timeout-sec", type=float, default=7200.0)
    ap.add_argument(
        "--clip-path",
        action="append",
        dest="clip_paths",
        default=None,
        help="Explicit clip file path (repeatable; order preserved). Skips folder scan.",
    )
    ap.add_argument(
        "--fail-on-clip-error",
        action="store_true",
        help="Strict mode: abort render on first clip normalize failure (default: skip and continue).",
    )
    args = ap.parse_args()

    result = run_job(
        input_folder=args.input_folder,
        output_name=args.output_name or args.input_folder.name,
        audio_mode=args.audio_mode,
        add_music=bool(args.add_music),
        dry_run=bool(args.dry_run),
        encode_timeout_sec=float(args.encode_timeout_sec),
        clip_paths=args.clip_paths,
        original_audio_volume=float(args.original_audio_volume),
        music_volume=float(args.music_volume),
        use_loudnorm=bool(args.use_loudnorm),
        fail_on_clip_error=bool(args.fail_on_clip_error),
    )
    print(json.dumps(result, indent=2))
    ready = "true" if result.get("ok") else "false"
    print(f"{MARKER_READY.split('=')[0]}={ready}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
