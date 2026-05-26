#!/usr/bin/env python3
"""StateVerge music-bed fade policy (ffmpeg afade) for filter_complex graphs."""
from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_FADE_IN_SEC = 2.0
DEFAULT_FADE_OUT_SEC = 3.0
SHORTS_FADE_IN_SEC = 0.8
SHORTS_FADE_OUT_SEC = 1.2
FAILOPEN_FADE_SEC = 2.0
MIN_CLIP_FOR_FULL_FADE_SEC = 6.0

MUSIC_FADE_POLICY_READY = True


@dataclass(frozen=True)
class MusicFadeSpec:
    fade_in_sec: float
    fade_out_sec: float
    fade_out_start_sec: float
    duration_sec: float

    def filter_chain(self) -> str:
        return (
            f"afade=t=in:st=0:d={self.fade_in_sec:.3f},"
            f"afade=t=out:st={self.fade_out_start_sec:.3f}:d={self.fade_out_sec:.3f}"
        )


def resolve_music_fade(
    duration_sec: float | None,
    *,
    shorts: bool = False,
) -> MusicFadeSpec:
    """Compute fade-in/out for a trimmed music segment (before amix)."""
    if duration_sec is None or not math.isfinite(duration_sec) or duration_sec <= 0:
        fade_in = FAILOPEN_FADE_SEC
        fade_out = FAILOPEN_FADE_SEC
        dur = fade_in + fade_out + 0.5
        return MusicFadeSpec(fade_in, fade_out, max(0.0, dur - fade_out), dur)

    dur = float(duration_sec)
    if shorts or dur < 30.0:
        fade_in = SHORTS_FADE_IN_SEC
        fade_out = SHORTS_FADE_OUT_SEC
    else:
        fade_in = DEFAULT_FADE_IN_SEC
        fade_out = DEFAULT_FADE_OUT_SEC

    if dur < MIN_CLIP_FOR_FULL_FADE_SEC:
        max_each = dur * 0.4
        fade_in = min(fade_in, max_each)
        fade_out = min(fade_out, max_each)
        total = fade_in + fade_out
        if total >= dur * 0.95:
            scale = (dur * 0.9) / max(total, 1e-6)
            fade_in *= scale
            fade_out *= scale

    fade_in = max(0.05, fade_in)
    fade_out = max(0.05, fade_out)
    if fade_in + fade_out >= dur:
        fade_in = min(fade_in, dur * 0.45)
        fade_out = min(fade_out, max(0.05, dur - fade_in - 0.05))

    st_out = max(0.0, dur - fade_out)
    return MusicFadeSpec(fade_in, fade_out, st_out, dur)


def music_fade_filter(duration_sec: float | None, *, shorts: bool = False) -> str:
    """Return comma-separated afade=in,out chain for ffmpeg filter_complex."""
    return resolve_music_fade(duration_sec, shorts=shorts).filter_chain()
