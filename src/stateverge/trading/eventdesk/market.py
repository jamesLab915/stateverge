"""Quotes for open confirmation: futures, VIX, premarket (Yahoo chart API, no key)."""

from __future__ import annotations

import json
import urllib.request
from typing import Callable
from urllib.parse import quote

Get = Callable[[str], bytes]


def _http(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def change_pct(symbol: str, get: Get | None = None) -> tuple[float, float] | None:
    """(last price incl. pre-market, % change vs previous close) or None."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range=1d&interval=5m&includePrePost=true"
    try:
        r = json.loads((get or _http)(url))["chart"]["result"][0]
        prev = r["meta"].get("chartPreviousClose") or r["meta"]["previousClose"]
        closes = [c for c in r["indicators"]["quote"][0]["close"] if c is not None]
        last = closes[-1] if closes else r["meta"]["regularMarketPrice"]
        return float(last), (float(last) / float(prev) - 1) * 100
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None
