"""Verbatim transcript verification and context extraction (Sections 3, 9).

A quote is only ``transcript_verified`` when it appears word-for-word in a
transcript of the original source. Matching ignores case, punctuation and
whitespace, but never paraphrase.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from .models import PoliticalClaim

_WORD = re.compile(r"[\w']+", re.UNICODE)
_CJK = re.compile(r"[㐀-鿿]")


@dataclass
class Segment:
    """A timed transcript segment (seconds)."""

    start: float
    end: float
    text: str


@dataclass
class TranscriptMatch:
    verified: bool
    matched_text: str = ""
    context_before: str = ""
    context_after: str = ""
    video_start: float | None = None
    video_end: float | None = None


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    out: list[str] = []
    for w in _WORD.findall(text):
        # CJK has no spaces: treat each character as a token.
        if _CJK.search(w):
            out.extend(ch for ch in w)
        else:
            out.append(w.strip("'"))
    return [t for t in out if t]


def _find(needle: list[str], hay: list[str]) -> int:
    n = len(needle)
    if n == 0:
        return -1
    for i in range(len(hay) - n + 1):
        if hay[i : i + n] == needle:
            return i
    return -1


def verify_quote(
    quote: str,
    transcript: str | Sequence[Segment],
    context_words: int = 80,
) -> TranscriptMatch:
    """Check that ``quote`` appears verbatim and pull surrounding context."""
    if isinstance(transcript, str):
        segments = [Segment(0.0, 0.0, transcript)]
        timed = False
    else:
        segments = list(transcript)
        timed = True

    # Flatten to tokens, remembering which segment each token came from.
    hay: list[str] = []
    owner: list[int] = []
    for idx, seg in enumerate(segments):
        toks = tokens(seg.text)
        hay.extend(toks)
        owner.extend([idx] * len(toks))

    needle = tokens(quote)
    pos = _find(needle, hay)
    if pos < 0:
        return TranscriptMatch(verified=False)

    end = pos + len(needle)
    joiner = "" if _CJK.search(quote) else " "
    match = TranscriptMatch(
        verified=True,
        matched_text=joiner.join(hay[pos:end]),
        context_before=joiner.join(hay[max(0, pos - context_words) : pos]),
        context_after=joiner.join(hay[end : end + context_words]),
    )
    if timed:
        match.video_start = segments[owner[pos]].start
        match.video_end = segments[owner[end - 1]].end
    return match


def apply(claim: PoliticalClaim, transcript: str | Sequence[Segment], context_words: int = 80) -> TranscriptMatch:
    """Verify ``claim.statement_text_original`` against a transcript and store
    the verification flag, context and (for timed transcripts) clip bounds."""
    result = verify_quote(claim.statement_text_original, transcript, context_words)
    claim.transcript_verified = result.verified
    if result.verified:
        claim.context_before = result.context_before
        claim.context_after = result.context_after
        if result.video_start is not None and claim.video_start is None:
            claim.video_start = result.video_start
            claim.video_end = result.video_end
    return result
