#!/usr/bin/env python3
"""Install .git/hooks/post-commit to run track_stateverge_activity.py (stdlib only)."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

HOOK_BODY = r'''#!/bin/sh
# Installed by scripts/tracking/install_git_hooks.py
# Non-blocking: every step swallows errors so a failed tracker can never
# interrupt a real `git commit`.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$REPO_ROOT" || exit 0

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  exit 0
fi

# 1. File-tree snapshot diff
"$PY" scripts/tracking/track_stateverge_activity.py --mode git-hook >/dev/null 2>&1 || true

# 2. Local git activity → github_activity_log.csv
"$PY" scripts/tracking/github_activity_collector.py --mode local-log >/dev/null 2>&1 || true

# 3. Topic / output progress snapshot
"$PY" scripts/tracking/project_progress_tracker.py >/dev/null 2>&1 || true

# 4. Tool usage frequency rebuild
"$PY" scripts/tracking/tool_usage_tracker.py >/dev/null 2>&1 || true

exit 0
'''


def _git_hooks_dir() -> Path | None:
    p = subprocess.run(
        [
            "git",
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "hooks",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        p = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    if p.returncode != 0 or not p.stdout.strip():
        return None
    raw = p.stdout.strip()
    hooks = Path(raw)
    if not hooks.is_absolute():
        hooks = (REPO_ROOT / hooks).resolve()
    return hooks


def _in_git_repo() -> bool:
    p = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return p.returncode == 0


def main() -> int:
    if not _in_git_repo():
        print(
            "[tracking] install_git_hooks: not a git repository at "
            f"{REPO_ROOT}. Run `git init` to enable post-commit auto-tracking. "
            "Manual scan: python3 scripts/tracking/track_stateverge_activity.py --mode manual"
        )
        return 0
    hooks_dir = _git_hooks_dir()
    if not hooks_dir:
        print("[tracking] install_git_hooks: could not resolve git hooks path.", file=sys.stderr)
        return 1
    post_commit = hooks_dir / "post-commit"
    marker = "track_stateverge_activity.py"
    extended_marker = "github_activity_collector.py"
    if post_commit.is_file():
        try:
            existing = post_commit.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[tracking] read_error path={post_commit} err={e}", file=sys.stderr)
            return 1
        if marker in existing and extended_marker in existing:
            print(f"[tracking] post-commit already up to date: {post_commit}")
            return 0
        if marker in existing:
            backup = post_commit.with_suffix(".bak")
            try:
                backup.write_text(existing, encoding="utf-8")
            except OSError:
                pass
            print(
                f"[tracking] upgrading existing StateVerge post-commit hook (backup: {backup.name})"
            )
        elif existing.strip():
            print(
                f"[tracking] warning: {post_commit} exists and is not from this installer (refusing to overwrite).",
                file=sys.stderr,
            )
            print("Append the following, or back up the file and re-run:\n", file=sys.stderr)
            print(HOOK_BODY, file=sys.stderr)
            return 1
    hooks_dir.mkdir(parents=True, exist_ok=True)
    post_commit.write_text(HOOK_BODY, encoding="utf-8")
    mode = post_commit.stat().st_mode
    post_commit.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[tracking] action=install_hook path={post_commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
