"""Deduplication and "same question?" matching between claims."""

from __future__ import annotations

from datetime import date
from itertools import combinations

from .models import PoliticalClaim, SourceTier
from .transcript import tokens

# Function words that carry no topical signal.
_STOP = frozenset(
    """a an the and or but if of to in on at by for with from as is are was were be been
    being it its this that these those i you he she we they me him her us them my your our
    their do does did done have has had will would shall should can could may might must
    not no so very just than then there here what which who whom when where why how all
    any some more most much many going gonna get got say said says""".split()
)

_TIER_RANK = {t.value: i for i, t in enumerate(SourceTier)}  # A=0 best … E=4


def content_tokens(text: str) -> set[str]:
    # Single CJK characters are meaningful tokens; single Latin letters are not.
    return {t for t in tokens(text) if t not in _STOP and (len(t) > 1 or not t.isascii())}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def text_similarity(a: PoliticalClaim, b: PoliticalClaim) -> float:
    return jaccard(content_tokens(a.statement_text_original), content_tokens(b.statement_text_original))


def is_duplicate(a: PoliticalClaim, b: PoliticalClaim, threshold: float = 0.85, max_days: int = 3) -> bool:
    """Same speaker saying the same words within a few days = one statement
    reported by several outlets."""
    if a.person_id != b.person_id:
        return False
    days = abs((date.fromisoformat(a.statement_date) - date.fromisoformat(b.statement_date)).days)
    return days <= max_days and text_similarity(a, b) >= threshold


def _better(a: PoliticalClaim, b: PoliticalClaim) -> PoliticalClaim:
    """Prefer the higher-tier source, then a verified transcript, then confidence."""
    key_a = (_TIER_RANK[a.source_type], not a.transcript_verified, -a.confidence)
    key_b = (_TIER_RANK[b.source_type], not b.transcript_verified, -b.confidence)
    return a if key_a <= key_b else b


def dedupe(claims: list[PoliticalClaim]) -> list[PoliticalClaim]:
    kept: list[PoliticalClaim] = []
    for claim in claims:
        for i, existing in enumerate(kept):
            if claim.claim_id == existing.claim_id or is_duplicate(claim, existing):
                kept[i] = _better(existing, claim)
                break
        else:
            kept.append(claim)
    return sorted(kept, key=lambda c: (c.statement_date, c.created_at))


def same_issue(a: PoliticalClaim, b: PoliticalClaim, min_overlap: float = 0.15) -> bool:
    """Do two claims address the same question? Section 17 requires this before
    any comparison is published."""
    if a.topic.strip().lower() != b.topic.strip().lower():
        return False
    sa, sb = a.subtopic.strip().lower(), b.subtopic.strip().lower()
    if sa and sb:
        return sa == sb
    return text_similarity(a, b) >= min_overlap


def candidate_pairs(claims: list[PoliticalClaim]) -> list[tuple[PoliticalClaim, PoliticalClaim]]:
    """All chronologically ordered (earlier, later) pairs from the same person on
    the same issue."""
    ordered = sorted(claims, key=lambda c: c.statement_date)
    return [
        (a, b)
        for a, b in combinations(ordered, 2)
        if a.person_id == b.person_id and a.statement_date < b.statement_date and same_issue(a, b)
    ]
