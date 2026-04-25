"""
Path helpers for the production layer (per-topic ``brief/``, ``script/``, ``output/``).
"""

from __future__ import annotations

from pathlib import Path


def topic_production_paths(root: Path, slug: str) -> dict[str, Path]:
    root = Path(root)
    base = root / "topics" / slug
    return {
        "root": base,
        "brief": base / "brief",
        "production_brief": base / "brief" / "production_brief.json",
        "ltx_scene_plan": base / "brief" / "ltx_scene_plan.json",
        "packaging_manifest": base / "brief" / "packaging_manifest.json",
        "script": base / "script",
        "narration_script": base / "script" / "narration_script.txt",
        "presenter_script": base / "script" / "presenter_script.json",
        "output": base / "output",
        "final_with_presenter": base / "output" / "final_with_presenter.mp4",
        "final_packaged": base / "output" / "final_packaged.mp4",
        "video_narrative": base / "video" / "narrative_main.mp4",
        "presenter_timeline": base / "presenter" / "plan" / "presenter_timeline.json",
    }
