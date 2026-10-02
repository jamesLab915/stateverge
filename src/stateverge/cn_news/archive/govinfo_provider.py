"""GovInfo collector — official US government transcripts (Tier A, public domain).

Collections used:
* CPD  — Compilation of Presidential Documents (remarks, press conferences,
         interviews, statements) — speaker is the sitting president.
* CREC — Congressional Record (floor speeches) — speaker named in the text.

API: https://api.govinfo.gov (key from https://api.data.gov/signup, env
GOVINFO_API_KEY; ``DEMO_KEY`` works for light testing).

Extraction is deterministic: the provider never paraphrases. It splits the
official text into speaker turns, keeps only the target speaker's turns,
and returns each sentence mentioning the keyword *verbatim*. The full text
is then used as the transcript, so quotes come back transcript-verified with
the preceding question as context. Everything is stored as a CANDIDATE for
human review — a keyword hit is not a position.

GovInfo publishes with a lag of days to weeks; very recent remarks will
not be there yet.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from . import copyright, transcript
from .claim_search import mentions
from .importer import stable_id
from .models import ArchiveStatus, ClaimType, PoliticalClaim, person_slug
from .source_resolver import resolve_claim

API = "https://api.govinfo.gov"
DETAILS = "https://www.govinfo.gov/app/details"

# (name, term start, term end-exclusive). Used to attribute CPD documents.
PRESIDENTS: list[tuple[str, str, str]] = [
    ("Barack Obama", "2009-01-20", "2017-01-20"),
    ("Donald Trump", "2017-01-20", "2021-01-20"),
    ("Joe Biden", "2021-01-20", "2025-01-20"),
    ("Donald Trump", "2025-01-20", "9999-12-31"),
]

Fetch = Callable[[str, bytes | None], bytes]


def _http(url: str, body: bytes | None) -> bytes:
    headers = {"Accept": "application/json", "User-Agent": "StatevergeArchive/1.0"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def president_terms(person_name: str) -> list[tuple[str, str]]:
    slug = person_slug(person_name)
    return [(s, e) for n, s, e in PRESIDENTS if person_slug(n) == slug]


# -- text parsing -------------------------------------------------------------

_PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
# "Q.", "The President.", "Senator Smith.", "Mr. SMITH.", "Prime Minister Doe."
_TURN = re.compile(
    r"^(Q\.|The President\.|The Vice President\.|(?:Mr|Mrs|Ms|Dr)\. [A-Z][A-Za-z'\-]+\.|"
    r"(?:Senator|Representative|Secretary|Prime Minister|President|Chancellor|Press Secretary)"
    r"(?: [A-Z][A-Za-z'\-]+){1,3}\.)\s+"
)
_SENT = re.compile(r"(?<=[.!?])[\"”’)]?\s+(?=[A-Z\"“])")


def html_to_text(raw: str) -> str:
    m = _PRE.search(raw)
    body = m.group(1) if m else raw
    text = html.unescape(_TAG.sub("", body))
    # Re-flow hard-wrapped lines into paragraphs.
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n|\n(?=\s{2,}\S)", text)]
    return "\n\n".join(p for p in paras if p)


@dataclass
class Turn:
    speaker: str
    text: str


def split_turns(text: str, default_speaker: str) -> list[Turn]:
    """Paragraphs opening with a speaker label start a new turn; others continue it."""
    turns: list[Turn] = []
    speaker = default_speaker
    for para in text.split("\n\n"):
        m = _TURN.match(para)
        if m:
            speaker = m.group(1).rstrip(".")
            para = para[m.end():]
        if turns and turns[-1].speaker == speaker and not m:
            turns[-1].text += " " + para
        else:
            turns.append(Turn(speaker, para))
    return turns


def _is_target(turn_speaker: str, person_name: str, is_president: bool) -> bool:
    if is_president and turn_speaker in ("The President", "__DOC__"):
        return True
    last = person_name.split()[-1].lower()
    return last in turn_speaker.lower() and turn_speaker != "Q"


_COMPLETE = re.compile(r"[.!?][\"”’)]?$")


def sentences(text: str, min_words: int = 5) -> list[str]:
    """Complete spoken sentences only — header lines and fragments are dropped."""
    out = []
    for s in _SENT.split(text):
        s = s.strip()
        if _COMPLETE.search(s) and len(s.split()) >= min_words:
            out.append(s)
    return out


def classify(title: str) -> str:
    t = title.lower()
    if "news conference" in t or "press conference" in t or "exchange with reporters" in t:
        return ClaimType.PRESS_CONFERENCE.value
    if "interview" in t:
        return ClaimType.INTERVIEW_STATEMENT.value
    if t.startswith("statement") or "statement on" in t:
        return ClaimType.OFFICIAL_STATEMENT.value
    if "rally" in t or "campaign" in t:
        return ClaimType.CAMPAIGN_STATEMENT.value
    return ClaimType.OFFICIAL_STATEMENT.value


# -- provider -----------------------------------------------------------------


class GovInfoProvider:
    """``SearchProvider`` for claim_search.search_history."""

    name = "govinfo"

    def __init__(
        self,
        api_key: str | None = None,
        fetch: Fetch | None = None,
        page_size: int = 20,
        max_quotes_per_doc: int = 3,
    ) -> None:
        self.api_key = (api_key or os.environ.get("GOVINFO_API_KEY", "")).strip() or "DEMO_KEY"
        self.fetch = fetch or _http
        self.page_size = page_size
        self.max_quotes_per_doc = max_quotes_per_doc

    def _url(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        return f"{API}{path}{sep}api_key={urllib.parse.quote(self.api_key)}"

    def build_query(self, person_name: str, keyword: str, since: str, until: str, president: bool) -> str:
        kw = keyword.replace('"', "")
        if president:
            return f'collection:(CPD) AND "{kw}" AND publishdate:range({since},{until})'
        last = person_name.split()[-1]
        return f'collection:(CREC) AND "{kw}" AND "{last}" AND publishdate:range({since},{until})'

    def _search(self, query: str) -> list[dict]:
        body = json.dumps(
            {
                "query": query,
                "pageSize": self.page_size,
                "offsetMark": "*",
                "sorts": [{"field": "publishdate", "sortOrder": "DESC"}],
            }
        ).encode()
        data = json.loads(self.fetch(self._url("/search"), body))
        return data.get("results", [])

    def _document(self, result: dict) -> str:
        pkg, gran = result.get("packageId", ""), result.get("granuleId")
        path = f"/packages/{pkg}/granules/{gran}/htm" if gran else f"/packages/{pkg}/htm"
        return html_to_text(self.fetch(self._url(path), None).decode("utf-8", errors="replace"))

    def search(self, person_name: str, keyword: str, since: str, until: str) -> list[PoliticalClaim]:
        terms = president_terms(person_name)
        is_president = bool(terms)
        try:
            results = self._search(self.build_query(person_name, keyword, since, until, is_president))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return []

        claims: list[PoliticalClaim] = []
        for r in results:
            day = (r.get("dateIssued") or "")[:10]
            if not day:
                continue
            # CPD documents belong to whoever was president that day.
            if is_president and not any(s <= day < e for s, e in terms):
                continue
            try:
                text = self._document(r)
            except (urllib.error.URLError, TimeoutError):
                continue
            claims += self.extract(person_name, keyword, text, r, day, is_president)
        return claims

    def extract(
        self, person_name: str, keyword: str, text: str, result: dict, day: str, is_president: bool
    ) -> list[PoliticalClaim]:
        title = result.get("title", "")
        pkg = result.get("packageId", "")
        link = f"{DETAILS}/{pkg}" + (f"/{result['granuleId']}" if result.get("granuleId") else "")
        default = "__DOC__" if is_president else "__UNKNOWN__"
        out: list[PoliticalClaim] = []
        for turn in split_turns(text, default):
            if not _is_target(turn.speaker, person_name, is_president):
                continue
            for sent in sentences(turn.text):
                if len(out) >= self.max_quotes_per_doc:
                    return out
                probe = PoliticalClaim(person_name, keyword, day, sent, "GovInfo")
                if not mentions(probe, keyword):
                    continue
                claim = PoliticalClaim(
                    claim_id=stable_id(person_name, day, sent),
                    person_name=person_name,
                    topic=keyword,
                    statement_date=day,
                    statement_text_original=sent,
                    source_name=f"GovInfo {result.get('collectionCode', '')}: {title}".strip(),
                    source_url=link,
                    claim_type=classify(title),
                    archive_status=ArchiveStatus.CANDIDATE,
                )
                resolve_claim(claim)
                copyright.apply(claim)
                transcript.apply(claim, text)
                claim.confidence = 0.6 if claim.transcript_verified else 0.2
                out.append(claim)
        return out

