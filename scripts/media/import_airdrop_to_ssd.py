#!/usr/bin/env python3
"""
StateVerge: import recent AirDrop/media files from ~/Downloads onto SV_CACHE.

Default destination: ``SV_CACHE/inbox`` (see ``utils.storage_paths``).
Set ``AIRDROP_USE_LEGACY_NYC=1`` to restore ``NYC_AUTO/raw/airdrop`` on the project SSD.

Fail-open: errors on one file do not stop the run. Never touches non-Downloads deletes except after verified copy.
"""
from __future__ import annotations

import csv
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

_SCRIPTS = Path(__file__).resolve().parent.parent
_REPO = _SCRIPTS.parent
_SRC = _REPO / "src"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stateverge_paths import CODE_ROOT, NYC_ROOT, SSD_ROOT  # noqa: E402
from utils.storage_paths import get_sv_cache  # noqa: E402

STATEVERGE_ROOT = CODE_ROOT
DOWNLOADS = Path.home() / "Downloads"
NYC = NYC_ROOT
SSD_LOG_DIR = NYC / "logs"
LOCAL_LOG_DIR = STATEVERGE_ROOT / "logs" / "system"
MANIFEST = CODE_ROOT / "logs" / "ingest" / "airdrop_import_manifest.csv"


def _use_legacy_airdrop() -> bool:
    return os.environ.get("AIRDROP_USE_LEGACY_NYC", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _ingest_airdrop_base() -> Path:
    """New workflow: ``SV_CACHE/inbox``; legacy: ``NYC_AUTO/raw/airdrop``."""
    if _use_legacy_airdrop():
        return NYC / "raw" / "airdrop"
    return get_sv_cache() / "inbox"

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac"}
ALL_EXT = VIDEO_EXT | IMAGE_EXT | AUDIO_EXT

RECENT_SECS = 7 * 24 * 3600


def log_line(path: Path, msg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def local_log_today() -> Path:
    d = datetime.now().strftime("%Y-%m-%d")
    return LOCAL_LOG_DIR / f"airdrop_import_{d}.log"


def ensure_ssd_tree() -> None:
    base = _ingest_airdrop_base()
    sub = [
        base / "video",
        base / "image",
        base / "audio",
        base / "other",
        SSD_LOG_DIR,
        NYC / "quarantine",
        MANIFEST.parent,
    ]
    for p in sub:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def time_bucket(hour: int) -> str:
    # 05:00-11:00 morning; 11:00-16:00 day; 16:00-19:00 golden_hour; 19:00-05:00 night
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 16:
        return "day"
    if 16 <= hour < 19:
        return "golden_hour"
    return "night"


def classify(ext: str) -> str:
    e = ext.lower()
    if e in VIDEO_EXT:
        return "video"
    if e in IMAGE_EXT:
        return "image"
    if e in AUDIO_EXT:
        return "audio"
    return "other"


def dest_dir_for(file_type: str, m: datetime) -> Path:
    y = m.strftime("%Y")
    ym = m.strftime("%Y-%m")
    dd = m.strftime("%d")
    base = _ingest_airdrop_base()
    if file_type == "video":
        bucket = time_bucket(m.hour)
        return base / "video" / y / ym / dd / bucket
    if file_type == "image":
        return base / "image" / y / ym / dd
    if file_type == "audio":
        return base / "audio" / y / ym / dd
    return base / "other" / y / ym / dd


def unique_destination(dest_dir: Path, original_name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidate = dest_dir / original_name
    if not candidate.exists():
        return candidate
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix
    n = 1
    while True:
        tag = f"_{n:03d}"
        cand = dest_dir / f"{stem}{tag}{suffix}"
        if not cand.exists():
            return cand
        n += 1
        if n > 9999:
            cand = dest_dir / f"{stem}_{int(time.time())}{suffix}"
            return cand


def append_manifest_row(
    manifest_path: Path,
    imported_at: str,
    source: str,
    dest: str,
    file_type: str,
    size_bytes: int,
    mtime_str: str,
    status: str,
    error: str,
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not manifest_path.exists()
    try:
        with manifest_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(
                    [
                        "imported_at",
                        "source_path",
                        "dest_path",
                        "file_type",
                        "size_bytes",
                        "mtime",
                        "status",
                        "error",
                    ]
                )
            w.writerow(
                [
                    imported_at,
                    source,
                    dest,
                    file_type,
                    str(size_bytes),
                    mtime_str,
                    status,
                    error,
                ]
            )
    except OSError as exc:
        log_line(local_log_today(), f"WARN: manifest write failed: {exc}")


def safe_import_one(src: Path) -> None:
    log = local_log_today()
    imported_at = datetime.now().isoformat(timespec="seconds")
    try:
        st = src.stat()
    except OSError as exc:
        append_manifest_row(
            MANIFEST,
            imported_at,
            str(src),
            "",
            "",
            0,
            "",
            "stat_fail",
            str(exc),
        )
        log_line(log, f"SKIP stat_fail {src}: {exc}")
        return

    ext = src.suffix
    if ext.lower() not in ALL_EXT:
        return

    mtime = datetime.fromtimestamp(st.st_mtime)
    mtime_str = mtime.isoformat(timespec="seconds")
    size = st.st_size
    ftype = classify(ext)
    dest_dir = dest_dir_for(ftype, mtime)
    dest_final = unique_destination(dest_dir, src.name)
    dest_part = dest_final.with_name(dest_final.name + ".part")

    try:
        shutil.copy2(src, dest_part)
    except OSError as exc:
        append_manifest_row(
            MANIFEST,
            imported_at,
            str(src),
            str(dest_final),
            ftype,
            size,
            mtime_str,
            "copy_fail",
            str(exc),
        )
        log_line(log, f"FAIL copy { src } -> {dest_part}: {exc}")
        if dest_part.exists():
            try:
                dest_part.unlink()
            except OSError:
                pass
        return

    try:
        part_sz = dest_part.stat().st_size
        if part_sz != size:
            raise OSError(f"size_mismatch src={size} part={part_sz}")
        os.replace(dest_part, dest_final)
    except OSError as exc:
        append_manifest_row(
            MANIFEST,
            imported_at,
            str(src),
            str(dest_final),
            ftype,
            size,
            mtime_str,
            "verify_fail",
            str(exc),
        )
        log_line(log, f"FAIL verify/rename {src}: {exc}")
        if dest_part.exists():
            try:
                dest_part.unlink()
            except OSError:
                pass
        if dest_final.exists() and dest_final.stat().st_size == 0:
            try:
                dest_final.unlink()
            except OSError:
                pass
        return

    try:
        src.unlink()
    except OSError as exc:
        append_manifest_row(
            MANIFEST,
            imported_at,
            str(src),
            str(dest_final),
            ftype,
            size,
            mtime_str,
            "delete_source_fail",
            str(exc),
        )
        log_line(log, f"WARN copied ok but could not delete source {src}: {exc}")
        return

    append_manifest_row(
        MANIFEST,
        imported_at,
        str(src),
        str(dest_final),
        ftype,
        size,
        mtime_str,
        "ok",
        "",
    )
    log_line(log, f"OK {src} -> {dest_final}")


def iter_candidates(root: Path, now: float) -> Iterable[Path]:
    if not root.is_dir():
        return
    try:
        for entry in root.iterdir():
            try:
                if entry.is_symlink():
                    continue
                if not entry.is_file():
                    continue
                st = entry.stat()
                if now - st.st_mtime > RECENT_SECS:
                    continue
                if entry.suffix.lower() not in ALL_EXT:
                    continue
                yield entry
            except OSError:
                continue
    except OSError:
        return


def main() -> int:
    LOCAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = local_log_today()
    log_line(log, "========== AirDrop import run started ==========")
    log_line(
        log,
        f"ingest_base={_ingest_airdrop_base()} legacy_nyc={_use_legacy_airdrop()} "
        f"sv_cache={get_sv_cache()} manifest={MANIFEST}",
    )

    if _use_legacy_airdrop():
        if not SSD_ROOT.is_dir():
            print(
                "ERROR: AIRDROP_USE_LEGACY_NYC requires StateVerge SSD mounted",
                file=sys.stderr,
            )
            log_line(log, "ERROR: legacy ingest aborted (SSD not mounted)")
            return 2

    ensure_ssd_tree()
    now = time.time()
    count = 0
    for f in iter_candidates(DOWNLOADS, now):
        try:
            safe_import_one(f)
            count += 1
        except Exception as exc:  # noqa: BLE001 fail-open
            log_line(log, f"UNEXPECTED {f}: {exc}")
            append_manifest_row(
                MANIFEST,
                datetime.now().isoformat(timespec="seconds"),
                str(f),
                "",
                "",
                0,
                "",
                "exception",
                str(exc),
            )

    log_line(log, f"========== AirDrop import finished (candidates_tried={count}) ==========")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
