#!/usr/bin/env python3
"""Diagnose Content Truth & Audio Routing v1 — scan recent metadata/job JSON for mismatches."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
_NYC = _SCRIPTS / "nyc_auto"
for p in (_SCRIPTS, _NYC):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

_CC_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"
_CC_LOGS.mkdir(parents=True, exist_ok=True)
_REPORT_MD = _CC_LOGS / "content_truth_audio_routing_report.md"

_SCAN_ROOTS = [
    Path("/Volumes/SV_TRANSFER/publish_pack/shorts_uploads"),
    Path("/Volumes/SV_TRANSFER/publish_pack/nyc_long_uploads"),
    _REPO / "data" / "shorts_runtime",
    _REPO / "data" / "long_runtime",
]
_JSON_NAMES = (
    "youtube_metadata.json",
    "shorts_job_result.json",
    "long_job_result.json",
    "job.json",
)
_MAX_FILES = 400


def _report_json_path() -> Path:
    cache = Path("/Volumes/SV_CACHE/review_reports")
    if cache.parent.is_dir():
        try:
            cache.mkdir(parents=True, exist_ok=True)
            return cache / "content_truth_audio_routing_report.json"
        except OSError:
            pass
    fallback = _REPO / "logs"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback / "content_truth_audio_routing_report.json"


def _iter_json_files() -> list[Path]:
    found: list[Path] = []
    for root in _SCAN_ROOTS:
        if not root.is_dir():
            continue
        for name in _JSON_NAMES:
            for p in root.rglob(name):
                if p.is_file():
                    found.append(p)
        if len(found) >= _MAX_FILES:
            break
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return found[:_MAX_FILES]


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _title_blob(data: dict[str, Any]) -> str:
    for key in ("title", "generated_title"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _theme(data: dict[str, Any]) -> str:
    return str(data.get("inferred_theme") or data.get("title_theme") or "").strip().lower()


def _tod(data: dict[str, Any]) -> str:
    ev = data.get("title_evidence")
    if isinstance(ev, dict):
        return str(ev.get("time_of_day") or "").strip().lower()
    return str(data.get("time_of_day") or "").strip().lower()


def _check_record(path: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    title = _title_blob(data).lower()
    theme = _theme(data)
    tod = _tod(data)
    audio_mode = str(data.get("audio_mode") or "").strip().lower()
    music_required = data.get("music_required")

    if tod in ("day", "morning", "afternoon") and re.search(r"\b(night|evening|sunset)\b", title):
        issues.append({"type": "day_title_has_night_terms", "path": str(path), "title": title, "time_of_day": tod})

    if "ferry" in title and theme != "ferry":
        issues.append({"type": "ferry_title_wrong_theme", "path": str(path), "title": title, "inferred_theme": theme})

    if re.search(r"\b(driving|drive)\b", title) and theme != "driving":
        issues.append({"type": "driving_title_wrong_theme", "path": str(path), "title": title, "inferred_theme": theme})

    if theme == "driving":
        if music_required is not True:
            issues.append(
                {
                    "type": "driving_missing_music_required",
                    "path": str(path),
                    "music_required": music_required,
                }
            )
        if audio_mode and audio_mode != "music_first":
            issues.append(
                {
                    "type": "driving_wrong_audio_mode",
                    "path": str(path),
                    "audio_mode": audio_mode,
                    "expected": "music_first",
                }
            )

    if theme == "ferry":
        if music_required is True:
            issues.append(
                {
                    "type": "ferry_music_required_true",
                    "path": str(path),
                    "music_required": music_required,
                }
            )
        if audio_mode and audio_mode != "real_ambience_primary":
            issues.append(
                {
                    "type": "ferry_wrong_audio_mode",
                    "path": str(path),
                    "audio_mode": audio_mode,
                    "expected": "real_ambience_primary",
                }
            )

    return issues


def _sample_titles() -> dict[str, str]:
    try:
        from content_routing_v1 import build_content_routing_bundle, pick_title_template  # noqa: WPS433

        driving = build_content_routing_bundle(
            {"path": "/Volumes/SV_CACHE/inbox/driving/sample.mov"},
            job_id="diagnose_driving",
        )
        ferry = build_content_routing_bundle(
            {"path": "/Volumes/SV_TRANSFER/00_INBOX/iphone/ferry/sample.mov"},
            job_id="diagnose_ferry",
        )
        unknown = build_content_routing_bundle({"path": "/tmp/nyc_generic_clip.mov"}, job_id="diagnose_unknown")
        return {
            "driving_title": str(driving.get("title") or pick_title_template("driving", "x")),
            "ferry_title": str(ferry.get("title") or pick_title_template("ferry", "x")),
            "unknown_title": str(unknown.get("title") or pick_title_template("unknown", "x")),
            "driving_audio_mode": str(driving.get("audio_mode") or ""),
            "ferry_audio_mode": str(ferry.get("audio_mode") or ""),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "driving_title": "",
            "ferry_title": "",
            "unknown_title": "",
            "driving_audio_mode": "",
            "ferry_audio_mode": "",
            "sample_error": str(exc),
        }


def main() -> int:
    warnings: list[str] = []
    cfg_path = _REPO / "config" / "stateverge_content_routing_v1.json"
    module_path = _NYC / "content_routing_v1.py"
    config_ok = cfg_path.is_file()
    module_ok = module_path.is_file()

    scanned: list[str] = []
    mismatches: list[dict[str, Any]] = []
    for jp in _iter_json_files():
        data = _load(jp)
        if not data:
            continue
        scanned.append(str(jp))
        mismatches.extend(_check_record(jp, data))
        nested = data.get("result")
        if isinstance(nested, dict):
            mismatches.extend(_check_record(jp, nested))

    samples = _sample_titles()
    ready = config_ok and module_ok and not samples.get("sample_error")

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "content_truth_audio_routing_v1_ready": ready,
        "config_path": str(cfg_path),
        "module_path": str(module_path),
        "config_ok": config_ok,
        "module_ok": module_ok,
        "scanned_files_count": len(scanned),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:200],
        "samples": samples,
        "warnings": warnings,
        "scanned_roots": [str(r) for r in _SCAN_ROOTS if r.is_dir()],
    }

    out_json = _report_json_path()
    try:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        fallback = _REPO / "logs" / "content_truth_audio_routing_report.json"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        out_json = fallback
        warnings.append("sv_cache_report_write_failed_used_repo_logs")

    lines = [
        "# Content Truth & Audio Routing v1",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- ready: {ready}",
        f"- config_ok: {config_ok}",
        f"- module_ok: {module_ok}",
        f"- scanned_files: {len(scanned)}",
        f"- mismatches: {len(mismatches)}",
        "",
        "## samples",
        f"- driving title: {samples.get('driving_title', '')}",
        f"- ferry title: {samples.get('ferry_title', '')}",
        f"- unknown title: {samples.get('unknown_title', '')}",
        f"- driving audio_mode: {samples.get('driving_audio_mode', '')}",
        f"- ferry audio_mode: {samples.get('ferry_audio_mode', '')}",
        "",
        "## mismatches (first 50)",
    ]
    if mismatches:
        for m in mismatches[:50]:
            lines.append(f"- `{m.get('type')}` — {m.get('path', '')}")
    else:
        lines.append("- (none in scanned window)")
    lines.extend(["", f"JSON: `{out_json}`"])
    _REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "CONTENT_TRUTH_AUDIO_ROUTING_V1_READY": ready,
                "REPORT_JSON": str(out_json),
                "REPORT_MD": str(_REPORT_MD),
                "mismatch_count": len(mismatches),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
