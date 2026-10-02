"""English-internet collectors (public endpoints, no key required).

* Hacker News  — Firebase API: top stories, last 24h
* GitHub       — search API: repos created in the last N days, by stars
                 (optional GITHUB_TOKEN raises the rate limit)
* Reddit       — public JSON: top of the day for chosen subreddits
                 (Reddit often blocks unauthenticated cloud IPs; failures are skipped)

Every collector takes an injectable ``fetch(url, headers) -> bytes`` and
returns [] on network errors, so one dead source never stops the scan.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote

from .models import Signal

Fetch = Callable[[str, dict], bytes]
UA = "StatevergeInfoGap/1.0 (+https://x.com/StatevergeCN)"

# Subreddits mapped to the four tracks (AI / money / US life / industry).
DEFAULT_SUBREDDITS = [
    "artificial", "LocalLLaMA", "SaaS", "SideProject", "Entrepreneur", "smallbusiness",
    "personalfinance", "Frugal", "povertyfinance", "doordash_drivers", "Costco", "technology", "Futurology",
]

_NET_ERRORS = (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError)


def http_fetch(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).replace(microsecond=0).isoformat()


class HackerNewsCollector:
    name = "hackernews"
    base = "https://hacker-news.firebaseio.com/v0"

    def __init__(self, fetch: Fetch | None = None, limit: int = 60) -> None:
        self.fetch = fetch or http_fetch
        self.limit = limit

    def collect(self, since: datetime) -> list[Signal]:
        try:
            ids = json.loads(self.fetch(f"{self.base}/topstories.json", {}))[: self.limit]
        except _NET_ERRORS:
            return []

        def item(i: int) -> dict | None:
            try:
                return json.loads(self.fetch(f"{self.base}/item/{i}.json", {}))
            except _NET_ERRORS:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            items = list(pool.map(item, ids))
        out = []
        for it in items:
            if not it or it.get("type") != "story" or it.get("dead") or it.get("deleted"):
                continue
            if datetime.fromtimestamp(it.get("time", 0), timezone.utc) < since:
                continue
            thread = f"https://news.ycombinator.com/item?id={it['id']}"
            out.append(Signal(
                source=self.name, title=it.get("title", ""), url=it.get("url") or thread,
                discussion_url=thread, created_at=_iso(it.get("time", 0)),
                points=int(it.get("score", 0)), comments=int(it.get("descendants", 0)),
            ))
        return out


class GitHubCollector:
    name = "github"

    def __init__(self, fetch: Fetch | None = None, days: int = 7, limit: int = 30) -> None:
        self.fetch = fetch or http_fetch
        self.days = days
        self.limit = limit

    def collect(self, since: datetime) -> list[Signal]:
        created = (since - timedelta(days=max(0, self.days - 1))).date().isoformat()
        q = quote(f"created:>={created} stars:>50")
        url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page={self.limit}"
        headers = {"Accept": "application/vnd.github+json"}
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        try:
            data = json.loads(self.fetch(url, headers))
        except _NET_ERRORS:
            return []
        return [
            Signal(
                source=self.name, title=r.get("full_name", ""), url=r.get("html_url", ""),
                summary=r.get("description") or "", created_at=r.get("created_at", ""),
                points=int(r.get("stargazers_count", 0)), comments=int(r.get("forks_count", 0)),
                tags=list(r.get("topics") or []) + ([r["language"]] if r.get("language") else []),
            )
            for r in data.get("items", [])
        ]


class RedditCollector:
    name = "reddit"

    def __init__(self, fetch: Fetch | None = None, subreddits: list[str] | None = None, limit: int = 10) -> None:
        self.fetch = fetch or http_fetch
        self.subreddits = subreddits or DEFAULT_SUBREDDITS
        self.limit = limit

    def collect(self, since: datetime) -> list[Signal]:
        out = []
        for sub in self.subreddits:
            try:
                data = json.loads(self.fetch(f"https://www.reddit.com/r/{sub}/top.json?t=day&limit={self.limit}", {}))
            except _NET_ERRORS:
                continue
            for child in data.get("data", {}).get("children", []):
                p = child.get("data", {})
                if p.get("stickied") or p.get("over_18"):
                    continue
                if datetime.fromtimestamp(p.get("created_utc", 0), timezone.utc) < since:
                    continue
                thread = f"https://www.reddit.com{p.get('permalink', '')}"
                link = p.get("url_overridden_by_dest") or thread
                out.append(Signal(
                    source=f"reddit:{sub}", title=p.get("title", ""), url=link, discussion_url=thread,
                    summary=(p.get("selftext") or "")[:500], created_at=_iso(p.get("created_utc", 0)),
                    points=int(p.get("ups", 0)), comments=int(p.get("num_comments", 0)), tags=[sub],
                ))
        return out


def default_collectors(fetch: Fetch | None = None) -> list:
    return [HackerNewsCollector(fetch), GitHubCollector(fetch), RedditCollector(fetch)]


def collect_all(collectors: list, hours: int = 24, now: datetime | None = None) -> list[Signal]:
    """Run every collector and dedupe by URL (keeping the most-discussed copy)."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    best: dict[str, Signal] = {}
    for c in collectors:
        for s in c.collect(since):
            if not s.title or not (s.url or s.discussion_url):
                continue
            key = s.signal_id
            if key not in best or (s.points + s.comments) > (best[key].points + best[key].comments):
                best[key] = s
    return list(best.values())
