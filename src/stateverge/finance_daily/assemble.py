"""ffmpeg assembly: one clip per segment → concat → subtitles + watermark."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

W, H, FPS = 1920, 1080, 30
GAP = 0.25  # breath after each segment
SUB_FONT = os.environ.get("FINANCE_SUB_FONT", "Noto Sans CJK SC")


@dataclass
class Shot:
    audio: Path | None  # None → silent card
    image: Path | None = None  # still frame (slow pan)
    video: Path | None = None  # anchor clip (Runway)
    overlay: Path | None = None  # transparent PNG on top (lower third)
    seconds: float = 0.0  # length for silent cards
    text: str = ""  # subtitle text


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def duration(path: Path) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def render_shot(shot: Shot, out: Path) -> float:
    dur = (duration(shot.audio) + GAP) if shot.audio else shot.seconds
    inputs: list[str] = []
    if shot.video:
        inputs += ["-i", str(shot.video)]
        v = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
             f"setsar=1,fps={FPS},tpad=stop_mode=clone:stop_duration={dur}[base]")
    else:
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(shot.image)]
        v = (f"[0:v]scale={int(W * 1.05)}:{int(H * 1.05)},crop={W}:{H}:x='{W * 0.05}*t/{dur:.3f}':"
             f"y='{H * 0.05}*t/{dur:.3f}',setsar=1,fps={FPS}[base]")
    chain = [v]
    if shot.overlay:
        inputs += ["-i", str(shot.overlay)]
        chain.append("[base][1:v]overlay=0:0[vout]")
    else:
        chain.append("[base]null[vout]")
    a_idx = len([x for x in inputs if x == "-i"])
    if shot.audio:
        inputs += ["-i", str(shot.audio)]
        chain.append(f"[{a_idx}:a]aresample=48000,aformat=channel_layouts=stereo,apad[aout]")
    else:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        chain.append(f"[{a_idx}:a]anull[aout]")
    _run([*inputs, "-filter_complex", ";".join(chain), "-map", "[vout]", "-map", "[aout]", "-t", f"{dur:.3f}",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(out)])
    return dur


def _chunks(text: str, limit: int = 18) -> list[str]:
    parts = [p for p in re.split(r"(?<=[,。!?;:、,!?;])", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        p = p.strip()
        while len(p) > limit:
            out.append(p[:limit])
            p = p[limit:]
        if p:
            out.append(p)
    return out


def _ts(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)) % 1000:03d}"


def srt(shots: list[Shot], durations: list[float]) -> str:
    lines, n, t = [], 1, 0.0
    for shot, dur in zip(shots, durations, strict=True):
        chunks = _chunks(shot.text) if shot.text else []
        speak = max(dur - GAP, 0.1)
        total = sum(len(c) for c in chunks) or 1
        start = t
        for c in chunks:
            end = start + speak * len(c) / total
            lines.append(f"{n}\n{_ts(start)} --> {_ts(end)}\n{c.rstrip(',、,')}\n")
            n, start = n + 1, end
        t += dur
    return "\n".join(lines)


def assemble(shots: list[Shot], work: Path, out: Path, watermark: Path | None = None) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    clips, durs = [], []
    for i, shot in enumerate(shots):
        clip = work / f"shot_{i:02d}.mp4"
        durs.append(render_shot(shot, clip))
        clips.append(clip)
    (work / "list.txt").write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    (work / "subs.srt").write_text(srt(shots, durs), encoding="utf-8")
    style = (f"FontName={SUB_FONT},FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
             "BorderStyle=1,Outline=2,Shadow=0,MarginV=36")
    vf = f"subtitles='{(work / 'subs.srt').resolve()}':force_style='{style}'"
    args = ["-f", "concat", "-safe", "0", "-i", str(work / "list.txt")]
    if watermark and watermark.exists():
        args += ["-i", str(watermark)]
        fc = (f"[0:v]{vf}[s];[1:v]scale=110:-1,format=rgba,colorchannelmixer=aa=0.55[wm];"
              f"[s][wm]overlay=W-w-40:H-h-40[v]")
    else:
        fc = f"[0:v]{vf}[v]"
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([*args, "-filter_complex", fc, "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "medium",
          "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
    return out
