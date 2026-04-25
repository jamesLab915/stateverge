"""
Envato / packaging: title concat, **drawtext** lower-thirds, BGM, SFX, then mux.
Audio is built in **stages** (48 kHz stereo WAV) to keep ffmpeg graphs small; no single ``adelay``
+ ``atrim`` + deep ``amix`` filter graph. Does **not** modify :mod:`presenter_pipeline` internals.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, List, Optional

from . import asset_selector
from .brief_loader import ProductionBrief, load_brief
from .paths import topic_production_paths

LOG = logging.getLogger("production.packaging")

LOWER_THIRD_DEFAULT_S = 5.0


def _rel_under_root(root: Path, p: Path) -> str:
    p = p.resolve()
    r = root.resolve()
    return str(p.relative_to(r)).replace("\\", "/")


def _valid_rel_file(root: Path, s: object) -> bool:
    if not isinstance(s, str) or not s.strip():
        return False
    return _resolve(root, s).is_file()


def _brief_mood(bpath: Path) -> Optional[str]:
    """``envato.music_mood`` or top-level ``mood`` in ``production_brief.json``."""
    s: str = ""
    b: Optional[ProductionBrief] = load_brief(bpath) if bpath.is_file() else None
    if b is not None:
        s = str(b.envato.music_mood or "").strip()
    if bpath.is_file():
        with bpath.open("r", encoding="utf-8") as f:
            raw: Any = json.load(f)
        if isinstance(raw, dict) and isinstance(raw.get("mood"), str):
            t = raw["mood"].strip()
            if t:
                s = t
    return s or None


def _lower_thirds_drawtext_fallback(
    bpath: Path, topic: str
) -> list[dict[str, Any]]:
    if not bpath.is_file():
        ttitle = topic.replace("-", " ").title()
        return [
            {
                "time_sec": 0.0,
                "duration_sec": LOWER_THIRD_DEFAULT_S,
                "text": ttitle,
                "subtext": "Global Systems",
            }
        ]
    b = load_brief(bpath)
    title = topic.replace("-", " ").title()
    sub = "Global Systems"
    if b is not None:
        if getattr(b, "title", ""):
            title = str(b.title)
        if getattr(b, "style", ""):
            sub = str(b.style)[:80]
    return [
        {
            "time_sec": 0.0,
            "duration_sec": LOWER_THIRD_DEFAULT_S,
            "text": title,
            "subtext": sub,
        }
    ]


def _normalize_manifest_sfx(
    root: Path, items: List[Any]
) -> List[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        fp = it.get("file", "")
        if not (isinstance(fp, str) and _valid_rel_file(root, fp)):
            continue
        p = _resolve(root, fp)
        try:
            sfile = str(p.resolve().relative_to(root.resolve())).replace(
                "\\", "/"
            )
        except ValueError:
            sfile = str(fp)
        d2 = dict(it)
        d2["file"] = sfile
        out.append(d2)
    return out


def _manifest_sfx_list_or_none(
    root: Path, m0: dict[str, Any]
) -> Optional[List[dict[str, Any]]]:
    raw = m0.get("sfx")
    if not isinstance(raw, list) or not raw:
        return None
    norm = _normalize_manifest_sfx(root, raw)
    return norm if norm else None


def _run(args: list[str]) -> None:
    p = subprocess.run(args, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        raise RuntimeError(
            f"Command failed: {' '.join(args)!s}\n{err[:5000]}"
        )


def _ffprobe_duration(path: Path) -> float:
    path = Path(path)
    a = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    p = subprocess.run(a, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    d = __import__("json").loads(p.stdout)
    return round(float((d.get("format") or {}).get("duration", 0.0)), 3)


def _duration(path: Path) -> float:
    """Video/audio container duration in seconds (same as :func:`_ffprobe_duration`)."""
    return _ffprobe_duration(path)


def has_audio_stream(path: Path) -> bool:
    """
    True if *path* has an audio stream (ffprobe ``a:0``); uses non-empty stdout as signal.
    """
    path = Path(path)
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


def _audio_log(msg: str) -> None:
    print(f"[audio] {msg}", flush=True)


def _resolve(root: Path, s: str) -> Path:
    s = (s or "").strip()
    if not s:
        return Path()
    p = Path(s)
    return p if p.is_absolute() else (Path(root) / p).resolve()


def _silence_48k_wav(duration_sec: float, out: Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            str(duration_sec),
            "-c:a",
            "pcm_s16le",
            str(out),
        ]
    )


def _audio_to_48k_stereo(
    in_path: Path, out_path: Path, duration_sec: float
) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(in_path),
            "-t",
            str(duration_sec),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(out_path),
        ]
    )


def _build_music_48k(
    mus_path: Path, duration_sec: float, out_path: Path
) -> None:
    """Resample/trim BGM to *duration_sec*, 48 kHz stereo (loop short music)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(mus_path),
            "-t",
            str(duration_sec),
            "-ar",
            "48000",
            "-ac",
            "2",
            str(out_path),
        ]
    )


def _build_sfx_mix(
    work: Path,
    root: Path,
    sfx: List[dict[str, Any]],
    duration_sec: float,
) -> Path:
    _audio_log("building sfx track")
    out_mix = work / "sfx_mix.wav"
    delayed: list[Path] = []
    n = 0
    for sx in sfx:
        if not isinstance(sx, dict):
            continue
        src = _resolve(root, str(sx.get("file", "")))
        if not src.is_file():
            continue
        t_sec = float(sx.get("time_sec", 0) or 0.0)
        dms = max(0, int(t_sec * 1000.0))
        npath = work / f"sfx_{n:04d}_norm.wav"
        dpath = work / f"sfx_{n:04d}_delayed.wav"
        n += 1
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-ar",
                "48000",
                "-ac",
                "2",
                str(npath),
            ]
        )
        af = f"adelay={dms}|{dms},apad=whole_dur={float(duration_sec)}"
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(npath),
                "-af",
                af,
                "-c:a",
                "pcm_s16le",
                str(dpath),
            ]
        )
        delayed.append(dpath)

    if not delayed:
        _silence_48k_wav(duration_sec, out_mix)
        return out_mix

    if len(delayed) == 1:
        shutil.copy2(delayed[0], out_mix)
        return out_mix

    in_args: list[str] = ["ffmpeg", "-y"]
    for p in delayed:
        in_args += ["-i", str(p)]
    n_in = len(delayed)
    in_args += [
        "-filter_complex",
        f"amix=inputs={n_in}:normalize=0",
        "-c:a",
        "pcm_s16le",
        str(out_mix),
    ]
    _run(in_args)
    return out_mix


def _build_final_audio_three_way(
    mic_48: Path, music_48: Path, sfx_mix: Path, out_path: Path
) -> None:
    _audio_log("mixing final audio")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(mic_48),
            "-i",
            str(music_48),
            "-i",
            str(sfx_mix),
            "-filter_complex",
            "amix=inputs=3:normalize=0",
            "-c:a",
            "pcm_s16le",
            str(out_path),
        ]
    )


def default_packaging_manifest() -> dict[str, Any]:
    return {
        "title_opener": "assets/envato/title_openers/news_intro.mp4",
        "background_music": "assets/envato/music/dark.wav",
        "sfx": [
            {
                "time_sec": 12.5,
                "file": "assets/envato/sfx/impact.wav",
            }
        ],
        "lower_thirds": [
            {
                "time_sec": 0.0,
                "duration_sec": 5.0,
                "text": "STATEVERGE",
                "subtext": "Global Systems",
            }
        ],
        "transitions": True,
    }


def normalize_clip(
    input_path: Path,
    output_path: Path,
    keep_audio: bool = False,
) -> None:
    """
    Normalize a clip to 1920x1080, 30 fps, H.264, yuv420p; only ``0:v:0`` is mapped.

    If *keep_audio* is true, also map ``0:a:0`` and re-encode to AAC 48 kHz stereo.
    Otherwise video only (``-an``), e.g. for title openers.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p"
    )
    if keep_audio:
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
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
                str(out),
            ]
        )
    else:
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-map",
                "0:v:0",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )


def _concat_two_norm_video_only(
    path_a: Path, path_b: Path, out: Path
) -> None:
    """*path_a* + *path_b* (H.264 yuv420p) → *out* (video only, re-encoded for safety)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path_a),
            "-i",
            str(path_b),
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )


def _mic_from_main_with_opener_prefix(
    main_norm: Path, opener_sec: float, total_d: float, out_mic: Path
) -> None:
    """PCM mic: silence for *opener_sec* then main audio, padded to *total_d* seconds."""
    part = out_mic.parent / "mic_from_main_part.wav"
    out_mic = Path(out_mic)
    out_mic.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(main_norm),
            "-vn",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(part),
        ]
    )
    if opener_sec <= 0.001:
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(part),
                "-af",
                f"apad=whole_dur={float(total_d)}",
                "-c:a",
                "pcm_s16le",
                str(out_mic),
            ]
        )
    else:
        ap = out_mic.parent / "mic_silence_pad.wav"
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t",
                str(opener_sec),
                "-c:a",
                "pcm_s16le",
                str(ap),
            ]
        )
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(ap),
                "-i",
                str(part),
                "-filter_complex",
                "[0:a][1:a]concat=n=2:v=0:a=1,apad=whole_dur=%s[a]"
                % float(total_d),
                "-map",
                "[a]",
                "-c:a",
                "pcm_s16le",
                str(out_mic),
            ]
        )
        ap.unlink(missing_ok=True)
    part.unlink(missing_ok=True)


def _concat_opener(
    root: Path,
    opener: Path,
    main: Path,
    out: Path,
    *,
    reencode: bool = True,
    main_has_audio: bool = False,
) -> tuple[Optional[Path], float]:
    """
    Returns a path to a file from which the **main** segment audio can be demuxed
    (``main_norm`` or *out* when there is no opener) if *main_has_audio* is true,
    plus the opener's duration in seconds (for leading silence in the mic track).
    The caller may delete the returned main audio file after building ``mic.wav``
    (never delete *out* when it is the return path and equals final v0).
    """
    if not main.is_file():
        raise FileNotFoundError(f"Missing {main}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not opener.is_file():
        normalize_clip(main, out, keep_audio=main_has_audio)
        if main_has_audio:
            return (out.resolve(), 0.0)
        return (None, 0.0)
    work = out.parent
    opener_norm = work / "opener_norm.mp4"
    main_norm = work / "main_norm.mp4"
    d_op = 0.0
    try:
        normalize_clip(opener, opener_norm, keep_audio=False)
        normalize_clip(main, main_norm, keep_audio=main_has_audio)
        d_op = _ffprobe_duration(opener_norm)
        _concat_two_norm_video_only(opener_norm, main_norm, out)
    finally:
        opener_norm.unlink(missing_ok=True)
        if not main_has_audio:
            main_norm.unlink(missing_ok=True)
    if main_has_audio and main_norm.is_file():
        return (main_norm.resolve(), float(d_op))
    return (None, 0.0)


def _apply_drawtexts(
    v_in: Path, v_out: Path, items: list[dict[str, Any]], dur: float
) -> None:
    v_in, v_out = Path(v_in), Path(v_out)
    if not items:
        shutil.copy2(v_in, v_out)
        return
    vchain: list[str] = []
    for it in items:
        ts = float(it.get("time_sec", 0) or 0.0)
        te = ts + float(it.get("duration_sec", LOWER_THIRD_DEFAULT_S) or 5.0)
        te = min(dur, te)
        tx = (
            str(it.get("text", ""))
            .replace("\\", r"\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
        )
        stx = (
            str(it.get("subtext", ""))
            .replace("\\", r"\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
        )
        en = f"between(t,{ts:.2f},{te:.2f})"
        vchain.append(
            f"drawtext=text='{tx}':fontsize=40:fontcolor=white:"
            f"box=1:boxcolor=black@0.55:boxborderw=10:x=(w-text_w)/2:y=h*0.72:enable='{en}'"
        )
        if stx:
            vchain.append(
                f"drawtext=text='{stx}':fontsize=28:fontcolor=white:"
                f"box=1:boxcolor=black@0.4:boxborderw=6:x=(w-text_w)/2:y=h*0.78:enable='{en}'"
            )
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(v_in),
            "-vf",
            ",".join(vchain),
            "-c:a",
            "copy",
            str(v_out),
        ]
    )


def apply_packaging(root: Path, topic: str) -> Path:
    root = Path(root)
    t = topic_production_paths(root, topic)
    t["output"].mkdir(parents=True, exist_ok=True)
    t["brief"].mkdir(parents=True, exist_ok=True)
    manp = t["packaging_manifest"]
    main = t["final_with_presenter"]
    if not main.is_file():
        raise FileNotFoundError(
            f"Need {main} (run presenter_pipeline to produce final_with_presenter.mp4)"
        )
    mpath = t["production_brief"]
    if manp.is_file():
        with manp.open("r", encoding="utf-8") as f:
            m0: dict[str, Any] = json.load(f)
    else:
        m0 = {}
        LOG.info(
            "No packaging manifest; auto from assets/envato/ (add %s to override)",
            manp,
        )
    m: dict[str, Any] = {}
    m["transitions"] = (
        bool(m0["transitions"]) if "transitions" in m0 else True
    )
    to = m0.get("title_opener", "")
    tos = to if isinstance(to, str) else str(to or "")
    if tos.strip() and _valid_rel_file(root, tos):
        m["title_opener"] = tos
    else:
        try:
            p = asset_selector.pick_title_opener(root)
            m["title_opener"] = _rel_under_root(root, p)
        except FileNotFoundError as e:
            m["title_opener"] = ""
            LOG.warning("No title_opener: %s", e)

    bg = m0.get("background_music", "")
    bgs = bg if isinstance(bg, str) else str(bg or "")
    if bgs.strip() and _valid_rel_file(root, bgs):
        m["background_music"] = bgs
    else:
        try:
            mo = _brief_mood(mpath)
            p = asset_selector.pick_music(mo, root)
            m["background_music"] = _rel_under_root(root, p)
        except FileNotFoundError as e:
            m["background_music"] = ""
            LOG.warning("No background_music: %s", e)

    lt0 = m0.get("lower_thirds")
    if isinstance(lt0, list) and lt0 and any(isinstance(x, dict) for x in lt0):
        m["lower_thirds"] = [x for x in lt0 if isinstance(x, dict)]
    else:
        m["lower_thirds"] = _lower_thirds_drawtext_fallback(mpath, topic)
        if not m0.get("lower_thirds"):
            LOG.info("lower_thirds: using drawtext fallback (no manifest list)")

    LOG.info(
        "Packaging sources (manifest if valid, else auto): title_opener=%s "
        "background_music=%s lower_thirds=%d",
        m.get("title_opener", "") or "—",
        m.get("background_music", "") or "—",
        len(list(m.get("lower_thirds") or [])),
    )
    work = t["output"] / "_packaging"
    if work.is_dir():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    v0 = work / "v0.mp4"
    opener = _resolve(root, str(m.get("title_opener") or ""))
    main_has_audio = has_audio_stream(main)
    mic_source_main, opener_sec = _concat_opener(
        root, opener, main, v0, reencode=True, main_has_audio=main_has_audio
    )
    d = _ffprobe_duration(v0)
    man_sfx = _manifest_sfx_list_or_none(root, m0)
    if man_sfx is not None:
        m["sfx"] = man_sfx
    else:
        m["sfx"] = asset_selector.build_default_sfx_timeline(root, d)
    LOG.info(
        "SFX: %d events (%s manifest)",
        len(m.get("sfx") or []),
        "from" if man_sfx is not None else "auto",
    )
    v_vis = work / "v_vis.mp4"
    _apply_drawtexts(v0, v_vis, list(m.get("lower_thirds") or []), d)
    if m.get("transitions") and mpath.is_file() and load_brief(mpath) is not None:
        # placeholder: xfade at chapter ends could be added in a future version
        pass
    mic = work / "mic.wav"
    d_vis = _duration(v_vis)
    if mic_source_main is not None:
        print(
            "[packaging] using original audio track as mic",
            flush=True,
        )
        _mic_from_main_with_opener_prefix(
            Path(mic_source_main), float(opener_sec), float(d_vis), mic
        )
        mpath_only = Path(mic_source_main).resolve()
        if mpath_only != v0.resolve() and mpath_only.is_file():
            mpath_only.unlink(missing_ok=True)
    elif has_audio_stream(v_vis):
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(v_vis),
                "-vn",
                "-c:a",
                "pcm_s16le",
                str(mic),
            ]
        )
    else:
        print(
            "[packaging] no audio stream found; generated silent mic.wav",
            flush=True,
        )
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t",
                str(d_vis),
                "-c:a",
                "pcm_s16le",
                str(mic),
            ]
        )
    mus = _resolve(root, str(m.get("background_music") or ""))
    sfxl = [x for x in (m.get("sfx") or []) if isinstance(x, dict)]

    mic_48k = work / "mic_48k.wav"
    _audio_to_48k_stereo(mic, mic_48k, d)

    music_48k = work / "music.wav"
    if mus.is_file():
        _build_music_48k(mus, d, music_48k)
    else:
        _silence_48k_wav(d, music_48k)

    sfx_mix = _build_sfx_mix(work, root, sfxl, d)

    final_wav = work / "final_audio.wav"
    _build_final_audio_three_way(mic_48k, music_48k, sfx_mix, final_wav)

    outv = t["final_packaged"]
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(v_vis),
            "-i",
            str(final_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            "-movflags",
            "+faststart",
            str(outv),
        ]
    )
    LOG.info("Packaged: %s", outv)
    return outv
