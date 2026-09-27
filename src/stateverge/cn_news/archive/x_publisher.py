"""Publish Archive Cards to X (Section 16).

Flow: Archive Card → x_post() text → publish_guard.check() → split into a
thread → POST https://api.x.com/2/tweets (OAuth 1.0a user context).

* Nothing is posted unless the guard returns ALLOW_PUBLISH.
* ``dry_run`` is the default; posting needs ``dry_run=False`` explicitly.
* Each card is posted at most once (``x_posts`` table), so a retry after a
  partial failure continues the thread instead of duplicating it.

Credentials come from the environment and are never printed:
X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote

from . import publish_guard
from .clip_builder import ClipPlan
from .database import ArchiveDB
from .models import ArchiveStatus
from .script_generator import ArchiveCard, NotPublishable, x_post

TWEETS_URL = "https://api.x.com/2/tweets"
MAX_WEIGHTED_LENGTH = 280
URL_LENGTH = 23
_URL = re.compile(r"https?://\S+")

# twitter-text v3: these code point ranges weigh 1, everything else (CJK, emoji) 2.
_LIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))

X_POSTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS x_posts (
    card_key    TEXT NOT NULL,
    part_index  INTEGER NOT NULL,
    tweet_id    TEXT NOT NULL,
    text        TEXT NOT NULL,
    posted_at   TEXT NOT NULL,
    PRIMARY KEY (card_key, part_index)
);
"""


class XPublishError(RuntimeError):
    pass


# -- length & thread splitting ------------------------------------------------


def _char_weight(ch: str) -> int:
    cp = ord(ch)
    return 1 if any(lo <= cp <= hi for lo, hi in _LIGHT_RANGES) else 2


def weighted_length(text: str) -> int:
    """X's character count: URLs = 23, CJK/emoji = 2, Latin = 1."""
    total, pos = 0, 0
    for m in _URL.finditer(text):
        total += sum(_char_weight(c) for c in text[pos : m.start()]) + URL_LENGTH
        pos = m.end()
    return total + sum(_char_weight(c) for c in text[pos:])


def _hard_split(text: str, limit: int) -> list[str]:
    """Split one oversized paragraph at sentence ends, then by characters."""
    pieces = re.split(r"(?<=[。!?!?;;.\n])", text)
    out, cur = [], ""
    for piece in pieces:
        if weighted_length(cur + piece) <= limit:
            cur += piece
            continue
        if cur:
            out.append(cur)
        cur = ""
        while weighted_length(piece) > limit:
            n = len(piece)
            while weighted_length(piece[:n]) > limit:
                n -= 1
            out.append(piece[:n])
            piece = piece[n:]
        cur = piece
    if cur:
        out.append(cur)
    return [p.strip() for p in out if p.strip()]


def split_thread(text: str, limit: int = MAX_WEIGHTED_LENGTH) -> list[str]:
    """Pack paragraphs into posts; the numbering suffix " n/m" is reserved."""
    budget = limit - 6
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    parts: list[str] = []
    cur = ""
    for para in paragraphs:
        candidate = f"{cur}\n\n{para}" if cur else para
        if weighted_length(candidate) <= budget:
            cur = candidate
            continue
        if cur:
            parts.append(cur)
        if weighted_length(para) <= budget:
            cur = para
        else:
            chunks = _hard_split(para, budget)
            parts.extend(chunks[:-1])
            cur = chunks[-1]
    if cur:
        parts.append(cur)
    if len(parts) > 1:
        parts = [f"{p}\n{i}/{len(parts)}" for i, p in enumerate(parts, 1)]
    return parts


# -- OAuth 1.0a ---------------------------------------------------------------


@dataclass(frozen=True)
class XCredentials:
    api_key: str
    api_secret: str
    access_token: str
    access_token_secret: str

    def __repr__(self) -> str:  # never leak secrets into logs
        return "XCredentials(***)"

    @classmethod
    def from_env(cls) -> "XCredentials":
        names = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            raise XPublishError(f"missing environment variables: {', '.join(missing)}")
        return cls(*(os.environ[n] for n in names))


def _pct(s: str) -> str:
    return quote(s, safe="~-._")


def oauth1_header(
    creds: XCredentials,
    method: str,
    url: str,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Authorization header for a JSON-body request (body is not signed)."""
    params = {
        "oauth_consumer_key": creds.api_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": creds.access_token,
        "oauth_version": "1.0",
    }
    param_str = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(params.items()))
    base = "&".join([method.upper(), _pct(url), _pct(param_str)])
    key = f"{_pct(creds.api_secret)}&{_pct(creds.access_token_secret)}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(params.items()))


# -- client -------------------------------------------------------------------

Transport = Callable[[str, dict, bytes], tuple[int, bytes]]


def _urllib_transport(url: str, headers: dict, body: bytes) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class XClient:
    def __init__(self, creds: XCredentials, transport: Transport | None = None) -> None:
        self.creds = creds
        self.transport = transport or _urllib_transport

    def create_post(self, text: str, reply_to: str | None = None) -> str:
        payload: dict = {"text": text}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to}
        headers = {
            "Authorization": oauth1_header(self.creds, "POST", TWEETS_URL),
            "Content-Type": "application/json",
        }
        status, body = self.transport(TWEETS_URL, headers, json.dumps(payload).encode())
        if status not in (200, 201):
            raise XPublishError(f"X API returned {status}: {body[:300].decode(errors='replace')}")
        return json.loads(body)["data"]["id"]


# -- publish ------------------------------------------------------------------


@dataclass
class XPublishResult:
    decision: str
    parts: list[str] = field(default_factory=list)
    tweet_ids: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    dry_run: bool = True

    @property
    def url(self) -> str | None:
        return f"https://x.com/i/status/{self.tweet_ids[0]}" if self.tweet_ids else None


def card_key(card: ArchiveCard) -> str:
    return card.kind + ":" + ",".join(sorted(c.claim_id for c in card.claims))


def publish_card(
    card: ArchiveCard,
    attestation: publish_guard.ReviewerAttestation,
    clips: list[ClipPlan] = (),
    db: ArchiveDB | None = None,
    client: XClient | None = None,
    dry_run: bool = True,
    text: str | None = None,
) -> XPublishResult:
    """Guard-check and (unless ``dry_run``) post the card as an X thread.

    ``text`` defaults to the Section 16 template; any edited text is
    re-checked by the guard, so edits cannot bypass it."""
    if text is None:
        try:
            text = x_post(card)
        except NotPublishable as e:
            return XPublishResult(publish_guard.BLOCK_PUBLISH, failures=[str(e)], dry_run=dry_run)
    guard = publish_guard.check(card, [text], list(clips), attestation)
    parts = split_thread(text)
    result = XPublishResult(guard.decision, parts, failures=guard.failures, dry_run=dry_run)
    if not guard.allowed or dry_run:
        return result

    if db is None:
        raise XPublishError("a database is required when posting (dedupe + archive_status)")
    if client is None:
        client = XClient(XCredentials.from_env())
    db.conn.executescript(X_POSTS_SCHEMA)
    key = card_key(card)

    done = {
        row["part_index"]: row["tweet_id"]
        for row in db.conn.execute("SELECT part_index, tweet_id FROM x_posts WHERE card_key = ?", (key,))
    }
    reply_to: str | None = None
    for i, part in enumerate(parts):
        if i in done:
            reply_to = done[i]
        else:
            reply_to = client.create_post(part, reply_to)
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO x_posts (card_key, part_index, tweet_id, text, posted_at) VALUES (?, ?, ?, ?, ?)",
                    (key, i, reply_to, part, datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
                )
        result.tweet_ids.append(reply_to)

    for claim in card.claims:
        stored = db.get(claim.claim_id)
        if stored:
            stored.archive_status = ArchiveStatus.PUBLISHED.value
            db.upsert(stored)
    return result
