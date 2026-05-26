#!/usr/bin/env python3
"""StateVerge DaVinci Folder Studio Runner v1 — folder in → YouTube-ready MP4 out.

Scans a folder for clips, builds a CFR timeline, applies a mode preset, renders via
Resolve when available or ffmpeg fallback (fail-open). Never modifies source files.
No upload, no token access.

Stdout ends with DAVINCI_FOLDER_RUNNER_READY=true|false (block_reason on failure).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO = _SCRIPT_DIR.parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from paths import (  # noqa: E402
    FINISHED_FOR_YOUTUBE_ROOT,
    FOLDER_RUNNER_MODES,
    REPORTS_ROOT,
    RUNNER_MARKER_READY,
    STUDIO_ROOT,
)
from render_job import run_job  # noqa: E402

MODE_PRESET_MAP: dict[str, dict[str, Any]] = {
    "ferry_real": {
        "youtube_preset": "youtube_ferry_real",
        "ambient_chain_id": "ferry_preserve_atmosphere",
        "audio_mode": "ferry_wind_light",
        "add_music": False,
        "use_loudnorm": False,
        "original_audio_volume": 1.0,
        "music_volume": 0.0,
        "description": "Preserve ferry ambience; light wind cleanup; no BGM",
    },
    "driving_music": {
        "youtube_preset": "youtube_long_calm",
        "ambient_chain_id": "driving_music_first_chain",
        "audio_mode": "driving_music_first",
        "add_music": True,
        "use_loudnorm": True,
        "original_audio_volume": 0.30,
        "music_volume": 0.85,
        "description": "Driving calm; Suno-first BGM when available, else nyc_long",
    },
    "shorts_cinematic": {
        "youtube_preset": "youtube_shorts_cinematic",
        "ambient_chain_id": "streaming_immersive_ambient",
        "audio_mode": "add_envato_music",
        "add_music": True,
        "use_loudnorm": True,
        "original_audio_volume": 0.28,
        "music_volume": 0.88,
        "description": "Shorts cinematic; Envato BGM, stronger loudness",
    },
}


def _resolve_output_path(output_name: str) -> Path:
    stem = output_name.strip()
    if not stem:
        raise ValueError("output_name_required")
    if not stem.lower().endswith(".mp4"):
        stem = f"{stem}.mp4"
    return FINISHED_FOR_YOUTUBE_ROOT / stem


def _load_preset_rules(preset_id: str) -> dict[str, Any]:
    try:
        _finish = _REPO / "scripts" / "davinci_audio_finish"
        if str(_finish) not in sys.path:
            sys.path.insert(0, str(_finish))
        from presets import preset_rules  # noqa: WPS433

        return dict(preset_rules(preset_id))
    except Exception as exc:  # noqa: BLE001
        return {"load_error": repr(exc), "preset": preset_id}


def _probe_fairlight_failopen(*, youtube_preset: str) -> dict[str, Any]:
    """Resolve / gate probe for audio_report (never blocks render)."""
    out: dict[str, Any] = {
        "fairlight_attempted": True,
        "resolve_connected": False,
        "ffmpeg_fallback_documented": True,
        "youtube_preset": youtube_preset,
        "warnings": [],
    }
    try:
        from resolve_bridge import probe_resolve_api  # noqa: WPS433

        probe = probe_resolve_api()
        out["resolve_probe"] = probe
        out["resolve_connected"] = bool(probe.get("resolve_connected"))
        if not out["resolve_connected"]:
            out["warnings"].append("resolve_not_connected_using_ffmpeg_semantics")
    except Exception as exc:  # noqa: BLE001
        out["warnings"].append(f"resolve_probe_failed:{exc!r}")

    out["preset_rules"] = _load_preset_rules(youtube_preset)
    return out


def _ensure_studio_dirs() -> list[str]:
    warnings: list[str] = []
    for d in (STUDIO_ROOT, REPORTS_ROOT, FINISHED_FOR_YOUTUBE_ROOT):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"mkdir_failed:{d}:{exc!r}")
    return warnings


def diagnose_runner() -> dict[str, Any]:
    report: dict[str, Any] = {
        "version": "davinci_folder_runner_v1",
        "modes": sorted(FOLDER_RUNNER_MODES),
        "mode_map": {k: v.get("youtube_preset") for k, v in MODE_PRESET_MAP.items()},
        "finished_for_youtube": str(FINISHED_FOR_YOUTUBE_ROOT),
        "reports_root": str(REPORTS_ROOT),
        "modules": {},
        "ok": True,
    }
    for name in (
        "folder_scan.py",
        "timeline_builder.py",
        "audio_modes.py",
        "render_job.py",
        "report_writer.py",
        "run_davinci_folder_job.py",
    ):
        fp = _SCRIPT_DIR / name
        report["modules"][name] = fp.is_file()
        if not fp.is_file():
            report["ok"] = False

    try:
        from folder_scan import scan_folder_dict  # noqa: WPS433

        report["folder_scan_ok"] = callable(scan_folder_dict)
    except Exception as exc:  # noqa: BLE001
        report["folder_scan_error"] = repr(exc)
        report["ok"] = False

    report["fairlight_probe"] = _probe_fairlight_failopen(youtube_preset="youtube_long_calm")
    return report


def run_folder_job(
    *,
    input_folder: Path,
    mode: str,
    output_name: str,
    dry_run: bool = False,
    encode_timeout_sec: float = 7200.0,
) -> dict[str, Any]:
    warnings = _ensure_studio_dirs()
    mode_key = (mode or "").strip().lower()
    if mode_key not in MODE_PRESET_MAP:
        return {
            "ok": False,
            "block_reason": f"invalid_mode:{mode_key}",
            "valid_modes": sorted(FOLDER_RUNNER_MODES),
            "warnings": warnings,
        }

    preset_cfg = MODE_PRESET_MAP[mode_key]
    input_folder = input_folder.expanduser().resolve()
    if not input_folder.is_dir():
        return {
            "ok": False,
            "block_reason": "input_folder_missing",
            "input_folder": str(input_folder),
            "warnings": warnings,
        }

    slug = output_name.strip() or input_folder.name
    try:
        render_path = _resolve_output_path(slug)
    except ValueError:
        return {
            "ok": False,
            "block_reason": "output_name_required",
            "warnings": warnings,
        }

    if render_path.is_file() and not dry_run:
        return {
            "ok": False,
            "block_reason": "output_exists",
            "render_path": str(render_path),
            "warnings": warnings,
        }

    youtube_preset = str(preset_cfg["youtube_preset"])
    fairlight = _probe_fairlight_failopen(youtube_preset=youtube_preset)
    warnings.extend(fairlight.get("warnings") or [])

    result = run_job(
        input_folder=input_folder,
        output_name=Path(slug).stem,
        audio_mode=str(preset_cfg["audio_mode"]),
        add_music=bool(preset_cfg["add_music"]),
        dry_run=dry_run,
        encode_timeout_sec=encode_timeout_sec,
        original_audio_volume=float(preset_cfg["original_audio_volume"]),
        music_volume=float(preset_cfg["music_volume"]),
        use_loudnorm=bool(preset_cfg["use_loudnorm"]),
        render_output_path=render_path,
        folder_mode=mode_key,
        youtube_preset=youtube_preset,
    )

    result["folder_mode"] = mode_key
    result["youtube_preset"] = youtube_preset
    result["mode_description"] = preset_cfg.get("description")
    result["finished_for_youtube_path"] = str(render_path)
    result["fairlight_probe"] = fairlight
    result["warnings"] = list(result.get("warnings") or []) + warnings

    if not result.get("ok"):
        result["block_reason"] = str(result.get("error") or result.get("block_reason") or "render_failed")

    # Enrich audio_report bundle via report_paths if present
    bundle = (result.get("report_paths") or {}).get("bundle_dir")
    if bundle:
        audio_path = Path(bundle) / "audio_report.json"
        if audio_path.is_file():
            try:
                audio_doc = json.loads(audio_path.read_text(encoding="utf-8"))
                audio_doc["fairlight_probe"] = fairlight
                audio_doc["youtube_preset"] = youtube_preset
                audio_doc["folder_mode"] = mode_key
                audio_path.write_text(json.dumps(audio_doc, indent=2) + "\n", encoding="utf-8")
            except (OSError, json.JSONDecodeError):
                pass

    return result


def _print_ready(result: dict[str, Any], *, dry_run: bool) -> None:
    ready = bool(result.get("ok")) or (dry_run and not result.get("block_reason"))
    marker = RUNNER_MARKER_READY.split("=")[0]
    if ready:
        print(f"{marker}=true")
    else:
        reason = result.get("block_reason") or result.get("error") or "unknown"
        print(f"{marker}=false block_reason={reason}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Modes:\n"
            "  ferry_real        — preserve ambience (youtube_ferry_real)\n"
            "  driving_music     — calm driving BGM, Suno-first (youtube_long_calm)\n"
            "  shorts_cinematic  — Shorts Envato BGM (youtube_shorts_cinematic)\n"
        ),
    )
    ap.add_argument(
        "--input",
        "--input-folder",
        dest="input_folder",
        type=Path,
        required=False,
        help="Folder of source clips (recursive .mov/.mp4/.m4v scan)",
    )
    ap.add_argument(
        "--mode",
        required=False,
        choices=sorted(FOLDER_RUNNER_MODES),
        help="Render/audio preset family",
    )
    ap.add_argument(
        "--output-name",
        default="",
        help="Output basename under finished_for_youtube/ (default: input folder name)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Plan timeline + render without writing MP4")
    ap.add_argument(
        "--diagnose",
        action="store_true",
        help="Check modules, paths, and presets; no render",
    )
    ap.add_argument(
        "--encode-timeout-sec",
        type=float,
        default=7200.0,
        help="FFmpeg normalize/concat timeout budget (default 7200)",
    )
    args = ap.parse_args()

    if args.diagnose:
        report = diagnose_runner()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        ready = bool(report.get("ok"))
        print(f"{RUNNER_MARKER_READY.split('=')[0]}={'true' if ready else 'false'}")
        return 0 if ready else 2

    if not args.input_folder or not args.mode:
        ap.error("--input and --mode are required unless --diagnose is set")

    result = run_folder_job(
        input_folder=args.input_folder,
        mode=args.mode,
        output_name=args.output_name or args.input_folder.name,
        dry_run=bool(args.dry_run),
        encode_timeout_sec=float(args.encode_timeout_sec),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    _print_ready(result, dry_run=bool(args.dry_run))
    return 0 if result.get("ok") or (args.dry_run and not result.get("block_reason")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
