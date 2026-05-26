#!/usr/bin/env python3
"""
Manual one-shot build for the Louvre episode 1 video.

Reads pre-curated source media from
``manual_videos/louvre_episode_01/{footage,artwork,paris,voice,music}/`` and
produces a single mp4 at
``manual_videos/louvre_episode_01/output/louvre_manual_v1.mp4``.

Design notes:
- Does NOT touch ``src/auto_video.py`` or any watcher / mix_engine code.
- Avoids ffmpeg 8.1's known concat-demuxer + ``h264_mp4toannexb`` regression
  by:
    1. normalizing every clip into an MPEG-TS segment (no audio), and
    2. concatenating TS segments either via ``-f concat -c copy`` (works on
       TS even on ffmpeg 8.1) or, as a fallback, the ``concat:`` URL
       protocol.
- Final length is locked to voice duration (``-t voice_duration``).
- Music (if present) is looped + ducked to ~8% and mixed with voice.

Usage:
    python scripts/build_louvre_manual_video.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# paths and constants
# ---------------------------------------------------------------------------

ROOT = Path(
    os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")
).resolve()
EP_DIR = ROOT / "manual_videos" / "louvre_episode_01"
OUT_DIR = EP_DIR / "output"
WORK_DIR = OUT_DIR / "_work_louvre_manual"
OUT_FILE = OUT_DIR / "louvre_manual_v1.mp4"

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a"}

W, H, FPS, AR = 1920, 1080, 30, 48000
MUSIC_VOL = 0.08

# Each per-source play length window. Smaller → more variety + faster encode.
SEG_MIN, SEG_MAX = 6.0, 10.0

# Safety limits.
MAX_CYCLES = 200
MAX_SEGMENTS = 600


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[manual_louvre] {msg}", flush=True)


def fail(msg: str, code: int = 2) -> "Optional[None]":
    log(f"FAIL: {msg}")
    sys.exit(code)


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def probe_duration(p: Path) -> float:
    r = run_capture(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(p),
        ]
    )
    if r.returncode != 0:
        return -1.0
    s = (r.stdout or "").strip()
    if not s:
        return -1.0
    try:
        return float(s)
    except ValueError:
        return -1.0


def has_video_stream(p: Path) -> bool:
    r = run_capture(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "default=nw=1:nk=1",
            str(p),
        ]
    )
    return r.returncode == 0 and (r.stdout or "").strip() == "video"


def has_audio_stream(p: Path) -> bool:
    r = run_capture(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "default=nw=1:nk=1",
            str(p),
        ]
    )
    return r.returncode == 0 and (r.stdout or "").strip() == "audio"


# ---------------------------------------------------------------------------
# voice resolution
# ---------------------------------------------------------------------------

def resolve_voice() -> Path:
    """
    Prefer ``voice/louvre_voice_full.mp3``. Otherwise concat any
    ``voice/louvre_voic*.mp3`` (sorted) into ``louvre_voice_full.mp3``.
    """
    vd = EP_DIR / "voice"
    full = vd / "louvre_voice_full.mp3"
    if full.is_file() and full.stat().st_size > 1024:
        return full

    if not vd.is_dir():
        fail(f"voice/ not found: {vd}", 2)

    parts: list[Path] = sorted(
        [
            p for p in vd.glob("louvre_voic*.mp3")
            if p.is_file() and p.name != "louvre_voice_full.mp3"
        ],
        key=lambda x: x.name.lower(),
    )
    if not parts:
        fail(
            f"no voice file in {vd}; expected louvre_voice_full.mp3 or "
            "louvre_voic01.mp3 + louvre_voic02.mp3 (etc.)",
            2,
        )

    log(f"concatenating voice parts -> louvre_voice_full.mp3 : {[p.name for p in parts]}")
    inputs: list[str] = []
    for p in parts:
        inputs += ["-i", str(p)]
    n = len(parts)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", f"concat=n={n}:v=0:a=1[a]",
        "-map", "[a]",
        "-c:a", "libmp3lame", "-q:a", "2",
        str(full),
    ]
    r = run_capture(cmd)
    if r.returncode != 0 or not full.is_file() or probe_duration(full) <= 0:
        fail(f"voice concat failed: {(r.stderr or '')[-400:]}", 3)
    return full


# ---------------------------------------------------------------------------
# video collection
# ---------------------------------------------------------------------------

def collect_video_sources() -> list[Path]:
    out: list[Path] = []
    for sub in ("footage", "artwork", "paris"):
        d = EP_DIR / sub
        if not d.is_dir():
            log(f"WARN missing dir: {d}")
            continue
        for p in d.iterdir():
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS and not p.name.startswith("."):
                out.append(p)
    out.sort(key=lambda x: x.name.lower())
    return out


# ---------------------------------------------------------------------------
# segment encode (mp4/mov -> normalized .ts)
# ---------------------------------------------------------------------------

def encode_segment(src: Path, want_dur: float, dst_ts: Path) -> bool:
    """
    Encode 0..want_dur seconds of ``src`` to a normalized 1080p30 H.264
    MPEG-TS file with no audio. Returns True on success.
    """
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={FPS},format=yuv420p,setsar=1"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+discardcorrupt+genpts",
        "-i", str(src),
        "-an",
        "-t", f"{want_dur:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-g", str(FPS),  # one keyframe per second; safer for concat
        "-bsf:v", "h264_mp4toannexb",
        "-f", "mpegts",
        str(dst_ts),
    ]
    r = run_capture(cmd)
    if r.returncode != 0:
        log(f"WARN encode failed for {src.name}: {(r.stderr or '')[-300:]}")
        return False
    if not dst_ts.is_file() or dst_ts.stat().st_size < 4096:
        log(f"WARN encode produced empty TS for {src.name}")
        return False
    return True


# ---------------------------------------------------------------------------
# silent video assembly
# ---------------------------------------------------------------------------

def build_silent_video(
    usable: list[tuple[Path, float]], target: float, work: Path
) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    seg_paths: list[Path] = []
    accumulated = 0.0
    seg_index = 0

    for cycle in range(MAX_CYCLES):
        if accumulated >= target - 0.05:
            break
        for src, d_src in usable:
            if accumulated >= target - 0.05:
                break
            remaining = max(0.0, target - accumulated)
            slot = min(SEG_MAX, max(SEG_MIN, min(d_src, SEG_MAX)))
            slot = min(slot, d_src)
            want = min(slot, remaining)
            if want < 0.5:
                # Tail too small for a meaningful encode; pad final via -t in mux step.
                accumulated = target
                break

            ts = work / f"seg_{seg_index:04d}.ts"
            ok = encode_segment(src, want, ts)
            if not ok:
                continue
            seg_paths.append(ts)
            actual = probe_duration(ts)
            if actual <= 0:
                actual = want
            accumulated += actual
            seg_index += 1
            log(
                f"segment {seg_index:03d} cycle={cycle+1} src={src.name[:60]} "
                f"want={want:.2f}s got={actual:.2f}s cum={accumulated:.2f}/{target:.2f}s"
            )
            if seg_index >= MAX_SEGMENTS:
                log("WARN hit MAX_SEGMENTS; stopping segment loop")
                break

    if not seg_paths:
        fail("no segments could be encoded", 4)

    # ------------------------------------------------------------------
    # concat TS segments → silent_video.mp4
    # use absolute paths in the concat list (per spec)
    # ------------------------------------------------------------------
    list_path = (work / "tmp_concat.txt").resolve()
    list_path.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in seg_paths),
        encoding="utf-8",
    )

    silent = work / "silent_video.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(silent),
    ]
    r = run_capture(cmd)
    silent_d = probe_duration(silent) if silent.is_file() else -1.0
    if r.returncode != 0 or silent_d <= 0:
        log("WARN concat-demuxer path failed; falling back to concat: protocol")
        url = "concat:" + "|".join(str(p.resolve()) for p in seg_paths)
        cmd2 = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", url,
            "-c", "copy",
            "-movflags", "+faststart",
            str(silent),
        ]
        r2 = run_capture(cmd2)
        silent_d = probe_duration(silent) if silent.is_file() else -1.0
        if r2.returncode != 0 or silent_d <= 0:
            fail(f"silent video assembly failed: {(r2.stderr or '')[-500:]}", 5)

    log(f"silent_video={silent} duration={silent_d:.2f}s")
    return silent


# ---------------------------------------------------------------------------
# music selection
# ---------------------------------------------------------------------------

def pick_music() -> Optional[Path]:
    md = EP_DIR / "music"
    if not md.is_dir():
        return None
    cands = sorted(
        [
            p for p in md.iterdir()
            if p.is_file()
            and p.suffix.lower() in AUDIO_EXTS
            and not p.name.startswith(".")
        ],
        key=lambda x: x.name.lower(),
    )
    return cands[0] if cands else None


# ---------------------------------------------------------------------------
# final mux: silent video + voice (+ optional ducked music)
# ---------------------------------------------------------------------------

def mux_final(
    silent: Path, voice: Path, music: Optional[Path], target: float, out: Path
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if music is not None and music.is_file():
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(silent),
            "-i", str(voice),
            "-stream_loop", "-1", "-i", str(music),
            "-filter_complex",
            (
                f"[1:a]aresample={AR},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"asetpts=PTS-STARTPTS[v];"
                f"[2:a]volume={MUSIC_VOL},aresample={AR},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"atrim=0:{target:.3f},asetpts=PTS-STARTPTS[bg];"
                f"[v][bg]amix=inputs=2:duration=first:dropout_transition=0[a]"
            ),
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2",
            "-t", f"{target:.3f}",
            "-movflags", "+faststart",
            str(out),
        ]
    else:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(silent),
            "-i", str(voice),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2",
            "-t", f"{target:.3f}",
            "-movflags", "+faststart",
            str(out),
        ]
    r = run_capture(cmd)
    if r.returncode != 0 or not out.is_file():
        fail(f"final mux failed: {(r.stderr or '')[-500:]}", 6)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    if not EP_DIR.is_dir():
        fail(f"episode dir not found: {EP_DIR}", 1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # voice
    voice = resolve_voice()
    log(f"voice={voice}")
    voice_d = probe_duration(voice)
    if voice_d <= 0.0:
        fail(f"could not probe voice duration: {voice}", 2)
    log(f"voice_duration={voice_d:.2f}s")

    # videos
    cands = collect_video_sources()
    log(f"video_candidates={len(cands)}")
    usable: list[tuple[Path, float]] = []
    for p in cands:
        if not has_video_stream(p):
            log(f"skip (no video stream): {p.name}")
            continue
        d = probe_duration(p)
        if d <= 0.5:
            log(f"skip (unusable duration={d:.2f}): {p.name}")
            continue
        usable.append((p, d))
    log(f"videos_found={len(usable)}")
    if not usable:
        fail("no usable video sources under footage/artwork/paris", 3)

    # work dir reset (keep voice + music + sources untouched)
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    # silent video matching voice length
    silent = build_silent_video(usable, voice_d, WORK_DIR)

    # music
    music = pick_music()
    log(f"music={music if music else 'NONE'}")

    # final mux
    mux_final(silent, voice, music, voice_d, OUT_FILE)

    # validate output
    final_d = probe_duration(OUT_FILE)
    has_v = has_video_stream(OUT_FILE)
    has_a = has_audio_stream(OUT_FILE)
    log(f"output={OUT_FILE}")
    log(f"final_duration={final_d:.2f}s")
    log(f"video_stream={'yes' if has_v else 'NO'}")
    log(f"audio_stream={'yes' if has_a else 'NO'}")

    if not (OUT_FILE.is_file() and final_d > 0 and has_v and has_a):
        fail("output validation failed", 7)

    log(f"OK file_size={OUT_FILE.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
