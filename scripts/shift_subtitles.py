"""
Shift the timestamps of an existing subtitle (.srt) file and re-burn into a
side-by-side video output. Does NOT touch the original .srt / .ass / .mp4.

Reuses helpers from scripts/stateverge_oneclick_video.py:
    - parse_srt_to_cues()
    - write_srt()
    - find_chinese_font()
    - convert_srt_to_ass()
    - burn_ass_subtitles()       (and its _filter_path_escape internally)
    - _build_fontsdir()
    - ffprobe_summary()
    - log() / section()

Usage:
    python scripts/shift_subtitles.py \\
      --input  topics/ai_future_cn/subtitles/subtitles.srt \\
      --output topics/ai_future_cn/subtitles/subtitles_shifted_minus_4s.srt \\
      --shift -4.0

Behavior:
    - shift < 0  -> subtitles arrive earlier
    - shift > 0  -> subtitles arrive later
    - new_start clamped to >= 0
    - new_end   clamped to >= new_start + 0.25
    - Original .srt is read-only.
    - New .srt and .ass are written next to the --output path.
    - Burned mp4 is written to topics/<slug>/output/final_video_with_<srt_stem>.mp4
      (override with --burned-output).
    - Source video defaults to topics/<slug>/output/final_video.mp4
      (override with --in-video).
    - On success, opens the resulting mp4 in the default player.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Make sibling oneclick script importable
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from stateverge_oneclick_video import (  # noqa: E402
    Cue,
    _build_fontsdir,
    burn_ass_subtitles,
    convert_srt_to_ass,
    ffprobe_summary,
    find_chinese_font,
    fmt_dur,
    log,
    parse_srt_to_cues,
    repo_root,
    section,
    write_srt,
)

LOG_PREFIX = "[shift_subs]"


def shift_cues(cues: list[Cue], shift: float, min_dur: float = 0.25) -> list[Cue]:
    out: list[Cue] = []
    for start, end, text in cues:
        new_start = start + shift
        new_end = end + shift
        if new_start < 0.0:
            new_start = 0.0
        if new_end < new_start + min_dur:
            new_end = new_start + min_dur
        out.append((new_start, new_end, text))
    return out


def derive_slug(srt_path: Path, root: Path) -> str | None:
    try:
        rel = srt_path.resolve().relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "topics":
        return parts[1]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Shift an SRT's timestamps, regenerate ASS, re-burn into a side-by-side "
            "video. Original files are NOT modified."
        )
    )
    ap.add_argument("--input", required=True, type=Path,
                    help="Source UTF-8 .srt to shift.")
    ap.add_argument("--output", required=True, type=Path,
                    help="Destination .srt path for the shifted version.")
    ap.add_argument("--shift", required=True, type=float,
                    help="Seconds to shift (negative = earlier, positive = later).")
    ap.add_argument("--in-video", type=Path, default=None,
                    help="Source video to burn into. Defaults to "
                         "topics/<slug>/output/final_video.mp4.")
    ap.add_argument("--burned-output", type=Path, default=None,
                    help="Override the burned mp4 output path. Default: "
                         "topics/<slug>/output/final_video_with_<srt_stem>.mp4.")
    ap.add_argument("--no-open", action="store_true",
                    help="Don't auto-open the burned mp4 at the end.")
    ns = ap.parse_args(argv)

    in_srt: Path = ns.input.resolve()
    out_srt: Path = ns.output.resolve()
    shift_sec: float = float(ns.shift)

    if not in_srt.is_file():
        print(f"{LOG_PREFIX} error: input srt not found at {in_srt}",
              file=sys.stderr)
        return 1
    if in_srt == out_srt:
        print(f"{LOG_PREFIX} error: --input and --output must differ "
              f"(refusing to overwrite original)",
              file=sys.stderr)
        return 1

    root = repo_root()
    slug = derive_slug(out_srt, root) or derive_slug(in_srt, root)
    if not slug:
        print(f"{LOG_PREFIX} error: cannot derive topic slug from path; "
              f"expected topics/<slug>/subtitles/...srt",
              file=sys.stderr)
        return 1

    # Resolve video paths
    if ns.in_video is None:
        in_video = (root / "topics" / slug / "output" / "final_video.mp4").resolve()
    else:
        in_video = ns.in_video.resolve()

    if ns.burned_output is None:
        burned_out = (
            root / "topics" / slug / "output" / f"final_video_with_{out_srt.stem}.mp4"
        ).resolve()
    else:
        burned_out = ns.burned_output.resolve()

    if not in_video.is_file():
        print(f"{LOG_PREFIX} error: source video not found at {in_video}",
              file=sys.stderr)
        return 1

    section("shift", "shift srt timestamps")
    log("shift", f"input  = {in_srt.relative_to(root)}")
    log("shift", f"output = {out_srt.relative_to(root)}")
    log("shift", f"shift  = {shift_sec:+.3f}s "
                 f"({'earlier' if shift_sec < 0 else 'later' if shift_sec > 0 else 'no-op'})")

    raw = in_srt.read_text(encoding="utf-8")
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff")
    cues = parse_srt_to_cues(raw)
    if not cues:
        print(f"{LOG_PREFIX} error: parsed 0 cues from {in_srt}; "
              f"is it a valid SRT?",
              file=sys.stderr)
        return 1

    shifted = shift_cues(cues, shift_sec)
    n_clamped_start = sum(
        1 for (s_old, _, _), (s_new, _, _) in zip(cues, shifted, strict=True)
        if shift_sec < 0 and s_new == 0.0 and (s_old + shift_sec) < 0.0
    )
    write_srt(shifted, out_srt)
    first = shifted[0]
    last = shifted[-1]
    log("shift",
        f"wrote {out_srt.relative_to(root)}  cues={len(shifted)}  "
        f"clamped_to_zero={n_clamped_start}  "
        f"first={first[0]:.3f}-{first[1]:.3f}  "
        f"last={last[0]:.3f}-{last[1]:.3f}")

    section("ass", "convert shifted SRT -> ASS")
    out_ass = out_srt.with_suffix(".ass").resolve()

    font_path, font_name = find_chinese_font()
    if font_path is None:
        log("ass", "WARN no CJK font file found; libass will fall back")
    else:
        log("ass", f"chosen font = {font_path}  ass_fontname={font_name!r}")

    fonts_dir = _build_fontsdir(slug, root, font_path)
    if fonts_dir is not None:
        try:
            log("ass", f"fontsdir = {fonts_dir.relative_to(root)}/  "
                       f"contents={[p.name for p in fonts_dir.iterdir()]}")
        except ValueError:
            log("ass", f"fontsdir = {fonts_dir}")

    n_dlg = convert_srt_to_ass(out_srt, out_ass, font_name)
    log("ass", f"wrote {out_ass.relative_to(root)}  dialogues={n_dlg}  "
               f"fontname={font_name!r}")

    section("burn", "ass= filter w/ fontsdir")
    log("burn", f"in_video    = {in_video.relative_to(root)}")
    log("burn", f"burned_out  = {burned_out.relative_to(root)}")

    if burned_out.exists():
        # Refuse to silently overwrite — caller can rm it explicitly.
        # For safety we DON'T delete; we just bail with a clear error.
        if burned_out == (root / "topics" / slug / "output" / "final_video_with_subtitles.mp4").resolve():
            print(
                f"{LOG_PREFIX} error: refusing to overwrite original "
                f"final_video_with_subtitles.mp4 (specify a different "
                f"--burned-output if you really mean it)",
                file=sys.stderr,
            )
            return 1
        log("burn", f"NOTE existing {burned_out.name} will be overwritten")

    burned_out.parent.mkdir(parents=True, exist_ok=True)
    ok, stderr = burn_ass_subtitles(
        in_video=in_video,
        ass_path=out_ass,
        fonts_dir=fonts_dir,
        out_video=burned_out,
    )
    if not ok or not burned_out.is_file():
        print(f"{LOG_PREFIX} error: burn failed (see stderr above). "
              f"Original final_video.mp4 is untouched.",
              file=sys.stderr)
        return 2

    info = ffprobe_summary(burned_out)
    log("burn",
        f"OK  {burned_out.relative_to(root)}  "
        f"dur={fmt_dur(info['duration_sec'])}  "
        f"{info['width']}x{info['height']}@{info['fps']:.2f}  "
        f"v={info['video_codec']}  a={info['audio_codec'] or 'none'}  "
        f"size={info['size_bytes'] / (1024 * 1024):.1f} MiB")

    if not ns.no_open:
        try:
            subprocess.Popen(
                ["open", str(burned_out)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log("open", f"requested open {burned_out.name}")
        except OSError as e:
            log("open", f"could not auto-open: {e}")

    print(f"\n{LOG_PREFIX} DONE  shift={shift_sec:+.3f}s  "
          f"-> {burned_out.relative_to(root)}\n", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
