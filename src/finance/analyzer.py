"""
Fetch FMP data, compute YoY / margins, build analysis.json fields, and render
a ~60s Chinese narration (template-only, no LLM).

All numeric extraction is defensive: FMP may return numbers as int/float/str.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.integrations.fmp_client import FMPClient, normalize_symbol


def _num(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        if x != x:  # NaN
            return None
        return float(x)
    try:
        return float(str(x).replace(",", "").strip())
    except ValueError:
        return None


def _sort_income_annual(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def year_key(r: dict[str, Any]) -> int:
        cy = r.get("calendarYear")
        if cy is not None:
            try:
                return int(cy)
            except (TypeError, ValueError):
                pass
        d = r.get("date") or ""
        if isinstance(d, str) and len(d) >= 4:
            try:
                return int(d[:4])
            except ValueError:
                pass
        return 0

    return sorted(rows, key=year_key, reverse=True)


def fetch_fmp_bundle(client: FMPClient, symbol: str) -> dict[str, Any]:
    """Call profile / quote / income_statement / key_metrics_ttm; return a dict for data.json."""
    sym = normalize_symbol(symbol)
    profile = client.profile(sym)
    quote = client.quote(sym)
    income = client.income_statement(sym, period="annual", limit=5)
    km = client.key_metrics_ttm(sym)
    return {
        "symbol": sym,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "financialmodelingprep.com",
        "profile": profile,
        "quote": quote,
        "income_statement_annual": income,
        "key_metrics_ttm": km,
    }


def compute_metrics(data: dict[str, Any]) -> dict[str, Any]:
    """
    Derive revenue_yoy, net_income_yoy, gross_margin, net_margin, market_cap
    from the raw bundle. Values may be null if data is missing.
    """
    income = data.get("income_statement_annual") or []
    if not isinstance(income, list):
        income = []
    income = [r for r in income if isinstance(r, dict)]
    ranked = _sort_income_annual(income)

    rev0 = rev1 = ni0 = ni1 = gp0 = None
    if len(ranked) >= 1:
        r0 = ranked[0]
        rev0 = _num(r0.get("revenue"))
        ni0 = _num(r0.get("netIncome"))
        gp0 = _num(r0.get("grossProfit"))
    if len(ranked) >= 2:
        r1 = ranked[1]
        rev1 = _num(r1.get("revenue"))
        ni1 = _num(r1.get("netIncome"))

    revenue_yoy = None
    if rev0 is not None and rev1 is not None and rev1 != 0:
        revenue_yoy = round((rev0 - rev1) / rev1 * 100.0, 2)

    net_income_yoy = None
    if ni0 is not None and ni1 is not None and ni1 != 0:
        net_income_yoy = round((ni0 - ni1) / ni1 * 100.0, 2)

    gross_margin = None
    if rev0 is not None and rev0 != 0 and gp0 is not None:
        gross_margin = round(gp0 / rev0 * 100.0, 2)

    net_margin = None
    if rev0 is not None and rev0 != 0 and ni0 is not None:
        net_margin = round(ni0 / rev0 * 100.0, 2)

    market_cap = _extract_market_cap(data)

    return {
        "revenue_yoy": revenue_yoy,
        "net_income_yoy": net_income_yoy,
        "gross_margin": gross_margin,
        "net_margin": net_margin,
        "market_cap": market_cap,
        "latest_fiscal_year": _latest_fy_label(ranked[0]) if ranked else None,
        "revenue_latest": rev0,
        "net_income_latest": ni0,
    }


def _latest_fy_label(row: dict[str, Any]) -> str | None:
    cy = row.get("calendarYear")
    if cy is not None:
        return str(cy)
    d = row.get("date")
    if isinstance(d, str) and d:
        return d[:4]
    return None


def _extract_market_cap(data: dict[str, Any]) -> float | None:
    prof = data.get("profile") or []
    if isinstance(prof, list) and prof and isinstance(prof[0], dict):
        mc = _num(prof[0].get("mktCap"))
        if mc is not None:
            return mc
    qt = data.get("quote") or []
    if isinstance(qt, list) and qt and isinstance(qt[0], dict):
        for k in ("marketCap", "mktCap"):
            mc = _num(qt[0].get(k))
            if mc is not None:
                return mc
    km = data.get("key_metrics_ttm") or []
    if isinstance(km, list) and km and isinstance(km[0], dict):
        for k in ("marketCapTTM", "marketCap"):
            mc = _num(km[0].get(k))
            if mc is not None:
                return mc
    return None


def _trend_from_yoy(yoy: float | None, *, thr: float = 2.0) -> str:
    if yoy is None:
        return "unknown"
    if yoy > thr:
        return "up"
    if yoy < -thr:
        return "down"
    return "flat"


def _risk_level(
    metrics: dict[str, Any],
    income_ranked: list[dict[str, Any]],
) -> str:
    ni0 = metrics.get("net_income_latest")
    rev_yoy = metrics.get("revenue_yoy")
    ni_yoy = metrics.get("net_income_yoy")
    net_m = metrics.get("net_margin")

    if ni0 is not None and ni0 < 0:
        return "high"
    if rev_yoy is not None and rev_yoy < -15:
        return "high"
    if ni_yoy is not None and ni_yoy < -25:
        return "high"
    if net_m is not None and net_m < -5:
        return "high"

    if ni_yoy is not None and ni_yoy < -10:
        return "medium"
    if rev_yoy is not None and rev_yoy < -5:
        return "medium"
    if ni0 is not None and ni0 == 0:
        return "medium"

    if rev_yoy is not None and ni_yoy is not None:
        if rev_yoy > 0 and ni_yoy > 0:
            return "low"

    if rev_yoy is None and ni_yoy is None and not income_ranked:
        return "medium"

    return "medium"


def _one_sentence_takeaway(
    revenue_trend: str,
    profit_trend: str,
    risk_level: str,
    metrics: dict[str, Any],
) -> str:
    if revenue_trend == "unknown" and profit_trend == "unknown":
        return "公开数据不足以判断同比动能，建议核对报告期或数据源后再做结论。"

    if revenue_trend == "up" and profit_trend == "up":
        base = "营收与利润同步改善，短期景气度偏友好"
    elif revenue_trend == "up" and profit_trend == "down":
        base = "营收仍扩张但利润走弱，典型“增收不增利”，要盯费用与毛利"
    elif revenue_trend == "down" and profit_trend == "down":
        base = "营收与利润双杀，经营逆风清晰"
    elif revenue_trend == "down" and profit_trend == "up":
        base = "营收承压但利润回暖，可能受降本或一次性项目驱动，需验证持续性"
    elif revenue_trend == "flat" and profit_trend == "flat":
        base = "整体增速平淡，更像存量博弈阶段"
    elif revenue_trend == "flat":
        base = "营收平淡，盈利变化主要由利润率与结构驱动"
    elif profit_trend == "flat":
        base = "利润增速钝化，边际上缺乏弹性"
    else:
        base = "盈利指标分化，建议把毛利、费用与非经常项拆开审视"

    tail = {
        "high": "；波动风险偏高。",
        "low": "；指标组合相对均衡。",
        "medium": "；不确定性仍值得关注。",
    }.get(risk_level, "。")

    return base + tail


def build_analysis(
    symbol: str,
    data: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    income = data.get("income_statement_annual") or []
    if not isinstance(income, list):
        income = []
    ranked = _sort_income_annual([r for r in income if isinstance(r, dict)])

    revenue_trend = _trend_from_yoy(metrics["revenue_yoy"])
    profit_trend = _trend_from_yoy(metrics["net_income_yoy"])
    risk = _risk_level(metrics, ranked)
    takeaway = _one_sentence_takeaway(
        revenue_trend, profit_trend, risk, metrics
    )

    return {
        "symbol": normalize_symbol(symbol),
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": {
            "revenue_yoy": metrics["revenue_yoy"],
            "net_income_yoy": metrics["net_income_yoy"],
            "gross_margin": metrics["gross_margin"],
            "net_margin": metrics["net_margin"],
            "market_cap": metrics["market_cap"],
        },
        "revenue_trend": revenue_trend,
        "profit_trend": profit_trend,
        "risk_level": risk,
        "one_sentence_takeaway": takeaway,
    }


def fmt_usd_market_cap_cn(mc: float | None) -> str:
    if mc is None:
        return "数据暂缺"
    if mc >= 1e12:
        return f"约 {mc / 1e12:.2f} 万亿美元"
    if mc >= 1e9:
        return f"约 {mc / 1e9:.2f} 亿美元"
    if mc >= 1e6:
        return f"约 {mc / 1e6:.2f} 百万美元"
    return f"约 {mc:.0f} 美元"


def fmt_usd_compact(n: float | None) -> str:
    """Format large USD amounts for narration (FMP uses USD for US listings)."""
    if n is None:
        return "数据暂缺"
    x = abs(n)
    sign = "-" if n < 0 else ""
    if x >= 1e12:
        return f"{sign}{x / 1e12:.2f} 万亿美元"
    if x >= 1e9:
        return f"{sign}{x / 1e9:.2f} 亿美元"
    if x >= 1e6:
        return f"{sign}{x / 1e6:.2f} 百万美元"
    return f"{sign}{x:.0f} 美元"


def template_narration_zh(
    symbol: str,
    data: dict[str, Any],
    metrics: dict[str, Any],
    analysis: dict[str, Any],
) -> str:
    """
    约 45–60 秒中文旁白，Shorts 金融解说风格：强钩子、少报数字、重趋势与风险。
    不使用大模型；缺同比数据时用语义兜底，不抛错。
    """
    sym = normalize_symbol(symbol)
    prof_list = data.get("profile") or []
    profile: dict[str, Any] = {}
    if isinstance(prof_list, list) and prof_list and isinstance(prof_list[0], dict):
        profile = prof_list[0]
    name = (profile.get("companyName") or "").strip() or sym

    am = analysis.get("metrics")
    if not isinstance(am, dict):
        am = {}
    rev = am.get("revenue_yoy")
    if rev is None:
        rev = metrics.get("revenue_yoy")
    prof_yoy = am.get("net_income_yoy")
    if prof_yoy is None:
        prof_yoy = metrics.get("net_income_yoy")

    # YoY 来自 compute_metrics，单位为「百分点」（如 5.2 表示 +5.2%）
    def trend_text(x: float | None) -> str:
        if x is None:
            return "暂时看不出明确方向"
        if x > 5:
            return "还在增长"
        if x > -5:
            return "基本停滞"
        return "明显下滑"

    rev_text = trend_text(rev)
    prof_text = trend_text(prof_yoy)

    if rev is not None and prof_yoy is not None:
        if rev < -5 and prof_yoy < -5:
            core = "它的基本面正在走弱"
        elif rev > 5 and prof_yoy < -5:
            core = "它表面还有增长，但利润端已经出现压力"
        elif rev > 5 and prof_yoy > 5:
            core = "它暂时稳住了增长和盈利"
        elif rev < -5 and prof_yoy > 5:
            core = "它的收入承压，但利润质量反而有所改善"
        else:
            core = "它正处在一个不太明确的调整阶段"
    else:
        core = "它的最新状态，还需要继续观察"

    takeaway = (analysis.get("one_sentence_takeaway") or "").strip()
    takeaway_block = (
        f"\n\n补充视角：{takeaway}"
        if takeaway
        else ""
    )

    return f"""这家公司，可能正在发生变化。

{name} 最新财报显示，
收入{rev_text}，
利润{prof_text}。

这意味着，{core}。

真正值得关注的，不只是这一季的数据，
而是它背后的趋势。

如果收入放缓，同时利润也被压缩，
说明公司面对的，可能不只是短期波动，
而是增长空间和竞争压力的问题。

短期来看，
{name} 还没有失去全部机会，
但它接下来能不能重新证明自己，
会比过去更难。{takeaway_block}

以上只基于公开财报数据整理，
不构成任何投资建议，
非美元公司请以公司原币披露为准。
"""


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "数据暂缺"
    return f"{v:+.1f}%"


def fmt_pct_plain(v: float | None) -> str:
    if v is None:
        return "数据暂缺"
    return f"{v:.1f}%"


__all__ = [
    "fetch_fmp_bundle",
    "compute_metrics",
    "build_analysis",
    "template_narration_zh",
]
