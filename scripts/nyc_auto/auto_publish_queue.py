#!/usr/bin/env python3
"""NYC long-form YouTube upload queue v2 (Pro-only).

- Dedupe gate v2: ledger + historical job JSON + sampled ``quick_hash`` / ``content_key``.
- Concurrency lock under ``publish_pack/nyc_long_uploads`` (home fallback).
- Token: official long ``token.json`` only (channel guard).
- Never uses Shorts token. Auto-publish privacy is ``private`` or ``unlisted`` only (never ``public``).
- ``--manual-review-upload`` uploads a single file as ``unlisted`` for Studio review only; it does not lift
  ``long_autopublish_emergency.json`` or restore autopublish; normal ``--upload`` stays blocked while emergency is active.
- ``--auto-review-upload`` picks a safe ``ready_to_upload`` finished clip (``nyc_long_clips`` first), same unlisted review
  path as manual review, without restoring autopublish.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent.parent
_SRC = _REPO / "src"
_SCRIPTS = _REPO / "scripts"
_NYC_AUTO = Path(__file__).resolve().parent
for p in (_SRC, _SCRIPTS, _NYC_AUTO):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from utils.storage_paths import get_transfer_ready_to_upload, get_sv_cache, get_sv_transfer  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")

    def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER/ready_to_upload")


from channel_guard import assert_long_upload_context, validate_long_channel_token  # noqa: E402
from media_quick_hash import content_key_v2, normalize_basename, triple_chunk_sha256  # noqa: E402
from youtube_token_paths import OFFICIAL_SHORTS_TOKEN_PATH, resolve_client_secrets, resolve_long_form_upload_token  # noqa: E402
from youtube_upload import upload_from_package_directory  # noqa: E402
from metadata_generator import ensure_youtube_metadata_file, sync_legacy_package_files  # noqa: E402
from long_audio_policy import (  # noqa: E402
    find_no_vocals_asset,
    infer_long_audio_policy,
    path_suggests_cleaned_audio,
)
from nyc_long_source_policy import (  # noqa: E402
    LONG_SOURCE_POLICY_VERSION,
    discover_long_video169_scan_directories,
    is_allowed_long_video169_candidate_path,
    is_non_raw_long_source_path,
    is_valid_nyc_long_source,
    nyc_long_source_policy_summary,
    summarize_rejections,
)
from nyc_long_schedule_v1 import (  # noqa: E402
    evaluate_publish_schedule_gate,
    schedule_metadata_for_job,
)

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}

_CONTENT_ROUTING_META_KEYS = (
    "inferred_theme",
    "title_theme",
    "audio_mode",
    "music_required",
    "music_disabled_by_default",
    "real_sound_secondary",
    "metadata_policy_version",
    "content_routing_version",
    "title_evidence",
    "blocked_title_terms",
)


def _content_routing_bundle_for_video(
    vid: Path,
    *,
    job_id: str,
    source_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from content_routing_v1 import build_content_routing_bundle  # noqa: WPS433

        asset: dict[str, Any] = {"path": str(vid), "job_id": job_id}
        if source_info:
            asset.update(source_info)
        return build_content_routing_bundle(asset, job_id=job_id)
    except Exception:  # noqa: BLE001
        return {}


def _merge_content_routing_into_extra(extra_merge: dict[str, Any], bundle: dict[str, Any]) -> None:
    if not bundle:
        return
    for key in _CONTENT_ROUTING_META_KEYS:
        if key in bundle:
            extra_merge[key] = bundle[key]
_SHORTS_PATH_FRAGMENTS = (
    "shorts_clips",
    "shorts_uploads",
    "youtube_shorts",
    "/reels/",
    "tiktok",
    "image_motion",
    "mixed_video_image",
    "_vertical_",
)
DEFAULT_DESC = "NYC long-form street footage by StateVerge."
DEFAULT_TAGS = "NYC, New York City, Driving, Street View, StateVerge"
MIN_DURATION_SEC = 180.0
MANUAL_REVIEW_UPLOAD_MIN_DURATION_SEC = 5.0
MIN_BYTES = 1024 * 1024
LEDGER_NAME = "long_used_assets.json"
LOCK_NAME = ".nyc_long_upload.lock"
STALE_LOCK_SEC = 6 * 3600


def _canonical_media_path(p: str) -> str:
    """Stable path key for dedupe sets (best-effort resolve)."""
    t = (p or "").strip()
    if not t:
        return ""
    try:
        return str(Path(t).expanduser().resolve())
    except OSError:
        try:
            return str(Path(t).expanduser())
        except OSError:
            return t


HOME_LONG_UPLOADS = Path.home() / "StateVerge" / "data" / "long_runtime" / "long_uploads"
HOME_SHORTS_UPLOADS = Path.home() / "StateVerge" / "data" / "shorts_runtime" / "shorts_uploads"
HOME_SHORTS_READY = Path.home() / "StateVerge" / "data" / "shorts_runtime" / "shorts_ready_clips"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_shorts_path(p: Path) -> bool:
    parts = {x.lower() for x in p.parts}
    if "shorts_clips" in parts or "shorts_uploads" in parts:
        return True
    s = str(p).replace("\\", "/").lower()
    return any(f in s for f in _SHORTS_PATH_FRAGMENTS)


def _title_from_filename(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return (stem or path.name)[:100]


def _run_real_sound_gate_long(
    video: Path,
    *,
    gate_job_id: str,
    dry_run: bool,
    report_dir: Path,
    warnings: list[str],
) -> tuple[Path, dict[str, Any] | None]:
    try:
        from real_sound_cleanup_gate import run_real_sound_cleanup

        upload_path, real_rep = run_real_sound_cleanup(
            video,
            "long",
            mode="auto",
            dry_run=dry_run,
            keep_intermediates=False,
            job_id=gate_job_id,
            preset=os.environ.get("NYC_LONG_REAL_SOUND_PRESET", "very_light"),
        )
        if real_rep:
            rp_txt = real_rep.get("report_path")
            if rp_txt:
                try:
                    shutil.copy(Path(str(rp_txt)), report_dir / "real_sound_quality_report.json")
                except OSError as exc:
                    warnings.append(f"real_sound_report_copy_failed:{exc!r}")
        return upload_path, real_rep
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"real_sound_gate_exception:{exc!r}")
        return video.resolve(), None


def _finalize_real_sound_upload_path(
    vid: Path,
    real_rep: dict[str, Any] | None,
    *,
    warnings: list[str],
) -> Path:
    fv = Path(str((real_rep or {}).get("final_video") or ""))
    if fv.is_file() and (real_rep or {}).get("publish_safe", True) is not False:
        out = fv.resolve()
    else:
        out = vid.resolve()
        if real_rep and (
            bool(real_rep.get("fallback_to_original")) or real_rep.get("publish_safe") is False
        ):
            warnings.append("real_sound_gate_fallback_original")
    if real_rep:
        for rw in real_rep.get("warnings") or []:
            warnings.append(f"real_sound_gate:{rw}")
        for re_ in real_rep.get("errors") or []:
            warnings.append(f"real_sound_gate_error:{re_}")
    return out


def _apply_davinci_youtube_audio_finish(
    upload_path: Path,
    *,
    content_kind: str = "long",
    warnings: list[str] | None = None,
) -> tuple[Path, dict[str, Any] | None, str | None]:
    """Prefer finished_for_youtube; optional gate when enabled. Never raises."""
    try:
        daf_dir = _SCRIPTS / "davinci_audio_finish"
        if daf_dir.is_dir() and str(daf_dir) not in sys.path:
            sys.path.insert(0, str(daf_dir))
        from upload_path import resolve_upload_video_path  # noqa: WPS433

        new_path, meta = resolve_upload_video_path(Path(upload_path), content_kind=content_kind)
        if warnings is not None:
            if meta.get("davinci_audio_finish_used"):
                warnings.append(
                    f"davinci_audio_finish:{meta.get('davinci_audio_finish_reason', 'used')}"
                )
            br = meta.get("davinci_audio_finish_block_reason")
            if br:
                warnings.append(f"davinci_audio_finish_block:{br}")
        block = meta.get("davinci_audio_finish_block_reason")
        return Path(new_path), meta, str(block) if block else None
    except Exception as exc:  # noqa: BLE001
        if warnings is not None:
            warnings.append(f"davinci_audio_finish_exception:{exc!r}")
        return Path(upload_path), None, None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def _parse_iso_age_seconds(ts: str) -> float | None:
    t = (ts or "").strip()
    if not t:
        return None
    try:
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except ValueError:
        return None


def ensure_publish_v2_dirs(warnings: list[str]) -> None:
    """Best-effort mkdir for official + home paths; never raises."""
    specs: list[Path] = []
    try:
        xfer = get_sv_transfer(verbose=False)
        cache = get_sv_cache(verbose=False)
        ready = get_transfer_ready_to_upload(verbose=False)
        specs.extend(
            [
                xfer / "publish_pack" / "nyc_long_uploads",
                xfer / "publish_pack" / "shorts_uploads",
                ready / "nyc_long_clips",
                ready / "shorts_clips",
                cache / "jobs" / "nyc_long",
                cache / "jobs" / "shorts",
            ]
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"ensure_dirs_sv_paths:{exc!r}")
    specs.extend(
        [
            HOME_LONG_UPLOADS,
            HOME_SHORTS_UPLOADS,
            HOME_SHORTS_READY,
        ]
    )
    for d in specs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"ensure_dir_failed:{d}:{exc!r}")


def _resolve_pack_ledger_lock(warnings: list[str]) -> tuple[Path, Path, Path, bool]:
    """Return (job_pack_parent, ledger_path, lock_path, fallback_used)."""
    xfer = get_sv_transfer(verbose=False)
    primary = xfer / "publish_pack" / "nyc_long_uploads"
    ledger_primary = primary / LEDGER_NAME
    lock_primary = primary / LOCK_NAME
    try:
        primary.mkdir(parents=True, exist_ok=True)
        probe = primary / ".nyc_long_write_probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return primary, ledger_primary, lock_primary, False
    except OSError:
        try:
            HOME_LONG_UPLOADS.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        warnings.append("nyc_long_pack_fallback_to_home")
        ledger_fb = HOME_LONG_UPLOADS / LEDGER_NAME
        lock_fb = HOME_LONG_UPLOADS / LOCK_NAME
        return HOME_LONG_UPLOADS, ledger_fb, lock_fb, True


def _ffprobe_json(path: Path) -> dict[str, Any] | None:
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
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "{}")
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
        return None


def _duration_and_has_video(probe: dict[str, Any] | None) -> tuple[float | None, bool]:
    if not probe:
        return None, False
    dur: float | None = None
    try:
        fmt = probe.get("format") or {}
        d = float((fmt.get("duration") or "0").strip() or 0.0)
        if d > 0:
            dur = d
    except (TypeError, ValueError):
        dur = None
    has_v = False
    for st in probe.get("streams") or []:
        if isinstance(st, dict) and (st.get("codec_type") or "").lower() == "video":
            has_v = True
            break
    return dur, has_v


def _primary_video_dims(probe: dict[str, Any] | None) -> tuple[int, int]:
    if not probe:
        return 0, 0
    for st in probe.get("streams") or []:
        if not isinstance(st, dict):
            continue
        if (st.get("codec_type") or "").lower() != "video":
            continue
        try:
            return int(st.get("width") or 0), int(st.get("height") or 0)
        except (TypeError, ValueError):
            return 0, 0
    return 0, 0


def _under_dir(path: Path, ancestor: Path) -> bool:
    try:
        rp = path.resolve()
        ra = ancestor.resolve()
    except OSError:
        return False
    for p in rp.parents:
        if p == ra:
            return True
    return rp == ra


def _list_video_files(root: Path, warnings: list[str]) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        warnings.append(f"scan_skip_missing_dir:{root}")
        return out
    try:
        for p in root.rglob("*"):
            if not p.is_file() or p.name.startswith("._"):
                continue
            if p.suffix.lower() not in VIDEO_EXTS:
                continue
            if _is_shorts_path(p):
                continue
            out.append(p)
    except OSError as exc:
        warnings.append(f"scan_error:{root}:{exc!r}")
    return out


def _stable_mtime_sort(paths: list[Path]) -> list[Path]:
    def key(pp: Path) -> tuple[float, str]:
        try:
            return (float(pp.stat().st_mtime), str(pp))
        except OSError:
            return (0.0, str(pp))

    return sorted(paths, key=key)


def _is_clean_real_sound_long_filename(path: Path) -> bool:
    return "clean_real_sound" in path.name.lower()


def _resolve_real_sound_cleanup_sidecar(stem: str) -> tuple[Path | None, dict[str, Any] | None]:
    """Return path + parsed JSON for ``<stem>_real_sound_cleanup_report.json`` if present."""
    candidates = [
        get_sv_cache(verbose=False) / "audio_clean" / "reports" / f"{stem}_real_sound_cleanup_report.json",
        Path.home() / "StateVerge" / "data" / "audio_clean" / "reports" / f"{stem}_real_sound_cleanup_report.json",
    ]
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    return p, data
            except (OSError, json.JSONDecodeError):
                continue
    return None, None


def _resolve_real_sound_cleanup_report_for_output(out_path: Path) -> tuple[Path | None, dict[str, Any] | None]:
    """Find a report whose ``output_path`` matches the clean master (report name uses *input* stem)."""
    try:
        target = str(out_path.resolve())
    except OSError:
        target = str(out_path)
    roots = [
        get_sv_cache(verbose=False) / "audio_clean" / "reports",
        Path.home() / "StateVerge" / "data" / "audio_clean" / "reports",
    ]
    best: tuple[float, Path, dict[str, Any]] | None = None
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for p in root.glob("*_real_sound_cleanup_report.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(d, dict):
                    continue
                if str(d.get("output_path") or "").strip() != target:
                    continue
                try:
                    mt = float(p.stat().st_mtime)
                except OSError:
                    mt = 0.0
                if best is None or mt > best[0]:
                    best = (mt, p, d)
        except OSError:
            continue
    if best:
        return best[1], best[2]
    return None, None


def _raw_nyc_long_master_needs_noise_gate(path: Path | None) -> bool:
    """True for ``nyc_long_master_*.mp4`` not yet named ``*clean_real_sound*`` (needs gate v1)."""
    if not path or not path.name:
        return False
    n = path.name.lower()
    if "clean_real_sound" in n:
        return False
    return n.startswith("nyc_long_master_") and n.endswith(".mp4")


def sort_probe_pass_prefer_clean_real_sound(
    probe_pass: list[tuple[Path, dict[str, Any], float, int]],
) -> list[tuple[Path, dict[str, Any], float, int]]:
    """Prefer ``*clean_real_sound*.mp4`` masters for NYC long pool ordering."""

    def key_row(row: tuple[Path, dict[str, Any], float, int]) -> tuple[int, float, str]:
        p = row[0]
        pri = 0 if _is_clean_real_sound_long_filename(p) else 1
        try:
            mt = -float(p.stat().st_mtime)
        except OSError:
            mt = 0.0
        return (pri, mt, str(p))

    return sorted(probe_pass, key=key_row)


def _long_default_raw_scan_roots(xfer: Path, cache: Path) -> list[Path]:
    return [xfer / "00_INBOX" / "iphone", cache / "inbox"]


def _long_extra_raw_source_roots_from_env(warnings: list[str]) -> list[Path]:
    raw = (os.environ.get("NYC_LONG_RAW_SOURCE_ROOTS") or "").strip()
    out: list[Path] = []
    for chunk in raw.split(os.pathsep if os.pathsep in raw else ":"):
        t = chunk.strip()
        if not t:
            continue
        p = Path(t).expanduser()
        if p.is_dir():
            out.append(p)
        else:
            warnings.append(f"nyc_long_raw_source_root_missing_or_not_dir:{t}")
    return out


def count_non_video169_inbox_long_videos_excluded(xfer: Path, cache: Path, warnings: list[str]) -> int:
    """Count video files under iphone + SV_CACHE inbox that are not allowed Long ``video169`` paths."""
    n = 0
    for base in (xfer / "00_INBOX" / "iphone", cache / "inbox"):
        if not base.is_dir():
            continue
        for p in _list_video_files(base, warnings):
            if is_non_raw_long_source_path(p):
                continue
            if is_allowed_long_video169_candidate_path(p):
                continue
            n += 1
    return n


def collect_long_raw_source_candidates(xfer: Path, cache: Path, warnings: list[str]) -> list[Path]:
    """Long queue scans only ``**/video169/`` under iphone, SV_CACHE inbox, and NYC_LONG_RAW_SOURCE_ROOTS."""
    dirs = discover_long_video169_scan_directories(xfer, cache, warnings)
    if not dirs:
        warnings.append("long_video169_scan_no_directories_found")
    seen: set[str] = set()
    ordered: list[Path] = []
    for root in dirs:
        phase = _list_video_files(root, warnings)
        for p in _stable_mtime_sort(phase):
            if is_non_raw_long_source_path(p):
                warnings.append(f"long_raw_scan_skip_non_raw_path:{p}")
                continue
            if not is_allowed_long_video169_candidate_path(p):
                warnings.append(f"long_raw_scan_skip_non_video169_path:{p}")
                continue
            try:
                k = str(p.resolve())
            except OSError:
                k = str(p)
            if k in seen:
                continue
            seen.add(k)
            ordered.append(p)
    return ordered


def count_long_output_artifact_pool_videos(
    xfer: Path, cache: Path, warnings: list[str], *, cap_per_root: int = 2500
) -> int:
    """Count video files under output/history trees (not used as Long **source** scan)."""
    roots = [
        xfer / "ready_to_upload",
        xfer / "publish_pack",
        cache / "renders",
        cache / "audio_clean",
        cache / "audio_separated",
    ]
    total = 0
    for root in roots:
        if not root.is_dir():
            continue
        n = 0
        try:
            for p in root.rglob("*"):
                if n >= cap_per_root:
                    warnings.append(f"long_artifact_count_capped:{root}:{cap_per_root}")
                    break
                if not p.is_file() or p.name.startswith("._"):
                    continue
                if p.suffix.lower() not in VIDEO_EXTS:
                    continue
                n += 1
                total += 1
        except OSError as exc:
            warnings.append(f"long_artifact_count_scan_error:{root}:{exc!r}")
    return total


def collect_long_candidates(ready: Path, warnings: list[str]) -> list[Path]:
    """Backward-compatible: ``ready`` is ignored; only raw inbox roots are scanned."""
    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)
    return collect_long_raw_source_candidates(xfer, cache, warnings)


def _load_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 2, "updated_at": "", "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            return {"version": 2, "updated_at": "", "entries": []}
        if "entries" not in data or not isinstance(data["entries"], list):
            data["entries"] = []
        data.setdefault("version", 2)
        return data
    except (OSError, json.JSONDecodeError):
        return {"version": 2, "updated_at": "", "entries": []}


def _save_ledger(path: Path, data: dict[str, Any], warnings: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        warnings.append(f"ledger_write_failed:{path}:{exc!r}")


def _norm_entry(e: dict[str, Any]) -> dict[str, Any]:
    rp = str(e.get("resolved_path") or e.get("path") or "").strip()
    src = str(e.get("source_path") or rp).strip()
    qh = str(e.get("quick_hash") or "").strip()
    ck = str(e.get("content_key") or "").strip()
    try:
        sz = int(e.get("size_bytes") or 0)
    except (TypeError, ValueError):
        sz = 0
    try:
        dsec = float(e.get("duration_sec") or e.get("duration_seconds") or 0.0)
    except (TypeError, ValueError):
        dsec = 0.0
    stem = str(e.get("stem") or Path(rp).stem if rp else "").strip().lower()
    bn = str(e.get("basename") or Path(rp).name if rp else "").strip().lower()
    return {
        "source_path": src,
        "resolved_path": rp,
        "quick_hash": qh,
        "content_key": ck,
        "size_bytes": sz,
        "duration_sec": dsec,
        "stem": stem,
        "basename": bn,
        "youtube_video_id": str(e.get("youtube_video_id") or e.get("video_id") or "").strip(),
        "uploaded": bool(e.get("uploaded")),
    }


class DedupeIndexV2:
    def __init__(self) -> None:
        self.content_keys: set[str] = set()
        self.quick_hashes: set[str] = set()
        self.resolved_path_keys: set[str] = set()
        self.basename_size_duration: list[tuple[str, int, float]] = []
        self.youtube_ids: set[str] = set()
        self.vid_to_paths: dict[str, set[str]] = {}

    def add_path_key(self, p: str) -> None:
        k = _canonical_media_path(p)
        if k:
            self.resolved_path_keys.add(k)


def _ingest_ledger_entries(data: dict[str, Any], idx: DedupeIndexV2) -> None:
    for raw in data.get("entries") or []:
        if not isinstance(raw, dict):
            continue
        e = _norm_entry(raw)
        if e["content_key"]:
            idx.content_keys.add(e["content_key"])
        if e["quick_hash"]:
            idx.quick_hashes.add(e["quick_hash"])
        if e["resolved_path"]:
            idx.add_path_key(e["resolved_path"])
        if e["source_path"] and e["source_path"] != e["resolved_path"]:
            idx.add_path_key(e["source_path"])
        if e["basename"] and e["size_bytes"]:
            idx.basename_size_duration.append((e["basename"], e["size_bytes"], e["duration_sec"]))
        vid = e["youtube_video_id"]
        if vid and e["resolved_path"]:
            idx.youtube_ids.add(vid)
            idx.vid_to_paths.setdefault(vid, set()).add(_canonical_media_path(e["resolved_path"]))
            idx.add_path_key(e["resolved_path"])


def _upload_result_paths(uj: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in ("source_path", "source_video", "video", "path", "resolved_path", "upload_video"):
        v = uj.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def _scan_history_job_jsons(roots: list[Path], idx: DedupeIndexV2, warnings: list[str]) -> None:
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for jpath in root.rglob("long_job_result.json"):
                try:
                    dj = json.loads(jpath.read_text(encoding="utf-8", errors="replace"))
                except (OSError, json.JSONDecodeError):
                    warnings.append(f"history_unread:{jpath}")
                    continue
                if not isinstance(dj, dict):
                    continue
                uploads = dj.get("uploads") if isinstance(dj.get("uploads"), list) else []
                uploaded_flag = dj.get("uploaded") is True
                success = bool(uploaded_flag)
                top_vid = str(dj.get("youtube_video_id") or "").strip()
                for u in uploads:
                    if not isinstance(u, dict):
                        continue
                    vid = str(u.get("video_id") or "").strip()
                    if u.get("ok") and vid:
                        success = True
                        vp = str(u.get("video") or "").strip()
                        if vp:
                            idx.add_path_key(vp)
                            idx.vid_to_paths.setdefault(vid, set()).add(_canonical_media_path(vp))
                if success:
                    for u in uploads:
                        if not isinstance(u, dict):
                            continue
                        vp = str(u.get("video") or "").strip()
                        if vp and u.get("ok"):
                            idx.add_path_key(vp)
                    for p in dj.get("picked") or []:
                        if isinstance(p, dict):
                            vp = str(p.get("video") or p.get("path") or "").strip()
                            if vp:
                                idx.add_path_key(vp)
                            ck2 = str(p.get("content_key") or "").strip()
                            if ck2:
                                idx.content_keys.add(ck2)
                            qh2 = str(p.get("quick_hash") or "").strip()
                            if qh2:
                                idx.quick_hashes.add(qh2)
                    nv = str(dj.get("next_video") or "").strip()
                    if nv and (uploaded_flag or top_vid):
                        idx.add_path_key(nv)

            for upath in root.rglob("upload_result.json"):
                try:
                    uj = json.loads(upath.read_text(encoding="utf-8", errors="replace"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(uj, dict):
                    continue
                vid = str(uj.get("video_id") or uj.get("youtube_video_id") or "").strip()
                uploaded_ok = bool(uj.get("ok")) and bool(vid)
                uploaded_flag = uj.get("uploaded") is True
                if not (uploaded_ok or uploaded_flag):
                    continue
                if vid:
                    idx.youtube_ids.add(vid)
                for vp in _upload_result_paths(uj):
                    idx.add_path_key(vp)
                    if vid:
                        idx.vid_to_paths.setdefault(vid, set()).add(_canonical_media_path(vp))
                sel = upath.parent / "selected_video_path.txt"
                if sel.is_file():
                    try:
                        for line in sel.read_text(encoding="utf-8", errors="replace").splitlines():
                            s = line.strip()
                            if s:
                                idx.add_path_key(s)
                                if vid:
                                    idx.vid_to_paths.setdefault(vid, set()).add(_canonical_media_path(s))
                                break
                    except OSError:
                        pass
        except OSError as exc:
            warnings.append(f"history_scan_oserror:{root}:{exc!r}")


def _is_used_candidate(meta: dict[str, Any], idx: DedupeIndexV2) -> tuple[bool, str]:
    rp = meta["resolved_path"]
    rp_key = _canonical_media_path(rp)
    src_key = _canonical_media_path(str(meta.get("source_path") or rp))
    qh = meta["quick_hash"]
    ck = meta["content_key"]
    if ck and ck in idx.content_keys:
        return True, "content_key"
    if qh and qh in idx.quick_hashes:
        return True, "quick_hash"
    if rp_key and rp_key in idx.resolved_path_keys:
        return True, "resolved_path"
    if src_key and src_key != rp_key and src_key in idx.resolved_path_keys:
        return True, "source_path"
    bn = normalize_basename(meta["basename"])
    sz = int(meta["size_bytes"])
    dsec = float(meta["duration_sec"])
    for obn, osz, odur in idx.basename_size_duration:
        if bn == normalize_basename(obn) and sz == osz and abs(dsec - odur) <= 2.0:
            return True, "basename_size_duration"
    return False, ""


def _try_acquire_lock(
    lock_path: Path,
    job_id: str,
    warnings: list[str],
    *,
    mode: str = "upload",
) -> tuple[bool, str | None]:
    payload = {
        "job_id": job_id,
        "pid": os.getpid(),
        "created_at": _utc_iso(),
        "mode": mode,
    }
    if lock_path.is_file():
        try:
            prev = json.loads(lock_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            prev = {}
        pid = int(prev.get("pid") or 0)
        created = str(prev.get("created_at") or "")
        age = _parse_iso_age_seconds(created)
        alive = _pid_alive(pid)
        if alive:
            return False, "nyc_long_job_already_running"
        try:
            m_age = max(0.0, time.time() - lock_path.stat().st_mtime)
        except OSError:
            m_age = 0.0
        eff_age = age if age is not None else m_age
        if not alive and pid > 0:
            try:
                lock_path.unlink(missing_ok=True)
                if eff_age > STALE_LOCK_SEC:
                    warnings.append("stale_nyc_long_upload_lock_removed")
                else:
                    warnings.append("dead_pid_nyc_long_upload_lock_removed")
            except OSError as exc:
                warnings.append(f"lock_remove_failed:{exc!r}")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        warnings.append(f"lock_write_failed:{exc!r}")
        return False, "lock_write_failed"
    return True, None


def _release_lock(lock_path: Path) -> None:
    try:
        if not lock_path.is_file():
            return
        try:
            prev = json.loads(lock_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            lock_path.unlink(missing_ok=True)
            return
        if int(prev.get("pid") or 0) == os.getpid():
            lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def _build_unified_base(
    *,
    job_id: str,
    status: str,
    privacy_status: str,
    ledger_path: str,
    fallback_used: bool,
    warnings: list[str],
    errors: list[str],
    block_reason: str = "",
) -> dict[str, Any]:
    now = _utc_iso()
    return {
        "job_id": job_id,
        "status": status,
        "video_type": "long",
        "channel": "NYC_LONG",
        "privacy_status": privacy_status,
        "picked": [],
        "uploads": [],
        "uploaded": False,
        "youtube_video_id": "",
        "youtube_url": "",
        "block_reason": block_reason,
        "warnings": list(warnings),
        "errors": list(errors),
        "ledger_path": ledger_path,
        "fallback_used": bool(fallback_used),
        "created_at": now,
        "finished_at": "",
    }


def _load_long_emergency_state(xfer: Path, warnings: list[str]) -> dict[str, Any]:
    p = xfer / "publish_pack" / "nyc_long_uploads" / "long_autopublish_emergency.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"long_emergency_json_unreadable:{exc!r}")
        return {}


def _load_long_rejected_outputs(xfer: Path, warnings: list[str]) -> list[dict[str, Any]]:
    p = xfer / "publish_pack" / "nyc_long_uploads" / "long_rejected_outputs.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        ent = data.get("entries") if isinstance(data, dict) else None
        if isinstance(ent, list):
            return [x for x in ent if isinstance(x, dict)]
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"long_rejected_outputs_unreadable:{exc!r}")
    return []


def _long_rejected_path_keys(entries: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for e in entries:
        for k in ("quarantine_path", "resolved_path", "path"):
            raw = str(e.get(k) or "").strip()
            if not raw:
                continue
            keys.add(raw)
            keys.add(_canonical_media_path(raw))
    return keys


def _auto_review_forbidden_path_slurp(vid: Path, *, allow_raw: bool) -> str | None:
    s = str(vid).replace("\\", "/").lower()
    if _is_shorts_path(vid):
        return "shorts_path_marker"
    for frag in ("/video916/", "/picture916/", "/picture169/", "/photos/", "/images/"):
        if frag in s:
            return f"forbidden_segment:{frag.strip('/')}"
    parts_lower = {p.lower() for p in vid.parts}
    if "_rejected_long_outputs" in parts_lower:
        return "rejected_outputs_quarantine_tree"
    if not allow_raw:
        if "video169" in parts_lower and "nyc_long_clips" not in parts_lower:
            return "raw_video169_tree"
        if "raw" in parts_lower and "nyc_long_clips" not in parts_lower:
            return "raw_subtree"
    return None


def _auto_review_sidecar_publish_unsafe(vid: Path) -> bool:
    if "clean_real_sound" not in vid.name.lower():
        return False
    parent = vid.parent
    candidates: list[Path] = [
        parent / f"{vid.stem}_real_sound_quality_report.json",
        parent / "real_sound_quality_report.json",
    ]
    try:
        candidates.extend(parent.glob(f"*{vid.stem}*real_sound*report*.json"))
        candidates.extend(parent.glob("*real_sound*quality*report*.json"))
    except OSError:
        pass
    seen: set[str] = set()
    for rep in candidates:
        try:
            rk = str(rep.resolve())
        except OSError:
            rk = str(rep)
        if rk in seen or not rep.is_file():
            continue
        seen.add(rk)
        try:
            data = json.loads(rep.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("publish_safe") is False:
            return True
    return False


def _auto_review_rejected_basename_audio_flag(vid: Path, rej_entries: list[dict[str, Any]]) -> bool:
    nm = vid.name.lower()
    hits = ("audio_damaged", "cleanup", "real_sound", "publish_safe", "clean_real_sound", "noise_gate")
    for e in rej_entries:
        p = Path(str(e.get("path") or e.get("resolved_path") or ""))
        if p.name.lower() != nm:
            continue
        blob = " ".join(str(x) for x in (e.get("reasons") or [])).lower()
        if any(h in blob for h in hits):
            return True
    return False


def _collect_auto_review_scan_rows(
    ready: Path, *, allow_raw: bool, warnings: list[str]
) -> list[tuple[int, float, Path]]:
    """Tier 0 = nyc_long_clips, tier 1 = other under ready_to_upload; sort newest mtime first per tier."""
    rows: list[tuple[int, float, Path]] = []
    if not ready.is_dir():
        warnings.append("ready_to_upload_missing")
        return rows
    a_root = ready / "nyc_long_clips"
    if a_root.is_dir():
        try:
            for p in a_root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS or p.name.startswith("."):
                    continue
                if _auto_review_forbidden_path_slurp(p, allow_raw=allow_raw):
                    continue
                try:
                    rows.append((0, float(p.stat().st_mtime), p))
                except OSError:
                    continue
        except OSError as exc:
            warnings.append(f"auto_review_scan_a:{exc!r}")
    try:
        for p in ready.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS or p.name.startswith("."):
                continue
            if {x.lower() for x in p.parts}.intersection({"nyc_long_clips"}):
                continue
            if _auto_review_forbidden_path_slurp(p, allow_raw=allow_raw):
                continue
            try:
                rows.append((1, float(p.stat().st_mtime), p))
            except OSError:
                continue
    except OSError as exc:
        warnings.append(f"auto_review_scan_b:{exc!r}")
    rows.sort(key=lambda r: (r[0], -r[1]))
    return rows


def _auto_review_upload_print_summary(
    *,
    candidates_count: int,
    selected: str,
    selected_reason: str,
    uploaded: bool,
    video_id: str,
    url: str,
    privacy: str,
    channel_title: str,
    result_path: str,
    block_reason: str,
) -> None:
    print(f"AUTO_REVIEW_UPLOAD_CANDIDATES={candidates_count}")
    print(f"AUTO_REVIEW_SELECTED_VIDEO={selected}")
    print(f"AUTO_REVIEW_SELECTED_REASON={selected_reason}")
    print(f"uploaded={'true' if uploaded else 'false'}")
    print(f"youtube_video_id={video_id}")
    print(f"youtube_url={url}")
    print(f"privacy_status={privacy}")
    print(f"channel_title={channel_title}")
    print("auto_review_upload=true")
    print("autopublish_restored=false")
    print("requires_review_before_public=true")
    print(f"result_path={result_path}")
    print(f"block_reason={block_reason}")


def _long_upload_emergency_active(em: dict[str, Any]) -> bool:
    if not em:
        return False
    if em.get("LONG_AUTOPUBLISH_DISABLED") is True:
        return True
    if em.get("SAFE_TO_ENABLE_LONG_UPLOAD") is False:
        return True
    return False


def _ledger_rejected_facets(entries: list[dict[str, Any]]) -> dict[str, int]:
    vertical = walking = mixed_aspect = mixed_source = 0
    for e in entries:
        blob = " ".join(str(x) for x in (e.get("reasons") or [])).lower()
        if any(x in blob for x in ("9_16", "vertical", "portrait")):
            vertical += 1
        if any(x in blob for x in ("walking", "handheld", "vlog")):
            walking += 1
        if "mixed_aspect" in blob or "9_16" in blob:
            mixed_aspect += 1
        if "mixed_walking" in blob or "mixed_source" in blob:
            mixed_source += 1
    return {
        "vertical": vertical,
        "walking": walking,
        "mixed_aspect": mixed_aspect,
        "mixed_source": mixed_source,
    }


def _scan_rejected_facets(rows: list[dict[str, Any]]) -> dict[str, int]:
    vertical = walking = mixed_aspect = mixed_source = 0
    for row in rows:
        rrs = [str(x) for x in (row.get("reject_reasons") or [])]
        rset = set(rrs)
        if "rejected_vertical_video" in rset or str(row.get("orientation") or "") == "portrait":
            vertical += 1
        elif "rejected_bad_aspect_ratio" in rset or "square_not_allowed" in str(row.get("reason") or ""):
            mixed_aspect += 1
        if "walking_not_allowed_for_nyc_long_channel" in rset:
            walking += 1
        if "rejected_long_source_type_not_allowed" in rset:
            mixed_source += 1
    return {
        "vertical": vertical,
        "walking": walking,
        "mixed_aspect": mixed_aspect,
        "mixed_source": mixed_source,
    }


def _merge_nyc_long_schedule_fields(
    base_out: dict[str, Any],
    *,
    force: bool = False,
    schedule_kind: str = "1h",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Attach schedule plan + gate; dry-run planning only when emergency blocks upload."""
    try:
        gate = evaluate_publish_schedule_gate(kind=schedule_kind, force=force)
        meta = schedule_metadata_for_job(
            {
                "nyc_long_schedule_gate_allowed": gate.get("allowed"),
                "nyc_long_schedule_gate_reason": gate.get("reason"),
                "nyc_long_picked_music_path": gate.get("picked_music_path"),
            }
        )
        base_out.update(meta)
        base_out["nyc_long_schedule_gate"] = gate
        if warnings is not None and not gate.get("allowed"):
            warnings.append(str(gate.get("reason") or "schedule_gate_blocked"))
        return gate
    except Exception as exc:  # noqa: BLE001
        if warnings is not None:
            warnings.append(f"nyc_long_schedule_merge_failed:{exc!r}")
        return {"allowed": True, "reason": "schedule_merge_failed_fail_open"}


def _apply_long_emergency_dry_run_out_fields(
    base_out: dict[str, Any],
    emergency_state: dict[str, Any],
    *,
    warnings: list[str],
) -> None:
    if not _long_upload_emergency_active(emergency_state):
        return
    br = str(emergency_state.get("block_reason") or "bad_audio_and_mixed_source_policy_review_required")
    base_out["LONG_AUTOPUBLISH_DISABLED"] = bool(emergency_state.get("LONG_AUTOPUBLISH_DISABLED", True))
    base_out["SAFE_TO_ENABLE_LONG_UPLOAD"] = False
    base_out["safe_to_enable_long_upload"] = False
    base_out["status"] = "blocked"
    base_out["LONG_DRY_RUN_STATUS"] = "blocked"
    base_out["block_reason"] = br
    base_out["LONG_NEXT_CANDIDATE"] = ""
    base_out["next_video"] = ""
    base_out["selected_video_path"] = ""
    warnings.append("long_dry_run_emergency_overlay_active")


def _auto_review_upload_main(args: argparse.Namespace) -> int:
    """Pick newest safe ready_to_upload clip and upload unlisted for Studio review; never lifts autopublish emergency."""
    warnings: list[str] = []
    errors: list[str] = []
    ensure_publish_v2_dirs(warnings)
    ready = get_transfer_ready_to_upload(verbose=False)
    xfer = get_sv_transfer(verbose=False)
    emergency_state = _load_long_emergency_state(xfer, warnings)
    pack_parent, ledger_path, lock_path, pack_fallback = _resolve_pack_ledger_lock(warnings)
    hist_roots = [
        xfer / "publish_pack" / "nyc_long_uploads",
        HOME_LONG_UPLOADS,
    ]
    primary_ledger = xfer / "publish_pack" / "nyc_long_uploads" / LEDGER_NAME
    home_ledger = HOME_LONG_UPLOADS / LEDGER_NAME
    ledger_data = _load_ledger(ledger_path)
    idx = DedupeIndexV2()
    seen_ledger: set[str] = set()
    for lp in (primary_ledger, home_ledger, ledger_path):
        lk = str(lp)
        if lk in seen_ledger:
            continue
        seen_ledger.add(lk)
        if lp.is_file():
            _ingest_ledger_entries(_load_ledger(lp), idx)
    _scan_history_job_jsons(hist_roots, idx, warnings)
    rej_entries = _load_long_rejected_outputs(xfer, warnings)
    rej_keys = _long_rejected_path_keys(rej_entries)
    allow_raw = bool(getattr(args, "allow_raw_review_upload", False))
    scan_rows = _collect_auto_review_scan_rows(ready, allow_raw=allow_raw, warnings=warnings)
    eligible: list[tuple[Path, dict[str, Any], str, str]] = []
    for tier, _mtime_key, vid in scan_rows:
        sk = str(vid)
        try:
            vkey = _canonical_media_path(str(vid.resolve()))
        except OSError:
            vkey = _canonical_media_path(sk)
        if vkey in rej_keys or sk in rej_keys:
            continue
        tier_reason = "priority_a_ready_clip" if tier == 0 else "priority_b_ready_clip"
        if _auto_review_sidecar_publish_unsafe(vid):
            warnings.append(f"auto_review_skip_sidecar_unsafe:{vid.name}")
            continue
        if _auto_review_rejected_basename_audio_flag(vid, rej_entries):
            warnings.append(f"auto_review_skip_rejected_basename:{vid.name}")
            continue
        try:
            sz = int(vid.stat().st_size)
        except OSError:
            continue
        if sz < MIN_BYTES:
            continue
        probe = _ffprobe_json(vid)
        dur, has_v = _duration_and_has_video(probe)
        if probe is None or not has_v or dur is None:
            continue
        dur_f = float(dur)
        if dur_f < float(MANUAL_REVIEW_UPLOAD_MIN_DURATION_SEC):
            continue
        vw, vh = _primary_video_dims(probe)
        if vw > 0 and vh > 0 and vh > vw:
            continue
        qh = triple_chunk_sha256(vid)
        bn_norm = normalize_basename(vid.name)
        ck = content_key_v2(size_bytes=sz, duration_sec=dur_f, quick_hash=qh, basename_normalized=bn_norm)
        item: dict[str, Any] = {
            "path": vid,
            "source_path": str(vid),
            "resolved_path": vkey,
            "basename": vid.name,
            "stem": vid.stem,
            "size_bytes": sz,
            "mtime": int(vid.stat().st_mtime),
            "duration_sec": dur_f,
            "quick_hash": qh,
            "content_key": ck,
            "video_width": vw,
            "video_height": vh,
        }
        used, _ = _is_used_candidate(item, idx)
        if used:
            continue
        eligible.append((vid, item, tier_reason, vkey))

    job_id = uuid.uuid4().hex[:16]
    job_dir = pack_parent / job_id
    result_json_path = str(job_dir / "long_job_result.json")
    cand_n = len(eligible)
    sel_path = ""
    sel_reason = ""
    if eligible:
        sel_path = str(eligible[0][0])
        sel_reason = str(eligible[0][2])

    base_out = _build_unified_base(
        job_id=job_id,
        status="running",
        privacy_status="unlisted",
        ledger_path=str(ledger_path),
        fallback_used=pack_fallback,
        warnings=warnings,
        errors=errors,
        block_reason="",
    )
    base_out["emergency_override"] = False
    base_out["manual_review_upload"] = False
    base_out["auto_review_upload"] = True
    base_out["autopublish_restored"] = False
    base_out["requires_review_before_public"] = True
    base_out["LONG_AUTOPUBLISH_DISABLED"] = bool(emergency_state.get("LONG_AUTOPUBLISH_DISABLED"))
    base_out["SAFE_TO_ENABLE_LONG_UPLOAD"] = False
    base_out["safe_to_enable_long_upload"] = False
    base_out["long_autopublish_emergency_path"] = str(
        xfer / "publish_pack" / "nyc_long_uploads" / "long_autopublish_emergency.json"
    )
    base_out["AUTO_REVIEW_UPLOAD_CANDIDATES"] = int(cand_n)
    base_out["AUTO_REVIEW_SELECTED_VIDEO"] = sel_path
    base_out["AUTO_REVIEW_SELECTED_REASON"] = sel_reason
    base_out["selected_video_path"] = sel_path
    base_out["review_queue_added"] = False
    base_out["review_status"] = "dry_run" if args.dry_run else "awaiting_upload"
    base_out["auto_public_upload_disabled"] = True

    token_path, tok_warn = resolve_long_form_upload_token(None)
    for w in tok_warn:
        warnings.append(w)
    sec = resolve_client_secrets(args.client_secrets)
    token_used = str(token_path)
    sec_used = str(sec)

    meta_job = {
        "job_id": job_id,
        "channel": "NYC_LONG",
        "video_type": "long",
        "privacy_status": "unlisted",
        "token_path": token_used,
        "client_secrets_path": sec_used,
        "ledger_path": str(ledger_path),
        "lock_path": str(lock_path),
        "manual_review_upload": False,
        "auto_review_upload": True,
    }

    def _print_auto(
        *,
        uploaded: bool,
        video_id: str,
        url: str,
        privacy: str,
        channel_title: str,
        result_path: str,
        block_reason: str,
    ) -> None:
        _auto_review_upload_print_summary(
            candidates_count=cand_n,
            selected=sel_path,
            selected_reason=sel_reason,
            uploaded=uploaded,
            video_id=video_id,
            url=url,
            privacy=privacy,
            channel_title=channel_title,
            result_path=result_path,
            block_reason=block_reason,
        )

    def _fail(block: str, msg: str | None = None) -> int:
        base_out["status"] = "blocked"
        base_out["block_reason"] = block
        if msg:
            base_out["errors"].append(msg)
        base_out["finished_at"] = _utc_iso()
        payload = {**base_out, "meta": meta_job}
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "long_job_result.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.append(f"long_job_result_write_failed:{exc!r}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        _print_auto(
            uploaded=False,
            video_id="",
            url="",
            privacy="unlisted",
            channel_title="",
            result_path=result_json_path,
            block_reason=block,
        )
        return 0

    try:
        if token_path.resolve() == OFFICIAL_SHORTS_TOKEN_PATH.resolve():
            return _fail("long_queue_must_not_use_token_shorts", "token_shorts.json")
    except OSError:
        pass

    ok_long_tok, tok_reason = validate_long_channel_token(token_path)
    if not ok_long_tok:
        return _fail("channel_guard_failed", tok_reason or "long_token_invalid")

    if not eligible:
        return _fail("no_safe_review_upload_candidate")

    vid, item, _tier_reason, vkey = eligible[0]
    sk = str(vid)
    dur_f = float(item["duration_sec"])
    sz = int(item["size_bytes"])
    qh = str(item["quick_hash"])
    ck = str(item["content_key"])
    vw = int(item.get("video_width") or 0)
    vh = int(item.get("video_height") or 0)

    guard = assert_long_upload_context(vid, token_path)
    if guard:
        return _fail("channel_guard_failed", json.dumps(guard, ensure_ascii=False))

    try:
        job_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fail("job_dir_unwritable", str(exc))

    if args.dry_run:
        if lock_path.is_file():
            try:
                data = json.loads(lock_path.read_text(encoding="utf-8", errors="replace"))
                if _pid_alive(int(data.get("pid") or 0)):
                    return _fail("nyc_long_job_already_running")
            except (OSError, json.JSONDecodeError):
                warnings.append("nyc_long_upload_lock_present_unparsed")
        base_out["status"] = "auto_review_dry_run_ok"
        base_out["LONG_DRY_RUN_STATUS"] = "auto_review_dry_run_ok"
        base_out["finished_at"] = _utc_iso()
        base_out["picked"] = [
            {
                "video": vkey,
                "content_key": ck,
                "quick_hash": qh,
                "would_upload": True,
                "privacy_status": "unlisted",
                "auto_review_upload": True,
            }
        ]
        payload = {**base_out, "meta": meta_job}
        try:
            (job_dir / "long_job_result.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.append(f"long_job_result_write_failed:{exc!r}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        _print_auto(
            uploaded=False,
            video_id="",
            url="",
            privacy="unlisted",
            channel_title="",
            result_path=result_json_path,
            block_reason="",
        )
        return 0

    lock_acquired = False
    try:
        from doctor_gate_client import (  # type: ignore
            check_real_upload_allowed_by_doctor,
            log_doctor_gate_block_upload,
        )

        allowed, detail, gw = check_real_upload_allowed_by_doctor(dry_run=False)
        warnings.extend(gw)
        if not allowed:
            base_out["blocked_by_doctor_gate"] = True
            log_doctor_gate_block_upload()
            return _fail("doctor_gate_block_upload", json.dumps(detail, ensure_ascii=False))

        ok_lock, reason = _try_acquire_lock(lock_path, job_id, warnings, mode="auto_review_upload")
        if not ok_lock:
            return _fail(reason or "nyc_long_job_already_running")
        lock_acquired = True

        try:
            used_now, used_reason = _is_used_candidate(item, idx)
            from doctor_gate_client import check_upload_safety_selected_file  # type: ignore

            selected_gate = check_upload_safety_selected_file(
                vid.resolve(),
                mode="long",
                dry_run=False,
                public_upload_requested=False,
                explicit_public_confirmation=False,
                target_upload_dir=job_dir,
                dedupe_hit=bool(used_now),
                already_uploaded=bool(used_now and used_reason in {"resolved_path", "source_path"}),
            )
            base_out["selected_file_check"] = selected_gate.get("selected_file_check")
            if selected_gate.get("warning_reasons"):
                warnings.extend([f"selected_gate:{w}" for w in selected_gate.get("warning_reasons") or []])
            if not bool(selected_gate.get("upload_allowed", True)):
                base_out["blocked_by_selected_file_gate"] = True
                base_out["selected_gate_detail"] = selected_gate
                return _fail("selected_file_gate_block_upload", "selected_file_gate")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"selected_file_gate_check_failed:{type(exc).__name__}:{exc!r}")

        single_job = job_dir / "auto_review_pack"
        try:
            single_job.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _fail("job_dir_unwritable", str(exc))

        upload_path = vid.resolve()
        upload_path, daf_meta, daf_block = _apply_davinci_youtube_audio_finish(
            upload_path, content_kind="long", warnings=warnings
        )
        if daf_block:
            return _fail(daf_block, "davinci_audio_finish_required")
        if daf_meta:
            base_out["davinci_audio_finish"] = daf_meta
        (single_job / "selected_video_path.txt").write_text(str(upload_path) + "\n", encoding="utf-8")

        uo = {"audio_mode": "music"}
        si = {"basename": vid.name, "stem": vid.stem, "vkey": vkey, "duration_sec": dur_f}
        policy = infer_long_audio_policy(vid, metadata=None, source_info=si, user_override=uo)
        cr_bundle = _content_routing_bundle_for_video(vid, job_id=job_id, source_info=si)
        title_fallback = _title_from_filename(vid)
        extra_merge: dict[str, Any] = {
            "source_video": vkey,
            "upload_video": str(upload_path),
            "duration_seconds": dur_f,
            "job_id": job_id,
            "categoryId": "22",
            "privacy_status": "unlisted",
            "manual_review_upload": False,
            "auto_review_upload": True,
            "emergency_override": False,
            "autopublish_restored": False,
            "requires_review_before_public": True,
        }
        _merge_content_routing_into_extra(extra_merge, cr_bundle)
        source_json = None
        src_policy_path = _REPO / "data" / "nyc_long_driving_sources.json"
        if src_policy_path.is_file():
            try:
                source_json = json.loads(src_policy_path.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError:
                source_json = None
        try:
            meta_long, _mp = ensure_youtube_metadata_file(
                single_job,
                upload_path,
                video_type="long",
                real_sound_report=None,
                source_json=source_json,
                job_result_json=None,
                style="auto",
                use_ai_metadata=bool(getattr(args, "use_ai_metadata", False)),
                dry_run=False,
                extra_merge=extra_merge,
                long_audio_policy=policy,
            )
            sync_legacy_package_files(single_job, meta_long)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"metadata_generation_exception:{exc!r}")
            meta_long = {
                "metadata_generated": False,
                "metadata_generator_version": "",
                "title": title_fallback,
                "description": DEFAULT_DESC,
                "tags": [t.strip() for t in DEFAULT_TAGS.split(",") if t.strip()],
                "video_type": "long",
                "warnings": ["metadata_generation_failed"],
            }
            try:
                (single_job / "youtube_metadata.json").write_text(
                    json.dumps({**extra_merge, **meta_long}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass

        gen_title = str((meta_long or {}).get("title") or title_fallback)

        res = upload_from_package_directory(
            single_job,
            privacy="unlisted",
            allow_public=False,
            title=None,
            description=None,
            tags_str=None,
            category_id="22",
            made_for_kids=False,
            dry_run=False,
            token_path=token_path,
            client_secrets=sec,
            force_reupload=False,
            allow_test_assets=False,
            token_path_used=token_used,
            client_secrets_path_used=sec_used,
            use_ai_metadata=bool(getattr(args, "use_ai_metadata", False)),
            agent_upload=False,
            review_queue=bool(getattr(args, "review_queue", True)),
            force_private=False,
            channel_type="long",
            channel_guard_status="passed_auto_review_upload",
            dedupe_status="passed_v2_dedupe_index",
            dedupe_key=str(ck),
            automation_job_id=job_id,
        )

        uentry = {
            "video": vkey,
            "ok": res.ok,
            "status": res.status,
            "video_id": res.video_id,
            "error": res.error,
        }
        base_out["uploads"] = [uentry]

        if res.ok and res.status == "success" and res.video_id:
            base_out["uploaded"] = True
            base_out["youtube_video_id"] = str(res.video_id)
            base_out["youtube_url"] = f"https://www.youtube.com/watch?v={res.video_id}"
            base_out["status"] = "uploaded"
            base_out["privacy_status"] = str(res.privacy_used or "unlisted")
            base_out["review_queue_added"] = bool(res.review_queue_path)
            base_out["review_status"] = "pending"
            base_out["requires_review_before_public"] = True
            base_out["manual_review_upload"] = False
            base_out["auto_review_upload"] = True
            base_out["emergency_override"] = False
            base_out["autopublish_restored"] = False
            if bool(getattr(args, "review_queue", True)) and not res.review_queue_path:
                warnings.append("upload_success_missing_review_queue_entry")
            now_ent = {
                "source_path": str(vid),
                "resolved_path": vkey,
                "basename": vid.name,
                "stem": vid.stem,
                "size_bytes": sz,
                "mtime": int(vid.stat().st_mtime),
                "duration_sec": dur_f,
                "quick_hash": qh,
                "content_key": ck,
                "youtube_video_id": str(res.video_id),
                "youtube_url": base_out["youtube_url"],
                "uploaded": True,
                "privacy_status": "unlisted",
                "review_status": "pending",
                "channel": "NYC_LONG",
                "job_id": job_id,
                "uploaded_at": _utc_iso(),
                "title": gen_title,
                "result_path": str(single_job / "upload_result.json"),
                "manual_review_upload": False,
                "auto_review_upload": True,
                "emergency_override": False,
                "autopublish_restored": False,
                "requires_review_before_public": True,
            }
            entries = ledger_data.get("entries") if isinstance(ledger_data.get("entries"), list) else []
            entries.append(now_ent)
            ledger_data["entries"] = entries
            ledger_data["version"] = 2
            ledger_data["updated_at"] = _utc_iso()
            _save_ledger(ledger_path, ledger_data, warnings)
            idx.content_keys.add(ck)
            idx.quick_hashes.add(qh)
            idx.add_path_key(vkey)
            idx.add_path_key(str(vid))
            ur: dict[str, Any] = {
                "ok": True,
                "video_id": res.video_id,
                "youtube_video_id": res.video_id,
                "url": base_out["youtube_url"],
                "privacy_used": res.privacy_used or res.privacy,
                "title_used": res.title_used or res.title,
                "description_used": res.description_used,
                "tags_used": res.tags_used,
                "metadata_path": res.metadata_path,
                "channel_guard_status": res.channel_guard_status,
                "dedupe_status": res.dedupe_status,
                "review_queue_path": res.review_queue_path,
                "review_queue_md_path": res.review_queue_md_path,
                "agent_uploaded": res.agent_uploaded,
                "human_review_required": res.human_review_required,
                "forced_private": res.forced_private,
                "manual_review_upload": False,
                "auto_review_upload": True,
                "emergency_override": False,
                "autopublish_restored": False,
                "requires_review_before_public": True,
            }
            (single_job / "upload_result.json").write_text(
                json.dumps(ur, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            base_out["finished_at"] = _utc_iso()
            out_payload = {**base_out, "meta": meta_job}
            try:
                (job_dir / "long_job_result.json").write_text(
                    json.dumps(out_payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                warnings.append(f"long_job_result_write_failed:{exc!r}")
            print(json.dumps(out_payload, indent=2, ensure_ascii=False))
            _print_auto(
                uploaded=True,
                video_id=str(res.video_id),
                url=base_out["youtube_url"],
                privacy=str(res.privacy_used or "unlisted"),
                channel_title=str(res.upload_channel_title or ""),
                result_path=str(single_job / "upload_result.json"),
                block_reason="",
            )
            return 0

        base_out["status"] = "upload_failed"
        base_out["review_status"] = "upload_failed"
        base_out["errors"].append(res.error or res.status or "upload_failed")
        base_out["finished_at"] = _utc_iso()
        err_payload = {**uentry, "uploaded": False}
        try:
            (single_job / "upload_error.json").write_text(
                json.dumps(err_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
        out_payload = {**base_out, "meta": meta_job}
        try:
            (job_dir / "long_job_result.json").write_text(
                json.dumps(out_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.append(f"long_job_result_write_failed:{exc!r}")
        print(json.dumps(out_payload, indent=2, ensure_ascii=False))
        _print_auto(
            uploaded=False,
            video_id="",
            url="",
            privacy="unlisted",
            channel_title=str(res.upload_channel_title or ""),
            result_path=str(single_job / "upload_error.json"),
            block_reason=str(res.error or res.status or "upload_failed"),
        )
        return 1
    finally:
        if lock_acquired:
            _release_lock(lock_path)


def _manual_review_upload_print_result_lines(
    *,
    uploaded: bool,
    video_id: str,
    url: str,
    privacy: str,
    channel_title: str,
    result_path: str,
) -> None:
    print(f"uploaded={'true' if uploaded else 'false'}")
    print(f"youtube_video_id={video_id}")
    print(f"youtube_url={url}")
    print(f"privacy_status={privacy}")
    print(f"channel_title={channel_title}")
    print("manual_review_upload=true")
    print("autopublish_restored=false")
    print("requires_review_before_public=true")
    print(f"result_path={result_path}")


def _manual_review_upload_main(args: argparse.Namespace, video_path_str: str) -> int:
    """Single unlisted long upload for YouTube Studio review; never lifts autopublish emergency."""
    warnings: list[str] = []
    errors: list[str] = []
    ensure_publish_v2_dirs(warnings)

    xfer = get_sv_transfer(verbose=False)
    emergency_state = _load_long_emergency_state(xfer, warnings)
    pack_parent, ledger_path, lock_path, pack_fallback = _resolve_pack_ledger_lock(warnings)

    hist_roots = [
        xfer / "publish_pack" / "nyc_long_uploads",
        HOME_LONG_UPLOADS,
    ]
    primary_ledger = xfer / "publish_pack" / "nyc_long_uploads" / LEDGER_NAME
    home_ledger = HOME_LONG_UPLOADS / LEDGER_NAME
    ledger_data = _load_ledger(ledger_path)
    idx = DedupeIndexV2()
    seen_ledger: set[str] = set()
    for lp in (primary_ledger, home_ledger, ledger_path):
        lk = str(lp)
        if lk in seen_ledger:
            continue
        seen_ledger.add(lk)
        if lp.is_file():
            _ingest_ledger_entries(_load_ledger(lp), idx)
    _scan_history_job_jsons(hist_roots, idx, warnings)

    rej_entries = _load_long_rejected_outputs(xfer, warnings)
    rej_keys = _long_rejected_path_keys(rej_entries)

    vid = Path(video_path_str).expanduser()
    sk = str(vid)
    try:
        vkey = _canonical_media_path(str(vid.resolve()))
    except OSError:
        vkey = _canonical_media_path(sk)

    job_id = uuid.uuid4().hex[:16]
    job_dir = pack_parent / job_id
    result_json_path = str(job_dir / "long_job_result.json")

    base_out = _build_unified_base(
        job_id=job_id,
        status="running",
        privacy_status="unlisted",
        ledger_path=str(ledger_path),
        fallback_used=pack_fallback,
        warnings=warnings,
        errors=errors,
        block_reason="",
    )
    base_out["emergency_override"] = False
    base_out["manual_review_upload"] = True
    base_out["autopublish_restored"] = False
    base_out["requires_review_before_public"] = True
    base_out["LONG_AUTOPUBLISH_DISABLED"] = bool(emergency_state.get("LONG_AUTOPUBLISH_DISABLED"))
    base_out["SAFE_TO_ENABLE_LONG_UPLOAD"] = False
    base_out["safe_to_enable_long_upload"] = False
    base_out["long_autopublish_emergency_path"] = str(
        xfer / "publish_pack" / "nyc_long_uploads" / "long_autopublish_emergency.json"
    )
    base_out["selected_video_path"] = sk
    base_out["review_queue_added"] = False
    base_out["review_status"] = "dry_run" if args.dry_run else "awaiting_upload"
    base_out["auto_public_upload_disabled"] = True

    token_path, tok_warn = resolve_long_form_upload_token(None)
    for w in tok_warn:
        warnings.append(w)
    sec = resolve_client_secrets(args.client_secrets)
    token_used = str(token_path)
    sec_used = str(sec)

    meta_job = {
        "job_id": job_id,
        "channel": "NYC_LONG",
        "video_type": "long",
        "privacy_status": "unlisted",
        "token_path": token_used,
        "client_secrets_path": sec_used,
        "ledger_path": str(ledger_path),
        "lock_path": str(lock_path),
        "manual_review_upload": True,
    }

    def _fail(block: str, msg: str | None = None) -> int:
        base_out["status"] = "blocked"
        base_out["block_reason"] = block
        if msg:
            base_out["errors"].append(msg)
        base_out["finished_at"] = _utc_iso()
        payload = {**base_out, "meta": meta_job}
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "long_job_result.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.append(f"long_job_result_write_failed:{exc!r}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        _manual_review_upload_print_result_lines(
            uploaded=False,
            video_id="",
            url="",
            privacy="unlisted",
            channel_title="",
            result_path=result_json_path,
        )
        return 0

    try:
        if token_path.resolve() == OFFICIAL_SHORTS_TOKEN_PATH.resolve():
            return _fail("long_queue_must_not_use_token_shorts", "token_shorts.json")
    except OSError:
        pass

    ok_long_tok, tok_reason = validate_long_channel_token(token_path)
    if not ok_long_tok:
        return _fail("channel_guard_failed", tok_reason or "long_token_invalid")

    if not vid.is_file():
        return _fail("video_not_found", f"not_a_file:{sk}")
    if vid.suffix.lower() not in VIDEO_EXTS:
        return _fail("video_extension_not_allowed", f"suffix:{vid.suffix}")

    try:
        sz = int(vid.stat().st_size)
    except OSError as exc:
        return _fail("stat_failed", str(exc))
    if sz < MIN_BYTES:
        return _fail("file_too_small", f"size_bytes={sz}")

    probe = _ffprobe_json(vid)
    dur, has_v = _duration_and_has_video(probe)
    if probe is None or not has_v or dur is None:
        return _fail("ffprobe_failed", "no_video_or_duration")
    dur_f = float(dur)
    if dur_f < float(MANUAL_REVIEW_UPLOAD_MIN_DURATION_SEC):
        return _fail("duration_below_min", f"duration_sec={dur_f}")

    if vkey in rej_keys or sk in rej_keys:
        return _fail("long_rejected_outputs_hit", sk)

    vw, vh = _primary_video_dims(probe)
    if vw > 0 and vh > 0 and vh > vw:
        return _fail("portrait_video_not_allowed_for_long_channel")

    qh = triple_chunk_sha256(vid)
    bn_norm = normalize_basename(vid.name)
    ck = content_key_v2(size_bytes=sz, duration_sec=dur_f, quick_hash=qh, basename_normalized=bn_norm)
    item: dict[str, Any] = {
        "path": vid,
        "source_path": str(vid),
        "resolved_path": vkey,
        "basename": vid.name,
        "stem": vid.stem,
        "size_bytes": sz,
        "mtime": int(vid.stat().st_mtime),
        "duration_sec": dur_f,
        "quick_hash": qh,
        "content_key": ck,
        "video_width": vw,
        "video_height": vh,
    }
    used, ureason = _is_used_candidate(item, idx)
    if used:
        return _fail("dedupe_ledger_hit", ureason)

    guard = assert_long_upload_context(vid, token_path)
    if guard:
        return _fail("channel_guard_failed", json.dumps(guard, ensure_ascii=False))

    try:
        job_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fail("job_dir_unwritable", str(exc))

    if args.dry_run:
        if lock_path.is_file():
            try:
                data = json.loads(lock_path.read_text(encoding="utf-8", errors="replace"))
                if _pid_alive(int(data.get("pid") or 0)):
                    return _fail("nyc_long_job_already_running")
            except (OSError, json.JSONDecodeError):
                warnings.append("nyc_long_upload_lock_present_unparsed")
        base_out["status"] = "manual_review_dry_run_ok"
        base_out["LONG_DRY_RUN_STATUS"] = "manual_review_dry_run_ok"
        base_out["finished_at"] = _utc_iso()
        base_out["picked"] = [
            {
                "video": vkey,
                "content_key": ck,
                "quick_hash": qh,
                "would_upload": True,
                "privacy_status": "unlisted",
            }
        ]
        payload = {**base_out, "meta": meta_job}
        try:
            (job_dir / "long_job_result.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.append(f"long_job_result_write_failed:{exc!r}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        _manual_review_upload_print_result_lines(
            uploaded=False,
            video_id="",
            url="",
            privacy="unlisted",
            channel_title="",
            result_path=result_json_path,
        )
        return 0

    lock_acquired = False
    try:
        from doctor_gate_client import (  # type: ignore
            check_real_upload_allowed_by_doctor,
            log_doctor_gate_block_upload,
        )

        allowed, detail, gw = check_real_upload_allowed_by_doctor(dry_run=False)
        warnings.extend(gw)
        if not allowed:
            base_out["blocked_by_doctor_gate"] = True
            log_doctor_gate_block_upload()
            return _fail("doctor_gate_block_upload", json.dumps(detail, ensure_ascii=False))

        ok_lock, reason = _try_acquire_lock(lock_path, job_id, warnings, mode="manual_review_upload")
        if not ok_lock:
            return _fail(reason or "nyc_long_job_already_running")
        lock_acquired = True

        try:
            used_now, used_reason = _is_used_candidate(item, idx)
            from doctor_gate_client import check_upload_safety_selected_file  # type: ignore

            selected_gate = check_upload_safety_selected_file(
                vid.resolve(),
                mode="long",
                dry_run=False,
                public_upload_requested=False,
                explicit_public_confirmation=False,
                target_upload_dir=job_dir,
                dedupe_hit=bool(used_now),
                already_uploaded=bool(used_now and used_reason in {"resolved_path", "source_path"}),
            )
            base_out["selected_file_check"] = selected_gate.get("selected_file_check")
            if selected_gate.get("warning_reasons"):
                warnings.extend([f"selected_gate:{w}" for w in selected_gate.get("warning_reasons") or []])
            if not bool(selected_gate.get("upload_allowed", True)):
                base_out["blocked_by_selected_file_gate"] = True
                base_out["selected_gate_detail"] = selected_gate
                return _fail("selected_file_gate_block_upload", "selected_file_gate")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"selected_file_gate_check_failed:{type(exc).__name__}:{exc!r}")

        single_job = job_dir / "manual_review_pack"
        try:
            single_job.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _fail("job_dir_unwritable", str(exc))

        upload_path = vid.resolve()
        upload_path, daf_meta, daf_block = _apply_davinci_youtube_audio_finish(
            upload_path, content_kind="long", warnings=warnings
        )
        if daf_block:
            return _fail(daf_block, "davinci_audio_finish_required")
        if daf_meta:
            base_out["davinci_audio_finish"] = daf_meta
        (single_job / "selected_video_path.txt").write_text(str(upload_path) + "\n", encoding="utf-8")

        uo = {"audio_mode": "music"}
        si = {"basename": vid.name, "stem": vid.stem, "vkey": vkey, "duration_sec": dur_f}
        policy = infer_long_audio_policy(vid, metadata=None, source_info=si, user_override=uo)
        cr_bundle = _content_routing_bundle_for_video(vid, job_id=job_id, source_info=si)
        title_fallback = _title_from_filename(vid)
        extra_merge: dict[str, Any] = {
            "source_video": vkey,
            "upload_video": str(upload_path),
            "duration_seconds": dur_f,
            "job_id": job_id,
            "categoryId": "22",
            "privacy_status": "unlisted",
            "manual_review_upload": True,
            "emergency_override": False,
            "autopublish_restored": False,
            "requires_review_before_public": True,
        }
        _merge_content_routing_into_extra(extra_merge, cr_bundle)
        source_json = None
        src_policy_path = _REPO / "data" / "nyc_long_driving_sources.json"
        if src_policy_path.is_file():
            try:
                source_json = json.loads(src_policy_path.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError:
                source_json = None
        try:
            meta_long, _mp = ensure_youtube_metadata_file(
                single_job,
                upload_path,
                video_type="long",
                real_sound_report=None,
                source_json=source_json,
                job_result_json=None,
                style="auto",
                use_ai_metadata=bool(getattr(args, "use_ai_metadata", False)),
                dry_run=False,
                extra_merge=extra_merge,
                long_audio_policy=policy,
            )
            sync_legacy_package_files(single_job, meta_long)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"metadata_generation_exception:{exc!r}")
            meta_long = {
                "metadata_generated": False,
                "metadata_generator_version": "",
                "title": title_fallback,
                "description": DEFAULT_DESC,
                "tags": [t.strip() for t in DEFAULT_TAGS.split(",") if t.strip()],
                "video_type": "long",
                "warnings": ["metadata_generation_failed"],
            }
            try:
                (single_job / "youtube_metadata.json").write_text(
                    json.dumps({**extra_merge, **meta_long}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass

        gen_title = str((meta_long or {}).get("title") or title_fallback)

        res = upload_from_package_directory(
            single_job,
            privacy="unlisted",
            allow_public=False,
            title=None,
            description=None,
            tags_str=None,
            category_id="22",
            made_for_kids=False,
            dry_run=False,
            token_path=token_path,
            client_secrets=sec,
            force_reupload=False,
            allow_test_assets=False,
            token_path_used=token_used,
            client_secrets_path_used=sec_used,
            use_ai_metadata=bool(getattr(args, "use_ai_metadata", False)),
            agent_upload=False,
            review_queue=bool(getattr(args, "review_queue", True)),
            force_private=False,
            channel_type="long",
            channel_guard_status="passed_manual_review_upload",
            dedupe_status="passed_v2_dedupe_index",
            dedupe_key=str(ck),
            automation_job_id=job_id,
        )

        uentry = {
            "video": vkey,
            "ok": res.ok,
            "status": res.status,
            "video_id": res.video_id,
            "error": res.error,
        }
        base_out["uploads"] = [uentry]

        if res.ok and res.status == "success" and res.video_id:
            base_out["uploaded"] = True
            base_out["youtube_video_id"] = str(res.video_id)
            base_out["youtube_url"] = f"https://www.youtube.com/watch?v={res.video_id}"
            base_out["status"] = "uploaded"
            base_out["privacy_status"] = str(res.privacy_used or "unlisted")
            base_out["review_queue_added"] = bool(res.review_queue_path)
            base_out["review_status"] = "pending"
            base_out["requires_review_before_public"] = True
            base_out["manual_review_upload"] = True
            base_out["emergency_override"] = False
            base_out["autopublish_restored"] = False
            if bool(getattr(args, "review_queue", True)) and not res.review_queue_path:
                warnings.append("upload_success_missing_review_queue_entry")
            now_ent = {
                "source_path": str(vid),
                "resolved_path": vkey,
                "basename": vid.name,
                "stem": vid.stem,
                "size_bytes": sz,
                "mtime": int(vid.stat().st_mtime),
                "duration_sec": dur_f,
                "quick_hash": qh,
                "content_key": ck,
                "youtube_video_id": str(res.video_id),
                "youtube_url": base_out["youtube_url"],
                "uploaded": True,
                "privacy_status": "unlisted",
                "review_status": "pending",
                "channel": "NYC_LONG",
                "job_id": job_id,
                "uploaded_at": _utc_iso(),
                "title": gen_title,
                "result_path": str(single_job / "upload_result.json"),
                "manual_review_upload": True,
                "emergency_override": False,
                "autopublish_restored": False,
                "requires_review_before_public": True,
            }
            entries = ledger_data.get("entries") if isinstance(ledger_data.get("entries"), list) else []
            entries.append(now_ent)
            ledger_data["entries"] = entries
            ledger_data["version"] = 2
            ledger_data["updated_at"] = _utc_iso()
            _save_ledger(ledger_path, ledger_data, warnings)
            idx.content_keys.add(ck)
            idx.quick_hashes.add(qh)
            idx.add_path_key(vkey)
            idx.add_path_key(str(vid))
            ur: dict[str, Any] = {
                "ok": True,
                "video_id": res.video_id,
                "youtube_video_id": res.video_id,
                "url": base_out["youtube_url"],
                "privacy_used": res.privacy_used or res.privacy,
                "title_used": res.title_used or res.title,
                "description_used": res.description_used,
                "tags_used": res.tags_used,
                "metadata_path": res.metadata_path,
                "channel_guard_status": res.channel_guard_status,
                "dedupe_status": res.dedupe_status,
                "review_queue_path": res.review_queue_path,
                "review_queue_md_path": res.review_queue_md_path,
                "agent_uploaded": res.agent_uploaded,
                "human_review_required": res.human_review_required,
                "forced_private": res.forced_private,
                "manual_review_upload": True,
                "emergency_override": False,
                "autopublish_restored": False,
                "requires_review_before_public": True,
            }
            (single_job / "upload_result.json").write_text(
                json.dumps(ur, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            base_out["finished_at"] = _utc_iso()
            out_payload = {**base_out, "meta": meta_job}
            try:
                (job_dir / "long_job_result.json").write_text(
                    json.dumps(out_payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                warnings.append(f"long_job_result_write_failed:{exc!r}")
            print(json.dumps(out_payload, indent=2, ensure_ascii=False))
            _manual_review_upload_print_result_lines(
                uploaded=True,
                video_id=str(res.video_id),
                url=base_out["youtube_url"],
                privacy=str(res.privacy_used or "unlisted"),
                channel_title=str(res.upload_channel_title or ""),
                result_path=str(single_job / "upload_result.json"),
            )
            return 0

        base_out["status"] = "upload_failed"
        base_out["review_status"] = "upload_failed"
        base_out["errors"].append(res.error or res.status or "upload_failed")
        base_out["finished_at"] = _utc_iso()
        err_payload = {**uentry, "uploaded": False}
        try:
            (single_job / "upload_error.json").write_text(
                json.dumps(err_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
        out_payload = {**base_out, "meta": meta_job}
        try:
            (job_dir / "long_job_result.json").write_text(
                json.dumps(out_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.append(f"long_job_result_write_failed:{exc!r}")
        print(json.dumps(out_payload, indent=2, ensure_ascii=False))
        _manual_review_upload_print_result_lines(
            uploaded=False,
            video_id="",
            url="",
            privacy="unlisted",
            channel_title=str(res.upload_channel_title or ""),
            result_path=str(single_job / "upload_error.json"),
        )
        return 1
    finally:
        if lock_acquired:
            _release_lock(lock_path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument(
        "--manual-review-upload",
        default="",
        metavar="VIDEO_PATH",
        help="NYC_LONG only: upload one pre-reviewed file as unlisted for YouTube Studio review; "
        "uses official token.json; does not lift autopublish emergency or modify emergency JSON.",
    )
    ap.add_argument(
        "--auto-review-upload",
        action="store_true",
        help="Pick newest safe ready_to_upload finished clip and upload as unlisted for Studio review; "
        "does not lift autopublish emergency.",
    )
    ap.add_argument(
        "--allow-raw-review-upload",
        action="store_true",
        help="With --auto-review-upload only: relax raw-path exclusions (still subject to guards and dedupe).",
    )
    ap.add_argument("--privacy-status", choices=("private", "unlisted"), default="unlisted")
    ap.add_argument("--max-count", type=int, default=1)
    ap.add_argument(
        "--agent-private-upload",
        action="store_true",
        help="Real upload as YouTube private only; writes review queue; forbids public.",
    )
    ap.add_argument(
        "--review-queue",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="After successful upload, write review queue entry (default: on).",
    )
    ap.add_argument(
        "--no-public",
        action="store_true",
        help="Reject public privacy (safety).",
    )
    ap.add_argument(
        "--no-real-sound-gate",
        action="store_true",
        help="Skip Real Sound Cleanup Gate v1 before long upload (default: run gate, fail-open to source).",
    )
    ap.add_argument(
        "--use-ai-metadata",
        action="store_true",
        help="Optional OpenAI metadata refine (OPENAI_API_KEY; fail-open).",
    )
    ap.add_argument("--min-duration-sec", type=float, default=MIN_DURATION_SEC)
    ap.add_argument("--token", type=Path, default=None, help="Override long token path (guard requires official path).")
    ap.add_argument("--client-secrets", type=Path, default=None)
    ap.add_argument(
        "--audio-mode",
        choices=("auto", "music", "real_sound", "clean_ambient", "no_vocals_clean_ambient"),
        default="auto",
        help="Long audio policy: auto infers from path; or force a mode.",
    )
    ap.add_argument("--music-volume", type=float, default=None, help="Override policy music_volume when set.")
    ap.add_argument("--original-volume", type=float, default=None, help="Override policy original_volume when set.")
    ap.add_argument(
        "--force-schedule",
        action="store_true",
        help="Bypass NYC long daily schedule gate (max 1x 1h per calendar day).",
    )
    ap.add_argument(
        "--schedule-kind",
        choices=("1h", "3h"),
        default="1h",
        help="Which schedule slot this run consumes (1h daily or 3h extended).",
    )
    ap.add_argument(
        "--run-demucs",
        action="store_true",
        help="Does not run Demucs in-queue; reserved so no_vocals mode can be relaxed manually later.",
    )
    args = ap.parse_args()

    mr_raw = str(getattr(args, "manual_review_upload", "") or "").strip()
    auto_r = bool(getattr(args, "auto_review_upload", False))
    if auto_r:
        if args.upload or mr_raw:
            print(
                "ERROR: --auto-review-upload cannot be combined with --upload or --manual-review-upload",
                file=sys.stderr,
            )
            return 2
        if args.privacy_status != "unlisted":
            print("ERROR: --auto-review-upload requires --privacy-status unlisted", file=sys.stderr)
            return 2
        if args.token is not None:
            print(
                "ERROR: --auto-review-upload forbids --token override (use official token.json only)",
                file=sys.stderr,
            )
            return 2
        if bool(getattr(args, "agent_private_upload", False)):
            print(
                "ERROR: --agent-private-upload cannot be combined with --auto-review-upload",
                file=sys.stderr,
            )
            return 2
        return _auto_review_upload_main(args)

    if mr_raw:
        if args.upload:
            print("ERROR: --upload cannot be combined with --manual-review-upload", file=sys.stderr)
            return 2
        if args.privacy_status != "unlisted":
            print("ERROR: --manual-review-upload requires --privacy-status unlisted", file=sys.stderr)
            return 2
        if args.token is not None:
            print("ERROR: --manual-review-upload forbids --token override (use official token.json only)", file=sys.stderr)
            return 2
        if bool(getattr(args, "agent_private_upload", False)):
            print("ERROR: --agent-private-upload cannot be combined with --manual-review-upload", file=sys.stderr)
            return 2
        return _manual_review_upload_main(args, mr_raw)

    if args.upload and args.dry_run:
        print("ERROR: use only one of --upload or --dry-run", file=sys.stderr)
        return 2
    if not args.upload and not args.dry_run:
        print("ERROR: specify --upload or --dry-run", file=sys.stderr)
        return 2
    agent_pu = bool(getattr(args, "agent_private_upload", False))
    if agent_pu and args.privacy_status != "private":
        print("ERROR: --agent-private-upload requires --privacy-status private", file=sys.stderr)
        return 3

    if args.upload and args.privacy_status == "public":
        print("ERROR: auto-publish never uses public privacy", file=sys.stderr)
        return 3

    if args.upload:
        try:
            from always_publish.queue_integration import (  # noqa: WPS433
                blocked_payload,
                preflight_long_upload,
                print_blocked,
            )

            pre = preflight_long_upload(force_schedule=bool(getattr(args, "force_schedule", False)))
            if not pre.get("allowed"):
                print_blocked(blocked_payload("long", pre))
                return 0
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"always_deliver_preflight_failed": repr(exc)}, ensure_ascii=False), flush=True)

    warnings: list[str] = []
    errors: list[str] = []
    if args.upload and not getattr(args, "manual_review_upload", None) and not getattr(args, "auto_review_upload", False):
        try:
            from always_publish.queue_integration import run_long_upload_from_delivery_queue  # noqa: WPS433

            dq_exit = run_long_upload_from_delivery_queue(args, warnings=warnings, errors=errors)
            if dq_exit is not None:
                return int(dq_exit)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"delivery_queue_upload_path_failed:{exc!r}")

    ensure_publish_v2_dirs(warnings)

    ready = get_transfer_ready_to_upload(verbose=False)
    cache = get_sv_cache(verbose=False)
    xfer = get_sv_transfer(verbose=False)
    roots = discover_long_video169_scan_directories(xfer, cache, warnings)
    pack_parent, ledger_path, lock_path, pack_fallback = _resolve_pack_ledger_lock(warnings)

    hist_roots = [
        xfer / "publish_pack" / "nyc_long_uploads",
        HOME_LONG_UPLOADS,
    ]

    primary_ledger = xfer / "publish_pack" / "nyc_long_uploads" / LEDGER_NAME
    home_ledger = HOME_LONG_UPLOADS / LEDGER_NAME
    ledger_data = _load_ledger(ledger_path)
    idx = DedupeIndexV2()
    _seen_ledger_files: set[str] = set()
    for lp in (primary_ledger, home_ledger, ledger_path):
        key = str(lp)
        if key in _seen_ledger_files:
            continue
        _seen_ledger_files.add(key)
        if lp.is_file():
            _ingest_ledger_entries(_load_ledger(lp), idx)
    _scan_history_job_jsons(hist_roots, idx, warnings)

    token_path, tw = resolve_long_form_upload_token(args.token)
    for w in tw:
        warnings.append(w)
    sec = resolve_client_secrets(args.client_secrets)
    token_used = str(token_path)
    sec_used = str(sec)

    try:
        if token_path.resolve() == OFFICIAL_SHORTS_TOKEN_PATH.resolve():
            print("ERROR: long queue must not use token_shorts.json", file=sys.stderr)
            return 4
    except OSError:
        pass

    ok_long_tok, tok_reason = validate_long_channel_token(token_path)
    if not ok_long_tok:
        job_id = uuid.uuid4().hex[:16]
        base = _build_unified_base(
            job_id=job_id,
            status="blocked",
            privacy_status=args.privacy_status,
            ledger_path=str(ledger_path),
            fallback_used=pack_fallback,
            warnings=warnings,
            errors=errors,
            block_reason="channel_guard_failed",
        )
        base["finished_at"] = _utc_iso()
        base["errors"].append(tok_reason or "long_token_invalid")
        job_dir = pack_parent / job_id
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "long_job_result.json").write_text(
                json.dumps(base, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
        print(json.dumps(base, indent=2, ensure_ascii=False))
        return 0

    emergency_state = _load_long_emergency_state(xfer, warnings)
    rej_entries = _load_long_rejected_outputs(xfer, warnings)
    rej_keys = _long_rejected_path_keys(rej_entries)
    ledger_facets = _ledger_rejected_facets(rej_entries)

    if args.upload and _long_upload_emergency_active(emergency_state):
        job_id = uuid.uuid4().hex[:16]
        br = str(emergency_state.get("block_reason") or "bad_audio_and_mixed_source_policy_review_required")
        base = _build_unified_base(
            job_id=job_id,
            status="blocked",
            privacy_status=args.privacy_status,
            ledger_path=str(ledger_path),
            fallback_used=pack_fallback,
            warnings=warnings,
            errors=errors,
            block_reason=br,
        )
        base["finished_at"] = _utc_iso()
        base["LONG_AUTOPUBLISH_DISABLED"] = bool(emergency_state.get("LONG_AUTOPUBLISH_DISABLED", True))
        base["SAFE_TO_ENABLE_LONG_UPLOAD"] = False
        base["LONG_DRY_RUN_STATUS"] = "blocked"
        base["LONG_NEXT_CANDIDATE"] = ""
        base["rejected_outputs_count"] = int(len(rej_entries))
        base["vertical_rejected_count"] = int(ledger_facets["vertical"])
        base["walking_rejected_count"] = int(ledger_facets["walking"])
        base["mixed_aspect_rejected_count"] = int(ledger_facets["mixed_aspect"])
        base["mixed_source_type_rejected_count"] = int(ledger_facets["mixed_source"])
        base["source_policy_summary"] = nyc_long_source_policy_summary()
        base["RAW_SOURCE_CANDIDATES_COUNT"] = 0
        base["OUTPUT_ARTIFACTS_EXCLUDED_COUNT"] = int(count_long_output_artifact_pool_videos(xfer, cache, warnings))
        base["UNKNOWN_LONG_CANDIDATES_COUNT"] = 0
        base["long_autopublish_emergency_path"] = str(xfer / "publish_pack" / "nyc_long_uploads" / "long_autopublish_emergency.json")
        _merge_nyc_long_schedule_fields(
            base,
            force=bool(getattr(args, "force_schedule", False)),
            schedule_kind=str(getattr(args, "schedule_kind", "1h") or "1h"),
            warnings=warnings,
        )
        warnings.append("long_upload_blocked_by_emergency_json")
        job_dir = pack_parent / job_id
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "long_job_result.json").write_text(
                json.dumps(base, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
        print(json.dumps(base, indent=2, ensure_ascii=False))
        print("LONG_AVAILABLE_NEW_CANDIDATES=0")
        print("LONG_NEXT_CANDIDATE=")
        print("LONG_DRY_RUN_STATUS=blocked")
        print("LONG_AUTOPUBLISH_DISABLED=true")
        print("SAFE_TO_ENABLE_LONG_UPLOAD=false")
        print(f"block_reason={br}")
        print("rejected_outputs_count=" + str(len(rej_entries)))
        print("vertical_rejected_count=" + str(base["vertical_rejected_count"]))
        print("walking_rejected_count=" + str(base["walking_rejected_count"]))
        print("mixed_aspect_rejected_count=" + str(base["mixed_aspect_rejected_count"]))
        print("mixed_source_type_rejected_count=" + str(base["mixed_source_type_rejected_count"]))
        print("source_policy_summary=" + base["source_policy_summary"])
        print(f"RAW_SOURCE_CANDIDATES_COUNT={base.get('RAW_SOURCE_CANDIDATES_COUNT', 0)}")
        print(f"OUTPUT_ARTIFACTS_EXCLUDED_COUNT={base.get('OUTPUT_ARTIFACTS_EXCLUDED_COUNT', 0)}")
        print(f"UNKNOWN_LONG_CANDIDATES_COUNT={base.get('UNKNOWN_LONG_CANDIDATES_COUNT', 0)}")
        return 0

    raw_paths = collect_long_candidates(ready, warnings)
    raw_kept: list[Path] = []
    for vid in raw_paths:
        try:
            vk = _canonical_media_path(str(vid.resolve()))
        except OSError:
            vk = _canonical_media_path(str(vid))
        sk = str(vid)
        if vk in rej_keys or sk in rej_keys:
            warnings.append(f"skipped_long_rejected_outputs_json:{vid.name}")
            continue
        raw_kept.append(vid)
    raw_paths = raw_kept
    probe_pass: list[tuple[Path, dict[str, Any], float, int]] = []
    skipped_small = 0
    skipped_probe = 0
    for vid in raw_paths:
        try:
            vkey = str(vid.resolve())
        except OSError:
            vkey = str(vid)
        try:
            sz = int(vid.stat().st_size)
        except OSError:
            skipped_probe += 1
            warnings.append(f"skipped_probe/stat_failed:{vid.name}")
            continue
        if sz < MIN_BYTES:
            skipped_small += 1
            continue
        probe = _ffprobe_json(vid)
        dur, has_v = _duration_and_has_video(probe)
        if probe is None:
            skipped_probe += 1
            warnings.append(f"skipped_probe/ffprobe_failed:{vid.name}")
            continue
        if not has_v:
            skipped_probe += 1
            warnings.append(f"skipped_probe/no_video_stream:{vid.name}")
            continue
        if dur is None:
            skipped_probe += 1
            warnings.append(f"skipped_probe/no_duration:{vid.name}")
            continue
        probe_pass.append((vid, probe, float(dur), sz))

    probe_pass = sort_probe_pass_prefer_clean_real_sound(probe_pass)

    candidate_count = len(probe_pass)

    eligible_rows: list[dict[str, Any]] = []
    duplicate_rows: list[dict[str, Any]] = []
    rejected_long_candidates: list[dict[str, Any]] = []
    skipped_long_source_policy = 0

    rejected_bad_aspect_count = 0
    vertical_rejected_count = 0

    for vid, probe, dur, sz in probe_pass:
        try:
            vkey = str(vid.resolve())
        except OSError:
            vkey = str(vid)
        if dur < float(args.min_duration_sec):
            skipped_probe += 1
            warnings.append(f"skipped_probe/duration_below_min:{vid.name}")
            continue
        pol_src = is_valid_nyc_long_source(vid, ffprobe_meta=probe)
        if not pol_src.get("long_allowed"):
            skipped_long_source_policy += 1
            rrs = [str(x) for x in (pol_src.get("reject_reasons") or [])]
            if "rejected_vertical_video" in rrs or str(pol_src.get("orientation") or "") == "portrait":
                vertical_rejected_count += 1
            elif "rejected_bad_aspect_ratio" in rrs or "square_not_allowed" in str(pol_src.get("reason") or ""):
                rejected_bad_aspect_count += 1
            warnings.append(f"skipped_long_candidate:{vid.name}:{pol_src.get('reason')}")
            rejected_long_candidates.append(
                {
                    "path": str(vid),
                    "reason": pol_src.get("reason"),
                    "reject_reasons": pol_src.get("reject_reasons") or [],
                    "width": pol_src.get("width"),
                    "height": pol_src.get("height"),
                    "aspect_ratio": pol_src.get("aspect_ratio"),
                    "display_aspect_ratio": pol_src.get("display_aspect_ratio"),
                    "aspect_policy": pol_src.get("aspect_policy"),
                    "source_type": pol_src.get("source_type"),
                    "shorts_allowed": pol_src.get("shorts_allowed"),
                }
            )
            continue
        vw, vh = _primary_video_dims(probe)
        qh = triple_chunk_sha256(vid)
        bn_norm = normalize_basename(vid.name)
        ck = content_key_v2(size_bytes=sz, duration_sec=dur, quick_hash=qh, basename_normalized=bn_norm)
        meta = {
            "path": vid,
            "source_path": str(vid),
            "resolved_path": vkey,
            "basename": vid.name,
            "stem": vid.stem,
            "size_bytes": sz,
            "mtime": int(vid.stat().st_mtime),
            "duration_sec": dur,
            "quick_hash": qh,
            "content_key": ck,
            "video_width": vw,
            "video_height": vh,
            "long_source_policy": pol_src,
        }
        used, reason = _is_used_candidate(meta, idx)
        if used:
            duplicate_rows.append({**meta, "dedupe_reason": reason, "path": str(vid)})
            continue
        eligible_rows.append(meta)

    skipped_duplicate_count = len(duplicate_rows)
    eligible_count = len(eligible_rows)
    available_new = len(eligible_rows)
    ledger_entry_count = len(ledger_data.get("entries") or []) if isinstance(ledger_data.get("entries"), list) else 0
    used_count = skipped_duplicate_count
    max_pick = max(1, int(args.max_count))
    picked_meta = eligible_rows[:max_pick]
    next_video = str(eligible_rows[0]["path"]) if eligible_rows else ""
    eligible_clean_real_sound_count = sum(
        1 for r in eligible_rows if _is_clean_real_sound_long_filename(Path(str(r.get("path") or ""))))
    next_video_is_clean = bool(next_video) and _is_clean_real_sound_long_filename(Path(next_video))
    next_path_sel = Path(next_video) if next_video else None
    selected_needs_clean = bool(next_path_sel) and _raw_nyc_long_master_needs_noise_gate(next_path_sel)
    selected_noise_gate_ok = not selected_needs_clean
    selected_is_clean_rs = bool(next_video_is_clean)
    real_sound_gate_status = "needs_cleanup" if selected_needs_clean else ("clean" if selected_is_clean_rs else "ok")
    _ts_clean = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recommended_cleanup_input = str(next_path_sel) if selected_needs_clean else ""
    recommended_cleanup_output = (
        str((ready / "nyc_long_clips" / f"nyc_long_master_clean_real_sound_{_ts_clean}.mp4"))
        if selected_needs_clean
        else ""
    )
    safe_to_enable_long_upload = bool(eligible_count > 0 and selected_noise_gate_ok)

    dedupe_summary = (
        f"candidates={candidate_count} eligible={eligible_count} "
        f"skipped_dup={skipped_duplicate_count} skipped_small={skipped_small} "
        f"skipped_probe={skipped_probe} skipped_long_policy={skipped_long_source_policy} "
        f"new_available={available_new}"
    )

    rej_by_long = summarize_rejections(rejected_long_candidates)
    artifact_excluded_count = count_long_output_artifact_pool_videos(xfer, cache, warnings)
    unknown_long_candidates_count = sum(
        1
        for r in rejected_long_candidates
        if str(r.get("source_type") or "") == "unknown"
        and "rejected_long_source_type_not_allowed" in [str(x) for x in (r.get("reject_reasons") or [])]
    )
    scan_f = _scan_rejected_facets(rejected_long_candidates)
    vertical_rej_total = int(vertical_rejected_count + ledger_facets["vertical"])
    walking_rej_total = int(scan_f["walking"] + ledger_facets["walking"])
    mixed_aspect_rej_total = int(rejected_bad_aspect_count + ledger_facets["mixed_aspect"])
    mixed_source_rej_total = int(scan_f["mixed_source"] + ledger_facets["mixed_source"])
    rejected_outputs_count = int(len(rej_entries))

    job_id = uuid.uuid4().hex[:16]
    job_dir = pack_parent / job_id
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        job_dir = HOME_LONG_UPLOADS / job_id
        warnings.append(f"job_dir_fallback:{exc!r}")
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc2:
            errors.append(f"cannot_create_job_dir:{exc2!r}")
            base = _build_unified_base(
                job_id=job_id,
                status="error",
                privacy_status=args.privacy_status,
                ledger_path=str(ledger_path),
                fallback_used=True,
                warnings=warnings,
                errors=errors,
                block_reason="job_dir_unwritable",
            )
            base["finished_at"] = _utc_iso()
            print(json.dumps(base, indent=2, ensure_ascii=False))
            return 1

    if args.dry_run and lock_path.is_file():
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8", errors="replace"))
            if _pid_alive(int(data.get("pid") or 0)):
                warnings.append("nyc_long_upload_lock_active_pid_alive")
        except (OSError, json.JSONDecodeError):
            warnings.append("nyc_long_upload_lock_present_unparsed")

    if args.dry_run and eligible_count > 0 and not next_video_is_clean:
        warnings.append("long_master_without_noise_cleanup_gate")

    dry_status_for_base = "running"
    block_for_base = ""
    if args.dry_run:
        if eligible_count > 0 and selected_needs_clean:
            dry_status_for_base = "needs_real_sound_cleanup"
            block_for_base = "raw_long_master_requires_real_sound_noise_cleanup_v1"
        elif eligible_count > 0:
            dry_status_for_base = "ready"
        else:
            dry_status_for_base = "dry_run"

    base_out = _build_unified_base(
        job_id=job_id,
        status=dry_status_for_base if args.dry_run else "running",
        privacy_status=args.privacy_status,
        ledger_path=str(ledger_path),
        fallback_used=pack_fallback,
        warnings=warnings,
        errors=errors,
        block_reason=block_for_base,
    )
    base_out["candidate_count"] = candidate_count
    base_out["eligible_count"] = eligible_count
    base_out["used_count"] = used_count
    base_out["ledger_entry_count"] = ledger_entry_count
    base_out["skipped_duplicate_count"] = skipped_duplicate_count
    base_out["available_new_candidates"] = available_new
    base_out["next_video"] = next_video
    base_out["next_video_is_clean_real_sound"] = next_video_is_clean
    base_out["eligible_clean_real_sound_count"] = int(eligible_clean_real_sound_count)
    base_out["selected_video_path"] = next_video
    base_out["selected_is_clean_real_sound"] = selected_is_clean_rs
    base_out["selected_needs_clean"] = selected_needs_clean
    base_out["selected_noise_gate_ok"] = selected_noise_gate_ok
    base_out["real_sound_gate_status"] = real_sound_gate_status
    base_out["recommended_cleanup_input"] = recommended_cleanup_input
    base_out["recommended_cleanup_output"] = recommended_cleanup_output
    base_out["safe_to_enable_long_upload"] = safe_to_enable_long_upload
    base_out["LONG_AVAILABLE_NEW_CANDIDATES"] = int(available_new)
    base_out["LONG_NEXT_CANDIDATE"] = next_video
    base_out["LONG_DRY_RUN_STATUS"] = str(base_out.get("status") or "")
    base_out["SAFE_TO_ENABLE_LONG_UPLOAD"] = safe_to_enable_long_upload
    rs_report_path: Path | None = None
    rs_report_data: dict[str, Any] | None = None
    if next_video and selected_is_clean_rs:
        rs_report_path, rs_report_data = _resolve_real_sound_cleanup_sidecar(Path(next_video).stem)
        if rs_report_data is None:
            rs_report_path, rs_report_data = _resolve_real_sound_cleanup_report_for_output(Path(next_video))
    base_out["REAL_SOUND_CLEANUP_STATUS"] = (
        str(rs_report_data.get("status") or "") if rs_report_data else ("report_missing" if (next_video and selected_is_clean_rs) else "")
    )
    base_out["REAL_SOUND_CLEANUP_OUTPUT"] = (
        str(rs_report_data.get("output_path") or next_video or "") if (next_video and selected_is_clean_rs) else ""
    )
    base_out["REAL_SOUND_CLEANUP_REPORT"] = str(rs_report_path) if rs_report_path else ""
    base_out["dedupe_summary"] = dedupe_summary
    base_out["roots"] = [str(r) for r in roots]
    base_out["long_source_policy_version"] = LONG_SOURCE_POLICY_VERSION
    base_out["long_aspect_gate_enabled"] = True
    base_out["rejected_bad_aspect_count"] = int(rejected_bad_aspect_count)
    base_out["vertical_rejected_count"] = vertical_rej_total
    base_out["mixed_aspect_rejected_count"] = mixed_aspect_rej_total
    base_out["walking_rejected_count"] = walking_rej_total
    base_out["mixed_source_type_rejected_count"] = mixed_source_rej_total
    base_out["rejected_outputs_count"] = rejected_outputs_count
    base_out["source_policy_summary"] = nyc_long_source_policy_summary()
    base_out["LONG_AUTOPUBLISH_DISABLED"] = bool(emergency_state.get("LONG_AUTOPUBLISH_DISABLED"))
    base_out["accepted_long_candidates"] = eligible_count
    base_out["rejected_long_candidates"] = rejected_long_candidates[:200]
    base_out["rejected_by_reason"] = rej_by_long
    base_out["skipped_long_source_policy"] = skipped_long_source_policy
    base_out["RAW_SOURCE_CANDIDATES_COUNT"] = int(candidate_count)
    base_out["OUTPUT_ARTIFACTS_EXCLUDED_COUNT"] = int(artifact_excluded_count)
    base_out["UNKNOWN_LONG_CANDIDATES_COUNT"] = int(unknown_long_candidates_count)
    if picked_meta:
        ps0 = picked_meta[0].get("long_source_policy") or {}
        base_out["selected_orientation"] = str(ps0.get("orientation") or "")
        base_out["selected_aspect_ratio"] = ps0.get("aspect_ratio")
        base_out["selected_source_type"] = str(ps0.get("source_type") or "")
        base_out["selected_width"] = int(ps0.get("width") or picked_meta[0].get("video_width") or 0)
        base_out["selected_height"] = int(ps0.get("height") or picked_meta[0].get("video_height") or 0)
        base_out["selected_display_aspect_ratio"] = str(ps0.get("display_aspect_ratio") or "")
        base_out["selected_aspect_policy"] = str(ps0.get("aspect_policy") or "")
    else:
        base_out["selected_orientation"] = ""
        base_out["selected_aspect_ratio"] = None
        base_out["selected_source_type"] = ""
        base_out["selected_width"] = 0
        base_out["selected_height"] = 0
        base_out["selected_display_aspect_ratio"] = ""
        base_out["selected_aspect_policy"] = ""

    base_out["requires_review_before_public"] = True
    base_out["auto_upload_privacy"] = str(args.privacy_status)
    base_out["auto_public_upload_disabled"] = True
    base_out["review_before_public"] = True
    base_out["review_queue_added"] = False
    base_out["review_status"] = "dry_run" if args.dry_run else "awaiting_upload"
    schedule_gate = _merge_nyc_long_schedule_fields(
        base_out,
        force=bool(getattr(args, "force_schedule", False)),
        schedule_kind=str(getattr(args, "schedule_kind", "1h") or "1h"),
        warnings=warnings,
    )

    meta_job = {
        "job_id": job_id,
        "channel": "NYC_LONG",
        "video_type": "long",
        "privacy_status": args.privacy_status,
        "token_path": token_used,
        "client_secrets_path": sec_used,
        "candidates_scanned": candidate_count,
        "ledger_path": str(ledger_path),
        "lock_path": str(lock_path),
    }

    if not schedule_gate.get("allowed") and not bool(getattr(args, "force_schedule", False)):
        br_sched = str(schedule_gate.get("reason") or "schedule_gate_blocked")
        base_out["block_reason"] = br_sched
        if args.upload:
            base_out["status"] = "blocked"
            base_out["LONG_DRY_RUN_STATUS"] = "blocked"
            base_out["finished_at"] = _utc_iso()
            payload = {**base_out, "meta": meta_job}
            try:
                (job_dir / "long_job_result.json").write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                warnings.append(f"long_job_result_write_failed:{exc!r}")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if args.dry_run:
            base_out["status"] = "schedule_gated"
            base_out["LONG_DRY_RUN_STATUS"] = "schedule_gated"
            if _long_upload_emergency_active(emergency_state):
                _apply_long_emergency_dry_run_out_fields(base_out, emergency_state, warnings=warnings)

    if not picked_meta:
        if not _long_upload_emergency_active(emergency_state):
            base_out["status"] = "blocked"
            base_out["block_reason"] = "no_new_nyc_long_assets"
        else:
            base_out["status"] = "blocked"
            base_out["block_reason"] = str(
                emergency_state.get("block_reason") or "bad_audio_and_mixed_source_policy_review_required"
            )
            base_out["LONG_AUTOPUBLISH_DISABLED"] = bool(emergency_state.get("LONG_AUTOPUBLISH_DISABLED", True))
            base_out["SAFE_TO_ENABLE_LONG_UPLOAD"] = False
            base_out["safe_to_enable_long_upload"] = False
        base_out["finished_at"] = _utc_iso()
        if args.dry_run:
            base_out["LONG_DRY_RUN_STATUS"] = "blocked"
            if _long_upload_emergency_active(emergency_state):
                _apply_long_emergency_dry_run_out_fields(base_out, emergency_state, warnings=warnings)
        payload = {**base_out, "meta": meta_job}
        try:
            (job_dir / "long_job_result.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.append(f"long_job_result_write_failed:{exc!r}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        if args.dry_run:
            print(f"LONG_AVAILABLE_NEW_CANDIDATES={payload.get('LONG_AVAILABLE_NEW_CANDIDATES', '')}")
            print(f"LONG_NEXT_CANDIDATE={payload.get('LONG_NEXT_CANDIDATE', '')}")
            print(f"LONG_DRY_RUN_STATUS={payload.get('LONG_DRY_RUN_STATUS', payload.get('status', ''))}")
            print(f"LONG_AUTOPUBLISH_DISABLED={str(payload.get('LONG_AUTOPUBLISH_DISABLED', False)).lower()}")
            print(f"SAFE_TO_ENABLE_LONG_UPLOAD={str(payload.get('SAFE_TO_ENABLE_LONG_UPLOAD', False)).lower()}")
            print(f"block_reason={payload.get('block_reason', '')}")
            print(f"rejected_outputs_count={payload.get('rejected_outputs_count', '')}")
            print(f"vertical_rejected_count={payload.get('vertical_rejected_count', '')}")
            print(f"walking_rejected_count={payload.get('walking_rejected_count', '')}")
            print(f"mixed_aspect_rejected_count={payload.get('mixed_aspect_rejected_count', '')}")
            print(f"mixed_source_type_rejected_count={payload.get('mixed_source_type_rejected_count', '')}")
            print(f"source_policy_summary={payload.get('source_policy_summary', '')}")
            print(f"RAW_SOURCE_CANDIDATES_COUNT={payload.get('RAW_SOURCE_CANDIDATES_COUNT', '')}")
            print(f"OUTPUT_ARTIFACTS_EXCLUDED_COUNT={payload.get('OUTPUT_ARTIFACTS_EXCLUDED_COUNT', '')}")
            print(f"UNKNOWN_LONG_CANDIDATES_COUNT={payload.get('UNKNOWN_LONG_CANDIDATES_COUNT', '')}")
        return 0

    lock_acquired = False
    if args.upload:
        if picked_meta:
            vchk = picked_meta[0].get("path")
            vp = Path(str(vchk)) if vchk is not None else None
            if _raw_nyc_long_master_needs_noise_gate(vp):
                _tsu = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                base_out["status"] = "blocked"
                base_out["block_reason"] = "raw_long_master_requires_real_sound_noise_cleanup_v1"
                base_out["recommended_cleanup_input"] = str(vp) if vp else ""
                base_out["recommended_cleanup_output"] = str(
                    (ready / "nyc_long_clips" / f"nyc_long_master_clean_real_sound_{_tsu}.mp4")
                )
                base_out["safe_to_enable_long_upload"] = False
                base_out["SAFE_TO_ENABLE_LONG_UPLOAD"] = False
                base_out["real_sound_gate_status"] = "needs_cleanup"
                base_out["finished_at"] = _utc_iso()
                payload = {**base_out, "meta": meta_job}
                try:
                    (job_dir / "long_job_result.json").write_text(
                        json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
        ok_lock, reason = _try_acquire_lock(lock_path, job_id, warnings)
        if not ok_lock:
            base_out["status"] = "blocked"
            base_out["block_reason"] = reason or "nyc_long_job_already_running"
            base_out["retryable"] = True
            base_out["finished_at"] = _utc_iso()
            payload = {**base_out, "meta": meta_job}
            try:
                (job_dir / "long_job_result.json").write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        lock_acquired = True
        try:
            from doctor_gate_client import (  # type: ignore
                check_real_upload_allowed_by_doctor,
                log_doctor_gate_block_upload,
            )

            allowed, detail, gw = check_real_upload_allowed_by_doctor(dry_run=False)
            warnings.extend(gw)
            if not allowed:
                base_out["blocked_by_doctor_gate"] = True
                base_out["status"] = "blocked"
                base_out["block_reason"] = "doctor_gate_block_upload"
                base_out["doctor_gate_detail"] = detail
                base_out["finished_at"] = _utc_iso()
                log_doctor_gate_block_upload()
                _release_lock(lock_path)
                lock_acquired = False
                payload = {**base_out, "meta": meta_job}
                try:
                    (job_dir / "long_job_result.json").write_text(
                        json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"doctor_gate_check_failed:{type(exc).__name__}:{exc!r}")

    try:
        for i, item in enumerate(picked_meta):
            vid: Path = item["path"]
            vkey = item["resolved_path"]
            pol0 = item.get("long_source_policy") or is_valid_nyc_long_source(vid, _ffprobe_json(vid))
            if str(pol0.get("source_type") or "") == "walking":
                base_out["status"] = "blocked"
                base_out["block_reason"] = "walking_source_selected_for_long_channel"
                base_out["finished_at"] = _utc_iso()
                try:
                    (job_dir / "long_job_result.json").write_text(
                        json.dumps({**base_out, "meta": meta_job}, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                print(json.dumps({**base_out, "meta": meta_job}, indent=2, ensure_ascii=False))
                return 0
            ap_chk = str(pol0.get("aspect_policy") or "")
            if ap_chk and ap_chk != "landscape_16_9_ok":
                base_out["status"] = "blocked"
                base_out["block_reason"] = "long_aspect_policy_not_allowed"
                base_out["finished_at"] = _utc_iso()
                try:
                    (job_dir / "long_job_result.json").write_text(
                        json.dumps({**base_out, "meta": meta_job}, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                print(json.dumps({**base_out, "meta": meta_job}, indent=2, ensure_ascii=False))
                return 0
            vw_chk, vh_chk = int(item.get("video_width") or 0), int(item.get("video_height") or 0)
            if vw_chk > 0 and vh_chk > 0 and vh_chk > vw_chk:
                base_out["status"] = "blocked"
                base_out["block_reason"] = "portrait_video_selected_for_long_channel"
                base_out["finished_at"] = _utc_iso()
                try:
                    (job_dir / "long_job_result.json").write_text(
                        json.dumps({**base_out, "meta": meta_job}, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                print(json.dumps({**base_out, "meta": meta_job}, indent=2, ensure_ascii=False))
                return 0
            title_fallback = _title_from_filename(vid)

            guard = assert_long_upload_context(vid, token_path)
            if guard:
                base_out["status"] = "blocked"
                base_out["block_reason"] = "channel_guard_failed"
                base_out["errors"].append(json.dumps(guard, ensure_ascii=False))
                base_out["finished_at"] = _utc_iso()
                try:
                    (job_dir / "long_job_result.json").write_text(
                        json.dumps({**base_out, "meta": meta_job}, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                print(json.dumps({**base_out, "meta": meta_job}, indent=2, ensure_ascii=False))
                return 0

            single_job = job_dir if len(picked_meta) == 1 else job_dir / f"slot_{i:02d}"
            try:
                single_job.mkdir(parents=True, exist_ok=True)
            except OSError:
                single_job = HOME_LONG_UPLOADS / job_id / f"slot_{i:02d}"
                single_job.mkdir(parents=True, exist_ok=True)
                warnings.append("per_video_job_dir_fallback")

            upload_path = vid.resolve()
            real_rep: dict[str, Any] | None = None
            gid = job_id if len(picked_meta) == 1 else f"{job_id}_s{i:02d}"

            uo: dict[str, Any] = {}
            if str(args.audio_mode).strip().lower() != "auto":
                uo["audio_mode"] = args.audio_mode
            if args.music_volume is not None:
                uo["music_volume"] = float(args.music_volume)
            if args.original_volume is not None:
                uo["original_volume"] = float(args.original_volume)
            si = {
                "basename": item["basename"],
                "stem": item["stem"],
                "vkey": vkey,
                "duration_sec": item["duration_sec"],
            }
            policy = infer_long_audio_policy(vid, metadata=None, source_info=si, user_override=uo if uo else None)
            cr_bundle = _content_routing_bundle_for_video(vid, job_id=gid, source_info=si)

            if args.dry_run:
                real_rep = None
            elif not bool(args.no_real_sound_gate):
                am = str(policy.get("audio_mode") or "music")
                if am == "music":
                    real_rep = None
                elif am == "real_sound":
                    _u0, real_rep = _run_real_sound_gate_long(
                        vid, gate_job_id=gid, dry_run=False, report_dir=single_job, warnings=warnings
                    )
                    upload_path = _finalize_real_sound_upload_path(vid, real_rep, warnings=warnings)
                elif am == "clean_ambient":
                    if path_suggests_cleaned_audio(vid):
                        upload_path = vid.resolve()
                        real_rep = None
                    else:
                        _u1, real_rep = _run_real_sound_gate_long(
                            vid, gate_job_id=gid, dry_run=False, report_dir=single_job, warnings=warnings
                        )
                        upload_path = _finalize_real_sound_upload_path(vid, real_rep, warnings=warnings)
                elif am == "no_vocals_clean_ambient":
                    nv = find_no_vocals_asset(vid)
                    if nv and nv.is_file():
                        upload_path = nv.resolve()
                        real_rep = None
                    elif bool(args.run_demucs):
                        warnings.append("run_demucs_set_but_queue_does_not_run_demucs")
                        _u2, real_rep = _run_real_sound_gate_long(
                            vid, gate_job_id=gid, dry_run=False, report_dir=single_job, warnings=warnings
                        )
                        upload_path = _finalize_real_sound_upload_path(vid, real_rep, warnings=warnings)
                    else:
                        warnings.append("no_vocals_asset_missing")
                        upload_path = vid.resolve()
                        real_rep = None

            upload_path, daf_meta, daf_block = _apply_davinci_youtube_audio_finish(
                upload_path, content_kind="long", warnings=warnings
            )
            if daf_meta:
                base_out["davinci_audio_finish"] = daf_meta
            if daf_block:
                base_out["status"] = "blocked"
                base_out["block_reason"] = daf_block
                base_out["finished_at"] = _utc_iso()
                try:
                    (job_dir / "long_job_result.json").write_text(
                        json.dumps({**base_out, "meta": meta_job}, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
                print(json.dumps({**base_out, "meta": meta_job}, indent=2, ensure_ascii=False))
                return 0

            (single_job / "selected_video_path.txt").write_text(str(upload_path) + "\n", encoding="utf-8")

            if not args.dry_run:
                try:
                    from doctor_gate_client import check_upload_safety_selected_file  # type: ignore

                    used_now, used_reason = _is_used_candidate(item, idx)
                    selected_gate = check_upload_safety_selected_file(
                        upload_path,
                        mode="long",
                        dry_run=False,
                        public_upload_requested=False,
                        explicit_public_confirmation=False,
                        target_upload_dir=single_job,
                        dedupe_hit=bool(used_now),
                        already_uploaded=bool(used_now and used_reason in {"resolved_path", "source_path"}),
                    )
                    base_out["selected_file_check"] = selected_gate.get("selected_file_check")
                    if selected_gate.get("warning_reasons"):
                        warnings.extend([f"selected_gate:{w}" for w in selected_gate.get("warning_reasons") or []])
                    if not bool(selected_gate.get("upload_allowed", True)):
                        base_out["blocked_by_selected_file_gate"] = True
                        base_out["status"] = "blocked"
                        base_out["block_reason"] = "selected_file_gate_block_upload"
                        base_out["selected_gate_detail"] = selected_gate
                        base_out["finished_at"] = _utc_iso()
                        payload = {**base_out, "meta": meta_job}
                        try:
                            (job_dir / "long_job_result.json").write_text(
                                json.dumps(payload, indent=2, ensure_ascii=False),
                                encoding="utf-8",
                            )
                        except OSError:
                            pass
                        print(json.dumps(payload, indent=2, ensure_ascii=False))
                        return 0
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"selected_file_gate_check_failed:{type(exc).__name__}:{exc!r}")

            rs_data: dict[str, Any] | None = None
            if real_rep:
                rs_data = dict(real_rep)
            else:
                rp_local = single_job / "real_sound_quality_report.json"
                if rp_local.is_file():
                    try:
                        rs_data = json.loads(rp_local.read_text(encoding="utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        rs_data = None

            source_json = None
            src_policy_path = _REPO / "data" / "nyc_long_driving_sources.json"
            if src_policy_path.is_file():
                try:
                    source_json = json.loads(src_policy_path.read_text(encoding="utf-8", errors="replace"))
                except json.JSONDecodeError:
                    source_json = None

            extra_merge: dict[str, Any] = {
                "source_video": vkey,
                "upload_video": str(upload_path),
                "duration_seconds": item["duration_sec"],
                "job_id": job_id,
                "categoryId": "22",
                "privacy_status": args.privacy_status,
            }
            if real_rep:
                extra_merge["real_sound_gate"] = {
                    "status": real_rep.get("status"),
                    "mode_used": real_rep.get("mode_used"),
                    "publish_safe": real_rep.get("publish_safe"),
                    "report_path": real_rep.get("report_path"),
                    "video_copy_used": real_rep.get("video_copy_used"),
                    "video_copy_reason": real_rep.get("video_copy_reason"),
                    "cfr_reencoded": real_rep.get("cfr_reencoded"),
                    "input_is_raw_iphone_or_vfr": real_rep.get("input_is_raw_iphone_or_vfr"),
                    "input_is_cfr_safe": real_rep.get("input_is_cfr_safe"),
                    "fallback_to_original": real_rep.get("fallback_to_original"),
                }
                extra_merge["real_sound_report_path"] = str(real_rep.get("report_path") or "")

            _merge_content_routing_into_extra(extra_merge, cr_bundle)

            meta_long: dict[str, Any] = {}
            try:
                meta_long, _mp = ensure_youtube_metadata_file(
                    single_job,
                    upload_path,
                    video_type="long",
                    real_sound_report=rs_data,
                    source_json=source_json,
                    job_result_json=None,
                    style="auto",
                    use_ai_metadata=bool(getattr(args, "use_ai_metadata", False)),
                    dry_run=False,
                    extra_merge=extra_merge,
                    long_audio_policy=policy,
                )
                sync_legacy_package_files(single_job, meta_long)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"metadata_generation_exception:{exc!r}")
                meta_long = {
                    "metadata_generated": False,
                    "metadata_generator_version": "",
                    "title": title_fallback,
                    "description": DEFAULT_DESC,
                    "tags": [t.strip() for t in DEFAULT_TAGS.split(",") if t.strip()],
                    "video_type": "long",
                    "warnings": ["metadata_generation_failed"],
                }
                try:
                    (single_job / "youtube_metadata.json").write_text(
                        json.dumps({**extra_merge, **meta_long}, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass

            gen_title = str((meta_long or {}).get("title") or title_fallback)
            pick_entry = {
                "video": vkey,
                "title": gen_title,
                "duration_sec": item["duration_sec"],
                "quick_hash": item["quick_hash"],
                "content_key": item["content_key"],
            }
            pick_entry["planned_metadata_generation"] = True
            pick_entry["generated_title"] = gen_title
            pick_entry["generated_description_preview"] = str((meta_long or {}).get("description") or "")[:240]
            pick_entry["metadata_path"] = str(single_job / "youtube_metadata.json")
            pick_entry["planned_audio_policy"] = {k: v for k, v in policy.items() if k != "warnings"}
            pick_entry["planned_audio_mode"] = policy["audio_mode"]
            if cr_bundle:
                pick_entry["planned_content_audio_mode"] = cr_bundle.get("audio_mode")
                pick_entry["planned_music_required"] = cr_bundle.get("music_required")
                pick_entry["planned_music_disabled_by_default"] = cr_bundle.get("music_disabled_by_default")
                pick_entry["inferred_theme"] = cr_bundle.get("inferred_theme")
            pick_entry["planned_real_sound_gate"] = bool(policy.get("use_real_sound_gate")) and not bool(
                args.no_real_sound_gate
            )
            pick_entry["planned_no_vocals"] = bool(policy.get("use_no_vocals"))
            pick_entry["planned_music"] = bool(policy.get("use_music"))
            try:
                same_upload_as_source = upload_path.resolve() == vid.resolve()
            except OSError:
                same_upload_as_source = str(upload_path) == str(vid)
            if (
                args.upload
                and real_rep
                and same_upload_as_source
                and (
                    bool(real_rep.get("fallback_to_original"))
                    or real_rep.get("publish_safe") is False
                    or str(real_rep.get("status") or "") == "error"
                )
            ):
                pick_entry["real_sound_gate_fallback_original"] = True
            if str(upload_path) != vkey:
                pick_entry["upload_video"] = str(upload_path)
            base_out["picked"].append(pick_entry)

            if args.dry_run:
                pick_entry["would_upload"] = True
                pick_entry["package_dir"] = str(single_job)
                continue

            res = upload_from_package_directory(
                single_job,
                privacy=args.privacy_status,
                allow_public=False,
                title=None,
                description=None,
                tags_str=None,
                category_id="22",
                made_for_kids=False,
                dry_run=False,
                token_path=token_path,
                client_secrets=sec,
                force_reupload=False,
                allow_test_assets=False,
                token_path_used=token_used,
                client_secrets_path_used=sec_used,
                use_ai_metadata=bool(getattr(args, "use_ai_metadata", False)),
                agent_upload=agent_pu,
                review_queue=bool(getattr(args, "review_queue", True)),
                force_private=agent_pu,
                channel_type="long",
                channel_guard_status="passed_long_queue",
                dedupe_status="passed_v2_dedupe_index",
                dedupe_key=str(item.get("content_key") or ""),
                automation_job_id=job_id,
            )
            uentry = {
                "video": vkey,
                "ok": res.ok,
                "status": res.status,
                "video_id": res.video_id,
                "error": res.error,
            }
            base_out["uploads"].append(uentry)
            if res.ok and res.status == "success" and res.video_id:
                base_out["uploaded"] = True
                base_out["youtube_video_id"] = str(res.video_id)
                base_out["youtube_url"] = f"https://www.youtube.com/watch?v={res.video_id}"
                base_out["status"] = "uploaded"
                base_out["privacy_status"] = str(res.privacy_used or args.privacy_status)
                base_out["review_queue_added"] = bool(res.review_queue_path)
                base_out["review_status"] = "pending"
                base_out["requires_review_before_public"] = True
                if bool(getattr(args, "review_queue", True)) and not res.review_queue_path:
                    warnings.append("upload_success_missing_review_queue_entry")
                now_ent = {
                    "source_path": str(vid),
                    "resolved_path": vkey,
                    "basename": item["basename"],
                    "stem": item["stem"],
                    "size_bytes": item["size_bytes"],
                    "mtime": item["mtime"],
                    "duration_sec": item["duration_sec"],
                    "quick_hash": item["quick_hash"],
                    "content_key": item["content_key"],
                    "youtube_video_id": str(res.video_id),
                    "youtube_url": base_out["youtube_url"],
                    "uploaded": True,
                    "privacy_status": "private" if agent_pu else args.privacy_status,
                    "review_status": "pending",
                    "channel": "NYC_LONG",
                    "job_id": job_id,
                    "uploaded_at": _utc_iso(),
                    "title": gen_title,
                    "result_path": str(single_job / "upload_result.json"),
                }
                entries = ledger_data.get("entries") if isinstance(ledger_data.get("entries"), list) else []
                entries.append(now_ent)
                ledger_data["entries"] = entries
                ledger_data["version"] = 2
                ledger_data["updated_at"] = _utc_iso()
                _save_ledger(ledger_path, ledger_data, warnings)
                idx.content_keys.add(item["content_key"])
                idx.quick_hashes.add(item["quick_hash"])
                idx.add_path_key(vkey)
                idx.add_path_key(str(vid))
                ur = {
                    "ok": True,
                    "video_id": res.video_id,
                    "youtube_video_id": res.video_id,
                    "url": base_out["youtube_url"],
                    "privacy_used": res.privacy_used or res.privacy,
                    "title_used": res.title_used or res.title,
                    "description_used": res.description_used,
                    "tags_used": res.tags_used,
                    "metadata_path": res.metadata_path,
                    "channel_guard_status": res.channel_guard_status,
                    "dedupe_status": res.dedupe_status,
                    "review_queue_path": res.review_queue_path,
                    "review_queue_md_path": res.review_queue_md_path,
                    "agent_uploaded": res.agent_uploaded,
                    "human_review_required": res.human_review_required,
                    "forced_private": res.forced_private,
                }
                (single_job / "upload_result.json").write_text(
                    json.dumps(ur, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            else:
                base_out["status"] = "upload_failed"
                base_out["review_status"] = "upload_failed"
                base_out["review_queue_added"] = False
                base_out["errors"].append(res.error or res.status or "upload_failed")
                err_payload = {**uentry, "uploaded": False}
                (single_job / "upload_error.json").write_text(
                    json.dumps(err_payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

        if args.dry_run:
            if str(base_out.get("status") or "") == "schedule_gated":
                base_out["LONG_DRY_RUN_STATUS"] = "schedule_gated"
            elif selected_needs_clean:
                base_out["status"] = "needs_real_sound_cleanup"
                base_out["block_reason"] = "raw_long_master_requires_real_sound_noise_cleanup_v1"
                base_out["safe_to_enable_long_upload"] = False
                base_out["SAFE_TO_ENABLE_LONG_UPLOAD"] = False
                base_out["LONG_DRY_RUN_STATUS"] = str(base_out.get("status") or "")
            else:
                if eligible_count > 0:
                    base_out["status"] = "ready"
                else:
                    base_out["status"] = "dry_run"
                base_out["block_reason"] = ""
                base_out["safe_to_enable_long_upload"] = bool(eligible_count > 0)
                base_out["SAFE_TO_ENABLE_LONG_UPLOAD"] = bool(eligible_count > 0)
                base_out["LONG_DRY_RUN_STATUS"] = str(base_out.get("status") or "")
            _apply_long_emergency_dry_run_out_fields(base_out, emergency_state, warnings=warnings)
        out_payload = {**base_out, "meta": meta_job}
        try:
            (job_dir / "long_job_result.json").write_text(
                json.dumps(out_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            warnings.append(f"long_job_result_write_failed:{exc!r}")

        print(json.dumps(out_payload, indent=2, ensure_ascii=False))
        if args.dry_run:
            print(f"LONG_AVAILABLE_NEW_CANDIDATES={out_payload.get('LONG_AVAILABLE_NEW_CANDIDATES', '')}")
            print(f"LONG_NEXT_CANDIDATE={out_payload.get('LONG_NEXT_CANDIDATE', '')}")
            print(f"LONG_DRY_RUN_STATUS={out_payload.get('LONG_DRY_RUN_STATUS', out_payload.get('status', ''))}")
            print(f"LONG_AUTOPUBLISH_DISABLED={str(out_payload.get('LONG_AUTOPUBLISH_DISABLED', False)).lower()}")
            print(f"SAFE_TO_ENABLE_LONG_UPLOAD={str(out_payload.get('SAFE_TO_ENABLE_LONG_UPLOAD', False)).lower()}")
            print(f"REAL_SOUND_CLEANUP_STATUS={out_payload.get('REAL_SOUND_CLEANUP_STATUS', '')}")
            print(f"REAL_SOUND_CLEANUP_OUTPUT={out_payload.get('REAL_SOUND_CLEANUP_OUTPUT', '')}")
            print(f"REAL_SOUND_CLEANUP_REPORT={out_payload.get('REAL_SOUND_CLEANUP_REPORT', '')}")
            print(f"block_reason={out_payload.get('block_reason', '')}")
            print(f"selected_video_path={out_payload.get('selected_video_path', '')}")
            print(f"selected_is_clean_real_sound={str(out_payload.get('selected_is_clean_real_sound', False)).lower()}")
            print(f"selected_needs_clean={str(out_payload.get('selected_needs_clean', False)).lower()}")
            print(f"selected_noise_gate_ok={str(out_payload.get('selected_noise_gate_ok', False)).lower()}")
            print(f"REAL_SOUND_GATE_STATUS={out_payload.get('real_sound_gate_status', '')}")
            print(f"RECOMMENDED_CLEANUP_INPUT={out_payload.get('recommended_cleanup_input', '')}")
            print(f"RECOMMENDED_CLEANUP_OUTPUT={out_payload.get('recommended_cleanup_output', '')}")
            print(f"rejected_outputs_count={out_payload.get('rejected_outputs_count', '')}")
            print(f"vertical_rejected_count={out_payload.get('vertical_rejected_count', '')}")
            print(f"walking_rejected_count={out_payload.get('walking_rejected_count', '')}")
            print(f"mixed_aspect_rejected_count={out_payload.get('mixed_aspect_rejected_count', '')}")
            print(f"mixed_source_type_rejected_count={out_payload.get('mixed_source_type_rejected_count', '')}")
            print(f"source_policy_summary={out_payload.get('source_policy_summary', '')}")
            print(f"RAW_SOURCE_CANDIDATES_COUNT={out_payload.get('RAW_SOURCE_CANDIDATES_COUNT', '')}")
            print(f"OUTPUT_ARTIFACTS_EXCLUDED_COUNT={out_payload.get('OUTPUT_ARTIFACTS_EXCLUDED_COUNT', '')}")
            print(f"UNKNOWN_LONG_CANDIDATES_COUNT={out_payload.get('UNKNOWN_LONG_CANDIDATES_COUNT', '')}")
            print(f"NYC_LONG_TODAY_PLAN={json.dumps(out_payload.get('nyc_long_today_plan', {}), ensure_ascii=False)}")
            print(f"NYC_LONG_SCHEDULE_GATE_ALLOWED={str((out_payload.get('nyc_long_schedule_gate') or {}).get('allowed', True)).lower()}")
        try:
            from always_publish.queue_integration import record_queue_outcome  # noqa: WPS433

            if args.upload:
                record_queue_outcome(kind="long", payload=base_out)
        except Exception:
            pass

        ok_all = args.dry_run or all(
            u.get("ok") for u in base_out.get("uploads", []) or [{"ok": True}]
        )
        return 0 if ok_all else 1
    finally:
        if lock_acquired:
            _release_lock(lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
