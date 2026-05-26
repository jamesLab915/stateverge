#!/usr/bin/env python3
"""Inventory NYC_AUTO media under raw, projects, and output → CSV (volume + repo mirror)."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import NYC_ROOT, path_matches_test_asset_marker  # noqa: E402


STATEVERGE_LOG = Path.home() / "StateVerge" / "logs" / "system"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".ts", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac"}
PACKAGE_EXTS = {".json", ".txt", ".csv"}


SCAN_ROOTS: tuple[Path, ...] = (
    NYC_ROOT / "raw",
    NYC_ROOT / "raw" / "airdrop",
    NYC_ROOT / "projects",
    NYC_ROOT / "output",
)

OUT_VOLUME = NYC_ROOT / "logs" / "asset_inventory.csv"
OUT_REPO_MIRR = STATEVERGE_LOG / "asset_inventory.csv"


def file_type_from_suffix(path: Path) -> str:
    suf = path.suffix.lower()
    if suf in VIDEO_EXTS:
        return "video"
    if suf in IMAGE_EXTS:
        return "image"
    if suf in AUDIO_EXTS:
        return "audio"
    if suf in PACKAGE_EXTS:
        return "package"
    return "other"


def iso_mtime(st_mtime: float) -> str:
    return datetime.fromtimestamp(st_mtime).isoformat(timespec="seconds")


def probe_video(path: Path) -> dict[str, Any]:
    """Return ffprobe-derived fields using JSON output (stdlib subprocess + json)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=180)
        data = json.loads(out.decode("utf-8", errors="replace"))
    except Exception:
        return {
            "duration": "",
            "width": "",
            "height": "",
            "has_audio": "",
            "codec": "",
            "status": "ffprobe_failed",
        }

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = ""
    dur_raw = fmt.get("duration") or (v.get("duration") if v else None)
    try:
        if dur_raw not in (None, ""):
            duration = str(float(dur_raw))
    except (TypeError, ValueError):
        duration = ""

    width = height = codec = ""
    if v:
        try:
            w = int(v.get("width") or 0)
            h = int(v.get("height") or 0)
            width = str(w) if w else ""
            height = str(h) if h else ""
        except (TypeError, ValueError):
            pass
        codec = str(v.get("codec_name") or "")

    has_audio = ""
    if a is not None:
        has_audio = "true"

    status = "ok"
    if not v:
        status = "no_video_stream"

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "has_audio": has_audio,
        "codec": codec,
        "status": status if status != "ok" else _append_codec_status("ok", codec, a),
    }


def _append_codec_status(st: str, vcodec: str, aud: Optional[dict[str, Any]]) -> str:
    ac = ""
    if aud:
        ac = str(aud.get("codec_name") or "")
    if vcodec or ac:
        parts = []
        if vcodec:
            parts.append(f"v={vcodec}")
        if ac:
            parts.append(f"a={ac}")
        return ";".join([st] + parts)
    return st


def unique_scan_roots(roots: tuple[Path, ...]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        try:
            key = str(r.resolve())
        except OSError:
            key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def iter_inventory_files(
    *,
    exclude_names: tuple[str, ...],
) -> list[Path]:
    """Collect files once per path (dedupe) under existing scan roots."""
    found: dict[str, Path] = {}

    def should_skip(rel_name: str) -> bool:
        base = Path(rel_name).name
        if base.startswith("._"):
            return True
        if base == ".DS_Store":
            return True
        return False

    for root in unique_scan_roots(roots=SCAN_ROOTS):
        try:
            if not root.exists():
                continue
        except OSError:
            continue
        try:
            for p in root.rglob("*"):
                try:
                    if not p.is_file():
                        continue
                except OSError:
                    continue
                if should_skip(str(p)):
                    continue
                if "quarantine" in p.parts:
                    continue
                try:
                    k = str(p.resolve())
                except OSError:
                    k = str(p)
                found[k] = p
        except OSError:
            continue

    return list(found.values())


def write_inventory_rows(rows: list[dict[str, str]]) -> None:
    OUT_VOLUME.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPO_MIRR.parent.mkdir(parents=True, exist_ok=True)
    cols = (
        "scanned_at",
        "file_path",
        "file_type",
        "size_bytes",
        "mtime",
        "duration",
        "width",
        "height",
        "has_audio",
        "is_test_asset",
        "status",
    )
    for outpath in (OUT_VOLUME, OUT_REPO_MIRR):
        with outpath.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in rows:
                w.writerow({c: row.get(c, "") for c in cols})


def row_for_path(scanned_at: str, path: Path) -> dict[str, str]:
    st = path.stat()
    ft = file_type_from_suffix(path)
    is_test = "true" if path_matches_test_asset_marker(str(path), path.name) else "false"
    dur = mw = mh = ha = ""

    probe_status = ""

    status = ""

    if ft == "video":
        pr = probe_video(path)
        dur = pr.get("duration") or ""
        mw = pr.get("width") or ""
        mh = pr.get("height") or ""
        ha = pr.get("has_audio") or ""
        status = pr.get("status") or ""

    elif ft == "package":
        status = "package_meta"

    elif ft == "other":
        status = "misc"

    else:
        status = "media"

    if not status:
        status = probe_status if probe_status else "ok"

    return {
        "scanned_at": scanned_at,
        "file_path": str(path.resolve()),
        "file_type": ft,
        "size_bytes": str(st.st_size),
        "mtime": iso_mtime(st.st_mtime),
        "duration": dur,
        "width": mw,
        "height": mh,
        "has_audio": ha,
        "is_test_asset": is_test,
        "status": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build NYC_AUTO asset inventory CSV.")
    parser.parse_args()

    scanned_at = datetime.now().isoformat(timespec="seconds")
    rows: list[dict[str, str]] = []

    paths = sorted(
        iter_inventory_files(exclude_names=()),
        key=lambda p: str(p).lower(),
    )

    print(f"No volume access for NYC_ROOT?" if not NYC_ROOT.exists() else f"ROOT {NYC_ROOT}")
    print(f"Scan roots: {[str(r) for r in SCAN_ROOTS]}")
    print(f"Found {len(paths)} unique files.")

    for p in paths:
        try:
            rows.append(row_for_path(scanned_at, p))
        except OSError as exc:
            rows.append(
                {
                    "scanned_at": scanned_at,
                    "file_path": str(p),
                    "file_type": "other",
                    "size_bytes": "",
                    "mtime": "",
                    "duration": "",
                    "width": "",
                    "height": "",
                    "has_audio": "",
                    "is_test_asset": "false",
                    "status": f"stat_error:{exc}",
                }
            )

    write_inventory_rows(rows)
    print(f"Wrote {OUT_VOLUME}")
    print(f"Wrote {OUT_REPO_MIRR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
