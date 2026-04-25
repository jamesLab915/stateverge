"""
Path helpers, directory creation, topic layout, and JSON read/write.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

LOG = logging.getLogger("presenter.fs")


def ensure_dir(p: Path) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def relposix(base: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def read_json(p: Path) -> Optional[dict[str, Any]]:
    if not p.is_file():
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(p: Path, data: Any, indent: int = 2) -> None:
    ensure_dir(p.parent)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
        f.write("\n")


def topic_paths(
    root: Path, topic: str
) -> dict[str, Path]:
    base = root / "topics" / topic
    return {
        "root": base,
        "script": base / "script",
        "audio": base / "audio" / "presenter",
        "narration": base / "audio" / "narration_full.wav",  # reserved
        "video": base / "video" / "narrative_main.mp4",
        "presenter": base / "presenter",
        "plan": base / "presenter" / "plan",
        "base": base / "presenter" / "base",
        "segments": base / "presenter" / "segments",
        "lipsync_input": base / "presenter" / "lipsync_input",
        "lipsync_output": base / "presenter" / "lipsync_output",
        "final": base / "presenter" / "final",
        "manifests": base / "presenter" / "manifests",
        "output": base / "output",
        "presenter_script": base / "script" / "presenter_script.json",
        "full_script": base / "script" / "full_script.txt",
        "timeline": base / "presenter" / "plan" / "presenter_timeline.json",
        "manifest": base / "presenter" / "manifests" / "presenter_manifest.json",
        "final_output": base / "output" / "final_with_presenter.mp4",
        "assets_reference": root / "assets" / "presenter_reference",
        "assets_generated": root / "assets" / "presenter_generated",
    }


def ensure_default_asset_tree(root: Path) -> None:
    """
    Create standard ``assets/`` folders: reference, masters, generated slots, topic-agnostic.
    """
    root = Path(root)
    ensure_dir(root / "assets" / "presenter_reference")
    ensure_dir(root / "assets" / "presenter_masters" / "5s")
    ensure_dir(root / "assets" / "presenter_masters" / "10s")
    for sub in ("intro", "insert", "outro"):
        ensure_dir(root / "assets" / "presenter_generated" / sub)
