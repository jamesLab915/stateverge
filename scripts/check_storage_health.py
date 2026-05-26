#!/usr/bin/env python3
"""Report mounted logical volumes, free space, writability, and light I/O probes."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import (  # noqa: E402
    get_stateverge_volume,
    get_sv_archive,
    get_sv_backup,
    get_sv_cache,
    get_sv_transfer,
    resolve_ts_input_root,
    summarize_targets,
    write_probe_file,
)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s %(message)s",
)
log = logging.getLogger("check_storage_health")


def _human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024.0 or unit == "TiB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"


def check_one(label: str, root: Path, *, dry_run: bool) -> None:
    log.info("---- %s ----", label)
    log.info("path: %s", root)
    exists = root.is_dir()
    log.info("exists: %s", exists)
    if not exists:
        log.warning("skip disk usage: path not a directory")
        log.info("writable_probe: skipped")
        return
    try:
        usage = shutil.disk_usage(root)
        log.info("free: %s / total: %s", _human(usage.free), _human(usage.total))
    except OSError as exc:
        log.warning("disk_usage failed: %s", exc)
    ok = write_probe_file(root, dry_run=dry_run)
    log.info("writable_probe: %s", ok)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Skip write probes.")
    args = ap.parse_args()

    log.info("=== resolved targets ===")
    for k, v in summarize_targets(verbose=True).items():
        log.info("  %s=%s", k, v)

    ts = resolve_ts_input_root(verbose=True)
    check_one("SV_CACHE", get_sv_cache(verbose=True), dry_run=args.dry_run)
    check_one("SV_TRANSFER", get_sv_transfer(verbose=True), dry_run=args.dry_run)
    check_one("SV_BACKUP", get_sv_backup(verbose=True), dry_run=args.dry_run)
    check_one("SV_ARCHIVE", get_sv_archive(verbose=True), dry_run=args.dry_run)
    check_one("STATEVERGE_VOL", get_stateverge_volume(verbose=True), dry_run=args.dry_run)
    if ts:
        check_one("STATEVERGE_TS_INPUT", ts, dry_run=args.dry_run)
    else:
        log.info("---- STATEVERGE_TS_INPUT ----")
        log.info("(unset)")

    log.info("health check complete (dry_run=%s)", args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
