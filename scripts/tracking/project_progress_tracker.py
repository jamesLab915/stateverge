#!/usr/bin/env python3
"""
Project progress tracker: auto-calculate StateVerge topic completion.
Tracks: script → audio → sections → final video.
Evidence for: sustained project development, continuous output.
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

OUTPUT_CSV = TRACK / "project_progress.csv"
TOPICS_DIR = REPO_ROOT / "topics"
OUTPUT_DIR = REPO_ROOT / "output"

CSV_HEADERS = [
    "Topic",
    "Stage",
    "Completion %",
    "Has Script",
    "Has Presenter Script",
    "Has Audio",
    "Section Count",
    "Has Final",
    "Has Packaged Final",
    "Last Updated",
]

STAGES = {
    "init": 10,
    "script_ready": 30,
    "audio_ready": 50,
    "sections_ready": 75,
    "final_ready": 90,
    "packaged_ready": 100,
}


_NARRATION_REL: tuple[Path, ...] = (
    Path("narration_script.txt"),
    Path("script") / "narration_script.txt",
    Path("brief") / "narration_script.txt",
)
_PRESENTER_REL: tuple[Path, ...] = (
    Path("presenter_script.json"),
    Path("script") / "presenter_script.json",
    Path("presenter") / "presenter_script.json",
)
_AUDIO_REL: tuple[Path, ...] = (
    Path("audio.wav"),
    Path("audio") / "audio.wav",
    Path("audio") / "narration.wav",
)
_SECTIONS_REL: tuple[Path, ...] = (
    Path("sections"),
    Path("presenter") / "segments",
    Path("presenter") / "final",
)
_FINAL_REL: tuple[Path, ...] = (
    Path("final_with_presenter.mp4"),
    Path("output") / "final_with_presenter.mp4",
)
_PACKAGED_REL: tuple[Path, ...] = (
    Path("final_packaged.mp4"),
    Path("output") / "final_packaged.mp4",
)


def _exists_any(base: Path, rels: tuple[Path, ...]) -> bool:
    return any((base / r).is_file() for r in rels)


def _section_count(base: Path) -> int:
    n = 0
    for r in _SECTIONS_REL:
        d = base / r
        if d.is_dir():
            n += len(list(d.glob("*.mp4")))
    return n


def _candidate_topic_dirs(slug: str) -> list[Path]:
    """Probe both ``topics/<slug>/`` and ``output/<slug>/`` (some setups use either)."""
    dirs: list[Path] = []
    a = TOPICS_DIR / slug
    b = OUTPUT_DIR / slug
    if a.is_dir():
        dirs.append(a)
    if b.is_dir():
        dirs.append(b)
    return dirs


def _get_stage_and_completion(topic_slug: str) -> tuple[str, int, dict]:
    """Determine stage and completion % for a topic by probing standard layouts."""
    dirs = _candidate_topic_dirs(topic_slug)
    has_script = any(_exists_any(d, _NARRATION_REL) for d in dirs)
    has_presenter_script = any(_exists_any(d, _PRESENTER_REL) for d in dirs)
    has_audio = any(_exists_any(d, _AUDIO_REL) for d in dirs)
    section_count = sum(_section_count(d) for d in dirs)
    has_final = any(_exists_any(d, _FINAL_REL) for d in dirs)
    has_packaged_final = any(_exists_any(d, _PACKAGED_REL) for d in dirs)

    metadata = {
        "has_script": has_script,
        "has_presenter_script": has_presenter_script,
        "has_audio": has_audio,
        "section_count": section_count,
        "has_final": has_final,
        "has_packaged_final": has_packaged_final,
    }

    if has_packaged_final:
        return "packaged_ready", STAGES["packaged_ready"], metadata
    if has_final:
        return "final_ready", STAGES["final_ready"], metadata
    if section_count > 0:
        return "sections_ready", STAGES["sections_ready"], metadata
    if has_audio:
        return "audio_ready", STAGES["audio_ready"], metadata
    if has_presenter_script or has_script:
        return "script_ready", STAGES["script_ready"], metadata
    return "init", STAGES["init"], metadata


def _get_last_modified(topic_slug: str) -> str:
    """Most recent mtime across both topics/<slug>/ and output/<slug>/."""
    best = 0.0
    for d in _candidate_topic_dirs(topic_slug):
        try:
            best = max(best, d.stat().st_mtime)
        except OSError:
            continue
    if best <= 0:
        return ""
    return datetime.fromtimestamp(best).isoformat(timespec="seconds")


def _all_topic_slugs() -> list[str]:
    slugs: set[str] = set()
    for base in (TOPICS_DIR, OUTPUT_DIR):
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if not d.is_dir():
                continue
            n = d.name
            if n.startswith(("_", ".", "tmp")):
                continue
            slugs.add(n)
    return sorted(slugs)


def collect_progress() -> list[dict]:
    """Collect progress for all topics found in topics/ or root output/."""
    records: list[dict] = []
    for slug in _all_topic_slugs():
        stage, completion, metadata = _get_stage_and_completion(slug)
        last_modified = _get_last_modified(slug)
        records.append(
            {
                "Topic": slug,
                "Stage": stage,
                "Completion %": str(completion),
                "Has Script": "Yes" if metadata["has_script"] else "No",
                "Has Presenter Script": "Yes" if metadata["has_presenter_script"] else "No",
                "Has Audio": "Yes" if metadata["has_audio"] else "No",
                "Section Count": str(metadata["section_count"]),
                "Has Final": "Yes" if metadata["has_final"] else "No",
                "Has Packaged Final": "Yes" if metadata["has_packaged_final"] else "No",
                "Last Updated": last_modified,
            }
        )
    return records


def write_csv(records: list[dict]) -> None:
    """Write progress report to CSV."""
    TRACK.mkdir(parents=True, exist_ok=True)

    try:
        with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()
            for record in records:
                writer.writerow(record)
        print(f"[project_progress_tracker] wrote {len(records)} topics to {OUTPUT_CSV}")
    except Exception as e:
        print(f"[project_progress_tracker] error writing CSV: {e}")


def main() -> None:
    records = collect_progress()
    if records:
        write_csv(records)
        final_count = len([r for r in records if r["Stage"] == "packaged_ready"])
        sections_count = len([r for r in records if r["Stage"] in ("sections_ready", "final_ready", "packaged_ready")])
        print(f"[project_progress_tracker] {len(records)} topics, {final_count} packaged, {sections_count} with video")
    else:
        print("[project_progress_tracker] no topics found")


if __name__ == "__main__":
    main()
