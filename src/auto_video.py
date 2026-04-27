"""
One-shot auto video pipeline for StateVerge.

Drives a topic from ``brief/narration_script.txt`` to
``topics/<topic>/output/final_mix.mp4`` by invoking the existing real CLIs
in sequence (no in-process patching, no monkey-patching of mix_engine /
production / presenter_pipeline).

**Important — narration vs. mix audio**

- The brief script is used by the **media selector** as a **search query
  source** (keywords, segments), **not** as a TTS input for ``auto_video`` by
  default.
- ``mix_engine`` builds each clip with **B-roll** audio (if present) or
  **silent** AAC, then concat → ``final_mix.mp4``. That is **not** a read-aloud
  of ``narration_script.txt``.
- Optional: pass ``--narration-voice`` to also run
  :mod:`src.finalize_narration_voice` after the mix: ElevenLabs
  (``ELEVENLABS_*``) → ``topics/<topic>/audio/voice.wav``, then mux
  → ``output/final_with_voice.mp4`` (ffprobe-verified; loop video if shorter
  than narration).

The flow:

    1. Verify ``topics/<topic>/brief/narration_script.txt`` exists.
    2. ``python -m src.integrations.media_sources.selector --topic <topic>``
    3. ``python -m src.integrations.media_sources.promote  --topic <topic>``
    4. If ``topics/<topic>/video/`` has no .mp4, copy up to
       ``--max-video-copy`` files from ``topics/<topic>/envato/`` into
       ``topics/<topic>/video/`` so mix_engine has LTX-side material.
    5. ``python -m src.mix_engine.generate_timeline --topic <topic> --force``
    6. ``python -m src.run_pipeline --topic <topic> --only-main``
    6b. (optional) ``--narration-voice`` →
        :mod:`src.finalize_narration_voice` → ``final_with_voice.mp4``
    7. ``./scripts/verify_final_video.sh <topic> final_mix.mp4``

CLI:

    python -m src.auto_video --topic watergate
    python -m src.auto_video --topic watergate --narration-voice
    python -m src.auto_video --topic watergate --no-download
    python -m src.auto_video --topic watergate --skip-verify
    python -m src.auto_video --topic watergate --force-timeline
    python -m src.auto_video --topic watergate --max-video-copy 4

Each step prints a clear ``[auto_video step N/7]`` banner. The first failing
step aborts the run with a non-zero exit code; the final ``final_mix.mp4``
absolute path is printed on success.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

TOTAL_STEPS = 7


def _root() -> Path:
    return Path(
        os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")
    ).resolve()


def _log(msg: str, *, step: Optional[int] = None) -> None:
    if step is None:
        prefix = "[auto_video]"
    else:
        prefix = f"[auto_video step {step}/{TOTAL_STEPS}]"
    print(f"{prefix} {msg}", flush=True)


def _banner(step: int, title: str) -> None:
    bar = "=" * 60
    _log(bar, step=step)
    _log(title, step=step)
    _log(bar, step=step)


def _run(cmd: Sequence[str], *, step: int, cwd: Path) -> int:
    """Run a subprocess, streaming stdout/stderr, return exit code."""
    pretty = " ".join(str(c) for c in cmd)
    _log(f"$ {pretty}", step=step)
    try:
        r = subprocess.run(list(cmd), cwd=str(cwd))
    except FileNotFoundError as e:
        _log(f"command not found: {e}", step=step)
        return 127
    if r.returncode != 0:
        _log(f"FAIL (exit={r.returncode}): {pretty}", step=step)
    return r.returncode


def _list_mp4(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(
        [p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"],
        key=lambda x: x.name,
    )


def _banner_extra(title: str) -> None:
    bar = "=" * 60
    print(f"[auto_video] {bar}", flush=True)
    print(f"[auto_video] {title}", flush=True)
    print(f"[auto_video] {bar}", flush=True)


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def step1_check_script(root: Path, topic: str) -> int:
    _banner(1, "check brief/narration_script.txt")
    p = root / "topics" / topic / "brief" / "narration_script.txt"
    if not p.is_file():
        _log(f"missing required file: {p}", step=1)
        _log(
            "hint: produce a narration script first, e.g. via the production "
            "CLI (`python -m src.production.cli --topic <topic> --generate-scripts`).",
            step=1,
        )
        return 2
    size = p.stat().st_size
    _log(f"OK: {p} ({size} bytes)", step=1)
    return 0


def step2_selector(root: Path, topic: str, *, no_download: bool) -> int:
    _banner(2, "media_sources.selector")
    _log("[MEDIA PRIORITY] auto_download_first=true", step=2)
    # Explicit key hints (avoid silent confusion when sources return 0).
    if not (os.environ.get("PEXELS_API_KEY") or "").strip():
        _log("WARN: missing PEXELS_API_KEY (Pexels downloads will be skipped)", step=2)
    if not (os.environ.get("PIXABAY_API_KEY") or "").strip():
        _log("WARN: missing PIXABAY_API_KEY (Pixabay downloads will be skipped)", step=2)
    use_dvids = (os.environ.get("USE_DVIDS", "") or "").strip().lower() in ("1", "true", "yes")
    if use_dvids and not (os.environ.get("DVIDS_API_KEY") or "").strip():
        _log(
            "WARN: USE_DVIDS=true but missing DVIDS_API_KEY (DVIDS may return HTTP 403).",
            step=2,
        )
    # For this auto flow we only include DVIDS when explicitly enabled.
    srcs = "pexels,pixabay" + (",dvids" if use_dvids else "")
    cmd = [
        sys.executable,
        "-m",
        "src.integrations.media_sources.selector",
        "--topic",
        topic,
        "--source",
        srcs,
    ]
    if no_download:
        cmd.append("--no-download")
    return _run(cmd, step=2, cwd=root)


def step3_promote(root: Path, topic: str) -> int:
    _banner(3, "media_sources.promote → video/")
    cmd = [
        sys.executable,
        "-m",
        "src.integrations.media_sources.promote",
        "--topic",
        topic,
        "--dest",
        "video",
    ]
    return _run(cmd, step=3, cwd=root)


def step4_seed_video(root: Path, topic: str, *, max_copy: int) -> int:
    _banner(4, "ensure topics/<topic>/video/ has mp4s")
    tdir = root / "topics" / topic
    video_dir = tdir / "video"
    envato_dir = tdir / "envato"
    video_dir.mkdir(parents=True, exist_ok=True)

    existing = _list_mp4(video_dir)
    # IMPORTANT: do not skip selector/download just because video/ already has material.
    # This step is only about filling video/ when it is still empty after promotion.
    if existing:
        _log(f"video/ already has {len(existing)} mp4(s).", step=4)
        return 0

    pool = _list_mp4(envato_dir)
    if not pool:
        _log(
            f"video/ is empty AND envato/ has no mp4 ({envato_dir}). "
            "Cannot seed video/ for mix_engine. "
            "Run selector/promote first or place real LTX renders under video/.",
            step=4,
        )
        return 3

    n = min(max_copy, len(pool))
    if n <= 0:
        _log(f"--max-video-copy={max_copy} is non-positive; refusing to copy.", step=4)
        return 4

    _log(
        f"video/ is empty; copying first {n} mp4 from envato/ → video/ "
        f"(max-video-copy={max_copy}, envato pool={len(pool)})",
        step=4,
    )
    copied: list[str] = []
    for src in pool[:n]:
        dst = video_dir / src.name
        if dst.exists():
            _log(f"skip (exists): {dst.name}", step=4)
            continue
        try:
            shutil.copy2(src, dst)
        except OSError as e:
            _log(f"copy failed {src} → {dst}: {e}", step=4)
            return 5
        copied.append(dst.name)
        _log(f"copied: envato/{src.name} → video/{dst.name}", step=4)
    _log(f"done; {len(copied)} file(s) seeded into video/.", step=4)
    return 0


def step5_timeline(root: Path, topic: str, *, force: bool) -> int:
    _banner(5, "mix_engine.generate_timeline")
    cmd = [
        sys.executable,
        "-m",
        "src.mix_engine.generate_timeline",
        "--topic",
        topic,
    ]
    if force:
        cmd.append("--force")
    return _run(cmd, step=5, cwd=root)


def step6_mix(root: Path, topic: str) -> int:
    _banner(6, "run_pipeline --only-main (mix_engine.build_video)")
    cmd = [
        sys.executable,
        "-m",
        "src.run_pipeline",
        "--topic",
        topic,
        "--only-main",
    ]
    return _run(cmd, step=6, cwd=root)


def step7_verify(root: Path, topic: str) -> int:
    _banner(7, "verify_final_video.sh final_mix.mp4")
    script = root / "scripts" / "verify_final_video.sh"
    if not script.is_file():
        _log(f"missing: {script}", step=7)
        return 6
    if not os.access(script, os.X_OK):
        _log(f"not executable: {script} (chmod +x required)", step=7)
        return 7
    cmd = [str(script), topic, "final_mix.mp4"]
    return _run(cmd, step=7, cwd=root)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    topic: str,
    *,
    root: Optional[Path] = None,
    no_download: bool = False,
    force_timeline: bool = True,
    max_video_copy: int = 8,
    skip_verify: bool = False,
    narration_voice: bool = False,
) -> int:
    r = (root or _root()).resolve()
    topic = topic.strip()
    if not topic:
        _log("topic must be non-empty.")
        return 2

    _log(f"root={r}")
    _log(f"topic={topic}")
    _log(
        f"flags: no_download={no_download} force_timeline={force_timeline} "
        f"max_video_copy={max_video_copy} skip_verify={skip_verify} "
        f"narration_voice={narration_voice}"
    )

    # Pre-capture counts for logging / fallback accounting.
    tdir = r / "topics" / topic
    assets_raw = tdir / "assets" / "raw"
    video_dir = tdir / "video"
    raw_before = len(list(assets_raw.glob("*"))) if assets_raw.is_dir() else 0
    video_before = len([p for p in video_dir.glob("*.mp4")]) if video_dir.is_dir() else 0

    rc = step1_check_script(r, topic)
    if rc:
        return rc
    rc = step2_selector(r, topic, no_download=no_download)
    if rc:
        return rc
    raw_after = len(list(assets_raw.glob("*"))) if assets_raw.is_dir() else 0
    _log(f"[MEDIA AUTO] downloaded_count={max(0, raw_after - raw_before)}", step=2)
    rc = step3_promote(r, topic)
    if rc:
        return rc
    video_after = len([p for p in video_dir.glob("*.mp4")]) if video_dir.is_dir() else 0
    promoted_added = max(0, video_after - video_before)
    local_used = video_before if promoted_added == 0 else 0
    _log(f"[MEDIA LOCAL FALLBACK] used_count={local_used}", step=3)
    rc = step4_seed_video(r, topic, max_copy=max_video_copy)
    if rc:
        return rc
    rc = step5_timeline(r, topic, force=force_timeline)
    if rc:
        return rc
    # Final summary: manifest entries, raw count, video mp4 count, timeline clip count.
    manifest_path = tdir / "assets" / "media_manifest.json"
    manifest_entries = 0
    try:
        if manifest_path.is_file():
            import json as _json

            m = _json.loads(manifest_path.read_text(encoding="utf-8"))
            segs = (m.get("segments") or {}) if isinstance(m, dict) else {}
            if isinstance(segs, dict):
                manifest_entries = sum(len(v or []) for v in segs.values() if isinstance(v, list))
    except Exception:
        manifest_entries = -1
    raw_total = len(list(assets_raw.glob("*"))) if assets_raw.is_dir() else 0
    video_total = len([p for p in video_dir.glob("*.mp4")]) if video_dir.is_dir() else 0
    timeline_path = tdir / "mix" / "timeline.json"
    timeline_n = 0
    try:
        if timeline_path.is_file():
            import json as _json

            tl = _json.loads(timeline_path.read_text(encoding="utf-8"))
            if isinstance(tl, list):
                timeline_n = len(tl)
    except Exception:
        timeline_n = -1
    _log(
        f"[MEDIA SUMMARY] manifest_entries={manifest_entries} raw_files={raw_total} "
        f"video_mp4={video_total} timeline_clips={timeline_n}"
    )
    rc = step6_mix(r, topic)
    if rc:
        return rc
    if narration_voice:
        from src.finalize_narration_voice import run_finalize

        _banner_extra("narration TTS (ElevenLabs) + final_with_voice.mp4")
        tr = run_finalize(r, topic, remux_only=False)
        if tr != 0:
            _log(
                f"narration finalize failed (exit {tr}): set ELEVENLABS_API_KEY, "
                "ELEVENLABS_VOICE_ID; ensure requests is installed."
            )
            return tr
    if skip_verify:
        _log("skip_verify=True → skipping step 7.", step=7)
    else:
        rc = step7_verify(r, topic)
        if rc:
            return rc

    final = r / "topics" / topic / "output" / "final_mix.mp4"
    _log("=" * 60)
    _log("ALL STEPS DONE.")
    _log(f"final_mix.mp4 = {final}")
    if final.is_file():
        _log(f"size = {final.stat().st_size} bytes")
    if narration_voice:
        fvoice = r / "topics" / topic / "output" / "final_with_voice.mp4"
        if fvoice.is_file():
            _log(f"final_with_voice.mp4 = {fvoice} ({fvoice.stat().st_size} bytes)")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.auto_video",
        description=(
            "One-shot pipeline: brief/narration_script.txt → media selector → "
            "promote → seed video/ → mix timeline → mix build → verify. "
            "Calls existing CLIs only; does not modify mix_engine / production / "
            "presenter_pipeline."
        ),
    )
    p.add_argument("--topic", required=True, help="Topic slug under topics/.")
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="StateVerge root (default: $STATEVERGE_ROOT or ~/StateVerge).",
    )
    p.add_argument(
        "--no-download",
        action="store_true",
        help="Pass --no-download to media selector (manifest-only, no fetch).",
    )
    p.add_argument(
        "--force-timeline",
        dest="force_timeline",
        action="store_true",
        default=True,
        help=(
            "Pass --force to generate_timeline (default on; matches the auto-flow "
            "spec which always rebuilds the timeline)."
        ),
    )
    p.add_argument(
        "--no-force-timeline",
        dest="force_timeline",
        action="store_false",
        help="Do NOT pass --force to generate_timeline.",
    )
    p.add_argument(
        "--max-video-copy",
        type=int,
        default=8,
        help=(
            "If topics/<topic>/video/ is empty, cap how many .mp4 to copy "
            "from topics/<topic>/envato/ into video/ as LTX seeds. Default 8."
        ),
    )
    p.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip the final verify_final_video.sh check.",
    )
    p.add_argument(
        "--narration-voice",
        action="store_true",
        help=(
            "After final_mix, read brief/narration_script.txt → ElevenLabs → "
            "audio/voice.wav, mux to output/final_with_voice.mp4 (requires "
            "ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, requests, ffmpeg)."
        ),
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return run(
            args.topic,
            root=args.root,
            no_download=bool(args.no_download),
            force_timeline=bool(args.force_timeline),
            max_video_copy=int(args.max_video_copy),
            skip_verify=bool(args.skip_verify),
            narration_voice=bool(args.narration_voice),
        )
    except KeyboardInterrupt:
        _log("interrupted by user.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
