"""Short excerpt planning (Section 8).

Default excerpt: 3–12 seconds, only what is needed to understand the
statement. Longer excerpts need a written reason and are capped. Every clip
must be paired with at least one transformative element; re-uploading with
new subtitles only is refused.

This module plans clips and builds ffmpeg commands; it never downloads or
executes anything itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .copyright import is_copyright_recorded
from .models import DownloadStatus, PoliticalClaim

MIN_SECONDS = 3.0
DEFAULT_MAX_SECONDS = 12.0
HARD_MAX_SECONDS = 30.0

TRANSFORMATIVE_ELEMENTS = frozenset(
    {
        "ZH_NARRATION",  # 中文旁白
        "HISTORICAL_COMPARISON",  # 历史对比
        "FACT_CHECK",  # 事实核查
        "TIMELINE",  # 时间线
        "CONTEXT_EXPLANATION",  # 上下文解释
        "POLICY_OUTCOME",  # 政策结果
    }
)

REPO_ROOT = Path(__file__).resolve().parents[4]
MEDIA_ROOT = REPO_ROOT / "media"
SOURCE_DIR = MEDIA_ROOT / "source"
CLIPS_DIR = MEDIA_ROOT / "clips"
THUMBS_DIR = MEDIA_ROOT / "thumbnails"


class ClipRefused(ValueError):
    pass


@dataclass
class ClipPlan:
    claim_id: str
    start: float
    end: float
    transformative: list[str]
    extend_reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    @property
    def clip_path(self) -> Path:
        return CLIPS_DIR / f"{self.claim_id}.mp4"

    @property
    def thumbnail_path(self) -> Path:
        return THUMBS_DIR / f"{self.claim_id}.jpg"


def plan_clip(
    claim: PoliticalClaim,
    transformative: list[str],
    extend_reason: str = "",
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> ClipPlan:
    if claim.video_start is None or claim.video_end is None:
        raise ClipRefused("claim has no video_start/video_end — verify against a timed transcript first")
    if not claim.transcript_verified:
        raise ClipRefused("quote is not transcript-verified")
    if not is_copyright_recorded(claim):
        raise ClipRefused("copyright owner/basis not recorded")

    elements = [e for e in transformative if e in TRANSFORMATIVE_ELEMENTS]
    unknown = sorted(set(transformative) - TRANSFORMATIVE_ELEMENTS)
    if unknown:
        raise ClipRefused(f"not transformative elements: {unknown} (subtitles alone do not count)")
    if not elements:
        raise ClipRefused("at least one transformative element is required")

    start, end = float(claim.video_start), float(claim.video_end)
    notes: list[str] = []
    duration = end - start

    if duration < MIN_SECONDS:
        pad = (MIN_SECONDS - duration) / 2
        start = max(0.0, start - pad)
        end = start + MIN_SECONDS
        notes.append(f"padded to {MIN_SECONDS:.0f}s minimum")
    elif duration > max_seconds:
        if not extend_reason.strip():
            raise ClipRefused(
                f"{duration:.1f}s exceeds {max_seconds:.0f}s — give extend_reason "
                "(e.g. the full sentence is needed to keep its meaning)"
            )
        if duration > HARD_MAX_SECONDS:
            raise ClipRefused(f"{duration:.1f}s exceeds the {HARD_MAX_SECONDS:.0f}s hard cap; split the excerpt")
        notes.append(f"extended to {duration:.1f}s: {extend_reason.strip()}")

    return ClipPlan(claim.claim_id, round(start, 3), round(end, 3), elements, extend_reason.strip(), notes)


def ffmpeg_commands(plan: ClipPlan, source_file: Path) -> list[list[str]]:
    """Commands to cut the excerpt and grab a freeze-frame thumbnail."""
    return [
        [
            "ffmpeg", "-y", "-ss", f"{plan.start:.3f}", "-to", f"{plan.end:.3f}",
            "-i", str(source_file), "-c:v", "libx264", "-c:a", "aac", str(plan.clip_path),
        ],
        [
            "ffmpeg", "-y", "-ss", f"{plan.end:.3f}", "-i", str(source_file),
            "-frames:v", "1", str(plan.thumbnail_path),
        ],
    ]


def source_file_for(claim: PoliticalClaim) -> Path | None:
    if claim.download_status != DownloadStatus.DOWNLOADED.value:
        return None
    path = SOURCE_DIR / f"{claim.claim_id}.mp4"
    return path if path.exists() else None

