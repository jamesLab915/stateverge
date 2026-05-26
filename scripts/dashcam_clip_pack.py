#!/usr/bin/env python3
"""
行车记录仪 / 自建素材：按顺序拼接多个片段，导出长视频（默认 1h / 2h 封顶）与 60s Short，
并对音轨做「人声友好」处理（高通去低频轰鸣 + FFT 降噪 + 轻度动态归一）。

依赖：本机 ffmpeg / ffprobe。独立脚本，不导入 StateVerge src。

示例：
  cd ~/stateverge && export PYTHONPATH="$PWD"
  python scripts/dashcam_clip_pack.py --input ~/Downloads/dash_raw --output ~/Movies/dash_out

可选：
  --long-durations 3600 7200   # 秒；总长不足则输出实际长度
  --short-duration 60 --short-at 0   # Short 起点（秒）
  --audio speech|light|off
  --horizontal-short           # Short 不切竖屏（默认导出 1080x1920）
  --delete-joined              # 删掉拼接母版以节省磁盘（可选）

说明：speech/light 使用 ffmpeg 高通 + FFT 降噪 + 动态归一化，侧重压低胎噪/风噪；
若要「更像播客人声分离」需另行接入 RNNoise 等模型。

4K 源（如 3840×2160）：拼接一致时用 stream-copy，长片默认保留 4K；竖屏 Short 从完整分辨率居中裁切再缩到
1080×1920，细节优于从 1080p 再放大。需要更小体积的长片可加 --long-max-height 1080。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm")

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"

_NUMBER_IN_NAME_RE = re.compile(r"\d+")


def log(msg: str) -> None:
    print(f"[dashcam_clip_pack] {msg}", flush=True)


def natural_sort_key(name: str) -> tuple:
    parts = _NUMBER_IN_NAME_RE.split(name)
    nums = _NUMBER_IN_NAME_RE.findall(name)
    out: list = []
    for i, part in enumerate(parts):
        out.append(part.lower())
        if i < len(nums):
            try:
                out.append(int(nums[i]))
            except ValueError:
                out.append(nums[i])
    return tuple(out)


def ffprobe_json(path: Path) -> dict[str, Any]:
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
        check=False,
    )
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def probe_video_summary(path: Path) -> dict[str, Any]:
    data = ffprobe_json(path)
    width = height = 0
    codec = ""
    fps = 0.0
    duration = 0.0
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    for s in data.get("streams") or []:
        if s.get("codec_type") != "video":
            continue
        width = int(s.get("width") or 0)
        height = int(s.get("height") or 0)
        codec = s.get("codec_name") or ""
        rate = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
        try:
            num, _, den = rate.partition("/")
            n, d = float(num), float(den or 1) or 1
            fps = n / d if d else 0.0
        except (TypeError, ValueError):
            fps = 0.0
        break
    has_audio = any(
        (s.get("codec_type") == "audio") for s in (data.get("streams") or [])
    )
    return {
        "duration_sec": duration,
        "width": width,
        "height": height,
        "codec": codec,
        "fps": fps,
        "has_audio": has_audio,
    }


def clip_uniform(paths: list[Path]) -> bool:
    if len(paths) < 2:
        return True
    first = probe_video_summary(paths[0])
    for p in paths[1:]:
        o = probe_video_summary(p)
        if (o["width"], o["height"]) != (first["width"], first["height"]):
            return False
        if o["codec"] != first["codec"]:
            return False
    return True


def concat_demuxer(paths: list[Path], out: Path) -> bool:
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        list_file = Path(f.name)
        for p in paths:
            esc = str(p.resolve()).replace("'", r"'\''")
            f.write(f"file '{esc}'\n")
    try:
        proc = subprocess.run(
            [
                FFMPEG,
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
                "-movflags",
                "+faststart",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0 and out.is_file() and out.stat().st_size > 0
    finally:
        try:
            list_file.unlink()
        except OSError:
            pass


def concat_reencode(paths: list[Path], out: Path, tw: int, th: int, fps_target: float) -> bool:
    """Normalize each clip to same WxH / fps / yuv420p + AAC, then concat."""
    n = len(paths)
    inputs: list[str] = []
    for p in paths:
        inputs.extend(["-i", str(p)])

    fps_s = str(int(round(fps_target))) if fps_target >= 15 else "30"

    parts: list[str] = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale={tw}:{th}:force_original_aspect_ratio=decrease,"
            f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={fps_s},format=yuv420p,setsar=1[v{i}]"
        )
        parts.append(f"[{i}:a?]aresample=async=1000:first_pts=0,asetpts=PTS-STARTPTS[a{i}]")

    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[vout][aout]")
    fg = ";".join(parts)

    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *inputs,
        "-filter_complex",
        fg,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or "")[:4000]
        log(f"reencode concat failed:\n{err}")
    return proc.returncode == 0 and out.is_file() and out.stat().st_size > 0


def build_joined(paths: list[Path], work: Path) -> Path | None:
    work.mkdir(parents=True, exist_ok=True)
    joined = work / "_joined_full.mp4"
    if joined.is_file():
        joined.unlink()

    if clip_uniform(paths):
        log("concat: trying stream-copy (uniform resolution + codec)")
        if concat_demuxer(paths, joined):
            return joined
        log("concat demuxer failed; falling back to re-encode")

    first = probe_video_summary(paths[0])
    tw, th = first["width"], first["height"]
    if tw <= 0 or th <= 0:
        tw, th = 1920, 1080
    fps_t = float(first["fps"] or 30)
    log(f"concat: re-encoding to {tw}x{th} @ ~{fps_t:.3f} fps")
    if concat_reencode(paths, joined, tw, th, fps_t):
        return joined
    return None


def audio_chain(profile: str) -> str | None:
    if profile == "off":
        return None
    # Road rumble + cabin hiss: high-pass first; afftdn with tracked noise floor;
    # dynaudnorm pulls perceived speech forward without heavy pumping.
    if profile == "light":
        return "highpass=f=100,afftdn=nf=-42:nr=11:tn=1"
    # speech default
    return (
        "highpass=f=120,afftdn=nf=-38:nr=14:tn=1,"
        "dynaudnorm=framelen=250:p=0.88:m=8"
    )


def run_ffmpeg(cmd: list[str]) -> bool:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log((proc.stderr or proc.stdout or "")[:6000])
        return False
    return True


def _is_uhd_probe(meta: dict[str, Any]) -> bool:
    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    return max(w, h) >= 3000


def export_long_clip(
    joined: Path,
    out_path: Path,
    max_seconds: float,
    audio_profile: str,
    has_audio: bool,
    *,
    max_height: int = 0,
    encode_preset: str = "medium",
    encode_crf: str = "20",
) -> float:
    """Trim to max_seconds. Video: stream-copy, or scale if max_height>0."""
    chain = audio_chain(audio_profile)
    reencode_video = max_height > 0

    if not reencode_video:
        cmd = [
            FFMPEG,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(joined),
            "-t",
            str(max_seconds),
            "-map",
            "0:v:0",
        ]
        if has_audio and chain:
            cmd.extend(
                [
                    "-map",
                    "0:a:0",
                    "-af",
                    chain,
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                ]
            )
        elif has_audio:
            cmd.extend(["-map", "0:a:0", "-c", "copy"])
        else:
            cmd.extend(["-c:v", "copy"])
        cmd.extend(["-movflags", "+faststart", str(out_path)])
        if not run_ffmpeg(cmd):
            return 0.0
        meta = probe_video_summary(out_path)
        return float(meta.get("duration_sec") or 0)

    # Downscale long-form (e.g. 4K → 1080p) — re-encode video + process/mux audio.
    vf = f"scale=-2:{max_height}:flags=lanczos,format=yuv420p"
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(joined),
        "-t",
        str(max_seconds),
        "-map",
        "0:v:0",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        encode_preset,
        "-crf",
        encode_crf,
    ]
    if has_audio:
        cmd.extend(["-map", "0:a:0"])
        if chain:
            cmd.extend(["-af", chain])
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    cmd.extend(["-movflags", "+faststart", str(out_path)])
    if not run_ffmpeg(cmd):
        return 0.0
    meta = probe_video_summary(out_path)
    return float(meta.get("duration_sec") or 0)


def export_short_vertical(
    joined: Path,
    out_path: Path,
    duration: float,
    start: float,
    audio_profile: str,
    horizontal: bool,
    has_audio: bool,
    *,
    v_preset: str = "fast",
    v_crf: str = "22",
) -> float:
    chain = audio_chain(audio_profile)
    vf: str | None = None
    if not horizontal:
        # Center pillar from landscape → fill 1080x1920 vertical Short.
        # 4K 源：先在完整分辨率裁切再缩放，保留街景细节。
        vf = (
            "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            "scale=1080:1920:flags=lanczos:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "fps=30,format=yuv420p"
        )

    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(start),
        "-i",
        str(joined),
        "-t",
        str(duration),
        "-map",
        "0:v:0",
    ]
    if vf:
        cmd.extend(["-vf", vf])
    cmd.extend(["-c:v", "libx264", "-preset", v_preset, "-crf", v_crf])
    if has_audio:
        cmd.extend(["-map", "0:a:0"])
        if chain:
            cmd.extend(["-af", chain])
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    cmd.extend(["-movflags", "+faststart", str(out_path)])

    if not run_ffmpeg(cmd):
        return 0.0
    return float(probe_video_summary(out_path).get("duration_sec") or 0)


def collect_inputs(inp: Path, recursive: bool) -> list[Path]:
    paths: list[Path] = []
    if inp.is_file():
        return [inp.resolve()]
    if not inp.is_dir():
        log(f"input not found: {inp}")
        return []

    if recursive:
        for ext in VIDEO_EXTS:
            paths.extend(inp.rglob(f"*{ext}"))
            paths.extend(inp.rglob(f"*{ext.upper()}"))
    else:
        for ext in VIDEO_EXTS:
            paths.extend(inp.glob(f"*{ext}"))
            paths.extend(inp.glob(f"*{ext.upper()}"))

    seen: set[Path] = set()
    uniq = [p for p in paths if p.is_file() and not (p in seen or seen.add(p))]
    uniq.sort(key=lambda p: natural_sort_key(p.name))
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser(description="Dashcam clip pack: join → long + Short.")
    ap.add_argument(
        "--input",
        "-i",
        type=Path,
        required=True,
        help="文件夹（多个片段）或单个视频文件",
    )
    ap.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="输出目录（将写入 joined、长片、short）",
    )
    ap.add_argument(
        "--sort",
        choices=("name", "mtime"),
        default="name",
        help="文件夹内排序方式（默认按文件名自然序）",
    )
    ap.add_argument("--recursive", "-r", action="store_true", help="递归扫描子文件夹")
    ap.add_argument(
        "--long-durations",
        nargs="+",
        type=int,
        default=[3600, 7200],
        metavar="SEC",
        help="长视频封顶时长（秒），默认 3600 7200",
    )
    ap.add_argument(
        "--short-duration",
        type=float,
        default=60.0,
        help="Short 时长（秒），默认 60",
    )
    ap.add_argument(
        "--short-at",
        type=float,
        default=0.0,
        help="Short 从成片开头算起的时间偏移（秒）",
    )
    ap.add_argument(
        "--audio",
        choices=("speech", "light", "off"),
        default="speech",
        help="音轨处理强度：speech（默认）/ light / off（全程拷贝）",
    )
    ap.add_argument(
        "--horizontal-short",
        action="store_true",
        help="Short 不切竖屏（默认导出 1080x1920）",
    )
    ap.add_argument(
        "--delete-joined",
        action="store_true",
        help="成功后删除拼接母版 <output>/_work/_joined_full.mp4 以省空间（默认保留）",
    )
    ap.add_argument(
        "--long-max-height",
        type=int,
        default=0,
        metavar="PX",
        help="长视频输出最大高度（如 1080）；0=保持原始分辨率（含 4K）。非 0 时会重编码画面",
    )
    ap.add_argument(
        "--long-preset",
        type=str,
        default="medium",
        help="长视频需要降分辨率时的 libx264 preset（默认 medium）",
    )
    ap.add_argument(
        "--long-crf",
        type=str,
        default="20",
        help="长视频降分辨率时的 CRF（默认 20）",
    )
    ap.add_argument(
        "--short-preset",
        type=str,
        default="",
        help="Short 的 libx264 preset；空则自动（约 4K 源用 medium，否则 fast）",
    )
    ap.add_argument(
        "--short-crf",
        type=str,
        default="",
        help="Short 的 CRF；空则自动（约 4K 源用 18，否则 22）",
    )
    args = ap.parse_args()

    try:
        subprocess.run(
            [FFMPEG, "-hide_banner", "-version"],
            capture_output=True,
            check=True,
            timeout=8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log(f"无法执行 ffmpeg：{FFMPEG!r}")
        return 3
    except subprocess.CalledProcessError as e:
        log(f"ffmpeg 异常退出 (code={e.returncode})")
        return 3
    try:
        subprocess.run(
            [FFPROBE, "-hide_banner", "-version"],
            capture_output=True,
            check=True,
            timeout=8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log(f"无法执行 ffprobe：{FFPROBE!r}")
        return 3
    except subprocess.CalledProcessError as e:
        log(f"ffprobe 异常退出 (code={e.returncode})")
        return 3

    clips = collect_inputs(args.input.resolve(), args.recursive)
    if args.sort == "mtime":
        clips.sort(key=lambda p: p.stat().st_mtime)

    if not clips:
        log("没有找到可用的视频文件")
        return 1

    out_dir = args.output.resolve()
    work_dir = out_dir / "_work"
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"clips ({len(clips)}): " + ", ".join(c.name for c in clips[:12]) +
        (" ..." if len(clips) > 12 else ""))

    joined_path = build_joined(clips, work_dir)
    if not joined_path:
        log("拼接失败")
        return 2

    joined_meta = probe_video_summary(joined_path)
    total_dur = float(joined_meta.get("duration_sec") or 0)
    has_audio = bool(joined_meta.get("has_audio"))
    uhd = _is_uhd_probe(joined_meta)

    short_preset = args.short_preset.strip() or ("medium" if uhd else "fast")
    short_crf = args.short_crf.strip() or ("18" if uhd else "22")
    if uhd:
        log(
            "检出高分辨率素材（约 4K）：长片默认保留分辨率；Short 使用 "
            f"preset={short_preset} crf={short_crf}（可用 --short-preset / --short-crf 覆盖）"
        )

    manifest: dict[str, Any] = {
        "input": str(args.input),
        "clips": [{"path": str(c), **probe_video_summary(c)} for c in clips],
        "joined": str(joined_path),
        "joined_resolution": {
            "width": joined_meta.get("width"),
            "height": joined_meta.get("height"),
        },
        "joined_duration_sec": total_dur,
        "approx_uhd": uhd,
        "audio_profile": args.audio,
        "short_encode": {"preset": short_preset, "crf": short_crf},
        "outputs": [],
    }

    lm = int(args.long_max_height)
    if lm > 0:
        manifest["long_downscale"] = {
            "max_height": lm,
            "preset": args.long_preset,
            "crf": args.long_crf,
        }

    # Long exports
    for sec in args.long_durations:
        label = f"long_max_{sec}s.mp4"
        dest = out_dir / label
        want = float(sec)
        actual_cap = min(want, total_dur) if total_dur > 0 else want
        mode = f"keep native + copy" if lm <= 0 else f"scale max height {lm}px"
        log(f"writing {label} (cap {want}s, available ~{actual_cap:.1f}s, video {mode})")
        dur_out = export_long_clip(
            joined_path,
            dest,
            want,
            args.audio,
            has_audio,
            max_height=lm,
            encode_preset=args.long_preset.strip() or "medium",
            encode_crf=str(args.long_crf).strip() or "20",
        )
        manifest["outputs"].append(
            {
                "role": "long",
                "file": label,
                "requested_sec": want,
                "actual_sec": dur_out,
            }
        )

    # Short
    sd = float(args.short_duration)
    st = float(args.short_at)
    short_name = "short_60s_vertical.mp4" if not args.horizontal_short else "short_60s_horizontal.mp4"
    if round(sd) != 60:
        tag = str(int(round(sd)))
        short_name = (
            f"short_{tag}s_vertical.mp4"
            if not args.horizontal_short
            else f"short_{tag}s_horizontal.mp4"
        )

    dest_s = out_dir / short_name
    avail = max(0.0, total_dur - st)
    take = min(sd, avail)
    log(f"writing {short_name} from t={st:.1f}s, duration={take:.1f}s")
    if take <= 0:
        log("Short 跳过：偏移超出总长")
    else:
        export_short_vertical(
            joined_path,
            dest_s,
            take,
            st,
            args.audio,
            horizontal=args.horizontal_short,
            has_audio=has_audio,
            v_preset=short_preset,
            v_crf=short_crf,
        )
        manifest["outputs"].append(
            {
                "role": "short",
                "file": short_name,
                "requested_sec": sd,
                "actual_sec": take,
                "start_sec": st,
            }
        )

    mf = out_dir / "dashcam_clip_manifest.json"
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote manifest: {mf}")

    if args.delete_joined and joined_path.is_file():
        try:
            joined_path.unlink()
            log("removed joined interim (--delete-joined)")
        except OSError as e:
            log(f"could not remove joined: {e}")

    log("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
