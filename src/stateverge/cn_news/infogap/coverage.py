"""中文互联网覆盖度检测.

Asks a web search API how many Chinese-language pages from the past week
already talk about the item. Providers, first configured wins:

* Tavily (``TAVILY_API_KEY``, https://tavily.com — 1,000 free credits/month,
  no card): searches only major Chinese tech/business/social sites.
* Brave Search (``BRAVE_API_KEY``) — $5 monthly credit, card required.

Without a key the check is skipped and recorded as unchecked — the score
then uses a neutral scarcity and the review file says so. It never guesses.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlencode

from .models import Coverage, Signal

Fetch = Callable[[str, dict], bytes]
_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9.+\-]*[A-Za-z0-9+]|[A-Za-z]")

# Words that carry no identity for a search query.
_STOP = set("""a an the and or of to in on for with from by at is are was were be how why what when who
new show ask hn tell my your our we you i it its this that these those just now via vs using use
launch launches launched introducing release released open source free best top first""".split())


def query_for(signal: Signal) -> str:
    """Distinctive terms: repo name for GitHub, else capitalised/technical words."""
    if signal.source == "github" and "/" in signal.title:
        return signal.title.split("/", 1)[1].replace("-", " ").replace("_", " ")
    words = _WORD.findall(signal.title)
    named = [w for w in words if w.lower() not in _STOP and (w[0].isupper() or any(c.isdigit() for c in w))]
    picked = named[:3] or [w for w in words if w.lower() not in _STOP][:4]
    return " ".join(picked)


def _http(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


class BraveCoverage:
    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str | None = None, fetch: Fetch | None = None) -> None:
        self.api_key = (api_key or os.environ.get("BRAVE_API_KEY", "")).strip()
        self.fetch = fetch or _http

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def check(self, signal: Signal) -> Coverage:
        q = query_for(signal)
        if not self.available or not q:
            return Coverage(checked=False, query=q, provider=self.name)
        params = {"q": q, "search_lang": "zh-hans", "freshness": "pw", "count": "20"}
        try:
            data = json.loads(self.fetch(f"{self.endpoint}?{urlencode(params)}", {"X-Subscription-Token": self.api_key}))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            return Coverage(checked=False, query=q, provider=self.name, error=_describe(e))
        results = data.get("web", {}).get("results", [])
        zh = [r for r in results if _CJK.search(f"{r.get('title', '')} {r.get('description', '')}")]
        return Coverage(True, q, len(zh), [r.get("url", "") for r in zh[:5]], self.name)


# Major Chinese-language tech / business / community sites.
ZH_DOMAINS = [
    "36kr.com", "huxiu.com", "ithome.com", "sspai.com", "zhihu.com", "juejin.cn", "csdn.net", "oschina.net",
    "infoq.cn", "jiqizhixin.com", "qbitai.com", "geekpark.net", "tmtpost.com", "ifanr.com", "cnbeta.com.tw",
    "v2ex.com", "weibo.com", "bilibili.com", "sohu.com", "163.com", "sina.com.cn", "qq.com", "thepaper.cn",
    "jiemian.com", "caixin.com", "yicai.com", "cls.cn", "wallstreetcn.com", "xueqiu.com", "zaobao.com",
    "cn.nytimes.com", "cn.wsj.com", "ftchinese.com", "ithome.com.tw", "inside.com.tw",
]


class TavilyCoverage:
    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str | None = None, post=None) -> None:
        self.api_key = (api_key or os.environ.get("TAVILY_API_KEY", "")).strip()
        self.post = post or _post

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def check(self, signal: Signal) -> Coverage:
        q = query_for(signal)
        if not self.available or not q:
            return Coverage(checked=False, query=q, provider=self.name)
        body = json.dumps({
            "query": q, "search_depth": "basic", "time_range": "week", "max_results": 20,
            "include_domains": ZH_DOMAINS,
        }).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            data = json.loads(self.post(self.endpoint, headers, body))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            return Coverage(checked=False, query=q, provider=self.name, error=_describe(e))
        results = data.get("results", [])
        # Only count pages that actually mention the query terms.
        terms = [t.lower() for t in q.split() if len(t) > 2] or [q.lower()]
        hits = [r for r in results if any(t in f"{r.get('title', '')} {r.get('content', '')}".lower() for t in terms)]
        return Coverage(True, q, len(hits), [r.get("url", "") for r in hits[:5]], self.name)


def _describe(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = e.read()[:200].decode(errors="replace")
        except Exception:  # noqa: BLE001 - best effort
            detail = ""
        return f"HTTP {e.code} {detail}".strip()
    return f"{type(e).__name__}: {e}"[:200]


def _post(url: str, headers: dict, body: bytes) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


class NoCoverage:
    name = "none"
    available = False

    def check(self, signal: Signal) -> Coverage:
        return Coverage(checked=False, query=query_for(signal), provider=self.name)


def default_coverage():
    for provider in (TavilyCoverage(), BraveCoverage()):
        if provider.available:
            return provider
    return NoCoverage()
