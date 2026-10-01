"""Render a Section 15 video from an Archive Card with ffmpeg.

    录像不会失忆。 → 旧视频 (日期/人物/来源) → 定格 + 旁白
    → 新视频 → 旁白 / 事实核查 → 时间线 → 结尾 STATEVERGE ARCHIVE

Text is burned in with ASS subtitles (libass), so only an ffmpeg build with
the ``subtitles`` filter and a CJK font are needed — no drawtext. Narration is
on-screen text; voice-over is not generated.

Excerpts come from ``clip_builder.plan_clip`` (3–12 s, reason required for
longer, transformative elements required), so the guard's limits apply to
the rendered video too. Sources are local files you are allowed to use;
this module never downloads anything.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .clip_builder import ClipPlan
from .models import PoliticalClaim
from .script_generator import BRAND, ArchiveCard, Shot, video_script, zh_month

W, H, FPS = 1280, 720, 30
X_MAX_SECONDS = 140  # X limit for regular accounts
DEFAULT_FONT = "Noto Sans CJK SC"

_ENCODE = [
    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k", "-movflags", "+faststart",
]


class RenderError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        raise RenderError("ffmpeg not found — install it or set FFMPEG=/path/to/ffmpeg")
    return exe


# -- ASS subtitles ------------------------------------------------------------


def _ass_text(s: str) -> str:
    s = s.replace("\\", "＼").replace("{", "（").replace("}", "）")
    return s.replace("\r", "").replace("\n", "\\N")


def _ts(sec: float) -> str:
    cs = int(round(sec * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_document(lines: list[tuple[str, str]], duration: float, font: str) -> str:
    """lines: (style, text). Styles: Title, Caption, Sub, Brand."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,{font},60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,5,80,80,40,1
Style: Caption,{font},34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,3,8,0,7,40,40,36,1
Style: Sub,{font},34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,3,8,0,2,80,80,48,1
Style: Brand,{font},22,&H00C8C8C8,&H00C8C8C8,&H00000000,&H00000000,1,0,0,0,100,100,2,0,1,1,0,9,30,30,24,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [f"Dialogue: 0,{_ts(0)},{_ts(duration)},{style},,0,0,0,,{_ass_text(text)}" for style, text in lines]
    events.append(f"Dialogue: 0,{_ts(0)},{_ts(duration)},Brand,,0,0,0,,{BRAND}")
    return head + "\n".join(events) + "\n"


# -- segments -----------------------------------------------------------------


@dataclass
class Segment:
    kind: str  # CARD | CLIP | FREEZE
    duration: float
    lines: list[tuple[str, str]]
    claim: PoliticalClaim | None = None
    plan: ClipPlan | None = None


def _card_seconds(text: str) -> float:
    return round(min(6.0, max(2.5, len(text) / 7)), 2)


def _clip_lines(c: PoliticalClaim) -> list[tuple[str, str]]:
    lines = [("Caption", f"{zh_month(c.statement_date)}  {c.person_name}\n来源:{c.source_name}")]
    lines.append(("Sub", c.statement_text_zh or c.statement_text_original))
    return lines


def plan_segments(card: ArchiveCard, plans: dict[str, ClipPlan]) -> list[Segment]:
    claims = {c.claim_id: c for c in card.claims}
    shots: list[Shot] = video_script(card)
    segs: list[Segment] = []
    i = 0
    while i < len(shots):
        s = shots[i]
        if s.kind == "CLIP":
            plan = plans.get(s.claim_id)
            if plan is None:
                raise RenderError(f"no clip plan for claim {s.claim_id}")
            segs.append(Segment("CLIP", plan.duration, _clip_lines(claims[s.claim_id]), claims[s.claim_id], plan))
        elif s.kind == "FREEZE":
            # The narration that follows is shown over the frozen frame.
            text = shots[i + 1].text if i + 1 < len(shots) and shots[i + 1].kind == "NARRATION" else ""
            if text:
                i += 1
            segs.append(Segment("FREEZE", 2.5, [("Sub", text)] if text else [], claims[s.claim_id], plans[s.claim_id]))
        else:
            text = s.text or s.caption
            segs.append(Segment("CARD", 3.5 if s.kind == "END" else _card_seconds(text), [("Title", text)]))
        i += 1
    total = sum(seg.duration for seg in segs)
    if total > X_MAX_SECONDS:
        raise RenderError(f"video would be {total:.0f}s, over X's {X_MAX_SECONDS}s limit")
    return segs


def _has_audio(ffmpeg: str, src: Path) -> bool:
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(src)], capture_output=True, text=True)
    return bool(re.search(r"Stream #\S+.*Audio:", probe.stderr))


def _fit() -> str:
    return (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}")


def _segment_cmd(ffmpeg: str, seg: Segment, src: Path | None, ass: Path, out: Path) -> list[str]:
    sub = f"subtitles=filename={ass.as_posix()}"
    silence = ["-f", "lavfi", "-t", f"{seg.duration}", "-i", "anullsrc=r=44100:cl=stereo"]
    if seg.kind == "CARD":
        return [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={seg.duration}", *silence,
                "-vf", sub, "-map", "0:v", "-map", "1:a", "-t", f"{seg.duration}", *_ENCODE, str(out)]
    assert src is not None and seg.plan is not None
    if seg.kind == "FREEZE":
        at = max(0.0, seg.plan.end - 0.05)
        vf = f"trim=end_frame=1,setpts=PTS-STARTPTS,{_fit()},tpad=stop_mode=clone:stop_duration={seg.duration},{sub}"
        return [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(src), *silence,
                "-vf", vf, "-map", "0:v", "-map", "1:a", "-t", f"{seg.duration}", *_ENCODE, str(out)]
    # CLIP
    head = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{seg.plan.start:.3f}", "-t", f"{seg.duration:.3f}", "-i", str(src)]
    if _has_audio(ffmpeg, src):
        return [*head, "-vf", f"{_fit()},{sub}", "-map", "0:v", "-map", "0:a",
                "-af", "aresample=44100", *_ENCODE, str(out)]
    return [*head, *silence, "-vf", f"{_fit()},{sub}", "-map", "0:v", "-map", "1:a",
            "-t", f"{seg.duration}", *_ENCODE, str(out)]


def render(
    card: ArchiveCard,
    plans: dict[str, ClipPlan],
    sources: dict[str, Path],
    out: Path,
    font: str | None = None,
    ffmpeg: str | None = None,
) -> Path:
    """Render the card to ``out`` (mp4, 1280×720). ``plans`` and ``sources`` are
    keyed by claim_id."""
    ffmpeg = ffmpeg or ffmpeg_bin()
    font = font or os.environ.get("ARCHIVE_FONT") or DEFAULT_FONT
    segs = plan_segments(card, plans)
    for seg in segs:
        if seg.claim and not Path(sources.get(seg.claim.claim_id, "")).is_file():
            raise RenderError(f"source video missing for claim {seg.claim.claim_id}")

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="archive-render-") as tmp:
        tmpdir = Path(tmp)
        parts = []
        for n, seg in enumerate(segs):
            ass = tmpdir / f"{n:02d}.ass"
            ass.write_text(ass_document(seg.lines, seg.duration, font), encoding="utf-8")
            part = tmpdir / f"{n:02d}.mp4"
            src = Path(sources[seg.claim.claim_id]) if seg.claim else None
            r = subprocess.run(_segment_cmd(ffmpeg, seg, src, ass, part), capture_output=True, text=True)
            if r.returncode != 0:
                raise RenderError(f"segment {n} ({seg.kind}) failed: {r.stderr[-500:]}")
            parts.append(part)
        listing = tmpdir / "list.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        r = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(out)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RenderError(f"concat failed: {r.stderr[-500:]}")
    return out
