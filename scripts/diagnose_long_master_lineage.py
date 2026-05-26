#!/usr/bin/env python3
"""Inspect master_generation JSON sources: geometry, policy, walking/driving/portrait flags."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
_NYC = _SCRIPTS / "nyc_auto"
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import auto_publish_queue as apq  # noqa: E402

try:
    from utils.storage_paths import get_sv_transfer  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")


from nyc_long_source_policy import (  # noqa: E402
    is_allowed_long_video169_candidate_path,
    is_valid_nyc_long_source,
    is_video169_long_source_path,
    long_source_pool_origin,
)


def _latest_manifest(xfer: Path) -> Path | None:
    root = xfer / "publish_pack" / "nyc_long_uploads"
    if not root.is_dir():
        return None
    cands = sorted(
        [
            *root.glob("master_generation_*.json"),
            *root.glob("master_dry_run_plan_allowed_raw_*.json"),
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return cands[0] if cands else None


def _infer_flags(pol: dict[str, Any], low_path: str) -> dict[str, Any]:
    st = str(pol.get("source_type") or "").lower()
    orient = str(pol.get("orientation") or "").lower()
    walking = "walking_not_allowed" in " ".join(str(x) for x in (pol.get("reject_reasons") or []))
    walking = walking or st == "walking" or "walking" in low_path or "handheld" in low_path or "vlog" in low_path
    driving = st == "driving" or any(
        t in low_path for t in ("drive", "driving", "dash", "dashcam", "highway", "route", "vehicle")
    )
    portrait = orient == "portrait" or any(x in low_path for x in ("9_16", "vertical", "portrait", "reels", "tiktok"))
    landscape = orient == "landscape" or (pol.get("width") and pol.get("height") and int(pol["width"]) > int(pol["height"]))
    return {
        "is_walking": bool(walking),
        "is_driving": bool(driving),
        "is_portrait": bool(portrait),
        "is_landscape": bool(landscape),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=None,
        help="Path to master_generation_*.json (default: latest under SV_TRANSFER/publish_pack/nyc_long_uploads).",
    )
    args = ap.parse_args()

    xfer = get_sv_transfer(verbose=False)
    man = args.manifest
    if man is None:
        lp = _latest_manifest(xfer)
        if not lp:
            print(json.dumps({"ok": False, "error": "no_master_generation_manifest_found"}, indent=2))
            return 2
        man = lp
    man = man.expanduser()
    if not man.is_file():
        print(json.dumps({"ok": False, "error": "manifest_not_found", "path": str(man)}, indent=2))
        return 2
    try:
        doc = json.loads(man.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": repr(exc), "path": str(man)}, indent=2))
        return 3

    sources = doc.get("sources") if isinstance(doc, dict) else None
    if not isinstance(sources, list):
        print(json.dumps({"ok": False, "error": "no_sources_array", "path": str(man)}, indent=2))
        return 4

    rows: list[dict[str, Any]] = []
    for ent in sources:
        if not isinstance(ent, dict):
            continue
        pstr = str(ent.get("path") or "").strip()
        if not pstr:
            continue
        p = Path(pstr).expanduser()
        probe = apq._ffprobe_json(p) if p.is_file() else None
        dur, hv = apq._duration_and_has_video(probe) if probe else (None, False)
        pol = is_valid_nyc_long_source(p, ffprobe_meta=probe) if probe else is_valid_nyc_long_source(p)
        low = str(p).replace("\\", "/").lower()
        origin = long_source_pool_origin(p) if p.is_file() else "unknown_pool"
        v169_ok = bool(
            p.is_file()
            and is_video169_long_source_path(p)
            and is_allowed_long_video169_candidate_path(p)
            and origin == "raw_video169_source_pool"
        )
        non_viol = not v169_ok
        w, h = int(pol.get("width") or 0), int(pol.get("height") or 0)
        ar = float(pol.get("aspect_ratio") or (float(w) / float(h) if w > 0 and h > 0 else 0.0))
        flags = _infer_flags(pol, low)
        rows.append(
            {
                "source_path": str(p),
                "width": w,
                "height": h,
                "aspect_ratio": round(ar, 6),
                "orientation": pol.get("orientation"),
                "inferred_source_type": pol.get("source_type"),
                "duration_sec": float(dur or ent.get("duration_sec") or 0.0),
                "reason_selected": str(ent.get("usable_reason") or ent.get("pick_reason") or "manifest_entry"),
                "long_allowed": bool(pol.get("long_allowed")),
                "reject_reasons": pol.get("reject_reasons") or [],
                "is_video169_long_source_path": bool(p.is_file() and is_video169_long_source_path(p)),
                "source_pool_origin": origin,
                "non_video169_violation": bool(non_viol),
                **flags,
            }
        )

    lineage_ok = not any(bool(r.get("non_video169_violation")) for r in rows)
    out = {
        "ok": True,
        "manifest": str(man),
        "rows": rows,
        "master_output": doc.get("output_path") or doc.get("planned_output_path"),
        "master_lineage_video169_ok": lineage_ok,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"MASTER_LINEAGE_VIDEO169_OK={str(lineage_ok).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
