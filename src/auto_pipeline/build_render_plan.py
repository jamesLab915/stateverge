"""Build the final FFmpeg/LTX/HeyGen render plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import ensure_topic_dirs, log_step


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _ensure_inputs(topic: str, root: Path | None = None) -> dict[str, Path]:
    paths = ensure_topic_dirs(topic, root)
    if not (paths["script"] / "sections.json").is_file():
        from .split_sections import split_sections

        split_sections(topic, root)
    if not (paths["visuals"] / "ltx_prompts.json").is_file():
        from .generate_prompts import generate_prompts

        generate_prompts(topic, root)
    if not (paths["assets"] / "media_manifest.json").is_file():
        from .generate_queries import generate_queries

        generate_queries(topic, root)
    if not (paths["final"] / "heygen_timeline.json").is_file():
        from .generate_heygen_plan import generate_heygen_plan

        generate_heygen_plan(topic, root)
    return paths


def _seconds(timecode: str) -> int:
    parts = [int(x) for x in timecode.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def build_render_plan(topic: str, root: Path | None = None) -> Path:
    paths = _ensure_inputs(topic, root)
    sections = _read_json(paths["script"] / "sections.json", [])
    prompts = _read_json(paths["visuals"] / "ltx_prompts.json", [])
    manifest = _read_json(paths["assets"] / "media_manifest.json", {})
    heygen = _read_json(paths["final"] / "heygen_timeline.json", [])
    prompt_by_id = {str(x.get("section_id")): x for x in prompts if isinstance(x, dict)}

    render_sections = []
    cursor = 0
    for sec in sections if isinstance(sections, list) else []:
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("id") or "")
        typ = str(sec.get("type") or "tech")
        source = "AI" if typ in {"hook", "tech", "future", "geo", "outro"} else "stock"
        duration = int(prompt_by_id.get(sid, {}).get("duration_seconds") or 6)
        insert = [
            h
            for h in heygen
            if isinstance(h, dict)
            and cursor <= _seconds(str(h.get("timecode") or "00:00")) < cursor + duration
        ]
        render_sections.append(
            {
                "section_id": sid,
                "type": typ,
                "order": len(render_sections) + 1,
                "video_source": source,
                "ltx_prompt_ref": f"visuals/ltx_prompts.json#{sid}",
                "stock_manifest": manifest.get(sid, []) if isinstance(manifest, dict) else [],
                "insert_heygen": bool(insert),
                "heygen_inserts": insert,
                "audio_path": "script/narration_audio.wav",
                "estimated_duration_seconds": duration,
            }
        )
        cursor += duration

    plan = {
        "topic": topic,
        "output_video": "final/final_render.mp4",
        "audio_path": "script/narration_audio.wav",
        "sections_order": [r["section_id"] for r in render_sections],
        "render_sections": render_sections,
        "inputs": {
            "narration_script": "script/narration_script.txt",
            "sections": "script/sections.json",
            "ltx_prompts": "visuals/ltx_prompts.json",
            "media_manifest": "assets/media_manifest.json",
            "heygen_timeline": "final/heygen_timeline.json",
        },
        "next_steps": [
            "Generate narration_audio.wav from narration_script.txt.",
            "Render AI clips from ltx_prompts.json using LTX.",
            "Download stock clips from media_manifest.json queries.",
            "Generate presenter clips from heygen_timeline.json.",
            "Assemble with FFmpeg according to render_sections order.",
        ],
    }
    out = paths["final"] / "final_render_plan.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log_step("build_render_plan", topic, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    args = ap.parse_args(argv)
    build_render_plan(args.topic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
