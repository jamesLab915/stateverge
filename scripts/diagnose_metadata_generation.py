#!/usr/bin/env python3
"""Diagnose StateVerge Auto Metadata Generation v1 wiring."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CC = Path.home() / "StateVerge_Control_Center" / "logs"
_CC.mkdir(parents=True, exist_ok=True)
_OUT_JSON = _CC / "metadata_generation_diagnose.json"
_OUT_MD = _CC / "metadata_generation_diagnose.md"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _grep(hay: str, pat: str) -> bool:
    return re.search(pat, hay, re.MULTILINE | re.DOTALL) is not None


def main() -> int:
    warnings: list[str] = []
    mg = _REPO / "scripts" / "nyc_auto" / "metadata_generator.py"
    yu = _REPO / "scripts" / "nyc_auto" / "youtube_upload.py"
    longq = _REPO / "scripts" / "nyc_auto" / "auto_publish_queue.py"
    shortq = _REPO / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py"
    shorts_w = _REPO / "scripts" / "jobs" / "shorts_cut_upload_job.py"

    metadata_generator_exists = mg.is_file()
    youtube_upload_reads_metadata = yu.is_file() and _grep(_read(yu), r"resolve_package_snippet")
    long_queue_metadata_connected = longq.is_file() and _grep(_read(longq), r"ensure_youtube_metadata_file")
    shorts_metadata_connected = shorts_w.is_file() and _grep(_read(shorts_w), r"_shorts_write_youtube_metadata_pack")
    shorts_queue_forwards_ai = shortq.is_file() and _grep(_read(shortq), r"use-ai-metadata")

    manual_root = Path("/Volumes/SV_TRANSFER/publish_pack/manual_uploads")
    manual_ok = False
    sample_pkg = ""
    if manual_root.is_dir():
        for c in sorted(manual_root.iterdir()):
            if c.is_dir():
                sample_pkg = str(c)
                manual_ok = (c / "youtube_metadata.json").is_file() or (c / "selected_video_path.txt").is_file()
                break

    shorts_root = Path("/Volumes/SV_TRANSFER/publish_pack/shorts_uploads")
    latest_shorts_meta = ""
    latest_shorts_title = ""
    if shorts_root.is_dir():
        metas = sorted(shorts_root.rglob("youtube_metadata.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if metas:
            latest_shorts_meta = str(metas[0])
            try:
                dj = json.loads(metas[0].read_text(encoding="utf-8", errors="replace"))
                latest_shorts_title = str(dj.get("title") or "")
            except (OSError, json.JSONDecodeError):
                warnings.append("latest_shorts_metadata_unreadable")

    sample_long_title = ""
    probe_video = Path("/Volumes/SV_TRANSFER/ready_to_upload/shorts_clips")
    if probe_video.is_dir():
        mp4s = sorted(probe_video.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if mp4s:
            try:
                r2 = subprocess.run(
                    [
                        sys.executable or "python3",
                        str(mg),
                        "--type",
                        "long",
                        "--video",
                        str(mp4s[0]),
                        "--dry-run",
                    ],
                    cwd=str(_REPO / "scripts" / "nyc_auto"),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                if r2.stdout.strip().startswith("{"):
                    dj = json.loads(r2.stdout)
                    sample_long_title = str(dj.get("title") or "")[:120]
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                warnings.append(f"sample_long_clip:{type(exc).__name__}")
    if not sample_long_title:
        warnings.append("sample_long_title_skipped_no_ready_mp4")

    sample_shorts_title = (latest_shorts_title or "")[:120]

    status = "ok"
    if not metadata_generator_exists:
        status = "error"
    if not youtube_upload_reads_metadata or not long_queue_metadata_connected:
        status = "warning"

    payload = {
        "metadata_generator_exists": metadata_generator_exists,
        "youtube_upload_reads_metadata": youtube_upload_reads_metadata,
        "long_queue_metadata_connected": long_queue_metadata_connected,
        "shorts_metadata_connected": shorts_metadata_connected,
        "shorts_queue_forwards_use_ai_metadata_flag": shorts_queue_forwards_ai,
        "manual_package_metadata_ok": manual_ok,
        "latest_shorts_metadata_ok": bool(latest_shorts_meta),
        "sample_long_title": sample_long_title,
        "sample_shorts_title": sample_shorts_title,
        "warnings": warnings,
        "status": status,
        "sample_manual_package": sample_pkg,
        "latest_shorts_metadata_path": latest_shorts_meta,
    }

    _OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_lines = [
        "# Metadata generation diagnose (v1)",
        "",
        f"- metadata_generator_exists: {metadata_generator_exists}",
        f"- youtube_upload_reads_metadata: {youtube_upload_reads_metadata}",
        f"- long_queue_metadata_connected: {long_queue_metadata_connected}",
        f"- shorts_metadata_connected: {shorts_metadata_connected}",
        f"- shorts_queue_forwards_use_ai_metadata_flag: {shorts_queue_forwards_ai}",
        f"- manual_package_metadata_ok: {manual_ok}",
        f"- latest_shorts_metadata_ok: {bool(latest_shorts_meta)}",
        f"- sample_long_title: {sample_long_title}",
        f"- sample_shorts_title: {sample_shorts_title}",
        f"- status: {status}",
        "",
        "## warnings",
        "\n".join(f"- {w}" for w in warnings) or "- (none)",
    ]
    _OUT_MD.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": status != "error", "status": status, "wrote_json": str(_OUT_JSON), "wrote_md": str(_OUT_MD)}, indent=2))
    return 0 if status != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
