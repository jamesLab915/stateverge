"""
``presenter_manifest.json`` load/save, merge with timeline, reference image state, variants stub.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Optional

from . import fs_utils

LOG = logging.getLogger("presenter.manifest")


def _now_ts() -> str:
    return datetime.datetime.now().astimezone().isoformat()


def _empty_segment_row(row: dict[str, Any]) -> dict[str, Any]:
    name = row.get("name")
    return {
        "name": name,
        "type": row.get("type"),
        "text": row.get("text", ""),
        "audio_path": row.get("audio_path"),
        "audio_duration_sec": None,
        "split_parts": [],
        "split_points": [],
        "base_video_paths": [],
        "selected_master_files": [],
        "selected_plan": [],
        "runway_input_paths": [],
        "lipsync_output_paths": [],
        "runway_lipsync": row.get("runway_lipsync") or {},
        "eleven_lipsync": row.get("eleven_lipsync") or {},
        "final_segment_path": None,
        "timeline_start": row.get("timeline_start"),
        "status": row.get("status", "placeholder"),
        "errors": [],
    }


def empty_manifest(
    topic_slug: str, video_duration: Optional[float]
) -> dict[str, Any]:
    return {
        "topic_slug": topic_slug,
        "video_duration_sec": video_duration,
        "timeline_path": None,
        "reference_image_path": None,
        "reference_image_status": "unknown",
        "generated_variants": [],
        "segments": [],
        "errors": [],
        "final_output_path": None,
        "updated_at": _now_ts(),
    }


def merge_timeline_into_manifest(
    manifest: dict[str, Any], from_timeline: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_name = {
        s.get("name"): s for s in manifest.get("segments", []) if s.get("name")
    }
    out: list[dict[str, Any]] = []
    for row in from_timeline:
        name = row.get("name")
        base = _empty_segment_row(row)
        if name in by_name:
            o = by_name[name]
            for k in list(base.keys()):
                if o.get(k) is not None:
                    base[k] = o.get(k, base[k])
        out.append(base)
    return out


def load_or_init(
    path: Path, topic: str, video_duration: Optional[float]
) -> dict[str, Any]:
    m = fs_utils.read_json(path)
    if m is None:
        return empty_manifest(topic, video_duration)
    m.setdefault("topic_slug", topic)
    m.setdefault("segments", [])
    m.setdefault("errors", [])
    m.setdefault("generated_variants", [])
    m.setdefault("updated_at", _now_ts())
    m.setdefault("reference_image_path", None)
    m.setdefault("reference_image_status", "unknown")
    for seg in m.get("segments") or []:
        if isinstance(seg, dict):
            seg.setdefault("runway_lipsync", {})
            seg.setdefault("eleven_lipsync", {})
    return m


def save(path: Path, m: dict[str, Any]) -> None:
    m["updated_at"] = _now_ts()
    fs_utils.write_json(path, m)


def apply_host_reference(
    root: Path, manifest: dict[str, Any]
) -> None:
    """
    Record ``assets/presenter_reference/host_master.png``; **missing** is allowed for
    master-only workflows but is logged and blocks future variant gen until fixed.
    """
    p = root / "assets" / "presenter_reference" / "host_master.png"
    try:
        rel = p.resolve().relative_to(root.resolve())
    except Exception:
        rel = Path("assets/presenter_reference/host_master.png")
    manifest["reference_image_path"] = Path(rel).as_posix()
    if p.is_file():
        manifest["reference_image_status"] = "ok"
    else:
        manifest["reference_image_status"] = "missing"
        err = "host_master.png missing at assets/presenter_reference/; future variant module disabled; master concat continues"
        errs = manifest.setdefault("errors", [])
        if err not in errs:
            errs.append(err)
        from .logutil import log_presenter

        log_presenter(
            LOG,
            manifest.get("topic_slug") or "--",
            "--",
            "host_reference",
            "missing: place host_master.png for identity-locked future variants",
            level=logging.ERROR,
        )
