#!/usr/bin/env python3
"""DaVinci Bypass: copy/move validated renders into ready_to_upload (no Resolve).

When ``DAVINCI_BYPASS=1`` (default if unset), use this instead of inbox→exports.
Paths come from ``utils.storage_paths`` only. Fail-open per file; never overwrites
existing targets (timestamp suffix on conflict).
"""

from __future__ import annotations

# IMPORTANT:
# Do not mux original iPhone/VFR/high-fps MOV/MP4 with -c:v copy after audio replacement.
# It can stretch long footage into multi-hour slow motion.
# FFmpeg publish paths must use normalized CFR inputs or re-encode video for final deliverables.

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

from utils.davinci_ffprobe import ffprobe_json, stream_summary  # noqa: E402
from utils.storage_paths import (  # noqa: E402
    get_davinci_logs_dir,
    get_sv_cache_renders,
    get_transfer_ready_to_upload,
)

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}

MIN_DURATION_SEC = 5.0
MIN_BYTES = 1 * 1024 * 1024

DEFAULT_KEEP_NAME_SUBSTRINGS = ("final_audio_clean", "cleaned_audio_mix")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("davinci_bypass_collect")


def davinci_bypass_enabled() -> bool:
    """Unset env → bypass ON (per project convention). ``0``/``false``/``no`` → full DaVinci path."""
    raw = os.environ.get("DAVINCI_BYPASS")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def manifest_columns() -> list[str]:
    return [
        "timestamp",
        "source_path",
        "target_path",
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
    return ensure_manifest_dir() / "davinci_bypass_manifest.csv"


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


def _is_rough_cut(p: Path) -> bool:
    return p.name.lower() == "rough_cut.mp4"


def _is_default_keep(p: Path) -> bool:
    s = p.name.lower()
    if "audio_clean" in p.as_posix().lower():
        # Prefer anything under audio_clean/, but still allow name-based matching too.
        return True
    return any(sub in s for sub in DEFAULT_KEEP_NAME_SUBSTRINGS)


def collect_videos(root: Path, *, limit: int | None, include_rough_cut: bool) -> list[Path]:
    if not root.is_dir():
        log.warning("renders missing or not a directory: %s", root)
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
            # Default policy: only collect audio-clean outputs.
            if _is_rough_cut(p):
                if not include_rough_cut:
                    continue
            else:
                if not _is_default_keep(p):
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


def validate_media(src: Path) -> tuple[bool, bool, bool, float, str]:
    """Returns ok, has_video, has_audio, duration, error."""
    try:
        st = src.stat()
        if st.st_size <= MIN_BYTES:
            return False, False, False, 0.0, f"size_le_{MIN_BYTES}_bytes"
    except OSError as exc:
        return False, False, False, 0.0, f"stat:{exc}"

    data, prob_err = ffprobe_json(src)
    if data is None:
        return False, False, False, 0.0, prob_err or "ffprobe_failed"
    hv, ha, dur = stream_summary(data)
    if not hv:
        return False, hv, ha, dur, "no_video_stream"
    if dur <= MIN_DURATION_SEC:
        return False, hv, ha, dur, f"duration_le_{MIN_DURATION_SEC}s"
    return True, hv, ha, dur, ""


def transfer_one(
    src: Path,
    dest_dir: Path,
    *,
    dry_run: bool,
    use_move: bool,
) -> tuple[str, Path | None, str, bool, bool, float, str]:
    ok, hv, ha, dur, verr = validate_media(src)
    if not ok:
        return "skipped", None, "fail", hv, ha, dur, verr

    dst = unique_destination(src, dest_dir)
    action = "move" if use_move else "copy"

    if dry_run:
        return "dry_run", dst, "dry_run", hv, ha, dur, ""

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
    ap.add_argument("--move", action="store_true", help="Move out of renders instead of copy.")
    ap.add_argument("--copy", action="store_true", help="Explicit copy (default).")
    ap.add_argument(
        "--include-rough-cut",
        action="store_true",
        help="Also allow collecting rough_cut.mp4 (default: excluded).",
    )
    ap.add_argument("--limit", type=int, default=0, help="Max files (0 = no limit).")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    bypass_on = davinci_bypass_enabled()
    log.info("DAVINCI_BYPASS effective bypass=%s (env raw=%r)", bypass_on, os.environ.get("DAVINCI_BYPASS"))
    if not bypass_on:
        log.warning(
            "DAVINCI_BYPASS is off; this script is for bypass workflow. "
            "Continuing anyway (fail-open).",
        )

    use_move = bool(args.move)
    if args.move and args.copy:
        log.warning("both --move and --copy; preferring --move")
    elif args.copy:
        use_move = False

    renders_root = get_sv_cache_renders(verbose=args.verbose)
    ready_root = get_transfer_ready_to_upload(verbose=args.verbose)
    log.info("renders -> %s", renders_root)
    log.info("ready_to_upload -> %s", ready_root)

    lim = args.limit if args.limit and args.limit > 0 else None
    jobs = collect_videos(renders_root, limit=lim, include_rough_cut=bool(args.include_rough_cut))
    log.info("found %s render candidates", len(jobs))

    for src in jobs:
        action, dst, status, hv, ha, dur, err = transfer_one(
            src, ready_root, dry_run=args.dry_run, use_move=use_move
        )
        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "source_path": str(src),
            "target_path": str(dst) if dst else "",
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
