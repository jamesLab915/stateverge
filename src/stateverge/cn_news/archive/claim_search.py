"""History search for a hot event (Sections 2D, 10).

When a person becomes news, find what they said about the same issue over
the past 10 years (by default), from the local archive plus any pluggable
external providers, then dedupe and sort chronologically.

External providers are how the existing StatevergeCN collectors (C-SPAN,
White House, wire services …) feed the archive; results from them are
stored as CANDIDATE claims and must still pass source/transcript/context
verification before any comparison is published.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Protocol

from .claim_matcher import dedupe
from .database import ArchiveDB
from .models import NewsEvent, PoliticalClaim, person_slug
from .source_resolver import resolve_claim
from .transcript import tokens

DEFAULT_LOOKBACK_YEARS = 10

# Issue -> related search terms ("Trump + Iran" also searches war, troops, …).
TOPIC_EXPANSIONS: dict[str, list[str]] = {
    "iran": ["iran", "war", "military action", "regime change", "troops", "ceasefire", "nuclear", "sanctions", "伊朗"],
    "china": ["china", "tariff", "tariffs", "trade war", "taiwan", "xi jinping", "中国"],
    "taiwan": ["taiwan", "china", "defense", "arms sales", "台湾", "台灣"],
    "ukraine": ["ukraine", "russia", "putin", "zelensky", "aid", "ceasefire", "nato", "乌克兰"],
    "russia": ["russia", "putin", "sanctions", "ukraine", "nato", "俄罗斯"],
    "israel": ["israel", "gaza", "hamas", "ceasefire", "netanyahu", "以色列"],
    "tariffs": ["tariff", "tariffs", "trade deal", "trade war", "import tax", "关税"],
    "immigration": ["immigration", "border", "deportation", "asylum", "wall", "移民"],
    "economy": ["economy", "inflation", "jobs", "recession", "prices", "经济"],
    "healthcare": ["healthcare", "obamacare", "affordable care act", "medicare", "medicaid", "医保"],
}


class SearchProvider(Protocol):
    name: str

    def search(self, person_name: str, keyword: str, since: str, until: str) -> list[PoliticalClaim]:
        ...


@dataclass
class Query:
    person_name: str
    keyword: str

    def __str__(self) -> str:
        return f"{self.person_name} + {self.keyword}"


def expand_keywords(event: NewsEvent) -> list[str]:
    terms: list[str] = []
    for raw in [*event.topics, *event.countries, *event.keywords]:
        key = raw.strip().lower()
        if not key:
            continue
        terms.append(key)
        terms.extend(TOPIC_EXPANSIONS.get(key, []))
    seen: set[str] = set()
    return [t for t in terms if not (t in seen or seen.add(t))]


def build_queries(event: NewsEvent) -> list[Query]:
    kws = expand_keywords(event)
    return [Query(p, k) for p in event.people for k in kws]


def _window(event: NewsEvent, years: int) -> tuple[str, str]:
    end = date.fromisoformat(event.event_date) if event.event_date else date.today()
    try:
        start = end.replace(year=end.year - years)
    except ValueError:  # 29 Feb
        start = end.replace(year=end.year - years, day=28)
    return start.isoformat(), end.isoformat()


def mentions(claim: PoliticalClaim, keyword: str) -> bool:
    """Whole-word match (so "war" does not match "award")."""
    kw = tokens(keyword)
    if not kw:
        return False
    for field_text in (claim.topic, claim.subtopic, claim.statement_text_original, claim.statement_text_zh):
        hay = tokens(field_text)
        for i in range(len(hay) - len(kw) + 1):
            if hay[i : i + len(kw)] == kw:
                return True
    return False


def search_history(
    event: NewsEvent,
    db: ArchiveDB,
    providers: Iterable[SearchProvider] = (),
    years: int = DEFAULT_LOOKBACK_YEARS,
    store_candidates: bool = True,
) -> dict[str, list[PoliticalClaim]]:
    """Return {person_name: chronologically sorted, deduplicated claims}."""
    since, until = _window(event, years)
    keywords = expand_keywords(event)
    providers = list(providers)
    out: dict[str, list[PoliticalClaim]] = {}

    for person in event.people:
        pid = person_slug(person)
        found = [
            c
            for c in db.search(person_id=pid, keywords=keywords, since=since, until=until)
            if any(mentions(c, k) for k in keywords)
        ]
        for provider in providers:
            for kw in keywords:
                for claim in provider.search(person, kw, since, until):
                    if claim.person_id != pid or not (since <= claim.statement_date <= until):
                        continue
                    resolve_claim(claim)
                    # Providers tag hits with the search keyword; file them under the event's issue.
                    if event.topics and not claim.subtopic:
                        claim.topic = event.topics[0]
                    if store_candidates:
                        db.upsert(claim)
                    found.append(claim)
        out[person] = dedupe(found)
    return out
