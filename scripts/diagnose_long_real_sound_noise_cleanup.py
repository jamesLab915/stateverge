#!/usr/bin/env python3
"""Diagnose Long Real Sound Noise Cleanup Gate v1 — ffmpeg filters, reports, clean master inventory."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path.home() / "StateVerge"
_SCRIPTS = _REPO / "scripts"
_NYC = _SCRIPTS / "nyc_auto"
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from utils.storage_paths import get_sv_cache, get_transfer_ready_to_upload  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")

    def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER/ready_to_upload")


CONTROL_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"
OUT_JSON = CONTROL_LOGS / "long_real_sound_noise_cleanup_diagnose.json"
OUT_MD = CONTROL_LOGS / "long_real_sound_noise_cleanup_diagnose.md"

GATE_SCRIPT = _SCRIPTS / "long_real_sound_noise_cleanup_gate.py"
AUTO_QUEUE = _NYC / "auto_publish_queue.py"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe_bin() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def _filter_help(name: str) -> bool:
    try:
        r = subprocess.run(
            [_ffmpeg_bin(), "-hide_banner", "-h", f"filter={name}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        txt = (r.stderr or "") + (r.stdout or "")
        if r.returncode != 0:
            return False
        return bool(re.search(rf"Filter\s+{re.escape(name)}", txt, re.I))
    except (OSError, subprocess.TimeoutExpired):
        return False


def _latest_under(root: Path, pattern: str) -> str:
    best: tuple[float, Path] | None = None
    if not root.is_dir():
        return ""
    try:
        for p in root.rglob(pattern):
            if not p.is_file():
                continue
            try:
                mt = float(p.stat().st_mtime)
            except OSError:
                continue
            if best is None or mt > best[0]:
                best = (mt, p)
    except OSError:
        return ""
    return str(best[1]) if best else ""


def _count_masters(clips: Path) -> tuple[int, int]:
    raw_c = clean_c = 0
    if not clips.is_dir():
        return 0, 0
    try:
        for p in clips.glob("nyc_long_master*.mp4"):
            if not p.is_file():
                continue
            low = p.name.lower()
            if "clean_real_sound" in low:
                clean_c += 1
            else:
                raw_c += 1
    except OSError:
        pass
    return raw_c, clean_c


def _auto_queue_prefers_clean() -> bool:
    if not AUTO_QUEUE.is_file():
        return False
    try:
        txt = AUTO_QUEUE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "sort_probe_pass_prefer_clean_real_sound" in txt and "_is_clean_real_sound_long_filename" in txt


def main() -> int:
    ffmpeg = _ffmpeg_bin()
    ffprobe = _ffprobe_bin()
    xfer_cache = get_sv_cache(verbose=False)
    home_reports = Path.home() / "StateVerge" / "data" / "audio_runtime" / "long_real_sound_noise_cleanup"
    primary_reports = xfer_cache / "audio_processing" / "long_real_sound_noise_cleanup"

    ready = get_transfer_ready_to_upload(verbose=False)
    clips = ready / "nyc_long_clips"
    raw_n, clean_n = _count_masters(clips)

    latest_report = _latest_under(primary_reports, "long_real_sound_noise_cleanup_report.json")
    if not latest_report:
        latest_report = _latest_under(home_reports, "long_real_sound_noise_cleanup_report.json")

    latest_final = _latest_under(primary_reports, "final_long_clean_real_sound.mp4")
    if not latest_final:
        latest_final = _latest_under(home_reports, "final_long_clean_real_sound.mp4")

    summary: dict[str, Any] = {
        "generated_at": _utc(),
        "gate_script_exists": GATE_SCRIPT.is_file(),
        "gate_script_path": str(GATE_SCRIPT),
        "ffmpeg_path": ffmpeg,
        "ffmpeg_exists": shutil.which(ffmpeg) is not None or Path(ffmpeg).is_file(),
        "ffprobe_path": ffprobe,
        "ffprobe_exists": shutil.which(ffprobe) is not None or Path(ffprobe).is_file(),
        "ffmpeg_supports_afftdn": _filter_help("afftdn"),
        "ffmpeg_supports_anlmdn": _filter_help("anlmdn"),
        "latest_long_real_sound_noise_cleanup_report_json": latest_report,
        "latest_final_long_clean_real_sound_mp4": latest_final,
        "nyc_long_clips_raw_master_count": raw_n,
        "nyc_long_clips_clean_real_sound_master_count": clean_n,
        "auto_publish_queue_prefers_clean_real_sound": _auto_queue_prefers_clean(),
    }

    CONTROL_LOGS.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"summary": summary}, indent=2, ensure_ascii=False), encoding="utf-8")
    md = [
        f"# Long real sound noise cleanup diagnose ({summary['generated_at']})",
        "",
        f"- gate_script_exists: **{summary['gate_script_exists']}**",
        f"- ffmpeg / ffprobe: **{summary['ffmpeg_exists']}** / **{summary['ffprobe_exists']}**",
        f"- afftdn / anlmdn: **{summary['ffmpeg_supports_afftdn']}** / **{summary['ffmpeg_supports_anlmdn']}**",
        f"- latest report JSON: `{summary['latest_long_real_sound_noise_cleanup_report_json'] or '—'}`",
        f"- latest final mp4: `{summary['latest_final_long_clean_real_sound_mp4'] or '—'}`",
        f"- nyc_long_clips raw masters: **{raw_n}**",
        f"- nyc_long_clips clean_real_sound masters: **{clean_n}**",
        f"- auto_publish_queue prefers clean_real_sound ordering: **{summary['auto_publish_queue_prefers_clean_real_sound']}**",
        "",
    ]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(OUT_JSON), "md": str(OUT_MD), **summary}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
