#!/usr/bin/env python3
"""Query Media Intelligence v3 locally; prints JSON (scores + clip fields + highlight times)."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import struct
import sys
from pathlib import Path
from typing import Any

# Align with index_media_library.VIDEO_EXTS / PHOTO_EXTS for media-type inference.
VIDEO_EXTS = frozenset({".mp4", ".mov", ".m4v", ".hevc"})
PHOTO_EXTS = frozenset({".jpg", ".jpeg", ".png", ".heic", ".dng"})

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_media_embeddings import DEFAULT_DIM, embedding_text, load_items, token_hash_vector

try:
    from storage_paths import get_sv_transfer  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")


def media_index_dir() -> Path:
    return get_sv_transfer(verbose=False) / "media_index"


def default_media_index_path() -> Path:
    d = media_index_dir()
    enriched = d / "media_index_v3_enriched.json"
    if enriched.is_file():
        return enriched
    return d / "media_index_v3.json"


def unpack_vector(blob: bytes, dim: int) -> list[float]:
    try:
        return list(struct.unpack(f"{int(dim)}f", blob))
    except Exception:
        return []


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm_terms(q: str) -> list[str]:
    return [x for x in "".join(ch.lower() if ch.isalnum() else " " for ch in q).split() if len(x) >= 2]


def _haystack(item: dict[str, Any]) -> str:
    parts: list[str] = [embedding_text(item)]
    for k in ("borough", "landmark", "neighborhood", "time_of_day", "camera_motion", "ai_summary"):
        v = item.get(k)
        if isinstance(v, str) and v:
            parts.append(v)
    for k in ("scene_type", "mood", "usable_for", "search_keywords"):
        v = item.get(k)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
    return " ".join(parts).lower()


def composite_keyword_score(item: dict[str, Any], terms: list[str]) -> float:
    if not terms:
        return 0.0
    hay = _haystack(item)
    if not hay.strip():
        return 0.0
    score = 0.0
    for t in terms:
        if not t:
            continue
        if t in hay:
            w = 1.0
            if t in str(item.get("borough") or "").lower():
                w += 1.4
            if any(t in str(x).lower() for x in (item.get("scene_type") or []) if x):
                w += 1.1
            if any(t in str(x).lower() for x in (item.get("mood") or []) if x):
                w += 0.9
            if t in str(item.get("landmark") or "").lower() or t in str(item.get("neighborhood") or "").lower():
                w += 1.0
            score += w
    return score / math.sqrt(float(len(terms)))


def _best_highlights(item: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    segs = item.get("highlight_segments")
    if not isinstance(segs, list):
        return []
    scored: list[dict[str, Any]] = []
    for s in segs:
        if not isinstance(s, dict):
            continue
        try:
            sc = float(s.get("score") or 0.0)
        except (TypeError, ValueError):
            sc = 0.0
        scored.append(s)
    scored.sort(key=lambda d: float(d.get("score") or 0.0), reverse=True)
    out: list[dict[str, Any]] = []
    for s in scored[:limit]:
        out.append(
            {
                "start_sec": s.get("start_sec"),
                "end_sec": s.get("end_sec"),
                "score": s.get("score"),
                "reason": s.get("reason"),
            }
        )
    return out


def sqlite_query(db_path: Path, query: str) -> list[dict[str, Any]] | None:
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute("SELECT path, id, embedding_text, keywords_json, dim, vector, updated_at FROM embeddings")
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return None
    if not rows:
        return None
    dim = int(rows[0][4] or DEFAULT_DIM)
    qv = token_hash_vector(query, dim=dim)
    terms = _norm_terms(query)
    scored: list[dict[str, Any]] = []
    for path, mid, text, keywords_json, row_dim, blob, updated_at in rows:
        vec = unpack_vector(blob, int(row_dim or dim))
        if not vec:
            continue
        base = dot(qv, vec)
        kw = composite_keyword_score({"path": path, "embedding_text": text, "search_keywords": json.loads(keywords_json or "[]")}, terms)
        score = float(base) + 0.15 * kw
        scored.append(
            {
                "path": path,
                "id": mid,
                "score": round(score, 6),
                "borough": "",
                "mood": [],
                "scene_type": [],
                "usable_for": [],
                "duration_sec": None,
                "highlight_timestamps": [],
                "embedding_text": text,
                "search_keywords": json.loads(keywords_json or "[]"),
                "updated_at": updated_at,
                "source": "sqlite_vector",
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def index_keyword_query(index_path: Path, query: str) -> list[dict[str, Any]]:
    items = load_items(index_path)
    terms = _norm_terms(query)
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in items:
        s = composite_keyword_score(item, terms)
        if s <= 0:
            continue
        highs = _best_highlights(item)
        scored.append(
            (
                s,
                {
                    "path": item.get("path"),
                    "id": item.get("id"),
                    "score": round(float(s), 6),
                    "borough": item.get("borough") or "",
                    "mood": item.get("mood") if isinstance(item.get("mood"), list) else [],
                    "scene_type": item.get("scene_type") if isinstance(item.get("scene_type"), list) else [],
                    "usable_for": item.get("usable_for") if isinstance(item.get("usable_for"), list) else [],
                    "duration_sec": item.get("duration_sec"),
                    "time_of_day": item.get("time_of_day"),
                    "highlight_timestamps": highs,
                    "ai_summary": item.get("ai_summary"),
                    "search_keywords": item.get("search_keywords") or [],
                    "source": "keyword_index",
                },
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored]


ROUTE_FIELD_KEYS: tuple[str, ...] = (
    "inferred_route_id",
    "inferred_route_type",
    "inferred_location_confidence",
    "inferred_from_neighbor",
    "inferred_neighbor_distance_sec",
    "inferred_neighbor_ids",
    "inferred_borough",
    "inferred_landmark",
    "inferred_skyline_continuity",
)


def load_routes_by_path(routes_path: Path) -> dict[str, dict[str, Any]]:
    if not routes_path.is_file():
        return {}
    try:
        data = json.loads(routes_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        p = str(it.get("path") or "")
        if not p:
            continue
        slice_d = {k: it[k] for k in ROUTE_FIELD_KEYS if k in it}
        if slice_d:
            out[p] = slice_d
    return out


def merge_route_fields(rows: list[dict[str, Any]], routes_by_path: dict[str, dict[str, Any]]) -> None:
    if not routes_by_path:
        return
    for r in rows:
        p = str(r.get("path") or "")
        extra = routes_by_path.get(p)
        if not extra:
            continue
        for k, v in extra.items():
            r[k] = v


def infer_media_kind(item: dict[str, Any]) -> str:
    """video | photo — extension + duration, consistent with indexer defaults."""
    path = str(item.get("path") or "")
    suf = Path(path).suffix.lower()
    try:
        dur = float(item.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    is_video_ext = suf in VIDEO_EXTS
    is_photo_ext = suf in PHOTO_EXTS
    if dur > 0 or is_video_ext:
        return "video"
    if is_photo_ext:
        return "photo"
    # Indexer: mtype is "video" if is_video else "photo" (non-video extensions default to photo).
    return "photo"


_BOROUGH_CANON: dict[str, str] = {
    "manhattan": "manhattan",
    "mn": "manhattan",
    "mh": "manhattan",
    "brooklyn": "brooklyn",
    "bk": "brooklyn",
    "queens": "queens",
    "qn": "queens",
    "qns": "queens",
    "bronx": "bronx",
    "bx": "bronx",
    "staten island": "staten island",
    "staten is": "staten island",
    "staten isl": "staten island",
    "si": "staten island",
    "richmond county": "staten island",
}


def _normalize_borough_label(s: str) -> str:
    k = " ".join((s or "").strip().casefold().split())
    if not k:
        return ""
    return _BOROUGH_CANON.get(k, k)


def _borough_matches_item(want: str, item: dict[str, Any]) -> bool:
    want_n = _normalize_borough_label(want)
    if not want_n:
        return True
    raw_vals = [str(item.get("borough") or ""), str(item.get("inferred_borough") or "")]
    for rv in raw_vals:
        item_n = _normalize_borough_label(rv)
        if not item_n:
            continue
        if want_n == item_n or want_n in item_n or item_n in want_n:
            return True
    return False


def _list_tag_matches(item: dict[str, Any], key: str, needle: str) -> bool:
    needle = needle.strip().casefold()
    if not needle:
        return True
    vals = item.get(key)
    if not isinstance(vals, list):
        return False
    for x in vals:
        xs = str(x).casefold()
        if needle == xs or needle in xs or xs in needle:
            return True
    return False


def passes_filters(item: dict[str, Any], args: argparse.Namespace) -> bool:
    """Post-merge filters; defaults (--media-type any, no other flags) let all rows through."""
    if getattr(args, "min_score", None) is not None:
        try:
            if float(item.get("score") or 0.0) < float(args.min_score):
                return False
        except (TypeError, ValueError):
            return False

    mt = getattr(args, "media_type", None) or "any"
    if mt and mt != "any":
        kind = infer_media_kind(item)
        if mt == "video" and kind != "video":
            return False
        if mt == "photo" and kind != "photo":
            return False

    b = str(getattr(args, "borough", None) or "").strip()
    if b and not _borough_matches_item(b, item):
        return False

    rt = str(getattr(args, "route_type", None) or "").strip()
    if rt and str(item.get("inferred_route_type") or "") != rt:
        return False

    if not _list_tag_matches(item, "scene_type", str(getattr(args, "scene_type", None) or "")):
        return False
    if not _list_tag_matches(item, "mood", str(getattr(args, "mood", None) or "")):
        return False
    if not _list_tag_matches(item, "usable_for", str(getattr(args, "usable_for", None) or "")):
        return False

    return True


def enrich_from_index(index_path: Path, rows: list[dict[str, Any]]) -> None:
    """Attach borough/mood/highlights from v3 index for sqlite-only hits."""
    by_path: dict[str, dict[str, Any]] = {}
    for it in load_items(index_path):
        p = str(it.get("path") or "")
        if p:
            by_path[p] = it
    for r in rows:
        p = str(r.get("path") or "")
        it = by_path.get(p)
        if not it:
            continue
        r["borough"] = it.get("borough") or r.get("borough") or ""
        r["mood"] = it.get("mood") if isinstance(it.get("mood"), list) else r.get("mood")
        r["scene_type"] = it.get("scene_type") if isinstance(it.get("scene_type"), list) else r.get("scene_type")
        r["usable_for"] = it.get("usable_for") if isinstance(it.get("usable_for"), list) else r.get("usable_for")
        r["time_of_day"] = it.get("time_of_day", r.get("time_of_day"))
        r["duration_sec"] = it.get("duration_sec", r.get("duration_sec"))
        r["highlight_timestamps"] = _best_highlights(it)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Pipeline: score all candidates, merge route fields when enabled, apply filters, "
            "sort by score, then truncate to --top-k / --limit.\n\n"
            "Route annotations: by default, if media_index_v3_routes.json exists next to --index, "
            "merge inferred_route_* fields into each hit. Override with --routes, disable with --no-routes, "
            "or pass --use-routes to expect that sibling file (warns if missing)."
        ),
    )
    ap.add_argument("--query", required=True)
    ap.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Max results after scoring → filters → truncation (see --limit).",
    )
    ap.add_argument("--limit", type=int, default=0, help="Alias for --top-k when set >0")
    ap.add_argument(
        "--media-type",
        choices=("video", "photo", "any"),
        default="any",
        help="Filter by inferred media kind (extension + duration_sec; default any).",
    )
    ap.add_argument(
        "--borough",
        default="",
        help="Case-insensitive match on borough or inferred_borough (aliases e.g. SI → Staten Island).",
    )
    ap.add_argument(
        "--route-type",
        default="",
        help="Exact match on inferred_route_type after routes merge.",
    )
    ap.add_argument(
        "--scene-type",
        default="",
        help="Case-insensitive substring or exact match on any scene_type tag.",
    )
    ap.add_argument(
        "--mood",
        default="",
        help="Case-insensitive substring or exact match on any mood tag.",
    )
    ap.add_argument(
        "--usable-for",
        default="",
        dest="usable_for",
        help="Case-insensitive substring or exact match on any usable_for tag.",
    )
    ap.add_argument(
        "--min-score",
        type=float,
        default=None,
        metavar="FLOAT",
        help="Drop rows with score below this value after scoring (default: no cutoff).",
    )
    ap.add_argument("--db", default=str(media_index_dir() / "media_embeddings.sqlite"))
    ap.add_argument("--index", default=str(default_media_index_path()))
    ap.add_argument(
        "--routes",
        default="",
        help="Path to media_index_v3_routes.json (default: sibling of --index when that file exists).",
    )
    ap.add_argument(
        "--use-routes",
        action="store_true",
        help="Load route layer from <index_dir>/media_index_v3_routes.json (warn if absent).",
    )
    ap.add_argument("--no-routes", action="store_true", help="Do not merge route annotations.")
    args = ap.parse_args()

    top_k = max(1, int(args.limit or args.top_k))
    db_path = Path(args.db).expanduser()
    index_path = Path(args.index).expanduser()
    results = sqlite_query(db_path, args.query)
    mode = "sqlite_vector"
    if results is None:
        results = index_keyword_query(index_path, args.query)
        mode = "keyword_index"
    else:
        enrich_from_index(index_path, results)

    routes_path: Path | None = None
    routes_loaded = False
    sibling = index_path.parent / "media_index_v3_routes.json"
    if not args.no_routes:
        ra = str(args.routes or "").strip()
        if ra:
            routes_path = Path(ra).expanduser()
        else:
            routes_path = sibling
        if routes_path.is_file():
            merge_route_fields(results, load_routes_by_path(routes_path))
            routes_loaded = True
        elif ra:
            print(f"warning: --routes file not found: {routes_path}", file=sys.stderr)
        elif args.use_routes:
            print(f"warning: --use-routes but routes file missing: {routes_path}", file=sys.stderr)

    results = [r for r in results if passes_filters(r, args)]
    results.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    results = results[:top_k]

    print(
        json.dumps(
            {
                "ok": True,
                "query": args.query,
                "top_k": top_k,
                "mode": mode,
                "db": str(db_path),
                "index": str(index_path),
                "routes": str(routes_path) if routes_path else None,
                "routes_merged": routes_loaded,
                "results": results,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
