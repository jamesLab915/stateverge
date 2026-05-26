"""Generate Pexels/Pixabay media queries and placeholder media manifest."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import ensure_topic_dirs, log_step

STOPWORDS = {
    "the",
    "and",
    "that",
    "with",
    "this",
    "from",
    "will",
    "into",
    "when",
    "what",
    "they",
    "their",
    "about",
    "future",
    "system",
    "systems",
    "technology",
    "become",
    "becomes",
}

QUERY_MAP = {
    "ai factory": "robot factory automation",
    "factory": "robot factory automation",
    "robot": "robot factory automation",
    "automation": "industrial automation robotics",
    "supply chain": "cargo shipping port",
    "shipping": "cargo shipping port",
    "chips": "semiconductor factory",
    "data": "data center server room",
    "energy": "renewable energy infrastructure",
    "geopolitical": "global map data overlay",
    "global": "global network map",
    "city": "smart city technology",
}


def _sections(topic: str, root: Path | None = None) -> list[dict[str, Any]]:
    paths = ensure_topic_dirs(topic, root)
    p = paths["script"] / "sections.json"
    if not p.is_file():
        from .split_sections import split_sections

        split_sections(topic, root)
    data = json.loads(p.read_text(encoding="utf-8"))
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _keywords(text: str, limit: int = 4) -> list[str]:
    low = text.lower()
    hits = [v for k, v in QUERY_MAP.items() if k in low]
    words = re.findall(r"[a-zA-Z][a-zA-Z-]{3,}", low)
    counts = Counter(w for w in words if w not in STOPWORDS)
    for word, _count in counts.most_common(limit):
        if len(hits) >= limit:
            break
        hits.append(word.replace("-", " "))
    clean: list[str] = []
    for item in hits:
        if item not in clean:
            clean.append(item)
    return clean[:limit] or ["future technology documentary"]


def _query_for_section(sec: dict[str, Any]) -> str:
    text = str(sec.get("text") or "")
    typ = str(sec.get("type") or "")
    keys = _keywords(text)
    if typ == "geo":
        return "global map geopolitical data overlay"
    if typ == "tech":
        return keys[0] if "automation" in keys[0] else f"{keys[0]} technology automation"
    if typ == "future":
        return f"future {keys[0]} technology"
    return keys[0]


def generate_queries(topic: str, root: Path | None = None) -> tuple[Path, Path]:
    paths = ensure_topic_dirs(topic, root)
    sections = _sections(topic, root)
    queries: list[dict[str, str]] = []
    manifest: dict[str, list[dict[str, str]]] = {}
    for sec in sections:
        sid = str(sec.get("id") or "")
        query = _query_for_section(sec)
        queries.append(
            {
                "section_id": sid,
                "query": query,
                "pexels_query": query,
                "pixabay_query": query,
            }
        )
        manifest[sid] = [
            {"query": query, "source": "pexels"},
            {"query": query, "source": "pixabay"},
        ]
    qout = paths["assets"] / "media_queries.json"
    mout = paths["assets"] / "media_manifest.json"
    qout.write_text(json.dumps(queries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mout.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log_step("generate_queries", topic, qout)
    log_step("generate_media_manifest", topic, mout)
    return qout, mout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    args = ap.parse_args(argv)
    generate_queries(args.topic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
