"""Split narration_script.txt into 8-12 documentary sections."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import ensure_topic_dirs, log_step

TYPE_BY_HEADING = {
    "hook": "hook",
    "current reality": "reality",
    "tech breakdown": "tech",
    "future speculation": "future",
    "geopolitical impact": "geo",
    "conclusion": "outro",
}
VALID_TYPES = {"hook", "reality", "tech", "future", "geo", "outro"}


def _read_script(topic: str, root: Path | None = None) -> str:
    paths = ensure_topic_dirs(topic, root)
    p = paths["script"] / "narration_script.txt"
    if not p.is_file():
        fallback = (
            "## hook\n\nA future technology story begins.\n\n"
            "## current reality\n\nThe current reality is already changing.\n\n"
            "## tech breakdown\n\nThe technical system combines sensors, AI, and automation.\n\n"
            "## future speculation\n\nThe future expands these systems into everyday infrastructure.\n\n"
            "## geopolitical impact\n\nNations compete over data, compute, chips, and supply chains.\n\n"
            "## conclusion\n\nThe question is how society chooses to use the technology.\n"
        )
        p.write_text(fallback, encoding="utf-8")
    return p.read_text(encoding="utf-8", errors="replace")


def _heading_blocks(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?im)^##\s+(.+?)\s*$", text))
    if not matches:
        return [("tech", text.strip())] if text.strip() else []
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        typ = TYPE_BY_HEADING.get(title, _infer_type(title + "\n" + body))
        if body:
            out.append((typ, body))
    return out


def _infer_type(text: str) -> str:
    low = text.lower()
    if "hook" in low or "opening" in low:
        return "hook"
    if "geopolitical" in low or "nation" in low or "global" in low:
        return "geo"
    if "future" in low or "speculation" in low or "decade" in low:
        return "future"
    if "robot" in low or "sensor" in low or "automation" in low or "model" in low:
        return "tech"
    if "conclusion" in low or "finally" in low:
        return "outro"
    return "reality"


def _split_paragraphs(body: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n+", body) if p.strip()]
    if len(parts) <= 1:
        sentences = re.split(r"(?<=[.!?])\s+", body.strip())
        parts = []
        buf: list[str] = []
        for s in sentences:
            if not s:
                continue
            buf.append(s)
            if len(" ".join(buf)) > 550:
                parts.append(" ".join(buf).strip())
                buf = []
        if buf:
            parts.append(" ".join(buf).strip())
    return parts or [body.strip()]


def split_sections(topic: str, root: Path | None = None) -> Path:
    paths = ensure_topic_dirs(topic, root)
    blocks = _heading_blocks(_read_script(topic, root))
    candidates: list[tuple[str, str]] = []
    for typ, body in blocks:
        for part in _split_paragraphs(body):
            if part:
                candidates.append((typ if typ in VALID_TYPES else "tech", part))
    if not candidates:
        candidates = [("hook", "Opening narration placeholder.")]

    # Compress/expand to 8-12 sections while preserving order.
    while len(candidates) > 12:
        i = min(range(len(candidates) - 1), key=lambda idx: len(candidates[idx][1]))
        typ = candidates[i][0]
        merged = f"{candidates[i][1]}\n\n{candidates[i + 1][1]}"
        candidates[i : i + 2] = [(typ, merged)]
    while len(candidates) < 8:
        idx = max(range(len(candidates)), key=lambda i: len(candidates[i][1]))
        typ, text = candidates[idx]
        mid = max(1, len(text) // 2)
        cut = text.find(". ", mid)
        if cut < 0:
            cut = mid
        left = text[: cut + 1].strip()
        right = text[cut + 1 :].strip()
        if not right:
            break
        candidates[idx : idx + 1] = [(typ, left), (typ, right)]

    sections = [
        {
            "id": f"section_{i:02d}",
            "text": text,
            "type": typ,
        }
        for i, (typ, text) in enumerate(candidates[:12], start=1)
    ]
    out = paths["script"] / "sections.json"
    out.write_text(json.dumps(sections, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log_step("split_sections", topic, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    args = ap.parse_args(argv)
    split_sections(args.topic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
