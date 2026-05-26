#!/usr/bin/env python3
"""StateVerge calm ambient + music dual-channel mix v1 (long-form, low stimulation).

Orchestrates ``stateverge_drive_ambience_only_v1.py`` for a clean ambience bed,
picks a calm-ish track from the Envato / channel music library (bounded scan),
mixes with ffmpeg (amix + loudnorm), and muxes CFR30 H.264 + AAC (no ``-c:v copy``).

**Default music library root** (when ``--music-library-root`` is omitted):
``<SV_TRANSFER>/04_AUDIO/music`` via ``utils.storage_paths.get_sv_transfer()`` when
importable; else ``/Volumes/SV_TRANSFER/04_AUDIO/music``. This matches
``music_selector.default_music_index_path`` / ``envato_music_importer`` layout
(``04_AUDIO/music/nyc_long/...``). If that directory is missing, the script
fail-opens with warnings and ambience-only output when possible.

Fail-open: always writes JSON under ``.../reports/``, exits **0** even on errors.
Does not touch auto_publish, uploads, or delete inputs.
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
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from audio_fade_helpers import music_fade_filter  # noqa: E402

_REPO_ROOT = _SCRIPT_DIR.parent.parent
_DRIVE_AMBIENCE_SCRIPT = _SCRIPT_DIR / "stateverge_drive_ambience_only_v1.py"

_SV_CACHE_PRIMARY = Path("/Volumes/SV_CACHE")
_FALLBACK_SV_CACHE = Path.home() / "StateVerge" / "_storage_fallback" / "sv_cache"

_AUDIO_EXTS = frozenset({".mp3", ".wav", ".m4a", ".aac"})
_MAX_MUSIC_FILES = 8000
_MAX_WALK_DEPTH = 8

_FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
_FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"

_CALM_PREFER = (
    "ambient",
    "calm",
    "piano",
    "cinematic",
    "documentary",
    "night",
    "drive",
    "slow",
    "soft",
)
_CALM_EXCLUDE = (
    "edm",
    "trap",
    "vocal",
    "vocals",
    "aggressive",
    "beat",
    "club",
    "party",
)
_DUCK_PATH_KEYS = ("ferry", "bridge", "fdr", "water", "traffic")

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
    """Resolve SV_TRANSFER/04_AUDIO/music (Envato channel tree root)."""
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
    return any(x in lp for x in _CALM_EXCLUDE)


def _prefer_hits(lp: str) -> int:
    return sum(1 for k in _CALM_PREFER if k in lp)


def _score_calm_track(p: Path) -> tuple[float, float]:
    lp = _lower_path(p)
    if _excluded_path(lp):
        base = -500.0
    else:
        base = 0.0
    pref = _prefer_hits(lp) * 12.0
    try:
        dur = float(max(0.0, p.stat().st_size / 200_000.0))
    except OSError:
        dur = 0.0
    try:
        sz = p.stat().st_size
        dur_bonus = math.log10(max(sz, 1)) * 3.0
    except OSError:
        dur_bonus = 0.0
    return base + pref + dur_bonus, float(sz)


def pick_calm_music(root: Path | None) -> tuple[Path | None, list[str]]:
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
    best = max(pool, key=lambda p: _score_calm_track(p)[0])
    if good:
        warnings.append(f"music_candidates_scanned:{len(files)}_usable_non_excluded:{len(good)}")
    else:
        warnings.append(f"music_fail_open_all_excluded_pick_least_bad_scanned:{len(files)}")
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


def path_duck_extra_db(video_path: Path) -> tuple[bool, float]:
    lp = video_path.as_posix().lower()
    hit = any(k in lp for k in _DUCK_PATH_KEYS)
    return hit, -3.0 if hit else 0.0


def astats_rms_spike_extra_db(amb_mp4: Path) -> tuple[bool, str, float]:
    """Rough RMS spread on first ~90s of ambience audio; extra duck on music if wide."""
    cmd = [
        _FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-t",
        "90",
        "-i",
        str(amb_mp4),
        "-vn",
        "-af",
        "astats=metadata=1:reset=0.5",
        "-f",
        "null",
        "-",
    ]
    rc, blob = _run_cmd(cmd, timeout=300.0)
    if rc != 0:
        return False, "astats_run_failed", 0.0
    rms_vals: list[float] = []
    for line in blob.splitlines():
        m = re.search(r"RMS level dB:\s*([-\d.]+)", line, re.I)
        if not m:
            m = re.search(r"lavfi\.astats\.Overall\.RMS_level\s*:\s*([-\d.]+)", line, re.I)
        if not m:
            m = re.search(r"RMS\s+difference\s*:\s*([-\d.]+)", line, re.I)
        if m:
            try:
                rms_vals.append(float(m.group(1)))
            except ValueError:
                continue
    if len(rms_vals) < 3:
        return False, "astats_insufficient_rms_samples", 0.0
    lo = min(rms_vals)
    hi = max(rms_vals)
    spread = hi - lo
    if spread > 8.0:
        return True, f"rms_level_spread_db:{spread:.2f}", -3.0
    return False, f"rms_level_spread_db:{spread:.2f}", 0.0


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
                "18",
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
        tail_hint = tail[-2000:] if tail else ""
        warnings.append(f"mux_tail_{label}:{tail_hint}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-video", required=True, help="Source video path.")
    ap.add_argument(
        "--music-library-root",
        default=None,
        help="Music library root (Envato tree). Default: SV_TRANSFER/04_AUDIO/music.",
    )
    ap.add_argument("--output-video", default=None, help="Optional explicit output MP4 path.")
    ap.add_argument(
        "--emit-pipeline-ready",
        action="store_true",
        help="Print CALM_AMBIENT_PIPELINE_READY=true and exit 0.",
    )
    args = ap.parse_args()

    if args.emit_pipeline_ready:
        print("CALM_AMBIENT_PIPELINE_READY=true")
        return 0

    cache_base = _resolve_sv_cache_base(leaf="audio_calm_ambient")
    out_dir = cache_base / "audio_calm_ambient"
    reports_dir = out_dir / "reports"
    report_path = reports_dir / "calm_ambient_mix_report.json"
    work_root = out_dir / "work" / f"calm_ambient_{_utc_stamp_filename()}"
    stamp = _utc_stamp_filename()
    canonical_out = out_dir / f"calm_ambient_mix_{stamp}.mp4"

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
    selected_music: str | None = None
    ducking_applied = False
    ducking_detail = "none"
    ambience_gain_db = -24.0
    music_gain_db = -8.0
    duration_sec: float | None = None
    success = False

    payload: dict[str, Any] = {
        "input_video": str(video_in),
        "output_video": str(final_out),
        "selected_music": selected_music,
        "ambience_gain_db": ambience_gain_db,
        "music_gain_db": music_gain_db,
        "ducking_applied": ducking_applied,
        "ducking_detail": ducking_detail,
        "target_lufs": -14.0,
        "target_true_peak": -1.5,
        "ambience_profile": "calm_ambient_nyc_v1",
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
        ambi_mp4 = work_root / "drive_ambience_clean.mp4"

        if _DRIVE_AMBIENCE_SCRIPT.is_file():
            drv_argv = ["--input-video", str(video_in), "--output-video", str(ambi_mp4)]
            rc_d, tail_d = _run_py_script(_DRIVE_AMBIENCE_SCRIPT, drv_argv)
            if rc_d != 0:
                warnings.append(f"drive_ambience_nonzero_exit:{rc_d}")
                warnings.append(f"drive_ambience_tail:{tail_d[-1500:]}")
            rep_drive = cache_base / "audio_drive_ambience" / "reports" / "drive_ambience_only_report.json"
            if not _mp4_usable(ambi_mp4):
                try:
                    for rp in (rep_drive,):
                        if rp.is_file():
                            dj = json.loads(rp.read_text(encoding="utf-8", errors="replace"))
                            ov = dj.get("output_video")
                            if isinstance(ov, str) and _mp4_usable(Path(ov)):
                                ambi_mp4 = Path(ov)
                                warnings.append(f"using_drive_report_output:{ambi_mp4}")
                                break
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
            warnings.append("duration_probe_failed_using_default_600")
            duration_sec = 600.0

        music_path, mw = pick_calm_music(music_root)
        warnings.extend(mw)
        if music_path:
            selected_music = str(music_path)

        ambience_gain_db = round(random.uniform(-30.0, -20.0), 2)
        music_gain_db = -8.0

        path_duck, path_extra = path_duck_extra_db(video_in)
        spike_duck, spike_msg, spike_extra = astats_rms_spike_extra_db(ambi_mp4)
        extra_music_db = path_extra + spike_extra
        if path_duck or spike_duck:
            ducking_applied = True
            parts = []
            if path_duck:
                parts.append(f"path_keywords_extra_db:{path_extra}")
            if spike_duck:
                parts.append(f"astats:{spike_msg}_extra_db:{spike_extra}")
            ducking_detail = ";".join(parts) if parts else "heuristic"
        else:
            ducking_detail = spike_msg if spike_msg else "none"

        music_gain_db = round(music_gain_db + extra_music_db, 2)
        payload["selected_music"] = selected_music
        payload["ambience_gain_db"] = ambience_gain_db
        payload["music_gain_db"] = music_gain_db
        payload["ducking_applied"] = ducking_applied
        payload["ducking_detail"] = ducking_detail
        payload["duration_sec"] = duration_sec

        dur_s = f"{duration_sec:.3f}"
        mus_fade = music_fade_filter(duration_sec, shorts=False)

        if music_path and _mp4_usable(music_path):
            fc = (
                f"[0:v]fps=30,format=yuv420p[vout];"
                f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo,channels=2,volume={ambience_gain_db}dB[bed];"
                f"[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo,channels=2,"
                f"atrim=duration={dur_s},asetpts=PTS-STARTPTS,{mus_fade},volume={music_gain_db}dB[mus];"
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
            fc = (
                f"[0:v]fps=30,format=yuv420p[vout];"
                f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo,channels=2,"
                f"volume={ambience_gain_db}dB, loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
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
