#!/usr/bin/env python3
"""Build a theme-tagged rough cut from ``media_index.json`` (read-only on sources).

Uses SV_TRANSFER media index + premium/raw paths already indexed. Selects landscape
video only (no portrait in the same rough cut). Prefers Times Square, then Midtown
when theme mentions Times Square. Respects timelapse slice caps via ``max_segment_seconds``.

Writes only under ``--output-dir`` (default ``rough_cut.mp4``). Never deletes or overwrites sources.
"""

from __future__ import annotations

# IMPORTANT:
# Do not mux original iPhone/VFR/high-fps MOV/MP4 with -c:v copy after audio replacement.
# It can stretch 1-hour footage into multi-hour slow motion.
# Always normalize to CFR 30/60fps before final muxing (this pipeline uses normalize_media_for_timeline).

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.davinci_ffprobe import ffprobe_json, stream_summary  # noqa: E402
from utils.storage_paths import get_sv_transfer  # noqa: E402
from normalize_media_for_timeline import normalize_many, slugify  # noqa: E402

log = logging.getLogger("create_theme_rough_cut")

MEDIA_INDEX_REL = Path("media_index") / "media_index.json"
_THEME_TIMES = re.compile(
    r"times square|时代广场|timessquare|\b42nd\b|42nd\s+street|broadway",
    re.I,
)
FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"


def load_index() -> dict[str, Any] | None:
    p = get_sv_transfer(verbose=False) / MEDIA_INDEX_REL
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def item_times_square(it: dict[str, Any]) -> bool:
    if (it.get("location_name") or "") == "Times Square":
        return True
    if (it.get("nearby_landmark") or "") == "Times Square":
        return True
    s = f"{it.get('file_name', '')} {it.get('file_path', '')}".lower()
    return bool(re.search(r"times square|timessquare|\b42nd\b|broadway", s))


def item_midtown(it: dict[str, Any]) -> bool:
    return (it.get("location_name") or "") == "Midtown Manhattan"


def theme_wants_times_square(theme: str) -> bool:
    return bool(_THEME_TIMES.search(theme.strip()))


def effective_use_duration(it: dict[str, Any]) -> float:
    raw = float(it.get("duration") or 0.0)
    if raw <= 0:
        return 0.0
    if it.get("is_manual_timelapse") or it.get("require_slice_segments"):
        mx = int(it.get("max_segment_seconds") or 8)
        return min(raw, max(mx * 4, mx))
    return raw


def score_item(it: dict[str, Any], *, tier: int) -> float:
    s = float(tier * 1_000_000)
    if it.get("is_manual_timelapse"):
        s -= 5_000
    if (it.get("quality_hint") or "") == "premium":
        s += 500
    if it.get("likely_source_type") in ("driving_fixed", "walking_handheld"):
        s += 50
    s += min(120.0, effective_use_duration(it))
    return s


def is_candidate(it: dict[str, Any]) -> bool:
    if it.get("is_video") is not True and it.get("media_type") != "video":
        return False
    if (it.get("orientation") or "") != "landscape":
        return False
    fp = str(it.get("file_path") or "")
    if not fp:
        return False
    return Path(fp).suffix.lower() in {".mp4", ".mov", ".m4v"}


def pick_items(
    items: list[dict[str, Any]],
    theme: str,
    dmin: float,
    dmax: float,
) -> list[tuple[dict[str, Any], float]]:
    """Return (item, use_seconds) list, sum use_seconds in [dmin, dmax] when possible."""
    cands = [it for it in items if isinstance(it, dict) and is_candidate(it)]
    tiers: list[tuple[int, list[dict[str, Any]]]] = []
    if theme_wants_times_square(theme):
        ts = [x for x in cands if item_times_square(x)]
        md = [x for x in cands if item_midtown(x) and x not in ts]
        tiers = [(1, ts), (2, md)]
    else:
        q = theme.strip().lower()
        loc = [x for x in cands if q in str(x.get("location_name") or "").lower()]
        tiers = [(1, loc), (2, [x for x in cands if x not in loc])]

    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tier, lst in tiers:
        lst = sorted(lst, key=lambda it: score_item(it, tier=tier), reverse=True)
        for it in lst:
            k = str(it.get("file_path") or "")
            if k and k not in seen:
                seen.add(k)
                ordered.append(it)

    out: list[tuple[dict[str, Any], float]] = []
    total = 0.0
    for it in ordered:
        if total >= dmax:
            break
        eff = effective_use_duration(it)
        if eff < 3.0:
            continue
        remain = dmax - total
        if remain <= 0:
            break
        use = min(eff, remain)
        if total + use > dmax:
            use = max(0.0, dmax - total)
        if use < 3.0:
            continue
        out.append((it, use))
        total += use
        if total >= dmin:
            break

    if total < dmin and out:
        log.warning("only_reached %.1fs of requested min %.1fs", total, dmin)
    return out


def run_ffmpeg_concat(
    segments: list[tuple[Path, float]],
    outp: Path,
    *,
    timeout: float,
) -> tuple[bool, str]:
    """Concat using concat demuxer over pre-trimmed CFR30 segments (never -c copy)."""
    if not segments:
        return False, "no_segments"
    outp.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = outp.parent / "_tmp_segments"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    def _trim_one(i: int, src: Path, dur: float) -> tuple[Path | None, str]:
        seg_out = tmp_dir / f"seg_{i:04d}.mp4"
        tmp = seg_out.with_suffix(".tmp.mp4")

        data, _ = ffprobe_json(src)
        hv = ha = False
        if data:
            hv, ha, _ = stream_summary(data)
        if not hv:
            return None, "no_video_stream"

        # If clip has no audio, add silent stereo so concat stays consistent.
        if ha:
            cmd = [
                FFMPEG,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                str(src),
                "-t",
                str(dur),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                "fps=30,format=yuv420p",
                "-r",
                "30",
                "-fps_mode",
                "cfr",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(tmp),
            ]
        else:
            cmd = [
                FFMPEG,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                str(src),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t",
                str(dur),
                "-shortest",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-vf",
                "fps=30,format=yuv420p",
                "-r",
                "30",
                "-fps_mode",
                "cfr",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(tmp),
            ]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except FileNotFoundError:
            return None, "ffmpeg_missing"
        except subprocess.TimeoutExpired:
            return None, "ffmpeg_timeout"
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 1024:
            return None, (r.stderr or r.stdout or "")[-4000:] or "trim_failed"
        try:
            tmp.replace(seg_out)
        except OSError as exc:
            return None, repr(exc)
        return seg_out, ""

    seg_files: list[Path] = []
    for i, (src, dur) in enumerate(segments):
        segp, err = _trim_one(i, src, dur)
        if segp is None:
            return False, f"trim_failed[{i}]:{err}"
        seg_files.append(segp)

    concat_list = tmp_dir / f"concat_list_{os.getpid()}.txt"
    try:
        def _concat_escape(pp: Path) -> str:
            # concat demuxer list uses single-quoted paths; escape single quotes safely.
            return pp.as_posix().replace("'", "'\\''")

        lines = ["file '" + _concat_escape(p) + "'" for p in seg_files]
        concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        return False, f"concat_list_write:{exc}"

    tmp_out = outp.parent / f"{outp.stem}._rough_tmp_{os.getpid()}{outp.suffix}"
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-r",
        "30",
        "-fps_mode",
        "cfr",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(tmp_out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return False, "ffmpeg_missing"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg_timeout"
    if r.returncode != 0 or not tmp_out.is_file() or tmp_out.stat().st_size < 1024:
        return False, (r.stderr or r.stdout or "")[-4000:]
    try:
        tmp_out.replace(outp)
    except OSError as exc:
        return False, repr(exc)
    return True, ""


def verify_output(
    outp: Path,
    *,
    expected_duration: float,
    require_video: bool = True,
) -> tuple[bool, dict[str, object]]:
    data, err = ffprobe_json(outp)
    rep: dict[str, object] = {"path": str(outp), "ok": False, "error": err or ""}
    if data is None:
        rep["ok"] = False
        return False, rep
    hv, ha, dur = stream_summary(data)
    rep["has_video"] = hv
    rep["has_audio"] = ha
    rep["duration"] = dur
    fps = 0.0
    vcodec = ""
    pix = ""
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video":
            vcodec = str(s.get("codec_name") or "")
            pix = str(s.get("pix_fmt") or "")
            afr = str(s.get("avg_frame_rate") or "")
            rfr = str(s.get("r_frame_rate") or "")
            try:
                fps = float(afr.split("/")[0]) / float(afr.split("/")[1]) if "/" in afr else float(afr or 0.0)
            except Exception:
                fps = 0.0
            if fps <= 0.0:
                try:
                    fps = float(rfr.split("/")[0]) / float(rfr.split("/")[1]) if "/" in rfr else float(rfr or 0.0)
                except Exception:
                    fps = 0.0
            break
    rep["video_codec"] = vcodec
    rep["pix_fmt"] = pix
    rep["fps"] = fps

    if require_video and not hv:
        rep["ok"] = False
        rep["error"] = "no_video_stream"
        return False, rep
    if dur <= 0.5:
        rep["ok"] = False
        rep["error"] = "duration_too_short"
        return False, rep
    # Reasonable duration vs planned sum (allow some drift).
    if expected_duration > 1.0:
        if not (0.85 * expected_duration <= dur <= 1.25 * expected_duration):
            rep["ok"] = False
            rep["error"] = f"duration_unreasonable expected~{expected_duration:.3f}"
            return False, rep
    if not (29.0 <= float(fps) <= 31.0):
        rep["ok"] = False
        rep["error"] = f"fps_not_30 got={fps}"
        return False, rep
    rep["ok"] = True
    return True, rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--theme", required=True, help='e.g. "Times Square"')
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--duration-min", type=float, default=120.0)
    ap.add_argument("--duration-max", type=float, default=300.0)
    ap.add_argument("--output-name", default="rough_cut.mp4")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    data = load_index()
    if not data or not isinstance(data.get("items"), list):
        log.error("media_index missing or empty; run index_media_library.py first")
        return 2

    items = [x for x in data["items"] if isinstance(x, dict)]
    picked = pick_items(items, args.theme, args.duration_min, args.duration_max)
    if not picked:
        log.error("no_landscape_clips_for_theme theme=%r", args.theme)
        return 3

    out_dir = args.output_dir.expanduser()
    out_path = out_dir / args.output_name
    if args.dry_run:
        log.info("[dry-run] would write %s segments=%s", out_path, len(picked))
        for it, u in picked[:20]:
            log.info("  %.1fs  %s", u, it.get("file_path"))
        if len(picked) > 20:
            log.info("  ... +%s more", len(picked) - 20)
        return 0

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("mkdir %s: %s", out_dir, exc)
        return 4

    segs: list[tuple[Path, float]] = []
    for it, use in picked:
        p = Path(str(it.get("file_path") or ""))
        if not p.is_file():
            log.warning("skip_missing %s", p)
            continue
        data_p, _ = ffprobe_json(p)
        if not data_p:
            log.warning("skip_ffprobe %s", p)
            continue
        hv, ha, d_real = stream_summary(data_p)
        if not hv:
            log.warning("skip_no_video %s", p)
            continue
        if not ha:
            log.warning("no_audio (will_add_silence) %s", p)
        use_adj = min(float(use), float(d_real)) if d_real > 0.5 else float(use)
        if use_adj < 3.0:
            continue
        segs.append((p, use_adj))

    if not segs:
        log.error("no_valid_segments_after_probe")
        return 5

    # Normalize first; rough_cut must ONLY use normalized clips.
    project_slug = slugify(out_dir.name or args.theme)
    src_paths = [p for p, _u in segs]
    norm_dir, _norm_paths, norm_manifest = normalize_many(
        src_paths,
        project_slug=project_slug,
        expect_orientation="landscape",
        allow_portrait=False,
        timeout_sec=7200.0,
        verbose=bool(args.verbose),
    )
    norm_map: dict[str, Path] = {}
    for ent in norm_manifest:
        if not isinstance(ent, dict):
            continue
        if str(ent.get("status") or "").startswith("ok") and ent.get("source_path") and ent.get("normalized_path"):
            norm_map[str(ent["source_path"])] = Path(str(ent["normalized_path"]))

    norm_segs: list[tuple[Path, float]] = []
    for src, dur in segs:
        try:
            k = str(src.expanduser().resolve())
        except OSError:
            k = str(src)
        np = norm_map.get(k)
        if np is None or not np.is_file():
            log.warning("skip_no_normalized %s (normalized_dir=%s)", src, norm_dir)
            continue
        norm_segs.append((np, dur))

    if not norm_segs:
        log.error("no_segments_after_normalize")
        return 5

    ok, err = run_ffmpeg_concat(norm_segs, out_path, timeout=7200.0)
    if not ok:
        log.error("ffmpeg_failed %s", err[:2000] if err else "")
        return 6

    expected_total = float(sum(u for _p, u in norm_segs))
    v_ok, v_rep = verify_output(out_path, expected_duration=expected_total, require_video=True)
    if not v_ok:
        log.error("rough_cut_verify_failed %s", v_rep.get("error") or "")
        try:
            (out_dir / "rough_cut_verify.json").write_text(json.dumps(v_rep, indent=2), encoding="utf-8")
        except OSError:
            pass
        return 7
    try:
        (out_dir / "rough_cut_verify.json").write_text(json.dumps(v_rep, indent=2), encoding="utf-8")
    except OSError:
        pass

    meta = {
        "theme": args.theme,
        "output": str(out_path),
        "segment_count": len(norm_segs),
        "duration_min": args.duration_min,
        "duration_max": args.duration_max,
        "normalized_dir": str(norm_dir),
        "sources": [{"path": str(p), "seconds": u} for p, u in segs],
        "normalized_sources": [{"path": str(p), "seconds": u} for p, u in norm_segs],
    }
    try:
        (out_dir / "rough_cut_manifest.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("manifest_write_failed %s", exc)

    log.info("wrote %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
