"""Information Gap engine — records.

英文世界的一手信号 → 中文互联网还没注意到 → 解释"这跟我有什么关系"。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Track(str, Enum):
    """The four content lines (主线)."""

    AI_TECH = "AI_TECH"  # AI / 科技信息差
    MONEY = "MONEY"  # 美国赚钱 / 小生意信息差
    US_LIFE = "US_LIFE"  # 美国普通人生活信息差
    INDUSTRY = "INDUSTRY"  # 产业 / 商业趋势信息差
    OTHER = "OTHER"


TRACK_ZH = {
    Track.AI_TECH: "AI 信息差",
    Track.MONEY: "美国赚钱/小生意",
    Track.US_LIFE: "美国生活",
    Track.INDUSTRY: "产业趋势",
    Track.OTHER: "其他",
}

# Score weights (sum = 1). Threshold to be worth making: 70.
WEIGHTS = {
    "overseas_heat": 0.30,
    "cn_scarcity": 0.30,
    "practical_value": 0.20,
    "discussion": 0.10,
    "visual": 0.10,
}
THRESHOLD = 70.0


def normalize_url(url: str) -> str:
    url = re.sub(r"^https?://(www\.)?", "", url.strip().lower())
    url = re.sub(r"[?#].*$", "", url)
    return url.rstrip("/")


@dataclass
class Signal:
    """One item seen on the English internet."""

    source: str  # hackernews | github | reddit:<sub> | ...
    title: str
    url: str  # the original (article / repo / post)
    discussion_url: str = ""  # HN / Reddit thread
    summary: str = ""
    created_at: str = ""  # ISO
    points: int = 0  # HN points, Reddit upvotes, GitHub stars
    comments: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def signal_id(self) -> str:
        return hashlib.sha256(normalize_url(self.url or self.discussion_url).encode()).hexdigest()[:16]


@dataclass
class Coverage:
    """How much the Chinese internet already covers this."""

    checked: bool
    query: str = ""
    zh_results: int = 0
    sample_urls: list[str] = field(default_factory=list)
    provider: str = ""


@dataclass
class Assessment:
    """Model (or heuristic) judgement of value to Chinese readers, plus a draft."""

    track: Track = Track.OTHER
    practical_value: float = 0.0  # 0-100
    discussion: float = 0.0
    visual: float = 0.0
    by_llm: bool = False
    # Draft (Stateverge structure)
    headline_zh: str = ""
    what: str = ""  # 发生了什么
    why_now: str = ""  # 为什么现在突然出现
    how_used: str = ""  # 美国人在怎么用
    why_cn: str = ""  # 中国用户为什么应该注意
    opportunity: str = ""  # 有没有赚钱机会
    risks: str = ""  # 限制/风险
    x_line: str = ""  # one-line hook for X


@dataclass
class ScoredSignal:
    signal: Signal
    coverage: Coverage
    assessment: Assessment
    overseas_heat: float
    cn_scarcity: float
    total: float
    notes: list[str] = field(default_factory=list)

    @property
    def is_gap(self) -> bool:
        return self.total >= THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["signal_id"] = self.signal.signal_id
        d["assessment"]["track"] = self.assessment.track.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScoredSignal":
        a = dict(d["assessment"])
        a["track"] = Track(a["track"])
        return cls(
            signal=Signal(**d["signal"]),
            coverage=Coverage(**d["coverage"]),
            assessment=Assessment(**a),
            overseas_heat=d["overseas_heat"],
            cn_scarcity=d["cn_scarcity"],
            total=d["total"],
            notes=list(d.get("notes", [])),
        )
