"""
Build base presenter video from 5s/10s clips: **masters first**, then optional
``presenter_generated/{intro,insert,outro}`` b-roll, matched to (audio + buffer) length.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

from . import duration, fs_utils
from .config import PipelineConfig
from .logutil import log_presenter

LOG = logging.getLogger("presenter.builder")


def segment_type_to_generated_bucket(segment_type: str) -> str:
    """``intro|insert|outro`` timeline types map to subfolders under ``presenter_generated/``."""
    t = (segment_type or "").lower()
    if t == "outro":
        return "outro"
    if t == "intro":
        return "intro"
    return "insert"


def _classify_5vs10_sec(path: Path) -> float:
    d = duration.ffprobe_duration(path)
    if d <= 6.75:
        return 5.0
    return 10.0


def _list_masters(assets: Path) -> tuple[list[Path], list[Path]]:
    d5 = assets / "5s"
    d10 = assets / "10s"
    p5 = sorted(p for p in d5.glob("*.mp4") if p.is_file()) if d5.is_dir() else []
    p10 = sorted(p for p in d10.glob("*.mp4") if p.is_file()) if d10.is_dir() else []
    return p5, p10


def _list_generated_pool(config: PipelineConfig, bucket: str) -> tuple[list[Path], list[Path]]:
    gdir = config.assets_presenter_generated() / bucket
    p5, p10 = [], []
    if not gdir.is_dir():
        return p5, p10
    for f in sorted(gdir.glob("*.mp4")):
        if not f.is_file():
            continue
        if _classify_5vs10_sec(f) >= 7.5:
            p10.append(f)
        else:
            p5.append(f)
    return p5, p10


def _merged_pools(
    config: PipelineConfig, segment_type: str
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """
    Return (master5, master10, gen5, gen10). Selection prefers **all masters**,
    then **generated** for the segment bucket, so masters stay primary.
    """
    m5, m10 = _list_masters(config.assets_masters())
    buck = segment_type_to_generated_bucket(segment_type)
    g5, g10 = _list_generated_pool(config, buck)
    return m5, m10, g5, g10


def _pool_pair_for_segment(
    config: PipelineConfig, segment_type: str
) -> tuple[list[Path], list[Path]]:
    m5, m10, g5, g10 = _merged_pools(config, segment_type)
    return m5 + g5, m10 + g10


def _need_chunk_lengths(target_sec: float) -> list[float]:
    t = max(0.01, float(target_sec))
    parts: list[float] = []
    s = 0.0
    while s < t - 1e-6:
        if s + 10.0 >= t - 1e-6:
            parts.append(10.0)
            break
        if s + 5.0 >= t - 1e-6:
            parts.append(5.0)
            break
        if s + 10.0 <= t:
            parts.append(10.0)
            s += 10.0
        else:
            parts.append(5.0)
            s += 5.0
    return parts if parts else [5.0]


def _esc_concat_path(p: Path) -> str:
    s = p.resolve().as_posix()
    return s.replace("'", r"'\''")


def _normalize_segment(
    src: Path, out_path: Path, config: PipelineConfig
) -> bool:
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        f"scale={config.target_width}:{config.target_height}:force_original_aspect_ratio=decrease,"
        f"pad={config.target_width}:{config.target_height}:(ow-iw)/2:(oh-ih)/2,fps={config.target_fps}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-b:a",
        "192k",
        str(out_path),
    ]
    code, _o, e = duration.run_cmd(args)
    if code != 0:
        log_presenter(
            LOG, "--", "--", "ffmpeg_norm_master", e[:2000], level=logging.ERROR
        )
        return False
    return True


def _pick_pool_sequence(
    need: Sequence[float], pool5: list[Path], pool10: list[Path]
) -> list[tuple[Path, float, str, str]]:
    """
    (path, nominal_len, size_tag, source_tag). ``source_tag`` is ``master`` or ``generated``;
    list order in ``pool5``/``pool10`` must place **masters** before **generated** candidates.
    """
    if not need:
        return []
    n5, n10 = 0, 0
    last: Optional[Path] = None
    out: list[tuple[Path, float, str, str]] = []
    for L in need:
        use_10 = L >= 7.5
        pool, tag = (pool10, "10s") if use_10 and pool10 else (pool5, "5s")
        if not pool:
            pool, tag = (pool5, "5s") if pool5 else (pool10, "10s")
        if not pool:
            raise RuntimeError(
                "No 5s/10s .mp4 in assets/presenter_masters/ or assets/presenter_generated/"
            )
        start = n10 if tag == "10s" else n5
        p: Optional[Path] = None
        for j in range(len(pool)):
            cand = pool[(start + j) % len(pool)]
            if last is not None and cand == last and len(pool) > 1:
                continue
            p = cand
            break
        if p is None:
            p = pool[start % len(pool)]
        st = "generated" if "presenter_generated" in p.as_posix() else "master"
        if tag == "10s":
            n10 += 1
        else:
            n5 += 1
        out.append((p, L, tag, st))
        last = p
    return out


def build_base_video_for_audio_part(
    target_duration_sec: float,
    out_mp4: Path,
    config: PipelineConfig,
    _root: Path,
    segment_type: str = "insert",
) -> dict[str, Any]:
    """
    Build one base ``.mp4`` of exact ``target_duration_sec`` (trim) using **master** pool
    first (same folder merge order) plus ``presenter_generated`` for
    :func:`segment_type_to_generated_bucket` (``insert``/``intro``/``outro``).
    """
    out_mp4 = Path(out_mp4)
    fs_utils.ensure_dir(out_mp4.parent)
    p5, p10 = _pool_pair_for_segment(config, segment_type)
    if not p5 and not p10:
        return {
            "ok": False,
            "error": "no clips in presenter_masters or presenter_generated for this slot",
            "target_duration_sec": target_duration_sec,
        }
    need = _need_chunk_lengths(target_duration_sec)
    try:
        choices = _pick_pool_sequence(need, p5, p10)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(e),
            "target_duration_sec": target_duration_sec,
        }
    selected_files: list[str] = []
    plan: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        norm_paths: list[Path] = []
        for i, (src, nom, size_tag, src_tag) in enumerate(choices):
            npath = tdir / f"norm_{i:03d}.mp4"
            if not _normalize_segment(Path(src), npath, config):
                return {
                    "ok": False,
                    "error": "normalize_failed",
                    "at": str(src),
                }
            selected_files.append(src.as_posix())
            plan.append(
                {
                    "file": src.as_posix(),
                    "nominal_sec": nom,
                    "size": size_tag,
                    "source": src_tag,
                }
            )
            norm_paths.append(npath)
        concat_list = tdir / "list.txt"
        with concat_list.open("w", encoding="utf-8") as f:
            for np in norm_paths:
                f.write("file '%s'\n" % _esc_concat_path(np))
        raw_cat = tdir / "raw_concat.mp4"
        cargs = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(raw_cat),
        ]
        code, _o, e = duration.run_cmd(cargs)
        if code != 0:
            reencode = tdir / "reencoded.mp4"
            cargs2 = [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                str(reencode),
            ]
            c2, _2, e2 = duration.run_cmd(cargs2)
            if c2 != 0:
                return {
                    "ok": False,
                    "error": f"concat_failed: {e!s} | {e2!s}",
                }
            src_for_trim = reencode
        else:
            src_for_trim = raw_cat
        fargs = [
            "ffmpeg",
            "-y",
            "-i",
            str(src_for_trim),
            "-t",
            f"{float(target_duration_sec):.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]
        c3, _3, e3 = duration.run_cmd(fargs)
        if c3 != 0:
            return {"ok": False, "error": f"final_trim: {e3!s}"}
    return {
        "ok": True,
        "target_duration_sec": target_duration_sec,
        "segment_type": segment_type,
        "selected_master_files": selected_files,
        "selected_plan": plan,
        "out_path": out_mp4.as_posix(),
    }
