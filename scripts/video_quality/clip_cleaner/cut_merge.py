"""Parse manual cut specs and merge overlapping cut ranges."""
from __future__ import annotations

import re
from typing import Any

_CUT_RE = re.compile(
    r"^\s*"
    r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d+))?"
    r"\s*-\s*"
    r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d+))?"
    r"\s*$"
)


def _part_to_sec(h: str | None, m: str, s: str, frac: str | None = None) -> float:
    hours = int(h) if h else 0
    minutes = int(m)
    seconds = int(s)
    ms = float(f"0.{frac}") if frac else 0.0
    return float(hours * 3600 + minutes * 60 + seconds) + ms


def parse_cut_spec(spec: str) -> tuple[float, float]:
    """Parse ``HH:MM:SS-HH:MM:SS`` (hours optional on left side only when 3 parts)."""
    raw = (spec or "").strip()
    if not raw:
        raise ValueError("empty_cut_spec")

    m = _CUT_RE.match(raw)
    if m:
        start = _part_to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _part_to_sec(m.group(5), m.group(6), m.group(7), m.group(8))
        if end <= start:
            raise ValueError(f"cut_end_must_exceed_start:{raw}")
        return start, end

    if "-" in raw:
        left, right = raw.split("-", 1)
        left, right = left.strip(), right.strip()
        if left and right and ":" in left and ":" in right:
            return parse_cut_spec(f"{left}-{right}")

    raise ValueError(f"invalid_cut_spec:{raw}")


def merge_cut_ranges(ranges: list[dict[str, Any]], *, gap_sec: float = 0.25) -> list[dict[str, Any]]:
    """Sort and merge overlapping or nearly-adjacent cut ranges (dedupe reasons)."""
    if not ranges:
        return []

    norm: list[dict[str, Any]] = []
    for r in ranges:
        try:
            start = float(r.get("start_sec", r.get("start", 0.0)))
            end = float(r.get("end_sec", r.get("end", 0.0)))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        reason = str(r.get("reason") or "unspecified")
        source = str(r.get("source") or "auto")
        score = r.get("score")
        norm.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "reason": reason,
                "source": source,
                "score": score,
            }
        )

    norm.sort(key=lambda x: (x["start_sec"], x["end_sec"]))
    merged: list[dict[str, Any]] = []
    for item in norm:
        if not merged:
            merged.append(dict(item))
            continue
        prev = merged[-1]
        if item["start_sec"] <= prev["end_sec"] + gap_sec:
            prev["end_sec"] = round(max(prev["end_sec"], item["end_sec"]), 3)
            reasons = {prev.get("reason", ""), item.get("reason", "")}
            prev["reason"] = "+".join(sorted(x for x in reasons if x))
            if prev.get("source") != item.get("source"):
                prev["source"] = "mixed"
            ps, ns = prev.get("score"), item.get("score")
            if ps is not None or ns is not None:
                try:
                    prev["score"] = round(max(float(ps or 0.0), float(ns or 0.0)), 4)
                except (TypeError, ValueError):
                    pass
        else:
            merged.append(dict(item))
    return merged


def cuts_to_keep_segments(
    cuts: list[dict[str, Any]],
    *,
    duration_sec: float,
    min_keep_sec: float = 0.5,
) -> list[dict[str, float]]:
    """Invert cut list into kept segments for concat rendering."""
    if duration_sec <= 0:
        return []
    merged = merge_cut_ranges(cuts)
    keep: list[dict[str, float]] = []
    cursor = 0.0
    for cut in merged:
        start = float(cut["start_sec"])
        if start > cursor + min_keep_sec:
            keep.append({"start_sec": round(cursor, 3), "end_sec": round(start, 3)})
        cursor = max(cursor, float(cut["end_sec"]))
    if cursor < duration_sec - min_keep_sec:
        keep.append({"start_sec": round(cursor, 3), "end_sec": round(duration_sec, 3)})
    return keep
