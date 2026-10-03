#!/usr/bin/env python3
"""Information Gap engine — all network (HN, GitHub, Reddit, Brave, OpenAI, X) faked.

The fixture items below are fictional.
"""

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
from stateverge.cn_news.infogap import digest  # noqa: E402
from stateverge.cn_news.infogap.assessor import PLACEHOLDER, LLMAssessor  # noqa: E402
from stateverge.cn_news.infogap.collectors import (  # noqa: E402
    GitHubCollector,
    HackerNewsCollector,
    RedditCollector,
    collect_all,
)
from stateverge.cn_news.infogap.coverage import BraveCoverage, NoCoverage, query_for  # noqa: E402
from stateverge.cn_news.infogap.models import Coverage, Signal, Track  # noqa: E402
from stateverge.cn_news.infogap.runner import InfoGapRunner  # noqa: E402
from stateverge.cn_news.infogap.scoring import heat, heuristic_assessment, scarcity, score  # noqa: E402

from tests.cn_news_archive.test_x_publisher import CREDS, FakeTransport  # noqa: E402

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
T = int((NOW - timedelta(hours=3)).timestamp())
OLD = int((NOW - timedelta(days=3)).timestamp())


def fake_web(url: str, headers: dict) -> bytes:
    if url.endswith("/topstories.json"):
        return json.dumps([1, 2, 3, 4]).encode()
    if "/item/" in url:
        i = int(url.rsplit("/", 1)[1].split(".")[0])
        items = {
            1: {"id": 1, "type": "story", "title": "Show HN: Ledgerly, an open-source AI agent that does small-business bookkeeping",
                "url": "https://example.com/ledgerly", "score": 640, "descendants": 210, "time": T},
            2: {"id": 2, "type": "story", "title": "Old story", "url": "https://example.com/old", "score": 900, "time": OLD},
            3: {"id": 3, "type": "job", "title": "Hiring", "time": T},
            4: {"id": 4, "type": "story", "title": "Data center power demand is reshaping US electricity prices",
                "url": "https://example.com/grid", "score": 420, "descendants": 380, "time": T},
        }
        return json.dumps(items[i]).encode()
    if "api.github.com" in url:
        return json.dumps({"items": [
            {"full_name": "acme/voice-clone-studio", "html_url": "https://github.com/acme/voice-clone-studio",
             "description": "Self-hosted AI voice tool", "created_at": "2026-09-30T00:00:00Z",
             "stargazers_count": 1800, "forks_count": 90, "topics": ["ai", "tts"], "language": "Python"},
        ]}).encode()
    if "reddit.com/r/SideProject" in url:
        return json.dumps({"data": {"children": [
            {"data": {"title": "How I made $4k/month selling printable planners on Etsy with AI", "ups": 2300,
                      "num_comments": 410, "created_utc": T, "permalink": "/r/SideProject/comments/abc/x/",
                      "selftext": "Details inside", "stickied": False}},
            {"data": {"title": "Pinned rules", "ups": 5, "created_utc": T, "permalink": "/r/x", "stickied": True}},
        ]}}).encode()
    if "reddit.com" in url:
        raise OSError("blocked")
    raise AssertionError(url)


def collectors():
    return [HackerNewsCollector(fake_web), GitHubCollector(fake_web), RedditCollector(fake_web, ["SideProject", "Costco"])]


def brave(counts: dict[str, int]):
    def fetch(url, headers):
        assert headers["X-Subscription-Token"] == "k"
        n = next((v for k, v in counts.items() if k in url), 0)
        return json.dumps({"web": {"results": [{"title": f"中文报道{i}", "url": f"https://cn.example/{i}"}
                                               for i in range(n)]}}).encode()
    return BraveCoverage("k", fetch)


def llm(track="AI_TECH", risks="需要自行确认数据安全和地区限制"):
    def post(url, headers, body):
        title = json.loads(body)["messages"][1]["content"].split("标题:")[1].split("\n")[0]
        content = {"track": track, "practical_value": 85, "discussion": 70, "visual": 60,
                   "headline_zh": f"中文用户还没注意到:{title[:20]}", "what": "w", "why_now": "n", "how_used": "h",
                   "why_cn": "这对中文创作者意味着成本下降", "opportunity": "o", "risks": risks, "x_line": f"一句话:{title[:15]}"}
        return json.dumps({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}).encode()
    return LLMAssessor("k", post=post)


class TestCollect(unittest.TestCase):
    def test_collect_and_filter(self):
        sigs = collect_all(collectors(), now=NOW)
        titles = [s.title for s in sigs]
        self.assertEqual(len(sigs), 4)
        self.assertNotIn("Old story", titles)
        self.assertNotIn("Hiring", titles)
        self.assertNotIn("Pinned rules", titles)
        hn = next(s for s in sigs if s.source == "hackernews" and "Ledgerly" in s.title)
        self.assertEqual(hn.discussion_url, "https://news.ycombinator.com/item?id=1")

    def test_dedupe_by_url(self):
        a = Signal("hackernews", "A", "https://www.example.com/x/?utm=1", points=10)
        b = Signal("reddit:x", "A", "http://example.com/x", points=500)

        class C:
            def __init__(self, s):
                self.s = s

            def collect(self, since):
                return [self.s]

        out = collect_all([C(a), C(b)], now=NOW)
        self.assertEqual([s.points for s in out], [500])


class TestScoring(unittest.TestCase):
    def test_heat_and_scarcity(self):
        self.assertEqual(heat(Signal("hackernews", "t", "u", points=1000)), 100.0)
        self.assertLess(heat(Signal("hackernews", "t", "u", points=10)), 40)
        self.assertEqual(scarcity(Coverage(True, zh_results=0)), 100)
        self.assertEqual(scarcity(Coverage(True, zh_results=30)), 10)
        self.assertEqual(scarcity(Coverage(False)), 50)

    def test_heuristic_tracks(self):
        self.assertEqual(heuristic_assessment(Signal("x", "Open-source LLM agent", "u")).track, Track.AI_TECH)
        self.assertEqual(heuristic_assessment(Signal("x", "Rent and salary in Ohio", "u")).track, Track.US_LIFE)
        self.assertEqual(heuristic_assessment(Signal("x", "Data center power grid strain", "u")).track, Track.INDUSTRY)

    def test_weights(self):
        s = Signal("hackernews", "t", "u", points=1000)
        a = heuristic_assessment(s)
        a.practical_value = a.discussion = a.visual = 100
        self.assertEqual(score(s, Coverage(True, zh_results=0), a).total, 100.0)
        self.assertIn("未经 AI 评估", score(s, Coverage(False), a).notes[-1])

    def test_query(self):
        self.assertEqual(query_for(Signal("github", "acme/voice-clone-studio", "u")), "voice clone studio")
        self.assertIn("Ledgerly", query_for(Signal("hackernews", "Show HN: Ledgerly, an AI agent", "u")))

    def test_brave_counts_only_chinese(self):
        def fetch(url, headers):
            self.assertIn("search_lang=zh-hans", url)
            self.assertIn("freshness=pw", url)
            return json.dumps({"web": {"results": [{"title": "English only"}, {"title": "中文"}]}}).encode()
        cov = BraveCoverage("k", fetch).check(Signal("hackernews", "Ledgerly agent", "u"))
        self.assertEqual((cov.checked, cov.zh_results), (True, 1))
        self.assertFalse(NoCoverage().check(Signal("x", "t", "u")).checked)


class TestDigest(unittest.TestCase):
    def test_scan_selects_gaps_with_track_diversity(self):
        r = digest.scan(collectors(), brave({"q=Data": 30}), llm(), now=NOW)
        self.assertEqual(r.collected, 4)
        self.assertTrue(all(c.total >= 70 for c in r.candidates))
        self.assertLessEqual(len(r.picks), 3)
        # Heavily covered in Chinese → scarcity 10 → filtered out or ranked last.
        grid = next(s for s in r.shortlisted if "grid" in s.signal.url)
        self.assertEqual(grid.cn_scarcity, 10.0)
        text = digest.x_text(r.picks)
        self.assertTrue(text.startswith(digest.HEADER))
        for p in r.picks:
            self.assertIn(p.signal.url, text)
        md = digest.review_markdown(r)
        self.assertIn("X 预览", md)
        self.assertNotIn("未检测中文覆盖度", md)

    def test_pick_prefers_different_tracks(self):
        def mk(track, total):
            s = score(Signal("x", f"{track}{total}", f"https://e/{track}{total}"), Coverage(True), heuristic_assessment(Signal("x", "t", "u")))
            s.assessment.track, s.total = track, total
            return s
        c = [mk(Track.AI_TECH, 95), mk(Track.AI_TECH, 94), mk(Track.MONEY, 80), mk(Track.US_LIFE, 75)]
        self.assertEqual([p.assessment.track for p in digest.pick(c)], [Track.AI_TECH, Track.MONEY, Track.US_LIFE])

    def test_guard(self):
        r = digest.scan(collectors(), brave({}), llm(), now=NOW)
        good = digest.DigestReview("editor", True, True)
        text = digest.x_text(r.picks)
        self.assertTrue(digest.check(r.picks, text, good).allowed)
        self.assertFalse(digest.check(r.picks, text + "\n稳赚不赔", good).allowed)
        self.assertFalse(digest.check(r.picks, text.replace(r.picks[0].signal.url, ""), good).allowed)
        self.assertFalse(digest.check(r.picks, text, digest.DigestReview("editor", True, False)).allowed)

    def test_without_llm_cannot_post(self):
        r = digest.scan(collectors(), NoCoverage(), LLMAssessor(""), now=NOW)
        text = digest.x_text(r.picks) if r.picks else ""
        if r.picks:
            self.assertIn(PLACEHOLDER, text)
        g = digest.check(r.picks, text, digest.DigestReview("editor", True, True))
        self.assertFalse(g.allowed)
        self.assertIn("未使用 AI", digest.review_markdown(r))


class TestRunner(unittest.TestCase):
    def test_scan_then_post_once(self):
        root = Path(tempfile.mkdtemp())
        (root / "digest_requests.json").write_text('[{"status": "pending"}]')
        InfoGapRunner(root, collectors(), brave({}), llm()).run()
        item = json.loads((root / "digest_requests.json").read_text())[0]
        self.assertEqual(item["status"], "drafted")
        self.assertTrue((root / item["review"]).exists())
        self.assertTrue(item["preview"])

        item.update(post=True, reviewer="editor", facts_checked=True, access_checked=True, status="pending", picks=[1, 2])
        (root / "digest_requests.json").write_text(json.dumps([item]))
        t = FakeTransport()
        InfoGapRunner(root, x_client=xp.XClient(CREDS, t)).run()
        item = json.loads((root / "digest_requests.json").read_text())[0]
        self.assertEqual(item["status"], "posted", item.get("failures"))
        self.assertEqual(len(t.calls), len(item["preview"]))
        self.assertEqual(len(json.loads((root / "x_ledger.json").read_text())), len(t.calls))

        item["status"] = "pending"
        (root / "digest_requests.json").write_text(json.dumps([item]))
        t2 = FakeTransport()
        InfoGapRunner(root, x_client=xp.XClient(CREDS, t2)).run()
        self.assertEqual(t2.calls, [])  # ledger prevents double posting

    def test_post_blocked_without_review(self):
        root = Path(tempfile.mkdtemp())
        (root / "digest_requests.json").write_text('[{"status": "pending"}]')
        InfoGapRunner(root, collectors(), brave({}), llm()).run()
        item = json.loads((root / "digest_requests.json").read_text())[0]
        item.update(post=True, status="pending")
        (root / "digest_requests.json").write_text(json.dumps([item]))
        t = FakeTransport()
        InfoGapRunner(root, x_client=xp.XClient(CREDS, t)).run()
        item = json.loads((root / "digest_requests.json").read_text())[0]
        self.assertEqual(item["status"], "blocked")
        self.assertEqual(t.calls, [])

    def test_post_without_scan(self):
        root = Path(tempfile.mkdtemp())
        (root / "digest_requests.json").write_text('[{"status": "pending", "post": true}]')
        InfoGapRunner(root).run()
        self.assertEqual(json.loads((root / "digest_requests.json").read_text())[0]["status"], "error")


if __name__ == "__main__":
    unittest.main()


class TestBalance(unittest.TestCase):
    def test_shortlist_quota_and_native_chinese(self):
        sigs = [Signal("github", f"g/{i}", f"https://g/{i}", points=5000) for i in range(30)]
        sigs += [Signal("hackernews", f"h{i}", f"https://h/{i}", points=300) for i in range(5)]
        top = digest.shortlist_balanced(sigs, 10)
        self.assertEqual(sum(s.source == "hackernews" for s in top), 5)
        zh = Signal("github", "x/aihot", "https://g/zh", summary="AI 热点日报", points=2000)
        a = heuristic_assessment(zh)
        scored = score(zh, Coverage(True, zh_results=0), a)
        self.assertEqual(scored.cn_scarcity, 10.0)
        self.assertIn("原文本身含中文", scored.notes[0])


class TestTavily(unittest.TestCase):
    def test_restricts_to_chinese_sites_and_counts_relevant(self):
        from stateverge.cn_news.infogap.coverage import TavilyCoverage, ZH_DOMAINS

        def post(url, headers, body):
            req = json.loads(body)
            self.assertEqual(headers["Authorization"], "Bearer k")
            self.assertEqual(req["time_range"], "week")
            self.assertEqual(req["include_domains"], ZH_DOMAINS)
            return json.dumps({"results": [
                {"title": "Ledgerly 记账 AI 上线", "url": "https://36kr.com/p/1", "content": ""},
                {"title": "无关文章", "url": "https://ithome.com/2", "content": "别的"},
            ]}).encode()
        cov = TavilyCoverage("k", post).check(Signal("hackernews", "Show HN: Ledgerly, an AI agent", "u"))
        self.assertEqual((cov.checked, cov.zh_results, cov.provider), (True, 1, "tavily"))

    def test_default_prefers_tavily(self):
        import os
        from stateverge.cn_news.infogap import coverage
        saved = {k: os.environ.pop(k, None) for k in ("TAVILY_API_KEY", "BRAVE_API_KEY")}
        try:
            self.assertEqual(coverage.default_coverage().name, "none")
            os.environ["BRAVE_API_KEY"] = "b"
            self.assertEqual(coverage.default_coverage().name, "brave")
            os.environ["TAVILY_API_KEY"] = "t"
            self.assertEqual(coverage.default_coverage().name, "tavily")
        finally:
            for k in ("TAVILY_API_KEY", "BRAVE_API_KEY"):
                os.environ.pop(k, None)
                if saved[k] is not None:
                    os.environ[k] = saved[k]


class TestCoverageErrors(unittest.TestCase):
    def test_http_error_is_recorded(self):
        import io
        import urllib.error
        from stateverge.cn_news.infogap.coverage import TavilyCoverage

        def post(url, headers, body):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b'{"detail": "invalid key"}'))
        cov = TavilyCoverage("k", post).check(Signal("hackernews", "Ledgerly agent", "u"))
        self.assertFalse(cov.checked)
        self.assertIn("HTTP 401", cov.error)
        self.assertIn("invalid key", cov.error)


class TestKeyShape(unittest.TestCase):
    def test_never_reveals_key(self):
        from stateverge.cn_news.infogap.coverage import key_shape
        shape = key_shape("tvly-SECRETVALUE123")
        self.assertNotIn("SECRET", shape)
        self.assertIn("starts with tvly-", shape)
        self.assertIn("unexpected", key_shape("tvly-abc•••"))
        self.assertIn("NOT", key_shape("sk-abc"))


def _responses(annotations):
    return {"output": [
        {"type": "web_search_call", "action": {"query": "x"}},
        {"type": "message", "content": [{"type": "output_text", "text": "...", "annotations": annotations}]},
    ]}


class TestOpenAICoverage(unittest.TestCase):
    def test_counts_distinct_chinese_citations(self):
        from stateverge.cn_news.infogap.coverage import OpenAICoverage

        sent = []

        def post(url, headers, body):
            sent.append(json.loads(body))
            self.assertTrue(url.endswith("/v1/responses"))
            return json.dumps(_responses([
                {"type": "url_citation", "url": "https://36kr.com/p/1", "title": "Ledgerly"},
                {"type": "url_citation", "url": "https://36kr.com/p/1", "title": "dup"},
                {"type": "url_citation", "url": "https://blog.example/x", "title": "Ledgerly 中文评测"},
                {"type": "url_citation", "url": "https://techcrunch.com/x", "title": "English only"},
            ])).encode()
        cov = OpenAICoverage("k", post=post).check(Signal("hackernews", "Show HN: Ledgerly agent", "u"))
        self.assertEqual((cov.checked, cov.zh_results, cov.provider), (True, 2, "openai"))
        self.assertEqual(sent[0]["tools"][0]["type"], "web_search")
        self.assertIn("allowed_domains", sent[0]["tools"][0]["filters"])

    def test_retries_without_filters_on_400(self):
        import io
        import urllib.error
        from stateverge.cn_news.infogap.coverage import OpenAICoverage

        calls = []

        def post(url, headers, body):
            calls.append(json.loads(body))
            if len(calls) == 1:
                raise urllib.error.HTTPError(url, 400, "Bad", {}, io.BytesIO(b"filters not supported"))
            return json.dumps(_responses([])).encode()
        cov = OpenAICoverage("k", post=post).check(Signal("hackernews", "Ledgerly agent", "u"))
        self.assertEqual((cov.checked, cov.zh_results), (True, 0))
        self.assertNotIn("filters", calls[1]["tools"][0])

    def test_forced_provider(self):
        import os
        from stateverge.cn_news.infogap import coverage
        keys = ("INFOGAP_COVERAGE", "OPENAI_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY")
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            os.environ.update(INFOGAP_COVERAGE="openai", OPENAI_API_KEY="o", TAVILY_API_KEY="bad")
            self.assertEqual(coverage.default_coverage().name, "openai")
            os.environ["INFOGAP_COVERAGE"] = "none"
            self.assertEqual(coverage.default_coverage().name, "none")
        finally:
            for k in keys:
                os.environ.pop(k, None)
                if saved[k] is not None:
                    os.environ[k] = saved[k]


class TestPromptRules(unittest.TestCase):
    def test_prompt_requires_attribution(self):
        from stateverge.cn_news.infogap.assessor import SYSTEM_PROMPT
        self.assertIn("Show HN", SYSTEM_PROMPT)
        self.assertIn("不得写成", SYSTEM_PROMPT)


class TestVetoWindow(unittest.TestCase):
    def _scan_auto(self, assessor=None):
        root = Path(tempfile.mkdtemp())
        (root / "digest_requests.json").write_text('[{"status": "pending", "auto": true}]')
        InfoGapRunner(root, collectors(), brave({}), assessor or llm()).run()
        return root, json.loads((root / "digest_requests.json").read_text())[0]

    def test_scheduled_then_posted_after_window(self):
        from datetime import datetime, timedelta, timezone
        root, item = self._scan_auto()
        self.assertEqual(item["status"], "scheduled", item)
        due = datetime.fromisoformat(item["post_after"])
        self.assertTrue(item["preview"][-1].rstrip().endswith("3/3") or "AI 辅助整理" in "".join(item["preview"]))

        t = FakeTransport()
        InfoGapRunner(root, x_client=xp.XClient(CREDS, t)).run(now=due - timedelta(minutes=5))
        self.assertEqual(t.calls, [])  # still inside the veto window

        InfoGapRunner(root, x_client=xp.XClient(CREDS, t)).run(now=due + timedelta(minutes=1))
        item = json.loads((root / "digest_requests.json").read_text())[0]
        self.assertEqual(item["status"], "posted", item.get("failures"))
        self.assertIn("auto", item["approved_by"])
        self.assertIn("AI 辅助整理", "".join(c["body"]["text"] for c in t.calls))

        t2 = FakeTransport()
        InfoGapRunner(root, x_client=xp.XClient(CREDS, t2)).run(now=datetime.now(timezone.utc) + timedelta(days=1))
        self.assertEqual(t2.calls, [])  # posted once

    def test_veto(self):
        from datetime import datetime, timedelta
        root, item = self._scan_auto()
        due = datetime.fromisoformat(item["post_after"])
        item["status"] = "vetoed"
        (root / "digest_requests.json").write_text(json.dumps([item]))
        t = FakeTransport()
        InfoGapRunner(root, x_client=xp.XClient(CREDS, t)).run(now=due + timedelta(hours=1))
        self.assertEqual(t.calls, [])
        self.assertEqual(json.loads((root / "digest_requests.json").read_text())[0]["status"], "vetoed")

    def test_official_claim_on_personal_project_not_auto(self):
        from stateverge.cn_news.infogap.digest import auto_eligible
        r = digest.scan(collectors(), brave({}), llm(), now=NOW)
        c = next(x for x in r.candidates if x.signal.source == "github")
        c.assessment.headline_zh = "Acme 官方推出语音克隆工具"
        ok, why = auto_eligible(c)
        self.assertFalse(ok)
        self.assertIn("官方", why)

    def test_not_enough_eligible_stays_drafted(self):
        root, item = self._scan_auto(assessor=LLMAssessor(""))  # no AI → placeholders → nothing eligible
        self.assertEqual(item["status"], "drafted")
        self.assertIn("note_auto", item)

    def test_auto_guard_never_fakes_human_review(self):
        from stateverge.cn_news.infogap.digest import auto_text, check_auto
        r = digest.scan(collectors(), brave({}), llm(), now=NOW)
        picks = r.candidates[:2]
        self.assertTrue(check_auto(picks, auto_text(picks)).allowed)
        self.assertFalse(check_auto(picks, digest.x_text(picks)).allowed)  # footer required
