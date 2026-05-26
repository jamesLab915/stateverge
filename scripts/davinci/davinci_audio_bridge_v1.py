#!/usr/bin/env python3
"""StateVerge DaVinci API bridge v1 — scan inbox videos, import to Resolve, timeline, render queue.

End-to-end: import → timeline → AddRenderJob only (never StartRendering / Render All).
Stdlib + ffprobe when available. Fail-open: exits 0; JSON + stderr diagnostics; stdout is
exactly three flag lines at end on success path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

PRIMARY_JSON = Path("/Volumes/SV_CACHE/davinci/logs/davinci_audio_bridge_v1.json")
FALLBACK_JSON = Path(
    "/Users/ziweizhang/StateVerge/_storage_fallback/sv_cache/davinci/logs/"
    "davinci_audio_bridge_v1.json"
)
PRIMARY_OUTPUT_DIR = Path("/Volumes/SV_CACHE/davinci/audio_exports")
FALLBACK_OUTPUT_DIR = Path(
    "/Users/ziweizhang/StateVerge/_storage_fallback/sv_cache/davinci/audio_exports"
)

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


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _log_err(msg: str) -> None:
    print(msg, file=sys.stderr)


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
    p = shutil.which("ffprobe")
    return p


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
    """Return (score, reason_snippet). Higher is better."""
    if probe is None:
        return 0.0, "no_ffprobe_data"

    w = int(probe["width"])
    h = int(probe["height"])
    d = float(probe["duration"])
    ratio = w / max(h, 1)

    score = 0.0
    bits: list[str] = []

    # 16:9 landscape preference
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

    # duration 60–300s
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
    """Pick best video path and a short human reason."""
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

    # de-dup preserve order
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

    # scored picks (only positive score requires probe data)
    with_probe = [(p, pr, sc, rs) for p, pr, sc, rs in probed if pr is not None]
    if with_probe:
        best = max(with_probe, key=lambda t: (t[2], t[0].stat().st_mtime_ns))
        if best[2] > 0:
            return best[0], f"score={best[2]:.1f} ({best[3]})"
        # all zero scores but valid probes — pick best mtime among probed
        best_mt = max(with_probe, key=lambda t: t[0].stat().st_mtime_ns)
        return best_mt[0], f"highest_mtime_among_probe_valid ({best_mt[3]})"

    # no ffprobe-valid entries — mtime among all ext matches
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
    # CreateProject returns None if projectName is not unique — project may exist.
    try:
        proj = pm.LoadProject(PROJECT_NAME)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"LoadProject (after create) failed: {exc}")
    if proj:
        return proj
    warnings.append("Could not load or create project.")
    return None


def _try_render_configure(
    project: Any,
    timeline: Any,
    resolve: Any,
    output_dir: Path,
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
        "YouTube",
        "YouTube 1080p",
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
            ns = str(name).lower()
            if "h264" in ns or "h.264" in ns or "youtube" in ns or "mp4" in ns:
                try:
                    if project.LoadRenderPreset(str(name)):
                        preset_loaded = True
                        warnings.append(f"Loaded render preset from list: {name}")
                        break
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"LoadRenderPreset({name!r}) failed: {exc}")

    if not preset_loaded:
        for pname in preset_candidates:
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

    try:
        ok = project.SetRenderSettings(
            {"SelectAllFrames": 1, "TargetDir": str(output_dir.resolve())}
        )
        if not ok:
            warnings.append("SetRenderSettings returned falsy.")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"SetRenderSettings failed: {exc}")
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


def main() -> None:
    warnings: list[str] = []
    ts_suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    timeline_name = f"StateVerge_Audio_Test_TL_{ts_suffix}"

    selected_path, selected_reason = _scan_and_select(warnings)
    selected_s = str(selected_path) if selected_path else ""

    output_dir = _choose_output_dir(warnings)
    json_path = _choose_json_path(warnings)

    payload: dict[str, Any] = {
        "selected_video": selected_s or None,
        "selected_reason": selected_reason,
        "project_name": PROJECT_NAME,
        "timeline_name": timeline_name,
        "render_job_id": None,
        "output_dir": str(output_dir.resolve()),
        "api_connected": False,
        "resolve_version": None,
        "warnings": warnings,
        "timestamp": _utc_iso(),
    }

    ready = False
    render_added = False

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

    if resolve and selected_path:
        pm = None
        try:
            pm = resolve.GetProjectManager()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"GetProjectManager failed: {exc}")

        project = _open_or_create_project(pm, warnings) if pm else None
        if project:
            mp = None
            try:
                mp = project.GetMediaPool()
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
                                project.SetCurrentTimeline(timeline)
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
                                warnings.append(
                                    "AppendToTimeline returned None/False."
                                )
                            elif isinstance(append_items, list) and len(append_items) > 0:
                                timeline_ok = True
                            else:
                                warnings.append(
                                    "AppendToTimeline returned no timeline items."
                                )

                if timeline and timeline_ok:
                    jid = _try_render_configure(
                        project, timeline, resolve, output_dir, warnings
                    )
                    if jid and str(jid).strip():
                        payload["render_job_id"] = str(jid).strip()
                        render_added = True
                        try:
                            project.SaveProject()
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(f"SaveProject failed (non-fatal): {exc}")
                        ready = True
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

    payload["warnings"] = warnings
    payload["timestamp"] = _utc_iso()

    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        warnings.append(f"JSON write failed {json_path}: {exc}")
        payload["warnings"] = warnings
        try:
            fb = FALLBACK_JSON
            fb.parent.mkdir(parents=True, exist_ok=True)
            with open(fb, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
        except OSError as exc2:
            _log_err(f"secondary JSON write failed: {exc2}")

    sys.stdout.write(
        "DAVINCI_AUDIO_BRIDGE_READY=" + ("true" if ready else "false") + "\n"
        "SELECTED_VIDEO=" + selected_s + "\n"
        "RENDER_JOB_ADDED=" + ("true" if render_added else "false") + "\n"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
