"""
Call OpenAI Chat Completions to create ``narration_script.txt`` and ``presenter_script.json`` from
a :class:`brief_loader.ProductionBrief` (read-only: does not modify presenter pipeline).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional, Tuple

import requests

from .brief_loader import ProductionBrief, brief_to_dict, load_brief
from .paths import topic_production_paths

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = """You are a senior documentary head writer. Output ONLY valid JSON (no markdown fences).

The JSON must have exactly these top-level keys:
- "narration_script" (string): full narration, organized BY CHAPTER. Each chapter block MUST start with a line
  of the form: `### Chapter: <name>  (target ~Xs)` where X is the target length in seconds from the brief.
  Write prose suitable for a professional TTS or voiceover; be concrete and time-aware; match total intent to duration_target_sec.
- "presenter_script" (object): keys "intro" ( { "text": "..."} ), "inserts" (array of { "anchor_sec": number, "text": "..." } ),
  and "outro" ( { "text": "..."} ).

Rules for presenter_script:
- intro and outro are mandatory strings (non-empty).
- inserts: place anchors at k * frequency_sec (k=1,2,3,...) in SECONDS, strictly less than (duration_target_sec - 20).
- Each insert 5-12 seconds spoken; short punchy.
- Thematically align with the brief "style" and each chapter "focus" where relevant.
- All presenter copy must be in clear spoken English (or the same language as the brief title if the brief is non-English).

Do not include timestamps inside paragraph lines except the chapter header lines in narration_script.
"""


def _openai_key() -> Optional[str]:
    return os.environ.get("OPENAI_API_KEY", "").strip() or None


def _openai_model() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()


def _insert_anchor_list(duration_target_sec: int, frequency_sec: float) -> list[int]:
    if frequency_sec <= 0:
        return []
    out: list[int] = []
    step = int(round(frequency_sec))
    if step < 1:
        step = 1
    a = step
    while a < int(duration_target_sec) - 20:
        out.append(a)
        a += step
    return out


def _build_user_payload(brief: ProductionBrief) -> str:
    d = brief_to_dict(brief)
    d["_instruction"] = {
        "expected_insert_anchors": _insert_anchor_list(
            brief.duration_target_sec, float(brief.presenter.frequency_sec)
        ),
        "insert_anchor_policy": (
            f"Use anchors {brief.presenter.frequency_sec} seconds apart, "
            "until the last anchor is < duration_target_sec-20s."
        ),
    }
    return json.dumps(d, ensure_ascii=False, indent=2)


def _parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", text)
    t = re.sub(r"\s*```\s*$", "", t, flags=re.M)
    return json.loads(t)


def _validate_presenter(p: Any) -> bool:
    if not isinstance(p, dict):
        return False
    if not (isinstance(p.get("intro"), dict) and (p.get("intro") or {}).get("text")):
        return False
    if not (isinstance(p.get("outro"), dict) and (p.get("outro") or {}).get("text")):
        return False
    ins = p.get("inserts")
    if not isinstance(ins, list):
        return False
    for it in ins:
        if not isinstance(it, dict) or "anchor_sec" not in it or "text" not in it:
            return False
    return True


def generate_scripts(
    root: Path, topic_slug: str, brief: Optional[ProductionBrief] = None
) -> Tuple[Path, Path]:
    """
    Call OpenAI; write ``script/narration_script.txt`` and ``script/presenter_script.json``.
    Returns (narration_path, presenter_path).
    """
    root = Path(root)
    t = topic_production_paths(root, topic_slug)
    pbrief = t["production_brief"]
    if brief is None:
        b = load_brief(pbrief)
    else:
        b = brief
    if b is None:
        raise FileNotFoundError(
            f"Missing production brief: {pbrief} (run --generate-brief first)"
        )
    key = _openai_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set in the environment or .env")

    user_content = _build_user_payload(b)
    body = {
        "model": _openai_model(),
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Here is the production_brief (JSON). Generate narration and presenter as specified.\n\n{user_content}",
            },
        ],
        "temperature": 0.7,
    }
    h = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    r = requests.post(OPENAI_URL, headers=h, data=json.dumps(body), timeout=300)
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"OpenAI error HTTP {r.status_code}: {r.text[:2000]}"
        )
    data = r.json()
    try:
        msg = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"OpenAI response shape error: {data!r}") from e
    out = _parse_json_response(str(msg))
    ntxt = out.get("narration_script")
    ps = out.get("presenter_script")
    if not isinstance(ntxt, str) or not ntxt.strip():
        raise ValueError("Model did not return a valid narration_script string")
    if not _validate_presenter(ps):
        raise ValueError("Model did not return a valid presenter_script object")

    t["script"].mkdir(parents=True, exist_ok=True)
    nap = t["narration_script"]
    psp = t["presenter_script"]
    nap.write_text(ntxt.strip() + "\n", encoding="utf-8")
    psp.write_text(
        json.dumps(ps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return nap, psp
