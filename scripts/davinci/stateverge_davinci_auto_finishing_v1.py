#!/usr/bin/env python3
"""StateVerge DaVinci auto finishing v1 — export/finish layer only (not primary denoise).

No YouTube upload, no ``auto_publish`` edits; never deletes ``--input-video``.
Fail-open: always exits 0; diagnostics in JSON + stderr; stdout is exactly the last
three status lines.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# --- sys.path: Blackmagic Resolve scripting modules (system first, then optional user) ---
# Prepend order (first in sys.path wins): (1) system DaVinci Resolve modules,
# (2) system Blackmagic modules, (3–4) same two under ~/Library if present.
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

PRIMARY_JSON = Path("/Volumes/SV_CACHE/davinci/logs/davinci_auto_finishing_v1.json")
FALLBACK_JSON = Path(
    "/Users/ziweizhang/StateVerge/_storage_fallback/sv_cache/davinci/logs/"
    "davinci_auto_finishing_v1.json"
)
PRIMARY_OUTPUT_DIR = Path("/Volumes/SV_CACHE/davinci/auto_finishing")
FALLBACK_OUTPUT_DIR = Path(
    "/Users/ziweizhang/StateVerge/_storage_fallback/sv_cache/davinci/auto_finishing"
)

PROJECT_NAME = "StateVerge_Auto_Finishing"

MODE_CHOICES = ("calm_long", "cinematic_short", "review_render")
QUALITY_CHOICES = ("fast", "standard", "high")

RENDER_POLL_SEC = 5.0
RENDER_MAX_WAIT_SEC = 7200
MIN_OUTPUT_BYTES = 1024 * 1024
MIN_OUTPUT_DURATION_SEC = 5.0


def _mode_video_bitrate_mbps(mode: str, quality: str) -> float:
    table: dict[str, dict[str, float]] = {
        "calm_long": {"fast": 25.0, "standard": 45.0, "high": 65.0},
        "cinematic_short": {"fast": 12.0, "standard": 18.0, "high": 25.0},
        "review_render": {"fast": 8.0, "standard": 12.0, "high": 20.0},
    }
    return table[mode][quality]


def _mode_layout(mode: str) -> tuple[int, int, float, str, str]:
    """width, height, fps, export_prefix (without ts), intended_audio_role."""
    if mode == "calm_long":
        return 3840, 2160, 30.0, "stateverge_finished_calm_long_", "calm_ambient_master"
    if mode == "cinematic_short":
        return 1080, 1920, 30.0, "stateverge_finished_cinematic_short_", "cinematic_music_master"
    return 1920, 1080, 30.0, "stateverge_finished_review_", "review_audio"


def _mode_audio_kbps(mode: str) -> int:
    return 320 if mode != "review_render" else 192


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _local_timestamp_filename() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _log_err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _dir_writable(dir_path: Path) -> bool:
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    try:
        with tempfile.NamedTemporaryFile(
            dir=dir_path, prefix=".sv_af_probe_", delete=True
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
        f"Primary log dir not writable, using fallback: {PRIMARY_JSON.parent}"
    )
    return FALLBACK_JSON


def _choose_output_dir(warnings: list[str]) -> Path:
    if _dir_writable(PRIMARY_OUTPUT_DIR):
        return PRIMARY_OUTPUT_DIR
    warnings.append(
        f"Primary output dir not writable ({PRIMARY_OUTPUT_DIR}); using fallback."
    )
    try:
        FALLBACK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warnings.append(f"Fallback output mkdir failed: {exc}")
    return FALLBACK_OUTPUT_DIR


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
    n = name.lower()
    return "youtube" in n


def _ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def _ffprobe_duration(path: Path) -> float | None:
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


def _suggest_precalm_ambient_mix(mode: str, input_path: Path) -> bool:
    if mode != "calm_long":
        return False
    s = str(input_path).lower()
    if "calm_ambient" in s or "audio_calm_ambient" in s:
        return False
    return True


def _try_set_setting(obj: Any, label: str, key: str, value: str, warnings: list[str]) -> bool:
    if obj is None or not hasattr(obj, "SetSetting"):
        return False
    try:
        obj.SetSetting(key, value)
        return True
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{label}.SetSetting({key!r}) failed: {exc}")
        return False


def _apply_timeline_resolution_best_effort(
    timeline: Any,
    project: Any,
    width: int,
    height: int,
    fps: float,
    mode: str,
    warnings: list[str],
) -> bool:
    """Best-effort timeline / project resolution (vertical for cinematic_short).

    Returns True if cinematic_short vertical timeline could not be confirmed via API.
    """
    pairs = (
        ("timelineResolutionWidth", str(width)),
        ("timelineResolutionHeight", str(height)),
        ("timelineFrameRate", str(fps)),
        ("timelinePlaybackFrameRate", str(fps)),
    )
    for key, val in pairs:
        _try_set_setting(timeline, "timeline", key, val, warnings)
    for key, val in pairs:
        _try_set_setting(project, "project", key, val, warnings)
    _try_set_setting(project, "project", "useCustomSettings", "1", warnings)
    vertical_unconfirmed = False
    if mode == "cinematic_short":
        ok_w = _try_set_setting(
            timeline, "timeline", "timelineResolutionWidth", "1080", warnings
        )
        ok_h = _try_set_setting(
            timeline, "timeline", "timelineResolutionHeight", "1920", warnings
        )
        if not (ok_w and ok_h):
            vertical_unconfirmed = True
            warnings.append(
                "Vertical timeline (1080x1920) could not be fully confirmed via API; "
                "render dimensions still target 1080x1920 (see render_settings)."
            )
    return vertical_unconfirmed


def _bitrate_key_variants(video_mbps: float, audio_kbps: int) -> list[dict[str, Any]]:
    """Ordered optional fragments to merge onto base render settings."""
    fragments: list[dict[str, Any]] = []
    for bk in (
        "BitrateMbps",
        "VideoBitrateMbps",
        "TargetBitrateMbps",
        "VideoBitrate",
        "Quality",
    ):
        fragments.append({bk: video_mbps})
    for ak, av in (
        ("AudioBitrateKbps", audio_kbps),
        ("AudioBitRate", audio_kbps),
        ("AudioBitrate", audio_kbps),
        ("AudioBitrateMbps", audio_kbps / 1000.0),
    ):
        fragments.append({ak: av})
    # Combined first candidate
    fragments.insert(0, {"BitrateMbps": video_mbps, "AudioBitrateKbps": audio_kbps})
    fragments.insert(1, {"BitrateMbps": video_mbps, "AudioBitRate": audio_kbps})
    return fragments


def _first_truthy_set_render_settings(
    project: Any,
    base: dict[str, Any],
    extra_fragments: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any] | None:
    """Try base alone, then base+each fragment, then base+first fragment+second, etc."""
    tried: list[str] = []

    def attempt(d: dict[str, Any]) -> dict[str, Any] | None:
        try:
            ok = project.SetRenderSettings(d)
            tried.append(f"keys={list(d.keys())!r} ok={ok!r}")
            if ok:
                return dict(d)
        except Exception as exc:  # noqa: BLE001
            tried.append(f"keys={list(d.keys())!r} exc={exc}")
        return None

    merged = attempt(dict(base))
    if merged:
        warnings.append(f"SetRenderSettings succeeded: {tried[-1]}")
        return merged

    for frag in extra_fragments:
        cand = {**base, **frag}
        merged = attempt(cand)
        if merged:
            warnings.append(f"SetRenderSettings succeeded: {tried[-1]}")
            return merged

    # Stack fragments cumulatively
    acc = dict(base)
    for frag in extra_fragments:
        acc.update(frag)
        merged = attempt(dict(acc))
        if merged:
            warnings.append(f"SetRenderSettings succeeded (stacked): {tried[-1]}")
            return merged

    for line in tried[-12:]:
        warnings.append(f"SetRenderSettings attempt: {line}")
    return None


def _configure_deliver_and_render_job(
    project: Any,
    timeline: Any,
    resolve: Any,
    output_dir: Path,
    export_basename: str,
    width: int,
    height: int,
    fps: float,
    video_mbps: float,
    audio_kbps: int,
    warnings: list[str],
) -> tuple[str | None, dict[str, Any]]:
    """Open Deliver page, presets, format/codec, SetRenderSettings, AddRenderJob."""
    meta: dict[str, Any] = {"render_settings": {}, "set_render_settings_attempts": []}

    try:
        resolve.OpenPage("deliver")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"OpenPage(deliver) failed (non-fatal): {exc}")

    try:
        project.SetCurrentTimeline(timeline)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"SetCurrentTimeline failed: {exc}")
        return None, meta

    preset_candidates = ("H.264", "H264", "QuickTime", "MP4", "Custom")
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

    format_codec_ok = False
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

    base_dim: dict[str, Any] = {
        "SelectAllFrames": 1,
        "TargetDir": target,
        "CustomName": export_basename,
        "FormatWidth": width,
        "FormatHeight": height,
        "FrameRate": fps,
    }
    codec_fragments: list[dict[str, Any]] = [
        {"VideoCodec": "H264"},
        {"VideoCodec": "H.264"},
        {"AudioCodec": "AAC"},
        {"AudioCodec": "aac"},
    ]
    bitrate_frags = _bitrate_key_variants(video_mbps, audio_kbps)

    settings_ok_dict: dict[str, Any] | None = None
    for custom in (export_basename, stem_no_ext):
        base = {**base_dim, "CustomName": custom}
        chain: list[dict[str, Any]] = [*codec_fragments, *bitrate_frags]
        settings_ok_dict = _first_truthy_set_render_settings(project, base, chain, warnings)
        if settings_ok_dict:
            break

    if not settings_ok_dict:
        try:
            minimal = {"SelectAllFrames": 1, "TargetDir": target, "CustomName": stem_no_ext}
            ok = project.SetRenderSettings(minimal)
            if ok:
                settings_ok_dict = dict(minimal)
                warnings.append("SetRenderSettings OK (minimal TargetDir+CustomName only).")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"SetRenderSettings (minimal) failed: {exc}")

    if settings_ok_dict:
        meta["render_settings"] = settings_ok_dict
    else:
        warnings.append("SetRenderSettings: no successful configuration.")
        return None, meta

    try:
        jid = project.AddRenderJob()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"AddRenderJob failed: {exc}")
        return None, meta

    if jid is None:
        warnings.append("AddRenderJob returned None.")
        return None, meta
    sj = (_safe_str(jid) or "").strip()
    if sj:
        return sj, meta
    if jid is True or jid is False:
        warnings.append(f"AddRenderJob returned bool: {jid}")
        return None, meta
    try:
        cand = str(jid).strip()
        if cand:
            return cand, meta
    except Exception:
        pass
    warnings.append("AddRenderJob returned empty or unusable job id.")
    return None, meta


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
    candidates: list[tuple[str, Callable[[], Any]]] = []

    for jid in _job_id_call_variants(job_id):
        candidates.append(
            (
                f"project.StartRendering([{type(jid).__name__}])",
                lambda j=jid: project.StartRendering([j]),  # noqa: B023
            )
        )
        candidates.append(
            (
                f"project.StartRendering({type(jid).__name__})",
                lambda j=jid: project.StartRendering(j),  # noqa: B023
            )
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


def _poll_render_until_done(
    project: Any,
    resolve: Any,
    warnings: list[str],
    errors: list[str],
) -> bool:
    """Return True if poll ended assuming render finished (then caller verifies file)."""
    t0 = time.monotonic()
    poll_count = 0
    saw_true = False
    none_after_true_polls = 0

    while True:
        elapsed = time.monotonic() - t0
        if elapsed >= RENDER_MAX_WAIT_SEC:
            errors.append(
                f"render wait timeout after {RENDER_MAX_WAIT_SEC}s; "
                "treating as not completed (fail-open)."
            )
            return False

        in_prog = _is_rendering_in_progress(project, resolve, warnings)
        poll_count += 1
        if in_prog is True:
            saw_true = True
            none_after_true_polls = 0
        elif saw_true and in_prog is None:
            none_after_true_polls += 1

        if saw_true and in_prog is False:
            return True

        if saw_true and in_prog is None and none_after_true_polls >= 6:
            warnings.append(
                "IsRenderingInProgress returned None repeatedly after True; "
                "assuming render finished; proceeding to file check."
            )
            return True

        if (in_prog is False or in_prog is None) and not saw_true:
            if poll_count * RENDER_POLL_SEC >= 120.0:
                warnings.append(
                    "IsRenderingInProgress stayed false/None for 120s after StartRendering; "
                    "assuming render finished or API unavailable; proceeding to file check."
                )
                return True

        time.sleep(RENDER_POLL_SEC)


def _newest_mp4_in_dir(output_dir: Path) -> Path | None:
    try:
        cands = [p for p in output_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"]
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
        warnings.append("ffprobe duration unavailable for output.")
        errors.append("output verification: ffprobe duration unavailable or invalid")
    elif dur <= MIN_OUTPUT_DURATION_SEC:
        errors.append(
            f"output verification: duration {dur:.2f}s <= {MIN_OUTPUT_DURATION_SEC}s"
        )
    ok = sz > MIN_OUTPUT_BYTES and dur is not None and dur > MIN_OUTPUT_DURATION_SEC
    return ok, sz, dur


def _write_json(path: Path, payload: dict[str, Any], warnings: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        warnings.append(f"Failed to write JSON to {path}: {exc}")


def _parse_args(argv: list[str]) -> argparse.Namespace | None:
    p = argparse.ArgumentParser(
        description="StateVerge DaVinci auto finishing v1 (export/finish only)."
    )
    p.add_argument("--input-video", required=True, help="Source media file (never deleted).")
    p.add_argument(
        "--mode",
        choices=list(MODE_CHOICES),
        default="calm_long",
        help="Finishing preset (resolution / bitrate table).",
    )
    p.add_argument(
        "--start-render",
        action="store_true",
        default=False,
        help="Queue is built with AddRenderJob; if set, also StartRendering and wait.",
    )
    p.add_argument(
        "--output-video",
        default=None,
        help="Full path for rendered mp4; default under SV_CACHE auto_finishing.",
    )
    p.add_argument(
        "--quality",
        choices=list(QUALITY_CHOICES),
        default="standard",
        help="Video bitrate tier (see mode table).",
    )
    try:
        return p.parse_args(argv)
    except SystemExit:
        return None


def _run_resolve_pipeline(
    args: argparse.Namespace,
    input_path: Path,
    output_dir: Path,
    expected_output: Path,
    export_basename: str,
    timeline_name: str,
    width: int,
    height: int,
    fps: float,
    video_mbps: float,
    audio_kbps: int,
    mode: str,
    warnings: list[str],
    errors: list[str],
) -> dict[str, Any]:
    """Execute Resolve steps; returns fields to merge into payload."""
    out: dict[str, Any] = {
        "api_connected": False,
        "resolve_version": None,
        "render_job_id": None,
        "render_settings": {},
        "render_started": False,
        "timeline_resolution_vertical_unconfirmed": False,
    }

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

    out["api_connected"] = bool(resolve)
    if resolve:
        out["resolve_version"] = _resolve_version_str(resolve, warnings)

    ready = False
    render_completed = False
    output_video = ""

    if not resolve:
        return {
            **out,
            "davinci_auto_finishing_ready": False,
            "render_completed": False,
            "output_video": "",
        }

    pm = None
    try:
        pm = resolve.GetProjectManager()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"GetProjectManager failed: {exc}")

    project_obj = _open_or_create_project(pm, warnings) if pm else None
    if not project_obj:
        return {
            **out,
            "davinci_auto_finishing_ready": False,
            "render_completed": False,
            "output_video": "",
        }

    mp = None
    try:
        mp = project_obj.GetMediaPool()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"GetMediaPool failed: {exc}")

    clip = None
    timeline = None
    if mp:
        try:
            clips = mp.ImportMedia([str(input_path.resolve())])
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
                        warnings.append(f"SetCurrentTimeline (empty TL) failed: {exc}")
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
                        warnings.append("AppendToTimeline returned no timeline items.")

            if timeline and timeline_ok:
                out["timeline_resolution_vertical_unconfirmed"] = (
                    _apply_timeline_resolution_best_effort(
                        timeline, project_obj, width, height, fps, mode, warnings
                    )
                )
                jid, rmeta = _configure_deliver_and_render_job(
                    project_obj,
                    timeline,
                    resolve,
                    output_dir,
                    export_basename,
                    width,
                    height,
                    fps,
                    video_mbps,
                    audio_kbps,
                    warnings,
                )
                out["render_settings"] = rmeta.get("render_settings") or {}
                if jid and str(jid).strip():
                    out["render_job_id"] = str(jid).strip()
                    try:
                        project_obj.SaveProject()
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"SaveProject failed (non-fatal): {exc}")
                    ready = True

                    if args.start_render:
                        ok_sr, sig = _try_start_rendering(
                            project_obj, resolve, str(jid).strip(), warnings
                        )
                        out["start_render_signature"] = sig
                        out["render_started"] = ok_sr
                        if ok_sr:
                            poll_ok = _poll_render_until_done(
                                project_obj, resolve, warnings, errors
                            )
                            out["render_poll_assumed_done"] = poll_ok
                            final_path = _ensure_output_at_expected(
                                expected_output, output_dir, warnings
                            )
                            if final_path:
                                output_video = str(final_path)
                                ok_v, sz, dur = _verify_output(
                                    final_path, errors, warnings
                                )
                                out["output_size_bytes"] = sz
                                out["output_duration_sec"] = dur
                                out["output_exists"] = final_path.is_file()
                                render_completed = bool(ok_v and poll_ok)
                                if not ok_v:
                                    errors.append(
                                        "render marked incomplete: ffprobe verification failed"
                                    )
                            else:
                                out["output_exists"] = False
                                errors.append("render output file not found after poll")
                        else:
                            warnings.append(
                                "StartRendering did not succeed; skipping poll."
                            )
                            out["render_started"] = False
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

    return {
        **out,
        "davinci_auto_finishing_ready": ready,
        "render_completed": render_completed,
        "output_video": output_video,
    }


def main(argv: list[str] | None = None) -> None:
    warnings: list[str] = []
    errors: list[str] = []
    ts = _local_timestamp_filename()

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args is None:
        errors.append("invalid CLI arguments (see stderr / run with -h)")
        _log_err("Argument parse failed; use --input-video and valid flags.")

    json_path = _choose_json_path(warnings)

    payload: dict[str, Any] = {
        "node": "stateverge_davinci_auto_finishing_v1",
        "timestamp": _utc_iso(),
        "errors": errors,
        "warnings": warnings,
        "input_video": "",
        "mode": "calm_long",
        "quality": "standard",
        "output_dir": "",
        "export_basename": "",
        "expected_output_video": "",
        "project_name": PROJECT_NAME,
        "timeline_name": "",
        "intended_audio_role": "calm_ambient_master",
        "suggest_precalm_ambient_mix": False,
        "start_render_requested": False,
        "api_connected": False,
        "resolve_version": None,
        "davinci_auto_finishing_ready": False,
        "render_job_id": None,
        "render_settings": {},
        "render_started": False,
        "start_render_signature": None,
        "render_completed": False,
        "render_poll_assumed_done": None,
        "output_video": "",
        "output_exists": False,
        "output_size_bytes": None,
        "output_duration_sec": None,
        "timeline_resolution_vertical_unconfirmed": False,
    }

    ready_flag = False
    completed_flag = False
    output_line = ""

    try:
        if args is None:
            pass
        else:
            payload["start_render_requested"] = bool(args.start_render)
            mode = str(args.mode)
            quality = str(args.quality)
            payload["mode"] = mode
            payload["quality"] = quality

            input_path = Path(args.input_video).expanduser()
            payload["input_video"] = str(input_path)

            width, height, fps, prefix, intended_role = _mode_layout(mode)
            payload["intended_audio_role"] = intended_role
            payload["suggest_precalm_ambient_mix"] = _suggest_precalm_ambient_mix(
                mode, input_path
            )

            export_basename = f"{prefix}{ts}.mp4"
            timeline_name = f"stateverge_auto_{mode}_{ts}"

            if args.output_video:
                out_vp = Path(args.output_video).expanduser().resolve()
                output_dir = out_vp.parent
                export_basename = out_vp.name
                expected_output = out_vp
                try:
                    output_dir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    warnings.append(f"output-video parent mkdir failed: {exc}")
            else:
                output_dir = _choose_output_dir(warnings)
                expected_output = (output_dir / export_basename).resolve()

            payload["timeline_name"] = timeline_name
            payload["export_basename"] = export_basename
            payload["output_dir"] = str(output_dir.resolve())
            payload["expected_output_video"] = str(expected_output)

            if not input_path.is_file():
                errors.append(f"input video not found: {input_path}")
            else:
                video_mbps = _mode_video_bitrate_mbps(mode, quality)
                audio_kbps = _mode_audio_kbps(mode)
                merge = _run_resolve_pipeline(
                    args,
                    input_path,
                    output_dir,
                    expected_output,
                    export_basename,
                    timeline_name,
                    width,
                    height,
                    fps,
                    video_mbps,
                    audio_kbps,
                    mode,
                    warnings,
                    errors,
                )
                payload.update(merge)
                ready_flag = bool(merge.get("davinci_auto_finishing_ready"))
                if not args.start_render:
                    payload["render_completed"] = False
                    payload["output_video"] = ""
                    payload["output_exists"] = False
                    payload["output_size_bytes"] = None
                    payload["output_duration_sec"] = None
                else:
                    completed_flag = bool(payload.get("render_completed"))
                    output_line = str(payload.get("output_video") or "")

    except Exception as exc:  # noqa: BLE001 — fail-open
        errors.append(f"unexpected exception: {exc}")
        _log_err(str(exc))

    payload["errors"] = errors
    payload["warnings"] = warnings
    payload["timestamp"] = _utc_iso()

    _write_json(json_path, payload, warnings)
    if not json_path.is_file():
        _write_json(FALLBACK_JSON, payload, warnings)

    sys.stdout.write(
        "DAVINCI_AUTO_FINISHING_READY=" + ("true" if ready_flag else "false") + "\n"
        "RENDER_COMPLETED=" + ("true" if completed_flag else "false") + "\n"
        "OUTPUT_VIDEO=" + output_line + "\n"
    )
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
