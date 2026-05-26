#!/usr/bin/env python3
"""Location Recovery v1 — enrich ``media_index_v3`` borough/landmark fields from path/GPS rules.

Read-only on media files; writes JSON reports and ``media_index_v3_enriched.json`` next to the index.
``--write-back`` optionally overwrites ``media_index_v3.json`` (off by default).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    from storage_paths import get_sv_transfer  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")


def default_index_path() -> Path:
    return get_sv_transfer(verbose=False) / "media_index" / "media_index_v3.json"


def default_rules_path() -> Path:
    return Path.home() / "StateVerge" / "config" / "location_recovery_rules.json"


def _norm_str(s: Any) -> str:
    if s is None:
        return ""
    return str(s).strip()


def _is_empty_field(val: Any) -> bool:
    return not _norm_str(val)


def _as_contains_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val.strip() else []
    if isinstance(val, list):
        return [str(x) for x in val if _norm_str(x)]
    return []


def _path_parents_folder_names(path_str: str) -> list[str]:
    p = Path(path_str)
    parts = p.parts
    if len(parts) <= 1:
        return []
    return [x for x in parts[:-1] if x not in (os.sep, "/", ".")]


def _path_starts_with_hint(path_str: str, hint: str) -> bool:
    h = hint.strip().replace("\\", "/").rstrip("/")
    if not h:
        return False
    cur = str(path_str).replace("\\", "/")
    return cur == h or cur.startswith(h + "/")


def _parse_gps(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _gps_in_bbox(lat: float, lon: float, box: dict[str, Any]) -> bool:
    try:
        lat_min = float(box["lat_min"])
        lat_max = float(box["lat_max"])
        lon_min = float(box["lon_min"])
        lon_max = float(box["lon_max"])
    except (KeyError, TypeError, ValueError):
        return False
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _rule_has_condition(rule: dict[str, Any]) -> bool:
    if _norm_str(rule.get("default_source_root_hint")):
        return True
    if rule.get("gps_bbox") and isinstance(rule["gps_bbox"], dict):
        return True
    if _as_contains_list(rule.get("path_contains")):
        return True
    if _as_contains_list(rule.get("filename_contains")):
        return True
    if _as_contains_list(rule.get("parent_folder_contains")):
        return True
    return False


def _match_contains_any(haystack_lower: str, needles: list[str]) -> bool:
    if not needles:
        return True
    return any(n.lower() in haystack_lower for n in needles)


def _match_parent_folders(parent_names_lower: list[str], needles: list[str]) -> bool:
    if not needles:
        return True
    for folder in parent_names_lower:
        for n in needles:
            if n.lower() in folder:
                return True
    return False


def rule_matches(rule: dict[str, Any], *, path_str: str, path_lower: str, filename_lower: str, parent_lower: list[str], lat: float | None, lon: float | None) -> bool:
    if not _rule_has_condition(rule):
        return False

    hint = _norm_str(rule.get("default_source_root_hint"))
    if hint:
        if not _path_starts_with_hint(path_str, hint):
            return False
        other = bool(_as_contains_list(rule.get("path_contains"))) or bool(_as_contains_list(rule.get("filename_contains"))) or bool(_as_contains_list(rule.get("parent_folder_contains")))
        box = rule.get("gps_bbox")
        has_gps = isinstance(box, dict) and any(k in box for k in ("lat_min", "lat_max", "lon_min", "lon_max"))
        if not other and not has_gps:
            return True

    pc = _as_contains_list(rule.get("path_contains"))
    if pc and not _match_contains_any(path_lower, pc):
        return False

    fc = _as_contains_list(rule.get("filename_contains"))
    if fc and not _match_contains_any(filename_lower, fc):
        return False

    pfc = _as_contains_list(rule.get("parent_folder_contains"))
    if pfc and not _match_parent_folders(parent_lower, pfc):
        return False

    box = rule.get("gps_bbox")
    if isinstance(box, dict) and any(k in box for k in ("lat_min", "lat_max", "lon_min", "lon_max")):
        if lat is None or lon is None:
            return False
        if not _gps_in_bbox(lat, lon, box):
            return False

    return True


def load_rules_json(path: Path) -> tuple[list[dict[str, Any]], int | str, str]:
    if not path.is_file():
        print(f"[location_recovery] rules file missing, continuing with no rules: {path}", file=sys.stderr)
        return [], 0, "missing"
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[location_recovery] rules JSON unreadable, continuing with no rules: {exc}", file=sys.stderr)
        return [], 0, "invalid"
    if not isinstance(data, dict):
        return [], 0, "invalid"
    rules = data.get("rules")
    if not isinstance(rules, list):
        return [], data.get("version", 0), "invalid"
    out: list[dict[str, Any]] = []
    for i, r in enumerate(rules):
        if isinstance(r, dict):
            rid = _norm_str(r.get("rule_id")) or f"rule_{i}"
            rr = dict(r)
            rr["rule_id"] = rid
            out.append(rr)
    ver = data.get("version", 0)
    schema = str(data.get("schema") or "")
    return out, ver, schema


def load_index(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def apply_rule_to_item(
    item: dict[str, Any],
    rule: dict[str, Any],
    rule_index: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return (merged_item_slice, location_recovery or None if nothing applied)."""
    out = copy.deepcopy(item)
    if "location_recovery" in out:
        del out["location_recovery"]

    updates: dict[str, str] = {}
    fields = ("borough", "neighborhood", "landmark", "location_name")
    for f in fields:
        val = rule.get(f)
        if val is None or not _norm_str(val):
            continue
        if _is_empty_field(out.get(f)):
            out[f] = _norm_str(val)
            updates[f] = _norm_str(val)

    if not updates:
        return out, None

    conf_raw = rule.get("confidence", 0.5)
    try:
        confidence = float(conf_raw)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    src = _norm_str(rule.get("source")) or "path_rule"
    loc_rec: dict[str, Any] = {
        "borough": out.get("borough") or "",
        "source": src,
        "confidence": confidence,
        "rule_id": str(rule.get("rule_id") or f"rule_{rule_index}"),
        "rule_index": rule_index,
    }
    desc = _norm_str(rule.get("description"))
    if desc:
        loc_rec["rule_description"] = desc
    out["location_recovery"] = loc_rec
    return out, loc_rec


def count_missing_borough(items: list[dict[str, Any]]) -> int:
    return sum(1 for it in items if _is_empty_field(it.get("borough")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", default=str(default_index_path()), help="Input media_index_v3.json path")
    ap.add_argument("--rules", default=str(default_rules_path()), help="location_recovery_rules.json")
    ap.add_argument("--write-back", action="store_true", help="Overwrite media_index_v3.json with enriched items")
    args = ap.parse_args()

    index_path = Path(args.index).expanduser().resolve()
    rules_path = Path(args.rules).expanduser().resolve()

    data = load_index(index_path)
    if data is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "index_missing_or_unreadable",
                    "index": str(index_path),
                    "hint": "Expected media_index_v3.json under get_sv_transfer()/media_index/ (check SV_TRANSFER / storage_map).",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    items_raw = data.get("items")
    items_in = [copy.deepcopy(x) for x in items_raw if isinstance(x, dict)] if isinstance(items_raw, list) else []
    before_missing = count_missing_borough(items_in)

    rules, rules_version, rules_schema = load_rules_json(rules_path)

    enriched: list[dict[str, Any]] = []
    recovery_by_source: dict[str, int] = {}
    recovered_any_field = 0
    low_confidence_count = 0
    gained_borough: list[dict[str, Any]] = []

    for item in items_in:
        path_str = str(item.get("path") or "")
        path_lower = path_str.lower()
        filename_lower = Path(path_str).name.lower()
        parent_lower = [p.lower() for p in _path_parents_folder_names(path_str)]
        lat = _parse_gps(item.get("gps_lat"))
        lon = _parse_gps(item.get("gps_lon"))

        before_borough_empty = _is_empty_field(item.get("borough"))
        snapshot_before = {k: _norm_str(item.get(k)) for k in ("borough", "neighborhood", "landmark", "location_name")}

        merged = copy.deepcopy(item)
        if "location_recovery" in merged:
            del merged["location_recovery"]

        applied = False
        for ri, rule in enumerate(rules):
            if not rule_matches(
                rule,
                path_str=path_str,
                path_lower=path_lower,
                filename_lower=filename_lower,
                parent_lower=parent_lower,
                lat=lat,
                lon=lon,
            ):
                continue
            merged, loc_rec = apply_rule_to_item(merged, rule, ri)
            if loc_rec is not None:
                applied = True
                src = str(loc_rec.get("source") or "path_rule")
                recovery_by_source[src] = recovery_by_source.get(src, 0) + 1
                try:
                    c = float(loc_rec.get("confidence") or 0.0)
                except (TypeError, ValueError):
                    c = 0.0
                if c < 0.35:
                    low_confidence_count += 1
                if before_borough_empty and not _is_empty_field(merged.get("borough")):
                    gained_borough.append(
                        {
                            "path": path_str,
                            "borough": _norm_str(merged.get("borough")),
                            "source": src,
                            "confidence": c,
                            "rule_id": loc_rec.get("rule_id"),
                        }
                    )
            break

        if applied:
            after_snap = {k: _norm_str(merged.get(k)) for k in ("borough", "neighborhood", "landmark", "location_name")}
            if after_snap != snapshot_before:
                recovered_any_field += 1

        enriched.append(merged)

    after_missing = count_missing_borough(enriched)

    out_dir = index_path.parent
    report_json_path = out_dir / "media_index_v3_location_recovery_report.json"
    report_md_path = out_dir / "media_index_v3_location_recovery_report.md"
    enriched_path = out_dir / "media_index_v3_enriched.json"

    gained_borough.sort(key=lambda d: str(d.get("path") or ""))
    unresolved = sorted([{"path": str(it.get("path") or "")} for it in enriched if _is_empty_field(it.get("borough")) and str(it.get("path") or "")], key=lambda d: d["path"])

    report_obj: dict[str, Any] = {
        "ok": True,
        "index_input": str(index_path),
        "rules_path": str(rules_path),
        "rules_version": rules_version,
        "rules_schema": rules_schema,
        "before_missing_borough": before_missing,
        "after_missing_borough": after_missing,
        "recovered_count": recovered_any_field,
        "recovery_by_source": dict(sorted(recovery_by_source.items(), key=lambda kv: (-kv[1], kv[0]))),
        "low_confidence_count": low_confidence_count,
        "unresolved_examples": unresolved[:20],
        "recovered_examples": gained_borough[:20],
        "items_total": len(enriched),
        "write_back": bool(args.write_back),
    }

    report_json_path.write_text(json.dumps(report_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        "# Media index location recovery (v1)",
        "",
        f"- **Index:** `{index_path}`",
        f"- **Rules:** `{rules_path}` (version {rules_version})",
        f"- **Items:** {len(enriched)}",
        "",
        "## Summary",
        "",
        f"- **before_missing_borough:** {before_missing}",
        f"- **after_missing_borough:** {after_missing}",
        f"- **recovered_count** (any location field filled from empty): {recovered_any_field}",
        f"- **low_confidence_count** (confidence < 0.35): {low_confidence_count}",
        "",
        "## recovery_by_source",
        "",
    ]
    for k, v in report_obj["recovery_by_source"].items():
        md_lines.append(f"- `{k}`: {v}")
    md_lines.extend(
        [
            "",
            "## recovered_examples (up to 20)",
            "",
        ]
    )
    for ex in report_obj["recovered_examples"]:
        md_lines.append(
            f"- `{ex.get('path')}` → **{ex.get('borough')}** "
            f"(source={ex.get('source')}, confidence={ex.get('confidence')}, rule_id={ex.get('rule_id')})"
        )
    md_lines.extend(["", "## unresolved_examples (borough still empty, up to 20)", ""])
    for ex in report_obj["unresolved_examples"]:
        md_lines.append(f"- `{ex.get('path')}`")

    md_lines.append("")
    report_md_path.write_text("\n".join(md_lines), encoding="utf-8")

    out_root = copy.deepcopy(data)
    out_root["items"] = enriched

    enriched_path.write_text(json.dumps(out_root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.write_back:
        index_path.write_text(json.dumps(out_root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report_obj, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
