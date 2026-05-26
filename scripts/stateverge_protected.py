"""Protected-path rules for all StateVerge SSD automation (私人别碰)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _default_private_root() -> Path:
    try:
        from stateverge_paths import SSD_ROOT

        p = SSD_ROOT / "私人别碰"
    except Exception:
        p = Path("/Volumes/StateVerge/私人别碰")
    try:
        return p.resolve()
    except OSError:
        return p


def _config_candidates() -> tuple[Path, ...]:
    try:
        from stateverge_paths import SSD_ROOT

        vol_cfg = SSD_ROOT / "08_CONFIG" / "protected_paths.json"
    except Exception:
        vol_cfg = Path("/Volumes/StateVerge/08_CONFIG/protected_paths.json")
    code_root = Path(
        os.environ.get("STATEVERGE_ROOT", str(Path.home() / "StateVerge"))
    ).expanduser()
    return (vol_cfg, code_root / "config" / "protected_paths.json")


def _load_protected_roots() -> tuple[Path, ...]:
    for cfg in _config_candidates():
        if not cfg.is_file():
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            paths = data.get("protected_paths") or []
            out: list[Path] = []
            for p in paths:
                if isinstance(p, str) and p.strip():
                    out.append(Path(p).expanduser().resolve())
            if out:
                return tuple(out)
        except (json.JSONDecodeError, OSError, ValueError):
            continue
    return (_default_private_root(),)


_PROTECTED_ROOTS: tuple[Path, ...] | None = None


def protected_roots() -> tuple[Path, ...]:
    global _PROTECTED_ROOTS
    if _PROTECTED_ROOTS is None:
        _PROTECTED_ROOTS = _load_protected_roots()
    return _PROTECTED_ROOTS


def reload_protected_roots() -> None:
    global _PROTECTED_ROOTS
    _PROTECTED_ROOTS = None


def is_protected_path(path: str | Path) -> bool:
    """True if absolute path is inside any protected root (私人别碰)."""
    try:
        p = Path(path).expanduser().resolve()
    except OSError:
        return False
    s = str(p)
    for root in protected_roots():
        rs = str(root.resolve())
        if s == rs or s.startswith(rs + os.sep):
            return True
    return False
