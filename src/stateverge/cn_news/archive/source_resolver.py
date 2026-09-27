"""Source tiers (Section 11) and footage priority (Section 7).

Tier E material (reposts, anonymous accounts, re-uploads) may be used to
*discover* a statement but never as the final evidence: the resolver marks
it as needing an upstream original.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .models import PoliticalClaim, SourceTier

# Domain suffix -> tier. Longest suffix wins.
_DOMAIN_TIERS: dict[str, SourceTier] = {
    # A: government / primary records
    "whitehouse.gov": SourceTier.SOURCE_A,
    "congress.gov": SourceTier.SOURCE_A,
    "senate.gov": SourceTier.SOURCE_A,
    "house.gov": SourceTier.SOURCE_A,
    "state.gov": SourceTier.SOURCE_A,
    "defense.gov": SourceTier.SOURCE_A,
    "uscourts.gov": SourceTier.SOURCE_A,
    "supremecourt.gov": SourceTier.SOURCE_A,
    "govinfo.gov": SourceTier.SOURCE_A,
    "presidency.ucsb.edu": SourceTier.SOURCE_A,
    "c-span.org": SourceTier.SOURCE_A,
    "gov": SourceTier.SOURCE_A,
    "mil": SourceTier.SOURCE_A,
    # B: wire services / public broadcasters
    "reuters.com": SourceTier.SOURCE_B,
    "apnews.com": SourceTier.SOURCE_B,
    "afp.com": SourceTier.SOURCE_B,
    "bbc.com": SourceTier.SOURCE_B,
    "bbc.co.uk": SourceTier.SOURCE_B,
    # C: major outlets
    "cnn.com": SourceTier.SOURCE_C,
    "foxnews.com": SourceTier.SOURCE_C,
    "nbcnews.com": SourceTier.SOURCE_C,
    "cbsnews.com": SourceTier.SOURCE_C,
    "abcnews.go.com": SourceTier.SOURCE_C,
    "nytimes.com": SourceTier.SOURCE_C,
    "washingtonpost.com": SourceTier.SOURCE_C,
    "wsj.com": SourceTier.SOURCE_C,
    # E: platforms where the uploader is usually not the speaker
    "tiktok.com": SourceTier.SOURCE_E,
    "x.com": SourceTier.SOURCE_E,
    "twitter.com": SourceTier.SOURCE_E,
    "reddit.com": SourceTier.SOURCE_E,
    "youtube.com": SourceTier.SOURCE_E,
    "youtu.be": SourceTier.SOURCE_E,
    "facebook.com": SourceTier.SOURCE_E,
    "instagram.com": SourceTier.SOURCE_E,
    "truthsocial.com": SourceTier.SOURCE_E,
}

_NAME_TIERS: dict[str, SourceTier] = {
    "white house": SourceTier.SOURCE_A,
    "congress": SourceTier.SOURCE_A,
    "c-span": SourceTier.SOURCE_A,
    "cspan": SourceTier.SOURCE_A,
    "court": SourceTier.SOURCE_A,
    "reuters": SourceTier.SOURCE_B,
    "associated press": SourceTier.SOURCE_B,
    "ap": SourceTier.SOURCE_B,
    "afp": SourceTier.SOURCE_B,
    "bbc": SourceTier.SOURCE_B,
    "cnn": SourceTier.SOURCE_C,
    "fox news": SourceTier.SOURCE_C,
    "fox": SourceTier.SOURCE_C,
    "nbc": SourceTier.SOURCE_C,
    "cbs": SourceTier.SOURCE_C,
    "abc": SourceTier.SOURCE_C,
    "new york times": SourceTier.SOURCE_C,
    "nyt": SourceTier.SOURCE_C,
    "washington post": SourceTier.SOURCE_C,
    "wsj": SourceTier.SOURCE_C,
    "wall street journal": SourceTier.SOURCE_C,
}

# Section 7: lower number = preferred footage.
FOOTAGE_PRIORITY: dict[str, int] = {
    "GOVERNMENT_OFFICIAL_VIDEO": 1,
    "CSPAN_OR_FULL_PRESSER": 2,
    "PERSON_OFFICIAL_ACCOUNT": 3,
    "CAMPAIGN_OFFICIAL_VIDEO": 4,
    "TV_INTERVIEW_ORIGINAL": 5,
    "MEDIA_EDITED_CLIP": 6,
    "SOCIAL_REPOST": 7,
}


@dataclass
class SourceResolution:
    tier: SourceTier
    final_evidence_eligible: bool
    needs_original: bool
    reason: str


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def tier_for_url(url: str) -> SourceTier | None:
    host = _host(url)
    if not host:
        return None
    best: tuple[int, SourceTier] | None = None
    for suffix, tier in _DOMAIN_TIERS.items():
        if host == suffix or host.endswith("." + suffix):
            if best is None or len(suffix) > best[0]:
                best = (len(suffix), tier)
    return best[1] if best else None


def tier_for_name(name: str) -> SourceTier | None:
    n = f" {name.lower().strip()} "
    for key in sorted(_NAME_TIERS, key=len, reverse=True):
        if f" {key} " in n or n.strip() == key:
            return _NAME_TIERS[key]
    return None


def classify_source(
    source_name: str,
    url: str = "",
    official_account_of_speaker: bool = False,
) -> SourceResolution:
    """Tier a source. ``official_account_of_speaker`` marks a verified account /
    campaign site of the speaker themself, which is Tier D even on a platform
    that is otherwise Tier E."""
    if official_account_of_speaker:
        return SourceResolution(SourceTier.SOURCE_D, True, False, "speaker's own official account")

    tier = tier_for_url(url) or tier_for_name(source_name) or SourceTier.SOURCE_E
    if tier is SourceTier.SOURCE_E:
        return SourceResolution(
            tier, False, True, "Tier E is a lead only — locate the original source"
        )
    return SourceResolution(tier, True, False, f"classified as {tier.value}")


def footage_priority(resolution: SourceResolution, is_full_record: bool = False) -> int:
    """Map a resolved source onto the Section 7 footage ladder."""
    if resolution.tier is SourceTier.SOURCE_A:
        return FOOTAGE_PRIORITY["CSPAN_OR_FULL_PRESSER" if is_full_record else "GOVERNMENT_OFFICIAL_VIDEO"]
    if resolution.tier is SourceTier.SOURCE_D:
        return FOOTAGE_PRIORITY["PERSON_OFFICIAL_ACCOUNT"]
    if resolution.tier in (SourceTier.SOURCE_B, SourceTier.SOURCE_C):
        return FOOTAGE_PRIORITY["TV_INTERVIEW_ORIGINAL" if is_full_record else "MEDIA_EDITED_CLIP"]
    return FOOTAGE_PRIORITY["SOCIAL_REPOST"]


def resolve_claim(claim: PoliticalClaim, official_account_of_speaker: bool = False) -> SourceResolution:
    """Classify a claim's source and write the tier back onto it."""
    res = classify_source(claim.source_name, claim.source_url or claim.video_url, official_account_of_speaker)
    claim.source_type = res.tier.value
    return res


def is_final_evidence(claim: PoliticalClaim) -> bool:
    return claim.source_type != SourceTier.SOURCE_E.value
