"""Archive Cards, X posts and video scripts (Sections 2, 13–16).

Every politician quote placed in copy comes verbatim from
``statement_text_original`` of a stored claim — this module has no way to
accept free-form quote text. Copy describes ("2025年他说的是A,2026年变成了B"),
it does not judge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from . import fact_check
from .contradiction import PromiseTrack
from .models import ComparisonResult, ComparisonStatus, Evidence, PoliticalClaim

BRAND = "STATEVERGE ARCHIVE"
SLOGAN = "政治人物会改口,录像不会。"
PARODY_LABEL = "【讽刺 / 虚构情景】"

STATUS_ZH = {
    ComparisonStatus.CONSISTENT: "前后一致",
    ComparisonStatus.POSITION_CHANGED: "立场变化",
    ComparisonStatus.APPARENT_CONTRADICTION: "说法明显不一致",
    ComparisonStatus.CONTEXT_CHANGED: "背景不同",
    ComparisonStatus.INSUFFICIENT_EVIDENCE: "证据不足",
}


class NotPublishable(ValueError):
    pass


def zh_month(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.year}年{d.month}月"


def zh_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.year}年{d.month}月{d.day}日"


def quote(claim: PoliticalClaim) -> str:
    return f"“{claim.statement_text_original}”"


def _evidence_for(claim: PoliticalClaim, role: str) -> Evidence:
    return Evidence(
        role=role,
        description=f"{claim.person_name} {claim.statement_date} 原话",
        source_name=claim.source_name,
        source_url=claim.source_url or claim.video_url,
        source_tier=claim.source_type,
        date=claim.statement_date,
    )


@dataclass
class ArchiveCard:
    """The unit handed to the LLM-draft stage and to human review."""

    kind: str  # REWIND (录像不会失忆 / 当时你可不是这么说的) | PROMISE | TIMELINE
    person_name: str
    topic: str
    claims: list[PoliticalClaim]
    status: ComparisonStatus | None = None
    reasons: list[str] = field(default_factory=list)
    context_notes: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    timeline: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def uses_fact_check(self) -> bool:
        return any(e.role == "FACT_CHECK" for e in self.evidence)


def card_from_comparison(comparison: ComparisonResult) -> ArchiveCard:
    a, b = comparison.earlier, comparison.later
    evidence = [_evidence_for(a, "ORIGINAL"), _evidence_for(b, "FOLLOW_UP")]
    for c in (a, b):
        fc = fact_check.as_evidence(c)
        if fc:
            evidence.append(fc)
    return ArchiveCard(
        kind="REWIND",
        person_name=a.person_name,
        topic=a.topic,
        claims=[a, b],
        status=comparison.status,
        reasons=list(comparison.reasons),
        context_notes=list(comparison.context.notes) if comparison.context else [],
        evidence=evidence,
    )


def card_from_promise(track: PromiseTrack) -> ArchiveCard:
    p = track.promise
    evidence = [_evidence_for(p, "ORIGINAL")]
    evidence += [_evidence_for(c, "FOLLOW_UP") for c in track.later_statements]
    evidence += [
        Evidence("FOLLOW_UP", o.description, o.source_name, o.source_url, date=o.date) for o in track.outcomes
    ]
    return ArchiveCard(
        kind="PROMISE",
        person_name=p.person_name,
        topic=p.topic,
        claims=[p, *track.later_statements],
        evidence=evidence,
        timeline=track.timeline(),
    )


def card_from_history(person_name: str, topic: str, claims: Iterable[PoliticalClaim]) -> ArchiveCard:
    """今天翻旧账: a chronological list of what the person said on the topic."""
    ordered = sorted(claims, key=lambda c: c.statement_date)
    return ArchiveCard(
        kind="TIMELINE",
        person_name=person_name,
        topic=topic,
        claims=ordered,
        evidence=[_evidence_for(c, "ORIGINAL" if i == 0 else "FOLLOW_UP") for i, c in enumerate(ordered)],
        timeline=[(c.statement_date, "讲话", c.statement_text_original) for c in ordered],
    )


def _require_comparable(card: ArchiveCard) -> tuple[PoliticalClaim, PoliticalClaim]:
    if card.kind != "REWIND" or len(card.claims) != 2:
        raise NotPublishable("this template needs a two-claim REWIND card")
    if card.status is ComparisonStatus.INSUFFICIENT_EVIDENCE:
        raise NotPublishable("INSUFFICIENT_EVIDENCE: 不生成对比内容")
    return card.claims[0], card.claims[1]


def _gap_phrase(a: PoliticalClaim, b: PoliticalClaim) -> str:
    da, db = date.fromisoformat(a.statement_date), date.fromisoformat(b.statement_date)
    months = (db.year - da.year) * 12 + (db.month - da.month)
    return f"{months}个月" if months >= 1 else f"{(db - da).days}天"


def status_line(card: ArchiveCard, a: PoliticalClaim, b: PoliticalClaim) -> str:
    gap = _gap_phrase(a, b)
    return {
        ComparisonStatus.CONSISTENT: f"同一个问题,相隔{gap},说法前后一致。",
        ComparisonStatus.POSITION_CHANGED: f"同一个问题,相隔{gap},两种说法。",
        ComparisonStatus.APPARENT_CONTRADICTION: f"同一个问题,相隔{gap},两个说法看起来无法同时成立。",
        ComparisonStatus.CONTEXT_CHANGED: "这两次讲话存在明显差异,但背景也发生了变化。",
    }[card.status]


def x_post(card: ArchiveCard) -> str:
    """Section 16 template."""
    a, b = _require_comparable(card)
    lines = [
        "📼 录像不会失忆",
        "",
        f"【{zh_date(a.statement_date)}】",
        f"{a.person_name}:",
        quote(a),
    ]
    if a.statement_text_zh:
        lines.append(f"(中文译文:{a.statement_text_zh})")
    lines += ["", f"【{zh_date(b.statement_date)}】", f"{b.person_name}:", quote(b)]
    if b.statement_text_zh:
        lines.append(f"(中文译文:{b.statement_text_zh})")
    lines += ["", "两段讲话针对的是:", a.subtopic or a.topic, ""]
    lines.append(status_line(card, a, b))
    if card.context_notes:
        lines += ["", "背景变化:", *card.context_notes]
    for c in (a, b):
        sentence = fact_check.attribution_sentence(c)
        if sentence:
            lines += ["", sentence]
    lines += ["", "原始来源:"]
    lines += [f"{e.source_name} {e.source_url}".strip() for e in card.evidence]
    lines += ["", "Stateverge Archive", "", "让原始记录自己说话。"]
    return "\n".join(lines)


@dataclass
class Shot:
    kind: str  # TITLE / CLIP / FREEZE / NARRATION / TIMELINE / END
    text: str = ""
    caption: str = ""
    claim_id: str = ""


def video_script(card: ArchiveCard) -> list[Shot]:
    """Section 15 template. CONTEXT_CHANGED gets an explainer, not a "打脸" cut."""
    a, b = _require_comparable(card)
    gap = _gap_phrase(a, b)
    shots = [
        Shot("TITLE", "录像不会失忆。"),
        Shot("CLIP", caption=f"{zh_month(a.statement_date)}\n{a.person_name}", claim_id=a.claim_id),
        Shot("FREEZE", claim_id=a.claim_id),
        Shot("NARRATION", f"这是{a.person_name}当时的说法。"),
        Shot("CLIP", caption=f"{zh_month(b.statement_date)}\n{b.person_name}", claim_id=b.claim_id),
    ]
    if card.status is ComparisonStatus.CONTEXT_CHANGED:
        shots.append(Shot("NARRATION", f"{gap}之后,说法不同了,但当时的背景也不一样。"))
        shots += [Shot("NARRATION", note) for note in card.context_notes]
    elif card.status is ComparisonStatus.CONSISTENT:
        shots.append(Shot("NARRATION", f"{gap}之后,{b.person_name}的说法没有变。"))
    else:
        shots.append(Shot("NARRATION", f"{gap}之后,{b.person_name}给出了另一个版本。"))
    for c in (a, b):
        sentence = fact_check.attribution_sentence(c)
        if sentence:
            shots.append(Shot("NARRATION", sentence))
    shots += [
        Shot("TIMELINE", f"{date.fromisoformat(a.statement_date).year}\n↓\n{date.fromisoformat(b.statement_date).year}"),
        Shot("END", "我们不替你判断。\n我们只是按了重播键。\n" + BRAND),
    ]
    return shots


def promise_text(card: ArchiveCard) -> str:
    """承诺保质期: "这是当时的承诺,这是后来发生的事情。" """
    if card.kind != "PROMISE":
        raise NotPublishable("promise_text needs a PROMISE card")
    lines = [f"⏳ 承诺保质期|{card.person_name}|{card.topic}", ""]
    for d, kind, text in card.timeline:
        body = f"“{text}”" if kind != "实际结果" else text
        lines.append(f"{zh_date(d)}|{kind}:{body}")
    lines += ["", "这是当时的承诺,这是后来发生的事情。", "", "原始来源:"]
    lines += [f"{e.source_name} {e.source_url}".strip() for e in card.evidence]
    lines += ["", BRAND]
    return "\n".join(lines)


def history_text(card: ArchiveCard) -> str:
    """今天翻旧账."""
    if card.kind != "TIMELINE":
        raise NotPublishable("history_text needs a TIMELINE card")
    lines = [f"🗂 今天翻旧账|{card.person_name} 谈 {card.topic}", ""]
    for c in card.claims:
        lines.append(f"{zh_date(c.statement_date)}({c.source_name}):{quote(c)}")
    lines += ["", "按时间排列,原始记录如上。", BRAND]
    return "\n".join(lines)


def parody_script(
    fictional_lines: list[tuple[str, str]],
    real_claims: list[PoliticalClaim],
    known_politicians: Iterable[str] = (),
) -> str:
    """PARODY_MODE. Fictional characters may speak (unquoted); a real politician
    can only appear through stored quotes with date and source."""
    real_names = {c.person_name.lower() for c in real_claims} | {n.lower() for n in known_politicians}
    out = [PARODY_LABEL, ""]
    for speaker, line in fictional_lines:
        if speaker.lower() in real_names:
            raise NotPublishable(f"不得为真实政治人物 {speaker} 编写台词")
        out.append(f"{speaker}(虚构):{line}")
    for c in real_claims:
        out.append(f"{c.person_name}(真实原话,{zh_date(c.statement_date)},{c.source_name}):{quote(c)}")
    out += ["", PARODY_LABEL]
    return "\n".join(out)
