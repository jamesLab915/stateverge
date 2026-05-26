#!/usr/bin/env python3
"""Build a local offline embeddings store for Media Intelligence v3.

The production storage layer is sqlite for portability:
``SV_TRANSFER/media_index/media_embeddings.sqlite``. Vectors are deterministic
hashed text embeddings, so the script works offline without model downloads.
If ``faiss`` and ``numpy`` are importable, a best-effort FAISS sidecar is also
written, but sqlite remains the source of truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from storage_paths import get_sv_transfer  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")


DEFAULT_DIM = 384


def media_index_dir() -> Path:
    return get_sv_transfer(verbose=False) / "media_index"


def load_items(index_path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(index_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else []
    return [x for x in items if isinstance(x, dict)]


def token_hash_vector(text: str, *, dim: int = DEFAULT_DIM) -> list[float]:
    vec = [0.0] * dim
    words = [w for w in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if w]
    if not words:
        return vec
    for w in words:
        h = hashlib.blake2b(w.encode("utf-8", errors="replace"), digest_size=8).digest()
        n = int.from_bytes(h, "little")
        idx = n % dim
        sign = -1.0 if (n >> 9) & 1 else 1.0
        vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def embedding_text(item: dict[str, Any]) -> str:
    bits: list[str] = []
    for k in ("embedding_text", "ai_summary", "filename", "path", "borough", "neighborhood", "landmark", "orientation"):
        v = item.get(k)
        if isinstance(v, str):
            bits.append(v)
    for k in ("search_keywords", "scene_type", "mood", "usable_for"):
        v = item.get(k)
        if isinstance(v, list):
            bits.extend(str(x) for x in v)
    return " ".join(x for x in bits if x).strip()


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embeddings (
            path TEXT PRIMARY KEY,
            id TEXT,
            embedding_text TEXT NOT NULL,
            keywords_json TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector BLOB NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_id ON embeddings(id)")
    conn.commit()


def write_faiss_optional(rows: list[tuple[str, list[float]]], out_path: Path) -> tuple[bool, str | None]:
    try:
        import faiss  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return False, f"faiss_unavailable:{exc!r}"
    if not rows:
        return False, "no_rows"
    try:
        arr = np.array([v for _p, v in rows], dtype="float32")
        index = faiss.IndexFlatIP(arr.shape[1])
        index.add(arr)
        faiss.write_index(index, str(out_path))
        out_path.with_suffix(".faiss.meta.json").write_text(
            json.dumps({"paths": [p for p, _v in rows], "generated_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False),
            encoding="utf-8",
        )
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"faiss_write_failed:{exc!r}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", default=str(media_index_dir() / "media_index_v3.json"))
    ap.add_argument("--db", default=str(media_index_dir() / "media_embeddings.sqlite"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dim", type=int, default=DEFAULT_DIM)
    ap.add_argument("--faiss", action="store_true", help="Also attempt to write media_embeddings.faiss if deps exist.")
    args = ap.parse_args()

    index_path = Path(args.index).expanduser()
    db_path = Path(args.db).expanduser()
    items = load_items(index_path)
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"sqlite_open_failed:{exc!r}", "db": str(db_path)}))
        return 1

    init_db(conn)
    rows_for_faiss: list[tuple[str, list[float]]] = []
    written = 0
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        for item in items:
            path = str(item.get("path") or "")
            if not path:
                continue
            text = embedding_text(item)
            if not text:
                continue
            vec = token_hash_vector(text, dim=max(8, int(args.dim)))
            rows_for_faiss.append((path, vec))
            conn.execute(
                """
                INSERT INTO embeddings(path, id, embedding_text, keywords_json, dim, vector, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    id=excluded.id,
                    embedding_text=excluded.embedding_text,
                    keywords_json=excluded.keywords_json,
                    dim=excluded.dim,
                    vector=excluded.vector,
                    updated_at=excluded.updated_at
                """,
                (
                    path,
                    str(item.get("id") or ""),
                    text,
                    json.dumps(item.get("search_keywords") or [], ensure_ascii=False),
                    len(vec),
                    pack_vector(vec),
                    now,
                ),
            )
            written += 1
    conn.close()

    faiss_ok = False
    faiss_warning = None
    if args.faiss:
        faiss_ok, faiss_warning = write_faiss_optional(rows_for_faiss, db_path.with_name("media_embeddings.faiss"))

    print(
        json.dumps(
            {
                "ok": True,
                "index": str(index_path),
                "db": str(db_path),
                "written": written,
                "faiss_written": faiss_ok,
                "faiss_warning": faiss_warning,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
