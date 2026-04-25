"""
Split long presenter audio into <= max_sec parts, preferring silence cut points.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, List, Optional

from . import duration, fs_utils
from .config import PipelineConfig
from .logutil import log_presenter

LOG = logging.getLogger("presenter.audio_splitter")


def _pick_splits(
    total: float, max_part: float, candidates: list[float]
) -> list[float]:
    """
    Return interior split boundaries (in seconds) so each segment <= max_part.
    candidates: possible cut points in (0, total), e.g. silence points.
    """
    if total <= max_part + 1e-6:
        return []
    cuts: list[float] = []
    start = 0.0
    cands = sorted(c for c in candidates if 0.0 < c < total)
    while total - start > max_part + 1e-3:
        lo = start + 1e-3
        hi = start + max_part
        # prefer largest cut in (lo, hi] from cands, else hard at hi
        best: Optional[float] = None
        for c in cands:
            if lo < c <= hi:
                if best is None or c > best:
                    best = c
        cut = best if best is not None else hi
        cut = min(cut, total - 1e-3)
        cut = max(cut, lo)
        if cut - start < 0.1:
            cut = min(start + max_part, total - 1e-3)
        cuts.append(round(cut, 3))
        start = cut
    return cuts


def _ffmpeg_extract_wav(
    input_path: Path, start: float, out_len: float, out_path: Path
) -> bool:
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(input_path),
        "-t",
        f"{out_len:.3f}",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        str(out_path),
    ]
    code, _o, e = duration.run_cmd(args)
    if code != 0:
        log_presenter(
            LOG, "--", "--", "ffmpeg_split", e[:2000], level=logging.ERROR
        )
        return False
    return True


def split_presenter_audio(
    audio_path: Path,
    segment_name: str,
    topic_paths: dict[str, Path],
    config: PipelineConfig,
) -> dict[str, Any]:
    """
    Split one presenter wav into parts under presenter/segments/<segment_name>/
    with audio_part_01.wav, ...
    Returns summary dict: original_audio_duration, split_count, split_points, part_paths
    """
    audio_path = Path(audio_path)
    root = topic_paths["root"]
    segs = topic_paths["segments"] / segment_name
    fs_utils.ensure_dir(segs)
    for old in segs.glob("audio_part_*.wav"):
        try:
            old.unlink()
        except OSError:
            pass
    if not audio_path.is_file():
        return {
            "original_audio_duration": None,
            "split_count": 0,
            "split_points": [],
            "part_paths": [],
            "error": "audio_missing",
        }
    d = duration.get_media_duration(audio_path)
    maxp = config.max_audio_part_sec
    if d <= maxp + 1e-3:
        dest = segs / "audio_part_01.wav"
        shutil.copy2(audio_path, dest)
        return {
            "original_audio_duration": d,
            "split_count": 1,
            "split_points": [],
            "part_paths": [fs_utils.relposix(root, dest)],
        }
    cands = duration.detect_silence_endpoints(
        audio_path,
        noise_db=config.silence_db,
        min_silence=config.min_silence_sec,
    )
    cut_pts = _pick_splits(d, maxp, cands)
    boundaries = [0.0] + cut_pts + [d]
    parts: list[Path] = []
    for k in range(len(boundaries) - 1):
        s0, s1 = boundaries[k], boundaries[k + 1]
        length = s1 - s0
        out = segs / f"audio_part_{k+1:02d}.wav"
        if not _ffmpeg_extract_wav(audio_path, s0, length, out):
            return {
                "original_audio_duration": d,
                "split_count": 0,
                "split_points": cut_pts,
                "error": "ffmpeg_extract_failed",
            }
        parts.append(out)
    return {
        "original_audio_duration": d,
        "split_count": len(parts),
        "split_points": cut_pts,
        "part_paths": [fs_utils.relposix(root, p) for p in parts],
    }
