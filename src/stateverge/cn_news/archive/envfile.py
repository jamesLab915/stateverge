"""Load secrets from local ``.env.local`` / ``.env`` files (never committed).

Precedence: variables already set in the environment (e.g. GitHub Actions
secrets) > ``.env.local`` > ``.env``. Both files are git-ignored (``.env*``);
only ``.env.example`` is tracked, with empty placeholders.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_FILES = (".env.local", ".env")

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, raw = m.groups()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        else:
            raw = raw.split(" #", 1)[0].rstrip()  # inline comment on unquoted value
        values[key] = raw
    return values


def load(root: Path = REPO_ROOT, files: tuple[str, ...] = DEFAULT_FILES) -> list[str]:
    """Set variables that are not already in the environment. Returns the names
    that were loaded (never the values)."""
    loaded: list[str] = []
    for name in files:
        path = Path(root) / name
        if not path.is_file():
            continue
        for key, value in parse(path.read_text(encoding="utf-8")).items():
            if value and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded
