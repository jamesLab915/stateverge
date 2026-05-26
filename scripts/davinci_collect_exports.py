#!/usr/bin/env python3
"""Collect DaVinci exports from SV_CACHE/davinci_exports into SV_TRANSFER/ready_to_upload.

Requires video stream (ffprobe). Audio optional; both recorded in manifest.
Default: copy (preserve originals). Fail-open per file.
"""

from __future__ import annotations

import argparse
import csv
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.davinci_ffprobe import ffprobe_json, stream_summary  # noqa: E402
from utils.storage_paths import (  # noqa: E402
    get_davinci_exports,
    get_davinci_logs_dir,
    get_transfer_ready_to_upload,
)

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("davinci_collect_exports")


def manifest_columns() -> list[str]:
    return [
        "timestamp",
        "export_path",
        "ready_to_upload_path",
        "action",
        "has_video",
        "has_audio",
        "duration",
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
    return ensure_manifest_dir() / "davinci_export_manifest.csv"


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


def collect_videos(root: Path, *, limit: int | None) -> list[Path]:
    if not root.is_dir():
        log.warning("davinci_exports missing or not a directory: %s", root)
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


def unique_destination(src: Path, dest_dir: Path) -> Path:
    candidate = dest_dir / src.name
    if not candidate.exists():
        return candidate
    tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    return dest_dir / f"{src.stem}_{tag}{src.suffix}"


def transfer_one(
    src: Path,
    dest_dir: Path,
    *,
    dry_run: bool,
    use_move: bool,
) -> tuple[str, Path | None, str, bool, bool, float, str]:
    """Returns action, dest_path or None, status, has_video, has_audio, duration, error."""
    data, prob_err = ffprobe_json(src)
    if data is None:
        return "skipped", None, "fail", False, False, 0.0, prob_err or "ffprobe_failed"
    hv, ha, dur = stream_summary(data)
    if not hv:
        return "skipped", None, "fail", hv, ha, dur, "no_video_stream"

    dst = unique_destination(src, dest_dir)
    action = "move" if use_move else "copy"

    if dry_run:
        return "dry_run", dst, "ok", hv, ha, dur, ""

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return action, dst, "fail", hv, ha, dur, f"mkdir_dest:{exc}"

    try:
        if use_move:
            shutil.move(str(src), str(dst))
        else:
            shutil.copy2(src, dst)
    except OSError as exc:
        return action, dst, "fail", hv, ha, dur, str(exc)

    return action, dst, "ok", hv, ha, dur, ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--move",
        action="store_true",
        help="Move files out of davinci_exports instead of copying.",
    )
    ap.add_argument(
        "--copy",
        action="store_true",
        help="Explicit copy mode (default).",
    )
    ap.add_argument("--limit", type=int, default=0, help="Max files (0 = no limit).")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    use_move = bool(args.move)
    if args.move and args.copy:
        log.warning("both --move and --copy set; preferring --move")
    elif args.copy:
        use_move = False

    exports_root = get_davinci_exports(verbose=args.verbose)
    ready_root = get_transfer_ready_to_upload(verbose=args.verbose)
    log.info("davinci_exports -> %s", exports_root)
    log.info("ready_to_upload -> %s", ready_root)

    lim = args.limit if args.limit and args.limit > 0 else None
    jobs = collect_videos(exports_root, limit=lim)
    log.info("found %s export candidates", len(jobs))

    for src in jobs:
        action, dst, status, hv, ha, dur, err = transfer_one(
            src, ready_root, dry_run=args.dry_run, use_move=use_move
        )
        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "export_path": str(src),
            "ready_to_upload_path": str(dst) if dst else "",
            "action": action,
            "has_video": str(hv).lower(),
            "has_audio": str(ha).lower(),
            "duration": f"{dur:.3f}",
            "status": status,
            "error": err,
        }
        append_manifest_row(row)
        log.info(
            "%s %s action=%s dest=%s v=%s a=%s dur=%.3fs err=%s",
            status,
            src.name,
            action,
            dst.name if dst else "-",
            hv,
            ha,
            dur,
            err or "-",
        )

    log.info("done dry_run=%s move=%s", args.dry_run, use_move)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
