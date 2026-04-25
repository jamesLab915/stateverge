"""
Scan ``assets/presenter_masters/10s/``, classify 5s vs 10s by duration, de-dup by MD5,
normalize with ffmpeg, write ``5s/master_NNN.mp4`` and ``10s/master_NNN.mp4``,
move originals to ``_raw_backup/``.

Run::

    export PYTHONPATH="$HOME/StateVerge"
    python -m src.utils.organize_presenter_masters
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from fractions import Fraction

log = logging.getLogger("organize_presenter_masters")

VIDEO_EXTS = {".mp4", ".mov"}


def get_video_info(path: Path) -> dict[str, Any]:
    """
    Use ffprobe to read duration (seconds), width, height, fps (float).
    """
    path = Path(path)
    args = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    p = subprocess.run(
        args, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace"
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({p.returncode}): {p.stderr or p.stdout}")
    data = json.loads(p.stdout)
    fmt = data.get("format", {})
    raw_d = fmt.get("duration")
    if raw_d is None:
        d = 0.0
    else:
        d = float(raw_d)
    w, h, fps = 0, 0, 0.0
    for s in data.get("streams", []):
        if s.get("codec_type") != "video":
            continue
        w = int(s.get("width", 0) or 0)
        h = int(s.get("height", 0) or 0)
        r = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
        try:
            fps = float(Fraction(r))
        except (ValueError, ZeroDivisionError, TypeError):
            fps = 0.0
        if w and h and fps:
            break
    if not w or not h:
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                w = int(s.get("width", 0) or 0)
                h = int(s.get("height", 0) or 0)
                r = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
                try:
                    fps = float(Fraction(r))
                except (ValueError, ZeroDivisionError, TypeError):
                    fps = 0.0
                break
    return {
        "duration": round(d, 3),
        "width": w,
        "height": h,
        "fps": round(fps, 3),
    }


def _md5_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def normalize_video(input_path: Path, output_path: Path) -> None:
    """
    Encode to 1920x1080, 30 fps, yuv420p, AAC 48kHz stereo, +faststart.
    """
    input_path, output_path = Path(input_path), Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".tmp.mp4")
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    p = subprocess.run(
        args, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace"
    )
    if p.returncode != 0:
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise RuntimeError(
            f"ffmpeg normalize failed ({p.returncode}): {(p.stderr or p.stdout)[:3000]}"
        )
    tmp.replace(output_path)


def _log_line(
    *,
    file: str,
    duration: str,
    category: str,
    normalized: str,
    duplicate: str = "no",
) -> None:
    print(
        f"[presenter_masters] file={file} duration={duration} "
        f"category={category} normalized={normalized} duplicate={duplicate}"
    )


def _next_index(dest_dir: Path) -> int:
    dest_dir = Path(dest_dir)
    m = 0
    for p in dest_dir.glob("master_*.mp4"):
        s = p.stem.replace("master_", "")
        if s.isdigit():
            m = max(m, int(s))
    return m + 1


def _unique_backup_name(backup_dir: Path, name: str) -> Path:
    p = backup_dir / name
    if not p.is_file():
        return p
    n, suf = 2, p.suffix
    stem = p.stem
    while n < 1000:
        alt = backup_dir / f"{stem}__v{n}{suf}"
        if not alt.is_file():
            return alt
        n += 1
    return backup_dir / f"{stem}__v{n}{suf}"


def run(assets_root: Optional[Path] = None) -> int:
    """
    Process all videos under ``.../assets/presenter_masters/10s/`` only.
    """
    if assets_root is None:
        here = Path(__file__).resolve()
        # .../StateVerge/src/utils/... -> parent parent parent = StateVerge
        assets_root = here.parents[2] / "assets" / "presenter_masters"
    assets_root = Path(assets_root).resolve()
    scan_dir = assets_root / "10s"
    out_5 = assets_root / "5s"
    out_10 = assets_root / "10s"
    backup = assets_root / "_raw_backup"
    backup_dup = backup / "_duplicates"
    for d in (out_5, out_10, backup, backup_dup):
        d.mkdir(parents=True, exist_ok=True)

    files = sorted(
        p
        for p in scan_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    )
    if not files:
        log.info("No .mp4/.mov files under %s; nothing to do", scan_dir)
        print(
            f"[presenter_masters] file=— duration=— category=— normalized=— "
            f"duplicate=—  (no inputs in {scan_dir})"
        )
        return 0

    seen_hash: set[str] = set()
    idx5 = _next_index(out_5)
    idx10 = _next_index(out_10)

    for src in files:
        try:
            rel = str(src.relative_to(assets_root))
        except ValueError:
            rel = str(src)
        try:
            digest = _md5_file(src)
        except OSError as e:
            _log_line(
                file=rel,
                duration="—",
                category="error",
                normalized="no",
                duplicate="—",
            )
            log.exception("md5: %s", e)
            continue
        if digest in seen_hash:
            try:
                dest = _unique_backup_name(backup_dup, src.name)
                shutil.move(str(src), str(dest))
            except OSError as e:
                _log_line(
                    file=rel,
                    duration="—",
                    category="10s_input",
                    normalized="no",
                    duplicate="yes",
                )
                log.exception("move duplicate: %s", e)
                continue
            _log_line(
                file=rel,
                duration="—",
                category="10s_input",
                normalized="no",
                duplicate="yes",
            )
            try:
                note = f" {dest.relative_to(assets_root)}"
            except ValueError:
                note = f" {dest}"
            print(f"  [presenter_masters] note=duplicate_moved_to{note}")
            continue

        try:
            info = get_video_info(src)
        except Exception as e:  # noqa: BLE001
            _log_line(
                file=rel,
                duration="—",
                category="error",
                normalized="no",
                duplicate="no",
            )
            log.exception("ffprobe: %s", e)
            continue

        dur = float(info["duration"])
        cat = "5s" if dur < 7.0 else "10s"
        if cat == "5s":
            out = out_5 / f"master_{idx5:03d}.mp4"
        else:
            out = out_10 / f"master_{idx10:03d}.mp4"

        try:
            normalize_video(src, out)
        except Exception as e:  # noqa: BLE001
            _log_line(
                file=rel,
                duration=str(dur),
                category=cat,
                normalized="no",
                duplicate="no",
            )
            log.exception("normalize: %s", e)
            continue

        if cat == "5s":
            idx5 += 1
        else:
            idx10 += 1
        seen_hash.add(digest)
        out_rel = str(out.relative_to(assets_root))
        _log_line(
            file=rel,
            duration=str(dur),
            category=cat,
            normalized=f"yes->{out_rel}",
            duplicate="no",
        )
        try:
            bpath = _unique_backup_name(backup, src.name)
            shutil.move(str(src), str(bpath))
        except OSError as e:
            log.exception("backup move failed for %s: %s", src, e)
            print(
                f"  [presenter_masters] warning=kept source (backup failed): {e}"
            )

    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr
    )
    sys.exit(run())


if __name__ == "__main__":
    main()
