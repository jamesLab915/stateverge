#!/usr/bin/env python3
"""Read-only stats for ``media_index_v3.json`` (no indexing, no media moves)."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from storage_paths import get_sv_transfer  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")


def default_index_path() -> Path:
    return get_sv_transfer(verbose=False) / "media_index" / "media_index_v3.json"


def _is_ferry(it: dict[str, Any]) -> bool:
    pl = str(it.get("path") or "").lower()
    st = [str(x).lower() for x in (it.get("scene_type") or []) if x]
    if "ferry" in pl or "staten" in pl or "whitehall" in pl or "st_george" in pl:
        return True
    return any("ferry" in x for x in st)


def _is_skyline(it: dict[str, Any]) -> bool:
    pl = str(it.get("path") or "").lower()
    st = [str(x).lower() for x in (it.get("scene_type") or []) if x]
    if any(k in pl for k in ("skyline", "dumbo", "esb", "empire")):
        return True
    return any("skyline" in x for x in st)


def _is_night(it: dict[str, Any]) -> bool:
    if str(it.get("time_of_day") or "").lower() == "night":
        return True
    pl = str(it.get("path") or "").lower()
    return "night" in pl or "nocturnal" in pl


def compute_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    dur = 0.0
    boroughs: Counter[str] = Counter()
    for it in items:
        try:
            dur += float(it.get("duration_sec") or it.get("duration") or 0.0)
        except (TypeError, ValueError):
            pass
        bor = str(it.get("borough") or "").strip() or "unknown"
        boroughs[bor] += 1
    return {
        "total_indexed": total,
        "total_hours": round(dur / 3600.0, 4),
        "borough_counts": dict(sorted(boroughs.items(), key=lambda kv: (-kv[1], kv[0]))),
        "ferry_clips": sum(1 for it in items if _is_ferry(it)),
        "skyline_clips": sum(1 for it in items if _is_skyline(it)),
        "night_clips": sum(1 for it in items if _is_night(it)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", type=Path, default=default_index_path(), help="Path to media_index_v3.json")
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    args = ap.parse_args()
    p = args.index.expanduser()
    if not p.is_file():
        msg = {"ok": False, "error": "index_missing", "path": str(p)}
        print(json.dumps(msg, ensure_ascii=False, indent=2))
        return 1
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": repr(exc), "path": str(p)}, ensure_ascii=False))
        return 1
    raw = data.get("items") if isinstance(data, dict) else []
    items = [x for x in raw if isinstance(x, dict)]
    stats = compute_stats(items)
    out = {
        "ok": True,
        "index_path": str(p),
        "schema_version": data.get("schema_version") if isinstance(data, dict) else None,
        "generated_at": data.get("generated_at") if isinstance(data, dict) else None,
        **stats,
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
