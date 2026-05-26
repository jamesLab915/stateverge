#!/usr/bin/env python3
"""Diagnose Long Video Audio Policy v1 wiring."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CC = Path.home() / "StateVerge_Control_Center" / "logs"
_CC.mkdir(parents=True, exist_ok=True)
_OUT_JSON = _CC / "long_audio_policy_diagnose.json"
_OUT_MD = _CC / "long_audio_policy_diagnose.md"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _grep(hay: str, pat: str) -> bool:
    return re.search(pat, hay, re.MULTILINE | re.DOTALL) is not None


def main() -> int:
    warnings: list[str] = []
    pol = _REPO / "scripts" / "nyc_auto" / "long_audio_policy.py"
    longq = _REPO / "scripts" / "nyc_auto" / "auto_publish_queue.py"
    mg = _REPO / "scripts" / "nyc_auto" / "metadata_generator.py"

    policy_module_exists = pol.is_file()
    auto_publish_queue_connected = longq.is_file() and _grep(_read(longq), r"infer_long_audio_policy")
    metadata_generator_connected = mg.is_file() and _grep(_read(mg), r"apply_long_audio_policy_to_meta")

    def _inf(p: str) -> str:
        try:
            sys.path.insert(0, str(_REPO / "scripts" / "nyc_auto"))
            from long_audio_policy import infer_long_audio_policy  # noqa: WPS433

            r = infer_long_audio_policy(Path(p))
            return str(r.get("audio_mode") or "")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"infer_fail:{p}:{type(exc).__name__}")
            return ""

    sample_music_ok = _inf("/Volumes/SV_CACHE/inbox/truck_bad_audio_sample.mov") == "music"
    sample_real_sound_ok = _inf("/Volumes/SV_CACHE/inbox/ferry_sunset_sample.mov") == "real_sound"
    sample_no_vocals_ok = _inf("/Volumes/SV_CACHE/inbox/voice_conversation_sample.mov") == "no_vocals_clean_ambient"
    yew = "/Volumes/SV_CACHE/audio_separated/yewan/fixed/yewan_no_vocals_CFR30_FIXED.mp4"
    ymode = _inf(yew)
    sample_clean_ambient_ok = ymode in ("clean_ambient", "no_vocals_clean_ambient")

    latest_pkg_audio = ""
    mp = Path("/Volumes/SV_TRANSFER/publish_pack/manual_uploads/brooklyn_night_drive_yewan/youtube_metadata.json")
    if mp.is_file():
        try:
            dj = json.loads(mp.read_text(encoding="utf-8", errors="replace"))
            latest_pkg_audio = str(dj.get("audio_mode") or "")
        except (OSError, json.JSONDecodeError):
            warnings.append("brooklyn_metadata_unreadable")

    status = "ok"
    if not policy_module_exists:
        status = "error"
    if not auto_publish_queue_connected or not metadata_generator_connected:
        status = "warning"

    payload = {
        "policy_module_exists": policy_module_exists,
        "auto_publish_queue_connected": auto_publish_queue_connected,
        "metadata_generator_connected": metadata_generator_connected,
        "sample_music_ok": sample_music_ok,
        "sample_real_sound_ok": sample_real_sound_ok,
        "sample_no_vocals_ok": sample_no_vocals_ok,
        "sample_clean_ambient_ok": sample_clean_ambient_ok,
        "latest_package_audio_mode": latest_pkg_audio,
        "warnings": warnings,
        "status": status,
    }
    _OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Long audio policy diagnose (v1)",
        "",
        *(f"- {k}: {payload[k]}" for k in payload if k != "warnings"),
        "",
        "## warnings",
        "\n".join(f"- {w}" for w in warnings) or "- (none)",
    ]
    _OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": status != "error", "status": status, "wrote_json": str(_OUT_JSON)}, indent=2))
    return 0 if status != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
