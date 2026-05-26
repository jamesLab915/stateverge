#!/usr/bin/env python3
"""Integrate auto_clip_cleaner cut_list → DaVinci timeline cuts (no ffmpeg -c copy on iPhone VFR)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from davinci_production_agent.paths import CUTLISTS_ROOT, IPHONE_VFR_MARKERS  # noqa: E402


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_iphone_vfr_source(path: Path) -> bool:
    s = str(path.resolve()).lower()
    return any(m.lower() in s for m in IPHONE_VFR_MARKERS)


def forbid_ffmpeg_copy_trim(source_path: Path, *, operation: str = "") -> dict[str, Any]:
    """Policy guard: never ffmpeg -c copy trim on iPhone VFR."""
    blocked = is_iphone_vfr_source(source_path)
    return {
        "blocked": blocked,
        "reason": "forbid_ffmpeg_copy_trim_iphone_vfr" if blocked else "",
        "source": str(source_path),
        "operation": operation,
        "required_path": "davinci_timeline_cut",
    }


def load_cut_recommendations(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def build_davinci_cut_plan(
    source_path: Path,
    cut_recommendations: dict[str, Any],
    *,
    job_id: str,
) -> dict[str, Any]:
    """Translate cut_recommendations.json into DaVinci timeline cut plan (no ffmpeg copy)."""
    guard = forbid_ffmpeg_copy_trim(source_path)
    cuts = cut_recommendations.get("cuts") or cut_recommendations.get("cut_ranges") or []
    plan: dict[str, Any] = {
        "version": "professional_davinci_cut_plan_v1",
        "created_at": _utc_iso(),
        "job_id": job_id,
        "source": str(source_path),
        "iphone_vfr": guard["blocked"],
        "ffmpeg_copy_trim_forbidden": guard["blocked"],
        "cuts": cuts,
        "apply_via": "davinci_resolve_timeline",
        "policy": "Never ffmpeg -c copy trim on iPhone VFR; re-encode in Resolve if needed.",
    }
    if guard["blocked"]:
        plan["warnings"] = [guard["reason"]]
    return plan


def write_cut_artifacts(
    source_path: Path,
    cut_recommendations_path: Path,
    *,
    job_id: str,
) -> dict[str, Path]:
    """Write cut_list + cut_list_applied stub for Resolve workflow."""
    recs = load_cut_recommendations(cut_recommendations_path) or {}
    out_dir = CUTLISTS_ROOT / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cut_plan = build_davinci_cut_plan(source_path, recs, job_id=job_id)
    cut_list_path = out_dir / "cut_list.json"
    cut_list_path.write_text(json.dumps(cut_plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    applied = {
        "version": "cut_list_applied_v1",
        "created_at": _utc_iso(),
        "job_id": job_id,
        "source": str(source_path),
        "applied": False,
        "applied_via": None,
        "note": "v1 scaffold: apply cuts in Resolve when API connected; never ffmpeg -c copy on VFR.",
        "cut_list": str(cut_list_path),
    }
    applied_path = out_dir / "cut_list_applied.json"
    applied_path.write_text(json.dumps(applied, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {"cut_list": cut_list_path, "cut_list_applied": applied_path}


def run_auto_detect_cut_list(source_path: Path, *, job_id: str) -> dict[str, Any]:
    """Optional: invoke auto_clip_cleaner detectors to produce cut_recommendations."""
    result: dict[str, Any] = {"source": str(source_path), "ok": False}
    guard = forbid_ffmpeg_copy_trim(source_path)
    result["vfr_guard"] = guard
    if not source_path.is_file():
        result["error"] = "source_missing"
        return result
    try:
        from video_quality.clip_cleaner.detectors import run_auto_detect  # noqa: WPS433

        detected = run_auto_detect(source_path)
        out_dir = CUTLISTS_ROOT / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        rec_path = out_dir / "cut_recommendations.json"
        body = {
            "version": "cut_recommendations_v1",
            "created_at": _utc_iso(),
            "source": str(source_path),
            "cuts": detected.get("cuts") or detected.get("recommended_cuts") or [],
            "detector_meta": detected,
        }
        rec_path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths = write_cut_artifacts(source_path, rec_path, job_id=job_id)
        result["ok"] = True
        result["paths"] = {k: str(v) for k, v in paths.items()}
        result["cut_recommendations"] = str(rec_path)
    except Exception as exc:  # noqa: BLE001
        result["error"] = repr(exc)
    return result
