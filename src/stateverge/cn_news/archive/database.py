"""SQLite storage for ``political_claims`` (Section 3)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .models import (
    ArchiveStatus,
    ClaimType,
    ComparisonStatus,
    CopyrightType,
    DownloadStatus,
    FactCheckStatus,
    PoliticalClaim,
    SourceTier,
)

# repo_root/data/political_archive.db
DEFAULT_DB_PATH = Path(__file__).resolve().parents[4] / "data" / "political_archive.db"


def _check(column: str, enum_cls) -> str:
    values = ", ".join(f"'{m.value}'" for m in enum_cls)
    return f"CHECK ({column} IN ({values}))"


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS political_claims (
    claim_id                TEXT PRIMARY KEY,
    person_id               TEXT NOT NULL,
    person_name             TEXT NOT NULL,
    party                   TEXT NOT NULL DEFAULT '',
    office                  TEXT NOT NULL DEFAULT '',

    topic                   TEXT NOT NULL,
    subtopic                TEXT NOT NULL DEFAULT '',

    statement_date          TEXT NOT NULL,
    statement_text_original TEXT NOT NULL,
    statement_text_zh       TEXT NOT NULL DEFAULT '',

    source_type             TEXT NOT NULL {_check('source_type', SourceTier)},
    source_name             TEXT NOT NULL,
    source_url              TEXT NOT NULL DEFAULT '',

    video_url               TEXT NOT NULL DEFAULT '',
    video_start             REAL,
    video_end               REAL,

    transcript_verified     INTEGER NOT NULL DEFAULT 0,

    context_before          TEXT NOT NULL DEFAULT '',
    context_after           TEXT NOT NULL DEFAULT '',

    claim_type              TEXT NOT NULL {_check('claim_type', ClaimType)},

    related_claim_id        TEXT REFERENCES political_claims(claim_id),

    comparison_status       TEXT NOT NULL {_check('comparison_status', ComparisonStatus)},

    fact_check_status       TEXT NOT NULL {_check('fact_check_status', FactCheckStatus)},
    fact_check_source       TEXT NOT NULL DEFAULT '',
    fact_check_url          TEXT NOT NULL DEFAULT '',
    fact_check_rating       TEXT NOT NULL DEFAULT '',

    copyright_owner         TEXT NOT NULL DEFAULT 'Unknown',
    copyright_type          TEXT NOT NULL {_check('copyright_type', CopyrightType)},
    government_work         INTEGER NOT NULL DEFAULT 0,
    fair_use_purpose        TEXT NOT NULL DEFAULT '',

    download_status         TEXT NOT NULL {_check('download_status', DownloadStatus)},
    archive_status          TEXT NOT NULL {_check('archive_status', ArchiveStatus)},

    confidence              REAL NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),

    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,

    CHECK (video_start IS NULL OR video_end IS NULL OR video_end > video_start)
);

CREATE INDEX IF NOT EXISTS idx_claims_person_date
    ON political_claims (person_id, statement_date);
CREATE INDEX IF NOT EXISTS idx_claims_topic
    ON political_claims (topic, subtopic);
CREATE INDEX IF NOT EXISTS idx_claims_related
    ON political_claims (related_claim_id);
"""

_COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(PoliticalClaim))


class ArchiveDB:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ArchiveDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    # -- writes -------------------------------------------------------------

    def upsert(self, claim: PoliticalClaim) -> PoliticalClaim:
        claim.updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        row = claim.to_row()
        cols = ", ".join(_COLUMNS)
        marks = ", ".join(f":{c}" for c in _COLUMNS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c not in ("claim_id", "created_at"))
        with self.transaction() as conn:
            conn.execute(
                f"INSERT INTO political_claims ({cols}) VALUES ({marks}) "
                f"ON CONFLICT(claim_id) DO UPDATE SET {updates}",
                row,
            )
        return claim

    def upsert_many(self, claims: Iterable[PoliticalClaim]) -> int:
        n = 0
        for claim in claims:
            self.upsert(claim)
            n += 1
        return n

    def link(self, claim_id: str, related_claim_id: str, status: ComparisonStatus) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE political_claims SET related_claim_id = ?, comparison_status = ?, "
                "updated_at = ? WHERE claim_id = ?",
                (
                    related_claim_id,
                    status.value,
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    claim_id,
                ),
            )

    # -- reads --------------------------------------------------------------

    def get(self, claim_id: str) -> PoliticalClaim | None:
        row = self.conn.execute(
            "SELECT * FROM political_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        return PoliticalClaim.from_row(dict(row)) if row else None

    def search(
        self,
        person_id: str | None = None,
        keywords: Sequence[str] = (),
        since: str | None = None,
        until: str | None = None,
        exclude_status: Sequence[str] = (ArchiveStatus.REJECTED.value,),
        limit: int = 500,
    ) -> list[PoliticalClaim]:
        """Chronological search. Keywords are OR-ed across topic/subtopic/text."""
        where: list[str] = []
        params: list[object] = []
        if person_id:
            where.append("person_id = ?")
            params.append(person_id)
        if since:
            where.append("statement_date >= ?")
            params.append(since)
        if until:
            where.append("statement_date <= ?")
            params.append(until)
        if exclude_status:
            where.append(f"archive_status NOT IN ({', '.join('?' for _ in exclude_status)})")
            params.extend(exclude_status)
        kw = [k.strip().lower() for k in keywords if k and k.strip()]
        if kw:
            ors = []
            for k in kw:
                like = f"%{k}%"
                ors.append(
                    "(lower(topic) LIKE ? OR lower(subtopic) LIKE ? "
                    "OR lower(statement_text_original) LIKE ? OR statement_text_zh LIKE ?)"
                )
                params.extend([like, like, like, like])
            where.append("(" + " OR ".join(ors) + ")")
        sql = "SELECT * FROM political_claims"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY statement_date ASC, created_at ASC LIMIT ?"
        params.append(limit)
        return [PoliticalClaim.from_row(dict(r)) for r in self.conn.execute(sql, params)]
