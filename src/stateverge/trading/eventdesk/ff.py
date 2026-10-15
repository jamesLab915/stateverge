"""Consensus (Forecast column) from ForexFactory's public weekly calendar export.

The export is refreshed about hourly; fetch it once per run, not in a loop.
Manual input always wins over this; anything from here is labelled for checking.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Callable

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# catalog key → ForexFactory title (USD rows)
TITLES = {
    "cpi": "CPI m/m",
    "core_cpi": "Core CPI m/m",
    "nfp": "Non-Farm Employment Change",
    "unrate": "Unemployment Rate",
    "ppi": "PPI m/m",
    "core_pce": "Core PCE Price Index m/m",
    "retail": "Retail Sales m/m",
}


def _http(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def parse_value(s: str | None) -> float | None:
    """'0.3%' → 0.3, '-0.1%' → -0.1, '50K' → 50 (thousands), '' → None."""
    m = re.fullmatch(r"\s*([-+]?\d+(?:\.\d+)?)\s*([%KkMm]?)\s*", s or "")
    if not m:
        return None
    v = float(m.group(1))
    return v * 1000 if m.group(2) in ("M", "m") else v


def week(get: Callable[[str], bytes] | None = None) -> list[dict]:
    return [r for r in json.loads((get or _http)(FEED)) if r.get("country") == "USD"]


def forecast(key: str, day: str, rows: list[dict]) -> tuple[float, str] | None:
    """(forecast, as-of note) for a catalog event on `day` (YYYY-MM-DD), else None."""
    title = TITLES.get(key)
    for r in rows:
        if r.get("title") == title and str(r.get("date", "")).startswith(day):
            v = parse_value(r.get("forecast"))
            if v is not None:
                return v, f"ForexFactory {r['date']} forecast {r.get('forecast')}"
    return None


def high_impact(day: str, rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("impact") == "High" and str(r.get("date", "")).startswith(day)]
