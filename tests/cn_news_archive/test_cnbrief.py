#!/usr/bin/env python3
"""国内要闻 — network, OpenAI and X faked. Headlines below are fictional."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from stateverge.cn_news.archive import x_publisher as xp  # noqa: E402
from stateverge.cn_news.cnbrief import sources  # noqa: E402
from stateverge.cn_news.cnbrief.editor import Editor, post_parts  # noqa: E402
from stateverge.cn_news.cnbrief.guard import check  # noqa: E402
from stateverge.cn_news.cnbrief.runner import CnBriefRunner  # noqa: E402

from tests.cn_news_archive.test_x_publisher import CREDS, FakeTransport  # noqa: E402

NOW = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)


def rss(items):
    body = "".join(f"<item><title>{t} - {o}</title><link>https://news.example/{i}</link>"
                   f"<pubDate>Wed, 14 Oct 2026 00:00:00 GMT</pubDate><source url='https://{o}'>{o}</source></item>"
                   for i, (t, o) in enumerate(items))
    return f"<rss><channel>{body}</channel></rss>".encode()


def fetch(url):
    if "%E5%A4%AE%E8%A1%8C" in url:  # 央行 topic
        return rss([("示例银行宣布下调五年期LPR 10个基点", "示例日报"), ("五年期LPR下调10个基点", "示例财经"),
                    ("某省发生示例事故", "示例晚报")])
    return rss([])


def editor_reply(events):
    def post(url, headers, body):
        return json.dumps({"choices": [{"message": {"content": json.dumps({"events": events}, ensure_ascii=False)}}]}).encode()
    return Editor("k", post=post)


GOOD = {"category": "利率货币", "title": "五年期LPR下调10个基点", "facts": ["两家媒体报道下调10个基点"],
        "why": "影响房贷利率", "source_ids": [0, 1], "importance": 90, "political": False}


class TestSources(unittest.TestCase):
    def test_outlet_stripped_and_kept(self):
        hs = sources.collect(fetch, now=NOW, topics={"利率货币": "央行"})
        self.assertEqual(hs[0].title, "示例银行宣布下调五年期LPR 10个基点")
        self.assertEqual(hs[0].outlet, "示例日报")


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.by_id = {h.id: h.to_dict() for h in sources.collect(fetch, now=NOW, topics={"利率货币": "央行"})}

    def test_good_event_passes_and_links_go_in_reply(self):
        parts = post_parts(GOOD, self.by_id)
        self.assertNotIn("https://", parts[0])
        self.assertIn("https://", parts[-1])
        self.assertEqual(check(GOOD, self.by_id, "\n".join(parts)), [])

    def test_single_outlet_blocked(self):
        ev = dict(GOOD, source_ids=[0])
        self.assertTrue(any("独立来源" in p for p in check(ev, self.by_id, "\n".join(post_parts(ev, self.by_id)))))

    def test_political_and_hype_blocked(self):
        ev = dict(GOOD, title="震惊!政治局会议定调楼市")
        problems = " ".join(check(ev, self.by_id, "\n".join(post_parts(ev, self.by_id))))
        self.assertIn("政治", problems)
        self.assertIn("震惊", problems)

    def test_out_of_scope_category(self):
        ev = dict(GOOD, category="外交")
        self.assertTrue(check(ev, self.by_id, "\n".join(post_parts(ev, self.by_id))))


class TestRunner(unittest.TestCase):
    def test_auto_schedules_spaced_then_posts_once(self):
        root = Path(tempfile.mkdtemp())
        (root / "requests.json").write_text('[{"status": "pending", "auto": true}]')
        bad = dict(GOOD, title="另一件事", source_ids=[0])
        CnBriefRunner(root, fetch, editor_reply([GOOD, bad])).run(now=NOW)
        item = json.loads((root / "requests.json").read_text())[0]
        self.assertEqual(item["status"], "scheduled", item)
        posts = json.loads((root / "posts.json").read_text())
        self.assertEqual(len(posts), 1)  # the single-source event never gets scheduled
        due = datetime.fromisoformat(posts[0]["post_after"])
        self.assertEqual(due, NOW + timedelta(hours=2))

        t = FakeTransport()
        CnBriefRunner(root, fetch, editor_reply([]), xp.XClient(CREDS, t)).run(now=due - timedelta(minutes=1))
        self.assertEqual(t.calls, [])
        CnBriefRunner(root, fetch, editor_reply([]), xp.XClient(CREDS, t)).run(now=due + timedelta(minutes=1))
        self.assertEqual(json.loads((root / "posts.json").read_text())[0]["status"], "posted")
        n = len(t.calls)
        CnBriefRunner(root, fetch, editor_reply([]), xp.XClient(CREDS, t)).run(now=due + timedelta(hours=5))
        self.assertEqual(len(t.calls), n)

    def test_veto(self):
        root = Path(tempfile.mkdtemp())
        (root / "requests.json").write_text('[{"status": "pending", "auto": true}]')
        CnBriefRunner(root, fetch, editor_reply([GOOD])).run(now=NOW)
        posts = json.loads((root / "posts.json").read_text())
        posts[0]["status"] = "vetoed"
        (root / "posts.json").write_text(json.dumps(posts))
        t = FakeTransport()
        CnBriefRunner(root, fetch, editor_reply([]), xp.XClient(CREDS, t)).run(now=NOW + timedelta(days=1))
        self.assertEqual(t.calls, [])

    def test_already_posted_event_not_rescheduled(self):
        root = Path(tempfile.mkdtemp())
        (root / "requests.json").write_text('[{"status": "pending", "auto": true}]')
        t = FakeTransport()
        CnBriefRunner(root, fetch, editor_reply([GOOD]), xp.XClient(CREDS, t)).run(now=NOW)
        (root / "requests.json").write_text('[{"status": "pending", "auto": true}]')
        CnBriefRunner(root, fetch, editor_reply([GOOD]), xp.XClient(CREDS, t)).run(now=NOW + timedelta(days=1))
        self.assertEqual(len(json.loads((root / "posts.json").read_text())), 1)


if __name__ == "__main__":
    unittest.main()
