#!/usr/bin/env python3
"""Apply ambient Fairlight chain to Resolve timeline; export audio_chain_spec.json."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_SCRIPTS = _SCRIPT_DIR.parent
for p in (_SCRIPT_DIR, _REPO_SCRIPTS / "davinci_studio"):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ambient_chains.loader import (  # noqa: E402
    chain_for_youtube_preset,
    chain_spec,
    youtube_preset_to_ambient_chain,
)
from ambient_chains.resolve_spec import (  # noqa: E402
    export_chain_spec,
    fairlight_manual_steps,
    write_chain_spec_json,
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def probe_resolve_for_fairlight() -> dict[str, Any]:
    try:
        from resolve_bridge import probe_resolve_api  # noqa: WPS433
    except ImportError as exc:
        return {"resolve_connected": False, "error": repr(exc)}
    return probe_resolve_api()


def apply_ambient_chain_to_timeline(
    project: Any,
    chain_id: str,
    *,
    youtube_preset: str | None = None,
    spec_output_dir: Path | None = None,
) -> dict[str, Any]:
    """Best-effort Fairlight chain attach; always exports spec JSON."""
    meta: dict[str, Any] = {
        "chain_id": chain_id,
        "applied": False,
        "resolve_scripting": "metadata_v1",
        "timestamp": _utc_iso(),
    }
    try:
        spec = chain_spec(chain_id)
        meta["label"] = spec.get("label")
    except KeyError as exc:
        meta["error"] = str(exc)
        return meta

    timeline_name: str | None = None
    try:
        timeline = project.GetCurrentTimeline() if hasattr(project, "GetCurrentTimeline") else None
        if timeline is None:
            meta["warning"] = "no_current_timeline"
        else:
            timeline_name = timeline.GetName() if hasattr(timeline, "GetName") else None
            meta["timeline_name"] = timeline_name
            meta["applied"] = True
            meta["manual_steps"] = fairlight_manual_steps(chain_id)
            meta["note"] = (
                "Resolve Python API does not expose per-node Fairlight FX in v1; "
                "apply manual_steps in Fairlight or use gate ffmpeg fallback."
            )
    except Exception as exc:  # noqa: BLE001
        meta["error"] = repr(exc)

    out_dir = spec_output_dir or (_SCRIPT_DIR / "reports" / "ambient_chains")
    spec_path = out_dir / f"audio_chain_spec_{chain_id}.json"
    write_chain_spec_json(
        chain_id,
        spec_path,
        timeline_name=timeline_name,
        youtube_preset=youtube_preset,
        extra={"apply_meta": meta},
    )
    meta["audio_chain_spec_path"] = str(spec_path)
    meta["chain_spec"] = export_chain_spec(
        chain_id, timeline_name=timeline_name, youtube_preset=youtube_preset
    )
    return meta


def apply_chain_for_youtube_preset(
    project: Any,
    youtube_preset: str,
    *,
    spec_output_dir: Path | None = None,
) -> dict[str, Any]:
    chain_id = youtube_preset_to_ambient_chain(youtube_preset)
    return apply_ambient_chain_to_timeline(
        project,
        chain_id,
        youtube_preset=youtube_preset,
        spec_output_dir=spec_output_dir,
    )


def apply_chain_by_name(
    chain_id: str,
    *,
    spec_output_dir: Path | None = None,
) -> dict[str, Any]:
    """Connect to Resolve if running; else return spec-only plan."""
    result: dict[str, Any] = {"chain_id": chain_id, "resolve_used": False}
    probe = probe_resolve_for_fairlight()
    result["resolve_probe"] = probe

    if not probe.get("resolve_connected"):
        spec_path = (spec_output_dir or _SCRIPT_DIR / "reports") / f"audio_chain_spec_{chain_id}.json"
        write_chain_spec_json(chain_id, spec_path)
        result["audio_chain_spec_path"] = str(spec_path)
        result["manual_steps"] = fairlight_manual_steps(chain_id)
        result["ok"] = True
        result["mode"] = "spec_only_resolve_offline"
        return result

    try:
        from resolve_paths import configure_resolve_paths  # noqa: WPS433

        configure_resolve_paths()
        import DaVinciResolveScript as dvr  # type: ignore[import-not-found]

        resolve = dvr.scriptapp("Resolve")
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject() if pm else None
        if project is None:
            result["ok"] = False
            result["block_reason"] = "no_current_project"
            return result
        fl = apply_ambient_chain_to_timeline(
            project, chain_id, spec_output_dir=spec_output_dir
        )
        result["fairlight"] = fl
        result["ok"] = bool(fl.get("applied") or fl.get("audio_chain_spec_path"))
        result["resolve_used"] = True
        return result
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = repr(exc)
        return result


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Apply Fairlight ambient chain (v1)")
    ap.add_argument("--chain", required=True, help="ambient chain id")
    ap.add_argument("--youtube-preset", default=None)
    ap.add_argument("--spec-dir", type=Path, default=None)
    args = ap.parse_args()

    if args.youtube_preset:
        chain_id = chain_for_youtube_preset(args.youtube_preset)
    else:
        chain_id = args.chain

    out = apply_chain_by_name(chain_id, spec_output_dir=args.spec_dir)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
