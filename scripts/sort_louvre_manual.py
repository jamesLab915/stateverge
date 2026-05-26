#!/usr/bin/env python3
"""
Manual Louvre / museum / Paris asset sorter.

One-shot sorter that scans the **top level** of ``~/Downloads`` (no recursion,
no watcher, no daemon) and moves matching media files into:

    ~/StateVerge/manual_videos/louvre_episode_01/<category>/

Categories:
    footage/       Louvre exterior, gallery interiors, visitors, etc.
    artwork/       Paintings, sculptures, statues, artifacts
    paris/         Paris cityscape, Seine, Eiffel, France
    music/         Music / soundtrack / ambient / piano / cinematic
    templates/     After Effects / opener / lower third / slideshow
    _raw_unsorted/ Anything that didn't match a keyword bucket

Only files with these extensions are considered:
    .mp4 .mov .m4v .mp3 .wav .zip .jpg .png

Behaviour:
- Top level only (subdirectories of ~/Downloads are ignored).
- Filename matching is case-insensitive; ``_`` and ``-`` are normalised to
  spaces so that "museum_interior_shot.mp4" matches "museum interior".
- Files are MOVED with ``shutil.move``; missing destinations are auto-created.
- On name collision, ``_2``, ``_3`` ... is appended before the extension.

Usage:
    python scripts/sort_louvre_manual.py
    python scripts/sort_louvre_manual.py --dry-run
    python scripts/sort_louvre_manual.py --src ~/Downloads --dst ~/somewhere

This script does not modify any existing watcher / daemon and does not run
itself automatically.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable


def _default_downloads_src() -> Path:
    try:
        from src.utils.home_downloads import resolve_home_downloads

        return resolve_home_downloads()
    except ImportError:
        home = Path.home()
        for name in ("Downloads", "downloads", "下载"):
            p = home / name
            if p.is_dir():
                return p.resolve()
        return (home / "Downloads").resolve()


ALLOWED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mp3",
    ".wav",
    ".zip",
    ".jpg",
    ".png",
}

CATEGORIES: list[tuple[str, list[str]]] = [
    (
        "footage",
        ["louvre", "pyramid", "building", "architecture", "exterior"],
    ),
    (
        "footage",
        ["gallery", "museum interior", "hall", "visitor"],
    ),
    (
        "artwork",
        ["painting", "renaissance", "sculpture", "statue", "artifact"],
    ),
    (
        "paris",
        ["paris", "france", "seine", "eiffel", "city"],
    ),
    (
        "music",
        ["music", "piano", "cinematic", "ambient", "soundtrack"],
    ),
    (
        "templates",
        ["template", "opener", "title", "lower third", "slideshow"],
    ),
]
DEFAULT_BUCKET = "_raw_unsorted"
ALL_BUCKETS = ["footage", "artwork", "paris", "music", "templates", DEFAULT_BUCKET]

_NORMALISE = re.compile(r"[_\-]+")


def _normalise(name: str) -> str:
    """Lowercase + replace ``_``/``-`` runs with single spaces (for matching)."""
    return _NORMALISE.sub(" ", name.lower())


def classify(filename: str) -> str:
    """Return category bucket name for ``filename`` based on keywords."""
    n = _normalise(filename)
    for bucket, keywords in CATEGORIES:
        for kw in keywords:
            if kw in n:
                return bucket
    return DEFAULT_BUCKET


def _resolve_collision(dst: Path) -> Path:
    """If ``dst`` exists, return ``dst`` with ``_2``/``_3``... before suffix."""
    if not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    parent = dst.parent
    i = 2
    while True:
        cand = parent / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def _iter_top_level_files(src: Path) -> Iterable[Path]:
    """Yield top-level files in ``src`` (no recursion)."""
    if not src.is_dir():
        return
    for p in sorted(src.iterdir(), key=lambda x: x.name):
        if p.is_file():
            yield p


def sort_downloads(
    src: Path,
    dst_root: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Sort top-level files in ``src`` into ``dst_root/<bucket>/``.

    Returns a per-bucket count of files moved (or that would be moved).
    Files with non-allowed extensions are skipped silently.
    """
    src = src.expanduser().resolve()
    dst_root = dst_root.expanduser().resolve()

    counts: dict[str, int] = {b: 0 for b in ALL_BUCKETS}
    skipped = 0

    if not src.is_dir():
        print(f"[sort] error: source not found: {src}", file=sys.stderr)
        return counts

    for bucket in ALL_BUCKETS:
        (dst_root / bucket).mkdir(parents=True, exist_ok=True)

    moved_total = 0
    for p in _iter_top_level_files(src):
        if p.suffix.lower() not in ALLOWED_EXTENSIONS:
            skipped += 1
            continue
        bucket = classify(p.name)
        target_dir = dst_root / bucket
        target = _resolve_collision(target_dir / p.name)

        if dry_run:
            print(f"[sort] DRY file={p.name} → category={bucket} target={target.name}")
        else:
            try:
                shutil.move(str(p), str(target))
            except OSError as e:
                print(
                    f"[sort] error moving file={p.name} → category={bucket}: {e}",
                    file=sys.stderr,
                )
                continue
            print(f"[sort] file={p.name} → category={bucket}")
        counts[bucket] += 1
        moved_total += 1

    print("[sort] ===== summary =====")
    print(f"[sort] source     : {src}")
    print(f"[sort] destination: {dst_root}")
    print(f"[sort] dry_run    : {dry_run}")
    print(f"[sort] processed  : {moved_total} (skipped non-allowed ext: {skipped})")
    for bucket in ALL_BUCKETS:
        print(f"[sort] {bucket:<14} {counts[bucket]}")
    return counts


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python scripts/sort_louvre_manual.py",
        description=(
            "Manual one-shot sorter for Louvre / museum / Paris assets. "
            "Scans the TOP LEVEL of ~/Downloads (or ~/downloads / ~/下载 when resolved) "
            "into ~/StateVerge/manual_videos/louvre_episode_01/<bucket>/."
        ),
    )
    p.add_argument(
        "--src",
        type=Path,
        default=_default_downloads_src(),
        help="Source directory (default: first existing of ~/Downloads, ~/downloads, ~/下载).",
    )
    p.add_argument(
        "--dst",
        type=Path,
        default=Path.home() / "StateVerge" / "manual_videos" / "louvre_episode_01",
        help="Destination root (default: ~/StateVerge/manual_videos/louvre_episode_01).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only; do not actually move files.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    sort_downloads(args.src, args.dst, dry_run=bool(args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
