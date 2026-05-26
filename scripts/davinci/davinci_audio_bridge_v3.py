#!/usr/bin/env python3
"""StateVerge DaVinci API bridge v3 — v2 flow + ``--audio-preset`` + JSON ``intended_audio_chain``.

v2-compatible import → timeline → AddRenderJob; with ``--start-render``, StartRendering
and poll ``IsRenderingInProgress`` (max 2h), atomic JSON updates every ~5s.

Presets ``ferry_ambient_v1`` / ``driving_ambient_v1`` only annotate JSON with suggested EQ;
they do **not** toggle AI Voice Isolation or apply EQ inside Resolve (same API surface as v2).

Preset ``ferry_ambient_v2`` additionally attempts a fail-open Fairlight chain (EQ, noise
reduction, loudness, limiter) across all audio tracks via documented and speculative Resolve
scripting calls before render. AI Voice Isolation is never enabled by this script.

StartRendering compatibility (first non-raising, non-False return wins; see warnings):
``project.StartRendering([job_id])``, ``project.StartRendering(job_id)`` (str/int),
then the same two forms on ``resolve`` if ``resolve.StartRendering`` exists.

Stdlib + ffprobe when available. Fail-open: exits 0; JSON + stderr diagnostics; stdout is
exactly three flag lines at end.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# --- sys.path: Blackmagic Resolve scripting modules (system first, then optional user) ---
_RESOLVE_MODULES_SYSTEM = Path(
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
)
_BMD_MODULES_SYSTEM = Path(
    "/Library/Application Support/Blackmagic Design/Developer/Scripting/Modules"
)
_RESOLVE_MODULES_USER = Path.home() / (
    "Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
)
_BMD_MODULES_USER = Path.home() / (
    "Library/Application Support/Blackmagic Design/Developer/Scripting/Modules"
)

for _p in (
    _BMD_MODULES_USER,
    _RESOLVE_MODULES_USER,
    _BMD_MODULES_SYSTEM,
    _RESOLVE_MODULES_SYSTEM,
):
    if _p.is_dir():
        _s = str(_p)
        if _s not in sys.path:
            sys.path.insert(0, _s)

PRIMARY_JSON = Path("/Volumes/SV_CACHE/davinci/logs/davinci_audio_bridge_v3.json")
FALLBACK_JSON = Path(
    "/Users/ziweizhang/StateVerge/_storage_fallback/sv_cache/davinci/logs/"
    "davinci_audio_bridge_v3.json"
)
PRIMARY_OUTPUT_DIR = Path("/Volumes/SV_CACHE/davinci/audio_exports")
FALLBACK_OUTPUT_DIR = Path(
    "/Users/ziweizhang/StateVerge/_storage_fallback/sv_cache/davinci/audio_exports"
)

AUDIO_PRESET_CHOICES = (
    "ferry_ambient_v1",
    "ferry_ambient_v2",
    "driving_ambient_v1",
    "raw_passthrough",
)

# --- ferry_ambient_v2 targets (JSON + Fairlight attempt metadata) ---
FERRY_AMBIENT_V2_EQ_PROFILE = (
    "HP 90 Hz; notch 3500 Hz -4 dB (Q~8); high shelf 6000 Hz -3 dB; low-mid bell 240 Hz -2 dB"
)
FERRY_AMBIENT_V2_NR_STRENGTH = 28
FERRY_AMBIENT_V2_TARGET_LUFS = -14.0
FERRY_AMBIENT_V2_TARGET_TRUE_PEAK = -1.5

SEARCH_ROOTS: tuple[Path, ...] = (
    Path("/Volumes/SV_TRANSFER/00_INBOX/iphone"),
    Path("/Volumes/SV_CACHE/inbox"),
    Path("/Volumes/SV_TRANSFER/ready_to_upload/nyc_long_clips"),
)
VIDEO_EXTS = frozenset({".mp4", ".mov", ".m4v"})
EXCLUDE_PATH_SUBSTR = (
    "video916",
    "picture916",
    "picture169",
    "shorts",
    "_rejected_long_outputs",
)
KEYWORDS = ("ferry", "drive", "driving", "nyc", "img")
MAX_WALK_FILES_PER_ROOT = 4000

PROJECT_NAME = "StateVerge_DaVinci_Audio_Test"

RENDER_POLL_SEC = 5.0
RENDER_MAX_WAIT_SEC = 7200
MIN_OUTPUT_BYTES = 1024 * 1024
MIN_OUTPUT_DURATION_SEC = 5.0


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _local_timestamp_filename() -> str:
    """Local wall clock for export basename (YYYYMMDD_HHMMSS)."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _log_err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _intended_audio_chain_for_preset(preset: str) -> dict[str, Any]:
    """Structured EQ suggestions only (not applied in Resolve)."""
    if preset == "ferry_ambient_v1":
        return {
            "eq_suggestions": [
                {"type": "highpass", "freq_hz": 90},
                {"type": "band", "freq_hz": 3500, "gain_db": -2},
                {"type": "band", "freq_hz": 6000, "gain_db": -3},
            ],
            "notes": (
                "ferry_ambient_v1: suggested chain for review; "
                "no auto Voice Isolation or EQ in Resolve from this bridge."
            ),
        }
    if preset == "driving_ambient_v1":
        return {
            "eq_suggestions": [
                {"type": "highpass", "freq_hz": 80},
                {"type": "band", "freq_hz": 3000, "gain_db": -1.5},
                {"type": "band", "freq_hz": 6000, "gain_db": -2},
            ],
            "notes": (
                "driving_ambient_v1: suggested chain for review; "
                "no auto Voice Isolation or EQ in Resolve from this bridge."
            ),
        }
    if preset == "ferry_ambient_v2":
        return {
            "eq_suggestions": [
                {"type": "highpass", "freq_hz": 90},
                {"type": "notch", "freq_hz": 3500, "gain_db": -4, "q": 8},
                {"type": "highshelf", "freq_hz": 6000, "gain_db": -3},
                {"type": "bell", "freq_hz": 240, "gain_db": -2},
            ],
            "notes": (
                "ferry_ambient_v2: Fairlight auto-chain attempted via scripting before render; "
                "targets HP 90 Hz, notch 3500 Hz -4 dB (Q~8), high shelf 6 kHz -3 dB, "
                "bell 240 Hz -2 dB; noise reduction strength ~28 (moderate); "
                "loudness targets -14 LUFS integrated / -1.5 dB true peak; limiter for transients. "
                "See applied_audio_chain in JSON. AI Voice Isolation is not used."
            ),
        }
    # raw_passthrough — minimal / empty chain
    return {"eq_suggestions": [], "notes": "raw_passthrough: no suggested EQ beyond v2 flow."}


def _manual_review_required_for_preset(preset: str) -> bool:
    return preset != "raw_passthrough"


def _manual_review_note_for_preset(preset: str) -> str | None:
    if preset == "ferry_ambient_v2":
        return "auto chain attempted; verify Fairlight chain and levels in Resolve."
    return None


def _chain_step(attempted: bool, applied: bool, detail: str) -> dict[str, Any]:
    return {"attempted": attempted, "applied": applied, "detail": detail}


def _empty_applied_audio_chain() -> dict[str, Any]:
    empty = _chain_step(False, False, "not run")
    return {
        "eq": empty.copy(),
        "noise_reduction": empty.copy(),
        "loudness": empty.copy(),
        "limiter": empty.copy(),
    }


def _normalize_track_item_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if x is not None]
    if isinstance(raw, dict):
        return [x for x in raw.values() if x is not None]
    return []


def _get_items_in_audio_track(timeline: Any, track_index: int, warnings: list[str]) -> list[Any]:
    for label, getter in (
        ("GetItemListInTrack", lambda: timeline.GetItemListInTrack("audio", track_index)),
        ("GetItemsInTrack", lambda: timeline.GetItemsInTrack("audio", track_index)),
    ):
        if not hasattr(timeline, label):
            continue
        try:
            raw = getter()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{label}(audio,{track_index}) failed: {exc}")
            continue
        items = _normalize_track_item_list(raw)
        if items or raw is not None:
            return items
    return []


def _call_if_callable(obj: Any, name: str, args: tuple[Any, ...], warnings: list[str], ctx: str) -> Any:
    if obj is None or not hasattr(obj, name):
        return _MISSING
    fn = getattr(obj, name)
    if not callable(fn):
        return _MISSING
    try:
        return fn(*args)
    except TypeError:
        return _MISSING
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{ctx} {name}{args!r}: {exc}")
        return _MISSING


_MISSING = object()

_ADD_FX_METHODS = (
    "AddFairlightEffect",
    "AddFairlightFX",
    "InsertFairlightEffect",
    "AddFairlightPlugin",
    "AddAudioEffect",
    "AddAudioPluginEffect",
    "AddPlugin",
    "InsertPlugin",
)

_TIMELINE_TRACK_FX_METHODS = (
    "AddFairlightTrackEffect",
    "InsertFairlightTrackEffect",
    "AddTrackFairlightEffect",
    "SetFairlightTrackEffect",
)


def _truthy_effect_return(ret: Any) -> bool:
    if ret is True:
        return True
    if ret is False or ret is None:
        return False
    # Some APIs return an effect handle object
    return ret is not _MISSING


def _try_plugin_names_on_obj(
    target: Any,
    plugin_names: tuple[str, ...],
    warnings: list[str],
    ctx: str,
) -> bool:
    for meth in _ADD_FX_METHODS:
        for pname in plugin_names:
            for args in ((pname,), (pname, 0), (pname, "pre")):
                ret = _call_if_callable(target, meth, args, warnings, ctx)
                if ret is _MISSING:
                    continue
                if _truthy_effect_return(ret):
                    return True
    return False


def _try_plugin_on_timeline_track(
    timeline: Any,
    track_index: int,
    plugin_names: tuple[str, ...],
    warnings: list[str],
) -> bool:
    for meth in _TIMELINE_TRACK_FX_METHODS:
        for pname in plugin_names:
            for args in ((track_index, pname), ("audio", track_index, pname), (pname, track_index)):
                ret = _call_if_callable(timeline, meth, args, warnings, f"timeline[{meth}]")
                if ret is not _MISSING and _truthy_effect_return(ret):
                    return True
    track_obj = _call_if_callable(
        timeline, "GetTrack", ("audio", track_index), warnings, "timeline.GetTrack"
    )
    if track_obj is not _MISSING and track_obj is not None:
        if _try_plugin_names_on_obj(
            track_obj, plugin_names, warnings, f"trackObj audio#{track_index}"
        ):
            return True
    return False


def _try_configure_effect_param(
    item: Any,
    param_specs: list[tuple[str, Any]],
    warnings: list[str],
    ctx: str,
) -> bool:
    """Try common setter names for Fairlight / plugin parameters (best-effort)."""
    setter_names = (
        "SetFairlightEffectParameter",
        "SetFairlightPluginParameter",
        "SetPluginParameter",
        "SetEffectParameter",
        "SetParameter",
        "SetControl",
    )
    any_ok = False
    for setter in setter_names:
        if not hasattr(item, setter):
            continue
        fn = getattr(item, setter)
        if not callable(fn):
            continue
        for key, val in param_specs:
            try:
                for args in ((key, val), (0, key, val), ("", key, val)):
                    try:
                        r = fn(*args)
                    except TypeError:
                        continue
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"{ctx} {setter}{args!r}: {exc}")
                        break
                    if r is True:
                        any_ok = True
                        break
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{ctx} {setter} loop: {exc}")
    return any_ok


def _apply_nr_strength_on_items(
    items: list[Any],
    strength: int,
    warnings: list[str],
    ctx: str,
) -> bool:
    keys_float = (
        "Reduction",
        "reduction",
        "Amount",
        "amount",
        "Strength",
        "strength",
        "NoiseReduction",
        "noise reduction",
    )
    any_ok = False
    for it in items:
        for k in keys_float:
            if _try_configure_effect_param(it, [(k, float(strength))], warnings, ctx):
                any_ok = True
    return any_ok


def _apply_loudness_targets_on_items(
    items: list[Any],
    lufs: float,
    true_peak: float,
    warnings: list[str],
    ctx: str,
) -> bool:
    specs: list[tuple[str, Any]] = [
        ("Integrated", lufs),
        ("integrated", lufs),
        ("TargetLoudness", lufs),
        ("targetLoudness", lufs),
        ("LUFS", lufs),
        ("Program Loudness", lufs),
        ("TruePeak", true_peak),
        ("truePeak", true_peak),
        ("True Peak", true_peak),
        ("Ceiling", true_peak),
    ]
    any_ok = False
    for it in items:
        if _try_configure_effect_param(it, specs, warnings, ctx):
            any_ok = True
    return any_ok


_EQ_PLUGIN_NAMES = (
    "Equalizer",
    "Fairlight EQ",
    "Fairlight Equalizer",
    "EQ",
    "FairlightFX EQ",
)
_NR_PLUGIN_NAMES = (
    "Noise Reduction",
    "Fairlight Noise Reduction",
    "Denoiser",
    "Fairlight Denoiser",
    "NoiseReduction",
)
_LOUDNESS_PLUGIN_NAMES = (
    "Loudness Normalizer",
    "Fairlight Loudness Normalizer",
    "Loudness",
    "Fairlight Loudness",
    "Loudness Meter",
    "FairlightFX Loudness",
)
_LIMITER_PLUGIN_NAMES = (
    "Limiter",
    "Fairlight Limiter",
    "FairlightFX Limiter",
    "Brick Wall Limiter",
)


def _apply_ferry_ambient_v2_fairlight_chain(
    resolve: Any,
    project: Any,
    timeline: Any,
    warnings: list[str],
    errors: list[str],
) -> dict[str, Any]:
    """Fail-open Fairlight attempts for ferry_ambient_v2 (all audio tracks)."""
    out = _empty_applied_audio_chain()
    detail_notes: list[str] = []

    try:
        if resolve is not None and hasattr(resolve, "OpenPage"):
            resolve.OpenPage("fairlight")
            detail_notes.append("OpenPage(fairlight) OK")
    except Exception as exc:  # noqa: BLE001
        msg = f"OpenPage(fairlight) failed: {exc}"
        warnings.append(msg)
        errors.append(msg)

    try:
        project.SetCurrentTimeline(timeline)
    except Exception as exc:  # noqa: BLE001
        msg = f"SetCurrentTimeline before Fairlight chain failed: {exc}"
        warnings.append(msg)
        errors.append(msg)

    # Optional newer API: timeline-level Fairlight preset (not used for custom bands).
    for meth in ("ApplyFairlightPreset",):
        if hasattr(timeline, meth):
            detail_notes.append(f"timeline.{meth} exists (not invoked; custom v2 chain)")

    n_audio = 0
    try:
        n_audio = int(timeline.GetTrackCount("audio"))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"GetTrackCount(audio) failed: {exc}")
        n_audio = 0

    if n_audio <= 0:
        for key in out:
            out[key] = _chain_step(True, False, "no audio tracks on timeline")
        out["eq"]["detail"] = "no audio tracks; EQ not applied"
        warnings.append("ferry_ambient_v2 Fairlight: timeline has no audio tracks.")
        return out

    # --- EQ ---
    eq_applied_any = False
    eq_attempted = True
    for ti in range(1, n_audio + 1):
        items = _get_items_in_audio_track(timeline, ti, warnings)
        ctx = f"A{ti}"
        if _try_plugin_on_timeline_track(timeline, ti, _EQ_PLUGIN_NAMES, warnings):
            eq_applied_any = True
        for it in items:
            if _try_plugin_names_on_obj(it, _EQ_PLUGIN_NAMES, warnings, ctx):
                eq_applied_any = True
            # Speculative EQ band parameters (Resolve scripting rarely exposes these).
            band_specs: list[tuple[str, Any]] = [
                ("HighPassFreq", 90.0),
                ("highpassFreq", 90.0),
                ("HPF", 90.0),
                ("Band2Freq", 3500.0),
                ("Band2Gain", -4.0),
                ("Band2Q", 8.0),
                ("HighShelfFreq", 6000.0),
                ("HighShelfGain", -3.0),
                ("LowMidFreq", 240.0),
                ("LowMidGain", -2.0),
            ]
            if _try_configure_effect_param(it, band_specs, warnings, ctx):
                eq_applied_any = True
    out["eq"] = _chain_step(
        eq_attempted,
        eq_applied_any,
        (
            f"tried AddFairlight*/track methods on {n_audio} audio track(s); "
            f"target bands per eq_profile; plugin attach success={eq_applied_any}. "
            + ("; ".join(detail_notes) if detail_notes else "")
        ).strip(),
    )

    # --- Noise reduction ---
    nr_applied_any = False
    for ti in range(1, n_audio + 1):
        items = _get_items_in_audio_track(timeline, ti, warnings)
        ctx = f"A{ti}"
        if _try_plugin_on_timeline_track(timeline, ti, _NR_PLUGIN_NAMES, warnings):
            nr_applied_any = True
        for it in items:
            if _try_plugin_names_on_obj(it, _NR_PLUGIN_NAMES, warnings, ctx):
                nr_applied_any = True
        if _apply_nr_strength_on_items(
            items, FERRY_AMBIENT_V2_NR_STRENGTH, warnings, f"{ctx} NR"
        ):
            nr_applied_any = True
    out["noise_reduction"] = _chain_step(
        True,
        nr_applied_any,
        (
            f"moderate NR target strength={FERRY_AMBIENT_V2_NR_STRENGTH} "
            f"(plugin attach success={nr_applied_any}); per-clip parameter set best-effort."
        ),
    )

    # --- Loudness ---
    loud_applied_any = False
    for ti in range(1, n_audio + 1):
        items = _get_items_in_audio_track(timeline, ti, warnings)
        ctx = f"A{ti}"
        if _try_plugin_on_timeline_track(timeline, ti, _LOUDNESS_PLUGIN_NAMES, warnings):
            loud_applied_any = True
        for it in items:
            if _try_plugin_names_on_obj(it, _LOUDNESS_PLUGIN_NAMES, warnings, ctx):
                loud_applied_any = True
        if _apply_loudness_targets_on_items(
            items,
            FERRY_AMBIENT_V2_TARGET_LUFS,
            FERRY_AMBIENT_V2_TARGET_TRUE_PEAK,
            warnings,
            f"{ctx} loudness",
        ):
            loud_applied_any = True
    out["loudness"] = _chain_step(
        True,
        loud_applied_any,
        (
            f"targets integrated {FERRY_AMBIENT_V2_TARGET_LUFS} LUFS, "
            f"true peak {FERRY_AMBIENT_V2_TARGET_TRUE_PEAK} dB; "
            f"plugin attach success={loud_applied_any}."
        ),
    )

    # --- Limiter ---
    lim_applied_any = False
    for ti in range(1, n_audio + 1):
        items = _get_items_in_audio_track(timeline, ti, warnings)
        ctx = f"A{ti}"
        if _try_plugin_on_timeline_track(timeline, ti, _LIMITER_PLUGIN_NAMES, warnings):
            lim_applied_any = True
        for it in items:
            if _try_plugin_names_on_obj(it, _LIMITER_PLUGIN_NAMES, warnings, ctx):
                lim_applied_any = True
    out["limiter"] = _chain_step(
        True,
        lim_applied_any,
        f"transient catch-up limiter; plugin attach success={lim_applied_any}.",
    )

    return out


def _dir_writable(dir_path: Path) -> bool:
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    try:
        with tempfile.NamedTemporaryFile(
            dir=dir_path, prefix=".sv_bridge_probe_", delete=True
        ) as fh:
            fh.write(b"ok")
            fh.flush()
    except OSError:
        return False
    return True


def _choose_json_path(warnings: list[str]) -> Path:
    parent = PRIMARY_JSON.parent
    if _dir_writable(parent):
        return PRIMARY_JSON
    warnings.append(
        f"Primary JSON dir not writable, using fallback: {FALLBACK_JSON.parent}"
    )
    return FALLBACK_JSON


def _choose_output_dir(warnings: list[str]) -> Path:
    if _dir_writable(PRIMARY_OUTPUT_DIR):
        return PRIMARY_OUTPUT_DIR
    warnings.append(
        f"Primary audio export dir not writable ({PRIMARY_OUTPUT_DIR}), using fallback."
    )
    try:
        FALLBACK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warnings.append(f"Fallback output mkdir failed: {exc}")
    return FALLBACK_OUTPUT_DIR


def _ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def _ffprobe_video(path: Path) -> dict[str, Any] | None:
    bin_ff = _ffprobe_bin()
    if not bin_ff:
        return None
    cmd = [
        bin_ff,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    streams = data.get("streams") or []
    w = h = None
    if streams and isinstance(streams[0], dict):
        wh = streams[0].get("width")
        ht = streams[0].get("height")
        if wh is not None and ht is not None:
            try:
                w, h = int(wh), int(ht)
            except (TypeError, ValueError):
                pass
    dur_s: float | None = None
    fmt = data.get("format") or {}
    d_raw = fmt.get("duration")
    if d_raw is not None:
        try:
            dur_s = float(d_raw)
        except (TypeError, ValueError):
            dur_s = None
    if dur_s is None or dur_s <= 0 or w is None or h is None or w <= 0 or h <= 0:
        return None
    return {"width": w, "height": h, "duration": dur_s}


def _ffprobe_duration(path: Path) -> float | None:
    """Container duration (seconds) or None."""
    bin_ff = _ffprobe_bin()
    if not bin_ff:
        return None
    cmd = [
        bin_ff,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        return float(proc.stdout.strip())
    except (TypeError, ValueError):
        return None


def _should_exclude_path(p: Path) -> bool:
    s = str(p).lower()
    for sub in EXCLUDE_PATH_SUBSTR:
        if sub.lower() in s:
            return True
    name = p.name
    if name.startswith("._"):
        return True
    return False


def _is_video_ext(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXTS


def _score_candidate(path: Path, probe: dict[str, Any] | None) -> tuple[float, str]:
    if probe is None:
        return 0.0, "no_ffprobe_data"

    w = int(probe["width"])
    h = int(probe["height"])
    d = float(probe["duration"])
    ratio = w / max(h, 1)

    score = 0.0
    bits: list[str] = []

    near_169 = w >= h and abs(ratio - (16.0 / 9.0)) < 0.02
    wide_landscape = w > h
    if near_169:
        score += 5000.0
        bits.append("16:9_landscape")
    elif w >= h and wide_landscape:
        score += 2000.0
        bits.append("landscape_wh")
    elif w >= h:
        score += 800.0
        bits.append("landscape_or_square")

    if 60.0 <= d <= 300.0:
        score += 2000.0
        bits.append("duration_60_300")
    elif d > 0:
        dist = min(abs(d - 60.0), abs(d - 300.0), abs(d - 180.0))
        score += max(0.0, 500.0 - dist)
        bits.append(f"duration_near_{int(d)}s")

    hay = (str(path).lower() + path.name.lower())
    kw_hits = [k for k in KEYWORDS if k.lower() in hay]
    if kw_hits:
        score += 100.0 * len(kw_hits)
        bits.append("kw:" + ",".join(kw_hits))

    return score, "+".join(bits) if bits else "baseline"


def _scan_and_select(warnings: list[str]) -> tuple[Path | None, str]:
    all_paths: list[Path] = []
    per_root: dict[str, int] = {}

    for root in SEARCH_ROOTS:
        if not root.is_dir():
            _log_err(f"skip missing root: {root}")
            continue
        walked = 0
        capped = False
        for dirpath, _dirnames, filenames in os.walk(
            str(root), topdown=True, followlinks=False
        ):
            for fname in filenames:
                walked += 1
                if walked > MAX_WALK_FILES_PER_ROOT:
                    warnings.append(
                        f"walk cap {MAX_WALK_FILES_PER_ROOT} reached under {root}"
                    )
                    capped = True
                    break
                fp = Path(dirpath) / fname
                if not _is_video_ext(fp):
                    continue
                if _should_exclude_path(fp):
                    continue
                try:
                    if not fp.is_file():
                        continue
                except OSError:
                    continue
                all_paths.append(fp.resolve())
            if capped:
                break
        per_root[str(root)] = walked

    _log_err(f"scan roots file.walk counts (capped): {per_root}")
    if not all_paths:
        return None, "no_matching_video_files"

    seen: set[str] = set()
    unique: list[Path] = []
    for p in all_paths:
        k = str(p)
        if k not in seen:
            seen.add(k)
            unique.append(p)

    ffprobe_ok = _ffprobe_bin() is not None
    if not ffprobe_ok:
        warnings.append("ffprobe not found on PATH; selecting by mtime among extensions")

    probed: list[tuple[Path, dict[str, Any] | None, float, str]] = []
    for p in unique:
        pr: dict[str, Any] | None = None
        if ffprobe_ok:
            pr = _ffprobe_video(p)
        sc, rs = _score_candidate(p, pr)
        probed.append((p, pr, sc, rs))

    with_probe = [(p, pr, sc, rs) for p, pr, sc, rs in probed if pr is not None]
    if with_probe:
        best = max(with_probe, key=lambda t: (t[2], t[0].stat().st_mtime_ns))
        if best[2] > 0:
            return best[0], f"score={best[2]:.1f} ({best[3]})"
        best_mt = max(with_probe, key=lambda t: t[0].stat().st_mtime_ns)
        return best_mt[0], f"highest_mtime_among_probe_valid ({best_mt[3]})"

    best_FALLBACK = max(unique, key=lambda p: p.stat().st_mtime_ns)
    return best_FALLBACK, "mtime_fallback_no_ffprobe_valid"


def _safe_str(val: Any) -> str | None:
    if val is None:
        return None
    try:
        s = str(val)
    except Exception:
        return None
    return s if s else None


def _import_dvr_script(warnings: list[str]) -> Any:
    try:
        import DaVinciResolveScript as dvr_script  # type: ignore[import-not-found]

        return dvr_script
    except ImportError as exc:
        warnings.append(f"import DaVinciResolveScript failed: {exc}")
        try:
            spec = importlib.util.find_spec("DaVinciResolveScript")
            if spec is not None and spec.loader is not None:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
        except Exception as exc2:  # noqa: BLE001
            warnings.append(f"importlib load DaVinciResolveScript failed: {exc2}")
    return None


def _resolve_version_str(resolve: Any, warnings: list[str]) -> str | None:
    for meth in ("GetVersionString", "GetVersion"):
        if hasattr(resolve, meth):
            try:
                fn = getattr(resolve, meth)
                ver = fn() if callable(fn) else fn
                s = _safe_str(ver)
                if s:
                    return s
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{meth} failed: {exc}")
    return None


def _open_or_create_project(pm: Any, warnings: list[str]) -> Any:
    proj = None
    try:
        proj = pm.LoadProject(PROJECT_NAME)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"LoadProject failed: {exc}")
    if proj:
        return proj
    try:
        proj = pm.CreateProject(PROJECT_NAME)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"CreateProject failed: {exc}")
    if proj:
        return proj
    try:
        proj = pm.LoadProject(PROJECT_NAME)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"LoadProject (after create) failed: {exc}")
    if proj:
        return proj
    warnings.append("Could not load or create project.")
    return None


def _preset_name_excluded(name: str) -> bool:
    """Do not load YouTube-branded presets (user constraint: no YouTube)."""
    n = name.lower()
    return "youtube" in n


def _try_render_configure(
    project: Any,
    timeline: Any,
    resolve: Any,
    output_dir: Path,
    export_basename: str,
    warnings: list[str],
) -> str | None:
    try:
        resolve.OpenPage("deliver")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"OpenPage(deliver) failed (non-fatal): {exc}")

    try:
        project.SetCurrentTimeline(timeline)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"SetCurrentTimeline failed: {exc}")
        return None

    preset_candidates = (
        "H.264",
        "H264",
        "QuickTime",
        "MP4",
        "Custom",
    )
    preset_loaded = False
    try:
        plist = project.GetRenderPresetList()
    except Exception:
        plist = None
    if isinstance(plist, list):
        for name in plist:
            if _preset_name_excluded(str(name)):
                continue
            ns = str(name).lower()
            if "h264" in ns or "h.264" in ns or "mp4" in ns:
                try:
                    if project.LoadRenderPreset(str(name)):
                        preset_loaded = True
                        warnings.append(f"Loaded render preset from list: {name}")
                        break
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"LoadRenderPreset({name!r}) failed: {exc}")

    if not preset_loaded:
        for pname in preset_candidates:
            if _preset_name_excluded(pname):
                continue
            try:
                if project.LoadRenderPreset(pname):
                    preset_loaded = True
                    warnings.append(f"LoadRenderPreset OK: {pname!r}")
                    break
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"LoadRenderPreset({pname!r}) failed: {exc}")
    if not preset_loaded:
        warnings.append("No render preset loaded; trying format/codec only.")

    format_codec_ok = False
    try:
        fmts = project.GetRenderFormats() or {}
    except Exception as exc:
        warnings.append(f"GetRenderFormats failed: {exc}")
        fmts = {}

    def try_pair(fmt: str, codec: str) -> bool:
        try:
            if project.SetCurrentRenderFormatAndCodec(fmt, codec):
                return True
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SetCurrentRenderFormatAndCodec({fmt!r},{codec!r}): {exc}")
        return False

    preferred_fmt_tokens = ("mp4", "mov", "quicktime", "MP4", "Mp4")
    for fmt_key in list(fmts.keys()):
        fl = str(fmt_key).lower()
        if any(t.lower() in fl for t in preferred_fmt_tokens):
            try:
                codecs = project.GetRenderCodecs(str(fmt_key)) or {}
            except Exception:
                codecs = {}
            for codec_name in list(codecs.values()) + list(codecs.keys()):
                c = str(codec_name)
                cl = c.lower()
                if "h264" in cl or "h.264" in cl or "avc" in cl:
                    if try_pair(str(fmt_key), c):
                        format_codec_ok = True
                        warnings.append(f"Set format/codec: {fmt_key!r} + {c!r}")
                        break
            if format_codec_ok:
                break

    if not format_codec_ok:
        for fmt_try, codec_try in (
            ("MP4", "H264"),
            ("Mp4", "H264"),
            ("mp4", "H264"),
            ("QuickTime", "H264"),
            ("MOV", "H264"),
        ):
            if try_pair(fmt_try, codec_try):
                format_codec_ok = True
                warnings.append(f"Set format/codec fallback: {fmt_try}/{codec_try}")
                break

    if not format_codec_ok:
        warnings.append("Could not SetCurrentRenderFormatAndCodec; proceeding anyway.")

    target = str(output_dir.resolve())
    stem = export_basename
    if stem.lower().endswith(".mp4"):
        stem_no_ext = stem[:-4]
    else:
        stem_no_ext = stem

    settings_variants: list[dict[str, Any]] = [
        {"SelectAllFrames": 1, "TargetDir": target, "CustomName": export_basename},
        {"SelectAllFrames": 1, "TargetDir": target, "CustomName": stem_no_ext},
    ]
    settings_ok = False
    for rs in settings_variants:
        try:
            ok = project.SetRenderSettings(rs)
            if ok:
                settings_ok = True
                warnings.append(f"SetRenderSettings OK keys={list(rs.keys())}")
                break
            warnings.append(f"SetRenderSettings returned falsy for keys={list(rs.keys())}")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SetRenderSettings failed ({list(rs.keys())}): {exc}")
    if not settings_ok:
        try:
            ok = project.SetRenderSettings({"SelectAllFrames": 1, "TargetDir": target})
            if ok:
                settings_ok = True
                warnings.append("SetRenderSettings OK (TargetDir only; no CustomName).")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SetRenderSettings (TargetDir only) failed: {exc}")
            return None

    job_id: str | None = None
    try:
        jid = project.AddRenderJob()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"AddRenderJob failed: {exc}")
        return None

    if jid is None:
        warnings.append("AddRenderJob returned None.")
        return None
    sj = (_safe_str(jid) or "").strip()
    if sj:
        job_id = sj
    elif jid is True or jid is False:
        warnings.append(f"AddRenderJob returned bool: {jid}")
        return None
    else:
        try:
            cand = str(jid).strip()
            job_id = cand if cand else None
        except Exception:
            job_id = None
        if not job_id:
            warnings.append("AddRenderJob returned empty or unusable job id.")
            return None

    return job_id


def _job_id_call_variants(job_id: str) -> list[Any]:
    out: list[Any] = [job_id]
    try:
        out.append(int(job_id))
    except (TypeError, ValueError):
        pass
    return out


def _try_start_rendering(
    project: Any,
    resolve: Any,
    job_id: str,
    warnings: list[str],
) -> tuple[bool, str | None]:
    """Try compatible StartRendering forms; return (started_ok, signature_used)."""
    candidates: list[tuple[str, Callable[[], Any]]] = []

    for jid in _job_id_call_variants(job_id):
        candidates.append(
            (f"project.StartRendering([{type(jid).__name__}])", lambda j=jid: project.StartRendering([j]))  # noqa: B023
        )
        candidates.append(
            (f"project.StartRendering({type(jid).__name__})", lambda j=jid: project.StartRendering(j))  # noqa: B023
        )

    if resolve is not None:
        for jid in _job_id_call_variants(job_id):
            if hasattr(resolve, "StartRendering"):
                candidates.append(
                    (
                        f"resolve.StartRendering([{type(jid).__name__}])",
                        lambda j=jid: resolve.StartRendering([j]),  # noqa: B023
                    )
                )
                candidates.append(
                    (
                        f"resolve.StartRendering({type(jid).__name__})",
                        lambda j=jid: resolve.StartRendering(j),  # noqa: B023
                    )
                )

    seen: set[str] = set()
    for label, fn in candidates:
        if label in seen:
            continue
        seen.add(label)
        try:
            ret = fn()
            if ret is False:
                warnings.append(
                    f"StartRendering returned False for {label}; trying next form."
                )
                continue
            warnings.append(f"StartRendering succeeded via: {label} (return={ret!r})")
            return True, label
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"StartRendering failed ({label}): {exc}")

    warnings.append("StartRendering: all compatible forms failed or returned falsy.")
    return False, None


def _is_rendering_in_progress(project: Any, resolve: Any, warnings: list[str]) -> bool | None:
    for obj, name in ((project, "project"), (resolve, "resolve")):
        if obj is None:
            continue
        if hasattr(obj, "IsRenderingInProgress"):
            try:
                fn = getattr(obj, "IsRenderingInProgress")
                v = fn() if callable(fn) else fn
                if isinstance(v, bool):
                    return v
                if v is None:
                    continue
                return bool(v)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{name}.IsRenderingInProgress failed: {exc}")
    return None


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_write_json(path: Path, payload: dict[str, Any], warnings: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warnings.append(f"JSON mkdir failed {path.parent}: {exc}")
        return
    try:
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=".davinci_audio_bridge_v3_",
            suffix=".json.tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, path)
        except Exception:
            try:
                if os.path.isfile(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        warnings.append(f"atomic JSON write failed {path}: {exc}")


def _merge_write_status_json(
    json_path: Path,
    base: dict[str, Any],
    extra: dict[str, Any],
    warnings: list[str],
) -> None:
    disk = _read_json_if_exists(json_path)
    merged = {**disk, **base, **extra}
    merged["warnings"] = base.get("warnings", [])
    merged["errors"] = base.get("errors", [])
    _atomic_write_json(json_path, merged, warnings)


def _newest_mp4_in_dir(output_dir: Path) -> Path | None:
    try:
        cands = [
            p
            for p in output_dir.iterdir()
            if p.is_file() and p.suffix.lower() == ".mp4"
        ]
    except OSError:
        return None
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime_ns)


def _ensure_output_at_expected(
    expected: Path,
    output_dir: Path,
    warnings: list[str],
) -> Path | None:
    if expected.is_file():
        return expected.resolve()
    newest = _newest_mp4_in_dir(output_dir)
    if newest is None:
        warnings.append(
            f"Expected output missing ({expected}); no mp4 found in {output_dir}."
        )
        return None
    if newest.resolve() == expected.resolve():
        return expected.resolve()
    try:
        shutil.copy2(newest, expected)
        warnings.append(
            f"Copied newest mp4 to expected name: {newest.name} -> {expected.name}"
        )
        return expected.resolve() if expected.is_file() else None
    except OSError as exc:
        warnings.append(f"Copy/rename to expected output failed: {exc}; using {newest}")
        return newest.resolve()


def _verify_output(
    path: Path | None,
    errors: list[str],
    warnings: list[str],
) -> tuple[bool, int | None, float | None]:
    if path is None or not path.is_file():
        errors.append("output verification: file missing")
        return False, None, None
    try:
        sz = path.stat().st_size
    except OSError as exc:
        errors.append(f"output verification: stat failed: {exc}")
        return False, None, None
    if sz <= MIN_OUTPUT_BYTES:
        errors.append(
            f"output verification: size {sz} bytes <= {MIN_OUTPUT_BYTES} (1 MiB)"
        )
    dur = _ffprobe_duration(path)
    if dur is None:
        warnings.append("ffprobe duration unavailable for output; treating as fail check.")
        errors.append("output verification: ffprobe duration unavailable or invalid")
    elif dur <= MIN_OUTPUT_DURATION_SEC:
        errors.append(
            f"output verification: duration {dur:.2f}s <= {MIN_OUTPUT_DURATION_SEC}s"
        )
    ok = sz > MIN_OUTPUT_BYTES and dur is not None and dur > MIN_OUTPUT_DURATION_SEC
    return ok, sz, dur


def _poll_render_until_done(
    project: Any,
    resolve: Any,
    job_id: str,
    json_path: Path,
    base_payload: dict[str, Any],
    warnings: list[str],
    errors: list[str],
) -> None:
    """Poll IsRenderingInProgress every ~5s; update JSON; max RENDER_MAX_WAIT_SEC."""
    t0 = time.monotonic()
    poll_count = 0
    saw_true = False
    none_after_true_polls = 0

    while True:
        elapsed = time.monotonic() - t0
        if elapsed >= RENDER_MAX_WAIT_SEC:
            errors.append(
                f"render wait timeout after {RENDER_MAX_WAIT_SEC}s (7200s); "
                "render_completed=false (fail-open)."
            )
            base_payload["render_completed"] = False
            base_payload["render_poll_count"] = poll_count
            base_payload["last_poll_at"] = _utc_iso()
            base_payload["render_in_progress"] = None
            _merge_write_status_json(
                json_path,
                base_payload,
                {
                    "render_poll_count": poll_count,
                    "last_poll_at": base_payload["last_poll_at"],
                    "render_in_progress": None,
                },
                warnings,
            )
            return

        in_prog = _is_rendering_in_progress(project, resolve, warnings)
        poll_count += 1
        now_iso = _utc_iso()
        if in_prog is True:
            saw_true = True
            none_after_true_polls = 0
        elif saw_true and in_prog is None:
            none_after_true_polls += 1

        base_payload["render_poll_count"] = poll_count
        base_payload["last_poll_at"] = now_iso
        base_payload["render_in_progress"] = in_prog

        _merge_write_status_json(
            json_path,
            base_payload,
            {
                "render_poll_count": poll_count,
                "last_poll_at": now_iso,
                "render_in_progress": in_prog,
            },
            warnings,
        )

        if saw_true and in_prog is False:
            base_payload["render_completed"] = True
            base_payload["render_in_progress"] = False
            _merge_write_status_json(
                json_path,
                base_payload,
                {
                    "render_completed": True,
                    "render_in_progress": False,
                    "render_poll_count": poll_count,
                    "last_poll_at": _utc_iso(),
                },
                warnings,
            )
            return

        elif saw_true and in_prog is None and none_after_true_polls >= 6:
            warnings.append(
                "IsRenderingInProgress returned None repeatedly after seeing True; "
                "assuming render finished; proceeding to file check."
            )
            base_payload["render_completed"] = True
            base_payload["render_in_progress"] = False
            _merge_write_status_json(
                json_path,
                base_payload,
                {
                    "render_completed": True,
                    "render_in_progress": False,
                    "render_poll_count": poll_count,
                    "last_poll_at": _utc_iso(),
                },
                warnings,
            )
            return

        elif (in_prog is False or in_prog is None) and not saw_true:
            if poll_count * RENDER_POLL_SEC >= 120.0:
                if in_prog is None:
                    warnings.append(
                        "IsRenderingInProgress unavailable (None); after 120s "
                        "assuming render finished or API unsupported; file check next."
                    )
                else:
                    warnings.append(
                        "IsRenderingInProgress stayed false for 120s after StartRendering; "
                        "assuming render finished or API unavailable; proceeding to file check."
                    )
                base_payload["render_completed"] = True
                base_payload["render_in_progress"] = False
                _merge_write_status_json(
                    json_path,
                    base_payload,
                    {
                        "render_completed": True,
                        "render_in_progress": False,
                        "render_poll_count": poll_count,
                        "last_poll_at": _utc_iso(),
                    },
                    warnings,
                )
                return

        time.sleep(RENDER_POLL_SEC)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DaVinci audio bridge v3 (StateVerge).")
    p.add_argument(
        "--start-render",
        action="store_true",
        default=False,
        help="After AddRenderJob, call StartRendering and poll until done (max 2h).",
    )
    p.add_argument(
        "--audio-preset",
        choices=list(AUDIO_PRESET_CHOICES),
        default="raw_passthrough",
        help=(
            "Audio preset: raw_passthrough; ferry_ambient_v1 / driving_ambient_v1 add JSON "
            "EQ suggestions only; ferry_ambient_v2 runs a fail-open Fairlight auto-chain on "
            "all audio tracks before render when a timeline exists."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    start_render = bool(args.start_render)
    audio_preset = str(args.audio_preset)

    warnings: list[str] = []
    errors: list[str] = []

    ts_suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    timeline_name = f"StateVerge_Audio_Test_TL_{ts_suffix}"
    local_ts = _local_timestamp_filename()
    if audio_preset == "ferry_ambient_v2":
        export_basename = f"clean_real_sound_v2_{local_ts}.mp4"
    else:
        export_basename = f"clean_real_sound_test_{local_ts}.mp4"

    selected_path, selected_reason = _scan_and_select(warnings)
    selected_s = str(selected_path) if selected_path else ""

    output_dir = _choose_output_dir(warnings)
    json_path = _choose_json_path(warnings)
    expected_output = (output_dir / export_basename).resolve()

    intended_chain = _intended_audio_chain_for_preset(audio_preset)
    manual_review = _manual_review_required_for_preset(audio_preset)
    manual_note = _manual_review_note_for_preset(audio_preset)

    applied_audio_chain: dict[str, Any] | None = None

    payload: dict[str, Any] = {
        "selected_video": selected_s or None,
        "selected_reason": selected_reason,
        "project_name": PROJECT_NAME,
        "timeline_name": timeline_name,
        "export_basename": export_basename,
        "output_dir": str(output_dir.resolve()),
        "render_job_id": None,
        "render_started": False,
        "render_completed": False,
        "output_video": "",
        "output_exists": False,
        "output_size_bytes": None,
        "output_duration_sec": None,
        "api_connected": False,
        "resolve_version": None,
        "warnings": warnings,
        "errors": errors,
        "timestamp": _utc_iso(),
        "render_poll_count": 0,
        "last_poll_at": None,
        "render_in_progress": None,
        "start_render_signature": None,
        "audio_preset": audio_preset,
        "intended_audio_chain": intended_chain,
        "manual_review_required": manual_review,
    }
    if manual_note is not None:
        payload["manual_review_note"] = manual_note

    ready = False
    render_added = False
    render_started_flag = False
    output_video_line = ""

    if not selected_path:
        warnings.append("No video selected; skipping Resolve API steps.")

    dvr_script = _import_dvr_script(warnings)
    resolve = None
    if dvr_script is not None and hasattr(dvr_script, "scriptapp"):
        try:
            resolve = dvr_script.scriptapp("Resolve")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"scriptapp('Resolve') failed: {exc}")
    else:
        if dvr_script is None:
            warnings.append("DaVinciResolveScript unavailable.")
        else:
            warnings.append("DaVinciResolveScript missing scriptapp.")

    payload["api_connected"] = bool(resolve)
    if resolve:
        payload["resolve_version"] = _resolve_version_str(resolve, warnings)

    project_obj: Any = None
    if resolve and selected_path:
        pm = None
        try:
            pm = resolve.GetProjectManager()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"GetProjectManager failed: {exc}")

        project_obj = _open_or_create_project(pm, warnings) if pm else None
        if project_obj:
            mp = None
            try:
                mp = project_obj.GetMediaPool()
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"GetMediaPool failed: {exc}")

            clip = None
            timeline = None
            if mp:
                try:
                    clips = mp.ImportMedia([str(selected_path.resolve())])
                except Exception as exc:  # noqa: BLE001
                    clips = None
                    warnings.append(f"ImportMedia failed: {exc}")

                if clips and isinstance(clips, list) and len(clips) > 0:
                    clip = clips[0]
                else:
                    warnings.append("ImportMedia returned empty list or unexpected value.")

                if clip:
                    timeline_ok = False
                    append_items: list[Any] | None = None
                    try:
                        timeline = mp.CreateTimelineFromClips(timeline_name, [clip])
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"CreateTimelineFromClips failed: {exc}")
                        timeline = None

                    if timeline:
                        timeline_ok = True
                    else:
                        try:
                            timeline = mp.CreateEmptyTimeline(timeline_name)
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(f"CreateEmptyTimeline failed: {exc}")
                            timeline = None

                        if timeline:
                            try:
                                project_obj.SetCurrentTimeline(timeline)
                            except Exception as exc:  # noqa: BLE001
                                warnings.append(
                                    f"SetCurrentTimeline (empty TL) failed: {exc}"
                                )
                            try:
                                append_items = mp.AppendToTimeline([clip])
                            except Exception as exc:  # noqa: BLE001
                                warnings.append(f"AppendToTimeline failed: {exc}")
                                append_items = None
                            if append_items is None or append_items is False:
                                warnings.append("AppendToTimeline returned None/False.")
                            elif isinstance(append_items, list) and len(append_items) > 0:
                                timeline_ok = True
                            else:
                                warnings.append(
                                    "AppendToTimeline returned no timeline items."
                                )

                if timeline and timeline_ok:
                    if audio_preset == "ferry_ambient_v2":
                        applied_audio_chain = _apply_ferry_ambient_v2_fairlight_chain(
                            resolve,
                            project_obj,
                            timeline,
                            warnings,
                            errors,
                        )
                        payload["applied_audio_chain"] = applied_audio_chain
                        payload["noise_reduction_strength"] = FERRY_AMBIENT_V2_NR_STRENGTH
                        payload["eq_profile"] = FERRY_AMBIENT_V2_EQ_PROFILE
                        payload["target_lufs"] = FERRY_AMBIENT_V2_TARGET_LUFS
                        payload["target_true_peak"] = FERRY_AMBIENT_V2_TARGET_TRUE_PEAK
                    jid = _try_render_configure(
                        project_obj,
                        timeline,
                        resolve,
                        output_dir,
                        export_basename,
                        warnings,
                    )
                    if jid and str(jid).strip():
                        payload["render_job_id"] = str(jid).strip()
                        render_added = True
                        try:
                            project_obj.SaveProject()
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(f"SaveProject failed (non-fatal): {exc}")
                        ready = True

                        if start_render:
                            ok_sr, sig = _try_start_rendering(
                                project_obj, resolve, str(jid).strip(), warnings
                            )
                            payload["start_render_signature"] = sig
                            render_started_flag = ok_sr
                            payload["render_started"] = ok_sr
                            if ok_sr:
                                payload["warnings"] = warnings
                                payload["errors"] = errors
                                _atomic_write_json(json_path, payload, warnings)
                                _poll_render_until_done(
                                    project_obj,
                                    resolve,
                                    str(jid).strip(),
                                    json_path,
                                    payload,
                                    warnings,
                                    errors,
                                )
                                payload["warnings"] = warnings
                                payload["errors"] = errors
                                final_path = _ensure_output_at_expected(
                                    expected_output, output_dir, warnings
                                )
                                if final_path:
                                    payload["output_video"] = str(final_path)
                                    ok_v, sz, dur = _verify_output(
                                        final_path, errors, warnings
                                    )
                                    payload["output_exists"] = final_path.is_file()
                                    payload["output_size_bytes"] = sz
                                    payload["output_duration_sec"] = dur
                                    if not ok_v:
                                        payload["render_completed"] = False
                                else:
                                    payload["output_exists"] = False
                            else:
                                warnings.append(
                                    "StartRendering did not succeed; skipping poll."
                                )
                    else:
                        warnings.append("Render job not added (see prior warnings).")
                else:
                    if clip:
                        warnings.append(
                            "Timeline missing or not populated with the imported clip."
                        )
                    else:
                        warnings.append("No timeline object after creation attempts.")
            else:
                warnings.append("MediaPool unavailable.")
        else:
            warnings.append("Project open/create failed.")

    if not start_render:
        payload["render_started"] = False
        payload["render_completed"] = False
        payload["output_video"] = ""
        payload["output_exists"] = False
        payload["output_size_bytes"] = None
        payload["output_duration_sec"] = None

    payload["warnings"] = warnings
    payload["errors"] = errors
    payload["timestamp"] = _utc_iso()
    payload["audio_preset"] = audio_preset
    payload["intended_audio_chain"] = intended_chain
    payload["manual_review_required"] = manual_review
    if manual_note is not None:
        payload["manual_review_note"] = manual_note

    if audio_preset == "ferry_ambient_v2":
        skip_detail = "Resolve/timeline path skipped; Fairlight chain not executed."
        payload["applied_audio_chain"] = (
            applied_audio_chain
            if applied_audio_chain is not None
            else {
                "eq": _chain_step(False, False, skip_detail),
                "noise_reduction": _chain_step(False, False, skip_detail),
                "loudness": _chain_step(False, False, skip_detail),
                "limiter": _chain_step(False, False, skip_detail),
            }
        )
        payload["noise_reduction_strength"] = FERRY_AMBIENT_V2_NR_STRENGTH
        payload["eq_profile"] = FERRY_AMBIENT_V2_EQ_PROFILE
        payload["target_lufs"] = FERRY_AMBIENT_V2_TARGET_LUFS
        payload["target_true_peak"] = FERRY_AMBIENT_V2_TARGET_TRUE_PEAK

    if start_render and payload.get("output_video"):
        output_video_line = str(payload["output_video"])

    _atomic_write_json(json_path, payload, warnings)
    if not json_path.is_file():
        fb = FALLBACK_JSON
        _atomic_write_json(fb, payload, warnings)

    sys.stdout.write(
        "DAVINCI_AUDIO_BRIDGE_READY=" + ("true" if ready else "false") + "\n"
        "RENDER_STARTED=" + ("true" if render_started_flag else "false") + "\n"
        "OUTPUT_VIDEO=" + output_video_line + "\n"
    )
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
