#!/usr/bin/env python3
"""Resolve scripting path detection (env-first, then Blackmagic install candidates)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# fusionscript.so — Studio before free Resolve; additional apps discovered under /Applications
_FUSIONSCRIPT_CANDIDATES: tuple[str, ...] = (
    "/Applications/DaVinci Resolve Studio.app/Contents/Libraries/Fusion/fusionscript.so",
    "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
)

# RESOLVE_SCRIPT_API root (Modules/ is appended to sys.path)
_SCRIPT_API_CANDIDATES: tuple[str, ...] = (
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
    str(
        Path.home()
        / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    ),
    "/Library/Application Support/Blackmagic Design/Developer/Scripting",
    str(Path.home() / "Library/Application Support/Blackmagic Design/Developer/Scripting"),
)

_MODULES_FALLBACK_DIRS: tuple[str, ...] = (
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
    str(
        Path.home()
        / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
    ),
    "/Library/Application Support/Blackmagic Design/Developer/Scripting/Modules",
    str(Path.home() / "Library/Application Support/Blackmagic Design/Developer/Scripting/Modules"),
)

_CONFIGURED = False


@dataclass(frozen=True)
class ResolvedResolvePaths:
    script_lib: str | None
    script_lib_source: str
    script_api: str | None
    script_api_source: str
    modules_dir: str | None
    modules_source: str
    detection_order_lib: tuple[str, ...]
    detection_order_api: tuple[str, ...]


def _exists_file(path: str | Path) -> bool:
    try:
        return Path(path).is_file()
    except OSError:
        return False


def _exists_dir(path: str | Path) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def _glob_fusionscript_candidates() -> list[str]:
    """Discover Resolve*.app installs under /Applications."""
    found: list[str] = []
    apps = Path("/Applications")
    if not apps.is_dir():
        return found
    for app in sorted(apps.glob("DaVinci Resolve*.app")):
        candidate = app / "Contents/Libraries/Fusion/fusionscript.so"
        s = str(candidate)
        if _exists_file(candidate) and s not in found:
            found.append(s)
    return found


def fusionscript_detection_order() -> tuple[str, ...]:
    """Ordered candidate paths for fusionscript.so (env checked separately)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for p in _FUSIONSCRIPT_CANDIDATES:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    for p in _glob_fusionscript_candidates():
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return tuple(ordered)


def script_api_detection_order() -> tuple[str, ...]:
    return _SCRIPT_API_CANDIDATES


def resolve_script_lib() -> tuple[str | None, str]:
    """Return (path, source). Source: env | candidate:<path> | none."""
    env = (os.environ.get("RESOLVE_SCRIPT_LIB") or "").strip()
    if env and _exists_file(env):
        return env, "env"
    if env:
        # Env set but missing — fall through to auto-detect
        pass
    for candidate in fusionscript_detection_order():
        if _exists_file(candidate):
            return candidate, f"candidate:{candidate}"
    return None, "none"


def resolve_script_api_root() -> tuple[str | None, str]:
    """Return (scripting API root dir, source)."""
    env = (os.environ.get("RESOLVE_SCRIPT_API") or "").strip()
    if env and _exists_dir(env):
        return env.rstrip("/"), "env"
    for candidate in script_api_detection_order():
        if _exists_dir(candidate):
            return candidate, f"candidate:{candidate}"
    return None, "none"


def resolve_modules_dir(script_api: str | None) -> tuple[str | None, str]:
    if script_api:
        modules = Path(script_api) / "Modules"
        if modules.is_dir():
            return str(modules), f"{script_api}/Modules"
    for candidate in _MODULES_FALLBACK_DIRS:
        if _exists_dir(candidate):
            return candidate, f"candidate:{candidate}"
    return None, "none"


def get_resolved_paths() -> ResolvedResolvePaths:
    lib, lib_src = resolve_script_lib()
    api, api_src = resolve_script_api_root()
    modules, mod_src = resolve_modules_dir(api)
    return ResolvedResolvePaths(
        script_lib=lib,
        script_lib_source=lib_src,
        script_api=api,
        script_api_source=api_src,
        modules_dir=modules,
        modules_source=mod_src,
        detection_order_lib=fusionscript_detection_order(),
        detection_order_api=script_api_detection_order(),
    )


def _prepend_sys_path(path: str) -> None:
    if path and path not in sys.path:
        sys.path.insert(0, path)


def configure_resolve_paths(*, force: bool = False) -> ResolvedResolvePaths:
    """Set RESOLVE_SCRIPT_* env vars and prepend Modules to sys.path (idempotent)."""
    global _CONFIGURED
    resolved = get_resolved_paths()

    if resolved.script_lib and (force or not os.environ.get("RESOLVE_SCRIPT_LIB")):
        os.environ["RESOLVE_SCRIPT_LIB"] = resolved.script_lib
    elif resolved.script_lib and not _exists_file(os.environ.get("RESOLVE_SCRIPT_LIB", "")):
        os.environ["RESOLVE_SCRIPT_LIB"] = resolved.script_lib

    if resolved.script_api and (force or not os.environ.get("RESOLVE_SCRIPT_API")):
        os.environ["RESOLVE_SCRIPT_API"] = resolved.script_api
    elif resolved.script_api and not _exists_dir(os.environ.get("RESOLVE_SCRIPT_API", "")):
        os.environ["RESOLVE_SCRIPT_API"] = resolved.script_api

    if resolved.modules_dir:
        _prepend_sys_path(resolved.modules_dir)

    _CONFIGURED = True
    return resolved


def resolved_paths_dict() -> dict[str, object]:
    """JSON-serializable summary for probes and CLI."""
    r = get_resolved_paths()
    return {
        "resolve_script_lib": r.script_lib,
        "resolve_script_lib_source": r.script_lib_source,
        "resolve_script_api": r.script_api,
        "resolve_script_api_source": r.script_api_source,
        "modules_dir": r.modules_dir,
        "modules_source": r.modules_source,
        "detection_order_lib": list(r.detection_order_lib),
        "detection_order_api": list(r.detection_order_api),
        "env_resolve_script_lib": os.environ.get("RESOLVE_SCRIPT_LIB"),
        "env_resolve_script_api": os.environ.get("RESOLVE_SCRIPT_API"),
    }
