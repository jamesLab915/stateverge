#!/usr/bin/env python3
"""Quality audit for ``media_index_v3.json`` (read-only; writes reports next to index)."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from storage_paths import get_sv_transfer  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")


VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".hevc"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".dng"}


def default_index_path() -> Path:
    return get_sv_transfer(verbose=False) / "media_index" / "media_index_v3.json"


def _parse_usable(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x) for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x for x in val.split("|") if x.strip()]
    return []


def _flatten_tags(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    return []


def _is_video_item(it: dict[str, Any]) -> bool:
    try:
        d = float(it.get("duration_sec") or it.get("duration") or 0.0)
    except (TypeError, ValueError):
        d = 0.0
    if d > 0:
        return True
    p = str(it.get("path") or "")
    suf = Path(p).suffix.lower()
    return suf in VIDEO_EXTS


def _is_photo_item(it: dict[str, Any]) -> bool:
    if _is_video_item(it):
        return False
    p = str(it.get("path") or "")
    suf = Path(p).suffix.lower()
    return suf in PHOTO_EXTS


def _scene_weak(scene: list[str]) -> bool:
    if not scene:
        return True
    low = [str(x).lower() for x in scene]
    if len(low) == 1 and low[0] == "city_broll":
        return True
    if set(low) <= {"city_broll"}:
        return True
    if any("unknown" in x for x in low):
        return True
    return False


def _mood_too_generic(mood: list[str]) -> bool:
    if not mood:
        return False
    low = [str(x).lower() for x in mood]
    if low == ["urban"]:
        return True
    generic = {"urban", "cinematic"}
    if len(low) <= 2 and set(low) <= generic:
        return True
    return False


def _borough_missing(b: str) -> bool:
    s = (b or "").strip().lower()
    return not s or s == "unknown"


def _time_of_day_missing(t: str) -> bool:
    return str(t or "").strip() == ""


def _suspicious_ext_scene(it: dict[str, Any]) -> bool:
    p = str(it.get("path") or "")
    suf = Path(p).suffix.lower()
    scene = [str(x).lower() for x in _flatten_tags(it.get("scene_type"))]
    has_still = any("still_photo" in x for x in scene)
    if suf in VIDEO_EXTS and has_still:
        return True
    if suf in PHOTO_EXTS and not has_still and scene:
        # "vice versa": photo ext but scene looks video-native (driving/traffic) without still_photo
        videoish = {"driving", "traffic", "city_street", "dash_fixed"}
        if any(x in videoish for x in scene):
            return True
    return False


def _realpath_best_effort(p: str) -> str:
    try:
        return os.path.realpath(p)
    except OSError:
        return p


def audit_items(items: list[dict[str, Any]], *, quality_threshold: float) -> dict[str, Any]:
    total = len(items)
    video_n = sum(1 for it in items if _is_video_item(it))
    photo_n = sum(1 for it in items if _is_photo_item(it))

    missing_borough = 0
    missing_tod = 0
    weak_scene = 0
    generic_mood = 0
    empty_usable = 0
    suspicious = 0
    low_quality = 0

    scene_ctr: Counter[str] = Counter()
    mood_ctr: Counter[str] = Counter()
    usable_ctr: Counter[str] = Counter()

    ids: list[str] = []
    basenames: list[str] = []
    realpaths: list[str] = []

    for it in items:
        bor = str(it.get("borough") or "")
        if _borough_missing(bor):
            missing_borough += 1
        if _time_of_day_missing(str(it.get("time_of_day") or "")):
            missing_tod += 1

        scene = _flatten_tags(it.get("scene_type"))
        if _scene_weak(scene):
            weak_scene += 1
        for x in scene:
            scene_ctr[str(x)] += 1

        mood = _flatten_tags(it.get("mood"))
        if _mood_too_generic(mood):
            generic_mood += 1
        for x in mood:
            mood_ctr[str(x)] += 1

        usable = _parse_usable(it.get("usable_for"))
        if not usable:
            empty_usable += 1
        for x in usable:
            usable_ctr[str(x)] += 1

        if _suspicious_ext_scene(it):
            suspicious += 1

        try:
            qs = float(it.get("quality_score") or 0.0)
        except (TypeError, ValueError):
            qs = 0.0
        try:
            dur = float(it.get("duration_sec") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        res = str(it.get("resolution") or "").strip()
        is_vid = _is_video_item(it)
        bad_dur = is_vid and dur < 1.5
        bad_res = is_vid and not res
        if qs < quality_threshold or bad_dur or bad_res:
            low_quality += 1

        ids.append(str(it.get("id") or ""))
        p = str(it.get("path") or "")
        basenames.append(Path(p).name.lower())
        realpaths.append(_realpath_best_effort(p))

    def _dup_cluster_rows(values: list[str]) -> tuple[int, int]:
        """Return (rows_with_non_unique_value, distinct_values_with_count_gt_1)."""
        ct = Counter(values)
        bad_keys = {k for k, c in ct.items() if c > 1 and k}
        rows_affected = sum(1 for v in values if v in bad_keys)
        return rows_affected, len(bad_keys)

    dup_id_rows, dup_id_keys = _dup_cluster_rows(ids)
    dup_bn_rows, dup_bn_keys = _dup_cluster_rows(basenames)
    dup_rp_rows, dup_rp_keys = _dup_cluster_rows(realpaths)

    def top10(ct: Counter[str]) -> list[dict[str, Any]]:
        return [{"value": k, "count": int(v)} for k, v in ct.most_common(10)]

    return {
        "totals": {
            "entries": total,
            "video_inferred": video_n,
            "photo_inferred": photo_n,
        },
        "issues": {
            "missing_borough": missing_borough,
            "missing_time_of_day": missing_tod,
            "weak_scene_type": weak_scene,
            "mood_too_generic": generic_mood,
            "empty_usable_for": empty_usable,
            "suspicious_ext_vs_scene": suspicious,
            "low_quality": low_quality,
        },
        "duplicates": {
            "duplicate_id_rows_affected": dup_id_rows,
            "duplicate_id_distinct_keys": dup_id_keys,
            "duplicate_basename_rows_affected": dup_bn_rows,
            "duplicate_basename_distinct_keys": dup_bn_keys,
            "duplicate_realpath_rows_affected": dup_rp_rows,
            "duplicate_realpath_distinct_keys": dup_rp_keys,
        },
        "top_10": {
            "scene_type": top10(scene_ctr),
            "mood": top10(mood_ctr),
            "usable_for": top10(usable_ctr),
        },
        "parameters": {"quality_threshold": quality_threshold},
    }


def _write_md(summary: dict[str, Any], index_path: Path) -> str:
    lines = [
        "# Media index v3 quality audit",
        "",
        f"- **Index:** `{index_path}`",
        f"- **Entries:** {summary['totals']['entries']}",
        "",
        "## Totals",
        "",
        f"- Videos (inferred): **{summary['totals']['video_inferred']}**",
        f"- Photos (inferred): **{summary['totals']['photo_inferred']}**",
        "",
        "## Issue counts",
        "",
    ]
    for k, v in summary["issues"].items():
        lines.append(f"- `{k}`: **{v}**")
    lines.extend(
        [
            "",
            "## Duplicates (value-level)",
            "",
        ]
    )
    for k, v in summary["duplicates"].items():
        lines.append(f"- `{k}`: **{v}**")
    lines.extend(["", "## Top 10 scene_type", ""])
    for row in summary["top_10"]["scene_type"]:
        lines.append(f"- {row['value']}: {row['count']}")
    lines.extend(["", "## Top 10 mood", ""])
    for row in summary["top_10"]["mood"]:
        lines.append(f"- {row['value']}: {row['count']}")
    lines.extend(["", "## Top 10 usable_for", ""])
    for row in summary["top_10"]["usable_for"]:
        lines.append(f"- {row['value']}: {row['count']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", type=Path, default=default_index_path(), help="Path to media_index_v3.json")
    ap.add_argument(
        "--quality-threshold",
        type=float,
        default=0.4,
        help="Flag low_quality when quality_score < threshold OR duration_sec < 1.5 OR video with empty resolution",
    )
    args = ap.parse_args()
    index_path = args.index.expanduser()
    if not index_path.is_file():
        print(json.dumps({"ok": False, "error": "index_missing", "path": str(index_path)}, ensure_ascii=False))
        return 1
    try:
        data = json.loads(index_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": repr(exc), "path": str(index_path)}, ensure_ascii=False))
        return 1
    raw = data.get("items") if isinstance(data, dict) else []
    items = [x for x in raw if isinstance(x, dict)]
    summary = audit_items(items, quality_threshold=float(args.quality_threshold))
    out_dir = index_path.parent
    json_out = out_dir / "media_index_v3_quality_audit.json"
    md_out = out_dir / "media_index_v3_quality_audit.md"
    payload = {
        "ok": True,
        "index_path": str(index_path),
        "schema_version": data.get("schema_version") if isinstance(data, dict) else None,
        "generated_at": data.get("generated_at") if isinstance(data, dict) else None,
        **summary,
    }
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_out.write_text(_write_md(payload, index_path), encoding="utf-8")
    print(json.dumps({"ok": True, "wrote_json": str(json_out), "wrote_md": str(md_out), "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
