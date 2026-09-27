"""Pre-publish risk check (Sections 5, 8, 12, 13, 14, 17).

Every item on the Section 17 checklist must pass; any failure returns
BLOCK_PUBLISH. Items a machine can verify are computed from the data.
Items that need judgement are reviewer attestations — required, but they can
never override a failed automatic check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from . import fact_check
from .claim_matcher import same_issue
from .clip_builder import DEFAULT_MAX_SECONDS, HARD_MAX_SECONDS, ClipPlan
from .copyright import is_copyright_recorded
from .models import FORBIDDEN_VERDICT_TERMS, ComparisonStatus, PoliticalClaim
from .script_generator import PARODY_LABEL, ArchiveCard
from .source_resolver import is_final_evidence

BLOCK_PUBLISH = "BLOCK_PUBLISH"
ALLOW_PUBLISH = "ALLOW_PUBLISH"

# Straight quotes are not scanned: they appear inside English originals.
_QUOTED = re.compile(r"“([^”]+)”|「([^」]+)」|『([^』]+)』")
# Output of fact_check.attribution_sentence().
_ATTRIBUTION = re.compile(
    "(?:" + "|".join(re.escape(o) for o in fact_check.RECOGNISED_FACT_CHECKERS) + ") 将该说法评为「[^」]*」。"
)


@dataclass
class ReviewerAttestation:
    """Judgement calls signed off by the human reviewer (Section 10: 人工审核)."""

    reviewer: str = ""
    opinion_not_stated_as_fact: bool = False  # 没有把观点误写成事实
    corrections_checked: bool = False  # 已检查后续更正
    context_reviewed: bool = False  # 已读取前后完整语义


@dataclass
class GuardResult:
    decision: str
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW_PUBLISH


def forbidden_terms_in(text: str) -> list[str]:
    """Verdict words used as Stateverge's own voice. Sentences that attribute a
    rating to a named fact-checker are exempt (Section 5), and so are quoted
    passages — those are the speaker's own words, checked separately."""
    text = _ATTRIBUTION.sub("", text)
    text = _QUOTED.sub("", text)
    upper = text.upper()
    hits: list[str] = []
    for term in FORBIDDEN_VERDICT_TERMS:
        if term.isascii():
            if re.search(rf"\b{term}\b", upper):
                hits.append(term)
        elif term in text:
            hits.append(term)
    return sorted(set(hits))


def unsourced_quotes(text: str, claims: list[PoliticalClaim]) -> list[str]:
    """Quoted passages that are not verbatim from a stored claim or rating."""
    allowed = []
    for c in claims:
        allowed += [c.statement_text_original, c.statement_text_zh, c.fact_check_rating]
    allowed = [a for a in allowed if a]
    bad = []
    for m in _QUOTED.finditer(text):
        q = next(g for g in m.groups() if g is not None).strip()
        if not any(q in a for a in allowed):
            bad.append(q)
    return bad


def _valid_past_date(iso: str) -> bool:
    try:
        return date.fromisoformat(iso) <= date.today()
    except ValueError:
        return False


def check(
    card: ArchiveCard,
    texts: list[str],
    clips: list[ClipPlan],
    attestation: ReviewerAttestation,
    parody_mode: bool = False,
) -> GuardResult:
    claims = card.claims
    all_text = "\n".join(texts)
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def item(key: str, ok: bool, why: str) -> None:
        checks[key] = ok
        if not ok:
            failures.append(f"{key}: {why}")

    item("quote_verified", bool(claims) and all(c.transcript_verified for c in claims), "原话未逐字核对")
    item("date_correct", all(_valid_past_date(c.statement_date) for c in claims), "日期无效或在未来")
    item(
        "original_source",
        all(is_final_evidence(c) and (c.source_url or c.video_url) for c in claims),
        "来源不是原始来源(E级只能作线索)或缺少链接",
    )
    item(
        "context_complete",
        attestation.context_reviewed
        and all(c.context_before.strip() or c.context_after.strip() for c in claims),
        "缺少前后文或未人工阅读上下文",
    )
    same = True
    if card.kind == "REWIND":
        same = len(claims) == 2 and same_issue(claims[0], claims[1])
        same = same and card.status is not ComparisonStatus.INSUFFICIENT_EVIDENCE
    item("same_issue", same, "两句话讨论的不是同一问题,或证据不足")
    item("human_reviewed", bool(attestation.reviewer.strip()), "缺少审核人署名")
    item("opinion_vs_fact", attestation.opinion_not_stated_as_fact, "审核人未确认没有把观点写成事实")
    hits = forbidden_terms_in(all_text)
    if card.status is ComparisonStatus.CONTEXT_CHANGED and "打脸" in all_text:
        hits.append("打脸")
    item("no_liar_verdict", not hits, f"文案出现判定用语 {hits}(预测失败≠撒谎;评级须归属事实核查机构)")
    item("corrections_checked", attestation.corrections_checked, "审核人未确认是否存在后续更正")
    item("copyright_recorded", all(is_copyright_recorded(c) for c in claims), "素材版权来源未记录")
    item(
        "minimal_excerpt",
        all(
            p.duration <= DEFAULT_MAX_SECONDS or (p.extend_reason and p.duration <= HARD_MAX_SECONDS)
            for p in clips
        ),
        "片段超过必要长度",
    )
    item(
        "transformative",
        all(p.transformative for p in clips) and bool(all_text.strip()),
        "缺少中文旁白/对比/核查/时间线等转换性内容",
    )
    item("parody_labeled", (not parody_mode) or all(PARODY_LABEL in t for t in texts), "讽刺内容未标记")
    bad = unsourced_quotes(all_text, claims)
    item("no_fabricated_quotes", not bad, f"引语不在数据库中: {bad}")

    # Section 12: at least two pieces of evidence; three for fact-check content.
    needed = 3 if card.uses_fact_check else 2
    item("evidence_count", len(card.evidence) >= needed, f"证据不足:需要 {needed} 条,现有 {len(card.evidence)} 条")
    if card.uses_fact_check:
        rated = [c for c in claims if fact_check.attribution_sentence(c)]
        item(
            "fact_check_attributed",
            all(fact_check.attribution_sentence(c) in all_text for c in rated),
            "事实核查结论必须以「某机构将该说法评为……」形式出现",
        )

    return GuardResult(BLOCK_PUBLISH if failures else ALLOW_PUBLISH, checks, failures)
