#!/usr/bin/env python3
"""
Loop a licensed music track to a target duration with fades, optional normalization,
and optional ambience mix — for long-form exports (e.g. YouTube).

Uses ffmpeg only. Intended for music you already have rights to use (e.g. Envato license).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


LOG_PREFIX = "[build_long_music]"


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


def check_ffmpeg() -> str | None:
    path = shutil.which("ffmpeg")
    if not path:
        return None
    r = subprocess.run(
        ["ffmpeg", "-version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        return None
    return path


def encoder_args(output: Path) -> list[str]:
    ext = output.suffix.lower()
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", "192k"]
    if ext in {".m4a", ".mp4", ".aac"}:
        # .m4a: AAC in MPEG-4 container
        return ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    log(f"WARN unknown extension {ext!r}; defaulting to AAC in .m4a-style container")
    return ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]


def build_filter_music_only(
    music_vol: float,
    fade_in: float,
    fade_out: float,
    duration: float,
) -> str:
    fade_out_start = max(0.0, duration - fade_out)
    # Single-input afilter chain
    return (
        f"volume={music_vol:.6f},"
        f"afade=t=in:st=0:d={fade_in:.6f},"
        f"afade=t=out:st={fade_out_start:.6f}:d={fade_out:.6f}"
    )


def build_filter_with_ambience(
    music_vol: float,
    amb_vol: float,
    fade_in: float,
    fade_out: float,
    duration: float,
) -> str:
    fade_out_start = max(0.0, duration - fade_out)
    # amix: normalize=0 keeps levels predictable from volume filters
    return (
        f"[0:a]volume={music_vol:.6f}[m];"
        f"[1:a]volume={amb_vol:.6f}[e];"
        f"[m][e]amix=inputs=2:duration=longest:normalize=0[mix];"
        f"[mix]afade=t=in:st=0:d={fade_in:.6f},"
        f"afade=t=out:st={fade_out_start:.6f}:d={fade_out:.6f}[out]"
    )


def run_ffmpeg(cmd: list[str]) -> int:
    log("running: " + " ".join(cmd[:12]) + (" …" if len(cmd) > 12 else ""))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log("ffmpeg failed")
        if proc.stderr:
            log(proc.stderr.strip())
        return proc.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Loop licensed music to a target duration with fades, volume scaling, "
            "and optional ambience bed (ffmpeg)."
        ),
    )
    ap.add_argument("--music", required=True, type=Path, help="Input music file")
    ap.add_argument("--output", required=True, type=Path, help="Output .m4a or .mp3 path")
    ap.add_argument("--duration", type=float, default=7200.0, help="Target length in seconds")
    ap.add_argument("--ambience", type=Path, default=None, help="Optional ambience bed")
    ap.add_argument("--music-volume", type=float, default=0.85, help="Music linear gain")
    ap.add_argument("--ambience-volume", type=float, default=0.12, help="Ambience linear gain")
    ap.add_argument("--fade-in", type=float, default=5.0, help="Fade-in duration (seconds)")
    ap.add_argument("--fade-out", type=float, default=8.0, help="Fade-out duration (seconds)")
    ns = ap.parse_args(argv)

    music = ns.music.expanduser().resolve()
    out = ns.output.expanduser().resolve()
    ambience = ns.ambience.expanduser().resolve() if ns.ambience else None

    if not music.is_file():
        log(f"ERROR: music file not found: {music}")
        return 2

    if ambience is not None and not ambience.is_file():
        log(f"ERROR: ambience file not found: {ambience}")
        return 2

    duration = float(ns.duration)
    fade_in = float(ns.fade_in)
    fade_out = float(ns.fade_out)
    if duration <= 0:
        log("ERROR: --duration must be positive")
        return 2
    if fade_in < 0 or fade_out < 0:
        log("ERROR: fade durations must be non-negative")
        return 2
    if duration < fade_in + fade_out:
        log(
            f"ERROR: --duration ({duration}s) must be >= fade-in + fade-out "
            f"({fade_in + fade_out}s)"
        )
        return 2

    ff = check_ffmpeg()
    if not ff:
        log("ERROR: ffmpeg not found on PATH")
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    enc = encoder_args(out)

    cmd: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
    ]

    if ambience is None:
        filt = build_filter_music_only(
            ns.music_volume,
            fade_in,
            fade_out,
            duration,
        )
        cmd += [
            "-stream_loop",
            "-1",
            "-i",
            str(music),
            "-af",
            filt,
            "-t",
            f"{duration:.6f}",
            *enc,
            str(out),
        ]
    else:
        filt = build_filter_with_ambience(
            ns.music_volume,
            ns.ambience_volume,
            fade_in,
            fade_out,
            duration,
        )
        cmd += [
            "-stream_loop",
            "-1",
            "-i",
            str(music),
            "-stream_loop",
            "-1",
            "-i",
            str(ambience),
            "-filter_complex",
            filt,
            "-map",
            "[out]",
            "-t",
            f"{duration:.6f}",
            *enc,
            str(out),
        ]

    log(f"music={music.name} → {out}")
    log(f"duration={duration}s fade_in={fade_in}s fade_out={fade_out}s")
    log(f"music_volume={ns.music_volume} ambience={'yes' if ambience else 'no'}")
    if ambience:
        log(f"ambience={ambience.name} ambience_volume={ns.ambience_volume}")

    rc = run_ffmpeg(cmd)
    if rc != 0:
        return rc if rc != 0 else 1
    log(f"OK wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
