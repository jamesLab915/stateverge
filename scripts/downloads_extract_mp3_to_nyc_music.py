#!/usr/bin/env python3
"""
从「下载」文件夹解压压缩包，抽出其中的音频文件（默认含 MP3 / WAV / M4A / FLAC 等），
移动到 StateVerge 盘 NYC/music，然后删除压缩包原件（解压目录用临时文件夹，用完即删）。

默认：
  - 下载目录：依次尝试 ~/Downloads、~/downloads、~/下载（选用首个存在的目录）
  - 目标目录：/Volumes/StateVerge/NYC/music（不存在则创建）
  - 默认只处理下载文件夹「顶层」的压缩包（与旧行为一致）

支持压缩格式：.zip、.tar.gz、.tgz、.tar.bz2、.tbz2（依赖系统 unzip / tar）

用法：
  python3 scripts/downloads_extract_mp3_to_nyc_music.py
  python3 scripts/downloads_extract_mp3_to_nyc_music.py --dry-run
  python3 scripts/downloads_extract_mp3_to_nyc_music.py --mp3-only --loose-mp3   # 仅 MP3（旧行为）
  python3 scripts/downloads_extract_mp3_to_nyc_music.py --deep                      # 递归：压缩包 + 散落音频
  python3 scripts/downloads_extract_mp3_to_nyc_music.py --deep --purge-remnants --dry-run
  python3 scripts/downloads_extract_mp3_to_nyc_music.py --deep --purge-remnants    # 迁入 NYC/music 后清空下载目录

另见 ``downloads_media_to_stateverge_nyc.py``：视频→ ``NYC/video``、音频→ ``NYC/music``。

本脚本也可用 ``--purge-remnants``：在解压并迁入音频后，删除「下载」文件夹内的全部残留（PDF/DMG/空文件夹等）。
务必先 ``--dry-run`` 核对后再执行。
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

from utils.storage_paths import get_nyc_music_root, get_stateverge_volume  # noqa: E402


ARCHIVE_EXTS = (
    ".zip",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
)

# Stock music packs (Pixabay / Envato / etc.) often ship WAV instead of MP3.
AUDIO_EXTS_FULL: frozenset[str] = frozenset(
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


def audio_extensions(mp3_only: bool) -> frozenset[str]:
    return frozenset({".mp3"}) if mp3_only else AUDIO_EXTS_FULL


def log(msg: str) -> None:
    print(f"[downloads→NYC/music] {msg}", flush=True)


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


def default_dest() -> Path:
    return get_nyc_music_root()


def _is_archive_file(p: Path) -> bool:
    if not p.is_file():
        return False
    if p.name.startswith("._"):
        return False
    name_lower = p.name.lower()
    return any(name_lower.endswith(ext) for ext in ARCHIVE_EXTS)


def _under_downloads_hidden_component(dls: Path, p: Path) -> bool:
    """Skip paths inside dot-directories (.Trash, etc.) relative to Downloads."""
    try:
        rel = p.relative_to(dls.resolve())
    except ValueError:
        return True
    return any(part.startswith(".") for part in rel.parts[:-1])


def list_top_level_archives(dls: Path) -> list[Path]:
    out: list[Path] = []
    if not dls.is_dir():
        return out
    base = dls.resolve()
    for p in dls.iterdir():
        if not p.is_file():
            continue
        if _under_downloads_hidden_component(base, p.resolve()):
            continue
        if _is_archive_file(p):
            out.append(p)
    out.sort(key=lambda x: x.name.lower())
    return out


def list_archives_recursive(dls: Path) -> list[Path]:
    """All archive files anywhere under Downloads (excluding dot-directories)."""
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
    # Shorter paths first — tends to clear nested packages before deeper siblings (deterministic).
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


def iter_audio_files(root: Path, exts: frozenset[str]) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirs, files in os.walk(root):
        # Skip Apple zip junk folders (double-sized extracts otherwise).
        dirs[:] = [d for d in dirs if d != "__MACOSX" and not d.startswith(".")]
        for fn in files:
            if fn.startswith("._"):
                continue
            suf = Path(fn).suffix.lower()
            if suf not in exts:
                continue
            found.append(Path(dirpath) / fn)
    found.sort(key=lambda p: str(p).lower())
    return found


def unique_dest(dest_dir: Path, stem: str, suffix: str) -> Path:
    """suffix includes leading dot, e.g. ``.wav``."""
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
    return s if s else "track"


def move_audio(src: Path, dest_dir: Path, dry_run: bool) -> Path | None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    suf = src.suffix.lower()
    dst = unique_dest(dest_dir, safe_stem(src), suf)
    if dry_run:
        log(f"[dry-run] 将移动 {src.name} → {dst}")
        return dst
    shutil.move(str(src), str(dst))
    log(f"已移动 {src.name} → {dst.name}")
    return dst


def process_archive(
    archive: Path, dest_dir: Path, exts: frozenset[str]
) -> tuple[int, bool]:
    """Returns (audio_count_moved, archive_removed)."""
    with tempfile.TemporaryDirectory(prefix="sv_mp3_", dir=None) as td:
        tmp = Path(td)

        if not extract_archive(archive, tmp):
            log(f"跳过（解压失败）: {archive.name}")
            return (0, False)

        tracks = iter_audio_files(tmp, exts)
        if not tracks:
            log(f"压缩包内无可识别的音频文件（{'仅 .mp3' if exts == {'.mp3'} else '.mp3/.wav/…'}），保留原件: {archive.name}")
            return (0, False)

        for m in tracks:
            move_audio(m, dest_dir, dry_run=False)

        archive.unlink()
        log(f"已删除压缩包: {archive.name}")

        return (len(tracks), True)


def process_loose_audio_top_level(
    dls: Path, dest_dir: Path, dry_run: bool, exts: frozenset[str]
) -> int:
    n = 0
    base = dls.resolve()
    dest_resolved = dest_dir.resolve()
    for p in sorted(dls.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf not in exts:
            continue
        if p.name.startswith("._"):
            continue
        rp = p.resolve()
        if _under_downloads_hidden_component(base, rp):
            continue
        try:
            if rp.is_relative_to(dest_resolved):
                continue
        except (ValueError, OSError):
            pass
        if dry_run:
            log(f"[dry-run] 将移动顶层音频: {p.name}")
            n += 1
            continue
        move_audio(p, dest_dir, dry_run=False)
        n += 1
    return n


def process_loose_audio_recursive(
    dls: Path, dest_dir: Path, dry_run: bool, exts: frozenset[str]
) -> int:
    """Move every audio file under Downloads (except dot-dir trees / junk / dest overlap)."""
    n = 0
    base = dls.resolve()
    dest_resolved = dest_dir.resolve()
    candidates: list[Path] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if p.name.startswith("._"):
            continue
        suf = p.suffix.lower()
        if suf not in exts:
            continue
        rp = p.resolve()
        if "__MACOSX" in rp.parts:
            continue
        if _under_downloads_hidden_component(base, rp):
            continue
        candidates.append(p)
    candidates.sort(key=lambda x: str(x).lower())
    for p in candidates:
        rp = p.resolve()
        try:
            if rp.is_relative_to(dest_resolved):
                continue
        except (ValueError, OSError):
            pass
        rel = rp.relative_to(base)
        label = str(rel)
        if dry_run:
            log(f"[dry-run] 将移动音频: {label}")
            n += 1
            continue
        move_audio(p, dest_dir, dry_run=False)
        log(f"（散落）已移动 {label}")
        n += 1
    return n


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
        description="Downloads archives → audio files → StateVerge NYC/music"
    )
    ap.add_argument(
        "--downloads",
        type=Path,
        default=None,
        help="下载目录（默认 ~/Downloads 或 ~/下载）",
    )
    ap.add_argument(
        "--dest",
        type=Path,
        default=None,
        help=f"音频目标目录（默认 {default_dest()}）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写盘")
    ap.add_argument(
        "--mp3-only",
        action="store_true",
        help="只抽取 .mp3（默认包含 .wav/.m4a/.flac 等常见配乐格式）",
    )
    ap.add_argument(
        "--loose-mp3",
        action="store_true",
        help="同时处理下载文件夹「顶层」内的散落音频（直接 move）",
    )
    ap.add_argument(
        "--deep",
        action="store_true",
        help="递归整棵下载目录：处理所有压缩包 + 移动所有散落音频文件",
    )
    ap.add_argument(
        "--purge-remnants",
        action="store_true",
        help="迁入完成后删除下载目录内全部残留（慎用；务必先配合 --dry-run 预览）",
    )
    args = ap.parse_args()

    dls = (args.downloads or resolve_downloads()).resolve()
    dest = (args.dest or default_dest()).resolve()

    if not dls.is_dir():
        log(f"下载目录不存在: {dls}")
        return 2

    vol = get_stateverge_volume()
    if not dest.is_dir():
        if (
            len(dest.parts) >= 3
            and dest.parts[1] == "Volumes"
            and dest.parts[2] == vol.name
            and not vol.is_dir()
        ):
            log(f"错误：未挂载项目 SSD（{vol}），无法创建 {dest}")
            return 3
        if not args.dry_run:
            dest.mkdir(parents=True, exist_ok=True)
            log(f"已创建目标目录: {dest}")

    exts = audio_extensions(bool(args.mp3_only))
    deep = bool(args.deep)
    archives = (
        list_archives_recursive(dls) if deep else list_top_level_archives(dls)
    )
    log(f"下载目录: {dls}")
    log(f"目标目录: {dest}")
    log(f"音频后缀: {', '.join(sorted(exts))}")
    log(f"模式: {'递归 (--deep)' if deep else '仅顶层压缩包'}")
    log(f"待处理压缩包: {len(archives)} 个")
    if args.purge_remnants:
        log(
            "Purge 残留: 是"
            + ("（dry-run 仅列出）" if args.dry_run else "（完成后将清空 Downloads 内全部顶层项）")
        )
        if not deep:
            log(
                "警告：未使用 --deep 时不会递归解压子文件夹内的压缩包；"
                "Purge 仍会删除 Downloads 下的顶层文件夹整体。建议改用 --deep --purge-remnants。"
            )
    else:
        log("Purge 残留: 否")

    total_audio = 0
    removed_archives = 0

    if args.dry_run:
        for a in archives:
            rel = a.relative_to(dls.resolve()) if deep else Path(a.name)
            log(f"[dry-run] 将处理压缩包: {rel}")
        if deep:
            process_loose_audio_recursive(dls, dest, dry_run=True, exts=exts)
        elif args.loose_mp3:
            process_loose_audio_top_level(dls, dest, dry_run=True, exts=exts)
        if args.purge_remnants:
            log("")
            log("--- Purge 下载目录残留（预览）---")
            purge_downloads_remainders(dls, dry_run=True)
        log("[dry-run] 结束（未解压、未删除压缩包；purge 未执行删除）")
        return 0

    for a in archives:
        cnt, removed = process_archive(a, dest, exts)
        total_audio += cnt
        if removed:
            removed_archives += 1

    loose = 0
    if deep:
        loose = process_loose_audio_recursive(dls, dest, dry_run=False, exts=exts)
    elif args.loose_mp3:
        loose = process_loose_audio_top_level(dls, dest, dry_run=False, exts=exts)

    purged = 0
    if args.purge_remnants:
        log("")
        log("--- Purge 下载目录残留 ---")
        purged = purge_downloads_remainders(dls, dry_run=False)

    log("")
    log("========== 汇总 ==========")
    log(f"扫描到的压缩包数: {len(archives)}")
    log(f"移出的音频文件数（来自压缩包）: {total_audio}")
    log(f"已删除的压缩包数（解压成功且包内有目标音频）: {removed_archives}")
    if deep:
        log(f"散落音频移动数（递归）: {loose}")
    elif args.loose_mp3:
        log(f"顶层散落音频移动数: {loose}")
    if args.purge_remnants:
        log(f"Purge 删除的顶层项数: {purged}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
