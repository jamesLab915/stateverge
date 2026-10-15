"""War report: surprise → direction → contracts, with the hard risk rules attached."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .events import EventType

BUDGET = 1000.0  # total per event
MAX_PER_CONTRACT = 400.0  # premium ×100 per contract
RULES = [
    "单张期权 ≤ $400,总仓位 ≤ $1,000;每个事件最多 2 笔,看错不反手",
    "只用限价单;买卖价差 > 10% 不做",
    "+100% 平一半,剩余回吐 40% 全走;-50% 全斩",
    "SPY/QQQ 0DTE 15:00 前清仓;个股周权周五收市前清仓;不加仓摊平",
    "当日亏损达 $1,000 即停",
]


@dataclass
class Leg:
    symbol: str
    side: str  # "put" | "call"
    ref_price: float | None
    strike: float | None
    note: str = ""


@dataclass
class WarReport:
    event: str
    tier: str
    actual: float
    consensus: float | None
    consensus_source: str
    surprise: float | None
    size: str  # "big" | "small" | "unknown"
    direction: str  # "risk_off" | "risk_on" | "none"
    legs: list[Leg] = field(default_factory=list)
    rules: list[str] = field(default_factory=lambda: list(RULES))
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def strike_step(price: float) -> float:
    return 0.5 if price < 25 else 1.0 if price < 200 else 5.0


def one_otm(price: float, side: str) -> float:
    step = strike_step(price)
    target = price * (0.99 if side == "put" else 1.01)
    return (math.floor if side == "put" else math.ceil)(target / step) * step


def contracts_for(premium: float) -> int:
    """How many contracts the rules allow at a given premium (per share)."""
    cost = premium * 100
    if cost <= 0 or cost > MAX_PER_CONTRACT:
        return 0
    return int(BUDGET // cost)


def build(ev: EventType, actual: float, consensus: float | None, consensus_source: str,
          pool: list[str], prices: dict[str, float | None]) -> WarReport:
    if consensus is None:
        return WarReport(ev.name, ev.tier, actual, None, consensus_source, None, "unknown", "none",
                         warnings=["没有预期值,无法判断偏离,不出交易方向"])
    surprise = round(actual - consensus, 3)
    size = "big" if abs(surprise) >= ev.big else "small"
    direction = "none"
    if size == "big" and ev.tier in ("S", "A"):
        direction = "risk_off" if surprise * ev.sign > 0 else "risk_on"
    rep = WarReport(ev.name, ev.tier, actual, consensus, consensus_source, surprise, size, direction)
    if consensus_source != "manual":
        rep.warnings.append(f"预期值来自 {consensus_source},请在 Moomoo 核对后再参考")
    if direction == "none":
        rep.warnings.append("偏离不够大或事件级别不够,今天不做")
        return rep
    side = "put" if direction == "risk_off" else "call"
    for sym in pool:
        p = prices.get(sym)
        rep.legs.append(Leg(sym, side, p, one_otm(p, side) if p else None,
                            "价外一档;下单前在券商确认价差和权利金 ≤ $4.00" if p else "取不到价格,手动选价外一档"))
    return rep


def render(rep: WarReport) -> str:
    if rep.consensus is None:
        head = f"实际 {rep.actual}"
    else:
        head = f"实际 {rep.actual} vs 预期 {rep.consensus}({rep.consensus_source}),偏离 {rep.surprise:+}"
    lines = [f"## 战报 · {rep.event}({rep.tier} 级)", head,
             f"方向:{ {'risk_off': '偏空(买 put)', 'risk_on': '偏多(买 call)', 'none': '不做'}[rep.direction]}"]
    for leg in rep.legs:
        px = f"${leg.ref_price:.2f}" if leg.ref_price else "?"
        k = f"${leg.strike:g}" if leg.strike else "?"
        lines.append(f"- {leg.symbol} {leg.side} strike {k}(参考价 {px})· {leg.note}")
    lines += ["", "**铁律**"] + [f"- {r}" for r in rep.rules]
    lines += [f"⚠️ {w}" for w in rep.warnings]
    lines.append("\n仅为规则化信号,不是投资建议;只出信号,不下单。")
    return "\n".join(lines)
