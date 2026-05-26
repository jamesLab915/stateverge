#!/usr/bin/env python3
"""Diagnose DaVinci ambient audio system v1 — modules, chains, Resolve, gates."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO = _SCRIPT_DIR.parent.parent
_CC_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"
_REPORT_MD = _CC_LOGS / "davinci_ambient_audio_system_v1.md"
_CONFIG = _REPO / "config" / "davinci_ambient_audio_system_v1.json"
_DOCS = _REPO / "docs" / "davinci_ambient_audio_system_v1.md"

if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_diagnose() -> dict:
    report: dict = {
        "version": "davinci_ambient_audio_system_v1",
        "timestamp": _utc_iso(),
        "ok": True,
        "checks": {},
        "warnings": [],
    }

    paths = {
        "chains_json": _SCRIPT_DIR / "ambient_chains" / "chains.json",
        "fairlight_chain_v1": _SCRIPT_DIR / "fairlight_chain_v1.py",
        "config": _CONFIG,
        "docs": _DOCS,
        "audio_policy": _REPO / "scripts" / "audio" / "stateverge_audio_policy_v1.json",
        "content_routing": _REPO / "config" / "stateverge_content_routing_v1.json",
    }
    for name, p in paths.items():
        report["checks"][f"path_{name}"] = {"ok": p.is_file(), "path": str(p)}
        if not p.is_file() and name in ("chains_json", "fairlight_chain_v1", "config", "docs"):
            report["ok"] = False

    try:
        from ambient_chains.loader import (  # noqa: WPS433
            AMBIENT_CHAIN_IDS,
            validate_chains_document,
            load_chains_document,
        )

        doc = load_chains_document()
        errs = validate_chains_document(doc)
        report["checks"]["chains_schema"] = {"ok": not errs, "errors": errs}
        report["ambient_chain_ids"] = list(AMBIENT_CHAIN_IDS)
        if errs:
            report["ok"] = False
    except Exception as exc:  # noqa: BLE001
        report["checks"]["chains_schema"] = {"ok": False, "error": repr(exc)}
        report["ok"] = False

    try:
        from ambient_chains.ffmpeg_fallback import build_ffmpeg_af_for_chain  # noqa: WPS433

        for cid in (
            "broadcast_ambient_master",
            "streaming_immersive_ambient",
            "ferry_preserve_atmosphere",
            "sleep_calm_ambient",
        ):
            af, meta = build_ffmpeg_af_for_chain(cid, shorts=(cid == "streaming_immersive_ambient"))
            report.setdefault("ffmpeg_samples", {})[cid] = {"af": af[:120] if af else "", "meta": meta}
    except Exception as exc:  # noqa: BLE001
        report["checks"]["ffmpeg_fallback"] = {"ok": False, "error": repr(exc)}
        report["ok"] = False

    try:
        from fairlight_chain_v1 import probe_resolve_for_fairlight  # noqa: WPS433

        report["resolve_probe"] = probe_resolve_for_fairlight()
    except Exception as exc:  # noqa: BLE001
        report["resolve_probe"] = {"error": repr(exc)}

    try:
        from presets import PRESET_RULES  # noqa: WPS433

        missing = [p for p in PRESET_RULES if "ambient_chain_id" not in PRESET_RULES[p]]
        report["checks"]["youtube_preset_ambient_mapping"] = {
            "ok": not missing,
            "missing": missing,
        }
        if missing:
            report["warnings"].append("presets_missing_ambient_chain_id")
    except Exception as exc:  # noqa: BLE001
        report["checks"]["youtube_presets"] = {"ok": False, "error": repr(exc)}

    if _CONFIG.is_file():
        try:
            cfg = json.loads(_CONFIG.read_text(encoding="utf-8"))
            report["config"] = cfg
            report["checks"]["config_enabled"] = {"ok": bool(cfg.get("enabled"))}
        except (OSError, json.JSONDecodeError) as exc:
            report["checks"]["config"] = {"ok": False, "error": repr(exc)}

    return report


def write_markdown_report(report: dict) -> Path:
    _CC_LOGS.mkdir(parents=True, exist_ok=True)
    lines = [
        "# DaVinci Ambient Audio System v1 — Diagnose",
        "",
        f"- **Time (UTC):** {report.get('timestamp')}",
        f"- **OK:** `{report.get('ok')}`",
        "",
        "## Ambient chains",
        "",
    ]
    for cid in report.get("ambient_chain_ids") or []:
        lines.append(f"- `{cid}`")
    lines.extend(
        [
            "",
            "## Resolve",
            "",
            f"```json\n{json.dumps(report.get('resolve_probe') or {}, indent=2)}\n```",
            "",
            "## Schema validation",
            "",
            f"```json\n{json.dumps(report.get('checks', {}).get('chains_schema', {}), indent=2)}\n```",
            "",
            "## Usage",
            "",
            "- Doc: `~/StateVerge/docs/davinci_ambient_audio_system_v1.md`",
            "- CLI chain: `python3 ~/StateVerge/scripts/davinci_audio_finish/fairlight_chain_v1.py --chain ferry_preserve_atmosphere`",
            "- Gate: `python3 ~/StateVerge/scripts/davinci_audio_finish/gate_v1.py --input-video <path> --preset auto`",
            "- Control Center: `/davinci-audio-finish`",
            "",
        ]
    )
    _REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _REPORT_MD


def main() -> int:
    report = run_diagnose()
    md_path = write_markdown_report(report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"report_md={md_path}")
    ready = bool(report.get("ok"))
    print(f"DAVINCI_AMBIENT_AUDIO_SYSTEM_V1_READY={str(ready).lower()}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
