#!/usr/bin/env python3
"""Ingest long videos (>=180s) from NYC airdrop raw: register projects, no file moves."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import (  # noqa: E402
    LONG_VIDEO_LIB,
    NYC_ROOT,
    RAW_VIDEO,
    ensure_nyc_dirs,
    ensure_project_layout,
    index_csv_path,
    log_lines,
    nyc_daily_log,
    run_ffprobe,
    slug_filename,
    stable_project_id,
    summarize_probe,
)

MIN_LONG_SEC = 180.0
NOTE_TEMPLATE = """NYC_AUTO V1 project notes
-------------------------
Edit freely. Source is never modified by automation.
"""


def load_index_paths() -> set[str]:
    idx = index_csv_path()
    out: set[str] = set()
    if not idx.is_file():
        return out
    try:
        with idx.open(newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                sp = (row.get("source_path") or "").strip()
                if sp:
                    out.add(sp)
    except OSError:
        pass
    return out


def append_index(row: dict[str, str]) -> None:
    LONG_VIDEO_LIB.mkdir(parents=True, exist_ok=True)
    idx = index_csv_path()
    new_file = not idx.is_file()
    fields = [
        "project_id",
        "source_path",
        "imported_at",
        "duration",
        "width",
        "height",
        "has_audio",
        "status",
    ]
    try:
        with idx.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if new_file:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in fields})
    except OSError:
        pass


def iter_video_files(root: Path):
    if not root.is_dir():
        return
    try:
        for p in root.rglob("*"):
            try:
                if not p.is_file() or p.name.startswith("."):
                    continue
                if p.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
                    continue
                yield p
            except OSError:
                continue
    except OSError:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="NYC ingest long videos into library index + projects.")
    parser.parse_args()

    log_nyc = nyc_daily_log("nyc_ingest")
    if not NYC_ROOT.is_dir():
        log_lines("nyc_ingest", ["ERROR: NYC_ROOT not available (is StateVerge SSD mounted?)"], log_nyc)
        return 2

    ensure_nyc_dirs()
    registered = load_index_paths()
    log_lines("nyc_ingest", ["========== ingest start =========="], log_nyc)

    count_new = 0
    count_skip = 0

    for src in iter_video_files(RAW_VIDEO):
        try:
            resolved = src.resolve()
            sp = str(resolved)
        except OSError:
            continue

        if sp in registered:
            count_skip += 1
            continue

        probe_raw = run_ffprobe(resolved)
        if not probe_raw:
            log_lines("nyc_ingest", [f"SKIP ffprobe_failed {sp}"], log_nyc)
            continue

        info = summarize_probe(probe_raw)
        dur = float(info["duration"])
        if dur < MIN_LONG_SEC:
            log_lines("nyc_ingest", [f"SKIP not_long_enough dur={dur:.1f}s {sp}"], log_nyc)
            continue

        try:
            mtime = resolved.stat().st_mtime
        except OSError:
            mtime = 0.0

        pid = stable_project_id(resolved, mtime)
        pdir = ensure_project_layout(pid)
        if (pdir / "source.json").is_file():
            registered.add(sp)
            count_skip += 1
            log_lines("nyc_ingest", [f"SKIP already_project {pid}"], log_nyc)
            continue

        imported_at = datetime.now().isoformat(timespec="seconds")
        source_payload = {
            "source_path": sp,
            "project_id": pid,
            "imported_at": imported_at,
            "duration": dur,
            "width": int(info["width"]),
            "height": int(info["height"]),
            "has_audio": bool(info["has_audio"]),
            "status": "ingested",
        }

        try:
            (pdir / "source.json").write_text(
                json.dumps(source_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (pdir / "ffprobe.json").write_text(
                json.dumps(probe_raw, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (pdir / "notes.txt").write_text(NOTE_TEMPLATE, encoding="utf-8")
        except OSError as exc:
            log_lines("nyc_ingest", [f"FAIL write project {pid}: {exc}"], log_nyc)
            continue

        append_index(
            {
                "project_id": pid,
                "source_path": sp,
                "imported_at": imported_at,
                "duration": f"{dur:.2f}",
                "width": str(info["width"]),
                "height": str(info["height"]),
                "has_audio": str(bool(info["has_audio"])).lower(),
                "status": "ingested",
            }
        )
        registered.add(sp)
        count_new += 1
        log_lines("nyc_ingest", [f"OK registered {pid} ({slug_filename(resolved.name)})"], log_nyc)

    log_lines(
        "nyc_ingest",
        [f"========== ingest done new={count_new} skipped_or_short={count_skip} =========="],
        log_nyc,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
