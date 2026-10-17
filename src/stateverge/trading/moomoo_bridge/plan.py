"""Daily plan: what the owner wants traded today, checked against the hard rules.

{
  "date": "2026-10-19",
  "event": "CPI",
  "thesis": "CPI 高于预期 → 科技股承压",
  "require_confirm": true,
  "entry_window_et": ["09:30", "10:00"],
  "legs": [
    {"symbol": "QQQ", "side": "put", "strike": "otm1", "expiry": "0dte", "qty": 2, "max_price": 2.00,
     "underlying_max": 730}
  ],
  "exit": {"take_half_at_pct": 100, "trail_giveback_pct": 40, "stop_loss_pct": 50, "flat_by_et": "15:00",
           "max_hold_min": 45}
}

strike: a number, or "atm" / "otm1" / "otm2" (picked from the live chain at entry).
expiry: "YYYY-MM-DD", "0dte" (today; index ETFs only) or "weekly" (nearest Friday).
max_hold_min (optional): sell everything that many minutes after entry, whatever the P&L.

underlying_min/max (optional): only enter while the stock trades inside that range.
hold_until (optional, default = plan date): positions carry overnight until that day's
flat_by_et; the bridge saves them at 16:00 ET and `--resume` picks them up next morning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

BUDGET = 1000.0
MAX_PER_CONTRACT = 400.0
MAX_LEGS = 2
MAX_SPREAD_PCT = 10.0
DAILY_LOSS_STOP = -1000.0


@dataclass
class Leg:
    symbol: str
    side: str
    strike: float | str
    expiry: str
    qty: int
    max_price: float
    underlying_min: float | None = None
    underlying_max: float | None = None


@dataclass
class Exit:
    take_half_at_pct: float = 100.0
    trail_giveback_pct: float = 40.0
    stop_loss_pct: float = 50.0
    flat_by_et: str = "15:00"
    hold_until: str | None = None
    max_hold_min: float | None = None


@dataclass
class Plan:
    date: str
    event: str
    thesis: str
    legs: list[Leg]
    require_confirm: bool = True
    entry_window_et: tuple[str, str] = ("09:30", "10:00")
    exit: Exit = field(default_factory=Exit)

    @property
    def direction(self) -> str:
        sides = {leg.side for leg in self.legs}
        return "risk_off" if sides == {"put"} else "risk_on" if sides == {"call"} else "mixed"

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(date=d["date"], event=d.get("event", ""), thesis=d.get("thesis", ""),
                   legs=[Leg(symbol=str(x["symbol"]).upper(), side=x["side"], strike=_strike(x["strike"]),
                             expiry=x["expiry"], qty=int(x["qty"]), max_price=float(x["max_price"]),
                             underlying_min=x.get("underlying_min"), underlying_max=x.get("underlying_max"))
                         for x in d.get("legs", [])],
                   require_confirm=bool(d.get("require_confirm", True)),
                   entry_window_et=tuple(d.get("entry_window_et", ("09:30", "10:00"))),
                   exit=Exit(**d.get("exit", {})))


STRIKE_WORDS = ("atm", "otm1", "otm2")
EXPIRY_WORDS = ("0dte", "weekly")


def _strike(v) -> float | str:
    return v.lower() if isinstance(v, str) and not v.replace(".", "", 1).isdigit() else float(v)


def problems(p: Plan) -> list[str]:
    """Static rule check. Empty list = plan may run."""
    out = []
    if not p.legs:
        out.append("没有任何一笔")
    if len(p.legs) > MAX_LEGS:
        out.append(f"每个事件最多 {MAX_LEGS} 笔(现在 {len(p.legs)})")
    seen = [(leg.symbol, leg.side) for leg in p.legs]
    if len(set(seen)) < len(seen):
        out.append("同一标的同方向买两次 = 加仓摊平,不允许")
    if p.direction == "mixed":
        out.append("同一事件同时买 put 和 call,违反「看错不反手」")
    total = 0.0
    for leg in p.legs:
        tag = f"{leg.symbol} {leg.side} {leg.strike}"
        if isinstance(leg.strike, str) and leg.strike not in STRIKE_WORDS:
            out.append(f"{tag}: strike 只能是数字或 {'/'.join(STRIKE_WORDS)}")
        dated = leg.expiry not in EXPIRY_WORDS
        if leg.side not in ("put", "call"):
            out.append(f"{tag}: side 只能是 put/call")
        if leg.qty < 1:
            out.append(f"{tag}: 张数要 ≥1")
        if leg.max_price * 100 > MAX_PER_CONTRACT:
            out.append(f"{tag}: 限价 ${leg.max_price} ×100 超过单张 ${MAX_PER_CONTRACT:.0f}")
        if dated and leg.expiry < p.date:
            out.append(f"{tag}: 到期日 {leg.expiry} 早于交易日")
        if leg.expiry == "0dte" and p.exit.hold_until and p.exit.hold_until > p.date:
            out.append(f"{tag}: 0DTE 不能持仓过夜")
        if dated and p.exit.hold_until and leg.expiry < p.exit.hold_until:
            out.append(f"{tag}: 到期日 {leg.expiry} 早于 hold_until {p.exit.hold_until}")
        total += leg.max_price * 100 * max(leg.qty, 0)
    if total > BUDGET:
        out.append(f"最坏成本 ${total:.0f} 超过总仓位 ${BUDGET:.0f}")
    if p.exit.stop_loss_pct > 50:
        out.append("止损不能宽过 -50%")
    try:
        date.fromisoformat(p.date)
        a, b = (time.fromisoformat(t) for t in p.entry_window_et)
        if not a < b:
            out.append("入场时间窗口无效")
        time.fromisoformat(p.exit.flat_by_et)
        if p.exit.hold_until and p.exit.hold_until < p.date:
            out.append("hold_until 早于交易日")
    except ValueError as e:
        out.append(f"日期/时间格式错: {e}")
    return out


def spread_pct(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 100 if mid > 0 else float("inf")
