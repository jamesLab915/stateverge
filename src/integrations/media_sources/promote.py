"""
Promote stock-source clips from ``topics/<topic>/assets/raw/`` into a target
directory under ``topics/<topic>/``.

Historically this promoted into ``envato/`` so that
``src.mix_engine.generate_timeline`` can pick them up as ``envato``-type clips.
Some workflows instead promote directly into ``video/`` (treating downloaded
clips as extra local material).

Also writes a sidecar ``<dest_dir>/_attribution.json`` preserving the source /
url / download_url / license / author / query / segment that mix_engine itself
does not retain in its timeline schema.

This module does **not** import or modify mix_engine; it only produces files
that the existing mix_engine flow already understands.

CLI:

    python -m src.integrations.media_sources.promote --topic <slug>
        [--dry-run] [--symlink] [--force]
        [--dest envato|video]
        [--filter source=pexels,pixabay,dvids]
        [--segment segment_01,segment_02]
        [--max N]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import default_repo_root

LOG_PREFIX = "[promote]"


def _log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "segments" not in data:
        raise ValueError(
            f"invalid manifest at {path}: top-level object must contain 'segments'"
        )
    return data


def _parse_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [t.strip().lower() for t in s.split(",") if t.strip()]


def _filter_kv(s: str | None) -> dict[str, list[str]]:
    """Parse '--filter source=pexels,pixabay kind=video' into {key: [vals]}."""
    out: dict[str, list[str]] = {}
    if not s:
        return out
    for chunk in s.split():
        if "=" not in chunk:
            continue
        k, _, v = chunk.partition("=")
        k = k.strip().lower()
        vs = [t.strip().lower() for t in v.split(",") if t.strip()]
        if k and vs:
            out[k] = vs
    return out


def _entry_passes_filters(
    entry: dict[str, Any],
    filters: dict[str, list[str]],
) -> bool:
    for k, allowed in filters.items():
        v = str(entry.get(k, "")).lower()
        if v not in allowed:
            return False
    return True


def _safe_copy(src: Path, dst: Path, *, symlink: bool, force: bool) -> str:
    """
    Returns one of: "copied", "linked", "skipped_existing", "skipped_missing",
    "would_copy", "would_link", "would_skip_existing", "would_skip_missing".
    """
    dry = False  # caller controls dry-run by not invoking this; kept simple
    if not src.is_file():
        return "skipped_missing"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if not force:
            return "skipped_existing"
        try:
            if dst.is_symlink() or dst.is_file():
                dst.unlink()
        except OSError:
            pass
    if symlink:
        try:
            os.symlink(src, dst)
        except OSError as e:
            raise RuntimeError(f"symlink failed src={src!s} dst={dst!s}: {e!s}") from e
        return "linked"
    shutil.copy2(src, dst)
    return "copied"


def _basename_for(entry: dict[str, Any], default: str) -> str:
    p = (entry.get("file") or "").strip()
    if not p:
        return default
    return Path(p).name


def run(
    *,
    root: Path,
    topic: str,
    dest: str,
    dry_run: bool,
    symlink: bool,
    force: bool,
    filters: dict[str, list[str]],
    segments_filter: list[str],
    max_total: int | None,
) -> int:
    tdir = root / "topics" / topic
    manifest_path = tdir / "assets" / "media_manifest.json"
    dest = (dest or "envato").strip().lower()
    if dest not in ("envato", "video"):
        raise ValueError("--dest must be one of: envato,video")
    dest_dir = tdir / dest
    attribution_path = dest_dir / "_attribution.json"

    data = _read_manifest(manifest_path)
    segments = data.get("segments") or {}
    if not isinstance(segments, dict):
        raise ValueError("manifest 'segments' must be an object")

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {
        "copied": 0, "linked": 0,
        "skipped_existing": 0, "skipped_missing": 0,
        "skipped_no_file": 0, "skipped_filtered": 0, "skipped_max": 0,
    }
    promoted: dict[str, dict[str, Any]] = {}
    promoted_total = 0

    seg_ids = sorted(segments.keys())
    if segments_filter:
        seg_ids = [s for s in seg_ids if s in segments_filter]

    for sid in seg_ids:
        items = segments[sid] or []
        if not isinstance(items, list):
            continue
        for entry in items:
            if not isinstance(entry, dict):
                continue
            if filters and not _entry_passes_filters(entry, filters):
                counts["skipped_filtered"] += 1
                continue
            file_rel = (entry.get("file") or "").strip()
            if not file_rel:
                counts["skipped_no_file"] += 1
                continue
            if max_total is not None and promoted_total >= max_total:
                counts["skipped_max"] += 1
                continue

            src = (root / file_rel).resolve()
            dst_name = _basename_for(entry, src.name)
            dst = (dest_dir / dst_name).resolve()

            src_short = file_rel
            dst_short = f"topics/{topic}/{dest}/{dst_name}"

            if dry_run:
                if not src.is_file():
                    counts["skipped_missing"] += 1
                    _log(
                        f"DRY would_skip_missing seg={sid} src={src_short}"
                    )
                    continue
                if dst.exists() and not force:
                    counts["skipped_existing"] += 1
                    _log(
                        f"DRY would_skip_existing seg={sid} dst={dst_short}"
                    )
                    continue
                action = "would_link" if symlink else "would_copy"
                counts["linked" if symlink else "copied"] += 1
                _log(
                    f"DRY {action} seg={sid} source={entry.get('source', '?')} "
                    f"src={src_short} dst={dst_short}"
                )
                promoted_total += 1
                promoted[dst_name] = _attr_record(entry, sid, src_short, dst_short)
                continue

            try:
                result = _safe_copy(src, dst, symlink=symlink, force=force)
            except Exception as e:  # noqa: BLE001
                _log(f"error seg={sid} src={src_short}: {e!s}")
                continue

            counts[result] = counts.get(result, 0) + 1
            if result in ("skipped_missing", "skipped_existing"):
                _log(f"{result} seg={sid} src={src_short} dst={dst_short}")
                if result == "skipped_existing":
                    promoted[dst_name] = _attr_record(entry, sid, src_short, dst_short)
                continue

            promoted_total += 1
            _log(
                f"{result} seg={sid} source={entry.get('source', '?')} "
                f"src={src_short} dst={dst_short}"
            )
            promoted[dst_name] = _attr_record(entry, sid, src_short, dst_short)

    summary = (
        f"summary topic={topic} dry_run={dry_run} symlink={symlink} force={force} "
        + " ".join(f"{k}={v}" for k, v in counts.items() if v)
    )
    _log(summary)

    if not dry_run and promoted:
        _write_attribution(
            attribution_path, topic, manifest_path, promoted, root
        )
        try:
            rel_attr = attribution_path.relative_to(root)
        except ValueError:
            rel_attr = attribution_path
        _log(f"wrote {rel_attr}")

    return 0


def _attr_record(
    entry: dict[str, Any], seg_id: str, src_short: str, dst_short: str
) -> dict[str, Any]:
    return {
        "segment": seg_id,
        "source": entry.get("source", ""),
        "kind": entry.get("kind", ""),
        "query": entry.get("query", ""),
        "url": entry.get("url", ""),
        "download_url": entry.get("download_url", ""),
        "width": int(entry.get("width", 0) or 0),
        "height": int(entry.get("height", 0) or 0),
        "duration": int(entry.get("duration", 0) or 0),
        "license": entry.get("license", ""),
        "author": entry.get("author", ""),
        "raw_source_path": src_short,
        "promoted_to": dst_short,
    }


def _write_attribution(
    path: Path,
    topic: str,
    manifest_path: Path,
    promoted: dict[str, dict[str, Any]],
    root: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        manifest_rel = str(manifest_path.relative_to(root))
    except ValueError:
        manifest_rel = str(manifest_path)
    body = {
        "topic": topic,
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "source_manifest": manifest_rel,
        "note": (
            "This sidecar preserves source/url/license info that mix_engine's "
            "timeline schema does not retain. Keys are the basenames of files "
            "now sitting under the promotion destination directory. mix_engine ignores this "
            "file."
        ),
        "files": promoted,
    }
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Promote stock clips from topics/<topic>/assets/raw/ into "
            "topics/<topic>/<dest>/ (default: envato/)."
        )
    )
    ap.add_argument("--topic", required=True, help="Topic slug under topics/")
    ap.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root (defaults to ~/StateVerge resolved from this file)",
    )
    ap.add_argument(
        "--dest",
        type=str,
        default="envato",
        help="Promotion destination under topics/<topic>/: envato (default) or video",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen, write nothing",
    )
    ap.add_argument(
        "--symlink",
        action="store_true",
        help="Symlink instead of copy (saves disk; raw/ must persist)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files in destination dir",
    )
    ap.add_argument(
        "--filter",
        type=str,
        default="",
        help="Space-separated key=val[,val...] filters, e.g. "
        "'source=pexels,pixabay kind=video'",
    )
    ap.add_argument(
        "--segment",
        type=str,
        default="",
        help="Comma-separated segment ids to include (default: all)",
    )
    ap.add_argument(
        "--max",
        type=int,
        default=None,
        help="Cap on total promotions (after filters)",
    )
    ns = ap.parse_args(argv)

    root = (ns.root or default_repo_root()).resolve()
    try:
        return run(
            root=root,
            topic=ns.topic,
            dest=str(ns.dest),
            dry_run=bool(ns.dry_run),
            symlink=bool(ns.symlink),
            force=bool(ns.force),
            filters=_filter_kv(ns.filter),
            segments_filter=_parse_csv(ns.segment),
            max_total=ns.max,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"{LOG_PREFIX} error: {e}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
