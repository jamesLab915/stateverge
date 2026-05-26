#!/usr/bin/env python3
"""Shorts cut + optional YouTube upload (Pro-only).

**Source policy (independent of NYC long-form):** ``all_video_image_highlight_candidates``
scans only formal iphone + cache inboxes for **all** compliant videos and images.
No weekday 05:00–18:00, no driving-only pool, no chronological long assembly rules.

- Highlight scoring + random pick from top-N.
- Video: portrait pad or landscape center-crop to 1080×1920, CFR, yuv420p, no ``-c:v copy``.
- Image: Ken Burns segments; HEIC/DNG/TIFF via ``sips`` cache (originals untouched).
- Dedup ledger: ``publish_pack/shorts_uploads/shorts_used_assets.json`` (fcntl + atomic rewrite; segment
  reserved **before** encode; upload blocked if ``output_video`` already uploaded).
- Concurrency: one global non-blocking flock per machine (``~/StateVerge/data/shorts_runtime/.shorts_cut_upload.global.lock``);
  set ``STATEVERGE_SHORTS_SKIP_GLOBAL_LOCK=1`` only for emergencies.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

JOB_TYPE = "shorts_cut_upload"
SHORTS_SOURCE_POLICY = "all_video_image_highlight_candidates"
SHORTS_USES_LONG_POLICY = False

# Single-machine Shorts worker serialization (launchd + server + manual).
_FALLBACK_SHORTS_RUNTIME = Path.home() / "StateVerge" / "data" / "shorts_runtime"
SHORTS_LEDGER_FLOCK_SUFFIX = ".shorts_used_assets.fcntl.lock"
SHORTS_RESERVE_LEASE_SEC = 7200

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
_AUDIO_SCRIPTS = _SCRIPTS_ROOT / "audio"
_STATEVERGE_SRC = Path.home() / "StateVerge" / "src"
for p in (_SCRIPTS_ROOT, _AUDIO_SCRIPTS, _STATEVERGE_SRC):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audio_fade_helpers import music_fade_filter  # noqa: E402

_NYC_AUTO = _SCRIPTS_ROOT / "nyc_auto"
if str(_NYC_AUTO) not in sys.path:
    sys.path.insert(0, str(_NYC_AUTO))

from channel_guard import assert_shorts_upload_context  # noqa: E402
from confirm_shorts_channel import confirm_shorts_channel  # noqa: E402
from media_quick_hash import triple_chunk_sha256  # noqa: E402
from youtube_upload import upload_from_package_directory  # noqa: E402

try:
    from utils.shorts_paths import (  # noqa: E402
        ensure_shorts_dirs,
        long_channel_token_path,
        shorts_image_cache_dir,
        shorts_jobs_root,
        shorts_logs_root,
        shorts_publish_pack_root,
        shorts_ready_clips_dir,
        shorts_renders_root,
        shorts_used_assets_json,
        token_shorts_path,
        youtube_client_secrets_path,
    )
    from utils.storage_paths import get_sv_cache, get_sv_transfer  # noqa: E402
except Exception:
    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")

    def token_shorts_path() -> Path:  # type: ignore[misc]
        return Path.home() / "StateVerge/data/youtube/token_shorts.json"

    def youtube_client_secrets_path() -> Path:  # type: ignore[misc]
        return Path.home() / "StateVerge/.secrets/youtube/client_secrets.json"

    def long_channel_token_path() -> Path:  # type: ignore[misc]
        return Path.home() / "StateVerge/data/youtube/token.json"

    def shorts_ready_clips_dir(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return get_sv_transfer() / "ready_to_upload" / "shorts_clips"

    def shorts_publish_pack_root(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return get_sv_transfer() / "publish_pack" / "shorts_uploads"

    def shorts_used_assets_json(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return shorts_publish_pack_root() / "shorts_used_assets.json"

    def shorts_image_cache_dir(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return get_sv_cache() / "renders" / "shorts" / "image_cache"

    def shorts_jobs_root(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return get_sv_cache() / "jobs" / "shorts"

    def shorts_logs_root(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return get_sv_cache() / "logs" / "shorts"

    def shorts_renders_root(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return get_sv_cache() / "renders" / "shorts"

    def ensure_shorts_dirs(*, verbose: bool = False) -> dict[str, Path]:  # type: ignore[misc]
        return {}

_HOMEBREW_FFMPEG = Path("/opt/homebrew/bin/ffmpeg")
_HOMEBREW_FFPROBE = Path("/opt/homebrew/bin/ffprobe")
_IMAGE_DIM_PROBE_TIMEOUT_S = 8.0


def _resolve_ffmpeg_bin() -> str:
    env = (os.environ.get("FFMPEG_BIN") or "").strip()
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    if _HOMEBREW_FFMPEG.is_file():
        return str(_HOMEBREW_FFMPEG)
    raise FileNotFoundError(
        "ffmpeg not found: set FFMPEG_BIN, install ffmpeg, or use /opt/homebrew/bin/ffmpeg"
    )


def _resolve_ffprobe_bin() -> str:
    env = (os.environ.get("FFPROBE_BIN") or "").strip()
    if env:
        return env
    found = shutil.which("ffprobe")
    if found:
        return found
    if _HOMEBREW_FFPROBE.is_file():
        return str(_HOMEBREW_FFPROBE)
    raise FileNotFoundError(
        "ffprobe not found: set FFPROBE_BIN, install ffprobe, or use /opt/homebrew/bin/ffprobe"
    )


FFMPEG = _resolve_ffmpeg_bin()
FFPROBE = _resolve_ffprobe_bin()
SIPS = shutil.which("sips") or "/usr/bin/sips"
EXIFTOOL = shutil.which("exiftool")

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".dng", ".tif", ".tiff"}
MUSIC_SUBDIR = Path("04_AUDIO") / "music" / "shorts"
MUSIC_LIBRARY_ROOT = Path("04_AUDIO") / "music"
# Shorts default: Envato library only (no Suno default).
SHORTS_ENVATO_CATEGORY_ORDER = ("ambient", "cinematic", "calm_piano")
SHORTS_ENVATO_EXCLUDED_NAME_FRAGMENTS = ("vocal", "vocals", "trailer", "edm", "dubstep", "drop", "hardstyle")
METADATA_POLICY_VERSION = "stateverge_content_routing_v1"
MUSIC_FALLBACK_ORDER = SHORTS_ENVATO_CATEGORY_ORDER + ("archive",)
_MUSIC_GLOBS = ("*.m4a", "*.mp3", "*.aac", "*.wav", "*.flac", "*.aiff", "*.aif")
_MUSIC_SCAN_MAX_FILES = 4000
_MUSIC_MISSING_SOURCE_VOL = 0.85

LOUDNORM_AF = "loudnorm=I=-14:LRA=11:TP=-1.5"
# nyc_preserve_raw_v1 — light loudnorm for Shorts source/original audio (no music bed).
PRESERVE_RAW_LOUDNORM_AF = "loudnorm=I=-19:LRA=12:TP=-2"
SOURCE_AUDIO_MODES = frozenset({"original", "source_audio", "real_sound", "source_original"})
RESULT_AUDIO_SOURCE_ORIGINAL = "source_original"


def _is_source_audio_mode(mode: str) -> bool:
    return str(mode or "").strip().lower() in SOURCE_AUDIO_MODES


def _uses_music_bed(mode: str) -> bool:
    return str(mode or "").strip().lower() in ("music", "envato_music")


def _normalize_encode_audio_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    if m in SOURCE_AUDIO_MODES:
        return "original"
    if m in ("music", "envato_music", "silent"):
        return m
    return "envato_music"


def _result_encode_audio_mode_label(mode: str) -> str:
    m = str(mode or "").strip().lower()
    if _is_source_audio_mode(m):
        return RESULT_AUDIO_SOURCE_ORIGINAL
    if m == "envato_music":
        return "envato_music"
    if m == "original_fallback":
        return "original_fallback"
    return m or RESULT_AUDIO_SOURCE_ORIGINAL


def _source_audio_filter_chain(*, volume: float, apply_loudnorm: bool = True) -> str:
    ov = max(0.0, float(volume))
    if ov <= 0.001:
        ov = 1.0
    chain = f"volume={ov},alimiter=limit=0.99"
    if apply_loudnorm:
        chain += f",{PRESERVE_RAW_LOUDNORM_AF}"
    return chain

def _path_slash_lower(p: str) -> str:
    return str(p).replace("\\", "/").lower()


def _infer_path_source_type(low_path: str, basename: str) -> str:
    fn = basename.lower()
    if "/ferry/" in low_path or "staten_island_ferry" in low_path or "ferry" in fn:
        return "ferry"
    if "/driving/" in low_path or any(
        tok in low_path for tok in ("/drive/", "dashcam", "dash_cam", "driving_route")
    ):
        return "driving"
    return ""


def _infer_path_route_type(source_type: str, low_path: str, basename: str) -> str:
    fn = basename.lower()
    if source_type == "ferry" or "/ferry/" in low_path or "ferry" in fn or "staten_island_ferry" in low_path:
        return "ferry_route"
    if source_type == "driving" or "/driving/" in low_path:
        return "driving_route"
    return ""


def _ferry_title_signals(
    low_path: str,
    basename: str,
    *,
    inferred_source_type: str,
    route_type: str,
) -> bool:
    if "/ferry/" in low_path:
        return True
    if inferred_source_type == "ferry":
        return True
    if route_type == "ferry_route":
        return True
    if "ferry" in basename.lower():
        return True
    return False


def _driving_title_signals(
    low_path: str,
    *,
    inferred_source_type: str,
    route_type: str,
) -> bool:
    if "/driving/" in low_path:
        return True
    if inferred_source_type == "driving":
        return True
    if route_type == "driving_route":
        return True
    return False


def infer_shorts_title_routing(source_paths: list[str], job_id: str) -> dict[str, str]:
    """Delegate to content_routing_v1 (ferry before driving before skyline)."""
    from content_routing_v1 import infer_shorts_title_routing as _cr_infer  # noqa: WPS433

    return _cr_infer(source_paths, job_id)


def _apply_shorts_title_routing(result: dict[str, Any], job_id: str) -> None:
    paths = result.get("source_paths") or result.get("selected_assets") or []
    if not isinstance(paths, list):
        paths = [str(paths)]
    try:
        from content_routing_v1 import build_content_routing_bundle  # noqa: WPS433

        asset: dict[str, Any] = {"source_paths": [str(p) for p in paths], "job_id": job_id}
        if result.get("source_type"):
            asset["source_type"] = result["source_type"]
        if result.get("route_type"):
            asset["route_type"] = result["route_type"]
        bundle = build_content_routing_bundle(asset, job_id=job_id)
        result.update(
            {
                "inferred_theme": bundle.get("inferred_theme"),
                "title_theme": bundle.get("title_theme"),
                "title_template_used": bundle.get("title_template_used") or bundle.get("title"),
                "audio_mode": bundle.get("audio_mode"),
                "music_required": bundle.get("music_required"),
                "music_disabled_by_default": bundle.get("music_disabled_by_default"),
                "title_evidence": bundle.get("title_evidence"),
                "blocked_title_terms": bundle.get("blocked_title_terms"),
                "metadata_policy_version": bundle.get("metadata_policy_version"),
                "content_routing_version": bundle.get("content_routing_version"),
                "truth_guard_title": bundle.get("title"),
            }
        )
        low_paths = [_path_slash_lower(str(p)) for p in paths]
        st = ""
        rt = ""
        for raw, low in zip(paths, low_paths):
            base = Path(raw).name
            st = _infer_path_source_type(low, base) or st
            rt = _infer_path_route_type(st, low, base) or rt
        result.setdefault("source_type", st or bundle.get("inferred_theme") or "")
        result.setdefault("route_type", rt)
    except Exception as exc:  # noqa: BLE001
        routing = infer_shorts_title_routing([str(p) for p in paths], job_id)
        result.update(routing)
        result.setdefault("warnings", []).append(f"content_routing_v1_failed:{exc!r}")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        obj.setdefault("warnings", []).append(f"json_write_failed:{exc!r}")


def _write_job(path: Path, data: dict[str, Any]) -> None:
    _write_json(path, data)


def _shorts_write_youtube_metadata_pack(
    pack_dir: Path,
    video_path: Path,
    job_snapshot: dict[str, Any],
    *,
    use_ai_metadata: bool,
) -> dict[str, Any]:
    """Write ``pack_dir/youtube_metadata.json`` plus legacy title/description/tags."""
    try:
        from metadata_generator import ensure_youtube_metadata_file, sync_legacy_package_files
    except ImportError as exc:  # pragma: no cover
        return {"metadata_generated": False, "warnings": [f"metadata_import_failed:{exc!r}"]}

    meta, _outp = ensure_youtube_metadata_file(
        pack_dir,
        video_path,
        video_type="short",
        job_result_json=job_snapshot,
        use_ai_metadata=use_ai_metadata,
        dry_run=False,
    )
    try:
        sync_legacy_package_files(pack_dir, meta)
    except OSError:
        pass
    return meta


def load_timelapse_manifest_paths() -> set[str]:
    mp = Path.home() / "StateVerge" / "config" / "timelapse_slice_manifest.json"
    out: set[str] = set()
    if not mp.is_file():
        return out
    try:
        data = json.loads(mp.read_text(encoding="utf-8", errors="replace"))
        for ent in data.get("files") or []:
            if not isinstance(ent, dict):
                continue
            raw = (ent.get("path") or "").strip()
            if not raw:
                continue
            try:
                out.add(str(Path(raw).expanduser().resolve()))
            except OSError:
                out.add(str(Path(raw).expanduser()))
    except (OSError, json.JSONDecodeError):
        pass
    return out


def _iphone_inbox(xfer: Path) -> Path:
    return xfer / "00_INBOX" / "iphone"


def _cache_inbox(cache: Path) -> Path:
    return cache / "inbox"


def candidate_roots(xfer: Path, cache: Path) -> list[Path]:
    return [_iphone_inbox(xfer), _cache_inbox(cache)]


def _quick_hash(path: Path) -> str:
    h = hashlib.sha256()
    try:
        st = path.stat()
        h.update(f"{st.st_size}:{st.st_mtime_ns}:{path.name}".encode())
        with path.open("rb") as fh:
            h.update(fh.read(1024 * 1024))
    except OSError:
        h.update(str(path).encode())
    return h.hexdigest()[:32]


def _load_used_assets(ledger: Path) -> list[dict[str, Any]]:
    if not ledger.is_file():
        return []
    try:
        data = json.loads(ledger.read_text(encoding="utf-8", errors="replace"))
        ent = data.get("entries")
        return list(ent) if isinstance(ent, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _ledger_flock_path(ledger: Path) -> Path:
    return ledger.with_suffix(ledger.suffix + SHORTS_LEDGER_FLOCK_SUFFIX)


def _parse_time_range_segment(tr: str) -> tuple[float, float] | None:
    tr = (tr or "").strip()
    if "-" not in tr or tr.startswith("image:"):
        return None
    try:
        a, b = tr.split("-", 1)
        return float(a), float(b)
    except ValueError:
        return None


def _parse_lease_until(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_blocks_segment_dedupe(row: dict[str, Any], *, now: datetime) -> bool:
    """Whether this ledger row blocks re-using the same time window / image fingerprint."""
    if row.get("dedupe_active") is False:
        return False
    st = str(row.get("state") or "")
    if st in ("upload_failed", "abandoned"):
        return False
    lease_until = _parse_lease_until(str(row.get("lease_until") or ""))
    if st in ("reserved", "encoded") and lease_until is not None and now > lease_until.astimezone(timezone.utc):
        return False
    if row.get("uploaded") and str(row.get("youtube_video_id") or "").strip():
        return True
    if st == "uploaded":
        return True
    tr = str(row.get("selected_time_range") or "").strip()
    if tr.startswith("image:"):
        return True
    if st in ("reserved", "encoded"):
        return True
    if tr and str(row.get("source_path") or "").strip():
        return True
    return False


def _overlap_ranges(a: float, b: float, ranges: list[tuple[float, float]]) -> bool:
    for x, y in ranges:
        if max(a, x) < min(b, y) - 0.25:
            return True
    return False


def _segment_block_maps(
    entries: list[dict[str, Any]], *, now: datetime
) -> tuple[dict[str, list[tuple[float, float]]], dict[str, list[tuple[float, float]]], set[str]]:
    path_ranges: dict[str, list[tuple[float, float]]] = {}
    qh_ranges: dict[str, list[tuple[float, float]]] = {}
    dedupe_keys: set[str] = set()
    for row in entries:
        if not _row_blocks_segment_dedupe(row, now=now):
            continue
        dk = str(row.get("dedupe_key") or "").strip()
        if dk:
            dedupe_keys.add(dk)
        tr = str(row.get("selected_time_range") or "").strip()
        if tr.startswith("image:"):
            dedupe_keys.add(tr)
        pr = _parse_time_range_segment(tr)
        if not pr:
            continue
        sp = str(row.get("source_path") or "").strip()
        if sp:
            try:
                sp = str(Path(sp).resolve())
            except OSError:
                pass
            path_ranges.setdefault(sp, []).append(pr)
        qh = str(row.get("source_quick_hash") or "").strip()
        if qh:
            qh_ranges.setdefault(qh, []).append(pr)
    return path_ranges, qh_ranges, dedupe_keys


def _with_ledger_flock(ledger: Path, fn: Callable[[], Any]) -> Any:
    lock_path = _ledger_flock_path(ledger)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            return fn()
        return fn()
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _ledger_write_entries(ledger: Path, entries: list[dict[str, Any]]) -> None:
    _write_json(ledger, {"updated_at": _utc(), "entries": entries})


def _append_used_asset(ledger: Path, row: dict[str, Any]) -> None:
    """Append-only under flock (legacy callers). Prefer reserve + patch flow."""

    def op() -> None:
        prev = _load_used_assets(ledger)
        prev.append(row)
        _ledger_write_entries(ledger, prev)

    try:
        _with_ledger_flock(ledger, op)
    except OSError:
        op()


def _try_reserve_video_segment(
    ledger_path: Path,
    *,
    job_id: str,
    source_path: str,
    qh: str,
    time_range: str,
    selected_type: str,
    warnings: list[str],
    lease_sec: int = SHORTS_RESERVE_LEASE_SEC,
) -> bool:
    now = datetime.now(timezone.utc)
    dedupe_key = f"v:{qh}:{time_range.strip()}" if qh else f"p:{source_path}:{time_range.strip()}"

    def op() -> bool:
        entries = _load_used_assets(ledger_path)
        pr, qr, keys = _segment_block_maps(entries, now=now)
        pair = _parse_time_range_segment(time_range)
        if not pair:
            warnings.append("reserve_skip_bad_time_range")
            return False
        a, b = pair
        sp = source_path
        try:
            sp = str(Path(sp).resolve())
        except OSError:
            pass
        if dedupe_key in keys:
            return False
        if _overlap_ranges(a, b, pr.get(sp, [])):
            return False
        if qh and _overlap_ranges(a, b, qr.get(qh, [])):
            return False
        lease = (now + timedelta(seconds=lease_sec)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        entries.append(
            {
                "state": "reserved",
                "lease_until": lease,
                "job_id": job_id,
                "source_path": sp,
                "source_quick_hash": qh,
                "selected_time_range": time_range,
                "dedupe_key": dedupe_key,
                "output_video": "",
                "output_quick_hash": "",
                "uploaded": False,
                "youtube_video_id": "",
                "selected_asset_type": selected_type,
                "created_at": _utc(),
            }
        )
        _ledger_write_entries(ledger_path, entries)
        return True

    try:
        return bool(_with_ledger_flock(ledger_path, op))
    except OSError as exc:
        warnings.append(f"ledger_flock_failed:{exc!r}")
        return op()


def _try_reserve_image_bundle(
    ledger_path: Path,
    *,
    job_id: str,
    image_paths: list[str],
    selected_type: str,
    warnings: list[str],
    lease_sec: int = SHORTS_RESERVE_LEASE_SEC,
) -> tuple[bool, str]:
    """Returns (ok, fingerprint_str stored in selected_time_range)."""
    now = datetime.now(timezone.utc)
    canon = "\n".join(sorted({str(Path(p).expanduser()) for p in image_paths}))
    fp = "image:" + hashlib.sha256(canon.encode()).hexdigest()[:48]

    def op() -> bool:
        entries = _load_used_assets(ledger_path)
        _, _, keys = _segment_block_maps(entries, now=now)
        if fp in keys:
            return False
        lease = (now + timedelta(seconds=lease_sec)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        entries.append(
            {
                "state": "reserved",
                "lease_until": lease,
                "job_id": job_id,
                "source_path": canon[:2000],
                "source_quick_hash": "",
                "selected_time_range": fp,
                "dedupe_key": fp,
                "output_video": "",
                "output_quick_hash": "",
                "uploaded": False,
                "youtube_video_id": "",
                "selected_asset_type": selected_type,
                "created_at": _utc(),
            }
        )
        _ledger_write_entries(ledger_path, entries)
        return True

    try:
        ok = bool(_with_ledger_flock(ledger_path, op))
    except OSError as exc:
        warnings.append(f"ledger_flock_failed:{exc!r}")
        ok = op()
    return ok, fp


def _ledger_patch_job_by_id(ledger_path: Path, job_id: str, patch: dict[str, Any], warnings: list[str]) -> None:
    jid = str(job_id).strip()

    def op() -> None:
        entries = _load_used_assets(ledger_path)
        for i in range(len(entries) - 1, -1, -1):
            row = entries[i]
            if isinstance(row, dict) and str(row.get("job_id") or "").strip() == jid:
                merged = {**row, **patch}
                entries[i] = merged
                _ledger_write_entries(ledger_path, entries)
                return
        warnings.append(f"ledger_patch_missing_job:{jid}")

    try:
        _with_ledger_flock(ledger_path, op)
    except OSError as exc:
        warnings.append(f"ledger_patch_flock_failed:{exc!r}")
        op()


def _ledger_output_quick_hash_uploaded(ledger_path: Path, out_qh: str) -> bool:
    if not (out_qh or "").strip():
        return False

    def op() -> bool:
        for row in _load_used_assets(ledger_path):
            if not isinstance(row, dict):
                continue
            if str(row.get("output_quick_hash") or "").strip() != out_qh:
                continue
            if row.get("uploaded") and str(row.get("youtube_video_id") or "").strip():
                return True
        return False

    try:
        return bool(_with_ledger_flock(ledger_path, op))
    except OSError:
        return op()


def _ledger_output_already_uploaded(ledger_path: Path, out_video: Path) -> bool:
    try:
        key = str(out_video.resolve())
    except OSError:
        key = str(out_video)

    def op() -> bool:
        for row in _load_used_assets(ledger_path):
            if not isinstance(row, dict):
                continue
            ov = str(row.get("output_video") or "").strip()
            if not ov:
                continue
            try:
                ovk = str(Path(ov).resolve())
            except OSError:
                ovk = ov
            if ovk == key and row.get("uploaded") and str(row.get("youtube_video_id") or "").strip():
                return True
        return False

    try:
        return bool(_with_ledger_flock(ledger_path, op))
    except OSError:
        return op()


class ShortsGlobalLockBusy(Exception):
    """Another Shorts worker holds the global lock (non-blocking)."""


@contextmanager
def _shorts_global_lock():
    if os.environ.get("STATEVERGE_SHORTS_SKIP_GLOBAL_LOCK", "").strip().lower() in ("1", "true", "yes"):
        yield
        return
    path = _FALLBACK_SHORTS_RUNTIME / ".shorts_cut_upload.global.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        fh.close()
        raise ShortsGlobalLockBusy from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _collect_music_candidates(root: Path, *, recursive: bool) -> list[Path]:
    """Gather audio files under ``root`` (non-recursive glob or bounded rglob)."""
    if not root.is_dir():
        return []
    out: list[Path] = []
    try:
        if recursive:
            for pat in _MUSIC_GLOBS:
                for p in root.rglob(pat):
                    if len(out) >= _MUSIC_SCAN_MAX_FILES:
                        return out
                    if p.is_file() and not p.name.startswith("._"):
                        try:
                            if p.stat().st_size > 4096:
                                out.append(p)
                        except OSError:
                            continue
        else:
            for pat in _MUSIC_GLOBS:
                for p in root.glob(pat):
                    if p.is_file() and not p.name.startswith("._"):
                        try:
                            if p.stat().st_size > 4096:
                                out.append(p)
                        except OSError:
                            continue
    except OSError:
        return out
    return out


def _newest_music(cands: list[Path]) -> Path | None:
    if not cands:
        return None
    try:
        cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass
    return cands[0]


def _envato_track_allowed(path: Path) -> bool:
    low = path.name.lower()
    return not any(x in low for x in SHORTS_ENVATO_EXCLUDED_NAME_FRAGMENTS)


def _pick_envato_music_file(
    xfer: Path,
    category: str,
    *,
    warnings: list[str] | None = None,
) -> tuple[Path | None, str, bool]:
    """Envato Shorts BGM: shorts/ then nyc_long/ — prefer ambient, cinematic, calm_piano."""
    tried_cats = [category] + [c for c in MUSIC_FALLBACK_ORDER if c != category]
    base = xfer / MUSIC_SUBDIR
    for cat in tried_cats:
        if cat not in SHORTS_ENVATO_CATEGORY_ORDER and cat != category:
            continue
        d = base / cat
        cands = [p for p in _collect_music_candidates(d, recursive=True) if _envato_track_allowed(p)]
        pick = _newest_music(cands)
        if pick:
            return pick, cat, False
        cands = [p for p in _collect_music_candidates(d, recursive=False) if _envato_track_allowed(p)]
        pick = _newest_music(cands)
        if pick:
            return pick, cat, False

    for cat in SHORTS_ENVATO_CATEGORY_ORDER:
        d = xfer / MUSIC_LIBRARY_ROOT / "nyc_long" / cat
        cands = [p for p in _collect_music_candidates(d, recursive=True) if _envato_track_allowed(p)]
        pick = _newest_music(cands)
        if pick:
            if warnings is not None:
                warnings.append(f"envato_nyc_long_fallback:{cat}")
            return pick, f"nyc_long/{cat}", True

    if base.is_dir():
        cands = [p for p in _collect_music_candidates(base, recursive=True) if _envato_track_allowed(p)]
        pick = _newest_music(cands)
        if pick:
            return pick, "shorts_tree", False
    if warnings is not None:
        warnings.append("envato_music_not_found_fallback_original")
    return None, "", True


def _pick_music_file(
    xfer: Path,
    category: str,
    *,
    warnings: list[str] | None = None,
) -> tuple[Path | None, str]:
    """Legacy alias — Envato-only selection for Shorts."""
    pick, label, _ = _pick_envato_music_file(xfer, category, warnings=warnings)
    return pick, label


def _ffprobe_json(path: Path) -> dict[str, Any] | None:
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "{}")
    except Exception:
        return None


def _has_audio_stream(j: dict[str, Any] | None) -> bool:
    if not j:
        return False
    for st in j.get("streams") or []:
        if st.get("codec_type") == "audio":
            return True
    return False


def _duration_fp(path: Path, j: dict[str, Any] | None) -> float:
    if j:
        try:
            return float((j.get("format") or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            pass
    return 0.0


def _video_dims(j: dict[str, Any] | None) -> tuple[int, int]:
    if not j:
        return 0, 0
    for st in j.get("streams") or []:
        if st.get("codec_type") == "video":
            try:
                return int(st.get("width") or 0), int(st.get("height") or 0)
            except (TypeError, ValueError):
                return 0, 0
    return 0, 0


def _avg_fps_value(j: dict[str, Any] | None) -> float:
    if not j:
        return 0.0
    for st in j.get("streams") or []:
        if st.get("codec_type") != "video":
            continue
        s = str(st.get("avg_frame_rate") or "")
        m = re.match(r"^(\d+)/(\d+)$", s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return float(a) / float(b) if b else 0.0
    return 0.0


def _tag_blob(j: dict[str, Any] | None) -> str:
    if not j:
        return ""
    parts: list[str] = []
    fmt = j.get("format") or {}
    parts.append(json.dumps(fmt.get("tags") or {}))
    for st in j.get("streams") or []:
        parts.append(json.dumps(st.get("tags") or {}))
    return "|".join(parts).lower()


def _image_dims_sips(path: Path, *, warnings: list[str] | None = None) -> tuple[int, int]:
    """Read pixel dimensions via ``sips``. DNG skips sips (ffprobe fallback). HEIC/other: 8s fail-open."""
    ext = path.suffix.lower()
    if ext == ".dng":
        return 0, 0
    try:
        r = subprocess.run(
            [SIPS, "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
            capture_output=True,
            text=True,
            timeout=_IMAGE_DIM_PROBE_TIMEOUT_S,
            check=False,
        )
        if r.returncode != 0:
            return 0, 0
        w = h = 0
        for line in (r.stdout or "").splitlines():
            if "pixelWidth" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    w = int(m.group(1))
            if "pixelHeight" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    h = int(m.group(1))
        return w, h
    except subprocess.TimeoutExpired:
        if warnings is not None:
            warnings.append("image_dim_probe_timeout")
        return 0, 0
    except OSError:
        return 0, 0


def _image_has_gps(path: Path) -> bool:
    if not EXIFTOOL:
        return False
    try:
        r = subprocess.run(
            [EXIFTOOL, "-s", "-s", "-GPSLatitude", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return bool((r.stdout or "").strip())
    except OSError:
        return False


def score_video_highlight(path: Path, prob: dict[str, Any] | None) -> dict[str, Any]:
    w, h = _video_dims(prob)
    dur = _duration_fp(path, prob)
    blob = _tag_blob(prob)
    fps = _avg_fps_value(prob)
    br = 0.0
    if prob:
        try:
            br = float((prob.get("format") or {}).get("bit_rate") or 0.0)
        except (TypeError, ValueError):
            br = 0.0

    portrait_bonus = 22.0 if h > w * 1.05 else 0.0
    sharpness_score = min(18.0, max(0.0, min(w, h) / 120.0))
    exposure_score = 12.0 if 720 <= min(w, h) else 6.0
    stability_score = 10.0 if 23 <= fps <= 35 else (8.0 if fps > 0 else 5.0)
    motion_score = min(15.0, abs(fps - 30.0) * 0.3 + (6.0 if fps >= 50 else 0.0))
    night_hint = any(x in blob for x in ("night", "evening", "iso", "dark"))
    night_lights_score = 8.0 if night_hint else 4.0
    landmark_score = 6.0 if any(x in blob for x in ("gps", "location", "destination")) else 3.0
    location_score = landmark_score
    duration_score = 14.0 if dur >= 20 else (6.0 if dur >= 8 else -10.0)
    if dur > 90:
        duration_score += 8.0
    timelapse_penalty = 0.0
    if "timelapse" in blob or "time-lapse" in blob:
        timelapse_penalty = 35.0
    quality_penalty = 0.0
    if min(w, h) < 540:
        quality_penalty += 12.0
    total = (
        38.0
        + portrait_bonus
        + sharpness_score
        + exposure_score
        + stability_score
        + motion_score
        + night_lights_score
        + location_score
        + duration_score
        - timelapse_penalty
        - quality_penalty
    )
    return {
        "highlight_score": round(total, 3),
        "portrait_bonus": portrait_bonus,
        "sharpness_score": sharpness_score,
        "exposure_score": exposure_score,
        "stability_score": stability_score,
        "motion_score": motion_score,
        "night_lights_score": night_lights_score,
        "landmark_score": landmark_score,
        "location_score": location_score,
        "duration_score": duration_score,
        "timelapse_penalty": timelapse_penalty,
        "quality_penalty": quality_penalty,
        "timelapse_manifest_penalty": 0.0,
        "width": w,
        "height": h,
        "duration_sec": dur,
        "avg_fps": fps,
        "bitrate_hint": br,
    }


def score_image_highlight(path: Path, *, warnings: list[str] | None = None) -> dict[str, Any]:
    w, h = _image_dims_sips(path, warnings=warnings)
    if w <= 0 or h <= 0:
        prob = _ffprobe_json(path)
        if prob:
            w, h = _video_dims(prob)
    gps = _image_has_gps(path)
    ext = path.suffix.lower()
    megapixels = (w * h) / 1e6 if w and h else 0.0
    resolution_score = min(22.0, megapixels * 8.0)
    portrait_bonus = 15.0 if h > w * 1.05 else (8.0 if w > h * 1.05 else 10.0)
    composition_score = 10.0 if min(w, h) >= 1440 else 6.0
    landmark_score = 12.0 if gps else 5.0
    night_city_hint = any(x in path.name.lower() for x in ("night", "nyc", "times", "brooklyn", "manhattan"))
    street_score = 8.0 if night_city_hint else 5.0
    exposure_score = 8.0
    ken_burns_ok = min(w, h) >= 800
    ken_bonus = 10.0 if ken_burns_ok else 3.0
    raw_bonus = 6.0 if ext in (".heic", ".dng", ".tif", ".tiff") else 0.0
    total = (
        28.0 + resolution_score + portrait_bonus + composition_score + landmark_score + street_score + exposure_score + ken_bonus + raw_bonus
    )
    return {
        "highlight_score": round(total, 3),
        "resolution_score": resolution_score,
        "portrait_bonus": portrait_bonus,
        "composition_score": composition_score,
        "landmark_gps_score": landmark_score,
        "street_city_hint_score": street_score,
        "exposure_score": exposure_score,
        "ken_burns_suitable_score": ken_bonus,
        "raw_format_bonus": raw_bonus,
        "width": w,
        "height": h,
        "has_gps": gps,
    }


def discover_highlight_candidates(
    xfer: Path,
    cache: Path,
    tl_paths: set[str],
    log: list[str],
    *,
    warnings: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    videos: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    for root in candidate_roots(xfer, cache):
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.name.startswith("._") or p.name.startswith("."):
                    continue
                suf = p.suffix.lower()
                try:
                    key = str(p.resolve())
                except OSError:
                    key = str(p)
                if suf in VIDEO_EXT:
                    if key in tl_paths:
                        continue
                    prob = _ffprobe_json(p)
                    if not prob:
                        continue
                    if not any(s.get("codec_type") == "video" for s in prob.get("streams") or []):
                        continue
                    dur = _duration_fp(p, prob)
                    if dur > 0 and dur < 1.0:
                        continue
                    detail = score_video_highlight(p, prob)
                    videos.append({"path": p, "kind": "video", "score": detail["highlight_score"], "detail": detail})
                elif suf in IMAGE_EXT:
                    detail = score_image_highlight(p, warnings=warnings)
                    images.append({"path": p, "kind": "image", "score": detail["highlight_score"], "detail": detail})
        except OSError:
            continue
    videos.sort(key=lambda x: float(x["score"]), reverse=True)
    images.sort(key=lambda x: float(x["score"]), reverse=True)
    log.append(f"highlight_pool videos={len(videos)} images={len(images)} policy={SHORTS_SOURCE_POLICY}")
    return videos, images


def _filter_unused_video(
    videos: list[dict[str, Any]],
    used: list[dict[str, Any]],
    *,
    chunk_seconds: float,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    used_ranges, qh_used_ranges, _keys = _segment_block_maps(used, now=now)

    out: list[dict[str, Any]] = []
    for c in videos:
        p: Path = c["path"]
        prob = _ffprobe_json(p)
        dur = _duration_fp(p, prob)
        start_max = max(0.0, dur - chunk_seconds)
        start = 0.0 if dur <= chunk_seconds else min(start_max, max(0.0, dur * 0.12))
        end = min(dur, start + chunk_seconds)
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        ranges = used_ranges.get(key, [])
        qh = _quick_hash(p)
        qh_ranges = qh_used_ranges.get(qh, [])
        c2 = dict(c)
        overlap_path = _overlap_ranges(start, end, ranges)
        overlap_qh = _overlap_ranges(start, end, qh_ranges) if qh else False
        if overlap_path or overlap_qh:
            c2["_dedupe_overlap"] = True
        else:
            c2["_pick_start"] = start
            c2["_pick_end"] = end
        out.append(c2)
    clean = [x for x in out if not x.get("_dedupe_overlap")]
    return clean if clean else out


def _pick_top_random(rng: random.Random, items: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    if not items:
        return []
    head = items[: max(1, min(top_n, len(items)))]
    rng.shuffle(head)
    return head


def _vf_chain_for_video(*, fps: int, landscape_crop: bool) -> str:
    base = f"fps={fps},format=yuv420p,"
    if landscape_crop:
        return base + "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1"
    return base + "scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"


def _audio_chain_suffix(from_original_music_or_silent: str) -> str:
    if from_original_music_or_silent in ("silent", "source"):
        return ""
    return f",{LOUDNORM_AF}"


def _apply_shorts_encode_audio_fields(
    result: dict[str, Any],
    *,
    encode_mode: str,
    content_routing_audio_mode: str = "",
    music_path: Path | None = None,
) -> None:
    """Record encode-time audio truth in ``shorts_job_result.json`` (overrides metadata music intent)."""
    label = _result_encode_audio_mode_label(encode_mode)
    cr = str(content_routing_audio_mode or result.get("content_routing_audio_mode") or "").strip()
    if not cr and str(result.get("audio_mode") or "").strip() and not _is_source_audio_mode(str(result.get("audio_mode"))):
        cr = str(result.get("audio_mode"))
    if cr:
        result["content_routing_audio_mode"] = cr
    result["audio_mode"] = label
    result["encode_audio_mode"] = label
    result["music_disabled"] = label == RESULT_AUDIO_SOURCE_ORIGINAL
    result["music_path"] = str(music_path) if music_path and music_path.is_file() else None
    meta_wants_music = cr.lower() in ("music_first", "cinematic_music_first", "music")
    result["audio_intent_overridden"] = (
        RESULT_AUDIO_SOURCE_ORIGINAL if (label == RESULT_AUDIO_SOURCE_ORIGINAL and meta_wants_music) else ""
    )
    result["shorts_original_audio_ready"] = label == RESULT_AUDIO_SOURCE_ORIGINAL


def _encode_video_segment(
    src: Path,
    dst: Path,
    *,
    start: float,
    chunk_seconds: float,
    fps: int,
    landscape_crop: bool,
    audio_mode: str,
    music: Path | None,
    music_volume: float,
    original_volume: float,
    log: list[str],
    warnings: list[str],
) -> tuple[bool, dict[str, Any]]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    prob = _ffprobe_json(src)
    vf_main = _vf_chain_for_video(fps=fps, landscape_crop=landscape_crop)
    enc_vt = ["-c:v", "h264_videotoolbox", "-b:v", "12M", "-tag:v", "avc1"]
    enc_x264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-tag:v", "avc1"]
    attempts: list[tuple[str, list[str]]] = [("h264_videotoolbox", enc_vt), ("libx264", enc_x264)]

    for enc_name, enc_args in attempts:
        cmd: list[str] = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-fflags",
            "+genpts",
            "-ss",
            str(start),
            "-t",
            str(chunk_seconds),
            "-i",
            str(src),
        ]
        filter_complex = ""
        maps: list[str] = []

        def audio_tail(tag: str) -> str:
            return _audio_chain_suffix(tag)

        if _is_source_audio_mode(audio_mode):
            if _has_audio_stream(prob):
                af = _source_audio_filter_chain(volume=original_volume, apply_loudnorm=True)
                filter_complex = f"[0:v]{vf_main}[v];[0:a]{af}[a]"
                maps = ["-map", "[v]", "-map", "[a]"]
            else:
                warnings.append("source_video_no_audio_track_silent_output")
                cmd.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
                filter_complex = (
                    f"[0:v]{vf_main}[v];[1:a]atrim=end={chunk_seconds},asetpts=PTS-STARTPTS{audio_tail('silent')}[a]"
                )
                maps = ["-map", "[v]", "-map", "[a]"]
        elif audio_mode == "silent":
            cmd.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
            filter_complex = f"[0:v]{vf_main}[v];[1:a]atrim=end={chunk_seconds},asetpts=PTS-STARTPTS{audio_tail('silent')}[a]"
            maps = ["-map", "[v]", "-map", "[a]"]
        elif _uses_music_bed(audio_mode) and music and music.is_file():
            cmd.extend(["-stream_loop", "-1", "-i", str(music)])
            mv = max(0.0, float(music_volume))
            mus_fade = music_fade_filter(chunk_seconds, shorts=True)
            filter_complex = (
                f"[0:v]{vf_main}[v];"
                f"[1:a]aloop=loop=-1:start=0:size=2e+09,atrim=0:{chunk_seconds},asetpts=PTS-STARTPTS,"
                f"{mus_fade},volume={mv}{audio_tail('music')}[a]"
            )
            maps = ["-map", "[v]", "-map", "[a]"]
        elif _uses_music_bed(audio_mode) and _has_audio_stream(prob):
            ov = max(0.0, float(original_volume))
            if ov <= 0.001:
                ov = _MUSIC_MISSING_SOURCE_VOL
                warnings.append("music_missing_fallback_source_audio")
            filter_complex = (
                f"[0:v]{vf_main}[v];[0:a]volume={ov},alimiter=limit=0.99{audio_tail('music')}[a]"
            )
            maps = ["-map", "[v]", "-map", "[a]"]
        else:
            if _has_audio_stream(prob):
                warnings.append("music_missing_fallback_source_audio")
                af = _source_audio_filter_chain(volume=original_volume or 1.0, apply_loudnorm=True)
                filter_complex = f"[0:v]{vf_main}[v];[0:a]{af}[a]"
                maps = ["-map", "[v]", "-map", "[a]"]
            else:
                warnings.append("music_missing_no_source_audio_silent_output")
                cmd.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
                filter_complex = (
                    f"[0:v]{vf_main}[v];[1:a]atrim=end={chunk_seconds},asetpts=PTS-STARTPTS{audio_tail('silent')}[a]"
                )
                maps = ["-map", "[v]", "-map", "[a]"]

        cmd.extend(["-filter_complex", filter_complex, *maps, *enc_args])
        cmd.extend(["-c:a", "aac", "-ar", "48000", "-b:a", "192k", "-movflags", "+faststart", str(dst)])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(600, int(chunk_seconds) * 40), check=False)
        except subprocess.TimeoutExpired:
            warnings.append(f"ffmpeg_timeout:{enc_name}")
            continue
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 1024:
            log.append(f"ffmpeg_ok encoder={enc_name} segment")
            return True, {"encoder": enc_name, "stderr_tail": (r.stderr or "")[-600:]}
        warnings.append(f"ffmpeg_fail:{enc_name} rc={r.returncode}")
    return False, {"ffmpeg_error": "all_encoders_failed"}


def _ensure_raster_image(src: Path, cache_dir: Path) -> Path | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    suf = src.suffix.lower()
    if suf in (".jpg", ".jpeg", ".png"):
        return src
    out = cache_dir / f"{_quick_hash(src)}_{src.stem}.jpg"
    if out.is_file():
        return out
    try:
        r = subprocess.run(
            [SIPS, "-s", "format", "jpeg", str(src), "--out", str(out)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if r.returncode == 0 and out.is_file():
            return out
    except OSError:
        pass
    prob = _ffprobe_json(src)
    if prob and any(s.get("codec_type") == "video" for s in prob.get("streams") or []):
        return src
    return None


def _encode_image_ken_burns(
    img_raster: Path,
    dst: Path,
    *,
    segment_seconds: float,
    fps: int,
    audio_mode: str,
    music: Path | None,
    music_volume: float,
    log: list[str],
    warnings: list[str],
) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    d = max(1, int(segment_seconds * fps))
    zexpr = "min(zoom+0.0015,1.28)"
    vf = (
        f"scale=1440:2560:force_original_aspect_ratio=increase,zoompan=z='{zexpr}':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s=1080x1920:fps={fps},format=yuv420p"
    )
    enc_vt = ["-c:v", "h264_videotoolbox", "-b:v", "10M", "-tag:v", "avc1"]
    enc_x264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "21", "-tag:v", "avc1"]
    for enc_name, enc_args in [("h264_videotoolbox", enc_vt), ("libx264", enc_x264)]:
        cmd: list[str] = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loop", "1", "-i", str(img_raster), "-t", str(segment_seconds)]
        if _uses_music_bed(audio_mode) and music and music.is_file():
            cmd.extend(["-stream_loop", "-1", "-i", str(music)])
            mv = max(0.0, float(music_volume))
            mus_fade = music_fade_filter(segment_seconds, shorts=True)
            fc = (
                f"[0:v]{vf}[v];"
                f"[1:a]aloop=loop=-1:start=0:size=2e+09,atrim=0:{segment_seconds},asetpts=PTS-STARTPTS,"
                f"{mus_fade},volume={mv},{LOUDNORM_AF}[a]"
            )
            cmd.extend(["-filter_complex", fc, "-map", "[v]", "-map", "[a]", *enc_args])
        elif _is_source_audio_mode(audio_mode) or audio_mode == "silent":
            warnings.append("image_motion_no_source_audio_silent_track")
            cmd.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
            fc = f"[0:v]{vf}[v];[1:a]atrim=end={segment_seconds},asetpts=PTS-STARTPTS[a]"
            cmd.extend(["-filter_complex", fc, "-map", "[v]", "-map", "[a]", *enc_args])
        else:
            warnings.append("image_music_missing_silent_output")
            cmd.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
            fc = f"[0:v]{vf}[v];[1:a]atrim=end={segment_seconds},asetpts=PTS-STARTPTS[a]"
            cmd.extend(["-filter_complex", fc, "-map", "[v]", "-map", "[a]", *enc_args])
        cmd.extend(["-c:a", "aac", "-ar", "48000", "-b:a", "192k", "-movflags", "+faststart", str(dst)])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(300, int(segment_seconds) * 40), check=False)
        except subprocess.TimeoutExpired:
            warnings.append(f"image_ffmpeg_timeout:{enc_name}")
            continue
        if r.returncode == 0 and dst.is_file() and dst.stat().st_size > 512:
            log.append(f"image_motion_ok encoder={enc_name}")
            return True
        warnings.append(f"image_motion_fail:{enc_name}")
    return False


def _concat_segments(paths: list[Path], dst: Path, log: list[str], warnings: list[str]) -> bool:
    if not paths:
        return False
    if len(paths) == 1:
        try:
            shutil.copy2(paths[0], dst)
            return True
        except OSError:
            return False
    lst = dst.parent / f"{dst.stem}_concat.txt"
    try:
        lines = "\n".join([f"file '{p.resolve()}'" for p in paths])
        lst.write_text(lines, encoding="utf-8")
    except OSError:
        return False
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lst),
        "-c",
        "copy",
        str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    except subprocess.TimeoutExpired:
        warnings.append("concat_timeout")
        return False
    ok = r.returncode == 0 and dst.is_file() and dst.stat().st_size > 512
    if ok:
        log.append("concat_copy_ok")
    else:
        warnings.append("concat_copy_failed_try_reencode")
        cmd2 = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
        ]
        for p in paths:
            cmd2.extend(["-i", str(p)])
        n = len(paths)
        parts = "".join([f"[{i}:v][{i}:a]" for i in range(n)])
        fc = f"{parts}concat=n={n}:v=1:a=1[outv][outa]"
        cmd2.extend(["-filter_complex", fc, "-map", "[outv]", "-map", "[outa]"])
        cmd2.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "21", "-c:a", "aac", "-ar", "48000", str(dst)])
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=900, check=False)
        ok = r2.returncode == 0 and dst.is_file()
    return ok


def _ensure_writable_dir(preferred: Path, fallback_subdir: str, warnings: list[str]) -> Path:
    """Use ``preferred`` (usually under SV_CACHE); if TCC/volume blocks writes, mirror under ~/StateVerge/data."""
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".shorts_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return preferred
    except OSError:
        fb = _FALLBACK_SHORTS_RUNTIME / fallback_subdir
        try:
            fb.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        warnings.append(f"shorts_path_fallback:{preferred}->{fb}")
        return fb


def main() -> int:
    ap = argparse.ArgumentParser(description="Shorts highlight pool encode + optional Shorts-channel upload.")
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--duration-seconds", type=float, default=30.0)
    ap.add_argument(
        "--audio-mode",
        choices=("envato_music", "original", "source_audio", "real_sound", "music", "silent"),
        default="envato_music",
        help="Encode audio: envato_music (default), original/source, music (manual), or silent.",
    )
    ap.add_argument("--music-volume", type=float, default=0.30)
    ap.add_argument("--original-volume", type=float, default=1.0)
    ap.add_argument("--music-category", default="ambient", help="Envato category under 04_AUDIO/music/shorts/.")
    ap.add_argument("--fps", type=int, default=30, choices=(30, 60))
    ap.add_argument("--asset-mode", choices=("auto", "video_only", "image_only", "mixed"), default="auto")
    ap.add_argument("--allow-images", dest="allow_images", action="store_true")
    ap.add_argument("--no-allow-images", dest="allow_images", action="store_false")
    ap.add_argument("--allow-videos", dest="allow_videos", action="store_true")
    ap.add_argument("--no-allow-videos", dest="allow_videos", action="store_false")
    ap.add_argument("--highlight-mode", dest="highlight_mode", action="store_true")
    ap.add_argument("--no-highlight-mode", dest="highlight_mode", action="store_false")
    ap.set_defaults(allow_images=True, allow_videos=True, highlight_mode=True)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--privacy-status", choices=("private", "unlisted"), default="unlisted")
    ap.add_argument(
        "--no-real-sound-gate",
        action="store_true",
        help="Skip Real Sound Cleanup Gate v1 before Shorts upload (default: on for --upload).",
    )
    ap.add_argument(
        "--use-ai-metadata",
        action="store_true",
        help="Optional OpenAI metadata refine (OPENAI_API_KEY; fail-open).",
    )
    ap.add_argument("--agent-private-upload", action="store_true", help="Force private upload + review queue.")
    ap.add_argument(
        "--review-queue",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write review queue JSON after successful upload (default: on).",
    )
    ap.add_argument("--no-public", action="store_true", help="Reject public privacy.")
    args = ap.parse_args()
    args.audio_mode = _normalize_encode_audio_mode(str(args.audio_mode))
    warnings: list[str] = []
    try:
        # Dry-run does not upload; allow concurrent diagnostics without blocking on EX lock.
        if bool(args.dry_run) and not bool(args.upload):
            return _main_body(args, warnings)
        with _shorts_global_lock():
            return _main_body(args, warnings)
    except ShortsGlobalLockBusy:
        print(
            "ERROR: shorts_worker_global_lock_busy (another encode/upload is running). "
            "Unset STATEVERGE_SHORTS_SKIP_GLOBAL_LOCK unless debugging.",
            file=sys.stderr,
        )
        return 14


def _main_body(args: argparse.Namespace, warnings: list[str]) -> int:
    allow_images = bool(args.allow_images)
    allow_videos = bool(args.allow_videos)
    highlight_mode = bool(args.highlight_mode)

    job_id = str(args.job_id).strip()
    duration = float(args.duration_seconds)
    duration = max(15.0, min(60.0, duration))
    do_upload = bool(args.upload) and not bool(args.dry_run)
    agent_pu = bool(getattr(args, "agent_private_upload", False))
    if agent_pu and str(args.privacy_status) != "private":
        print("ERROR: --agent-private-upload requires --privacy-status private", file=sys.stderr)
        return 3

    rng = random.Random(int(hashlib.sha256(job_id.encode()).hexdigest()[:8], 16))

    ensure_shorts_dirs(verbose=False)
    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)

    token_shorts = token_shorts_path()
    client_sec = youtube_client_secrets_path()
    long_tok = long_channel_token_path()

    ready_dir = _ensure_writable_dir(shorts_ready_clips_dir(verbose=False), "shorts_ready_clips", warnings)
    pack_root = _ensure_writable_dir(shorts_publish_pack_root(verbose=False), "shorts_uploads", warnings)
    job_root = _ensure_writable_dir(shorts_jobs_root(verbose=False), "jobs", warnings)
    logs_root = _ensure_writable_dir(shorts_logs_root(verbose=False), "logs", warnings)
    renders_root = _ensure_writable_dir(shorts_renders_root(verbose=False), "renders", warnings)
    img_cache = _ensure_writable_dir(shorts_image_cache_dir(verbose=False), "image_cache", warnings)
    ledger_path = pack_root / "shorts_used_assets.json"

    for d in (ready_dir, pack_root, job_root, logs_root, renders_root, img_cache):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    job_dir = job_root / job_id
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: cannot create job_dir={job_dir}: {exc}", file=sys.stderr)
        return 4
    job_path = job_dir / "job.json"
    pack_dir = pack_root / job_id
    try:
        pack_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: cannot create pack_dir={pack_dir}: {exc}", file=sys.stderr)
        return 4
    result_path = pack_dir / "shorts_job_result.json"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"shorts_clip_{job_id}_{ts}.mp4"
    out_video = ready_dir / out_name
    work_dir = renders_root / job_id / ts
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: cannot create work_dir={work_dir}: {exc}", file=sys.stderr)
        return 4

    log_lines: list[str] = [
        f"shorts_job_start job_id={job_id} policy={SHORTS_SOURCE_POLICY} asset_mode={args.asset_mode} highlight={highlight_mode}"
    ]

    base_result: dict[str, Any] = {
        "job_id": job_id,
        "status": "running",
        "video_type": "short",
        "channel": "SHORTS",
        "privacy_status": str(args.privacy_status),
        "picked": [],
        "uploads": [],
        "errors": [],
        "ledger_path": "",
        "fallback_used": False,
        "created_at": _utc(),
        "finished_at": "",
        "shorts_source_policy": SHORTS_SOURCE_POLICY,
        "shorts_uses_long_policy": SHORTS_USES_LONG_POLICY,
        "requires_review_before_public": True,
        "auto_public_upload_disabled": True,
        "review_before_public": True,
        "review_queue_added": False,
        "review_status": "dry_run" if bool(args.dry_run) or not do_upload else "awaiting_upload",
        "candidate_count_video": 0,
        "candidate_count_image": 0,
        "selected_asset_type": "",
        "selected_assets": [],
        "selected_highlight_scores": [],
        "selected_reason": "",
        "selected_time_range": "",
        "output_video": "",
        "width": 1080,
        "height": 1920,
        "duration_seconds": duration,
        "fps": int(args.fps),
        "resolution": "1080x1920",
        "audio_mode": _result_encode_audio_mode_label(str(args.audio_mode)),
        "encode_audio_mode": _result_encode_audio_mode_label(str(args.audio_mode)),
        "music_source": "envato" if _uses_music_bed(str(args.audio_mode)) else "none",
        "selected_music_path": None,
        "music_license_mode": "envato_claim_clear",
        "metadata_policy_version": METADATA_POLICY_VERSION,
        "music_path": None,
        "music_disabled": _is_source_audio_mode(str(args.audio_mode)),
        "shorts_original_audio_ready": _is_source_audio_mode(str(args.audio_mode)),
        "upload_requested": bool(args.upload),
        "upload_attempted": False,
        "uploaded": False,
        "youtube_channel_title": "",
        "youtube_video_id": "",
        "youtube_url": "",
        "block_reason": "",
        "warnings": warnings,
        "confirm_shorts_channel_ok": False,
        "youtube_channel_id": "",
        "upload_result_path": "",
        "ready_to_upload_path": str(ready_dir),
        "source_paths": [],
    }
    base_result["ledger_path"] = str(ledger_path)

    job: dict[str, Any] = {
        "job_id": job_id,
        "type": JOB_TYPE,
        "status": "running",
        "progress": 5,
        "created_at": _utc(),
        "started_at": _utc(),
        "finished_at": "",
        "request": {
            "upload": bool(args.upload),
            "dry_run": bool(args.dry_run),
            "duration_seconds": duration,
            "audio_mode": args.audio_mode,
            "music_volume": float(args.music_volume),
            "original_volume": float(args.original_volume),
            "fps": int(args.fps),
            "asset_mode": args.asset_mode,
            "allow_images": allow_images,
            "allow_videos": allow_videos,
            "highlight_mode": highlight_mode,
        },
        "output": {},
        "result": base_result,
        "warnings": warnings,
        "errors": [],
    }
    _write_job(job_path, job)

    tl = load_timelapse_manifest_paths()
    videos_raw, images_raw = discover_highlight_candidates(xfer, cache, tl, log_lines, warnings=warnings)
    base_result["candidate_count_video"] = len(videos_raw)
    base_result["candidate_count_image"] = len(images_raw)

    videos = [v for v in videos_raw] if allow_videos else []
    images = [i for i in images_raw] if allow_images else []

    if not videos and not images:
        base_result.update({"status": "blocked", "block_reason": "no_shorts_pool_assets"})
        job["status"] = "blocked"
        job["progress"] = 100
        job["finished_at"] = _utc()
        job["result"] = base_result
        _write_job(job_path, job)
        _write_json(result_path, base_result)
        return 6

    used_entries = _load_used_assets(ledger_path)
    videos_scored = _filter_unused_video(videos, used_entries, chunk_seconds=min(duration, 60.0)) if videos else []

    top_n = 14
    if highlight_mode:
        v_pool = _pick_top_random(rng, videos_scored if videos_scored else videos, top_n)
        i_pool = _pick_top_random(rng, images, top_n)
    else:
        v_pool = videos[:top_n]
        i_pool = images[:top_n]
        rng.shuffle(v_pool)
        rng.shuffle(i_pool)

    asset_mode = str(args.asset_mode)
    selected_type = ""
    selected: list[dict[str, Any]] = []
    reason = ""

    best_v = v_pool[0] if v_pool else None
    best_i = i_pool[0] if i_pool else None
    v_score = float(best_v["score"]) if best_v else -1.0
    i_score = float(best_i["score"]) if best_i else -1.0

    if asset_mode == "video_only" and not v_pool:
        base_result.update({"status": "blocked", "block_reason": "no_video_candidates"})
        job["status"] = "blocked"
        job["progress"] = 100
        job["finished_at"] = _utc()
        job["result"] = base_result
        _write_job(job_path, job)
        _write_json(result_path, base_result)
        return 11
    if asset_mode == "image_only" and not i_pool:
        base_result.update({"status": "blocked", "block_reason": "no_image_candidates"})
        job["status"] = "blocked"
        job["progress"] = 100
        job["finished_at"] = _utc()
        job["result"] = base_result
        _write_job(job_path, job)
        _write_json(result_path, base_result)
        return 12

    def _vid_sel(c: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": str(c["path"]),
            "kind": "video",
            "score": c["score"],
            "detail": c.get("detail"),
            "_pick_start": c.get("_pick_start"),
            "_pick_end": c.get("_pick_end"),
            "_dedupe_overlap": c.get("_dedupe_overlap"),
        }

    if asset_mode == "video_only":
        selected_type = "video_clip_short"
        c = v_pool[0]
        selected = [_vid_sel(c)]
        reason = "asset_mode_video_only_top_random"
    elif asset_mode == "image_only":
        selected_type = "image_motion_short"
        n_img = max(1, min(4, int(round(duration / 10))))
        picked = i_pool[:n_img]
        selected = [{"path": str(p["path"]), "kind": "image", "score": p["score"], "detail": p.get("detail")} for p in picked]
        reason = f"asset_mode_image_only_count_{len(selected)}"
    elif asset_mode == "mixed":
        if not v_pool or not i_pool:
            asset_mode = "auto"
        else:
            selected_type = "mixed_video_image_short"
            selected_v = v_pool[0]
            n_img = max(1, min(3, int(round((duration * 0.55) / 8))))
            imgs = i_pool[:n_img]
            selected = [_vid_sel(selected_v)]
            selected.extend(
                [{"path": str(p["path"]), "kind": "image", "score": p["score"], "detail": p.get("detail")} for p in imgs]
            )
            reason = "mixed_split_video_plus_images"
    if asset_mode == "auto":
        pick_video = bool(v_pool) and (not i_pool or v_score >= i_score - 5.0)
        if pick_video:
            selected_type = "video_clip_short"
            c = v_pool[0]
            selected = [_vid_sel(c)]
            reason = "auto_prefer_video_highlight_score"
        else:
            selected_type = "image_motion_short"
            n_img = max(1, min(4, int(round(duration / 10))))
            picked = i_pool[:n_img]
            selected = [{"path": str(p["path"]), "kind": "image", "score": p["score"], "detail": p.get("detail")} for p in picked]
            reason = "auto_prefer_image_highlight_score"

    base_result["selected_asset_type"] = selected_type
    base_result["selected_assets"] = [s["path"] for s in selected]
    base_result["selected_highlight_scores"] = [float(s["score"]) for s in selected]
    base_result["selected_reason"] = reason
    base_result["source_paths"] = base_result["selected_assets"]
    _apply_shorts_title_routing(base_result, job_id)
    content_routing_audio_mode = str(base_result.get("audio_mode") or "")

    music_path: Path | None = None
    envato_fallback = False
    if _uses_music_bed(str(args.audio_mode)):
        music_path, mc, envato_fallback = _pick_envato_music_file(
            xfer, str(args.music_category), warnings=warnings
        )
        log_lines.append(f"envato_music_pick cat={mc} path={music_path} fallback={envato_fallback}")
        if music_path is None or not music_path.is_file():
            warnings.append("envato_music_not_found_fallback_original")
            args.audio_mode = "original"
            base_result["audio_mode"] = "original_fallback"
            base_result["encode_audio_mode"] = "original_fallback"
            base_result["music_source"] = "none"
            base_result["fallback_used"] = True
        else:
            base_result["music_path"] = str(music_path)
            base_result["selected_music_path"] = str(music_path)
            base_result["music_category_used"] = mc
            base_result["music_source"] = "envato"
            base_result["fallback_used"] = bool(envato_fallback)

    intermediate = work_dir / "final.mp4"
    ok_final = False
    enc_meta: dict[str, Any] = {}

    if selected_type == "video_clip_short":
        src = Path(selected[0]["path"])
        prob = _ffprobe_json(src)
        dur_all = _duration_fp(src, prob)
        start_max = max(0.0, dur_all - duration)
        default_start = 0.0 if dur_all <= duration else min(start_max, max(0.0, dur_all * 0.12))
        ps = selected[0].get("_pick_start")
        pe = selected[0].get("_pick_end")
        start = float(ps) if ps is not None else float(default_start)
        end = float(pe) if pe is not None else min(dur_all, start + duration)
        if selected[0].get("_dedupe_overlap"):
            start = rng.uniform(0.0, max(0.001, start_max))
            end = min(dur_all, start + duration)
            warnings.append("dedupe_overlap_using_random_window")
        w, h = _video_dims(prob)
        landscape_crop = w > h * 1.05
        base_result["selected_time_range"] = f"{start:.2f}-{end:.2f}"
        try:
            src_res = str(src.resolve())
        except OSError:
            src_res = str(src)
        qh_pre = _quick_hash(src)
        if not _try_reserve_video_segment(
            ledger_path,
            job_id=job_id,
            source_path=src_res,
            qh=qh_pre,
            time_range=base_result["selected_time_range"],
            selected_type=selected_type,
            warnings=warnings,
        ):
            base_result.update({"status": "blocked", "block_reason": "shorts_segment_already_reserved_or_used"})
            job["status"] = "blocked"
            job["progress"] = 100
            job["finished_at"] = _utc()
            job["result"] = base_result
            _write_job(job_path, job)
            _write_json(result_path, base_result)
            return 15
        ok_final, enc_meta = _encode_video_segment(
            src,
            intermediate,
            start=start,
            chunk_seconds=min(duration, max(8.0, end - start)),
            fps=int(args.fps),
            landscape_crop=landscape_crop,
            audio_mode=str(args.audio_mode),
            music=music_path,
            music_volume=float(args.music_volume),
            original_volume=float(args.original_volume),
            log=log_lines,
            warnings=warnings,
        )
    elif selected_type == "image_motion_short":
        img_ok, img_fp = _try_reserve_image_bundle(
            ledger_path,
            job_id=job_id,
            image_paths=[str(s["path"]) for s in selected],
            selected_type=selected_type,
            warnings=warnings,
        )
        if not img_ok:
            base_result.update({"status": "blocked", "block_reason": "shorts_image_bundle_already_reserved_or_used"})
            job["status"] = "blocked"
            job["progress"] = 100
            job["finished_at"] = _utc()
            job["result"] = base_result
            _write_job(job_path, job)
            _write_json(result_path, base_result)
            return 16
        base_result["selected_time_range"] = img_fp
        per = duration / max(1, len(selected))
        per = max(3.0, min(12.0, per))
        segs: list[Path] = []
        for i, sel in enumerate(selected):
            ip = Path(sel["path"])
            raster = _ensure_raster_image(ip, img_cache)
            if not raster:
                warnings.append(f"image_raster_fail:{ip}")
                continue
            seg = work_dir / f"imgseg_{i}.mp4"
            if _encode_image_ken_burns(
                raster,
                seg,
                segment_seconds=per,
                fps=int(args.fps),
                audio_mode=str(args.audio_mode),
                music=music_path,
                music_volume=float(args.music_volume),
                log=log_lines,
                warnings=warnings,
            ):
                segs.append(seg)
        ok_final = _concat_segments(segs, intermediate, log_lines, warnings) if segs else False
        enc_meta = {"segments": len(segs)}
    elif selected_type == "mixed_video_image_short":
        src = Path(selected[0]["path"])
        prob = _ffprobe_json(src)
        dur_all = _duration_fp(src, prob)
        dv = max(8.0, duration * 0.42)
        start_max = max(0.0, dur_all - dv)
        start = rng.uniform(0.0, start_max) if start_max > 0 else 0.0
        base_result["selected_time_range"] = f"{start:.2f}-{start + dv:.2f}"
        try:
            src_res_m = str(src.resolve())
        except OSError:
            src_res_m = str(src)
        qh_m = _quick_hash(src)
        if not _try_reserve_video_segment(
            ledger_path,
            job_id=job_id,
            source_path=src_res_m,
            qh=qh_m,
            time_range=base_result["selected_time_range"],
            selected_type=selected_type,
            warnings=warnings,
        ):
            base_result.update({"status": "blocked", "block_reason": "shorts_segment_already_reserved_or_used"})
            job["status"] = "blocked"
            job["progress"] = 100
            job["finished_at"] = _utc()
            job["result"] = base_result
            _write_job(job_path, job)
            _write_json(result_path, base_result)
            return 17
        w, h = _video_dims(prob)
        landscape_crop = w > h * 1.05
        part_v = work_dir / "part_video.mp4"
        ok_v, enc_meta_v = _encode_video_segment(
            src,
            part_v,
            start=start,
            chunk_seconds=dv,
            fps=int(args.fps),
            landscape_crop=landscape_crop,
            audio_mode=str(args.audio_mode),
            music=music_path,
            music_volume=float(args.music_volume),
            original_volume=float(args.original_volume),
            log=log_lines,
            warnings=warnings,
        )
        imgs_sel = selected[1:]
        di = max(8.0, duration - dv)
        per = di / max(1, len(imgs_sel))
        per = max(3.0, min(10.0, per))
        segs_img: list[Path] = []
        for i, sel in enumerate(imgs_sel):
            ip = Path(sel["path"])
            raster = _ensure_raster_image(ip, img_cache)
            if not raster:
                continue
            seg = work_dir / f"mix_img_{i}.mp4"
            if _encode_image_ken_burns(
                raster,
                seg,
                segment_seconds=per,
                fps=int(args.fps),
                audio_mode=str(args.audio_mode),
                music=music_path,
                music_volume=float(args.music_volume),
                log=log_lines,
                warnings=warnings,
            ):
                segs_img.append(seg)
        parts: list[Path] = []
        if ok_v:
            parts.append(part_v)
        parts.extend(segs_img)
        ok_final = _concat_segments(parts, intermediate, log_lines, warnings) if parts else False
        enc_meta = {"video_part": enc_meta_v, "image_segments": len(segs_img)}

    if not ok_final:
        _ledger_patch_job_by_id(
            ledger_path,
            job_id,
            {"state": "abandoned", "dedupe_active": False},
            warnings,
        )
        base_result["status"] = "failed"
        base_result["block_reason"] = "encode_failed"
        base_result["warnings"] = warnings
        job["status"] = "failed"
        job["progress"] = 100
        job["finished_at"] = _utc()
        job["result"] = base_result
        job["errors"].append(json.dumps(enc_meta)[:800])
        _write_job(job_path, job)
        _write_json(result_path, base_result)
        return 7

    try:
        shutil.move(str(intermediate), str(out_video))
    except OSError:
        shutil.copy2(intermediate, out_video)

    prob_out = _ffprobe_json(out_video)
    base_result["output_video"] = str(out_video)
    base_result["duration_seconds"] = round(min(duration, _duration_fp(out_video, prob_out) or duration), 3)
    base_result["warnings"] = warnings
    wo, ho = _video_dims(prob_out)
    if wo:
        base_result["width"] = wo
    if ho:
        base_result["height"] = ho

    _apply_shorts_encode_audio_fields(
        base_result,
        encode_mode=str(args.audio_mode),
        content_routing_audio_mode=content_routing_audio_mode,
        music_path=music_path,
    )
    os.environ["SHORTS_ORIGINAL_AUDIO_READY"] = (
        "true" if base_result.get("shorts_original_audio_ready") else "false"
    )

    qh = _quick_hash(Path(selected[0]["path"])) if selected else ""
    try:
        out_qh = triple_chunk_sha256(out_video)
    except OSError:
        out_qh = ""
    try:
        sp_row = str(Path(selected[0]["path"]).resolve()) if selected else ""
    except OSError:
        sp_row = str(selected[0]["path"]) if selected else ""
    enc_lease = (datetime.now(timezone.utc) + timedelta(seconds=SHORTS_RESERVE_LEASE_SEC)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    _ledger_patch_job_by_id(
        ledger_path,
        job_id,
        {
            "output_video": str(out_video),
            "output_quick_hash": out_qh,
            "state": "encoded",
            "lease_until": enc_lease,
            "source_quick_hash": qh,
            "selected_time_range": base_result.get("selected_time_range") or "",
            "source_path": sp_row,
            "selected_asset_type": selected_type,
            "uploaded": False,
        },
        warnings,
    )

    job["progress"] = 80
    job["output"] = {"video_path": str(out_video), "encoder_meta": enc_meta}

    upload_result_path = pack_dir / "upload_result.json"
    base_result["upload_result_path"] = str(upload_result_path)

    use_ai_md = bool(getattr(args, "use_ai_metadata", False))
    jr_meta = {**base_result}
    try:
        sm = _shorts_write_youtube_metadata_pack(pack_dir, Path(out_video), jr_meta, use_ai_metadata=use_ai_md)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"shorts_metadata_exception:{exc!r}")
        sm = {}
    if isinstance(sm, dict) and sm.get("metadata_generated"):
        base_result["metadata_generated"] = True
        base_result["generated_title"] = str(sm.get("title") or "")
        base_result["generated_description"] = str(sm.get("description") or "")
        base_result["generated_hashtags"] = sm.get("hashtags") if isinstance(sm.get("hashtags"), list) else []
        base_result["generated_tags"] = sm.get("tags") if isinstance(sm.get("tags"), list) else []
        base_result["metadata_path"] = str(pack_dir / "youtube_metadata.json")
        base_result["metadata_generator_version"] = str(sm.get("metadata_generator_version") or "")
        encode_audio = base_result.get("encode_audio_mode") or base_result.get("audio_mode")
        for k in (
            "inferred_theme",
            "title_theme",
            "source_type",
            "route_type",
            "title_template_used",
            "music_required",
            "music_disabled_by_default",
            "metadata_policy_version",
            "blocked_title_terms",
            "title_evidence",
            "content_routing_version",
        ):
            if sm.get(k) is not None and str(sm.get(k) or "").strip() != "":
                base_result[k] = sm.get(k)
        if sm.get("audio_mode") is not None and str(sm.get("audio_mode") or "").strip() != "":
            base_result["content_routing_audio_mode"] = str(sm.get("audio_mode"))
        if encode_audio:
            base_result["audio_mode"] = encode_audio
            base_result["encode_audio_mode"] = encode_audio
    base_result["warnings"] = warnings
    job["result"] = base_result
    _write_job(job_path, job)

    if not do_upload:
        base_result["status"] = "completed"
        base_result["upload_attempted"] = False
        base_result["uploaded"] = False
        job["status"] = "completed"
        job["progress"] = 100
        job["finished_at"] = _utc()
        job["result"] = base_result
        _write_job(job_path, job)
        _write_json(result_path, base_result)
        try:
            (logs_root / f"{job_id}.log").write_text("\n".join(log_lines), encoding="utf-8")
        except OSError:
            pass
        return 0

    if not token_shorts.is_file():
        base_result["status"] = "blocked"
        base_result["block_reason"] = "missing_token_shorts"
        base_result["upload_attempted"] = False
        job["status"] = "blocked"
        job["progress"] = 100
        job["finished_at"] = _utc()
        job["result"] = base_result
        _write_job(job_path, job)
        _write_json(result_path, base_result)
        return 8

    if long_tok.resolve() == token_shorts.resolve():
        warnings.append("token_path_equals_long_token_unexpected")

    conf = confirm_shorts_channel(token_path=token_shorts, client_secrets=client_sec)
    base_result["confirm_shorts_channel_ok"] = conf.confirm_shorts_channel_ok
    base_result["youtube_channel_title"] = conf.youtube_channel_title
    base_result["youtube_channel_id"] = conf.youtube_channel_id
    if not conf.ok:
        base_result["status"] = "blocked"
        base_result["block_reason"] = conf.block_reason or "shorts_channel_confirmation_failed"
        base_result["upload_attempted"] = False
        job["status"] = "blocked"
        job["progress"] = 100
        job["finished_at"] = _utc()
        job["result"] = base_result
        _write_job(job_path, job)
        _write_json(result_path, base_result)
        return 9

    try:
        out_qh_guard = triple_chunk_sha256(out_video)
    except OSError:
        out_qh_guard = ""

    if out_qh_guard and _ledger_output_quick_hash_uploaded(ledger_path, out_qh_guard):
        base_result["status"] = "blocked"
        base_result["block_reason"] = "shorts_output_quick_hash_already_uploaded"
        base_result["upload_attempted"] = False
        job["status"] = "blocked"
        job["progress"] = 100
        job["finished_at"] = _utc()
        job["result"] = base_result
        _write_job(job_path, job)
        _write_json(result_path, base_result)
        return 20

    if _ledger_output_already_uploaded(ledger_path, out_video):
        base_result["status"] = "blocked"
        base_result["block_reason"] = "output_video_path_already_uploaded"
        base_result["upload_attempted"] = False
        job["status"] = "blocked"
        job["progress"] = 100
        job["finished_at"] = _utc()
        job["result"] = base_result
        _write_job(job_path, job)
        _write_json(result_path, base_result)
        return 18

    upload_target = out_video.resolve()
    real_sound_rep: dict[str, Any] | None = None
    if do_upload and not bool(getattr(args, "no_real_sound_gate", False)):
        try:
            from real_sound_cleanup_gate import run_real_sound_cleanup

            upload_target, real_sound_rep = run_real_sound_cleanup(
                out_video,
                "short",
                mode="auto",
                dry_run=False,
                keep_intermediates=False,
                job_id=job_id,
            )
            fv = Path(str((real_sound_rep or {}).get("final_video") or ""))
            if fv.is_file() and (real_sound_rep or {}).get("publish_safe", True) is not False:
                upload_target = fv.resolve()
            else:
                upload_target = out_video.resolve()
                if real_sound_rep and (
                    bool(real_sound_rep.get("fallback_to_original"))
                    or real_sound_rep.get("publish_safe") is False
                    or str(real_sound_rep.get("status") or "") == "error"
                ):
                    base_result["real_sound_gate_fallback_original"] = True
            if real_sound_rep:
                for rw in real_sound_rep.get("warnings") or []:
                    warnings.append(f"real_sound_gate:{rw}")
                for re_ in real_sound_rep.get("errors") or []:
                    warnings.append(f"real_sound_gate_error:{re_}")
                rp_txt = real_sound_rep.get("report_path")
                if rp_txt:
                    try:
                        shutil.copy(Path(str(rp_txt)), pack_dir / "real_sound_quality_report.json")
                    except OSError as exc:
                        warnings.append(f"real_sound_report_copy_failed:{exc!r}")
            base_result["real_sound_gate"] = {
                "status": (real_sound_rep or {}).get("status"),
                "mode_used": (real_sound_rep or {}).get("mode_used"),
                "publish_safe": (real_sound_rep or {}).get("publish_safe"),
                "report_path": (real_sound_rep or {}).get("report_path"),
                "upload_target": str(upload_target),
                "video_copy_used": (real_sound_rep or {}).get("video_copy_used"),
                "video_copy_reason": (real_sound_rep or {}).get("video_copy_reason"),
                "cfr_reencoded": (real_sound_rep or {}).get("cfr_reencoded"),
                "fallback_to_original": (real_sound_rep or {}).get("fallback_to_original"),
            }
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"real_sound_gate_exception:{exc!r}")
            upload_target = out_video.resolve()
            real_sound_rep = None

    try:
        daf_dir = _SCRIPTS_ROOT / "davinci_audio_finish"
        if daf_dir.is_dir() and str(daf_dir) not in sys.path:
            sys.path.insert(0, str(daf_dir))
        from upload_path import resolve_upload_video_path  # noqa: WPS433

        upload_target, daf_meta = resolve_upload_video_path(
            Path(upload_target), content_kind="short"
        )
        if daf_meta.get("davinci_audio_finish_used"):
            warnings.append(
                f"davinci_audio_finish:{daf_meta.get('davinci_audio_finish_reason', 'used')}"
            )
            base_result["davinci_audio_finish"] = daf_meta
        daf_block = daf_meta.get("davinci_audio_finish_block_reason")
        if daf_block:
            base_result["status"] = "blocked"
            base_result["block_reason"] = str(daf_block)
            base_result["upload_attempted"] = False
            base_result["warnings"] = warnings
            job["status"] = "blocked"
            job["progress"] = 100
            job["finished_at"] = _utc()
            job["result"] = base_result
            _write_job(job_path, job)
            _write_json(result_path, base_result)
            return 20
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"davinci_audio_finish_exception:{exc!r}")

    guard_ctx = assert_shorts_upload_context(Path(upload_target), token_shorts)
    if guard_ctx:
        base_result["status"] = "blocked"
        base_result["block_reason"] = str(guard_ctx.get("block_reason") or "channel_guard_failed")
        base_result["upload_attempted"] = False
        base_result["warnings"] = warnings + [json.dumps(guard_ctx, ensure_ascii=False)]
        job["status"] = "blocked"
        job["progress"] = 100
        job["finished_at"] = _utc()
        job["result"] = base_result
        _write_job(job_path, job)
        _write_json(result_path, base_result)
        return 19

    mini = pack_dir / "youtube_package"
    mini.mkdir(parents=True, exist_ok=True)
    (mini / "selected_video_path.txt").write_text(str(Path(upload_target).resolve()) + "\n", encoding="utf-8")

    jr_upload = {**base_result, "output_video": str(upload_target)}
    try:
        sm_u = _shorts_write_youtube_metadata_pack(
            pack_dir,
            Path(upload_target),
            jr_upload,
            use_ai_metadata=use_ai_md,
        )
        if isinstance(sm_u, dict) and sm_u.get("metadata_generated"):
            base_result["generated_title"] = str(sm_u.get("title") or base_result.get("generated_title", ""))
            base_result["generated_description"] = str(
                sm_u.get("description") or base_result.get("generated_description", "")
            )
            if isinstance(sm_u.get("hashtags"), list):
                base_result["generated_hashtags"] = sm_u["hashtags"]
            if isinstance(sm_u.get("tags"), list):
                base_result["generated_tags"] = sm_u["tags"]
            base_result["metadata_path"] = str(pack_dir / "youtube_metadata.json")
            base_result["metadata_generator_version"] = str(
                sm_u.get("metadata_generator_version") or base_result.get("metadata_generator_version", "")
            )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"shorts_upload_metadata_refresh_exception:{exc!r}")

    meta_path_pack = pack_dir / "youtube_metadata.json"
    if meta_path_pack.is_file():
        try:
            shutil.copy2(meta_path_pack, mini / "youtube_metadata.json")
            from metadata_generator import load_existing_metadata, sync_legacy_package_files

            sync_legacy_package_files(mini, load_existing_metadata(mini / "youtube_metadata.json"))
        except OSError as exc:
            warnings.append(f"shorts_mini_metadata_failed:{exc!r}")
    else:
        warnings.append("shorts_pack_youtube_metadata_missing_before_upload")

    base_result["upload_attempted"] = True
    base_result["warnings"] = warnings
    job["result"] = base_result
    _write_job(job_path, job)

    res = upload_from_package_directory(
        mini,
        privacy=str(args.privacy_status),
        allow_public=False,
        title=None,
        description=None,
        tags_str=None,
        dry_run=False,
        token_path=token_shorts,
        client_secrets=client_sec,
        notify_subscribers=False,
        force_reupload=False,
        allow_test_assets=False,
        use_ai_metadata=use_ai_md,
        agent_upload=agent_pu,
        review_queue=bool(getattr(args, "review_queue", True)),
        force_private=agent_pu,
        channel_type="short",
        channel_guard_status="passed_shorts_worker",
        dedupe_status="passed_shorts_duplicate_guards",
        dedupe_key=str(out_qh_guard or ""),
        automation_job_id=job_id,
    )

    up_json = {
        "ok": bool(res.ok),
        "status": res.status,
        "video_id": res.video_id,
        "error": res.error,
        "title": res.title,
        "privacy": res.privacy,
        "privacy_used": res.privacy_used or res.privacy,
        "metadata_used": res.metadata_used,
        "metadata_path": res.metadata_path,
        "title_used": res.title_used or res.title,
        "description_used": res.description_used,
        "tags_used": res.tags_used,
        "metadata_generator_version": res.metadata_generator_version,
        "channel_guard_status": res.channel_guard_status,
        "dedupe_status": res.dedupe_status,
        "review_queue_path": res.review_queue_path,
        "review_queue_md_path": res.review_queue_md_path,
        "agent_uploaded": res.agent_uploaded,
        "human_review_required": res.human_review_required,
        "forced_private": res.forced_private,
    }
    try:
        upload_result_path.write_text(json.dumps(up_json, indent=2), encoding="utf-8")
    except OSError:
        pass

    base_result["uploaded"] = bool(res.ok and res.status == "success")
    if res.video_id:
        base_result["youtube_video_id"] = str(res.video_id)
        base_result["youtube_url"] = f"https://www.youtube.com/watch?v={res.video_id}"
    if base_result["uploaded"]:
        base_result["privacy_status"] = str(res.privacy_used or res.privacy or args.privacy_status)
        base_result["review_queue_added"] = bool(res.review_queue_path)
        base_result["review_status"] = "pending"
        base_result["requires_review_before_public"] = True
        if bool(getattr(args, "review_queue", True)) and not res.review_queue_path:
            warnings.append("upload_success_missing_review_queue_entry")
    if not base_result["uploaded"]:
        base_result["status"] = "upload_failed"
        base_result["review_status"] = "upload_failed"
        base_result["review_queue_added"] = False
        base_result["block_reason"] = res.error or res.status or "upload_failed"
        job["status"] = "failed"
        _ledger_patch_job_by_id(
            ledger_path,
            job_id,
            {
                "state": "upload_failed",
                "dedupe_active": False,
                "uploaded": False,
                "youtube_video_id": "",
            },
            warnings,
        )
    else:
        base_result["status"] = "uploaded"
        job["status"] = "completed"
        up_lease = (datetime.now(timezone.utc) + timedelta(seconds=SHORTS_RESERVE_LEASE_SEC)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        patch_uploaded = {
            "state": "uploaded",
            "uploaded": True,
            "youtube_video_id": base_result.get("youtube_video_id") or "",
            "output_quick_hash": out_qh_guard or str(triple_chunk_sha256(out_video)),
            "uploaded_at": _utc(),
            "lease_until": up_lease,
        }
        if agent_pu:
            patch_uploaded["review_status"] = "pending"
            patch_uploaded["privacy"] = "private"
        _ledger_patch_job_by_id(
            ledger_path,
            job_id,
            patch_uploaded,
            warnings,
        )
    job["progress"] = 100
    job["finished_at"] = _utc()
    job["result"] = base_result
    _write_job(job_path, job)
    _write_json(result_path, base_result)

    try:
        (logs_root / f"{job_id}.log").write_text("\n".join(log_lines), encoding="utf-8")
    except OSError:
        pass
    return 0 if base_result["status"] == "completed" else 10


if __name__ == "__main__":
    raise SystemExit(main())
