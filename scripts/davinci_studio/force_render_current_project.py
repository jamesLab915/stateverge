#!/usr/bin/env python3
"""Force-render the current (or named) DaVinci Resolve project timeline to MP4.

Requires Resolve Studio running with External Scripting = Local.
Does not modify source media. Writes to finished_for_youtube by default.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from paths import FINISHED_FOR_YOUTUBE_ROOT  # noqa: E402
from resolve_bridge import open_or_create_resolve_project, probe_resolve_api, sanitize_resolve_name  # noqa: E402

_RENDER_POLL_SEC = 2.0
_DEFAULT_TIMEOUT_SEC = 7200.0


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _poll_render(project: object, output_path: Path, *, timeout_sec: float) -> dict:
    meta: dict = {"ok": False, "output_path": str(output_path)}
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            in_progress = False
            if hasattr(project, "IsRenderingInProgress"):
                in_progress = bool(project.IsRenderingInProgress())
            elif hasattr(project, "IsRendering"):
                in_progress = bool(project.IsRendering())
            if output_path.is_file() and output_path.stat().st_size > 4096:
                meta["ok"] = True
                meta["bytes"] = output_path.stat().st_size
                return meta
            if not in_progress and output_path.is_file() and output_path.stat().st_size > 4096:
                meta["ok"] = True
                meta["bytes"] = output_path.stat().st_size
                return meta
        except OSError as exc:
            meta["poll_error"] = repr(exc)
        time.sleep(_RENDER_POLL_SEC)
    meta["block_reason"] = "resolve_render_timeout"
    return meta


def _latest_sv_project_name(pm: object) -> str | None:
    """Pick newest SV_FolderStudio_* / SV_ProDaVinci_* project from Resolve list."""
    names: list[str] = []
    for meth in ("GetProjectListInCurrentFolder", "GetProjectList"):
        if hasattr(pm, meth):
            try:
                fn = getattr(pm, meth)
                items = fn() if callable(fn) else fn
                if isinstance(items, list):
                    names = [str(x) for x in items if x]
                    break
            except Exception:  # noqa: BLE001
                pass
    candidates = [n for n in names if n.startswith(("SV_FolderStudio_", "SV_ProDaVinci_"))]
    if not candidates:
        return names[-1] if names else None
    return sorted(candidates)[-1]


def force_render(
    *,
    project_name: str | None,
    output_name: str | None,
    output_dir: Path,
    timeout_sec: float,
    overwrite: bool,
    audio_only: bool = False,
) -> dict:
    status = probe_resolve_api()
    result: dict = {
        "resolve_connected": bool(status.get("resolve_connected")),
        "ok": False,
        "render_success": False,
    }
    if not status.get("resolve_connected"):
        result["block_reason"] = "davinci_api_unavailable"
        result["hint"] = "Open DaVinci Resolve Studio and enable Preferences → System → External scripting: Local"
        return result

    import DaVinciResolveScript as dvr  # type: ignore[import-not-found]

    resolve_app = dvr.scriptapp("Resolve")
    pm = resolve_app.GetProjectManager()
    warnings: list[str] = []

    project = None
    if project_name:
        pmeta: dict = {"requested_name": project_name}
        # Prefer exact Resolve project name (spaces allowed in gallery).
        for exact in (project_name, project_name.strip()):
            try:
                loaded = pm.LoadProject(exact)
                if loaded:
                    project = loaded
                    pmeta["resolved_name"] = exact
                    pmeta["opened_via"] = "LoadProject_exact"
                    break
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"LoadProject_exact:{exact}:{exc!r}")
        if project is None:
            project, pmeta = open_or_create_resolve_project(pm, project_name, warnings)
        result["project_meta"] = pmeta
    else:
        project = pm.GetCurrentProject() if hasattr(pm, "GetCurrentProject") else None
        if project:
            result["opened_via"] = "GetCurrentProject"
        else:
            latest = _latest_sv_project_name(pm)
            if latest:
                project, pmeta = open_or_create_resolve_project(pm, latest, warnings)
                result["project_meta"] = pmeta
                result["loaded_latest"] = latest

    if not project:
        result["block_reason"] = "no_resolve_project"
        result["warnings"] = warnings
        return result

    timeline = project.GetCurrentTimeline() if hasattr(project, "GetCurrentTimeline") else None
    if not timeline and hasattr(project, "GetTimelineCount") and hasattr(project, "GetTimelineByIndex"):
        try:
            count = int(project.GetTimelineCount())
            for idx in range(1, count + 1):
                tl = project.GetTimelineByIndex(idx)
                if tl:
                    if hasattr(project, "SetCurrentTimeline"):
                        project.SetCurrentTimeline(tl)
                    timeline = tl
                    result["timeline_auto_selected"] = idx
                    break
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"timeline_enumerate:{exc!r}")
    if not timeline:
        result["block_reason"] = "no_current_timeline"
        result["hint"] = "Open the timeline you edited in Resolve, then run again"
        return result

    tl_name = timeline.GetName() if hasattr(timeline, "GetName") else "timeline"
    result["timeline_name"] = tl_name
    out_stem = output_name or sanitize_resolve_name(f"{tl_name}_{_utc_stamp()}")
    output_path = output_dir / (f"{out_stem}.wav" if audio_only else f"{out_stem}.mp4")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.is_file() and not overwrite:
        result["block_reason"] = "output_exists"
        result["output_path"] = str(output_path)
        return result

    settings = {
        "SelectAllFrames": True,
        "TargetDir": str(output_path.parent),
        "CustomName": output_path.stem,
        "Format": "mp4",
        "VideoCodec": "H264",
        "AudioCodec": "aac",
        "ExportVideo": not audio_only,
        "ExportAudio": True,
        "FormatWidth": 1920,
        "FormatHeight": 1080,
        "FrameRate": 30.0,
        "AudioSampleRate": 48000,
        "NetworkOptimization": True,
    }
    render_preset = "Audio Only" if audio_only else "YouTube - 1080p"
    try:
        if hasattr(resolve_app, "OpenPage"):
            resolve_app.OpenPage("deliver")
            time.sleep(0.5)
        if hasattr(project, "SetCurrentRenderFormatAndCodec"):
            project.SetCurrentRenderFormatAndCodec("mp4", "H264")
        if hasattr(project, "LoadRenderPreset"):
            loaded = project.LoadRenderPreset(render_preset)
            result["render_preset"] = render_preset
            result["render_preset_loaded"] = bool(loaded)
            if not loaded and hasattr(project, "GetRenderPresetList"):
                presets = project.GetRenderPresetList() or []
                for fallback in ("H.264 Master", "YouTube - 1080p", "water"):
                    if fallback in presets and project.LoadRenderPreset(fallback):
                        render_preset = fallback
                        result["render_preset"] = fallback
                        result["render_preset_loaded"] = True
                        break
        if hasattr(project, "DeleteAllRenderJobs"):
            project.DeleteAllRenderJobs()
        if hasattr(project, "SetRenderSettings"):
            project.SetRenderSettings(settings)
        render_job_id = project.AddRenderJob() if hasattr(project, "AddRenderJob") else None
        result["render_job_id"] = render_job_id
        result["render_job_list"] = (
            project.GetRenderJobList() if hasattr(project, "GetRenderJobList") else []
        )
        started = False
        if render_job_id and hasattr(project, "StartRendering"):
            try:
                started = bool(project.StartRendering([render_job_id]))
            except TypeError:
                try:
                    started = bool(project.StartRendering(render_job_id))
                except TypeError:
                    started = bool(project.StartRendering())
        elif hasattr(project, "RenderWithQuickExport"):
            qe_preset = str(result.get("render_preset") or "YouTube - 1080p")
            qe = project.RenderWithQuickExport(
                qe_preset,
                {
                    "TargetDir": str(output_path.parent),
                    "CustomName": output_path.stem,
                    "EnableUpload": False,
                },
            )
            result["quick_export"] = qe
            started = isinstance(qe, dict) and qe.get("JobStatus") in (
                "Render Complete",
                "Complete",
                "Rendering",
            )
            if started and qe.get("JobStatus") == "Render Complete":
                poll = _poll_render(project, output_path, timeout_sec=min(600.0, timeout_sec))
                result["render_poll"] = poll
                if poll.get("ok"):
                    result["render_success"] = True
                    result["ok"] = True
                    result["output_path"] = str(output_path)
                    result["warnings"] = warnings
                    return result
        elif hasattr(project, "StartRendering"):
            started = bool(project.StartRendering())
        result["render_started"] = started
        if not started:
            result["block_reason"] = "resolve_render_start_failed"
            result["hint"] = (
                "Open Deliver in Resolve, confirm YouTube - 1080p preset, disk space on SV_TRANSFER"
            )
            return result

        if render_job_id and hasattr(project, "GetRenderJobStatus"):
            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                try:
                    st = project.GetRenderJobStatus(render_job_id)
                    result["last_job_status"] = st
                    if isinstance(st, dict) and st.get("JobStatus") in ("Complete", "Failed"):
                        break
                except Exception:  # noqa: BLE001
                    pass
                if output_path.is_file() and output_path.stat().st_size > 4096:
                    break
                if hasattr(project, "IsRenderingInProgress") and not project.IsRenderingInProgress():
                    if output_path.is_file():
                        break
                time.sleep(2.0)

        poll = _poll_render(project, output_path, timeout_sec=timeout_sec)
        result["render_poll"] = poll
        ok = bool(poll.get("ok"))
        result["render_success"] = ok
        result["ok"] = ok
        result["output_path"] = str(output_path)
        if not ok:
            result["block_reason"] = poll.get("block_reason") or "resolve_render_failed"
    except Exception as exc:  # noqa: BLE001
        result["block_reason"] = "resolve_render_exception"
        result["error"] = repr(exc)

    result["warnings"] = warnings
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Force render current DaVinci Resolve timeline")
    ap.add_argument("--project-name", help="Load this Resolve project (default: current or latest SV_*)")
    ap.add_argument("--output-name", help="Output filename stem (no extension)")
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=FINISHED_FOR_YOUTUBE_ROOT,
        help="Render output directory",
    )
    ap.add_argument("--timeout-sec", type=float, default=_DEFAULT_TIMEOUT_SEC)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument(
        "--audio-only",
        action="store_true",
        help="Use Deliver preset Audio Only (ExportVideo=false)",
    )
    ap.add_argument("--launch-resolve", action="store_true", help="Try open Resolve Studio.app first")
    ap.add_argument("--wait-sec", type=float, default=25.0, help="Wait after launch before API connect")
    args = ap.parse_args()

    if args.launch_resolve:
        import subprocess

        subprocess.run(
            ["open", "-a", "DaVinci Resolve Studio"],
            check=False,
        )
        print(f"Waiting {args.wait_sec}s for Resolve…", flush=True)
        time.sleep(max(5.0, args.wait_sec))

    result = force_render(
        project_name=args.project_name,
        output_name=args.output_name,
        output_dir=args.output_dir.expanduser(),
        timeout_sec=args.timeout_sec,
        overwrite=args.overwrite,
        audio_only=args.audio_only,
    )
    report_dir = Path("/Volumes/SV_CACHE/davinci_studio/reports") / f"force_render_{_utc_stamp()}"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "force_render_report.json"
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"report={report_path}")
    if result.get("ok"):
        print("FORCE_RESOLVE_RENDER_READY=true")
        return 0
    print(f"FORCE_RESOLVE_RENDER_READY=false block_reason={result.get('block_reason')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
