#!/usr/bin/env python3
"""Generate 9:16 Shorts candidate clips with ffmpeg. Fail-open per clip."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import (  # noqa: E402
    NYC_ROOT,
    OUT_SHORTS,
    VALIDATION_FRAMES_DIR,
    ensure_nyc_dirs,
    ensure_project_layout,
    ffprobe_verify_streams,
    load_source_json,
    log_lines,
    nyc_daily_log,
    project_dir,
    with_each_project,
)

LONG_THRESHOLD = 20 * 60.0
VF_GRAPH = (
    "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
    "crop=1080:1920,setsar=1[v]"
)


def candidate_starts(duration: float, count: int, clip_d: float) -> list[float]:
    clip_d = min(clip_d, max(1.0, duration - 20.0))
    if duration > LONG_THRESHOLD:
        lo = max(10.0, 0.1 * duration)
        hi = min(duration - 10.0 - clip_d, 0.9 * duration - clip_d)
    else:
        lo = 10.0
        hi = duration - 10.0 - clip_d
    if hi < lo:
        lo = 10.0
        hi = duration - 10.0 - clip_d
    if hi < lo:
        lo = max(0.0, (duration - clip_d) / 2)
        hi = lo
    if count <= 1:
        return [float((lo + hi) / 2)]
    step = (hi - lo) / (count - 1)
    return [lo + step * i for i in range(count)]


def run_ffmpeg_short(src: Path, start: float, dur: float, dest: Path, has_audio: bool) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.stem}.partial{dest.suffix}")
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
    if not has_audio:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{dur:.3f}",
            "-i",
            str(src),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-filter_complex",
            VF_GRAPH,
            "-map",
            "[v]",
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
            str(tmp),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{dur:.3f}",
            "-i",
            str(src),
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
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
            str(tmp),
        ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=3600)
        ok, reason = ffprobe_verify_streams(tmp, want_audio=True)
        if not ok:
            log_lines("nyc_shorts", [f"VERIFY_FAIL {tmp}: {reason}"], None)
            try:
                tmp.unlink()
            except OSError:
                pass
            return False
        tmp.replace(dest)
        return True
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        log_lines("nyc_shorts", [f"FFMPEG_FAIL {dest}: {exc}"], None)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def extract_validation_frames(short_mp4: Path, pid: str, stem: str, clip_d: float) -> list[str]:
    """Save 3 JPEGs from a rendered short under cache/validation_frames (manual QC)."""
    base = VALIDATION_FRAMES_DIR / pid
    paths: list[str] = []
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return paths
    clip_d = max(0.1, float(clip_d))
    for idx, frac in enumerate((0.1, 0.5, 0.9), start=1):
        t = max(0.0, min(clip_d * frac, clip_d - 0.04))
        out = base / f"{stem}_vframe{idx}.jpg"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(short_mp4),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(out),
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=300,
            )
            if out.is_file():
                paths.append(str(out.resolve()))
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            continue
    return paths


def process_project(pid: str, count: int, clip_d: float, log_nyc: Path, log_proj: Path) -> None:
    def _l(msg: str) -> None:
        log_lines("nyc_shorts", [f"[{pid}] {msg}"], log_nyc)
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_proj.parent.mkdir(parents=True, exist_ok=True)
            with log_proj.open("a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except OSError:
            pass

    meta = load_source_json(pid)
    if not meta:
        _l("SKIP no source.json")
        return
    duration = float(meta.get("duration") or 0)
    if duration < 180:
        _l("SKIP duration < 180")
        return
    src = Path(meta["source_path"])
    if not src.is_file():
        _l(f"SKIP missing source {src}")
        return

    ensure_project_layout(pid)
    shorts_dir = project_dir(pid) / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)
    has_audio = bool(meta.get("has_audio"))
    starts = candidate_starts(duration, count, clip_d)

    for i in range(1, count + 1):
        name = f"short_{i:03d}.mp4"
        out_proj = shorts_dir / name
        if out_proj.is_file() and out_proj.stat().st_size > 0:
            _l(f"SKIP exists {name}")
            continue
        st = starts[i - 1]
        ok = run_ffmpeg_short(src, st, clip_d, out_proj, has_audio)
        if not ok:
            _l(f"FAIL {name}")
            continue
        out_global = OUT_SHORTS / f"{pid}_{name}"
        try:
            subprocess.run(["cp", "-f", str(out_proj), str(out_global)], check=False, timeout=600)
        except (OSError, subprocess.TimeoutExpired):
            pass
        vframes = extract_validation_frames(out_proj, pid, Path(name).stem, clip_d)
        meta_j = {
            "source_path": str(src),
            "start_time": st,
            "duration": clip_d,
            "output_path": str(out_proj),
            "width": 1080,
            "height": 1920,
            "validation_frames": vframes,
        }
        try:
            (shorts_dir / f"short_{i:03d}.json").write_text(
                json.dumps(meta_j, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            _l(f"WARN json write {exc}")
        verify_ok, reason = ffprobe_verify_streams(out_proj, want_audio=True)
        if verify_ok:
            _l(f"OK {name}")
        else:
            _l(f"WARN post_check {reason}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", action="append", dest="project_ids", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--duration", type=float, default=30.0)
    args = ap.parse_args()

    if not NYC_ROOT.is_dir():
        log_lines("nyc_shorts", ["ERROR: NYC_ROOT not mounted"], nyc_daily_log("nyc_shorts"))
        return 2

    ensure_nyc_dirs()
    log_nyc = nyc_daily_log("nyc_shorts")

    ids: list[str]
    if args.all:
        ids = with_each_project(
            None,
            True,
            lambda m: float(m.get("duration") or 0) >= 180,
        )
    elif args.project_ids:
        ids = list(dict.fromkeys(args.project_ids))
    else:
        log_lines("nyc_shorts", ["ERROR: specify --project-id or --all"], log_nyc)
        return 1

    log_lines("nyc_shorts", ["========== shorts start =========="], log_nyc)
    for pid in ids:
        proj_log = project_dir(pid) / "logs" / f"nyc_shorts_{datetime.now().strftime('%Y-%m-%d')}.log"
        try:
            process_project(pid, max(1, args.count), float(args.duration), log_nyc, proj_log)
        except Exception as exc:  # noqa: BLE001
            log_lines("nyc_shorts", [f"EXCEPTION {pid}: {exc}"], log_nyc)
    log_lines("nyc_shorts", ["========== shorts done =========="], log_nyc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
