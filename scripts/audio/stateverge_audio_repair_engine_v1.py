#!/usr/bin/env python3
"""StateVerge Audio Repair Engine v1 — street / dash / ferry ambience transient repair.

Fail-open: always writes ``repair_report.json`` (and ``transient_events.json`` when
detection runs), exits 0. Optional scientific stack: **librosa** / **scipy** improve
detection; otherwise **numpy**-only or **wave** RMS paths are used (see JSON
``detection_mode`` / ``transient_events.json``).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import traceback
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PIPELINE_SCRIPT = _REPO_ROOT / "scripts" / "audio_separation_pipeline.py"

_SV_CACHE_PRIMARY = Path("/Volumes/SV_CACHE")
_FALLBACK_SV_CACHE = Path.home() / "StateVerge" / "_storage_fallback" / "sv_cache"

_STEM_PRIORITY = ("no_vocals", "accompaniment", "other", "ambience")

_FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
_FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"

_DEMUCS_MODEL = "htdemucs"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_file() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _which_ffmpeg() -> str | None:
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    p = os.environ.get("FFMPEG_BIN")
    return p if p and Path(p).is_file() else None


def _resolve_sv_cache_base() -> Path:
    for cand in (_SV_CACHE_PRIMARY, _FALLBACK_SV_CACHE):
        try:
            root = cand
            probe = root / "audio_repair" / ".write_probe"
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)  # type: ignore[arg-type]
            return root
        except OSError:
            continue
    fb = _FALLBACK_SV_CACHE
    fb.mkdir(parents=True, exist_ok=True)
    return fb


def _run_cmd(
    cmd: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except FileNotFoundError:
        return 127, "executable_not_found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    tail = ((r.stderr or "") + "\n" + (r.stdout or ""))[-12000:]
    return r.returncode, tail


def _ffmpeg_summary_from_cmd(cmd: list[str]) -> str:
    if not cmd:
        return "(empty)"
    if Path(cmd[0]).name == "ffmpeg" or "ffmpeg" in cmd[0]:
        parts: list[str] = ["ffmpeg"]
        i = 0
        while i < len(cmd):
            if cmd[i] == "-i" and i + 1 < len(cmd):
                parts += ["-i", "<path>"]
                i += 2
                continue
            if cmd[i] in ("-af", "-filter:a", "-filter_complex") and i + 1 < len(cmd):
                parts += [cmd[i], "<filter>"]
                i += 2
                continue
            if cmd[i].startswith("-"):
                parts.append(cmd[i])
            i += 1
        return " ".join(parts)
    if "demucs" in str(cmd[0]) or (len(cmd) > 1 and cmd[1] == "-m"):
        return "demucs: " + " ".join(str(x) for x in cmd[1:10] if not str(x).startswith("/"))
    return str(cmd[0]) + ": <args>"


def _find_stem_wav(search_root: Path) -> tuple[str | None, Path | None]:
    skip_dir_parts = {"debug", "__pycache__", ".git"}
    wavs: list[Path] = []
    try:
        for p in search_root.rglob("*.wav"):
            if not p.is_file():
                continue
            if any(part.lower() in skip_dir_parts for part in p.parts):
                continue
            wavs.append(p)
    except OSError:
        return None, None

    def stem_matches(path: Path, token: str) -> bool:
        t = token.lower()
        if path.stem.lower() == t:
            return True
        return any(part.lower() == t for part in path.parts)

    for token in _STEM_PRIORITY:
        hits = [p for p in wavs if stem_matches(p, token)]
        if hits:
            hits.sort(key=lambda x: (len(x.parts), str(x)))
            return token, hits[0]
    return None, None


def _read_pipeline_no_vocals(pipeline_out_dir: Path) -> Path | None:
    rep = pipeline_out_dir / "audio_separation_report.json"
    if not rep.is_file():
        return None
    try:
        data = json.loads(rep.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    p = data.get("no_vocals_path")
    if not p:
        return None
    path = Path(str(p))
    return path if path.is_file() else None


def _try_separation_pipeline(
    video_in: Path,
    pipeline_root: Path,
    *,
    timeout_sec: float,
    summary: list[str],
    warnings: list[str],
    errors: list[str],
) -> tuple[str | None, Path | None, bool]:
    if not _PIPELINE_SCRIPT.is_file():
        warnings.append("audio_separation_pipeline_missing")
        return None, None, False
    pipeline_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(_PIPELINE_SCRIPT),
        "--input",
        str(video_in),
        "--output-root",
        str(pipeline_root),
        "--mode",
        "no_vocals",
        "--fps",
        "30",
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd))
    rc, tail = _run_cmd(cmd, timeout=timeout_sec)
    if rc != 0:
        errors.append(f"pipeline_exit_{rc}")
        if tail.strip():
            warnings.append("pipeline_stderr_tail:" + tail.strip()[-1200:])
        return None, None, True
    stem_dir_name = video_in.stem
    pipeline_track_dir = pipeline_root / stem_dir_name
    nv = _read_pipeline_no_vocals(pipeline_track_dir)
    if nv and nv.is_file():
        return "no_vocals", nv, True
    tok, path = _find_stem_wav(pipeline_track_dir)
    if path:
        return tok, path, True
    errors.append("pipeline_ok_but_no_stem_wav")
    return None, None, True


def _try_demucs(
    wav_in: Path,
    demucs_parent: Path,
    *,
    timeout_sec: float,
    summary: list[str],
    warnings: list[str],
    errors: list[str],
) -> tuple[str | None, Path | None]:
    cmd = [
        sys.executable,
        "-m",
        "demucs",
        "--two-stems",
        "vocals",
        "--device",
        "cpu",
        "--segment",
        "7",
        "--shifts",
        "0",
        "-n",
        _DEMUCS_MODEL,
        "-o",
        str(demucs_parent),
        str(wav_in),
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd))
    rc, tail = _run_cmd(cmd, timeout=timeout_sec)
    if rc != 0:
        errors.append(f"demucs_exit_{rc}")
        if "No module named" in tail:
            errors.append("demucs_module_not_installed")
        return None, None
    tok, path = _find_stem_wav(demucs_parent)
    if path:
        return tok, path
    errors.append("demucs_ok_but_no_stem_wav")
    return None, None


def _extract_wav(
    video_in: Path,
    wav_out: Path,
    *,
    timeout_sec: float,
    summary: list[str],
    errors: list[str],
) -> bool:
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(video_in),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(wav_out),
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd))
    rc, _tail = _run_cmd(cmd, timeout=timeout_sec)
    if rc != 0 or not wav_out.is_file() or wav_out.stat().st_size < 1024:
        errors.append("extract_wav_failed")
        return False
    return True


def _duration_sec(path: Path) -> float:
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
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120.0, check=False)
        if r.returncode != 0:
            return 0.0
        return float((r.stdout or "").strip() or 0.0)
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0


def _measure_integrated_lufs(path: Path, *, timeout: float) -> float | None:
    """Best-effort integrated loudness (LUFS) via ffmpeg ebur128 logs."""
    cmd = [
        _FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path),
        "-af",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]
    rc, blob = _run_cmd(cmd, timeout=timeout)
    if rc not in (0, 234):  # 234 partial file ok for measurement
        pass
    m = re.search(r"Integrated loudness:\s*I:\s*([-0-9.]+)\s*LUFS", blob)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m2 = re.search(r"I:\s*([-0-9.]+)\s*LUFS", blob)
    if m2:
        try:
            return float(m2.group(1))
        except ValueError:
            return None
    return None


def _try_import_numpy() -> Any | None:
    try:
        import numpy as np  # type: ignore

        return np
    except ImportError:
        return None


def _load_audio_mono(path: Path, warnings: list[str]) -> tuple[int, Any, str]:
    """Return (sample_rate, float32_mono_numpy_or_empty, mode_label)."""
    np = _try_import_numpy()
    if np is None:
        warnings.append("numpy_not_available_minimal_path")
        return 44100, None, "stdlib_only"

    try:
        import soundfile as sf  # type: ignore

        data, sr = sf.read(str(path), always_2d=True, dtype="float32")
        mono = np.mean(data, axis=1).astype(np.float32)
        return int(sr), mono, "soundfile"
    except ImportError:
        pass
    except OSError as e:
        warnings.append(f"soundfile_read_failed:{e}")

    try:
        from scipy.io import wavfile  # type: ignore

        sr, data = wavfile.read(str(path))
        if data.dtype == np.int16:
            x = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            x = data.astype(np.float32) / 2147483648.0
        else:
            x = data.astype(np.float32)
        if x.ndim > 1:
            x = np.mean(x, axis=1)
        return int(sr), x.astype(np.float32), "scipy_wavfile"
    except ImportError:
        pass
    except OSError as e:
        warnings.append(f"scipy_wavfile_failed:{e}")

    try:
        with wave.open(str(path), "rb") as wf:
            sr = wf.getframerate()
            nch = wf.getnchannels()
            sw = wf.getsampwidth()
            nframes = wf.getnframes()
            raw = wf.readframes(nframes)
        if sw != 2:
            warnings.append("wave_only_16bit_pcm_supported")
            return sr, np.zeros(0, dtype=np.float32), "wave_unsupported_width"
        n = len(raw) // 2
        arr = struct.unpack("<" + "h" * n, raw)
        x = np.asarray(arr, dtype=np.float32) / 32768.0
        if nch == 2:
            x = (x[0::2] + x[1::2]) * 0.5
        return sr, x.astype(np.float32), "wave_pcm_s16le"
    except (wave.Error, OSError, struct.error) as e:
        warnings.append(f"wave_parse_failed:{e}")
        return 44100, np.zeros(0, dtype=np.float32), "wave_failed"


def _numpy_rms_frames(x: Any, frame: int, hop: int, np: Any) -> Any:
    n = max(0, (len(x) - frame) // hop + 1)
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        sl = x[i * hop : i * hop + frame]
        out[i] = float(np.sqrt(np.mean(sl * sl) + 1e-12))
    return out


def _numpy_hf_emphasis(x: Any, frame: int, hop: int, np: Any) -> Any:
    """High-frequency proxy: RMS of residual vs moving average (numpy-only)."""
    kernel = np.ones(9, dtype=np.float64) / 9.0
    pad = len(kernel) // 2
    xp = np.pad(x.astype(np.float64), (pad, pad), mode="reflect")
    low = np.convolve(xp, kernel, mode="valid")
    hf = x.astype(np.float64) - low[: len(x)]
    return _numpy_rms_frames(hf.astype(np.float32), frame, hop, np)


def _numpy_spectral_flux(x: Any, sr: int, frame: int, hop: int, np: Any) -> Any:
    n = max(0, (len(x) - frame) // hop + 1)
    win = np.hanning(frame).astype(np.float64)
    flux = np.zeros(n, dtype=np.float64)
    prev = None
    for i in range(n):
        sl = (x[i * hop : i * hop + frame].astype(np.float64)) * win
        mag = np.abs(np.fft.rfft(sl))
        if prev is not None:
            d = np.maximum(mag - prev, 0.0)
            flux[i] = float(np.sum(d))
        prev = mag
    return flux


def _zscore(a: Any, np: Any) -> Any:
    m = float(np.mean(a))
    s = float(np.std(a)) + 1e-9
    return (a - m) / s


def _detect_transients(
    sr: int,
    mono: Any,
    np: Any,
    warnings: list[str],
    _errors: list[str],
) -> tuple[list[dict[str, Any]], str]:
    """Return (events, detection_mode)."""
    if mono is None or len(mono) < sr // 4:
        warnings.append("audio_too_short_or_empty_for_detection")
        return [], "no_audio"

    y_full = np.asarray(mono, dtype=np.float32)
    score: Any | None = None
    times: Any | None = None
    mode = "numpy_rms_hf_proxy_spectral_flux"

    try:
        import librosa  # type: ignore

        y = y_full.copy()
        sr_l = int(sr)
        if sr_l != 22050:
            y = librosa.resample(y, orig_sr=sr_l, target_sr=22050)
            sr_l = 22050
        hop = 256
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
        onset = librosa.onset.onset_strength(y=y, sr=sr_l, hop_length=hop)
        stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
        flux = np.maximum(np.diff(stft, axis=1), 0.0).sum(axis=0)
        flux = np.concatenate([[0.0], flux])
        n = min(len(rms), len(onset), len(flux))
        rms = rms[:n]
        onset = onset[:n]
        flux = flux[:n]
        zr = (rms - np.median(rms)) / (np.median(np.abs(rms - np.median(rms))) + 1e-9)
        zo = (onset - np.median(onset)) / (np.median(np.abs(onset - np.median(onset))) + 1e-9)
        zf = (flux - np.median(flux)) / (np.median(np.abs(flux - np.median(flux))) + 1e-9)
        score = 0.35 * zr + 0.35 * zo + 0.3 * zf
        times = librosa.frames_to_time(np.arange(n), sr=sr_l, hop_length=hop)
        mode = "librosa_rms_onset_spectral_flux"
    except Exception as e:
        warnings.append(f"librosa_detection_failed:{type(e).__name__}")

    if score is None:
        try:
            from scipy import signal  # type: ignore

            y = y_full
            frame = 2048
            hop = 512
            rms = _numpy_rms_frames(y, frame, hop, np)
            sos_hp = signal.butter(4, min(3000.0, sr * 0.45), btype="high", fs=sr, output="sos")
            y_hp = signal.sosfiltfilt(sos_hp, y)
            hf = _numpy_rms_frames(y_hp.astype(np.float32), frame, hop, np)
            flux = _numpy_spectral_flux(y, sr, frame, hop, np)
            n = min(len(rms), len(hf), len(flux))
            rms = rms[:n]
            hf = hf[:n]
            flux = flux[:n]
            score = _zscore(rms, np) + _zscore(hf, np) + _zscore(flux, np)
            times = (np.arange(n) * hop + frame / 2) / float(sr)
            mode = "scipy_numpy_rms_hf_spectral_flux"
        except Exception as e:
            warnings.append(f"scipy_detection_failed:{type(e).__name__}")

    if score is None:
        y = y_full
        frame = 2048
        hop = 512
        rms = _numpy_rms_frames(y, frame, hop, np)
        hf = _numpy_hf_emphasis(y, frame, hop, np)
        flux = _numpy_spectral_flux(y, sr, frame, hop, np)
        n = min(len(rms), len(hf), len(flux))
        rms = rms[:n]
        hf = hf[:n]
        flux = flux[:n]
        score = _zscore(rms, np) + _zscore(hf, np) + _zscore(flux, np)
        times = (np.arange(n) * hop + frame / 2) / float(sr)
        mode = "numpy_rms_hf_proxy_spectral_flux"

    thr = float(np.percentile(score, 97.5) + 2.5 * np.median(np.abs(score - np.median(score))))
    peaks: list[tuple[float, float]] = []
    for i in range(1, len(score) - 1):
        if score[i] > thr and score[i] >= score[i - 1] and score[i] >= score[i + 1]:
            peaks.append((float(times[i]), float(score[i])))

    merged: list[tuple[float, float]] = []
    gap = 0.045
    for t, s in sorted(peaks, key=lambda x: x[0]):
        if merged and t - merged[-1][0] < gap:
            if s > merged[-1][1]:
                merged[-1] = (t, s)
            continue
        merged.append((t, s))

    events: list[dict[str, Any]] = []
    for t, s in merged:
        half = 0.085 + min(0.515, float(s) * 0.012)
        half = max(0.025, min(0.6, half))
        t0 = max(0.0, t - half)
        t1 = t + half
        dur = t1 - t0
        if dur < 0.05:
            pad = (0.05 - dur) * 0.5
            t0 = max(0.0, t0 - pad)
            t1 = t1 + pad
            dur = t1 - t0
        if dur > 1.2:
            t0 = max(0.0, t - 0.6)
            t1 = t + 0.6
        events.append({"t_start": round(t0, 4), "t_end": round(t1, 4), "peak_time": round(t, 4), "score": round(float(s), 4)})

    if not events and float(np.max(score)) > float(np.percentile(score, 90)):
        warnings.append("no_events_above_merge_threshold_relaxed_not_run")
    return events, mode


def _estimate_noise_floor_db(mono: Any, np: Any) -> float | None:
    if mono is None or len(mono) < 256:
        return None
    x = np.asarray(mono, dtype=np.float64)
    rms = _numpy_rms_frames(x.astype(np.float32), 2048, 512, np)
    floor = float(np.percentile(rms, 10))
    if floor <= 0:
        return None
    return float(20.0 * math.log10(floor + 1e-12))


def _count_clipping(mono: Any, np: Any) -> int:
    if mono is None or len(mono) < 1:
        return 0
    x = np.asarray(mono, dtype=np.float64)
    return int(np.sum(np.abs(x) >= 0.999))


def _ffmpeg_trim_wav(
    src: Path,
    dst: Path,
    t0: float,
    t1: float,
    *,
    timeout: float,
    summary: list[str],
    errors: list[str],
) -> bool:
    dur = max(0.001, t1 - t0)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(src),
        "-ss",
        f"{t0:.6f}",
        "-t",
        f"{dur:.6f}",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(dst),
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd))
    rc, _ = _run_cmd(cmd, timeout=timeout)
    if rc != 0 or not dst.is_file() or dst.stat().st_size < 64:
        errors.append(f"trim_failed_{t0:.3f}_{t1:.3f}")
        return False
    return True


def _repair_snippet_wav(
    snippet_in: Path,
    snippet_orig: Path,
    out_path: Path,
    *,
    repair_strength: float,
    timeout: float,
    summary: list[str],
    errors: list[str],
    warnings: list[str],
) -> bool:
    """Attenuate HF band + gentle dip; crossfade-weight remix with dry snippet (fail-open)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    g_eq = 6.0 + repair_strength * 6.0
    g_vol = 2.0 + repair_strength * 4.0
    fade_d = 0.04
    dur = _duration_sec(snippet_in)
    if dur <= 0:
        errors.append("snippet_zero_duration")
        return False
    st_out = max(0.0, dur - fade_d)
    af_proc = (
        f"equalizer=f=4000:width_type=o:width=2:g=-{g_eq:.2f},"
        f"equalizer=f=6500:width_type=o:width=2:g=-{g_eq:.2f},"
        f"volume=-{g_vol:.2f}dB,"
        f"afade=t=in:ss=0:d={fade_d:.3f},"
        f"afade=t=out:st={st_out:.4f}:d={fade_d:.3f}"
    )
    tmp_proc = out_path.parent / f"{out_path.stem}._proc_{uuid.uuid4().hex}.wav"
    cmd1 = [
        _FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(snippet_in),
        "-af",
        af_proc,
        str(tmp_proc),
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd1))
    rc1, _ = _run_cmd(cmd1, timeout=timeout)
    if rc1 != 0 or not tmp_proc.is_file():
        errors.append("repair_proc_snippet_failed")
        try:
            shutil.copy2(snippet_in, out_path)
            warnings.append("repair_fell_back_to_unprocessed_snippet")
            return True
        except OSError:
            return False

    w_rep = 0.75 + 0.15 * repair_strength
    w_dry = 1.0 - w_rep
    fc = (
        f"[0:a]volume={w_rep:.3f}[a0];"
        f"[1:a]volume={w_dry:.3f}[a1];"
        f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )
    cmd2 = [
        _FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(tmp_proc),
        "-i",
        str(snippet_orig),
        "-filter_complex",
        fc,
        "-map",
        "[aout]",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(out_path),
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd2))
    rc2, _ = _run_cmd(cmd2, timeout=timeout)
    try:
        tmp_proc.unlink(missing_ok=True)  # type: ignore[arg-type]
    except OSError:
        pass
    if rc2 != 0 or not out_path.is_file():
        errors.append("repair_amix_snippet_failed")
        try:
            shutil.copy2(snippet_in, out_path)
            warnings.append("repair_amix_fallback_copy_input")
            return True
        except OSError:
            return False
    return True


def _concat_wavs_concat_demuxer(
    paths: list[Path],
    out_wav: Path,
    *,
    timeout: float,
    summary: list[str],
    errors: list[str],
) -> bool:
    if not paths:
        errors.append("concat_empty_list")
        return False
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    parent = paths[0].parent.resolve()
    for p in paths:
        if p.parent.resolve() != parent:
            errors.append("concat_paths_not_same_directory")
            return False
    lst = parent / f"concat_list_{uuid.uuid4().hex}.txt"
    lines: list[str] = []
    for p in paths:
        lines.append(f"file '{p.name}'")
    try:
        lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        errors.append(f"concat_list_write_failed:{e}")
        return False
    cmd = [
        _FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lst),
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd))
    rc, _ = _run_cmd(cmd, timeout=timeout)
    try:
        lst.unlink(missing_ok=True)  # type: ignore[arg-type]
    except OSError:
        pass
    if rc != 0 or not out_wav.is_file() or out_wav.stat().st_size < 256:
        errors.append("concat_demuxer_failed")
        return False
    return True


def _global_audio_chain(
    in_wav: Path,
    out_wav: Path,
    *,
    lowpass_14k: bool,
    timeout: float,
    summary: list[str],
    errors: list[str],
) -> bool:
    parts = ["highpass=f=80"]
    if lowpass_14k:
        parts.append("lowpass=f=14000")
    parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    af = ",".join(parts)
    cmd = [
        _FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(in_wav),
        "-af",
        af,
        str(out_wav),
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd))
    rc, _ = _run_cmd(cmd, timeout=timeout)
    if rc != 0 or not out_wav.is_file() or out_wav.stat().st_size < 256:
        errors.append("global_chain_failed")
        return False
    return True


def _mux_cfr_h264_aac(
    video_in: Path,
    audio_wav: Path,
    mp4_out: Path,
    *,
    timeout_sec: float,
    summary: list[str],
    errors: list[str],
) -> bool:
    mp4_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = mp4_out.parent / f"{mp4_out.stem}._tmp_{os.getpid()}_{uuid.uuid4().hex}{mp4_out.suffix}"
    vf = "fps=30,format=yuv420p"
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
            str(video_in),
            "-i",
            str(audio_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            vf,
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
            "-shortest",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
        summary.append(f"ffmpeg: mux CFR30 {label}+aac (no -c:v copy)")
        rc, _tail = _run_cmd(cmd, timeout=timeout_sec)
        if rc == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
            try:
                tmp.replace(mp4_out)
            except OSError:
                shutil.copy2(tmp, mp4_out)
                try:
                    tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
                except OSError:
                    pass
            return True
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
    errors.append("mux_cfr_h264_failed")
    return False


def _symlink_or_copy(src: Path, dst: Path, warnings: list[str]) -> None:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        warnings.append(f"user_output_dir_mkdir_failed:{e}")
        return
    try:
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
    except OSError:
        pass
    try:
        rel = os.path.relpath(src, start=dst.parent)
        os.symlink(rel, dst)
    except OSError as e:
        warnings.append(f"user_output_symlink_failed:{e};using_copy")
        try:
            shutil.copy2(src, dst)
        except OSError as e2:
            warnings.append(f"user_output_copy_failed:{e2}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-video", required=True, help="Source video path.")
    ap.add_argument(
        "--output-video",
        default=None,
        help="Optional user-visible output; canonical file still written under SV_CACHE.",
    )
    ap.add_argument(
        "--repair-strength",
        type=float,
        default=0.72,
        help="0–1 attenuation aggressiveness for transient windows (default 0.72).",
    )
    ap.add_argument(
        "--lowpass-14k",
        action="store_true",
        help="If set, apply 14 kHz lowpass before loudnorm (default off to preserve air).",
    )
    args = ap.parse_args()

    cache_base = _resolve_sv_cache_base()
    reports_dir = cache_base / "audio_repair" / "reports"
    repair_out_dir = cache_base / "audio_repair"
    report_path = reports_dir / "repair_report.json"
    events_path = reports_dir / "transient_events.json"

    video_in = Path(args.input_video).expanduser().resolve()
    ts = _timestamp_file()
    canonical_name = f"clean_real_sound_repaired_{ts}.mp4"
    canonical_out = repair_out_dir / canonical_name

    repair_strength = max(0.0, min(1.0, float(args.repair_strength)))

    payload: dict[str, Any] = {
        "input_video": str(video_in),
        "output_video": str(canonical_out),
        "transient_count": 0,
        "repaired_segments": [],
        "clipping_events": 0,
        "noise_floor": None,
        "loudness_before": None,
        "loudness_after": None,
        "used_demucs_model": None,
        "repair_strength": repair_strength,
        "detection_mode": None,
        "transient_events_path": str(events_path),
        "ffmpeg_commands_summary": [],
        "warnings": [],
        "errors": [],
        "timestamp": _utc_now_iso(),
        "success": False,
    }

    work_root: Path | None = None
    try:
        if not _which_ffmpeg():
            payload["errors"].append("ffmpeg_not_found")
            payload["warnings"].append("set_FFMPEG_BIN_or_install_ffmpeg")
            _write_json(report_path, payload)
            _write_json(events_path, {"events": [], "detection_mode": "skipped"})
            return 0

        if not video_in.is_file():
            payload["errors"].append("input_missing_or_not_file")
            _write_json(report_path, payload)
            _write_json(events_path, {"events": [], "detection_mode": "skipped"})
            return 0

        work_root = cache_base / "audio_repair" / "tmp" / f"audio_repair_{video_in.stem}_{uuid.uuid4().hex}"
        work_root.mkdir(parents=True, exist_ok=True)
        wav_path = work_root / "extracted.wav"
        stem_work = work_root / "stem.wav"
        pipeline_bundle = work_root / "pipeline_bundle"
        demucs_parent = work_root / "demucs_out"
        segments_dir = work_root / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        timeout_extract = min(14400.0, 7200.0)
        timeout_demucs = 14400.0
        timeout_short = min(3600.0, 1800.0)
        timeout_mux = 14400.0

        if not _extract_wav(
            video_in,
            wav_path,
            timeout_sec=timeout_extract,
            summary=payload["ffmpeg_commands_summary"],
            errors=payload["errors"],
        ):
            _write_json(report_path, payload)
            _write_json(events_path, {"events": [], "detection_mode": "skipped"})
            return 0

        stem_token: str | None = None
        stem_path: Path | None = None
        separation_backend = "none"
        pipeline_attempted = False

        if _PIPELINE_SCRIPT.is_file():
            st, sp, attempted = _try_separation_pipeline(
                video_in,
                pipeline_bundle,
                timeout_sec=timeout_demucs,
                summary=payload["ffmpeg_commands_summary"],
                warnings=payload["warnings"],
                errors=payload["errors"],
            )
            pipeline_attempted = attempted
            if sp:
                stem_token, stem_path = st, sp
                separation_backend = "pipeline"
                shutil.copy2(stem_path, stem_work)
                stem_path = stem_work

        if not stem_path:
            if pipeline_attempted:
                payload["warnings"].append("pipeline_failed_trying_demucs")
            st, sp = _try_demucs(
                wav_path,
                demucs_parent,
                timeout_sec=timeout_demucs,
                summary=payload["ffmpeg_commands_summary"],
                warnings=payload["warnings"],
                errors=payload["errors"],
            )
            if sp:
                stem_token, stem_path = st, sp
                separation_backend = "demucs"
                payload["used_demucs_model"] = _DEMUCS_MODEL
                shutil.copy2(stem_path, stem_work)
                stem_path = stem_work
            else:
                payload["warnings"].append("separation_failed_using_full_mix_for_detection_and_repair")
                stem_path = wav_path
                stem_token = "full_mix_fallback"
                payload["used_demucs_model"] = None
        else:
            if separation_backend == "demucs":
                payload["used_demucs_model"] = _DEMUCS_MODEL

        analyze_path = stem_path
        sr, mono, load_mode = _load_audio_mono(analyze_path, payload["warnings"])
        np = _try_import_numpy()
        if np is None:
            payload["detection_mode"] = "numpy_unavailable"
            events: list[dict[str, Any]] = []
            payload["warnings"].append("transient_detection_skipped_without_numpy")
        else:
            events, det_mode = _detect_transients(sr, mono, np, payload["warnings"], payload["errors"])
            payload["detection_mode"] = det_mode
            payload["transient_count"] = len(events)
            if mono is not None and len(mono):
                payload["noise_floor"] = _estimate_noise_floor_db(mono, np)
                payload["clipping_events"] = _count_clipping(mono, np)

        events_doc = {
            "detection_mode": payload.get("detection_mode"),
            "sample_rate_analyze": sr,
            "audio_load_mode": load_mode,
            "events": events,
        }
        _write_json(events_path, events_doc)

        loud_before = _measure_integrated_lufs(analyze_path, timeout=timeout_short)
        payload["loudness_before"] = loud_before

        duration = _duration_sec(stem_path)
        if duration <= 0:
            payload["errors"].append("stem_duration_zero")
            _write_json(report_path, payload)
            return 0

        sorted_ev = sorted(events, key=lambda e: float(e["t_start"]))
        pieces: list[Path] = []
        repaired_log: list[dict[str, Any]] = []
        cursor = 0.0
        seg_idx = 0
        per_seg_timeout = min(600.0, max(60.0, duration * 0.5))

        for ev in sorted_ev:
            t0 = float(ev["t_start"])
            t1 = float(ev["t_end"])
            t0 = max(0.0, min(t0, duration))
            t1 = max(t0 + 0.05, min(t1, duration))
            if t1 - t0 > 1.2:
                mid = (t0 + t1) * 0.5
                t0, t1 = mid - 0.6, mid + 0.6
                t0 = max(0.0, t0)
                t1 = min(duration, t1)

            if cursor < t0 - 1e-4:
                p = segments_dir / f"seg_{seg_idx:05d}_copy.wav"
                seg_idx += 1
                if _ffmpeg_trim_wav(
                    stem_path,
                    p,
                    cursor,
                    t0,
                    timeout=per_seg_timeout,
                    summary=payload["ffmpeg_commands_summary"],
                    errors=payload["errors"],
                ):
                    pieces.append(p)

            dry = segments_dir / f"seg_{seg_idx:05d}_evt_dry.wav"
            seg_idx += 1
            if not _ffmpeg_trim_wav(
                stem_path,
                dry,
                t0,
                t1,
                timeout=per_seg_timeout,
                summary=payload["ffmpeg_commands_summary"],
                errors=payload["errors"],
            ):
                cursor = t1
                continue
            rep = segments_dir / f"seg_{seg_idx:05d}_evt_rep.wav"
            seg_idx += 1
            ok = _repair_snippet_wav(
                dry,
                dry,
                rep,
                repair_strength=repair_strength,
                timeout=per_seg_timeout,
                summary=payload["ffmpeg_commands_summary"],
                errors=payload["errors"],
                warnings=payload["warnings"],
            )
            if ok:
                pieces.append(rep)
                repaired_log.append({"t_start": t0, "t_end": t1, "ok": True})
            else:
                payload["warnings"].append(f"repair_segment_failed_using_gap_fill_{t0:.3f}")
                pieces.append(dry)
                repaired_log.append({"t_start": t0, "t_end": t1, "ok": False})
            cursor = t1

        if cursor < duration - 1e-4:
            p = segments_dir / f"seg_{seg_idx:05d}_tail.wav"
            if _ffmpeg_trim_wav(
                stem_path,
                p,
                cursor,
                duration,
                timeout=per_seg_timeout,
                summary=payload["ffmpeg_commands_summary"],
                errors=payload["errors"],
            ):
                pieces.append(p)

        stitched = work_root / "stitched.wav"
        if not pieces:
            try:
                shutil.copy2(stem_path, stitched)
                payload["warnings"].append("no_segment_pieces_copied_stem")
            except OSError as e:
                payload["errors"].append(f"stitch_copy_failed:{e}")
                _write_json(report_path, payload)
                return 0
        elif len(pieces) == 1:
            try:
                shutil.copy2(pieces[0], stitched)
            except OSError as e:
                payload["errors"].append(f"single_piece_copy_failed:{e}")
                _write_json(report_path, payload)
                return 0
        else:
            if not _concat_wavs_concat_demuxer(
                pieces,
                stitched,
                timeout=min(14400.0, duration * 4.0 + 120.0),
                summary=payload["ffmpeg_commands_summary"],
                errors=payload["errors"],
            ):
                try:
                    shutil.copy2(stem_path, stitched)
                    payload["warnings"].append("concat_failed_copied_stem")
                except OSError:
                    pass

        global_out = work_root / "global_chain.wav"
        if not _global_audio_chain(
            stitched,
            global_out,
            lowpass_14k=bool(args.lowpass_14k),
            timeout=min(14400.0, duration * 2.0 + 300.0),
            summary=payload["ffmpeg_commands_summary"],
            errors=payload["errors"],
        ):
            try:
                shutil.copy2(stitched, global_out)
                payload["warnings"].append("global_chain_failed_copied_stitched")
            except OSError as e:
                payload["errors"].append(f"global_chain_fallback_failed:{e}")
                _write_json(report_path, payload)
                return 0

        loud_after = _measure_integrated_lufs(global_out, timeout=timeout_short)
        payload["loudness_after"] = loud_after
        payload["repaired_segments"] = repaired_log

        if not _mux_cfr_h264_aac(
            video_in,
            global_out,
            canonical_out,
            timeout_sec=timeout_mux,
            summary=payload["ffmpeg_commands_summary"],
            errors=payload["errors"],
        ):
            _write_json(report_path, payload)
            return 0

        if args.output_video:
            user_out = Path(args.output_video).expanduser().resolve()
            if user_out.resolve() != canonical_out.resolve():
                _symlink_or_copy(canonical_out, user_out, payload["warnings"])
                payload["warnings"].append(f"user_output_target:{user_out}")

        payload["success"] = True

    except Exception as e:
        payload["errors"].append(f"unexpected:{e}")
        payload["warnings"].append("traceback_tail:" + traceback.format_exc()[-4000:])
    finally:
        _write_json(report_path, payload)
        if work_root is not None:
            shutil.rmtree(work_root, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
