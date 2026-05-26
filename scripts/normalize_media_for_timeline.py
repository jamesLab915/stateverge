#!/usr/bin/env python3
"""Normalize source media into timeline-safe CFR clips (fail-open).

Inputs:
- single video file path
- a timeline_plan.json path (best-effort: extract file paths from common keys)
- a project directory (best-effort: look for timeline_plan.json or rough_cut_manifest.json)

Outputs:
  /Volumes/SV_CACHE/normalized/<project_slug>/
    - normalized mp4 clips
    - normalize_manifest.json

Rules:
- CFR 30fps
- H.264 mp4 + yuv420p
- AAC audio (if source has audio; mapping uses -map 0:a? so optional)
- Keep aspect ratio (no stretch); do not rotate or reframe
- Orientation filtering supported via --expect-orientation
"""

from __future__ import annotations

# IMPORTANT:
# Do not mux original iPhone/VFR/high-fps MOV/MP4 with -c:v copy after audio replacement.
# It can stretch 1-hour footage into multi-hour slow motion.
# Always normalize to CFR 30/60fps before final muxing.

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.davinci_ffprobe import ffprobe_json, stream_summary  # noqa: E402
from utils.storage_paths import get_sv_cache  # noqa: E402

log = logging.getLogger("normalize_media_for_timeline")

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".hevc"}


def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "project"


def _short_id(path: Path) -> str:
    try:
        rp = str(path.expanduser().resolve())
    except OSError:
        rp = str(path)
    h = hashlib.sha1(rp.encode("utf-8", errors="ignore")).hexdigest()
    return h[:10]


def _float_or_zero(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _parse_rate(rate: str) -> float:
    s = (rate or "").strip()
    if not s or s == "0/0":
        return 0.0
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            return float(a) / float(b)
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def probe_basic(path: Path) -> dict[str, Any] | None:
    data, err = ffprobe_json(path)
    if data is None:
        return None
    hv, ha, dur = stream_summary(data)
    info: dict[str, Any] = {
        "has_video": hv,
        "has_audio": ha,
        "duration": float(dur),
        "video": {},
        "audio": {},
    }
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not info["video"]:
            info["video"] = {
                "codec_name": s.get("codec_name") or "",
                "pix_fmt": s.get("pix_fmt") or "",
                "width": int(s.get("width") or 0),
                "height": int(s.get("height") or 0),
                "avg_frame_rate": s.get("avg_frame_rate") or "",
                "r_frame_rate": s.get("r_frame_rate") or "",
            }
        if s.get("codec_type") == "audio" and not info["audio"]:
            info["audio"] = {
                "codec_name": s.get("codec_name") or "",
                "sample_rate": s.get("sample_rate") or "",
                "channels": s.get("channels") or "",
            }
    return info


def orientation_of(probe: dict[str, Any]) -> str:
    v = probe.get("video") or {}
    try:
        w = int(v.get("width") or 0)
        h = int(v.get("height") or 0)
    except (TypeError, ValueError):
        return "unknown"
    if w <= 0 or h <= 0:
        return "unknown"
    if w > h:
        return "landscape"
    if h > w:
        return "portrait"
    return "square"


def normalized_ok(path: Path) -> tuple[bool, dict[str, Any] | None]:
    p = probe_basic(path)
    if not p or not p.get("has_video"):
        return False, p
    v = p.get("video") or {}
    if (v.get("codec_name") or "").lower() != "h264":
        return False, p
    if (v.get("pix_fmt") or "").lower() != "yuv420p":
        return False, p
    fps = _parse_rate(str(v.get("avg_frame_rate") or "")) or _parse_rate(str(v.get("r_frame_rate") or ""))
    if not (29.0 <= fps <= 31.0):
        return False, p
    if p.get("has_audio"):
        a = p.get("audio") or {}
        if (a.get("codec_name") or "").lower() != "aac":
            return False, p
    if float(p.get("duration") or 0.0) <= 0.5:
        return False, p
    return True, p


def normalized_output_path(out_dir: Path, src: Path) -> Path:
    sid = _short_id(src)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", src.stem).strip("._-") or "clip"
    return out_dir / f"{stem}__cfr30__{sid}.mp4"


def ensure_dir(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("mkdir failed %s: %s", p, exc)


def load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return [x for x in data["items"] if isinstance(x, dict)]
    except Exception:
        return []
    return []


def write_manifest(path: Path, items: list[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("manifest write failed %s: %s", path, exc)


def _extract_paths_from_json(obj: Any) -> set[str]:
    """Best-effort: walk JSON and collect strings that look like video file paths."""
    out: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, str):
                    sv = v.strip()
                    if sv and any(sv.lower().endswith(ext) for ext in VIDEO_EXTS):
                        out.add(sv)
                else:
                    walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)
        elif isinstance(x, str):
            sv = x.strip()
            if sv and any(sv.lower().endswith(ext) for ext in VIDEO_EXTS):
                out.add(sv)

    walk(obj)
    return out


def collect_sources(input_path: Path) -> list[Path]:
    p = input_path.expanduser()
    if p.is_file():
        if p.name.lower().endswith(".json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                return [p]
            found = sorted(_extract_paths_from_json(data))
            return [Path(x).expanduser() for x in found] or [p]
        return [p]

    if p.is_dir():
        # Prefer explicit timeline_plan.json if present.
        plan = p / "timeline_plan.json"
        if plan.is_file():
            return collect_sources(plan)
        # Also accept rough_cut_manifest.json (created by create_theme_rough_cut.py).
        rcm = p / "rough_cut_manifest.json"
        if rcm.is_file():
            try:
                data = json.loads(rcm.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                data = {}
            found: list[Path] = []
            for ent in (data.get("sources") or []):
                if isinstance(ent, dict) and ent.get("path"):
                    found.append(Path(str(ent["path"])).expanduser())
            if found:
                return found
        # Fallback: scan files directly (shallow-ish).
        found_files: list[Path] = []
        try:
            for pp in p.rglob("*"):
                if not pp.is_file():
                    continue
                if pp.suffix.lower() in VIDEO_EXTS:
                    found_files.append(pp)
        except OSError:
            pass
        return found_files

    return []


def normalize_one(
    src: Path,
    out_dir: Path,
    *,
    expect_orientation: str,
    allow_portrait: bool,
    timeout_sec: float,
    verbose: bool,
) -> tuple[Path | None, dict[str, Any]]:
    entry: dict[str, Any] = {
        "source_path": str(src),
        "normalized_path": "",
        "source_duration": 0.0,
        "normalized_duration": 0.0,
        "source_fps": 0.0,
        "normalized_fps": 30.0,
        "status": "unknown",
        "error": "",
    }

    try:
        if not src.is_file():
            entry["status"] = "missing"
            return None, entry
    except OSError as exc:
        entry["status"] = "missing"
        entry["error"] = f"is_file:{exc}"
        return None, entry

    sp = probe_basic(src)
    if not sp or not sp.get("has_video"):
        entry["status"] = "skip_no_video"
        entry["error"] = "ffprobe_failed_or_no_video"
        return None, entry

    entry["source_duration"] = float(sp.get("duration") or 0.0)
    v = sp.get("video") or {}
    fps_src = _parse_rate(str(v.get("avg_frame_rate") or "")) or _parse_rate(str(v.get("r_frame_rate") or ""))
    entry["source_fps"] = float(fps_src)

    ori = orientation_of(sp)
    if expect_orientation in ("landscape", "portrait") and ori not in ("unknown", "square"):
        if ori != expect_orientation and not (allow_portrait and ori == "portrait"):
            entry["status"] = "skipped_orientation"
            entry["error"] = f"orientation={ori}"
            return None, entry

    ensure_dir(out_dir)
    outp = normalized_output_path(out_dir, src)
    entry["normalized_path"] = str(outp)

    # Skip if exists and looks correct.
    if outp.is_file():
        ok, _ = normalized_ok(outp)
        if ok:
            entry["status"] = "ok_cached"
            np = probe_basic(outp) or {}
            entry["normalized_duration"] = float(np.get("duration") or 0.0)
            return outp, entry

    tmp = outp.with_suffix(".tmp.mp4")
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(src),
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
    if verbose:
        log.info("ffmpeg %s", " ".join(cmd))

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
    except FileNotFoundError:
        entry["status"] = "fail"
        entry["error"] = "ffmpeg_missing"
        return None, entry
    except subprocess.TimeoutExpired:
        entry["status"] = "fail"
        entry["error"] = "ffmpeg_timeout"
        return None, entry

    if r.returncode != 0 or not tmp.is_file():
        entry["status"] = "fail"
        entry["error"] = (r.stderr or r.stdout or "ffmpeg_failed").strip()[-4000:]
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None, entry

    try:
        tmp.replace(outp)
    except OSError as exc:
        entry["status"] = "fail"
        entry["error"] = f"rename:{exc}"
        return None, entry

    ok, np = normalized_ok(outp)
    if not ok:
        entry["status"] = "fail_probe"
        entry["error"] = "normalized_not_ok_after_encode"
        return None, entry

    entry["status"] = "ok"
    entry["normalized_duration"] = _float_or_zero((np or {}).get("duration"))
    return outp, entry


def normalize_many(
    sources: Iterable[Path],
    *,
    project_slug: str,
    expect_orientation: str,
    allow_portrait: bool,
    timeout_sec: float,
    verbose: bool,
) -> tuple[Path, list[Path], list[dict[str, Any]]]:
    out_dir = get_sv_cache(verbose=verbose) / "normalized" / slugify(project_slug)
    manifest_path = out_dir / "normalize_manifest.json"
    existing_items = load_manifest(manifest_path)
    items: list[dict[str, Any]] = []
    normalized_paths: list[Path] = []

    # We write a fresh manifest each run (keeps last-known status per source).
    for src in sources:
        try:
            sp = src.expanduser().resolve()
        except OSError:
            sp = src.expanduser()
        outp, ent = normalize_one(
            sp,
            out_dir,
            expect_orientation=expect_orientation,
            allow_portrait=allow_portrait,
            timeout_sec=timeout_sec,
            verbose=verbose,
        )
        items.append(ent)
        if outp is not None:
            normalized_paths.append(outp)

    # De-dupe by source_path: keep latest result.
    merged: dict[str, dict[str, Any]] = {}
    for ent in existing_items:
        k = str(ent.get("source_path") or "")
        if k:
            merged[k] = ent
    for ent in items:
        k = str(ent.get("source_path") or "")
        if k:
            merged[k] = ent
    write_manifest(manifest_path, list(merged.values()))
    return out_dir, normalized_paths, list(merged.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="Video file, timeline_plan.json, or project directory")
    ap.add_argument("--project-slug", default="", help="Output bucket slug (default: derived from input)")
    ap.add_argument(
        "--expect-orientation",
        default="any",
        choices=["any", "landscape", "portrait"],
        help="Filter by orientation (create_theme_rough_cut uses landscape).",
    )
    ap.add_argument(
        "--allow-portrait",
        action="store_true",
        help="Allow portrait even when expecting landscape (only for explicit needs_reframe cases).",
    )
    ap.add_argument("--timeout-sec", type=float, default=7200.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    inp = args.input.expanduser()
    project_slug = args.project_slug.strip()
    if not project_slug:
        project_slug = inp.stem if inp.is_file() else inp.name

    sources = collect_sources(inp)
    if not sources:
        log.error("no_sources_found input=%s", inp)
        return 2

    out_dir, normalized, manifest_items = normalize_many(
        sources,
        project_slug=project_slug,
        expect_orientation=args.expect_orientation,
        allow_portrait=bool(args.allow_portrait),
        timeout_sec=float(args.timeout_sec),
        verbose=args.verbose,
    )
    ok_n = sum(1 for it in manifest_items if str(it.get("status") or "").startswith("ok"))
    fail_n = sum(1 for it in manifest_items if str(it.get("status") or "").startswith("fail"))
    skip_n = sum(1 for it in manifest_items if str(it.get("status") or "").startswith("skip"))
    log.info("normalized_out=%s ok=%s fail=%s skip=%s total=%s", out_dir, ok_n, fail_n, skip_n, len(manifest_items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

