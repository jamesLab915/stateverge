#!/usr/bin/env python3
"""
将「下载」目录内的视频 / 音乐批量迁入外接 StateVerge：

  - 解压 zip / tar.gz / …（临时目录），包内视频 → NYC/video，音频 → NYC/music
  - 散落的视频 / 音频文件（递归）→ 同上（不重命名归档）
  - 压缩包仅在「解压成功且至少迁出一条视频或音频」后删除
  - 可选 ``--purge-remnants``：迁入结束后删除下载目录内所有残留（PDF/DMG/未识别格式等）

默认路径：
  - 下载目录：~/Downloads（或 ~/downloads / ~/下载，见 resolve_home_downloads）
  - 视频输出：/Volumes/StateVerge/NYC/video
  - 音频输出：/Volumes/StateVerge/NYC/music

用法：
  python3 scripts/downloads_media_to_stateverge_nyc.py --dry-run
  python3 scripts/downloads_media_to_stateverge_nyc.py
  python3 scripts/downloads_media_to_stateverge_nyc.py --purge-remnants   # 迁入后清空下载目录残留
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import get_nyc_music_root, get_nyc_video_root, get_stateverge_volume  # noqa: E402


ARCHIVE_EXTS = (
    ".zip",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
)

VIDEO_EXTS: frozenset[str] = frozenset(
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

AUDIO_EXTS: frozenset[str] = frozenset(
    {
        ".mp3",
        ".wav",
        ".m4a",
        ".flac",
        ".aac",
        ".ogg",
        ".aif",
        ".aiff",
    }
)


def log(msg: str) -> None:
    print(f"[downloads→NYC] {msg}", flush=True)


def resolve_downloads() -> Path:
    try:
        from src.utils.home_downloads import resolve_home_downloads

        return resolve_home_downloads()
    except ImportError:
        pass
    home = Path.home()
    for name in ("Downloads", "downloads", "下载"):
        p = home / name
        if p.is_dir():
            return p.resolve()
    return (home / "Downloads").resolve()


def default_video_dest() -> Path:
    return get_nyc_video_root()


def default_audio_dest() -> Path:
    return get_nyc_music_root()


def _is_archive_file(p: Path) -> bool:
    if not p.is_file():
        return False
    if p.name.startswith("._"):
        return False
    name_lower = p.name.lower()
    return any(name_lower.endswith(ext) for ext in ARCHIVE_EXTS)


def _under_downloads_hidden_component(dls: Path, p: Path) -> bool:
    try:
        rel = p.relative_to(dls.resolve())
    except ValueError:
        return True
    return any(part.startswith(".") for part in rel.parts[:-1])


def list_archives_recursive(dls: Path) -> list[Path]:
    out: list[Path] = []
    if not dls.is_dir():
        return out
    base = dls.resolve()
    for p in base.rglob("*"):
        if not _is_archive_file(p):
            continue
        if _under_downloads_hidden_component(base, p):
            continue
        out.append(p)
    out.sort(key=lambda x: (len(x.parts), str(x).lower()))
    return out


def extract_archive(archive: Path, tmp: Path) -> bool:
    suf = archive.name.lower()
    try:
        if suf.endswith(".zip"):
            r = subprocess.run(
                ["unzip", "-q", "-o", str(archive), "-d", str(tmp)],
                capture_output=True,
                text=True,
                timeout=3600,
            )
            return r.returncode == 0
        if suf.endswith((".tar.gz", ".tgz")):
            r = subprocess.run(
                ["tar", "-xzf", str(archive), "-C", str(tmp)],
                capture_output=True,
                text=True,
                timeout=3600,
            )
            return r.returncode == 0
        if suf.endswith((".tar.bz2", ".tbz2")):
            r = subprocess.run(
                ["tar", "-xjf", str(archive), "-C", str(tmp)],
                capture_output=True,
                text=True,
                timeout=3600,
            )
            return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log(f"解压失败 {archive.name}: {e}")
        return False
    log(f"不支持的格式（跳过）: {archive.name}")
    return False


def iter_video_audio_under(
    root: Path,
    video_exts: frozenset[str],
    audio_exts: frozenset[str],
) -> tuple[list[Path], list[Path]]:
    videos: list[Path] = []
    audios: list[Path] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__MACOSX" and not d.startswith(".")]
        for fn in files:
            if fn.startswith("._"):
                continue
            suf = Path(fn).suffix.lower()
            p = Path(dirpath) / fn
            if suf in video_exts:
                videos.append(p)
            elif suf in audio_exts:
                audios.append(p)
    videos.sort(key=lambda x: str(x).lower())
    audios.sort(key=lambda x: str(x).lower())
    return videos, audios


def unique_dest(dest_dir: Path, stem: str, suffix: str) -> Path:
    base = dest_dir / f"{stem}{suffix}"
    if not base.exists():
        return base
    i = 1
    while True:
        p = dest_dir / f"{stem}_{i}{suffix}"
        if not p.exists():
            return p
        i += 1


def safe_stem(path: Path) -> str:
    s = path.stem
    return s if s else "media"


def move_media(src: Path, dest_dir: Path, *, dry_run: bool, kind: str) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    suf = src.suffix.lower()
    dst = unique_dest(dest_dir, safe_stem(src), suf)
    if dry_run:
        log(f"[dry-run][{kind}] {src.name} → {dst.name}")
        return
    shutil.move(str(src), str(dst))
    log(f"[{kind}] 已移动 {src.name} → {dst.name}")


def process_archive(
    archive: Path,
    video_dest: Path,
    audio_dest: Path,
    *,
    dry_run: bool,
    video_exts: frozenset[str],
    audio_exts: frozenset[str],
) -> tuple[int, int, bool]:
    """Returns (video_moved, audio_moved, archive_deleted)."""
    with tempfile.TemporaryDirectory(prefix="sv_dls_", dir=None) as td:
        tmp = Path(td)
        if not extract_archive(archive, tmp):
            log(f"跳过（解压失败）: {archive.name}")
            return (0, 0, False)

        vids, auds = iter_video_audio_under(tmp, video_exts, audio_exts)
        if not vids and not auds:
            log(f"压缩包内无视频/音频，保留原件: {archive.name}")
            return (0, 0, False)

        for p in vids:
            move_media(p, video_dest, dry_run=dry_run, kind="video")
        for p in auds:
            move_media(p, audio_dest, dry_run=dry_run, kind="audio")

        if dry_run:
            log(f"[dry-run] 将删除压缩包: {archive.name}")
            return (len(vids), len(auds), False)

        archive.unlink()
        log(f"已删除压缩包: {archive.name}")
        return (len(vids), len(auds), True)


def process_loose_media_recursive(
    dls: Path,
    video_dest: Path,
    audio_dest: Path,
    *,
    dry_run: bool,
    video_exts: frozenset[str],
    audio_exts: frozenset[str],
) -> tuple[int, int]:
    """Move loose video/audio anywhere under Downloads."""
    base = dls.resolve()
    vd = video_dest.resolve()
    ad = audio_dest.resolve()
    loose_v = 0
    loose_a = 0

    candidates: list[Path] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if _is_archive_file(p):
            continue
        if p.name.startswith("._"):
            continue
        rp = p.resolve()
        if "__MACOSX" in rp.parts:
            continue
        if _under_downloads_hidden_component(base, rp):
            continue
        suf = p.suffix.lower()
        if suf not in video_exts and suf not in audio_exts:
            continue
        candidates.append(p)

    candidates.sort(key=lambda x: str(x).lower())
    for p in candidates:
        rp = p.resolve()
        try:
            if rp.is_relative_to(vd) or rp.is_relative_to(ad):
                continue
        except (ValueError, OSError):
            pass
        suf = p.suffix.lower()
        rel = rp.relative_to(base)
        label = str(rel)
        if suf in video_exts:
            if dry_run:
                log(f"[dry-run][video] 散落 {label}")
                loose_v += 1
                continue
            move_media(p, video_dest, dry_run=False, kind="video")
            loose_v += 1
        elif suf in audio_exts:
            if dry_run:
                log(f"[dry-run][audio] 散落 {label}")
                loose_a += 1
                continue
            move_media(p, audio_dest, dry_run=False, kind="audio")
            loose_a += 1

    return loose_v, loose_a


def purge_downloads_remainders(dls: Path, *, dry_run: bool) -> int:
    """Remove everything directly under Downloads (keep the folder itself)."""
    base = dls.resolve()
    name_ok = base.name.lower() in ("downloads", "下载")
    if not name_ok:
        log(f"拒绝 purge：目录名不是 Downloads/下载 → {base}")
        return 0

    n = 0
    for child in sorted(base.iterdir(), key=lambda x: x.name.lower()):
        if dry_run:
            log(f"[dry-run][purge] 将删除 {child.name}")
            n += 1
            continue
        try:
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                shutil.rmtree(child)
            n += 1
        except OSError as e:
            log(f"[purge] 失败 {child}: {e}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Downloads → StateVerge NYC/video + NYC/music (+ optional purge)"
    )
    ap.add_argument("--downloads", type=Path, default=None, help="下载目录")
    ap.add_argument(
        "--video-dest",
        type=Path,
        default=None,
        help=f"视频目录（默认 {default_video_dest()}）",
    )
    ap.add_argument(
        "--audio-dest",
        type=Path,
        default=None,
        help=f"音频目录（默认 {default_audio_dest()}）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    ap.add_argument(
        "--purge-remnants",
        action="store_true",
        help="迁入完成后删除下载目录内全部残留（慎用；建议先 --dry-run）",
    )
    args = ap.parse_args()

    dls = (args.downloads or resolve_downloads()).resolve()
    video_dest = (args.video_dest or default_video_dest()).resolve()
    audio_dest = (args.audio_dest or default_audio_dest()).resolve()

    if not dls.is_dir():
        log(f"下载目录不存在: {dls}")
        return 2

    def dest_on_project_ssd(p: Path) -> bool:
        try:
            vol = get_stateverge_volume().resolve()
            r = p.resolve()
            return (
                len(r.parts) >= 3
                and r.parts[1] == "Volumes"
                and r.parts[2] == vol.name
            )
        except OSError:
            return False

    if dest_on_project_ssd(video_dest) or dest_on_project_ssd(audio_dest):
        root_vol = get_stateverge_volume()
        if not root_vol.is_dir():
            log(f"错误：未挂载项目 SSD（{root_vol}）")
            return 3

    if not args.dry_run:
        video_dest.mkdir(parents=True, exist_ok=True)
        audio_dest.mkdir(parents=True, exist_ok=True)

    archives = list_archives_recursive(dls)
    log(f"下载目录: {dls}")
    log(f"视频输出: {video_dest}")
    log(f"音频输出: {audio_dest}")
    log(f"压缩包数量（递归）: {len(archives)}")
    if args.purge_remnants:
        log(f"Purge 残留: 是{'（dry-run 仅列出）' if args.dry_run else '（执行后将删除 Downloads 内残留）'}")
    else:
        log("Purge 残留: 否")

    tv = ta = ra = 0
    lv = la = 0

    if args.dry_run:
        for a in archives:
            rel = a.relative_to(dls.resolve())
            log(f"[dry-run] 将处理压缩包: {rel}")
        lv, la = process_loose_media_recursive(
            dls,
            video_dest,
            audio_dest,
            dry_run=True,
            video_exts=VIDEO_EXTS,
            audio_exts=AUDIO_EXTS,
        )
        if args.purge_remnants:
            purge_downloads_remainders(dls, dry_run=True)
        log("[dry-run] 结束（未解压、未删包、purge 未执行删除）")
        return 0

    for a in archives:
        v, au, removed = process_archive(
            a,
            video_dest,
            audio_dest,
            dry_run=False,
            video_exts=VIDEO_EXTS,
            audio_exts=AUDIO_EXTS,
        )
        tv += v
        ta += au
        if removed:
            ra += 1

    lv, la = process_loose_media_recursive(
        dls,
        video_dest,
        audio_dest,
        dry_run=False,
        video_exts=VIDEO_EXTS,
        audio_exts=AUDIO_EXTS,
    )

    purged = 0
    if args.purge_remnants:
        log("")
        log("--- Purge 下载目录残留 ---")
        purged = purge_downloads_remainders(dls, dry_run=False)

    log("")
    log("========== 汇总 ==========")
    log(f"压缩包删除数（内含媒体且解压成功）: {ra} / {len(archives)}")
    log(f"从压缩包迁出 — 视频: {tv} · 音频: {ta}")
    log(f"散落文件迁出 — 视频: {lv} · 音频: {la}")
    if args.purge_remnants:
        log(f"Purge 删除的顶层项数: {purged}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
