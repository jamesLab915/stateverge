#!/usr/bin/env python3
"""Diagnose YouTube LaunchAgent schedules (long NYC vs Shorts). Writes JSON + Markdown reports."""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
CONTROL = Path.home() / "StateVerge_Control_Center"
LOG_DIR = CONTROL / "logs"
OUT_JSON = LOG_DIR / "youtube_schedule_diagnose.json"
OUT_MD = LOG_DIR / "youtube_schedule_diagnose.md"

LONG_TOKEN = REPO / "data" / "youtube" / "token.json"
SHORTS_TOKEN = REPO / "data" / "youtube" / "token_shorts.json"
LONG_QUEUE = REPO / "scripts" / "nyc_auto" / "auto_publish_queue.py"
SHORTS_QUEUE = REPO / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py"
PLIST_LONG = CONTROL / "launchagents" / "com.stateverge.nyc.autopublish.plist"
PLIST_SHORTS = CONTROL / "launchagents" / "com.stateverge.shorts.autopublish.plist"
LONG_STDOUT = LOG_DIR / "launchd_nyc_long_stdout.log"
LONG_STDERR = LOG_DIR / "launchd_nyc_long_stderr.log"
SHORTS_STDOUT = LOG_DIR / "launchd_shorts_stdout.log"
SHORTS_STDERR = LOG_DIR / "launchd_shorts_stderr.log"

READY_TRANSFER = Path("/Volumes/SV_TRANSFER/ready_to_upload")
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
SHORTS_EXCLUDE = ("shorts", "shorts_clips", "youtube_shorts", "shorts_uploads")


def _is_shorts_path(p: Path) -> bool:
    s = str(p).replace("\\", "/").lower()
    return any(x in s for x in SHORTS_EXCLUDE)


def _tail(path: Path, n: int = 40) -> str:
    if not path.is_file():
        return f"(missing) {path}\n"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(read_error {exc}) {path}\n"
    chunk = lines[-n:] if len(lines) > n else lines
    return "\n".join(chunk) + ("\n" if chunk else "")


def _iter_ready_videos(roots: list[Path]) -> Iterator[Path]:
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file() or p.name.startswith("._"):
                    continue
                if p.suffix.lower() not in VIDEO_EXTS:
                    continue
                yield p
        except OSError:
            continue


def _count_long_shorts_candidates() -> dict[str, int]:
    roots = [READY_TRANSFER / "nyc_long_clips", READY_TRANSFER]
    long_n = 0
    shorts_n = 0
    for p in _iter_ready_videos(roots):
        if _is_shorts_path(p):
            shorts_n += 1
        else:
            long_n += 1
    return {"ready_long_candidate_files": long_n, "ready_shorts_candidate_files": shorts_n}


def _load_plist(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            return plistlib.load(f)
    except Exception:
        return None


def _launchctl_print(label: str) -> str:
    uid = os.getuid()
    key = f"gui/{uid}/{label}"
    try:
        r = subprocess.run(
            ["launchctl", "print", key],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(launchctl_print_failed: {exc})"


def _launchctl_list_grep(label: str) -> str:
    """Best-effort: full label often does not appear in `launchctl list` output on newer macOS."""
    try:
        r = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        body = (r.stdout or "") + (r.stderr or "")
        for line in body.splitlines():
            if label in line:
                return line.strip()
        short = label.replace("com.stateverge.", "")
        for line in body.splitlines():
            if short in line and "stateverge" in line.lower():
                return line.strip()
        return "(no matching line in launchctl list; see launchctl_print_* in this report)"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(launchctl_list_failed: {exc})"


def _service_loaded_from_print(print_text: str) -> bool:
    """launchctl print includes `path = …plist` when the job is registered for the domain."""
    if "path =" not in print_text or ".plist" not in print_text:
        return False
    return "could not find service" not in print_text.lower()


def _plist_intervals_and_args(pl: dict[str, Any] | None) -> tuple[list[Any], list[str]]:
    if not pl:
        return [], []
    intervals = pl.get("StartCalendarInterval")
    if isinstance(intervals, dict):
        intervals = [intervals]
    elif not isinstance(intervals, list):
        intervals = []
    args = pl.get("ProgramArguments") or []
    if not isinstance(args, list):
        args = []
    return intervals, [str(x) for x in args]


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    long_pl = _load_plist(PLIST_LONG)
    shorts_pl = _load_plist(PLIST_SHORTS)
    long_iv, long_args = _plist_intervals_and_args(long_pl or {})
    shorts_iv, shorts_args = _plist_intervals_and_args(shorts_pl or {})

    counts = _count_long_shorts_candidates()

    print_shorts = _launchctl_print("com.stateverge.shorts.autopublish")
    print_nyc = _launchctl_print("com.stateverge.nyc.autopublish")

    report: dict[str, Any] = {
        "token_json_exists": LONG_TOKEN.is_file(),
        "token_json_path": str(LONG_TOKEN),
        "token_shorts_json_exists": SHORTS_TOKEN.is_file(),
        "token_shorts_json_path": str(SHORTS_TOKEN),
        "auto_publish_queue_py_exists": LONG_QUEUE.is_file(),
        "auto_publish_queue_py_path": str(LONG_QUEUE),
        "auto_publish_queue_shorts_py_exists": SHORTS_QUEUE.is_file(),
        "auto_publish_queue_shorts_py_path": str(SHORTS_QUEUE),
        "shorts_plist_exists": PLIST_SHORTS.is_file(),
        "shorts_plist_path": str(PLIST_SHORTS),
        "long_plist_exists": PLIST_LONG.is_file(),
        "long_plist_path": str(PLIST_LONG),
        "launchctl_list_shorts": _launchctl_list_grep("com.stateverge.shorts.autopublish"),
        "launchctl_list_nyc_long": _launchctl_list_grep("com.stateverge.nyc.autopublish"),
        "launchctl_print_shorts": print_shorts,
        "launchctl_print_nyc_long": print_nyc,
        "shorts_service_loaded": _service_loaded_from_print(print_shorts),
        "nyc_long_service_loaded": _service_loaded_from_print(print_nyc),
        "shorts_plist_StartCalendarInterval": shorts_iv,
        "long_plist_StartCalendarInterval": long_iv,
        "shorts_plist_ProgramArguments": shorts_args,
        "long_plist_ProgramArguments": long_args,
        "tail_long_stdout": _tail(LONG_STDOUT),
        "tail_long_stderr": _tail(LONG_STDERR),
        "tail_shorts_stdout": _tail(SHORTS_STDOUT),
        "tail_shorts_stderr": _tail(SHORTS_STDERR),
        **counts,
    }

    issues: list[str] = []
    warns: list[str] = []
    if not report["token_json_exists"]:
        warns.append("missing_token_json")
    if not report["token_shorts_json_exists"]:
        warns.append("missing_token_shorts_json")
    if not report["auto_publish_queue_py_exists"]:
        issues.append("missing_auto_publish_queue_py")
    if not report["auto_publish_queue_shorts_py_exists"]:
        issues.append("missing_auto_publish_queue_shorts_py")
    if not report["long_plist_exists"]:
        issues.append("missing_long_plist")
    if not report["shorts_plist_exists"]:
        warns.append("missing_shorts_plist")
    if not READY_TRANSFER.is_dir():
        warns.append("sv_transfer_ready_to_upload_missing")

    if report["long_plist_exists"] and not report["nyc_long_service_loaded"]:
        warns.append("long_agent_not_loaded_in_launchd_bootstrap_required")

    if issues:
        status = "error"
    elif warns:
        status = "warning"
    else:
        status = "ok"

    report["status"] = status
    report["issues"] = issues
    report["warnings"] = warns

    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        "# YouTube schedule diagnose",
        "",
        f"**status**: `{status}`",
        "",
        "## Checks",
        "",
        f"- token.json: {'yes' if report['token_json_exists'] else 'no'} — `{report['token_json_path']}`",
        f"- token_shorts.json: {'yes' if report['token_shorts_json_exists'] else 'no'}",
        f"- auto_publish_queue.py: {'yes' if report['auto_publish_queue_py_exists'] else 'no'}",
        f"- auto_publish_queue_shorts.py: {'yes' if report['auto_publish_queue_shorts_py_exists'] else 'no'}",
        f"- long plist: {'yes' if report['long_plist_exists'] else 'no'} — `{report['long_plist_path']}`",
        f"- shorts plist: {'yes' if report['shorts_plist_exists'] else 'no'} — `{report['shorts_plist_path']}`",
        "",
        "## Ready-to-upload counts (SV_TRANSFER)",
        "",
        f"- long candidate files (non-shorts path): **{counts['ready_long_candidate_files']}**",
        f"- shorts-like path videos: **{counts['ready_shorts_candidate_files']}**",
        "",
        "## StartCalendarInterval",
        "",
        "### Long (NYC)",
        "",
        "```json",
        json.dumps(long_iv, indent=2, ensure_ascii=False),
        "```",
        "",
        "### Shorts",
        "",
        "```json",
        json.dumps(shorts_iv, indent=2, ensure_ascii=False),
        "```",
        "",
        "## ProgramArguments",
        "",
        "### Long",
        "",
        "```",
        "\n".join(long_args) if long_args else "(none)",
        "```",
        "",
        "### Shorts",
        "",
        "```",
        "\n".join(shorts_args) if shorts_args else "(none)",
        "```",
        "",
        "## launchctl list (matching line)",
        "",
        f"- shorts: `{report['launchctl_list_shorts']}`",
        f"- nyc long: `{report['launchctl_list_nyc_long']}`",
        "",
        "## launchd load state (from launchctl print)",
        "",
        f"- shorts_service_loaded: **{report['shorts_service_loaded']}**",
        f"- nyc_long_service_loaded: **{report['nyc_long_service_loaded']}**",
        "",
        "## Recent long stdout",
        "",
        "```",
        report["tail_long_stdout"].rstrip(),
        "```",
        "",
        "## Recent long stderr",
        "",
        "```",
        report["tail_long_stderr"].rstrip(),
        "```",
        "",
        "## Recent shorts stdout",
        "",
        "```",
        report["tail_shorts_stdout"].rstrip(),
        "```",
        "",
        "## Recent shorts stderr",
        "",
        "```",
        report["tail_shorts_stderr"].rstrip(),
        "```",
        "",
    ]
    if issues:
        md_lines += ["## Issues", "", "\n".join(f"- {i}" for i in issues), ""]
    if warns:
        md_lines += ["## Warnings", "", "\n".join(f"- {w}" for w in warns), ""]

    OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")
    print(json.dumps({"status": status, "wrote_json": str(OUT_JSON), "wrote_md": str(OUT_MD)}, indent=2))
    return 0 if status != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
