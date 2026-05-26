#!/usr/bin/env python3
"""Diagnose Envato music libraries for nyc_long vs shorts (separate status JSON per channel)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_SCRIPT_DIR = Path(__file__).resolve().parent
_SV_SRC = Path.home() / "StateVerge" / "src"
for _p in (_SCRIPT_DIR, _SV_SRC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".flac", ".m4a"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _xfer() -> Path:
    try:
        from utils.storage_paths import get_sv_transfer  # type: ignore

        return get_sv_transfer(verbose=False)
    except Exception:
        return Path("/Volumes/SV_TRANSFER")


def music_paths(channel: str) -> dict[str, Path]:
    root = _xfer() / "04_AUDIO" / "music" / channel
    return {
        "channel": channel,
        "music_root": root,
        "metadata_dir": root / "metadata",
        "index_path": root / "metadata" / "music_index.json",
        "status_path": root / "metadata" / "music_library_status.json",
        "rejected_dir": root / "rejected",
    }


def load_index(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"tracks": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict) and isinstance(data.get("tracks"), list):
            return data
    except Exception:
        pass
    return {"tracks": []}


def count_rejected(rejected_dir: Path) -> int:
    if not rejected_dir.is_dir():
        return 0
    n = 0
    try:
        for p in rejected_dir.iterdir():
            if p.name.startswith("."):
                continue
            if p.name.endswith(".reason.txt"):
                continue
            n += 1
    except OSError:
        return 0
    return n


def count_library_audio_files(music_root: Path, channel: str) -> int:
    cats = (
        ("ambient", "calm_piano", "cinematic", "dark_documentary", "night_drive", "archive")
        if channel == "nyc_long"
        else ("upbeat", "cinematic", "urban", "dramatic", "luxury", "cyberpunk", "archive")
    )
    n = 0
    for cat in cats:
        d = music_root / cat
        if not d.is_dir():
            continue
        try:
            for p in d.iterdir():
                if not p.is_file():
                    continue
                if p.name.startswith("._"):
                    continue
                if p.suffix.lower() in AUDIO_EXTS and "_stem_" not in p.name:
                    n += 1
        except OSError:
            continue
    return n


def _track_sort_key(t: dict[str, Any]) -> str:
    return str(t.get("imported_at") or "")


def diagnose_channel(channel: str) -> dict[str, Any]:
    mp = music_paths(channel)
    mp["metadata_dir"].mkdir(parents=True, exist_ok=True)
    idx = load_index(mp["index_path"])
    tracks: list[dict[str, Any]] = [x for x in idx["tracks"] if isinstance(x, dict)]
    license_valid_count = sum(1 for t in tracks if t.get("license_valid") is True)
    missing_license_count = sum(1 for t in tracks if not t.get("license_valid"))
    sorted_tracks = sorted(tracks, key=_track_sort_key, reverse=True)
    latest = sorted_tracks[:8]
    slim_latest: list[dict[str, Any]] = []
    for t in latest:
        slim_latest.append(
            {
                "track_id": t.get("track_id"),
                "filename": t.get("filename"),
                "category": t.get("category"),
                "imported_at": t.get("imported_at"),
                "license_valid": t.get("license_valid"),
            }
        )
    on_disk_main = count_library_audio_files(mp["music_root"], channel)
    report = {
        "music_channel": channel,
        "generated_at": _utc_iso(),
        "index_path": str(mp["index_path"]),
        "total_tracks": len(tracks),
        "on_disk_main_audio_approx": on_disk_main,
        "license_valid_count": license_valid_count,
        "missing_license_count": missing_license_count,
        "rejected_count": count_rejected(mp["rejected_dir"]),
        "latest_imported_tracks": slim_latest,
    }
    tmp = mp["status_path"].with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(mp["status_path"])
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Envato music library diagnose (per channel).")
    ap.add_argument("--channel", choices=("nyc_long", "shorts", "all"), required=True)
    ns = ap.parse_args()
    order: Iterable[str] = ("nyc_long", "shorts") if ns.channel == "all" else (ns.channel,)
    out: dict[str, Any] = {"ok": True, "channel": ns.channel, "reports": {}}
    for ch in order:
        out["reports"][ch] = diagnose_channel(ch)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
