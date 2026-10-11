#!/usr/bin/env python3
"""Finance daily — network faked; the assembly test runs real ffmpeg when available."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

try:
    import PIL  # noqa: F401
except ImportError:  # the archive workflow runs without Pillow
    raise unittest.SkipTest("Pillow not installed") from None

from stateverge.finance_daily import market, news, script, visuals  # noqa: E402
from stateverge.finance_daily.market import Quote  # noqa: E402
from stateverge.finance_daily.news import Headline  # noqa: E402
from stateverge.finance_daily.runway import RunwayClient, RunwayError  # noqa: E402
from stateverge.finance_daily.script import Script, Segment, check  # noqa: E402

NOW = datetime(2026, 10, 9, 21, tzinfo=timezone.utc)


def yahoo_json(closes):
    ts = [1759700000 + 86400 * i for i in range(len(closes))]
    return json.dumps({"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [{"close": closes}]}}]}}).encode()


QUOTES = [
    Quote("^GSPC", "标普500", "点", 6712.34, 6650.10, "2026-10-09", [6600, 6650.10, 6712.34], "yahoo"),
    Quote("^IXIC", "纳斯达克", "点", 22801.5, 22900.0, "2026-10-09", [22950, 22900, 22801.5], "yahoo"),
    Quote("^DJI", "道琼斯", "点", 46210.0, 46100.0, "2026-10-09", [46000, 46100, 46210], "yahoo"),
    Quote("^TNX", "10年期美债收益率", "%", 4.12, 4.05, "2026-10-09", [4.0, 4.05, 4.12], "yahoo"),
]
HEADS = [Headline("CNBC", "Fed minutes show officials split on pace of cuts", "https://example.com/a",
                  "Officials debated 2 more cuts this year.", NOW.isoformat())]


def good_script() -> Script:
    filler = "这一变化对在美国工作和投资的华人来说,意味着贷款成本和美元汇率都可能受到影响。" * 6
    return Script("美股分化 标普收涨0.94%", "今日美股收盘概况。", [
        Segment("anchor", "大家好,欢迎收看今天的美国财经日报。"),
        Segment("vo", f"标普500收于6,712.34点,上涨0.94%;纳斯达克下跌0.43%。{filler}", {"type": "board"}),
        Segment("vo", f"美联储会议纪要显示官员对今年再降息2次存在分歧。{filler}", {"type": "headline", "index": 0}),
        Segment("anchor", "接下来看看利率和美元。"),
        Segment("vo", f"10年期美债收益率升至4.12%。{filler}", {"type": "chart", "symbol": "^TNX"}),
        Segment("anchor", "以上就是今天的内容,我们明天见。"),
    ])


class TestMarket(unittest.TestCase):
    def test_yahoo_then_stooq_fallback(self):
        def fetch(url):
            if "yahoo" in url and "GSPC" in url:
                return yahoo_json([6600.0, None, 6650.1, 6712.34])
            if "stooq" in url and "ndq" in url:
                return b"Date,Open,High,Low,Close,Volume\n2026-10-08,1,1,1,22900,0\n2026-10-09,1,1,1,22801.5,0\n"
            raise OSError("down")
        qs = market.snapshot(fetch, market.SYMBOLS[:3])
        self.assertEqual([q.symbol for q in qs], ["^GSPC", "^IXIC"])  # DJI failed on both → left out
        self.assertAlmostEqual(qs[0].change_pct, (6712.34 / 6650.1 - 1) * 100)
        self.assertEqual(qs[1].source, "stooq")


class TestNews(unittest.TestCase):
    def test_recent_unique(self):
        rss = (b"<rss><channel><item><title>Fed minutes</title><link>https://x/a</link>"
               b"<pubDate>Fri, 09 Oct 2026 18:00:00 GMT</pubDate></item>"
               b"<item><title>Old</title><link>https://x/b</link><pubDate>Mon, 05 Oct 2026 18:00:00 GMT</pubDate></item>"
               b"</channel></rss>")
        hs = news.headlines(lambda url: rss, now=NOW, feeds={"A": "u1", "B": "u2"})
        self.assertEqual([h.title for h in hs], ["Fed minutes"])


class TestGuard(unittest.TestCase):
    def test_good_script_passes(self):
        self.assertEqual(check(good_script(), QUOTES, HEADS), [])

    def test_invented_number_and_advice_blocked(self):
        s = good_script()
        s.segments[1].text += "预计明天标普将升至7000点,建议买入。"
        problems = " ".join(check(s, QUOTES, HEADS))
        self.assertIn("7000", problems)
        self.assertIn("建议买入", problems)

    def test_anchor_rules(self):
        s = good_script()
        s.segments[0].text = "大家好" * 12
        s.segments.pop(3)
        problems = " ".join(check(s, QUOTES, HEADS))
        self.assertIn("anchor 段应为 3 段", problems)
        self.assertIn("超过", problems)

    def test_writer_retries_once_with_problems(self):
        calls = []
        bad = good_script().to_dict()
        bad["segments"][1]["text"] += "目标价9999。"

        def post(url, headers, body):
            calls.append(json.loads(body))
            content = bad if len(calls) == 1 else good_script().to_dict()
            return json.dumps({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}).encode()

        out = script.ScriptWriter("k", post=post).write(QUOTES, HEADS)
        self.assertEqual(len(calls), 2)
        self.assertIn("9999", calls[1]["messages"][-1]["content"])
        self.assertEqual(out.problems, [])


class TestRunway(unittest.TestCase):
    def test_avatar_video_flow(self):
        seen = []

        def request(method, url, headers, body):
            seen.append((method, url.split(".com")[1], json.loads(body) if body else None))
            self.assertEqual(headers["X-Runway-Version"], "2024-11-06")
            if url.endswith("/v1/avatar_videos"):
                return b'{"id": "t1"}'
            n = sum(1 for m, p, _ in seen if p == "/v1/tasks/t1")
            return json.dumps({"status": "RUNNING"} if n < 2 else {"status": "SUCCEEDED", "output": ["https://v/1.mp4"]}).encode()

        with tempfile.TemporaryDirectory() as d:
            audio = Path(d) / "a.mp3"
            audio.write_bytes(b"ID3fake")
            url = RunwayClient("k", request, sleep=lambda s: None).avatar_video("av1", audio)
        self.assertEqual(url, "https://v/1.mp4")
        body = seen[0][2]
        self.assertEqual(body["model"], "gwm1_avatars")
        self.assertEqual(body["avatar"], {"type": "custom", "avatarId": "av1"})
        self.assertTrue(body["speech"]["audio"].startswith("data:audio/mpeg;base64,"))

    def test_failed_task_raises(self):
        def request(method, url, headers, body):
            return b'{"id": "t"}' if method == "POST" else b'{"status": "FAILED", "failure": "moderation"}'
        with tempfile.TemporaryDirectory() as d:
            audio = Path(d) / "a.mp3"
            audio.write_bytes(b"x")
            with self.assertRaises(RunwayError):
                RunwayClient("k", request, sleep=lambda s: None).avatar_video("av", audio)


def _have_font() -> bool:
    try:
        visuals.font(10)
        return True
    except FileNotFoundError:
        return False


@unittest.skipUnless(shutil.which("ffmpeg") and _have_font(), "needs ffmpeg and a CJK font")
class TestEndToEnd(unittest.TestCase):
    def test_episode_with_fake_services(self):
        from stateverge.finance_daily.runner import FinanceRunner

        tmp = Path(tempfile.mkdtemp())
        anchor_src = tmp / "runway_out.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=1280x720:r=24",
                        "-t", "1", str(anchor_src)], check=True)

        class FakeSpeaker:
            voice = "onyx"

            def speak(self, text, out):
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=220",
                                "-t", "0.6", str(out)], check=True)
                return out

        class FakeWriter:
            def write(self, q, h):
                return good_script()

        class FakeRunway:
            available = True
            created = 0

            def create_avatar(self, name, portrait):
                self.created += 1
                return "av-1"

            def wait_avatar(self, a):
                return {"status": "READY"}

            def avatar_video(self, avatar, audio):
                return anchor_src.resolve().as_uri()

        def portrait(p):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=1536x1024",
                            "-frames:v", "1", str(p)], check=True)
            return p

        root = tmp / "data"
        root.mkdir()
        (root / "requests.json").write_text('[{"status": "pending"}]')
        published = []
        rw = FakeRunway()
        runner = FinanceRunner(root, tmp / "work", quotes_fn=lambda: QUOTES, news_fn=lambda: HEADS,
                               writer=FakeWriter(), speaker=FakeSpeaker(), runway=rw, portrait_fn=portrait,
                               publish=lambda v, tag, t, b: published.append((v, b)) or "https://dl/v.mp4")
        runner.run()
        item = json.loads((root / "requests.json").read_text())[0]
        self.assertEqual(item["status"], "rendered", item)
        self.assertEqual(item["anchor_clips"], 3)
        self.assertEqual(item["video_url"], "https://dl/v.mp4")
        self.assertTrue(published[0][0].stat().st_size > 10_000)
        self.assertIn("不构成投资建议", published[0][1])
        self.assertEqual(json.loads((root / "avatar.json").read_text())["avatar_id"], "av-1")
        self.assertTrue((root / "episodes" / "2026-10-09.json").exists())

        # second run reuses the avatar
        (root / "requests.json").write_text('[{"status": "pending"}]')
        FinanceRunner(root, tmp / "work2", quotes_fn=lambda: QUOTES, news_fn=lambda: HEADS, writer=FakeWriter(),
                      speaker=FakeSpeaker(), runway=rw, portrait_fn=portrait, publish=lambda *a: "u").run()
        self.assertEqual(rw.created, 1)


if __name__ == "__main__":
    unittest.main()
