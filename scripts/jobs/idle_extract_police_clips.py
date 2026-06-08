#!/usr/bin/env python3
"""Idle-time extract of police moments into ``素材/police_clips`` for Shorts.

**Detection (v3):** red+blue emergency lights + flash rhythm (high-frequency alternation)
+ siren/alarm audio in the same time window. Audio-only is never used alone.

Originals are never modified.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent.parent
_REPO = _SCRIPTS.parent
_SRC = _REPO / "src"
for p in (_SCRIPTS, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from utils.shorts_paths import shorts_materials_dir, shorts_police_clips_dir  # noqa: E402
except Exception:

    def shorts_materials_dir(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE/素材")

    def shorts_police_clips_dir(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE/素材/police_clips")


VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv"}
_POLICE_PATH_TOKENS = (
    "police",
    "nypd",
    "patrol",
    "cruiser",
    "cop_car",
    "copcar",
    "emergency",
    "siren",
    "警车",
    "警察",
    "_pd_",
    "law_enforcement",
)
_MANIFEST_NAME = ".police_extract_manifest.json"
_REJECTED_DIRNAME = "_rejected_false_positive"

# Visual scan: sample every N seconds (full file up to cap).
VISUAL_SAMPLE_SEC = 2.0  # faster sampling for high-frequency red/blue flash
MAX_SCAN_SEC = 3600.0
# Min emergency-light pixel score (see ``_score_frame_emergency_lights``).
VISUAL_SCORE_MIN = 0.018
VISUAL_PEAK_MIN = 0.028
VISUAL_MIN_RUN_SAMPLES = 2  # 2 * 2s = 4s minimum presence
# Flash rhythm: min on/off transitions per second (red-blue strobing).
FLASH_TRANSITIONS_PER_SEC_MIN = 0.35
# Audio siren: min score from band-limited RMS wobble (see ``_score_audio_siren``).
SIREN_SCORE_MIN = 0.42


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ffmpeg() -> str:
    return os.environ.get("FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg"


def _ffprobe() -> str:
    return os.environ.get("FFPROBE_BIN", "ffprobe").strip() or "ffprobe"


def _source_key(path: Path) -> str:
    try:
        st = path.stat()
        return f"{path.resolve()}|{st.st_size}|{int(st.st_mtime_ns)}"
    except OSError:
        return str(path.resolve())


def _path_police_hint(path: Path) -> bool:
    blob = f"{path.name}|{path.parent.name}|{path.as_posix()}".lower()
    return any(t in blob for t in _POLICE_PATH_TOKENS)


def _duration_sec(path: Path) -> float:
    try:
        r = subprocess.run(
            [
                _ffprobe(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return max(0.0, float((r.stdout or "").strip() or 0.0))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def _score_frame_emergency_lights(jpeg: Path) -> tuple[float, dict[str, float]]:
    """Score 0..~0.15 from simultaneous red+blue emergency-light pixels (upper frame)."""
    try:
        from PIL import Image
    except ImportError:
        return 0.0, {"error": "pil_missing"}
    try:
        img = Image.open(jpeg).convert("RGB")
    except OSError:
        return 0.0, {"error": "read_failed"}
    w, h = img.size
    if w < 32 or h < 32:
        return 0.0, {}
    upper = int(h * 0.65)
    red_n = blue_n = total = 0
    step = 4
    for y in range(0, upper, step):
        for x in range(0, w, step):
            r, g, b = img.getpixel((x, y))
            total += 1
            if r >= 168 and g <= 98 and b <= 98 and (r - g) >= 45 and (r - b) >= 45:
                red_n += 1
            if b >= 168 and r <= 98 and g <= 145 and (b - g) >= 28 and (b - r) >= 28:
                blue_n += 1
    if total < 50:
        return 0.0, {}
    rf = red_n / total
    bf = blue_n / total
    if rf < 0.001 or bf < 0.001:
        return 0.0, {"red_frac": rf, "blue_frac": bf}
    # Both colors required (light bar / grille flash); scale for thresholding.
    score = (rf * bf) ** 0.5 * 500.0
    return score, {"red_frac": rf, "blue_frac": bf, "score": score}


def _extract_sample_frames(src: Path, cache_dir: Path, *, scan_sec: float, step: float) -> list[tuple[float, Path]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for old in cache_dir.glob("f_*.jpg"):
        try:
            old.unlink()
        except OSError:
            pass
    fps = 1.0 / max(1.0, step)
    pattern = str(cache_dir / "f_%06d.jpg")
    try:
        r = subprocess.run(
            [
                _ffmpeg(),
                "-hide_banner",
                "-nostdin",
                "-y",
                "-t",
                str(max(1.0, scan_sec)),
                "-i",
                str(src),
                "-an",
                "-vf",
                f"fps={fps},scale=360:-1",
                "-q:v",
                "8",
                pattern,
            ],
            capture_output=True,
            text=True,
            timeout=max(300, int(scan_sec * 0.35) + 120),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    out: list[tuple[float, Path]] = []
    frames = sorted(cache_dir.glob("f_*.jpg"))
    for i, fp in enumerate(frames):
        out.append((i * step, fp))
    return out


def _flash_transition_rate(scored: list[tuple[float, float]], t0: float, t1: float) -> float:
    """Transitions/sec of emergency-light score crossing half-threshold (strobe proxy)."""
    window = [(t, sc) for t, sc in scored if t0 <= t <= t1]
    if len(window) < 3:
        return 0.0
    thr = max(VISUAL_SCORE_MIN * 0.55, 0.01)
    hits = [sc >= thr for _, sc in window]
    trans = sum(1 for i in range(1, len(hits)) if hits[i] != hits[i - 1])
    span = max(window[-1][0] - window[0][0], VISUAL_SAMPLE_SEC)
    return trans / span


def _score_audio_siren(path: Path, t0: float, t1: float) -> float:
    """
    Score 0..1 from band-limited RMS wobble (police siren / alarm wail proxy).
    Parses ffmpeg astats stderr blocks.
    """
    length = max(1.0, min(t1 - t0, 24.0))
    start = max(0.0, t0)
    try:
        r = subprocess.run(
            [
                _ffmpeg(),
                "-hide_banner",
                "-nostdin",
                "-ss",
                str(start),
                "-t",
                str(length),
                "-i",
                str(path),
                "-vn",
                "-af",
                "highpass=f=450,lowpass=f=3800,astats=metadata=1:reset=0.35",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    text = (r.stderr or "") + (r.stdout or "")
    rms_vals: list[float] = []
    for line in text.splitlines():
        if "RMS level dB" not in line and "RMS peak dB" not in line:
            continue
        m = re.search(r"-?\d+(?:\.\d+)?", line.split(":")[-1])
        if m:
            try:
                rms_vals.append(float(m.group(0)))
            except ValueError:
                pass
    if len(rms_vals) < 4:
        return 0.0
    mean = sum(rms_vals) / len(rms_vals)
    var = sum((x - mean) ** 2 for x in rms_vals) / len(rms_vals)
    std = var ** 0.5
    # Loud + oscillating (siren) vs steady traffic rumble.
    loud = max(0.0, min(1.0, (mean + 42.0) / 18.0))
    wobble = max(0.0, min(1.0, std / 7.0))
    return (loud * 0.45 + wobble * 0.55)


def _detect_police_segments_visual(
    path: Path,
    dur: float,
    cache_root: Path,
    *,
    log: list[str],
    require_siren: bool = True,
    require_flash_rhythm: bool = True,
) -> list[tuple[float, float, float, float, float]]:
    """Return (start, end, peak_visual, flash_rate, siren_score)."""
    scan = min(dur, MAX_SCAN_SEC)
    cache = cache_root / ".police_scan_cache" / path.stem
    samples = _extract_sample_frames(path, cache, scan_sec=scan, step=VISUAL_SAMPLE_SEC)
    if not samples:
        log.append(f"visual_scan_no_frames:{path.name}")
        return []

    scored: list[tuple[float, float]] = []
    for t, fp in samples:
        sc, _detail = _score_frame_emergency_lights(fp)
        scored.append((t, sc))

    active = [(t, sc) for t, sc in scored if sc >= VISUAL_SCORE_MIN]
    if not active:
        peak = max((sc for _, sc in scored), default=0.0)
        log.append(f"visual_no_hits:{path.name} peak={peak:.4f}")
        return []

    groups: list[list[tuple[float, float]]] = [[active[0]]]
    gap = VISUAL_SAMPLE_SEC * 2.5
    for pair in active[1:]:
        if pair[0] - groups[-1][-1][0] <= gap:
            groups[-1].append(pair)
        else:
            groups.append([pair])

    ranges: list[tuple[float, float, float, float, float]] = []
    for gs in groups:
        if len(gs) < VISUAL_MIN_RUN_SAMPLES:
            continue
        peak = max(sc for _, sc in gs)
        if peak < VISUAL_PEAK_MIN:
            continue
        a, b = gs[0][0], gs[-1][0] + VISUAL_SAMPLE_SEC
        flash_rate = _flash_transition_rate(scored, a, b)
        if require_flash_rhythm and flash_rate < FLASH_TRANSITIONS_PER_SEC_MIN:
            log.append(f"flash_weak:{path.name} {a:.0f}-{b:.0f}s rate={flash_rate:.2f}")
            continue
        siren = _score_audio_siren(path, a, b)
        if require_siren and siren < SIREN_SCORE_MIN:
            log.append(f"siren_weak:{path.name} {a:.0f}-{b:.0f}s siren={siren:.2f}")
            continue
        ranges.append((a, b, peak, flash_rate, siren))

    merged: list[tuple[float, float, float, float, float]] = []
    for a, b, pk, fr, sr in sorted(ranges, key=lambda x: x[0]):
        if merged and a <= merged[-1][1] + 8.0:
            la, lb, lp, lfr, lsr = merged[-1]
            merged[-1] = (la, max(lb, b), max(lp, pk), max(lfr, fr), max(lsr, sr))
        else:
            merged.append((a, b, pk, fr, sr))
    return merged


def _clip_window(start: float, end: float, dur: float, *, target: float, min_len: float, max_len: float) -> tuple[float, float]:
    length = end - start
    if length < min_len:
        pad = (min_len - length) * 0.5
        start = max(0.0, start - pad)
        end = min(dur, end + pad)
        length = end - start
    if length > max_len:
        mid = (start + end) * 0.5
        half = max_len * 0.5
        start = max(0.0, mid - half)
        end = min(dur, start + max_len)
    if length < target * 0.85:
        extra = (target - length) * 0.5
        start = max(0.0, start - extra)
        end = min(dur, end + extra)
    return start, min(dur, end)


def _encode_clip(src: Path, dst: Path, start: float, length: float, *, log: list[str]) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        str(max(0.0, start)),
        "-t",
        str(max(1.0, length)),
        "-i",
        str(src),
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-r",
        "30",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.append(f"encode_failed:{dst.name}:{exc!r}")
        return False
    if r.returncode != 0:
        log.append(f"encode_rc_{r.returncode}:{dst.name}")
        return False
    return dst.is_file() and dst.stat().st_size > 50_000


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 2, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            ent = data.get("entries")
            if isinstance(ent, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 2, "entries": {}}


def _save_manifest(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _iter_source_videos(materials: Path, police_out: Path) -> list[Path]:
    out: list[Path] = []
    if not materials.is_dir():
        return out
    police_res = police_out.resolve()
    for p in materials.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXT:
            continue
        try:
            if police_res in p.resolve().parents:
                continue
        except OSError:
            if "police_clips" in p.parts:
                continue
        if p.name.startswith("._") or p.name.startswith("."):
            continue
        out.append(p)
    out.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
    return out


def _idle_ok(warnings: list[str]) -> bool:
    try:
        from agent_idle_asset_organizer import idle_ok  # noqa: WPS433

        return idle_ok(max_runtime_minutes=60, warnings=warnings)
    except Exception:
        return True


def quarantine_existing_clips(out_dir: Path, *, log: list[str] | None = None) -> dict[str, Any]:
    """Move v1 audio-only false positives out of the Shorts pool."""
    lg = log if log is not None else []
    reject_dir = out_dir / _REJECTED_DIRNAME
    reject_dir.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for fp in sorted(out_dir.glob("*.mp4")):
        dst = reject_dir / fp.name
        try:
            if dst.is_file():
                dst.unlink()
            shutil.move(str(fp), str(dst))
            moved.append(fp.name)
            lg.append(f"quarantine {fp.name}")
        except OSError as exc:
            lg.append(f"quarantine_failed:{fp.name}:{exc!r}")
    manifest_path = out_dir / _MANIFEST_NAME
    body = _load_manifest(manifest_path)
    entries = body.get("entries") if isinstance(body.get("entries"), dict) else {}
    cleared = 0
    for k, v in list(entries.items()):
        if isinstance(v, dict) and v.get("clips"):
            v["done"] = False
            v["needs_rescan_v2_visual"] = True
            v.pop("clips", None)
            cleared += 1
    body["version"] = 2
    body["quarantine_at"] = _utc()
    body["entries"] = entries
    _save_manifest(manifest_path, body)
    return {"moved": moved, "manifest_keys_reset": cleared, "reject_dir": str(reject_dir)}


def run_once(
    *,
    max_sources: int,
    max_clips: int,
    clip_seconds: float,
    dry_run: bool,
    force: bool,
    quarantine_first: bool,
    visual_only: bool = False,
    scan_all: bool = False,
    scan_only: bool = False,
) -> dict[str, Any]:
    warnings: list[str] = []
    log: list[str] = []
    materials = shorts_materials_dir(verbose=False)
    out_dir = shorts_police_clips_dir(verbose=False)
    manifest_path = out_dir / _MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    entries: dict[str, Any] = manifest.get("entries") if isinstance(manifest.get("entries"), dict) else {}

    quarantine_report: dict[str, Any] | None = None
    if quarantine_first:
        out_dir.mkdir(parents=True, exist_ok=True)
        quarantine_report = quarantine_existing_clips(out_dir, log=log)

    if not _idle_ok(warnings) and not force:
        return {
            "status": "skipped",
            "reason": "idle_conditions_not_met",
            "warnings": warnings,
            "materials_dir": str(materials),
            "output_dir": str(out_dir),
            "quarantine": quarantine_report,
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    sources = _iter_source_videos(materials, out_dir)
    total_sources = len(sources)
    extracted = 0
    scanned = 0
    hits_found = 0
    unlimited_clips = max_clips <= 0
    t0 = time.monotonic()

    log.append(f"source_pool={materials} total_videos={total_sources} scan_all={scan_all}")

    for src in sources:
        if not scan_all and scanned >= max_sources:
            break
        if not unlimited_clips and extracted >= max_clips:
            break
        scanned += 1
        if scanned % 25 == 0 or scanned == 1:
            log.append(f"progress {scanned}/{total_sources} clips={extracted} hits={hits_found}")
        key = _source_key(src)
        prev = entries.get(key, {}) if isinstance(entries.get(key), dict) else {}
        if (
            not force
            and prev.get("done")
            and prev.get("detector") == "visual_audio_v3"
            and not prev.get("needs_rescan_v2_visual")
        ):
            continue
        dur = _duration_sec(src)
        if dur < 10.0:
            entries[key] = {"done": True, "skipped": "too_short", "at": _utc()}
            continue
        hint = _path_police_hint(src)
        visual = _detect_police_segments_visual(
            src, dur, out_dir, log=log,
            require_siren=not visual_only,
            require_flash_rhythm=not visual_only,
        )
        if not visual:
            entries[key] = {
                "done": True,
                "skipped": "no_police_flash_siren",
                "path_hint": hint,
                "detector": "visual_audio_v3",
                "at": _utc(),
            }
            continue

        hits_found += 1
        hit_rows = [
            {
                "start": a,
                "end": b,
                "peak": peak,
                "flash_rate": flash_rate,
                "siren_score": siren_sc,
            }
            for a, b, peak, flash_rate, siren_sc in visual[:5]
        ]
        if scan_only:
            entries[key] = {
                "done": True,
                "detector": "visual_audio_v3",
                "path_hint": hint,
                "hits": hit_rows,
                "at": _utc(),
            }
            log.append(f"hit {src.name} segments={len(hit_rows)}")
            continue

        for a, b, peak, flash_rate, siren_sc in visual[:3]:
            if not unlimited_clips and extracted >= max_clips:
                break
            start, end = _clip_window(a, b, dur, target=clip_seconds, min_len=12.0, max_len=min(45.0, clip_seconds + 10))
            length = end - start
            tag = f"{src.stem}_police_{int(start * 1000)}_{int(end * 1000)}"
            dst = out_dir / f"{tag}.mp4"
            if dst.is_file() and not force:
                extracted += 1
                continue
            log.append(
                f"plan {src.name} {start:.1f}-{end:.1f}s "
                f"peak={peak:.3f} flash={flash_rate:.2f}/s siren={siren_sc:.2f} -> {dst.name}"
            )
            if dry_run:
                extracted += 1
                continue
            if _encode_clip(src, dst, start, length, log=log):
                extracted += 1
                entries[key] = {
                    "done": True,
                    "detector": "visual_audio_v3",
                    "peak_score": peak,
                    "flash_rate": flash_rate,
                    "siren_score": siren_sc,
                    "clips": entries.get(key, {}).get("clips", []) + [str(dst)],
                    "at": _utc(),
                }
            else:
                warnings.append(f"encode_failed:{src.name}")

    if not dry_run:
        manifest["version"] = 2
        manifest["entries"] = entries
        manifest["updated_at"] = _utc()
        _save_manifest(manifest_path, manifest)

    return {
        "status": "ok",
        "dry_run": dry_run,
        "scan_only": scan_only,
        "scan_all": scan_all,
        "detector": "visual_audio_v3",
        "materials_dir": str(materials),
        "output_dir": str(out_dir),
        "sources_total": total_sources,
        "sources_scanned": scanned,
        "sources_with_hits": hits_found,
        "clips_extracted": extracted,
        "elapsed_sec": round(time.monotonic() - t0, 2),
        "warnings": warnings,
        "log": log[-60:],
        "manifest": str(manifest_path),
        "quarantine": quarantine_report,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Idle extract police clips (visual emergency-light detection).")
    ap.add_argument("--max-sources", type=int, default=8, help="Max source videos per run (ignored with --all).")
    ap.add_argument(
        "--max-clips",
        type=int,
        default=6,
        help="Max clips to encode per run; 0 = unlimited (use with --all).",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="Scan every video under 素材 (not only the newest N).",
    )
    ap.add_argument(
        "--scan-only",
        action="store_true",
        help="Detect and log hits in manifest only; do not encode mp4 clips.",
    )
    ap.add_argument(
        "--visual-only",
        action="store_true",
        help="Skip siren/flash-rhythm gates (legacy v2 behaviour).",
    )
    ap.add_argument("--clip-seconds", type=float, default=30.0, help="Target clip length (12–45s window).")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="Ignore idle gate and manifest done flags.")
    ap.add_argument(
        "--quarantine-existing",
        action="store_true",
        help="Before run: move police_clips/*.mp4 to _rejected_false_positive and reset manifest.",
    )
    ap.add_argument(
        "--quarantine-only",
        action="store_true",
        help="Only quarantine existing clips; do not scan sources.",
    )
    args = ap.parse_args()
    if args.quarantine_only:
        body = quarantine_existing_clips(shorts_police_clips_dir(verbose=False))
        print(json.dumps({"status": "ok", "quarantine": body}, indent=2, ensure_ascii=False))
        return 0
    max_clips = int(args.max_clips)
    body = run_once(
        max_sources=max(1, int(args.max_sources)),
        max_clips=max_clips,
        clip_seconds=float(args.clip_seconds),
        dry_run=bool(args.dry_run),
        force=bool(args.force),
        quarantine_first=bool(args.quarantine_existing),
        visual_only=bool(args.visual_only),
        scan_all=bool(args.all),
        scan_only=bool(args.scan_only),
    )
    print(json.dumps(body, indent=2, ensure_ascii=False))
    return 0 if body.get("status") in ("ok", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
