"""
Orchestration: plan timeline, TTS, manifest paths; manifest I/O lives in :mod:`.manifest`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from . import duration, eleven_client, fs_utils, manifest as manifest_mod, timeline_planner
from .config import ElevenSettings, PipelineConfig
from .logutil import log_presenter
from .script_parser import load_presenter_script

LOG = logging.getLogger("presenter.planner")

# Re-export for CLI and tools
load_or_init_manifest = manifest_mod.load_or_init
save_manifest = manifest_mod.save


def run_plan(
    config: PipelineConfig, topic: str, root: Path
) -> dict[str, Any]:
    fs_utils.ensure_default_asset_tree(root)
    tpaths = fs_utils.topic_paths(root, topic)
    fs_utils.ensure_dir(tpaths["plan"])
    fs_utils.ensure_dir(tpaths["manifests"])
    main = tpaths["video"]
    vdur: float = 0.0
    if main.is_file():
        vdur = duration.get_media_duration(main)
    else:
        log_presenter(
            LOG,
            topic,
            "--",
            "plan",
            "narrative_main missing; video_duration_sec=0",
            level=logging.WARNING,
        )
    script = load_presenter_script(tpaths["presenter_script"])
    tl = timeline_planner.plan_presenter_timeline(root, topic, vdur, script, config)
    fs_utils.write_json(tpaths["timeline"], tl)
    man = manifest_mod.load_or_init(tpaths["manifest"], topic, vdur)
    man["video_duration_sec"] = vdur
    man["timeline_path"] = fs_utils.relposix(root, tpaths["timeline"])
    man["segments"] = manifest_mod.merge_timeline_into_manifest(
        man, tl.get("segments", [])
    )
    manifest_mod.apply_host_reference(root, man)
    manifest_mod.save(tpaths["manifest"], man)
    log_presenter(LOG, topic, "--", "plan", f"wrote {tpaths['timeline']}")
    return tl


def run_tts(
    _config: PipelineConfig, topic: str, root: Path, settings: Optional[ElevenSettings]
) -> None:
    tpaths = fs_utils.topic_paths(root, topic)
    fs_utils.ensure_dir(tpaths["audio"])
    man = manifest_mod.load_or_init(tpaths["manifest"], topic, None)
    tl = fs_utils.read_json(tpaths["timeline"])
    if not tl:
        log_presenter(
            LOG,
            topic,
            "--",
            "tts",
            "no presenter_timeline.json; run --plan first",
            level=logging.ERROR,
        )
        return
    if not settings:
        log_presenter(
            LOG,
            topic,
            "--",
            "tts",
            "missing ELEVENLABS_* in .env",
            level=logging.ERROR,
        )
        for mseg in man.get("segments", []):
            mseg.setdefault("errors", []).append("tts_skipped_no_settings")
        manifest_mod.save(tpaths["manifest"], man)
        return

    for seg in tl.get("segments", []):
        name = str(seg.get("name", ""))
        text = str(seg.get("text") or "")
        st = str(seg.get("status") or "")
        mseg = next((s for s in man["segments"] if s.get("name") == name), None)
        if mseg is None:
            mseg = {
                "name": name,
                "type": seg.get("type"),
                "text": text,
                "errors": [],
                "audio_path": seg.get("audio_path"),
            }
            man["segments"].append(mseg)
        if st == "placeholder" or not text.strip():
            mseg["status"] = "skipped_no_text"
            mseg["errors"] = mseg.get("errors") or []
            log_presenter(LOG, topic, name, "tts", "skip empty or placeholder")
            continue
        out_wav = tpaths["audio"] / f"{name}.wav"
        ok = eleven_client.text_to_speech(text, settings, out_wav)
        if not ok:
            mseg.setdefault("errors", []).append("tts_failed")
            mseg["status"] = "tts_error"
        else:
            mseg["audio_path"] = fs_utils.relposix(root, out_wav)
            try:
                mseg["audio_duration_sec"] = duration.get_media_duration(out_wav)
            except Exception as e:  # noqa: BLE001
                mseg.setdefault("errors", []).append(f"duration_after_tts: {e!s}")
            mseg["status"] = "audio_ready"
        manifest_mod.save(tpaths["manifest"], man)
    log_presenter(LOG, topic, "--", "tts", "done")
