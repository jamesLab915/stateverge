#!/usr/bin/env python3
"""Shorts AI ranking helper — tie-breaker scores only; does not bypass guards."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATEVERGE = Path.home() / "StateVerge"
OUT_PRIMARY = Path("/Volumes/SV_TRANSFER/publish_pack/shorts_uploads/shorts_ai_rankings.json")
OUT_FALLBACK = STATEVERGE / "data" / "shorts_runtime" / "shorts_ai_rankings.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-candidates", type=int, default=12)
    args = ap.parse_args()
    scripts_dir = STATEVERGE / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from ai.ai_client import ai_request

    sample = {
        "candidates": [
            {
                "candidate_id": f"c{i}",
                "basename": f"clip_{i}.mp4",
                "media_type": "video",
                "duration_sec": 28.0,
                "width": 1080,
                "height": 1920,
                "orientation": "portrait",
                "location_hint": "NYC",
                "highlight_score": 0.5 + i * 0.01,
            }
            for i in range(min(args.max_candidates, 5))
        ]
    }
    r = ai_request(
        "shorts_highlight_scoring",
        json.dumps(sample, ensure_ascii=False),
        system_hint='Return JSON {"rankings": [{"candidate_id":"","ai_score":0,"ai_reason":"","suggested_title_angle":"","suggested_shorts_hook":"","risk_flags":[],"fallback_used":false}]}',
    )
    rows: list[dict[str, Any]] = []
    ok = bool(r.get("ok"))
    data = r.get("data") if isinstance(r.get("data"), dict) else {}
    for item in data.get("rankings") or []:
        if isinstance(item, dict):
            item.setdefault("fallback_used", not ok)
            rows.append(item)
    if not rows:
        rows = [
            {
                "candidate_id": "none",
                "ai_score": 0,
                "ai_reason": "ai_unavailable",
                "fallback_used": True,
            }
        ]
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "rankings": rows}
    out = OUT_PRIMARY
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        OUT_FALLBACK.parent.mkdir(parents=True, exist_ok=True)
        OUT_FALLBACK.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        out = OUT_FALLBACK
    print(json.dumps({"ok": True, "path": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
