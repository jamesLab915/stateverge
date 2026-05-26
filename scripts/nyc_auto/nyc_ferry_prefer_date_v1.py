#!/usr/bin/env python3
"""Prefer ferry (video169/ferry) sources from a calendar date — path tokens + mtime."""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

ENV_PREFER_SOURCE_DATE = "STATEVERGE_PREFER_SOURCE_DATE"
DEFAULT_PREFER_ISO = "2026-05-15"
ALT_MTIME_DATE_IF_NO_PATH_DATE = date(2025, 5, 15)

_PATH_PATTERNS = (
    re.compile(r"20260515", re.I),
    re.compile(r"2026[-_]05[-_]15", re.I),
    re.compile(r"20250515", re.I),
    re.compile(r"2025[-_]05[-_]15", re.I),
    re.compile(r"may[-_]?15", re.I),
    re.compile(r"\b0515\b"),
    re.compile(r"[-_/]5[-_]15[-_/]", re.I),
    re.compile(r"[-_/]05[-_]15[-_/]", re.I),
)


def resolve_prefer_dates(cli: str | None = None) -> list[date]:
    """CLI > env > default (2026-05-15). Comma-separated ISO dates allowed."""
    raw = (cli or os.environ.get(ENV_PREFER_SOURCE_DATE) or DEFAULT_PREFER_ISO).strip()
    if not raw:
        return [date.fromisoformat(DEFAULT_PREFER_ISO)]
    out: list[date] = []
    for part in raw.replace(";", ",").split(","):
        p = part.strip()
        if not p:
            continue
        out.append(date.fromisoformat(p))
    return out or [date.fromisoformat(DEFAULT_PREFER_ISO)]


def _mtime_local_date(path: Path) -> date | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return None


def path_has_embedded_date(path: Path) -> bool:
    name = path.name.lower()
    if re.search(r"20\d{2}[-_/]?\d{2}[-_/]?\d{2}", name):
        return True
    for pat in _PATH_PATTERNS:
        if pat.search(str(path)):
            return True
    return False


def matches_prefer_date(path: Path, prefer_dates: list[date], *, mtime: float | None = None) -> bool:
    """True when path tokens or local mtime match any prefer date (or alt mtime-only date)."""
    if not prefer_dates:
        return False
    prefer_set = set(prefer_dates)
    s = str(path)
    for pat in _PATH_PATTERNS:
        if pat.search(s):
            return True
    md: date | None
    if mtime is not None:
        try:
            md = datetime.fromtimestamp(float(mtime)).date()
        except (OSError, ValueError, OverflowError):
            md = None
    else:
        md = _mtime_local_date(path)
    if md is None:
        return False
    if md in prefer_set:
        return True
    if not path_has_embedded_date(path) and md == ALT_MTIME_DATE_IF_NO_PATH_DATE:
        return True
    return False


def prefer_sort_key(path: Path, prefer_dates: list[date], *, mtime: float | None = None) -> tuple[int, float, str]:
    """Sort ascending: preferred first, then newest mtime, then path."""
    if mtime is None:
        try:
            mtime = float(path.stat().st_mtime)
        except OSError:
            mtime = 0.0
    pref = 0 if matches_prefer_date(path, prefer_dates, mtime=mtime) else 1
    return (pref, -float(mtime), str(path))


def is_ferry_video169_path(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    return "video169" in parts and "ferry" in parts


def count_preferred_ferry_files(paths: list[Path], prefer_dates: list[date]) -> tuple[int, int]:
    """Return (ferry_total, ferry_preferred_count)."""
    ferry_total = 0
    ferry_pref = 0
    for p in paths:
        if not is_ferry_video169_path(p):
            continue
        ferry_total += 1
        if matches_prefer_date(p, prefer_dates):
            ferry_pref += 1
    return ferry_total, ferry_pref


def filter_preferred_only(paths: list[Path], prefer_dates: list[date]) -> list[Path]:
    return [p for p in paths if matches_prefer_date(p, prefer_dates)]


def append_prefer_manifest_fields(
    manifest: dict[str, Any],
    *,
    prefer_dates: list[date],
    may15_only_attempted: bool,
    may15_only_satisfied: bool,
    pick_note: str,
    warnings: list[str],
) -> None:
    manifest["prefer_source_dates"] = [d.isoformat() for d in prefer_dates]
    manifest["ferry_may15_priority"] = True
    if may15_only_attempted and not may15_only_satisfied and pick_note != "ok":
        if "insufficient_may15_ferry_footage" not in warnings:
            warnings.append("insufficient_may15_ferry_footage")
