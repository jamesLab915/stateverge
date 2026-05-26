"""Fallback levels L1–L6 for long and shorts (plan-only)."""

from __future__ import annotations

import re
from typing import Any

# Long content priority (Always Deliver v2)
LONG_CONTENT_PRIORITY: tuple[str, ...] = (
    "clean_real_sound",
    "ferry_real_ambience",
    "driving_music_first",
    "skyline_sequence",
)

_PRIORITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("clean_real_sound", re.compile(r"clean_real_sound|realsound|real_sound", re.I)),
    ("ferry_real_ambience", re.compile(r"ferry.*real|ferry.*ambien|ferry_1h", re.I)),
    ("driving_music_first", re.compile(r"driving.*music|music_first|driving_1h", re.I)),
    ("skyline_sequence", re.compile(r"skyline|manhattan.*view|city.*view", re.I)),
)


def _long_content_bucket(row: dict[str, Any]) -> str:
    path = str(row.get("path") or "")
    ctype = str(row.get("content_type") or "")
    blob = f"{path} {ctype}"
    for name, pat in _PRIORITY_PATTERNS:
        if pat.search(blob):
            return name
    if "ferry" in blob.lower():
        return "ferry_real_ambience"
    if "driving" in blob.lower() or "music" in blob.lower():
        return "driving_music_first"
    return "skyline_sequence"


def _sort_long_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple[int, float]:
        bucket = _long_content_bucket(row)
        try:
            pri = LONG_CONTENT_PRIORITY.index(bucket)
        except ValueError:
            pri = len(LONG_CONTENT_PRIORITY)
        return (pri, -float(row.get("mtime") or 0))

    return sorted(rows, key=key)


# Long: L1 ready nyc_long_clips → L6 no material
LONG_LEVELS = (
    "L1_ready_nyc_long_clips",
    "L2_ready_other_long",
    "L3_publish_pack_staged",
    "L4_backlog_index",
    "L5_older_ready_pool",
    "L6_no_material",
)

SHORTS_LEVELS = (
    "L1_ready_shorts_clips",
    "L2_publish_pack_shorts",
    "L3_backlog_shorts",
    "L4_relaxed_duration",
    "L5_any_vertical_pool",
    "L6_no_material",
)


def pick_long_level(candidates_by_level: dict[str, list[dict[str, Any]]]) -> tuple[str, dict[str, Any] | None]:
    for lvl in LONG_LEVELS:
        rows = _sort_long_rows(list(candidates_by_level.get(lvl) or []))
        if rows:
            row = rows[0]
            row = {**row, "content_priority": _long_content_bucket(row)}
            return lvl, row
    return "L6_no_material", None


def pick_shorts_level(
    candidates_by_level: dict[str, list[dict[str, Any]]], *, need: int
) -> tuple[str, list[dict[str, Any]]]:
    picked: list[dict[str, Any]] = []
    level_used = "L6_no_material"
    for lvl in SHORTS_LEVELS:
        rows = candidates_by_level.get(lvl) or []
        if not rows:
            continue
        level_used = lvl
        for row in rows:
            if len(picked) >= need:
                break
            picked.append(row)
        if len(picked) >= need:
            break
    return level_used, picked[:need]


def describe_fallback(level: str, *, kind: str) -> dict[str, Any]:
    return {"kind": kind, "fallback_level": level, "enabled": level != "L6_no_material"}
