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
  "exit": {"take_profit_pct": 30, "flat_by_et": "15:00"}
}

strike: a number, or "atm" / "otm1" / "otm2" (picked from the live chain at entry).
expiry: "YYYY-MM-DD", "0dte" (today; index ETFs only) or "weekly" (nearest Friday).
take_profit_pct: +30..50 → sell everything ("有盈利就跑,唔贪心").
stop_loss_pct (optional): default −30%, or −25% when the contract expires this week; may be
tightened to −20%, never loosened.
max_hold_min (optional, off by default): sell everything that many minutes after entry.

underlying_min/max (optional): only enter while the stock trades inside that range.
hold_until (optional, default = plan date): positions carry overnight until that day's
flat_by_et; the bridge saves them at 16:00 ET and `--resume` picks them up next morning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta

BUDGET = 900.0  # whole account as of 2026-10-19
MAX_PER_CONTRACT = 400.0
MAX_LEGS = 2
MAX_SPREAD_PCT = 10.0
TAKE_PROFIT_RANGE = (30.0, 50.0)
STOP_DEFAULT = 30.0
STOP_THIS_WEEK = 25.0
STOP_FLOOR = 20.0
DAILY_LOSS_STOP = -BUDGET  # TODO: owner to set a real daily stop


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
class Trail:
    """Opt-in "let winners run": at +activate_pct don't sell; instead exit when the option
    falls to max(entry +lock_pct, peak −giveback_pct) or the stock reverses underlying_reversal_pct
    from its best level since activation."""
    activate_pct: float = 30.0
    lock_pct: float = 20.0
    giveback_pct: float = 20.0
    underlying_reversal_pct: float = 0.3


@dataclass
class Exit:
    take_profit_pct: float = 30.0
    stop_loss_pct: float | None = None  # None → 30, or 25 for this-week expiry
    flat_by_et: str = "15:00"
    hold_until: str | None = None
    max_hold_min: float | None = None
    trail: Trail | None = None

    def __post_init__(self) -> None:
        if isinstance(self.trail, dict):
            self.trail = Trail(**self.trail)


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


def expires_this_week(expiry: str, day: str) -> bool:
    if expiry in EXPIRY_WORDS:
        return True
    try:
        d, e = date.fromisoformat(day), date.fromisoformat(expiry)
    except ValueError:
        return False
    return e <= d + timedelta(days=(4 - d.weekday()) % 7)


def stop_cap(expiry: str, day: str) -> float:
    return STOP_THIS_WEEK if expires_this_week(expiry, day) else STOP_DEFAULT


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
    if not TAKE_PROFIT_RANGE[0] <= p.exit.take_profit_pct <= TAKE_PROFIT_RANGE[1]:
        out.append(f"止盈要在 +{TAKE_PROFIT_RANGE[0]:g}%~+{TAKE_PROFIT_RANGE[1]:g}% 之间,全走")
    t = p.exit.trail
    if t:
        if not TAKE_PROFIT_RANGE[0] <= t.activate_pct <= TAKE_PROFIT_RANGE[1]:
            out.append(f"trail 启动点要在 +{TAKE_PROFIT_RANGE[0]:g}%~+{TAKE_PROFIT_RANGE[1]:g}%")
        if not 10 <= t.lock_pct < t.activate_pct:
            out.append("trail 锁利底线要 ≥ +10% 且低于启动点")
        if not 10 <= t.giveback_pct <= 30:
            out.append("trail 由高位回吐要在 10%~30%")
        if not 0.1 <= t.underlying_reversal_pct <= 2:
            out.append("trail 正股反转阈值要在 0.1%~2%")
    for leg in p.legs:
        cap = stop_cap(leg.expiry, p.date)
        if p.exit.stop_loss_pct is not None and not STOP_FLOOR <= p.exit.stop_loss_pct <= cap:
            out.append(f"{leg.symbol}: 止损要在 −{STOP_FLOOR:g}%~−{cap:g}% 之间(现在 −{p.exit.stop_loss_pct:g}%)")
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
