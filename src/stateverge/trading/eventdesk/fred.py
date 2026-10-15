"""FRED: which events release on a date, and their actual values (FRED_API_KEY, free)."""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import date
from typing import Callable
from urllib.parse import urlencode

from .events import CATALOG, EventType, transform

BASE = "https://api.stlouisfed.org/fred"
Get = Callable[[str], bytes]


def _http(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "StatevergeEventDesk/1.0"}),
                                timeout=30) as r:
        return r.read()


class Fred:
    def __init__(self, api_key: str | None = None, get: Get | None = None) -> None:
        self.key = (api_key or os.environ.get("FRED_API_KEY", "")).strip()
        self.get = get or _http

    @property
    def available(self) -> bool:
        return bool(self.key)

    def _q(self, path: str, **params) -> dict:
        params.update(api_key=self.key, file_type="json")
        return json.loads(self.get(f"{BASE}/{path}?{urlencode(params)}"))

    def release_id(self, series: str) -> int:
        return int(self._q("series/release", series_id=series)["releases"][0]["id"])

    def releases_on(self, day: date) -> list[EventType]:
        out = []
        cache: dict[int, set[str]] = {}
        for ev in CATALOG:
            rid = self.release_id(ev.series)
            if rid not in cache:
                d = self._q("release/dates", release_id=rid, realtime_start=day.isoformat(),
                            realtime_end=day.isoformat(), include_release_dates_with_no_data="true")
                cache[rid] = {x["date"] for x in d.get("release_dates", [])}
            if day.isoformat() in cache[rid]:
                out.append(ev)
        return out

    def actual(self, ev: EventType) -> tuple[float, str]:
        obs = self._q("series/observations", series_id=ev.series, sort_order="desc", limit=3)["observations"]
        vals = [float(o["value"]) for o in reversed(obs) if o["value"] not in (".", "")]
        return round(transform(vals, ev.transform), 3), obs[0]["date"]
