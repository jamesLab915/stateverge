"""StatevergeCN Political Archive v1 — 政治原话档案.

录像不会失忆。Stateverge stores who said what, when, what they said later,
and what happened — and leaves the verdict to the viewer.
"""

from .database import ArchiveDB
from .models import (
    ClaimType,
    ComparisonStatus,
    CopyrightType,
    NewsEvent,
    PoliticalClaim,
    SourceTier,
)
from .pipeline import run_archive_stage

__all__ = [
    "ArchiveDB",
    "ClaimType",
    "ComparisonStatus",
    "CopyrightType",
    "NewsEvent",
    "PoliticalClaim",
    "SourceTier",
    "run_archive_stage",
]
