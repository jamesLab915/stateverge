#!/usr/bin/env python3
"""NYC long music-first mix: suno/souno priority, very low ambient, CFR30 re-encode (no -c:v copy).

Default music roots (via schedule): ``/Volumes/SV_CACHE/inbox/suno`` first (recursive, all subdirs),
then souno, then nyc_long library.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_NYC = Path(__file__).resolve().parent
_SCRIPTS = _NYC.parent
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import auto_publish_queue as apq  # noqa: E402
from audio.audio_fade_helpers import music_fade_filter  # noqa: E402
from nyc_common import parse_fraction  # noqa: E402
from nyc_long_schedule_v1 import (  # noqa: E402
    load_schedule_config,
    pick_long_music_file,
    primary_music_root,
)

_FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
_FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"
_LONG_MASTER_MIN_SEC = 3600.0
_MTIME_STABLE_SEC = 60.0


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _mtime_stable_sec(path: Path, *, min_stable: float = _MTIME_STABLE_SEC) -> tuple[bool, float]:
    try:
        st = path.stat()
        age = max(0.0, time.time() - float(st.st_mtime))
        return age >= float(min_stable), age
    except OSError:
        return False, -1.0


def _master_ready_for_ambient_mix(path: Path) -> tuple[bool, str, dict[str, Any]]:
    """ffprobe OK, duration>=3600, not .tmp.mp4, mtime stable >=60s."""
    detail: dict[str, Any] = {"path": str(path)}
    name = path.name.lower()
    if ".tmp." in name or name.endswith(".tmp.mp4"):
        return False, "master_not_ready_or_invalid", {**detail, "reason": "tmp_output_in_progress"}
    stable, age = _mtime_stable_sec(path)
    detail["mtime_age_sec"] = round(age, 3) if age >= 0 else None
    if not stable:
        return False, "master_not_ready_or_invalid", {**detail, "reason": "mtime_not_stable"}
    probe = apq._ffprobe_json(path)  # noqa: SLF001
    if not probe:
        return False, "master_not_ready_or_invalid", {**detail, "reason": "ffprobe_failed"}
    dur, has_v = apq._duration_and_has_video(probe)  # noqa: SLF001
    detail["duration_sec"] = float(dur or 0.0)
    if not has_v or dur is None:
        return False, "master_not_ready_or_invalid", {**detail, "reason": "no_video_stream"}
    if float(dur) < _LONG_MASTER_MIN_SEC:
        return False, "master_not_ready_or_invalid", {**detail, "reason": "duration_below_3600"}
    w, h = apq._primary_video_dims(probe)  # noqa: SLF001
    detail["width"] = w
    detail["height"] = h
    vst = next(
        (s for s in (probe.get("streams") or []) if isinstance(s, dict) and s.get("codec_type") == "video"),
        None,
    )
    if vst:
        fps = parse_fraction(str(vst.get("avg_frame_rate") or "")) or 0.0
        detail["avg_frame_rate"] = str(vst.get("avg_frame_rate") or "")
        if fps and abs(fps - 30.0) > 0.6:
            return False, "master_not_ready_or_invalid", {**detail, "reason": "frame_rate_not_30"}
    return True, "", detail


def _probe_duration(path: Path) -> float:
    cmd = [
        _FFPROBE,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return 600.0
        return max(1.0, float((r.stdout or "600").strip().splitlines()[0]))
    except (ValueError, IndexError, subprocess.TimeoutExpired):
        return 600.0


def _mux(
    video_in: Path,
    music: Path,
    out_mp4: Path,
    *,
    ambient_db: float,
    music_db: float,
) -> tuple[bool, str]:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_mp4.parent / f"{out_mp4.stem}._tmp_{os.getpid()}.mp4"
    dur = _probe_duration(video_in)
    dur_s = f"{dur:.3f}"
    mus_fade = music_fade_filter(dur, shorts=False)
    fc = (
        f"[0:v]fps=30,format=yuv420p[vout];"
        f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo,volume={ambient_db}dB[bed];"
        f"[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"atrim=duration={dur_s},asetpts=PTS-STARTPTS,{mus_fade},volume={music_db}dB[mus];"
        f"[bed][mus]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[mx];"
        f"[mx]aformat=sample_fmts=fltp:channel_layouts=stereo,loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
    )
    enc_attempts = [
        ["-c:v", "h264_videotoolbox", "-b:v", "35M", "-tag:v", "avc1"],
        ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-tag:v", "avc1"],
    ]
    last_tail = ""
    for enc in enc_attempts:
        cmd = [
            _FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(video_in),
            "-stream_loop",
            "-1",
            "-i",
            str(music),
            "-filter_complex",
            fc,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *enc,
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
            str(tmp),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=14400, check=False)
            last_tail = (r.stderr or "")[-8000:]
            if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 4096:
                tmp.replace(out_mp4)
                return True, last_tail
        except (subprocess.TimeoutExpired, OSError) as exc:
            last_tail = repr(exc)
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return False, last_tail


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-video", required=True)
    ap.add_argument("--output-video", default=None)
    ap.add_argument("--music-file", default=None, help="Override picked music path.")
    ap.add_argument(
        "--music-library-root",
        default=None,
        help="Pick newest audio recursively (e.g. /Volumes/SV_CACHE/inbox/suno; includes subdirs).",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_schedule_config()
    ambient_db = float(cfg.get("ambient_volume_db") or -24)
    rel = float(cfg.get("music_volume_relative") or 0.5)
    music_lin = max(0.05, min(1.0, rel))
    music_db = round(20.0 * math.log10(music_lin), 2) if music_lin > 0 else -40.0

    warnings: list[str] = []
    music_path: Path | None
    music_label: str
    if args.music_file:
        music_path = Path(args.music_file).expanduser()
        music_label = "cli_override"
        if not music_path.is_file():
            music_path = None
    elif args.music_library_root:
        from nyc_long_schedule_v1 import _collect_music_candidates, _newest  # noqa: WPS433

        root = Path(args.music_library_root).expanduser()
        music_label = root.name or str(root)
        if root.is_dir():
            pick = _newest(_collect_music_candidates(root, recursive=True))
            music_path = pick
        else:
            music_path = None
            warnings.append(f"music_library_root_missing:{root}")
    else:
        music_path, music_label, warnings = pick_long_music_file(cfg=cfg, warnings=warnings)

    video_in = Path(args.input_video).expanduser().resolve()
    out = (
        Path(args.output_video).expanduser().resolve()
        if args.output_video
        else video_in.parent / f"nyc_long_music_first_{_utc_stamp()}.mp4"
    )

    music_source = "suno" if music_path and "suno" in music_label.lower() else (
        "envato" if music_path and "nyc_long" in str(music_path) else ("none" if not music_path else "library")
    )
    report: dict[str, Any] = {
        "input_video": str(video_in),
        "output_video": str(out),
        "music_root_primary": primary_music_root(cfg),
        "picked_music": str(music_path) if music_path else "",
        "picked_music_label": music_label,
        "selected_music_path": str(music_path) if music_path else "",
        "music_source": music_source,
        "music_priority": str(cfg.get("music_priority") or "suno_first"),
        "fallback_used": any("envato" in w or "fallback" in w for w in warnings),
        "content_theme": str(cfg.get("content_theme") or "driving"),
        "ambient_volume_db": ambient_db,
        "music_volume_db": music_db,
        "audio_mode": str(cfg.get("audio_mode_default") or "music_first_low_ambient"),
        "warnings": warnings,
        "dry_run": bool(args.dry_run),
    }

    if args.dry_run:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"NYC_LONG_AMBIENT_MIX_DRY_RUN=true")
        return 0

    if not video_in.is_file():
        report["ok"] = False
        report["error"] = "input_missing"
        report["block_reason"] = "input_missing"
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print("BLOCK_REASON=input_missing")
        return 2

    mix_ok, block_reason, ready_detail = _master_ready_for_ambient_mix(video_in)
    report["master_readiness"] = ready_detail
    if not mix_ok:
        report["ok"] = False
        report["error"] = block_reason
        report["block_reason"] = block_reason
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"BLOCK_REASON={block_reason}")
        return 5

    if not music_path or not music_path.is_file():
        report["ok"] = False
        report["error"] = "music_missing"
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 3

    ok, tail = _mux(video_in, music_path, out, ambient_db=ambient_db, music_db=music_db)
    report["ok"] = ok
    report["ffmpeg_stderr_tail"] = tail[-4000:]
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
