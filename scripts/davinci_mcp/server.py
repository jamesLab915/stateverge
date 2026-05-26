#!/usr/bin/env python3
"""Preset-driven DaVinci Resolve bridge for Folder Studio v1.

Connect Resolve, create project/timeline, append clips, queue render.
No AI EQ, Fairlight voice isolation, or aggressive audio processing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_STUDIO_DIR = _SCRIPT_DIR.parent / "davinci_studio"
if str(_STUDIO_DIR) not in sys.path:
    sys.path.insert(0, str(_STUDIO_DIR))

from resolve_bridge import (  # noqa: E402
    open_or_create_resolve_project,
    probe_resolve_api,
    sanitize_resolve_name,
)
from resolve_paths import configure_resolve_paths, resolved_paths_dict  # noqa: E402

configure_resolve_paths()

# H.264 CFR 30fps yuv420p AAC 192k faststart — matches davinci_studio.paths
RENDER_PRESET: dict[str, Any] = {
    "Format": "mp4",
    "VideoCodec": "H264",
    "AudioCodec": "AAC",
    "AudioBitDepth": "16",
    "AudioSampleRate": "48000",
    "ExportVideo": True,
    "ExportAudio": True,
    "EncodingProfile": "High",
    "NetworkOptimization": True,
}


def _connect_resolve() -> tuple[Any, dict[str, Any]]:
    status = probe_resolve_api()
    if not status.get("api_available"):
        return None, {"ok": False, "error": "resolve_api_unavailable", "resolve": status}
    if not status.get("resolve_connected"):
        return None, {"ok": False, "error": "resolve_not_connected", "resolve": status}
    try:
        import DaVinciResolveScript as dvr  # type: ignore[import-not-found]

        resolve = dvr.scriptapp("Resolve")
    except Exception as exc:  # noqa: BLE001
        return None, {"ok": False, "error": f"scriptapp_failed:{exc!r}", "resolve": status}
    if not resolve:
        return None, {"ok": False, "error": "resolve_null", "resolve": status}
    return resolve, {"ok": True, "resolve": status}


def create_project_timeline(
    *,
    project_name: str,
    timeline_name: str,
    clip_paths: list[str],
) -> dict[str, Any]:
    """Create project, import clips, append to new timeline."""
    resolve, meta = _connect_resolve()
    if resolve is None:
        return meta

    warnings: list[str] = []
    try:
        pm = resolve.GetProjectManager()
        safe_project = sanitize_resolve_name(project_name)
        safe_timeline = sanitize_resolve_name(timeline_name)
        project, proj_meta = open_or_create_resolve_project(pm, safe_project, warnings)
        if project is None:
            return {
                "ok": False,
                "error": "create_project_failed",
                "warnings": warnings,
                "project_meta": proj_meta,
            }

        media_pool = project.GetMediaPool()
        paths = [str(Path(p).expanduser()) for p in clip_paths if p]
        imported = media_pool.ImportMedia(paths) if paths else None
        timeline = media_pool.CreateEmptyTimeline(safe_timeline)
        appended = 0
        if timeline and imported:
            if media_pool.AppendToTimeline(imported):
                appended = len(imported) if isinstance(imported, list) else 1
            else:
                warnings.append("append_to_timeline_failed")
        elif not timeline:
            warnings.append("create_timeline_failed")

        if timeline and hasattr(project, "SetCurrentTimeline"):
            try:
                project.SetCurrentTimeline(timeline)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"set_current_timeline:{exc!r}")

        return {
            "ok": bool(timeline),
            "project_name": proj_meta.get("resolved_name", safe_project),
            "timeline_name": safe_timeline,
            "imported_count": len(imported) if imported else 0,
            "appended_count": appended,
            "warnings": warnings,
            "project_meta": proj_meta,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc), "warnings": warnings}


def start_render(
    *,
    target_dir: str,
    custom_name: str,
    preset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue a render job using preset settings only."""
    resolve, meta = _connect_resolve()
    if resolve is None:
        return meta

    settings = dict(RENDER_PRESET)
    if preset:
        settings.update(preset)
    settings["TargetDir"] = str(Path(target_dir).expanduser())
    settings["CustomName"] = custom_name

    warnings: list[str] = []
    try:
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if not project:
            return {"ok": False, "error": "no_current_project", "warnings": warnings}

        if not hasattr(project, "SetRenderSettings") or not hasattr(project, "AddRenderJob"):
            return {"ok": False, "error": "render_api_unavailable", "warnings": warnings}

        project.SetRenderSettings(settings)
        job_id = project.AddRenderJob()
        if not job_id:
            return {"ok": False, "error": "add_render_job_failed", "warnings": warnings}

        started = False
        if hasattr(project, "StartRendering"):
            started = bool(project.StartRendering(job_id))

        return {
            "ok": True,
            "render_job_id": job_id,
            "render_started": started,
            "target_dir": settings["TargetDir"],
            "custom_name": custom_name,
            "preset": settings,
            "warnings": warnings,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc), "warnings": warnings}


def run_pipeline(
    *,
    project_name: str,
    timeline_name: str,
    clip_paths: list[str],
    target_dir: str,
    custom_name: str,
) -> dict[str, Any]:
    """Full preset pipeline: project → timeline → clips → render queue."""
    tl = create_project_timeline(
        project_name=project_name,
        timeline_name=timeline_name,
        clip_paths=clip_paths,
    )
    if not tl.get("ok"):
        return {"ok": False, "stage": "timeline", **tl}

    render = start_render(target_dir=target_dir, custom_name=custom_name)
    return {
        "ok": bool(render.get("ok")),
        "timeline": tl,
        "render": render,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Probe Resolve API")
    p_status.set_defaults(cmd="status")

    p_paths = sub.add_parser("paths", help="Print resolved Resolve scripting paths")
    p_paths.set_defaults(cmd="paths")

    p_tl = sub.add_parser("timeline", help="Create project + timeline + append clips")
    p_tl.add_argument("--project", required=True)
    p_tl.add_argument("--timeline", required=True)
    p_tl.add_argument("--clip", action="append", dest="clips", default=[])
    p_tl.set_defaults(cmd="timeline")

    p_render = sub.add_parser("render", help="Queue render on current project")
    p_render.add_argument("--target-dir", required=True)
    p_render.add_argument("--name", required=True)
    p_render.set_defaults(cmd="render")

    p_all = sub.add_parser("pipeline", help="timeline + render")
    p_all.add_argument("--project", required=True)
    p_all.add_argument("--timeline", required=True)
    p_all.add_argument("--clip", action="append", dest="clips", default=[])
    p_all.add_argument("--target-dir", required=True)
    p_all.add_argument("--name", required=True)
    p_all.set_defaults(cmd="pipeline")

    args = ap.parse_args()

    if args.cmd == "paths":
        out = {"ok": True, **resolved_paths_dict()}
    elif args.cmd == "status":
        out = probe_resolve_api()
    elif args.cmd == "timeline":
        out = create_project_timeline(
            project_name=args.project,
            timeline_name=args.timeline,
            clip_paths=args.clips or [],
        )
    elif args.cmd == "render":
        out = start_render(target_dir=args.target_dir, custom_name=args.name)
    else:
        out = run_pipeline(
            project_name=args.project,
            timeline_name=args.timeline,
            clip_paths=args.clips or [],
            target_dir=args.target_dir,
            custom_name=args.name,
        )

    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
