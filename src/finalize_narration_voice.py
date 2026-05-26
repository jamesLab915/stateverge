"""
After :file:`output/final_mix.mp4` (mix_engine), add Chinese narration TTS and mux.

1. Read ``topics/<topic>/brief/narration_script.txt`` (see :mod:`.integrations.narration_eleven_wav`)
2. ElevenLabs → ``topics/<topic>/audio/voice.wav`` (``ELEVENLABS_API_KEY`` / ``ELEVENLABS_VOICE_ID``)
3. Mux: ``output/final_mix.mp4`` + ``audio/voice.wav`` → ``output/final_with_voice.mp4``  
   (if video is shorter than narration, **loop** the video; if longer, **-shortest** to narration)
4. ffprobe checks: video + audio streams, 1920×1080, duration within ~0.8s of ``voice.wav``

Use::

  python -m src.finalize_narration_voice --topic ftx_cn
  python -m src.finalize_narration_voice --topic ftx_cn --remux-only   # reuse existing voice.wav
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

LOG = "finalize_narration_voice"


def _log(msg: str) -> None:
    print(f"[{LOG}] {msg}", flush=True)


def _root() -> Path:
    return Path(os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")).resolve()


def _run_cap(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _ffprobe_json(path: Path) -> dict[str, Any]:
    args = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    p = _run_cap(args)
    if p.returncode != 0 or not (p.stdout or "").strip():
        raise RuntimeError(f"ffprobe failed: {path}\n{p.stderr}")
    return json.loads(p.stdout)


def probe_format_duration(path: Path) -> float:
    d = _ffprobe_json(path).get("format", {}).get("duration")
    if d is not None:
        return float(d)
    raise RuntimeError(f"ffprobe: no format duration: {path}")


def mux_video_to_narration_wav(video: Path, wav: Path, out: Path) -> bool:
    """
    Output duration = narration (``wav``) length. Loop ``video`` if it is shorter.
    """
    if not video.is_file() or not wav.is_file():
        return False
    d_vid = max(0.0, probe_format_duration(video))
    d_wav = max(0.0, probe_format_duration(wav))
    if d_wav < 0.1:
        _log("ERROR: voice.wav has no measurable duration")
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.unlink(missing_ok=True)
    except OSError:
        pass
    t_arg = f"{d_wav:.3f}"
    # Hard-cap output to narration length (avoids AAC + MP4 timebase drift vs. -shortest alone)
    t_out = ["-t", t_arg]

    # Loop only when we need to stretch video; otherwise -shortest trims the longer stream
    if d_wav > d_vid + 0.05:
        _log(
            f"log: video dur={d_vid:.3f}s < voice dur={d_wav:.3f}s — looping video with -stream_loop -1, -shortest"
        )
        # -stream_loop with two -i inputs: use -filter_complex so scale/pad
        # unambiguously targets [0:v] (a lone -vf between -i is parsed incorrectly on some ffmpeg).
        fcv = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p[vout]"
        )
        p = _run_cap(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                str(video),
                "-i",
                str(wav),
                "-filter_complex",
                fcv,
                "-map",
                "[vout]",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-b:a",
                "192k",
                "-shortest",
                *t_out,
                "-movflags",
                "+faststart",
                str(out),
            ],
        )
    else:
        _log(
            f"log: video dur={d_vid:.3f}s ≥ voice dur={d_wav:.3f}s — copy video, mux AAC from voice (-shortest)"
        )
        p = _run_cap(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-i",
                str(video),
                "-i",
                str(wav),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-b:a",
                "192k",
                "-shortest",
                *t_out,
                "-movflags",
                "+faststart",
                str(out),
            ],
        )
    if p.returncode != 0:
        _log(f"ERROR: ffmpeg mux: {(p.stderr or '')[:4000]}")
        return False
    if not out.is_file():
        return False
    return True


def verify_narration_output(
    out_mp4: Path,
    expected_wav: Path,
    *,
    max_dur_delta: float = 0.8,
) -> tuple[bool, str]:
    if not out_mp4.is_file():
        return False, "output mp4 missing"
    try:
        t_v = _ffprobe_json(out_mp4)
        t_a = _ffprobe_json(expected_wav)
    except OSError as e:
        return False, str(e)
    fmt_d = float(t_v.get("format", {}).get("duration", 0) or 0)
    wav_d = float(t_a.get("format", {}).get("duration", 0) or 0)
    if wav_d < 0.1:
        return False, f"ref voice.wav has bad duration: {wav_d!r}"
    has_v = False
    has_a = False
    vw = vh = 0
    for s in t_v.get("streams", []) or []:
        if s.get("codec_type") == "video":
            has_v = True
            try:
                vw, vh = int(s.get("width", 0)), int(s.get("height", 0))
            except (TypeError, ValueError):
                vw, vh = 0, 0
        if s.get("codec_type") == "audio":
            has_a = True
    errs: list[str] = []
    if not has_v:
        errs.append("no video stream")
    if not has_a:
        errs.append("no audio stream")
    if (vw, vh) != (1920, 1080):
        errs.append(f"expected 1920x1080, got {vw}x{vh}")
    if abs(fmt_d - wav_d) > max_dur_delta:
        errs.append(
            f"out duration {fmt_d:.3f}s not close to voice.wav {wav_d:.3f}s (Δ>{max_dur_delta}s)"
        )
    if errs:
        return False, "; ".join(errs)
    return True, f"ok video+audio 1920x1080 out={fmt_d:.3f}s ref_voice={wav_d:.3f}s"


def run_finalize(
    root: Path,
    topic: str,
    *,
    remux_only: bool = False,
) -> int:
    root = root.resolve()
    tdir = root / "topics" / topic
    out_wav = tdir / "audio" / "voice.wav"
    work = tdir / "output" / "_narration_tts_work"
    final_mix = tdir / "output" / "final_mix.mp4"
    out_mp4 = tdir / "output" / "final_with_voice.mp4"

    if not final_mix.is_file():
        _log(f"ERROR: missing {final_mix} (build mix first: run_pipeline / auto_video step 6)")
        return 2
    if remux_only:
        if not out_wav.is_file():
            _log(
                f"ERROR: --remux-only but {out_wav} is missing. Generate TTS first (omit --remux-only)."
            )
            return 3
    else:
        from src.integrations.narration_eleven_wav import build_voice_wav_from_brief

        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        if not build_voice_wav_from_brief(root, topic, out_wav, work):
            return 1
        _log(f"wrote voice.wav → {out_wav}")

    d_w = probe_format_duration(out_wav)
    _log(f"voice duration {d_w:.3f}s; muxing → {out_mp4.name}")
    if not mux_video_to_narration_wav(final_mix, out_wav, out_mp4):
        return 4
    ok, msg = verify_narration_output(out_mp4, out_wav)
    if not ok:
        _log(f"ffprobe verify FAIL: {msg}")
        return 5
    _log(f"ffprobe verify OK: {msg}")
    _log(f"output: {out_mp4}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Narration TTS (ElevenLabs) + mux to final_with_voice.mp4 from brief/narration_script.txt."
        )
    )
    ap.add_argument("--topic", required=True, help="Topic under topics/<slug>/")
    ap.add_argument(
        "--root", type=Path, default=None, help="StateVerge root (default: env or ~/StateVerge)"
    )
    ap.add_argument(
        "--remux-only",
        action="store_true",
        help="Skip ElevenLabs; use existing topics/<t>/audio/voice.wav",
    )
    args = ap.parse_args(argv)
    r = (args.root or _root()).resolve()
    if args.topic.strip() == "":
        _log("error: empty topic")
        return 2
    return run_finalize(r, args.topic.strip(), remux_only=bool(args.remux_only))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
