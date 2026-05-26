#!/usr/bin/env python3
"""DaVinci YouTube Audio Finishing Gate v1 — single-clip pre-upload finish.

Never overwrites the source file. Resolve API when available; ffmpeg fallback with
the same preset semantics documented in presets.PRESET_RULES.
"""

from __future__ import annotations

import argparse
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
_REPO_SCRIPTS = _SCRIPT_DIR.parent
_DAVINCI_STUDIO = _REPO_SCRIPTS / "davinci_studio"
_AUDIO = _REPO_SCRIPTS / "audio"
for p in (_SCRIPT_DIR, _AUDIO, _DAVINCI_STUDIO, _REPO_SCRIPTS):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

MARKER_READY = "DAVINCI_YOUTUBE_AUDIO_FINISHING_GATE_READY=true"
from gate_paths import (  # noqa: E402
    GATE_ROOT,
    INBOX_ROOT,
    MIN_OUTPUT_BYTES,
    RENDERS_ROOT,
    REPORTS_ROOT,
    REPORT_BASENAME,
    FINISHED_FOR_YOUTUBE_ROOT,
)
from presets import (  # noqa: E402
    PRESET_RULES,
    ambient_chain_for_preset,
    preset_rules,
    resolve_preset,
)

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ts_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ensure_dirs() -> list[str]:
    warnings: list[str] = []
    for d in (GATE_ROOT, INBOX_ROOT, RENDERS_ROOT, REPORTS_ROOT, FINISHED_FOR_YOUTUBE_ROOT):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"mkdir_failed:{d}:{exc!r}")
    return warnings


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


def _output_path_for(input_video: Path, preset: str) -> Path:
    stem = input_video.stem
    return FINISHED_FOR_YOUTUBE_ROOT / f"{stem}_yt_audio_{preset}_{_ts_slug()}.mp4"


def _build_ffmpeg_af(preset: str, *, duration_sec: float) -> tuple[list[str], dict[str, Any]]:
    """Return extra ffmpeg audio args + metadata."""
    rules = preset_rules(preset)
    chain_id = ambient_chain_for_preset(preset)
    meta: dict[str, Any] = {
        "preset_rules": rules,
        "ambient_chain_id": chain_id,
        "ffmpeg_filters": [],
    }

    try:
        from audio_modes import (  # noqa: WPS433
            LOUDNORM_FILTER,
            build_audio_metadata,
            canonical_mode,
            per_clip_audio_filter,
        )
        from audio_fade_helpers import music_fade_filter  # noqa: WPS433
        from ambient_chains.ffmpeg_fallback import (  # noqa: WPS433
            build_ffmpeg_af_for_chain,
            merge_with_music_mix,
        )
    except ImportError as exc:
        meta["import_error"] = repr(exc)
        return ["-c:a", "aac", "-b:a", "192k"], meta

    mode = str(rules.get("davinci_studio_audio_mode") or "preserve_raw")
    add_music = bool(rules.get("add_music_default"))
    use_loudnorm = bool(rules.get("use_loudnorm"))
    ov = float(rules.get("original_audio_volume", 0.30))
    mv = float(rules.get("music_volume", 0.85))
    shorts = bool(rules.get("shorts"))

    audio_meta = build_audio_metadata(
        mode,
        add_music=add_music,
        clip_paths=[str(preset)],
        original_audio_volume=ov,
        music_volume=mv,
        use_loudnorm=use_loudnorm,
    )
    meta["audio_policy"] = audio_meta

    per_af = per_clip_audio_filter(canonical_mode(mode), original_audio_volume=ov)
    chain_af, chain_meta = build_ffmpeg_af_for_chain(chain_id, shorts=shorts)
    meta["ambient_chain"] = chain_meta

    if add_music and mode in ("driving_music_first", "add_envato_music"):
        try:
            from davinci_studio.music_selector import pick_music  # noqa: WPS433
        except ImportError:
            from music_selector import pick_music  # noqa: WPS433

        track, mw = pick_music(mode, seed=preset, add_music=True)
        meta["warnings"] = list(mw)
        if not track or not track.is_file():
            meta["bgm_missing"] = True
            if per_af:
                meta["ffmpeg_filters"].append(per_af)
                return ["-af", per_af, "-c:a", "aac", "-b:a", "192k"], meta
            return ["-c:a", "aac", "-b:a", "192k"], meta

        fade = music_fade_filter(duration_sec, shorts=shorts)
        ln = LOUDNORM_FILTER if use_loudnorm else ""
        if mode == "driving_music_first":
            fc = (
                f"[0:a]volume={ov:.2f}[a0];"
                f"[1:a]aloop=loop=-1:size=2e+09,volume={mv:.2f},{fade}[a1];"
                "[a0][a1]amix=inputs=2:duration=first:dropout_transition=2"
            )
            if ln:
                fc += f",{ln}"
            fc += "[aout]"
        else:
            fc = f"[1:a]aloop=loop=-1:size=2e+09,volume={mv:.2f},{fade}"
            if ln:
                fc += f",{ln}"
            fc += "[aout]"
        fc = merge_with_music_mix(chain_id, fc, use_loudnorm=use_loudnorm)

        meta["ffmpeg_filters"].append(fc)
        meta["bgm_track"] = str(track)
        return ["-filter_complex", fc, "-i", str(track)], meta

    if chain_af and chain_id in (
        "ferry_preserve_atmosphere",
        "broadcast_ambient_master",
        "streaming_immersive_ambient",
        "sleep_calm_ambient",
    ):
        meta["ffmpeg_filters"].append(chain_af)
        return ["-af", chain_af, "-c:a", "aac", "-b:a", "192k"], meta

    filters: list[str] = []
    if per_af:
        filters.append(per_af)
    if chain_af:
        filters.append(chain_af)
    elif use_loudnorm and preset != "youtube_ferry_real":
        filters.append(LOUDNORM_FILTER)
    if not filters:
        return ["-c:a", "aac", "-b:a", "192k"], meta
    af = ",".join(filters)
    meta["ffmpeg_filters"].append(af)
    return ["-af", af, "-c:a", "aac", "-b:a", "192k"], meta


def _ffmpeg_render(input_video: Path, output_video: Path, preset: str) -> dict[str, Any]:
    dur = probe_duration_sec(input_video)
    extra, af_meta = _build_ffmpeg_af(preset, duration_sec=dur)
    cmd: list[str] = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_video)]
    if "-filter_complex" in extra:
        bgm_idx = extra.index("-i")
        track = extra[bgm_idx + 1]
        fc_idx = extra.index("-filter_complex")
        fc = extra[fc_idx + 1]
        cmd.extend(["-i", str(track), "-filter_complex", fc, "-map", "0:v:0", "-map", "[aout]"])
        cmd.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(output_video)])
    elif "-af" in extra:
        af_idx = extra.index("-af")
        cmd.extend(["-map", "0:v:0", "-map", "0:a:0", "-af", extra[af_idx + 1]])
        cmd.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(output_video)])
    else:
        cmd.extend(["-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy"])
        cmd.extend(extra)
        cmd.append(str(output_video))

    meta: dict[str, Any] = {"ffmpeg_fallback": True, "ffmpeg_cmd": cmd, **af_meta}
    if output_video.is_file():
        meta["ok"] = False
        meta["error"] = "output_exists_not_overwriting"
        return meta
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(600.0, dur * 3), check=False)
        meta["ffmpeg_returncode"] = r.returncode
        if r.returncode != 0:
            meta["ok"] = False
            meta["stderr_tail"] = (r.stderr or "")[-800:]
            return meta
        if not output_video.is_file() or output_video.stat().st_size < MIN_OUTPUT_BYTES:
            meta["ok"] = False
            meta["error"] = "output_too_small"
            return meta
        meta["ok"] = True
        return meta
    except (OSError, subprocess.TimeoutExpired) as exc:
        meta["ok"] = False
        meta["error"] = repr(exc)
        return meta


def _try_resolve_render(input_video: Path, output_video: Path, preset: str) -> dict[str, Any]:
    meta: dict[str, Any] = {"resolve_attempted": True, "resolve_used": False, "warnings": []}
    try:
        from resolve_bridge import probe_resolve_api  # noqa: WPS433
    except ImportError as exc:
        meta["warnings"].append(f"resolve_bridge_import:{exc!r}")
        return meta

    probe = probe_resolve_api()
    meta["resolve_probe"] = probe
    if not probe.get("resolve_connected"):
        meta["warnings"].append("resolve_not_connected")
        return meta

    try:
        from fairlight_chain_v1 import apply_chain_by_name  # noqa: WPS433

        chain_result = apply_chain_by_name(ambient_chain_for_preset(preset))
        meta["ambient_chain_apply"] = chain_result
        if chain_result.get("ok"):
            meta["resolve_used"] = bool(chain_result.get("resolve_used"))
            meta["warnings"].append("fairlight_chain_spec_exported_ffmpeg_mux_for_render")
    except ImportError as exc:
        meta["warnings"].append(f"fairlight_chain_import:{exc!r}")
    meta["warnings"].append("resolve_connected_v1_uses_ffmpeg_semantics_for_mux")
    return meta


def _write_report(report: dict[str, Any], job_id: str) -> Path:
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    rp = REPORTS_ROOT / f"{job_id}_{REPORT_BASENAME}"
    payload = {"version": "davinci_youtube_audio_finishing_gate_v1", "timestamp": _utc_iso(), **report}
    rp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    latest = REPORTS_ROOT / REPORT_BASENAME
    try:
        latest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return rp


def _empty_report(
    *,
    input_video: Path,
    preset: str,
    block_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "davinci_audio_finished": False,
        "davinci_preset": preset,
        "input_video": str(input_video),
        "output_video": None,
        "render_completed": False,
        "fallback_used": False,
        "block_reason": block_reason,
        "requires_manual_review": False,
    }


def run_gate(
    input_video: Path,
    *,
    preset: str | None = None,
    dry_run: bool = False,
    content_kind: str = "auto",
) -> dict[str, Any]:
    """Run finish gate; returns report dict (required fields always present)."""
    warnings = _ensure_dirs()
    src = Path(input_video).expanduser()
    if not src.is_file():
        rep = _empty_report(input_video=src, preset="auto", block_reason="input_missing")
        rep["warnings"] = warnings
        return rep

    if str(FINISHED_FOR_YOUTUBE_ROOT) in str(src.resolve()):
        rep = _empty_report(input_video=src, preset="auto", block_reason="already_finished_output")
        rep["warnings"] = warnings
        return rep

    resolved = resolve_preset(src, preset, content_kind=content_kind)
    out = _output_path_for(src, resolved)
    job_id = uuid.uuid4().hex[:12]

    report: dict[str, Any] = {
        "davinci_audio_finished": False,
        "davinci_preset": resolved,
        "input_video": str(src.resolve()),
        "output_video": str(out),
        "render_completed": False,
        "fallback_used": False,
        "block_reason": None,
        "requires_manual_review": False,
        "job_id": job_id,
        "dry_run": bool(dry_run),
        "warnings": warnings,
    }

    if dry_run:
        report["dry_run_plan"] = {
            "would_render_to": str(out),
            "preset_rules": PRESET_RULES.get(resolved),
        }
        _write_report(report, job_id)
        return report

    # Stage inbox copy (never touch source)
    staged = INBOX_ROOT / f"{job_id}_{src.name}"
    try:
        if not staged.is_file():
            shutil.copy2(src, staged)
        report["inbox_copy"] = str(staged)
    except OSError as exc:
        report["block_reason"] = "inbox_copy_failed"
        report["warnings"].append(repr(exc))
        _write_report(report, job_id)
        return report

    resolve_meta = _try_resolve_render(src, out, resolved)
    report["resolve"] = resolve_meta

    ff = _ffmpeg_render(src, out, resolved)
    report["ffmpeg"] = ff
    report["fallback_used"] = True

    if ff.get("ok"):
        report["davinci_audio_finished"] = True
        report["render_completed"] = True
        report["output_video"] = str(out.resolve())
        report["render_path"] = str(RENDERS_ROOT / out.name)
        try:
            shutil.copy2(out, RENDERS_ROOT / out.name)
        except OSError as exc:
            report["warnings"].append(f"renders_copy_failed:{exc!r}")
    else:
        report["block_reason"] = str(ff.get("error") or "ffmpeg_render_failed")
        report["requires_manual_review"] = resolved == "youtube_ferry_real"
        report["output_video"] = None

    _write_report(report, job_id)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="DaVinci YouTube Audio Finishing Gate v1")
    ap.add_argument("--input-video", required=True)
    ap.add_argument("--preset", default="auto", choices=list(PRESET_RULES.keys()) + ["auto"])
    ap.add_argument("--content-kind", default="auto", help="long | short | auto")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rep = run_gate(
        Path(args.input_video),
        preset=args.preset,
        dry_run=bool(args.dry_run),
        content_kind=args.content_kind,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    ready = bool(rep.get("davinci_audio_finished")) or bool(args.dry_run)
    print(f"DAVINCI_YOUTUBE_AUDIO_FINISHING_GATE_READY={str(ready).lower()}")
    return 0 if ready or not rep.get("block_reason") else 1


if __name__ == "__main__":
    raise SystemExit(main())
