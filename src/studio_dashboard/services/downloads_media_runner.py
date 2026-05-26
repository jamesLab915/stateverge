"""Run ``scripts/downloads_media_to_stateverge_nyc.py`` with validated destinations."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from .stateverge_scan import resolve_scan_directory, volume_mounted


def resolve_downloads_dir() -> Path:
    try:
        from src.utils.home_downloads import resolve_home_downloads

        return resolve_home_downloads()
    except ImportError:
        home = Path.home()
        for name in ("Downloads", "downloads", "下载"):
            p = home / name
            if p.is_dir():
                return p.resolve()
        return (home / "Downloads").resolve()


def parse_video_dest(rel: str | None) -> Path:
    return resolve_scan_directory(rel, default_rel="NYC/video")


def parse_audio_dest(rel: str | None) -> Path:
    return resolve_scan_directory(rel, default_rel="NYC/music")


def script_path(repo_root: Path) -> Path:
    return (repo_root / "scripts" / "downloads_media_to_stateverge_nyc.py").resolve()


def run_downloads_migration(
    repo_root: Path,
    *,
    video_dest: Path,
    audio_dest: Path,
    downloads_dir: Path | None = None,
    dry_run: bool,
    purge_remnants: bool,
    timeout_sec: float = 7200.0,
) -> dict:
    """Invoke CLI; returns stdout/stderr and exit code."""
    sp = script_path(repo_root)
    if not sp.is_file():
        dls = (downloads_dir or resolve_downloads_dir()).resolve()
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"脚本不存在: {sp}",
            "command_preview": "",
            "error": "脚本缺失",
            "downloads_path": str(dls),
            "video_dest": str(video_dest.resolve()),
            "audio_dest": str(audio_dest.resolve()),
            "dry_run": dry_run,
            "purge_remnants": purge_remnants,
            "stateverge_mounted": volume_mounted(),
        }

    dls = (downloads_dir or resolve_downloads_dir()).resolve()

    cmd: list[str] = [
        sys.executable,
        "-u",
        str(sp),
        "--downloads",
        str(dls),
        "--video-dest",
        str(video_dest.resolve()),
        "--audio-dest",
        str(audio_dest.resolve()),
    ]
    if dry_run:
        cmd.append("--dry-run")
    if purge_remnants:
        cmd.append("--purge-remnants")

    cmd_preview = " ".join(shlex.quote(c) for c in cmd)
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root.resolve()),
        "PYTHONUNBUFFERED": "1",
    }
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root.resolve()),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "command_preview": cmd_preview,
            "error": f"运行超时（>{int(timeout_sec)}s）",
            "downloads_path": str(dls),
            "video_dest": str(video_dest.resolve()),
            "audio_dest": str(audio_dest.resolve()),
            "dry_run": dry_run,
            "purge_remnants": purge_remnants,
            "stateverge_mounted": volume_mounted(),
        }
    except OSError as e:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "command_preview": cmd_preview,
            "error": "无法启动子进程",
            "downloads_path": str(dls),
            "video_dest": str(video_dest.resolve()),
            "audio_dest": str(audio_dest.resolve()),
            "dry_run": dry_run,
            "purge_remnants": purge_remnants,
            "stateverge_mounted": volume_mounted(),
        }

    ok = proc.returncode == 0
    out = proc.stdout or ""
    err = proc.stderr or ""
    header = (
        f"# Studio 执行命令（便于对照终端）\n# {cmd_preview}\n"
        f"# cwd: {repo_root.resolve()}\n"
        f"# dry_run={dry_run} purge_remnants={purge_remnants}\n\n"
    )
    return {
        "ok": ok,
        "exit_code": proc.returncode,
        "stdout": header + out,
        "stderr": err,
        "command_preview": cmd_preview,
        "error": None if ok else f"退出码 {proc.returncode}",
        "downloads_path": str(dls),
        "video_dest": str(video_dest.resolve()),
        "audio_dest": str(audio_dest.resolve()),
        "dry_run": dry_run,
        "purge_remnants": purge_remnants,
        "stateverge_mounted": volume_mounted(),
    }
