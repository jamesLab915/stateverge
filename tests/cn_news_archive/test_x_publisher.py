#!/usr/bin/env python3
"""X publisher tests. HTTP is faked; nothing is sent to X."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stateverge.cn_news.archive import publish_guard  # noqa: E402
from stateverge.cn_news.archive import script_generator as sg  # noqa: E402
from stateverge.cn_news.archive import x_publisher as xp  # noqa: E402
from stateverge.cn_news.archive.contradiction import compare  # noqa: E402
from stateverge.cn_news.archive.database import ArchiveDB  # noqa: E402
from stateverge.cn_news.archive.models import ArchiveStatus  # noqa: E402

from tests.cn_news_archive.test_archive import EARLIER, GOOD_REVIEW, LATER, make_claim  # noqa: E402

CREDS = xp.XCredentials("ck", "cs", "at", "ats")


class FakeTransport:
    def __init__(self, fail_on: int | None = None):
        self.calls: list[dict] = []
        self.fail_on = fail_on

    def __call__(self, url, headers, body):
        n = len(self.calls)
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body)})
        if self.fail_on is not None and n == self.fail_on:
            return 503, b'{"title":"Service Unavailable"}'
        return 201, json.dumps({"data": {"id": str(1000 + n), "text": "x"}}).encode()


def _setup():
    db = ArchiveDB(":memory:")
    a, b = make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20")
    db.upsert_many([a, b])
    return db, sg.card_from_comparison(compare(a, b))


class TestLength(unittest.TestCase):
    def test_weights(self):
        self.assertEqual(xp.weighted_length("abc"), 3)
        self.assertEqual(xp.weighted_length("录像"), 4)
        self.assertEqual(xp.weighted_length("📼"), 2)
        self.assertEqual(xp.weighted_length("see https://www.whitehouse.gov/a/very/long/path/indeed"), 4 + 23)

    def test_split_thread_respects_limit(self):
        _, card = _setup()
        parts = xp.split_thread(sg.x_post(card))
        self.assertGreater(len(parts), 1)
        for i, p in enumerate(parts, 1):
            self.assertLessEqual(xp.weighted_length(p), 280, p)
            self.assertTrue(p.endswith(f"{i}/{len(parts)}"))

    def test_oversized_paragraph(self):
        parts = xp.split_thread("长" * 400)
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(xp.weighted_length(p) <= 280 for p in parts))

    def test_short_text_single_post(self):
        self.assertEqual(xp.split_thread("hello"), ["hello"])


class TestOAuth(unittest.TestCase):
    def test_header_deterministic_and_complete(self):
        h1 = xp.oauth1_header(CREDS, "POST", xp.TWEETS_URL, nonce="n", timestamp="1")
        h2 = xp.oauth1_header(CREDS, "POST", xp.TWEETS_URL, nonce="n", timestamp="1")
        self.assertEqual(h1, h2)
        for k in ("oauth_consumer_key=\"ck\"", "oauth_token=\"at\"", "oauth_signature=", "HMAC-SHA1"):
            self.assertIn(k, h1)
        self.assertNotIn("cs", h1.replace("oauth_", ""))

    def test_creds_not_in_repr(self):
        self.assertNotIn("ats", repr(CREDS))

    def test_missing_env(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")}
        try:
            with self.assertRaises(xp.XPublishError):
                xp.XCredentials.from_env()
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class TestPublish(unittest.TestCase):
    def test_dry_run_posts_nothing(self):
        db, card = _setup()
        t = FakeTransport()
        r = xp.publish_card(card, GOOD_REVIEW, db=db, client=xp.XClient(CREDS, t))
        self.assertEqual(r.decision, publish_guard.ALLOW_PUBLISH)
        self.assertTrue(r.dry_run)
        self.assertEqual(t.calls, [])

    def test_posts_thread_as_replies(self):
        db, card = _setup()
        t = FakeTransport()
        r = xp.publish_card(card, GOOD_REVIEW, db=db, client=xp.XClient(CREDS, t), dry_run=False)
        self.assertEqual(len(t.calls), len(r.parts))
        self.assertNotIn("reply", t.calls[0]["body"])
        for i in range(1, len(t.calls)):
            self.assertEqual(t.calls[i]["body"]["reply"]["in_reply_to_tweet_id"], str(1000 + i - 1))
        self.assertEqual(r.url, "https://x.com/i/status/1000")
        self.assertEqual(db.get(card.claims[0].claim_id).archive_status, ArchiveStatus.PUBLISHED.value)

    def test_blocked_card_never_posts(self):
        db, card = _setup()
        t = FakeTransport()
        r = xp.publish_card(card, publish_guard.ReviewerAttestation(), db=db, client=xp.XClient(CREDS, t), dry_run=False)
        self.assertEqual(r.decision, publish_guard.BLOCK_PUBLISH)
        self.assertIn("human_reviewed: 缺少审核人署名", r.failures)
        self.assertEqual(t.calls, [])

    def test_edited_text_rechecked(self):
        db, card = _setup()
        t = FakeTransport()
        r = xp.publish_card(card, GOOD_REVIEW, db=db, client=xp.XClient(CREDS, t), dry_run=False,
                            text=sg.x_post(card) + "\n\n骗子!")
        self.assertEqual(r.decision, publish_guard.BLOCK_PUBLISH)
        self.assertEqual(t.calls, [])

    def test_insufficient_evidence_blocked(self):
        db = ArchiveDB(":memory:")
        a = make_claim(EARLIER, "2025-03-01", transcript_verified=False)
        card = sg.card_from_comparison(compare(a, make_claim(LATER, "2026-09-20")))
        r = xp.publish_card(card, GOOD_REVIEW, db=db, dry_run=False)
        self.assertEqual(r.decision, publish_guard.BLOCK_PUBLISH)

    def test_retry_resumes_without_duplicates(self):
        db, card = _setup()
        failing = FakeTransport(fail_on=1)
        with self.assertRaises(xp.XPublishError):
            xp.publish_card(card, GOOD_REVIEW, db=db, client=xp.XClient(CREDS, failing), dry_run=False)
        t = FakeTransport()
        r = xp.publish_card(card, GOOD_REVIEW, db=db, client=xp.XClient(CREDS, t), dry_run=False)
        self.assertEqual(len(t.calls), len(r.parts) - 1)  # first post not repeated
        self.assertEqual(t.calls[0]["body"]["reply"]["in_reply_to_tweet_id"], "1000")
        self.assertEqual(r.tweet_ids[0], "1000")


if __name__ == "__main__":
    unittest.main()
