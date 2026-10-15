"""Queue runner: data/trading/requests.json (GitHub Actions).

    {"action": "calendar", "date": "2026-10-16"}
        → which catalog events release that day (FRED) → reports/<date>-calendar.md
    {"action": "report", "event": "ppi", "consensus": 0.3, "actual": 1.3 (optional), "pool": [...] (optional)}
        → war report (actual from FRED unless given) → reports/<date>-<event>.md/.json
    {"action": "confirm", "report": "reports/2026-10-15-ppi.json"}
        → open confirmation appended to that report

Every run also refreshes stats.json from journal.json.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import confirm, journal, market, report
from .events import BY_KEY
from .fred import Fred


def _read(p: Path, default: Any) -> Any:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() and p.read_text(encoding="utf-8").strip() else default


def _write(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")


class DeskRunner:
    def __init__(self, root: Path, fred: Fred | None = None, quote=market.change_pct) -> None:
        self.root = Path(root)
        self.fred = fred or Fred()
        self.quote = quote
        self.config = _read(self.root / "config.json", {"pool": ["QQQ", "MU", "RKLB"]})

    def calendar(self, item: dict) -> None:
        day = date.fromisoformat(item.get("date") or date.today().isoformat())
        evs = self.fred.releases_on(day)
        lines = [f"# {day} 事件日历", ""] + [f"- **{e.tier} 级** {e.name}({e.time_et} ET)" for e in evs]
        if not evs:
            lines.append("今天没有目录里的高影响数据。")
        path = self.root / "reports" / f"{day}-calendar.md"
        _write(path, "\n".join(lines) + "\n")
        item.update(status="done", events=[e.key for e in evs], file=str(path.relative_to(self.root)))

    def report(self, item: dict) -> None:
        ev = BY_KEY[item["event"]]
        if item.get("actual") is not None:
            actual, period = float(item["actual"]), item.get("period", "")
        else:
            actual, period = self.fred.actual(ev)
        consensus = item.get("consensus")
        pool = item.get("pool") or self.config.get("pool", [])
        prices = {s: (q[0] if (q := self.quote(s)) else None) for s in pool}
        rep = report.build(ev, actual, float(consensus) if consensus is not None else None,
                           "manual" if consensus is not None else "none", pool, prices)
        day = date.today().isoformat()
        base = self.root / "reports" / f"{day}-{ev.key}"
        data = rep.to_dict() | {"period": period, "generated_at": datetime.now(timezone.utc).isoformat()}
        _write(base.with_suffix(".json"), data)
        _write(base.with_suffix(".md"), report.render(rep) + "\n")
        item.update(status="done", direction=rep.direction, surprise=rep.surprise,
                    file=str(base.with_suffix(".md").relative_to(self.root)))

    def confirm(self, item: dict) -> None:
        path = self.root / item["report"]
        data = _read(path, {})
        res = confirm.check(data.get("direction", "none"), [leg["symbol"] for leg in data.get("legs", [])], self.quote)
        data["confirmation"] = res | {"checked_at": datetime.now(timezone.utc).isoformat()}
        _write(path, data)
        md = path.with_suffix(".md")
        verdict = "✅ 可入场" if res["ok"] else "⛔ 不入场:" + ";".join(res["reasons"])
        _write(md, (md.read_text(encoding="utf-8") if md.exists() else "") + f"\n## 开盘确认\n{verdict}\n"
               f"纳指期货 {res.get('futures_pct')}% · VIX {res.get('vix_pct')}% · 同向标的 {res.get('movers')}\n")
        item.update(status="done", ok=res["ok"], reasons=res["reasons"])

    def run(self) -> list[str]:
        path = self.root / "requests.json"
        queue = _read(path, [])
        log = []
        for item in queue:
            if item.get("status", "pending") != "pending":
                continue
            item["processed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            try:
                getattr(self, item["action"])(item)
            except Exception as e:  # noqa: BLE001 - record and keep going
                item.update(status="error", failures=[f"{type(e).__name__}: {e}"[:300]])
            log.append(f"{item.get('action')}: {item['status']}")
        if queue:
            _write(path, queue)
        _write(self.root / "stats.json", journal.stats(_read(self.root / "journal.json", [])))
        return log


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="stateverge.trading.eventdesk")
    ap.add_argument("--root", default="data/trading")
    print("\n".join(DeskRunner(Path(ap.parse_args().root)).run()) or "nothing pending")
    return 0
