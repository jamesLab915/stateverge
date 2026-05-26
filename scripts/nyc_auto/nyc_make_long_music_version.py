#!/usr/bin/env python3
"""Create long-form music mix (optional bed) from source. Does not alter source file."""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import (  # noqa: E402
    NYC_ROOT,
    OUT_LONG,
    ensure_nyc_dirs,
    ensure_project_layout,
    ffprobe_verify_streams,
    load_source_json,
    log_lines,
    nyc_daily_log,
    project_dir,
)

MIN_OUT = 1.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--music", default=None)
    ap.add_argument("--cut-start", type=float, default=0.0)
    ap.add_argument("--target-minutes", type=float, default=None)
    args = ap.parse_args()

    pid = args.project_id
    log_nyc = nyc_daily_log("nyc_long_music")
    proj_log = project_dir(pid) / "logs" / f"nyc_long_music_{datetime.now().strftime('%Y-%m-%d')}.log"

    def _l(msg: str) -> None:
        log_lines("nyc_long_music", [f"[{pid}] {msg}"], log_nyc)
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            proj_log.parent.mkdir(parents=True, exist_ok=True)
            with proj_log.open("a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except OSError:
            pass

    if not NYC_ROOT.is_dir():
        _l("ERROR NYC_ROOT not mounted")
        return 2

    ensure_nyc_dirs()
    meta = load_source_json(pid)
    if not meta:
        _l("ERROR missing source.json")
        return 1
    src = Path(meta["source_path"])
    if not src.is_file():
        _l(f"ERROR missing source {src}")
        return 1

    duration = float(meta.get("duration") or 0)
    cut = max(0.0, float(args.cut_start))
    remaining = max(0.0, duration - cut)
    if remaining < MIN_OUT:
        _l("ERROR nothing left after cut-start")
        return 1

    if args.target_minutes is not None:
        out_len = min(float(args.target_minutes) * 60.0, remaining)
    else:
        out_len = remaining
    out_len = max(MIN_OUT, out_len)

    fade_out_st = max(0.0, out_len - 5.0)
    fade_in_d = min(3.0, out_len)
    fade_out_d = min(5.0, out_len - max(0.0, fade_out_st - 0.01))

    ensure_project_layout(pid)
    out_proj = project_dir(pid) / "output" / "long_music.mp4"
    out_tmp = out_proj.with_name(out_proj.stem + ".partial" + out_proj.suffix)
    if out_tmp.exists():
        try:
            out_tmp.unlink()
        except OSError:
            pass

    music_path = Path(args.music).expanduser() if args.music else None
    has_audio = bool(meta.get("has_audio"))

    try:
        if music_path and music_path.is_file():
            ol = out_len
            fos = max(0.0, ol - 5.0)
            if has_audio:
                fc = (
                    f"[0:a]volume=0.2,atrim=0:{ol},asetpts=PTS-STARTPTS[a0];"
                    f"[1:a]atrim=0:{ol},asetpts=PTS-STARTPTS,volume=0.8,"
                    f"afade=t=in:st=0:d={fade_in_d},afade=t=out:st={fos}:d={fade_out_d}[m];"
                    f"[a0][m]amix=inputs=2:duration=first:normalize=0[outa]"
                )
            else:
                fc = (
                    f"[1:a]atrim=0:{ol},asetpts=PTS-STARTPTS,volume=1.0,"
                    f"afade=t=in:st=0:d={fade_in_d},afade=t=out:st={fos}:d={fade_out_d}[outa]"
                )
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss",
                f"{cut:.3f}",
                "-t",
                f"{ol:.3f}",
                "-i",
                str(src),
                "-stream_loop",
                "-1",
                "-i",
                str(music_path),
                "-filter_complex",
                fc,
                "-map",
                "0:v",
                "-map",
                "[outa]",
                "-c:v",
                "libx264",
                "-crf",
                "20",
                "-preset",
                "fast",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(out_tmp),
            ]
        else:
            if has_audio:
                cmd = [
                    "ffmpeg",
                    "-hide_banner",
                    "-y",
                    "-ss",
                    f"{cut:.3f}",
                    "-t",
                    f"{out_len:.3f}",
                    "-i",
                    str(src),
                    "-c:v",
                    "libx264",
                    "-crf",
                    "20",
                    "-preset",
                    "fast",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    str(out_tmp),
                ]
            else:
                cmd = [
                    "ffmpeg",
                    "-hide_banner",
                    "-y",
                    "-ss",
                    f"{cut:.3f}",
                    "-t",
                    f"{out_len:.3f}",
                    "-i",
                    str(src),
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=stereo",
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-c:v",
                    "libx264",
                    "-crf",
                    "20",
                    "-preset",
                    "fast",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(out_tmp),
                ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=7200)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        _l(f"FFMPEG_FAIL {exc}")
        try:
            if out_tmp.exists():
                out_tmp.unlink()
        except OSError:
            pass
        return 1

    ok, reason = ffprobe_verify_streams(out_tmp, want_audio=True)
    if not ok:
        _l(f"VERIFY_FAIL {reason}")
        try:
            out_tmp.unlink()
        except OSError:
            pass
        return 1
    try:
        out_tmp.replace(out_proj)
    except OSError as exc:
        _l(f"FAIL rename {exc}")
        return 1

    out_global = OUT_LONG / f"{pid}_long_music.mp4"
    try:
        subprocess.run(["cp", "-f", str(out_proj), str(out_global)], check=False, timeout=3600)
    except (OSError, subprocess.TimeoutExpired):
        pass

    _l(f"OK {out_proj}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
