#!/usr/bin/env python3
"""
Move videos longer than N minutes under StateVerge NYC/video into a subfolder.

Default:
  - Source root: /Volumes/StateVerge/NYC/video
  - Destination folder (created if needed): <source>/long_over_10min
  - Threshold: 10 minutes (600 seconds)

Requires ``ffprobe`` on PATH.

Usage:
  python3 scripts/nyc_video_bucket_long.py --dry-run
  python3 scripts/nyc_video_bucket_long.py
  python3 scripts/nyc_video_bucket_long.py --minutes 15 --recursive
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import get_nyc_video_root, get_stateverge_volume  # noqa: E402


VIDEO_EXTS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".mov",
        ".m4v",
        ".mkv",
        ".webm",
        ".avi",
        ".wmv",
        ".mpg",
        ".mpeg",
        ".mts",
        ".m2ts",
        ".ts",
        ".3gp",
        ".flv",
    }
)

LONG_SUBDIR = "long_over_10min"

FFPROBE = shutil.which("ffprobe") or "ffprobe"


def log(msg: str) -> None:
    print(f"[nyc-video-long] {msg}", flush=True)


def probe_duration_sec(path: Path) -> float | None:
    try:
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
            timeout=180,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log(f"ffprobe 不可用或超时: {path.name} ({e})")
        return None
    if r.returncode != 0:
        return None
    try:
        return float((r.stdout or "").strip())
    except ValueError:
        return None


def unique_dest(dest_dir: Path, filename: str) -> Path:
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suf = Path(filename).suffix
    i = 1
    while True:
        c = dest_dir / f"{stem}_{i}{suf}"
        if not c.exists():
            return c
        i += 1


def iter_candidates(src: Path, *, recursive: bool) -> list[Path]:
    src_r = src.resolve()
    out: list[Path] = []
    if not src.is_dir():
        return out
    if recursive:
        for p in src.rglob("*"):
            if not p.is_file():
                continue
            if p.name.startswith("._"):
                continue
            suf = p.suffix.lower()
            if suf not in VIDEO_EXTS:
                continue
            try:
                rel = p.relative_to(src_r)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] == LONG_SUBDIR:
                continue
            out.append(p)
    else:
        for p in sorted(src.iterdir()):
            if not p.is_file():
                continue
            if p.name.startswith("._"):
                continue
            suf = p.suffix.lower()
            if suf not in VIDEO_EXTS:
                continue
            out.append(p)
    out.sort(key=lambda x: str(x).lower())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Bucket NYC/video files longer than N minutes")
    ap.add_argument(
        "--src",
        type=Path,
        default=get_nyc_video_root(),
        help="Source directory",
    )
    ap.add_argument(
        "--subdir",
        default=LONG_SUBDIR,
        help=f"Folder name under src (default: {LONG_SUBDIR})",
    )
    ap.add_argument(
        "--minutes",
        type=float,
        default=10.0,
        help="Threshold in minutes (default: 10)",
    )
    ap.add_argument(
        "--recursive",
        action="store_true",
        help="Scan subfolders too (still skips files inside --subdir)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = args.src.resolve()
    thresh_sec = float(args.minutes) * 60.0
    dest_dir = (src / args.subdir).resolve()

    if not src.is_dir():
        log(f"源目录不存在: {src}")
        return 2

    try:
        dest_dir.relative_to(src)
    except ValueError:
        log("--subdir 必须在源目录之下")
        return 2

    vol = get_stateverge_volume()
    if (
        len(src.parts) >= 3
        and src.parts[1] == "Volumes"
        and src.parts[2] == vol.name
        and not vol.is_dir()
    ):
        log(f"错误：项目 SSD（{vol.name}）未挂载")
        return 3

    candidates = iter_candidates(src, recursive=bool(args.recursive))
    log(f"源目录: {src}")
    log(f"长视频目录: {dest_dir}")
    log(f"阈值: > {args.minutes:g} 分钟 ({thresh_sec:g}s)")
    log(f"候选文件数: {len(candidates)} · recursive={'yes' if args.recursive else 'no'}")

    moved = 0
    skipped_short = 0
    skipped_unknown = 0

    if not args.dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)

    for p in candidates:
        dur = probe_duration_sec(p)
        if dur is None:
            log(f"跳过（无法读取时长）: {p.name}")
            skipped_unknown += 1
            continue
        if dur <= thresh_sec:
            skipped_short += 1
            continue

        dst = unique_dest(dest_dir, p.name)
        mins = dur / 60.0
        if args.dry_run:
            log(f"[dry-run] {p.name} · {mins:.2f} min → {dst.relative_to(src)}")
            moved += 1
            continue
        shutil.move(str(p), str(dst))
        log(f"已移动 {p.name} ({mins:.2f} min) → {dst.name}")
        moved += 1

    log("")
    log("========== 汇总 ==========")
    log(f"移入「{args.subdir}」: {moved}")
    log(f"未达阈值跳过: {skipped_short}")
    log(f"时长未知跳过: {skipped_unknown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
