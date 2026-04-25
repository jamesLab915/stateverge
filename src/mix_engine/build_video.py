"""
Assemble mix timeline: ``topics/<topic>/output/final_mix.mp4`` (or ``--out``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from .timeline_loader import (
    InterviewClip,
    LtxOrEnvatoClip,
    load_timeline,
    topic_mix_paths,
)

LOG = logging.getLogger("mix_engine.build_video")

W, H, FPS, AR = 1920, 1080, 30, 48000


def _root() -> Path:
    return Path(
        os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")
    ).resolve()


def _run(args: list[str]) -> str:
    p = subprocess.run(args, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        out = (p.stdout or "") + (p.stderr or "")
        raise RuntimeError(f"Command failed: {' '.join(args)}\n{out[:8000]}")
    return (p.stdout or "") + (p.stderr or "")


def _ffprobe_json(path: Path) -> dict[str, Any]:
    args = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    p = subprocess.run(args, capture_output=True, text=True, check=False)
    if p.returncode != 0 or not (p.stdout or "").strip():
        raise RuntimeError(f"ffprobe failed: {path}\n{p.stderr}")
    return json.loads(p.stdout)


def probe_format_duration(path: Path) -> float:
    d = _ffprobe_json(path).get("format", {}).get("duration")
    if d is not None:
        return float(d)
    raise RuntimeError(f"ffprobe: no format duration: {path}")


def has_audio_stream(path: Path) -> bool:
    args = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        str(path),
    ]
    p = subprocess.run(args, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        return False
    try:
        data = json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return False
    for s in data.get("streams", []) or []:
        if s.get("codec_type") == "audio":
            return True
    return False


def _resolve(root: Path, topic: str, relp: str) -> Path:
    base = (root / "topics" / topic).resolve()
    p = (base / relp).resolve()
    try:
        p.relative_to(base)
    except ValueError as e:
        raise ValueError(f"path escapes topic root: {relp!r}") from e
    return p


def _vf_base() -> str:
    return (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={FPS},format=yuv420p,setsar=1"
    )


def _build_ltx_envato(
    root: Path, topic: str, clip: LtxOrEnvatoClip, out_seg: Path, *, log_label: str
) -> None:
    src = _resolve(root, topic, clip.file)
    if not src.is_file():
        raise FileNotFoundError(
            f"[{log_label}] type={clip.type} file missing: {src} (file={clip.file!r})"
        )
    d_target = float(clip.duration)
    if d_target <= 0:
        raise ValueError(f"[{log_label}] duration must be positive, got {clip.duration}")
    d_in = max(0.0, probe_format_duration(src))
    if d_in < 0.1:
        raise ValueError(f"[{log_label}] could not get duration or file too short: {src}")
    d_tr = min(d_in, d_target)
    pad = max(0.0, d_target - d_tr)
    vmid = f"[0:v]setpts=PTS-STARTPTS,{_vf_base()}"
    if pad > 0.01:
        vg = f"{vmid},trim=end={d_tr},setpts=PTS-STARTPTS[vm];[vm]tpad=stop_mode=clone:stop_duration={pad},setpts=PTS-STARTPTS[vout]"
    else:
        vg = f"{vmid},trim=end={d_target},setpts=PTS-STARTPTS[vout]"

    if has_audio_stream(src):
        if pad > 0.01:
            ag = (
                f"[0:a]asetpts=PTS-STARTPTS,atrim=0:{d_tr},asetpts=PTS-STARTPTS,apad=pad_dur={pad},atrim=0:{d_target},asetpts=PTS-STARTPTS,aresample={AR},aformat=channel_layouts=stereo[aout]"
            )
        else:
            ag = f"[0:a]asetpts=PTS-STARTPTS,atrim=0:{d_target},asetpts=PTS-STARTPTS,aresample={AR},aformat=channel_layouts=stereo[aout]"
        fc = f"{vg};{ag}"
        args: list[str] = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-filter_complex",
            fc,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            str(AR),
            "-ac",
            "2",
            str(out_seg),
        ]
    else:
        anull = f"anullsrc=channel_layout=stereo:sample_rate={AR}"
        fc = f"{vg};[1:a]atrim=0:{d_target},asetpts=PTS-STARTPTS,aresample={AR},aformat=channel_layouts=stereo[aout]"
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-f",
            "lavfi",
            "-i",
            anull,
            "-filter_complex",
            fc,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            str(AR),
            "-ac",
            "2",
            "-shortest",
            str(out_seg),
        ]
    _run(args)


def _build_interview(
    root: Path, topic: str, clip: InterviewClip, out_seg: Path, *, log_label: str
) -> None:
    ap = _resolve(root, topic, clip.file)
    vp = _resolve(root, topic, clip.visual)
    if not ap.is_file():
        raise FileNotFoundError(
            f"[{log_label}] interview audio missing: {ap} (file={clip.file!r})"
        )
    if not vp.is_file():
        raise FileNotFoundError(
            f"[{log_label}] interview visual missing: {vp} (visual={clip.visual!r})"
        )
    if clip.duration is not None and float(clip.duration) > 0:
        d_target = float(clip.duration)
    else:
        try:
            d_target = probe_format_duration(ap)
        except Exception:  # noqa: BLE001
            d_target = 10.0
    d_target = max(0.1, d_target)
    d_vp = max(0.0, probe_format_duration(vp))
    d_ap = max(0.0, probe_format_duration(ap))
    d_tr = min(d_vp, d_ap, d_target) if d_vp > 0 and d_ap > 0 else d_target
    d_tr = max(0.1, min(d_tr, d_target))
    pad = max(0.0, d_target - d_tr)

    v0 = f"[0:v]setpts=PTS-STARTPTS,{_vf_base()}"
    if pad > 0.01:
        vg = f"{v0},trim=end={d_tr},setpts=PTS-STARTPTS[vm];[vm]tpad=stop_mode=clone:stop_duration={pad},setpts=PTS-STARTPTS[vout]"
    else:
        vg = f"{v0},trim=end={d_target},setpts=PTS-STARTPTS[vout]"
    if pad > 0.01:
        ag = f"[1:a]asetpts=PTS-STARTPTS,atrim=0:{d_tr},asetpts=PTS-STARTPTS,apad=pad_dur={pad},atrim=0:{d_target},asetpts=PTS-STARTPTS,aresample={AR},aformat=channel_layouts=stereo[aout]"
    else:
        ag = f"[1:a]asetpts=PTS-STARTPTS,atrim=0:{d_target},asetpts=PTS-STARTPTS,aresample={AR},aformat=channel_layouts=stereo[aout]"

    fc = f"{vg};{ag}"
    args: list[str] = [
        "ffmpeg",
        "-y",
        "-i",
        str(vp),
        "-i",
        str(ap),
        "-filter_complex",
        fc,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        str(AR),
        "-ac",
        "2",
        str(out_seg),
    ]
    _run(args)


def _concat_to_final(segment_paths: list[Path], out: Path) -> None:
    if not segment_paths:
        raise ValueError("no segments to concat")
    list_p = out.parent / ".concat_list.txt"
    try:
        lines: list[str] = []
        for p in segment_paths:
            s = p.resolve().as_posix().replace("'", r"'\''")
            lines.append(f"file '{s}'\n")
        list_p.write_text("".join(lines), encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.parent / f".{out.name}.tmp.mp4"
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_p),
                "-c",
                "copy",
                str(tmp),
            ]
        )
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
        try:
            tmp.unlink()
        except OSError:
            pass
    finally:
        try:
            list_p.unlink()
        except OSError:
            pass


def run_build(
    root: Path,
    topic: str,
    *,
    timeline: Optional[Path] = None,
    out: Optional[Path] = None,
    dry_run: bool = False,
    keep_temp: bool = False,
) -> Path:
    p = topic_mix_paths(root, topic)
    tpath = timeline or p["mix"] / "timeline.json"
    final = out or p["final_mix"]
    tl = load_timeline(tpath)
    p["mix"].mkdir(parents=True, exist_ok=True)
    cdir = p["clips_dir"]
    cdir.mkdir(parents=True, exist_ok=True)
    n = len(tl.clips)
    if dry_run:
        for i, c in enumerate(tl.clips):
            if isinstance(c, LtxOrEnvatoClip):
                ex = f" file={c.file!r} duration={c.duration}"
            else:
                ex = f" file={c.file!r} visual={c.visual!r} duration={c.duration!r}"
            print(f"DRY: clip {i+1}/{n} type={c.type}{ex}", flush=True)
        print(f"DRY: would write {n} files under {cdir} then {final}", flush=True)
        return final

    segment_paths: list[Path] = []
    for i, clip in enumerate(tl.clips):
        label = f"clip {i+1}/{n}"
        out_seg = cdir / f"clip_{i:04d}.mp4"
        try:
            LOG.info("%s type=%s", label, clip.type)
            if isinstance(clip, LtxOrEnvatoClip):
                _build_ltx_envato(root, topic, clip, out_seg, log_label=label)
            else:
                _build_interview(root, topic, clip, out_seg, log_label=label)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"{label} failed: {e}") from e
        segment_paths.append(out_seg)

    if not final.parent.exists():
        final.parent.mkdir(parents=True, exist_ok=True)
    _concat_to_final(segment_paths, final)

    if not keep_temp:
        for s in segment_paths:
            try:
                s.unlink()
            except OSError:
                pass
    LOG.info("wrote %s", final)
    return final


def build_final_mix(
    root: Path, topic: str, *, timeline_name: str = "timeline.json"
) -> Path:
    p = topic_mix_paths(root, topic)
    return run_build(root, topic, timeline=p["mix"] / timeline_name)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build final mix from mix/timeline.json (ffmpeg, stdlib only)."
    )
    ap.add_argument("--topic", required=True, help="Topic slug: topics/<slug>/")
    ap.add_argument(
        "--root", type=Path, default=None, help="StateVerge root (default env or ~/StateVerge)"
    )
    ap.add_argument(
        "--timeline",
        type=Path,
        default=None,
        help="Path to timeline.json (default: topics/<topic>/mix/timeline.json)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output mp4 (default: topics/<topic>/output/final_mix.mp4)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only, do not run ffmpeg",
    )
    ap.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep per-clip files under mix/clips/",
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    r = args.root or _root()
    try:
        out = run_build(
            r,
            args.topic,
            timeline=args.timeline,
            out=args.out,
            dry_run=bool(args.dry_run),
            keep_temp=bool(args.keep_temp),
        )
        if not args.dry_run:
            print(f"[mix_engine] output={out}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
