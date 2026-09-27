#!/usr/bin/env python3
"""GovInfo collector, LLM stance judge and cloud runner — all network faked.

The sample GovInfo document below is fictional and written in the CPD layout.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from stateverge.cn_news.archive import x_publisher as xp  # noqa: E402
from stateverge.cn_news.archive.cloud_runner import CloudRunner  # noqa: E402
from stateverge.cn_news.archive.contradiction import compare  # noqa: E402
from stateverge.cn_news.archive.govinfo_provider import (  # noqa: E402
    GovInfoProvider,
    html_to_text,
    sentences,
    split_turns,
)
from stateverge.cn_news.archive.models import ComparisonStatus, SourceTier  # noqa: E402
from stateverge.cn_news.archive.stance_llm import LLMStanceJudge  # noqa: E402

from tests.cn_news_archive.test_archive import EARLIER, LATER, make_claim  # noqa: E402
from tests.cn_news_archive.test_x_publisher import CREDS, FakeTransport  # noqa: E402

DOC = """<html><body><pre>
Administration of Example President, 2025

Remarks in an Exchange With Reporters on Iran
March 1, 2025

    The President. Thank you all for coming today. We will not send troops to Iran under any
circumstances. Our focus is diplomacy.

    Q. Mr. President, what about sanctions on Iran?

    The President. Sanctions on Iran will stay in place for now. We are watching closely.

    Prime Minister Doe. Iran is a concern for everyone in the region.
</pre></body></html>"""

SEARCH = {
    "results": [
        {"title": "Remarks in an Exchange With Reporters on Iran", "packageId": "DCPD-202500001",
         "dateIssued": "2025-03-01", "collectionCode": "CPD"},
        {"title": "Old remarks", "packageId": "DCPD-201000001", "dateIssued": "2010-01-01", "collectionCode": "CPD"},
    ]
}


class FakeFetch:
    def __init__(self):
        self.urls: list[str] = []
        self.bodies: list[dict] = []

    def __call__(self, url, body):
        self.urls.append(url)
        if body is not None:
            self.bodies.append(json.loads(body))
            return json.dumps(SEARCH).encode()
        return DOC.encode()


class TestGovInfo(unittest.TestCase):
    def test_parse(self):
        text = html_to_text(DOC)
        turns = split_turns(text, "__DOC__")
        speakers = [t.speaker for t in turns]
        self.assertIn("The President", speakers)
        self.assertIn("Q", speakers)
        self.assertIn("Prime Minister Doe", speakers)
        self.assertNotIn("Remarks in an Exchange With Reporters on Iran", sentences(turns[0].text))

    def test_search_extracts_verbatim_president_quotes(self):
        fetch = FakeFetch()
        p = GovInfoProvider(api_key="k", fetch=fetch)
        claims = p.search("Donald Trump", "iran", "2016-01-01", "2026-01-01")
        texts = [c.statement_text_original for c in claims]
        self.assertIn("We will not send troops to Iran under any circumstances.", texts)
        self.assertIn("Sanctions on Iran will stay in place for now.", texts)
        # Not the reporter, not the other leader, not the header.
        self.assertFalse(any("Prime Minister" in t or "Mr. President" in t or "Exchange With" in t for t in texts))
        # The 2010 document belongs to a different president and is skipped.
        self.assertEqual({c.statement_date for c in claims}, {"2025-03-01"})
        c = claims[0]
        self.assertTrue(c.transcript_verified)
        self.assertEqual(c.source_type, SourceTier.SOURCE_A.value)
        self.assertTrue(c.government_work)
        self.assertEqual(c.archive_status, "CANDIDATE")
        self.assertIn("collection:(CPD)", fetch.bodies[0]["query"])
        self.assertTrue(c.source_url.startswith("https://www.govinfo.gov/app/details/DCPD-202500001"))
        sanction = next(x for x in claims if x.statement_text_original.startswith("Sanctions"))
        self.assertIn("what about sanctions", sanction.context_before)

    def test_network_failure_returns_empty(self):
        import urllib.error

        def boom(url, body):
            raise urllib.error.URLError("blocked")

        self.assertEqual(GovInfoProvider(fetch=boom).search("Donald Trump", "iran", "2016-01-01", "2026-01-01"), [])

    def test_member_of_congress_uses_crec(self):
        fetch = FakeFetch()
        GovInfoProvider(fetch=fetch).search("Alex Rivera", "iran", "2016-01-01", "2026-01-01")
        self.assertIn("collection:(CREC)", fetch.bodies[0]["query"])
        self.assertIn('"Rivera"', fetch.bodies[0]["query"])


def _llm_reply(content: str):
    def post(url, headers, body):
        post.sent = json.loads(body)
        return json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    return post


class TestStanceLLM(unittest.TestCase):
    def test_uses_context_and_maps_stance(self):
        post = _llm_reply('{"stance": "SHIFTED", "reason": "conditions added"}')
        judge = LLMStanceJudge(api_key="k", post=post)
        r = compare(make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20"), stance_judge=judge)
        self.assertEqual(r.status, ComparisonStatus.POSITION_CHANGED)
        self.assertEqual(judge.last_reason, "conditions added")
        user_msg = post.sent["messages"][1]["content"]
        self.assertIn("Will you send troops", user_msg)  # context passed
        self.assertEqual(post.sent["temperature"], 0)

    def test_failures_fall_back_to_unclear(self):
        for content in ("not json", '{"stance": "LIAR"}', "{}"):
            judge = LLMStanceJudge(api_key="k", post=_llm_reply(content))
            r = compare(make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20"), stance_judge=judge)
            self.assertEqual(r.status, ComparisonStatus.INSUFFICIENT_EVIDENCE, content)
        self.assertEqual(LLMStanceJudge(api_key="")(make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20")),
                         "UNCLEAR")


class FakeProvider:
    name = "fake"

    def search(self, person, keyword, since, until):
        if keyword != "iran":
            return []
        c = make_claim("We will not send troops to Iran under any circumstances", "2025-03-01",
                       person_name=person, topic=keyword, subtopic="")
        return [c]


class TestCloudRunner(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "claims").mkdir()
        shutil.copy(_ROOT / "docs" / "archive_import_example.json", self.root / "claims" / "example.json")
        shutil.copytree(_ROOT / "docs" / "archive_import_example", self.root / "claims" / "archive_import_example")

    def _ids(self):
        runner = CloudRunner(self.root, providers=[])
        runner.load()
        ids = sorted((c.statement_date, c.claim_id) for c in runner.db.search())
        return ids[0][1], ids[1][1]

    def _queue(self, **overrides):
        a, b = self._ids()
        item = {"earlier_id": a, "later_id": b, "reviewer": "editor", "context_reviewed": True,
                "opinion_checked": True, "corrections_checked": True, "post": False, "status": "pending"}
        item.update(overrides)
        (self.root / "publish_queue.json").write_text(json.dumps([item]))

    def _read(self, name):
        return json.loads((self.root / name).read_text())

    def test_preview_then_post_once(self):
        self._queue()
        CloudRunner(self.root, providers=[]).run()
        item = self._read("publish_queue.json")[0]
        self.assertEqual(item["status"], "previewed")
        self.assertEqual(item["decision"], "ALLOW_PUBLISH")
        self.assertTrue(item["preview"])

        item.update(post=True, status="pending")
        (self.root / "publish_queue.json").write_text(json.dumps([item]))
        t = FakeTransport()
        CloudRunner(self.root, providers=[], x_client=xp.XClient(CREDS, t)).run()
        item = self._read("publish_queue.json")[0]
        self.assertEqual(item["status"], "posted")
        self.assertEqual(len(self._read("x_ledger.json")), len(item["preview"]))

        # Re-queueing the same pair does not post again: the ledger survives rebuilds.
        item.update(status="pending")
        (self.root / "publish_queue.json").write_text(json.dumps([item]))
        t2 = FakeTransport()
        CloudRunner(self.root, providers=[], x_client=xp.XClient(CREDS, t2)).run()
        self.assertEqual(t2.calls, [])

    def test_blocked_without_attestation(self):
        self._queue(reviewer="", post=True)
        t = FakeTransport()
        CloudRunner(self.root, providers=[], x_client=xp.XClient(CREDS, t)).run()
        item = self._read("publish_queue.json")[0]
        self.assertEqual(item["status"], "blocked")
        self.assertEqual(t.calls, [])

    def test_unknown_ids(self):
        (self.root / "publish_queue.json").write_text(json.dumps([{"earlier_id": "x", "later_id": "y"}]))
        CloudRunner(self.root, providers=[]).run()
        self.assertEqual(self._read("publish_queue.json")[0]["status"], "error")

    def test_collect_writes_reimportable_file(self):
        (self.root / "collect_requests.json").write_text(json.dumps([{"person": "Chris Doe", "topic": "Iran"}]))
        CloudRunner(self.root, providers=[FakeProvider()]).run()
        req = self._read("collect_requests.json")[0]
        self.assertEqual((req["status"], req["new"]), ("done", 1))
        # The collected file imports cleanly and re-verifies from its excerpt.
        runner = CloudRunner(self.root, providers=[])
        runner.load()
        doe = [c for c in runner.db.search(person_id="chris-doe")]
        self.assertEqual(len(doe), 1)
        self.assertTrue(doe[0].transcript_verified)
        self.assertEqual(doe[0].topic, "Iran")
        # Running the same request again adds nothing new.
        (self.root / "collect_requests.json").write_text(json.dumps([{"person": "Chris Doe", "topic": "Iran"}]))
        CloudRunner(self.root, providers=[FakeProvider()]).run()
        self.assertEqual(self._read("collect_requests.json")[0]["new"], 0)


if __name__ == "__main__":
    unittest.main()
