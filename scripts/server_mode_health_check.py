#!/usr/bin/env python3
"""Server Mode health check: three-disk mounts, RW probes, key dirs, Control Center ports.

Writes JSON to SV_CACHE/logs and Markdown to StateVerge_Control_Center/logs.
Fail-open: never raises; errors go to warnings/errors in the report.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from utils.storage_paths import get_sv_backup, get_sv_cache, get_sv_transfer  # noqa: E402
except Exception:  # noqa: BLE001
    get_sv_cache = get_sv_transfer = get_sv_backup = None  # type: ignore


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vol_entry(path: Path) -> dict[str, Any]:
    p = str(path)
    out: dict[str, Any] = {
        "path": p,
        "mounted": False,
        "readable": False,
        "writable": False,
        "free_bytes": 0,
        "total_bytes": 0,
    }
    try:
        exists = path.is_dir()
    except OSError:
        exists = False
    out["mounted"] = bool(exists)
    if not exists:
        return out
    out["readable"] = True
    try:
        probe = path / ".stateverge_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        out["writable"] = True
    except OSError:
        out["writable"] = False
    try:
        u = shutil.disk_usage(path)
        out["free_bytes"] = int(u.free)
        out["total_bytes"] = int(u.total)
    except OSError:
        pass
    return out


def _ensure_dir(p: Path, report: dict[str, Any], *, bucket: str) -> None:
    key = f"{bucket}:{p}"
    try:
        p.mkdir(parents=True, exist_ok=True)
        report["directories"][key] = "ok"
    except OSError as exc:
        report["directories"][key] = f"error:{exc!r}"
        report["warnings"].append(f"mkdir_failed:{key}")


def _http_code(url: str, *, timeout: float = 3.0) -> tuple[bool, int | None]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return True, int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        return True, int(exc.code)
    except Exception:
        return False, None


def _code_roots() -> dict[str, Any]:
    home = Path.home()
    roots = {
        "stateverge": home / "StateVerge",
        "control_center": home / "StateVerge_Control_Center",
        "frontend": home / "StateVerge_Control_Center" / "frontend",
        "backend": home / "StateVerge_Control_Center" / "backend",
    }
    out: dict[str, Any] = {}
    for k, p in roots.items():
        try:
            out[k] = {"path": str(p), "exists": p.is_dir()}
        except OSError:
            out[k] = {"path": str(p), "exists": False}
    return out


def run_check() -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []

    cache_p = transfer_p = backup_p = Path("/nonexistent")
    try:
        if get_sv_cache:
            cache_p = get_sv_cache(verbose=False)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"sv_cache_resolve:{exc!r}")
        cache_p = Path("/Volumes/SV_CACHE")
    try:
        if get_sv_transfer:
            transfer_p = get_sv_transfer(verbose=False)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"sv_transfer_resolve:{exc!r}")
        transfer_p = Path("/Volumes/SV_TRANSFER")
    try:
        if get_sv_backup:
            backup_p = get_sv_backup(verbose=False)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"sv_backup_resolve:{exc!r}")
        backup_p = Path("/Volumes/SV_BACKUP")

    report: dict[str, Any] = {
        "created_at": _utc(),
        "host": socket.gethostname(),
        "mode": "server_mode",
        "volumes": {
            "SV_CACHE": _vol_entry(cache_p),
            "SV_TRANSFER": _vol_entry(transfer_p),
            "SV_BACKUP": _vol_entry(backup_p),
        },
        "directories": {},
        "code_roots": _code_roots(),
        "control_center": {
            "backend_8765": {"running": False, "http_code": None},
            "frontend_3000": {"running": False, "http_code": None},
        },
        "warnings": warnings,
        "errors": errors,
    }

    for p, bucket, rels in (
        (
            cache_p,
            "SV_CACHE",
            [
                "inbox",
                "renders",
                "normalized",
                "audio_clean",
                "review_reports",
                "jobs",
                "logs",
            ],
        ),
        (
            transfer_p,
            "SV_TRANSFER",
            [
                "00_INBOX/iphone",
                "media_index",
                "ready_to_upload",
                "publish_pack",
                "premium_footage",
                "logs",
            ],
        ),
        (
            backup_p,
            "SV_BACKUP",
            [
                "project_backups",
                "exports_backup",
                "tracking",
                "config_backups",
                "logs",
            ],
        ),
    ):
        for rel in rels:
            _ensure_dir(p / rel, report, bucket=bucket)

    ok_b, code_b = _http_code("http://127.0.0.1:8765/api/dashboard")
    report["control_center"]["backend_8765"] = {
        "running": ok_b and code_b is not None and int(code_b) < 500,
        "http_code": code_b,
    }
    ok_f, code_f = _http_code("http://127.0.0.1:3000/")
    report["control_center"]["frontend_3000"] = {
        "running": ok_f and code_f is not None and int(code_f) < 500,
        "http_code": code_f,
    }

    for role, key in (("SV_CACHE", "SV_CACHE"), ("SV_TRANSFER", "SV_TRANSFER"), ("SV_BACKUP", "SV_BACKUP")):
        v = report["volumes"].get(key) or {}
        if not v.get("mounted"):
            warnings.append(f"volume_offline:{key}")
        elif not v.get("writable"):
            warnings.append(f"volume_not_writable:{key}")

    cr = report.get("code_roots") or {}
    for name in ("stateverge", "control_center", "frontend", "backend"):
        if not (cr.get(name) or {}).get("exists"):
            warnings.append(f"code_root_missing:{name}")

    return report


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        payload.setdefault("write_errors", []).append(f"{path}:{exc!r}")


def _write_md(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        f"# Server Mode Health Check",
        "",
        f"- **created_at**: {payload.get('created_at')}",
        f"- **host**: {payload.get('host')}",
        "",
        "## Volumes",
        "",
    ]
    for k, v in (payload.get("volumes") or {}).items():
        lines.append(f"### {k}")
        lines.append(f"- mounted: {v.get('mounted')}")
        lines.append(f"- readable: {v.get('readable')}")
        lines.append(f"- writable: {v.get('writable')}")
        lines.append(f"- free_bytes: {v.get('free_bytes')}")
        lines.append("")
    lines.append("## Control Center")
    lines.append(json.dumps(payload.get("control_center") or {}, indent=2))
    lines.append("")
    lines.append("## Warnings")
    for w in payload.get("warnings") or []:
        lines.append(f"- {w}")
    lines.append("")
    lines.append("## Errors")
    for e in payload.get("errors") or []:
        lines.append(f"- {e}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    payload = run_check()
    try:
        if Path("/Volumes/SV_CACHE").is_dir():
            json_path = Path("/Volumes/SV_CACHE/logs/server_mode_health_check.json")
        else:
            cache_logs = Path("/Volumes/SV_CACHE/logs")
            try:
                if get_sv_cache:
                    cache_logs = get_sv_cache(verbose=False) / "logs"
            except Exception:
                pass
            json_path = cache_logs / "server_mode_health_check.json"
    except OSError:
        json_path = Path.home() / "StateVerge" / "logs" / "server_mode_health_check.json"
    _write_json(json_path, payload)
    md_path = Path.home() / "StateVerge_Control_Center" / "logs" / "server_mode_health_check.md"
    _write_md(md_path, payload)
    print(json.dumps({"ok": True, "json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
