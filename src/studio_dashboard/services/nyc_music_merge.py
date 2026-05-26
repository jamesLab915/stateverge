"""
Audio picker + ffmpeg concat + optional loop-to-duration.

Scan root defaults to ``NYC/music`` under ``/Volumes/StateVerge``; any subdirectory
on that volume may be used (see ``stateverge_scan``).
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .stateverge_scan import (
    STATEVERGE_VOLUME,
    resolve_file_under_scan,
    resolve_scan_directory,
    volume_mounted,
)

DEFAULT_MUSIC_ROOT_REL = "NYC/music"
NYC_MUSIC_ROOT = STATEVERGE_VOLUME / DEFAULT_MUSIC_ROOT_REL

MERGED_SUBDIR = "merged"

AUDIO_SUFFIXES: frozenset[str] = frozenset(
    {
        ".mp3",
        ".wav",
        ".m4a",
        ".flac",
        ".aac",
        ".ogg",
        ".opus",
        ".aif",
        ".aiff",
        ".wma",
    }
)

MAX_LIST_FILES = 800


def mount_ok() -> bool:
    return volume_mounted()


def resolve_under_root(rel: str, *, scan_root: Path) -> Path:
    return resolve_file_under_scan(scan_root, rel)


def resolve_immediate_subdir(scan_root: Path, segment: str) -> Path:
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
    out: list[dict] = []
    if not scan_root.is_dir():
        return out
    for p in sorted(scan_root.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_dir():
            continue
        name = p.name
        if name.startswith("."):
            continue
        if name == MERGED_SUBDIR:
            continue
        out.append({"rel": name, "name": name})
    return out


def media_type_for_path(p: Path) -> str:
    m = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".aif": "audio/aiff",
        ".aiff": "audio/aiff",
        ".wma": "audio/x-ms-wma",
    }
    return m.get(p.suffix.lower(), "application/octet-stream")


def list_audio_files(
    *,
    scan_root: Path,
    recursive: bool,
    exclude_merged_outputs: bool,
) -> list[dict]:
    """Stat-only listing. Skips ``merged/`` when exclude_merged_outputs."""
    out: list[dict] = []
    if not scan_root.is_dir():
        return out

    root = scan_root.resolve()

    def skip_dir(name: str) -> bool:
        if name.startswith("."):
            return True
        if exclude_merged_outputs and name == MERGED_SUBDIR:
            return True
        return False

    def maybe_add(p: Path) -> None:
        if not p.is_file():
            return
        if p.name.startswith("._"):
            return
        suf = p.suffix.lower()
        if suf not in AUDIO_SUFFIXES:
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
        mtime_iso = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        birth_ts = getattr(st, "st_birthtime", None)
        birth_iso: str | None = None
        if isinstance(birth_ts, (int, float)) and birth_ts > 0:
            birth_iso = datetime.fromtimestamp(birth_ts, tz=timezone.utc).isoformat()
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


def _write_concat_list(paths: list[Path], list_file: Path) -> None:
    lines: list[str] = []
    for p in paths:
        ap = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{ap}'")
    list_file.write_text("\n".join(lines), encoding="utf-8")


def ffmpeg_concat_audio(paths: list[Path], out_m4a: Path) -> tuple[bool, str]:
    """Try stream copy then AAC re-encode."""
    list_file = out_m4a.with_suffix(".concat.txt")
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    try:
        _write_concat_list(paths, list_file)
        attempts: list[list[str]] = [
            [
                ffmpeg,
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
                "-c",
                "copy",
                str(out_m4a),
            ],
            [
                ffmpeg,
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
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(out_m4a),
            ],
        ]
        last_err = ""
        for cmd in attempts:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600 * 6)
            if r.returncode == 0 and out_m4a.is_file():
                return True, ""
            last_err = (r.stderr or r.stdout or "").strip()[-6000:]
        return False, last_err or "ffmpeg audio concat failed"
    finally:
        try:
            list_file.unlink(missing_ok=True)
        except OSError:
            pass


def ffmpeg_loop_audio(inp: Path, out_m4a: Path, target_sec: float) -> tuple[bool, str]:
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
            str(out_m4a),
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
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(out_m4a),
        ],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600 * 6)
        if r.returncode == 0 and out_m4a.is_file():
            return True, ""
    err = (r.stderr or r.stdout or "").strip()[-6000:]
    return False, err or "ffmpeg audio loop failed"


def merge_audio_paths_ordered(
    resolved: list[Path],
    *,
    scan_root: Path,
    loop_enabled: bool,
    loop_target_seconds: float | None,
    filename_slug: str | None = None,
) -> dict:
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

    merged_root = (scan_root / MERGED_SUBDIR).resolve()
    merged_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    slug_part = ""
    if filename_slug:
        s = sanitize_merge_slug(filename_slug)
        slug_part = f"{s}_"
    final_out = merged_root / f"{slug_part}merge_audio_{stamp}_{suffix}.m4a"
    work_concat = merged_root / f"_work_concat_audio_{uuid.uuid4().hex}.m4a"

    ok, err = ffmpeg_concat_audio(resolved, work_concat)
    if not ok:
        try:
            work_concat.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "error": "拼接失败", "stderr": err}

    dest_path = final_out
    if loop_enabled:
        if loop_target_seconds is None or loop_target_seconds < 1:
            try:
                work_concat.unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": False, "error": "开启循环时请填写目标时长（秒）≥ 1"}
        loop_out = merged_root / f"{slug_part}merge_audio_loop_{stamp}_{suffix}.m4a"
        ok2, err2 = ffmpeg_loop_audio(work_concat, loop_out, float(loop_target_seconds))
        try:
            work_concat.unlink(missing_ok=True)
        except OSError:
            pass
        if not ok2:
            return {"ok": False, "error": "循环输出失败", "stderr": err2}
        dest_path = loop_out
    else:
        try:
            work_concat.replace(dest_path)
        except OSError:
            shutil.move(str(work_concat), str(dest_path))

    try:
        out_rel = dest_path.relative_to(scan_r).as_posix()
    except ValueError:
        out_rel = dest_path.name

    return {
        "ok": True,
        "output_abs": str(dest_path),
        "output_rel": out_rel,
        "concat_inputs": len(resolved),
        "looped": bool(loop_enabled),
        "loop_seconds": loop_target_seconds if loop_enabled else None,
    }


def run_merge(
    rel_paths_ordered: list[str],
    *,
    scan_root: Path,
    loop_enabled: bool,
    loop_target_seconds: float | None,
) -> dict:
    resolved: list[Path] = []
    for rel in rel_paths_ordered:
        try:
            p = resolve_under_root(rel.strip(), scan_root=scan_root)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not p.is_file():
            return {"ok": False, "error": f"文件不存在: {rel}"}
        resolved.append(p)
    return merge_audio_paths_ordered(
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
        entries = list_audio_files(
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
                    "error": "文件夹内无可拼接音频",
                }
            )
            continue
        one = merge_audio_paths_ordered(
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
    return resolve_scan_directory(root_rel, default_rel=DEFAULT_MUSIC_ROOT_REL)
