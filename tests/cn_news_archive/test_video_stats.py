#!/usr/bin/env python3
"""Video render (real ffmpeg when available), X media upload and stats — network faked."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from stateverge.cn_news.archive import clip_builder, publish_guard, video_render, x_stats  # noqa: E402
from stateverge.cn_news.archive import script_generator as sg  # noqa: E402
from stateverge.cn_news.archive import x_publisher as xp  # noqa: E402
from stateverge.cn_news.archive.cloud_runner import CloudRunner  # noqa: E402
from stateverge.cn_news.archive.contradiction import compare  # noqa: E402
from stateverge.cn_news.archive.database import ArchiveDB  # noqa: E402

from tests.cn_news_archive.test_archive import EARLIER, GOOD_REVIEW, LATER, make_claim  # noqa: E402
from tests.cn_news_archive.test_x_publisher import CREDS  # noqa: E402

FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
ELEMENTS = ["HISTORICAL_COMPARISON", "TIMELINE"]


def _card():
    a, b = make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20")
    card = sg.card_from_comparison(compare(a, b))
    return a, b, card, {c.claim_id: clip_builder.plan_clip(c, ELEMENTS) for c in (a, b)}


class TestVideoPlan(unittest.TestCase):
    def test_segments_follow_template(self):
        _, _, card, plans = _card()
        segs = video_render.plan_segments(card, plans)
        self.assertEqual([s.kind for s in segs][:5], ["CARD", "CLIP", "FREEZE", "CLIP", "CARD"])
        self.assertEqual(segs[2].lines, [("Sub", "这是Alex Rivera当时的说法。")])
        self.assertIn(("Caption", "2025年3月  Alex Rivera\n来源:White House"), segs[1].lines)
        self.assertLessEqual(sum(s.duration for s in segs), video_render.X_MAX_SECONDS)

    def test_ass_escaping(self):
        doc = video_render.ass_document([("Sub", "a{\\b}\nc")], 3, "F")
        self.assertIn("a（＼b）\\Nc", doc)
        self.assertNotIn("{\\b}", doc)

    def test_missing_source(self):
        _, _, card, plans = _card()
        with self.assertRaises(video_render.RenderError):
            video_render.render(card, plans, {}, Path(tempfile.mkdtemp()) / "o.mp4", ffmpeg="ffmpeg")


@unittest.skipUnless(FFMPEG, "ffmpeg not installed")
class TestVideoRender(unittest.TestCase):
    def test_render_end_to_end(self):
        tmp = Path(tempfile.mkdtemp())
        a, b, card, plans = _card()
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=25:d=20",
                        "-f", "lavfi", "-i", "sine=d=20", "-shortest", str(tmp / "a.mp4")], check=True)
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=20",
                        str(tmp / "b.mp4")], check=True)  # no audio track
        out = video_render.render(card, plans, {a.claim_id: tmp / "a.mp4", b.claim_id: tmp / "b.mp4"},
                                  tmp / "out.mp4", ffmpeg=FFMPEG)
        info = subprocess.run([FFMPEG, "-hide_banner", "-i", str(out)], capture_output=True, text=True).stderr
        self.assertIn("1280x720", info)
        self.assertIn("Audio: aac", info)
        expected = sum(s.duration for s in video_render.plan_segments(card, plans))
        h, m, s = info.split("Duration: ")[1].split(",")[0].split(":")
        self.assertAlmostEqual(int(h) * 3600 + int(m) * 60 + float(s), expected, delta=1.0)


class FakeX:
    """Records POST/GET calls and answers like the X media + tweets endpoints."""

    def __init__(self, states=("in_progress", "succeeded")):
        self.posts: list[tuple[str, dict, bytes]] = []
        self.gets: list[str] = []
        self.states = list(states)
        self.slept: list[float] = []

    def post(self, url, headers, body):
        self.posts.append((url, headers, body))
        if url.endswith("/initialize"):
            return 200, b'{"data": {"id": "777"}}'
        if url.endswith("/append"):
            return 204, b""
        if url.endswith("/finalize"):
            return 200, json.dumps({"data": {"id": "777", "processing_info": {"state": "pending",
                                                                              "check_after_secs": 2}}}).encode()
        return 201, json.dumps({"data": {"id": str(len(self.posts))}}).encode()

    def get(self, url, headers):
        self.gets.append(url)
        state = self.states.pop(0) if self.states else "succeeded"
        return 200, json.dumps({"data": {"processing_info": {"state": state, "check_after_secs": 1}}}).encode()


class TestUpload(unittest.TestCase):
    def _client(self, fx):
        return xp.XClient(CREDS, fx.post, fx.get, sleep=fx.slept.append)

    def test_chunked_upload_and_attach(self):
        tmp = Path(tempfile.mkdtemp()) / "v.mp4"
        tmp.write_bytes(b"x" * (xp.CHUNK_BYTES * 2 + 10))
        fx = FakeX()
        db = ArchiveDB(":memory:")
        a, b, card, plans = _card()
        db.upsert_many([a, b])
        r = xp.publish_card(card, GOOD_REVIEW, clips=list(plans.values()), db=db,
                            client=self._client(fx), dry_run=False, video=tmp)
        self.assertEqual(r.decision, publish_guard.ALLOW_PUBLISH)
        urls = [u for u, _, _ in fx.posts]
        self.assertEqual(sum(u.endswith("/append") for u in urls), 3)
        self.assertIn(b'name="segment_index"\r\n\r\n2', fx.posts[3][2])
        self.assertTrue(all("command=STATUS&media_id=777" in g for g in fx.gets))
        tweets = [json.loads(body) for u, _, body in fx.posts if u == xp.TWEETS_URL]
        self.assertEqual(tweets[0]["media"], {"media_ids": ["777"]})
        self.assertTrue(all("media" not in t for t in tweets[1:]))

    def test_processing_failure(self):
        tmp = Path(tempfile.mkdtemp()) / "v.mp4"
        tmp.write_bytes(b"x")
        with self.assertRaises(xp.XPublishError):
            self._client(FakeX(states=["failed"])).upload_video(tmp)

    def test_video_requires_clip_plans(self):
        _, _, card, _ = _card()
        r = xp.publish_card(card, GOOD_REVIEW, video=Path("v.mp4"))
        self.assertEqual(r.decision, publish_guard.BLOCK_PUBLISH)

    def test_query_params_change_signature(self):
        h1 = xp.oauth1_header(CREDS, "GET", xp.MEDIA_URL, nonce="n", timestamp="1", query={"media_id": "1"})
        h2 = xp.oauth1_header(CREDS, "GET", xp.MEDIA_URL, nonce="n", timestamp="1", query={"media_id": "2"})
        self.assertNotEqual(h1, h2)
        self.assertNotIn("media_id", h1)


NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def _stats_get(url, headers):
    if "/users/me" in url:
        return 200, json.dumps({"data": {"id": "42", "username": "StatevergeCN",
                                         "public_metrics": {"followers_count": 120}}}).encode()
    posts = [
        {"created_at": (NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z"),
         "public_metrics": {"impression_count": 1000, "like_count": 5, "retweet_count": 2, "reply_count": 1}},
        {"created_at": (NOW - timedelta(days=40)).isoformat().replace("+00:00", "Z"),
         "public_metrics": {"impression_count": 9000, "like_count": 50, "retweet_count": 7, "reply_count": 3}},
    ]
    return 200, json.dumps({"data": posts, "meta": {}}).encode()


class TestStats(unittest.TestCase):
    def test_snapshot_and_history(self):
        client = xp.XClient(CREDS, get_transport=_stats_get)
        snap = x_stats.take_snapshot(client, now=NOW)
        self.assertEqual((snap.followers, snap.posts_7d, snap.impressions_7d, snap.impressions_90d),
                         (120, 1, 1000, 10000))
        path = Path(tempfile.mkdtemp()) / "x_stats.json"
        x_stats.record(snap, path)
        snap.followers = 150
        report = x_stats.record(snap, path)
        self.assertIn("关注者:150 (+30)", report)
        self.assertIn("10,000 / 5,000,000", report)
        self.assertIn("Creator Studio", report)
        self.assertEqual(len(json.loads(path.read_text())), 2)

    def test_cloud_stats_queue(self):
        root = Path(tempfile.mkdtemp())
        (root / "claims").mkdir()
        (root / "stats_requests.json").write_text('[{"status": "pending"}]')
        CloudRunner(root, providers=[], x_client=xp.XClient(CREDS, get_transport=_stats_get)).run()
        req = json.loads((root / "stats_requests.json").read_text())[0]
        self.assertEqual(req["status"], "done")
        self.assertIn("StatevergeCN", req["report"])
        # Nothing pending and no schedule → no API reads.
        calls = []
        CloudRunner(root, providers=[], x_client=xp.XClient(CREDS, get_transport=lambda u, h: calls.append(u))).run()
        self.assertEqual(calls, [])

    def test_api_error_recorded(self):
        root = Path(tempfile.mkdtemp())
        (root / "claims").mkdir()
        (root / "stats_requests.json").write_text('[{"status": "pending"}]')
        CloudRunner(root, providers=[], x_client=xp.XClient(CREDS, get_transport=lambda u, h: (401, b"no"))).run()
        self.assertEqual(json.loads((root / "stats_requests.json").read_text())[0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
