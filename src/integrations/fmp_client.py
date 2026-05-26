"""
Financial Modeling Prep (FMP) API client for StateVerge.

Uses the **stable** API base (legacy ``/api/v3`` path URLs are not available for new keys).

Reads from environment (after loading ``.env`` via ``load_env_from_dotenv_file``):

    FMP_API_KEY      — required for authenticated calls
    FMP_API_BASE     — optional; default ``https://financialmodelingprep.com/stable``

Docs: https://site.financialmodelingprep.com/developer/docs/

Run a quick smoke test (from repo root, ``PYTHONPATH`` = project root)::

    python -m src.integrations.fmp_client --profile AAPL
    python -m src.integrations.fmp_client --quote AAPL
    python -m src.integrations.fmp_client --income AAPL
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any
from urllib.parse import urlencode

import requests

from .media_sources import load_env_from_dotenv_file

_DEFAULT_BASE = "https://financialmodelingprep.com/stable"
_LOG = "[fmp]"

_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-^]+$")


class FMPError(RuntimeError):
    """HTTP or API-level failure (non-2xx, empty key, invalid JSON)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body_snippet: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body_snippet = body_snippet


def _normalize_base(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    return u or _DEFAULT_BASE


def _require_key() -> str:
    k = (os.environ.get("FMP_API_KEY") or "").strip()
    if not k:
        raise FMPError(
            "FMP_API_KEY is not set (add it to .env; see .env.example)",
        )
    return k


def normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s or not _SYMBOL_RE.match(s):
        raise FMPError(f"invalid ticker symbol: {symbol!r}")
    return s


def _as_list_of_dicts(data: Any, label: str) -> list[dict[str, Any]]:
    """
    Stable endpoints may return one object or a list; downstream expects a list.
    """
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        out: list[dict[str, Any]] = []
        for x in data:
            if isinstance(x, dict):
                out.append(x)
        return out
    raise FMPError(
        f"unexpected {label} shape: {type(data).__name__}",
    )


class FMPClient:
    """
    Thin wrapper around FMP **stable** REST endpoints (query ``symbol=`` + ``apikey=``).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self._key = (api_key or "").strip() or None
        self._base = _normalize_base(base_url or os.environ.get("FMP_API_BASE") or "")
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.setdefault(
            "User-Agent",
            "StateVerge-fmp-client/1.0 (+https://github.com)",
        )

    @property
    def base_url(self) -> str:
        return self._base

    def _get(self, endpoint: str, **query: Any) -> Any:
        """
        GET ``{base}/{endpoint}?apikey=...&...query``.

        *endpoint* is the stable path segment (e.g. ``profile``, ``income-statement``).
        """
        key = self._key or _require_key()
        ep = (endpoint or "").strip().lstrip("/")
        if not ep:
            raise FMPError("empty API endpoint")

        q: dict[str, Any] = {"apikey": key}
        for k, v in query.items():
            if v is None:
                continue
            q[k] = v

        url = f"{self._base}/{ep}?{urlencode(q, doseq=True)}"

        try:
            r = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as e:
            raise FMPError(f"request failed: {e}") from e

        snippet = (r.text or "")[:400]
        if r.status_code != 200:
            raise FMPError(
                f"FMP HTTP {r.status_code} for {ep}",
                status_code=r.status_code,
                body_snippet=snippet,
            )

        try:
            return r.json()
        except json.JSONDecodeError as e:
            raise FMPError(
                f"invalid JSON from FMP: {e}",
                status_code=r.status_code,
                body_snippet=snippet,
            ) from e

    # --- stable endpoints (query param symbol) ---

    def profile(self, symbol: str) -> list[dict[str, Any]]:
        sym = normalize_symbol(symbol)
        data = self._get("profile", symbol=sym)
        return _as_list_of_dicts(data, "profile")

    def quote(self, symbol: str) -> list[dict[str, Any]]:
        sym = normalize_symbol(symbol)
        data = self._get("quote", symbol=sym)
        return _as_list_of_dicts(data, "quote")

    def income_statement(
        self,
        symbol: str,
        *,
        period: str = "annual",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        sym = normalize_symbol(symbol)
        data = self._get(
            "income-statement",
            symbol=sym,
            period=period,
            limit=int(limit),
        )
        return _as_list_of_dicts(data, "income-statement")

    def balance_sheet_statement(
        self,
        symbol: str,
        *,
        period: str = "annual",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        sym = normalize_symbol(symbol)
        data = self._get(
            "balance-sheet-statement",
            symbol=sym,
            period=period,
            limit=int(limit),
        )
        return _as_list_of_dicts(data, "balance-sheet-statement")

    def key_metrics_ttm(self, symbol: str) -> list[dict[str, Any]]:
        sym = normalize_symbol(symbol)
        data = self._get("key-metrics-ttm", symbol=sym)
        return _as_list_of_dicts(data, "key-metrics-ttm")


def main(argv: list[str] | None = None) -> int:
    load_env_from_dotenv_file()
    ap = argparse.ArgumentParser(description="FMP stable API smoke test")
    ap.add_argument("--profile", metavar="SYM", help="GET /profile?symbol=SYM")
    ap.add_argument(
        "--quote",
        nargs="+",
        metavar="SYM",
        help="GET /quote?symbol=SYM (one or more tickers)",
    )
    ap.add_argument(
        "--income",
        metavar="SYM",
        help="GET /income-statement?symbol=SYM&period=annual&limit=3",
    )
    args = ap.parse_args(argv)

    if not (args.profile or args.quote or args.income):
        ap.print_help()
        return 1

    try:
        c = FMPClient()
    except FMPError as e:
        print(f"{_LOG} error: {e}", file=sys.stderr)
        return 2

    try:
        if args.profile:
            rows = c.profile(args.profile)
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        if args.quote:
            for sym in args.quote:
                rows = c.quote(sym)
                print(f"=== quote {sym} ===")
                print(json.dumps(rows, indent=2, ensure_ascii=False))
        if args.income:
            rows = c.income_statement(args.income, limit=3)
            print(json.dumps(rows, indent=2, ensure_ascii=False))
    except FMPError as e:
        print(f"{_LOG} error: {e}", file=sys.stderr)
        if e.body_snippet:
            print(e.body_snippet, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
