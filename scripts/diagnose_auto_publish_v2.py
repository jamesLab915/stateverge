#!/usr/bin/env python3
"""Auto publish v2 system diagnosis — writes JSON + MD under StateVerge_Control_Center/logs/."""
from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
NYC_AUTO = ROOT / "scripts" / "nyc_auto"
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for p in (SRC, SCRIPTS, NYC_AUTO):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

CONTROL = Path.home() / "StateVerge_Control_Center"
LOGS = CONTROL / "logs"
OUT_JSON = LOGS / "auto_publish_v2_diagnose.json"
OUT_MD = LOGS / "auto_publish_v2_diagnose.md"
ALLOWED_TASKS = CONTROL / "remote_agent" / "allowed_tasks.json"
LAUNCH = CONTROL / "launchagents"

SV_TRANSFER = Path("/Volumes/SV_TRANSFER")
SV_CACHE = Path("/Volumes/SV_CACHE")
HOME_LONG_LEDGER = Path.home() / "StateVerge" / "data" / "long_runtime" / "long_uploads" / "long_used_assets.json"
HOME_SHORTS_LEDGER = Path.home() / "StateVerge" / "data" / "shorts_runtime" / "shorts_uploads" / "shorts_used_assets.json"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _launchctl_print(label: str) -> tuple[int, str]:
    uid = os.getuid()
    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 99, str(exc)


def _plist_schedule(path: Path) -> list[dict[str, int]]:
    if not path.is_file():
        return []
    try:
        with path.open("rb") as fh:
            pl = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException):
        return []
    out: list[dict[str, int]] = []
    sci = pl.get("StartCalendarInterval")
    if isinstance(sci, dict):
        sci = [sci]
    if isinstance(sci, list):
        for ent in sci:
            if isinstance(ent, dict):
                h = int(ent.get("Hour", -1))
                m = int(ent.get("Minute", -1))
                if h >= 0 and m >= 0:
                    out.append({"hour": h, "minute": m})
    return out


def _plist_python_path(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as fh:
            pl = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException):
        return ""
    args = pl.get("ProgramArguments")
    if not isinstance(args, list):
        return ""
    blob = " ".join(str(a) for a in args)
    m = re.search(r"(/[^\s]+/\.venv_audio/bin/python3)", blob)
    if m:
        return m.group(1)
    if "/usr/bin/python3" in blob:
        return "/usr/bin/python3"
    return ""


def _plist_program_arguments(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        with path.open("rb") as fh:
            pl = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException):
        return []
    args = pl.get("ProgramArguments")
    if not isinstance(args, list):
        return []
    return [str(a) for a in args]


def _argv_public_or_allow_public(argv: list[str]) -> bool:
    low = [str(a).lower() for a in argv]
    if "--allow-public" in low:
        return True
    return "public" in low


def _argv_privacy_status_value(argv: list[str]) -> str:
    low = [str(a).lower() for a in argv]
    try:
        i = low.index("--privacy-status")
        if i + 1 < len(low):
            return low[i + 1]
    except ValueError:
        pass
    return ""


def _launchagent_unlisted_only(argv: list[str]) -> bool:
    if not argv:
        return False
    if _argv_public_or_allow_public(argv):
        return False
    return _argv_privacy_status_value(argv) == "unlisted"


def _http_code(url: str, timeout: float = 3.0) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), str(exc)
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def _count_json_matches(root: Path, name: str) -> int:
    n = 0
    if not root.is_dir():
        return 0
    try:
        for p in root.rglob(name):
            if p.is_file():
                n += 1
    except OSError:
        pass
    return n


FORBIDDEN_REF_SUBSTRINGS = (
    "/Users/" + "henny" + "howie",
    "/Volumes/SV_" + "WORK",
)
LONG_DEDUPE_BASELINE_BAD_BASENAMES = frozenset(
    {
        "ywan_no_vocals_mux_20260510t131108z.mp4",
        "yewan_no_vocals_mux_20260510t131108z.mp4",
    }
)


def _upload_result_paths_diag(uj: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in ("source_path", "source_video", "video", "path", "resolved_path", "upload_video"):
        v = uj.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def _add_basename_lower(out: set[str], path_str: str) -> None:
    s = path_str.strip()
    if not s:
        return
    out.add(Path(s).name.lower())


def _uploaded_basenames_from_ledger_file(path: Path) -> set[str]:
    out: set[str] = set()
    if not path.is_file():
        return out
    try:
        d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(d, dict):
        return out
    for e in d.get("entries") or []:
        if not isinstance(e, dict) or not e.get("uploaded"):
            continue
        for key in ("resolved_path", "source_path", "path", "result_path"):
            v = str(e.get(key) or "").strip()
            if v:
                _add_basename_lower(out, v)
    return out


def _collect_uploaded_basenames_history(roots: list[Path]) -> set[str]:
    """Match ``auto_publish_queue`` history scan: paths tied to successful uploads."""
    out: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for jpath in root.rglob("long_job_result.json"):
                try:
                    dj = json.loads(jpath.read_text(encoding="utf-8", errors="replace"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(dj, dict):
                    continue
                uploads = dj.get("uploads") if isinstance(dj.get("uploads"), list) else []
                uploaded_flag = dj.get("uploaded") is True
                success = bool(uploaded_flag)
                top_vid = str(dj.get("youtube_video_id") or "").strip()
                for u in uploads:
                    if not isinstance(u, dict):
                        continue
                    vid = str(u.get("video_id") or "").strip()
                    if u.get("ok") and vid:
                        success = True
                        vp = str(u.get("video") or "").strip()
                        if vp:
                            _add_basename_lower(out, vp)
                if success:
                    for u in uploads:
                        if not isinstance(u, dict):
                            continue
                        vp = str(u.get("video") or "").strip()
                        if vp and u.get("ok"):
                            _add_basename_lower(out, vp)
                    for p in dj.get("picked") or []:
                        if isinstance(p, dict):
                            vp = str(p.get("video") or p.get("path") or "").strip()
                            if vp:
                                _add_basename_lower(out, vp)
                    nv = str(dj.get("next_video") or "").strip()
                    if nv and (uploaded_flag or top_vid):
                        _add_basename_lower(out, nv)

            for upath in root.rglob("upload_result.json"):
                try:
                    uj = json.loads(upath.read_text(encoding="utf-8", errors="replace"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(uj, dict):
                    continue
                vid = str(uj.get("video_id") or uj.get("youtube_video_id") or "").strip()
                uploaded_ok = bool(uj.get("ok")) and bool(vid)
                uploaded_flag = uj.get("uploaded") is True
                if not (uploaded_ok or uploaded_flag):
                    continue
                for vp in _upload_result_paths_diag(uj):
                    _add_basename_lower(out, vp)
                sel = upath.parent / "selected_video_path.txt"
                if sel.is_file():
                    try:
                        for line in sel.read_text(encoding="utf-8", errors="replace").splitlines():
                            s = line.strip()
                            if s:
                                _add_basename_lower(out, s)
                                break
                    except OSError:
                        pass
        except OSError:
            continue
    return out


def _ledger_entry_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(d, dict):
        return 0
    ent = d.get("entries")
    return len(ent) if isinstance(ent, list) else 0


def _venv_python() -> Path:
    p = ROOT / ".venv_audio" / "bin" / "python3"
    return p if p.is_file() else Path(sys.executable)


def _scan_nyc_long_clips_masters(clips_dir: Path, *, min_duration_sec: float = 3600.0) -> dict[str, Any]:
    """Probe every nyc_long_master*.mp4; count skips but continue on errors (no under-count)."""
    import auto_publish_queue as apq  # noqa: E402

    out: dict[str, Any] = {
        "clips_dir": str(clips_dir),
        "scanned": 0,
        "qualified_count": 0,
        "skipped_probe_failed": 0,
        "skipped_too_short": 0,
        "skipped_corrupt": 0,
        "candidate_scan_error": 0,
        "latest_qualified_path": "",
        "latest_qualified_duration_sec": 0.0,
        "samples": [],
    }
    if not clips_dir.is_dir():
        out["error"] = "clips_dir_missing"
        return out

    def _mtime_key(p: Path) -> float:
        try:
            return float(p.stat().st_mtime)
        except OSError:
            return 0.0

    paths = sorted(clips_dir.glob("nyc_long_master*.mp4"), key=_mtime_key)
    for p in paths:
        if not p.is_file() or p.name.startswith("._"):
            continue
        if ".tmp." in p.name.lower():
            continue
        out["scanned"] += 1
        try:
            probe = apq._ffprobe_json(p)  # noqa: SLF001
            if probe is None:
                out["skipped_probe_failed"] += 1
                out["skipped_corrupt"] += 1
                out["samples"].append({"path": str(p), "skip": "skipped_probe_failed"})
                continue
            dur, has_v = apq._duration_and_has_video(probe)  # noqa: SLF001
            if not has_v or dur is None:
                out["skipped_probe_failed"] += 1
                out["samples"].append({"path": str(p), "skip": "skipped_probe_failed", "reason": "no_video"})
                continue
            if float(dur) < float(min_duration_sec):
                out["skipped_too_short"] += 1
                out["samples"].append({"path": str(p), "skip": "skipped_too_short", "duration_sec": float(dur)})
                continue
            w, h = apq._primary_video_dims(probe)  # noqa: SLF001
            out["qualified_count"] += 1
            out["latest_qualified_path"] = str(p)
            out["latest_qualified_duration_sec"] = float(dur)
            out["samples"].append(
                {
                    "path": str(p),
                    "qualified": True,
                    "duration_sec": float(dur),
                    "width": w,
                    "height": h,
                }
            )
        except OSError as exc:
            out["candidate_scan_error"] += 1
            out["samples"].append({"path": str(p), "skip": "candidate_scan_error", "error": repr(exc)})
        except Exception as exc:  # noqa: BLE001
            out["candidate_scan_error"] += 1
            out["samples"].append({"path": str(p), "skip": "candidate_scan_error", "error": repr(exc)})

    if len(out["samples"]) > 40:
        out["samples"] = out["samples"][-40:]
    return out


def _json_from_script(script: Path, argv: list[str]) -> dict[str, Any]:
    r = subprocess.run(
        [str(_venv_python()), str(script)] + argv,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    raw_out = (r.stdout or "").strip()
    try:
        return json.loads(raw_out)
    except json.JSONDecodeError:
        return {
            "_parse_error": True,
            "returncode": r.returncode,
            "stdout_tail": raw_out[-6000:],
            "stderr_tail": (r.stderr or "")[-4000:],
        }


def _latest_shorts_job_snapshot() -> dict[str, Any]:
    """Latest ``shorts_job_result.json`` under pack roots (no encode — avoids minutes-long dry-run)."""
    roots = [
        SV_TRANSFER / "publish_pack" / "shorts_uploads",
        Path.home() / "StateVerge" / "data" / "shorts_runtime" / "shorts_uploads",
    ]
    best: Path | None = None
    best_mt = 0.0
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("shorts_job_result.json"):
                try:
                    mt = float(p.stat().st_mtime)
                except OSError:
                    continue
                if mt > best_mt:
                    best_mt = mt
                    best = p
        except OSError:
            continue
    if not best:
        return {"status": "unknown", "note": "no_shorts_job_result_found"}
    try:
        out = json.loads(best.read_text(encoding="utf-8", errors="replace"))
        if isinstance(out, dict):
            out["_snapshot_path"] = str(best)
            return out
    except (OSError, json.JSONDecodeError):
        pass
    return {"status": "unknown", "note": "shorts_job_result_unreadable", "path": str(best)}


def _run_shorts_dry_diagnose() -> dict[str, Any]:
    """Optional live dry-run when ``STATEVERGE_DIAGNOSE_RUN_SHORTS_DRY=1`` (full encode)."""
    if os.environ.get("STATEVERGE_DIAGNOSE_RUN_SHORTS_DRY", "").strip().lower() not in ("1", "true", "yes"):
        snap = _latest_shorts_job_snapshot()
        snap["_shorts_dry_mode"] = "latest_snapshot_only_set_STATEVERGE_DIAGNOSE_RUN_SHORTS_DRY=1_to_encode"
        return snap
    job_id = f"diag{uuid.uuid4().hex[:12]}"
    r = subprocess.run(
        [
            str(_venv_python()),
            str(ROOT / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py"),
            "--dry-run",
            "--job-id",
            job_id,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=5400,
        check=False,
    )
    roots = [
        SV_TRANSFER / "publish_pack" / "shorts_uploads",
        Path.home() / "StateVerge" / "data" / "shorts_runtime" / "shorts_uploads",
    ]
    for root in roots:
        p = root / job_id / "shorts_job_result.json"
        if p.is_file():
            try:
                out = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                if isinstance(out, dict):
                    out["_subprocess_returncode"] = r.returncode
                    out["_shorts_dry_mode"] = "live_encode"
                    return out
            except (OSError, json.JSONDecodeError):
                continue
    return {
        "status": "unknown",
        "block_reason": "shorts_job_result_not_found",
        "_subprocess_returncode": r.returncode,
        "_shorts_dry_mode": "live_encode",
        "stderr_tail": (r.stderr or "")[-3000:],
    }


def main() -> int:
    try:
        from utils.storage_paths import get_sv_transfer  # noqa: E402
    except Exception:

        def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
            return Path("/Volumes/SV_TRANSFER")

    warnings: list[str] = []
    critical: list[str] = []
    wwarn: list[str] = []

    scripts = {
        "auto_publish_queue.py": ROOT / "scripts/nyc_auto/auto_publish_queue.py",
        "auto_publish_queue_shorts.py": ROOT / "scripts/nyc_auto/auto_publish_queue_shorts.py",
        "youtube_upload_direct_nyc.py": ROOT / "scripts/nyc_auto/youtube_upload_direct_nyc.py",
        "youtube_upload_direct_shorts.py": ROOT / "scripts/nyc_auto/youtube_upload_direct_shorts.py",
        "channel_guard.py": ROOT / "scripts/nyc_auto/channel_guard.py",
        "diagnose_auto_publish_v2.py": ROOT / "scripts/diagnose_auto_publish_v2.py",
    }
    scripts_ok = {k: v.is_file() for k, v in scripts.items()}
    if not scripts_ok.get("channel_guard.py"):
        critical.append("channel_guard_missing")

    for rel, sp in scripts.items():
        if not sp.is_file():
            continue
        try:
            blob = sp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            wwarn.append(f"script_unreadable_forbidden_scan:{rel}")
            continue
        for needle in FORBIDDEN_REF_SUBSTRINGS:
            if needle in blob:
                critical.append(f"forbidden_path_ref_in_script:{rel}:{needle}")

    tok_long = Path.home() / "StateVerge/data/youtube/token.json"
    tok_shorts = Path.home() / "StateVerge/data/youtube/token_shorts.json"
    tokens_ok = {"token.json": tok_long.is_file(), "token_shorts.json": tok_shorts.is_file()}
    if not tokens_ok["token.json"]:
        critical.append("token_long_missing")
    if not tokens_ok["token_shorts.json"]:
        critical.append("token_shorts_missing")

    uid = os.getuid()
    labels = {
        "com.stateverge.nyc.autopublish": "nyc_long",
        "com.stateverge.shorts.autopublish": "shorts",
        "com.stateverge.mobile.command.agent": "mobile_agent",
    }
    launch: dict[str, Any] = {}
    missing_agents = 0
    for lab, key in labels.items():
        code, txt = _launchctl_print(lab)
        loaded = "path = " in txt and "could not find" not in txt.lower()
        launch[key] = {"label": lab, "print_exit": code, "loaded_guess": loaded, "tail": txt[:1200]}
        if not loaded:
            missing_agents += 1

    if missing_agents >= 2 and not launch.get("nyc_long", {}).get("loaded_guess"):
        wwarn.append("launchagent_print_unclear")

    nyc_plist = LAUNCH / "com.stateverge.nyc.autopublish.plist"
    sh_plist = LAUNCH / "com.stateverge.shorts.autopublish.plist"
    nyc_sched = _plist_schedule(nyc_plist)
    sh_sched = _plist_schedule(sh_plist)
    long_py = _plist_python_path(nyc_plist)
    shorts_py = _plist_python_path(sh_plist)
    long_plist_ok = "venv_audio" in long_py or bool(long_py)
    shorts_plist_ok = "venv_audio" in shorts_py

    long_ledger_p = SV_TRANSFER / "publish_pack" / "nyc_long_uploads" / "long_used_assets.json"
    shorts_ledger_p = SV_TRANSFER / "publish_pack" / "shorts_uploads" / "shorts_used_assets.json"
    long_ledger = long_ledger_p if long_ledger_p.is_file() else HOME_LONG_LEDGER
    shorts_ledger = shorts_ledger_p if shorts_ledger_p.is_file() else HOME_SHORTS_LEDGER
    long_fb = not long_ledger_p.is_file() and HOME_LONG_LEDGER.is_file()
    shorts_fb = not shorts_ledger_p.is_file() and HOME_SHORTS_LEDGER.is_file()

    try:
        ld_long = json.loads(long_ledger.read_text(encoding="utf-8", errors="replace")) if long_ledger.is_file() else {}
        if not isinstance(ld_long, dict):
            ld_long = {}
    except (OSError, json.JSONDecodeError):
        ld_long = {}
    ld_shorts: dict[str, Any] = {}
    if shorts_ledger.is_file():
        try:
            ld_shorts = json.loads(shorts_ledger.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            ld_shorts = {}
    le_long = len(ld_long.get("entries") or []) if isinstance(ld_long.get("entries"), list) else 0
    le_long_primary = _ledger_entry_count(long_ledger_p)
    le_long_home = _ledger_entry_count(HOME_LONG_LEDGER)
    le_shorts = len(ld_shorts.get("entries") or []) if isinstance(ld_shorts.get("entries"), list) else 0

    xfer = get_sv_transfer(verbose=False)

    long_dry = _json_from_script(ROOT / "scripts" / "nyc_auto" / "auto_publish_queue.py", ["--dry-run", "--max-count", "1"])
    long_dry_parse_error = bool(long_dry.get("_parse_error"))
    if long_dry_parse_error:
        wwarn.append("long_dry_run_json_parse_error_using_local_master_scan")

    clips_dir = xfer / "ready_to_upload" / "nyc_long_clips"
    long_master_scan = _scan_nyc_long_clips_masters(clips_dir)

    shorts_dry = _run_shorts_dry_diagnose()

    long_sources_p = xfer / "media_index" / "nyc_long_driving_sources.json"
    long_policy_index: dict[str, Any] = {"path": str(long_sources_p), "file_exists": long_sources_p.is_file()}
    if long_sources_p.is_file():
        try:
            lpj = json.loads(long_sources_p.read_text(encoding="utf-8", errors="replace"))
            if isinstance(lpj, dict):
                long_policy_index.update(
                    {
                        "long_source_policy_version": lpj.get("long_source_policy_version"),
                        "long_candidates_count": lpj.get("long_candidates_count"),
                        "rejected_long_candidates_count": lpj.get("rejected_long_candidates_count"),
                        "rejected_by_reason": lpj.get("rejected_by_reason"),
                    }
                )
        except (OSError, json.JSONDecodeError):
            wwarn.append("nyc_long_driving_sources_json_unreadable")
    else:
        wwarn.append("nyc_long_driving_sources_json_missing")

    lpol_ver = str(long_dry.get("long_source_policy_version") or "")
    lstat = str(long_dry.get("status") or "")
    if lstat == "ready" and not lpol_ver:
        wwarn.append("long_dry_run_missing_policy_version")
    if lstat == "ready" and lpol_ver and "nyc_long_channel_source_policy" not in lpol_ver:
        wwarn.append("long_dry_run_unexpected_policy_version_string")
    sel_ori = str(long_dry.get("selected_orientation") or "").strip().lower()
    if sel_ori and sel_ori != "landscape":
        critical.append("long_dry_run_selected_non_landscape")
    long_vid = str(long_dry.get("next_video") or "")
    lv = long_vid.lower()
    if long_vid and any(x in lv for x in ("shorts_clips", "shorts_uploads", "youtube_shorts")):
        critical.append("long_queue_next_video_under_shorts_tree")
    st_src = str(long_dry.get("selected_source_type") or "").lower()
    if st_src == "walking":
        critical.append("long_dry_run_selected_walking_source_type")

    hist_roots_long = [
        xfer / "publish_pack" / "nyc_long_uploads",
        Path.home() / "StateVerge" / "data" / "long_runtime" / "long_uploads",
    ]
    uploaded_bn_union = _uploaded_basenames_from_ledger_file(long_ledger_p) | _uploaded_basenames_from_ledger_file(
        HOME_LONG_LEDGER
    )
    uploaded_bn_union |= _collect_uploaded_basenames_history(hist_roots_long)
    next_bn_gate = Path(long_vid).name.lower() if long_vid else ""
    if long_vid and next_bn_gate in LONG_DEDUPE_BASELINE_BAD_BASENAMES:
        critical.append("long_dry_run_next_is_uploaded_baseline_mux_20260510t131108z")
    if long_vid and next_bn_gate in uploaded_bn_union:
        critical.append("long_dry_run_next_basename_in_uploaded_ledger_or_history")

    raw_count = int(long_dry.get("candidate_count") or 0)
    eligible = int(long_dry.get("available_new_candidates") or 0)
    dup = int(long_dry.get("skipped_duplicate_count") or 0)
    if long_dry_parse_error and int(long_master_scan.get("qualified_count") or 0) > 0:
        eligible = max(eligible, int(long_master_scan.get("qualified_count") or 0))
        raw_count = max(raw_count, int(long_master_scan.get("scanned") or 0))
    basenames: list[str] = []
    qhs: list[str] = []
    ready = xfer / "ready_to_upload"
    if ready.is_dir():
        try:
            for p in ready.rglob("*.mp4"):
                if p.is_file() and p.stat().st_size >= 1024 * 1024:
                    basenames.append(p.name.lower())
        except OSError:
            pass
    basename_dupes = len(basenames) - len(set(basenames)) if basenames else 0
    qh_dupes = len(qhs) - len(set(qhs)) if qhs else 0

    def _latest_jobs(root: Path, fname: str, n: int = 3) -> list[Path]:
        if not root.is_dir():
            return []
        try:
            paths = sorted(root.rglob(fname), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []
        return paths[:n]

    long_roots = hist_roots_long
    latest_long = []
    for r in long_roots:
        latest_long.extend(_latest_jobs(r, "long_job_result.json", 3))
    latest_long = sorted(set(latest_long), key=lambda p: p.stat().st_mtime, reverse=True)[:3]

    shorts_roots = [xfer / "publish_pack" / "shorts_uploads", HOME_SHORTS_LEDGER.parent]
    latest_shorts = []
    for r in shorts_roots:
        latest_shorts.extend(_latest_jobs(r, "shorts_job_result.json", 3))
    latest_shorts = sorted(set(latest_shorts), key=lambda p: p.stat().st_mtime, reverse=True)[:3]

    long_pick_paths: list[str] = []
    latest_long_upload_bad_aspect_possible = False
    for p in latest_long:
        try:
            dj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            sor = str(dj.get("selected_orientation") or "").strip().lower()
            try:
                sar = float(dj.get("selected_aspect_ratio") or 0.0)
            except (TypeError, ValueError):
                sar = 0.0
            if sor == "portrait" or (sar > 0 and (sar < 1.55 or sar > 1.9)):
                latest_long_upload_bad_aspect_possible = True
            for it in dj.get("picked") or []:
                if not isinstance(it, dict):
                    continue
                if it.get("video"):
                    long_pick_paths.append(str(it["video"]))
                try:
                    iw = int(it.get("width") or 0)
                    ih = int(it.get("height") or 0)
                    iar = float(it.get("aspect_ratio") or 0.0)
                except (TypeError, ValueError):
                    continue
                if ih > iw or (iar > 0 and (iar < 1.55 or iar > 1.9)):
                    latest_long_upload_bad_aspect_possible = True
        except (OSError, json.JSONDecodeError):
            continue
    if latest_long_upload_bad_aspect_possible:
        wwarn.append("latest_long_upload_bad_aspect_possible=true")

    allowed_ids: list[str] = []
    if ALLOWED_TASKS.is_file():
        try:
            raw_a = json.loads(ALLOWED_TASKS.read_text(encoding="utf-8"))
            for t in raw_a.get("tasks") or []:
                if isinstance(t, dict) and t.get("id"):
                    allowed_ids.append(str(t["id"]))
        except (OSError, json.JSONDecodeError):
            wwarn.append("allowed_tasks_unreadable")

    status_code, status_err = _http_code("http://127.0.0.1:8765/api/mobile-command/status")
    root_code, _root_err = _http_code("http://127.0.0.1:8765/")

    auto_http = 0
    host_mode = ""
    try:
        req_a = urllib.request.Request("http://127.0.0.1:8765/api/automation/status", method="GET")
        with urllib.request.urlopen(req_a, timeout=4.0) as resp_a:  # noqa: S310
            auto_http = int(resp_a.status)
            raw_auto = resp_a.read().decode("utf-8", errors="replace")
        if auto_http == 200:
            try:
                body_auto = json.loads(raw_auto)
            except json.JSONDecodeError:
                wwarn.append("automation_status_non_json_body")
                body_auto = {}
            if isinstance(body_auto, dict):
                host_mode = str(body_auto.get("host_mode") or "").strip()
    except Exception as exc:  # noqa: BLE001
        auto_http = 0
        wwarn.append(f"automation_status_unreachable:{type(exc).__name__}")

    if auto_http == 200:
        if host_mode and host_mode != "PRO_ONLY":
            critical.append(f"host_mode_not_pro_only:{host_mode}")
        elif not host_mode:
            wwarn.append("host_mode_missing_in_automation_status")

    dup_risk_high = basename_dupes > 12 or qh_dupes > 6
    if dup_risk_high:
        critical.append("duplicate_risk_high")

    ledger_writable = False
    try:
        probe = long_ledger.parent / ".ledger_write_probe"
        long_ledger.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
        ledger_writable = True
    except OSError:
        try:
            probe2 = HOME_LONG_LEDGER.parent / ".ledger_write_probe"
            probe2.parent.mkdir(parents=True, exist_ok=True)
            probe2.write_text("1", encoding="utf-8")
            probe2.unlink(missing_ok=True)
            ledger_writable = True
        except OSError:
            critical.append("ledger_not_writable_primary_and_fallback")

    if not scripts_ok.get("channel_guard.py"):
        pass

    if eligible == 0 and launch.get("nyc_long", {}).get("loaded_guess") and tokens_ok["token.json"]:
        wwarn.append("no_new_long_assets_schedule_still_runs")

    bad_aspect_rejected_count = int(long_dry.get("rejected_bad_aspect_count") or 0)
    vertical_rejected_count = int(long_dry.get("vertical_rejected_count") or 0)
    next_ar = float(long_dry.get("selected_aspect_ratio") or 0.0)
    next_w = int(long_dry.get("selected_width") or 0)
    next_h = int(long_dry.get("selected_height") or 0)
    if next_ar <= 0 and next_w > 0 and next_h > 0:
        next_ar = next_w / float(next_h)
    next_ap = str(long_dry.get("selected_aspect_policy") or "")
    aspect_gate_next_ok = bool(
        long_vid
        and next_ar >= 1.55
        and next_ar <= 1.90
        and next_w > next_h
        and (next_ap in ("landscape_16_9_ok", "landscape_wide_warning") or next_ap == "")
    )

    nyc_argv = _plist_program_arguments(nyc_plist)
    sh_argv = _plist_program_arguments(sh_plist)
    long_launchagent_unlisted_only = _launchagent_unlisted_only(nyc_argv)
    shorts_launchagent_unlisted_only = _launchagent_unlisted_only(sh_argv)
    if nyc_plist.is_file() and not long_launchagent_unlisted_only:
        critical.append("long_launchagent_not_unlisted_only_or_public_leak")
    if sh_plist.is_file() and not shorts_launchagent_unlisted_only:
        critical.append("shorts_launchagent_not_unlisted_only_or_public_leak")

    bad_mobile_sub = (
        "upload_latest_long_public",
        "upload_latest_short_public",
        "allow_public",
        "allow-public",
        "public_upload",
    )
    mobile_public_buttons_removed = not any(
        any(b in str(tid).lower() for b in bad_mobile_sub) for tid in allowed_ids
    )
    if not mobile_public_buttons_removed:
        critical.append("mobile_allowed_tasks_public_leak")

    review_queue_writable = False
    try:
        from publish_review_queue import resolve_review_queue_dir

        rqroot = resolve_review_queue_dir(warnings=wwarn)
        probe_rq = rqroot / ".diagnose_review_queue_probe"
        probe_rq.write_text("1", encoding="utf-8")
        probe_rq.unlink(missing_ok=True)
        review_queue_writable = True
    except OSError:
        wwarn.append("review_queue_dir_not_writable_probe_failed")
    except Exception as exc:  # noqa: BLE001
        wwarn.append(f"review_queue_probe:{type(exc).__name__}")

    for _p in latest_long:
        try:
            _dj = json.loads(_p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(_dj, dict):
            continue
        if _dj.get("uploaded") is True or str(_dj.get("status") or "") == "uploaded":
            if _dj.get("review_queue_added") is False:
                critical.append("long_job_uploaded_without_review_queue_entry")

    overall = "ok"
    if critical:
        overall = "critical"
    elif wwarn or warnings or not shorts_plist_ok or status_code != 200:
        overall = "warning"

    long_status_out = str(long_dry.get("status") or "").strip()
    long_dry_run_status = str(long_dry.get("LONG_DRY_RUN_STATUS") or long_status_out or "").strip()
    br_long = str(long_dry.get("block_reason") or "").lower()
    blocked_long = long_dry_run_status.lower() == "blocked" or long_status_out.lower() == "blocked"
    emergency_long = bool(long_dry.get("LONG_AUTOPUBLISH_DISABLED")) or "policy_review_required" in br_long
    explicit_safe = long_dry.get("SAFE_TO_ENABLE_LONG_UPLOAD")
    next_is_clean = bool(long_dry.get("next_video_is_clean_real_sound"))
    nb = next_bn_gate
    is_raw_master = bool(nb) and nb.startswith("nyc_long_master_") and "clean_real_sound" not in nb
    long_noise_gate_ok = next_is_clean or not is_raw_master
    if explicit_safe is not None:
        safe_from_queue = bool(explicit_safe)
    else:
        safe_from_queue = bool(long_status_out == "ready" and eligible > 0 and bool(long_vid))
    safe_long = (
        overall != "critical"
        and safe_from_queue
        and not (blocked_long and emergency_long)
        and long_status_out != "needs_real_sound_cleanup"
        and long_noise_gate_ok
        and next_bn_gate not in LONG_DEDUPE_BASELINE_BAD_BASENAMES
        and next_bn_gate not in uploaded_bn_union
        and aspect_gate_next_ok
    )

    shorts_status_out = str(shorts_dry.get("status") or "").strip()
    shorts_block = str(shorts_dry.get("block_reason") or "").strip()
    safe_shorts = (
        overall != "critical"
        and not shorts_block
        and bool(shorts_status_out)
        and shorts_status_out not in ("upload_failed", "unknown")
    )

    next_step = (
        "fix_critical_or_launchagent_or_mobile_allowlist_then_rerun_diagnose"
        if critical
        else (
            "long_pool_dedupe_or_aspect_gate_see_LONG_DRY_RUN_STATUS"
            if not safe_long
            else "ok_monitor_scheduled_unlisted_uploads_and_review_queue"
        )
    )

    report: dict[str, Any] = {
        "status": overall,
        "generated_at": _utc(),
        "scripts": scripts_ok,
        "tokens_present": tokens_ok,
        "launchctl": launch,
        "schedules": {
            "nyc_long_plist": nyc_sched,
            "shorts_plist": sh_sched,
            "expected_long": [{"hour": 10, "minute": 0}, {"hour": 16, "minute": 0}],
            "expected_shorts": [
                {"hour": 9, "minute": 30},
                {"hour": 13, "minute": 30},
                {"hour": 17, "minute": 30},
                {"hour": 21, "minute": 30},
            ],
        },
        "plist_python": {"nyc_long": long_py, "shorts": shorts_py, "long_uses_venv_audio": "venv_audio" in long_py, "shorts_uses_venv_audio": "venv_audio" in shorts_py},
        "ledgers": {
            "long_primary": str(long_ledger_p),
            "long_effective": str(long_ledger),
            "long_entries": le_long,
            "long_entries_primary_file": le_long_primary,
            "long_entries_home_file": le_long_home,
            "long_uploaded_basename_union_count": len(uploaded_bn_union),
            "shorts_primary": str(shorts_ledger_p),
            "shorts_effective": str(shorts_ledger),
            "shorts_entries": le_shorts,
            "long_fallback_used": long_fb,
            "shorts_fallback_used": shorts_fb,
            "ledger_writable_probe": ledger_writable,
        },
        "candidates": {
            "long_total": raw_count,
            "long_duplicate_guess": dup,
            "long_available_new_guess": eligible,
            "long_next": long_dry.get("next_video") or "",
            "long_dry_run_status": long_dry.get("status"),
            "long_skipped_source_policy": int(long_dry.get("skipped_long_source_policy") or 0),
            "long_selected_orientation": long_dry.get("selected_orientation"),
            "long_selected_source_type": long_dry.get("selected_source_type"),
            "long_source_policy_version_dry": long_dry.get("long_source_policy_version"),
            "long_aspect_gate_enabled": True,
            "bad_aspect_rejected_count": bad_aspect_rejected_count,
            "vertical_rejected_count": vertical_rejected_count,
            "next_candidate_aspect_ratio": next_ar,
            "next_candidate_width": next_w,
            "next_candidate_height": next_h,
            "next_candidate_aspect_policy": next_ap,
            "latest_long_upload_bad_aspect_possible": latest_long_upload_bad_aspect_possible,
            "shorts_dry_run": {"status": shorts_dry.get("status"), "block_reason": shorts_dry.get("block_reason")},
            "basename_duplicates": basename_dupes,
            "quick_hash_duplicates": qh_dupes,
        },
        "nyc_long_source_policy_index": long_policy_index,
        "long_master_clips_scan": long_master_scan,
        "long_dry_run_parse_error": long_dry_parse_error,
        "long_dry_run_raw": long_dry,
        "shorts_dry_run_raw": shorts_dry,
        "recent_long_job_paths": [str(p) for p in latest_long],
        "recent_shorts_job_paths": [str(p) for p in latest_shorts],
        "long_last_picked_paths": long_pick_paths[:9],
        "mobile_command": {
            "status_http": status_code,
            "status_error": status_err,
            "root_http": root_code,
            "allowed_task_ids": allowed_ids,
            "has_diagnose_auto_publish_v2": "diagnose_auto_publish_v2" in allowed_ids,
        },
        "automation": {
            "status_http": auto_http,
            "host_mode": host_mode,
        },
        "stabilization": {
            "AUTO_UPLOAD_PRIVACY": "unlisted",
            "AUTO_PUBLIC_UPLOAD_DISABLED": True,
            "REVIEW_BEFORE_PUBLIC": True,
            "LONG_LAUNCHAGENT_UNLISTED_ONLY": long_launchagent_unlisted_only,
            "SHORTS_LAUNCHAGENT_UNLISTED_ONLY": shorts_launchagent_unlisted_only,
            "MOBILE_PUBLIC_BUTTONS_REMOVED": mobile_public_buttons_removed,
            "REVIEW_QUEUE_WRITABLE": review_queue_writable,
            "LONG_AVAILABLE_NEW_CANDIDATES": eligible,
            "LONG_NEXT_CANDIDATE": long_vid,
            "LONG_DRY_RUN_STATUS": long_status_out,
            "SAFE_TO_ENABLE_LONG_UPLOAD": safe_long,
            "SAFE_TO_ENABLE_SHORTS_UPLOAD": safe_shorts,
            "SHORTS_DRY_RUN_STATUS": shorts_status_out,
            "LONG_ASPECT_GATE_ENABLED": True,
            "BAD_ASPECT_REJECTED_COUNT": bad_aspect_rejected_count,
            "VERTICAL_REJECTED_COUNT": vertical_rejected_count,
            "NEXT_CANDIDATE_ASPECT_RATIO": next_ar,
            "NEXT_CANDIDATE_WIDTH": next_w,
            "NEXT_CANDIDATE_HEIGHT": next_h,
            "NEXT_CANDIDATE_ASPECT_POLICY": next_ap,
            "ASPECT_GATE_NEXT_OK": aspect_gate_next_ok,
            "LONG_NEXT_IS_CLEAN_REAL_SOUND": next_is_clean,
            "LONG_RAW_MASTER_CANDIDATE_NEEDS_CLEAN": is_raw_master and not next_is_clean,
            "LONG_REAL_SOUND_NOISE_GATE_OK": long_noise_gate_ok,
        },
        "warnings": warnings + wwarn,
        "critical": critical,
        "next_step": next_step,
    }

    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        print(f"WRITE_JSON_FAIL {OUT_JSON}: {exc}", file=sys.stderr)

    md_lines = [
        f"# Auto publish v2 diagnose ({report['generated_at']})",
        "",
        f"**Overall**: `{overall}`",
        "",
        "## Critical",
        "",
        "\n".join(f"- {c}" for c in critical) or "- (none)",
        "",
        "## Warnings",
        "",
        "\n".join(f"- {w}" for w in (warnings + wwarn)) or "- (none)",
        "",
        "## Long candidates",
        "",
        f"- total: {raw_count}",
        f"- duplicate-ish: {dup}",
        f"- new-ish: {eligible}",
        "",
        "## Stabilization (unlisted-only auto publish + gates)",
        "",
        f"- `AUTO_UPLOAD_PRIVACY`: unlisted",
        f"- `AUTO_PUBLIC_UPLOAD_DISABLED`: true",
        f"- `REVIEW_BEFORE_PUBLIC`: true",
        f"- `LONG_LAUNCHAGENT_UNLISTED_ONLY`: {long_launchagent_unlisted_only}",
        f"- `SHORTS_LAUNCHAGENT_UNLISTED_ONLY`: {shorts_launchagent_unlisted_only}",
        f"- `MOBILE_PUBLIC_BUTTONS_REMOVED`: {mobile_public_buttons_removed}",
        f"- `REVIEW_QUEUE_WRITABLE`: {review_queue_writable}",
        f"- `SAFE_TO_ENABLE_SHORTS_UPLOAD`: {safe_shorts}",
        f"- `SHORTS_DRY_RUN_STATUS`: `{shorts_status_out}`",
        f"- `LONG_AVAILABLE_NEW_CANDIDATES`: {eligible}",
        f"- `LONG_NEXT_CANDIDATE`: `{long_vid}`",
        f"- `LONG_DRY_RUN_STATUS`: `{long_status_out}`",
        f"- `SAFE_TO_ENABLE_LONG_UPLOAD`: `{safe_long}`",
        f"- `LONG_ASPECT_GATE_ENABLED`: true",
        f"- `BAD_ASPECT_REJECTED_COUNT`: {bad_aspect_rejected_count}",
        f"- `VERTICAL_REJECTED_COUNT`: {vertical_rejected_count}",
        f"- `NEXT_CANDIDATE_ASPECT_RATIO`: {next_ar}",
        f"- `NEXT_CANDIDATE_WIDTH` / `HEIGHT`: {next_w} x {next_h}",
        f"- `NEXT_CANDIDATE_ASPECT_POLICY`: `{next_ap}`",
        f"- `ASPECT_GATE_NEXT_OK`: {aspect_gate_next_ok}",
        f"- `LONG_NEXT_IS_CLEAN_REAL_SOUND`: {next_is_clean}",
        f"- `LONG_RAW_MASTER_CANDIDATE_NEEDS_CLEAN`: {is_raw_master and not next_is_clean}",
        f"- `LONG_REAL_SOUND_NOISE_GATE_OK`: {long_noise_gate_ok}",
        f"- host_mode (GET /api/automation/status): `{host_mode}` HTTP {auto_http}",
        "",
        "## NYC Long Source Policy v1",
        "",
        f"- index file: `{long_policy_index.get('path', '')}` exists={long_policy_index.get('file_exists')}",
        f"- index policy version: `{long_policy_index.get('long_source_policy_version', '')}`",
        f"- dry-run policy version: `{long_dry.get('long_source_policy_version', '')}`",
        f"- skipped by long source policy (dry): {int(long_dry.get('skipped_long_source_policy') or 0)}",
        f"- selected_orientation: `{long_dry.get('selected_orientation', '')}`",
        f"- selected_source_type: `{long_dry.get('selected_source_type', '')}`",
        "",
        "## Long master clips scan (ready_to_upload/nyc_long_clips)",
        "",
        f"- scanned: {int(long_master_scan.get('scanned') or 0)}",
        f"- qualified (>=3600s, ffprobe OK): {int(long_master_scan.get('qualified_count') or 0)}",
        f"- skipped_probe_failed: {int(long_master_scan.get('skipped_probe_failed') or 0)}",
        f"- skipped_too_short: {int(long_master_scan.get('skipped_too_short') or 0)}",
        f"- skipped_corrupt: {int(long_master_scan.get('skipped_corrupt') or 0)}",
        f"- candidate_scan_error: {int(long_master_scan.get('candidate_scan_error') or 0)}",
        f"- latest qualified: `{long_master_scan.get('latest_qualified_path', '')}`",
        "",
        "## Mobile command",
        "",
        f"- GET /api/mobile-command/status → HTTP {status_code}",
        f"- GET / → HTTP {root_code}",
        f"- diagnose_auto_publish_v2 allowed: {report['mobile_command']['has_diagnose_auto_publish_v2']}",
        "",
        "## Next step",
        "",
        f"- `{next_step}`",
        "",
    ]
    try:
        OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")
    except OSError as exc:
        print(f"WRITE_MD_FAIL {OUT_MD}: {exc}", file=sys.stderr)

    print(f"LONG_ASPECT_GATE_ENABLED=true")
    print(f"BAD_ASPECT_REJECTED_COUNT={bad_aspect_rejected_count}")
    print(f"VERTICAL_REJECTED_COUNT={vertical_rejected_count}")
    print(f"LONG_AVAILABLE_NEW_CANDIDATES={eligible}")
    print(f"LONG_NEXT_CANDIDATE={long_vid}")
    print(f"NEXT_CANDIDATE_WIDTH={next_w}")
    print(f"NEXT_CANDIDATE_HEIGHT={next_h}")
    print(f"NEXT_CANDIDATE_ASPECT_RATIO={next_ar}")
    print(f"NEXT_CANDIDATE_ASPECT_POLICY={next_ap}")
    print(f"LONG_DRY_RUN_STATUS={long_status_out}")
    print(f"SAFE_TO_ENABLE_LONG_UPLOAD={'true' if safe_long else 'false'}")
    print("AUTO_UPLOAD_PRIVACY=unlisted")
    print("AUTO_PUBLIC_UPLOAD_DISABLED=true")
    print("REVIEW_BEFORE_PUBLIC=true")
    print(f"LONG_LAUNCHAGENT_UNLISTED_ONLY={'true' if long_launchagent_unlisted_only else 'false'}")
    print(f"SHORTS_LAUNCHAGENT_UNLISTED_ONLY={'true' if shorts_launchagent_unlisted_only else 'false'}")
    print(f"MOBILE_PUBLIC_BUTTONS_REMOVED={'true' if mobile_public_buttons_removed else 'false'}")
    print(f"REVIEW_QUEUE_WRITABLE={'true' if review_queue_writable else 'false'}")
    print(f"SAFE_TO_ENABLE_SHORTS_UPLOAD={'true' if safe_shorts else 'false'}")
    print(f"SHORTS_DRY_RUN_STATUS={shorts_status_out}")
    lm_path = str(long_master_scan.get("latest_qualified_path") or "")
    lm_dur = float(long_master_scan.get("latest_qualified_duration_sec") or 0.0)
    lm_ready = bool(lm_path) and lm_dur >= 3600.0
    lm_block = ""
    if not lm_ready:
        if int(long_master_scan.get("skipped_corrupt") or 0) > 0:
            lm_block = "skipped_corrupt"
        elif int(long_master_scan.get("skipped_too_short") or 0) > 0:
            lm_block = "skipped_too_short"
        elif int(long_master_scan.get("skipped_probe_failed") or 0) > 0:
            lm_block = "skipped_probe_failed"
        elif long_dry_parse_error:
            lm_block = "long_dry_run_parse_error"
        elif not lm_path:
            lm_block = "no_qualified_long_master"
        else:
            lm_block = "long_master_not_ready"

    print(f"LONG_MASTER_READY={'true' if lm_ready else 'false'}")
    print(f"MASTER_PATH={lm_path}")
    print(f"DURATION_SEC={lm_dur:.3f}" if lm_dur else "DURATION_SEC=0")
    print(f"BLOCK_REASON={lm_block}")
    print(f"LONG_MASTER_SCAN_QUALIFIED={int(long_master_scan.get('qualified_count') or 0)}")
    print(f"LONG_MASTER_SCAN_SKIPPED_PROBE_FAILED={int(long_master_scan.get('skipped_probe_failed') or 0)}")
    print(f"LONG_MASTER_SCAN_SKIPPED_TOO_SHORT={int(long_master_scan.get('skipped_too_short') or 0)}")
    print(f"LONG_MASTER_SCAN_SKIPPED_CORRUPT={int(long_master_scan.get('skipped_corrupt') or 0)}")
    print(f"LONG_MASTER_SCAN_ERRORS={int(long_master_scan.get('candidate_scan_error') or 0)}")

    print(f"DIAGNOSE_JSON={OUT_JSON}")
    print(f"DIAGNOSE_MD={OUT_MD}")
    print(f"NEXT_STEP={next_step}")
    print(json.dumps({"ok": True, "status": overall, "wrote_json": str(OUT_JSON), "wrote_md": str(OUT_MD)}, indent=2))
    return 0 if overall != "critical" else 1


if __name__ == "__main__":
    raise SystemExit(main())
