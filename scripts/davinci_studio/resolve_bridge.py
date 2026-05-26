#!/usr/bin/env python3
"""DaVinci Resolve API bridge — create project/timeline or fail-open manifests."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from resolve_paths import configure_resolve_paths, resolved_paths_dict

configure_resolve_paths()

RESOLVE_NAME_MAX_LEN = 64
_PROJECT_PREFIX = "SV_FolderStudio_"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def probe_resolve_api() -> dict[str, Any]:
    configure_resolve_paths()
    warnings: list[str] = []
    paths = resolved_paths_dict()
    out: dict[str, Any] = {
        "api_available": False,
        "resolve_connected": False,
        "product_name": None,
        "version": None,
        "paths": paths,
        "warnings": warnings,
    }
    if not paths.get("resolve_script_lib"):
        warnings.append("resolve_script_lib_not_found")
    if not paths.get("modules_dir"):
        warnings.append("resolve_modules_dir_not_found")
    dvr_script = None
    try:
        import DaVinciResolveScript as dvr_script  # type: ignore[import-not-found]
    except ImportError as exc:
        warnings.append(f"import DaVinciResolveScript failed: {exc}")
        try:
            spec = importlib.util.find_spec("DaVinciResolveScript")
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                dvr_script = mod
        except Exception as exc2:  # noqa: BLE001
            warnings.append(f"importlib fallback failed: {exc2}")

    if dvr_script is None or not hasattr(dvr_script, "scriptapp"):
        return out

    out["api_available"] = True
    try:
        resolve = dvr_script.scriptapp("Resolve")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"scriptapp failed: {exc}")
        return out

    out["resolve_connected"] = bool(resolve)
    if not resolve:
        return out

    try:
        out["product_name"] = str(resolve.GetProductName())
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"GetProductName: {exc}")

    for meth in ("GetVersionString", "GetVersion"):
        if hasattr(resolve, meth):
            try:
                fn = getattr(resolve, meth)
                ver = fn() if callable(fn) else fn
                if ver:
                    out["version"] = str(ver)
                    break
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{meth}: {exc}")
    return out


def sanitize_resolve_name(name: str, *, prefix: str = "", max_len: int = RESOLVE_NAME_MAX_LEN) -> str:
    """Resolve-safe project/timeline name (alnum, underscore, hyphen)."""
    raw = f"{prefix}{name or ''}".strip()
    cleaned = re.sub(r"[^\w\-]+", "_", raw, flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "SV_FolderStudio"
    return cleaned[:max_len]


def folder_studio_project_name(output_name: str) -> str:
    slug = sanitize_resolve_name(str(output_name or "project"))
    base = f"{_PROJECT_PREFIX}{slug}"
    if len(base) <= RESOLVE_NAME_MAX_LEN:
        return base
    keep = max(8, RESOLVE_NAME_MAX_LEN - len(_PROJECT_PREFIX))
    return f"{_PROJECT_PREFIX}{slug[:keep]}"


def _project_list(pm: Any) -> list[str]:
    for meth in ("GetProjectListInCurrentFolder", "GetProjectList"):
        if hasattr(pm, meth):
            try:
                fn = getattr(pm, meth)
                items = fn() if callable(fn) else fn
                if isinstance(items, list):
                    return [str(x) for x in items if x]
            except Exception:  # noqa: BLE001
                pass
    return []


def _find_existing_project(pm: Any, project_name: str) -> str | None:
    names = _project_list(pm)
    if project_name in names:
        return project_name
    want = project_name.casefold()
    for n in names:
        if n.casefold() == want:
            return n
    return None


def open_or_create_resolve_project(
    pm: Any,
    project_name: str,
    warnings: list[str],
) -> tuple[Any | None, dict[str, Any]]:
    """Load existing Resolve project or create it. Never raises."""
    meta: dict[str, Any] = {"requested_name": project_name, "resolved_name": project_name}
    safe_name = sanitize_resolve_name(project_name)
    if safe_name != project_name:
        meta["sanitized_from"] = project_name
        meta["resolved_name"] = safe_name
        project_name = safe_name
        warnings.append(f"resolve_project_name_sanitized:{project_name}")

    existing = _find_existing_project(pm, project_name)
    if existing and existing != project_name:
        meta["matched_existing"] = existing
        project_name = existing
        meta["resolved_name"] = project_name

    project = None
    try:
        project = pm.LoadProject(project_name)
        if project:
            meta["opened_via"] = "LoadProject"
            return project, meta
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"LoadProject:{project_name}:{exc!r}")

    try:
        project = pm.CreateProject(project_name)
        if project:
            meta["opened_via"] = "CreateProject"
            return project, meta
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"CreateProject:{project_name}:{exc!r}")

    try:
        project = pm.LoadProject(project_name)
        if project:
            meta["opened_via"] = "LoadProject_after_create"
            return project, meta
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"LoadProject_after_create:{project_name}:{exc!r}")

    meta["available_projects"] = _project_list(pm)[:20]
    return None, meta


def _escape_edl_path(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/")


def write_edl(plan: dict[str, Any], project_dir: Path) -> Path:
    """Simple CMX3600-style EDL from timeline plan (fail-open reference)."""
    clips = plan.get("clips") or []
    fps = float((plan.get("timeline") or {}).get("fps") or 30)
    lines = ["TITLE: StateVerge DaVinci Folder Studio v1", f"FCM: NON-DROP FRAME"]
    record_in = 0.0
    for i, clip in enumerate(clips, start=1):
        src = Path(str(clip.get("path") or ""))
        dur = float(clip.get("duration_sec") or 5.0)
        src_in = _frames(record_in, fps)
        src_out = _frames(record_in + dur, fps)
        rec_in = _frames(record_in, fps)
        rec_out = _frames(record_in + dur, fps)
        lines.append(
            f"{i:03d}  AX       V     C        {rec_in} {rec_out} {rec_in} {rec_out}\n"
            f"* FROM CLIP NAME: {src.name}\n"
            f"* SOURCE FILE: {_escape_edl_path(src)}"
        )
        record_in += dur
    out = project_dir / "timeline.edl"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _frames(sec: float, fps: float) -> str:
    total = int(round(sec * fps))
    ff = total % int(fps)
    total //= int(fps)
    ss = total % 60
    total //= 60
    mm = total % 60
    hh = total // 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def write_manifest(plan: dict[str, Any], project_dir: Path, resolve_status: dict[str, Any]) -> Path:
    audio_policy = plan.get("audio_policy") or {}
    manifest = {
        "version": "davinci_folder_studio_v1",
        "created_at": _utc_iso(),
        "resolve": resolve_status,
        "plan": plan,
        "audio_policy": audio_policy,
        "note": "Resolve API unavailable or render deferred; use timeline_plan.json + EDL.",
    }
    out = project_dir / "resolve_manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return out


def try_resolve_timeline(plan: dict[str, Any]) -> dict[str, Any]:
    """Attempt Resolve project/timeline/import. Never raises."""
    status = probe_resolve_api()
    result: dict[str, Any] = {
        "resolve_used": False,
        "resolve_status": status,
        "warnings": [],
    }
    if not status.get("api_available") or not status.get("resolve_connected"):
        result["warnings"].append("resolve_not_available_using_manifest_only")
        return result

    try:
        import DaVinciResolveScript as dvr  # type: ignore[import-not-found]

        resolve = dvr.scriptapp("Resolve")
        pm = resolve.GetProjectManager()
        project_name = folder_studio_project_name(str(plan.get("output_name") or "project"))
        warnings: list[str] = []
        project, proj_meta = open_or_create_resolve_project(pm, project_name, warnings)
        result["warnings"].extend(warnings)
        result["project_meta"] = proj_meta
        if project is None:
            result["warnings"].append("resolve_create_project_failed")
            return result

        media_pool = project.GetMediaPool()
        clips = [str(c.get("path")) for c in (plan.get("clips") or []) if c.get("path")]
        imported = media_pool.ImportMedia(clips) if clips else None
        timeline_name = sanitize_resolve_name(f"{plan.get('output_name', 'timeline')}_v1")
        timeline = media_pool.CreateEmptyTimeline(timeline_name)
        appended = False
        if timeline and imported:
            try:
                appended = bool(media_pool.AppendToTimeline(imported))
            except Exception as exc:  # noqa: BLE001
                result["warnings"].append(f"resolve_append_to_timeline:{exc!r}")
        elif not timeline:
            result["warnings"].append("resolve_create_timeline_failed")
        if timeline and hasattr(project, "SetCurrentTimeline"):
            try:
                project.SetCurrentTimeline(timeline)
            except Exception as exc:  # noqa: BLE001
                result["warnings"].append(f"resolve_set_current_timeline:{exc!r}")
        result.update(
            {
                "resolve_used": bool(timeline),
                "project_name": proj_meta.get("resolved_name", project_name),
                "timeline_name": timeline_name,
                "imported_count": len(imported) if imported else 0,
                "timeline_appended": appended,
            }
        )
    except Exception as exc:  # noqa: BLE001
        result["warnings"].append(f"resolve_timeline_exception:{exc!r}")
    return result


def bridge_plan(plan: dict[str, Any]) -> dict[str, Any]:
    project_dir = Path(plan["project_dir"])
    project_dir.mkdir(parents=True, exist_ok=True)
    resolve_status = probe_resolve_api()
    edl_path = write_edl(plan, project_dir)
    manifest_path = write_manifest(plan, project_dir, resolve_status)
    resolve_result = try_resolve_timeline(plan)
    return {
        "project_dir": str(project_dir),
        "timeline_plan_path": str(project_dir / "timeline_plan.json"),
        "edl_path": str(edl_path),
        "manifest_path": str(manifest_path),
        "resolve_status": resolve_status,
        "resolve_result": resolve_result,
    }
