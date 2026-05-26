#!/usr/bin/env python3
"""Install / status / uninstall the StateVerge daily auto-tracking LaunchAgent.

Schedule: every day at 23:30 local time (StartCalendarInterval).

Plist label : com.stateverge.tracking.autorefresh
Plist path  : ~/Library/LaunchAgents/com.stateverge.tracking.autorefresh.plist
Runner      : <repo>/scripts/tracking/run_auto_tracking.sh
Logs        : <repo>/logs/tracking/launchd_stdout.log
              <repo>/logs/tracking/launchd_stderr.log

The agent is non-invasive: it only triggers Python tracker scripts that scan
the StateVerge repo tree. It does not monitor keyboard, screen, network,
or any folder outside the repo.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import stat
import subprocess
import sys
from pathlib import Path

_PR = Path(__file__).resolve().parent
if str(_PR) not in sys.path:
    sys.path.insert(0, str(_PR))
from _paths import REPO_ROOT, LOGS  # noqa: E402

LABEL = "com.stateverge.tracking.autorefresh"
PLIST_NAME = f"{LABEL}.plist"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS_DIR / PLIST_NAME
RUNNER = REPO_ROOT / "scripts" / "tracking" / "run_auto_tracking.sh"
STDOUT_LOG = LOGS / "launchd_stdout.log"
STDERR_LOG = LOGS / "launchd_stderr.log"

DAILY_HOUR = 23
DAILY_MINUTE = 30


def _ensure_runner_executable() -> None:
    if not RUNNER.is_file():
        print(f"[launchd] error=missing_runner path={RUNNER}", file=sys.stderr)
        sys.exit(1)
    mode = RUNNER.stat().st_mode
    RUNNER.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _build_plist() -> dict:
    LOGS.mkdir(parents=True, exist_ok=True)
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", str(RUNNER)],
        "WorkingDirectory": str(REPO_ROOT),
        "StandardOutPath": str(STDOUT_LOG),
        "StandardErrorPath": str(STDERR_LOG),
        "RunAtLoad": False,
        "StartCalendarInterval": {
            "Hour": DAILY_HOUR,
            "Minute": DAILY_MINUTE,
        },
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
            "LANG": "en_US.UTF-8",
        },
    }


def _write_plist() -> None:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    data = _build_plist()
    with PLIST_PATH.open("wb") as f:
        plistlib.dump(data, f)
    PLIST_PATH.chmod(0o644)


def _launchctl(args: list[str]) -> tuple[int, str, str]:
    p = subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        errors="replace",
    )
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _bootout_silent() -> None:
    uid = os.getuid()
    _launchctl(["bootout", f"gui/{uid}/{LABEL}"])
    _launchctl(["unload", str(PLIST_PATH)])


def _bootstrap() -> tuple[int, str]:
    uid = os.getuid()
    rc, out, err = _launchctl(["bootstrap", f"gui/{uid}", str(PLIST_PATH)])
    if rc == 0:
        return 0, "bootstrap"
    rc2, out2, err2 = _launchctl(["load", "-w", str(PLIST_PATH)])
    if rc2 == 0:
        return 0, "load -w"
    return rc, f"bootstrap_err={err or out} load_err={err2 or out2}"


def cmd_install() -> int:
    _ensure_runner_executable()
    _bootout_silent()
    _write_plist()
    rc, how = _bootstrap()
    if rc != 0:
        print(f"[launchd] action=install status=FAIL detail={how}", file=sys.stderr)
        return 1
    print(
        f"[launchd] action=install status=OK label={LABEL} via={how} "
        f"schedule=daily {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} plist={PLIST_PATH}"
    )
    print(f"[launchd]   stdout_log={STDOUT_LOG}")
    print(f"[launchd]   stderr_log={STDERR_LOG}")
    return 0


def cmd_uninstall() -> int:
    if not PLIST_PATH.is_file():
        print(f"[launchd] action=uninstall status=NOOP (no plist at {PLIST_PATH})")
        return 0
    _bootout_silent()
    try:
        PLIST_PATH.unlink()
    except OSError as e:
        print(f"[launchd] action=uninstall status=FAIL detail={e}", file=sys.stderr)
        return 1
    print(f"[launchd] action=uninstall status=OK plist_removed={PLIST_PATH}")
    return 0


def cmd_status() -> int:
    print(f"[launchd] label={LABEL}")
    print(f"[launchd] plist={PLIST_PATH} exists={PLIST_PATH.is_file()}")
    print(f"[launchd] runner={RUNNER} exists={RUNNER.is_file()}")
    print(f"[launchd] schedule=daily {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} (StartCalendarInterval)")
    print(f"[launchd] stdout_log={STDOUT_LOG}")
    print(f"[launchd] stderr_log={STDERR_LOG}")
    rc, out, err = _launchctl(["list"])
    loaded = False
    last_pid = ""
    last_status = ""
    if rc == 0:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[2].strip() == LABEL:
                loaded = True
                last_pid = parts[0].strip()
                last_status = parts[1].strip()
                break
    print(f"[launchd] loaded={loaded} last_pid={last_pid or '-'} last_exit={last_status or '-'}")
    if STDOUT_LOG.is_file():
        try:
            tail = STDOUT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
            if tail:
                print("[launchd] recent stdout:")
                for ln in tail:
                    print(f"  {ln}")
        except OSError:
            pass
    if STDERR_LOG.is_file():
        try:
            tail = STDERR_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
            if tail:
                print("[launchd] recent stderr:")
                for ln in tail:
                    print(f"  {ln}")
        except OSError:
            pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Install/status/uninstall StateVerge daily auto-tracking LaunchAgent (23:30 local)."
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true", help="Install / refresh and start the daily LaunchAgent")
    g.add_argument("--uninstall", action="store_true", help="Stop and remove the LaunchAgent")
    g.add_argument("--status", action="store_true", help="Show plist + launchctl + recent log status")
    args = ap.parse_args()
    if sys.platform != "darwin":
        print("[launchd] note: launchd is macOS-only; commands are no-ops on this platform.", file=sys.stderr)
        return 0
    if args.install:
        return cmd_install()
    if args.uninstall:
        return cmd_uninstall()
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())
