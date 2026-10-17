"""Exit rules as a pure function so they can be tested without a broker.

+take_half_at_pct → sell half; the rest exits when it gives back trail_giveback_pct
from its peak; −stop_loss_pct → sell all; flat_by_et → sell all.
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
    half_taken: bool = False


def decide(pos: Position, bid: float, rule: Exit, past_flat_time: bool) -> tuple[int, str]:
    """(contracts to sell now, reason). Updates pos.peak."""
    if pos.qty <= 0:
        return 0, ""
    pos.peak = max(pos.peak, bid)
    gain = (bid / pos.entry - 1) * 100
    if past_flat_time:
        return pos.qty, "时间止损(收市前清仓)"
    if gain <= -rule.stop_loss_pct:
        return pos.qty, f"止损 {gain:.0f}%"
    if not pos.half_taken and gain >= rule.take_half_at_pct:
        n = max(1, pos.qty // 2)
        return n, f"+{gain:.0f}% 平一半"
    if pos.half_taken and pos.peak > 0 and (1 - bid / pos.peak) * 100 >= rule.trail_giveback_pct:
        return pos.qty, f"由高位回吐 {(1 - bid / pos.peak) * 100:.0f}%"
    return 0, ""
