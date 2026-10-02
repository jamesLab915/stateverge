"""Information Gap Score.

    海外热度 × 30% + 中文稀缺度 × 30% + 实用价值 × 20% + 讨论潜力 × 10% + 可视化素材 × 10%

Heat and scarcity are measured (engagement numbers, Chinese search results).
Practical value, discussion potential, visual material and the track come
from the LLM assessment when available, otherwise from keyword heuristics
(and the item is marked as heuristic in the review file). ≥ 70 = worth making.
"""

from __future__ import annotations

import math
import re

from .models import WEIGHTS, Assessment, Coverage, ScoredSignal, Signal, Track

# Engagement that maps to a heat of 100, per source.
_HEAT_CEILING = {"hackernews": 1000, "github": 2000, "reddit": 5000}

_TRACK_WORDS: dict[Track, tuple[str, ...]] = {
    Track.AI_TECH: ("ai", "llm", "gpt", "claude", "gemini", "agent", "agents", "model", "open source", "open-source",
                    "automation", "copilot", "rag", "inference", "diffusion", "transformer", "ml", "chatbot"),
    Track.MONEY: ("side hustle", "revenue", "mrr", "saas", "startup", "small business", "business", "sell",
                  "etsy", "shopify", "amazon fba", "dropshipping", "freelance", "income", "profit", "customers",
                  "one-person", "solo", "bootstrapped", "$"),
    Track.US_LIFE: ("rent", "salary", "wage", "costco", "walmart", "target", "insurance", "car", "uber", "doordash",
                    "instacart", "gig", "tips", "groceries", "prices", "credit card", "landlord", "tax", "paycheck"),
    Track.INDUSTRY: ("data center", "datacenter", "power grid", "electricity", "nuclear", "robot", "robotics",
                     "autonomous", "self-driving", "waymo", "battery", "storage", "manufacturing", "reshoring",
                     "chip", "semiconductor", "tariff", "factory", "creator economy", "hollywood"),
}
_PRACTICAL = ("how i", "how we", "made $", "tool", "free", "launch", "template", "guide", "cost", "pricing",
              "replace", "automate", "open source", "self-host", "workflow", "tutorial")
_DEBATE = ("why", "vs", "ban", "lawsuit", "layoff", "controvers", "should", "is dead", "killed", "debate", "?")
_VISUAL = ("video", "demo", "chart", "screenshot", "image", "map", "photo", "visual", "robot", "github")


def heat(signal: Signal) -> float:
    family = signal.source.split(":", 1)[0]
    ceiling = _HEAT_CEILING.get(family, 1000)
    engagement = signal.points + 0.5 * signal.comments
    return round(min(100.0, 100 * math.log10(engagement + 1) / math.log10(ceiling + 1)), 1)


def scarcity(coverage: Coverage) -> float:
    if not coverage.checked:
        return 50.0  # unknown → neutral, flagged in notes
    n = coverage.zh_results
    if n == 0:
        return 100.0
    if n <= 2:
        return 80.0
    if n <= 5:
        return 60.0
    if n <= 10:
        return 35.0
    return 10.0


def _hits(text: str, words: tuple[str, ...]) -> int:
    t = f" {text.lower()} "
    return sum(1 for w in words if (w in t if not w.isalpha() else re.search(rf"\b{re.escape(w)}\b", t)))


def heuristic_assessment(signal: Signal) -> Assessment:
    text = " ".join([signal.title, signal.summary, " ".join(signal.tags), signal.source])
    track_hits = {t: _hits(text, words) for t, words in _TRACK_WORDS.items()}
    if signal.source == "github":
        track_hits[Track.AI_TECH] += 1
    best = max(track_hits, key=track_hits.get)
    track = best if track_hits[best] > 0 else Track.OTHER
    return Assessment(
        track=track,
        practical_value=min(100.0, 30 + 15 * _hits(text, _PRACTICAL) + (10 if track is not Track.OTHER else 0)),
        discussion=min(100.0, 20 + 20 * _hits(text, _DEBATE) + min(30, signal.comments / 10)),
        visual=min(100.0, 30 + 20 * _hits(text, _VISUAL)),
        by_llm=False,
    )


_CJK = re.compile(r"[\u4e00-\u9fff]")


def score(signal: Signal, coverage: Coverage, assessment: Assessment) -> ScoredSignal:
    h, s = heat(signal), scarcity(coverage)
    native_zh = bool(_CJK.search(f"{signal.title} {signal.summary}"))
    if native_zh:
        s = min(s, 10.0)  # the source itself is Chinese-facing: no gap to bridge
    total = (
        WEIGHTS["overseas_heat"] * h
        + WEIGHTS["cn_scarcity"] * s
        + WEIGHTS["practical_value"] * assessment.practical_value
        + WEIGHTS["discussion"] * assessment.discussion
        + WEIGHTS["visual"] * assessment.visual
    )
    notes = []
    if native_zh:
        notes.append("原文本身含中文,已面向中文用户(稀缺度按 10 计)")
    elif not coverage.checked:
        notes.append("中文覆盖度未检测(稀缺度按 50 计)")
    elif coverage.zh_results:
        notes.append(f"中文近一周已有 {coverage.zh_results} 条相关结果")
    if not assessment.by_llm:
        notes.append("实用价值等为关键词估算,未经 AI 评估")
    return ScoredSignal(signal, coverage, assessment, h, s, round(total, 1), notes)
