"""
Semi-automatic batch helper for LTX-style scene generation (no API calls).

Workflow:
- Read ``topics/<topic>/brief/ltx_scene_plan.json``
- Export per-scene prompt files under ``topics/<topic>/ltx/prompts/scene_XXX.txt``
- Write ``topics/<topic>/ltx/input_manifest.json`` to track expected outputs
- After you generate/download the clips into ``topics/<topic>/ltx/generated/scene_XXX.mp4``,
  assemble them into ``topics/<topic>/video/narrative_main.mp4`` (and also copy to
  ``topics/<topic>/ltx/narrative_main.mp4``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .paths import topic_production_paths


GLOBAL_STYLE = (
    "cinematic geopolitical documentary, dark blue tone, high contrast, "
    "global system, dramatic lighting, ultra realistic, 4k, smooth camera motion, "
    "no text, consistent style"
)


def _root() -> Path:
    return Path(os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge"))


def _run(args: list[str]) -> None:
    p = subprocess.run(args, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        msg = (p.stderr or p.stdout or "").strip()
        raise RuntimeError(f"Command failed: {' '.join(args)}\n{msg[:5000]}")


def _has_audio_stream(path: Path) -> bool:
    a = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
    ]
    p = subprocess.run(a, capture_output=True, text=True, check=False)
    return bool((p.stdout or "").strip())


def _relposix(base: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _scene_num(scene_id: str) -> int:
    m = re.search(r"(\d+)", scene_id or "")
    return int(m.group(1)) if m else 0


def _scene_file_id(scene_id: str, width: int = 3) -> str:
    n = _scene_num(scene_id)
    return f"scene_{n:0{width}d}"


def _load_scene_plan(path: Path) -> dict[str, Any]:
    if not Path(path).is_file():
        raise FileNotFoundError(f"Missing LTX scene plan: {path}")
    with Path(path).open("r", encoding="utf-8") as f:
        d: Any = json.load(f)
    if not isinstance(d, dict) or not isinstance(d.get("scenes"), list):
        raise ValueError(f"Invalid ltx_scene_plan.json schema: {path}")
    return d


def export_prompts(root: Path, topic: str) -> Path:
    """
    Create ``topics/<topic>/ltx/prompts/scene_XXX.txt`` and ``all_prompts_copyable.txt``,
    and write ``input_manifest.json``.
    """
    root = Path(root)
    t = topic_production_paths(root, topic)
    plan_path = t["ltx_scene_plan"]
    plan = _load_scene_plan(plan_path)
    scenes = [s for s in (plan.get("scenes") or []) if isinstance(s, dict)]
    scenes = sorted(scenes, key=lambda s: _scene_num(str(s.get("scene_id", ""))))

    ltx_dir = t["root"] / "ltx"
    prompts_dir = ltx_dir / "prompts"
    gen_dir = ltx_dir / "generated"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    gen_dir.mkdir(parents=True, exist_ok=True)

    items: list[dict[str, Any]] = []
    copyable_lines: list[str] = []
    for s in scenes:
        sid = str(s.get("scene_id", "")).strip()
        if not sid:
            continue
        dur = float(s.get("duration_sec", 8.0) or 8.0)
        sp = str(s.get("prompt", "")).strip()
        file_id = _scene_file_id(sid, width=3)
        prompt_path = prompts_dir / f"{file_id}.txt"
        expected_video_path = gen_dir / f"{file_id}.mp4"

        content = f"{GLOBAL_STYLE}\n\n{sp}\n"
        prompt_path.write_text(content, encoding="utf-8")

        items.append(
            {
                "scene_id": sid,
                "duration_sec": dur,
                "prompt_path": _relposix(root, prompt_path),
                "expected_video_path": _relposix(root, expected_video_path),
                "status": "pending",
            }
        )

        copyable_lines.append(f"=== {file_id} ===\n{GLOBAL_STYLE}\n\n{sp}\n")

    copyable = prompts_dir / "all_prompts_copyable.txt"
    copyable.write_text("\n".join(copyable_lines).rstrip() + "\n", encoding="utf-8")

    manifest_path = ltx_dir / "input_manifest.json"
    payload = {
        "topic": topic,
        "source_plan": _relposix(root, plan_path),
        "global_style": GLOBAL_STYLE,
        "items": items,
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


@dataclass(frozen=True)
class AssembleResult:
    normalized_count: int
    skipped_missing: int
    output_path: Path


def _normalize_clip(in_path: Path, out_path: Path) -> None:
    vf = (
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _has_audio_stream(in_path):
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(in_path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-vf",
                vf,
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
                "-movflags",
                "+faststart",
                str(out_path),
            ]
        )
    else:
        # Add a silent stereo track so concat is consistent.
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(in_path),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-shortest",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-vf",
                vf,
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
                "-movflags",
                "+faststart",
                str(out_path),
            ]
        )


def assemble(root: Path, topic: str) -> AssembleResult:
    """
    Scan ``topics/<topic>/ltx/generated/scene_*.mp4`` and build
    ``topics/<topic>/video/narrative_main.mp4`` (and copy to ``topics/<topic>/ltx/narrative_main.mp4``).

    Missing scenes are warned about and skipped.
    """
    root = Path(root)
    t = topic_production_paths(root, topic)
    ltx_dir = t["root"] / "ltx"
    gen_dir = ltx_dir / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)

    files = [p for p in gen_dir.glob("scene_*.mp4") if p.is_file()]
    if not files:
        print(f"[ltx] warning: no generated clips under {gen_dir}", flush=True)
        outp = t["video_narrative"]
        return AssembleResult(0, 0, outp)

    def _k(p: Path) -> tuple[int, str]:
        m = re.search(r"(\\d+)", p.stem)
        return (int(m.group(1)) if m else 0, p.name)

    files = sorted(files, key=_k)

    normalized: list[Path] = []
    skipped_missing = 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, src in enumerate(files, start=1):
            outn = tmp / f"norm_{i:04d}.mp4"
            try:
                _normalize_clip(src, outn)
            except Exception as e:  # noqa: BLE001
                print(f"[ltx] warning: normalize failed for {src.name}: {e}", flush=True)
                skipped_missing += 1
                continue
            normalized.append(outn)

        if not normalized:
            print("[ltx] warning: no clips could be normalized; nothing to assemble", flush=True)
            outp = t["video_narrative"]
            return AssembleResult(0, skipped_missing, outp)

        concat_list = tmp / "concat.txt"
        with concat_list.open("w", encoding="utf-8") as f:
            for p in normalized:
                f.write(f"file '{p.as_posix()}'\n")

        out_video = t["video_narrative"]
        out_video.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out_video),
            ]
        )

    # Also keep a copy under topics/<topic>/ltx/narrative_main.mp4
    ltx_out = ltx_dir / "narrative_main.mp4"
    ltx_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(out_video, ltx_out)
    except Exception:
        pass

    return AssembleResult(len(normalized), skipped_missing, out_video)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="LTX batch helper (no API).")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--export-prompts", action="store_true", dest="export_prompts")
    ap.add_argument("--assemble", action="store_true", dest="assemble")
    args = ap.parse_args(argv)

    if int(args.export_prompts) + int(args.assemble) != 1:
        ap.print_help()
        return 1

    root = Path(args.root) if args.root is not None else _root()
    if not (root / "src").is_dir():
        print(f"--root does not look like StateVerge: {root}", flush=True)
        return 1

    if args.export_prompts:
        mp = export_prompts(root, args.topic)
        print(f"Wrote input manifest: {mp}", flush=True)
        return 0

    if args.assemble:
        r = assemble(root, args.topic)
        if r.normalized_count > 0:
            print(f"Wrote narrative_main.mp4: {r.output_path}", flush=True)
        else:
            print("[ltx] warning: nothing assembled (no usable generated clips)", flush=True)
        print(
            f"Normalized clips: {r.normalized_count} (skipped: {r.skipped_missing})",
            flush=True,
        )
        return 0 if r.normalized_count > 0 else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

