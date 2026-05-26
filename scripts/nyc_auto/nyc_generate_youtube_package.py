#!/usr/bin/env python3
"""Generate local YouTube upload draft assets (no external APIs)."""
from __future__ import annotations

import argparse
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import (  # noqa: E402
    NYC_ROOT,
    OUT_PACKAGES,
    ensure_nyc_dirs,
    load_source_json,
    log_lines,
    nyc_daily_log,
    path_matches_test_asset_marker,
    project_dir,
    run_ffprobe,
    summarize_probe,
)


def pick_font() -> str:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    return ""


def time_bucket_from_path(src: Path) -> str:
    parts = [p.lower() for p in src.parts]
    for key in ("morning", "day", "golden_hour", "night"):
        if key in parts:
            return key.replace("_", " ")
    return ""


def build_titles(
    topic: Optional[str],
    tb: str,
    minutes: float,
    pkg_type: str,
    imported_at: str,
) -> List[str]:
    mood = random.choice(["Relaxing", "Cinematic", "Ambient", "Immersive", "4K"])
    area = "NYC Manhattan"
    duration_lbl = f"{int(round(minutes))} Minutes" if minutes >= 1 else "Short Clip"
    topic_line = topic.strip() if topic else None

    templates_long: List[str] = []
    if topic_line:
        templates_long.append(f"{topic_line} | {mood} {area} City Video")
        templates_long.append(f"{topic_line} — Walking & Driving in Manhattan")
    else:
        templates_long.append(f"{mood} {area} — City Ambience & Night Lights")
        templates_long.append(f"{area} City Drive | {mood} Urban Soundscape")
    templates_long.append(
        f"{duration_lbl} {area} — Walking Tour & City Sounds ({imported_at[:10]})"
    )
    templates_long.append(f"Manhattan {mood} Streets | NYC 4K Travel Relaxation")
    templates_long.append(f"NYC {duration_lbl} — Calming City Atmosphere for Focus & Sleep")

    if pkg_type == "long":
        return templates_long[:5]

    seed = topic_line or f"{mood} Manhattan"
    hooks = [
        "POV:",
        "Wait for this skyline…",
        "NYC hits different at night —",
        "Hypnotic city lights —",
        "Turn up for this Manhattan glow —",
        "You can almost hear the traffic —",
        "Golden hour in NYC is unreal —",
        "Street-level energy in 4K —",
        "This is why people love New York —",
        "Cinematic NYC in under a minute —",
    ]
    shorts: List[str] = []
    for h in hooks[:10]:
        shorts.append(f"{h} {seed}")
    if topic_line:
        shorts[0] = f"{topic_line} | NYC Short #{random.randint(1, 99)}"
    return shorts[:10]


def build_description(pkg_type: str, minutes: float, tb: str) -> str:
    lines = [
        "Draft generated locally by StateVerge NYC_AUTO (edit before upload).",
        "",
        f"Approx. runtime reference: {minutes:.1f} minutes ({pkg_type}).",
        "",
        "Suggested themes: New York City, Manhattan, 4K urban footage, walking tour, driving POV,",
        "city ambience, relaxing background, study/focus visuals, NYC night lights, street sounds.",
        "",
        "Hashtags / keywords to weave in: #NYC #Manhattan #4K #CityWalk #UrbanAmbience",
        "#Relaxing #DrivingPOV #Travel #StreetPhotography #NewYorkCity",
    ]
    if tb:
        lines.insert(4, f"Detected bucket from ingest path: {tb}.")
    lines += ["", "Chapters, credits, and music licenses: add manually if required."]
    return "\n".join(lines)


def build_tags(minutes: float) -> str:
    pool = [
        "nyc",
        "new york city",
        "manhattan",
        "4k",
        "uhd",
        "city walk",
        "walking tour",
        "driving",
        "pov",
        "urban",
        "city ambience",
        "city sounds",
        "relaxing",
        "ambient",
        "cinematic",
        "travel",
        "usa",
        "timelapse mood",
        "night city",
        "golden hour",
        "street photography",
        "background video",
        "study with me",
        "virtual tour",
        f"longform {int(max(1, round(minutes)))} min",
        "youtube",
        "shorts",
        "content creator",
    ]
    k = min(30, max(20, len(pool)))
    picked = pool[:k]
    return ", ".join(picked)


def resolve_selected_video(pid: str, pkg_type: str) -> Optional[Path]:
    pdir = project_dir(pid)
    if pkg_type == "shorts":
        cand = pdir / "shorts" / "short_001.mp4"
        if cand.is_file():
            return cand
        shorts = sorted((pdir / "shorts").glob("short_*.mp4"))
        return shorts[0] if shorts else None
    lm = pdir / "output" / "long_music.mp4"
    if lm.is_file():
        return lm
    meta = load_source_json(pid)
    if meta:
        sp = Path(meta["source_path"])
        if sp.is_file():
            return sp
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--type", choices=("shorts", "long"), required=True)
    ap.add_argument("--title-topic", default=None)
    args = ap.parse_args()

    pid = args.project_id
    log_nyc = nyc_daily_log("nyc_youtube_pkg")

    def _l(msg: str) -> None:
        log_lines("nyc_youtube_pkg", [f"[{pid}] {msg}"], log_nyc)

    if not NYC_ROOT.is_dir():
        _l("ERROR NYC_ROOT not mounted")
        return 2

    ensure_nyc_dirs()
    meta = load_source_json(pid)
    if not meta:
        _l("ERROR missing source.json")
        return 1

    src = Path(meta["source_path"])
    sel = resolve_selected_video(pid, args.type)
    if not sel or not sel.is_file():
        _l("ERROR could not resolve output video for package")
        return 1

    if path_matches_test_asset_marker(pid, str(src), str(sel)):
        print("BLOCKED_TEST_OR_PLACEHOLDER_ASSET", file=sys.stderr)
        _l("BLOCKED_TEST_OR_PLACEHOLDER_ASSET")
        return 5

    duration = float(meta.get("duration") or 0)
    probe = run_ffprobe(sel)
    if probe:
        info = summarize_probe(probe)
        if info["duration"] > 0:
            duration = float(info["duration"])
    minutes = max(duration / 60.0, 0.5)
    tb = time_bucket_from_path(src)
    imported_at = str(meta.get("imported_at") or datetime.now().isoformat(timespec="seconds"))

    titles = build_titles(args.title_topic, tb, minutes, args.type, imported_at)
    desc = build_description(args.type, minutes, tb)
    tags = build_tags(minutes)

    pkg_root = OUT_PACKAGES / pid
    pkg_root.mkdir(parents=True, exist_ok=True)
    thumbs = project_dir(pid) / "thumbnails"
    thumbs.mkdir(parents=True, exist_ok=True)

    mid = max(0.0, duration / 2.0)
    base_jpg = thumbs / "thumbnail_base.jpg"
    text_jpg = thumbs / "thumbnail_text.jpg"

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-ss",
                f"{mid:.3f}",
                "-i",
                str(sel),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(base_jpg),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=600,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        _l(f"WARN thumbnail_base {exc}")

    caption = (args.title_topic or titles[0]).strip()[:80]
    font = pick_font()
    if base_jpg.is_file() and font:
        tf_path: Optional[str] = None
        try:
            tf = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                delete=False,
                encoding="utf-8",
            )
            tf_path = tf.name
            tf.write(caption)
            tf.close()
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-y",
                    "-i",
                    str(base_jpg),
                    "-vf",
                    (
                        f"drawtext=fontfile={font}:textfile={tf_path}:"
                        "fontcolor=white:fontsize=42:box=1:boxcolor=black@0.45:"
                        "boxborderw=12:x=(w-text_w)/2:y=h-text_h-48"
                    ),
                    "-frames:v",
                    "1",
                    str(text_jpg),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=600,
            )
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
            _l(f"WARN thumbnail_text {exc}")
        finally:
            if tf_path:
                try:
                    Path(tf_path).unlink()
                except OSError:
                    pass
    elif base_jpg.is_file():
        try:
            shutil.copyfile(base_jpg, text_jpg)
        except OSError:
            pass

    for name, body in (
        ("title_options.txt", "\n".join(titles)),
        ("description.txt", desc),
        ("tags.txt", tags),
        ("selected_video_path.txt", str(sel.resolve())),
    ):
        try:
            (pkg_root / name).write_text(
                body + ("\n" if not body.endswith("\n") else ""),
                encoding="utf-8",
            )
        except OSError as exc:
            _l(f"FAIL write {name}: {exc}")

    for img in (base_jpg, text_jpg):
        if img.is_file():
            try:
                subprocess.run(
                    ["cp", "-f", str(img), str(pkg_root / img.name)],
                    check=False,
                    timeout=60,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

    _l(f"OK package -> {pkg_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
