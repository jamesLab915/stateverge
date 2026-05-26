#!/usr/bin/env python3
"""Diagnose DaVinci Folder Studio v1 readiness (paths, modules, no upload hooks)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_STUDIO = Path(__file__).resolve().parent
for p in (_REPO / "scripts", _STUDIO):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from davinci_studio.paths import (  # noqa: E402
    INBOX_ROOT,
    MARKER_READY,
    PROJECTS_ROOT,
    RENDERS_ROOT,
    REPORTS_ROOT,
    STUDIO_ROOT,
    THUMBNAILS_ROOT,
)

_REQUIRED_MODULES = (
    "folder_scan.py",
    "clip_thumbnails.py",
    "timeline_builder.py",
    "audio_modes.py",
    "music_selector.py",
    "render_job.py",
    "report_writer.py",
)


def main() -> int:
    report: dict[str, object] = {
        "version": "davinci_folder_studio_v1",
        "studio_root": str(STUDIO_ROOT),
        "dirs": {},
        "modules": {},
        "ok": True,
    }
    for name, p in (
        ("inbox", INBOX_ROOT),
        ("projects", PROJECTS_ROOT),
        ("renders", RENDERS_ROOT),
        ("reports", REPORTS_ROOT),
        ("cache", STUDIO_ROOT / "cache"),
        ("thumbnails", THUMBNAILS_ROOT),
    ):
        exists = p.is_dir()
        if not exists:
            try:
                p.mkdir(parents=True, exist_ok=True)
                exists = p.is_dir()
            except OSError:
                exists = False
        report["dirs"][name] = {"path": str(p), "exists": exists}
        if not exists:
            report["ok"] = False

    for mod in _REQUIRED_MODULES:
        fp = _STUDIO / mod
        report["modules"][mod] = fp.is_file()
        if not fp.is_file():
            report["ok"] = False

    cc_page = Path.home() / "StateVerge_Control_Center/frontend/src/app/davinci-studio/page.tsx"
    report["control_center_page"] = cc_page.is_file()

    try:
        from davinci_studio.audio_modes import AUDIO_MODES, MODE_LABELS  # noqa: WPS433

        report["audio_modes"] = list(AUDIO_MODES)
        report["audio_mode_labels"] = dict(MODE_LABELS)
    except Exception as exc:  # noqa: BLE001
        report["audio_modes_error"] = repr(exc)
        report["ok"] = False

    ready = bool(report.get("ok")) and bool(report.get("control_center_page")) and not report.get("audio_modes_error")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(MARKER_READY if ready else "DAVINCI_FOLDER_STUDIO_V1_READY=false")
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
