"""Chinese-language headlines from Google News RSS topic searches (no key).

Each item keeps the publishing outlet's name, so the guard can require two
independent outlets per event.
"""

from __future__ import annotations

import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import quote

Fetch = Callable[[str], bytes]
UA = "Mozilla/5.0 (compatible; StatevergeCN/1.0)"

# category → search query (Google News syntax; "when:1d" = last 24h)
TOPICS = {
    "利率货币": "央行 OR 降息 OR 降准 OR LPR OR 人民币汇率 when:1d",
    "房地产": "房地产 OR 楼市 OR 房贷 OR 限购 when:1d",
    "宏观数据": "国家统计局 OR GDP OR CPI OR 出口 OR 海关总署 OR PMI when:1d",
    "科技AI": "大模型 OR 人工智能 OR 芯片 OR 华为 OR 比亚迪 OR 小米 when:1d",
    "政策民生": "国务院 OR 新规 OR 医保 OR 社保 OR 个税 OR 消费券 when:1d",
    "资本市场": "A股 OR 证监会 OR 沪指 OR 港股 when:1d",
    "安全事故": "事故 通报 OR 应急管理部 when:1d",
}


@dataclass
class Headline:
    id: int
    category: str
    title: str
    outlet: str
    url: str
    published: str

    def to_dict(self) -> dict:
        return asdict(self)


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def rss_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"


def parse(category: str, raw: bytes, since: datetime) -> list[tuple]:
    out = []
    for item in ET.fromstring(raw).iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        src = item.find("source")
        outlet = (src.text or "").strip() if src is not None and src.text else ""
        if outlet and title.endswith(f" - {outlet}"):
            title = title[: -len(outlet) - 3].strip()
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if title and link and outlet and when >= since:
            out.append((category, title, outlet, link, when.isoformat()))
    return out


def collect(fetch: Fetch | None = None, now: datetime | None = None, hours: int = 24,
            topics: dict[str, str] = TOPICS, per_topic: int = 20) -> list[Headline]:
    fetch = fetch or http_get
    since = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    seen: set[str] = set()
    out: list[Headline] = []
    for category, query in topics.items():
        try:
            rows = parse(category, fetch(rss_url(query)), since)[:per_topic]
        except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError):
            continue
        for cat, title, outlet, link, when in rows:
            if title in seen:
                continue
            seen.add(title)
            out.append(Headline(len(out), cat, title, outlet, link, when))
    return out
