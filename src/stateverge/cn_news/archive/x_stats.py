"""Weekly X account snapshot toward the revenue-sharing thresholds.

Reads (X API v2, OAuth 1.0a user context):
* GET /2/users/me?user.fields=public_metrics          → followers
* GET /2/users/:id/tweets?tweet.fields=public_metrics → impressions per post

Limits worth knowing:
* The API reports *total* impressions and *total* followers. X's thresholds
  count only *verified* (Premium) impressions and followers, which the API
  does not expose — so the progress figures here are upper bounds. The
  authoritative numbers are in Creator Studio.
* The timeline endpoint returns recent posts only; very old posts drop out.
* On the pay-per-use plan every read is billed; the defaults stay small.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from .x_publisher import XClient, XPublishError, oauth1_header

API = "https://api.x.com/2"
IMPRESSIONS_TARGET = 5_000_000  # verified home-timeline impressions, last 3 months
FOLLOWERS_TARGET = 500  # verified followers


@dataclass
class Snapshot:
    taken_at: str
    username: str
    followers: int
    posts_7d: int
    impressions_7d: int
    likes_7d: int
    reposts_7d: int
    replies_7d: int
    posts_90d: int
    impressions_90d: int

    def report(self, previous: "Snapshot | None" = None) -> str:
        def delta(now: int, before: int | None) -> str:
            return "" if before is None else f"({now - before:+,})"

        imp_pct = min(100.0, self.impressions_90d / IMPRESSIONS_TARGET * 100)
        fol_pct = min(100.0, self.followers / FOLLOWERS_TARGET * 100)
        prev_f = previous.followers if previous else None
        return "\n".join([
            f"📊 @{self.username} 周报 {self.taken_at[:10]}",
            f"关注者:{self.followers:,} {delta(self.followers, prev_f)}",
            f"近 7 天:{self.posts_7d} 帖,浏览 {self.impressions_7d:,},赞 {self.likes_7d:,},"
            f"转发 {self.reposts_7d:,},回复 {self.replies_7d:,}",
            f"近 90 天浏览:{self.impressions_90d:,} / {IMPRESSIONS_TARGET:,}(≤{imp_pct:.1f}%)",
            f"关注者进度:{self.followers:,} / {FOLLOWERS_TARGET}(≤{fol_pct:.0f}%)",
            "注:API 只有总浏览量和总关注者;收益分成只算认证(Premium)用户,实际进度以 Creator Studio 为准。",
        ])


def _get(client: XClient, path: str, query: dict[str, str]) -> dict:
    url = f"{API}{path}"
    headers = {"Authorization": oauth1_header(client.creds, "GET", url, query=query)}
    status, body = client.get_transport(f"{url}?{urlencode(query, quote_via=quote)}", headers)
    if status != 200:
        raise XPublishError(f"GET {path}: X API returned {status}: {body[:300].decode(errors='replace')}")
    return json.loads(body)


def take_snapshot(client: XClient, now: datetime | None = None, max_pages: int = 3) -> Snapshot:
    now = now or datetime.now(timezone.utc)
    me = _get(client, "/users/me", {"user.fields": "public_metrics"})["data"]
    since_90 = now - timedelta(days=90)
    since_7 = now - timedelta(days=7)

    posts: list[dict] = []
    query = {
        "max_results": "100",
        "tweet.fields": "created_at,public_metrics",
        "exclude": "retweets",
        "start_time": since_90.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    for _ in range(max_pages):
        page = _get(client, f"/users/{me['id']}/tweets", query)
        posts += page.get("data", [])
        token = page.get("meta", {}).get("next_token")
        if not token:
            break
        query = {**query, "pagination_token": token}

    def created(p: dict) -> datetime:
        return datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))

    def total(items: list[dict], metric: str) -> int:
        return sum(int(p.get("public_metrics", {}).get(metric, 0)) for p in items)

    week = [p for p in posts if created(p) >= since_7]
    return Snapshot(
        taken_at=now.replace(microsecond=0).isoformat(),
        username=me.get("username", ""),
        followers=int(me.get("public_metrics", {}).get("followers_count", 0)),
        posts_7d=len(week),
        impressions_7d=total(week, "impression_count"),
        likes_7d=total(week, "like_count"),
        reposts_7d=total(week, "retweet_count"),
        replies_7d=total(week, "reply_count"),
        posts_90d=len(posts),
        impressions_90d=total(posts, "impression_count"),
    )


def record(snapshot: Snapshot, path: Path) -> str:
    """Append to the JSON history and return the report (with week-over-week change)."""
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() and path.read_text().strip() else []
    previous = Snapshot(**history[-1]) if history else None
    history.append(asdict(snapshot))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return snapshot.report(previous)
