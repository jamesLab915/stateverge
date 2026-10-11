"""US market snapshot: last close, change and a ~1-month daily series.

Yahoo Finance's public chart endpoint first, Stooq CSV as fallback. Every
fetcher is injectable so tests never touch the network; a symbol that fails
on both sources is simply left out (and the script is told it is missing).
"""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote

Fetch = Callable[[str], bytes]
UA = "Mozilla/5.0 (compatible; StatevergeFinance/1.0)"

# (yahoo symbol, stooq symbol, Chinese name, unit shown on screen)
SYMBOLS = [
    ("^GSPC", "^spx", "标普500", "点"),
    ("^IXIC", "^ndq", "纳斯达克", "点"),
    ("^DJI", "^dji", "道琼斯", "点"),
    ("^TNX", "10usy.b", "10年期美债收益率", "%"),
    ("CL=F", "cl.f", "WTI原油", "美元"),
    ("GC=F", "gc.f", "黄金", "美元"),
    ("BTC-USD", "btcusd", "比特币", "美元"),
    ("CNY=X", "usdcny", "美元兑人民币", ""),
]


@dataclass
class Quote:
    symbol: str
    name: str
    unit: str
    close: float
    prev_close: float
    as_of: str  # date of the close, YYYY-MM-DD
    series: list[float] = field(default_factory=list)
    source: str = ""

    @property
    def change(self) -> float:
        return self.close - self.prev_close

    @property
    def change_pct(self) -> float:
        return (self.close / self.prev_close - 1) * 100 if self.prev_close else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(change=round(self.change, 4), change_pct=round(self.change_pct, 2))
        return d


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


_ERR = (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError)


def yahoo_quote(sym: str, name: str, unit: str, fetch: Fetch) -> Quote | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}?range=1mo&interval=1d"
    try:
        r = json.loads(fetch(url))["chart"]["result"][0]
        ts = r["timestamp"]
        closes = r["indicators"]["quote"][0]["close"]
        pts = [(t, c) for t, c in zip(ts, closes, strict=False) if c is not None]
        if len(pts) < 2:
            return None
        day = datetime.fromtimestamp(pts[-1][0], timezone.utc).date().isoformat()
        return Quote(sym, name, unit, float(pts[-1][1]), float(pts[-2][1]), day, [float(c) for _, c in pts], "yahoo")
    except _ERR:
        return None


def stooq_quote(sym: str, name: str, unit: str, fetch: Fetch, yahoo_sym: str) -> Quote | None:
    try:
        rows = list(csv.DictReader(io.StringIO(fetch(f"https://stooq.com/q/d/l/?s={quote(sym)}&i=d").decode())))
        rows = [r for r in rows if r.get("Close") not in (None, "", "N/D")][-23:]
        if len(rows) < 2:
            return None
        series = [float(r["Close"]) for r in rows]
        return Quote(yahoo_sym, name, unit, series[-1], series[-2], rows[-1]["Date"], series, "stooq")
    except _ERR:
        return None


def snapshot(fetch: Fetch | None = None, symbols=SYMBOLS) -> list[Quote]:
    fetch = fetch or http_get
    out = []
    for ysym, ssym, name, unit in symbols:
        q = yahoo_quote(ysym, name, unit, fetch) or stooq_quote(ssym, name, unit, fetch, ysym)
        if q:
            out.append(q)
    return out
