#!/usr/bin/env python3
"""
Tool usage tracker: frequency statistics for commercial evidence.
Tracks adoption of: Cursor, Runway, LTX, HeyGen, ElevenLabs, Envato, OpenAI, etc.
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

_PR = Path(__file__).resolve().parent
if str(_PR) not in sys.path:
    sys.path.insert(0, str(_PR))
from _paths import REPO_ROOT, TRACK

OUTPUT_CSV = TRACK / "tool_usage_stats.csv"
CSV_HEADERS = ["Tool", "Usage Count", "Last Used", "Area", "Evidence Value", "IRS Relevance", "EB1_NIW_Relevance"]

TOOLS = [
    "Cursor",
    "Runway",
    "LTX",
    "HeyGen",
    "ElevenLabs",
    "Envato",
    "OpenAI",
    "ChatGPT",
    "CapCut",
    "Vercel",
    "Neon",
    "GitHub",
    "Hostinger",
    "FFmpeg",
    "Whisper",
    "Pexels",
    "Pixabay",
    "DVIDS",
]

TOOL_INFO = {
    "Cursor": {
        "area": "Development",
        "evidence": "Medium",
        "irs": "Tool expense",
        "eb1niv": "Development platform",
    },
    "Runway": {
        "area": "Video generation",
        "evidence": "High",
        "irs": "Video processing tool",
        "eb1niv": "AI video generation capability",
    },
    "LTX": {
        "area": "Video generation",
        "evidence": "High",
        "irs": "Video generation tool",
        "eb1niv": "Advanced video synthesis",
    },
    "HeyGen": {
        "area": "Presenter generation",
        "evidence": "High",
        "irs": "AI presenter tool",
        "eb1niv": "Presenter automation, AI integration",
    },
    "ElevenLabs": {
        "area": "Audio generation",
        "evidence": "High",
        "irs": "Audio synthesis tool",
        "eb1niv": "Voice generation, text-to-speech",
    },
    "OpenAI": {
        "area": "AI/ML",
        "evidence": "High",
        "irs": "LLM subscription",
        "eb1niv": "Core AI infrastructure",
    },
    "ChatGPT": {
        "area": "Development",
        "evidence": "High",
        "irs": "AI tool subscription",
        "eb1niv": "AI-assisted development",
    },
    "CapCut": {
        "area": "Video editing",
        "evidence": "Medium",
        "irs": "Video editing software",
        "eb1niv": "Final video refinement",
    },
    "GitHub": {
        "area": "Version control",
        "evidence": "High",
        "irs": "Hosting service",
        "eb1niv": "Development infrastructure, R&D tracking",
    },
    "Vercel": {
        "area": "Hosting",
        "evidence": "Medium",
        "irs": "Server infrastructure",
        "eb1niv": "Deployment platform",
    },
    "Neon": {
        "area": "Database",
        "evidence": "Medium",
        "irs": "Database service",
        "eb1niv": "Data infrastructure",
    },
    "Hostinger": {
        "area": "Hosting",
        "evidence": "Medium",
        "irs": "Web hosting",
        "eb1niv": "Application hosting",
    },
    "Pexels": {
        "area": "Media library",
        "evidence": "Low",
        "irs": "Stock media",
        "eb1niv": "Content sourcing",
    },
    "Pixabay": {
        "area": "Media library",
        "evidence": "Low",
        "irs": "Stock media",
        "eb1niv": "Content sourcing",
    },
    "DVIDS": {
        "area": "Public archive media",
        "evidence": "Medium",
        "irs": "Public stock footage (no fee)",
        "eb1niv": "Authoritative archive integration",
    },
    "Envato": {
        "area": "Stock library",
        "evidence": "Medium",
        "irs": "Subscription / licensed assets",
        "eb1niv": "Production asset pipeline",
    },
    "FFmpeg": {
        "area": "Video processing",
        "evidence": "Medium",
        "irs": "Media processing",
        "eb1niv": "Video synthesis infrastructure",
    },
    "Whisper": {
        "area": "Audio processing",
        "evidence": "Medium",
        "irs": "Audio tool",
        "eb1niv": "Speech recognition",
    },
}


def _count_mentions_in_csv(csv_file: Path, tool_name: str) -> int:
    """Count rows whose any cell mentions ``tool_name`` (case-insensitive, once per row)."""
    if not csv_file.is_file():
        return 0
    count = 0
    needle = tool_name.lower()
    try:
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for key, value in row.items():
                    if key is None:
                        if isinstance(value, list):
                            joined = " ".join(str(v) for v in value if v)
                        else:
                            joined = str(value or "")
                        if needle in joined.lower():
                            count += 1
                            break
                    elif value and needle in str(value).lower():
                        count += 1
                        break
    except OSError:
        return 0
    return count


def _count_mentions_in_text(text_file: Path, tool_name: str) -> int:
    """Count line-level mentions in a markdown / text file (each matching line counts once)."""
    if not text_file.is_file():
        return 0
    needle = tool_name.lower()
    n = 0
    try:
        for line in text_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if needle in line.lower():
                n += 1
    except OSError:
        return 0
    return n


def _get_last_mention(csv_file: Path, tool_name: str) -> str:
    """Latest date-like field across rows that mention ``tool_name``."""
    if not csv_file.is_file():
        return ""
    last_date = ""
    needle = tool_name.lower()
    try:
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                hit = any(needle in str(v).lower() for v in row.values() if v)
                if not hit:
                    continue
                for key, value in row.items():
                    if not value or not isinstance(key, str):
                        continue
                    klow = key.lower()
                    if klow in ("date", "logged at", "timestamp", "last updated", "last used"):
                        if str(value) > last_date:
                            last_date = str(value)
    except OSError:
        return ""
    return last_date


def collect_tool_usage() -> list[dict]:
    """Aggregate tool mentions across CSV + Markdown sources under docs/tracking/."""
    records: list[dict] = []
    csv_sources: list[Path] = [
        TRACK / "github_activity_log.csv",
        TRACK / "email_subscription_log.csv",
        TRACK / "auto_tracking_events.csv",
        TRACK / "dev_time_summary.csv",
        TRACK / "irs_expense_log.csv",
        TRACK / "project_progress.csv",
    ]
    text_sources: list[Path] = [
        TRACK / "research_log.md",
        TRACK / "subscriptions.md",
        TRACK / "dev_milestones.md",
        TRACK / "tool_usage_matrix.md",
    ]
    for tool in TOOLS:
        count = 0
        for cf in csv_sources:
            count += _count_mentions_in_csv(cf, tool)
        for tf in text_sources:
            count += _count_mentions_in_text(tf, tool)
        if count == 0:
            continue
        last_used = ""
        for cf in csv_sources:
            mention = _get_last_mention(cf, tool)
            if mention > last_used:
                last_used = mention
        info = TOOL_INFO.get(
            tool,
            {
                "area": "General",
                "evidence": "Low",
                "irs": "Tool",
                "eb1niv": "Infrastructure",
            },
        )
        records.append(
            {
                "Tool": tool,
                "Usage Count": str(count),
                "Last Used": last_used,
                "Area": info["area"],
                "Evidence Value": info["evidence"],
                "IRS Relevance": info["irs"],
                "EB1_NIW_Relevance": info["eb1niv"],
            }
        )
    return records


def write_csv(records: list[dict]) -> None:
    """Write tool usage stats to CSV."""
    TRACK.mkdir(parents=True, exist_ok=True)

    # Sort by usage count (descending)
    records = sorted(records, key=lambda x: int(x.get("Usage Count", "0")), reverse=True)

    try:
        with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()
            for record in records:
                writer.writerow(record)
        print(f"[tool_usage_tracker] wrote {len(records)} tools to {OUTPUT_CSV}")
    except Exception as e:
        print(f"[tool_usage_tracker] error writing CSV: {e}")


def main() -> None:
    records = collect_tool_usage()
    if records:
        write_csv(records)
        total_usage = sum(int(r.get("Usage Count", "0")) for r in records)
        high_value = len([r for r in records if r.get("Evidence Value") == "High"])
        print(f"[tool_usage_tracker] {len(records)} tools, {total_usage} total mentions, {high_value} high-value")
    else:
        print("[tool_usage_tracker] no tool usage data found")


if __name__ == "__main__":
    main()
