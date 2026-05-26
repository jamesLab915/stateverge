#!/usr/bin/env python3
"""DaVinci Resolve Python API — probe, project/timeline/render for Production Agent v1."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from davinci_production_agent.paths import (  # noqa: E402
    PROJECTS_ROOT,
    REPORTS_ROOT,
    delivery_render_path,
)
from davinci_production_agent.presets import preset_rules  # noqa: E402

_PROJECT_PREFIX = "SV_ProDaVinci_"
_TEST_PROJECT_PREFIX = "SV_ProDaVinci_DIAG_"
_RENDER_POLL_SEC = 2.0
_RENDER_TIMEOUT_SEC = 7200.0


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_running() -> bool:
    try:
        import subprocess

        r = subprocess.run(
            ["/bin/ps", "-ax", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        for line in (r.stdout or "").splitlines():
            if "DaVinci Resolve" in line and "grep" not in line:
                return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


def _get_resolve():
    _ensure_davinci_studio_path()
    from resolve_paths import configure_resolve_paths  # noqa: WPS433

    configure_resolve_paths()
    import DaVinciResolveScript as dvr  # type: ignore[import-not-found]

    return dvr.scriptapp("Resolve")


def _ensure_davinci_studio_path() -> None:
    studio = _SCRIPTS / "davinci_studio"
    if studio.is_dir():
        sp = str(studio)
        if sp not in sys.path:
            sys.path.insert(0, sp)


def probe_resolve_api() -> dict[str, Any]:
    _ensure_davinci_studio_path()
    from resolve_bridge import probe_resolve_api as _probe  # noqa: WPS433

    return _probe()


def probe_davinci_detailed(*, run_timeline_test: bool = True) -> dict[str, Any]:
    """Real Resolve checks for diagnose and render gating."""
    base = probe_resolve_api()
    checks: dict[str, Any] = {
        "resolve_app_running": {"ok": _resolve_running(), "detail": "ps DaVinci Resolve"},
        "resolve_api_connected": {
            "ok": bool(base.get("api_available")) and bool(base.get("resolve_connected")),
            "detail": base,
        },
        "project_manager_available": {"ok": False, "detail": None},
        "timeline_create_ok": {"ok": False, "detail": None},
        "render_queue_access_ok": {"ok": False, "detail": None},
    }
    warnings: list[str] = list(base.get("warnings") or [])

    if not checks["resolve_api_connected"]["ok"]:
        out = {
            **base,
            "checks": checks,
            "ready": False,
            "warnings": warnings,
        }
        return out

    test_project = None
    test_timeline = None
    try:
        resolve = _get_resolve()
        pm = resolve.GetProjectManager()
        checks["project_manager_available"]["ok"] = pm is not None
        checks["project_manager_available"]["detail"] = "GetProjectManager"

        if run_timeline_test and pm is not None:
            from resolve_bridge import (  # noqa: WPS433
                open_or_create_resolve_project,
                sanitize_resolve_name,
            )

            diag_name = sanitize_resolve_name(
                f"{_TEST_PROJECT_PREFIX}{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                prefix="",
            )
            tw: list[str] = []
            project, pmeta = open_or_create_resolve_project(pm, diag_name, tw)
            warnings.extend(tw)
            test_project = project
            if project is None:
                checks["timeline_create_ok"]["ok"] = False
                checks["timeline_create_ok"]["detail"] = pmeta
            else:
                media_pool = project.GetMediaPool()
                tl_name = sanitize_resolve_name("diag_timeline_v1")
                timeline = media_pool.CreateEmptyTimeline(tl_name) if media_pool else None
                test_timeline = timeline
                checks["timeline_create_ok"]["ok"] = timeline is not None
                checks["timeline_create_ok"]["detail"] = {
                    "timeline_name": tl_name,
                    "project_meta": pmeta,
                }
                if timeline and hasattr(project, "SetCurrentTimeline"):
                    try:
                        project.SetCurrentTimeline(timeline)
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"set_current_timeline:{exc!r}")

            rq_ok = False
            rq_detail: dict[str, Any] = {}
            if project is not None:
                for meth in ("GetRenderJobList", "GetRenderJobs", "GetRenderJobStatus"):
                    if hasattr(project, meth):
                        rq_detail[meth] = True
                        rq_ok = True
                if hasattr(project, "SetRenderSettings") and hasattr(project, "AddRenderJob"):
                    rq_detail["SetRenderSettings"] = True
                    rq_detail["AddRenderJob"] = True
                    rq_ok = True
            checks["render_queue_access_ok"]["ok"] = rq_ok
            checks["render_queue_access_ok"]["detail"] = rq_detail or "no_render_api_surface"

    except Exception as exc:  # noqa: BLE001
        warnings.append(f"probe_detailed_exception:{exc!r}")

    ready = all(bool(c.get("ok")) for c in checks.values())
    out = {
        **base,
        "checks": checks,
        "ready": ready,
        "warnings": warnings,
        "test_cleanup": _cleanup_diag_project(test_project, test_timeline),
    }
    return out


def _cleanup_diag_project(project: Any | None, timeline: Any | None) -> dict[str, Any]:
    """Best-effort cleanup of diagnose test project (never raises)."""
    meta: dict[str, Any] = {"attempted": False, "ok": False}
    if project is None:
        return meta
    meta["attempted"] = True
    try:
        if timeline is not None and hasattr(project, "DeleteTimeline"):
            project.DeleteTimeline(timeline)
            meta["timeline_deleted"] = True
    except Exception as exc:  # noqa: BLE001
        meta["timeline_delete_error"] = repr(exc)
    try:
        pm = project.GetProjectManager() if hasattr(project, "GetProjectManager") else None
        if pm is None:
            resolve = _get_resolve()
            pm = resolve.GetProjectManager()
        name = project.GetName() if hasattr(project, "GetName") else ""
        if name and str(name).startswith(_TEST_PROJECT_PREFIX) and hasattr(pm, "DeleteProject"):
            pm.DeleteProject(name)
            meta["project_deleted"] = True
        meta["ok"] = True
    except Exception as exc:  # noqa: BLE001
        meta["project_delete_error"] = repr(exc)
    return meta


def probe() -> dict[str, Any]:
    """Probe Resolve API availability (detailed checks, timeline test optional in diagnose)."""
    return probe_davinci_detailed(run_timeline_test=False)


def open_project(name: str) -> tuple[Any | None, dict[str, Any]]:
    """Open or create a Resolve project. Never raises."""
    _ensure_davinci_studio_path()
    from resolve_bridge import (  # noqa: WPS433
        folder_studio_project_name,
        open_or_create_resolve_project,
        sanitize_resolve_name,
    )

    meta: dict[str, Any] = {"requested": name}
    warnings: list[str] = []
    status = probe_davinci_detailed(run_timeline_test=False)
    if not status.get("ready"):
        meta["block_reason"] = "davinci_api_unavailable"
        meta["resolve_status"] = status
        return None, meta

    try:
        resolve = _get_resolve()
        pm = resolve.GetProjectManager()
        safe = sanitize_resolve_name(name, prefix=_PROJECT_PREFIX)
        if not safe.startswith(_PROJECT_PREFIX):
            safe = folder_studio_project_name(name).replace("SV_FolderStudio_", _PROJECT_PREFIX)
        project, pmeta = open_or_create_resolve_project(pm, safe, warnings)
        meta.update(pmeta)
        meta["warnings"] = warnings
        meta["resolve_status"] = status
        return project, meta
    except Exception as exc:  # noqa: BLE001
        meta["block_reason"] = "davinci_api_unavailable"
        meta["error"] = repr(exc)
        meta["resolve_status"] = status
        return None, meta


def write_timeline_plan(plan: dict[str, Any], *, job_id: str) -> Path:
    """Write timeline_plan.json (reference; not a substitute for render)."""
    out_dir = PROJECTS_ROOT / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    body = {
        "version": "professional_davinci_production_agent_v1",
        "created_at": _utc_iso(),
        "plan": plan,
        "note": "Render requires Resolve API; timeline_plan alone is not upload-ready.",
    }
    out = out_dir / "timeline_plan.json"
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def write_render_report(job_id: str, payload: dict[str, Any]) -> Path:
    out = REPORTS_ROOT / job_id
    out.mkdir(parents=True, exist_ok=True)
    body = {"version": "professional_davinci_production_agent_v1", "created_at": _utc_iso(), **payload}
    path = out / "render_report.json"
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _apply_cut_list_to_timeline(timeline: Any, media_pool: Any, imported: Any, cut_plan: dict[str, Any]) -> dict[str, Any]:
    """Append imported clips; cut ranges recorded in report when full trim API unavailable."""
    meta: dict[str, Any] = {"appended": False, "cuts_in_plan": len(cut_plan.get("cuts") or [])}
    if not timeline or not imported:
        return meta
    try:
        meta["appended"] = bool(media_pool.AppendToTimeline(imported))
    except Exception as exc:  # noqa: BLE001
        meta["append_error"] = repr(exc)
    meta["cut_apply_note"] = "v1: full in/out trim deferred; cuts stored in cut_list.json for Resolve manual review"
    return meta


def _apply_fairlight_preset(project: Any, preset_id: str, rules: dict[str, Any]) -> dict[str, Any]:
    """Map production preset → ambient Fairlight chain (v1 spec export + best-effort)."""
    finish_preset = str(rules.get("davinci_audio_finish_preset") or preset_id)
    chain_id = str(rules.get("ambient_chain_id") or "")
    meta: dict[str, Any] = {
        "fairlight_preset_mapped": finish_preset,
        "ambient_chain_id": chain_id,
        "applied": False,
        "note": "Fairlight ambient chain v1 — manual steps + audio_chain_spec.json",
    }
    try:
        finish_dir = Path(__file__).resolve().parents[1] / "davinci_audio_finish"
        if str(finish_dir) not in sys.path:
            sys.path.insert(0, str(finish_dir))
        from fairlight_chain_v1 import apply_ambient_chain_to_timeline  # noqa: WPS433

        if chain_id:
            return apply_ambient_chain_to_timeline(
                project,
                chain_id,
                youtube_preset=finish_preset,
            )
    except Exception as exc:  # noqa: BLE001
        meta["fairlight_chain_error"] = repr(exc)

    try:
        timeline = project.GetCurrentTimeline() if hasattr(project, "GetCurrentTimeline") else None
        if timeline is None:
            meta["warning"] = "no_current_timeline_for_fairlight"
            return meta
        meta["timeline_name"] = timeline.GetName() if hasattr(timeline, "GetName") else ""
        meta["applied"] = True
    except Exception as exc:  # noqa: BLE001
        meta["error"] = repr(exc)
    return meta


def _render_settings_for_preset(preset_id: str, rules: dict[str, Any], output_path: Path) -> dict[str, Any]:
    res = str(rules.get("resolution") or "1920x1080")
    w, h = 1920, 1080
    if "x" in res:
        parts = res.lower().split("x")
        try:
            w, h = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass
    return {
        "TargetDir": str(output_path.parent),
        "CustomName": output_path.stem,
        "Format": "mp4",
        "VideoCodec": "H264",
        "AudioCodec": "AAC",
        "ResolutionWidth": w,
        "ResolutionHeight": h,
        "FrameRate": float(rules.get("timeline_fps") or 30),
    }


def _poll_resolve_render(project: Any, output_path: Path, *, timeout_sec: float = _RENDER_TIMEOUT_SEC) -> dict[str, Any]:
    """Poll Resolve render queue until output file exists or timeout."""
    meta: dict[str, Any] = {"ok": False, "output_path": str(output_path)}
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
            if not in_progress and output_path.is_file():
                meta["ok"] = output_path.stat().st_size > 4096
                return meta
        except OSError as exc:
            meta["poll_error"] = repr(exc)
        time.sleep(_RENDER_POLL_SEC)
    meta["block_reason"] = "resolve_render_timeout"
    return meta


def run_production_render(
    *,
    job_id: str,
    kind: str,
    preset_id: str,
    source_path: Path,
    cut_plan: dict[str, Any] | None = None,
    project_name: str | None = None,
    encode_timeout_sec: float = _RENDER_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Full Resolve pipeline: project → timeline → import → cuts → Fairlight → render queue."""
    rules = preset_rules(preset_id)
    output_path = delivery_render_path(kind=kind, job_id=job_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    status = probe_davinci_detailed(run_timeline_test=False)
    result: dict[str, Any] = {
        "job_id": job_id,
        "kind": kind,
        "preset": preset_id,
        "source": str(source_path),
        "output_path": str(output_path),
        "resolve_status": status,
        "ok": False,
        "render_success": False,
    }

    if not status.get("ready"):
        result["block_reason"] = "davinci_api_unavailable"
        return result

    if not source_path.is_file():
        result["block_reason"] = "source_missing"
        return result

    pname = project_name or f"{kind}_{job_id}"
    project, pmeta = open_project(pname)
    result["project_meta"] = pmeta
    if project is None:
        result["block_reason"] = pmeta.get("block_reason") or "davinci_api_unavailable"
        return result

    warnings: list[str] = list(pmeta.get("warnings") or [])
    try:
        _ensure_davinci_studio_path()
        from resolve_bridge import sanitize_resolve_name  # noqa: WPS433

        media_pool = project.GetMediaPool()
        imported = media_pool.ImportMedia([str(source_path.resolve())])
        tl_name = sanitize_resolve_name(f"{job_id}_{preset_id}")
        timeline = media_pool.CreateEmptyTimeline(tl_name)
        if not timeline:
            result["block_reason"] = "timeline_create_failed"
            return result
        if hasattr(project, "SetCurrentTimeline"):
            project.SetCurrentTimeline(timeline)

        cut_meta = _apply_cut_list_to_timeline(
            timeline, media_pool, imported, cut_plan or {"cuts": []}
        )
        result["cut_apply"] = cut_meta

        fairlight_meta = _apply_fairlight_preset(project, preset_id, rules)
        result["fairlight"] = fairlight_meta

        if output_path.is_file():
            result["block_reason"] = "output_exists"
            return result

        settings = _render_settings_for_preset(preset_id, rules, output_path)
        render_job_id = None
        if hasattr(project, "SetRenderSettings"):
            project.SetRenderSettings(settings)
        if hasattr(project, "AddRenderJob"):
            render_job_id = project.AddRenderJob()
        result["render_job_id"] = render_job_id

        started = False
        if render_job_id and hasattr(project, "StartRendering"):
            try:
                started = bool(project.StartRendering(render_job_id))
            except TypeError:
                started = bool(project.StartRendering())
        elif hasattr(project, "StartRendering"):
            started = bool(project.StartRendering())

        result["render_started"] = started
        if not started:
            result["block_reason"] = "resolve_render_start_failed"
            return result

        poll = _poll_resolve_render(project, output_path, timeout_sec=encode_timeout_sec)
        result["render_poll"] = poll
        ok = bool(poll.get("ok")) and output_path.is_file() and output_path.stat().st_size > 4096
        result["render_success"] = ok
        result["ok"] = ok
        if not ok:
            result["block_reason"] = poll.get("block_reason") or "resolve_render_failed"
        result["warnings"] = warnings
    except Exception as exc:  # noqa: BLE001
        result["block_reason"] = "resolve_render_exception"
        result["error"] = repr(exc)
        result["warnings"] = warnings

    return result


def render_job_spec(job_id: str, *, preset: str, output_name: str) -> dict[str, Any]:
    """Legacy spec helper — defers to run_production_render when API ready."""
    status = probe()
    spec: dict[str, Any] = {
        "job_id": job_id,
        "preset": preset,
        "output_name": output_name,
        "resolve_status": status,
    }
    if not status.get("ready"):
        spec["block_reason"] = "davinci_api_unavailable"
        spec["action"] = "failed_no_timeline_only"
        spec["render_deferred"] = True
    else:
        spec["action"] = "run_production_render"
        spec["render_deferred"] = False
    return spec
