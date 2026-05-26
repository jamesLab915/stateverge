"""
OpenAI-backed writer service for the StateVerge Studio /create endpoint.

Reads from `.env`:
    OPENAI_API_KEY    (required)
    OPENAI_MODEL      (default: gpt-4o-mini)

A single LLM call returns a structured JSON payload:
    {
        "title":        "<refined title>",
        "narration":    "<paragraphed body>",
        "shot_prompts": [
            {"id": 1, "text_chunk": "...", "visual": "...", "duration_sec": 6.5},
            ...
        ]
    }

The service is responsible for:
    1. building the system + user prompt from (slug, title, length, lang, style)
    2. calling OpenAI with response_format=json_object
    3. validating the returned JSON shape
    4. archiving any existing brief/* artifacts
    5. writing
        topics/<slug>/brief/title.txt
        topics/<slug>/brief/narration_script.txt
        topics/<slug>/brief/shot_prompts.json

API keys NEVER leave this module — the dashboard route only sees structured
results + counts + paths.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import requests

from .paths import archive_existing, safe_subpath, safe_topic_dir

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"

# Length targets — purely advisory hints to the model. The model can deviate
# but the prompt is explicit about the target band.
LENGTH_TARGETS = {
    ("short", "zh"): {"chars": "500-800",   "shots": "8-14"},
    ("long",  "zh"): {"chars": "3000-4500", "shots": "30-60"},
    ("short", "en"): {"chars": "150-250 words",   "shots": "8-14"},
    ("long",  "en"): {"chars": "1500-2500 words", "shots": "30-60"},
}

STYLE_GUIDE = {
    "finance":     ("Data-driven, rational, references real companies/events, "
                    "no melodrama, deck-style framing"),
    "documentary": ("Calm, depth, StateVerge channel voice — rational, real-"
                    "world, neither academic nor news-anchor; documentary "
                    "feel"),
    "social":      ("Conversational, fast pacing, hook-first, line-broken for "
                    "rhythm; written for short-form social platforms"),
    "funny":       ("Witty, light, internet-savvy with deliberate contrast "
                    "and timing; never cynical"),
}


# ---------------------------------------------------------------------------
# Result shape returned to the dashboard route
# ---------------------------------------------------------------------------


@dataclass
class WriteResult:
    ok: bool
    topic: str
    title_path: str = ""
    narration_path: str = ""
    shot_prompts_path: str = ""
    title_chars: int = 0
    narration_chars: int = 0
    shot_count: int = 0
    archive_dir: str = ""
    model: str = ""
    duration_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_script(
    *,
    slug: str,
    title: str,
    length: str,            # "short" | "long"
    language: str,          # "zh" | "en"
    style: str,             # "finance" | "documentary" | "social" | "funny"
    repo_root: Path,
) -> WriteResult:
    """
    One-shot script generation. On success, files are written to
    topics/<slug>/brief/. On failure, an error WriteResult is returned and
    no files are written.
    """
    t0 = time.monotonic()

    # ---- validate args ----
    length = length.lower().strip()
    language = language.lower().strip()
    style = style.lower().strip()
    if length not in {"short", "long"}:
        return WriteResult(ok=False, topic=slug,
                           error=f"length must be 'short' or 'long', got {length!r}")
    if language not in {"zh", "en"}:
        return WriteResult(ok=False, topic=slug,
                           error=f"language must be 'zh' or 'en', got {language!r}")
    if style not in STYLE_GUIDE:
        return WriteResult(ok=False, topic=slug,
                           error=f"style must be one of {sorted(STYLE_GUIDE)}, got {style!r}")

    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return WriteResult(ok=False, topic=slug,
                           error="OPENAI_API_KEY missing in .env")

    model = (os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL).strip()

    try:
        topic_dir = safe_topic_dir(slug, repo_root)
    except ValueError as e:
        return WriteResult(ok=False, topic=slug, error=str(e))

    # ---- build prompts ----
    sys_msg, user_msg = _build_prompts(
        slug=slug, title=title, length=length, language=language, style=style
    )

    # ---- call OpenAI ----
    try:
        raw_content = _chat_complete(
            api_key=api_key,
            model=model,
            system=sys_msg,
            user=user_msg,
        )
    except requests.RequestException as e:
        return WriteResult(ok=False, topic=slug, model=model,
                           error=f"OpenAI HTTP error: {e}")
    except RuntimeError as e:
        return WriteResult(ok=False, topic=slug, model=model, error=str(e))

    # ---- parse + validate ----
    try:
        payload = _parse_payload(raw_content)
    except ValueError as e:
        return WriteResult(ok=False, topic=slug, model=model,
                           error=f"OpenAI response parse error: {e}")

    # ---- archive existing brief/* before writing ----
    brief_dir = safe_subpath(topic_dir, "brief")
    brief_dir.mkdir(parents=True, exist_ok=True)
    title_path = safe_subpath(brief_dir, "title.txt")
    narration_path = safe_subpath(brief_dir, "narration_script.txt")
    shots_path = safe_subpath(brief_dir, "shot_prompts.json")

    archive_dir = archive_existing(
        [title_path, narration_path, shots_path],
        archive_root=brief_dir / "archive",
    )

    # ---- write fresh artifacts (UTF-8, LF, no BOM) ----
    title_text = payload["title"].strip()
    narration_text = payload["narration"].strip() + "\n"
    shots_json = json.dumps(
        {"shots": payload["shot_prompts"]},
        ensure_ascii=False, indent=2,
    ) + "\n"

    title_path.write_text(title_text + "\n", encoding="utf-8")
    narration_path.write_text(narration_text, encoding="utf-8")
    shots_path.write_text(shots_json, encoding="utf-8")

    return WriteResult(
        ok=True,
        topic=slug,
        title_path=str(title_path.relative_to(repo_root)),
        narration_path=str(narration_path.relative_to(repo_root)),
        shot_prompts_path=str(shots_path.relative_to(repo_root)),
        title_chars=len(title_text),
        narration_chars=len(narration_text.strip()),
        shot_count=len(payload["shot_prompts"]),
        archive_dir=(
            str(archive_dir.relative_to(repo_root)) if archive_dir else ""
        ),
        model=model,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_prompts(
    *, slug: str, title: str, length: str, language: str, style: str,
) -> tuple[str, str]:
    target = LENGTH_TARGETS[(length, language)]
    style_hint = STYLE_GUIDE[style]
    lang_label = {"zh": "Simplified Chinese (中文)",
                  "en": "English"}[language]

    sys_msg = (
        "You are a senior creative director and head writer for the StateVerge "
        "channel. StateVerge produces structured, intellectually honest videos "
        "about how systems (markets, governments, technologies, civilisations) "
        "rise, strain, and collapse.\n"
        "Your output is ALWAYS a single JSON object that matches the schema "
        "described by the user — no preamble, no Markdown fences."
    )

    user_msg = (
        f"Generate the full creative package for ONE video.\n\n"
        f"INPUTS\n"
        f"  topic_slug : {slug}\n"
        f"  draft_title: {title or '(none — invent one)'}\n"
        f"  length     : {length}        (target ~{target['chars']})\n"
        f"  language   : {lang_label}\n"
        f"  style      : {style}        ({style_hint})\n"
        f"  shots      : ~{target['shots']} visual shots\n\n"
        f"OUTPUT JSON SCHEMA (return EXACTLY this shape, no extra keys):\n"
        f"{{\n"
        f'  "title":     "<one refined headline in {lang_label}>",\n'
        f'  "narration": "<full narration body in {lang_label}, paragraphs separated by a blank line>",\n'
        f'  "shot_prompts": [\n'
        f'    {{\n'
        f'      "id": 1,\n'
        f'      "text_chunk": "<the narration line that pairs with this shot, in {lang_label}>",\n'
        f'      "visual":     "<one-sentence English visual description for Runway/LTX (camera language, lighting, mood)>",\n'
        f'      "duration_sec": <number, 4-9>\n'
        f'    }},\n'
        f'    ...\n'
        f'  ]\n'
        f'}}\n\n'
        f"RULES\n"
        f"  - Title is one line, no quotes around it.\n"
        f"  - Narration is plain paragraphs (no Markdown, no headings, no bullets).\n"
        f"  - shot_prompts.text_chunk MUST be an exact substring of narration when "
        f"possible (so the editor can align cues 1:1).\n"
        f"  - shot_prompts.visual is always English even when language is zh — it "
        f"feeds Runway / LTX, which expects English prompts.\n"
        f"  - Sum of duration_sec should roughly cover the narration speaking "
        f"length at ~3.5 chars/sec (zh) or ~2.5 words/sec (en).\n"
        f"  - Do NOT prepend or append any text outside the JSON object.\n"
    )
    return sys_msg, user_msg


def _chat_complete(
    *, api_key: str, model: str, system: str, user: str,
    timeout: int = 180,
) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    r = requests.post(OPENAI_URL, headers=headers, json=body, timeout=timeout)
    if r.status_code != 200:
        # Surface a short snippet without leaking the full request
        snippet = (r.text or "")[:400]
        raise RuntimeError(f"OpenAI HTTP {r.status_code}: {snippet}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"unexpected OpenAI response shape: {e}") from e


def _parse_payload(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        # strip leading "json\n" if model still wrapped it
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON: {e}") from e

    title = obj.get("title")
    narration = obj.get("narration")
    shots = obj.get("shot_prompts")

    if not isinstance(title, str) or not title.strip():
        raise ValueError("missing or empty 'title'")
    if not isinstance(narration, str) or not narration.strip():
        raise ValueError("missing or empty 'narration'")
    if not isinstance(shots, list) or not shots:
        raise ValueError("'shot_prompts' must be a non-empty array")

    cleaned_shots: list[dict] = []
    for i, s in enumerate(shots, start=1):
        if not isinstance(s, dict):
            raise ValueError(f"shot_prompts[{i}] is not an object")
        cleaned_shots.append({
            "id": int(s.get("id") or i),
            "text_chunk": str(s.get("text_chunk") or "").strip(),
            "visual":     str(s.get("visual") or "").strip(),
            "duration_sec": float(s.get("duration_sec") or 6.0),
        })

    return {"title": title, "narration": narration, "shot_prompts": cleaned_shots}


def env_status() -> dict[str, Any]:
    """Light-weight inspection of OpenAI env (safe to expose to UI)."""
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    return {
        "configured": bool(key),
        "model": (os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL).strip(),
        # Never return the key. Just last 4 for "yes I see it" UX.
        "key_tail": ("…" + key[-4:]) if len(key) >= 4 else "",
    }
