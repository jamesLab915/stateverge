"""
Rule-driven presenter placement: intro, ~60s cadence, min gap, tail guard, outro.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from .config import PipelineConfig
from .script_parser import PresenterScript

LOG = logging.getLogger("presenter.timeline_planner")


@dataclass
class SegmentSpec:
    name: str
    type: str
    anchor_sec: float
    text: str
    status: str
    audio_path_rel: str
    timeline_start: float = 0.0


def _dedupe_min_gap(anchors: List[float], min_gap: float) -> list[float]:
    a = sorted(anchors)
    if not a:
        return []
    r = [a[0]]
    for x in a[1:]:
        if x - r[-1] >= min_gap - 1e-6:
            r.append(x)
    return r


def _middle_anchors(
    t_end: float,
    config: PipelineConfig,
    script: PresenterScript,
) -> list[float]:
    """
    Narrative-time anchors (seconds) for middle host segments, excluding 0 and T.
    """
    min_tail = config.min_narrative_tail_sec
    min_gap = config.min_gap_between_anchors_sec
    interval = config.insert_interval_sec
    t_end = max(0.0, float(t_end))

    # User-defined anchors (when script lists inserts, even with empty text)
    if script.has_file and len(script.inserts) > 0:
        raw = [float(x.anchor_sec) for x in script.inserts]
    else:
        raw = [interval * n for n in range(1, 10_000) if interval * n < t_end - min_tail]
        raw = [x for x in raw if 0.0 < x < t_end - min_tail]  # upper bound
        if not raw:
            return []
        raw = _dedupe_min_gap(raw, min_gap)
        return [x for x in raw if 0.0 < x < t_end - min_tail - 1e-9]

    # Script-driven anchors: filter range, drop too close to intro, dedupe, tail
    cands = sorted(
        a for a in raw if 0.0 < a < t_end - min_tail and a >= min_gap
    )  # >=35s from t=0 (intro) as in spec
    cands = _dedupe_min_gap(cands, min_gap)
    return [a for a in cands if 0.0 < a < t_end - min_tail - 1e-9]


def build_segment_specs(
    topic_slug: str,
    video_duration_sec: float,
    script: PresenterScript,
    config: PipelineConfig,
) -> list[SegmentSpec]:
    t_end = max(0.0, float(video_duration_sec))
    mid_anchors = _middle_anchors(t_end, config, script)
    segs: list[SegmentSpec] = []

    # ---- intro
    intro_text = script.intro_text if script.has_file else ""
    st_int = "planned" if intro_text.strip() else "placeholder"
    segs.append(
        SegmentSpec(
            name="intro",
            type="intro",
            anchor_sec=0.0,
            text=intro_text,
            status=st_int,
            audio_path_rel="topics/%s/audio/presenter/intro.wav" % topic_slug,
            timeline_start=0.0,
        )
    )

    def _text_for_anchor(a: float) -> str:
        if not script.has_file or not script.inserts:
            return ""
        best = min(script.inserts, key=lambda it: abs(it.anchor_sec - a))
        if abs(best.anchor_sec - a) < 0.5:
            return best.text
        return ""

    for j, a in enumerate(mid_anchors, start=1):
        name = f"insert_{j:02d}"
        tx = _text_for_anchor(a)
        st = "planned" if (tx and tx.strip()) else "placeholder"
        segs.append(
            SegmentSpec(
                name=name,
                type="insert",
                anchor_sec=float(a),
                text=tx,
                status=st,
                audio_path_rel="topics/%s/audio/presenter/%s.wav" % (topic_slug, name),
                timeline_start=float(a),
            )
        )

    # ---- outro
    outro_text = script.outro_text if script.has_file else ""
    st_ou = "planned" if outro_text.strip() else "placeholder"
    segs.append(
        SegmentSpec(
            name="outro",
            type="outro",
            anchor_sec=t_end,
            text=outro_text,
            status=st_ou,
            audio_path_rel="topics/%s/audio/presenter/outro.wav" % topic_slug,
            timeline_start=t_end,
        )
    )
    return segs


def _audio_rel(root: Path, topic_slug: str, seg_name: str) -> str:
    if seg_name == "intro":
        fn = "intro.wav"
    elif seg_name == "outro":
        fn = "outro.wav"
    else:
        fn = f"{seg_name}.wav"
    p = root / "topics" / topic_slug / "audio" / "presenter" / fn
    try:
        return p.relative_to(root).as_posix()
    except Exception:
        return f"topics/{topic_slug}/audio/presenter/{fn}"


def plan_presenter_timeline(
    root: Path,
    topic_slug: str,
    video_duration_sec: float,
    script: PresenterScript,
    config: PipelineConfig,
) -> dict[str, Any]:
    """Build presenter_timeline.json dict: audio_path relative to project root."""
    segs = build_segment_specs(topic_slug, video_duration_sec, script, config)
    out_segments: list[dict[str, Any]] = []
    for s in segs:
        ap = _audio_rel(root, topic_slug, s.name)
        out_segments.append(
            {
                "name": s.name,
                "type": s.type,
                "timeline_start": float(s.timeline_start),
                "anchor_sec": float(s.anchor_sec),
                "text": s.text,
                "audio_path": ap,
                "status": s.status,
            }
        )
    return {
        "topic_slug": topic_slug,
        "video_duration_sec": round(float(video_duration_sec), 3),
        "segments": out_segments,
    }
