#!/usr/bin/env python3
"""Create standard SV_CACHE / SV_TRANSFER / SV_BACKUP directories (non-destructive).

Never deletes or moves existing files; only ``mkdir -p`` for known layout roots.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import (  # noqa: E402
    get_sv_backup,
    get_sv_cache,
    get_sv_transfer,
    summarize_targets,
)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s %(message)s",
)
log = logging.getLogger("init_storage_layout")

SV_CACHE_LAYOUT = (
    "inbox",
    "active_projects",
    "audio_clean",
    "ffmpeg_cache",
    "whisper_cache",
    "runway_exports",
    "renders",
    "uploads",
    "temp",
    # DaVinci pre-publish layer (mkdir only; never moves/deletes media)
    "davinci_inbox",
    "davinci_projects",
    "davinci_exports",
    "davinci_done",
)

SV_TRANSFER_LAYOUT = (
    "selected_clips",
    "ready_to_edit",
    "ready_to_upload",  # DaVinci masters land here before publish gate / upload
)

SV_BACKUP_LAYOUT = (
    "project_backups",
    "exports_backup",
    "tracking",
    "important_docs",
)


def ensure_under(root: Path, names: tuple[str, ...], *, dry_run: bool) -> list[Path]:
    created: list[Path] = []
    for name in names:
        p = root / name
        if dry_run:
            log.info("[dry-run] would mkdir %s", p)
            continue
        try:
            p.mkdir(parents=True, exist_ok=True)
            created.append(p)
            log.info("ok mkdir %s", p)
        except OSError as exc:
            log.warning("skip mkdir %s: %s", p, exc)
    return created


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without creating directories.",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Alias for DEBUG logging (default is already verbose).",
    )
    args = ap.parse_args()
    if args.verbose:
        log.setLevel(logging.DEBUG)

    log.info("=== storage targets ===")
    for k, v in summarize_targets(verbose=True).items():
        log.info("  %s=%s", k, v)

    cache_root = get_sv_cache(verbose=True)
    xfer_root = get_sv_transfer(verbose=True)
    backup_root = get_sv_backup(verbose=True)

    log.info("--- SV_CACHE layout ---")
    ensure_under(cache_root, SV_CACHE_LAYOUT, dry_run=args.dry_run)
    log.info("--- SV_TRANSFER layout ---")
    ensure_under(xfer_root, SV_TRANSFER_LAYOUT, dry_run=args.dry_run)
    log.info("--- SV_BACKUP layout ---")
    ensure_under(backup_root, SV_BACKUP_LAYOUT, dry_run=args.dry_run)

    log.info("done (dry_run=%s)", args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
