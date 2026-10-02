"""「Stateverge · 24H 信息差」— scan, select, draft, guard.

    英文互联网过去 24 小时 → 去重 → 按海外热度取前 N 条
    → 中文覆盖度检测 → AI 评估/草稿 → Information Gap Score
    → ≥70 分的 5–10 条进审核清单 → 最多发 3 条(尽量覆盖不同主线)
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .assessor import PLACEHOLDER, LLMAssessor
from .collectors import collect_all
from .models import THRESHOLD, TRACK_ZH, ScoredSignal, Track
from .scoring import heat, score

HYPE_WORDS = ("稳赚", "躺赚", "暴富", "保证收益", "无风险", "必赚", "零风险", "月入百万", "一夜", "割韭菜")
HEADER = "🇺🇸 Stateverge · 24H 信息差"
SUBHEAD = "今天英文互联网有{n}个值得中文用户注意的变化"
MARKS = "①②③④⑤"


@dataclass
class ScanResult:
    scanned_at: str
    collected: int
    shortlisted: list[ScoredSignal]
    candidates: list[ScoredSignal]  # ≥ threshold, best first
    picks: list[ScoredSignal]  # what the digest will post
    coverage_checked: bool
    llm_used: bool
    by_source: dict[str, int] = field(default_factory=dict)


def scan(collectors, coverage, assessor: LLMAssessor, hours: int = 24, shortlist: int = 20,
         max_candidates: int = 10, max_picks: int = 3, now: datetime | None = None) -> ScanResult:
    now = now or datetime.now(timezone.utc)
    signals = collect_all(collectors, hours=hours, now=now)
    # Only the hottest items get the (paid) coverage + LLM calls, with a
    # per-source quota so one source (e.g. GitHub) cannot fill the list.
    top = shortlist_balanced(signals, shortlist)
    scored = [score(s, coverage.check(s), assessor.assess(s)) for s in top]
    scored.sort(key=lambda x: x.total, reverse=True)
    candidates = [s for s in scored if s.is_gap][:max_candidates]
    return ScanResult(
        scanned_at=now.replace(microsecond=0).isoformat(),
        collected=len(signals),
        shortlisted=scored,
        candidates=candidates,
        picks=pick(candidates, max_picks),
        coverage_checked=any(s.coverage.checked for s in scored),
        llm_used=any(s.assessment.by_llm for s in scored),
        by_source=dict(Counter(_family(s) for s in signals)),
    )


def _family(s) -> str:
    return s.source.split(":", 1)[0]


def shortlist_balanced(signals, n: int):
    """Round-robin the hottest items of each source family, then fill by heat."""
    groups: dict[str, list] = {}
    for s in sorted(signals, key=heat, reverse=True):
        groups.setdefault(_family(s), []).append(s)
    out: list = []
    while len(out) < n and any(groups.values()):
        for fam in sorted(groups):
            if groups[fam] and len(out) < n:
                out.append(groups[fam].pop(0))
    return out


def pick(candidates: list[ScoredSignal], n: int = 3) -> list[ScoredSignal]:
    """Best first, but prefer covering different tracks before repeating one."""
    chosen: list[ScoredSignal] = []
    seen: set[Track] = set()
    for c in candidates:
        if len(chosen) < n and c.assessment.track not in seen:
            chosen.append(c)
            seen.add(c.assessment.track)
    for c in candidates:
        if len(chosen) < n and c not in chosen:
            chosen.append(c)
    return sorted(chosen, key=lambda x: x.total, reverse=True)


# -- copy ---------------------------------------------------------------------


def x_text(picks: list[ScoredSignal]) -> str:
    """X thread text; each item is its own paragraph so the splitter keeps it whole."""
    lines = [f"{HEADER}\n{SUBHEAD.format(n=len(picks))}"]
    for i, p in enumerate(picks):
        a = p.assessment
        lines.append(f"{MARKS[i]} {a.x_line or a.headline_zh}\n为什么重要:{a.why_cn}\n🔗 {p.signal.url}")
    lines.append("我们只做一件事:把英文互联网里真正有价值的信息,提前翻译成中文世界能理解的机会、风险和趋势。")
    return "\n\n".join(lines)


def review_markdown(result: ScanResult) -> str:
    out = [f"# Stateverge · 24H 信息差 审核稿 {result.scanned_at[:10]}", ""]
    out.append(f"- 抓取 {result.collected} 条,按热度评估 {len(result.shortlisted)} 条,≥{THRESHOLD:.0f} 分 "
               f"{len(result.candidates)} 条,入选 {len(result.picks)} 条")
    if result.by_source:
        out.append("- 各来源抓取:" + "、".join(f"{k} {v}" for k, v in sorted(result.by_source.items())))
    errors = sorted({s.coverage.error for s in result.shortlisted if s.coverage.error})
    if errors:
        out.append(f"- ❌ 中文覆盖度检测出错({result.shortlisted[0].coverage.provider}):" + ";".join(errors[:3]))
    if not result.coverage_checked:
        out.append("- ⚠️ 未检测中文覆盖度(没有 TAVILY_API_KEY / BRAVE_API_KEY),稀缺度按 50 计")
    if not result.llm_used:
        out.append(f"- ⚠️ 未使用 AI(没有 OPENAI_API_KEY),草稿为 {PLACEHOLDER},需要人工撰写")
    out.append("")
    for i, p in enumerate(result.candidates, 1):
        a, s = p.assessment, p.signal
        star = "✅ 入选" if p in result.picks else "候选"
        out += [
            f"## {i}. [{star}] {a.headline_zh}",
            f"- 主线:{TRACK_ZH[a.track]}|总分 **{p.total}**"
            f"(热度 {p.overseas_heat} / 稀缺 {p.cn_scarcity} / 实用 {a.practical_value:.0f} / "
            f"讨论 {a.discussion:.0f} / 可视化 {a.visual:.0f})",
            f"- 原文:{s.url}" + (f" |讨论:{s.discussion_url}" if s.discussion_url and s.discussion_url != s.url else ""),
            f"- 来源:{s.source}|热度 {s.points}|评论 {s.comments}|中文检索词:{p.coverage.query or '-'}",
        ]
        if p.coverage.sample_urls:
            out.append(f"- 已有中文报道示例:{', '.join(p.coverage.sample_urls[:3])}")
        out += [f"- 备注:{';'.join(p.notes)}" if p.notes else "", "",
                f"**发生了什么**:{a.what}", "", f"**为什么现在**:{a.why_now}", "",
                f"**美国人在怎么用**:{a.how_used}", "", f"**中国用户为什么应该注意**:{a.why_cn}", "",
                f"**有没有机会**:{a.opportunity}", "", f"**限制/风险**:{a.risks}", ""]
    if result.picks:
        out += ["---", "## X 预览", "", "```", x_text(result.picks), "```"]
    return "\n".join(out) + "\n"


# -- guard --------------------------------------------------------------------

DIGEST_BLOCK = "BLOCK_PUBLISH"
DIGEST_ALLOW = "ALLOW_PUBLISH"


@dataclass
class DigestReview:
    reviewer: str = ""
    facts_checked: bool = False  # 已打开原文核对事实,AI 没有编造
    access_checked: bool = False  # 已确认中国用户能否使用 / 地区限制写清楚


@dataclass
class DigestGuard:
    decision: str
    failures: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision == DIGEST_ALLOW


def check(picks: list[ScoredSignal], text: str, review: DigestReview) -> DigestGuard:
    failures = []
    if not picks:
        failures.append("没有入选内容")
    if not review.reviewer.strip():
        failures.append("缺少审核人署名")
    if not review.facts_checked:
        failures.append("审核人未确认已核对原文事实")
    if not review.access_checked:
        failures.append("审核人未确认中国用户可用性/限制已写清")
    if PLACEHOLDER in text:
        failures.append(f"文案仍有 {PLACEHOLDER} 占位内容")
    for p in picks:
        if not p.signal.url.startswith("http") or p.signal.url not in text:
            failures.append(f"缺少原文链接:{p.signal.title}")
        if not p.assessment.risks.strip() or p.assessment.risks.strip() == PLACEHOLDER:
            failures.append(f"缺少限制/风险说明:{p.signal.title}")
        if p.total < THRESHOLD:
            failures.append(f"低于 {THRESHOLD:.0f} 分:{p.signal.title}")
    hype = sorted({w for w in HYPE_WORDS if w in text})
    if hype:
        failures.append(f"出现夸大承诺用语:{hype}")
    if re.search(r"(?i)\b(guaranteed|get rich|risk[- ]free)\b", text):
        failures.append("出现英文夸大承诺用语")
    return DigestGuard(DIGEST_BLOCK if failures else DIGEST_ALLOW, failures)


def digest_key(day: str | None = None) -> str:
    return f"INFOGAP24H:{day or date.today().isoformat()}"
