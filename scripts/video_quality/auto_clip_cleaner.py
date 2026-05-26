#!/usr/bin/env python3
"""StateVerge Auto Clip Cleaner v1 — detect bad shots, build cut list, preview/apply (never touches source)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1]
_REPO = _SCRIPTS.parent
for _p in (_SCRIPTS, _REPO / "src", _REPO / "scripts"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from storage_paths import get_sv_cache  # noqa: E402
except Exception:  # noqa: BLE001
    try:
        from utils.storage_paths import get_sv_cache  # type: ignore[attr-defined]  # noqa: E402
    except Exception:

        def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
            return Path("/Volumes/SV_CACHE")

from video_quality.clip_cleaner.cut_merge import (  # noqa: E402
    cuts_to_keep_segments,
    merge_cut_ranges,
    parse_cut_spec,
)
from video_quality.clip_cleaner.detectors import run_auto_detect  # noqa: E402

_HOMEBREW_FFMPEG = Path("/opt/homebrew/bin/ffmpeg")
_HOMEBREW_FFPROBE = Path("/opt/homebrew/bin/ffprobe")

VIDEO_VF_CFR = "fps=30,format=yuv420p"
PREVIEW_MAX_SEC = float(os.environ.get("AUTO_CLIP_CLEANER_PREVIEW_MAX_SEC", "600"))
ENCODE_TIMEOUT_SEC = float(os.environ.get("AUTO_CLIP_CLEANER_ENCODE_TIMEOUT_SEC", str(6 * 3600)))


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_ffmpeg() -> str:
    env = (os.environ.get("FFMPEG_BIN") or "").strip()
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    if _HOMEBREW_FFMPEG.is_file():
        return str(_HOMEBREW_FFMPEG)
    raise FileNotFoundError("ffmpeg not found")


def _resolve_ffprobe() -> str:
    env = (os.environ.get("FFPROBE_BIN") or "").strip()
    if env:
        return env
    found = shutil.which("ffprobe")
    if found:
        return found
    if _HOMEBREW_FFPROBE.is_file():
        return str(_HOMEBREW_FFPROBE)
    raise FileNotFoundError("ffprobe not found")


def _write_json(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _ffprobe_json(path: Path, *, ffprobe: str) -> dict[str, Any] | None:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "{}")
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
        return None


def _probe_media(path: Path, *, ffprobe: str) -> dict[str, Any]:
    pr = _ffprobe_json(path, ffprobe=ffprobe) or {}
    out: dict[str, Any] = {
        "duration_sec": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "has_video": False,
        "has_audio": False,
        "video_codec": "",
        "audio_codec": "",
    }
    try:
        out["duration_sec"] = float((pr.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        out["duration_sec"] = 0.0
    for st in pr.get("streams") or []:
        if not isinstance(st, dict):
            continue
        ctype = (st.get("codec_type") or "").lower()
        if ctype == "video":
            out["has_video"] = True
            if not out["video_codec"]:
                out["video_codec"] = str(st.get("codec_name") or "")
            if not out["width"]:
                out["width"] = int(st.get("width") or 0)
                out["height"] = int(st.get("height") or 0)
            afr = str(st.get("avg_frame_rate") or "0/0")
            try:
                num, den = afr.split("/")
                fd = float(den) if float(den) != 0 else 1.0
                out["fps"] = round(float(num) / fd, 3)
            except (ValueError, ZeroDivisionError):
                pass
        elif ctype == "audio":
            out["has_audio"] = True
            out["audio_codec"] = str(st.get("codec_name") or "")
    return out


def _job_roots(*, basename: str, stamp: str) -> tuple[Path, Path]:
    job_name = f"{basename}_{stamp}"
    review = get_sv_cache(verbose=False) / "review_reports" / "clip_cleaner" / job_name
    renders = get_sv_cache(verbose=False) / "renders" / "clip_cleaner" / job_name
    review.mkdir(parents=True, exist_ok=True)
    renders.mkdir(parents=True, exist_ok=True)
    return review, renders


def _manual_cuts(specs: list[str]) -> list[dict[str, Any]]:
    cuts: list[dict[str, Any]] = []
    for spec in specs:
        start, end = parse_cut_spec(spec)
        cuts.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "reason": "manual_cut",
                "source": "manual",
                "score": 1.0,
            }
        )
    return cuts


def _render_concat(
    inp: Path,
    keep_segments: list[dict[str, float]],
    outp: Path,
    *,
    ffmpeg: str,
    full_encode: bool,
    max_output_sec: float | None,
) -> tuple[bool, str]:
    if not keep_segments:
        return False, "no_keep_segments"

    lst = outp.with_suffix(".concat.txt")
    lines: list[str] = []
    total = 0.0
    for seg in keep_segments:
        start = float(seg["start_sec"])
        end = float(seg["end_sec"])
        if max_output_sec is not None and total >= max_output_sec:
            break
        if max_output_sec is not None and total + (end - start) > max_output_sec:
            end = start + max(0.05, max_output_sec - total)
        if end <= start:
            continue
        total += end - start
        lines.append(f"file '{inp.as_posix()}'")
        lines.append(f"inpoint {start:.3f}")
        lines.append(f"outpoint {end:.3f}")

    if not lines:
        return False, "empty_concat_list"

    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    outp.parent.mkdir(parents=True, exist_ok=True)

    if full_encode:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-vf",
            VIDEO_VF_CFR,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(outp),
        ]
    else:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-vf",
            VIDEO_VF_CFR,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(outp),
        ]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=ENCODE_TIMEOUT_SEC, check=False)
    except subprocess.TimeoutExpired:
        try:
            lst.unlink(missing_ok=True)
        except OSError:
            pass
        return False, "ffmpeg_timeout"
    except OSError as exc:
        try:
            lst.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"ffmpeg_spawn_error:{exc!r}"

    try:
        lst.unlink(missing_ok=True)
    except OSError:
        pass

    ok = r.returncode == 0 and outp.is_file() and outp.stat().st_size > 4096
    tail = (r.stderr or "")[-6000:]
    return ok, tail if not ok else ""


def run_job(
    *,
    inp: Path,
    auto_detect: bool,
    manual_specs: list[str],
    preview_only: bool,
    apply: bool,
    job_id: str,
) -> dict[str, Any]:
    ffmpeg = _resolve_ffmpeg()
    ffprobe = _resolve_ffprobe()

    inp = inp.expanduser().resolve()
    stamp = _utc_stamp()
    basename = inp.stem or "input"
    jid = (job_id or "").strip() or uuid.uuid4().hex[:10]
    review_dir, render_dir = _job_roots(basename=basename, stamp=stamp)

    report: dict[str, Any] = {
        "version": "auto_clip_cleaner_v1",
        "status": "running",
        "job_id": jid,
        "input_path": str(inp),
        "input_mtime": inp.stat().st_mtime if inp.is_file() else None,
        "source_never_modified": True,
        "created_at": _utc_iso(),
        "review_dir": str(review_dir),
        "render_dir": str(render_dir),
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "auto_detect": bool(auto_detect),
        "preview_only": bool(preview_only),
        "apply": bool(apply),
        "block_reason": "",
        "warnings": [],
        "issues": [],
        "cuts_raw": [],
        "cuts_merged": [],
        "keep_segments": [],
    }

    bad_report_path = review_dir / "bad_shot_report.json"
    cut_list_path = review_dir / "cut_list.json"
    preview_path = render_dir / "preview_clean.mp4"
    clean_path = render_dir / "clean_full.mp4"

    def _fail(reason: str, *, extra_warnings: list[str] | None = None) -> dict[str, Any]:
        report["status"] = "blocked"
        report["block_reason"] = reason
        if extra_warnings:
            report["warnings"].extend(extra_warnings)
        _write_json(bad_report_path, report)
        _write_json(
            cut_list_path,
            {
                "version": "auto_clip_cleaner_v1",
                "status": "blocked",
                "block_reason": reason,
                "cuts": [],
                "job_id": jid,
                "input_path": str(inp),
            },
        )
        return report

    if not inp.is_file():
        return _fail("input_missing")
    if inp.resolve() == preview_path.resolve() or inp.resolve() == clean_path.resolve():
        return _fail("input_is_output_path")

    try:
        probe = _probe_media(inp, ffprobe=ffprobe)
    except OSError as exc:
        return _fail("ffprobe_failed", extra_warnings=[repr(exc)])

    report["probe"] = probe
    duration = float(probe.get("duration_sec") or 0.0)
    if not probe.get("has_video"):
        return _fail("input_no_video_stream")
    if duration <= 0:
        return _fail("invalid_duration")

    cuts: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    if auto_detect:
        try:
            det_issues, det_cuts = run_auto_detect(
                inp,
                ffmpeg=ffmpeg,
                duration_sec=duration,
                has_audio=bool(probe.get("has_audio")),
            )
            issues.extend(det_issues)
            cuts.extend(det_cuts)
        except Exception as exc:  # noqa: BLE001
            report["warnings"].append(f"auto_detect_error:{exc!r}")

    if manual_specs:
        try:
            cuts.extend(_manual_cuts(manual_specs))
        except ValueError as exc:
            return _fail("invalid_manual_cut", extra_warnings=[str(exc)])

    merged = merge_cut_ranges(cuts)
    keep = cuts_to_keep_segments(merged, duration_sec=duration)

    report["issues"] = issues
    report["cuts_raw"] = cuts
    report["cuts_merged"] = merged
    report["keep_segments"] = keep
    report["status"] = "analyzed"

    bad_body = {
        "version": "auto_clip_cleaner_v1",
        "status": "ok",
        "job_id": jid,
        "input_path": str(inp),
        "duration_sec": duration,
        "issue_count": len(issues),
        "issues": issues,
        "created_at": _utc_iso(),
    }
    cut_body = {
        "version": "auto_clip_cleaner_v1",
        "status": "ok",
        "job_id": jid,
        "input_path": str(inp),
        "duration_sec": duration,
        "cuts": merged,
        "keep_segments": keep,
        "created_at": _utc_iso(),
    }

    _write_json(bad_report_path, bad_body)
    _write_json(cut_list_path, cut_body)

    if not preview_only and not apply:
        report["status"] = "analyzed_only"
        report["bad_shot_report"] = str(bad_report_path)
        report["cut_list"] = str(cut_list_path)
        return report

    if not keep:
        return _fail("no_segments_after_cuts", extra_warnings=["all_content_removed"])

    if preview_only:
        ok, err = _render_concat(
            inp,
            keep,
            preview_path,
            ffmpeg=ffmpeg,
            full_encode=False,
            max_output_sec=PREVIEW_MAX_SEC,
        )
        if not ok:
            report["status"] = "blocked"
            report["block_reason"] = err or "preview_render_failed"
            _write_json(bad_report_path, {**bad_body, "status": "blocked", "block_reason": report["block_reason"]})
            return report
        report["status"] = "preview_ok"
        report["preview_clean"] = str(preview_path)
        report["bad_shot_report"] = str(bad_report_path)
        report["cut_list"] = str(cut_list_path)
        return report

    if apply:
        ok, err = _render_concat(
            inp,
            keep,
            clean_path,
            ffmpeg=ffmpeg,
            full_encode=True,
            max_output_sec=None,
        )
        if not ok:
            report["status"] = "blocked"
            report["block_reason"] = err or "apply_render_failed"
            _write_json(bad_report_path, {**bad_body, "status": "blocked", "block_reason": report["block_reason"]})
            return report
        report["status"] = "apply_ok"
        report["clean_full"] = str(clean_path)
        report["bad_shot_report"] = str(bad_report_path)
        report["cut_list"] = str(cut_list_path)
        return report

    report["status"] = "analyzed_only"
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path, help="Source video (never modified)")
    ap.add_argument("--auto-detect", action="store_true", help="Run v1 bad-shot detectors")
    ap.add_argument("--preview-only", action="store_true", help="Write preview_clean.mp4 after analyze")
    ap.add_argument("--apply", action="store_true", help="Write clean_full.mp4 (CFR 30 H.264 AAC)")
    ap.add_argument(
        "--cut",
        action="append",
        default=[],
        metavar="HH:MM:SS-HH:MM:SS",
        help="Manual cut range (repeatable)",
    )
    ap.add_argument("--job-id", default="", help="Optional job id suffix")
    args = ap.parse_args()

    if args.preview_only and args.apply:
        print("error: use --preview-only or --apply, not both", file=sys.stderr)
        return 2

    if not args.auto_detect and not args.cut and not args.preview_only and not args.apply:
        print("hint: pass --auto-detect and/or --cut; use --preview-only or --apply to render", file=sys.stderr)

    try:
        body = run_job(
            inp=args.input,
            auto_detect=bool(args.auto_detect),
            manual_specs=list(args.cut or []),
            preview_only=bool(args.preview_only),
            apply=bool(args.apply),
            job_id=str(args.job_id or ""),
        )
    except FileNotFoundError as exc:
        print(json.dumps({"status": "blocked", "block_reason": str(exc)}, indent=2))
        print("AUTO_CLIP_CLEANER_V1_READY=false")
        return 1

    blocked = body.get("status") == "blocked"
    ok = not blocked and body.get("status") not in ("failed",)
    print(json.dumps(body, indent=2, ensure_ascii=False))
    print(f"AUTO_CLIP_CLEANER_V1_READY={'true' if ok else 'false'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
