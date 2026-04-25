"""
CLI for presenter pipeline: plan, tts, split, base build, Runway prep, assembly.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import (
    audio_splitter,
    builder,
    duration,
    fs_utils,
    manifest,
    planner,
    runway_prep,
    timeline_assembler,
    variant_generator,
)
from .config import (
    ElevenSettings,
    PipelineConfig,
    default_stateverge_root,
    load_dotenv_silent,
)
from .logutil import log_presenter

LOG = logging.getLogger("presenter.cli")


def _setup_log() -> None:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    r = logging.getLogger("presenter")
    r.setLevel(logging.INFO)
    r.handlers = []
    r.addHandler(h)
    r.propagate = False


def _root() -> Path:
    return default_stateverge_root()


def _load_cfg() -> tuple[PipelineConfig, ElevenSettings | None]:
    r = _root()
    load_dotenv_silent(r)
    if os.environ.get("ELEVENLABS_API_KEY"):
        try:
            e = ElevenSettings.from_env()
        except Exception:
            e = None
    else:
        e = None
    return PipelineConfig.load(r), e


def cmd_plan(args: argparse.Namespace) -> int:
    cfg, _e = _load_cfg()
    root = _root()
    planner.run_plan(cfg, args.topic, root)
    return 0


def cmd_tts(args: argparse.Namespace) -> int:
    cfg, el = _load_cfg()
    root = _root()
    if el is None:
        log_presenter(
            LOG,
            args.topic,
            "--",
            "cli_tts",
            "set ELEVENLABS_* in .env; TTS will no-op",
            level=logging.ERROR,
        )
    planner.run_tts(cfg, args.topic, root, el)
    return 0


def _save_m(topic: str, root: Path, m: dict) -> None:
    p = fs_utils.topic_paths(root, topic)["manifest"]
    manifest.save(p, m)


def cmd_split_audio(args: argparse.Namespace) -> int:
    cfg, _e = _load_cfg()
    root = _root()
    tpaths = fs_utils.topic_paths(root, args.topic)
    m = manifest.load_or_init(
        tpaths["manifest"], args.topic, None
    )
    tl = fs_utils.read_json(tpaths["timeline"])
    if not tl:
        log_presenter(
            LOG,
            args.topic,
            "--",
            "split",
            "no timeline; run --plan",
            level=logging.ERROR,
        )
        return 1
    for seg in tl.get("segments", []):
        name = str(seg.get("name", ""))
        ap = root / (seg.get("audio_path") or "")
        mseg = next((s for s in m["segments"] if s.get("name") == name), None)
        if mseg is None:
            mseg = {"name": name}
            m["segments"].append(mseg)
        if not ap.is_file():
            mseg["status"] = mseg.get("status") or "no_audio"
            mseg.setdefault("errors", []).append("split_skip_missing_audio")
            _save_m(args.topic, root, m)
            continue
        r = audio_splitter.split_presenter_audio(ap, name, tpaths, cfg)
        mseg["original_audio_duration"] = r.get("original_audio_duration")
        mseg["split_parts"] = r.get("part_paths", [])
        mseg["split_count"] = r.get("split_count", 0)
        mseg["split_points"] = r.get("split_points", [])
        if r.get("error"):
            mseg.setdefault("errors", []).append(r["error"])
        mseg["status"] = "split_done"
        _save_m(args.topic, root, m)
        log_presenter(
            LOG,
            args.topic,
            name,
            "split",
            f"count={mseg.get('split_count')}",
        )
    return 0


def cmd_build_base(args: argparse.Namespace) -> int:
    cfg, _e = _load_cfg()
    root = _root()
    tpaths = fs_utils.topic_paths(root, args.topic)
    m = manifest.load_or_init(tpaths["manifest"], args.topic, None)
    tl = fs_utils.read_json(tpaths["timeline"])
    if not tl:
        return 1
    for seg in tl.get("segments", []):
        name = str(seg.get("name", ""))
        sbase = tpaths["segments"] / name
        mseg = next((s for s in m["segments"] if s.get("name") == name), None)
        if mseg is None:
            mseg = {"name": name}
            m["segments"].append(mseg)
        parts = sorted(sbase.glob("audio_part_*.wav"))
        if not parts and seg.get("audio_path"):
            ap = root / seg["audio_path"]
            if ap.is_file():
                parts = [ap]
        if not parts:
            mseg.setdefault("errors", []).append("build_base_no_audio_parts")
            _save_m(args.topic, root, m)
            continue
        bpaths: list[str] = []
        plans: list = []
        masters: list[str] = []
        seg_t = str(seg.get("type") or "insert")
        for idx, pw in enumerate(parts, start=1):
            try:
                ad = duration.get_media_duration(pw)
            except Exception as e:  # noqa: BLE001
                mseg.setdefault("errors", []).append(f"probe_{idx}:{e!s}")
                continue
            target = ad + cfg.audio_lipsync_buffer_sec
            outb = tpaths["base"] / name / f"base_part_{idx:02d}.mp4"
            fs_utils.ensure_dir(outb.parent)
            r = builder.build_base_video_for_audio_part(
                target, outb, cfg, root, seg_t
            )
            if r.get("ok"):
                bpaths.append(fs_utils.relposix(root, outb))
                plans.append(r.get("selected_plan", []))
                masters.extend(r.get("selected_master_files", []))
            else:
                mseg.setdefault("errors", []).append(
                    f"base_build_failed:{r.get('error')}"
                )
        mseg["base_video_paths"] = bpaths
        mseg["selected_plan"] = plans
        mseg["selected_master_files"] = masters
        mseg["status"] = "base_built" if bpaths else mseg.get("status")
        _save_m(args.topic, root, m)
    return 0


def cmd_prepare_runway(args: argparse.Namespace) -> int:
    cfg, _e = _load_cfg()
    root = _root()
    tpaths = fs_utils.topic_paths(root, args.topic)
    m = manifest.load_or_init(tpaths["manifest"], args.topic, None)
    runway_prep.prepare_runway_inputs(cfg, args.topic, root, m)
    _save_m(args.topic, root, m)
    return 0


def cmd_assemble_segments(args: argparse.Namespace) -> int:
    cfg, _e = _load_cfg()
    root = _root()
    tpaths = fs_utils.topic_paths(root, args.topic)
    m = manifest.load_or_init(tpaths["manifest"], args.topic, None)
    tl = fs_utils.read_json(tpaths["timeline"])
    if not tl:
        return 1
    for seg in tl.get("segments", []):
        name = str(seg.get("name", ""))
        timeline_assembler.assemble_presenter_segment(
            args.topic, name, root, cfg, m
        )
    _save_m(args.topic, root, m)
    return 0


def cmd_assemble_full(args: argparse.Namespace) -> int:
    cfg, _e = _load_cfg()
    root = _root()
    m = manifest.load_or_init(
        fs_utils.topic_paths(root, args.topic)["manifest"], args.topic, None
    )
    timeline_assembler.assemble_full_timeline(args.topic, root, cfg, m)
    _save_m(args.topic, root, m)
    return 0


def cmd_variants_stub(args: argparse.Namespace) -> int:
    cfg, _e = _load_cfg()
    root = _root()
    tpaths = fs_utils.topic_paths(root, args.topic)
    m = manifest.load_or_init(tpaths["manifest"], args.topic, None)
    variant_generator.run_variant_generation_stub(cfg, args.topic, m)
    manifest.save(tpaths["manifest"], m)
    return 0


def cmd_full_prep(args: argparse.Namespace) -> int:
    r = 0
    for fn in (cmd_plan, cmd_tts, cmd_split_audio, cmd_build_base, cmd_prepare_runway):
        c = fn(args)
        if c != 0:
            r = c
    return r


def main() -> int:
    _setup_log()
    p = argparse.ArgumentParser(description="StateVerge presenter pipeline")
    p.add_argument("--topic", required=True, help="topic slug under topics/")
    p.add_argument("--plan", action="store_true")
    p.add_argument("--tts", action="store_true")
    p.add_argument("--split-audio", action="store_true", dest="split_audio")
    p.add_argument("--build-base", action="store_true", dest="build_base")
    p.add_argument("--prepare-runway", action="store_true", dest="prepare_runway")
    p.add_argument(
        "--assemble-segments", action="store_true", dest="assemble_segments"
    )
    p.add_argument("--assemble-full", action="store_true", dest="assemble_full")
    p.add_argument("--full-prep", action="store_true", dest="full_prep")
    p.add_argument(
        "--variants-stub",
        action="store_true",
        dest="variants_stub",
        help="v1: record variant-gen policy; requires host_master.png for a positive stub; no API",
    )
    a = p.parse_args()
    flags = [
        a.plan,
        a.tts,
        a.split_audio,
        a.build_base,
        a.prepare_runway,
        a.assemble_segments,
        a.assemble_full,
        a.full_prep,
        a.variants_stub,
    ]
    if not any(flags):
        p.print_help()
        return 1
    if a.full_prep:
        return cmd_full_prep(a)
    if a.variants_stub:
        return cmd_variants_stub(a)
    if a.plan:
        cmd_plan(a)
    if a.tts:
        cmd_tts(a)
    if a.split_audio:
        cmd_split_audio(a)
    if a.build_base:
        cmd_build_base(a)
    if a.prepare_runway:
        cmd_prepare_runway(a)
    if a.assemble_segments:
        cmd_assemble_segments(a)
    if a.assemble_full:
        cmd_assemble_full(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
