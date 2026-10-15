"""Open confirmation: all three must agree before a war report becomes "可入场"."""

from __future__ import annotations

from .market import change_pct

FUTURES = "NQ=F"
VIX = "^VIX"
MIN_PREMARKET_MOVE = 2.0


def check(direction: str, pool: list[str], quote=change_pct) -> dict:
    if direction not in ("risk_off", "risk_on"):
        return {"ok": False, "reasons": ["战报没有方向"]}
    want = -1 if direction == "risk_off" else 1
    reasons, ok = [], True
    fut = quote(FUTURES)
    if not fut or fut[1] * want <= 0:
        ok = False
        reasons.append(f"纳指期货方向不一致({fut[1]:+.2f}%)" if fut else "取不到纳指期货")
    vix = quote(VIX)
    if not vix or vix[1] * -want <= 0:
        ok = False
        reasons.append(f"VIX 方向不一致({vix[1]:+.2f}%)" if vix else "取不到 VIX")
    movers = {}
    for sym in pool:
        q = quote(sym)
        if q and q[1] * want >= MIN_PREMARKET_MOVE:
            movers[sym] = round(q[1], 2)
    if not movers:
        ok = False
        reasons.append(f"标的盘前没有 ≥{MIN_PREMARKET_MOVE}% 的同向波动")
    return {"ok": ok, "reasons": reasons, "movers": movers,
            "futures_pct": round(fut[1], 2) if fut else None, "vix_pct": round(vix[1], 2) if vix else None}
