"""
Concat per-segment Runway parts; insert presenter clips into the full narrative.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from . import duration, fs_utils
from .config import PipelineConfig
from .logutil import log_presenter

LOG = logging.getLogger("presenter.timeline_assembler")


def _esc(p: Path) -> str:
    s = p.resolve().as_posix()
    return s.replace("'", r"'\''")


def _norm_segment(in_path: Path, out_path: Path, config: PipelineConfig) -> bool:
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(in_path),
        "-vf",
        f"scale={config.target_width}:{config.target_height}:force_original_aspect_ratio=decrease,"
        f"pad={config.target_width}:{config.target_height}:(ow-iw)/2:(oh-ih)/2,fps={config.target_fps}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    c, _o, e = duration.run_cmd(args)
    if c != 0:
        log_presenter(
            LOG, "--", "--", "norm_segment", e[:2000], level=logging.ERROR
        )
        return False
    return True


def _extract_main(
    main: Path, start: float, end: float, out: Path, config: PipelineConfig, tmp: Path
) -> bool:
    if end - start < 0.01:
        return False
    raw = tmp / "raw_ex.mp4"
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(main),
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{end - start:.3f}",
        str(raw),
    ]
    c, _o, e = duration.run_cmd(args)
    if c != 0:
        log_presenter(
            LOG, "--", "--", "extract_main", e[:2000], level=logging.ERROR
        )
        return False
    if not _norm_segment(raw, out, config):
        return False
    return True


def _concat_ffmpeg(
    files: list[Path], out: Path, config: PipelineConfig, tmp: Path
) -> bool:
    if not files:
        return False
    if len(files) == 1:
        return _norm_segment(files[0], out, config)
    cl = tmp / "concat.txt"
    with cl.open("w", encoding="utf-8") as f:
        for p in files:
            f.write("file '%s'\n" % _esc(p))
    t = tmp / "catout.mp4"
    args = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(cl),
        str(t),
    ]
    c, _o, e = duration.run_cmd(args)
    if c != 0:
        log_presenter(
            LOG, "--", "--", "concat_full", e[:2000], level=logging.ERROR
        )
        return False
    return _norm_segment(t, out, config)


def _final_for_segment(
    tpaths: dict[str, Path], name: str, mseg: dict[str, Any], root: Path
) -> Optional[Path]:
    p = mseg.get("final_segment_path")
    if p:
        fp = root / p
        if fp.is_file():
            return fp
    fp2 = tpaths["final"] / f"{name}_final.mp4"
    if fp2.is_file():
        return fp2
    return None


def assemble_presenter_segment(
    topic: str,
    segment_name: str,
    root: Path,
    config: PipelineConfig,
    manifest: dict[str, Any],
) -> Optional[Path]:
    tpaths = fs_utils.topic_paths(root, topic)
    in_dir = tpaths["lipsync_output"] / segment_name
    mseg = next(
        (s for s in manifest.get("segments", []) if s.get("name") == segment_name),
        None,
    )
    if mseg is None:
        mseg = {"name": segment_name, "errors": []}
        manifest.setdefault("segments", []).append(mseg)
    fs_utils.ensure_dir(in_dir)
    parts = [
        p
        for p in in_dir.iterdir()
        if p.suffix.lower() == ".mp4" and p.is_file()
    ]

    def _k(p: Path) -> tuple[int, str]:
        m2 = re.search(r"(\d+)", p.stem)
        return (int(m2.group(1)) if m2 else 0, p.name)
    parts = sorted(parts, key=_k)
    if not parts:
        if mseg:
            mseg.setdefault("errors", []).append("lipsync_parts_missing")
        return None
    out = tpaths["final"] / f"{segment_name}_final.mp4"
    fs_utils.ensure_dir(out.parent)
    with tempfile.TemporaryDirectory() as tmpd:
        ok = _concat_ffmpeg(
            [Path(p) for p in parts], out, config, Path(tmpd)
        )
    if not ok and mseg:
        mseg.setdefault("errors", []).append("concat_segment_failed")
    if mseg and ok:
        mseg["lipsync_output_paths"] = [fs_utils.relposix(root, p) for p in parts]
        mseg["final_segment_path"] = fs_utils.relposix(root, out)
        mseg["status"] = "segment_finalized"
    if ok:
        return out
    return None


def assemble_full_timeline(
    topic: str,
    root: Path,
    config: PipelineConfig,
    manifest: dict[str, Any],
) -> Optional[Path]:
    tpaths = fs_utils.topic_paths(root, topic)
    main = tpaths["video"]
    if not main.is_file():
        manifest.setdefault("errors", []).append("narrative_main_missing")
        return None
    tl = fs_utils.read_json(tpaths["timeline"])
    if not tl:
        manifest.setdefault("errors", []).append("no_timeline")
        return None
    T = float(tl.get("video_duration_sec", 0.0))
    if T <= 0:
        T = duration.ffprobe_duration(main)
    segs: List[dict[str, Any]] = list(tl.get("segments", []))
    by_name: dict[str, dict[str, Any]] = {
        m.get("name"): m for m in manifest.get("segments", []) if m.get("name")
    }
    intro = next((s for s in segs if s.get("type") == "intro"), None)
    outro = next((s for s in segs if s.get("type") == "outro"), None)
    mids = [
        s for s in segs
        if s.get("type") == "insert"
    ]
    mids = sorted(mids, key=lambda x: float(x.get("anchor_sec", 0.0)))
    anchors = [float(m.get("anchor_sec", 0.0)) for m in mids]

    with tempfile.TemporaryDirectory() as tmpd:
        tmp = Path(tmpd)
        pieces: list[Path] = []
        n = 0

        def _append_presenter(sname: str) -> None:
            nonlocal n
            mseg = by_name.get(sname)
            if mseg is None:
                mseg = {"name": sname, "errors": []}
                manifest.setdefault("segments", []).append(mseg)
                by_name[sname] = mseg
            fpath = _final_for_segment(tpaths, sname, mseg, root)
            if fpath and fpath.is_file():
                np = tmp / f"seg_{n:04d}.mp4"
                if _norm_segment(fpath, np, config):
                    pieces.append(np)
                else:
                    mseg.setdefault("errors", []).append("norm_presenter_failed")
            else:
                mseg.setdefault("errors", []).append(
                    f"presenter_final_missing:{sname}"
                )
                log_presenter(
                    LOG,
                    topic,
                    sname,
                    "assemble_full",
                    "skip: presenter final missing",
                    level=logging.WARNING,
                )
            n += 1

        if intro is not None:
            _append_presenter(str(intro.get("name")))

        a_prev = 0.0
        for j, a in enumerate(anchors):
            iname = str(mids[j].get("name", ""))
            mseg_ = by_name.get(iname)
            if mseg_ is None:
                mseg_ = {"name": iname, "errors": []}
                manifest.setdefault("segments", []).append(mseg_)
                by_name[iname] = mseg_
            if a_prev < a and (a - a_prev) > 0.01:
                np = tmp / f"main_{n:04d}.mp4"
                if _extract_main(main, a_prev, a, np, config, tmp):
                    pieces.append(np)
                else:
                    mseg_.setdefault("errors", []).append("extract_narrative_failed")
            sname = str(mids[j].get("name", ""))
            _append_presenter(sname)
            a_prev = a
        if a_prev < T and (T - a_prev) > 0.01:
            np = tmp / f"main_{n:04d}.mp4"
            if not _extract_main(main, a_prev, T, np, config, tmp):
                manifest.setdefault("errors", []).append("extract_tail_narrative_failed")
            else:
                pieces.append(np)
        if outro is not None:
            _append_presenter(str(outro.get("name")))

        if not pieces:
            manifest.setdefault("errors", []).append("no_pieces_to_concat")
            return None
        out = tpaths["final_output"]
        fs_utils.ensure_dir(out.parent)
        if not _concat_ffmpeg(pieces, out, config, tmp):
            manifest.setdefault("errors", []).append("final_concat_failed")
            return None
    manifest["final_output_path"] = fs_utils.relposix(root, out)
    log_presenter(
        LOG, topic, "--", "assemble_full", f"wrote {out}"
    )
    return out
