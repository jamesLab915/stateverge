"""
Re-align subtitles by transcribing the FINAL muxed audio (`final_video.mp4`)
with a real ASR backend. Strict rule: NO naive timing fallback.

Touches ONLY:
    topics/<slug>/subtitles/realigned/
    topics/<slug>/output/final_video_realigned_subtitles.mp4

Will refuse to overwrite:
    topics/<slug>/subtitles/subtitles.srt
    topics/<slug>/subtitles/subtitles.ass
    topics/<slug>/output/final_video_with_subtitles.mp4

Backends, in priority order:
    1. openai-whisper Python CLI    (`whisper`)
    2. faster-whisper Python module (`from faster_whisper import WhisperModel`)
    3. whisper.cpp                  (`whisper-cli` / `whisper-cpp` / `main`)
       — requires a usable ggml model file (NOT the for-tests-tiny one)

If none is available, prints a clear remediation message and exits 2.

Usage:
    python scripts/realign_subtitles_from_final_audio.py --topic ai_future_cn

Optional flags:
    --model-size  small|medium|large-v3   (for openai-whisper / faster-whisper)
    --whisper-model PATH                  (explicit ggml model for whisper.cpp)
    --language    Chinese                 (default: Chinese)
    --no-open                             (don't auto-open the burned mp4)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Make sibling oneclick script importable
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from stateverge_oneclick_video import (  # noqa: E402
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
    write_utf8_text,
)

LOG_PREFIX = "[realign]"

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

# Models that count as "no real model" — refuse to use these.
TINY_TEST_MODEL_NAMES = {"for-tests-ggml-tiny.bin", "ggml-tiny.bin"}

# Where to look for whisper.cpp ggml models, in priority order.
GGML_MODEL_CANDIDATES = [
    "~/whisper.cpp/models/ggml-large-v3.bin",
    "~/whisper.cpp/models/ggml-large-v3-q5_0.bin",
    "~/whisper.cpp/models/ggml-medium.bin",
    "~/whisper.cpp/models/ggml-medium-q5_0.bin",
    "~/whisper.cpp/models/ggml-small.bin",
    "~/whisper.cpp/models/ggml-base.bin",
    "~/.cache/whisper.cpp/ggml-large-v3.bin",
    "~/.cache/whisper.cpp/ggml-medium.bin",
    "~/.cache/whisper.cpp/ggml-small.bin",
    "~/.cache/whisper.cpp/ggml-base.bin",
    "/opt/homebrew/share/whisper.cpp/ggml-medium.bin",
    "/opt/homebrew/share/whisper.cpp/ggml-small.bin",
    "/opt/homebrew/share/whisper.cpp/ggml-base.bin",
    "/opt/homebrew/share/whisper-cpp/ggml-medium.bin",
    "/opt/homebrew/share/whisper-cpp/ggml-small.bin",
    "/opt/homebrew/share/whisper-cpp/ggml-base.bin",
]


def _resolve_first(paths: list[str]) -> Path | None:
    for s in paths:
        p = Path(s).expanduser()
        if p.is_file() and p.name not in TINY_TEST_MODEL_NAMES:
            return p
    return None


def detect_openai_whisper() -> Path | None:
    cli = shutil.which("whisper")
    if not cli:
        return None
    # `whisper` is also the name of openai-whisper's CLI. Sanity check: verify
    # `--help` mentions language/output_dir flags. If it errors out, treat as
    # unavailable.
    try:
        out = subprocess.run(
            [cli, "--help"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    blob = (out.stdout or "") + (out.stderr or "")
    if "--output_dir" not in blob and "--output_format" not in blob:
        return None
    return Path(cli)


def detect_faster_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def detect_whisper_cpp(explicit_model: Path | None = None) -> tuple[Path, Path] | None:
    cli = (
        shutil.which("whisper-cli")
        or shutil.which("whisper-cpp")
        or shutil.which("main")
    )
    if not cli:
        return None
    cli_path = Path(cli)

    if explicit_model is not None:
        ep = explicit_model.expanduser()
        if ep.is_file():
            return cli_path, ep

    env_model = os.environ.get("WHISPER_MODEL", "").strip()
    if env_model:
        mp = Path(env_model).expanduser()
        if mp.is_file() and mp.name not in TINY_TEST_MODEL_NAMES:
            return cli_path, mp

    model = _resolve_first(GGML_MODEL_CANDIDATES)
    if model is None:
        return None
    return cli_path, model


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------


def extract_audio_for_asr(in_video: Path, out_wav: Path) -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(in_video),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Backend invocations
# ---------------------------------------------------------------------------


def transcribe_openai_whisper(
    cli: Path,
    wav: Path,
    out_dir: Path,
    language: str,
    model_size: str,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(cli),
        str(wav),
        "--language", language,
        "--task", "transcribe",
        "--output_format", "srt",
        "--output_dir", str(out_dir),
        "--model", model_size,
    ]
    log("asr", f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"openai-whisper exited {proc.returncode}; see stderr above"
        )
    srt_path = out_dir / f"{wav.stem}.srt"
    if not srt_path.is_file():
        raise RuntimeError(f"openai-whisper did not produce {srt_path}")
    return srt_path


_FW_LANG_MAP = {
    "chinese": "zh", "zh": "zh", "zh-cn": "zh", "zh_cn": "zh",
    "english": "en", "en": "en",
}


def transcribe_faster_whisper(
    wav: Path,
    out_dir: Path,
    language: str,
    model_size: str,
) -> Path:
    from faster_whisper import WhisperModel

    out_dir.mkdir(parents=True, exist_ok=True)
    lang = _FW_LANG_MAP.get(language.lower(), language.lower())
    log("asr", f"faster-whisper model_size={model_size!r} lang={lang!r} "
               f"compute_type=auto")
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    segments_iter, info = model.transcribe(
        str(wav),
        language=lang,
        task="transcribe",
        vad_filter=True,
        word_timestamps=False,
    )
    log("asr", f"detected_language={info.language!r} "
               f"prob={info.language_probability:.2f} "
               f"duration={info.duration:.1f}s")
    cues = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if not text:
            continue
        cues.append((float(seg.start), float(seg.end), text))
    if not cues:
        raise RuntimeError("faster-whisper produced 0 segments")
    srt_path = out_dir / f"{wav.stem}.srt"
    write_srt(cues, srt_path)
    return srt_path


def transcribe_whisper_cpp(
    cli: Path,
    model: Path,
    wav: Path,
    out_dir: Path,
    language: str,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_base = out_dir / wav.stem
    lang = _FW_LANG_MAP.get(language.lower(), language.lower())
    cmd = [
        str(cli),
        "-m", str(model),
        "-f", str(wav),
        "-l", lang,
        "-osrt",
        "-of", str(out_base),
        "-pp",
        "-t", "8",
    ]
    log("asr", f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"whisper-cli exited {proc.returncode}; see stderr above"
        )
    srt_path = Path(str(out_base) + ".srt")
    if not srt_path.is_file():
        raise RuntimeError(f"whisper-cli did not produce {srt_path}")
    return srt_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


REMEDIATION_MSG = """
[realign] FATAL: no usable Whisper backend on this machine.

    没有可用 whisper，不能做准确字幕。不要继续用 naive 时间轴。

Detected:
    openai-whisper (Python `whisper` CLI) : {oa}
    faster-whisper (Python module)        : {fw}
    whisper.cpp    (`whisper-cli` etc.)   : {cpp}
    whisper.cpp ggml model                : {model}

To fix, install ONE of the following:

  (A) Python openai-whisper (recommended on Apple Silicon, GPU via MLX is
      optional). NOTE: pulls torch (~2-3 GB).
        ./.venv/bin/pip install -U openai-whisper

  (B) Python faster-whisper (CPU/CTranslate2). Smaller install footprint.
        ./.venv/bin/pip install -U faster-whisper
        # then re-run with: --model-size medium

  (C) whisper.cpp ggml model for the existing `whisper-cli` binary
      (already installed at /opt/homebrew/bin/whisper-cli).
        mkdir -p ~/whisper.cpp/models
        curl -L -o ~/whisper.cpp/models/ggml-medium.bin \\
          https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin
        # then re-run; this script auto-finds it.

Then re-run:
    python scripts/realign_subtitles_from_final_audio.py --topic {slug}

Refusing to fall back to naive subtitles. Exiting (code 2).
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Realign subtitles by re-transcribing the final muxed "
                    "audio with a real ASR backend. NO naive fallback."
    )
    ap.add_argument("--topic", required=True,
                    help="Topic slug, e.g. ai_future_cn.")
    ap.add_argument("--in-video", type=Path, default=None,
                    help="Source video. Default: topics/<slug>/output/final_video.mp4")
    ap.add_argument("--language", default="Chinese",
                    help="ASR language. Default: Chinese.")
    ap.add_argument("--model-size", default="medium",
                    help="Model size for openai-whisper / faster-whisper "
                         "(tiny|base|small|medium|large-v3). Default: medium.")
    ap.add_argument("--whisper-model", type=Path, default=None,
                    help="Explicit ggml model file for whisper.cpp.")
    ap.add_argument("--prefer-backend",
                    choices=["openai", "faster", "cpp", "auto"],
                    default="auto",
                    help="Force a backend. Default: auto (openai>faster>cpp).")
    ap.add_argument("--no-open", action="store_true",
                    help="Don't auto-open the burned mp4.")
    ns = ap.parse_args(argv)

    slug: str = ns.topic
    root = repo_root()

    in_video: Path = (
        ns.in_video.resolve() if ns.in_video is not None
        else (root / "topics" / slug / "output" / "final_video.mp4").resolve()
    )

    realigned_dir = (root / "topics" / slug / "subtitles" / "realigned").resolve()
    out_video = (
        root / "topics" / slug / "output" / "final_video_realigned_subtitles.mp4"
    ).resolve()

    # Hard refuse to clobber originals.
    forbidden = {
        (root / "topics" / slug / "subtitles" / "subtitles.srt").resolve(),
        (root / "topics" / slug / "subtitles" / "subtitles.ass").resolve(),
        (root / "topics" / slug / "output" / "final_video_with_subtitles.mp4").resolve(),
    }
    if out_video in forbidden:
        print(f"{LOG_PREFIX} refuse to overwrite original {out_video.name}",
              file=sys.stderr)
        return 1

    section("input", "validate inputs")
    if not in_video.is_file():
        print(f"{LOG_PREFIX} error: missing source video {in_video}",
              file=sys.stderr)
        return 1
    log("input", f"in_video      = {in_video.relative_to(root)}")
    log("input", f"realigned_dir = {realigned_dir.relative_to(root)}/")
    log("input", f"out_video     = {out_video.relative_to(root)}")

    realigned_dir.mkdir(parents=True, exist_ok=True)

    # ---------- Backend detection ----------
    section("asr", "detect whisper backends")
    oa_cli = detect_openai_whisper()
    fw_ok = detect_faster_whisper()
    cpp_pair = detect_whisper_cpp(ns.whisper_model)

    log("asr", f"openai-whisper CLI : {oa_cli or 'NOT FOUND'}")
    log("asr", f"faster-whisper py  : {'AVAILABLE' if fw_ok else 'NOT FOUND'}")
    if cpp_pair is not None:
        log("asr", f"whisper.cpp CLI    : {cpp_pair[0]}")
        log("asr", f"whisper.cpp model  : {cpp_pair[1]}")
    else:
        # Probe just the CLI separately for diagnostics
        cli_only = (
            shutil.which("whisper-cli")
            or shutil.which("whisper-cpp")
            or shutil.which("main")
        )
        log("asr",
            f"whisper.cpp CLI    : {cli_only or 'NOT FOUND'}; "
            f"model: NOT FOUND (or only test-tiny)")

    # ---------- Pick backend ----------
    chosen = None
    pref = ns.prefer_backend
    order = (
        ["openai", "faster", "cpp"] if pref == "auto" else [pref]
    )
    for b in order:
        if b == "openai" and oa_cli is not None:
            chosen = ("openai", oa_cli)
            break
        if b == "faster" and fw_ok:
            chosen = ("faster", None)
            break
        if b == "cpp" and cpp_pair is not None:
            chosen = ("cpp", cpp_pair)
            break

    if chosen is None:
        sys.stderr.write(REMEDIATION_MSG.format(
            oa=str(oa_cli) if oa_cli else "NOT FOUND",
            fw="AVAILABLE" if fw_ok else "NOT FOUND",
            cpp=str(
                shutil.which("whisper-cli")
                or shutil.which("whisper-cpp")
                or shutil.which("main")
                or "NOT FOUND"
            ),
            model="NOT FOUND" if cpp_pair is None else str(cpp_pair[1]),
            slug=slug,
        ))
        return 2

    backend_name = chosen[0]
    log("asr", f"=> selected backend: {backend_name!r}")

    # ---------- Extract audio ----------
    section("audio", "extract mono 16k WAV from final video")
    final_audio = realigned_dir / "final_audio.wav"
    extract_audio_for_asr(in_video, final_audio)
    aud_info = ffprobe_summary(final_audio)
    log("audio",
        f"wrote {final_audio.relative_to(root)}  "
        f"dur={fmt_dur(aud_info['duration_sec'])}  "
        f"a={aud_info['audio_codec']}  "
        f"size={aud_info['size_bytes'] / (1024 * 1024):.1f} MiB")

    # ---------- Transcribe ----------
    section("asr", f"transcribe via {backend_name}")
    raw_srt: Path
    if backend_name == "openai":
        raw_srt = transcribe_openai_whisper(
            chosen[1], final_audio, realigned_dir,
            ns.language, ns.model_size,
        )
    elif backend_name == "faster":
        raw_srt = transcribe_faster_whisper(
            final_audio, realigned_dir, ns.language, ns.model_size,
        )
    else:
        cli, model = chosen[1]
        raw_srt = transcribe_whisper_cpp(
            cli, model, final_audio, realigned_dir, ns.language,
        )

    # Normalize to canonical name (do NOT touch the originals elsewhere).
    realigned_srt = realigned_dir / "subtitles_realigned.srt"
    if raw_srt.resolve() != realigned_srt.resolve():
        text = raw_srt.read_text(encoding="utf-8", errors="replace")
        if text.startswith("\ufeff"):
            text = text.lstrip("\ufeff")
        write_utf8_text(realigned_srt, text)
        log("asr", f"copied {raw_srt.name} -> {realigned_srt.name}")

    cues = parse_srt_to_cues(realigned_srt.read_text(encoding="utf-8"))
    if not cues:
        print(f"{LOG_PREFIX} error: realigned SRT parsed 0 cues — aborting",
              file=sys.stderr)
        return 3
    first, last = cues[0], cues[-1]
    log("asr",
        f"realigned SRT: {realigned_srt.relative_to(root)}  "
        f"cues={len(cues)}  first={first[0]:.3f}-{first[1]:.3f}  "
        f"last={last[0]:.3f}-{last[1]:.3f}")

    # ---------- SRT -> ASS (reuse stable oneclick logic) ----------
    section("ass", "convert realigned SRT -> ASS")
    realigned_ass = realigned_dir / "subtitles_realigned.ass"

    font_path, font_name = find_chinese_font()
    if font_path is None:
        log("ass", "WARN no CJK font file found; libass will fall back")
    else:
        log("ass", f"chosen font = {font_path}  ass_fontname={font_name!r}")

    # Build a fonts dir local to the realigned subfolder so we don't touch
    # the original `_fonts/` next to subtitles.srt.
    local_fonts_dir = realigned_dir / "_fonts"
    if local_fonts_dir.exists():
        for child in local_fonts_dir.iterdir():
            try:
                child.unlink()
            except OSError:
                pass
    else:
        local_fonts_dir.mkdir(parents=True, exist_ok=True)
    fonts_dir: Path | None
    if font_path is not None:
        link_target = local_fonts_dir / font_path.name
        try:
            link_target.symlink_to(font_path)
        except FileExistsError:
            pass
        except OSError:
            shutil.copy2(font_path, link_target)
        fonts_dir = local_fonts_dir
        log("ass",
            f"fontsdir = {fonts_dir.relative_to(root)}/  "
            f"contents={[p.name for p in fonts_dir.iterdir()]}")
    else:
        fonts_dir = None

    n_dlg = convert_srt_to_ass(realigned_srt, realigned_ass, font_name)
    log("ass",
        f"wrote {realigned_ass.relative_to(root)}  dialogues={n_dlg}  "
        f"fontname={font_name!r}")

    # ---------- Burn ----------
    section("burn", "burn realigned ASS into a fresh mp4")
    if out_video.exists():
        log("burn", f"NOTE existing {out_video.name} will be overwritten")
    out_video.parent.mkdir(parents=True, exist_ok=True)
    log("burn", f"in_video   = {in_video.relative_to(root)}")
    log("burn", f"out_video  = {out_video.relative_to(root)}")

    ok, _ = burn_ass_subtitles(
        in_video=in_video,
        ass_path=realigned_ass,
        fonts_dir=fonts_dir,
        out_video=out_video,
    )
    if not ok or not out_video.is_file():
        print(f"{LOG_PREFIX} error: burn failed; original final_video.mp4 "
              f"is untouched.", file=sys.stderr)
        return 4

    info = ffprobe_summary(out_video)
    log("burn",
        f"OK  {out_video.relative_to(root)}  "
        f"dur={fmt_dur(info['duration_sec'])}  "
        f"{info['width']}x{info['height']}@{info['fps']:.2f}  "
        f"v={info['video_codec']}  a={info['audio_codec'] or 'none'}  "
        f"size={info['size_bytes'] / (1024 * 1024):.1f} MiB")

    # ---------- Open ----------
    if not ns.no_open:
        try:
            subprocess.Popen(
                ["open", str(out_video)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log("open", f"requested open {out_video.name}")
        except OSError as e:
            log("open", f"could not auto-open: {e}")

    # ---------- Final summary ----------
    print()
    print(f"{LOG_PREFIX} DONE")
    print(f"  whisper backend : {backend_name}")
    print(f"  extracted audio : {final_audio}")
    print(f"  realigned SRT   : {realigned_srt}")
    print(f"  realigned ASS   : {realigned_ass}")
    print(f"  burned video    : {out_video}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
