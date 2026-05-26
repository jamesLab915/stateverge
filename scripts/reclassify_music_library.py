#!/usr/bin/env python3
"""Re-scan and re-classify nyc_long music files + sync music_index.json (track_id stable)."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SV_SRC = Path.home() / "StateVerge" / "src"
for _p in (_SCRIPT_DIR, _SV_SRC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _xfer() -> Path:
    try:
        from utils.storage_paths import get_sv_transfer  # type: ignore

        return get_sv_transfer(verbose=False)
    except Exception:
        return Path("/Volumes/SV_TRANSFER")


NYC_CATS = ("ambient", "calm_piano", "cinematic", "dark_documentary", "night_drive")


def music_root(xfer: Path) -> Path:
    return xfer / "04_AUDIO" / "music" / "nyc_long"


def index_path(xfer: Path) -> Path:
    return music_root(xfer) / "metadata" / "music_index.json"


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suf = dest.stem, dest.suffix
    for i in range(2, 10_000):
        cand = dest.with_name(f"{stem}__{i}{suf}")
        if not cand.exists():
            return cand
    return dest


def count_main_tracks(xfer: Path) -> dict[str, int]:
    root = music_root(xfer)
    out: dict[str, int] = {}
    for c in NYC_CATS:
        d = root / c
        n = 0
        if d.is_dir():
            try:
                for p in d.glob("sv_nyc_long_*.mp3"):
                    if p.is_file() and "_stem_" not in p.name and not p.name.startswith("._"):
                        n += 1
            except OSError:
                pass
        out[c] = n
    return out


def scan_report(xfer: Path) -> dict[str, Any]:
    counts = count_main_tracks(xfer)
    empty = [c for c, n in counts.items() if n == 0]
    likely_cin: list[str] = []
    amb = music_root(xfer) / "ambient"
    if amb.is_dir():
        cine_hints = (
            "cinematic",
            "film",
            "movie",
            "trailer",
            "urban",
            "atmosphere",
            "documentary",
            "drama",
            "story",
            "score",
            "epic",
        )
        try:
            for p in amb.glob("sv_nyc_long_*.mp3"):
                if not p.is_file() or "_stem_" in p.name:
                    continue
                n = p.name.lower()
                if any(h in n for h in cine_hints):
                    likely_cin.append(str(p))
        except OSError:
            pass
    return {"track_counts": counts, "empty_categories": empty, "ambient_likely_cinematic_paths": likely_cin[:200]}


def _blob_for_track(t: dict[str, Any]) -> str:
    parts: list[str] = []
    for k in ("filename", "project_name", "original_zip", "original_source", "category"):
        v = t.get(k)
        if v:
            parts.append(str(v).lower())
    for sf in t.get("stem_files") or []:
        try:
            parts.append(Path(str(sf)).name.lower())
        except Exception:
            parts.append(str(sf).lower())
    return " ".join(parts)


def infer_category(blob: str) -> str:
    b = blob.lower()

    if any(x in b for x in ("rain", "fog", "tension", "investigative", "financial district", "crime", "noir")):
        return "dark_documentary"
    if "documentary" in b and any(x in b for x in ("investigative", "dark", "rain", "tension", "fog")):
        return "dark_documentary"

    if any(x in b for x in ("piano", "emotional", "minimal", "soft")):
        return "calm_piano"

    if any(
        x in b
        for x in (
            "midnight",
            "neon",
            "cyber",
            "night drive",
            "night_drive",
            "fdr",
            "tunnel",
            "bridge",
        )
    ):
        return "night_drive"
    if "night" in b and any(x in b for x in ("drive", "driving", "midtown", "manhattan")):
        return "night_drive"

    if any(
        x in b
        for x in (
            "urban cinematic",
            "atmosphere cinematic",
            "cinematic atmosphere",
            "cinematic",
            "film",
            "movie",
            "trailer",
            "drama",
            "story",
            "score",
            "epic",
            "high_view",
            "clouds",
        )
    ):
        return "cinematic"
    if "documentary" in b:
        return "cinematic"

    return "ambient"


def _parse_idx_from_main(fn: str) -> str | None:
    m = re.match(r"^sv_nyc_long_[a-z_]+_(\d+)\.mp3$", fn, re.I)
    return m.group(1) if m else None


def _rename_main_for_category(old_fn: str, old_cat: str, new_cat: str) -> str:
    idx = _parse_idx_from_main(old_fn)
    if idx:
        return f"sv_nyc_long_{new_cat}_{idx}.mp3"
    return old_fn.replace(f"_{old_cat}_", f"_{new_cat}_", 1)


def _rename_stem(stem_name: str, old_cat: str, new_cat: str) -> str:
    if f"nyc_long_{old_cat}_" in stem_name:
        return stem_name.replace(f"nyc_long_{old_cat}_", f"nyc_long_{new_cat}_", 1)
    return stem_name


def load_index(xfer: Path) -> dict[str, Any]:
    p = index_path(xfer)
    if not p.is_file():
        return {"tracks": [], "updated_at": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict) and isinstance(data.get("tracks"), list):
            return data
    except Exception:
        pass
    return {"tracks": [], "updated_at": None}


def save_index(xfer: Path, data: dict[str, Any]) -> None:
    p = index_path(xfer)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def write_cinematic_recommendations(xfer: Path) -> None:
    obj = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "keywords": [
            "cinematic atmosphere",
            "urban cinematic",
            "documentary cinematic",
            "film score ambient",
            "dark cinematic city",
            "futuristic cinematic",
        ],
        "note": "Envato / Elements search hints when cinematic_track_count < 5.",
    }
    p = music_root(xfer) / "metadata" / "cinematic_download_recommendations.json"
    t = p.with_suffix(".json.tmp")
    t.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    t.replace(p)


def run(*, xfer: Path, dry_run: bool) -> dict[str, Any]:
    root = music_root(xfer)
    scan_before = scan_report(xfer)
    data = load_index(xfer)
    tracks: list[dict[str, Any]] = [x for x in data.get("tracks") or [] if isinstance(x, dict)]
    reclassified = 0
    cinematic_added = 0

    for t in tracks:
        if str(t.get("music_channel") or "") != "nyc_long":
            continue
        if t.get("license_valid") is not True:
            continue
        old_cat = str(t.get("category") or "").strip()
        if old_cat not in NYC_CATS:
            continue
        fn = str(t.get("filename") or "").strip()
        if not fn or "_stem_" in fn:
            continue
        old_path = root / old_cat / fn
        if not old_path.is_file():
            continue
        blob = _blob_for_track(t)
        new_cat = infer_category(blob)
        if new_cat == old_cat:
            continue
        if new_cat == "cinematic" and old_cat != "cinematic":
            cinematic_added += 1

        new_fn = _rename_main_for_category(fn, old_cat, new_cat)
        dest_dir = root / new_cat
        dest_main = _unique_dest(dest_dir / new_fn)
        new_fn_final = dest_main.name

        if dry_run:
            reclassified += 1
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_path), str(dest_main))
        reclassified += 1

        new_stems: list[str] = []
        for sfp in list(t.get("stem_files") or []):
            sp = Path(str(sfp))
            if not sp.is_file():
                continue
            new_stem_name = _rename_stem(sp.name, old_cat, new_cat)
            dest_stem = _unique_dest(dest_dir / new_stem_name)
            shutil.move(str(sp), str(dest_stem))
            new_stems.append(str(dest_stem))
        t["stem_files"] = new_stems
        t["category"] = new_cat
        t["filename"] = new_fn_final

        lic_paths: list[str] = []
        for lp in list(t.get("license_files") or []):
            lpth = Path(str(lp))
            if lpth.is_file() and str(lpth.parent) == str(root / "licenses"):
                lic_paths.append(str(lpth))
            elif lpth.is_file():
                lic_paths.append(str(lpth))
        t["license_files"] = lic_paths
        lf = t.get("license_file")
        if lf and isinstance(lf, str) and Path(lf).is_file():
            t["license_file"] = lf

    if not dry_run:
        data["tracks"] = tracks
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        save_index(xfer, data)

    counts_after = count_main_tracks(xfer)
    scan_after = scan_report(xfer)
    cine_n = counts_after.get("cinematic", 0)
    need_more = cine_n < 5
    if need_more and not dry_run:
        write_cinematic_recommendations(xfer)

    if not dry_run:
        last_p = music_root(xfer) / "metadata" / "reclassify_music_library_last.json"
        try:
            t = last_p.with_suffix(".json.tmp")
            t.write_text(
                json.dumps(
                    {
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "reclassified_count": reclassified,
                        "cinematic_added_count": cinematic_added,
                        "NEED_MORE_CINEMATIC_TRACKS": need_more,
                        "track_counts_after": counts_after,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            t.replace(last_p)
        except OSError:
            pass

    return {
        "ok": True,
        "dry_run": dry_run,
        "scan_before": scan_before,
        "scan_after": scan_after,
        "track_counts_after": counts_after,
        "reclassified_count": reclassified,
        "cinematic_added_count": cinematic_added,
        "NEED_MORE_CINEMATIC_TRACKS": need_more,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Reclassify nyc_long music library + sync music_index.json")
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args()
    xfer = _xfer()
    rep = scan_report(xfer)
    print(json.dumps({"phase": "scan", **rep}, ensure_ascii=False, indent=2))
    out = run(xfer=xfer, dry_run=bool(ns.dry_run))
    print(json.dumps({"phase": "reclassify", **out}, ensure_ascii=False, indent=2))
    tc = out.get("track_counts_after") or {}
    if not isinstance(tc, dict):
        tc = {}
    missing = ",".join(c for c in NYC_CATS if int(tc.get(c) or 0) == 0)
    print("", flush=True)
    print("SEMANTIC_MUSIC_LIBRARY_RECLASSIFY_DONE", flush=True)
    print(f"RECLASSIFIED_COUNT={out.get('reclassified_count', 0)}", flush=True)
    print(f"CINEMATIC_TRACK_COUNT={int(tc.get('cinematic') or 0)}", flush=True)
    print(f"AMBIENT_TRACK_COUNT={int(tc.get('ambient') or 0)}", flush=True)
    print(f"NIGHT_DRIVE_TRACK_COUNT={int(tc.get('night_drive') or 0)}", flush=True)
    print(f"MISSING_CATEGORIES={missing}", flush=True)
    print(f"NEED_MORE_CINEMATIC_TRACKS={str(bool(out.get('NEED_MORE_CINEMATIC_TRACKS'))).lower()}", flush=True)
    print(f"CINEMATIC_ADDED_COUNT={out.get('cinematic_added_count', 0)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
