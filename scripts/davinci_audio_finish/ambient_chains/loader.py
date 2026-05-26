#!/usr/bin/env python3
"""Load and validate ambient chain JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CHAINS_JSON = Path(__file__).resolve().parent / "chains.json"

_REQUIRED_CHAIN_KEYS = ("label", "fairlight", "ffmpeg_fallback")
_REQUIRED_TOP_KEYS = ("version", "chains", "youtube_preset_to_chain", "content_type_default_chain")


def load_chains_document(path: Path | None = None) -> dict[str, Any]:
    p = path or _CHAINS_JSON
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("chains_document_must_be_object")
    return data


def validate_chains_document(doc: dict[str, Any] | None = None) -> list[str]:
    """Return list of validation errors (empty = OK)."""
    errors: list[str] = []
    if doc is None:
        try:
            doc = load_chains_document()
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return [f"load_failed:{exc!r}"]

    for key in _REQUIRED_TOP_KEYS:
        if key not in doc:
            errors.append(f"missing_top_key:{key}")

    chains = doc.get("chains")
    if not isinstance(chains, dict) or not chains:
        errors.append("chains_must_be_non_empty_object")
        return errors

    for chain_id, spec in chains.items():
        if not isinstance(spec, dict):
            errors.append(f"chain_not_object:{chain_id}")
            continue
        for rk in _REQUIRED_CHAIN_KEYS:
            if rk not in spec:
                errors.append(f"chain_{chain_id}_missing:{rk}")

    yt_map = doc.get("youtube_preset_to_chain")
    if not isinstance(yt_map, dict):
        errors.append("youtube_preset_to_chain_must_be_object")
    elif yt_map:
        for preset, cid in yt_map.items():
            if cid not in chains:
                errors.append(f"youtube_map_unknown_chain:{preset}->{cid}")

    ct_map = doc.get("content_type_default_chain")
    if not isinstance(ct_map, dict):
        errors.append("content_type_default_chain_must_be_object")
    elif ct_map:
        for ctype, cid in ct_map.items():
            if cid not in chains:
                errors.append(f"content_type_unknown_chain:{ctype}->{cid}")

    ferry = chains.get("ferry_preserve_atmosphere") if isinstance(chains, dict) else None
    if isinstance(ferry, dict):
        forbidden = set(ferry.get("fairlight", {}).get("forbidden") or [])
        forbidden |= set(ferry.get("ffmpeg_fallback", {}).get("forbidden") or [])
        for req in ("afftdn", "demucs", "ai_voice_isolation"):
            if req not in forbidden:
                errors.append(f"ferry_missing_forbidden:{req}")

    return errors


def _doc() -> dict[str, Any]:
    return load_chains_document()


def chain_spec(chain_id: str) -> dict[str, Any]:
    doc = _doc()
    chains = doc.get("chains") or {}
    if chain_id not in chains:
        raise KeyError(f"unknown_ambient_chain:{chain_id}")
    return dict(chains[chain_id])


def youtube_preset_to_ambient_chain(youtube_preset: str) -> str:
    doc = _doc()
    mapping = doc.get("youtube_preset_to_chain") or {}
    return str(mapping.get(youtube_preset) or "broadcast_ambient_master")


def chain_for_youtube_preset(youtube_preset: str) -> str:
    return youtube_preset_to_ambient_chain(youtube_preset)


def chain_for_content_type(content_type: str) -> str:
    doc = _doc()
    mapping = doc.get("content_type_default_chain") or {}
    return str(mapping.get((content_type or "unknown").strip().lower()) or "broadcast_ambient_master")


def _chain_labels() -> dict[str, str]:
    doc = _doc()
    out: dict[str, str] = {}
    for cid, spec in (doc.get("chains") or {}).items():
        if isinstance(spec, dict):
            out[cid] = str(spec.get("label") or cid)
    return out


_doc_cache: dict[str, Any] | None = None


def _get_doc() -> dict[str, Any]:
    global _doc_cache  # noqa: PLW0603
    if _doc_cache is None:
        _doc_cache = load_chains_document()
    return _doc_cache


def reload_chains() -> None:
    global _doc_cache  # noqa: PLW0603
    _doc_cache = None


AMBIENT_CHAIN_IDS: tuple[str, ...] = tuple(
    sorted((_get_doc().get("chains") or {}).keys())
)

CHAIN_LABELS: dict[str, str] = _chain_labels()

GLOBAL_FORBIDDEN_FILTERS: tuple[str, ...] = tuple(
    (_get_doc().get("forbidden_global") or [])
)

FERRY_FORBIDDEN_FILTERS: tuple[str, ...] = (
    "afftdn",
    "demucs",
    "ai_voice_isolation",
    "anlmdn",
    "arnndn",
)
