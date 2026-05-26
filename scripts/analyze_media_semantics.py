#!/usr/bin/env python3
"""Fail-open semantic enrichment for StateVerge Media Intelligence v3.

This script reads ``media_index_v3.json`` or explicit media paths and writes
``media_semantics_sidecars.json`` under ``SV_TRANSFER/media_index``. The indexer
merges that sidecar on the next run by exact media ``path``; semantic fields in
the sidecar override heuristic defaults while physical metadata is refreshed
from ffprobe/file stats. No raw footage is moved or modified.

OpenAI is used only when ``OPENAI_API_KEY`` is present and the ``openai`` package
imports successfully. API errors, ffmpeg thumbnail errors, and malformed rows
are recorded as warnings and never block sidecar output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from storage_paths import get_sv_transfer  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")


MEDIA_INDEX_V3 = "media_index_v3.json"
SEMANTIC_SIDECAR = "media_semantics_sidecars.json"


def media_index_dir() -> Path:
    return get_sv_transfer(verbose=False) / "media_index"


def media_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8", errors="replace")).hexdigest()[:20]


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def index_items(index_path: Path) -> list[dict[str, Any]]:
    data = load_json(index_path)
    items = data.get("items") if isinstance(data, dict) else []
    return [x for x in items if isinstance(x, dict)]


def path_item(path: Path) -> dict[str, Any]:
    return {
        "id": media_id(str(path)),
        "path": str(path),
        "filename": path.name,
        "embedding_text": path.stem.replace("_", " "),
        "search_keywords": keywords_from_text(path.stem),
    }


def keywords_from_text(text: str) -> list[str]:
    words = re.sub(r"[^A-Za-z0-9]+", " ", text).lower().split()
    out: list[str] = []
    for w in words:
        if len(w) >= 3 and w not in out:
            out.append(w)
    return out[:24]


def heuristic_semantics(item: dict[str, Any]) -> dict[str, Any]:
    path = str(item.get("path") or item.get("file_path") or "")
    blob = " ".join(
        str(x or "")
        for x in [
            path,
            item.get("filename") or item.get("file_name"),
            item.get("orientation"),
            item.get("borough"),
            item.get("neighborhood"),
            item.get("landmark") or item.get("nearby_landmark"),
            item.get("time_of_day"),
            item.get("likely_source_type"),
        ]
    ).lower()
    scene: list[str] = []
    mood: list[str] = []
    audio: list[str] = []
    if "timelapse" in blob or "skyline" in blob:
        scene.extend(["timelapse", "skyline"])
    if any(x in blob for x in ("drive", "driving", "dash", "road")):
        scene.extend(["city_street", "driving"])
    if any(x in blob for x in ("walk", "walking", "pov")):
        scene.extend(["city_street", "walking"])
    if any(x in blob for x in ("times square", "midtown", "chinatown", "financial", "central park", "dumbo")):
        scene.append("landmark")
    if not scene:
        scene.append("city_broll")
    if "night" in blob:
        mood.append("night")
    if "rain" in blob:
        mood.append("rainy")
    if str(item.get("orientation") or "").lower() == "landscape":
        mood.append("cinematic")
    mood.append("urban")
    tod = str(item.get("time_of_day") or "").lower()
    if not tod:
        if "sunset" in blob or "sunrise" in blob or "golden" in blob:
            tod = "evening"
        elif "night" in blob or "midnight" in blob:
            tod = "night"
        else:
            tod = "day"
    cam = "unknown"
    if any(x in blob for x in ("drive", "driving", "dash")):
        cam = "dash_fixed"
    elif any(x in blob for x in ("walk", "walking", "pov")):
        cam = "handheld_walk"
    elif "timelapse" in blob:
        cam = "tripod_timelapse"
    usable: list[str] = []
    if str(item.get("orientation") or "").lower() == "portrait" and "walk" in blob:
        usable.extend(["shorts", "reels"])
    if "ferry" in blob or "waterfront" in blob:
        usable.extend(["ferry_broll", "waterfront"])
    if str(item.get("orientation") or "").lower() == "landscape":
        usable.extend(["documentary_broll", "longform"])
    if not usable:
        usable = ["archive_broll"]
    if item.get("duration_sec") or item.get("duration"):
        audio.append("original_sound")
    text_bits = [
        item.get("embedding_text"),
        item.get("filename") or item.get("file_name"),
        item.get("borough"),
        item.get("neighborhood"),
        item.get("landmark") or item.get("nearby_landmark"),
        " ".join(scene),
        " ".join(mood),
    ]
    embedding_text = " ".join(str(x) for x in text_bits if x).strip()
    kws = list(item.get("search_keywords") or [])
    for k in keywords_from_text(embedding_text):
        if k not in kws:
            kws.append(k)
    return {
        "scene_type": list(dict.fromkeys(scene)),
        "audio_type": list(dict.fromkeys(audio)),
        "mood": list(dict.fromkeys(mood)),
        "time_of_day": tod,
        "camera_motion": cam,
        "usable_for": list(dict.fromkeys(usable)),
        "embedding_text": embedding_text,
        "ai_summary": str(item.get("ai_summary") or embedding_text[:220]).strip(),
        "search_keywords": kws[:40],
    }


def make_thumbnail(item: dict[str, Any], thumb_dir: Path) -> str:
    path = Path(str(item.get("path") or item.get("file_path") or ""))
    if not path.is_file() or shutil.which("ffmpeg") is None:
        return ""
    try:
        thumb_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""
    out = thumb_dir / f"{media_id(str(path))}.jpg"
    if out.is_file():
        return str(out)
    dur = 0.0
    try:
        dur = float(item.get("duration_sec") or item.get("duration") or 0.0)
    except Exception:
        dur = 0.0
    seek = max(0.0, min(dur * 0.2, 8.0)) if dur > 0 else 0.0
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(round(seek, 2)), "-i", str(path), "-frames:v", "1", "-y", str(out)]
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, timeout=20, check=False)
        if r.returncode == 0 and out.is_file():
            return str(out)
    except Exception:
        return ""
    return ""


def openai_semantics(item: dict[str, Any], current: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        return {}, None
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {}, f"openai_import_failed:{exc!r}"
    prompt = {
        "path": item.get("path") or item.get("file_path"),
        "filename": item.get("filename") or item.get("file_name"),
        "metadata": {
            "orientation": item.get("orientation"),
            "location": item.get("landmark") or item.get("nearby_landmark") or item.get("neighborhood") or item.get("borough"),
            "time_of_day": item.get("time_of_day"),
            "heuristics": current,
        },
    }
    try:
        client = OpenAI()
        resp = client.chat.completions.create(
            model=(os.environ.get("STATEVERGE_MEDIA_AI_MODEL") or "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": "Return compact JSON keys: scene_type, mood, ai_summary, search_keywords. No prose."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=220,
        )
        text = (resp.choices[0].message.content or "").strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}, "openai_invalid_json_shape"
        out: dict[str, Any] = {}
        for k in ("scene_type", "mood", "ai_summary", "search_keywords"):
            if k in data:
                out[k] = data[k]
        return out, None
    except Exception as exc:  # noqa: BLE001
        return {}, f"openai_failed:{exc!r}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", default=str(media_index_dir() / MEDIA_INDEX_V3), help="Index JSON to read.")
    ap.add_argument("--paths", nargs="*", default=None, help="Explicit media paths to analyze instead of index items.")
    ap.add_argument("--limit", type=int, default=0, help="Limit rows for incremental batches.")
    ap.add_argument("--thumbnails", action="store_true", help="Generate best-effort ffmpeg thumbnails under media_index/thumbnails.")
    ap.add_argument("--output", default=str(media_index_dir() / SEMANTIC_SIDECAR), help="Sidecar JSON output path.")
    args = ap.parse_args()

    out_path = Path(args.output).expanduser()
    items = [path_item(Path(p).expanduser()) for p in args.paths] if args.paths else index_items(Path(args.index).expanduser())
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    prior = load_json(out_path)
    prior_items = prior.get("items") if isinstance(prior, dict) else {}
    merged: dict[str, Any] = dict(prior_items) if isinstance(prior_items, dict) else {}
    warnings: list[str] = []
    thumb_dir = out_path.parent / "thumbnails"

    for item in items:
        p = str(item.get("path") or item.get("file_path") or "")
        if not p:
            warnings.append("row_missing_path")
            continue
        sem = heuristic_semantics(item)
        if args.thumbnails:
            thumb = make_thumbnail(item, thumb_dir)
            if thumb:
                sem["thumbnail_path"] = thumb
        ai, err = openai_semantics(item, sem)
        if err:
            warnings.append(f"{Path(p).name}:{err}")
        sem.update(ai)
        sem["path"] = p
        sem["id"] = str(item.get("id") or media_id(p))
        sem["updated_at"] = datetime.now().isoformat(timespec="seconds")
        merged[p] = sem

    payload = {
        "schema_version": "media_semantics_sidecars_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_index": str(Path(args.index).expanduser()),
        "merge_strategy": "index_media_library.py merges by exact path; sidecar semantic fields override heuristic v3 defaults",
        "count": len(merged),
        "warnings": warnings[-100:],
        "items": merged,
    }
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(out_path)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": repr(exc), "output": str(out_path)}))
        return 1

    print(json.dumps({"ok": True, "output": str(out_path), "analyzed": len(items), "warnings": warnings[-20:]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
