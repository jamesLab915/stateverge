"""Render a hand-checked episode JSON (data/explainer/episodes/<id>.json) to video."""

from __future__ import annotations

import json
from pathlib import Path

from ..finance_daily.assemble import Shot, assemble
from ..finance_daily.runner import _download, publish_release
from ..finance_daily.runway import RunwayClient, RunwayError
from ..finance_daily.tts import Speaker
from . import frames

WATERMARK = Path("branding/youtube/watermark_S_150.png")
PORTRAIT = Path("data/finance/anchor_portrait.jpg")
EN_INSTRUCTIONS = ("Speak in clear, warm, natural American English like a calm educational explainer host. "
                   "Measured pace, friendly, no hype.")
SUB_STYLE = ("FontName=Noto Sans CJK SC,FontSize=17,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
             "BorderStyle=1,Outline=2,Shadow=0,MarginV=34")


def en_chunks(text: str, limit: int = 46) -> list[str]:
    out, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if len(trial) > limit and line:
            out.append(line)
            line = word
        else:
            line = trial
        if line.endswith((".", "?", "!")) and len(line) > limit * 0.5:
            out.append(line)
            line = ""
    if line:
        out.append(line)
    return out


def render_episode(root: Path, episode_id: str, item: dict, speaker=None, runway=None,
                   publish=publish_release) -> dict:
    ep = json.loads((root / "episodes" / f"{episode_id}.json").read_text(encoding="utf-8"))
    work = Path("explainer_build") / episode_id
    work.mkdir(parents=True, exist_ok=True)
    series = ep.get("series", "THE FED, EXPLAINED")

    speaker = speaker or Speaker(voice=ep.get("voice", "alloy"), instructions=EN_INSTRUCTIONS)

    runway = runway if runway is not None else RunwayClient()
    avatar = ""
    notes = []
    if runway.available and item.get("anchor", True):
        state = Path("data/finance/avatar.json")
        avatar = json.loads(state.read_text()).get("avatar_id", "") if state.exists() else ""
        if not avatar:
            notes.append("no Runway avatar yet (render a finance episode first); presenter shown as still")
    elif item.get("anchor", True):
        notes.append("RUNWAYML_API_SECRET not set; presenter shown as still")

    lower = frames.lower_third(work / "lower_third.png")
    shots = []
    for i, seg in enumerate(ep["segments"]):
        audio = speaker.speak(seg["text"], work / f"seg_{i:02d}.mp3")
        if seg["kind"] == "anchor":
            clip = None
            if avatar:
                try:
                    clip = _download(runway.avatar_video(avatar, audio), work / f"anchor_{i:02d}.mp4")
                except (RunwayError, OSError) as e:
                    notes.append(f"Runway failed on segment {i}: {e}"[:200])
            shots.append(Shot(audio, image=None if clip else PORTRAIT, video=clip, overlay=lower, text=seg["text"]))
            continue
        v = seg["visual"]
        frame = frames.RENDERERS[v["type"]](v, series, work / f"frame_{i:02d}.png")
        shots.append(Shot(audio, image=frame, text=seg["text"]))

    video = assemble(shots, work / "build", work / f"stateverge_markets_{episode_id}.mp4", WATERMARK,
                     chunker=en_chunks, sub_style=SUB_STYLE)
    refs = "\n".join(f"- {s}" for s in ep.get("sources", []))
    body = f"{ep.get('description', '')}\n\nSources:\n{refs}\n\nEducational content, not financial advice. " \
           "Presenter is an AI avatar. Not affiliated with the Federal Reserve."
    from datetime import datetime, timezone
    url = publish(video, f"explainer-{episode_id}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}", ep["title"], body)
    return {"status": "rendered", "video_url": url, "title": ep["title"], "notes": notes,
            "segments": len(ep["segments"])}
