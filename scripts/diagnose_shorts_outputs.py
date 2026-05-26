#!/usr/bin/env python3
"""
诊断 topics/<slug>/shorts/ 的真实内容质量。

输出一份每条 short 的体检报告，verdict 为：
    READY               - 可以上传
    FAIL_NO_VOICE       - 缺 voice.wav 或 < 5s
    FAIL_SILENT_AUDIO   - final_short.mp4 音轨整体静音
    FAIL_BLACK_VIDEO    - 至少 1 个 seg_*.mp4 黑屏
    FAIL_NO_BROLL       - shorts/assets/broll/ 没有真正的 B-roll 素材
    FAIL_BAD_FORMAT     - final 分辨率/时长不符合 1080x1920 / 20-35s
    FAIL_MISSING_FINAL  - 没有 final_short.mp4

用法：
    python3 scripts/diagnose_shorts_outputs.py
    python3 scripts/diagnose_shorts_outputs.py --only ai_jobs_short_01,housing_truth_01
    python3 scripts/diagnose_shorts_outputs.py --json   # 机器可读
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

DEFAULT_SLUGS = [
    "ai_jobs_short_01",
    "young_people_lie_flat_01",
    "housing_truth_01",
    "middle_class_disappear_01",
    "save_money_truth_01",
    "city_escape_01",
    "work_exchange_01",
    "ai_skill_gap_01",
    "us_living_cost_01",
    "effort_is_not_enough_01",
]

SILENCE_MEAN_DB = -45.0
SILENCE_MAX_DB = -35.0
GOOD_MAX_DB = -30.0
BLACK_MIN_DURATION = 0.5
BLACK_PIX_TH = 0.10
BLACK_FRAC_FAIL = 0.50  # 一段 >= 50% 黑屏算失败
MIN_VOICE_SEC = 5.0
MIN_FINAL_SEC = 20.0
MAX_FINAL_SEC = 35.0
W, H = 1080, 1920


def _root() -> Path:
    return Path(
        os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")
    ).resolve()


def ffprobe_json(p: Path) -> dict[str, Any]:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(p),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def ffprobe_duration(p: Path) -> float:
    if not p.is_file():
        return 0.0
    info = ffprobe_json(p)
    try:
        return float((info.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def has_audio_stream(p: Path) -> bool:
    for s in ffprobe_json(p).get("streams") or []:
        if s.get("codec_type") == "audio":
            return True
    return False


_VOL_RE_MEAN = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_VOL_RE_MAX = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def volumedetect(p: Path) -> tuple[Optional[float], Optional[float]]:
    """Return (mean_dB, max_dB) of audio in p, or (None, None)."""
    if not p.is_file() or not has_audio_stream(p):
        return None, None
    r = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(p),
            "-vn",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    err = r.stderr or ""
    m = _VOL_RE_MEAN.search(err)
    x = _VOL_RE_MAX.search(err)
    return (float(m.group(1)) if m else None, float(x.group(1)) if x else None)


_BLACK_RE = re.compile(r"black_start:(\d+(?:\.\d+)?)\s+black_end:(\d+(?:\.\d+)?)")


def black_fraction(p: Path) -> float:
    """Return fraction (0..1) of `p`'s duration that is detected as black."""
    if not p.is_file():
        return 1.0
    dur = ffprobe_duration(p)
    if dur <= 0:
        return 1.0
    r = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(p),
            "-vf",
            f"blackdetect=d={BLACK_MIN_DURATION}:pix_th={BLACK_PIX_TH}",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    err = r.stderr or ""
    total = 0.0
    for m in _BLACK_RE.finditer(err):
        s = float(m.group(1))
        e = float(m.group(2))
        total += max(0.0, e - s)
    return min(1.0, total / dur)


def video_resolution(p: Path) -> tuple[int, int, float]:
    info = ffprobe_json(p)
    w = h = 0
    fps = 0.0
    for s in info.get("streams") or []:
        if s.get("codec_type") == "video":
            w = int(s.get("width") or 0)
            h = int(s.get("height") or 0)
            r = s.get("r_frame_rate") or "0/1"
            try:
                num, den = r.split("/")
                fps = float(num) / float(den) if float(den) else 0.0
            except Exception:
                fps = 0.0
            break
    return w, h, fps


@dataclass
class ShortReport:
    slug: str
    script_exists: bool = False
    script_chars: int = 0
    voice_mp3_exists: bool = False
    voice_wav_exists: bool = False
    voice_duration: float = 0.0
    final_exists: bool = False
    final_duration: float = 0.0
    final_w: int = 0
    final_h: int = 0
    final_fps: float = 0.0
    final_audio_stream: bool = False
    final_audio_mean_db: Optional[float] = None
    final_audio_max_db: Optional[float] = None
    work_segments_count: int = 0
    black_segments_count: int = 0
    black_segments: list[str] = field(default_factory=list)
    broll_assets_count: int = 0
    has_broll_assets: bool = False
    verdict: str = "UNKNOWN"
    notes: list[str] = field(default_factory=list)


def diagnose(root: Path, slug: str) -> ShortReport:
    r = ShortReport(slug=slug)
    sdir = root / "topics" / slug / "shorts"
    script = sdir / "script.txt"
    if script.is_file():
        txt = script.read_text(encoding="utf-8", errors="replace")
        r.script_exists = bool(txt.strip())
        r.script_chars = len(txt)

    audio_dir = sdir / "audio"
    voice_mp3 = audio_dir / "voice.mp3"
    voice_wav = audio_dir / "voice.wav"
    r.voice_mp3_exists = voice_mp3.is_file() and voice_mp3.stat().st_size > 1000
    r.voice_wav_exists = voice_wav.is_file() and voice_wav.stat().st_size > 1000
    if r.voice_wav_exists:
        r.voice_duration = ffprobe_duration(voice_wav)
    elif r.voice_mp3_exists:
        r.voice_duration = ffprobe_duration(voice_mp3)

    final = sdir / "output" / "final_short.mp4"
    r.final_exists = final.is_file() and final.stat().st_size > 100_000
    if r.final_exists:
        r.final_duration = ffprobe_duration(final)
        r.final_w, r.final_h, r.final_fps = video_resolution(final)
        r.final_audio_stream = has_audio_stream(final)
        if r.final_audio_stream:
            mean_db, max_db = volumedetect(final)
            r.final_audio_mean_db = mean_db
            r.final_audio_max_db = max_db

    work = sdir / "assets" / "_work"
    segs = sorted(work.glob("seg_*.mp4")) if work.is_dir() else []
    r.work_segments_count = len(segs)
    for s in segs:
        frac = black_fraction(s)
        if frac >= BLACK_FRAC_FAIL:
            r.black_segments_count += 1
            r.black_segments.append(f"{s.name} ({frac*100:.0f}%black)")

    broll_dir = sdir / "assets" / "broll"
    if broll_dir.is_dir():
        files = [
            p for p in broll_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv"}
            and p.stat().st_size > 100_000
        ]
        r.broll_assets_count = len(files)
        r.has_broll_assets = len(files) >= 1

    if not r.final_exists:
        r.verdict = "FAIL_MISSING_FINAL"
        r.notes.append("final_short.mp4 不存在或太小")
        return r
    if not (r.final_w == W and r.final_h == H and 28.0 <= r.final_fps <= 31.0
            and MIN_FINAL_SEC <= r.final_duration <= MAX_FINAL_SEC):
        r.verdict = "FAIL_BAD_FORMAT"
        r.notes.append(
            f"格式异常 {r.final_w}x{r.final_h} fps={r.final_fps:.2f} dur={r.final_duration:.2f}"
        )
        return r
    if not r.voice_wav_exists or r.voice_duration < MIN_VOICE_SEC:
        r.verdict = "FAIL_NO_VOICE"
        r.notes.append(
            f"voice.wav 缺失或过短 (exists={r.voice_wav_exists} dur={r.voice_duration:.2f})"
        )
        return r
    if not r.has_broll_assets:
        r.verdict = "FAIL_NO_BROLL"
        r.notes.append("shorts/assets/broll/ 内无可用 B-roll")
        return r
    if r.black_segments_count > 0:
        r.verdict = "FAIL_BLACK_VIDEO"
        r.notes.append(f"黑屏段：{', '.join(r.black_segments)}")
        return r
    if not r.final_audio_stream:
        r.verdict = "FAIL_SILENT_AUDIO"
        r.notes.append("final 没有 audio stream")
        return r
    mean_db = r.final_audio_mean_db if r.final_audio_mean_db is not None else -100.0
    max_db = r.final_audio_max_db if r.final_audio_max_db is not None else -100.0
    if mean_db < SILENCE_MEAN_DB or max_db < SILENCE_MAX_DB:
        r.verdict = "FAIL_SILENT_AUDIO"
        r.notes.append(
            f"音频静音 mean={mean_db:.1f}dB max={max_db:.1f}dB"
        )
        return r
    if max_db < GOOD_MAX_DB:
        r.notes.append(
            f"音量偏低 max={max_db:.1f}dB（< {GOOD_MAX_DB}dB），建议提升"
        )
    r.verdict = "READY"
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔的 slug 子集")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    root = _root()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    slugs = [s for s in DEFAULT_SLUGS if (not only or s in only)]
    if only:
        # Allow extra slugs not in DEFAULT_SLUGS
        for extra in only:
            if extra not in slugs:
                slugs.append(extra)

    reports = [diagnose(root, s) for s in slugs]

    if args.json:
        print(json.dumps([asdict(x) for x in reports], ensure_ascii=False, indent=2))
        return 0 if all(r.verdict == "READY" for r in reports) else 1

    print("=" * 96)
    print(f" Shorts 内容质检  (root={root})")
    print("=" * 96)
    print(
        f"{'slug':<32}{'script':>7}{'voice':>8}{'final':>7}"
        f"{'res':>10}{'aud_mean':>10}{'aud_max':>9}{'work':>5}"
        f"{'black':>6}{'broll':>6}  verdict"
    )
    print("-" * 96)
    for r in reports:
        res = f"{r.final_w}x{r.final_h}" if r.final_exists else "-"
        am = f"{r.final_audio_mean_db:.1f}" if r.final_audio_mean_db is not None else "-"
        ax = f"{r.final_audio_max_db:.1f}" if r.final_audio_max_db is not None else "-"
        print(
            f"{r.slug:<32}"
            f"{r.script_chars:>7}"
            f"{r.voice_duration:>7.1f}s"
            f"{r.final_duration:>6.1f}s"
            f"{res:>10}"
            f"{am:>10}"
            f"{ax:>9}"
            f"{r.work_segments_count:>5}"
            f"{r.black_segments_count:>6}"
            f"{r.broll_assets_count:>6}  "
            f"{r.verdict}"
        )
        for n in r.notes:
            print(f"    └─ {n}")
    print("-" * 96)
    ok = sum(1 for r in reports if r.verdict == "READY")
    fail = len(reports) - ok
    print(f" READY={ok}  FAIL={fail}  total={len(reports)}")
    print("=" * 96)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
