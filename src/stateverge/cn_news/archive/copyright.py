"""Footage copyright bookkeeping (Section 6).

This records *who owns* a clip and *on what basis* Stateverge uses it. It is
bookkeeping, not legal advice: FAIR_USE_COMMENTARY is only assigned when the
caller supplies a commentary purpose, and UNKNOWN always blocks publishing.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .models import CopyrightType, PoliticalClaim

# Federal hosts whose own recordings are US government works (17 U.S.C. §105).
# State/local .gov sites are *not* automatically public domain.
_FEDERAL_HOSTS: dict[str, str] = {
    "whitehouse.gov": "White House",
    "congress.gov": "Congress",
    "senate.gov": "U.S. Senate",
    "house.gov": "U.S. House",
    "state.gov": "U.S. Department of State",
    "defense.gov": "U.S. Department of Defense",
    "dvidshub.net": "U.S. Department of Defense (DVIDS)",
    "uscourts.gov": "U.S. Courts",
    "supremecourt.gov": "U.S. Supreme Court",
    "govinfo.gov": "U.S. Government Publishing Office",
    "archives.gov": "National Archives",
}

_OWNER_HOSTS: dict[str, str] = {
    "c-span.org": "C-SPAN",
    "cnn.com": "CNN",
    "foxnews.com": "Fox News",
    "nbcnews.com": "NBC",
    "abcnews.go.com": "ABC",
    "cbsnews.com": "CBS",
    "reuters.com": "Reuters",
    "apnews.com": "AP",
    "gettyimages.com": "Getty",
    "bbc.com": "BBC",
    "bbc.co.uk": "BBC",
    "nytimes.com": "New York Times",
    "washingtonpost.com": "Washington Post",
    "wsj.com": "Wall Street Journal",
}


@dataclass
class CopyrightAssessment:
    owner: str
    copyright_type: CopyrightType
    government_work: bool
    publishable: bool
    note: str


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower() if url else ""
    return host[4:] if host.startswith("www.") else host


def _match(host: str, table: dict[str, str]) -> str | None:
    for suffix, owner in table.items():
        if host == suffix or host.endswith("." + suffix):
            return owner
    return None


def assess(
    url: str,
    source_name: str = "",
    licensed_from: str | None = None,
    fair_use_purpose: str = "",
    campaign_material: bool = False,
) -> CopyrightAssessment:
    host = _host(url)

    federal = _match(host, _FEDERAL_HOSTS)
    if federal:
        return CopyrightAssessment(
            federal, CopyrightType.US_GOVERNMENT_WORK, True, True,
            "US federal government work; still credit the source",
        )

    owner = _match(host, _OWNER_HOSTS) or (
        "Campaign" if campaign_material else (source_name.strip() or "Unknown")
    )
    if host.endswith(".gov"):
        owner = source_name.strip() or host
        note_prefix = "non-federal .gov — public-domain status must be checked; "
    else:
        note_prefix = ""

    if licensed_from:
        return CopyrightAssessment(
            licensed_from, CopyrightType.LICENSED, False, True, note_prefix + "licensed footage"
        )
    if fair_use_purpose.strip() and owner != "Unknown":
        return CopyrightAssessment(
            owner, CopyrightType.FAIR_USE_COMMENTARY, False, True,
            note_prefix + "fair-use commentary: short excerpt plus transformative commentary required",
        )
    return CopyrightAssessment(
        owner, CopyrightType.UNKNOWN, False, False,
        note_prefix + "copyright basis unknown — BLOCK until owner and basis are recorded",
    )


def apply(claim: PoliticalClaim, **kwargs) -> CopyrightAssessment:
    """Assess the claim's footage (video_url, else source_url) and store the result."""
    kwargs.setdefault("fair_use_purpose", claim.fair_use_purpose)
    result = assess(claim.video_url or claim.source_url, claim.source_name, **kwargs)
    claim.copyright_owner = result.owner
    claim.copyright_type = result.copyright_type.value
    claim.government_work = result.government_work
    return result


def is_copyright_recorded(claim: PoliticalClaim) -> bool:
    if claim.copyright_type == CopyrightType.UNKNOWN.value:
        return False
    if not claim.copyright_owner or claim.copyright_owner == "Unknown":
        return False
    if claim.copyright_type == CopyrightType.FAIR_USE_COMMENTARY.value and not claim.fair_use_purpose.strip():
        return False
    return True
