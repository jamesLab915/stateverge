#!/usr/bin/env python3
"""Move renders/uploads into SV_CACHE/davinci_inbox for DaVinci (hardlink, then copy2).

Fail-open: scan continues after per-file errors. Paths use ``utils.storage_paths`` only.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import (  # noqa: E402
    get_davinci_inbox,
    get_davinci_logs_dir,
    get_sv_cache_renders,
    get_sv_cache_uploads,
)

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("davinci_prepare_queue")


def manifest_columns() -> list[str]:
    return [
        "timestamp",
        "source_path",
        "davinci_inbox_path",
        "mode",
        "status",
        "error",
    ]


def ensure_manifest_dir() -> Path:
    d = get_davinci_logs_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("could not mkdir logs dir %s: %s", d, exc)
    return d


def manifest_path() -> Path:
    return ensure_manifest_dir() / "davinci_queue_manifest.csv"


def append_manifest_row(row: dict[str, str]) -> None:
    mp = manifest_path()
    fields = manifest_columns()
    is_new = not mp.is_file()
    try:
        with mp.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if is_new:
                w.writeheader()
            w.writerow(row)
    except OSError as exc:
        log.warning("manifest append failed %s: %s", mp, exc)


def resolve_source(kind: str, *, verbose: bool) -> Path:
    if kind == "renders":
        return get_sv_cache_renders(verbose=verbose)
    if kind == "uploads":
        return get_sv_cache_uploads(verbose=verbose)
    raise ValueError(kind)


def collect_videos(root: Path, *, limit: int | None) -> list[Path]:
    if not root.is_dir():
        log.warning("source missing or not a directory: %s", root)
        return []
    found: list[Path] = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.name.startswith("._") or (p.name.startswith(".") and not p.stem):
                continue
            if p.suffix.lower() not in VIDEO_EXTS:
                continue
            found.append(p)
    except OSError as exc:
        log.warning("rglob failed under %s: %s", root, exc)
        return []

    def mtime(pp: Path) -> float:
        try:
            return pp.stat().st_mtime
        except OSError:
            return 0.0

    found.sort(key=mtime, reverse=True)
    if limit is not None and limit > 0:
        found = found[:limit]
    return found


def link_or_copy(src: Path, dst: Path, *, dry_run: bool) -> tuple[str, str]:
    """Return (mode, error_message)."""
    if dry_run:
        return "dry_run", ""
    try:
        if dst.exists():
            return "skipped_duplicate", ""
    except OSError as exc:
        return "fail", f"stat_dst:{exc}"
    try:
        os.link(src, dst)
        return "hardlink", ""
    except OSError as exc_h:
        log.info("hardlink failed for %s -> %s (%s); trying copy2", src.name, dst.name, exc_h)
        try:
            shutil.copy2(src, dst)
            return "copy2", ""
        except OSError as exc_c:
            return "fail", str(exc_c)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        choices=("renders", "uploads"),
        default="renders",
        help="Logical queue under SV_CACHE (default: renders).",
    )
    ap.add_argument("--limit", type=int, default=0, help="Max files (0 = no limit).")
    ap.add_argument("--dry-run", action="store_true", help="Log only; no link/copy.")
    ap.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging.")
    args = ap.parse_args()
    if args.verbose:
        log.setLevel(logging.DEBUG)
        logging.getLogger("utils.storage_paths").setLevel(logging.DEBUG)

    verbose = args.verbose
    inbox_root = get_davinci_inbox(verbose=verbose)
    source_root = resolve_source(args.source, verbose=verbose)

    log.info("source=%s -> %s", args.source, source_root)
    log.info("davinci_inbox -> %s", inbox_root)

    if not args.dry_run:
        try:
            inbox_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("mkdir inbox failed (fail-open continue): %s", exc)

    lim = args.limit if args.limit and args.limit > 0 else None
    jobs = collect_videos(source_root, limit=lim)
    log.info("found %s candidate media files", len(jobs))

    for src in jobs:
        dst = inbox_root / src.name
        mode, err = link_or_copy(src, dst, dry_run=args.dry_run)
        status = "ok" if mode in ("hardlink", "copy2", "dry_run") else "fail"
        if mode == "skipped_duplicate":
            status = "skipped"
        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "source_path": str(src),
            "davinci_inbox_path": str(dst),
            "mode": mode,
            "status": status,
            "error": err,
        }
        append_manifest_row(row)
        if args.verbose or status != "ok":
            log.info(
                "%s %s -> %s mode=%s err=%s",
                row["status"],
                src.name,
                dst.name,
                mode,
                err or "-",
            )

    log.info("done dry_run=%s", args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
