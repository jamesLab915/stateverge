#!/usr/bin/env python3
"""Replace video audio with looped Suno music (fade in/out, drop original track)."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_NYC = _SCRIPTS / "nyc_auto"
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from audio.audio_fade_helpers import resolve_music_fade  # noqa: E402
from nyc_long_schedule_v1 import _collect_music_candidates, _newest  # noqa: E402

SUNO_ROOT = Path("/Volumes/SV_CACHE/inbox/suno")
SOUNO_ROOT = Path("/Volumes/SV_CACHE/inbox/souno")

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def probe_duration(path: Path) -> float:
    r = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return max(0.0, float((r.stdout or "0").strip().splitlines()[0]))


def pick_music(*, music_root: Path | None = None) -> tuple[Path | None, str]:
    roots: list[tuple[Path, str]] = []
    if music_root is not None:
        roots.append((music_root, "suno"))
    roots.extend(((SUNO_ROOT, "suno"), (SOUNO_ROOT, "souno")))
    seen: set[Path] = set()
    for root, label in roots:
        key = root.resolve()
        if key in seen:
            continue
        seen.add(key)
        if not root.is_dir():
            continue
        pick = _newest(_collect_music_candidates(root, recursive=True))
        if pick:
            return pick, label
    return None, ""


def build_audio_fc(dur: float) -> str:
    fade = resolve_music_fade(dur, shorts=False)
    dur_s = f"{dur:.3f}"
    return (
        f"[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"atrim=duration={dur_s},asetpts=PTS-STARTPTS,"
        f"{fade.filter_chain()},"
        f"loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
    )


def run_ffmpeg(cmd: list[str], *, timeout: float) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        tail = ((r.stderr or "") + (r.stdout or ""))[-12000:]
        return int(r.returncode or 0), tail
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as exc:
        return 125, repr(exc)


def verify_output(path: Path, expected_dur: float) -> dict:
    out: dict = {"ok": False, "reasons": []}
    if not path.is_file() or path.stat().st_size < 4096:
        out["reasons"].append("missing_or_tiny_output")
        return out

    dur = probe_duration(path)
    out["duration_sec"] = dur
    if abs(dur - expected_dur) > 2.0:
        out["reasons"].append(f"duration_mismatch:expected={expected_dur:.3f},got={dur:.3f}")

    r = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    has_audio = (r.stdout or "").strip() == "audio"
    out["has_audio"] = has_audio
    if not has_audio:
        out["reasons"].append("no_audio_stream")

    vol_r = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    blob = (vol_r.stderr or "") + (vol_r.stdout or "")
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", blob)
    mean_vol = float(m.group(1)) if m else None
    out["mean_volume_db"] = mean_vol
    if mean_vol is None:
        out["reasons"].append("mean_volume_unparsed")
    elif mean_vol < -45.0 or mean_vol > -6.0:
        out["reasons"].append(f"mean_volume_out_of_range:{mean_vol}")

    out["ok"] = not out["reasons"]
    return out


def replace_audio(
    input_video: Path,
    output_video: Path,
    *,
    music_root: Path | None = None,
    work_dir: Path | None = None,
    log_path: Path | None = None,
    encode_timeout_sec: float = 6 * 3600,
) -> dict:
    log: dict = {
        "started_at": _utc(),
        "input_video": str(input_video),
        "output_video": str(output_video),
        "ffmpeg": FFMPEG,
        "ffprobe": FFPROBE,
    }

    if not input_video.is_file():
        log.update(ok=False, error="input_missing", finished_at=_utc())
        return log

    if output_video.resolve() == input_video.resolve():
        log.update(ok=False, error="output_same_as_input", finished_at=_utc())
        return log

    music, music_label = pick_music(music_root=music_root)
    log["music_label"] = music_label
    if not music:
        log.update(ok=False, error="no_music_found", finished_at=_utc())
        return log

    log["music_file"] = str(music)
    dur = probe_duration(input_video)
    log["duration_sec"] = dur
    fade = resolve_music_fade(dur, shorts=False)
    log["fade"] = {
        "fade_in_sec": fade.fade_in_sec,
        "fade_out_sec": fade.fade_out_sec,
        "fade_out_start_sec": fade.fade_out_start_sec,
    }

    encode_dir = work_dir or output_video.parent
    encode_dir.mkdir(parents=True, exist_ok=True)
    tmp = encode_dir / f"{output_video.stem}._tmp_{os.getpid()}.mp4"
    log["work_dir"] = str(encode_dir)
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass

    fc = build_audio_fc(dur)
    dur_s = f"{dur:.3f}"
    base = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(input_video),
        "-stream_loop",
        "-1",
        "-i",
        str(music),
        "-filter_complex",
        fc,
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-t",
        dur_s,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
    ]

    attempts = [
        {"mode": "copy", "extra": ["-c:v", "copy"]},
        {
            "mode": "libx264_cfr30",
            "extra": ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-r", "30", "-vsync", "cfr"],
        },
    ]

    ok_encode = False
    for att in attempts:
        cmd = [*base, *att["extra"], str(tmp)]
        log.setdefault("encode_attempts", []).append({"mode": att["mode"], "cmd_head": cmd[:16]})
        rc, tail = run_ffmpeg(cmd, timeout=encode_timeout_sec)
        log["encode_attempts"][-1]["returncode"] = rc
        log["encode_attempts"][-1]["stderr_tail"] = tail[-4000:]
        if rc == 0 and tmp.is_file() and tmp.stat().st_size > 4096:
            ok_encode = True
            log["encode_mode"] = att["mode"]
            break
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

    if not ok_encode:
        log.update(ok=False, error="ffmpeg_failed", finished_at=_utc())
        return log

    try:
        output_video.parent.mkdir(parents=True, exist_ok=True)
        if output_video.is_file():
            output_video.unlink()
        shutil.move(str(tmp), str(output_video))
        log["deliver_mode"] = "move"
    except OSError as move_exc:
        try:
            shutil.copy2(tmp, output_video)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            log["deliver_mode"] = "copy"
        except OSError as copy_exc:
            log["staged_output"] = str(tmp)
            log["deliver_error"] = f"move:{move_exc};copy:{copy_exc}"
            verify = verify_output(tmp, dur)
            log["verify"] = verify
            log["finished_at"] = _utc()
            log["ok"] = bool(verify.get("ok"))
            if log_path:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
            return log

    verify = verify_output(output_video, dur)
    log["verify"] = verify
    log["finished_at"] = _utc()
    log["ok"] = bool(verify.get("ok"))
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    return log


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--music-root", type=Path, default=None, help="Suno inbox root (recursive pick)")
    ap.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Directory for ffmpeg temp output (default: output parent)",
    )
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--encode-timeout-sec", type=float, default=6 * 3600)
    args = ap.parse_args()

    log = replace_audio(
        args.input,
        args.output,
        music_root=args.music_root,
        work_dir=args.work_dir,
        log_path=args.log,
        encode_timeout_sec=args.encode_timeout_sec,
    )

    ok = bool(log.get("ok"))
    music = log.get("music_file", "")
    dur = log.get("duration_sec", 0)
    staged = log.get("staged_output")
    print(f"SUNO_REPLACE_OK={str(ok).lower()}")
    print(f"OUTPUT_VIDEO={args.output}")
    if staged:
        print(f"STAGED_OUTPUT={staged}")
    print(f"MUSIC_FILE={music}")
    print(f"DURATION_SEC={dur}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
