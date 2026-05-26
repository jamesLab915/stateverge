#!/usr/bin/env python3
"""Move NYC_AUTO paths matching synthetic/test markers into quarantine/bad_outputs.

Default preview mode lists planned mirrors under ``bad_outputs``. Use ``--apply`` to relocate
matching files never deleting them. Mirrors preserve subpaths rooted at each scanned tree.
Writes matching operations to SSD + repo twin logs ``quarantine_YYYY-MM-DD.log``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import (  # noqa: E402
    LOCAL_LOG_DIR,
    NYC_LOGS,
    NYC_ROOT,
    OUT_PACKAGES,
    OUT_SHORTS,
    PROJECTS,
    CACHE as NYC_CACHE,
    path_matches_test_asset_marker,
)

QUAR_PARENT = NYC_ROOT / "quarantine" / "bad_outputs"
QUARANTINE_ROOT = NYC_ROOT / "quarantine"

SCAN_TARGETS: tuple[tuple[str, Path], ...] = (
    ("projects", PROJECTS),
    ("packages", OUT_PACKAGES),
    ("shorts", OUT_SHORTS),
    ("cache", NYC_CACHE),
)


def is_inside_quarantine(path: Path) -> bool:
    try:
        path.resolve().relative_to(QUARANTINE_ROOT.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        try:
            str(path.resolve()).startswith(str(QUARANTINE_ROOT.resolve()))
        except OSError:
            return False
        return False


def is_already_mirror(path: Path) -> bool:
    try:
        path.resolve().relative_to(QUAR_PARENT.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        try:
            return str(path.resolve()).startswith(str(QUAR_PARENT.resolve()))
        except OSError:
            return False


def stamp_unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return dest.with_name(f"{dest.stem}__{ts}{dest.suffix}")


def destination_for(scan_label: str, scan_root: Path, src_file: Path) -> Path:
    rel = src_file.relative_to(scan_root)
    return QUAR_PARENT / scan_label / rel


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_dual_logs(lines: list[str]) -> None:
    dated = datetime.now().strftime("%Y-%m-%d")
    sd_path = NYC_LOGS / f"quarantine_{dated}.log"
    rr_path = LOCAL_LOG_DIR / f"quarantine_{dated}.log"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    LOCAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        NYC_LOGS.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    blob = "".join(f"[{stamp}] {ln}\n" for ln in lines)

    for target in (sd_path, rr_path):
        try:
            with target.open("a", encoding="utf-8") as fh:
                fh.write(blob)
        except OSError as exc:
            print(f"[quarantine_bad_test_outputs][warn] log_failed {target}: {exc}", file=sys.stderr)


def gather_files(scan_root: Path) -> list[Path]:
    out: list[Path] = []
    if not scan_root.is_dir():
        return out
    try:
        for candidate in scan_root.rglob("*"):
            try:
                if not candidate.is_file():
                    continue
            except OSError:
                continue
            if candidate.name.startswith("._"):
                continue
            if candidate.name == ".DS_Store":
                continue
            if is_inside_quarantine(candidate) or is_already_mirror(candidate):
                continue
            out.append(candidate)
    except OSError as exc:
        print(f"[quarantine_bad_test_outputs][warn] walk_failed {scan_root}: {exc}", file=sys.stderr)
    return sorted(out, key=lambda p: len(str(p)), reverse=True)


def marker_hit(src: Path) -> bool:
    return path_matches_test_asset_marker(src.as_posix(), src.name)


def run_move(scan_label: str, scan_root: Path, src: Path, apply: bool) -> str:
    dest_primary = destination_for(scan_label, scan_root, src)
    dest_final = stamp_unique(dest_primary)

    if apply:
        try:
            ensure_parent(dest_final)
            shutil.move(str(src), str(dest_final))
            append_dual_logs(
                [
                    f"MOVED label={scan_label} src={src}",
                    f"DEST_PRIMARY={dest_primary}",
                    f"DEST_FINAL={dest_final}",
                ],
            )
            print(f"[APPLY] {src}\n       -> {dest_final}")
            return "moved"
        except OSError as exc:
            append_dual_logs(
                [
                    f"FAILED label={scan_label} src={src}",
                    f"PRIMARY={dest_primary} err={exc}",
                ],
            )
            print(f"[ERROR] FAILED move src={src} err={exc}", file=sys.stderr)
            return "move_failed"

    append_dual_logs(
        [
            f"DRY-RUN label={scan_label}",
            f"src={src}",
            f"planned_dest_primary={dest_primary}",
            f"planned_dest_final={dest_final}",
        ],
    )
    print(f"[DRY-RUN] {src}\n          -> {dest_final}")
    return "planned"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Relocate flagged synthetic/test artefacts into NYC_AUTO/quarantine/bad_outputs.",
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Only log + print mirrors (implicit default when omitting flags).",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Actually move flagged files preserving mirrored layout.",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run:
        print("ERROR: use either --dry-run or --apply, not both.", file=sys.stderr)
        return 2

    apply = bool(args.apply)
    QUAR_PARENT.mkdir(parents=True, exist_ok=True)

    inspected = 0
    flagged = 0
    moved_ok = 0
    failures = 0

    for scan_label, root in SCAN_TARGETS:
        sources = gather_files(root)
        for src in sources:
            inspected += 1
            if not marker_hit(src):
                continue
            flagged += 1
            status = run_move(scan_label, root, src, apply=apply)
            if status == "moved":
                moved_ok += 1
            elif status == "move_failed":
                failures += 1

    summary = (
        f"SUMMARY inspected={inspected} flagged={flagged} apply={apply} "
        f"moved={moved_ok} failures={failures}"
    )
    print(f"=== {summary} ===")
    append_dual_logs([summary])

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
