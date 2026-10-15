"""Trade journal (paper or real) and the numbers that decide phase 2.

data/trading/journal.json entries (one per closed trade):
    {"date": "2026-10-15", "event": "PPI", "symbol": "RKLB", "side": "put", "strike": 52,
     "expiry": "2026-10-16", "qty": 3, "entry": 1.20, "exit": 2.40, "underlying_entry": 59.3,
     "underlying_exit": 51.0, "paper": true, "reason": "..."}
"""

from __future__ import annotations

PHASE2_MIN_TRADES = 30
PHASE2_MIN_WIN = 0.60
PHASE2_MIN_PAYOFF = 1.5


def pnl(t: dict) -> float:
    return round((float(t["exit"]) - float(t["entry"])) * 100 * int(t.get("qty", 1)), 2)


def stats(trades: list[dict]) -> dict:
    results = [pnl(t) for t in trades if t.get("exit") is not None]
    wins = [r for r in results if r > 0]
    losses = [-r for r in results if r <= 0]
    n = len(results)
    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    payoff = avg_win / avg_loss if avg_loss else (float("inf") if wins else 0.0)
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in results:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    ready = n >= PHASE2_MIN_TRADES and win_rate > PHASE2_MIN_WIN and payoff > PHASE2_MIN_PAYOFF
    return {"trades": n, "win_rate": round(win_rate, 3), "payoff": round(payoff, 2) if payoff != float("inf") else None,
            "total_pnl": round(sum(results), 2), "max_drawdown": round(dd, 2),
            "phase2_ready": ready,
            "phase2_note": (f"样本 {n}/{PHASE2_MIN_TRADES} 笔" if n < PHASE2_MIN_TRADES else
                            "达标" if ready else "未达标(胜率需 >60%、盈亏比需 >1.5)")}
