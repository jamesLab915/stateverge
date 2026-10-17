"""Broker adapters. MoomooPaper talks to OpenD with TrdEnv.SIMULATE hard-coded;
DryRun uses real quotes but only pretends to fill. No real-money adapter exists."""

from __future__ import annotations

import itertools
from dataclasses import dataclass


@dataclass
class Fill:
    status: str  # "filled" | "partial" | "open" | "cancelled" | "failed"
    qty: int
    price: float


class MoomooPaper:
    """Needs `pip install moomoo-api` and Moomoo OpenD running and logged in."""

    name = "paper"

    def __init__(self, host: str = "127.0.0.1", port: int = 11111) -> None:
        import moomoo as mm  # local machine only

        self.mm = mm
        self.env = mm.TrdEnv.SIMULATE  # never REAL
        self.q = mm.OpenQuoteContext(host=host, port=port)
        self.t = mm.OpenSecTradeContext(filter_trdmarket=mm.TrdMarket.US, host=host, port=port,
                                        security_firm=mm.SecurityFirm.FUTUINC)

    def close(self) -> None:
        self.q.close()
        self.t.close()

    def _ok(self, res):
        ret, data = res
        if ret != self.mm.RET_OK:
            raise RuntimeError(str(data))
        return data

    def option_code(self, symbol: str, side: str, strike: float, expiry: str) -> str | None:
        kind = self.mm.OptionType.PUT if side == "put" else self.mm.OptionType.CALL
        df = self._ok(self.q.get_option_chain(code=f"US.{symbol}", start=expiry, end=expiry, option_type=kind))
        hit = df[(abs(df["strike_price"] - strike) < 1e-6)]
        return None if hit.empty else str(hit.iloc[0]["code"])

    def quote(self, code: str) -> tuple[float, float, float]:
        r = self._ok(self.q.get_market_snapshot([code])).iloc[0]
        return float(r["bid_price"]), float(r["ask_price"]), float(r["last_price"])

    def _order(self, side, code: str, qty: int, price: float) -> str:
        df = self._ok(self.t.place_order(price=price, qty=qty, code=code, trd_side=side,
                                         order_type=self.mm.OrderType.NORMAL, trd_env=self.env))
        return str(df.iloc[0]["order_id"])

    def buy(self, code: str, qty: int, price: float) -> str:
        return self._order(self.mm.TrdSide.BUY, code, qty, price)

    def sell(self, code: str, qty: int, price: float) -> str:
        return self._order(self.mm.TrdSide.SELL, code, qty, price)

    def fill(self, order_id: str) -> Fill:
        r = self._ok(self.t.order_list_query(order_id=order_id, trd_env=self.env)).iloc[0]
        st = str(r["order_status"])
        status = ("filled" if st == "FILLED_ALL" else "partial" if st == "FILLED_PART"
                  else "cancelled" if st.startswith("CANCELLED") else "failed" if "FAIL" in st else "open")
        return Fill(status, int(r["dealt_qty"]), float(r["dealt_avg_price"] or 0))

    def cancel(self, order_id: str) -> None:
        self._ok(self.t.modify_order(self.mm.ModifyOrderOp.CANCEL, order_id, 0, 0, trd_env=self.env))


class DryRun:
    """Real quotes from `quotes` (or fixed table), fills at the limit, no order sent."""

    name = "dry"

    def __init__(self, quotes=None) -> None:
        self.quotes = quotes
        self.orders: dict[str, tuple[int, float]] = {}
        self._ids = itertools.count(1)

    def close(self) -> None:
        if hasattr(self.quotes, "close"):
            self.quotes.close()

    def option_code(self, symbol, side, strike, expiry):
        return self.quotes.option_code(symbol, side, strike, expiry)

    def quote(self, code):
        return self.quotes.quote(code)

    def _order(self, qty, price):
        oid = f"dry-{next(self._ids)}"
        self.orders[oid] = (qty, price)
        return oid

    def buy(self, code, qty, price):
        return self._order(qty, price)

    def sell(self, code, qty, price):
        return self._order(qty, price)

    def fill(self, order_id):
        qty, price = self.orders[order_id]
        return Fill("filled", qty, price)

    def cancel(self, order_id):
        self.orders.pop(order_id, None)
