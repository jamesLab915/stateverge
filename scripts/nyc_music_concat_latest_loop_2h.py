#!/usr/bin/env python3
"""
将外接 StateVerge 盘 ``NYC/music`` 里「最近写入/导入」的 N 首音频拼成一条轨；
若总时长不足 ``--target-hours``，按同一顺序整段循环，直至达到目标时长（默认精确截到 2 小时）。

默认目录：``/Volumes/StateVerge/NYC/music``（与 ``downloads_extract_mp3_to_nyc_music.py`` 一致）

依赖：系统已安装 ``ffmpeg``、``ffprobe``。

用法：
  ./.venv/bin/python scripts/nyc_music_concat_latest_loop_2h.py
  ./.venv/bin/python scripts/nyc_music_concat_latest_loop_2h.py --music-dir /Volumes/StateVerge/NYC/music \\
      --count 30 --output ~/Desktop/nyc_latest30_2h.mp3
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import get_nyc_music_root  # noqa: E402


AUDIO_EXTS = frozenset({
    ".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".aif", ".aiff",
})

DEFAULT_MUSIC_DIR = get_nyc_music_root()


def log(msg: str) -> None:
    print(f"[nyc_music_2h] {msg}", flush=True)


def iter_audio_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.iterdir():
        if not p.is_file():
            continue
        name = p.name
        # AppleDouble / resource forks on exFAT/FAT show up as ._filename — not real audio
        if name.startswith("._") or name == ".DS_Store":
            continue
        if p.suffix.lower() in AUDIO_EXTS:
            out.append(p)
    return out


def ffprobe_duration_sec(path: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {r.stderr.strip()}")
    line = (r.stdout or "").strip()
    if not line:
        return 0.0
    try:
        return max(0.0, float(line))
    except ValueError:
        return 0.0


def escape_concat_path(path: Path) -> str:
    s = str(path.resolve())
    return s.replace("'", "'\\''")


def write_concat_list(paths: list[Path], dest: Path) -> None:
    lines = [f"file '{escape_concat_path(p)}'\n" for p in paths]
    dest.write_text("".join(lines), encoding="utf-8")


def require_bin(name: str) -> None:
    r = subprocess.run(["which", name], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"missing `{name}` on PATH; install ffmpeg")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Concat latest N tracks from NYC/music and loop to ~2h.",
    )
    ap.add_argument(
        "--music-dir",
        type=Path,
        default=DEFAULT_MUSIC_DIR,
        help=f"folder containing audio files (default: {DEFAULT_MUSIC_DIR})",
    )
    ap.add_argument("--count", type=int, default=30,
                    help="how many most-recent files to include (by mtime)")
    ap.add_argument(
        "--target-hours",
        type=float,
        default=2.0,
        help="target duration in hours (default: 2)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output mp3 path (default: <music-dir>/mix_latest<N>_<H>h.mp3)",
    )
    ap.add_argument(
        "--bitrate",
        default="192k",
        help="lame bitrate when re-encoding (default: 192k)",
    )
    ns = ap.parse_args(argv)

    require_bin("ffmpeg")
    require_bin("ffprobe")

    music_dir: Path = ns.music_dir.expanduser().resolve()
    if not music_dir.is_dir():
        log(f"ERROR: music dir does not exist: {music_dir}")
        return 2

    files = iter_audio_files(music_dir)
    if not files:
        log(f"ERROR: no audio files (*{', *'.join(sorted(AUDIO_EXTS))}) in {music_dir}")
        return 2

    # Newest imports first (mtime descending), then take --count
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    take = max(1, min(ns.count, len(files)))
    picked = files[:take]
    # Playlist order: older → newer within this batch (natural listening order)
    playlist = sorted(picked, key=lambda p: p.stat().st_mtime)

    log(f"music_dir={music_dir}")
    log(f"found {len(files)} audio files; using latest {take} by mtime")

    durations: dict[Path, float] = {}
    for p in playlist:
        try:
            durations[p] = ffprobe_duration_sec(p)
        except Exception as exc:
            log(f"WARN skip {p.name}: {exc}")
    playlist = [p for p in playlist if durations.get(p, 0) > 0.01]
    if not playlist:
        log("ERROR: no playable tracks after ffprobe")
        return 2

    cycle_sec = sum(durations[p] for p in playlist)
    target_sec = max(1.0, float(ns.target_hours) * 3600.0)
    log(
        f"one playlist cycle ≈ {cycle_sec / 60:.1f} min (from ffprobe sum); "
        f"target output {target_sec / 3600:.2f} h via stream_loop"
    )

    out_path = ns.output
    if out_path is None:
        hlabel = str(ns.target_hours).replace(".", "p")
        out_path = music_dir / f"mix_latest{take}_{hlabel}h.mp3"
    else:
        out_path = out_path.expanduser().resolve()

    tmp_dir = Path(tempfile.mkdtemp(prefix="nyc_music_concat_"))
    list_file = tmp_dir / "concat.txt"
    cycle_wav = tmp_dir / "_one_cycle.wav"
    try:
        write_concat_list(playlist, list_file)
        # Pass 1: concat once → normalized PCM (mixed formats ok).
        cmd_concat = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-ar", "44100",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(cycle_wav),
        ]
        log(f"concat one cycle (PCM) → {cycle_wav.name}")
        r1 = subprocess.run(cmd_concat, capture_output=True, text=True)
        if r1.returncode != 0:
            log(f"ffmpeg concat stderr:\n{r1.stderr}")
            return r1.returncode or 1
        cycle_actual = ffprobe_duration_sec(cycle_wav)
        if cycle_actual < 1.0:
            log("ERROR: concat produced nearly empty audio")
            return 2
        drift = abs(cycle_actual - cycle_sec)
        if drift > max(15.0, cycle_sec * 0.05):
            log(
                f"WARN PCM cycle {cycle_actual:.1f}s vs ffprobe-sum {cycle_sec:.1f}s "
                f"(drift {drift:.1f}s)"
            )
        # Pass 2: loop PCM until -t target (avoids long concat demuxer lists).
        cmd_enc = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-y",
            "-stream_loop", "-1",
            "-i", str(cycle_wav),
            "-t", f"{target_sec:.3f}",
            "-c:a", "libmp3lame",
            "-b:a", ns.bitrate,
            str(out_path),
        ]
        log(f"loop+encode MP3 → {out_path}")
        r = subprocess.run(cmd_enc, capture_output=True, text=True)
        if r.returncode != 0:
            log(f"ffmpeg encode stderr:\n{r.stderr}")
            return r.returncode or 1
    finally:
        try:
            list_file.unlink(missing_ok=True)
            cycle_wav.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass

    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
