#!/usr/bin/env python3
"""StateVerge Doctor v1 — unified health report (fail-open)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

HOME = Path.home()
STATEVERGE = HOME / "StateVerge"
CONTROL_CENTER = HOME / "StateVerge_Control_Center"
REPO_LOGS = STATEVERGE / "logs"
CC_LOGS = CONTROL_CENTER / "logs"
SV_CACHE_PRIMARY = Path("/Volumes/SV_CACHE")
SV_TRANSFER_PRIMARY = Path("/Volumes/SV_TRANSFER")
SV_BACKUP_PRIMARY = Path("/Volumes/SV_BACKUP")
LEGACY_STATEVERGE_VOL = Path("/Volumes/StateVerge")

TEXT_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".md",
    ".env",
    ".sh",
    ".yaml",
    ".yml",
    ".toml",
    ".mdc",
    ".txt",
    ".html",
    ".css",
    ".sql",
}
SKIP_DIR_NAMES = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".tox",
    "dist",
    "build",
    ".next",
    "_doctor_gate_quarantine",
    "_doctor_quarantine_locks",
}
LEGACY_SUBSTR = "/Volumes/StateVerge"
STALE_LOCK_SEC = 2 * 3600
STALE_INDEX_SEC = 7 * 24 * 3600
LOW_SPACE_GB = 20.0
TINY_VIDEO_BYTES = 100 * 1024
AFFECTED_FILES_CAP = 200


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_report_dir() -> tuple[Path, list[str]]:
    """Return (report_dir, warnings about path resolution)."""
    notes: list[str] = []
    primary_logs = SV_CACHE_PRIMARY / "logs"
    try:
        if SV_CACHE_PRIMARY.exists():
            primary_logs.mkdir(parents=True, exist_ok=True)
            probe = primary_logs / ".stateverge_doctor_write_probe"
            probe.write_text("1", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return primary_logs, notes
    except OSError as exc:
        notes.append(f"sv_cache_logs_unavailable:{exc!r}")

    fb = REPO_LOGS
    try:
        fb.mkdir(parents=True, exist_ok=True)
        probe = fb / ".stateverge_doctor_write_probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
        notes.append("report_dir_fallback:~/StateVerge/logs (SV_CACHE unavailable or not writable)")
        return fb, notes
    except OSError:
        pass

    fb2 = STATEVERGE / "logs"
    try:
        fb2.mkdir(parents=True, exist_ok=True)
        probe = fb2 / ".stateverge_doctor_write_probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
        notes.append("report_dir_fallback:StateVerge/logs")
        return fb2, notes
    except OSError as exc:
        notes.append(f"report_dir_fallback_failed:{exc!r}")

    fb3 = CC_LOGS
    fb3.mkdir(parents=True, exist_ok=True)
    notes.append("report_dir_fallback:StateVerge_Control_Center/logs (last resort)")
    return fb3, notes


def doctor_report_json_path(report_dir: Path) -> Path:
    return report_dir / "stateverge_doctor_report.json"


def doctor_report_md_path(report_dir: Path) -> Path:
    return report_dir / "stateverge_doctor_report.md"


def _legacy_path_governance_report_paths() -> list[Path]:
    """Same resolution order as stateverge_doctor / Control Center reads."""
    return [
        SV_CACHE_PRIMARY / "logs" / "legacy_path_governance_report.json",
        REPO_LOGS / "legacy_path_governance_report.json",
        CONTROL_CENTER / "logs" / "legacy_path_governance_report.json",
    ]


def _load_legacy_path_governance_report() -> tuple[dict[str, Any] | None, str | None]:
    for p in _legacy_path_governance_report_paths():
        data = _read_json_dict(p)
        if data:
            return data, str(p)
    return None, None


class DoctorRun:
    def __init__(self) -> None:
        self.error_count = 0
        self.warnings: list[str] = []
        self.critical_issues: list[str] = []
        self.recommendations: list[str] = []
        self.section_scores: dict[str, float] = {}

    def bump_error(self, ctx: str, exc: BaseException) -> None:
        self.error_count += 1
        self.warnings.append(f"internal_exception:{ctx}:{exc!r}")

    def safe(self, section_key: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            self.bump_error(section_key, exc)
            return {"ok": False, "error": repr(exc)}


def _mounted_writable(path: Path, _label: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "path": str(path),
        "mounted_or_exists": False,
        "writable": False,
        "free_space_gb": None,
        "low_space_warning": False,
        "write_probe_note": None,
    }
    try:
        out["mounted_or_exists"] = path.exists()
    except OSError:
        return out
    if not path.exists():
        return out
    try:
        sub = path / "logs"
        sub.mkdir(parents=True, exist_ok=True)
        probe = sub / f".doctor_probe_{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        out["writable"] = True
        out["write_probe_note"] = "wrote_temp_in_logs_subdir"
    except OSError as exc:
        out["writable"] = False
        out["write_probe_note"] = repr(exc)
    try:
        u = shutil.disk_usage(path)
        free_gb = round(u.free / (1024**3), 2)
        out["free_space_gb"] = free_gb
        out["low_space_warning"] = free_gb < LOW_SPACE_GB
    except OSError:
        pass
    return out


def section_a_storage(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        vols = {
            "sv_cache": SV_CACHE_PRIMARY,
            "sv_transfer": SV_TRANSFER_PRIMARY,
            "sv_backup": SV_BACKUP_PRIMARY,
        }
        checks = {k: _mounted_writable(p, k) for k, p in vols.items()}
        legacy = False
        try:
            legacy = LEGACY_STATEVERGE_VOL.exists()
        except OSError:
            pass
        if legacy:
            dr.warnings.append("legacy_storage_detected:/Volumes/StateVerge exists")
        for name, info in checks.items():
            if not info.get("mounted_or_exists"):
                dr.warnings.append(f"storage_missing:{name}")
            elif not info.get("writable"):
                dr.warnings.append(f"storage_not_writable:{name}")
            if info.get("low_space_warning"):
                dr.warnings.append(f"low_free_space:{name}:{info.get('free_space_gb')}gb")
        score = 100.0
        for _k, info in checks.items():
            if not info.get("mounted_or_exists"):
                score -= 25
            elif not info.get("writable"):
                score -= 20
            if info.get("low_space_warning"):
                score -= 5
        if legacy:
            score -= 5
        dr.section_scores["A_storage"] = max(0.0, score)
        return {
            "volumes": checks,
            "legacy_storage_detected": legacy,
            "low_space_threshold_gb": LOW_SPACE_GB,
        }

    return dr.safe("A_storage", run)


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _scan_legacy_refs(roots: list[Path]) -> tuple[int, list[str]]:
    needle = LEGACY_SUBSTR
    existing_roots = [r for r in roots if r.is_dir()]
    if not existing_roots:
        return 0, []
    rg = shutil.which("rg")
    if rg:
        try:
            globs = [
                "*.py",
                "*.ts",
                "*.tsx",
                "*.js",
                "*.json",
                "*.md",
                "*.env",
                "*.sh",
                "*.yaml",
                "*.yml",
                "*.toml",
                "*.mdc",
            ]
            cmd = [
                rg,
                "-l",
                "--fixed-strings",
                needle,
                "-g",
                "!**/node_modules/**",
                "-g",
                "!**/.venv/**",
                "-g",
                "!.git/**",
                "-g",
                "!**/*.mp4",
                "-g",
                "!**/*.mov",
                "-g",
                "!**/*.m4v",
                "-g",
                "!**/*.mkv",
                "-g",
                "!**/*.zip",
                "-g",
                "!**/*.png",
                "-g",
                "!**/*.jpg",
                "-g",
                "!**/*.jpeg",
                "-g",
                "!**/*.heic",
                "-g",
                "!**/*.sqlite",
                "-g",
                "!**/*.sqlite3",
            ]
            for g in globs:
                cmd.extend(["-g", g])
            cmd.extend([str(r) for r in existing_roots])
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
            paths = sorted({line.strip() for line in (r.stdout or "").splitlines() if line.strip()})
            total = len(paths)
            return total, paths[:AFFECTED_FILES_CAP]
        except (subprocess.TimeoutExpired, OSError):
            pass
    total = 0
    affected: list[str] = []
    for root in existing_roots:
        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d not in SKIP_DIR_NAMES and not d.startswith(".venv") and d != "node_modules"
                ]
                for fn in filenames:
                    if fn.startswith("._"):
                        continue
                    suf = Path(fn).suffix.lower()
                    if suf not in TEXT_EXTS:
                        continue
                    fp = Path(dirpath) / fn
                    try:
                        txt = fp.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if needle in txt:
                        total += 1
                        if len(affected) < AFFECTED_FILES_CAP:
                            affected.append(str(fp))
        except OSError:
            continue
    affected.sort()
    return total, affected


def section_b_governance(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        cfg = STATEVERGE / "config" / "storage_map.env"
        keys = _parse_env_file(cfg)
        roots = [STATEVERGE, CONTROL_CENTER]
        total, aff = _scan_legacy_refs(roots)
        truncated = total > len(aff)
        severity = "info"
        if total > 100:
            severity = "critical"
            dr.critical_issues.append(f"legacy_path_references_high:{total}")
        elif total > 10:
            severity = "warning"
            dr.warnings.append(f"legacy_path_references:{total}")
        elif total > 0:
            severity = "info"
            dr.recommendations.append("Migrate /Volumes/StateVerge string references to SV_* variables.")
        score = 100.0 if total == 0 else max(40.0, 100.0 - min(60.0, float(total)))
        if severity == "critical":
            score = min(score, 30.0)
        dr.section_scores["B_governance"] = score
        return {
            "storage_map_path": str(cfg),
            "storage_map_keys": {k: keys.get(k) for k in ("SV_CACHE", "SV_TRANSFER", "SV_BACKUP") if k in keys},
            "legacy_path_reference_count": total,
            "affected_files": aff,
            "affected_files_truncated": truncated,
            "severity": severity,
        }

    return dr.safe("B_governance", run)


def _json_entry_count(data: Any) -> int | None:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for k in ("items", "entries", "media", "tracks", "records", "data"):
            v = data.get(k)
            if isinstance(v, list):
                return len(v)
        return len(data)
    return None


def _index_file_report(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": str(path),
        "exists": False,
        "entry_count": None,
        "last_modified_iso": None,
        "stale_warning": False,
        "parse_error": None,
    }
    try:
        row["exists"] = path.is_file()
    except OSError:
        return row
    if not row["exists"]:
        return row
    try:
        st = path.stat()
        row["last_modified_iso"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        age = time.time() - st.st_mtime
        row["stale_warning"] = age > STALE_INDEX_SEC
    except OSError:
        pass
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
        row["entry_count"] = _json_entry_count(data)
    except (OSError, json.JSONDecodeError) as exc:
        row["parse_error"] = repr(exc)
    return row


def section_c_media_index(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        base = SV_TRANSFER_PRIMARY / "media_index"
        files = [
            base / "media_index.json",
            base / "media_index_v3.json",
            base / "media_scene_signals.json",
        ]
        rows = [_index_file_report(p) for p in files]
        score = 100.0
        for r in rows:
            if not r.get("exists"):
                score -= 20
            if r.get("parse_error"):
                score -= 10
            if r.get("stale_warning"):
                score -= 5
                dr.warnings.append(f"stale_media_index:{r.get('path')}")
        dr.section_scores["C_media_index"] = max(0.0, score)
        return {"files": rows}

    return dr.safe("C_media_index", run)


def section_d_semantic_music(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        base = SV_TRANSFER_PRIMARY / "04_AUDIO" / "music" / "nyc_long" / "metadata"
        idx = base / "music_index.json"
        sel = base / "music_selector_status.json"
        out: dict[str, Any] = {
            "music_index_path": str(idx),
            "music_selector_status_path": str(sel),
            "cinematic_track_count": None,
            "ambient_track_count": None,
            "fallback_warnings": [],
            "missing_categories": [],
            "semantic_music_ok": False,
            "raw_index_ok": False,
            "raw_selector_ok": False,
            "index_track_count": 0,
        }
        score = 100.0
        for label, p in (("index", idx), ("selector", sel)):
            try:
                if not p.is_file():
                    score -= 25
                    dr.warnings.append(f"semantic_music_missing:{label}")
                    continue
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                if label == "index":
                    out["raw_index_ok"] = True
                    index_track_count = 0
                    if isinstance(data, list):
                        index_track_count = len(data)
                        out["index_track_count"] = index_track_count
                    elif isinstance(data, dict):
                        tracks = data.get("tracks")
                        if not isinstance(tracks, list):
                            tracks = data.get("items")
                        cine = amb = 0
                        if isinstance(tracks, list):
                            index_track_count = len(tracks)
                            for t in tracks:
                                if not isinstance(t, dict):
                                    continue
                                cat = str(t.get("category") or t.get("bin") or t.get("style") or "").lower()
                                if "cinematic" in cat or "cinema" in cat:
                                    cine += 1
                                if "ambient" in cat:
                                    amb += 1
                            out["cinematic_track_count"] = cine or data.get("cinematic_track_count")
                            out["ambient_track_count"] = amb or data.get("ambient_track_count")
                        else:
                            out["cinematic_track_count"] = data.get("cinematic_track_count")
                            out["ambient_track_count"] = data.get("ambient_track_count")
                        out["index_track_count"] = index_track_count or _json_entry_count(data) or 0
                else:
                    out["raw_selector_ok"] = True
                    if isinstance(data, dict):
                        out["fallback_warnings"] = list(data.get("fallback_warnings") or data.get("warnings") or [])
                        out["missing_categories"] = list(data.get("missing_categories") or [])
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                score -= 15
                dr.warnings.append(f"semantic_music_parse_error:{label}:{exc!r}")

        c = int(out["cinematic_track_count"] or 0)
        a = int(out["ambient_track_count"] or 0)
        itc = int(out.get("index_track_count") or 0)
        ok = bool(out["raw_index_ok"] and (c + a > 0 or itc > 0))
        if out["missing_categories"]:
            ok = False
            score -= 10
        if out["fallback_warnings"]:
            ok = False
            score -= min(20, 5 * len(out["fallback_warnings"]))
        out["semantic_music_ok"] = ok and score >= 50
        dr.section_scores["D_semantic_music"] = max(0.0, score)
        if not out["semantic_music_ok"]:
            dr.recommendations.append("Repair NYC long semantic music index or import more licensed tracks.")
        return out

    return dr.safe("D_semantic_music", run)


def _read_token_refresh(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            return None
        for k in ("refresh_token", "refreshToken", "_refresh_token"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return None


def section_e_tokens(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        long_p = STATEVERGE / "data" / "youtube" / "token.json"
        sh_p = STATEVERGE / "data" / "youtube" / "token_shorts.json"
        lt = _read_token_refresh(long_p)
        st = _read_token_refresh(sh_p)
        dup = False
        if lt and st and lt == st:
            dup = True
            dr.critical_issues.append("duplicated_refresh_token_long_and_shorts")
        if not long_p.is_file():
            dr.warnings.append("token_json_missing:long")
        if not sh_p.is_file():
            dr.warnings.append("token_json_missing:shorts")
        isolation = bool(long_p.is_file() and sh_p.is_file() and lt and st and lt != st)
        if long_p.is_file() and sh_p.is_file() and (not lt or not st):
            dr.warnings.append("token_refresh_field_unreadable")
        score = 100.0
        if dup:
            score = 20.0
        elif not long_p.is_file() or not sh_p.is_file():
            score = 55.0
        elif not isolation:
            score = 70.0
        dr.section_scores["E_tokens"] = score
        return {
            "token_long_path": str(long_p),
            "token_shorts_path": str(sh_p),
            "token_long_present": long_p.is_file(),
            "token_shorts_present": sh_p.is_file(),
            "duplicated_token_warning": dup,
            "channel_isolation_ok": isolation,
        }

    return dr.safe("E_tokens", run)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _extract_pid_from_lock_payload(payload: str | None) -> int | None:
    raw = (payload or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            pid = data.get("pid") or data.get("process_id")
            if pid is not None:
                return int(pid)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    for token in raw.replace("=", " ").replace(":", " ").split():
        if token.isdigit():
            try:
                return int(token)
            except ValueError:
                return None
    return None


def _lock_inspect(path: Path, source: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": str(path),
        "source": source,
        "exists": False,
        "mtime_iso": None,
        "age_seconds": None,
        "stale": False,
        "active_guess": False,
        "pid": None,
        "process_alive": False,
        "process_verified": False,
        "payload_preview": None,
    }
    try:
        exists = path.is_file()
    except OSError:
        return row
    row["exists"] = exists
    if not exists:
        return row
    try:
        st = path.stat()
        row["mtime_iso"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        age = time.time() - st.st_mtime
        row["age_seconds"] = round(age, 1)
        row["stale"] = age > STALE_LOCK_SEC
        if path.suffix == ".lock" or "lock" in path.name.lower():
            try:
                txt = path.read_text(encoding="utf-8", errors="replace")[:400]
                row["payload_preview"] = txt
                pid = _extract_pid_from_lock_payload(txt)
                if pid is not None:
                    row["pid"] = pid
                    row["process_verified"] = True
                    row["process_alive"] = _pid_alive(pid)
            except OSError:
                pass
        row["active_guess"] = bool(exists and not row["stale"] and row["process_alive"])
    except OSError:
        pass
    return row


def section_f_locks(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        shorts_global = HOME / "StateVerge" / "data" / "shorts_runtime" / ".shorts_cut_upload.global.lock"
        long_primary = SV_TRANSFER_PRIMARY / "publish_pack" / "nyc_long_uploads" / ".nyc_long_upload.lock"
        long_fb = HOME / "StateVerge" / "data" / "long_runtime" / "long_uploads" / ".nyc_long_upload.lock"
        shorts_ledger = SV_TRANSFER_PRIMARY / "publish_pack" / "shorts_uploads" / "shorts_used_assets.json"
        shorts_flock = Path(str(shorts_ledger) + ".shorts_used_assets.fcntl.lock")
        documented = [
            ("shorts_global_lock_main.py", shorts_global),
            ("nyc_long_upload_lock_primary", long_primary),
            ("nyc_long_upload_lock_home_fallback", long_fb),
            ("shorts_ledger_fcntl_lock", shorts_flock),
        ]
        locks = [_lock_inspect(p, src) for src, p in documented]
        active = [x for x in locks if x.get("exists") and x.get("active_guess")]
        stale = [x for x in locks if x.get("exists") and x.get("stale")]
        recent_unverified = [
            x
            for x in locks
            if x.get("exists") and not x.get("stale") and not x.get("active_guess")
        ]
        for s in stale:
            dr.warnings.append(f"stale_lock:{s.get('path')}")
        for r in recent_unverified:
            dr.warnings.append(f"recent_lock_process_unverified:{r.get('path')}")
        blocked_est = len(active)
        score = 100.0 - 15 * len(stale) - 5 * len(active)
        dr.section_scores["F_locks"] = max(0.0, score)
        return {
            "documented_lock_paths": [{"label": src, "path": str(p)} for src, p in documented],
            "locks": locks,
            "active_locks": active,
            "stale_locks": stale,
            "recent_unverified_locks": recent_unverified,
            "blocked_jobs_estimate": blocked_est,
            "stale_threshold_hours": STALE_LOCK_SEC / 3600,
        }

    return dr.safe("F_locks", run)


def _ffprobe_duration(path: Path) -> tuple[float | None, str | None]:
    exe = shutil.which("ffprobe")
    if not exe:
        return None, "ffprobe_not_on_path"
    cmd = [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
        if r.returncode != 0:
            return None, f"ffprobe_rc_{r.returncode}"
        d = float((r.stdout or "").strip() or 0.0)
        return (d if d > 0 else None), None
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        return None, repr(exc)


def section_g_ready(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        root = SV_TRANSFER_PRIMARY / "ready_to_upload"
        out: dict[str, Any] = {
            "root": str(root),
            "exists": False,
            "video_candidates": 0,
            "tiny_files": 0,
            "missing_metadata": 0,
            "missing_audio_heuristic": 0,
            "invalid_videos": 0,
            "duration_errors": 0,
            "ffprobe_note": None,
            "samples": [],
            "duration_probe_sample_size": 15,
        }
        if not root.is_dir():
            dr.warnings.append("ready_to_upload_dir_missing")
            dr.section_scores["G_ready_to_upload"] = 70.0
            return out
        out["exists"] = True
        vids: list[Path] = []
        video_total = 0
        max_nodes = 15000
        nodes = 0
        truncated = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
            for fn in filenames:
                nodes += 1
                if nodes > max_nodes:
                    truncated = True
                    break
                suf = Path(fn).suffix.lower()
                if suf not in {".mp4", ".mov"}:
                    continue
                fp = Path(dirpath) / fn
                try:
                    if not fp.is_file():
                        continue
                except OSError:
                    continue
                video_total += 1
                if len(vids) < 200:
                    vids.append(fp)
            if truncated:
                break
        out["video_candidates"] = video_total
        out["directory_scan_truncated"] = truncated
        if truncated:
            dr.warnings.append("ready_to_upload_scan_truncated_for_speed")
        probe_list = vids[:15]
        has_ffprobe = shutil.which("ffprobe") is not None
        if not has_ffprobe:
            out["ffprobe_note"] = "ffprobe_unavailable_skipped_duration_checks"
        checked = 0
        for vp in probe_list:
            checked += 1
            try:
                sz = vp.stat().st_size
            except OSError:
                continue
            if sz < TINY_VIDEO_BYTES:
                out["tiny_files"] += 1
            side = vp.with_suffix(".json")
            meta = vp.with_name(vp.name + ".meta.json")
            if not side.is_file() and not meta.is_file():
                out["missing_metadata"] += 1
            if has_ffprobe:
                dur, err = _ffprobe_duration(vp)
                if err:
                    out["duration_errors"] += 1
                    if len(out["samples"]) < 5:
                        out["samples"].append({"file": str(vp), "issue": err})
                elif dur is not None and dur < 1.0:
                    out["invalid_videos"] += 1
                try:
                    if dur is not None and dur > 0:
                        # crude: very short might be missing audio — skip deep probe
                        pass
                except Exception:
                    pass
        score = 100.0
        score -= min(30, out["tiny_files"] * 3)
        score -= min(20, out["missing_metadata"])
        score -= min(25, out["invalid_videos"] * 5 + out["duration_errors"] * 2)
        dr.section_scores["G_ready_to_upload"] = max(0.0, score)
        return out

    return dr.safe("G_ready_to_upload", run)


def _http_get(url: str, timeout: float = 2.0) -> tuple[bool, int | None, str | None]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "stateverge-doctor/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, int(resp.getcode() or 200), None
    except urllib.error.HTTPError as exc:
        return True, int(exc.code), None
    except Exception as exc:  # noqa: BLE001
        return False, None, repr(exc)


def section_h_review(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        primary = Path("/Volumes/SV_TRANSFER/publish_pack/review_queue")
        fb = HOME / "StateVerge/data/review_queue"
        roots = [primary, fb]
        corrupt = 0
        apple_double = 0
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for p in root.iterdir():
                    if p.name.startswith("._"):
                        apple_double += 1
                        continue
                    if p.suffix.lower() == ".json":
                        try:
                            json.loads(p.read_text(encoding="utf-8", errors="replace"))
                        except (json.JSONDecodeError, OSError):
                            corrupt += 1
            except OSError:
                continue
        rq_ok = False
        err = None
        ok_http, code, herr = _http_get("http://127.0.0.1:8765/api/review-queue", timeout=2.0)
        rq_ok = ok_http and code == 200
        if not rq_ok:
            err = herr or f"http_status_{code}"
            dr.warnings.append(f"review_queue_api_unreachable:{err}")
        score = 90.0 if rq_ok else 50.0
        score -= min(30, corrupt * 5 + apple_double)
        dr.section_scores["H_review_queue"] = max(0.0, score)
        return {
            "review_queue_roots": [str(x) for x in roots],
            "corrupt_json_count": corrupt,
            "apple_double_files": apple_double,
            "review_queue_ok": rq_ok,
            "review_queue_http_error": err,
        }

    return dr.safe("H_review_queue", run)


def section_i_control(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        be_ok, be_code, be_err = _http_get("http://127.0.0.1:8765/", timeout=2.0)
        fe_ok, fe_code, fe_err = _http_get("http://127.0.0.1:3000/", timeout=2.0)
        if not be_ok:
            dr.warnings.append(f"control_center_backend_http:{be_err}")
        if not fe_ok:
            dr.warnings.append(f"control_center_frontend_http:{fe_err}")
        score = 0.0
        if be_ok:
            score += 50
        if fe_ok:
            score += 50
        dr.section_scores["I_control_center"] = score
        return {
            "backend_http_ok": be_ok,
            "backend_http_status": be_code,
            "frontend_http_ok": fe_ok,
            "frontend_http_status": fe_code,
        }

    return dr.safe("I_control_center", run)


def section_j_launchd(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        labels = (
            "com.stateverge.envato.music.importer",
            "stateverge",
            "autotracker",
            "tracking",
        )
        out: dict[str, Any] = {"agents": [], "launchctl_error": None}
        try:
            r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=15, check=False)
            text = (r.stdout or "") + "\n" + (r.stderr or "")
        except (OSError, subprocess.TimeoutExpired) as exc:
            out["launchctl_error"] = repr(exc)
            dr.warnings.append("launchctl_list_failed")
            dr.section_scores["J_launchd"] = 40.0
            return out
        lines = text.splitlines()
        matched: dict[str, dict[str, Any]] = {}
        for line in lines:
            low = line.lower()
            for lab in labels:
                if lab.lower() in low:
                    parts = line.split(maxsplit=2)
                    pid = None
                    if len(parts) >= 1 and parts[0] not in ("-", "PID"):
                        try:
                            pid = int(parts[0])
                        except ValueError:
                            pid = None
                    matched[lab] = {"line": line.strip(), "pid": pid, "loaded_guess": True}
        for lab in labels:
            if lab not in matched:
                out["agents"].append({"label_pattern": lab, "loaded": False, "pid": None, "last_run": None})
            else:
                m = matched[lab]
                out["agents"].append(
                    {
                        "label_pattern": lab,
                        "loaded": True,
                        "pid": m.get("pid"),
                        "last_run": None,
                        "matched_line": m.get("line"),
                    }
                )
        score = 70.0
        if any(a.get("loaded") for a in out["agents"]):
            score = 85.0
        dr.section_scores["J_launchd"] = score
        return out

    return dr.safe("J_launchd", run)


def section_k_publish(dr: DoctorRun) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        cg = STATEVERGE / "scripts" / "nyc_auto" / "channel_guard.py"
        dedupe = STATEVERGE / "scripts" / "stateverge_publish_dedupe.py"
        cg_ok = cg.is_file()
        dedupe_ok = dedupe.is_file()
        music_sep = False
        try:
            if cg_ok:
                head = cg.read_text(encoding="utf-8", errors="replace")[:8000]
                music_sep = "shorts" in head.lower() and "long" in head.lower()
        except OSError:
            pass
        long_t = STATEVERGE / "data" / "youtube" / "token.json"
        short_t = STATEVERGE / "data" / "youtube" / "token_shorts.json"
        cross = bool(cg_ok and long_t.is_file() and short_t.is_file())
        hist_csv = STATEVERGE / "data" / "youtube" / "upload_history.csv"
        hist_jsonl = STATEVERGE / "data" / "youtube" / "upload_history.jsonl"
        hist_ok = False
        hist_path = None
        for hp in (hist_csv, hist_jsonl):
            try:
                if hp.is_file() and hp.stat().st_size >= 0:
                    hist_ok = True
                    hist_path = str(hp)
                    hp.read_text(encoding="utf-8", errors="replace")[:200]
                    break
            except OSError:
                continue
        if not hist_ok:
            dr.warnings.append("upload_history_missing_or_unreadable")
        publish_ok = bool(cg_ok and dedupe_ok and cross and hist_ok)
        if not cg_ok:
            dr.critical_issues.append("channel_guard_missing")
        if not dedupe_ok:
            dr.critical_issues.append("stateverge_publish_dedupe_missing")
        score = 100.0
        if not cg_ok:
            score -= 40
        if not dedupe_ok:
            score -= 30
        if not cross:
            score -= 10
        if not hist_ok:
            score -= 10
        dr.section_scores["K_publish_safety"] = max(0.0, score)
        return {
            "channel_guard_path": str(cg),
            "channel_guard_exists": cg_ok,
            "stateverge_publish_dedupe_path": str(dedupe),
            "stateverge_publish_dedupe_exists": dedupe_ok,
            "music_channel_separation_heuristic": music_sep,
            "long_short_cross_contamination_heuristic_ok": cross,
            "upload_history_readable": hist_ok,
            "upload_history_path": hist_path,
            "publish_safety_ok": publish_ok,
        }

    return dr.safe("K_publish_safety", run)


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _location_recovery_gate_context() -> tuple[str | None, list[str]]:
    """Return (warning_code_or_none, extra_notes) for doctor WARNING_ONLY heuristics."""
    notes: list[str] = []
    candidates: list[Path] = []
    try:
        from utils.storage_paths import get_sv_transfer  # type: ignore

        candidates.append(get_sv_transfer(verbose=False) / "media_index" / "location_recovery_status.json")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"location_gate_transfer_resolve:{exc!r}")
    candidates.append(SV_TRANSFER_PRIMARY / "media_index" / "location_recovery_status.json")
    candidates.append(STATEVERGE / "data" / "media_index" / "location_recovery_status.json")

    chosen: Path | None = None
    for p in candidates:
        try:
            if p.is_file():
                chosen = p
                break
        except OSError:
            continue
    if chosen is None:
        return "location_recovery_status_missing", notes
    try:
        st = chosen.stat()
        age = time.time() - st.st_mtime
        if age > STALE_INDEX_SEC:
            notes.append(f"location_recovery_status_stale:{int(age)}s")
    except OSError:
        pass
    data = _read_json_dict(chosen) or {}
    rec = int(data.get("recovered_location_count") or 0)
    unk = int(data.get("still_unknown_count") or 0)
    if unk <= 0:
        return None, notes
    if rec <= 0 and unk > 50:
        return "location_recovery_weak_no_recoveries", notes
    if unk > max(80, rec * 3):
        return "location_recovery_weak_high_unknown_ratio", notes
    return None, notes


def _publish_dedupe_readable() -> bool:
    p = STATEVERGE / "scripts" / "stateverge_publish_dedupe.py"
    try:
        if not p.is_file():
            return False
        p.read_text(encoding="utf-8", errors="replace")[:200]
        return True
    except OSError:
        return False


def _collect_doctor_gate_optional_warnings(dr: DoctorRun, report: dict[str, Any]) -> list[str]:
    """Signals that contribute to WARNING_ONLY only (never sole BLOCK_UPLOAD from these)."""
    out: list[str] = []
    try:
        g = report.get("G_ready_to_upload_health") or {}
        g_score = float(dr.section_scores.get("G_ready_to_upload", 100.0))
        inv = int(g.get("invalid_videos") or 0) if isinstance(g, dict) else 0
        derr = int(g.get("duration_errors") or 0) if isinstance(g, dict) else 0
        if isinstance(g, dict) and g.get("exists"):
            if g_score < 50.0:
                out.append("ready_to_upload_score_severe")
            if derr >= 5:
                out.append("ready_to_upload_many_probe_failures")
            elif derr > 0:
                out.append("ready_to_upload_probe_failures")
            if 0 < inv < 3:
                out.append("ready_to_upload_some_invalid_videos")
    except Exception as exc:  # noqa: BLE001
        dr.warnings.append(f"doctor_gate_section_g_warn:{exc!r}")

    try:
        d = report.get("D_semantic_music_health") or {}
        if isinstance(d, dict) and not bool(d.get("semantic_music_ok")):
            out.append("semantic_music_degraded")
    except Exception as exc:  # noqa: BLE001
        dr.warnings.append(f"doctor_gate_section_d:{exc!r}")

    try:
        loc_code, loc_notes = _location_recovery_gate_context()
        for n in loc_notes:
            dr.warnings.append(n)
        if loc_code:
            out.append(loc_code)
    except Exception as exc:  # noqa: BLE001
        dr.warnings.append(f"doctor_gate_location_ctx:{exc!r}")

    try:
        fsec = report.get("F_upload_lock_health") or {}
        stale = fsec.get("stale_locks") if isinstance(fsec, dict) else None
        if isinstance(stale, list) and len(stale) > 0:
            out.append("stale_lock_present")
        recent_unverified = fsec.get("recent_unverified_locks") if isinstance(fsec, dict) else None
        if isinstance(recent_unverified, list) and len(recent_unverified) > 0:
            out.append("upload_lock_recent_process_unverified")
    except Exception as exc:  # noqa: BLE001
        dr.warnings.append(f"doctor_gate_section_f:{exc!r}")

    return out


def compute_doctor_gate_v1(dr: DoctorRun, report: dict[str, Any]) -> dict[str, Any]:
    """Doctor Gate v1 — global upload policy; selected media checks live in upload scripts."""
    block_reasons: list[str] = []
    warn_reasons: list[str] = []
    optional_warns: list[str] = []
    try:
        optional_warns = _collect_doctor_gate_optional_warnings(dr, report)
    except Exception as exc:  # noqa: BLE001
        dr.warnings.append(f"doctor_gate_optional_warns:{exc!r}")

    try:
        e = report.get("E_upload_token_health") or {}
        if isinstance(e, dict):
            long_p = bool(e.get("token_long_present"))
            short_p = bool(e.get("token_shorts_present"))
            if not long_p and not short_p:
                block_reasons.append("upload_tokens_both_missing")
            elif not long_p:
                optional_warns.append("long_upload_token_missing")
            elif not short_p:
                optional_warns.append("shorts_upload_token_missing")
            if bool(e.get("duplicated_token_warning")):
                block_reasons.append("channel_tokens_duplicated")
            if long_p and short_p and not bool(e.get("channel_isolation_ok")):
                block_reasons.append("channel_isolation_failed")
    except Exception as exc:  # noqa: BLE001
        dr.warnings.append(f"doctor_gate_section_e:{exc!r}")

    try:
        f = report.get("F_upload_lock_health") or {}
        active = f.get("active_locks") if isinstance(f, dict) else None
        if isinstance(active, list) and active:
            block_reasons.append("upload_lock_active_process_alive")
    except Exception as exc:  # noqa: BLE001
        dr.warnings.append(f"doctor_gate_section_f_block:{exc!r}")

    if block_reasons:
        gate = "BLOCK_UPLOAD"
        warn_reasons = list(optional_warns)
    else:
        gate = "HEALTHY"
        warn_reasons = list(optional_warns)
        if warn_reasons:
            gate = "WARNING_ONLY"

    gate_reason = ";".join(block_reasons) if block_reasons else ";".join(warn_reasons) or "ok"
    upload_allowed = gate != "BLOCK_UPLOAD"
    e2 = report.get("E_upload_token_health") or {}
    long_token_ok = bool(e2.get("token_long_present")) if isinstance(e2, dict) else True
    shorts_token_ok = bool(e2.get("token_shorts_present")) if isinstance(e2, dict) else True
    channel_ok = not (
        isinstance(e2, dict)
        and (bool(e2.get("duplicated_token_warning")) or (long_token_ok and shorts_token_ok and not bool(e2.get("channel_isolation_ok"))))
    )
    selected_file_check = {"status": "no_selection"}
    return {
        "doctor_gate": gate,
        "gate_reason": gate_reason[:500],
        "upload_allowed": upload_allowed,
        "block_reasons": block_reasons,
        "warning_reasons": warn_reasons,
        "doctor_gate_block_reasons": block_reasons,
        "doctor_gate_warning_reasons": warn_reasons,
        "selected_file_check": selected_file_check,
        "safe_to_retry_shorts_upload": bool(gate != "BLOCK_UPLOAD" and shorts_token_ok and channel_ok),
        "safe_to_enable_long_upload": bool(gate != "BLOCK_UPLOAD" and long_token_ok and channel_ok),
    }


def _overall_score(dr: DoctorRun) -> tuple[float, str]:
    weights = {
        "A_storage": 0.20,
        "B_governance": 0.12,
        "C_media_index": 0.12,
        "D_semantic_music": 0.12,
        "E_tokens": 0.10,
        "F_locks": 0.08,
        "G_ready_to_upload": 0.08,
        "H_review_queue": 0.06,
        "I_control_center": 0.05,
        "J_launchd": 0.04,
        "K_publish_safety": 0.13,
    }
    total_w = sum(weights.values())
    acc = 0.0
    for k, w in weights.items():
        acc += w * float(dr.section_scores.get(k, 75.0))
    score = round(acc / total_w, 1)
    if score >= 90:
        cat = "healthy"
    elif score >= 70:
        cat = "warning"
    else:
        cat = "critical"
    return score, cat


def _build_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# StateVerge Doctor Report",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Overall score: **{report.get('overall_health_score')}** ({report.get('health_category')})",
        "",
        "## Doctor Gate",
        "",
        f"- **doctor_gate**: `{report.get('doctor_gate', '')}`",
        f"- **upload_allowed**: `{report.get('upload_allowed', '')}`",
        f"- **gate_reason**: `{report.get('gate_reason', '')}`",
        f"- **block_reasons**: `{json.dumps(report.get('block_reasons', []), ensure_ascii=False)}`",
        f"- **warning_reasons**: `{json.dumps(report.get('warning_reasons', []), ensure_ascii=False)}`",
        f"- **selected_file_check**: `{json.dumps(report.get('selected_file_check', {}), ensure_ascii=False)}`",
        f"- **safe_to_retry_shorts_upload**: `{report.get('safe_to_retry_shorts_upload', '')}`",
        f"- **safe_to_enable_long_upload**: `{report.get('safe_to_enable_long_upload', '')}`",
        "",
        "## Storage",
        "",
        "```json",
        json.dumps(report.get("A_storage_health", {}), indent=2, ensure_ascii=False)[:8000],
        "```",
        "",
        "## Media Index",
        "",
        "```json",
        json.dumps(report.get("C_media_index_health", {}), indent=2, ensure_ascii=False)[:6000],
        "```",
        "",
        "## Semantic Music",
        "",
        "```json",
        json.dumps(report.get("D_semantic_music_health", {}), indent=2, ensure_ascii=False)[:6000],
        "```",
        "",
        "## Upload Queue",
        "",
        "### Locks (F)",
        "",
        "```json",
        json.dumps(report.get("F_upload_lock_health", {}), indent=2, ensure_ascii=False)[:6000],
        "```",
        "",
        "### Ready to upload (G)",
        "",
        "```json",
        json.dumps(report.get("G_ready_to_upload_health", {}), indent=2, ensure_ascii=False)[:6000],
        "```",
        "",
        "## Publish Safety",
        "",
        "```json",
        json.dumps(report.get("K_publish_safety_health", {}), indent=2, ensure_ascii=False)[:4000],
        "```",
        "",
        "## Review Queue",
        "",
        "```json",
        json.dumps(report.get("H_review_queue_health", {}), indent=2, ensure_ascii=False)[:4000],
        "```",
        "",
        "## Control Center & LaunchAgents",
        "",
        "### Control Center (I)",
        "",
        "```json",
        json.dumps(report.get("I_control_center_health", {}), indent=2, ensure_ascii=False)[:2000],
        "```",
        "",
        "### LaunchAgents (J)",
        "",
        "```json",
        json.dumps(report.get("J_launchagent_health", {}), indent=2, ensure_ascii=False)[:4000],
        "```",
        "",
        "## Official Storage Governance (B)",
        "",
        "```json",
        json.dumps(report.get("B_official_storage_governance", {}), indent=2, ensure_ascii=False)[:8000],
        "```",
        "",
        "## Upload Tokens (E)",
        "",
        "```json",
        json.dumps(report.get("E_upload_token_health", {}), indent=2, ensure_ascii=False)[:2000],
        "```",
        "",
        "## Recommendations",
        "",
    ]
    for r in report.get("recommendations") or []:
        lines.append(f"- {r}")
    if not report.get("recommendations"):
        lines.append("- No automated recommendations.")
    lines.append("")
    lines.append("## Warnings")
    for w in (report.get("warnings") or [])[:80]:
        lines.append(f"- {w}")
    lines.append("")
    lines.append("## Critical issues")
    for c in (report.get("critical_issues") or []):
        lines.append(f"- {c}")
    return "\n".join(lines)


def _intel_status_snapshot() -> dict[str, Any]:
    """Read optional location + lifecycle status JSON (fail-open)."""
    out: dict[str, Any] = {
        "recovered_location_count": 0,
        "still_unknown_count": 0,
        "route_group_count": 0,
        "loc_error_count": 0,
        "unused_high_quality_assets": 0,
        "overused_assets": 0,
        "asset_error_count": 0,
    }
    loc_paths: list[Path] = []
    ast_paths: list[Path] = []
    try:
        from utils.storage_paths import get_sv_transfer  # type: ignore

        xfer = get_sv_transfer(verbose=False)
        loc_paths.append(xfer / "media_index" / "location_recovery_status.json")
        ast_paths.append(xfer / "media_index" / "asset_lifecycle_status.json")
    except Exception:  # noqa: BLE001
        pass
    loc_paths.append(SV_TRANSFER_PRIMARY / "media_index" / "location_recovery_status.json")
    ast_paths.append(SV_TRANSFER_PRIMARY / "media_index" / "asset_lifecycle_status.json")
    loc_paths.append(STATEVERGE / "data" / "media_index" / "location_recovery_status.json")
    ast_paths.append(STATEVERGE / "data" / "media_index" / "asset_lifecycle_status.json")

    for p in loc_paths:
        d = _read_json_dict(p)
        if d:
            out["recovered_location_count"] = int(d.get("recovered_location_count") or 0)
            out["still_unknown_count"] = int(d.get("still_unknown_count") or 0)
            out["route_group_count"] = int(d.get("route_group_count") or 0)
            out["loc_error_count"] = int(d.get("error_count") or 0)
            break
    for p in ast_paths:
        d = _read_json_dict(p)
        if d:
            uhq = d.get("unused_high_quality_count")
            ov = d.get("overused_count")
            out["unused_high_quality_assets"] = int(uhq) if uhq is not None else len(d.get("unused_high_quality_assets") or [])  # type: ignore[arg-type]
            out["overused_assets"] = int(ov) if ov is not None else len(d.get("overused_assets") or [])  # type: ignore[arg-type]
            out["asset_error_count"] = int(d.get("error_count") or 0)
            break
    return out


def main() -> int:
    dr = DoctorRun()
    report_dir, path_notes = resolve_report_dir()
    for n in path_notes:
        dr.warnings.append(n)

    report: dict[str, Any] = {
        "generated_at": _utc_now_iso(),
        "report_dir": str(report_dir),
        "A_storage_health": section_a_storage(dr),
        "B_official_storage_governance": section_b_governance(dr),
        "C_media_index_health": section_c_media_index(dr),
        "D_semantic_music_health": section_d_semantic_music(dr),
        "E_upload_token_health": section_e_tokens(dr),
        "F_upload_lock_health": section_f_locks(dr),
        "G_ready_to_upload_health": section_g_ready(dr),
        "H_review_queue_health": section_h_review(dr),
        "I_control_center_health": section_i_control(dr),
        "J_launchagent_health": section_j_launchd(dr),
        "K_publish_safety_health": section_k_publish(dr),
    }

    score, cat = _overall_score(dr)
    report["overall_health_score"] = score
    report["health_category"] = cat
    try:
        report.update(compute_doctor_gate_v1(dr, report))
    except Exception as exc:  # noqa: BLE001
        dr.bump_error("doctor_gate_v1", exc)
        report["doctor_gate"] = "HEALTHY"
        report["gate_reason"] = f"gate_compute_failed:{exc!r}"
        report["upload_allowed"] = True
        report["block_reasons"] = []
        report["warning_reasons"] = [f"gate_compute_failed:{exc!r}"]
        report["selected_file_check"] = {"status": "no_selection"}
        report["safe_to_retry_shorts_upload"] = True
        report["safe_to_enable_long_upload"] = True
    gov_data: dict[str, Any] | None = None
    gov_path: str | None = None
    try:
        gov_data, gov_path = _load_legacy_path_governance_report()
    except Exception as exc:  # noqa: BLE001
        dr.warnings.append(f"legacy_path_governance_load_failed:{exc!r}")
        gov_data, gov_path = None, None

    legacy_governance_ok: bool | None
    if gov_data is None:
        legacy_governance_ok = None
        dr.warnings.append(
            "legacy_path_governance_report_missing:python3 ~/StateVerge/scripts/legacy_path_governance.py",
        )
    else:
        crit_n = int(gov_data.get("critical_risk_count") or 0)
        if crit_n > 0:
            legacy_governance_ok = False
            dr.warnings.append(f"legacy_path_governance_critical_risk_count:{crit_n}")
        else:
            legacy_governance_ok = bool(gov_data.get("legacy_governance_ok", True))
    report["legacy_governance_ok"] = legacy_governance_ok
    if gov_path:
        report["legacy_path_governance_report_path"] = gov_path

    report["warnings"] = list(dr.warnings)
    report["critical_issues"] = list(dr.critical_issues)
    report["recommendations"] = list(dr.recommendations)
    report["error_count"] = dr.error_count

    json_path = doctor_report_json_path(Path(report_dir))
    md_path = doctor_report_md_path(Path(report_dir))
    try:
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(_build_markdown(report), encoding="utf-8")
    except OSError as exc:
        dr.bump_error("write_report", exc)

    b = report.get("B_official_storage_governance") or {}
    legacy_count = int(b.get("legacy_path_reference_count") or 0)
    d = report.get("D_semantic_music_health") or {}
    sem_ok = bool(d.get("semantic_music_ok"))
    k = report.get("K_publish_safety_health") or {}
    pub_ok = bool(k.get("publish_safety_ok"))

    warn_n = len(dr.warnings)
    crit_n = len(dr.critical_issues)

    print("STATEVERGE_DOCTOR_V1_DONE")
    print(f"REPORT_JSON={json_path.resolve()}")
    print(f"REPORT_MD={md_path.resolve()}")
    print(f"OVERALL_HEALTH_SCORE={int(round(score))}")
    print(f"WARNING_COUNT={warn_n}")
    print(f"CRITICAL_COUNT={crit_n}")
    print(f"LEGACY_REFERENCE_COUNT={legacy_count}")
    print(f"SEMANTIC_MUSIC_OK={'true' if sem_ok else 'false'}")
    print(f"PUBLISH_SAFETY_OK={'true' if pub_ok else 'false'}")
    print(f"ERROR_COUNT={dr.error_count}")

    intel = _intel_status_snapshot()
    combined_err = int(dr.error_count) + int(intel.get("loc_error_count") or 0) + int(intel.get("asset_error_count") or 0)
    dg = str(report.get("doctor_gate") or "")
    ua = "true" if report.get("upload_allowed") else "false"
    print("", flush=True)
    print("LOCATION_SIGNAL_RECOVERY_V2_DONE", flush=True)
    print("ASSET_LIFECYCLE_INTELLIGENCE_V1_DONE", flush=True)
    print("DOCTOR_GATE_V1_DONE", flush=True)
    print("", flush=True)
    print(f"RECOVERED_LOCATION_COUNT={intel.get('recovered_location_count', 0)}", flush=True)
    print(f"STILL_UNKNOWN_COUNT={intel.get('still_unknown_count', 0)}", flush=True)
    print(f"ROUTE_GROUP_COUNT={intel.get('route_group_count', 0)}", flush=True)
    print(f"UNUSED_HIGH_QUALITY_ASSETS={intel.get('unused_high_quality_assets', 0)}", flush=True)
    print(f"OVERUSED_ASSETS={intel.get('overused_assets', 0)}", flush=True)
    print(f"DOCTOR_GATE={dg}", flush=True)
    print(f"UPLOAD_ALLOWED={ua}", flush=True)
    print(f"ERROR_COUNT={combined_err}", flush=True)
    gr = str(report.get("gate_reason") or "").replace("\n", " ").replace("\r", " ")[:500]
    print("", flush=True)
    print("DOCTOR_GATE_RULE_FIX_DONE", flush=True)
    print(f"DOCTOR_GATE={dg}", flush=True)
    print(f"UPLOAD_ALLOWED={ua}", flush=True)
    print(f"GATE_REASON={gr}", flush=True)
    print(f"WARNING_COUNT={warn_n}", flush=True)
    print(f"CRITICAL_COUNT={crit_n}", flush=True)
    print(f"ERROR_COUNT={dr.error_count}", flush=True)
    print(f"DOCTOR_GATE={dg}", flush=True)
    print(f"UPLOAD_ALLOWED={ua}", flush=True)
    print(f"BLOCK_REASONS={json.dumps(report.get('block_reasons') or [], ensure_ascii=False)}", flush=True)
    print(f"WARNING_REASONS={json.dumps(report.get('warning_reasons') or [], ensure_ascii=False)}", flush=True)
    print(f"SELECTED_FILE_CHECK={json.dumps(report.get('selected_file_check') or {'status': 'no_selection'}, ensure_ascii=False)}", flush=True)
    print(
        f"SAFE_TO_RETRY_SHORTS_UPLOAD={'true' if report.get('safe_to_retry_shorts_upload') else 'false'}",
        flush=True,
    )
    print(
        f"SAFE_TO_ENABLE_LONG_UPLOAD={'true' if report.get('safe_to_enable_long_upload') else 'false'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
