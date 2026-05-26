#!/usr/bin/env python3
"""Asset Pool Probe Repair v1 — ffprobe every ready-pool media file; JSON + MD for Control Center logs."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
_NYC = _SCRIPTS / "nyc_auto"
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import auto_publish_queue as apq  # noqa: E402

try:
    from nyc_long_source_policy import is_valid_nyc_long_source  # noqa: E402
except Exception:  # noqa: BLE001

    def is_valid_nyc_long_source(_p: Path, *, ffprobe_meta: dict[str, Any] | None = None) -> dict[str, Any]:  # type: ignore[misc]
        return {"long_allowed": False, "reason": "policy_import_failed", "reject_reasons": []}


try:
    from utils.storage_paths import get_sv_transfer, get_transfer_ready_to_upload  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER/ready_to_upload")


CONTROL_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"
OUT_JSON = CONTROL_LOGS / "ready_pool_streams_diagnose.json"
OUT_MD = CONTROL_LOGS / "ready_pool_streams_diagnose.md"

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
LONG_MIN_SEC_FOR_SAFE = 180.0
LONG_AR_MIN, LONG_AR_MAX = 1.55, 1.90


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ffprobe_run(path: Path) -> tuple[dict[str, Any] | None, str, int]:
    exe = shutil.which("ffprobe") or "ffprobe"
    cmd = [
        exe,
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
        tail = (r.stderr or "")[-4000:]
        if r.returncode != 0:
            return None, tail, int(r.returncode)
        return json.loads(r.stdout or "{}"), tail, 0
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as exc:
        return None, repr(exc)[-4000:], -1


def _first_stream(probe: dict[str, Any] | None, codec_type: str) -> dict[str, Any] | None:
    if not probe:
        return None
    for st in probe.get("streams") or []:
        if isinstance(st, dict) and (st.get("codec_type") or "").lower() == codec_type.lower():
            return st
    return None


def _duration_sec(probe: dict[str, Any] | None) -> float | None:
    if not probe:
        return None
    try:
        fmt = probe.get("format") or {}
        d = float((str(fmt.get("duration") or "0").strip() or "0"))
        return d if d > 0 else None
    except (TypeError, ValueError):
        return None


def _pool_type(path: Path, *, xfer: Path, ready: Path, shorts_fb: Path, runtime_long: Path) -> str:
    try:
        rp = path.resolve()
    except OSError:
        rp = path
    s = str(rp).replace("\\", "/")
    rt = str(runtime_long.resolve()).replace("\\", "/")
    if s.startswith(rt):
        return "runtime"
    sfb = str(shorts_fb.resolve()).replace("\\", "/")
    if s.startswith(sfb):
        return "shorts_ready"
    shorts_clips = xfer / "ready_to_upload" / "shorts_clips"
    try:
        sc = str(shorts_clips.resolve()).replace("\\", "/")
    except OSError:
        sc = ""
    if sc and (s == sc or s.startswith(sc + "/")):
        return "shorts_ready"
    nyc = ready / "nyc_long_clips"
    try:
        nc = str(nyc.resolve()).replace("\\", "/")
    except OSError:
        nc = ""
    ru = str(ready.resolve()).replace("\\", "/")
    if nc and (s == nc or s.startswith(nc + "/")):
        return "long_ready"
    if s.startswith(ru) and "/_invalid_probe/" not in s + "/":
        return "long_ready"
    return "other"


def _shorts_marker_in_long_path(path: Path, pool: str) -> bool:
    if pool != "long_ready":
        return False
    low = str(path).replace("\\", "/").lower()
    if "/shorts_clips/" in low or "/shorts_uploads/" in low:
        return True
    base = path.name.lower()
    if re.search(r"\bshorts\b", base) or "tiktok" in base or "reels" in base:
        return True
    return False


def _collect_unique_files(roots: list[Path], warnings: list[str]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            warnings.append(f"missing_scan_root:{root}")
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file() or p.name.startswith("._"):
                    continue
                if p.suffix.lower() not in VIDEO_EXTS:
                    continue
                low = str(p).replace("\\", "/").lower()
                if "/_invalid_probe/" in low:
                    continue
                if "/00_inbox/iphone" in low or "/sv_cache/inbox" in low:
                    continue
                try:
                    k = str(p.resolve())
                except OSError:
                    k = str(p)
                if k in seen:
                    continue
                seen.add(k)
                out.append(p)
        except OSError as exc:
            warnings.append(f"scan_error:{root}:{exc!r}")
    out.sort(key=lambda x: (str(x)))
    return out


def _classify_row(
    path: Path,
    *,
    xfer: Path,
    ready: Path,
    shorts_fb: Path,
    runtime_long: Path,
) -> dict[str, Any]:
    pool = _pool_type(path, xfer=xfer, ready=ready, shorts_fb=shorts_fb, runtime_long=runtime_long)
    row: dict[str, Any] = {
        "path": str(path),
        "pool_type": pool,
        "exists": False,
        "size_bytes": 0,
        "mtime": 0.0,
        "ffprobe_ok": False,
        "has_video_stream": False,
        "has_audio_stream": False,
        "video_codec": "",
        "audio_codec": "",
        "width": 0,
        "height": 0,
        "duration_sec": 0.0,
        "aspect_ratio": 0.0,
        "format_name": "",
        "error_tail": "",
        "upload_candidate_safe": False,
        "rejected_reason": "missing_file",
    }
    if path.suffix.lower() not in VIDEO_EXTS:
        row["rejected_reason"] = "unsupported_extension"
        return row
    try:
        st = path.stat()
        row["exists"] = True
        row["size_bytes"] = int(st.st_size)
        row["mtime"] = float(st.st_mtime)
    except OSError as exc:
        row["error_tail"] = repr(exc)[-4000:]
        row["rejected_reason"] = "missing_file"
        return row

    if row["size_bytes"] < apq.MIN_BYTES:
        row["rejected_reason"] = "file_too_small"
        row["error_tail"] = f"min_bytes={apq.MIN_BYTES}"
        return row

    probe, err_tail, rc = _ffprobe_run(path)
    row["error_tail"] = (err_tail or "")[-2000:]
    if probe is None or rc != 0:
        row["rejected_reason"] = "ffprobe_failed"
        row["ffprobe_ok"] = False
        return row

    row["ffprobe_ok"] = True
    fmt = probe.get("format") or {}
    row["format_name"] = str(fmt.get("format_name") or "")

    vst = _first_stream(probe, "video")
    ast = _first_stream(probe, "audio")
    row["has_video_stream"] = vst is not None
    row["has_audio_stream"] = ast is not None
    row["video_codec"] = str((vst or {}).get("codec_name") or "")
    row["audio_codec"] = str((ast or {}).get("codec_name") or "")

    if not row["has_video_stream"]:
        row["rejected_reason"] = "no_video_stream"
        return row

    try:
        row["width"] = int((vst or {}).get("width") or 0)
        row["height"] = int((vst or {}).get("height") or 0)
    except (TypeError, ValueError):
        row["width"] = row["height"] = 0

    d = _duration_sec(probe)
    row["duration_sec"] = float(d or 0.0)
    w, h = row["width"], row["height"]
    ar = (float(w) / float(h)) if w > 0 and h > 0 else 0.0
    row["aspect_ratio"] = round(ar, 4)

    if _shorts_marker_in_long_path(path, pool):
        row["rejected_reason"] = "path_contains_shorts_for_long"
        return row

    if pool == "long_ready":
        if row["duration_sec"] < LONG_MIN_SEC_FOR_SAFE:
            row["rejected_reason"] = "duration_lt_180_for_long"
            return row
        if w <= h or not (LONG_AR_MIN <= ar <= LONG_AR_MAX):
            row["rejected_reason"] = "bad_aspect_for_long"
            return row
        pol = is_valid_nyc_long_source(path, ffprobe_meta=probe)
        if not pol.get("long_allowed"):
            row["rejected_reason"] = "bad_aspect_for_long"
            return row
        row["rejected_reason"] = "ok"
        row["upload_candidate_safe"] = True
        return row

    if pool == "shorts_ready":
        row["rejected_reason"] = "ok"
        row["upload_candidate_safe"] = bool(row["has_video_stream"] and row["duration_sec"] >= 3.0)
        return row

    if pool == "runtime":
        row["rejected_reason"] = "ok" if row["has_video_stream"] else "no_video_stream"
        row["upload_candidate_safe"] = False
        return row

    row["rejected_reason"] = "ok" if row["has_video_stream"] else "no_video_stream"
    row["upload_candidate_safe"] = False
    return row


def main() -> int:
    warnings: list[str] = []
    xfer = get_sv_transfer(verbose=False)
    ready = get_transfer_ready_to_upload(verbose=False)
    shorts_fb = Path.home() / "StateVerge" / "data" / "shorts_runtime" / "shorts_ready_clips"
    runtime_long = Path.home() / "StateVerge" / "data" / "long_runtime" / "long_uploads"

    roots = [
        ready / "nyc_long_clips",
        ready,
        ready / "shorts_clips",
        shorts_fb,
        runtime_long,
    ]
    files = _collect_unique_files(roots, warnings)
    rows: list[dict[str, Any]] = []
    for p in files:
        rows.append(_classify_row(p, xfer=xfer, ready=ready, shorts_fb=shorts_fb, runtime_long=runtime_long))

    def in_long_ready(r: dict[str, Any]) -> bool:
        return r.get("pool_type") == "long_ready"

    def in_shorts_ready(r: dict[str, Any]) -> bool:
        return r.get("pool_type") == "shorts_ready"

    long_ready_files = [r for r in rows if in_long_ready(r)]
    shorts_ready_files = [r for r in rows if in_shorts_ready(r)]
    long_has_v = sum(1 for r in long_ready_files if r.get("has_video_stream"))
    long_no_v = sum(1 for r in long_ready_files if not r.get("has_video_stream"))
    shorts_has_v = sum(1 for r in shorts_ready_files if r.get("has_video_stream"))

    no_v = [r for r in rows if r.get("rejected_reason") == "no_video_stream"]
    ff_fail = [r for r in rows if r.get("rejected_reason") == "ffprobe_failed"]
    rejected = [r for r in rows if r.get("rejected_reason") not in ("ok",)]

    def sort_bad(r: dict[str, Any]) -> tuple[int, str]:
        return (-int(r.get("size_bytes") or 0), str(r.get("path") or ""))

    rejected_sorted = sorted(rejected, key=sort_bad)
    top25 = rejected_sorted[:25]

    suggested = (
        "Run quarantine_invalid_ready_videos.py --dry-run after review; then replace or regenerate "
        "masters with ffmpeg exports that pass ffprobe video stream. Re-run this diagnose to confirm."
    )
    if not xfer.is_dir():
        suggested = "Mount /Volumes/SV_TRANSFER; then re-run. " + suggested
    elif long_no_v == len(long_ready_files) and long_ready_files:
        suggested = "All long_ready files lack a video stream (corrupt mux or wrong container). " + suggested

    summary = {
        "generated_at": _utc(),
        "total_files_scanned": len(rows),
        "long_ready_files": len(long_ready_files),
        "long_ready_has_video_count": long_has_v,
        "long_ready_no_video_count": long_no_v,
        "shorts_ready_files": len(shorts_ready_files),
        "shorts_ready_has_video_count": shorts_has_v,
        "no_video_stream_files": len(no_v),
        "ffprobe_failed_files": len(ff_fail),
        "top_25_rejected_files": top25,
        "suggested_next_action": suggested,
        "scan_roots": [str(x) for x in roots],
        "warnings": warnings,
    }

    report = {"summary": summary, "files": rows}
    CONTROL_LOGS.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        f"# Ready pool stream diagnose ({summary['generated_at']})",
        "",
        "## Summary",
        "",
        f"- total_files_scanned: **{summary['total_files_scanned']}**",
        f"- long_ready_files: **{summary['long_ready_files']}**",
        f"- long_ready_has_video_count: **{summary['long_ready_has_video_count']}**",
        f"- long_ready_no_video_count: **{summary['long_ready_no_video_count']}**",
        f"- shorts_ready_files: **{summary['shorts_ready_files']}**",
        f"- shorts_ready_has_video_count: **{summary['shorts_ready_has_video_count']}**",
        f"- no_video_stream_files: **{summary['no_video_stream_files']}**",
        f"- ffprobe_failed_files: **{summary['ffprobe_failed_files']}**",
        "",
        "## Top 25 rejected",
        "",
    ]
    for r in top25:
        md_lines.append(
            f"- `{r.get('path')}` — **{r.get('rejected_reason')}** "
            f"(size={r.get('size_bytes')}, ffprobe_ok={r.get('ffprobe_ok')})"
        )
    if len(rejected_sorted) > 25:
        md_lines.append(f"- … **{len(rejected_sorted) - 25}** more in JSON `files`.")
    md_lines.extend(["", "## Suggested next action", "", suggested, ""])
    OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")

    print(json.dumps({"ok": True, "json": str(OUT_JSON), "md": str(OUT_MD), **summary}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
