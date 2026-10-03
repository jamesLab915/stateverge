"""Queue runner for GitHub Actions — drive the 24H digest from a phone.

``data/infogap/digest_requests.json`` lifecycle of one entry:

    {"status": "pending"}                          → scan the last 24h
    → status "drafted", "digest": "digests/2026-10-02.json",
      "review": "digests/2026-10-02.md", "preview": [...thread...]

    Read the .md, then set on the same entry:
    {"post": true, "reviewer": "名字", "facts_checked": true, "access_checked": true,
     "status": "pending"}
    optional "picks": [1, 3, 4]   choose candidates by their number in the .md
    optional "text": "..."        your edited X text (re-checked by the guard)
    → status "posted" + url, or "blocked" + failures

Veto-window mode — ``{"status": "pending", "auto": true}`` (written daily by
the scheduler): after the scan, if at least 2 items pass the stricter auto
criteria, the entry becomes ``"scheduled"`` with ``post_after`` (+3h by
default). Any run after that time posts it unless you changed the status to
``"vetoed"`` first. You can also approve early the normal way (post: true …).

Posted threads are recorded in ``x_ledger.json`` so a re-run never double-posts.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..archive import x_publisher
from .assessor import LLMAssessor
from .collectors import default_collectors
from .coverage import default_coverage
from .digest import (
    DigestReview,
    ScanResult,
    auto_picks,
    auto_text,
    check,
    check_auto,
    digest_key,
    pick,
    review_markdown,
    scan,
    x_text,
)
from .models import ScoredSignal

DEFAULT_VETO_HOURS = 3


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read(path: Path, default: Any) -> Any:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def save_scan(result: ScanResult, root: Path) -> tuple[Path, Path]:
    day = result.scanned_at[:10]
    data = asdict(result)
    data["shortlisted"] = [s.to_dict() for s in result.shortlisted]
    data["candidates"] = [s.to_dict() for s in result.candidates]
    data["picks"] = [s.signal.signal_id for s in result.picks]
    js, md = root / "digests" / f"{day}.json", root / "digests" / f"{day}.md"
    _write(js, data)
    _write(md, review_markdown(result))
    return js, md


def load_candidates(path: Path) -> tuple[list[ScoredSignal], list[str]]:
    data = _read(path, {})
    return [ScoredSignal.from_dict(d) for d in data.get("candidates", [])], list(data.get("picks", []))


class InfoGapRunner:
    def __init__(self, root: Path, collectors=None, coverage=None, assessor=None, x_client=None) -> None:
        self.root = Path(root)
        self.collectors = collectors
        self.coverage = coverage
        self.assessor = assessor
        self.x_client = x_client
        self.log: list[str] = []
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(x_publisher.X_POSTS_SCHEMA)
        for row in _read(self.root / "x_ledger.json", []):
            self.conn.execute(
                "INSERT OR REPLACE INTO x_posts VALUES (?, ?, ?, ?, ?)",
                (row["card_key"], row["part_index"], row["tweet_id"], row["text"], row["posted_at"]),
            )

    def _save_ledger(self) -> None:
        cols = ("card_key", "part_index", "tweet_id", "text", "posted_at")
        rows = [dict(zip(cols, r, strict=True)) for r in self.conn.execute(f"SELECT {', '.join(cols)} FROM x_posts ORDER BY posted_at")]
        _write(self.root / "x_ledger.json", rows)

    def _scan(self, item: dict, now: datetime | None = None) -> None:
        result = scan(
            self.collectors if self.collectors is not None else default_collectors(),
            self.coverage or default_coverage(),
            self.assessor or LLMAssessor(),
            hours=int(item.get("hours", 24)),
            now=now,
        )
        js, md = save_scan(result, self.root)
        item.update(
            status="drafted",
            digest=str(js.relative_to(self.root)),
            review=str(md.relative_to(self.root)),
            collected=result.collected,
            candidates=len(result.candidates),
            preview=x_publisher.split_thread(x_text(result.picks)) if result.picks else [],
        )
        errors = sorted({s.coverage.error for s in result.shortlisted if s.coverage.error})
        if errors:
            item["coverage_errors"] = errors[:3]
        if item.get("auto"):
            picks, skipped = auto_picks(result.candidates)
            item["auto_skipped"] = skipped[:10]
            if len(picks) >= 2:
                post_after = (now or datetime.now(timezone.utc)) + timedelta(hours=float(item.get("veto_hours", DEFAULT_VETO_HOURS)))
                item.update(
                    status="scheduled",
                    auto_signal_ids=[p.signal.signal_id for p in picks],
                    post_after=post_after.replace(microsecond=0).isoformat(),
                    preview=x_publisher.split_thread(auto_text(picks)),
                    how_to_veto='把 "status" 改成 "vetoed" 并提交,即可取消这次自动发布',
                )
            else:
                item["note_auto"] = "符合自动发布条件的不足 2 条,今天不自动发;可人工审核后发"
        self.log.append(f"扫描:抓取 {result.collected} 条,候选 {len(result.candidates)} 条,入选 {len(result.picks)} 条 → {md.name}")

    def _post(self, item: dict) -> None:
        candidates, default_ids = load_candidates(self.root / item.get("digest", ""))
        if item.get("picks"):
            chosen = [candidates[i - 1] for i in item["picks"] if 1 <= int(i) <= len(candidates)]
        else:
            by_id = {c.signal.signal_id: c for c in candidates}
            chosen = [by_id[i] for i in default_ids if i in by_id] or pick(candidates)
        text = item.get("text") or x_text(chosen)
        review = DigestReview(str(item.get("reviewer", "")), bool(item.get("facts_checked")),
                              bool(item.get("access_checked")))
        guard = check(chosen, text, review)
        parts = x_publisher.split_thread(text)
        item.update(preview=parts, failures=guard.failures)
        if not guard.allowed:
            item["status"] = "blocked"
            self.log.append(f"发布被拦截:{'; '.join(guard.failures)}")
            return
        client = self.x_client or x_publisher.XClient(x_publisher.XCredentials.from_env())
        day = Path(item.get("digest", "")).stem or None
        try:
            ids = x_publisher.post_thread(client, parts, digest_key(day), self.conn)
        except x_publisher.XPublishError as e:
            item.update(status="error", failures=[str(e)])
            return
        finally:
            self._save_ledger()
        item.update(status="posted", url=f"https://x.com/i/status/{ids[0]}")
        self.log.append(f"已发布:{item['url']}")

    def _post_scheduled(self, item: dict) -> None:
        candidates, _ = load_candidates(self.root / item.get("digest", ""))
        by_id = {c.signal.signal_id: c for c in candidates}
        picks = [by_id[i] for i in item.get("auto_signal_ids", []) if i in by_id]
        text = auto_text(picks)
        guard = check_auto(picks, text)
        item.update(processed_at=_now(), failures=guard.failures, preview=x_publisher.split_thread(text))
        if not guard.allowed:
            item["status"] = "blocked"
            self.log.append(f"自动发布被拦截:{'; '.join(guard.failures)}")
            return
        client = self.x_client or x_publisher.XClient(x_publisher.XCredentials.from_env())
        day = Path(item.get("digest", "")).stem or None
        try:
            ids = x_publisher.post_thread(client, item["preview"], digest_key(day), self.conn)
        except x_publisher.XPublishError as e:
            item.update(status="error", failures=[str(e)])
            return
        finally:
            self._save_ledger()
        item.update(status="posted", approved_by="auto (no veto before post_after)",
                    url=f"https://x.com/i/status/{ids[0]}")
        self.log.append(f"自动发布:{item['url']}")

    def run(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        path = self.root / "digest_requests.json"
        queue = _read(path, [])
        for item in queue:
            if item.get("status") == "scheduled":
                due = datetime.fromisoformat(item["post_after"])
                if due <= now:
                    self._post_scheduled(item)
                continue
            if item.get("status", "pending") != "pending":
                continue
            item["processed_at"] = _now()
            if item.get("post"):
                if not item.get("digest"):
                    item.update(status="error", failures=["先扫描生成审核稿,再设置 post: true"])
                    continue
                self._post(item)
            else:
                self._scan(item, now)
        if queue:
            _write(path, queue)
        return self.log
