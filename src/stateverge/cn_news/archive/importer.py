"""Bulk import of hand-curated statements from a JSON file.

The file is a list of records (or ``{"claims": [...]}``). Each record goes
through the same checks as collected material:

* source tier and copyright are *computed* from the URL, never taken on trust;
* ``transcript_verified`` cannot be set in the file — it is only true when the
  quote is found verbatim in the supplied transcript (text, file or timed
  segments), which also fills context_before/after and clip bounds;
* fact checks go through ``fact_check.record_fact_check`` (recognised orgs only).

Records are keyed by a stable id (person + date + quote), so re-importing the
same file updates rows instead of duplicating them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from . import copyright, fact_check, transcript
from .database import ArchiveDB
from .models import PoliticalClaim, SourceTier, person_slug
from .source_resolver import resolve_claim

# Keys a record may carry. Anything else is rejected so typos surface.
CLAIM_KEYS = {
    "claim_id", "person_name", "party", "office", "topic", "subtopic",
    "statement_date", "statement_text_original", "statement_text_zh",
    "source_name", "source_url", "video_url", "video_start", "video_end",
    "context_before", "context_after", "claim_type", "fair_use_purpose",
}
EXTRA_KEYS = {
    "official_account_of_speaker",  # bool: speaker's own verified account/campaign site
    "licensed_from",  # str: licensor when footage is licensed
    "campaign_material",  # bool
    "transcript",  # str: full transcript text
    "transcript_file",  # str: path to a .txt transcript, relative to the JSON file
    "transcript_segments",  # [{"start": s, "end": s, "text": "..."}]
    "fact_check",  # {"source": ..., "url": ..., "rating": ...}
}
REQUIRED = ("person_name", "topic", "statement_date", "statement_text_original", "source_name")

# Set by later pipeline stages; a re-import must not reset them.
_WORKFLOW_FIELDS = ("archive_status", "related_claim_id", "comparison_status", "download_status")

_TIER_CONFIDENCE = {
    SourceTier.SOURCE_A.value: 0.9,
    SourceTier.SOURCE_B.value: 0.8,
    SourceTier.SOURCE_C.value: 0.7,
    SourceTier.SOURCE_D.value: 0.7,
    SourceTier.SOURCE_E.value: 0.2,
}


@dataclass
class RecordReport:
    index: int
    claim_id: str = ""
    label: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class ImportReport:
    records: list[RecordReport] = field(default_factory=list)

    @property
    def imported(self) -> int:
        return sum(1 for r in self.records if not r.error)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.records if r.error)

    def render(self) -> str:
        lines = []
        for r in self.records:
            head = f"#{r.index + 1} {r.label}".rstrip()
            if r.error:
                lines.append(f"✗ {head}: {r.error}")
                continue
            lines.append(f"✓ {head} [{r.claim_id}]")
            lines += [f"    ! {w}" for w in r.warnings]
        lines.append(f"\n导入 {self.imported} 条,失败 {self.failed} 条")
        return "\n".join(lines)


def stable_id(person_name: str, statement_date: str, text: str) -> str:
    key = f"{person_slug(person_name)}|{statement_date}|{' '.join(transcript.tokens(text))}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _load_transcript(rec: dict[str, Any], base: Path):
    if rec.get("transcript_segments"):
        return [transcript.Segment(float(s["start"]), float(s["end"]), s["text"]) for s in rec["transcript_segments"]]
    if rec.get("transcript_file"):
        path = (base / rec["transcript_file"]).resolve()
        return path.read_text(encoding="utf-8")
    return rec.get("transcript") or None


def build_claim(rec: dict[str, Any], base: Path = Path(".")) -> tuple[PoliticalClaim, list[str]]:
    """Validate one record and return the claim plus reviewer warnings."""
    if not isinstance(rec, dict):
        raise ValueError("record must be an object")
    unknown = sorted(set(rec) - CLAIM_KEYS - EXTRA_KEYS)
    if unknown:
        raise ValueError(f"unknown fields: {unknown}")
    missing = [k for k in REQUIRED if not str(rec.get(k, "")).strip()]
    if missing:
        raise ValueError(f"missing required fields: {missing}")

    data = {k: rec[k] for k in CLAIM_KEYS if k in rec}
    data.setdefault("claim_id", stable_id(rec["person_name"], rec["statement_date"], rec["statement_text_original"]))
    claim = PoliticalClaim(**data)  # validates enum fields
    date.fromisoformat(claim.statement_date)
    warnings: list[str] = []

    res = resolve_claim(claim, bool(rec.get("official_account_of_speaker")))
    if res.needs_original:
        warnings.append(f"{res.reason}(当前来源只能作线索)")

    cr = copyright.apply(
        claim,
        licensed_from=rec.get("licensed_from"),
        campaign_material=bool(rec.get("campaign_material")),
    )
    if not cr.publishable:
        warnings.append(f"版权:{cr.note}")

    tx = _load_transcript(rec, base)
    if tx is None:
        warnings.append("未提供逐字稿:transcript_verified = false,不能用于对比或发布")
    else:
        match = transcript.apply(claim, tx)
        if not match.verified:
            warnings.append("原话在逐字稿中找不到逐字对应(可能是改写或翻译):transcript_verified = false")

    fc = rec.get("fact_check")
    if fc:
        fact_check.record_fact_check(claim, fc.get("source", ""), fc.get("url", ""), fc.get("rating", ""))

    claim.confidence = _TIER_CONFIDENCE[claim.source_type] * (1.0 if claim.transcript_verified else 0.5)
    if not (claim.context_before.strip() or claim.context_after.strip()):
        warnings.append("缺少前后文 context_before/context_after")
    return claim, warnings


def import_file(path: str | Path, db: ArchiveDB, dry_run: bool = False) -> ImportReport:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload["claims"] if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("expected a JSON list or {\"claims\": [...]}")

    report = ImportReport()
    for i, rec in enumerate(records):
        rr = RecordReport(i)
        if isinstance(rec, dict):
            rr.label = f"{rec.get('person_name', '?')} {rec.get('statement_date', '?')}"
        try:
            claim, rr.warnings = build_claim(rec, path.parent)
            rr.claim_id = claim.claim_id
            existing = db.get(claim.claim_id)
            if existing:
                # Re-import refreshes the record but keeps workflow state.
                for name in _WORKFLOW_FIELDS:
                    setattr(claim, name, getattr(existing, name))
                claim.created_at = existing.created_at
                rr.warnings.append("已存在,已更新(保留发布状态与对比关系)")
            if not dry_run:
                db.upsert(claim)
        except (ValueError, KeyError, TypeError, OSError) as e:
            rr.error = str(e)
        report.records.append(rr)
    return report
