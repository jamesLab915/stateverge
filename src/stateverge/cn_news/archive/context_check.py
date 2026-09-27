"""Anti-out-of-context check (Section 9).

Before two statements can be framed as a reversal, both must have their
surrounding context on file, and the check below looks for the usual ways a
clip misleads: a different question, a hypothetical, the speaker quoting
someone else, a different audience or period, or an edited source clip.

Heuristics can only *raise* flags. Reviewers can add flags manually; nothing
here can clear a flag a reviewer set.
"""

from __future__ import annotations

import re
from typing import Iterable

from .claim_matcher import content_tokens, jaccard
from .models import ContextReport, PoliticalClaim

CONTEXT_FLAGS = frozenset(
    {
        "CLIP_EDITED",  # source clip was cut in a way that changes meaning
        "DIFFERENT_QUESTION",  # the two answers respond to different questions
        "DIFFERENT_TIMEFRAME",  # facts on the ground changed between the two
        "DIFFERENT_AUDIENCE",  # e.g. a foreign leader vs a domestic rally
        "HYPOTHETICAL",  # one statement answers an "if…" scenario
        "QUOTING_OTHERS",  # the speaker is paraphrasing someone else's view
    }
)

_HYPOTHETICAL = re.compile(
    r"\b(if|suppose|supposing|hypothetically|imagine|what if|in the event)\b|假如|如果|假设|假設",
    re.IGNORECASE,
)
_QUOTING = re.compile(
    r"\b(they say|they said|people say|some say|critics say|he said|she said|"
    r"according to|they claim|you hear)\b|有人说|他们说|據說|据说",
    re.IGNORECASE,
)
_QUESTION = re.compile(r"([^.?!。？！]*[?？])")


def _tail(text: str, chars: int = 300) -> str:
    return text[-chars:]


def _last_question(text: str) -> str:
    qs = _QUESTION.findall(_tail(text, 600))
    return qs[-1].strip() if qs else ""


def _auto_flags(claim: PoliticalClaim) -> list[str]:
    flags = []
    lead_in = _tail(claim.context_before, 200) + " " + claim.statement_text_original[:120]
    if _HYPOTHETICAL.search(lead_in):
        flags.append("HYPOTHETICAL")
    if _QUOTING.search(_tail(claim.context_before, 120) + " " + claim.statement_text_original[:60]):
        flags.append("QUOTING_OTHERS")
    return flags


def check_pair(
    earlier: PoliticalClaim,
    later: PoliticalClaim,
    manual_flags: Iterable[str] = (),
    question_overlap_threshold: float = 0.2,
) -> ContextReport:
    report = ContextReport()
    report.context_available = all(
        c.context_before.strip() or c.context_after.strip() for c in (earlier, later)
    )
    if not report.context_available:
        report.notes.append("缺少前后文:至少一条言论没有保存 context_before/context_after")

    flags: list[str] = []
    for flag in manual_flags:
        if flag not in CONTEXT_FLAGS:
            raise ValueError(f"unknown context flag: {flag}")
        flags.append(flag)

    # Framing that only one of the two statements has.
    a_auto, b_auto = set(_auto_flags(earlier)), set(_auto_flags(later))
    for flag in sorted(a_auto ^ b_auto):
        flags.append(flag)
        who = "较早" if flag in a_auto else "较新"
        report.notes.append(f"{who}的言论带有 {flag} 语境")

    q1, q2 = _last_question(earlier.context_before), _last_question(later.context_before)
    if q1 and q2 and jaccard(content_tokens(q1), content_tokens(q2)) < question_overlap_threshold:
        flags.append("DIFFERENT_QUESTION")
        report.notes.append(f"提问不同:「{q1}」vs「{q2}」")

    if earlier.office and later.office and earlier.office != later.office:
        report.notes.append(f"身份变化:{earlier.office} → {later.office}(不单独构成语境变化,需人工判断)")

    report.flags = sorted(set(flags))
    return report
