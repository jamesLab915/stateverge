#!/usr/bin/env python3
"""StateVerge DaVinci API Bridge v1 — verify only that DaVinci Resolve Python API works.

No audio fix, timeline creation, upload, or pipeline changes. Stdlib only; exits 0
always (fail-open). Writes JSON probe; stdout ends with three RESOLVE_* lines.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- sys.path: Blackmagic Resolve scripting modules (system first, then optional user) ---
# Prepend order (first in sys.path wins): (1) system DaVinci Resolve modules,
# (2) system Blackmagic modules, (3–4) same two under ~/Library if present.
_RESOLVE_MODULES_SYSTEM = Path(
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
)
_BMD_MODULES_SYSTEM = Path(
    "/Library/Application Support/Blackmagic Design/Developer/Scripting/Modules"
)
_RESOLVE_MODULES_USER = Path.home() / (
    "Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
)
_BMD_MODULES_USER = Path.home() / (
    "Library/Application Support/Blackmagic Design/Developer/Scripting/Modules"
)

for _p in (
    _BMD_MODULES_USER,
    _RESOLVE_MODULES_USER,
    _BMD_MODULES_SYSTEM,
    _RESOLVE_MODULES_SYSTEM,
):
    if _p.is_dir():
        _s = str(_p)
        if _s not in sys.path:
            sys.path.insert(0, _s)

PRIMARY_JSON = Path("/Volumes/SV_CACHE/davinci/logs/resolve_api_check.json")
FALLBACK_JSON = Path(
    "/Users/ziweizhang/StateVerge/_storage_fallback/sv_cache/davinci/logs/"
    "resolve_api_check.json"
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dir_writable(dir_path: Path) -> bool:
    """Return True if dir_path exists or can be created and a temp file can be written."""
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    try:
        with tempfile.NamedTemporaryFile(
            dir=dir_path, prefix=".resolve_api_probe_", delete=True
        ) as fh:
            fh.write(b"ok")
            fh.flush()
    except OSError:
        return False
    return True


def _choose_json_path(warnings: list[str]) -> Path:
    parent = PRIMARY_JSON.parent
    if _dir_writable(parent):
        return PRIMARY_JSON
    warnings.append(
        f"Primary log dir not writable, using fallback: {PRIMARY_JSON.parent}"
    )
    return FALLBACK_JSON


def _safe_str(val: Any) -> str | None:
    if val is None:
        return None
    try:
        s = str(val)
    except Exception:
        return None
    return s if s else None


def main() -> None:
    warnings: list[str] = []
    payload: dict[str, Any] = {
        "api_available": False,
        "resolve_connected": False,
        "product_name": None,
        "version": None,
        "current_project": None,
        "project_manager_available": False,
        "timestamp": _utc_iso(),
        "warnings": warnings,
    }

    dvr_script = None
    # Documented import first; importlib fallback if normal import fails (path timing, etc.).
    try:
        import DaVinciResolveScript as dvr_script  # type: ignore[import-not-found]
    except ImportError as exc:
        warnings.append(f"import DaVinciResolveScript failed: {exc}")
        try:
            spec = importlib.util.find_spec("DaVinciResolveScript")
            if spec is not None and spec.loader is not None:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                dvr_script = mod
        except Exception as exc2:  # noqa: BLE001 — probe only
            warnings.append(f"importlib load DaVinciResolveScript failed: {exc2}")

    if dvr_script is not None and hasattr(dvr_script, "scriptapp"):
        payload["api_available"] = True
        resolve = None
        try:
            resolve = dvr_script.scriptapp("Resolve")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"scriptapp('Resolve') failed: {exc}")

        # Truthy Resolve app handle; GetProductName used below when possible.
        payload["resolve_connected"] = bool(resolve)

        if resolve:
            try:
                name = resolve.GetProductName()
                payload["product_name"] = _safe_str(name)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"GetProductName failed: {exc}")

            for meth in ("GetVersionString", "GetVersion"):
                if hasattr(resolve, meth):
                    try:
                        fn = getattr(resolve, meth)
                        ver = fn() if callable(fn) else fn
                        payload["version"] = _safe_str(ver)
                        if payload["version"]:
                            break
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"{meth} failed: {exc}")

            pm = None
            try:
                pm = resolve.GetProjectManager()
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"GetProjectManager failed: {exc}")

            if pm:
                payload["project_manager_available"] = True
                try:
                    proj = pm.GetCurrentProject()
                    if proj is not None:
                        try:
                            payload["current_project"] = _safe_str(proj.GetName())
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(f"GetCurrentProject().GetName failed: {exc}")
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"GetCurrentProject failed: {exc}")
    else:
        if dvr_script is None:
            warnings.append("DaVinciResolveScript module not available after import attempts.")
        else:
            warnings.append(
                "DaVinciResolveScript loaded but scriptapp is missing (unexpected API surface)."
            )

    out_path = _choose_json_path(warnings)
    payload["timestamp"] = _utc_iso()
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        warnings.append(f"Failed to write JSON to {out_path}: {exc}")
        payload["warnings"] = warnings

    # Stdout: exactly these last three lines (no prior stdout).
    api_flag = "true" if payload["api_available"] else "false"
    conn_flag = "true" if payload["resolve_connected"] else "false"
    ver_out = payload["version"] if payload["version"] else "unknown"
    sys.stdout.write(
        f"RESOLVE_API_AVAILABLE={api_flag}\n"
        f"RESOLVE_CONNECTED={conn_flag}\n"
        f"RESOLVE_VERSION={ver_out}\n"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
