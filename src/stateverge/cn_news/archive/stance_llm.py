"""LLM stance judge for ``contradiction.compare(stance_judge=...)``.

Reads both statements *with their full saved context* and answers only
SAME / SHIFTED / OPPOSITE / UNCLEAR. It is never asked whether anyone lied,
and any failure (network, bad JSON, unknown label) falls back to UNCLEAR,
which ``compare`` turns into INSUFFICIENT_EVIDENCE — the safe direction.

Uses the OpenAI Chat Completions API like the rest of StateVerge
(``OPENAI_API_KEY``; model from ``ARCHIVE_STANCE_MODEL``, default gpt-4o-mini).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

from .models import PoliticalClaim

API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"
VALID = ("SAME", "SHIFTED", "OPPOSITE", "UNCLEAR")

SYSTEM_PROMPT = """You compare two statements by the same politician on the same issue for a neutral archive.
Decide ONLY whether the speaker's position changed. Read each statement together with its context.

Labels:
- SAME: the position is the same, even if worded differently.
- SHIFTED: the position moved (softened, hardened, added conditions) but is not a flat reversal.
- OPPOSITE: the later statement asserts the opposite of the earlier one.
- UNCLEAR: context is missing, the statements answer different questions, one is hypothetical, sarcastic,
  or quoting someone else, or you are not sure.

Rules:
- Different wording alone is never a change.
- A prediction that did not come true is not a reversal of position.
- Never judge honesty. Do not use words like lie, liar or dishonest.
- When in doubt, answer UNCLEAR.

Reply with JSON only: {"stance": "SAME|SHIFTED|OPPOSITE|UNCLEAR", "reason": "<one sentence>"}"""

Post = Callable[[str, dict, bytes], bytes]


def _http(url: str, headers: dict, body: bytes) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _describe(label: str, c: PoliticalClaim) -> str:
    return (
        f"{label} ({c.statement_date}, {c.claim_type}, source: {c.source_name})\n"
        f"Context before: {c.context_before[-1500:] or '(none)'}\n"
        f"STATEMENT: {c.statement_text_original}\n"
        f"Context after: {c.context_after[:800] or '(none)'}"
    )


class LLMStanceJudge:
    """Callable ``(earlier, later) -> stance``; ``last_reason`` keeps the model's note."""

    def __init__(self, api_key: str | None = None, model: str | None = None, post: Post | None = None) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("ARCHIVE_STANCE_MODEL") or DEFAULT_MODEL
        self.post = post or _http
        self.last_reason = ""

    def __call__(self, earlier: PoliticalClaim, later: PoliticalClaim) -> str:
        self.last_reason = ""
        if not self.api_key:
            self.last_reason = "OPENAI_API_KEY not set"
            return "UNCLEAR"
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Issue: {earlier.topic} / {earlier.subtopic or '-'}\n\n"
                        + _describe("EARLIER", earlier)
                        + "\n\n"
                        + _describe("LATER", later),
                    },
                ],
            }
        ).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            raw = json.loads(self.post(API_URL, headers, body))
            answer = json.loads(raw["choices"][0]["message"]["content"])
            stance = str(answer.get("stance", "")).upper().strip()
            self.last_reason = str(answer.get("reason", ""))[:300]
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as e:
            self.last_reason = f"stance judge failed: {type(e).__name__}"
            return "UNCLEAR"
        return stance if stance in VALID else "UNCLEAR"
