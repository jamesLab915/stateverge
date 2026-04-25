"""
Package base video + audio for Runway Lip Sync; write meta.json per part.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from . import duration, fs_utils
from .config import PipelineConfig
from .logutil import log_presenter

LOG = logging.getLogger("presenter.runway_prep")


def _copy_wav_48k(in_wav: Path, out_wav: Path) -> bool:
    if not in_wav.is_file():
        return False
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(in_wav),
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]
    c, o, e = duration.run_cmd(args)
    if c != 0:
        log_presenter(
            LOG, "--", "--", "ffmpeg_rewrap_wav", e[:2000], level=logging.ERROR
        )
        return False
    return True


def _meta(
    topic: str,
    seg: str,
    part: str,
    segment_type: str,
    audio_dur: float,
    base_path: str,
    audio_in: str,
    expected_out: str,
) -> dict[str, Any]:
    return {
        "topic_slug": topic,
        "segment_name": seg,
        "part_name": part,
        "segment_type": segment_type,
        "audio_duration_sec": audio_dur,
        "base_video_path": base_path,
        "audio_path": audio_in,
        "expected_output_path": expected_out,
    }


def prepare_runway_inputs(
    config: PipelineConfig, topic: str, root: Path, manifest: dict[str, Any]
) -> None:
    tpaths = fs_utils.topic_paths(root, topic)
    fs_utils.ensure_dir(tpaths["lipsync_input"])
    tl = fs_utils.read_json(tpaths["timeline"]) or {}
    t_by_name = {r.get("name"): r for r in (tl.get("segments") or []) if r.get("name")}
    for seg in manifest.get("segments", []):
        name = str(seg.get("name", ""))
        if name:
            fs_utils.ensure_dir(tpaths["lipsync_output"] / name)
        stype = str(
            (t_by_name.get(name) or seg).get("type") or seg.get("type") or "insert"
        )
        seg["runway_input_paths"] = []
        sdir = tpaths["segments"] / name
        if not sdir.is_dir():
            continue
        parts = sorted(sdir.glob("audio_part_*.wav"))
        if not parts:
            a_r = root / (seg.get("audio_path") or "")
            if a_r.is_file():
                parts = [a_r]
        for idx, p in enumerate(parts, start=1):
            part = f"part_{idx:02d}"
            out_dir = tpaths["lipsync_input"] / name / part
            fs_utils.ensure_dir(out_dir)
            base_src = tpaths["base"] / name / f"base_part_{idx:02d}.mp4"
            av = out_dir / "audio.wav"
            bv = out_dir / "base_video.mp4"
            if not _copy_wav_48k(Path(p), av):
                seg.setdefault("errors", []).append(f"no_audio_to_prepare:{p}")
                continue
            if not base_src.is_file():
                seg.setdefault("errors", []).append(
                    f"missing_base:{base_src.name}"
                )
                continue
            shutil.copy2(base_src, bv)
            try:
                ad = duration.get_media_duration(av)
            except Exception as e:  # noqa: BLE001
                ad = 0.0
                seg.setdefault("errors", []).append(f"audio_probe:{e!s}")
            exp = tpaths["lipsync_output"] / name / f"{part}.mp4"
            m = _meta(
                topic,
                name,
                part,
                stype,
                ad,
                fs_utils.relposix(root, bv),
                fs_utils.relposix(root, av),
                fs_utils.relposix(root, exp),
            )
            fs_utils.write_json(out_dir / "meta.json", m)
            seg["runway_input_paths"].append(
                fs_utils.relposix(root, out_dir)
            )
    log_presenter(LOG, topic, "--", "prepare_runway", "ok")
