"""Political Archive v1 — core enums and record types.

Stateverge stores who said what, when, and what happened next. It never
decides for the viewer who is lying, so the vocabularies below deliberately
contain no LIE / LIAR / DISHONEST verdicts.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ClaimType(str, Enum):
    PROMISE = "PROMISE"
    PREDICTION = "PREDICTION"
    POLICY_POSITION = "POLICY_POSITION"
    FACTUAL_CLAIM = "FACTUAL_CLAIM"
    DENIAL = "DENIAL"
    ACCUSATION = "ACCUSATION"
    CAMPAIGN_STATEMENT = "CAMPAIGN_STATEMENT"
    INTERVIEW_STATEMENT = "INTERVIEW_STATEMENT"
    PRESS_CONFERENCE = "PRESS_CONFERENCE"
    SOCIAL_MEDIA_POST = "SOCIAL_MEDIA_POST"
    OFFICIAL_STATEMENT = "OFFICIAL_STATEMENT"


class ComparisonStatus(str, Enum):
    CONSISTENT = "CONSISTENT"
    POSITION_CHANGED = "POSITION_CHANGED"
    APPARENT_CONTRADICTION = "APPARENT_CONTRADICTION"
    CONTEXT_CHANGED = "CONTEXT_CHANGED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class CopyrightType(str, Enum):
    US_GOVERNMENT_WORK = "US_GOVERNMENT_WORK"
    LICENSED = "LICENSED"
    FAIR_USE_COMMENTARY = "FAIR_USE_COMMENTARY"
    UNKNOWN = "UNKNOWN"


class SourceTier(str, Enum):
    """Section 11. A = primary government record … E = lead-only."""

    SOURCE_A = "SOURCE_A"
    SOURCE_B = "SOURCE_B"
    SOURCE_C = "SOURCE_C"
    SOURCE_D = "SOURCE_D"
    SOURCE_E = "SOURCE_E"


class FactCheckStatus(str, Enum):
    NONE = "NONE"  # no third-party check found
    PENDING = "PENDING"  # searched, not yet reviewed
    RATED = "RATED"  # a recognised fact-checker published a rating


class DownloadStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    DOWNLOADED = "DOWNLOADED"
    FAILED = "FAILED"
    NOT_ALLOWED = "NOT_ALLOWED"


class ArchiveStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    PUBLISHED = "PUBLISHED"


# Words the system itself must never use as a verdict (Section 5 / 13).
FORBIDDEN_VERDICT_TERMS: tuple[str, ...] = (
    "LIAR",
    "LIE",
    "LIED",
    "LIES",
    "LYING",
    "DISHONEST",
    "撒谎",
    "说谎",
    "骗子",
    "骗了",
    "谎言",
    "撒謊",
    "說謊",
    "騙子",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class PoliticalClaim:
    """One row of ``political_claims`` (Section 3)."""

    person_name: str
    topic: str
    statement_date: str  # ISO date, YYYY-MM-DD
    statement_text_original: str
    source_name: str

    claim_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    person_id: str = ""
    party: str = ""
    office: str = ""
    subtopic: str = ""

    statement_text_zh: str = ""

    source_type: str = SourceTier.SOURCE_E.value
    source_url: str = ""

    video_url: str = ""
    video_start: float | None = None  # seconds
    video_end: float | None = None

    transcript_verified: bool = False

    context_before: str = ""
    context_after: str = ""

    claim_type: str = ClaimType.OFFICIAL_STATEMENT.value

    related_claim_id: str | None = None

    comparison_status: str = ComparisonStatus.INSUFFICIENT_EVIDENCE.value

    fact_check_status: str = FactCheckStatus.NONE.value
    fact_check_source: str = ""
    fact_check_url: str = ""
    fact_check_rating: str = ""

    copyright_owner: str = "Unknown"
    copyright_type: str = CopyrightType.UNKNOWN.value
    government_work: bool = False
    fair_use_purpose: str = ""

    download_status: str = DownloadStatus.NOT_STARTED.value
    archive_status: str = ArchiveStatus.CANDIDATE.value

    confidence: float = 0.0

    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        # Normalise enum inputs to their string values and validate them.
        for name, enum_cls in _ENUM_FIELDS.items():
            value = getattr(self, name)
            if isinstance(value, Enum):
                value = value.value
            enum_cls(value)  # raises ValueError on an unknown value
            setattr(self, name, value)
        if not self.person_id:
            self.person_id = person_slug(self.person_name)
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be within [0, 1]")

    @property
    def clip_duration(self) -> float | None:
        if self.video_start is None or self.video_end is None:
            return None
        return self.video_end - self.video_start

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["transcript_verified"] = int(self.transcript_verified)
        row["government_work"] = int(self.government_work)
        return row

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PoliticalClaim":
        names = {f.name for f in fields(cls)}
        data = {k: row[k] for k in row.keys() if k in names}
        data["transcript_verified"] = bool(data.get("transcript_verified"))
        data["government_work"] = bool(data.get("government_work"))
        return cls(**data)


_ENUM_FIELDS: dict[str, type[Enum]] = {
    "claim_type": ClaimType,
    "comparison_status": ComparisonStatus,
    "copyright_type": CopyrightType,
    "source_type": SourceTier,
    "fact_check_status": FactCheckStatus,
    "download_status": DownloadStatus,
    "archive_status": ArchiveStatus,
}


def person_slug(name: str) -> str:
    return "-".join("".join(c.lower() if c.isalnum() else " " for c in name).split())


@dataclass
class NewsEvent:
    """A clustered hot event handed over by the existing StatevergeCN pipeline."""

    event_id: str
    title: str
    people: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    event_date: str = ""  # ISO date; defaults to today when empty


@dataclass
class ContextReport:
    """Result of the Section 9 anti-out-of-context check."""

    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    context_available: bool = False

    @property
    def context_changed(self) -> bool:
        return bool(self.flags)


@dataclass
class ComparisonResult:
    earlier: PoliticalClaim
    later: PoliticalClaim
    status: ComparisonStatus
    reasons: list[str] = field(default_factory=list)
    context: ContextReport | None = None

    @property
    def months_apart(self) -> int:
        a = datetime.fromisoformat(self.earlier.statement_date)
        b = datetime.fromisoformat(self.later.statement_date)
        return (b.year - a.year) * 12 + (b.month - a.month)


@dataclass
class Evidence:
    """Section 12. role: ORIGINAL / FOLLOW_UP / FACT_CHECK."""

    role: str
    description: str
    source_name: str
    source_url: str
    source_tier: str = SourceTier.SOURCE_E.value
    date: str = ""
