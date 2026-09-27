"""Claim comparison (Sections 2B, 5, 9).

Outputs only the five ``ComparisonStatus`` values. It never outputs a
honesty verdict, and it is conservative by construction:

* different wording alone never yields a change — it yields INSUFFICIENT_EVIDENCE;
* any context flag yields CONTEXT_CHANGED, which is never framed as a "打脸";
* a failed PREDICTION is at most POSITION_CHANGED, never a contradiction;
* unverified transcripts or lead-only (Tier E) sources yield INSUFFICIENT_EVIDENCE.

The stance heuristic is intentionally crude. Production callers should pass a
``stance_judge`` (e.g. an LLM prompt reading both full contexts); either way
the result is a *candidate* that goes to human review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Literal

from .claim_matcher import content_tokens, jaccard, same_issue
from .context_check import check_pair
from .models import ClaimType, ComparisonResult, ComparisonStatus, PoliticalClaim
from .source_resolver import is_final_evidence
from .transcript import tokens

Stance = Literal["SAME", "SHIFTED", "OPPOSITE", "UNCLEAR"]
StanceJudge = Callable[[PoliticalClaim, PoliticalClaim], Stance]

_NEGATIONS = frozenset(
    "not no never none nobody nothing neither nor cannot without against".split()
)
_CJK_NEGATIONS = ("不", "没", "沒", "无", "無", "未", "别", "別", "非")

# Claim types where opposite statements cannot both be true.
_FACTUAL_TYPES = frozenset(
    {ClaimType.FACTUAL_CLAIM.value, ClaimType.DENIAL.value, ClaimType.ACCUSATION.value}
)


def _polarity(text: str) -> int:
    count = 0
    for t in tokens(text):
        if t in _NEGATIONS or t.endswith("n't"):
            count += 1
        elif len(t) == 1 and t in _CJK_NEGATIONS:
            count += 1
    return count % 2


def heuristic_stance(a: PoliticalClaim, b: PoliticalClaim) -> Stance:
    ta, tb = a.statement_text_original, b.statement_text_original
    overlap = jaccard(content_tokens(ta) - _NEGATIONS, content_tokens(tb) - _NEGATIONS)
    if overlap < 0.35:
        return "UNCLEAR"  # different wording is not evidence of a change
    if _polarity(ta) != _polarity(tb):
        return "OPPOSITE"
    return "SAME" if overlap >= 0.6 else "UNCLEAR"


def compare(
    earlier: PoliticalClaim,
    later: PoliticalClaim,
    stance_judge: StanceJudge | None = None,
    manual_context_flags: Iterable[str] = (),
) -> ComparisonResult:
    if earlier.person_id != later.person_id:
        raise ValueError("comparisons are only made between statements by the same person")
    if earlier.statement_date > later.statement_date:
        earlier, later = later, earlier

    def result(status: ComparisonStatus, *reasons: str, context=None) -> ComparisonResult:
        return ComparisonResult(earlier, later, status, list(reasons), context)

    if not same_issue(earlier, later):
        return result(ComparisonStatus.INSUFFICIENT_EVIDENCE, "两段言论讨论的不是同一问题")

    for c in (earlier, later):
        if not c.transcript_verified:
            return result(ComparisonStatus.INSUFFICIENT_EVIDENCE, f"{c.statement_date} 的原话尚未逐字核对")
        if not is_final_evidence(c):
            return result(
                ComparisonStatus.INSUFFICIENT_EVIDENCE,
                f"{c.statement_date} 的来源为 E 级线索,需找到原始来源",
            )

    context = check_pair(earlier, later, manual_context_flags)
    if not context.context_available:
        return result(ComparisonStatus.INSUFFICIENT_EVIDENCE, *context.notes, context=context)
    if context.context_changed:
        return result(
            ComparisonStatus.CONTEXT_CHANGED,
            f"语境差异:{', '.join(context.flags)}",
            *context.notes,
            context=context,
        )

    stance = (stance_judge or heuristic_stance)(earlier, later)
    if stance not in ("SAME", "SHIFTED", "OPPOSITE", "UNCLEAR"):
        raise ValueError(f"stance_judge returned an unknown stance: {stance!r}")

    if stance == "SAME":
        return result(ComparisonStatus.CONSISTENT, "两次说法一致", context=context)
    if stance == "UNCLEAR":
        return result(
            ComparisonStatus.INSUFFICIENT_EVIDENCE, "措辞不同,但不足以判断立场变化", context=context
        )
    if stance == "SHIFTED":
        return result(ComparisonStatus.POSITION_CHANGED, "立场出现变化", context=context)

    # OPPOSITE
    if earlier.claim_type == ClaimType.PREDICTION.value:
        return result(
            ComparisonStatus.POSITION_CHANGED,
            "较早的言论是预测;预测未实现不等于撒谎,只标记为说法变化",
            context=context,
        )
    if earlier.claim_type in _FACTUAL_TYPES and later.claim_type in _FACTUAL_TYPES:
        return result(ComparisonStatus.APPARENT_CONTRADICTION, "两个事实性说法看起来互相矛盾", context=context)
    return result(ComparisonStatus.POSITION_CHANGED, "前后立场相反", context=context)


# -- 承诺保质期 (Section 2C) ---------------------------------------------------


@dataclass
class OutcomeRecord:
    """What actually happened: a policy action, official record, or data point."""

    date: str
    description: str
    source_name: str
    source_url: str


@dataclass
class PromiseTrack:
    promise: PoliticalClaim
    later_statements: list[PoliticalClaim] = field(default_factory=list)
    outcomes: list[OutcomeRecord] = field(default_factory=list)

    def timeline(self) -> list[tuple[str, str, str]]:
        """(date, kind, text) rows in order: 承诺 → 上任后讲话 → 实际政策/结果."""
        rows = [(self.promise.statement_date, "承诺", self.promise.statement_text_original)]
        rows += [(c.statement_date, "后续讲话", c.statement_text_original) for c in self.later_statements]
        rows += [(o.date, "实际结果", o.description) for o in self.outcomes]
        return sorted(rows, key=lambda r: r[0])


def track_promise(
    promise: PoliticalClaim,
    candidates: Iterable[PoliticalClaim],
    outcomes: Iterable[OutcomeRecord] = (),
) -> PromiseTrack:
    if promise.claim_type not in (ClaimType.PROMISE.value, ClaimType.CAMPAIGN_STATEMENT.value):
        raise ValueError("track_promise expects a PROMISE or CAMPAIGN_STATEMENT claim")
    later = [
        c
        for c in candidates
        if c.claim_id != promise.claim_id
        and c.person_id == promise.person_id
        and c.statement_date > promise.statement_date
        and same_issue(promise, c)
    ]
    return PromiseTrack(
        promise,
        sorted(later, key=lambda c: c.statement_date),
        sorted(outcomes, key=lambda o: o.date),
    )
