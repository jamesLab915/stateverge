#!/usr/bin/env python3
"""Export Fairlight chain spec for reports and manual Resolve workflow."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ambient_chains.loader import chain_spec, load_chains_document


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def export_chain_spec(
    chain_id: str,
    *,
    timeline_name: str | None = None,
    youtube_preset: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = chain_spec(chain_id)
    doc = load_chains_document()
    return {
        "version": doc.get("version"),
        "exported_at": _utc_iso(),
        "ambient_chain_id": chain_id,
        "label": spec.get("label"),
        "youtube_preset": youtube_preset,
        "timeline_name": timeline_name,
        "fairlight": spec.get("fairlight"),
        "ffmpeg_fallback": spec.get("ffmpeg_fallback"),
        "forbidden_global": doc.get("forbidden_global"),
        "manual_resolve_page": "fairlight",
        **(extra or {}),
    }


def write_chain_spec_json(
    chain_id: str,
    output_path: Path,
    *,
    timeline_name: str | None = None,
    youtube_preset: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    payload = export_chain_spec(
        chain_id,
        timeline_name=timeline_name,
        youtube_preset=youtube_preset,
        extra=extra,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def fairlight_manual_steps(chain_id: str) -> list[str]:
    """Human-readable steps for DaVinci Fairlight UI."""
    spec = chain_spec(chain_id)
    fl = spec.get("fairlight") or {}
    steps: list[str] = [
        f"1. Open Fairlight page on timeline; select chain: {spec.get('label', chain_id)}",
        f"2. Bus layout: {', '.join(fl.get('bus_layout') or ['Master'])}",
    ]
    if fl.get("highpass_hz"):
        steps.append(f"3. EQ: high-pass {fl['highpass_hz']} Hz (gentle slope 12 dB/oct)")
    if fl.get("volume") is not None:
        steps.append(f"4. Ambience track volume: {fl['volume']}")
    if fl.get("multiband_compressor", {}).get("enabled"):
        mc = fl["multiband_compressor"]
        steps.append(
            f"5. Multiband compressor (light): ratio {mc.get('ratio', 1.4)}, "
            f"threshold {mc.get('threshold_db', -18)} dB"
        )
    if fl.get("deesser", {}).get("enabled"):
        steps.append(f"6. De-esser on {fl['deesser'].get('bus', 'Ambience')} bus only")
    if fl.get("mid_side_widen", {}).get("enabled"):
        steps.append(
            f"7. Mid/Side widen ~{fl['mid_side_widen'].get('amount_pct', 12)}% (subtle)"
        )
    if fl.get("wind_shelf", {}).get("enabled"):
        ws = fl["wind_shelf"]
        steps.append(
            f"8. Wind shelf: {ws.get('gain_db', -3)} dB above {ws.get('freq_hz', 8000)} Hz (spikes only)"
        )
    if fl.get("ambience_bus_when_music_db") is not None:
        steps.append(
            f"9. Sidechain: reduce ambience bus to {fl['ambience_bus_when_music_db']} dB when music plays"
        )
    loud = fl.get("loudness") or {}
    if loud:
        lufs = loud.get("integrated_lufs") or loud.get("integrated_lufs_long")
        tp = loud.get("true_peak_db", -1.5)
        if lufs is not None:
            steps.append(f"10. Loudness meter target: {lufs} LUFS integrated, true peak {tp} dBTP")
    if fl.get("limiter"):
        steps.append(f"11. Limiter ceiling: {fl['limiter'].get('true_peak_db', -2)} dBTP")
    if fl.get("forbidden"):
        steps.append(f"12. NEVER use: {', '.join(fl['forbidden'])}")
    nr = fl.get("spectral_repair_optional") or {}
    if nr.get("enabled"):
        steps.append(
            f"Optional NR max {nr.get('fairlight_nr_max_reduction_db', -20)} dB reduction only"
        )
    elif nr:
        steps.append("Optional Fairlight NR: off by default (preserve room tone)")
    return steps
