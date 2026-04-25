"""
Build ``ltx_scene_plan.json`` for manual LTX / image-video workflows (no API calls).

Scenes: 5–10s, prompts deduplicated, style aligned with the production brief.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .brief_loader import ProductionBrief, load_brief
from .paths import topic_production_paths


def _relposix(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.as_posix()


@dataclass(frozen=True)
class LtxScenePlanResult:
    """Result of :func:`generate_ltx_scene_plan` (for CLI / callers)."""

    output_path: Path
    scene_count: int
    production_brief_path: Path
    narration_script_path: Path


def _decompose_duration_to_scenes(chapter_len_sec: float) -> list[float]:
    """
    Split ``chapter_len_sec`` into 5.0–10.0s segments, sum matches within epsilon.
    """
    r = max(0.0, float(chapter_len_sec))
    if r < 0.1:
        return [5.0]
    out: list[float] = []
    while r > 0.1:
        if r <= 10.0 + 1e-6:
            if r < 5.0 and out:
                out[-1] = round(out[-1] + r, 3)
            else:
                out.append(round(min(r, 10.0), 3))
            break
        take = 8.0
        if r - take < 5.0 and r - take > 0.01:
            take = max(5.0, r - 5.0)
        take = max(5.0, min(10.0, take, r))
        out.append(round(take, 3))
        r -= take
    return out or [5.0]


def _base_prompt(
    style: str, chapter_name: str, focus: str, purpose_short: str, variant_key: str
) -> str:
    return (
        f"{style}, chapter «{chapter_name}»: {focus}. Cinematic, photoreal, "
        f"8k, filmic color grade, {purpose_short}, consistent world look, "
        f"global composition, {variant_key}"
    ).strip()


def _unique_prompt(
    style: str,
    chapter_name: str,
    focus: str,
    purpose_short: str,
    used: set[str],
    salt: int,
) -> str:
    key = 0
    p = _base_prompt(style, chapter_name, focus, purpose_short, f"v{key}")
    while p in used and key < 50:
        key += 1
        p = _base_prompt(
            style,
            chapter_name,
            focus,
            purpose_short,
            f"alternative lens and lighting v{key}, id {salt}",
        )
    used.add(p)
    return p


def build_scene_plan(
    brief: ProductionBrief, *, extra_style_hint: str = ""
) -> dict[str, Any]:
    used_prompts: set[str] = set()
    style = (brief.style + " " + extra_style_hint).strip()
    scenes: list[dict[str, Any]] = []
    n = 0
    for ch in brief.chapters:
        durs = _decompose_duration_to_scenes(float(ch.duration_sec))
        for j, d in enumerate(durs):
            n += 1
            purpose = (
                f"support «{ch.name}» segment {j+1}/{len(durs)}: visual beat"
            )
            salt = n * 1000 + j + hash(
                f"{ch.name}{ch.focus}{j}".encode()
            ) % 997
            ptxt = _unique_prompt(
                style, ch.name, ch.focus, purpose, used_prompts, salt
            )
            sid = f"scene_{n:02d}"
            h = hashlib.md5(
                f"{sid}|{ptxt}".encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:8]
            ptxt2 = f"{ptxt} [sig:{h}]"  # extra uniqueness token without changing look much
            used_prompts.add(ptxt2)
            scenes.append(
                {
                    "scene_id": sid,
                    "duration_sec": float(d),
                    "prompt": ptxt2,
                    "purpose": f"establishing shot for {ch.name} — {purpose}",
                }
            )
    return {
        "scenes": scenes,
        "meta": {
            "title": brief.title,
            "duration_target_sec": brief.duration_target_sec,
            "style": brief.style,
        },
    }


def generate_ltx_scene_plan(root: Path, topic_slug: str) -> LtxScenePlanResult:
    """
    Read ``brief/production_brief.json`` and ``script/narration_script.txt``,
    write ``brief/ltx_scene_plan.json`` (editable JSON).

    Raises :exc:`FileNotFoundError` if either input file is missing.
    Raises :exc:`ValueError` if the brief cannot be parsed or narration is empty.
    """
    root = Path(root).resolve()
    t = topic_production_paths(root, topic_slug)
    brief_path = t["production_brief"]
    narration_path = t["narration_script"]

    if not brief_path.is_file():
        raise FileNotFoundError(
            f"Missing production_brief.json (create or copy it first): {brief_path}"
        )
    if not narration_path.is_file():
        raise FileNotFoundError(
            f"Missing narration_script.txt (create it under script/ first): {narration_path}"
        )

    b = load_brief(brief_path)
    if b is None:
        raise ValueError(
            f"Could not parse production_brief.json (invalid JSON or structure): {brief_path}"
        )

    narration_text = narration_path.read_text(encoding="utf-8")
    if not narration_text.strip():
        raise ValueError(f"narration_script.txt is empty: {narration_path}")

    plan = build_scene_plan(b)
    plan_meta = plan.setdefault("meta", {})
    plan_meta["production_brief"] = _relposix(root, brief_path)
    plan_meta["narration_script"] = _relposix(root, narration_path)

    out = t["ltx_scene_plan"]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
        f.write("\n")

    n_scenes = len(plan.get("scenes") or [])
    return LtxScenePlanResult(
        output_path=out,
        scene_count=n_scenes,
        production_brief_path=brief_path,
        narration_script_path=narration_path,
    )


if __name__ == "__main__":
    from .brief_loader import default_brief_template

    _plan = build_scene_plan(default_brief_template("ltx_test"))
    print("ltx_planner: OK, scenes =", len(_plan["scenes"]))
