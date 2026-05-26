"""Demucs invocation with segment fallback and full logging (CPU-first for long audio)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def is_htdemucs_family(model: str) -> bool:
    m = (model or "").strip().lower()
    return m == "htdemucs" or "htdemucs" in m


def is_mdx_family(model: str) -> bool:
    m = (model or "").strip().lower()
    if "mdx_extra_q" in m or "mdx_extra" in m:
        return True
    if m == "mdx" or m.startswith("mdx_"):
        return True
    return False


def effective_demucs_segment_request(model: str, cli_segment: int | None) -> int:
    """CLI ``None`` → model-specific default (7 stable; MDX families default 15)."""
    if cli_segment is not None:
        return int(cli_segment)
    return 15 if is_mdx_family(model) else 7


def clamp_demucs_segment(model: str, requested: int) -> tuple[int, list[str]]:
    """Return ``(segment_used, warnings)`` with hard caps for transformer vs MDX models."""
    warnings: list[str] = []
    req = int(requested)
    if is_htdemucs_family(model):
        cap = 7
        used = min(req, cap)
        if req > cap:
            warnings.append(f"demucs_segment_clamped_for_htdemucs: requested={req} used={used}")
    elif is_mdx_family(model):
        cap = 15
        used = min(req, cap)
        if req > cap:
            warnings.append(f"demucs_segment_clamped_for_mdx: requested={req} used={used}")
    else:
        cap = 15
        used = min(req, cap)
        if req > cap:
            warnings.append(f"demucs_segment_clamped: requested={req} used={used}")
    return max(1, used), warnings


def demucs_fallback_segments(model: str) -> tuple[int, ...]:
    """Secondary segment lengths after the primary (deduped with primary by caller)."""
    if is_htdemucs_family(model):
        return (5, 3)
    if is_mdx_family(model):
        return (12, 10, 7, 5, 3)
    return (5, 3)


def demucs_executable() -> str:
    return os.environ.get("DEMUCS_BIN") or shutil.which("demucs") or "demucs"


def build_demucs_cmd(
    *,
    demucs_exe: str,
    wav_in: Path,
    out_parent: Path,
    model: str,
    device: str,
    segment: int,
    shifts: int,
) -> list[str]:
    return [
        demucs_exe,
        "--two-stems",
        "vocals",
        "--device",
        str(device),
        "--segment",
        str(int(segment)),
        "--shifts",
        str(int(shifts)),
        "-n",
        str(model),
        "-o",
        str(out_parent),
        str(wav_in),
    ]


def _unique_segment_plan(primary: int, fallbacks: tuple[int, ...]) -> list[int]:
    out: list[int] = []
    for x in (primary,) + fallbacks:
        xi = max(1, int(x))
        if xi not in out:
            out.append(xi)
    return out


def _clear_model_output(out_parent: Path, model: str) -> None:
    root = out_parent / model
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


def run_demucs_long_audio(
    *,
    demucs_exe: str,
    wav_in: Path,
    out_parent: Path,
    model: str,
    device: str,
    segment_requested: int,
    segment_primary: int,
    shifts: int,
    timeout_sec: float,
    dry_run: bool,
    log_file: Path,
    fallback_segments: tuple[int, ...] = (5, 3),
) -> tuple[bool, dict[str, Any]]:
    """Try Demucs with segment ``segment_primary``, then each ``fallback_segments`` value (deduped).

    Writes concatenated stdout/stderr for every attempt to ``log_file``.
    Returns ``(success, info_dict)`` where ``info_dict`` includes reporting fields.
    """
    log_path_str = str(log_file)
    meta: dict[str, Any] = {
        "demucs_full_log_path": log_path_str,
        "demucs_full_log": log_path_str,
        "demucs_attempts": [],
        "demucs_stderr_tail": "",
        "demucs_model": model,
        "demucs_device": device,
        "demucs_segment_requested": int(segment_requested),
        "demucs_segment_used": int(segment_primary),
        "demucs_shifts": shifts,
        "demucs_returncode": -1,
    }

    if dry_run:
        meta["demucs_returncode"] = 0
        return True, meta

    out_parent.mkdir(parents=True, exist_ok=True)
    segments = _unique_segment_plan(segment_primary, fallback_segments)
    log_lines: list[str] = []

    last_rc = -1
    last_tail = ""
    attempts: list[dict[str, Any]] = []

    for attempt_idx, seg in enumerate(segments):
        if attempt_idx > 0:
            _clear_model_output(out_parent, model)

        cmd = build_demucs_cmd(
            demucs_exe=demucs_exe,
            wav_in=wav_in,
            out_parent=out_parent,
            model=model,
            device=device,
            segment=seg,
            shifts=shifts,
        )
        banner = (
            f"\n{'=' * 72}\n"
            f"demucs_attempt={attempt_idx + 1} segment={seg} device={device} shifts={shifts}\n"
            f"cmd={' '.join(cmd)}\n"
            f"{'=' * 72}\n"
        )
        log_lines.append(banner)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=float(timeout_sec),
                check=False,
            )
        except FileNotFoundError:
            block = "demucs_executable_not_found\n"
            log_lines.append(block)
            last_rc = 127
            last_tail = block
            attempts.append(
                {
                    "attempt": attempt_idx + 1,
                    "demucs_segment": seg,
                    "demucs_device": device,
                    "demucs_shifts": shifts,
                    "demucs_returncode": 127,
                    "demucs_stderr_tail": block.strip(),
                    "cmd": cmd,
                }
            )
            break
        except subprocess.TimeoutExpired:
            block = f"demucs_timeout_after_{timeout_sec}s\n"
            log_lines.append(block)
            last_rc = 124
            last_tail = block
            attempts.append(
                {
                    "attempt": attempt_idx + 1,
                    "demucs_segment": seg,
                    "demucs_device": device,
                    "demucs_shifts": shifts,
                    "demucs_returncode": 124,
                    "demucs_stderr_tail": block.strip(),
                    "cmd": cmd,
                }
            )
            break

        full_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        log_lines.append(full_out)
        last_rc = int(proc.returncode)
        last_tail = full_out[-8000:] if full_out else ""

        attempts.append(
            {
                "attempt": attempt_idx + 1,
                "demucs_segment": seg,
                "demucs_device": device,
                "demucs_shifts": shifts,
                "demucs_returncode": last_rc,
                "demucs_stderr_tail": last_tail[-4000:],
                "cmd": cmd,
            }
        )

        if proc.returncode == 0:
            meta["demucs_attempts"] = attempts
            meta["demucs_segment_used"] = seg
            meta["demucs_returncode"] = 0
            meta["demucs_stderr_tail"] = last_tail[-4000:]
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text("".join(log_lines), encoding="utf-8", errors="replace")
            return True, meta

    meta["demucs_attempts"] = attempts
    if attempts:
        meta["demucs_segment_used"] = attempts[-1]["demucs_segment"]
    meta["demucs_returncode"] = last_rc
    meta["demucs_stderr_tail"] = last_tail[-4000:]
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("".join(log_lines), encoding="utf-8", errors="replace")
    return False, meta
