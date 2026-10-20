"""Exit rules as a pure function so they can be tested without a broker.

+take_profit_pct → sell all (有盈利就跑); −stop → sell all; flat_by_et / max_hold_min → sell all.
With exit.trail set, +activate_pct starts trailing instead of selling (see plan.Trail).
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
    trailing: bool = False
    u_best: float | None = None  # best underlying price since trailing began


def decide(pos: Position, bid: float, rule: Exit, force: str = "", stop_pct: float = 30.0,
           side: str = "", underlying: float | None = None) -> tuple[int, str]:
    """(contracts to sell now, reason). Updates pos.peak. `force` = time-stop reason, sells all."""
    if pos.qty <= 0:
        return 0, ""
    pos.peak = max(pos.peak, bid)
    gain = (bid / pos.entry - 1) * 100
    if force:
        return pos.qty, force
    if gain <= -stop_pct:
        return pos.qty, f"止损 {gain:.0f}%"
    t = rule.trail
    if t is None:
        if gain >= rule.take_profit_pct:
            return pos.qty, f"止盈 +{gain:.0f}% 全走"
        return 0, ""
    if not pos.trailing:
        if gain < t.activate_pct:
            return 0, ""
        pos.trailing, pos.u_best = True, underlying
    floor = max(pos.entry * (1 + t.lock_pct / 100), pos.peak * (1 - t.giveback_pct / 100))
    if bid <= floor:
        return pos.qty, f"跟踪出场 +{gain:.0f}%(高位 +{(pos.peak / pos.entry - 1) * 100:.0f}%)"
    if underlying is not None and side in ("put", "call"):
        fav = -1 if side == "put" else 1
        if pos.u_best is None or (underlying - pos.u_best) * fav > 0:
            pos.u_best = underlying
        rev = (pos.u_best - underlying) * fav / pos.u_best * 100
        if rev >= t.underlying_reversal_pct:
            return pos.qty, f"正股势头反转 {rev:.2f}%,+{gain:.0f}% 出场"
    return 0, ""
