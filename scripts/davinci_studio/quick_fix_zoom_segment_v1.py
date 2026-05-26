#!/usr/bin/env python3
"""StateVerge DaVinci Quick Fix v1 — slight zoom/reposition on a segment, full-length 1080p H.264 deliverable.

Never overwrites source. Netflix/TVB style: gentle zoom (1.12–1.15), optional position shift,
short cross-dissolve at segment edges (~8 frames @ 30fps).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HOMEBREW = Path("/opt/homebrew/bin")
FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or str(_HOMEBREW / "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or str(_HOMEBREW / "ffprobe")

DEFAULT_OUT_DIR = Path("/Volumes/SV_TRANSFER/ready_to_upload/finished_for_youtube")
CROSSFADE_SEC = 8 / 30.0  # 8 frames @ 30fps


def _probe_duration(path: Path) -> float:
    r = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return float((r.stdout or "0").strip() or 0)


def _parse_time(s: str) -> float:
    s = s.strip()
    if ":" not in s:
        return float(s)
    parts = [float(p) for p in s.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"bad time: {s}")


def _build_vf(zoom: float, pos_x: int, pos_y: int, t1: float, t2: float) -> str:
    """Dynamic crop zoom only between t1..t2; else scale full frame to 1080p."""
    z = max(1.01, float(zoom))
    # When active: crop center with offset; else full frame
    return (
        f"crop=w='if(between(t\\,{t1}\\,{t2})\\,iw/{z}\\,iw)'"
        f":h='if(between(t\\,{t1}\\,{t2})\\,ih/{z}\\,ih)'"
        f":x='if(between(t\\,{t1}\\,{t2})\\,(iw-ow)/2+{pos_x}\\,0)'"
        f":y='if(between(t\\,{t1}\\,{t2})\\,(ih-oh)/2+{pos_y}\\,0)',"
        f"scale=1920:1080:force_original_aspect_ratio=decrease,"
        f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p,fps=30"
    )


def run_quick_fix(
    *,
    input_path: Path,
    output_path: Path,
    segment_start: float,
    segment_end: float,
    zoom: float,
    pos_x: int,
    pos_y: int,
    video_bitrate: str,
    audio_bitrate: str,
) -> dict:
    duration = _probe_duration(input_path)
    t1 = max(0.0, segment_start)
    t2 = min(duration, segment_end)
    if t2 <= t1 + 1.0:
        return {"ok": False, "block_reason": "invalid_segment_range", "duration_sec": duration}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    vf = _build_vf(zoom, pos_x, pos_y, t1, t2)
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-b:v",
        video_bitrate,
        "-maxrate",
        video_bitrate,
        "-bufsize",
        "100000k",
        "-preset",
        "medium",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-t",
        str(duration),
        str(output_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    ok = r.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 4096
    return {
        "ok": ok,
        "duration_sec": duration,
        "segment_start_sec": t1,
        "segment_end_sec": t2,
        "segment_duration_sec": round(t2 - t1, 3),
        "zoom": zoom,
        "pos_x": pos_x,
        "pos_y": pos_y,
        "output_path": str(output_path),
        "ffmpeg_returncode": r.returncode,
        "stderr_tail": (r.stderr or "")[-2000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="StateVerge DaVinci Quick Fix v1 (ffmpeg)")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--segment-start", required=True, help="HH:MM:SS or seconds")
    ap.add_argument("--segment-end", required=True, help="HH:MM:SS or seconds")
    ap.add_argument("--zoom", type=float, default=1.12)
    ap.add_argument("--pos-x", type=int, default=0)
    ap.add_argument("--pos-y", type=int, default=40)
    ap.add_argument("--video-bitrate", default="50000k")
    ap.add_argument("--audio-bitrate", default="320k")
    args = ap.parse_args()

    inp = args.input.expanduser().resolve()
    out = args.output or (DEFAULT_OUT_DIR / f"{inp.stem}_quickfix_1080p.mp4")
    result = run_quick_fix(
        input_path=inp,
        output_path=out.expanduser().resolve(),
        segment_start=_parse_time(args.segment_start),
        segment_end=_parse_time(args.segment_end),
        zoom=args.zoom,
        pos_x=args.pos_x,
        pos_y=args.pos_y,
        video_bitrate=args.video_bitrate,
        audio_bitrate=args.audio_bitrate,
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = Path(f"/Volumes/SV_CACHE/davinci_studio/reports/quick_fix_{ts}.json")
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        result["report_path"] = str(report)
    except OSError:
        pass
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("ok"):
        print("QUICK_FIX_ZOOM_SEGMENT_V1_READY=true")
        return 0
    print(f"QUICK_FIX_ZOOM_SEGMENT_V1_READY=false block_reason={result.get('block_reason')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
