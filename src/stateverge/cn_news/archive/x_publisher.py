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
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from pathlib import Path
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
        missing = [n for n in names if not os.environ.get(n, "").strip()]
        if missing:
            raise XPublishError(f"missing environment variables: {', '.join(missing)}")
        return cls(*(os.environ[n].strip() for n in names))


def _pct(s: str) -> str:
    return quote(s, safe="~-._")


def oauth1_header(
    creds: XCredentials,
    method: str,
    url: str,
    nonce: str | None = None,
    timestamp: str | None = None,
    query: dict[str, str] | None = None,
) -> str:
    """Authorization header. JSON and multipart bodies are not signed; query
    string parameters (GET) are, and ``url`` must exclude the query string."""
    params = {
        "oauth_consumer_key": creds.api_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": creds.access_token,
        "oauth_version": "1.0",
    }
    signed = {**params, **(query or {})}
    param_str = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(signed.items()))
    base = "&".join([method.upper(), _pct(url), _pct(param_str)])
    key = f"{_pct(creds.api_secret)}&{_pct(creds.access_token_secret)}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(params.items()))


# -- client -------------------------------------------------------------------

Transport = Callable[[str, dict, bytes], tuple[int, bytes]]
GetTransport = Callable[[str, dict], tuple[int, bytes]]

MEDIA_URL = "https://api.x.com/2/media/upload"
CHUNK_BYTES = 4 * 1024 * 1024  # X recommends <= 5 MB per APPEND segment


def _urllib_transport(url: str, headers: dict, body: bytes) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _urllib_get(url: str, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _multipart(fields: dict[str, str], file_field: str, data: bytes) -> tuple[str, bytes]:
    boundary = "----stateverge" + secrets.token_hex(12)
    out = bytearray()
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="blob"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    out += data + f"\r\n--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", bytes(out)


class XClient:
    def __init__(
        self,
        creds: XCredentials,
        transport: Transport | None = None,
        get_transport: GetTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.creds = creds
        self.transport = transport or _urllib_transport
        self.get_transport = get_transport or _urllib_get
        self.sleep = sleep

    def _check(self, status: int, body: bytes, what: str) -> dict:
        if status not in (200, 201, 202, 204):
            raise XPublishError(f"{what}: X API returned {status}: {body[:300].decode(errors='replace')}")
        return json.loads(body) if body.strip() else {}

    def upload_video(self, path: Path, max_wait: float = 600) -> str:
        """Chunked upload (POST /2/media/upload/initialize → /{id}/append →
        /{id}/finalize, then poll STATUS). Returns the media id."""
        data = Path(path).read_bytes()
        init_url = f"{MEDIA_URL}/initialize"
        body = json.dumps({"media_type": "video/mp4", "total_bytes": len(data), "media_category": "tweet_video"})
        headers = {"Authorization": oauth1_header(self.creds, "POST", init_url), "Content-Type": "application/json"}
        media_id = str(self._check(*self.transport(init_url, headers, body.encode()), "initialize")["data"]["id"])

        append_url = f"{MEDIA_URL}/{media_id}/append"
        for index, offset in enumerate(range(0, len(data), CHUNK_BYTES)):
            ctype, payload = _multipart({"segment_index": str(index)}, "media", data[offset : offset + CHUNK_BYTES])
            headers = {"Authorization": oauth1_header(self.creds, "POST", append_url), "Content-Type": ctype}
            self._check(*self.transport(append_url, headers, payload), f"append #{index}")

        fin_url = f"{MEDIA_URL}/{media_id}/finalize"
        headers = {"Authorization": oauth1_header(self.creds, "POST", fin_url)}
        info = self._check(*self.transport(fin_url, headers, b""), "finalize").get("data", {}).get("processing_info")

        waited = 0.0
        while info and info.get("state") in ("pending", "in_progress"):
            delay = float(info.get("check_after_secs", 5))
            if waited + delay > max_wait:
                raise XPublishError(f"video processing did not finish within {max_wait:.0f}s")
            self.sleep(delay)
            waited += delay
            query = {"command": "STATUS", "media_id": media_id}
            headers = {"Authorization": oauth1_header(self.creds, "GET", MEDIA_URL, query=query)}
            url = f"{MEDIA_URL}?command=STATUS&media_id={quote(media_id)}"
            info = self._check(*self.get_transport(url, headers), "status").get("data", {}).get("processing_info")
        if info and info.get("state") == "failed":
            raise XPublishError(f"video processing failed: {info.get('error', info)}")
        return media_id

    def create_post(self, text: str, reply_to: str | None = None, media_ids: list[str] | None = None) -> str:
        payload: dict = {"text": text}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to}
        if media_ids:
            payload["media"] = {"media_ids": list(media_ids)}
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


def post_thread(
    client: XClient,
    parts: list[str],
    key: str,
    conn: sqlite3.Connection,
    video: Path | None = None,
) -> list[str]:
    """Post ``parts`` as a reply chain, recording each post under ``key`` in
    ``x_posts`` so a retry resumes the thread instead of duplicating it."""
    conn.executescript(X_POSTS_SCHEMA)
    done = {
        row[0]: row[1]
        for row in conn.execute("SELECT part_index, tweet_id FROM x_posts WHERE card_key = ?", (key,))
    }
    ids: list[str] = []
    reply_to: str | None = None
    for i, part in enumerate(parts):
        if i in done:
            reply_to = done[i]
        else:
            media = [client.upload_video(Path(video))] if (i == 0 and video is not None) else None
            reply_to = client.create_post(part, reply_to, media)
            with conn:
                conn.execute(
                    "INSERT INTO x_posts (card_key, part_index, tweet_id, text, posted_at) VALUES (?, ?, ?, ?, ?)",
                    (key, i, reply_to, part, datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
                )
        ids.append(reply_to)
    return ids


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
    video: Path | None = None,
) -> XPublishResult:
    """Guard-check and (unless ``dry_run``) post the card as an X thread.

    ``text`` defaults to the Section 16 template; any edited text is
    re-checked by the guard, so edits cannot bypass it. ``video`` (rendered by
    video_render) is attached to the first post; pass its ``clips`` so the
    guard checks excerpt length and transformative content."""
    if video is not None and not clips:
        return XPublishResult(publish_guard.BLOCK_PUBLISH, failures=["video needs its clip plans for the guard"],
                              dry_run=dry_run)
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
    result.tweet_ids = post_thread(client, parts, card_key(card), db.conn, video=video)

    for claim in card.claims:
        stored = db.get(claim.claim_id)
        if stored:
            stored.archive_status = ArchiveStatus.PUBLISHED.value
            db.upsert(stored)
    return result
