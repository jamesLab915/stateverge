#!/usr/bin/env python3
"""StateVerge Shorts cinematic dual-channel mix v1 (music-first, very low ambience).

Calls ``stateverge_drive_ambience_only_v1.py`` with ``--skip-repair`` for a faster
ambience bed, selects a more energetic (but still non-EDM / non-vocal) cinematic track,
applies a short hook fade + treble emphasis on the first second when duration allows,
mixes, loudnorm, and muxes CFR30 H.264 + AAC (no ``-c:v copy``).

**Default music library root** matches ``stateverge_calm_ambient_mix_v1``:
``<SV_TRANSFER>/04_AUDIO/music`` (see that script header for Envato layout notes).

``selection_keywords_matched`` reflects path tokens on ``--input-video`` only; the
clip is still processed regardless of matches (metadata for editorial context).

Fail-open: JSON report always written, exits **0**. No auto_publish, uploads, or
input deletion.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_AUDIO_DIR = _SCRIPT_DIR.parent / "audio"
if str(_AUDIO_DIR) not in sys.path:
    sys.path.insert(0, str(_AUDIO_DIR))
from audio_fade_helpers import music_fade_filter  # noqa: E402

_REPO_ROOT = _SCRIPT_DIR.parent.parent
_DRIVE_AMBIENCE_SCRIPT = _REPO_ROOT / "scripts" / "audio" / "stateverge_drive_ambience_only_v1.py"

_SV_CACHE_PRIMARY = Path("/Volumes/SV_CACHE")
_FALLBACK_SV_CACHE = Path.home() / "StateVerge" / "_storage_fallback" / "sv_cache"

_AUDIO_EXTS = frozenset({".mp3", ".wav", ".m4a", ".aac"})
_MAX_MUSIC_FILES = 8000
_MAX_WALK_DEPTH = 8

_FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
_FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"

_CINEMA_PREFER = ("cinematic", "epic", "trailer", "ambient", "orchestral", "film", "score")
_SHORTS_PATH_KEYS = (
    "skyline",
    "sunset",
    "bridge",
    "rain",
    "tunnel",
    "manhattan",
    "ferry",
    "reflection",
    "timelapse",
)
_EXCLUDE = (
    "edm",
    "trap",
    "vocal",
    "vocals",
    "aggressive",
    "beat",
    "club",
    "party",
)

_SUBPROCESS_TIMEOUT = 14400.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_stamp_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_sv_cache_base(*, leaf: str) -> Path:
    for cand in (_SV_CACHE_PRIMARY, _FALLBACK_SV_CACHE):
        try:
            probe = cand / leaf / ".write_probe"
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)  # type: ignore[arg-type]
            return cand
        except OSError:
            continue
    fb = _FALLBACK_SV_CACHE
    fb.mkdir(parents=True, exist_ok=True)
    return fb


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _run_py_script(script: Path, argv: list[str]) -> tuple[int, str]:
    cmd = [sys.executable, str(script), *argv]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return 127, "python_executable_not_found"
    except subprocess.TimeoutExpired:
        return 124, "subprocess_timeout"
    tail = ((r.stderr or "") + "\n" + (r.stdout or ""))[-12000:]
    return r.returncode, tail


def _run_cmd(cmd: list[str], *, timeout: float) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return 127, "executable_not_found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    tail = ((r.stderr or "") + "\n" + (r.stdout or ""))[-24000:]
    return r.returncode, tail


def _mp4_usable(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 1024
    except OSError:
        return False


def discover_default_music_library_root() -> Path:
    try:
        _src = _REPO_ROOT / "src"
        if _src.is_dir() and str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        from utils.storage_paths import get_sv_transfer  # type: ignore

        xfer = get_sv_transfer(verbose=False)
    except Exception:
        xfer = Path("/Volumes/SV_TRANSFER")
    return xfer / "04_AUDIO" / "music"


def _walk_music_files(root: Path) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        return out
    root_r = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        depth = len(Path(dirpath).relative_to(root_r).parts)
        if depth > _MAX_WALK_DEPTH:
            dirnames[:] = []
            continue
        for fn in filenames:
            if len(out) >= _MAX_MUSIC_FILES:
                return out
            suf = Path(fn).suffix.lower()
            if suf in _AUDIO_EXTS:
                out.append(Path(dirpath) / fn)
    return out


def _lower_path(p: Path) -> str:
    return str(p).lower()


def _excluded_path(lp: str) -> bool:
    return any(x in lp for x in _EXCLUDE)


def _cinema_hits(lp: str) -> int:
    return sum(1 for k in _CINEMA_PREFER if k in lp)


def _score_cinematic_track(p: Path) -> float:
    lp = _lower_path(p)
    if _excluded_path(lp):
        base = -500.0
    else:
        base = 0.0
    pref = _cinema_hits(lp) * 15.0
    try:
        sz = p.stat().st_size
        bonus = math.log10(max(sz, 1)) * 2.0
    except OSError:
        bonus = 0.0
    return base + pref + bonus


def pick_cinematic_music(root: Path | None) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []
    if root is None or not root.is_dir():
        warnings.append("music_library_root_missing_or_not_dir")
        return None, warnings
    files = _walk_music_files(root)
    if not files:
        warnings.append("music_library_empty_after_bounded_scan")
        return None, warnings
    good = [p for p in files if not _excluded_path(_lower_path(p))]
    pool = good if good else files
    best = max(pool, key=_score_cinematic_track)
    if good:
        warnings.append(f"music_candidates_scanned:{len(files)}_non_excluded:{len(good)}")
    else:
        warnings.append("music_fail_open_all_excluded_pick_least_bad")
    return best, warnings


def probe_duration_sec(path: Path) -> float | None:
    cmd = [
        _FFPROBE,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    rc, out = _run_cmd(cmd, timeout=120.0)
    if rc != 0:
        return None
    try:
        v = float((out.strip().splitlines() or ["0"])[0].strip())
        return v if math.isfinite(v) and v > 0 else None
    except ValueError:
        return None


def selection_keywords_matched(video_path: Path) -> list[str]:
    lp = video_path.as_posix().lower()
    return [k for k in _SHORTS_PATH_KEYS if k in lp]


def _mux_filter_complex_video_audio(
    video_src: Path,
    filter_complex: str,
    map_audio_label: str,
    out_mp4: Path,
    *,
    extra_inputs: list[Path],
    extra_input_prefixes: list[list[str]] | None,
    warnings: list[str],
    errors: list[str],
) -> bool:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_mp4.parent / f"{out_mp4.stem}._tmp_{os.getpid()}.mp4"
    attempts: list[tuple[str, list[str]]] = [
        (
            "h264_videotoolbox",
            [
                "-c:v",
                "h264_videotoolbox",
                "-b:v",
                "35M",
                "-tag:v",
                "avc1",
            ],
        ),
        (
            "libx264",
            [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-tag:v",
                "avc1",
            ],
        ),
    ]
    for label, enc in attempts:
        cmd = [
            _FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(video_src),
        ]
        prefixes = extra_input_prefixes or [[] for _ in extra_inputs]
        while len(prefixes) < len(extra_inputs):
            prefixes.append([])
        for idx, p in enumerate(extra_inputs):
            cmd.extend(prefixes[idx])
            cmd.extend(["-i", str(p)])
        cmd.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[vout]",
                "-map",
                map_audio_label,
                *enc,
                "-r",
                "30",
                "-vsync",
                "cfr",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                str(tmp),
            ]
        )
        rc, tail = _run_cmd(cmd, timeout=_SUBPROCESS_TIMEOUT)
        if rc == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
            try:
                tmp.replace(out_mp4)
            except OSError:
                shutil.copy2(tmp, out_mp4)
                try:
                    tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
                except OSError:
                    pass
            warnings.append(f"mux_encoder_used:{label}")
            return True
        errors.append(f"mux_failed_{label}_rc:{rc}")
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        warnings.append(f"mux_tail_{label}:{tail[-2000:] if tail else ''}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-video", required=True, help="Source video path.")
    ap.add_argument(
        "--music-library-root",
        default=None,
        help="Music library root. Default: SV_TRANSFER/04_AUDIO/music.",
    )
    ap.add_argument("--output-video", default=None, help="Optional explicit output MP4 path.")
    ap.add_argument(
        "--emit-pipeline-ready",
        action="store_true",
        help="Print SHORTS_CINEMATIC_PIPELINE_READY=true and exit 0.",
    )
    args = ap.parse_args()

    if args.emit_pipeline_ready:
        print("SHORTS_CINEMATIC_PIPELINE_READY=true")
        return 0

    cache_base = _resolve_sv_cache_base(leaf="audio_shorts_cinematic")
    out_dir = cache_base / "audio_shorts_cinematic"
    reports_dir = out_dir / "reports"
    report_path = reports_dir / "shorts_cinematic_mix_report.json"
    stamp = _utc_stamp_filename()
    work_root = out_dir / "work" / f"shorts_cinematic_{stamp}"
    canonical_out = out_dir / f"shorts_cinematic_mix_{stamp}.mp4"

    video_in = Path(args.input_video).expanduser().resolve()
    user_out = Path(args.output_video).expanduser().resolve() if args.output_video else None
    final_out = user_out if user_out is not None else canonical_out

    music_root = (
        Path(args.music_library_root).expanduser().resolve()
        if args.music_library_root
        else discover_default_music_library_root()
    )

    warnings: list[str] = []
    errors: list[str] = []
    kw_matched = selection_keywords_matched(video_in)
    hook_processing = "pending"
    success = False
    duration_sec: float | None = None
    ambience_gain_db = -35.0
    music_gain_db = -6.0

    payload: dict[str, Any] = {
        "input_video": str(video_in),
        "output_video": str(final_out),
        "selected_music": None,
        "ambience_gain_db": ambience_gain_db,
        "music_gain_db": music_gain_db,
        "hook_processing": hook_processing,
        "cinematic_profile": "shorts_cinematic_music_first_v1",
        "selection_keywords_matched": kw_matched,
        "duration_sec": duration_sec,
        "warnings": warnings,
        "errors": errors,
        "success": success,
        "timestamp": _utc_now_iso(),
    }

    try:
        if not shutil.which(_FFMPEG) and not Path(_FFMPEG).is_file():
            errors.append("ffmpeg_not_found")
            _write_report(report_path, payload)
            return 0

        if not video_in.is_file():
            errors.append("input_missing_or_not_file")
            _write_report(report_path, payload)
            return 0

        work_root.mkdir(parents=True, exist_ok=True)
        ambi_mp4 = work_root / "drive_ambience_skip_repair.mp4"

        if _DRIVE_AMBIENCE_SCRIPT.is_file():
            drv_argv = [
                "--input-video",
                str(video_in),
                "--output-video",
                str(ambi_mp4),
                "--skip-repair",
            ]
            rc_d, tail_d = _run_py_script(_DRIVE_AMBIENCE_SCRIPT, drv_argv)
            if rc_d != 0:
                warnings.append(f"drive_ambience_nonzero_exit:{rc_d}")
                warnings.append(f"drive_ambience_tail:{tail_d[-1500:]}")
            rep_drive = cache_base / "audio_drive_ambience" / "reports" / "drive_ambience_only_report.json"
            if not _mp4_usable(ambi_mp4):
                try:
                    if rep_drive.is_file():
                        dj = json.loads(rep_drive.read_text(encoding="utf-8", errors="replace"))
                        ov = dj.get("output_video")
                        if isinstance(ov, str) and _mp4_usable(Path(ov)):
                            ambi_mp4 = Path(ov)
                            warnings.append(f"using_drive_report_output:{ambi_mp4}")
                except Exception:
                    pass
        else:
            warnings.append("drive_ambience_script_missing_using_input_video_bed")
            ambi_mp4 = video_in

        if not _mp4_usable(ambi_mp4):
            warnings.append("ambience_bed_unusable_fallback_input_video")
            ambi_mp4 = video_in

        duration_sec = probe_duration_sec(ambi_mp4)
        if duration_sec is None:
            warnings.append("duration_probe_failed_using_default_120")
            duration_sec = 120.0

        music_path, mw = pick_cinematic_music(music_root)
        warnings.extend(mw)
        if music_path:
            payload["selected_music"] = str(music_path)

        ambience_gain_db = round(random.uniform(-38.0, -32.0), 2)
        music_gain_db = -6.0
        payload["ambience_gain_db"] = ambience_gain_db
        payload["music_gain_db"] = music_gain_db
        payload["duration_sec"] = duration_sec

        dur_s = f"{duration_sec:.3f}"
        mus_fade = music_fade_filter(duration_sec, shorts=True)

        if music_path and _mp4_usable(music_path):
            if duration_sec >= 1.05:
                hook_processing = (
                    "first_1s_high_shelf_concat+policy_music_fade_on_segment;"
                    "equalizer=f=9000:width_type=h:width=3000:g=2.5_on_0-1s"
                )
                mus_chain = (
                    f"[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo,channels=2,"
                    f"atrim=duration={dur_s},asetpts=PTS-STARTPTS,"
                    f"asplit=2[h0][t0];"
                    f"[h0]atrim=0:1,asetpts=PTS-STARTPTS,"
                    f"equalizer=f=9000:width_type=h:width=3000:g=2.5[hb];"
                    f"[t0]atrim=start=1,asetpts=PTS-STARTPTS[tb];"
                    f"[hb][tb]concat=n=2:v=0:a=1[hk];"
                    f"[hk]{mus_fade},volume={music_gain_db}dB[mus]"
                )
            else:
                hook_processing = (
                    "duration_lt_hook_window_used_policy_fade_plus_global_treble_lift;"
                    "limitation:first_second_only_shelf_not_isolated"
                )
                warnings.append("hook_simplified_short_duration")
                mus_chain = (
                    f"[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo,channels=2,"
                    f"atrim=duration={dur_s},asetpts=PTS-STARTPTS,"
                    f"equalizer=f=9000:width_type=h:width=3500:g=2.0,"
                    f"{mus_fade},volume={music_gain_db}dB[mus]"
                )

            fc = (
                f"[0:v]fps=30,format=yuv420p[vout];"
                f"{mus_chain};"
                f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo,channels=2,"
                f"volume={ambience_gain_db}dB[bed];"
                f"[bed][mus]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[mx];"
                f"[mx]aformat=sample_fmts=fltp:channel_layouts=stereo,loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
            )
            ok = _mux_filter_complex_video_audio(
                ambi_mp4,
                fc,
                "[aout]",
                final_out,
                extra_inputs=[music_path],
                extra_input_prefixes=[["-stream_loop", "-1"]],
                warnings=warnings,
                errors=errors,
            )
        else:
            hook_processing = "skipped_no_music_ambience_only"
            fc = (
                f"[0:v]fps=30,format=yuv420p[vout];"
                f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo,channels=2,"
                f"volume={ambience_gain_db}dB,loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
            )
            ok = _mux_filter_complex_video_audio(
                ambi_mp4,
                fc,
                "[aout]",
                final_out,
                extra_inputs=[],
                extra_input_prefixes=[],
                warnings=warnings,
                errors=errors,
            )

        payload["hook_processing"] = hook_processing
        success = ok and _mp4_usable(final_out)
        payload["success"] = success
        if not success:
            errors.append("final_mp4_missing_or_mux_failed")
        _write_report(report_path, payload)
        return 0

    except Exception as e:  # noqa: BLE001
        errors.append(f"orchestrator_exception:{type(e).__name__}:{e}")
        warnings.append(traceback.format_exc()[-8000:])
        payload["success"] = False
        payload["errors"] = errors
        payload["warnings"] = warnings
        _write_report(report_path, payload)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
