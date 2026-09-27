#!/usr/bin/env python3
"""Political Archive v1 tests.

All people and quotes here are fictional ("Alex Rivera") on purpose: the
archive must never put invented words in a real politician's mouth, test
fixtures included.
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stateverge.cn_news.archive import clip_builder, copyright, fact_check, publish_guard  # noqa: E402
from stateverge.cn_news.archive import script_generator as sg  # noqa: E402
from stateverge.cn_news.archive import transcript  # noqa: E402
from stateverge.cn_news.archive.claim_matcher import dedupe, same_issue  # noqa: E402
from stateverge.cn_news.archive.claim_search import build_queries, mentions  # noqa: E402
from stateverge.cn_news.archive.context_check import check_pair  # noqa: E402
from stateverge.cn_news.archive.contradiction import OutcomeRecord, compare, track_promise  # noqa: E402
from stateverge.cn_news.archive.database import ArchiveDB  # noqa: E402
from stateverge.cn_news.archive.models import (  # noqa: E402
    ClaimType,
    ComparisonStatus,
    CopyrightType,
    NewsEvent,
    PoliticalClaim,
    SourceTier,
)
from stateverge.cn_news.archive.pipeline import run_archive_stage  # noqa: E402
from stateverge.cn_news.archive.source_resolver import classify_source, resolve_claim  # noqa: E402

PERSON = "Alex Rivera"


def make_claim(text: str, day: str, **kw) -> PoliticalClaim:
    defaults = dict(
        person_name=PERSON,
        topic="Iran",
        subtopic="troops",
        statement_date=day,
        statement_text_original=text,
        source_name="White House",
        source_url="https://www.whitehouse.gov/briefings/example",
        video_url="https://www.whitehouse.gov/videos/example",
        source_type=SourceTier.SOURCE_A,
        transcript_verified=True,
        context_before="Reporter: Will you send troops to the region?",
        context_after="Next question.",
        claim_type=ClaimType.POLICY_POSITION,
        video_start=10.0,
        video_end=16.0,
    )
    defaults.update(kw)
    claim = PoliticalClaim(**defaults)
    copyright.apply(claim)
    return claim


EARLIER = "We will not send troops to Iran under any circumstances"
LATER = "We will send troops to Iran under these circumstances"


class TestModels(unittest.TestCase):
    def test_rejects_unknown_enum_values(self):
        with self.assertRaises(ValueError):
            make_claim("x", "2025-01-01", comparison_status="LIAR")
        with self.assertRaises(ValueError):
            make_claim("x", "2025-01-01", claim_type="LIE")

    def test_db_check_constraint_blocks_verdicts(self):
        db = ArchiveDB(":memory:")
        claim = db.upsert(make_claim(EARLIER, "2025-03-01"))
        with self.assertRaises(sqlite3.IntegrityError):
            db.conn.execute(
                "UPDATE political_claims SET comparison_status='LIAR' WHERE claim_id=?", (claim.claim_id,)
            )

    def test_roundtrip(self):
        db = ArchiveDB(":memory:")
        claim = db.upsert(make_claim(EARLIER, "2025-03-01"))
        got = db.get(claim.claim_id)
        self.assertEqual(got.statement_text_original, EARLIER)
        self.assertTrue(got.transcript_verified)
        self.assertTrue(got.government_work)
        self.assertEqual(got.person_id, "alex-rivera")


class TestSources(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(classify_source("", "https://www.c-span.org/video/x").tier, SourceTier.SOURCE_A)
        self.assertEqual(classify_source("Reuters").tier, SourceTier.SOURCE_B)
        self.assertEqual(classify_source("", "https://edition.cnn.com/x").tier, SourceTier.SOURCE_C)
        tiktok = classify_source("someone", "https://www.tiktok.com/@anon/video/1")
        self.assertEqual(tiktok.tier, SourceTier.SOURCE_E)
        self.assertFalse(tiktok.final_evidence_eligible)
        self.assertTrue(tiktok.needs_original)
        own = classify_source("Campaign", "https://x.com/candidate", official_account_of_speaker=True)
        self.assertEqual(own.tier, SourceTier.SOURCE_D)

    def test_copyright(self):
        self.assertEqual(copyright.assess("https://www.whitehouse.gov/v").copyright_type, CopyrightType.US_GOVERNMENT_WORK)
        cspan = copyright.assess("https://www.c-span.org/video/x")
        self.assertEqual((cspan.owner, cspan.copyright_type), ("C-SPAN", CopyrightType.UNKNOWN))
        self.assertFalse(cspan.publishable)
        fair = copyright.assess("https://www.c-span.org/video/x", fair_use_purpose="历史对比评论")
        self.assertEqual(fair.copyright_type, CopyrightType.FAIR_USE_COMMENTARY)
        state = copyright.assess("https://governor.example.gov/v", "Governor's Office")
        self.assertEqual(state.copyright_type, CopyrightType.UNKNOWN)


class TestTranscript(unittest.TestCase):
    def test_verbatim_with_timing_and_context(self):
        segs = [
            transcript.Segment(0, 5, "Reporter: will you send troops?"),
            transcript.Segment(5, 9, "Well, look. We will not send troops"),
            transcript.Segment(9, 12, "to Iran under any circumstances. Thank you."),
        ]
        m = transcript.verify_quote(EARLIER, segs)
        self.assertTrue(m.verified)
        self.assertEqual((m.video_start, m.video_end), (5, 12))
        self.assertIn("reporter", m.context_before)
        self.assertEqual(m.context_after, "thank you")

    def test_paraphrase_is_not_verified(self):
        self.assertFalse(transcript.verify_quote("We won't send troops to Iran", EARLIER).verified)

    def test_chinese(self):
        self.assertTrue(transcript.verify_quote("我们不会派兵", "记者提问。我们不会派兵。谢谢。").verified)


class TestMatching(unittest.TestCase):
    def test_dedupe_prefers_higher_tier(self):
        a = make_claim(EARLIER, "2025-03-01", source_type=SourceTier.SOURCE_C, source_name="CNN")
        b = make_claim(EARLIER + ".", "2025-03-02")
        kept = dedupe([a, b])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].source_type, SourceTier.SOURCE_A.value)

    def test_same_issue(self):
        a = make_claim(EARLIER, "2025-03-01")
        self.assertFalse(same_issue(a, make_claim(LATER, "2026-09-20", subtopic="sanctions")))
        self.assertFalse(same_issue(a, make_claim(LATER, "2026-09-20", topic="China")))

    def test_keyword_whole_word(self):
        c = make_claim("I received an award today", "2025-01-01", topic="Awards", subtopic="")
        self.assertFalse(mentions(c, "war"))
        self.assertTrue(mentions(make_claim(EARLIER, "2025-01-01"), "troops"))

    def test_query_expansion(self):
        qs = [str(q) for q in build_queries(NewsEvent("e", "t", people=["Trump"], topics=["Iran"]))]
        for expected in ("Trump + iran", "Trump + regime change", "Trump + ceasefire", "Trump + troops"):
            self.assertIn(expected, qs)


class TestCompare(unittest.TestCase):
    def test_opposite_policy_is_position_changed_not_lie(self):
        r = compare(make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20"))
        self.assertEqual(r.status, ComparisonStatus.POSITION_CHANGED)
        self.assertEqual(r.months_apart, 18)

    def test_opposite_factual_is_apparent_contradiction(self):
        a = make_claim("I never met the ambassador", "2025-03-01", claim_type=ClaimType.DENIAL)
        b = make_claim("I met the ambassador", "2025-06-01", claim_type=ClaimType.FACTUAL_CLAIM)
        self.assertEqual(compare(a, b).status, ComparisonStatus.APPARENT_CONTRADICTION)

    def test_failed_prediction_is_not_contradiction(self):
        a = make_claim("There will not be a war with Iran this year", "2025-01-01", claim_type=ClaimType.PREDICTION)
        b = make_claim("There will be a war with Iran this year", "2025-09-01", claim_type=ClaimType.FACTUAL_CLAIM)
        self.assertEqual(compare(a, b).status, ComparisonStatus.POSITION_CHANGED)

    def test_same_words_consistent(self):
        r = compare(make_claim(EARLIER, "2025-03-01"), make_claim(EARLIER, "2026-03-01"))
        self.assertEqual(r.status, ComparisonStatus.CONSISTENT)

    def test_different_wording_alone_is_insufficient(self):
        a = make_claim(EARLIER, "2025-03-01")
        b = make_claim("Our region policy remains focused on diplomacy and allies", "2026-03-01")
        self.assertEqual(compare(a, b).status, ComparisonStatus.INSUFFICIENT_EVIDENCE)

    def test_unverified_or_tier_e_is_insufficient(self):
        a = make_claim(EARLIER, "2025-03-01", transcript_verified=False)
        self.assertEqual(compare(a, make_claim(LATER, "2026-09-20")).status, ComparisonStatus.INSUFFICIENT_EVIDENCE)
        e = make_claim(EARLIER, "2025-03-01", source_type=SourceTier.SOURCE_E)
        self.assertEqual(compare(e, make_claim(LATER, "2026-09-20")).status, ComparisonStatus.INSUFFICIENT_EVIDENCE)

    def test_hypothetical_is_context_changed(self):
        a = make_claim(EARLIER, "2025-03-01")
        b = make_claim(LATER, "2026-09-20", context_before="Reporter: If Iran attacks a US base, what then? Answer:")
        r = compare(a, b)
        self.assertEqual(r.status, ComparisonStatus.CONTEXT_CHANGED)
        self.assertIn("HYPOTHETICAL", r.context.flags)

    def test_manual_flag(self):
        r = compare(make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20"),
                    manual_context_flags=["DIFFERENT_AUDIENCE"])
        self.assertEqual(r.status, ComparisonStatus.CONTEXT_CHANGED)
        with self.assertRaises(ValueError):
            check_pair(make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20"), ["LIAR"])

    def test_missing_context_is_insufficient(self):
        a = make_claim(EARLIER, "2025-03-01", context_before="", context_after="")
        self.assertEqual(compare(a, make_claim(LATER, "2026-09-20")).status, ComparisonStatus.INSUFFICIENT_EVIDENCE)

    def test_custom_stance_judge_validated(self):
        with self.assertRaises(ValueError):
            compare(make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20"), stance_judge=lambda a, b: "LIE")


class TestFactCheck(unittest.TestCase):
    def test_only_recognised_orgs(self):
        c = make_claim(EARLIER, "2025-03-01")
        with self.assertRaises(ValueError):
            fact_check.record_fact_check(c, "Random Blog", "https://blog.example/x", "False")
        with self.assertRaises(ValueError):
            fact_check.record_fact_check(c, "FactCheck.org", "https://evil.example/x", "False")
        fact_check.record_fact_check(c, "factcheck.org", "https://www.factcheck.org/2025/03/x/", "False")
        self.assertEqual(fact_check.attribution_sentence(c), "FactCheck.org 将该说法评为「False」。")


def _ready_card():
    a, b = make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20")
    return sg.card_from_comparison(compare(a, b))


def _clips(card):
    return [clip_builder.plan_clip(c, ["ZH_NARRATION", "HISTORICAL_COMPARISON"]) for c in card.claims]


GOOD_REVIEW = publish_guard.ReviewerAttestation("editor", True, True, True)


class TestScripts(unittest.TestCase):
    def test_x_post(self):
        text = sg.x_post(_ready_card())
        self.assertIn("📼 录像不会失忆", text)
        self.assertIn(f"“{EARLIER}”", text)
        self.assertIn("相隔18个月,两种说法", text)
        self.assertIn("让原始记录自己说话。", text)
        self.assertEqual(publish_guard.forbidden_terms_in(text), [])

    def test_video_script(self):
        shots = sg.video_script(_ready_card())
        self.assertEqual(shots[0].text, "录像不会失忆。")
        self.assertIn("2025年3月", shots[1].caption)
        self.assertIn("18个月之后,Alex Rivera给出了另一个版本。", [s.text for s in shots])
        self.assertIn("STATEVERGE ARCHIVE", shots[-1].text)

    def test_insufficient_evidence_generates_nothing(self):
        a = make_claim(EARLIER, "2025-03-01", transcript_verified=False)
        card = sg.card_from_comparison(compare(a, make_claim(LATER, "2026-09-20")))
        with self.assertRaises(sg.NotPublishable):
            sg.x_post(card)

    def test_parody_cannot_script_real_people(self):
        c = make_claim(EARLIER, "2025-03-01")
        text = sg.parody_script([("记者小王", "今天天气不错。")], [c])
        self.assertTrue(text.startswith(sg.PARODY_LABEL))
        with self.assertRaises(sg.NotPublishable):
            sg.parody_script([(PERSON, "编造的话")], [c])

    def test_promise_track(self):
        p = make_claim("We will not send troops to Iran", "2024-06-01", claim_type=ClaimType.PROMISE)
        later = make_claim(LATER, "2026-09-20")
        track = track_promise(p, [later], [OutcomeRecord("2026-10-01", "国防部宣布部署", "Defense.gov", "https://www.defense.gov/x")])
        text = sg.promise_text(sg.card_from_promise(track))
        self.assertIn("这是当时的承诺,这是后来发生的事情。", text)
        self.assertLess(text.index("2024年"), text.index("2026年10月"))


class TestClips(unittest.TestCase):
    def test_limits(self):
        c = make_claim(EARLIER, "2025-03-01", video_start=0.0, video_end=20.0)
        with self.assertRaises(clip_builder.ClipRefused):
            clip_builder.plan_clip(c, ["ZH_NARRATION"])
        plan = clip_builder.plan_clip(c, ["ZH_NARRATION"], extend_reason="完整句子")
        self.assertEqual(plan.duration, 20.0)
        c.video_end = 45.0
        with self.assertRaises(clip_builder.ClipRefused):
            clip_builder.plan_clip(c, ["ZH_NARRATION"], extend_reason="完整句子")

    def test_short_clip_padded(self):
        c = make_claim(EARLIER, "2025-03-01", video_start=10.0, video_end=11.0)
        self.assertEqual(clip_builder.plan_clip(c, ["TIMELINE"]).duration, 3.0)

    def test_subtitles_only_refused(self):
        c = make_claim(EARLIER, "2025-03-01")
        with self.assertRaises(clip_builder.ClipRefused):
            clip_builder.plan_clip(c, ["SUBTITLES"])
        with self.assertRaises(clip_builder.ClipRefused):
            clip_builder.plan_clip(c, [])

    def test_unknown_copyright_refused(self):
        c = make_claim(EARLIER, "2025-03-01", video_url="https://www.c-span.org/video/x")
        with self.assertRaises(clip_builder.ClipRefused):
            clip_builder.plan_clip(c, ["ZH_NARRATION"])


class TestPublishGuard(unittest.TestCase):
    def test_happy_path(self):
        card = _ready_card()
        r = publish_guard.check(card, [sg.x_post(card)], _clips(card), GOOD_REVIEW)
        self.assertTrue(r.allowed, r.failures)

    def test_liar_copy_blocked(self):
        card = _ready_card()
        r = publish_guard.check(card, [sg.x_post(card) + "\n他又撒谎了。"], _clips(card), GOOD_REVIEW)
        self.assertEqual(r.decision, publish_guard.BLOCK_PUBLISH)
        self.assertFalse(r.checks["no_liar_verdict"])
        self.assertTrue(publish_guard.forbidden_terms_in("Rivera LIED again"))
        self.assertFalse(publish_guard.forbidden_terms_in("I believe the plan works"))

    def test_speaker_own_words_not_flagged(self):
        c = make_claim("That report is a lie", "2025-03-01")
        self.assertEqual(publish_guard.forbidden_terms_in(f"Rivera:“{c.statement_text_original}”"), [])

    def test_fabricated_quote_blocked(self):
        card = _ready_card()
        r = publish_guard.check(card, [sg.x_post(card) + "\n他还说:“我从来没说过”"], _clips(card), GOOD_REVIEW)
        self.assertFalse(r.checks["no_fabricated_quotes"])

    def test_missing_attestation_blocked(self):
        card = _ready_card()
        r = publish_guard.check(card, [sg.x_post(card)], _clips(card), publish_guard.ReviewerAttestation())
        self.assertFalse(r.allowed)
        self.assertFalse(r.checks["corrections_checked"])

    def test_fact_check_needs_three_evidence_and_attribution(self):
        a, b = make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20")
        fact_check.record_fact_check(b, "PolitiFact", "https://www.politifact.com/x", "Half True")
        card = sg.card_from_comparison(compare(a, b))
        self.assertEqual(len(card.evidence), 3)
        text = sg.x_post(card)
        self.assertIn("PolitiFact 将该说法评为「Half True」。", text)
        self.assertTrue(publish_guard.check(card, [text], _clips(card), GOOD_REVIEW).allowed)
        # A verdict smuggled into the same line is still caught.
        self.assertTrue(publish_guard.forbidden_terms_in("他是骗子,PolitiFact 将该说法评为「False」。"))

    def test_parody_label_required(self):
        card = _ready_card()
        r = publish_guard.check(card, [sg.x_post(card)], _clips(card), GOOD_REVIEW, parody_mode=True)
        self.assertFalse(r.checks["parody_labeled"])

    def test_context_changed_cannot_be_打脸(self):
        a = make_claim(EARLIER, "2025-03-01")
        b = make_claim(LATER, "2026-09-20", context_before="Reporter: If Iran attacks, what then?")
        card = sg.card_from_comparison(compare(a, b))
        text = sg.x_post(card) + "\n打脸现场"
        self.assertFalse(publish_guard.check(card, [text], _clips(card), GOOD_REVIEW).checks["no_liar_verdict"])


class TestPipeline(unittest.TestCase):
    def test_archive_stage(self):
        db = ArchiveDB(":memory:")
        earlier, later = make_claim(EARLIER, "2025-03-01"), make_claim(LATER, "2026-09-20")
        other = make_claim("Tariffs on steel", "2025-05-01", topic="Tariffs", subtopic="steel")
        db.upsert_many([earlier, later, other])

        class Provider:
            name = "fake"

            def search(self, person, kw, since, until):
                if kw != "troops":
                    return []
                c = make_claim(EARLIER, "2025-03-02", source_name="Repost",
                               source_url="https://www.tiktok.com/@anon/1", video_url="")
                resolve_claim(c)
                return [c]

        event = NewsEvent("evt-1", "Iran", people=[PERSON], topics=["Iran"], event_date="2026-09-21")
        res = run_archive_stage(event, db, providers=[Provider()])
        kinds = [c.kind for c in res.cards]
        self.assertEqual(kinds, ["TIMELINE", "REWIND"])
        # The TikTok repost was folded into the White House original.
        self.assertEqual(len(res.cards[0].claims), 2)
        self.assertEqual(res.cards[1].status, ComparisonStatus.POSITION_CHANGED)
        self.assertEqual(db.get(later.claim_id).related_claim_id, earlier.claim_id)
        self.assertNotIn(other.claim_id, [c.claim_id for c in res.cards[0].claims])


if __name__ == "__main__":
    unittest.main()
