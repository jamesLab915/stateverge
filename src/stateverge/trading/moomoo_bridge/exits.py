"""Exit rules as a pure function so they can be tested without a broker.

+take_profit_pct → sell all (有盈利就跑); −stop → sell all; flat_by_et / max_hold_min → sell all.
"""

from __future__ import annotations

from dataclasses import dataclass

from .plan import Exit


@dataclass
class Position:
    code: str
    qty: int
    entry: float
    peak: float


def decide(pos: Position, bid: float, rule: Exit, force: str = "", stop_pct: float = 30.0) -> tuple[int, str]:
    """(contracts to sell now, reason). Updates pos.peak. `force` = time-stop reason, sells all."""
    if pos.qty <= 0:
        return 0, ""
    pos.peak = max(pos.peak, bid)
    gain = (bid / pos.entry - 1) * 100
    if force:
        return pos.qty, force
    if gain <= -stop_pct:
        return pos.qty, f"止损 {gain:.0f}%"
    if gain >= rule.take_profit_pct:
        return pos.qty, f"止盈 +{gain:.0f}% 全走"
    return 0, ""
