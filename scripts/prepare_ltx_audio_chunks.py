"""
Prepare a per-clip audio package for LTX video generation.

Slices ``topics/<topic>/audio/voice.wav`` into fixed-duration WAV chunks
(default 55 seconds each) suitable for one-by-one upload to LTX.

Reads:
    topics/<topic>/audio/voice.wav

Writes:
    topics/<topic>/ltx/audio_chunks/ltx_audio_NNN.wav     (48kHz / stereo / pcm_s16le)
    topics/<topic>/ltx/audio_chunks/audio_chunks_manifest.json
    topics/<topic>/ltx/audio_chunks/README_FOR_LTX.md

If ``audio_chunks/`` already exists with content, it is moved to
``topics/<topic>/ltx/archive/audio_chunks_<YYYYMMDD_HHMMSS>/`` before regeneration.

Usage:
    python scripts/prepare_ltx_audio_chunks.py --topic ai_future_cn --chunk-sec 55
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LOG_PREFIX = "[ltx_audio_chunks]"


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {(r.stderr or '').strip()}")
    out = (r.stdout or "").strip()
    try:
        return float(out)
    except ValueError as e:
        raise RuntimeError(f"ffprobe returned unparseable duration: {out!r}") from e


def ffmpeg_extract_wav(
    in_path: Path,
    start_sec: float,
    duration_sec: float,
    out_path: Path,
) -> None:
    """Output-seek + re-encode to 48kHz stereo pcm_s16le for sample-accurate cuts."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(in_path),
        "-ss", f"{start_sec:.6f}",
        "-t", f"{duration_sec:.6f}",
        "-ar", "48000",
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"ffmpeg extract failed for {out_path.name}: {(r.stderr or '')[:400]}"
        )


def fmt_secs(s: float) -> str:
    s = max(0.0, float(s))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    ss = s - h * 3600 - m * 60
    if h:
        return f"{h}:{m:02d}:{ss:05.2f}"
    return f"{m}:{ss:05.2f}"


def archive_existing_chunks(chunks_dir: Path, archive_root: Path) -> Path | None:
    if not chunks_dir.exists():
        return None
    has_content = any(chunks_dir.iterdir())
    if not has_content:
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = archive_root / f"audio_chunks_{ts}"
    archive_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(chunks_dir), str(dst))
    return dst


def write_readme(
    readme_path: Path,
    *,
    topic: str,
    chunk_sec: int,
    chunk_count: int,
    total_duration: float,
) -> None:
    width = max(3, len(str(chunk_count)))
    sample_audio = f"ltx_audio_{1:0{width}d}.wav"
    sample_video = f"ltx_video_{1:0{width}d}.mp4"
    body = f"""# LTX 视频生成说明 - {topic}

本目录包含按 **{chunk_sec} 秒** 等长切分的旁白音频包，用于逐段喂给 LTX 生成视频。

## 总览

- 旁白总时长: **{fmt_secs(total_duration)} ({total_duration:.2f}s)**
- 切分段数: **{chunk_count}**
- 单段目标长度: **{chunk_sec} s**（最后一段可能略短）
- 音频参数: 48000 Hz / stereo / PCM s16le

## 操作流程

1. 按文件名顺序，把每个 `ltx_audio_NNN.wav` 依次上传给 LTX。
   - 第一段: `{sample_audio}` → 输出: `{sample_video}`
   - 第二段: `ltx_audio_{2:0{width}d}.wav` → 输出: `ltx_video_{2:0{width}d}.mp4`
   - …以此类推，直到 `ltx_audio_{chunk_count:0{width}d}.wav`。

2. 每段建议生成 **{chunk_sec} 秒视频**，与音频时长严格对齐。

3. 风格统一使用：

   ```
   realistic cinematic documentary style
   no text, no readable text, no logos
   ```

   每段 prompt 都用同一句风格描述，仅替换主题/场景描述部分；这样下载回来后镜头风格才连续。

4. 下载视频后按 LTX 的输出命名为：

   ```
   ltx_video_{1:0{width}d}.mp4
   ltx_video_{2:0{width}d}.mp4
   ...
   ltx_video_{chunk_count:0{width}d}.mp4
   ```

5. 把所有下载完成的 `.mp4` 放进：

   ```
   topics/{topic}/ltx/generated/
   ```

   后续合并/对齐脚本会从该目录读取。

## 切片清单

详见同目录下 `audio_chunks_manifest.json`，字段：

- `chunk_id`：从 1 开始的序号
- `file`：相对仓库根的路径
- `start_sec`：从 voice.wav 的起始秒
- `end_sec`：在 voice.wav 中的结束秒
- `duration_sec`：本段实际时长

## 风格 prompt 参考

```
A realistic cinematic documentary shot. {topic}. Subtle camera motion. Natural lighting. High detail. No text, no readable text, no captions, no logos, no watermark.
```

请保持每段 prompt 起手都是 `realistic cinematic documentary style, no text, no logos` —— 这条规则不要改。
"""
    readme_path.write_text(body, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    root = repo_root()
    topic = args.topic
    chunk_sec = float(args.chunk_sec)
    if chunk_sec <= 0:
        print(f"{LOG_PREFIX} error: --chunk-sec must be > 0", file=sys.stderr)
        return 1

    tdir = root / "topics" / topic
    voice_wav = tdir / "audio" / "voice.wav"
    ltx_dir = tdir / "ltx"
    chunks_dir = ltx_dir / "audio_chunks"
    archive_root = ltx_dir / "archive"
    manifest_path = chunks_dir / "audio_chunks_manifest.json"
    readme_path = chunks_dir / "README_FOR_LTX.md"

    if not voice_wav.is_file():
        print(
            f"{LOG_PREFIX} error: voice.wav not found at {voice_wav}",
            file=sys.stderr,
        )
        return 1

    duration = ffprobe_duration(voice_wav)
    if duration <= 0.0:
        print(
            f"{LOG_PREFIX} error: voice.wav reports duration<=0 ({duration})",
            file=sys.stderr,
        )
        return 1

    n_chunks = max(1, int(math.ceil(duration / chunk_sec)))
    log(f"topic={topic}")
    log(f"voice.wav duration={fmt_secs(duration)} ({duration:.3f}s)")
    log(f"chunk_sec={chunk_sec:g}  n_chunks={n_chunks}")

    archived = archive_existing_chunks(chunks_dir, archive_root)
    if archived is not None:
        try:
            rel = archived.relative_to(root)
        except ValueError:
            rel = archived
        log(f"archived previous audio_chunks/ -> {rel}")

    chunks_dir.mkdir(parents=True, exist_ok=True)

    width = max(3, len(str(n_chunks)))
    manifest_entries: list[dict] = []
    chunk_paths: list[Path] = []

    for i in range(n_chunks):
        start = i * chunk_sec
        end = min((i + 1) * chunk_sec, duration)
        dur = end - start
        if dur <= 0.0:
            break
        cid = i + 1
        out_name = f"ltx_audio_{cid:0{width}d}.wav"
        out_path = chunks_dir / out_name
        log(
            f"slice {cid:0{width}d}/{n_chunks}  "
            f"start={start:7.2f}s  end={end:7.2f}s  dur={dur:5.2f}s  -> {out_name}"
        )
        ffmpeg_extract_wav(voice_wav, start, dur, out_path)
        try:
            rel_file = str(out_path.relative_to(root))
        except ValueError:
            rel_file = str(out_path)
        manifest_entries.append({
            "chunk_id": cid,
            "file": rel_file,
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "duration_sec": round(dur, 3),
        })
        chunk_paths.append(out_path)

    manifest = {
        "topic": topic,
        "source_audio": str(voice_wav.relative_to(root)),
        "source_duration_sec": round(duration, 3),
        "chunk_sec": chunk_sec,
        "chunk_count": len(manifest_entries),
        "wav_format": {"sample_rate": 48000, "channels": 2, "codec": "pcm_s16le"},
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "chunks": manifest_entries,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    write_readme(
        readme_path,
        topic=topic,
        chunk_sec=int(round(chunk_sec)),
        chunk_count=len(manifest_entries),
        total_duration=duration,
    )

    log("")
    log("=== summary ===")
    log(f"  voice.wav total       : {fmt_secs(duration)} ({duration:.3f}s)")
    log(f"  chunk_sec target      : {chunk_sec:g}s")
    log(f"  chunks generated      : {len(manifest_entries)}")
    for p in chunk_paths:
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = p
        log(f"    - {rel}")
    try:
        rel_manifest = manifest_path.relative_to(root)
    except ValueError:
        rel_manifest = manifest_path
    try:
        rel_readme = readme_path.relative_to(root)
    except ValueError:
        rel_readme = readme_path
    log(f"  manifest              : {rel_manifest}")
    log(f"  README for LTX        : {rel_readme}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Slice topics/<topic>/audio/voice.wav into fixed-duration WAV chunks "
            "for LTX video generation, with manifest + README + auto archive."
        )
    )
    ap.add_argument("--topic", required=True, help="Topic slug under topics/")
    ap.add_argument(
        "--chunk-sec",
        type=float,
        default=55.0,
        help="Target chunk duration in seconds (default 55).",
    )
    ns = ap.parse_args(argv)
    return run(ns)


if __name__ == "__main__":
    raise SystemExit(main())
