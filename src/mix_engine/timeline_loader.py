"""
Load and validate ``topics/<topic>/mix/timeline.json`` (JSON **array** of clips).

**ltx** / **envato** — ``file`` = video (relative to topic), ``duration`` = target length (seconds).

**interview** — ``file`` = audio, ``visual`` = video, ``duration`` optional (default: use audio
length, or 10.0 s if unknown).
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, Union

LOG = logging.getLogger("mix_engine.timeline_loader")

Intensity = Literal["low", "mid", "high"]


@dataclass(frozen=True)
class LtxOrEnvatoClip:
    type: Literal["ltx", "envato"]
    file: str
    duration: float
    note: str = ""
    intensity: Optional[Intensity] = None


@dataclass(frozen=True)
class InterviewClip:
    file: str
    """Audio path relative to ``topics/<topic>/``."""
    visual: str
    """Video (B-roll) path relative to topic root."""
    duration: Optional[float] = None
    type: Literal["interview"] = "interview"
    note: str = ""
    intensity: Optional[Intensity] = None


MixClip = Union[LtxOrEnvatoClip, InterviewClip]


@dataclass
class MixTimeline:
    """Timeline is a flat list of clips; optional metadata kept in dataclass for future use."""

    clips: list[MixClip]


def _f(x: Any) -> float:
    if isinstance(x, (int, float)) and (not isinstance(x, bool)) and x >= 0 and math.isfinite(
        float(x)
    ):
        return float(x)
    raise TypeError("duration must be a non-negative finite number")


def _opt_note(raw: dict[str, Any]) -> str:
    n = raw.get("note")
    if n is None:
        return ""
    if isinstance(n, str):
        return n
    return str(n)


def _opt_intensity(raw: dict[str, Any]) -> Optional[Intensity]:
    v = raw.get("intensity")
    if v is None:
        return None
    if v in ("low", "mid", "high"):
        return v  # type: ignore[return-value]
    raise ValueError("intensity must be low, mid, or high when set")


def _parse_clip(raw: Any, index: int) -> MixClip:
    if not isinstance(raw, dict):
        raise ValueError(f"clip[{index}] must be a JSON object")
    t = raw.get("type")
    if t not in ("ltx", "envato", "interview"):
        raise ValueError(
            f"clip[{index}].type must be ltx, envato, or interview, got {t!r}"
        )
    note = _opt_note(raw)
    oi = _opt_intensity(raw)
    if t in ("ltx", "envato"):
        p = raw.get("file")
        if not isinstance(p, str) or not p.strip():
            raise ValueError(
                f"clip[{index}] (type {t!r}) requires non-empty string 'file'"
            )
        if "duration" not in raw:
            raise ValueError(
                f"clip[{index}] (type {t!r}) requires 'duration' (seconds)"
            )
        d = _f(raw["duration"])
        return LtxOrEnvatoClip(
            type=t, file=p.strip(), duration=d, note=note, intensity=oi
        )
    fa = raw.get("file")
    vis = raw.get("visual")
    if not isinstance(fa, str) or not fa.strip():
        raise ValueError(
            f"clip[{index}] (interview) requires non-empty 'file' (audio path)"
        )
    if not isinstance(vis, str) or not vis.strip():
        raise ValueError(
            f"clip[{index}] (interview) requires non-empty 'visual' (video path)"
        )
    od = raw.get("duration", None)
    d_opt: Optional[float] = None
    if od is not None:
        d_opt = _f(od)
    return InterviewClip(
        file=fa.strip(),
        visual=vis.strip(),
        duration=d_opt,
        type="interview",
        note=note,
        intensity=oi,
    )


def load_timeline(path: Path) -> MixTimeline:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"mix timeline not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "clips" in data:
        raw_clips = data["clips"]
        if not isinstance(raw_clips, list) or not raw_clips:
            raise ValueError("object.timeline 'clips' must be a non-empty array")
    elif isinstance(data, list):
        raw_clips = data
    else:
        raise ValueError(
            "timeline must be a JSON array of clip objects, or { \"version\":1, \"clips\": [...] }"
        )
    if not raw_clips:
        raise ValueError("timeline clips array is empty")
    clips: list[MixClip] = []
    for i, c in enumerate(raw_clips):
        clips.append(_parse_clip(c, i))
    LOG.info("loaded mix timeline: %s clips from %s", len(clips), path)
    return MixTimeline(clips=clips)


def topic_mix_paths(root: Path, topic: str) -> dict[str, Path]:
    base = Path(root) / "topics" / topic
    return {
        "root": base,
        "ltx": base / "ltx",
        "video": base / "video",
        "envato": base / "envato",
        "sources": base / "sources",
        "mix": base / "mix",
        "clips_dir": base / "mix" / "clips",
        "timeline": base / "mix" / "timeline.json",
        "output": base / "output",
        "final_mix": base / "output" / "final_mix.mp4",
    }
