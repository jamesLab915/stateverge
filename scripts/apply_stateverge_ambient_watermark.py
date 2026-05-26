#!/usr/bin/env python3
"""Apply StateVerge Ambient Cinema channel watermark (v1) to a finished video.

Spec: bottom-right default (SV v1), 320x80 @ 1080p, 40px safe margin.
Does not modify the source file unless --in-place is set.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BRAND_DIR = _REPO / "assets" / "branding"
_DEFAULT_PNG = _BRAND_DIR / "stateverge_watermark_sv" / "transparent_png" / "sv_watermark_transparent.png"
_LEGACY_PNG = _BRAND_DIR / "stateverge_ambient_cinema_watermark_320x80.png"
_DESIGN_SOURCE = Path(
    "/Users/ziweizhang/Library/Application Support/Cursor/User/workspaceStorage/"
    "empty-window/images/image-ce1a3cb0-4f72-462b-90cf-f91ddec0bb70.png"
)

# 1080p reference
REF_W = 320
REF_H = 80
MARGIN_X = 40
MARGIN_Y = 40
DEFAULT_OPACITY = 0.82


def _run(cmd: list[str]) -> None:
    print("RUN:", " ".join(shlex.quote(str(x)) for x in cmd))
    subprocess.check_call(cmd)


def _ffprobe_wh(path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    w, h = out.split(",")
    return int(w), int(h)


def _ensure_watermark_png(dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file():
        return dst
    if _LEGACY_PNG.is_file():
        return _LEGACY_PNG
    if _DESIGN_SOURCE.is_file():
        # Crop hero logo from style-guide sheet (top banner).
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(_DESIGN_SOURCE),
                "-vf",
                "crop=920:200:180:70,scale=320:80:flags=lanczos,format=rgba",
                "-frames:v",
                "1",
                "-update",
                "1",
                str(dst),
            ]
        )
        return dst
    # Drawtext fallback when design PNG unavailable.
    filt = (
        "color=c=black@0.0:s=320x80,format=rgba,"
        "drawbox=x=0:y=0:w=58:h=58:color=0x0A0A0A@0.92:t=fill,"
        "drawbox=x=0:y=0:w=58:h=58:color=0xE50914@0.15:t=2,"
        "drawtext=text='S':x=14:y=8:fontsize=34:fontcolor=0xE50914:borderw=0,"
        "drawtext=text='STATEVERGE':x=68:y=14:fontsize=22:fontcolor=white@0.95,"
        "drawtext=text='AMBIENT CINEMA':x=68:y=44:fontsize=13:fontcolor=0xB3B3B3@0.9"
    )
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", filt, "-frames:v", "1", "-update", "1", str(dst)])
    return dst


def _scaled_overlay_filter(*, video_w: int, video_h: int, opacity: float, position: str) -> str:
    scale = max(0.35, min(1.5, float(video_h) / 1080.0))
    wm_w = max(160, int(round(REF_W * scale)))
    wm_h = max(40, int(round(REF_H * scale)))
    mx = max(20, int(round(MARGIN_X * scale)))
    my = max(20, int(round(MARGIN_Y * scale)))
    pos = (position or "top_left").strip().lower()
    if pos == "bottom_right":
        overlay_xy = f"x=main_w-overlay_w-{mx}:y=main_h-overlay_h-{my}"
    elif pos == "bottom_left":
        overlay_xy = f"x={mx}:y=main_h-overlay_h-{my}"
    else:
        overlay_xy = f"x={mx}:y={my}"
    op = max(0.1, min(1.0, float(opacity)))
    return (
        f"[1:v]scale={wm_w}:{wm_h}:flags=lanczos,format=rgba,"
        f"colorchannelmixer=aa={op}[wm];"
        f"[0:v][wm]overlay={overlay_xy}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="Source video (not overwritten unless --in-place)")
    ap.add_argument("--output", default="", help="Output path (default: <stem>_stateverge_branded.mp4 beside input)")
    ap.add_argument("--watermark-png", type=Path, default=_DEFAULT_PNG)
    ap.add_argument("--position", choices=("top_left", "bottom_right", "bottom_left"), default="bottom_right")
    ap.add_argument("--opacity", type=float, default=DEFAULT_OPACITY)
    ap.add_argument("--in-place", action="store_true", help="Overwrite input (writes via temp file first)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    inp = Path(args.input).expanduser().resolve()
    if not inp.is_file():
        print(f"ERROR: input not found: {inp}", file=sys.stderr)
        return 1

    if args.in_place:
        out = inp.parent / f".{inp.stem}_wm_tmp{inp.suffix}"
    elif args.output:
        out = Path(args.output).expanduser().resolve()
    else:
        out = inp.with_name(f"{inp.stem}_stateverge_branded{inp.suffix}")

    out.parent.mkdir(parents=True, exist_ok=True)
    wm_png = _ensure_watermark_png(args.watermark_png.expanduser().resolve())
    vw, vh = _ffprobe_wh(inp)
    vf = _scaled_overlay_filter(video_w=vw, video_h=vh, opacity=args.opacity, position=args.position)

    meta = {
        "watermark_version": "stateverge_ambient_cinema_v1",
        "input": str(inp),
        "output": str(out),
        "watermark_png": str(wm_png),
        "video_width": vw,
        "video_height": vh,
        "position": args.position,
        "opacity": args.opacity,
        "filter": vf,
    }
    print(json.dumps(meta, indent=2, ensure_ascii=False))

    if args.dry_run:
        print("WATERMARK_DRY_RUN=true")
        print(f"WATERMARK_OUTPUT={out}")
        return 0

    # Prefer Apple VideoToolbox on long 4K jobs (much faster than libx264 software).
    use_vt = sys.platform == "darwin"
    cmd: list[str] = ["ffmpeg", "-y"]
    if use_vt:
        cmd += ["-hwaccel", "videotoolbox"]
    cmd += ["-i", str(inp), "-i", str(wm_png), "-filter_complex", vf, "-map", "0:a?"]
    if use_vt:
        cmd += ["-c:v", "h264_videotoolbox", "-b:v", "28M", "-maxrate", "32M", "-bufsize", "64M", "-profile:v", "high"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "copy", "-movflags", "+faststart", str(out)]
    _run(cmd)

    if args.in_place:
        tmp = out
        final = inp
        backup = inp.with_suffix(inp.suffix + ".pre_watermark.bak")
        if not backup.is_file():
            inp.rename(backup)
        tmp.rename(final)
        out = final
        print(f"WATERMARK_BACKUP={backup}")

    print("WATERMARK_APPLIED=true")
    print(f"WATERMARK_INPUT={inp}")
    print(f"WATERMARK_OUTPUT={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
