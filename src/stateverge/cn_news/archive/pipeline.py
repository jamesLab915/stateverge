"""StatevergeCN integration (Section 19).

    采集 → 热度 → 聚类
        → Political Archive Search → 历史言论匹配 → Claim Compare
        → Source Verification → Copyright Check → Archive Card
    → LLM 草稿 → 人审 → 发布

``run_archive_stage`` is called once per clustered event and answers
"这个人以前说过什么?". It returns Archive Cards for the LLM-draft stage; it
never publishes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .claim_matcher import same_issue
from .claim_search import SearchProvider, search_history
from .contradiction import StanceJudge, compare
from .database import ArchiveDB
from .models import ComparisonResult, ComparisonStatus, NewsEvent
from .script_generator import ArchiveCard, card_from_comparison, card_from_history


@dataclass
class ArchiveStageResult:
    event_id: str
    cards: list[ArchiveCard] = field(default_factory=list)
    # Pairs not turned into cards, kept so reviewers can see why.
    held_back: list[ComparisonResult] = field(default_factory=list)


def run_archive_stage(
    event: NewsEvent,
    db: ArchiveDB,
    providers: Iterable[SearchProvider] = (),
    stance_judge: StanceJudge | None = None,
    years: int = 10,
) -> ArchiveStageResult:
    result = ArchiveStageResult(event.event_id)
    history = search_history(event, db, providers, years=years)

    for person, claims in history.items():
        if not claims:
            continue
        topic = event.topics[0] if event.topics else claims[-1].topic
        result.cards.append(card_from_history(person, topic, claims))

        # Compare the most recent statement against each earlier one on the same issue.
        latest = claims[-1]
        for earlier in claims[:-1]:
            if earlier.statement_date >= latest.statement_date or not same_issue(earlier, latest):
                continue
            cmp = compare(earlier, latest, stance_judge=stance_judge)
            if cmp.status is ComparisonStatus.INSUFFICIENT_EVIDENCE:
                result.held_back.append(cmp)
                continue
            db.link(latest.claim_id, earlier.claim_id, cmp.status)
            result.cards.append(card_from_comparison(cmp))
    return result
