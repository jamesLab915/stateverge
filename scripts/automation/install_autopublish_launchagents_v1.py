#!/usr/bin/env python3
"""Install official StateVerge autopublish LaunchAgents (v1).

Fail-open: always exits 0; actions and errors are recorded in JSON.

Official Python: ``~/StateVerge/.venv_audio/bin/python3``.
Logs: ``~/StateVerge_Control_Center/logs/``.

Default is dry-run (no plist writes, no launchctl). Use ``--apply`` to write plists
and reload agents.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHORTS_LABEL = "com.stateverge.shorts.autopublish"
NYC_LONG_SHORTS_LABEL = "com.stateverge.nyc_long_shorts.autopublish"
SHORTS_DUAL_LABEL = "com.stateverge.shorts.dual.autopublish"
NYC_LABEL = "com.stateverge.nyc.autopublish"
LEGACY_YOUTUBE_LABEL = "com.stateverge.youtube.publish"

# Both Shorts channels: every 3h local (8 slots/day) from SV_CACHE/air portrait pool.
_SHORTS_EVERY_3H: list[tuple[int, int]] = [(0, 0), (3, 0), (6, 0), (9, 0), (12, 0), (15, 0), (18, 0), (21, 0)]
SHORTS_SCHEDULE: list[tuple[int, int]] = list(_SHORTS_EVERY_3H)
NYC_LONG_SHORTS_SCHEDULE: list[tuple[int, int]] = list(_SHORTS_EVERY_3H)
NYC_SCHEDULE: list[tuple[int, int]] = [(10, 0), (16, 0)]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _home() -> Path:
    return Path.home()


def _stateverge_root() -> Path:
    return _home() / "StateVerge"


def _control_root() -> Path:
    return _home() / "StateVerge_Control_Center"


def _logs_dir() -> Path:
    return _control_root() / "logs"


def _official_python() -> Path:
    return _stateverge_root() / ".venv_audio" / "bin" / "python3"


def _user_launchagents() -> Path:
    return _home() / "Library" / "LaunchAgents"


def _template_dir() -> Path:
    return _control_root() / "launchagents"


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _render_shorts_plist() -> str:
    root = _stateverge_root()
    py = _official_python()
    q = root / "scripts" / "nyc_auto" / "auto_publish_queue_shorts.py"
    logs = _logs_dir()
    intervals = []
    for h, m in SHORTS_SCHEDULE:
        intervals.append(
            f"""    <dict>
      <key>Hour</key>
      <integer>{h}</integer>
      <key>Minute</key>
      <integer>{m}</integer>
    </dict>"""
        )
    intervals_xml = "\n".join(intervals)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{SHORTS_LABEL}</string>
  <key>WorkingDirectory</key>
  <string>{root}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LANG</key>
    <string>en_US.UTF-8</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>FFMPEG_BIN</key>
    <string>/opt/homebrew/bin/ffmpeg</string>
    <key>FFPROBE_BIN</key>
    <string>/opt/homebrew/bin/ffprobe</string>
    <key>STATEVERGE_SHORTS_MATERIALS_DIR</key>
    <string>/Volumes/SV_CACHE/air</string>
    <key>STATEVERGE_SHORTS_PORTRAIT_ONLY</key>
    <string>0</string>
    <key>PYTHONPATH</key>
    <string>{root / "src"}:{root / "scripts"}:{root / "scripts" / "nyc_auto"}</string>
  </dict>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>{q}</string>
    <string>--upload</string>
    <string>--privacy-status</string>
    <string>unlisted</string>
    <string>--asset-mode</string>
    <string>video_only</string>
    <string>--max-count</string>
    <string>1</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
{intervals_xml}
  </array>
  <key>StandardOutPath</key>
  <string>{logs / "launchd_shorts_stdout.log"}</string>
  <key>StandardErrorPath</key>
  <string>{logs / "launchd_shorts_stderr.log"}</string>
</dict>
</plist>
"""


def _render_nyc_long_shorts_plist() -> str:
    root = _stateverge_root()
    py = _official_python()
    q = root / "scripts" / "nyc_auto" / "auto_publish_queue_nyc_long_shorts.py"
    logs = _logs_dir()
    intervals = []
    for h, m in NYC_LONG_SHORTS_SCHEDULE:
        intervals.append(
            f"""    <dict>
      <key>Hour</key>
      <integer>{h}</integer>
      <key>Minute</key>
      <integer>{m}</integer>
    </dict>"""
        )
    intervals_xml = "\n".join(intervals)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{NYC_LONG_SHORTS_LABEL}</string>
  <key>WorkingDirectory</key>
  <string>{root}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LANG</key>
    <string>en_US.UTF-8</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>FFMPEG_BIN</key>
    <string>/opt/homebrew/bin/ffmpeg</string>
    <key>FFPROBE_BIN</key>
    <string>/opt/homebrew/bin/ffprobe</string>
    <key>STATEVERGE_SHORTS_MATERIALS_DIR</key>
    <string>/Volumes/SV_CACHE/air</string>
    <key>STATEVERGE_SHORTS_PORTRAIT_ONLY</key>
    <string>0</string>
    <key>PYTHONPATH</key>
    <string>{root / "src"}:{root / "scripts"}:{root / "scripts" / "nyc_auto"}</string>
  </dict>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>{q}</string>
    <string>--upload</string>
    <string>--privacy-status</string>
    <string>unlisted</string>
    <string>--asset-mode</string>
    <string>video_only</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
{intervals_xml}
  </array>
  <key>StandardOutPath</key>
  <string>{logs / "launchd_nyc_long_shorts_stdout.log"}</string>
  <key>StandardErrorPath</key>
  <string>{logs / "launchd_nyc_long_shorts_stderr.log"}</string>
</dict>
</plist>
"""


def _render_shorts_dual_plist() -> str:
    """Single agent: both Shorts channels per tick (sequential encode, dual upload)."""
    root = _stateverge_root()
    py = _official_python()
    q = root / "scripts" / "nyc_auto" / "auto_publish_queue_shorts_dual.py"
    logs = _logs_dir()
    intervals = []
    for h, m in SHORTS_SCHEDULE:
        intervals.append(
            f"""    <dict>
      <key>Hour</key>
      <integer>{h}</integer>
      <key>Minute</key>
      <integer>{m}</integer>
    </dict>"""
        )
    intervals_xml = "\n".join(intervals)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{SHORTS_DUAL_LABEL}</string>
  <key>WorkingDirectory</key>
  <string>{root}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LANG</key>
    <string>en_US.UTF-8</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>FFMPEG_BIN</key>
    <string>/opt/homebrew/bin/ffmpeg</string>
    <key>FFPROBE_BIN</key>
    <string>/opt/homebrew/bin/ffprobe</string>
    <key>STATEVERGE_SHORTS_MATERIALS_DIR</key>
    <string>/Volumes/SV_CACHE/air</string>
    <key>STATEVERGE_SHORTS_PORTRAIT_ONLY</key>
    <string>0</string>
    <key>PYTHONPATH</key>
    <string>{root / "src"}:{root / "scripts"}:{root / "scripts" / "nyc_auto"}</string>
  </dict>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>{q}</string>
    <string>--upload</string>
    <string>--privacy-status</string>
    <string>unlisted</string>
    <string>--asset-mode</string>
    <string>video_only</string>
    <string>--audio-mode</string>
    <string>original</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
{intervals_xml}
  </array>
  <key>StandardOutPath</key>
  <string>{logs / "launchd_shorts_dual_stdout.log"}</string>
  <key>StandardErrorPath</key>
  <string>{logs / "launchd_shorts_dual_stderr.log"}</string>
</dict>
</plist>
"""


def _render_nyc_plist() -> str:
    root = _stateverge_root()
    py = _official_python()
    q = root / "scripts" / "nyc_auto" / "auto_publish_queue.py"
    logs = _logs_dir()
    intervals = []
    for h, m in NYC_SCHEDULE:
        intervals.append(
            f"""    <dict>
      <key>Hour</key>
      <integer>{h}</integer>
      <key>Minute</key>
      <integer>{m}</integer>
    </dict>"""
        )
    intervals_xml = "\n".join(intervals)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{NYC_LABEL}</string>
  <key>WorkingDirectory</key>
  <string>{root}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>{q}</string>
    <string>--upload</string>
    <string>--privacy-status</string>
    <string>unlisted</string>
    <string>--max-count</string>
    <string>1</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
{intervals_xml}
  </array>
  <key>StandardOutPath</key>
  <string>{logs / "launchd_nyc_long_stdout.log"}</string>
  <key>StandardErrorPath</key>
  <string>{logs / "launchd_nyc_long_stderr.log"}</string>
</dict>
</plist>
"""


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".apl_", suffix=".plist", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _run_launchctl(args: list[str]) -> dict[str, Any]:
    try:
        r = subprocess.run(
            ["launchctl", *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return {
            "args": args,
            "returncode": int(r.returncode),
            "stdout": (r.stdout or "").strip(),
            "stderr": (r.stderr or "").strip(),
        }
    except Exception as exc:
        return {"args": args, "returncode": -1, "error": repr(exc)}


def _reload_agent(plist_path: Path, label: str, report: dict[str, Any]) -> None:
    dom = _launchd_domain()
    steps: list[dict[str, Any]] = []
    # Best-effort unload, then load/bootstrap (user spec: unload then load).
    steps.append(_run_launchctl(["bootout", dom, label]))
    steps.append(_run_launchctl(["unload", str(plist_path)]))
    rb = _run_launchctl(["bootstrap", dom, str(plist_path)])
    steps.append(rb)
    if rb.get("returncode") != 0:
        rl = _run_launchctl(["load", str(plist_path)])
        steps.append(rl)
        ok = rl.get("returncode") == 0
    else:
        ok = True
    report.setdefault("launchctl", {})[label] = {"steps": steps, "reload_ok": ok}


def sync_official_autopublish_plists(
    *,
    apply: bool,
    disable_legacy_youtube_publish: bool,
    report: dict[str, Any],
) -> None:
    """Write golden plist XML to Control Center templates and optionally ~/Library."""
    shorts_xml = _render_shorts_plist()
    nyc_long_shorts_xml = _render_nyc_long_shorts_plist()
    shorts_dual_xml = _render_shorts_dual_plist()
    nyc_xml = _render_nyc_plist()
    tmpl_shorts = _template_dir() / f"{SHORTS_LABEL}.plist"
    tmpl_nyc_long_shorts = _template_dir() / f"{NYC_LONG_SHORTS_LABEL}.plist"
    tmpl_shorts_dual = _template_dir() / f"{SHORTS_DUAL_LABEL}.plist"
    tmpl_nyc = _template_dir() / f"{NYC_LABEL}.plist"
    user_shorts = _user_launchagents() / f"{SHORTS_LABEL}.plist"
    user_nyc_long_shorts = _user_launchagents() / f"{NYC_LONG_SHORTS_LABEL}.plist"
    user_shorts_dual = _user_launchagents() / f"{SHORTS_DUAL_LABEL}.plist"
    user_nyc = _user_launchagents() / f"{NYC_LABEL}.plist"

    report["shorts_template_path"] = str(tmpl_shorts)
    report["nyc_long_shorts_template_path"] = str(tmpl_nyc_long_shorts)
    report["shorts_dual_template_path"] = str(tmpl_shorts_dual)
    report["nyc_template_path"] = str(tmpl_nyc)
    report["shorts_user_plist_path"] = str(user_shorts)
    report["nyc_long_shorts_user_plist_path"] = str(user_nyc_long_shorts)
    report["shorts_dual_user_plist_path"] = str(user_shorts_dual)
    report["nyc_user_plist_path"] = str(user_nyc)
    report["official_python"] = str(_official_python())
    report["shorts_dual_schedule"] = SHORTS_SCHEDULE
    report["legacy_single_channel_agents_disabled_on_apply"] = [SHORTS_LABEL, NYC_LONG_SHORTS_LABEL]

    if not apply:
        report["would_write"] = [
            str(tmpl_shorts_dual),
            str(tmpl_nyc),
            str(user_shorts_dual),
            str(user_nyc),
        ]
        return

    try:
        _logs_dir().mkdir(parents=True, exist_ok=True)
        _template_dir().mkdir(parents=True, exist_ok=True)
        _user_launchagents().mkdir(parents=True, exist_ok=True)
        _atomic_write_text(tmpl_shorts, shorts_xml)
        _atomic_write_text(tmpl_nyc_long_shorts, nyc_long_shorts_xml)
        _atomic_write_text(tmpl_shorts_dual, shorts_dual_xml)
        _atomic_write_text(tmpl_nyc, nyc_xml)
        _atomic_write_text(user_shorts_dual, shorts_dual_xml)
        _atomic_write_text(user_nyc, nyc_xml)
        report["plists_written"] = True
    except Exception as exc:
        report.setdefault("errors", []).append(f"plist_write:{exc!r}")
        report["plists_written"] = False
        return

    dom = _launchd_domain()
    for legacy_label in (SHORTS_LABEL, NYC_LONG_SHORTS_LABEL):
        report.setdefault("launchctl_disabled", {})[legacy_label] = _run_launchctl(
            ["bootout", dom, legacy_label]
        )

    for plist_path, label in (
        (user_shorts_dual, SHORTS_DUAL_LABEL),
        (user_nyc, NYC_LABEL),
    ):
        try:
            _reload_agent(plist_path, label, report)
        except Exception as exc:
            report.setdefault("errors", []).append(f"launchctl_reload:{label}:{exc!r}")

    if disable_legacy_youtube_publish:
        dom = _launchd_domain()
        leg = _run_launchctl(["bootout", dom, LEGACY_YOUTUBE_LABEL])
        leg2 = _run_launchctl(["unload", str(_user_launchagents() / f"{LEGACY_YOUTUBE_LABEL}.plist")])
        report["legacy_youtube_publish_disable"] = {"bootout": leg, "unload": leg2}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install official autopublish LaunchAgents v1")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write plists and run launchctl (default: dry-run plan only)",
    )
    ap.add_argument(
        "--disable-legacy-youtube-publish",
        action="store_true",
        help="With --apply: best-effort unload legacy com.stateverge.youtube.publish (plist not deleted)",
    )
    ns = ap.parse_args(argv)
    apply = bool(ns.apply)
    report: dict[str, Any] = {
        "schema": "autopublish_launchagents_install_v1",
        "generated_at": _utc_iso(),
        "dry_run": not apply,
        "apply": apply,
        "disable_legacy_youtube_publish": bool(ns.disable_legacy_youtube_publish),
    }
    if ns.disable_legacy_youtube_publish and not apply:
        report["disable_legacy_youtube_publish_note"] = "ignored_without_apply"

    try:
        sync_official_autopublish_plists(
            apply=apply,
            disable_legacy_youtube_publish=bool(ns.disable_legacy_youtube_publish and apply),
            report=report,
        )
    except Exception as exc:
        report.setdefault("errors", []).append(f"sync:{exc!r}")

    out_json = _logs_dir() / "autopublish_launchagents_install_v1.json"
    try:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        report.setdefault("errors", []).append(f"json_write:{exc!r}")

    if not apply:
        print("[dry-run] Official autopublish LaunchAgent install plan (no writes).", file=sys.stderr)
        for p in report.get("would_write") or []:
            print(f"  would write: {p}", file=sys.stderr)
    else:
        print("[apply] Wrote official autopublish plists + best-effort launchctl reload.", file=sys.stderr)
    print(str(out_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
