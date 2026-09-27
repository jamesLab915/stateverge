"""Third-party fact checks (Sections 5, 12).

A rating is only ever reported as *attributed* to the organisation that
published it — "FactCheck.org 将该说法评为……" — never as Stateverge's own verdict.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .models import Evidence, FactCheckStatus, PoliticalClaim, SourceTier

# Organisation name -> canonical host.
RECOGNISED_FACT_CHECKERS: dict[str, str] = {
    "FactCheck.org": "factcheck.org",
    "PolitiFact": "politifact.com",
    "AP Fact Check": "apnews.com",
    "Reuters Fact Check": "reuters.com",
    "The Washington Post Fact Checker": "washingtonpost.com",
    "AFP Fact Check": "factcheck.afp.com",
    "CNN Facts First": "cnn.com",
    "Snopes": "snopes.com",
}


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def canonical_checker(name: str) -> str | None:
    n = name.strip().lower()
    for org in RECOGNISED_FACT_CHECKERS:
        if org.lower() == n:
            return org
    return None


def record_fact_check(claim: PoliticalClaim, source: str, url: str, rating: str) -> None:
    """Attach a published rating. Rejects unknown organisations and URLs that
    are not on the organisation's own site."""
    org = canonical_checker(source)
    if org is None:
        raise ValueError(f"{source!r} is not a recognised fact-checking organisation")
    host = _host(url)
    expected = RECOGNISED_FACT_CHECKERS[org]
    if not (host == expected or host.endswith("." + expected)):
        raise ValueError(f"fact-check URL must be on {expected}, got {host or url!r}")
    if not rating.strip():
        raise ValueError("rating text is required")
    claim.fact_check_status = FactCheckStatus.RATED.value
    claim.fact_check_source = org
    claim.fact_check_url = url
    claim.fact_check_rating = rating.strip()


def mark_pending(claim: PoliticalClaim) -> None:
    if claim.fact_check_status != FactCheckStatus.RATED.value:
        claim.fact_check_status = FactCheckStatus.PENDING.value


def attribution_sentence(claim: PoliticalClaim) -> str | None:
    """The only allowed way to put a rating into copy."""
    if claim.fact_check_status != FactCheckStatus.RATED.value:
        return None
    return f"{claim.fact_check_source} 将该说法评为「{claim.fact_check_rating}」。"


def as_evidence(claim: PoliticalClaim) -> Evidence | None:
    if claim.fact_check_status != FactCheckStatus.RATED.value:
        return None
    return Evidence(
        role="FACT_CHECK",
        description=attribution_sentence(claim) or "",
        source_name=claim.fact_check_source,
        source_url=claim.fact_check_url,
        source_tier=SourceTier.SOURCE_B.value,
    )
