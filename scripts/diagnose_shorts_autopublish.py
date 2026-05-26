#!/usr/bin/env python3
"""One-shot Shorts autopublish diagnostics + audit report (Pro-only)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"
SIPS = shutil.which("sips") or "/usr/bin/sips"
VIDEO_EXT_DIAG = {".mp4", ".mov", ".m4v"}
IMAGE_EXT_DIAG = {".jpg", ".jpeg", ".png", ".heic", ".dng", ".tif", ".tiff"}

ROOTS_AUDIT = (
    Path.home() / "StateVerge",
    Path.home() / "StateVerge_Control_Center",
)
LOG_CC = Path.home() / "StateVerge_Control_Center" / "logs"
OUT_JSON = LOG_CC / "shorts_autopublish_diagnose_v1.json"
OUT_MD = LOG_CC / "shorts_autopublish_diagnose_v1.md"
AUDIT_JSON = LOG_CC / "shorts_auto_publish_audit_v1.json"
AUDIT_MD = LOG_CC / "shorts_auto_publish_audit_v1.md"

SKIP_DIR_NAMES = {
    "node_modules",
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
}

CONTENT_KEYS = re.compile(
    r"short|shorts|auto_publish_queue_shorts|token_shorts|Real NYC Shorts|youtube_batch_upload|"
    r"youtube_upload|nyc-cut-upload|shorts_clip|launchd|LaunchAgents|\.plist",
    re.I,
)

BANNED_SNIPPETS = (
    "/Users/hennyhowie",
    "192.168.12.90",
    "/Volumes/SV_WORK",
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tail_file(path: Path, n: int = 40) -> str:
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def _launchctl_shorts_loaded() -> bool:
    label = "com.stateverge.shorts.autopublish"
    try:
        r = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return r.returncode == 0
    except OSError:
        return False


def _backend_ping() -> dict[str, Any]:
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8765/api/shorts/status", timeout=3) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return {"ok": True, "body": json.loads(raw)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:500]}


def _frontend_ping() -> dict[str, Any]:
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:3000", timeout=3) as resp:
            _ = resp.read(256)
        return {"ok": True, "status": getattr(resp, "status", 200)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:500]}


def _count_videos_under(roots: list[Path]) -> tuple[int, int]:
    """Return (all_video_files, candidates_after_min_size)."""
    ext = {".mp4", ".mov", ".m4v"}
    total = 0
    cand = 0
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file() or p.name.startswith("._"):
                    continue
                if p.suffix.lower() not in ext:
                    continue
                total += 1
                try:
                    if p.stat().st_size >= 512 * 1024:
                        cand += 1
                except OSError:
                    continue
        except OSError:
            continue
    return total, cand


def _ffprobe_wh_dur(path: Path) -> tuple[int, int, float]:
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=False)
        if r.returncode != 0:
            return 0, 0, 0.0
        j = json.loads(r.stdout or "{}")
        w = h = 0
        for st in j.get("streams") or []:
            if st.get("codec_type") == "video":
                w = int(st.get("width") or 0)
                h = int(st.get("height") or 0)
                break
        dur = 0.0
        try:
            dur = float((j.get("format") or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        return w, h, dur
    except Exception:
        return 0, 0, 0.0


def _image_wh_sips(path: Path) -> tuple[int, int]:
    try:
        r = subprocess.run(
            [SIPS, "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if r.returncode != 0:
            return 0, 0
        w = h = 0
        for line in (r.stdout or "").splitlines():
            if "pixelWidth" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    w = int(m.group(1))
            if "pixelHeight" in line:
                m = re.search(r"(\d+)", line)
                if m:
                    h = int(m.group(1))
        return w, h
    except OSError:
        return 0, 0


def scan_shorts_highlight_pool(iphone: Path, cinbox: Path, ledger_paths: set[str]) -> dict[str, Any]:
    video_paths: list[Path] = []
    image_paths: list[Path] = []
    for root in (iphone, cinbox):
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file() or p.name.startswith("._"):
                    continue
                suf = p.suffix.lower()
                if suf in VIDEO_EXT_DIAG:
                    video_paths.append(p)
                elif suf in IMAGE_EXT_DIAG:
                    image_paths.append(p)
        except OSError:
            continue

    portrait_v = landscape_v = square_v = 0
    high_score = 0
    for vp in video_paths:
        w, h, dur = _ffprobe_wh_dur(vp)
        if w <= 0 or h <= 0:
            continue
        if h > w * 1.05:
            portrait_v += 1
        elif w > h * 1.05:
            landscape_v += 1
        else:
            square_v += 1
        if min(w, h) >= 720 and dur >= 3.0:
            high_score += 1

    for ip in image_paths:
        w, h = _image_wh_sips(ip)
        if w <= 0:
            w, h, _ = _ffprobe_wh_dur(ip)
        mp = (w * h) / 1e6 if w and h else 0.0
        if mp >= 6.0 or min(w, h) >= 1600:
            high_score += 1

    def keyify(p: Path) -> str:
        try:
            return str(p.resolve())
        except OSError:
            return str(p)

    all_keys = [keyify(p) for p in video_paths + image_paths]
    unused = sum(1 for k in all_keys if k not in ledger_paths)

    return {
        "total_video_candidates": len(video_paths),
        "total_image_candidates": len(image_paths),
        "portrait_video_candidates": portrait_v,
        "landscape_video_candidates": landscape_v,
        "square_video_candidates": square_v,
        "high_score_candidates": high_score,
        "used_assets_count": len(ledger_paths),
        "unused_assets_count": unused,
        "shorts_source_policy": "all_video_image_highlight_candidates",
    }


def run_audit() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for base in ROOTS_AUDIT:
        if not base.is_dir():
            rows.append(
                {
                    "discovered_file": str(base),
                    "role": "root_missing",
                    "current_path": str(base),
                    "issues_found": ["audit_root_missing"],
                    "proposed_fix": "Ensure repo exists at official path on Pro",
                    "severity": "high",
                }
            )
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dp = Path(dirpath)
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            rel = dp.relative_to(base)
            if len(rel.parts) > 14:
                dirnames[:] = []
                continue
            for fn in filenames:
                fp = dp / fn
                low = fn.lower()
                if not (
                    low.endswith((".py", ".plist", ".sh", ".tsx", ".ts", ".json", ".md", ".env", ".yaml", ".yml"))
                ):
                    continue
                path_s = str(fp)
                key_hit = CONTENT_KEYS.search(fn) or CONTENT_KEYS.search(path_s.replace("\\", "/"))
                try:
                    txt = fp.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if not key_hit and not CONTENT_KEYS.search(txt[:8000]):
                    continue
                issues: list[str] = []
                sev = "low"
                for b in BANNED_SNIPPETS:
                    if b in txt or b in path_s:
                        issues.append(f"banned_reference:{b}")
                        sev = "high"
                if "/Volumes/StateVerge" in txt:
                    issues.append("mentions_/Volumes/StateVerge")
                    if sev != "high":
                        sev = "medium"
                role = "shorts_automation_related"
                if low.endswith(".plist") or "launchagent" in low:
                    role = "launchd_or_agent"
                elif "youtube" in low:
                    role = "youtube_script_or_config"
                elif "shorts" in low or "short" in low:
                    role = "shorts_script_or_doc"
                fix = "Review path/token usage; align with SV_TRANSFER/SV_CACHE and token_shorts.json"
                if issues:
                    fix = "Remove/replace legacy host references; use Pro paths only"
                rows.append(
                    {
                        "discovered_file": path_s,
                        "role": role,
                        "current_path": path_s,
                        "issues_found": issues or ["none_critical"],
                        "proposed_fix": fix,
                        "severity": sev,
                    }
                )
    return rows


def main() -> int:
    LOG_CC.mkdir(parents=True, exist_ok=True)
    sv_src = Path.home() / "StateVerge" / "src"
    if sv_src.is_dir() and str(sv_src) not in sys.path:
        sys.path.insert(0, str(sv_src))
    try:
        from utils.shorts_paths import (  # type: ignore
            ensure_shorts_dirs,
            shorts_publish_pack_root,
            shorts_ready_clips_dir,
            shorts_used_assets_json,
            token_shorts_path,
            youtube_client_secrets_path,
        )
        from utils.storage_paths import get_sv_cache, get_sv_transfer  # type: ignore
    except Exception as exc:  # noqa: BLE001
        print(f"[diagnose] import utils failed: {exc}", file=sys.stderr)
        return 2

    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)
    token_s = token_shorts_path()
    sec = youtube_client_secrets_path()
    ready = shorts_ready_clips_dir(verbose=False)
    pack = shorts_publish_pack_root(verbose=False)
    ledger_p = shorts_used_assets_json(verbose=False)
    ensure_shorts_dirs(verbose=False)

    iphone = xfer / "00_INBOX" / "iphone"
    cinbox = cache / "inbox"
    src_total, src_cand = _count_videos_under([iphone, cinbox])

    ledger_paths: set[str] = set()
    try:
        if ledger_p.is_file():
            doc = json.loads(ledger_p.read_text(encoding="utf-8", errors="replace"))
            for ent in doc.get("entries") or []:
                if isinstance(ent, dict) and ent.get("source_path"):
                    ledger_paths.add(str(ent["source_path"]))
    except (OSError, json.JSONDecodeError):
        pass

    pool_stats = scan_shorts_highlight_pool(iphone, cinbox, ledger_paths)

    audit_rows = run_audit()
    try:
        AUDIT_JSON.write_text(json.dumps({"generated_at": _utc(), "items": audit_rows}, indent=2), encoding="utf-8")
    except OSError:
        pass
    try:
        lines = ["# Shorts auto publish audit v1", "", f"- Generated: {_utc()}", ""]
        for it in audit_rows[:400]:
            lines.append(f"## `{it.get('discovered_file')}`")
            lines.append(f"- role: {it.get('role')}")
            lines.append(f"- severity: {it.get('severity')}")
            lines.append(f"- issues: {it.get('issues_found')}")
            lines.append(f"- proposed_fix: {it.get('proposed_fix')}")
            lines.append("")
        AUDIT_MD.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass

    shorts_outs = sorted(ready.glob("shorts_clip_*.mp4"), key=lambda p: p.stat().st_mtime if p.is_file() else 0)
    latest_out = str(shorts_outs[-1]) if shorts_outs else ""

    jobs_root = cache / "jobs" / "shorts"
    latest_job_file = ""
    latest_job_id = ""
    if jobs_root.is_dir():
        best_t = 0.0
        for d in jobs_root.iterdir():
            jp = d / "job.json"
            if not jp.is_file():
                continue
            try:
                t = jp.stat().st_mtime
            except OSError:
                continue
            if t > best_t:
                best_t = t
                latest_job_file = str(jp)
                latest_job_id = d.name

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.stateverge.shorts.autopublish.plist"

    payload: dict[str, Any] = {
        "generated_at": _utc(),
        "host_mode": "PRO_ONLY",
        "official_paths": {
            "stateverge": str(Path.home() / "StateVerge"),
            "control_center": str(Path.home() / "StateVerge_Control_Center"),
            "sv_cache": str(cache),
            "sv_transfer": str(xfer),
        },
        "volumes_mounted": {
            "SV_CACHE": cache.is_dir(),
            "SV_TRANSFER": xfer.is_dir(),
        },
        "token_shorts_path": str(token_s),
        "token_shorts_exists": token_s.is_file(),
        "client_secrets_path": str(sec),
        "client_secrets_exists": sec.is_file(),
        "ready_to_upload_writable": os.access(ready, os.W_OK) if ready.is_dir() else False,
        "publish_pack_writable": os.access(pack, os.W_OK) if pack.is_dir() else False,
        "source_video_count_total": src_total,
        "candidate_shorts_source_count": src_cand,
        **pool_stats,
        "latest_shorts_output": latest_out,
        "latest_shorts_job_path": latest_job_file,
        "latest_shorts_job_id": latest_job_id,
        "launchd_plist_exists": plist_path.is_file(),
        "launchd_loaded": _launchctl_shorts_loaded(),
        "launchd_stdout_tail": _tail_file(LOG_CC / "launchd_shorts_stdout.log"),
        "launchd_stderr_tail": _tail_file(LOG_CC / "launchd_shorts_stderr.log"),
        "backend_shorts_status": _backend_ping(),
        "frontend_ping": _frontend_ping(),
        "audit_written": {"json": str(AUDIT_JSON), "md": str(AUDIT_MD)},
    }

    try:
        OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"WARN: cannot write {OUT_JSON}: {exc}", file=sys.stderr)

    md_lines = [
        "# Shorts autopublish diagnose v1",
        "",
        f"- **generated_at**: `{payload['generated_at']}`",
        f"- **host_mode**: `{payload['host_mode']}`",
        f"- **token_shorts_exists**: `{payload['token_shorts_exists']}`",
        f"- **latest_shorts_output**: `{payload['latest_shorts_output']}`",
        f"- **launchd_loaded**: `{payload['launchd_loaded']}`",
        "",
        "## launchd stdout (tail)",
        "```",
        payload["launchd_stdout_tail"] or "(empty)",
        "```",
        "",
        "## launchd stderr (tail)",
        "```",
        payload["launchd_stderr_tail"] or "(empty)",
        "```",
        "",
    ]
    try:
        OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")
    except OSError:
        pass

    print(json.dumps({"ok": True, "wrote": str(OUT_JSON)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
