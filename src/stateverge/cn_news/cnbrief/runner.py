"""Queue runner (GitHub Actions): ``data/cnbrief/requests.json``.

    {"status": "pending"}                → scan: draft posts, write scans/<date>.json
    {"status": "pending", "auto": true}  → scan, then schedule every event that
        passes the guard: first one 2 h later, the rest 2 h apart (veto window).
        Each event becomes its own entry in posts.json with "post_after".

posts.json entries: {"key", "status": "scheduled"|"posted"|"vetoed"|"blocked"|"error",
                     "post_after", "parts", "event"} — change status to "vetoed"
before post_after to stop one. Any run posts the due ones (the daily tick
also triggers runs).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..archive import x_publisher
from . import sources
from .editor import Editor, post_parts
from .guard import check

FIRST_DELAY_H, SPACING_H, MAX_PER_DAY = 2, 2, 4


def _read(path: Path, default: Any) -> Any:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class CnBriefRunner:
    def __init__(self, root: Path, fetch=None, editor=None, x_client=None) -> None:
        self.root = Path(root)
        self.fetch = fetch
        self.editor = editor or Editor()
        self.x_client = x_client
        self.log: list[str] = []
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(x_publisher.X_POSTS_SCHEMA)
        for r in _read(self.root / "x_ledger.json", []):
            self.conn.execute("INSERT OR REPLACE INTO x_posts VALUES (?, ?, ?, ?, ?)",
                              (r["card_key"], r["part_index"], r["tweet_id"], r["text"], r["posted_at"]))

    def _save_ledger(self) -> None:
        cols = ("card_key", "part_index", "tweet_id", "text", "posted_at")
        _write(self.root / "x_ledger.json",
               [dict(zip(cols, r, strict=True)) for r in self.conn.execute(f"SELECT {', '.join(cols)} FROM x_posts")])

    def seen_titles(self) -> set[str]:
        return {p["event"].get("title", "") for p in _read(self.root / "posts.json", [])}

    def scan(self, item: dict, now: datetime) -> None:
        heads = sources.collect(self.fetch, now=now)
        by_id = {h.id: h.to_dict() for h in heads}
        events = self.editor.events(heads)
        seen = self.seen_titles()
        drafts = []
        for ev in events:
            parts = post_parts(ev, by_id)
            problems = check(ev, by_id, "\n".join(parts))
            if ev.get("title") in seen:
                problems.append("之前已发过同一事件")
            drafts.append({"event": ev, "parts": parts, "problems": problems})
        day = now.date().isoformat()
        _write(self.root / "scans" / f"{day}.json", {"scanned_at": _iso(now), "headlines": [h.to_dict() for h in heads],
                                                    "drafts": drafts})
        ok = [d for d in drafts if not d["problems"]][:MAX_PER_DAY]
        item.update(status="drafted", scan=f"scans/{day}.json", collected=len(heads), events=len(events),
                    passed=len(ok), blocked=[f"{d['event'].get('title')}: {'; '.join(d['problems'])}"
                                             for d in drafts if d["problems"]][:10])
        if item.get("auto") and ok:
            posts = _read(self.root / "posts.json", [])
            for k, d in enumerate(ok):
                when = now + timedelta(hours=FIRST_DELAY_H + k * SPACING_H)
                posts.append({"key": f"CNBRIEF:{day}:{k}", "status": "scheduled", "post_after": _iso(when),
                              "parts": d["parts"], "event": d["event"]})
            _write(self.root / "posts.json", posts)
            item.update(status="scheduled", scheduled=[{"title": d["event"].get("title"),
                                                         "post_after": _iso(now + timedelta(hours=FIRST_DELAY_H + k * SPACING_H))}
                                                        for k, d in enumerate(ok)])
        self.log.append(f"国内要闻:抓取 {len(heads)} 条,事件 {len(events)} 个,通过 {len(ok)} 个")

    def post_due(self, now: datetime) -> None:
        path = self.root / "posts.json"
        posts = _read(path, [])
        changed = False
        for p in posts:
            if p.get("status") != "scheduled" or datetime.fromisoformat(p["post_after"]) > now:
                continue
            changed = True
            by_id = {}  # re-check on the stored text; sources were checked at scan time
            problems = [x for x in check(p["event"], by_id, "\n".join(p["parts"])) if "来源不足" not in x]
            if problems:
                p.update(status="blocked", failures=problems)
                continue
            try:
                client = self.x_client or x_publisher.XClient(x_publisher.XCredentials.from_env())
                ids = x_publisher.post_thread(client, p["parts"], p["key"], self.conn)
            except x_publisher.XPublishError as e:
                p.update(status="error", failures=[str(e)[:300]])
                continue
            finally:
                self._save_ledger()
            p.update(status="posted", url=f"https://x.com/i/status/{ids[0]}", posted_at=_iso(now),
                     approved_by="auto (no veto before post_after)")
            self.log.append(f"国内要闻已发布:{p['url']}")
        if changed:
            _write(path, posts)

    def run(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        path = self.root / "requests.json"
        queue = _read(path, [])
        for item in queue:
            if item.get("status", "pending") != "pending":
                continue
            item["processed_at"] = _iso(now)
            try:
                self.scan(item, now)
            except RuntimeError as e:
                item.update(status="error", failures=[str(e)[:300]])
        if queue:
            _write(path, queue)
        self.post_due(now)
        return self.log
