"""Git-driven runner for GitHub Actions (works from a phone, no merge needed).

Everything the cloud job needs lives in tracked JSON under ``data/archive/``:

    claims/*.json          curated + collected statements (importer format)
    collect_requests.json  [{"person": ..., "topic": ..., "years": 10, "status": "pending"}]
    publish_queue.json     [{"earlier_id": ..., "later_id": ..., "reviewer": ...,
                             "context_reviewed": true, "opinion_checked": true,
                             "corrections_checked": true, "post": false, "status": "pending"}]
    x_ledger.json          what has been posted to X (prevents double posting)
    stats_requests.json    [{"status": "pending"}] → weekly account snapshot
    x_stats.json           snapshot history (followers, impressions)

Each run rebuilds a throwaway SQLite DB from these files, processes every
``pending`` request, and writes results back into the same files; the
workflow then commits them. Editing a JSON file in the GitHub web/mobile
editor is the whole UI.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from . import importer, publish_guard, x_publisher, x_stats
from .claim_search import search_history
from .contradiction import compare
from .database import ArchiveDB
from .models import NewsEvent, PoliticalClaim
from .script_generator import card_from_comparison


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def claim_to_record(c: PoliticalClaim) -> dict[str, Any]:
    """Importer-format record. The transcript is the verbatim official excerpt
    (context + quote), so re-import re-verifies it instead of trusting a flag."""
    rec: dict[str, Any] = {
        "claim_id": c.claim_id,
        "person_name": c.person_name,
        "party": c.party,
        "office": c.office,
        "topic": c.topic,
        "subtopic": c.subtopic,
        "statement_date": c.statement_date,
        "claim_type": c.claim_type,
        "statement_text_original": c.statement_text_original,
        "statement_text_zh": c.statement_text_zh,
        "source_name": c.source_name,
        "source_url": c.source_url,
        "video_url": c.video_url,
    }
    if c.transcript_verified:
        rec["transcript"] = " ".join(p for p in (c.context_before, c.statement_text_original, c.context_after) if p)
    return {k: v for k, v in rec.items() if v not in ("", None)}


class CloudRunner:
    def __init__(self, root: Path, providers: list | None = None, x_client: x_publisher.XClient | None = None,
                 stance_judge=None) -> None:
        self.root = Path(root)
        self.claims_dir = self.root / "claims"
        self.db = ArchiveDB(":memory:")
        self.providers = providers
        self.x_client = x_client
        self.stance_judge = stance_judge
        self.log: list[str] = []

    # -- setup / teardown ---------------------------------------------------

    def load(self) -> None:
        for f in sorted(self.claims_dir.glob("*.json")):
            report = importer.import_file(f, self.db)
            self.log.append(f"导入 {f.name}: {report.imported} 条,失败 {report.failed} 条")
            self.log += [f"  ✗ #{r.index + 1}: {r.error}" for r in report.records if r.error]
        self.db.conn.executescript(x_publisher.X_POSTS_SCHEMA)
        for row in _read(self.root / "x_ledger.json", []):
            self.db.conn.execute(
                "INSERT OR REPLACE INTO x_posts (card_key, part_index, tweet_id, text, posted_at) VALUES (?, ?, ?, ?, ?)",
                (row["card_key"], row["part_index"], row["tweet_id"], row["text"], row["posted_at"]),
            )
        self.db.conn.commit()

    def save_ledger(self) -> None:
        rows = [dict(r) for r in self.db.conn.execute("SELECT * FROM x_posts ORDER BY posted_at, card_key, part_index")]
        _write(self.root / "x_ledger.json", rows)

    # -- collection ---------------------------------------------------------

    def run_collect(self) -> None:
        path = self.root / "collect_requests.json"
        requests = _read(path, [])
        if self.providers is None:
            from .govinfo_provider import GovInfoProvider

            self.providers = [GovInfoProvider()]
        for req in requests:
            if req.get("status", "pending") != "pending":
                continue
            person, topic = req["person"], req["topic"]
            event = NewsEvent("cloud", topic, people=[person], topics=[topic], event_date=date.today().isoformat())
            found = search_history(event, self.db, self.providers, years=int(req.get("years", 10)))[person]
            new = [c for c in found if not self._already_filed(c.claim_id)]
            out = self.claims_dir / f"collected-{date.today().isoformat()}-{_slug(person)}-{_slug(topic)}.json"
            existing = _read(out, {"claims": []})["claims"]
            ids = {r["claim_id"] for r in existing}
            existing += [claim_to_record(c) for c in new if c.claim_id not in ids]
            if existing:
                _write(out, {"claims": existing})
            req.update(status="done", processed_at=_now(), found=len(found), new=len(new),
                       file=str(out.relative_to(self.root)) if existing else "")
            self.log.append(f"采集 {person} / {topic}: 找到 {len(found)} 条,新增 {len(new)} 条")
        if requests:
            _write(path, requests)

    def _already_filed(self, claim_id: str) -> bool:
        for f in self.claims_dir.glob("*.json"):
            data = _read(f, [])
            recs = data["claims"] if isinstance(data, dict) else data
            if any(isinstance(r, dict) and r.get("claim_id") == claim_id for r in recs):
                return True
        return False

    # -- publishing ---------------------------------------------------------

    def run_publish(self) -> None:
        path = self.root / "publish_queue.json"
        queue = _read(path, [])
        for item in queue:
            if item.get("status", "pending") != "pending":
                continue
            item["processed_at"] = _now()
            earlier, later = self.db.get(item.get("earlier_id", "")), self.db.get(item.get("later_id", ""))
            if earlier is None or later is None:
                item.update(status="error", failures=["claim_id 不存在"])
                continue
            cmp = compare(earlier, later, stance_judge=self.stance_judge)
            card = card_from_comparison(cmp)
            attestation = publish_guard.ReviewerAttestation(
                reviewer=str(item.get("reviewer", "")),
                opinion_not_stated_as_fact=bool(item.get("opinion_checked")),
                corrections_checked=bool(item.get("corrections_checked")),
                context_reviewed=bool(item.get("context_reviewed")),
            )
            want_post = bool(item.get("post"))
            try:
                result = x_publisher.publish_card(
                    card, attestation, db=self.db, client=self.x_client, dry_run=not want_post
                )
            except x_publisher.XPublishError as e:
                item.update(status="error", failures=[str(e)])
                self.save_ledger()  # keep parts of a thread that did go out
                continue
            item.update(
                comparison_status=cmp.status.value,
                comparison_reasons=cmp.reasons,
                decision=result.decision,
                failures=result.failures,
                preview=result.parts,
            )
            if result.tweet_ids:
                item.update(status="posted", url=result.url)
            elif result.decision == publish_guard.BLOCK_PUBLISH:
                item["status"] = "blocked"
            else:
                item["status"] = "previewed"  # set post: true and status: pending to publish
            self.log.append(f"发布 {item['earlier_id'][:8]}→{item['later_id'][:8]}: {item['status']}")
        if queue:
            _write(path, queue)
        self.save_ledger()

    # -- stats --------------------------------------------------------------

    def run_stats(self, force: bool = False) -> None:
        """Snapshot the account when a stats request is pending (or ``force``,
        used by the weekly schedule)."""
        path = self.root / "stats_requests.json"
        requests = _read(path, [])
        pending = [r for r in requests if r.get("status", "pending") == "pending"]
        if not (pending or force):
            return
        client = self.x_client or x_publisher.XClient(x_publisher.XCredentials.from_env())
        try:
            report = x_stats.record(x_stats.take_snapshot(client), self.root / "x_stats.json")
            outcome = {"status": "done", "report": report}
        except x_publisher.XPublishError as e:
            outcome = {"status": "error", "failures": [str(e)]}
            report = f"统计失败:{e}"
        for r in pending:
            r.update(processed_at=_now(), **outcome)
        if requests:
            _write(path, requests)
        self.log.append(report)

    def run(self, stats: bool = False) -> list[str]:
        self.load()
        self.run_collect()
        self.run_publish()
        self.run_stats(force=stats)
        return self.log


def _slug(s: str) -> str:
    return "-".join("".join(c.lower() if c.isalnum() else " " for c in s).split())[:40] or "x"
