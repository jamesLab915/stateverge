#!/usr/bin/env python3
"""Build a CFR 30fps timeline plan from scanned clips."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from folder_scan import scan_folder_dict
from paths import OUTPUT_FPS, OUTPUT_AUDIO_BITRATE, OUTPUT_PIX_FMT, PROJECTS_ROOT


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_timeline_plan(
    *,
    input_folder: Path,
    output_name: str,
    audio_mode: str,
    add_music: bool,
    clips_payload: dict[str, Any] | None = None,
    audio_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_folder = input_folder.expanduser().resolve()
    payload = clips_payload if clips_payload is not None else scan_folder_dict(input_folder)
    clips = payload.get("clips") or []
    slug = output_name.strip() or input_folder.name
    plan_id = uuid.uuid4().hex[:12]
    project_dir = PROJECTS_ROOT / slug / plan_id
    plan: dict[str, Any] = {
        "version": "davinci_folder_studio_v1",
        "plan_id": plan_id,
        "created_at": _utc_iso(),
        "input_folder": str(input_folder),
        "output_name": slug,
        "project_dir": str(project_dir),
        "audio_mode": audio_mode,
        "add_music": bool(add_music),
        "timeline": {
            "fps": OUTPUT_FPS,
            "video_codec": "h264",
            "pixel_format": OUTPUT_PIX_FMT,
            "audio_codec": "aac",
            "audio_bitrate": OUTPUT_AUDIO_BITRATE,
            "movflags": "+faststart",
        },
        "clips": clips,
        "clip_count": len(clips),
    }
    if audio_metadata:
        for key in (
            "original_audio_volume",
            "music_volume",
            "audio_filter_chain",
            "audio_protection_policy",
            "audio_was_heavily_processed",
            "denoise_used",
            "demucs_used",
            "voice_isolation_used",
            "loudnorm_used",
            "loudnorm_target",
        ):
            if key in audio_metadata:
                plan[key] = audio_metadata[key]
        plan["audio_policy"] = audio_metadata
    return plan


def write_timeline_plan(plan: dict[str, Any]) -> Path:
    project_dir = Path(plan["project_dir"])
    project_dir.mkdir(parents=True, exist_ok=True)
    out = project_dir / "timeline_plan.json"
    out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--output-name", default="")
    ap.add_argument("--audio-mode", default="preserve_raw")
    ap.add_argument("--add-music", action="store_true")
    args = ap.parse_args()
    name = args.output_name or args.folder.name
    plan = build_timeline_plan(
        input_folder=args.folder,
        output_name=name,
        audio_mode=args.audio_mode,
        add_music=bool(args.add_music),
    )
    path = write_timeline_plan(plan)
    print(json.dumps({"timeline_plan_path": str(path), "plan": plan}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
