"""Last-24h US finance headlines from public RSS feeds (no key)."""

from __future__ import annotations

import urllib.error
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

from .market import http_get

Fetch = Callable[[str], bytes]

FEEDS = {
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "CNBC Markets": "https://www.cnbc.com/id/15839069/device/rss/rss.html",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
}


@dataclass
class Headline:
    source: str
    title: str
    url: str
    summary: str
    published: str

    def to_dict(self) -> dict:
        return asdict(self)


def _text(el, tag: str) -> str:
    node = el.find(tag)
    return (node.text or "").strip() if node is not None and node.text else ""


def parse_feed(source: str, raw: bytes, since: datetime) -> list[Headline]:
    out = []
    for item in ET.fromstring(raw).iter("item"):
        title, link = _text(item, "title"), _text(item, "link")
        try:
            when = parsedate_to_datetime(_text(item, "pubDate"))
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if title and link and when >= since:
            out.append(Headline(source, title, link, _text(item, "description")[:400], when.isoformat()))
    return out


def headlines(fetch: Fetch | None = None, now: datetime | None = None, hours: int = 24,
              feeds: dict[str, str] = FEEDS, limit: int = 25) -> list[Headline]:
    fetch = fetch or http_get
    since = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    seen, out = set(), []
    for source, url in feeds.items():
        try:
            items = parse_feed(source, fetch(url), since)
        except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError):
            continue
        for h in items:
            key = h.title.lower()
            if key not in seen:
                seen.add(key)
                out.append(h)
    out.sort(key=lambda h: h.published, reverse=True)
    return out[:limit]
