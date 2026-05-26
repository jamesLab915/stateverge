#!/usr/bin/env python3
"""Write markdown + JSON reports for DaVinci Folder Studio v1."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import REPORTS_ROOT


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_report(job: dict[str, Any]) -> dict[str, str]:
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    slug = str(job.get("output_name") or "render")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = REPORTS_ROOT / f"{slug}_{ts}"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")

    payload = {
        "version": "davinci_folder_studio_v1",
        "timestamp": _utc_iso(),
        **job,
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# DaVinci Folder Studio v1 Report",
        "",
        f"- **Timestamp (UTC)**: {payload['timestamp']}",
        f"- **Output name**: {slug}",
        f"- **Input folder**: `{job.get('input_folder', '')}`",
        f"- **Audio mode**: {job.get('audio_mode', '')}",
        f"- **Add music**: {job.get('add_music', False)}",
        f"- **Audio protection**: `{job.get('audio_protection_policy', '')}`",
        f"- **Render status**: {job.get('render_status', '—')}",
        f"- **Audio status**: {job.get('audio_status', '—')}",
        f"- **Filter chain**: `{job.get('audio_filter_chain', '') or '—'}`",
        f"- **Original vol / Music vol**: {job.get('original_audio_volume', '—')} / {job.get('music_volume', '—')}",
        f"- **Per-clip filter**: `{((job.get('audio_policy') or {}).get('per_clip_audio_filter')) or '—'}`",
        f"- **Heavily processed**: {job.get('audio_was_heavily_processed', False)} · "
        f"denoise={job.get('denoise_used', False)} · demucs={job.get('demucs_used', False)} · "
        f"loudnorm={job.get('loudnorm_used', False)} (target={job.get('loudnorm_target')})",
        f"- **Clip count**: {job.get('clip_count', 0)}",
        f"- **Skipped clips (normalize)**: {job.get('failed_normalize_count', 0)}",
        f"- **Normalize cache hits**: {job.get('normalize_cache_hits', 0)}",
        f"- **Strict mode (fail on clip error)**: {job.get('fail_on_clip_error', False)}",
        f"- **Render path**: `{job.get('render_path', '')}`",
        f"- **Dry run**: {job.get('dry_run', False)}",
        f"- **Resolve used**: {job.get('resolve_used', False)}",
        f"- **FFmpeg fallback**: {job.get('ffmpeg_fallback', False)}",
        "",
        "## Status",
        "",
        f"- **ok**: {job.get('ok', False)}",
        f"- **error**: {job.get('error', '') or '—'}",
        "",
    ]
    skipped = job.get("skipped_clips") or []
    failed_paths = job.get("failed_normalize_paths") or []
    suggested = str(job.get("suggested_action") or "").strip()
    if skipped or failed_paths:
        lines.extend(
            [
                "## Skipped clips (normalize failure)",
                "",
                f"- **Count**: {job.get('failed_normalize_count', len(skipped))}",
            ]
        )
        for name in skipped:
            lines.append(f"- `{name}`")
        if failed_paths:
            lines.append("")
            lines.append("### Failed source paths")
            lines.append("")
            for fp in failed_paths:
                lines.append(f"- `{fp}`")
        if suggested:
            lines.extend(
                [
                    "",
                    f"**Suggested action**: {suggested}",
                ]
            )
        lines.append("")
    lines.extend(
        [
        "## Warnings",
        "",
    ]
    )
    policy = job.get("audio_policy") or {}
    for w in policy.get("warnings") or []:
        lines.append(f"- {w}")
    skipped = policy.get("skipped_aggressive_filters") or []
    if skipped:
        lines.append("")
        lines.append("## Skipped aggressive filters (ferry / preserve policy)")
        lines.append("")
        for s in skipped:
            lines.append(f"- `{s}`")
    lines.append("")
    lines.append("## Job warnings")
    lines.append("")
    for w in job.get("warnings") or []:
        lines.append(f"- {w}")
    if not job.get("warnings") and not policy.get("warnings"):
        lines.append("- _(none)_")

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    bundle_dir = REPORTS_ROOT / f"{slug}_{ts}"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    structured = _write_structured_bundle(bundle_dir, job, payload)
    out = {"json": str(json_path), "markdown": str(md_path), **structured}
    return out


def _write_structured_bundle(bundle_dir: Path, job: dict[str, Any], payload: dict[str, Any]) -> dict[str, str]:
    """Per-job report bundle: timeline_manifest, audio, assets, render, metadata preview."""
    paths: dict[str, str] = {}
    slug = str(job.get("output_name") or "render")
    plan_path = str(job.get("timeline_plan_path") or "")
    timeline_manifest: dict[str, Any] = {"version": "davinci_folder_studio_v1", "timeline_plan_path": plan_path}
    if plan_path:
        try:
            timeline_manifest["plan"] = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            timeline_manifest["plan"] = {}
    p = bundle_dir / "timeline_manifest.json"
    p.write_text(json.dumps(timeline_manifest, indent=2) + "\n", encoding="utf-8")
    paths["timeline_manifest"] = str(p)

    if plan_path:
        try:
            plan_src = Path(plan_path)
            plan_copy = bundle_dir / "timeline_plan.json"
            if plan_src.is_file():
                plan_copy.write_text(plan_src.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                plan_copy.write_text(
                    json.dumps(timeline_manifest.get("plan") or {}, indent=2) + "\n",
                    encoding="utf-8",
                )
            paths["timeline_plan"] = str(plan_copy)
        except OSError:
            pass

    audio_report = dict(job.get("audio_policy") or {})
    audio_report.setdefault("audio_mode", job.get("audio_mode"))
    audio_report.setdefault("audio_filter_chain", job.get("audio_filter_chain"))
    p = bundle_dir / "audio_report.json"
    p.write_text(json.dumps(audio_report, indent=2) + "\n", encoding="utf-8")
    paths["audio_report"] = str(p)

    selected = {
        "clip_paths": list(job.get("clip_paths") or []),
        "input_folder": job.get("input_folder"),
        "selection_mode": job.get("selection_mode"),
        "clip_count": job.get("clip_count", 0),
    }
    p = bundle_dir / "selected_assets.json"
    p.write_text(json.dumps(selected, indent=2) + "\n", encoding="utf-8")
    paths["selected_assets"] = str(p)

    render_report = {
        "render_status": job.get("render_status"),
        "render_path": job.get("render_path"),
        "resolve_used": job.get("resolve_used"),
        "ffmpeg_fallback": job.get("ffmpeg_fallback"),
        "skipped_clips": job.get("skipped_clips"),
        "failed_normalize_count": job.get("failed_normalize_count"),
        "ok": job.get("ok"),
        "error": job.get("error"),
    }
    p = bundle_dir / "render_report.json"
    p.write_text(json.dumps(render_report, indent=2) + "\n", encoding="utf-8")
    paths["render_report"] = str(p)

    meta_preview = {
        "title": f"StateVerge NYC — {slug.replace('_', ' ').title()}",
        "privacy_status": "unlisted",
        "upload_disabled": True,
        "note": "Studio preview only — no auto-upload",
        "audio_mode": job.get("audio_mode"),
        "output_name": slug,
    }
    p = bundle_dir / "youtube_metadata_preview.json"
    p.write_text(json.dumps(meta_preview, indent=2) + "\n", encoding="utf-8")
    paths["youtube_metadata_preview"] = str(p)
    paths["bundle_dir"] = str(bundle_dir)
    return paths


def latest_report_paths(limit: int = 5) -> list[dict[str, str]]:
    if not REPORTS_ROOT.is_dir():
        return []
    items: list[tuple[float, Path]] = []
    for p in REPORTS_ROOT.glob("*.json"):
        try:
            items.append((p.stat().st_mtime, p))
        except OSError:
            continue
    items.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, str]] = []
    for _, p in items[:limit]:
        md = p.with_suffix(".md")
        out.append(
            {
                "json": str(p),
                "markdown": str(md) if md.is_file() else "",
                "mtime": str(p.stat().st_mtime),
            }
        )
    return out
