"""Generate LTX cinematic prompts from sections.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import ensure_topic_dirs, log_step

PROMPT_BY_TYPE = {
    "future": "cinematic futuristic AI system, robotic automation, glowing data networks, ultra realistic, 4k",
    "tech": "advanced robotics system, industrial automation, mechanical precision, cinematic lighting",
    "geo": "global map, geopolitical tension, data overlay visualization",
    "hook": "cinematic opening shot, mysterious future technology, dramatic lighting, ultra realistic, 4k",
    "reality": "realistic modern industry, AI infrastructure, data center and factory floor, documentary style",
    "outro": "cinematic closing scene, human future with intelligent machines, atmospheric lighting, 4k",
}


def _load_sections(topic: str, root: Path | None = None) -> list[dict[str, Any]]:
    paths = ensure_topic_dirs(topic, root)
    p = paths["script"] / "sections.json"
    if not p.is_file():
        from .split_sections import split_sections

        split_sections(topic, root)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def generate_prompts(topic: str, root: Path | None = None) -> Path:
    paths = ensure_topic_dirs(topic, root)
    sections = _load_sections(topic, root)
    out_rows = []
    for sec in sections:
        typ = str(sec.get("type") or "tech")
        prompt = PROMPT_BY_TYPE.get(typ, PROMPT_BY_TYPE["tech"])
        out_rows.append(
            {
                "section_id": sec.get("id", ""),
                "type": typ,
                "prompt": prompt,
                "negative_prompt": "low resolution, blurry, distorted faces, unreadable text, watermark",
                "duration_seconds": 6 if typ not in ("hook", "outro") else 5,
            }
        )
    out = paths["visuals"] / "ltx_prompts.json"
    out.write_text(json.dumps(out_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log_step("generate_prompts", topic, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    args = ap.parse_args(argv)
    generate_prompts(args.topic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
