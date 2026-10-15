"""Tradable US macro releases.

``sign`` turns a surprise into a risk direction: +1 means "higher than expected
is hot / risk-off" (inflation, payrolls); -1 means the opposite
(unemployment rate). ``big`` is the surprise size that counts as a big wave.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventType:
    key: str
    name: str
    tier: str  # S / A / B — only S and A are traded
    series: str  # FRED series id
    transform: str  # "pct_mom" | "diff" | "level"
    unit: str
    big: float  # surprise (in unit) that counts as a big move
    sign: int
    time_et: str = "08:30"


CATALOG = [
    EventType("cpi", "CPI 环比", "S", "CPIAUCSL", "pct_mom", "%", 0.1, +1),
    EventType("core_cpi", "核心 CPI 环比", "S", "CPILFESL", "pct_mom", "%", 0.1, +1),
    EventType("nfp", "非农新增就业", "S", "PAYEMS", "diff", "千人", 50.0, +1),
    EventType("unrate", "失业率", "A", "UNRATE", "level", "%", 0.1, -1),
    EventType("ppi", "PPI 环比", "A", "PPIFIS", "pct_mom", "%", 0.2, +1),
    EventType("core_pce", "核心 PCE 环比", "A", "PCEPILFE", "pct_mom", "%", 0.1, +1),
    EventType("retail", "零售销售环比", "B", "RSAFS", "pct_mom", "%", 0.3, +1),
]
BY_KEY = {e.key: e for e in CATALOG}


def transform(values: list[float], how: str) -> float:
    """Latest reading from a list of observations (oldest → newest)."""
    if how == "level":
        return values[-1]
    if how == "diff":
        return values[-1] - values[-2]
    return (values[-1] / values[-2] - 1) * 100
