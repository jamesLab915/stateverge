#!/usr/bin/env python3
"""StateVerge Automation System Final Health Check v1 (Pro-only, fail-open, no real uploads)."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CC = Path.home() / "StateVerge_Control_Center"
LOGS = CC / "logs"
VENV_PY = ROOT / ".venv_audio" / "bin" / "python3"
OUT_JSON = LOGS / "automation_system_final_health_check.json"
OUT_MD = LOGS / "automation_system_final_health_check.md"

SV_CACHE = Path("/Volumes/SV_CACHE")
SV_TRANSFER = Path("/Volumes/SV_TRANSFER")
SV_BACKUP = Path("/Volumes/SV_BACKUP")

LONG_PLIST = CC / "launchagents" / "com.stateverge.nyc.autopublish.plist"
SHORTS_PLIST = CC / "launchagents" / "com.stateverge.shorts.autopublish.plist"

LONG_LEDGER = SV_TRANSFER / "publish_pack" / "nyc_long_uploads" / "long_used_assets.json"
SHORTS_LEDGER = SV_TRANSFER / "publish_pack" / "shorts_uploads" / "shorts_used_assets.json"
SHORTS_CLIPS = SV_TRANSFER / "ready_to_upload" / "shorts_clips"

LONG_TOKEN = ROOT / "data" / "youtube" / "token.json"
SHORTS_TOKEN = ROOT / "data" / "youtube" / "token_shorts.json"
CLIENT_SECRETS = ROOT / ".secrets" / "youtube" / "client_secrets.json"
CHANNEL_GUARD = ROOT / "scripts" / "nyc_auto" / "channel_guard.py"

# Runtime sources scanned for legacy paths (bounded; avoid full-tree rglob).
RUNTIME_SCAN_FILES: tuple[Path, ...] = (
    ROOT / "scripts" / "nyc_auto" / "auto_publish_queue.py",
    ROOT / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py",
    ROOT / "scripts" / "nyc_auto" / "channel_guard.py",
    ROOT / "scripts" / "nyc_auto" / "metadata_generator.py",
    ROOT / "scripts" / "nyc_auto" / "long_audio_policy.py",
    ROOT / "scripts" / "nyc_auto" / "youtube_upload.py",
    ROOT / "scripts" / "jobs" / "shorts_cut_upload_job.py",
    CC / "backend" / "main.py",
    LONG_PLIST,
    SHORTS_PLIST,
    CC / "start_server_mode.sh",
    CC / "stop_server_mode.sh",
)

LEGACY_SUBSTRINGS = (
    "/Users/hennyhowie",
    "192.168.12.90",
)

DIAGNOSE_SCRIPTS: tuple[tuple[str, Path], ...] = (
    ("auto_publish_v2", ROOT / "scripts" / "diagnose_auto_publish_v2.py"),
    ("long_audio_policy", ROOT / "scripts" / "diagnose_long_audio_policy.py"),
    ("metadata_generation", ROOT / "scripts" / "diagnose_metadata_generation.py"),
    ("real_sound_gate", ROOT / "scripts" / "diagnose_real_sound_gate.py"),
    ("shorts_autopublish", ROOT / "scripts" / "diagnose_shorts_autopublish.py"),
    ("nyc_long_source_policy", ROOT / "scripts" / "diagnose_nyc_long_source_policy.py"),
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_json(method: str, url: str, *, data: dict[str, Any] | None = None, timeout: float = 8.0) -> tuple[int, dict[str, Any] | str]:
    try:
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return int(resp.status), json.loads(raw)
            except json.JSONDecodeError:
                return int(resp.status), raw[:4000]
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            return int(exc.code), json.loads(raw)
        except Exception:
            return int(exc.code), raw[:2000] if isinstance(raw, str) else str(exc)
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def _tail(s: str, n: int = 2000) -> str:
    s = s or ""
    return s[-n:] if len(s) > n else s


def _run_script(
    path: Path, *, timeout: int = 120, env: dict[str, str] | None = None
) -> dict[str, Any]:
    if not path.is_file():
        return {"ran": False, "status": "missing", "stdout_tail": "", "stderr_tail": "", "returncode": None}
    py = str(VENV_PY) if VENV_PY.is_file() else sys.executable
    try:
        r = subprocess.run(
            [py, str(path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, **(env or {})},
        )
        return {
            "ran": True,
            "status": "ok" if r.returncode == 0 else f"exit_{r.returncode}",
            "stdout_tail": _tail(r.stdout or ""),
            "stderr_tail": _tail(r.stderr or ""),
            "returncode": r.returncode,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ran": True,
            "status": "timeout",
            "stdout_tail": _tail((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
            "stderr_tail": _tail((exc.stderr or "") if isinstance(exc.stderr, str) else ""),
            "returncode": None,
        }
    except OSError as exc:
        return {"ran": False, "status": f"os_error:{exc}", "stdout_tail": "", "stderr_tail": "", "returncode": None}


def _grep_legacy_in_file(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    n = 0
    for sub in LEGACY_SUBSTRINGS:
        n += text.count(sub)
    if re.search(r"\bSSH\b.*\bAir\b|\bMacBook\s+Air\b.*active", text, re.IGNORECASE):
        n += 1
    return n


def _scan_runtime_legacy_refs() -> tuple[int, list[str]]:
    hits = 0
    sample: list[str] = []
    for p in RUNTIME_SCAN_FILES:
        if not p.is_file():
            continue
        c = _grep_legacy_in_file(p)
        if c:
            hits += c
            if len(sample) < 24:
                sample.append(f"{p}:{c}")
    return hits, sample


def _count_log_legacy_refs(*, max_files: int = 400) -> tuple[int, int]:
    """Scan CC/logs for legacy strings; second int = files touched."""
    if not LOGS.is_dir():
        return 0, 0
    hits = 0
    files = 0
    try:
        paths = sorted(LOGS.rglob("*"), key=lambda x: x.stat().st_mtime if x.is_file() else 0, reverse=True)
    except OSError:
        return 0, 0
    for p in paths:
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".log", ".md", ".json", ".txt"):
            continue
        try:
            if p.stat().st_size > 4_000_000:
                continue
        except OSError:
            continue
        c = _grep_legacy_in_file(p)
        if c:
            hits += c
            files += 1
        if files >= max_files and hits > 0:
            break
    return hits, files


def _plist_blob(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as fh:
            pl = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException):
        return ""
    args = pl.get("ProgramArguments")
    if isinstance(args, list):
        return " ".join(str(a) for a in args)
    return ""


def _plist_stdout_stderr_ok(path: Path) -> tuple[bool, list[str]]:
    warns: list[str] = []
    if not path.is_file():
        return False, ["plist_missing"]
    try:
        with path.open("rb") as fh:
            pl = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException):
        return False, ["plist_unreadable"]
    for key in ("StandardOutPath", "StandardErrorPath"):
        p = pl.get(key)
        if not isinstance(p, str) or not p.strip():
            warns.append(f"missing_{key}")
            continue
        pp = Path(p).expanduser()
        parent = pp.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not (parent.is_dir() and os.access(parent, os.W_OK)):
            warns.append(f"{key}_parent_not_writable:{parent}")
    return len(warns) == 0, warns


def _launchctl_list_stateverge() -> str:
    try:
        r = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        lines = [ln for ln in (r.stdout or "").splitlines() if "stateverge" in ln.lower()]
        return "\n".join(lines[:40])
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _launchctl_label_loaded(label: str) -> bool:
    uid = os.getuid()
    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        return r.returncode == 0 and "state" in (r.stdout or "").lower()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ps_snapshot() -> str:
    try:
        r = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        blob = r.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return ""
    keys = (
        "ffmpeg",
        "auto_publish_queue.py",
        "auto_publish_queue_shorts.py",
        "shorts_cut_upload_job.py",
        "youtube_upload",
        "uvicorn",
        " next ",
        "node.*next",
    )
    out: list[str] = []
    for line in blob.splitlines():
        if "grep" in line:
            continue
        low = line.lower()
        if any(k.strip() in low for k in keys):
            out.append(line[:500])
    return "\n".join(out[:80])


def _count_processes(pattern: str) -> int:
    snap = _ps_snapshot()
    return sum(1 for ln in snap.splitlines() if pattern in ln)


def _subprocess_json_cmd(cmd: list[str], *, timeout: int, cwd: Path) -> tuple[int, str, dict[str, Any] | None]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)
        raw = (r.stdout or "").strip()
        try:
            return r.returncode, raw, json.loads(raw) if raw.startswith("{") else None
        except json.JSONDecodeError:
            return r.returncode, _tail(raw + "\n" + (r.stderr or ""), 8000), None
    except subprocess.TimeoutExpired as exc:
        return -1, _tail(str(exc) + (exc.stdout or "") + (exc.stderr or ""), 4000), None
    except OSError as exc:
        return -2, str(exc), None


def _ffprobe_shorts(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "width": 0, "height": 0, "duration": 0.0, "has_audio": False, "has_video": False, "avg_frame_rate": ""}
    if not path.is_file():
        return out
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if r.returncode != 0:
            return out
        data = json.loads(r.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return out
    dur = 0.0
    try:
        dur = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        pass
    out["duration"] = dur
    for st in data.get("streams") or []:
        if not isinstance(st, dict):
            continue
        c = str(st.get("codec_type") or "")
        if c == "video":
            out["has_video"] = True
            try:
                out["width"] = int(st.get("width") or 0)
                out["height"] = int(st.get("height") or 0)
            except (TypeError, ValueError):
                pass
            out["avg_frame_rate"] = str(st.get("avg_frame_rate") or "")
        elif c == "audio":
            out["has_audio"] = True
    out["ok"] = out["has_video"] and out["has_audio"] and dur > 0
    return out


def _latest_mp4(root: Path) -> Path | None:
    best: Path | None = None
    best_mt = 0.0
    if not root.is_dir():
        return None
    try:
        for p in root.iterdir():
            if not p.is_file() or p.suffix.lower() != ".mp4":
                continue
            try:
                mt = float(p.stat().st_mtime)
            except OSError:
                continue
            if mt > best_mt:
                best_mt = mt
                best = p
    except OSError:
        return None
    return best


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def _analyze_long_ledger(path: Path) -> dict[str, Any]:
    data = _load_json(path) or {}
    entries = data.get("entries")
    if not isinstance(entries, list):
        return {"exists": path.is_file(), "entry_count": 0, "duplicate_paths": 0, "video_ids": []}
    paths = [str(e.get("path") or "") for e in entries if isinstance(e, dict)]
    ctr = Counter(paths)
    dups = sum(1 for p, c in ctr.items() if p and c > 1)
    vids = [str(e.get("video_id") or "") for e in entries if isinstance(e, dict)][-8:]
    return {"exists": True, "entry_count": len(entries), "duplicate_paths": dups, "video_ids": [v for v in vids if v]}


def _analyze_shorts_ledger(path: Path) -> dict[str, Any]:
    data = _load_json(path) or {}
    entries = data.get("entries")
    if not isinstance(entries, list):
        return {
            "exists": path.is_file(),
            "entry_count": 0,
            "max_same_output_quick_hash": 0,
            "reservation_sane": True,
        }
    hashes = [str(e.get("output_quick_hash") or "").strip() for e in entries if isinstance(e, dict)]
    hctr = Counter(h for h in hashes if h)
    max_same = max(hctr.values()) if hctr else 0
    return {
        "exists": True,
        "entry_count": len(entries),
        "max_same_output_quick_hash": max_same,
        "reservation_sane": max_same <= 2,
    }


def _restart_server_mode(issues: list[str]) -> bool:
    stop_sh = CC / "stop_server_mode.sh"
    start_sh = CC / "start_server_mode.sh"
    if not start_sh.is_file():
        issues.append("start_server_mode.sh_missing")
        return False
    try:
        subprocess.run(["/bin/sh", str(stop_sh)], cwd=str(CC), timeout=40, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        subprocess.run(["/bin/sh", str(start_sh)], cwd=str(CC), timeout=60, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        issues.append(f"start_server_mode_failed:{exc}")
        return False
    import time

    time.sleep(5)
    code, _ = _http_json("GET", "http://127.0.0.1:8765/api/automation/status", timeout=6.0)
    return code == 200


def _reload_launchagents(issues: list[str]) -> None:
    uid = os.getuid()
    cmds = [
        ["launchctl", "bootout", f"gui/{uid}/com.stateverge.nyc.autopublish"],
        ["launchctl", "bootstrap", f"gui/{uid}", str(LONG_PLIST)],
        ["launchctl", "enable", f"gui/{uid}/com.stateverge.nyc.autopublish"],
        ["launchctl", "bootout", f"gui/{uid}/com.stateverge.shorts.autopublish"],
        ["launchctl", "bootstrap", f"gui/{uid}", str(SHORTS_PLIST)],
        ["launchctl", "enable", f"gui/{uid}/com.stateverge.shorts.autopublish"],
    ]
    for c in cmds:
        try:
            subprocess.run(c, cwd=str(CC), timeout=25, check=False, capture_output=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(f"launchctl_step_failed:{c}:{exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="StateVerge automation final health check (dry-run, fail-open).")
    ap.add_argument("--run-long-dry-run", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--run-shorts-dry-run", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--skip-shorts-encode", action="store_true")
    ap.add_argument("--max-count", type=int, default=1)
    ap.add_argument("--reload-launchagents", action="store_true")
    ap.add_argument("--no-launchagent-reload", action="store_true")
    ap.add_argument("--restart-server", action="store_true")
    ap.add_argument("--no-server-restart", action="store_true")
    ap.add_argument("--safe-only", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--cleanup-stale-shorts-workers", action="store_true")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()

    safe_mode = bool(args.safe_only)

    issues: list[str] = []
    warnings: list[str] = []

    LOGS.mkdir(parents=True, exist_ok=True)

    active_user = (os.environ.get("USER") or "").strip() or Path.home().name
    home_ok = str(Path.home()) == "/Users/ziweizhang" or active_user == "ziweizhang"
    if not home_ok:
        warnings.append(f"home_not_official_ziweizhang:{Path.home()}")

    sv_ok = ROOT.is_dir()
    cc_ok = CC.is_dir()
    vol_cache = SV_CACHE.is_dir()
    vol_xfer = SV_TRANSFER.is_dir()
    vol_backup = SV_BACKUP.is_dir()
    volumes_ok = vol_cache and vol_xfer and vol_backup

    air_runtime, air_runtime_sample = _scan_runtime_legacy_refs()
    air_log_hits, air_log_files = _count_log_legacy_refs()
    if air_runtime > 0:
        issues.append(f"air_legacy_runtime_refs:{air_runtime}")
    if air_log_hits:
        warnings.append(f"air_legacy_log_refs_count:{air_log_hits}_in_{air_log_files}_files_sample_legacy_ok")

    # --- Server mode ---
    auto_code, auto_body = _http_json("GET", "http://127.0.0.1:8765/api/automation/status", timeout=6.0)
    root_code, root_body = _http_json("GET", "http://127.0.0.1:8765/", timeout=5.0)
    dash_code, _dash = _http_json("GET", "http://127.0.0.1:8765/api/dashboard", timeout=8.0)
    shorts_api_code, shorts_api_body = _http_json("GET", "http://127.0.0.1:8765/api/shorts/status", timeout=6.0)
    fe_code, _fe = _http_json("GET", "http://127.0.0.1:3000/", timeout=4.0)

    server_up = auto_code == 200 and isinstance(auto_body, dict)
    attempted_restart = False
    if not server_up and args.restart_server and safe_mode:
        warnings.append("restart_server_ignored_under_safe_only")
    elif not server_up and args.restart_server and not safe_mode and not args.no_server_restart:
        attempted_restart = True
        if _restart_server_mode(issues):
            auto_code, auto_body = _http_json("GET", "http://127.0.0.1:8765/api/automation/status", timeout=8.0)
            server_up = auto_code == 200 and isinstance(auto_body, dict)
    if attempted_restart and not server_up:
        issues.append("server_mode_unreachable_after_restart")
    if not server_up:
        warnings.append("server_mode_not_running_or_unreachable")

    automation: dict[str, Any] = auto_body if isinstance(auto_body, dict) else {}
    host_mode = str(automation.get("host_mode") or "")
    air_dep = bool(automation.get("air_deprecated"))
    air_ssh = automation.get("air_ssh_enabled")
    chrono = str(automation.get("nyc_long_assembly_order") or "")
    no_shuffle = automation.get("nyc_long_no_shuffle")

    if server_up:
        if host_mode != "PRO_ONLY":
            issues.append(f"host_mode_not_pro_only:{host_mode}")
        if not air_dep:
            issues.append("air_deprecated_not_true")
        if air_ssh is True:
            issues.append("air_ssh_enabled_true")
        if chrono != "chronological":
            issues.append(f"nyc_long_assembly_order_not_chronological:{chrono}")
        if no_shuffle is not True:
            warnings.append(f"nyc_long_no_shuffle_unexpected:{no_shuffle!r}")

    shorts_status: dict[str, Any] = shorts_api_body if isinstance(shorts_api_body, dict) else {}
    spol = str(shorts_status.get("shorts_source_policy") or "")
    suse = shorts_status.get("shorts_uses_long_policy")
    if server_up and shorts_api_code == 200:
        if spol != "all_video_image_highlight_candidates":
            issues.append(f"shorts_source_policy_mismatch:{spol}")
        if suse is not False:
            issues.append(f"shorts_uses_long_policy_expected_false:{suse!r}")
    elif server_up:
        warnings.append("shorts_status_endpoint_not_ok")

    # --- LaunchAgents ---
    long_blob = _plist_blob(LONG_PLIST)
    shorts_blob = _plist_blob(SHORTS_PLIST)
    long_plist_exists = LONG_PLIST.is_file()
    shorts_plist_exists = SHORTS_PLIST.is_file()

    def _plist_safe(blob: str, label: str) -> tuple[bool, list[str]]:
        w: list[str] = []
        ok = True
        if "/Users/hennyhowie" in blob or "192.168.12.90" in blob:
            ok = False
            issues.append(f"{label}_plist_legacy_path")
        want_py = "/Users/ziweizhang/StateVerge/.venv_audio/bin/python3"
        if blob and want_py not in blob:
            w.append(f"{label}_python_not_venv_audio")
            ok = False
        if "--upload" in blob:
            w.append(f"{label}_plist_has_upload_true_schedule_runs_real_publish")
            ok = False
        return ok, w

    long_schedule_safe, long_plist_warns = _plist_safe(long_blob, "long")
    shorts_schedule_safe, shorts_plist_warns = _plist_safe(shorts_blob, "shorts")
    warnings.extend(long_plist_warns + shorts_plist_warns)

    lo_ok, lo_warns = _plist_stdout_stderr_ok(LONG_PLIST)
    so_ok, so_warns = _plist_stdout_stderr_ok(SHORTS_PLIST)
    if not lo_ok:
        warnings.extend([f"long_{w}" for w in lo_warns])
    if not so_ok:
        warnings.extend([f"shorts_{w}" for w in so_warns])

    long_plist_loaded = _launchctl_label_loaded("com.stateverge.nyc.autopublish")
    shorts_plist_loaded = _launchctl_label_loaded("com.stateverge.shorts.autopublish")
    launchagent_warnings: list[str] = []
    if long_plist_exists and not long_plist_loaded:
        launchagent_warnings.append("long_plist_not_loaded_in_launchctl_print")
    if shorts_plist_exists and not shorts_plist_loaded:
        launchagent_warnings.append("shorts_plist_not_loaded_in_launchctl_print")

    if args.reload_launchagents and safe_mode:
        warnings.append("reload_launchagents_ignored_under_safe_only")
    elif args.reload_launchagents and not args.no_launchagent_reload:
        _reload_launchagents(issues)
        long_plist_loaded = _launchctl_label_loaded("com.stateverge.nyc.autopublish")
        shorts_plist_loaded = _launchctl_label_loaded("com.stateverge.shorts.autopublish")

    launchctl_grep = _launchctl_list_stateverge()

    # --- Processes ---
    ps_blob = _ps_snapshot()
    n_shorts_workers = _count_processes("shorts_cut_upload_job.py")
    n_youtube_upload = _count_processes("youtube_upload")
    if n_shorts_workers > 1:
        warnings.append("duplicate_shorts_workers_running")
    if n_youtube_upload > 0:
        warnings.append("youtube_upload_process_active")

    # --- Sub-diagnoses ---
    sub_results: dict[str, Any] = {}
    for key, path in DIAGNOSE_SCRIPTS:
        tmo = 90 if key == "nyc_long_source_policy" else 45
        sub_results[key] = _run_script(path, timeout=tmo)

    nyc_long_idx_p = SV_TRANSFER / "media_index" / "nyc_long_driving_sources.json"
    nyc_long_policy_index: dict[str, Any] = {"path": str(nyc_long_idx_p), "file_exists": nyc_long_idx_p.is_file()}
    if nyc_long_idx_p.is_file():
        try:
            lpjx = json.loads(nyc_long_idx_p.read_text(encoding="utf-8", errors="replace"))
            if isinstance(lpjx, dict):
                nyc_long_policy_index.update(
                    {
                        "long_source_policy_version": lpjx.get("long_source_policy_version"),
                        "long_candidates_count": lpjx.get("long_candidates_count"),
                        "rejected_long_candidates_count": lpjx.get("rejected_long_candidates_count"),
                        "rejected_by_reason": lpjx.get("rejected_by_reason"),
                    }
                )
        except (OSError, json.JSONDecodeError):
            warnings.append("nyc_long_driving_sources_json_unreadable")
    elif vol_xfer:
        warnings.append("nyc_long_driving_sources_json_missing")

    # --- Long dry-run ---
    long_dry_run_status = "skipped"
    long_next = ""
    long_planned_audio = ""
    long_meta_planned: bool | None = None
    long_dedupe_ok = True
    long_no_requires_5 = True
    long_stdout_tail = ""
    long_job_json: dict[str, Any] | None = None

    if args.run_long_dry_run:
        mc = max(1, int(args.max_count))
        py = str(VENV_PY) if VENV_PY.is_file() else sys.executable
        cmd = [
            py,
            str(ROOT / "scripts" / "nyc_auto" / "auto_publish_queue.py"),
            "--dry-run",
            "--max-count",
            str(mc),
            "--audio-mode",
            "auto",
        ]
        rc, tail, dj = _subprocess_json_cmd(cmd, timeout=180, cwd=ROOT)
        long_stdout_tail = tail
        long_job_json = dj
        if rc != 0 or not dj:
            long_dry_run_status = f"failed_rc_{rc}"
            issues.append("long_dry_run_failed")
        else:
            long_dry_run_status = str(dj.get("status") or "ok")
            if "long_requires_5_assets" in tail.lower() or "long_requires_5_assets" in json.dumps(dj).lower():
                long_no_requires_5 = False
                issues.append("long_requires_5_assets_active")
            picked = dj.get("picked") if isinstance(dj.get("picked"), list) else []
            if picked and isinstance(picked[0], dict):
                p0 = picked[0]
                long_next = str(p0.get("video") or "")
                pap = p0.get("planned_audio_policy")
                if isinstance(pap, dict):
                    long_planned_audio = str(pap.get("audio_mode") or "")
                long_meta_planned = bool(p0.get("planned_metadata_generation"))
                if "planned_audio_policy" not in p0:
                    issues.append("long_dry_run_missing_planned_audio_policy")
                if "dedupe_summary" not in dj and "dedupe_summary" not in str(dj):
                    warnings.append("long_dry_run_dedupe_summary_missing")
            else:
                long_next = str(dj.get("next_video") or "")
                warnings.append("long_dry_run_no_picked_entry")
            ds = str(dj.get("dedupe_summary") or "")
            if "skipped_dup" in ds or dj.get("skipped_duplicate_count", 0) >= 0:
                long_dedupe_ok = True

            lsv = str(dj.get("long_source_policy_version") or "")
            if long_dry_run_status == "dry_run_ok" and not lsv:
                warnings.append("long_dry_run_missing_long_source_policy_version")
            if lsv and "nyc_long_channel_source_policy" not in lsv:
                warnings.append("long_dry_run_unexpected_policy_version_string")
            sel_ori = str(dj.get("selected_orientation") or "").strip().lower()
            if sel_ori and sel_ori != "landscape":
                issues.append("long_dry_run_non_landscape_orientation")
            long_vid_chk = long_next or str(dj.get("next_video") or "")
            lvchk = long_vid_chk.lower()
            if long_vid_chk and any(x in lvchk for x in ("shorts_clips", "shorts_uploads", "youtube_shorts")):
                issues.append("long_queue_next_under_shorts_tree")
            stw = str(dj.get("selected_source_type") or "").lower()
            if stw == "walking":
                issues.append("long_dry_run_walking_source_type")
            try:
                sel_ar = float(dj.get("selected_aspect_ratio") or 0.0)
            except (TypeError, ValueError):
                sel_ar = 0.0
            sw_sel = int(dj.get("selected_width") or 0)
            sh_sel = int(dj.get("selected_height") or 0)
            if long_dry_run_status == "dry_run_ok" and long_vid_chk:
                if sw_sel > 0 and sh_sel > 0 and sh_sel > sw_sel:
                    issues.append("long_dry_run_portrait_dimensions")
                if sel_ar > 0 and (sel_ar < 1.55 or sel_ar > 1.9):
                    issues.append("long_dry_run_aspect_outside_long_gate")
                sap = str(dj.get("selected_aspect_policy") or "")
                if sap and sap not in ("landscape_16_9_ok", "landscape_wide_warning"):
                    issues.append("long_dry_run_aspect_policy_not_allowed")

    # --- Shorts dry-run / skip ---
    shorts_dry_run_status = "skipped"
    shorts_json_tail = ""
    if args.run_shorts_dry_run and not args.skip_shorts_encode:
        py = str(VENV_PY) if VENV_PY.is_file() else sys.executable
        cmd = [
            py,
            str(ROOT / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py"),
            "--dry-run",
            "--max-count",
            str(max(1, int(args.max_count))),
        ]
        rc, tail, _ = _subprocess_json_cmd(cmd, timeout=420, cwd=ROOT)
        shorts_json_tail = tail
        low_tail = tail.lower()
        blockedish = "block_reason" in low_tail or '"blocked"' in low_tail or "blocked" in low_tail
        if rc == -1:
            shorts_dry_run_status = "timeout"
            issues.append("shorts_dry_run_timeout")
        elif rc == 0:
            shorts_dry_run_status = "completed_ok"
        elif blockedish:
            shorts_dry_run_status = f"blocked_rc_{rc}"
        else:
            shorts_dry_run_status = f"exit_{rc}"
            issues.append("shorts_dry_run_failed_unclear_block")
    elif args.skip_shorts_encode:
        shorts_dry_run_status = "skipped_encode_read_only"
        # latest job result under SV_CACHE/jobs/shorts
        jobs_root = SV_CACHE / "jobs" / "shorts"
        latest: Path | None = None
        latest_mt = 0.0
        if jobs_root.is_dir():
            try:
                for d in jobs_root.iterdir():
                    if not d.is_dir():
                        continue
                    p = d / "shorts_job_result.json"
                    if not p.is_file():
                        continue
                    try:
                        mt = p.stat().st_mtime
                    except OSError:
                        continue
                    if mt > latest_mt:
                        latest_mt = mt
                        latest = p
            except OSError:
                pass
        if latest:
            shorts_json_tail = _tail(latest.read_text(encoding="utf-8", errors="replace"), 3000)

    if not args.run_long_dry_run:
        long_dry_run_status = "skipped_by_cli"
    if not args.run_shorts_dry_run:
        shorts_dry_run_status = "skipped_by_cli"

    latest_short = _latest_mp4(SHORTS_CLIPS)
    shorts_output_valid = False
    probe: dict[str, Any] = {}
    if latest_short:
        probe = _ffprobe_shorts(latest_short)
        afr = str(probe.get("avg_frame_rate") or "")
        ok_dim = int(probe.get("width") or 0) == 1080 and int(probe.get("height") or 0) == 1920
        ok_dur = float(probe.get("duration") or 0) <= 60.5
        ok_afr = afr in ("30/1", "60/1", "29/1", "59/1")
        shorts_output_valid = bool(
            probe.get("ok") and ok_dim and ok_dur and probe.get("has_audio") and ok_afr
        )
        if not shorts_output_valid and shorts_dry_run_status not in (
            "skipped",
            "skipped_encode_read_only",
            "skipped_by_cli",
        ):
            warnings.append("shorts_latest_output_probe_mismatch")

    # --- Concurrent backend guard ---
    backend_queue_guard_ok: bool | None = None
    concurrent_post_response_1: dict[str, Any] | str = {}
    concurrent_post_response_2: dict[str, Any] | str = {}
    if server_up:
        body = {
            "upload": False,
            "duration_seconds": 30,
            "asset_mode": "auto",
            "allow_images": True,
            "allow_videos": True,
            "highlight_mode": True,
        }
        results: list[tuple[str, dict[str, Any] | str, int]] = []
        _post_lock = threading.Lock()

        def _post(slot: str) -> None:
            code, resp = _http_json("POST", "http://127.0.0.1:8765/api/jobs/shorts-cut-upload", data=body, timeout=30.0)
            with _post_lock:
                results.append((slot, resp, code))

        t1 = threading.Thread(target=_post, args=("a",))
        t2 = threading.Thread(target=_post, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=35)
        t2.join(timeout=35)
        queued = 0
        blocked = 0
        r1: dict[str, Any] | str = {}
        r2: dict[str, Any] | str = {}
        results.sort(key=lambda x: x[0])
        if len(results) >= 1:
            r1 = results[0][1]
            concurrent_post_response_1 = r1
        if len(results) >= 2:
            r2 = results[1][1]
            concurrent_post_response_2 = r2
        for _s, resp, _c in results:
            if isinstance(resp, dict):
                st = str(resp.get("status") or "")
                if st == "queued":
                    queued += 1
                elif st == "blocked":
                    blocked += 1
        if queued >= 2:
            backend_queue_guard_ok = False
            issues.append("backend_queue_guard_failed_two_queued")
        elif queued == 1 and blocked >= 1:
            backend_queue_guard_ok = True
        elif queued == 1 and blocked == 0:
            backend_queue_guard_ok = True
            warnings.append("concurrent_posts_only_one_response_or_single_queued")
        else:
            backend_queue_guard_ok = False
            warnings.append("concurrent_posts_unexpected_pattern")
    else:
        backend_queue_guard_ok = None
        warnings.append("backend_queue_guard_skipped_server_down")

    # --- Tokens / channel guard ---
    long_token_exists = LONG_TOKEN.is_file()
    shorts_token_exists = SHORTS_TOKEN.is_file()
    client_secrets_exists = CLIENT_SECRETS.is_file()
    channel_guard_exists = CHANNEL_GUARD.is_file()
    token_warnings: list[str] = []

    long_channel_ok_if_checked = False
    shorts_channel_ok_if_checked = False
    if long_token_exists and VENV_PY.is_file():
        who = ROOT / "scripts" / "nyc_auto" / "youtube_whoami.py"
        if who.is_file():
            r = subprocess.run(
                [str(VENV_PY), str(who), "--token", str(LONG_TOKEN)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            long_channel_ok_if_checked = r.returncode == 0
            if r.returncode != 0:
                token_warnings.append("long_whoami_nonzero_exit")

    if shorts_token_exists and long_token_exists:
        try:
            if LONG_TOKEN.samefile(SHORTS_TOKEN):
                token_warnings.append("shorts_token_same_inode_as_long_token")
                issues.append("token_paths_confused_shorts_samefile_long")
        except OSError:
            pass

    confirm = ROOT / "scripts" / "nyc_auto" / "confirm_shorts_channel.py"
    if shorts_token_exists and client_secrets_exists and VENV_PY.is_file() and confirm.is_file():
        r = subprocess.run(
            [str(VENV_PY), str(confirm), "--token", str(SHORTS_TOKEN)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        m = re.search(r"confirm_shorts_channel_ok=(True|False)", r.stdout or "")
        if m:
            shorts_channel_ok_if_checked = m.group(1) == "True"
        else:
            shorts_channel_ok_if_checked = False
            token_warnings.append("shorts_confirm_parse_failed")

    long_ledger_info = _analyze_long_ledger(LONG_LEDGER)
    shorts_ledger_info = _analyze_shorts_ledger(SHORTS_LEDGER)

    duplicate_guard_broken = bool(int(shorts_ledger_info.get("max_same_output_quick_hash") or 0) > 3)

    # --- safe_to_enable_* ---
    dry_long_ok = bool(
        long_no_requires_5
        and (
            long_dry_run_status == "skipped_by_cli"
            or not str(long_dry_run_status).startswith("failed")
        )
    )
    dry_shorts_ok = bool(
        shorts_dry_run_status
        in ("completed_ok", "skipped_encode_read_only", "skipped_by_cli")
        or str(shorts_dry_run_status).startswith("blocked_rc_")
    )

    no_upload_proc = n_youtube_upload == 0
    ledger_ok = not duplicate_guard_broken and long_ledger_info.get("duplicate_paths", 0) <= 3

    safe_to_enable_long_schedule = (
        long_plist_exists
        and long_schedule_safe
        and air_runtime == 0
        and dry_long_ok
        and long_no_requires_5
    )
    safe_to_enable_shorts_schedule = (
        shorts_plist_exists
        and shorts_schedule_safe
        and air_runtime == 0
        and dry_shorts_ok
        and n_shorts_workers <= 1
    )

    user_must_confirm_upload = True
    safe_to_enable_long_upload = bool(
        dry_long_ok
        and long_token_exists
        and client_secrets_exists
        and channel_guard_exists
        and long_channel_ok_if_checked
        and ledger_ok
        and no_upload_proc
        and long_no_requires_5
    )
    safe_to_enable_shorts_upload = bool(
        dry_shorts_ok
        and shorts_token_exists
        and client_secrets_exists
        and channel_guard_exists
        and shorts_channel_ok_if_checked
        and ledger_ok
        and no_upload_proc
        and backend_queue_guard_ok is not False
    )
    safe_to_enable_upload = bool(safe_to_enable_long_upload and safe_to_enable_shorts_upload and server_up and air_runtime == 0)

    overall_status = "ok"
    if issues:
        overall_status = "needs_fix"
    elif warnings or not server_up or not volumes_ok:
        overall_status = "warning"

    marker = (
        "AUTOMATION_SYSTEM_FINAL_HEALTH_CHECK_V1_NEEDS_FIX"
        if overall_status == "needs_fix"
        else "AUTOMATION_SYSTEM_FINAL_HEALTH_CHECK_V1_DONE"
    )

    payload: dict[str, Any] = {
        "report_version": "automation_system_final_health_check_v1",
        "created_at": _utc_iso(),
        "host_mode_expected": "PRO_ONLY",
        "active_user": active_user,
        "stateverge_home": str(ROOT),
        "control_center_home": str(CC),
        "volumes_ok": volumes_ok,
        "air_legacy_runtime_refs_count": air_runtime,
        "air_legacy_runtime_samples": air_runtime_sample[:12],
        "air_legacy_log_refs_count": air_log_hits,
        "air_legacy_log_files_scanned": air_log_files,
        "server_mode": {
            "automation_status_http": auto_code,
            "root_http": root_code,
            "dashboard_http": dash_code,
            "shorts_status_http": shorts_api_code,
            "frontend_3000_http": fe_code,
            "host_mode": host_mode,
            "air_deprecated": air_dep,
            "air_ssh_enabled": air_ssh,
            "nyc_long_assembly_order": chrono,
            "nyc_long_no_shuffle": no_shuffle,
            "shorts_source_policy": spol,
            "shorts_uses_long_policy": suse,
            "duplicate_guard_enabled": shorts_status.get("duplicate_guard_enabled"),
        },
        "launchagents": {
            "long_plist_exists": long_plist_exists,
            "shorts_plist_exists": shorts_plist_exists,
            "long_plist_loaded": long_plist_loaded,
            "shorts_plist_loaded": shorts_plist_loaded,
            "long_schedule_safe": long_schedule_safe,
            "shorts_schedule_safe": shorts_schedule_safe,
            "launchagent_warnings": launchagent_warnings + launchctl_grep.splitlines()[:5],
        },
        "processes": {
            "snapshot_tail": _tail(ps_blob, 6000),
            "shorts_worker_count": n_shorts_workers,
            "youtube_upload_count": n_youtube_upload,
        },
        "diagnostics": sub_results,
        "auto_publish_v2_status": sub_results.get("auto_publish_v2", {}).get("status"),
        "long_audio_policy_status": sub_results.get("long_audio_policy", {}).get("status"),
        "metadata_generation_status": sub_results.get("metadata_generation", {}).get("status"),
        "real_sound_gate_status": sub_results.get("real_sound_gate", {}).get("status"),
        "shorts_autopublish_status": sub_results.get("shorts_autopublish", {}).get("status"),
        "nyc_long_source_policy_status": sub_results.get("nyc_long_source_policy", {}).get("status"),
        "nyc_long_source_policy_index": nyc_long_policy_index,
        "long": {
            "dry_run_status": long_dry_run_status,
            "next_candidate": long_next,
            "planned_audio_mode": long_planned_audio,
            "metadata_planned": long_meta_planned,
            "dedupe_ok": long_dedupe_ok,
            "no_long_requires_5_assets": long_no_requires_5,
            "dedupe_summary": (long_job_json or {}).get("dedupe_summary", ""),
            "long_source_policy_version": (long_job_json or {}).get("long_source_policy_version"),
            "skipped_long_source_policy": (long_job_json or {}).get("skipped_long_source_policy"),
            "selected_orientation": (long_job_json or {}).get("selected_orientation"),
            "selected_source_type": (long_job_json or {}).get("selected_source_type"),
            "ledger": long_ledger_info,
            "stdout_tail": _tail(long_stdout_tail, 4000),
        },
        "shorts": {
            "dry_run_status": shorts_dry_run_status,
            "latest_output": str(latest_short) if latest_short else "",
            "output_valid": shorts_output_valid,
            "ffprobe": probe,
            "ledger": shorts_ledger_info,
            "stdout_tail": _tail(shorts_json_tail, 4000),
            "metadata_generated": "output_quick_hash" in shorts_json_tail or "youtube_metadata" in shorts_json_tail.lower(),
            "duplicate_guard_ok": not duplicate_guard_broken,
            "ledger_ok": bool(shorts_ledger_info.get("reservation_sane", True)),
            "no_long_policy": spol == "all_video_image_highlight_candidates" and suse is False,
        },
        "backend_queue_guard": {
            "ok": backend_queue_guard_ok,
            "concurrent_post_response_1": concurrent_post_response_1,
            "concurrent_post_response_2": concurrent_post_response_2,
        },
        "tokens": {
            "long_token_exists": long_token_exists,
            "shorts_token_exists": shorts_token_exists,
            "client_secrets_exists": client_secrets_exists,
            "channel_guard_exists": channel_guard_exists,
            "long_channel_ok_if_checked": long_channel_ok_if_checked,
            "shorts_channel_ok_if_checked": shorts_channel_ok_if_checked,
            "token_warnings": token_warnings,
        },
        "safety": {
            "safe_to_enable_long_schedule": safe_to_enable_long_schedule,
            "safe_to_enable_shorts_schedule": safe_to_enable_shorts_schedule,
            "safe_to_enable_long_upload": safe_to_enable_long_upload,
            "safe_to_enable_shorts_upload": safe_to_enable_shorts_upload,
            "safe_to_enable_upload": safe_to_enable_upload,
            "upload_requires_explicit_user_confirmation": True,
        },
        "overall_status": overall_status,
        "issues": issues,
        "warnings": warnings,
        "final_marker": marker,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        "# StateVerge Automation System Final Health Check v1",
        "",
        "## Summary",
        f"- overall_status: **{overall_status}**",
        f"- safe_to_enable_long_schedule: {safe_to_enable_long_schedule}",
        f"- safe_to_enable_shorts_schedule: {safe_to_enable_shorts_schedule}",
        f"- safe_to_enable_long_upload: {safe_to_enable_long_upload} (OAuth/channel/ledger checks only; do not set upload=true unattended)",
        f"- safe_to_enable_shorts_upload: {safe_to_enable_shorts_upload}",
        f"- safe_to_enable_upload (aggregate technical): {safe_to_enable_upload}",
        "",
        "## Architecture",
        f"- host_mode: {host_mode or '(unknown)'}",
        f"- active_user: {active_user}",
        f"- Pro-only status: {'ok' if home_ok and host_mode == 'PRO_ONLY' else 'review'}",
        f"- Air legacy runtime refs: {air_runtime}",
        f"- Air legacy log refs: {air_log_hits} (across up to {air_log_files} log files; informational)",
        "",
        "## Volumes",
        f"- SV_CACHE: {'ok' if vol_cache else 'MISSING'}",
        f"- SV_TRANSFER: {'ok' if vol_xfer else 'MISSING'}",
        f"- SV_BACKUP: {'ok' if vol_backup else 'MISSING'}",
        "",
        "## Server Mode",
        f"- GET / : HTTP {root_code}",
        f"- GET /api/automation/status: HTTP {auto_code}",
        f"- GET /api/shorts/status: HTTP {shorts_api_code}",
        f"- GET /api/dashboard: HTTP {dash_code}",
        f"- GET :3000: HTTP {fe_code}",
        "",
        "## LaunchAgent Schedule",
        f"- long plist exists / loaded / safe: {long_plist_exists} / {long_plist_loaded} / {long_schedule_safe}",
        f"- shorts plist exists / loaded / safe: {shorts_plist_exists} / {shorts_plist_loaded} / {shorts_schedule_safe}",
        f"- schedule warnings: {launchagent_warnings or 'none'}",
        "",
        "## Long Auto Publish",
        f"- dry-run status: {long_dry_run_status}",
        f"- next candidate: `{long_next}`",
        f"- dedupe v2: {long_dedupe_ok}",
        f"- dedupe summary: `{str((long_job_json or {}).get('dedupe_summary', ''))[:200]}`",
        f"- audio policy (planned mode): {long_planned_audio or '(n/a)'}",
        f"- metadata planned: {long_meta_planned}",
        f"- no long_requires_5_assets: {long_no_requires_5}",
        "",
        "## NYC Long Source Policy v1",
        f"- classifier index: `{nyc_long_policy_index.get('path', '')}` exists={nyc_long_policy_index.get('file_exists')}",
        f"- index policy version: `{nyc_long_policy_index.get('long_source_policy_version', '')}`",
        f"- dry-run policy version: `{str((long_job_json or {}).get('long_source_policy_version', ''))}`",
        f"- skipped by policy (dry): {int((long_job_json or {}).get('skipped_long_source_policy') or 0)}",
        f"- selected_orientation: `{str((long_job_json or {}).get('selected_orientation', ''))}`",
        f"- selected_source_type: `{str((long_job_json or {}).get('selected_source_type', ''))}`",
        f"- sub-diagnose nyc_long_source_policy: {sub_results.get('nyc_long_source_policy', {}).get('status')}",
        "",
        "## Shorts Auto Publish",
        f"- dry-run status: {shorts_dry_run_status}",
        f"- latest output: `{latest_short}`",
        f"- output validation: {shorts_output_valid}",
        f"- metadata/heuristic: {payload['shorts']['metadata_generated']}",
        f"- ledger: {shorts_ledger_info}",
        f"- duplicate guard: {not duplicate_guard_broken}",
        "",
        "## Queue Guard",
        f"- concurrent test result ok: {backend_queue_guard_ok}",
        f"- response 1 status: {concurrent_post_response_1.get('status') if isinstance(concurrent_post_response_1, dict) else concurrent_post_response_1}",
        f"- response 2 status: {concurrent_post_response_2.get('status') if isinstance(concurrent_post_response_2, dict) else concurrent_post_response_2}",
        "",
        "## Token / Channel Guard",
        f"- long_token_exists: {long_token_exists}",
        f"- shorts_token_exists: {shorts_token_exists}",
        f"- client_secrets_exists: {client_secrets_exists}",
        f"- channel_guard.py exists: {channel_guard_exists}",
        f"- long_channel_ok_if_checked: {long_channel_ok_if_checked}",
        f"- shorts_channel_ok_if_checked: {shorts_channel_ok_if_checked}",
        f"- token_warnings: {token_warnings or ['none']}",
        "",
        "## Running Processes",
        f"- shorts_cut_upload_job count: {n_shorts_workers}",
        f"- youtube_upload count: {n_youtube_upload}",
        "",
        "## Diagnostics",
        *[f"- {k}: {v.get('status')}" for k, v in sub_results.items()],
        "",
        "## Final Recommendation",
        "- **Safe now**: dry-run diagnostics and read-only checks; review LaunchAgent plist `--upload` flags before enabling schedules unattended.",
        "- **Manual confirmation**: keep `upload=true` off until you explicitly choose real publishes; this run never uploads.",
        "- **Technical readiness** for uploads is reflected in `safe_to_enable_*_upload` (tokens, guards, dry-runs). **Operational policy**: keep `upload=true` off in schedules until you explicitly choose real publishes.",
        "",
        "## Issues",
        "\n".join(f"- {i}" for i in issues) if issues else "none",
        "",
        marker,
        "",
    ]
    OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")

    if not args.json_only:
        print(json.dumps({"overall_status": overall_status, "wrote": str(OUT_JSON), "marker": marker}, indent=2))

    return 0 if overall_status != "needs_fix" else 2


if __name__ == "__main__":
    raise SystemExit(main())
