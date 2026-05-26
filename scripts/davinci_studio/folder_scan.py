#!/usr/bin/env python3
"""Scan a folder for timeline video clips (.mov/.mp4/.m4v only).

Sort order: creation_time (birthtime when available), then filename, then mtime.
Never deletes or modifies source files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from paths import VIDEO_EXTS


@dataclass(frozen=True)
class ScannedClip:
    path: str
    filename: str
    size_bytes: int
    creation_time: float
    mtime: float
    sort_key: tuple


def _creation_time(path: Path) -> float:
    try:
        st = path.stat()
    except OSError:
        return 0.0
    birth = getattr(st, "st_birthtime", None)
    if birth is not None and birth > 0:
        return float(birth)
    return float(st.st_mtime)


def _append_clip(clips: list[ScannedClip], entry: Path) -> None:
    if not entry.is_file() or entry.name.startswith("._"):
        return
    if entry.suffix.lower() not in VIDEO_EXTS:
        return
    try:
        st = entry.stat()
        size = int(st.st_size)
        ctime = _creation_time(entry)
        mtime = float(st.st_mtime)
    except OSError:
        return
    clips.append(
        ScannedClip(
            path=str(entry.resolve()),
            filename=entry.name,
            size_bytes=size,
            creation_time=ctime,
            mtime=mtime,
            sort_key=(ctime, entry.name.lower(), mtime),
        )
    )


def scan_folder(folder: Path, *, max_depth: int = 8) -> list[ScannedClip]:
    """List videos under ``folder``, including nested subfolders (automation inbox layout)."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"input folder not found: {folder}")

    clips: list[ScannedClip] = []

    def _walk(dir_path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name.startswith("._"):
                continue
            if entry.is_dir():
                _walk(entry, depth + 1)
                continue
            _append_clip(clips, entry)

    _walk(folder, 0)
    clips.sort(key=lambda c: c.sort_key)
    return clips


def scan_folder_dict(folder: Path) -> dict:
    items = scan_folder(folder)
    return {
        "input_folder": str(folder.resolve()),
        "clip_count": len(items),
        "clips": [
            {
                "path": c.path,
                "filename": c.filename,
                "size_bytes": c.size_bytes,
                "creation_time": c.creation_time,
                "mtime": c.mtime,
            }
            for c in items
        ],
    }


def clips_payload_from_paths(
    paths: list[str],
    *,
    input_folder: Path | None = None,
) -> dict:
    """Build scan-shaped payload from explicit paths (order preserved)."""
    clips: list[dict] = []
    folder_str = str(input_folder.resolve()) if input_folder and input_folder.is_dir() else ""
    for raw in paths:
        p = Path(raw).expanduser().resolve()
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS or p.name.startswith("._"):
            continue
        try:
            st = p.stat()
            size = int(st.st_size)
            ctime = _creation_time(p)
            mtime = float(st.st_mtime)
        except OSError:
            continue
        clips.append(
            {
                "path": str(p),
                "filename": p.name,
                "size_bytes": size,
                "creation_time": ctime,
                "mtime": mtime,
            }
        )
    if not folder_str and clips:
        folder_str = str(Path(clips[0]["path"]).parent)
    return {
        "input_folder": folder_str,
        "clip_count": len(clips),
        "clips": clips,
        "selection_mode": "explicit_paths",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", type=Path, help="Folder to scan")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()
    try:
        payload = scan_folder_dict(args.folder)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    text = json.dumps(payload, indent=2) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
