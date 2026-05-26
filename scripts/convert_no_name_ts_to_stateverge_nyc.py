#!/usr/bin/env python3
"""
Recursively convert .TS/.ts under ``STATEVERGE_TS_INPUT`` (env or
``config/storage_map.env``) to stream-copied MP4 under ``<STATEVERGE_VOL>/NYC/video``.
Delete each source .TS only after ffprobe passes.

Configure the card mount path once (e.g. export STATEVERGE_TS_INPUT='/Volumes/NO NAME')
instead of hardcoding vendor volume labels in scripts.

If ffmpeg fails with "Invalid data found when processing input", automatically
retries once with ``-f mpegts`` (some dashcam segments lack reliable probes).

Safety: only touches *.ts files under the input volume; never deletes non-TS files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import (  # noqa: E402
    get_nyc_video_root,
    get_stateverge_volume,
    resolve_ts_input_root,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _atomic_write_json(path: Path, obj: dict) -> None:
    payload = dict(obj)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _pid_lock_acquire(repo: Path) -> tuple[Path, Path] | None:
    """Return (pid_path, progress_path) or None if another run holds the lock."""
    logs = repo / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    pid_file = logs / "ts_convert.pid"
    prog_file = logs / "ts_convert_progress.json"

    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = -1
        if pid > 0:
            try:
                os.kill(pid, 0)
                return None
            except ProcessLookupError:
                pass
            except PermissionError:
                return None

    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    return pid_file, prog_file


def _pid_lock_release(pid_file: Path) -> None:
    try:
        if pid_file.is_file():
            txt = pid_file.read_text(encoding="utf-8").strip()
            if txt == str(os.getpid()):
                pid_file.unlink()
    except OSError:
        pass

MIN_BYTES = 1024 * 1024  # 1 MiB
MIN_DURATION_SEC = 1.0

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def log(msg: str) -> None:
    print(msg, flush=True)


def collect_ts_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.is_dir():
        return files
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # Skip AppleDouble resource forks on external FAT/exFAT volumes (not real MPEG-TS).
        if p.name.startswith("._"):
            continue
        if p.suffix.lower() != ".ts":
            continue
        files.append(p.resolve())
    files.sort(key=lambda x: str(x).lower())
    return files


def unique_mp4_path(out_dir: Path, stem: str) -> Path:
    """stem without extension; FILE.ts -> FILE.mp4; collisions -> FILE_1.mp4 …"""
    candidate = out_dir / f"{stem}.mp4"
    if not candidate.exists():
        return candidate
    i = 1
    while True:
        p = out_dir / f"{stem}_{i}.mp4"
        if not p.exists():
            return p
        i += 1


def _ffmpeg_copy_attempt(inp: Path, outp: Path, *, force_mpegts: bool) -> tuple[bool, str]:
    cmd: list[str] = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if force_mpegts:
        cmd.extend(["-f", "mpegts"])
    cmd.extend(
        [
            "-i",
            str(inp),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(outp),
        ]
    )
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600 * 24,
        )
    except subprocess.TimeoutExpired:
        try:
            outp.unlink(missing_ok=True)
        except OSError:
            pass
        return False, "ffmpeg timeout"
    except Exception as e:
        try:
            outp.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"ffmpeg invocation error: {e}"
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        try:
            outp.unlink(missing_ok=True)
        except OSError:
            pass
        return False, err[-4000:] if err else f"ffmpeg exit {r.returncode}"
    return True, ""


def run_ffmpeg_copy(inp: Path, outp: Path) -> tuple[bool, str]:
    """
    Stream-copy TS -> MP4. If demuxer autodetect fails (common on some dashcam
    segments), retry once with ``-f mpegts``.
    """
    ok, err = _ffmpeg_copy_attempt(inp, outp, force_mpegts=False)
    if ok:
        return True, ""
    low = err.lower()
    if "invalid data found when processing input" in low:
        log(f"[retry -f mpegts] {inp.name}")
        ok2, err2 = _ffmpeg_copy_attempt(inp, outp, force_mpegts=True)
        if ok2:
            return True, ""
        return (
            False,
            f"ffmpeg: {err}\n--- retry mpegts ---\nffmpeg: {err2}",
        )
    return False, err


def validate_mp4(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "输出文件不存在"
    sz = path.stat().st_size
    if sz <= MIN_BYTES:
        return False, f"文件过小 ({sz} bytes，要求 > {MIN_BYTES})"
    try:
        r = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        return False, f"ffprobe 调用失败: {e}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "ffprobe 失败")[:2000]
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError as e:
        return False, f"ffprobe JSON 解析失败: {e}"
    streams = data.get("streams") or []
    has_video = any((s.get("codec_type") == "video") for s in streams)
    if not has_video:
        return False, "无 video stream"
    try:
        dur = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur <= MIN_DURATION_SEC:
        return False, f"时长过短 ({dur}s，要求 > {MIN_DURATION_SEC}s)"
    return True, "ok"


def main() -> int:
    repo = _repo_root()
    lock = _pid_lock_acquire(repo)
    if lock is None:
        log("[abort] 已有 TS 转换任务在运行（见 logs/ts_convert.pid）")
        return 3

    pid_file, prog_file = lock

    _verb = os.environ.get("STATEVERGE_STORAGE_VERBOSE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    vol_no_name = resolve_ts_input_root(verbose=_verb)
    output_root = get_nyc_video_root(verbose=_verb)
    vol_sv = get_stateverge_volume(verbose=_verb)

    started = datetime.now(timezone.utc).isoformat()
    progress: dict = {
        "running": True,
        "phase": "preflight",
        "started_at": started,
        "updated_at": started,
        "input_root": str(vol_no_name) if vol_no_name else "",
        "output_root": str(output_root),
        "total_ts": 0,
        "processed": 0,
        "converted_ok": 0,
        "deleted_ts": 0,
        "failed": 0,
        "current_file": "",
        "exit_code": None,
        "preflight_error": "",
        "failures_recent": [],
        "failures_full_count": 0,
    }

    try:
        if vol_no_name is None:
            msg = "STATEVERGE_TS_INPUT 未配置；跳过 TS 转换（可在环境或 storage_map.env 中设置）。"
            log(f"[skip] {msg}")
            progress["running"] = False
            progress["phase"] = "skipped_no_input"
            progress["preflight_error"] = msg
            progress["exit_code"] = 0
            _atomic_write_json(prog_file, progress)
            return 0
        if not vol_no_name.is_dir():
            msg = f"输入卷不存在或不可用: {vol_no_name}"
            log(f"[skip] {msg}")
            progress["running"] = False
            progress["phase"] = "skipped_input_unmounted"
            progress["preflight_error"] = msg
            progress["exit_code"] = 0
            _atomic_write_json(prog_file, progress)
            return 0
        if not vol_sv.is_dir():
            msg = f"输出卷不存在或不可用: {vol_sv}"
            log(f"[skip] {msg}")
            progress["running"] = False
            progress["phase"] = "skipped_output_unmounted"
            progress["preflight_error"] = msg
            progress["exit_code"] = 0
            _atomic_write_json(prog_file, progress)
            return 0

        out_dir = output_root
        out_dir.mkdir(parents=True, exist_ok=True)

        progress["phase"] = "scanning"
        _atomic_write_json(prog_file, progress)

        ts_files = collect_ts_files(vol_no_name)
        log(f"[scan] 找到 TS 文件: {len(ts_files)} 个")

        progress["phase"] = "converting"
        progress["total_ts"] = len(ts_files)
        progress["processed"] = 0
        _atomic_write_json(prog_file, progress)

        ok_convert = 0
        ok_deleted = 0
        failed = 0
        failures: list[tuple[str, str]] = []

        for idx, src in enumerate(ts_files):
            stem = src.stem
            dst = unique_mp4_path(out_dir, stem)

            progress["current_file"] = src.name
            progress["processed"] = idx
            progress["converted_ok"] = ok_convert
            progress["deleted_ts"] = ok_deleted
            progress["failed"] = failed
            progress["failures_recent"] = [
                {"path": p, "reason": r[:800]} for p, r in failures[-15:]
            ]
            progress["failures_full_count"] = len(failures)
            _atomic_write_json(prog_file, progress)

            log(f"[conv] {src.name} -> {dst.name}")

            ok_ffmpeg, ffmpeg_msg = run_ffmpeg_copy(src, dst)
            if not ok_ffmpeg:
                failed += 1
                failures.append((str(src), f"ffmpeg: {ffmpeg_msg}"))
                log(f"[FAIL] ffmpeg: {src}\n       {ffmpeg_msg[:500]}")
                progress["processed"] = idx + 1
                progress["failed"] = failed
                progress["failures_recent"] = [
                    {"path": p, "reason": r[:800]} for p, r in failures[-15:]
                ]
                progress["failures_full_count"] = len(failures)
                _atomic_write_json(prog_file, progress)
                continue

            ok_probe, probe_msg = validate_mp4(dst)
            if not ok_probe:
                failed += 1
                failures.append((str(src), f"ffprobe: {probe_msg}"))
                log(f"[FAIL] ffprobe: {dst}\n       {probe_msg}")
                try:
                    dst.unlink(missing_ok=True)
                except OSError:
                    pass
                progress["processed"] = idx + 1
                progress["failed"] = failed
                progress["failures_recent"] = [
                    {"path": p, "reason": r[:800]} for p, r in failures[-15:]
                ]
                progress["failures_full_count"] = len(failures)
                _atomic_write_json(prog_file, progress)
                continue

            ok_convert += 1
            try:
                src.unlink()
                ok_deleted += 1
                log(f"[ok] 已验证并删除原文件: {src.name}")
            except OSError as e:
                failed += 1
                failures.append((str(src), f"删除原 TS 失败（MP4 已生成）: {e}"))
                log(f"[FAIL] 无法删除原 TS（输出已保留）: {src}\n       {e}")

            progress["processed"] = idx + 1
            progress["converted_ok"] = ok_convert
            progress["deleted_ts"] = ok_deleted
            progress["failed"] = failed
            progress["failures_recent"] = [
                {"path": p, "reason": r[:800]} for p, r in failures[-15:]
            ]
            progress["failures_full_count"] = len(failures)
            _atomic_write_json(prog_file, progress)

        log("")
        log("========== 汇总 ==========")
        log(f"TS 扫描总数:     {len(ts_files)}")
        log(f"转换成功数:       {ok_convert}")
        log(f"原 TS 删除数:     {ok_deleted}")
        log(f"失败数:           {failed}")
        if failures:
            log("")
            log("---------- 失败列表 ----------")
            for path, reason in failures:
                log(f"- {path}")
                for line in reason.splitlines():
                    log(f"    {line}")

        rc = 0 if failed == 0 else 1
        progress["running"] = False
        progress["phase"] = "done"
        progress["current_file"] = ""
        progress["exit_code"] = rc
        progress["converted_ok"] = ok_convert
        progress["deleted_ts"] = ok_deleted
        progress["failed"] = failed
        progress["processed"] = len(ts_files)
        progress["failures_recent"] = [
            {"path": p, "reason": r[:800]} for p, r in failures[-15:]
        ]
        progress["failures_full_count"] = len(failures)
        _atomic_write_json(prog_file, progress)
        return rc
    finally:
        _pid_lock_release(pid_file)


if __name__ == "__main__":
    sys.exit(main())
