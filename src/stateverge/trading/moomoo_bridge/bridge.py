"""Run one day's plan: checks → entry (limit orders) → exit management → journal."""

from __future__ import annotations

import json
import math
import time as _time
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from ..eventdesk import confirm, journal
from . import exits
from .plan import DAILY_LOSS_STOP, MAX_PER_CONTRACT, MAX_SPREAD_PCT, BUDGET, Plan, problems, spread_pct

ET = ZoneInfo("America/New_York")
FILL_WAIT_S = 90
POLL_S = 15
CLOSE_ET = "16:00"


class Bridge:
    def __init__(self, plan: Plan, broker, journal_path: Path, *, now: Callable[[], datetime] | None = None,
                 sleep: Callable[[float], None] = _time.sleep, confirm_quote=None,
                 log: Callable[[str], None] = print) -> None:
        self.plan, self.b, self.journal_path = plan, broker, Path(journal_path)
        self.now = now or (lambda: datetime.now(ET))
        self.sleep, self.log = sleep, log
        self.confirm_quote = confirm_quote
        self.positions: list[tuple[exits.Position, dict]] = []
        self.state_path = self.journal_path.parent / "open_positions.json"

    # --- overnight state ----------------------------------------------------
    def _open_state(self) -> list[dict]:
        p = self.state_path
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() and p.read_text().strip() else []

    def _save_state(self) -> None:
        mine = [dict(pos.__dict__, meta=meta, plan=self.plan_dict) for pos, meta in self.positions if pos.qty > 0]
        others = [r for r in self._open_state() if r.get("plan") != self.plan_dict]
        rows = others + mine
        if rows:
            self.state_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif self.state_path.exists():
            self.state_path.unlink()

    @property
    def plan_dict(self) -> dict:
        return {"date": self.plan.date, "event": self.plan.event}

    # --- guards -----------------------------------------------------------
    def _journal(self) -> list[dict]:
        p = self.journal_path
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() and p.read_text().strip() else []

    def preflight(self) -> list[str]:
        out = problems(self.plan)
        today = [t for t in self._journal() if t.get("date") == self.plan.date]
        if sum(journal.pnl(t) for t in today if t.get("exit") is not None) <= DAILY_LOSS_STOP:
            out.append(f"今日已亏损达 ${-DAILY_LOSS_STOP:.0f},停手")
        done = {(t.get("symbol"), t.get("opened_at")) for t in today if t.get("event") == self.plan.event}
        if len(done) + len(self.plan.legs) > 2:
            out.append(f"这个事件今天已做 {len(done)} 笔,再加 {len(self.plan.legs)} 笔会超过 2 笔")
        held = self._open_state()
        if any(r["meta"]["symbol"] == leg.symbol and r["meta"]["side"] == leg.side
               for r in held for leg in self.plan.legs):
            out.append("已持有同一标的同方向的仓位,再买 = 加仓摊平")
        if self.open_cost() >= BUDGET:
            out.append(f"隔夜持仓成本 ${self.open_cost():.0f} 已用完 ${BUDGET:.0f} 预算")
        if self.now().date().isoformat() != self.plan.date:
            out.append(f"计划日期 {self.plan.date} 不是今天(美东 {self.now().date()})")
        return out

    def open_cost(self) -> float:
        return sum(r["entry"] * 100 * r["qty"] for r in self._open_state())

    def _at(self, hhmm: str, day: date | None = None) -> datetime:
        return datetime.combine(day or self.now().date(), time.fromisoformat(hhmm), ET)

    # --- entry ------------------------------------------------------------
    def wait_for_window(self) -> bool:
        start, end = (self._at(t) for t in self.plan.entry_window_et)
        while self.now() < start:
            self.sleep(min(60.0, (start - self.now()).total_seconds()))
        if self.now() > end:
            self.log(f"已过入场时间窗口 {self.plan.entry_window_et[1]} ET,今天不入场")
            return False
        return True

    def enter(self) -> None:
        budget = BUDGET - self.open_cost()
        for leg in self.plan.legs:
            tag = f"{leg.symbol} {leg.expiry} {leg.strike:g}{leg.side[0].upper()}"
            if leg.underlying_min is not None or leg.underlying_max is not None:
                px = self.b.quote(f"US.{leg.symbol}")[2]
                lo, hi = leg.underlying_min or 0, leg.underlying_max or float("inf")
                if not lo <= px <= hi:
                    self.log(f"{tag}: 正股 ${px} 不在入场区 {lo:g}–{hi:g},不入场")
                    continue
            code = self.b.option_code(leg.symbol, leg.side, leg.strike, leg.expiry)
            if not code:
                self.log(f"{tag}: 找不到合约,跳过")
                continue
            bid, ask, _ = self.b.quote(code)
            sp = spread_pct(bid, ask)
            if sp > MAX_SPREAD_PCT:
                self.log(f"{tag}: 买卖价差 {sp:.1f}% > {MAX_SPREAD_PCT:.0f}%,跳过")
                continue
            price = round(min(ask, leg.max_price), 2)
            if price * 100 > MAX_PER_CONTRACT:
                self.log(f"{tag}: ${price} 超单张上限,跳过")
                continue
            qty = min(leg.qty, math.floor(budget / (price * 100)))
            if qty < 1:
                self.log(f"{tag}: 余下预算 ${budget:.0f} 不够一张,跳过")
                continue
            f = self._wait_fill(self.b.buy(code, qty, price))
            if f.qty < 1:
                self.log(f"{tag}: 限价 ${price} 未成交,已撤单")
                continue
            budget -= f.price * 100 * f.qty
            self.log(f"{tag}: 买入 {f.qty} 张 @ ${f.price}({self.b.name})")
            meta = {"symbol": leg.symbol, "side": leg.side, "strike": leg.strike, "expiry": leg.expiry,
                    "opened_at": self.now().isoformat(timespec="seconds")}
            self.positions.append((exits.Position(code, f.qty, f.price, f.price), meta))

    def _wait_fill(self, order_id: str):
        waited = 0.0
        while True:
            f = self.b.fill(order_id)
            if f.status in ("filled", "cancelled", "failed") or waited >= FILL_WAIT_S:
                break
            self.sleep(5)
            waited += 5
        if f.status not in ("filled", "cancelled", "failed"):
            self.b.cancel(order_id)
            f = self.b.fill(order_id)
        return f

    # --- exits ------------------------------------------------------------
    def manage(self) -> None:
        last_day = date.fromisoformat(self.plan.exit.hold_until or self.plan.date)
        flat_by = self._at(self.plan.exit.flat_by_et, last_day)
        while any(p.qty > 0 for p, _ in self.positions):
            if self.now().date() < last_day and self.now() >= self._at(CLOSE_ET):
                self._save_state()
                self.log(f"收市,持仓过夜(最迟 {last_day} {self.plan.exit.flat_by_et} ET 清仓)。明早用 --resume 继续")
                return
            past = self.now() >= flat_by
            for pos, meta in self.positions:
                if pos.qty <= 0:
                    continue
                bid, _, _ = self.b.quote(pos.code)
                n, why = exits.decide(pos, bid, self.plan.exit, past)
                if n <= 0:
                    continue
                f = self._wait_fill(self.b.sell(pos.code, n, round(bid, 2)))
                if f.qty < 1:
                    self.log(f"{pos.code}: 卖出未成交,下轮再试({why})")
                    continue
                pos.qty -= f.qty
                if not pos.half_taken and "平一半" in why:
                    pos.half_taken = True
                self.log(f"{pos.code}: 卖出 {f.qty} 张 @ ${f.price} — {why}")
                self._record(meta, pos, f.qty, f.price, why)
                self._save_state()
            if any(p.qty > 0 for p, _ in self.positions):
                self.sleep(POLL_S)

    def _record(self, meta: dict, pos: exits.Position, qty: int, price: float, why: str) -> None:
        rows = self._journal()
        rows.append(meta | {"date": self.plan.date, "event": self.plan.event, "qty": qty, "entry": pos.entry,
                            "exit": price, "closed_at": self.now().isoformat(timespec="seconds"),
                            "paper": True, "mode": self.b.name, "reason": why, "thesis": self.plan.thesis})
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # --- whole day --------------------------------------------------------
    def run(self) -> int:
        bad = self.preflight()
        if bad:
            for x in bad:
                self.log("⛔ " + x)
            return 2
        if not self.wait_for_window():
            return 0
        if self.plan.require_confirm:
            res = confirm.check(self.plan.direction, [leg.symbol for leg in self.plan.legs],
                                **({"quote": self.confirm_quote} if self.confirm_quote else {}))
            if not res["ok"]:
                self.log("⛔ 开盘确认不通过:" + ";".join(res["reasons"]))
                return 0
            self.log(f"✅ 开盘确认通过 {res['movers']}")
        self.enter()
        self._save_state()
        self.manage()
        self.log("今日计划完成")
        return 0

    def resume(self) -> int:
        """Manage positions carried overnight for this plan (no new entries)."""
        rows = [r for r in self._open_state() if r.get("plan") == self.plan_dict]
        if not rows:
            self.log("没有这个计划的隔夜持仓")
            return 0
        for r in rows:
            self.positions.append((exits.Position(r["code"], r["qty"], r["entry"], r["peak"], r["half_taken"]),
                                   r["meta"]))
        while self.now() < self._at("09:30"):
            self.sleep(min(60.0, (self._at("09:30") - self.now()).total_seconds()))
        self.manage()
        return 0
