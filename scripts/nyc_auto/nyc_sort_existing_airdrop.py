#!/usr/bin/env python3
"""Re-home scattered files under ``NYC_AUTO/raw/airdrop/`` into type/date buckets.

Default preview mode (dry-run): writes manifest CSV + logs, no filesystem moves unless
``--apply`` is passed.
"""

from __future__ import annotations

import argparse
import csv
import errno
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import LOCAL_LOG_DIR, NYC_ROOT, path_matches_test_asset_marker  # noqa: E402


AIRDROP_ROOT = NYC_ROOT / "raw" / "airdrop"

MANIFEST = NYC_ROOT / "logs" / "airdrop_resort_manifest.csv"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".ts", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac"}
SKIP_NAMES = frozenset({".DS_Store"})
SKIP_UNDER_AIRDROP_SEGMENTS = frozenset(
    {
        "quarantine",
        "projects",
        "output",
        "logs",
        "cache",
    },
)

CSV_COLS = (
    "timestamp",
    "source_path",
    "dest_path",
    "file_type",
    "size_bytes",
    "mtime",
    "action",
    "status",
    "error",
)


def file_kind(path: Path) -> str:
    suf = path.suffix.lower()
    if suf in VIDEO_EXTS:
        return "video"
    if suf in IMAGE_EXTS:
        return "image"
    if suf in AUDIO_EXTS:
        return "audio"
    return "other"


def iso_mtime(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def canonical_rel(kind: str, filename: str, st_mtime: float) -> Path:
    dt = datetime.fromtimestamp(st_mtime)
    yyyy = f"{dt.year:04d}"
    ym = f"{dt.year:04d}-{dt.month:02d}"
    dd = f"{dt.day:02d}"

    boundary_morning = 5 * 60
    boundary_day = 11 * 60
    boundary_golden = 16 * 60
    boundary_night = 19 * 60
    t_min = dt.hour * 60 + dt.minute
    bucket = "night"

    if boundary_morning <= t_min < boundary_day:
        bucket = "morning"
    elif boundary_day <= t_min < boundary_golden:
        bucket = "day"
    elif boundary_golden <= t_min < boundary_night:
        bucket = "golden_hour"

    if kind == "video":
        return Path("video") / yyyy / ym / dd / bucket / filename
    if kind == "image":
        return Path("image") / yyyy / ym / dd / filename
    if kind == "audio":
        return Path("audio") / yyyy / ym / dd / filename
    return Path("other") / yyyy / ym / dd / filename


def is_blocked_rel(rel_parts: tuple[str, ...]) -> tuple[bool, str | None]:
    """Skip files living under guarded folder names."""

    for part in rel_parts:
        lowered = part.lower()
        if lowered in SKIP_UNDER_AIRDROP_SEGMENTS:
            return True, lowered
    return False, None


def unique_dest_under_parent(dest_abs: Path) -> Path:
    """Choose unique destination beneath the folder without overwriting."""

    if not dest_abs.exists():
        return dest_abs
    stem = dest_abs.stem
    suf = dest_abs.suffix
    parent = dest_abs.parent
    idx = 1
    while idx < 10000:
        cand = parent / f"{stem}_{idx:03d}{suf}"
        if not cand.exists():
            return cand
        idx += 1
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return parent / f"{stem}_{ts}{suf}"


def append_local_log(lines: list[str]) -> None:
    day_stamp = datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target = LOCAL_LOG_DIR / f"airdrop_resort_{day_stamp}.log"
    LOCAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    block = "".join(f"[{stamp}] {line}\n" for line in lines)
    try:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(block)
    except OSError as exc:
        print(f"[warn] cannot append {target}: {exc}", file=sys.stderr)


def relocate_file(src: Path, dest: Path) -> tuple[bool, str]:
    """Try rename/replace then cross-device tolerant copy semantics."""

    detail = ""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"mkdir_failed:{exc}"

    try:
        os.replace(src, dest)
        detail = "rename_ok"
        return True, detail
    except OSError as err:
        if err.errno not in {errno.EXDEV, errno.ENOTSUP, errno.EINVAL}:
            return False, f"rename_failed:{err}"

    part_path = dest.with_name(dest.name + ".part")
    src_size = 0

    try:
        src_size = src.stat().st_size
    except OSError as exc:
        return False, f"src_stat_failed:{exc}"

    try:
        if part_path.exists():
            part_path.unlink()
    except OSError:
        pass

    try:
        shutil.copyfile(src, part_path)
    except OSError as exc:
        try:
            if part_path.exists():
                part_path.unlink()
        except OSError:
            pass
        return False, f"copy_failed:{exc}"

    try:
        copied = part_path.stat().st_size
    except OSError as exc:
        try:
            part_path.unlink()
        except OSError:
            pass
        return False, f"part_stat_failed:{exc}"

    if copied != src_size:
        try:
            part_path.unlink()
        except OSError:
            pass
        return False, f"size_mismatch:{src_size}!={copied}"

    try:
        if dest.exists():
            try:
                part_path.unlink()
            except OSError:
                pass
            return False, "dest_exists_collision_unexpected"
        os.replace(part_path, dest)
    except OSError as exc:
        try:
            part_path.unlink()
        except OSError:
            pass
        return False, f"finalize_replace_failed:{exc}"

    try:
        src.unlink()
    except OSError as exc:
        return False, f"copy_completed_rm_src_failed:{exc}"

    detail = "copy_part_verify_rm_src"
    return True, detail


def iter_airdrop_files() -> tuple[list[Path], int]:
    files: list[Path] = []
    subtree_masked = 0
    if not AIRDROP_ROOT.exists():
        return files, subtree_masked

    try:
        for cand in AIRDROP_ROOT.rglob("*"):
            try:
                if not cand.is_file():
                    continue
            except OSError:
                continue
            try:
                rel = cand.relative_to(AIRDROP_ROOT)
            except ValueError:
                continue
            if rel.name.startswith("._"):
                continue
            if rel.name in SKIP_NAMES:
                continue
            blocked, _label = is_blocked_rel(rel.parts[:-1])
            if blocked:
                subtree_masked += 1
                continue
            files.append(cand)
    except OSError as exc:
        print(f"[warn] walk interrupted {AIRDROP_ROOT}: {exc}", file=sys.stderr)

    return files, subtree_masked


class Summary:
    scanned = 0
    organized = 0
    planned_or_moved = 0
    skipped_subtrees = 0
    failed = 0

    vid = img = snd = misc = 0

    suspicious = 0
    suspicious_samples: list[str] = []

    dest_dirs_preview: set[str] = set()


def summarize_test_hint(path: Path) -> bool:
    flagged = path_matches_test_asset_marker(str(path), path.name)
    lowered = path.name.lower()
    if not flagged:
        blob = lowered
        flagged = any(key in blob for key in ("synthetic", "test", "dummy", "placeholder"))
    return flagged


def run_sort(*, apply: bool) -> int:
    run_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    AIRDROP_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    summary = Summary()

    sources, subtree_masks = iter_airdrop_files()
    summary.skipped_subtrees = subtree_masks
    sources.sort(key=lambda item: str(item).lower())

    def note(message: str) -> None:
        append_local_log([message])

    note(f"BEGIN apply={apply} candidates={len(sources)} root={AIRDROP_ROOT} subtree_masked={summary.skipped_subtrees}")

    for src in sources:
        summary.scanned += 1
        rel = src.relative_to(AIRDROP_ROOT)
        kind = file_kind(src)
        if kind == "video":
            summary.vid += 1
        elif kind == "image":
            summary.img += 1
        elif kind == "audio":
            summary.snd += 1
        else:
            summary.misc += 1

        if summarize_test_hint(src):
            summary.suspicious += 1
            if len(summary.suspicious_samples) < 8:
                summary.suspicious_samples.append(str(src))

        try:
            st_meta = src.stat()
        except OSError as exc:
            summary.failed += 1
            row = {
                "timestamp": run_ts,
                "source_path": str(src.resolve()),
                "dest_path": "",
                "file_type": kind,
                "size_bytes": "",
                "mtime": "",
                "action": "skip_stat",
                "status": "failed",
                "error": str(exc),
            }
            rows.append(row)
            note(f"FAIL stat src={src} err={exc}")
            continue

        size_bytes_str = str(st_meta.st_size)
        m_iso = iso_mtime(st_meta.st_mtime)
        goal_rel = canonical_rel(kind, src.name, st_meta.st_mtime)
        dest_abs = AIRDROP_ROOT / goal_rel

        if rel == goal_rel:
            summary.organized += 1
            rows.append(
                {
                    "timestamp": run_ts,
                    "source_path": str(src.resolve()),
                    "dest_path": str(dest_abs.resolve()),
                    "file_type": kind,
                    "size_bytes": size_bytes_str,
                    "mtime": m_iso,
                    "action": "already_ok",
                    "status": "ok",
                    "error": "",
                },
            )
            continue

        dest_unique = unique_dest_under_parent(dest_abs)
        summary.dest_dirs_preview.add(str(dest_unique.parent.resolve()))

        if not apply:
            summary.planned_or_moved += 1
            rows.append(
                {
                    "timestamp": run_ts,
                    "source_path": str(src.resolve()),
                    "dest_path": str(dest_unique.resolve()),
                    "file_type": kind,
                    "size_bytes": size_bytes_str,
                    "mtime": m_iso,
                    "action": "would_move",
                    "status": "dry_run",
                    "error": "",
                },
            )
            continue

        ok, detail = relocate_file(src.resolve(), dest_unique.resolve())
        if ok:
            summary.planned_or_moved += 1
            rows.append(
                {
                    "timestamp": run_ts,
                    "source_path": str(src.resolve()),
                    "dest_path": str(dest_unique.resolve()),
                    "file_type": kind,
                    "size_bytes": size_bytes_str,
                    "mtime": m_iso,
                    "action": "moved",
                    "status": "ok",
                    "error": detail,
                },
            )
            note(f"MOVED ok {src} -> {dest_unique} detail={detail!r}")
        else:
            summary.failed += 1
            rows.append(
                {
                    "timestamp": run_ts,
                    "source_path": str(src.resolve()),
                    "dest_path": str(dest_unique.resolve()),
                    "file_type": kind,
                    "size_bytes": size_bytes_str,
                    "mtime": m_iso,
                    "action": "move_failed",
                    "status": "failed",
                    "error": detail,
                },
            )
            note(f"FAIL move {src} planned={dest_unique} detail={detail}")

    with MANIFEST.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in CSV_COLS})

    print(f"manifest_written={MANIFEST}")

    mover_label = summary.planned_or_moved
    print("")
    print("=== airdrop_resort_summary ===")
    print(f"scanned_files={summary.scanned}")
    print(f"already_organized={summary.organized}")
    print(f"moves_{'applied' if apply else 'planned'}={mover_label}")
    print(f"skipped_blocked_subtrees={summary.skipped_subtrees}")
    print(f"failures={summary.failed}")
    print(f"type_counts video={summary.vid} image={summary.img} audio={summary.snd} other={summary.misc}")
    print(f"suspicious_keyword_hits≈test_family={summary.suspicious}")

    preview_dirs = sorted(summary.dest_dirs_preview)
    limit = min(40, len(preview_dirs))
    print("")
    print(f"planned_dest_dirs_sample({limit}/{len(preview_dirs)}):")
    for item in preview_dirs[:limit]:
        print(f"  {item}")

    if summary.suspicious_samples:
        print("")
        print("suspicious_path_samples:")
        for sample in summary.suspicious_samples:
            print(f"  {sample}")

    note(
        "END_SUMMARY scanned={scan} organized={org} mover={mv} subtree_skips={sub} failures={fail}".format(
            scan=summary.scanned,
            org=summary.organized,
            mv=mover_label,
            sub=summary.skipped_subtrees,
            fail=summary.failed,
        ),
    )
    return 0 if summary.failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Sort NYC_AUTO/raw/airdrop media into buckets.")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run (same as omitting flags). Never moves files.",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Perform moves respecting collision suffix + cross-volume copy semantics.",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run:
        print("ERROR: use either --dry-run or --apply, not both.", file=sys.stderr)
        return 2

    apply = bool(args.apply)

    if not AIRDROP_ROOT.exists():
        print(f"[warn] AIRDROP_ROOT missing: {AIRDROP_ROOT}", file=sys.stderr)

    return run_sort(apply=apply)


if __name__ == "__main__":
    raise SystemExit(main())
