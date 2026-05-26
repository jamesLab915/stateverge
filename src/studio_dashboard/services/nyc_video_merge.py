"""
NYC/video-style picker + ffmpeg concat + optional loop-to-duration.

Scan root defaults to ``NYC/video`` under ``/Volumes/StateVerge`` but may be any
subdirectory on that volume (paths validated by ``stateverge_scan``).
"""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .stateverge_scan import (
    STATEVERGE_VOLUME,
    is_under_or_equal,
    resolve_file_under_scan,
    resolve_scan_directory,
    volume_mounted,
)

DEFAULT_VIDEO_ROOT_REL = "NYC/video"
NYC_VIDEO_ROOT = STATEVERGE_VOLUME / DEFAULT_VIDEO_ROOT_REL

MERGED_SUBDIR = "merged"
RAW_FILE_BUCKET_DIR = "FILE"
THUMB_CACHE_REL = ".cache/nyc_merge_thumbs"

VIDEO_SUFFIXES: frozenset[str] = frozenset(
    {
        ".mp4",
        ".mov",
        ".m4v",
        ".mkv",
        ".webm",
        ".avi",
        ".wmv",
        ".mpg",
        ".mpeg",
        ".mts",
        ".m2ts",
        ".ts",
        ".3gp",
        ".flv",
    }
)

MAX_LIST_FILES = 800


def mount_ok() -> bool:
    return volume_mounted()


def thumb_cache_dir(repo_root: Path) -> Path:
    d = repo_root / THUMB_CACHE_REL
    d.mkdir(parents=True, exist_ok=True)
    return d


def _thumb_name(scan_key: str, rel_posix: str, mtime_ns: int) -> str:
    h = hashlib.sha256(f"{scan_key}|{rel_posix}|{mtime_ns}".encode()).hexdigest()[:28]
    return f"{h}.jpg"


def resolve_under_root(rel: str, *, scan_root: Path) -> Path:
    """Raise ValueError if path escapes scan_root."""
    return resolve_file_under_scan(scan_root, rel)


def resolve_immediate_subdir(scan_root: Path, segment: str) -> Path:
    """Single path segment under ``scan_root``; must exist as a directory."""
    raw = (segment or "").strip().strip("/\\")
    if not raw or raw == "..":
        raise ValueError("无效的子文件夹名")
    parts = Path(raw.replace("\\", "/")).parts
    if len(parts) != 1:
        raise ValueError(
            "下一级拼接仅支持扫描目录的直接子文件夹（单层名称，不含 /）"
        )
    name = parts[0]
    if name == ".." or name == ".":
        raise ValueError("无效的子文件夹名")
    p = resolve_under_root(name, scan_root=scan_root)
    if not p.is_dir():
        raise ValueError(f"不是文件夹或不存在: {name}")
    return p


def sanitize_merge_slug(name: str, *, max_len: int = 48) -> str:
    import re

    n = name.strip()
    if not n:
        return "folder"
    n = re.sub(r'[/\\\x00<>:"\|?*]+', "_", n)
    n = re.sub(r"\s+", " ", n).strip()
    if not n:
        return "folder"
    return n[:max_len]


def list_immediate_subdirs(scan_root: Path) -> list[dict]:
    """Immediate child directories under ``scan_root`` (for next-level merge UI)."""
    out: list[dict] = []
    if not scan_root.is_dir():
        return out
    root = scan_root.resolve()
    apply_file_skips = is_under_or_equal(
        root, (STATEVERGE_VOLUME / "NYC/video").resolve()
    )
    for p in sorted(scan_root.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_dir():
            continue
        name = p.name
        if name.startswith("."):
            continue
        if name == MERGED_SUBDIR:
            continue
        if apply_file_skips and name == RAW_FILE_BUCKET_DIR:
            continue
        out.append({"rel": name, "name": name})
    return out


def list_video_files(
    *,
    scan_root: Path,
    recursive: bool,
    exclude_merged_outputs: bool,
) -> list[dict]:
    """Stat-only listing for UI (fast).

    When scan_root lies under ``NYC/video``, skips ``merged/``, ``FILE/``, and
    filenames starting with ``FILE``.
    """
    out: list[dict] = []
    if not scan_root.is_dir():
        return out

    root = scan_root.resolve()
    apply_file_skips = is_under_or_equal(
        root, (STATEVERGE_VOLUME / "NYC/video").resolve()
    )

    def skip_dir(name: str) -> bool:
        if name.startswith("."):
            return True
        if exclude_merged_outputs and name == MERGED_SUBDIR:
            return True
        if apply_file_skips and name == RAW_FILE_BUCKET_DIR:
            return True
        return False

    def maybe_add(p: Path) -> None:
        if not p.is_file():
            return
        if p.name.startswith("._"):
            return
        if apply_file_skips and p.name.startswith("FILE"):
            return
        suf = p.suffix.lower()
        if suf not in VIDEO_SUFFIXES:
            return
        try:
            rel = p.relative_to(root)
        except ValueError:
            return
        rel_posix = rel.as_posix()
        try:
            st = p.stat()
        except OSError:
            return
        mtime_iso = datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc
        ).isoformat()
        birth_ts = getattr(st, "st_birthtime", None)
        birth_iso: str | None = None
        if isinstance(birth_ts, (int, float)) and birth_ts > 0:
            birth_iso = datetime.fromtimestamp(
                birth_ts, tz=timezone.utc
            ).isoformat()
        out.append(
            {
                "rel": rel_posix,
                "name": p.name,
                "size_bytes": st.st_size,
                "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
                "mtime_iso": mtime_iso,
                "birthtime_iso": birth_iso,
            }
        )

    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            dn = Path(dirpath).name
            if Path(dirpath) != root and skip_dir(dn):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if not skip_dir(d)]
            for fn in filenames:
                maybe_add(Path(dirpath) / fn)
                if len(out) >= MAX_LIST_FILES:
                    break
            if len(out) >= MAX_LIST_FILES:
                break
    else:
        for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            if p.is_dir():
                continue
            maybe_add(p)

    out.sort(key=lambda x: (-x["mtime_ns"], x["rel"].lower()))
    return out[:MAX_LIST_FILES]


def probe_duration_sec(path: Path, *, timeout_sec: float = 120.0) -> float | None:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        r = subprocess.run(
            [
                ffprobe,
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
            timeout=timeout_sec,
        )
        if r.returncode != 0:
            return None
        v = float((r.stdout or "").strip())
        if not math.isfinite(v) or v < 0:
            return None
        return v
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return None


def ffprobe_has_audio(path: Path, *, timeout_sec: float = 90.0) -> bool | None:
    """Return True if at least one audio stream exists; False if none; None if probe failed."""
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        r = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = (r.stdout or "").strip()
    if r.returncode == 0:
        return bool(out)
    combined = ((r.stderr or "") + "\n" + (r.stdout or "")).lower()
    if "matches no streams" in combined or "stream specifier" in combined:
        return False
    return None


def concat_audio_plan(paths: list[Path]) -> tuple[str, str | None]:
    """How to mux audio when concatenating.

    Returns ``(mode, hint)``. ``mode`` is ``normal`` (keep audio), ``video_only``
    (omit audio — all inputs silent), ``mixed_strip`` (mixed silent / non-silent —
    omit all audio so concat succeeds), or ``unknown`` (could not classify).
    ``hint`` is only used for documentation in mixed case (caller shows as warning).
    """
    flags = [ffprobe_has_audio(p) for p in paths]
    if any(f is None for f in flags):
        return "unknown", None
    if all(f for f in flags):
        return "normal", None
    if not any(f for f in flags):
        return "video_only", None
    return (
        "mixed_strip",
        "所选素材音轨不一致（部分有声、部分无声），已自动改为仅拼接画面（无音轨）。",
    )


def probe_duration_for_rels(
    rel_paths: list[str],
    *,
    scan_root: Path,
) -> dict[str, float | None]:
    ordered_unique = list(dict.fromkeys(rel_paths))
    if not ordered_unique:
        return {}
    if not scan_root.is_dir():
        return {rk: None for rk in rel_paths}

    def probe_rel(rel_posix: str) -> tuple[str, float | None]:
        try:
            p = resolve_under_root(rel_posix, scan_root=scan_root)
        except ValueError:
            return rel_posix, None
        if not p.is_file():
            return rel_posix, None
        d = probe_duration_sec(p, timeout_sec=45.0)
        return rel_posix, d

    results: dict[str, float | None] = {}
    workers = max(1, min(10, len(ordered_unique)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(probe_rel, rk) for rk in ordered_unique]
        for fut in as_completed(futures):
            try:
                rk, val = fut.result()
                results[rk] = val
            except Exception:
                pass

    uniq_out = {rk: results.get(rk) for rk in ordered_unique}
    return {rk: uniq_out[rk] for rk in rel_paths}


def ensure_thumbnail(
    repo_root: Path,
    rel_posix: str,
    src: Path,
    *,
    scan_root: Path,
) -> Path | None:
    scan_key = scan_root.resolve().as_posix()
    try:
        st = src.stat()
    except OSError:
        return None
    mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
    cache = thumb_cache_dir(repo_root) / _thumb_name(scan_key, rel_posix, mtime_ns)
    if cache.is_file() and cache.stat().st_size > 100:
        return cache

    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    tmp = cache.with_suffix(".tmp.jpg")
    try:
        r = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "0.5",
                "-i",
                str(src),
                "-frames:v",
                "1",
                "-q:v",
                "4",
                "-y",
                str(tmp),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if r.returncode != 0 or not tmp.is_file():
            return None
        tmp.replace(cache)
        return cache
    except (subprocess.TimeoutExpired, OSError):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _write_concat_list(paths: list[Path], list_file: Path) -> None:
    lines: list[str] = []
    for p in paths:
        ap = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{ap}'")
    list_file.write_text("\n".join(lines), encoding="utf-8")


def ffmpeg_concat(
    paths: list[Path], out_mp4: Path, *, strip_audio: bool = False
) -> tuple[bool, str]:
    """Try stream copy, then libx264 (+aac unless strip_audio), then yuv420p encode."""
    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
    list_file = out_mp4.with_suffix(".concat.txt")
    try:
        _write_concat_list(paths, list_file)
        concat_in = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
        ]
        attempts: list[list[str]] = []
        if strip_audio:
            attempts.append(
                concat_in + ["-c:v", "copy", "-an", str(out_mp4)],
            )
        else:
            attempts.append(concat_in + ["-c", "copy", str(out_mp4)])

        enc_tail: list[str]
        if strip_audio:
            enc_tail = ["-an", "-movflags", "+faststart", str(out_mp4)]
        else:
            enc_tail = [
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(out_mp4),
            ]
        attempts.append(
            concat_in
            + [
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
            ]
            + enc_tail,
        )
        attempts.append(
            concat_in
            + [
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
            ]
            + enc_tail,
        )
        last_err = ""
        for cmd in attempts:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600 * 12)
            if r.returncode == 0 and out_mp4.is_file():
                return True, ""
            last_err = (r.stderr or r.stdout or "").strip()[-6000:]
        return False, last_err or "ffmpeg concat failed"
    finally:
        try:
            list_file.unlink(missing_ok=True)
        except OSError:
            pass


def ffmpeg_loop_to_duration(inp: Path, out_mp4: Path, target_sec: float) -> tuple[bool, str]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    for cmd in (
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-stream_loop",
            "-1",
            "-i",
            str(inp),
            "-t",
            str(target_sec),
            "-c",
            "copy",
            str(out_mp4),
        ],
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-stream_loop",
            "-1",
            "-i",
            str(inp),
            "-t",
            str(target_sec),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(out_mp4),
        ],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600 * 12)
        if r.returncode == 0 and out_mp4.is_file():
            return True, ""
    err = (r.stderr or r.stdout or "").strip()[-6000:]
    return False, err or "ffmpeg loop failed"


def merge_video_paths_ordered(
    resolved: list[Path],
    *,
    scan_root: Path,
    loop_enabled: bool,
    loop_target_seconds: float | None,
    filename_slug: str | None = None,
) -> dict:
    """Concatenate resolved files into ``scan_root/merged/``.

    ``filename_slug``: optional prefix for output basename (next-level batch).
    """
    if not volume_mounted():
        return {"ok": False, "error": "StateVerge 未挂载"}

    scan_r = scan_root.resolve()
    try:
        scan_r.relative_to(STATEVERGE_VOLUME.resolve())
    except ValueError:
        return {"ok": False, "error": "扫描目录须在 StateVerge 卷内"}

    if not scan_root.is_dir():
        return {"ok": False, "error": "扫描目录不存在"}

    if not resolved:
        return {"ok": False, "error": "没有可拼接的文件"}

    audio_mode, audio_note = concat_audio_plan(resolved)
    strip_audio = audio_mode in ("video_only", "mixed_strip")
    audio_warning = audio_note if audio_mode == "mixed_strip" else None

    merged_root = (scan_root / MERGED_SUBDIR).resolve()
    merged_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sufx = uuid.uuid4().hex[:6]
    slug_part = ""
    if filename_slug:
        s = sanitize_merge_slug(filename_slug)
        slug_part = f"{s}_"
    base_name = f"{slug_part}merge_{stamp}_{sufx}.mp4"
    final_out = merged_root / base_name
    loop_final = merged_root / f"{slug_part}merge_loop_{stamp}_{sufx}.mp4"

    cache_parent = Path.home() / "Library" / "Caches" / "studio_dashboard_nyc_merge"
    cache_parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="merge_", dir=str(cache_parent)))

    try:
        work_concat = tmp_dir / f"_work_concat_{uuid.uuid4().hex}.mp4"

        ok, err = ffmpeg_concat(resolved, work_concat, strip_audio=strip_audio)
        if not ok:
            return {"ok": False, "error": "拼接失败", "stderr": err}

        if loop_enabled:
            if loop_target_seconds is None or loop_target_seconds < 1:
                return {"ok": False, "error": "开启循环时请填写目标时长（秒）≥ 1"}
            loop_tmp = tmp_dir / f"_work_loop_{uuid.uuid4().hex}.mp4"
            ok2, err2 = ffmpeg_loop_to_duration(
                work_concat, loop_tmp, float(loop_target_seconds)
            )
            try:
                work_concat.unlink(missing_ok=True)
            except OSError:
                pass
            if not ok2:
                return {"ok": False, "error": "循环输出失败", "stderr": err2}
            shutil.move(str(loop_tmp), str(loop_final))
            dest_path = loop_final
        else:
            shutil.move(str(work_concat), str(final_out))
            dest_path = final_out
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    try:
        out_rel = dest_path.relative_to(scan_r).as_posix()
    except ValueError:
        out_rel = dest_path.name

    payload: dict = {
        "ok": True,
        "output_abs": str(dest_path),
        "output_rel": out_rel,
        "concat_inputs": len(resolved),
        "looped": bool(loop_enabled),
        "loop_seconds": loop_target_seconds if loop_enabled else None,
    }
    if audio_warning:
        payload["audio_warning"] = audio_warning
    return payload


def run_merge(
    rel_paths_ordered: list[str],
    *,
    scan_root: Path,
    loop_enabled: bool,
    loop_target_seconds: float | None,
) -> dict:
    """Writes under ``scan_root / merged /``."""
    resolved: list[Path] = []
    for rel in rel_paths_ordered:
        try:
            p = resolve_under_root(rel.strip(), scan_root=scan_root)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not p.is_file():
            return {"ok": False, "error": f"文件不存在: {rel}"}
        resolved.append(p)
    return merge_video_paths_ordered(
        resolved,
        scan_root=scan_root,
        loop_enabled=loop_enabled,
        loop_target_seconds=loop_target_seconds,
        filename_slug=None,
    )


def run_merge_next_level(
    subdir_segments_ordered: list[str],
    *,
    scan_root: Path,
    recursive_in_subdir: bool,
    loop_enabled: bool,
    loop_target_seconds: float | None,
) -> dict:
    """One concat output per immediate subdirectory (under ``scan_root``).

    Outputs go to ``scan_root / merged /`` with basename prefixed by folder slug.
    """
    results: list[dict] = []
    all_ok = True
    for seg in subdir_segments_ordered:
        folder_display = seg.strip()
        try:
            sub_root = resolve_immediate_subdir(scan_root, seg)
        except ValueError as e:
            all_ok = False
            results.append(
                {"ok": False, "folder": folder_display, "error": str(e)}
            )
            continue
        entries = list_video_files(
            scan_root=sub_root,
            recursive=recursive_in_subdir,
            exclude_merged_outputs=True,
        )
        resolved = [(sub_root / e["rel"]).resolve() for e in entries]
        if not resolved:
            all_ok = False
            results.append(
                {
                    "ok": False,
                    "folder": folder_display,
                    "error": "文件夹内无可拼接视频",
                }
            )
            continue
        one = merge_video_paths_ordered(
            resolved,
            scan_root=scan_root,
            loop_enabled=loop_enabled,
            loop_target_seconds=loop_target_seconds,
            filename_slug=sub_root.name,
        )
        if one.get("ok"):
            results.append(
                {
                    "ok": True,
                    "folder": folder_display,
                    "output_rel": one.get("output_rel"),
                    "output_abs": one.get("output_abs"),
                    "concat_inputs": one.get("concat_inputs"),
                    "looped": one.get("looped"),
                    "loop_seconds": one.get("loop_seconds"),
                }
            )
        else:
            all_ok = False
            results.append(
                {
                    "ok": False,
                    "folder": folder_display,
                    "error": one.get("error", "拼接失败"),
                    "stderr": one.get("stderr"),
                }
            )

    summary_err = "" if all_ok else "部分或全部子文件夹拼接失败"
    return {
        "ok": all_ok,
        "merge_next_level": True,
        "error": summary_err if not all_ok else None,
        "results": results,
    }


def parse_scan_root(root_rel: str | None) -> Path:
    """API helper; raises ValueError / RuntimeError."""
    return resolve_scan_directory(root_rel, default_rel=DEFAULT_VIDEO_ROOT_REL)
