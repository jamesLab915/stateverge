"""
CLI: discover scripts under ``topics/<topic>/``, run Pexels → Pixabay → DVIDS, write
``assets/media_manifest.json`` and files under ``assets/raw/``."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import LOG_PREFIX, default_repo_root, load_env_from_dotenv_file, safe_base_filename
from . import dvids as mod_dvids
from . import pexels as mod_pexels
from . import pixabay as mod_pixabay


@dataclass
class _Seg:
    seg_id: str
    text: str


# Stopwords (EN + 中文虚词) — minimal, no extra deps
_EN_STOP = frozenset(
    "the a an and or but if in on at to for of as is was are were be been being "
    "it its this that these those with from by not no yes so if about into over "
    "out up down can could should would may will just also only very much then "
    "how what when who which where why".split()
)
_ZH_STOP = frozenset(
    "的 了 是 在 有 和 就 不 人 一 我 这 中 大 为 个 上 以 要 要 以 时 来 会 "
    "到 到 以 为 为 和 能 能 到 这 个 了 是 的 了 在 有 我 和 不 是 的 一 是 "
    "们 来 为 有 有 是 的 是 的 的 的".split()
)

# Topic slugs (lowercase) → added query phrases
_TOPIC_ALIASES: dict[str, list[str]] = {
    "chernobyl": [
        "chernobyl",
        "pripyat",
        "nuclear power plant",
        "soviet",
        "reactor",
        "evacuation",
        "firefighters",
        "exclusion zone",
        "liquidators",
    ],
    "pripyat": [
        "pripyat",
        "chernobyl",
        "exclusion zone ukraine",
    ],
}

_HISTORY_HINTS: frozenset[str] = frozenset(
    "nuclear world war soviet ukraine revolution empire colony congress treaty "
    "military strategy documentary archive crisis evacuation reactor cold war".split()
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_input_file(root: Path, topic: str, input_override: Path | None) -> Path:
    if input_override and input_override.is_file():
        return input_override.resolve()
    tdir = root / "topics" / topic
    candidates: list[Path] = [
        tdir / "brief" / "narration_script.txt",
        tdir / "narration_script.txt",
        tdir / "presenter" / "presenter_script.json",
        tdir / "script" / "narration_script.txt",  # common scaffold (before brief json)
        tdir / "brief" / "production_brief.json",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    names = " → ".join(str(p.relative_to(root)) for p in candidates)
    raise FileNotFoundError(
        f"No input script for topic {topic!r}. Tried (in order): {names}."
    )


def segments_from_narration_text(text: str) -> list[_Seg]:
    tr = str.maketrans("０１２３４５６７８９", "0123456789")
    segs: list[_Seg] = []
    for block in re.split(r"(?=第\s*[0-9０-９]+\s*章)", text):
        b = block.strip()
        if not b:
            continue
        m = re.match(r"第\s*([0-9０-９]+)\s*章[^\n]*\n*([\s\S]*)", b, re.DOTALL)
        if m:
            n = m.group(1).translate(tr)
            try:
                idx = int(n)
            except ValueError:
                continue
            body = (m.group(2) or "").strip()
            if body:
                segs.append(_Seg(f"segment_{idx:02d}", body))
    if segs:
        return segs
    chunks = re.split(
        r"^\s*[-─=]{3,}\s*$\n*",
        text,
        flags=re.M,
    )
    parts = [c.strip() for c in chunks if len(c.strip()) > 20]
    if not parts:
        if text.strip():
            return [_Seg("segment_01", text.strip())]
        return []
    return [_Seg(f"segment_{i+1:02d}", t) for i, t in enumerate(parts)]


def segments_from_presenter(path: Path) -> list[_Seg]:
    j = _read_json(path)
    segs: list[_Seg] = []
    n = 0
    for it in j.get("inserts") or []:
        name = str(it.get("segment") or "")
        t = (it.get("text") or "").strip()
        if not t:
            continue
        if name in ("intro", "outro"):
            sid = f"segment_{name}"
        else:
            n += 1
            sid = f"segment_{n:02d}"
        segs.append(_Seg(sid, t))
    if not segs and path.is_file():
        return [_Seg("segment_01", path.read_text(encoding="utf-8")[:4000])]
    return segs


def segments_from_brief(path: Path) -> list[_Seg]:
    j = _read_json(path)
    ch = j.get("chapters") or []
    if isinstance(ch, list) and ch:
        out: list[_Seg] = []
        for i, c in enumerate(ch, start=1):
            c = c if isinstance(c, dict) else {}
            t = f"{c.get('name', '')} {c.get('focus', '')} {c.get('duration_sec', '')}".strip()
            out.append(_Seg(f"segment_{i:02d}", t or f"chapter {i}"))
        return out
    title = str(j.get("title", ""))
    st = str(j.get("style", ""))
    return [
        _Seg("segment_01", f"{title} {st}".strip() or "stateverge"),
    ]


def load_segments_for_path(path: Path) -> list[_Seg]:
    s = path.suffix.lower()
    t = path.read_text(encoding="utf-8", errors="replace")
    if s == ".txt":
        return segments_from_narration_text(t)
    if s == ".json" and "presenter" in path.name.lower() and "inserts" in t:
        return segments_from_presenter(path)
    if s == ".json" and path.name == "production_brief.json":
        return segments_from_brief(path)
    if s == ".json":
        try:
            j = json.loads(t)
        except json.JSONDecodeError:
            return [_Seg("segment_01", t[:5000])]
        if "inserts" in j:
            return segments_from_presenter(path)
        if "chapters" in j:
            return segments_from_brief(path)
        if isinstance(j, dict) and (j.get("narration") or j.get("text")):
            s0 = j.get("narration") or j.get("text")
            if isinstance(s0, str) and s0.strip():
                return segments_from_narration_text(s0)
        return segments_from_brief(path)
    return segments_from_narration_text(t)


def _words_and_phrases(txt: str) -> list[str]:
    t = re.sub(
        r"[#*=_`\[\]（）\(\)「」《》<>\d]+", " ", txt, flags=re.UNICODE
    )
    parts: list[str] = []
    for w in re.split(r"[\s。，,、;；!！?？\n\r/\\|·•]+", t):
        s = w.strip(" \t'\"-")
        if len(s) < 2 or len(s) > 64:
            continue
        if re.match(r"^[A-Za-z][A-Za-z\-]{2,}$", s) and s.lower() in _EN_STOP:
            continue
        if len(s) <= 4 and s in _ZH_STOP:
            continue
        parts.append(s)
    for m in re.findall(
        r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)+)",
        " " + txt,
    ):
        s = m.strip()
        if len(s) > 2:
            parts.append(s)
    for m in re.findall(
        r"(\d{3,4})\s*年?|\b(19|20)\d{2}\b", txt, re.IGNORECASE
    ):
        if m:
            parts.append(str(m))
    for m in re.findall(r"[\u4e00-\u9fff]{4,12}", txt)[:4]:
        parts.append(m)
    for m in re.findall(
        r"[\u4e00-\u9fff]+(?:\s*[\u4e00-\u9fff]+){0,2}",
        txt,
    )[:3]:
        if 4 <= len(m) <= 20:
            parts.append(m)
    for m in re.findall(
        r"\b[a-z]{3,}(?:\s[a-z]{3,}){0,3}\b",
        t.lower() + " " + txt,
        re.IGNORECASE,
    ):
        if m.split()[0] not in _EN_STOP or len(m) > 10:
            parts.append(m)
    for h in _HISTORY_HINTS:
        if h in t.lower() and h not in (x.lower() for x in parts if x.isascii()):
            parts.append(h)
    return parts


def build_smart_queries(
    topic_slug: str,
    segments: list[_Seg],
    max_queries: int = 8,
) -> list[str]:
    ukey = topic_slug.lower().strip().replace(" ", "-").replace("_", "-")
    boost: list[str] = list(_TOPIC_ALIASES.get(ukey, ()))
    if not boost and ukey.replace("-", "_") in _TOPIC_ALIASES:
        boost = list(_TOPIC_ALIASES[ukey.replace("-", "_")])
    blob = " \n".join(f"{s.seg_id} {s.text}" for s in segments)
    cands: list[str] = list(boost) + [ukey, topic_slug, topic_slug.replace(" ", "")]
    cands += _words_and_phrases(blob)[: 12 * max(1, len(segments) or 1)]
    for s in segments:
        cands += _words_and_phrases(s.text)[:4]
    if not cands:
        cands = [f"{ukey} documentary"]
    out: list[str] = []
    s2: set[str] = set()
    for c in cands:
        t = (c or "").strip()[:100]
        if len(t) < 2:
            continue
        k = t.lower() if t.isascii() else t
        if k in s2:
            continue
        s2.add(k)
        out.append(t)
        if len(out) >= max(1, max_queries):
            break
    if not out:
        out = [f"{ukey} documentary"[:100].strip() or f"{ukey} world"[:100]]
    return out


def _dedup_key(d: dict[str, Any]) -> str:
    return str(d.get("download_url") or d.get("url") or d.get("raw", {}).get("id", ""))


def _pixels(d: dict[str, Any]) -> int:
    return int(d.get("width") or 0) * int(d.get("height") or 0)


def _gather_all(
    queries: list[str],
    per_page: int,
    source_order: list[str],
) -> list[dict[str, Any]]:
    pool: list[dict[str, Any]] = []
    for q in queries:
        if "pexels" in source_order:
            for item in mod_pexels.search(q, per_page):
                item2 = {**item, "query": q, "_src": "pexels"}
                pool.append(item2)
        if "pixabay" in source_order:
            for item in mod_pixabay.search(q, per_page):
                item2 = {**item, "query": q, "_src": "pixabay"}
                pool.append(item2)
        if "dvids" in source_order:
            for item in mod_dvids.search(q, per_page):
                item2 = {**item, "query": q, "_src": "dvids"}
                pool.append(item2)
    seen2: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for d in pool:
        k0 = _dedup_key(d)
        if not k0 or k0 in seen2:
            continue
        seen2.add(k0)
        uniq.append(d)
    return sorted(uniq, key=_pixels, reverse=True)


def _assign_segments(
    items: list[dict[str, Any]],
    segments: list[_Seg],
    max_per: int,
) -> dict[str, list[dict[str, Any]]]:
    if not segments:
        return {"segment_01": (items)[: max_per] if max_per else items}
    sids = [s.seg_id for s in segments]
    out: dict[str, list[dict[str, Any]]] = {sid: [] for sid in sids}

    def overlap(a: str, b: str) -> int:
        ax = set(re.findall(r"[\u4e00-\u9fff]{1,3}", a)) | set(
            re.findall(r"\b\w\w\w+\b", a.lower())
        )
        bx = set(re.findall(r"[\u4e00-\u9fff]{1,3}", b)) | set(
            re.findall(r"\b\w\w\w+\b", b.lower())
        )
        return len(ax & bx)

    for d in items:
        q2 = f"{d.get('title', '')} {d.get('query', '')} {d.get('author', '')}"
        best, sc = sids[0], -1
        for s in segments:
            o = overlap(s.text + s.seg_id, q2) + 2 * overlap(
                s.text, str(d.get("raw", q2))
            )
            if o > sc:
                sc, best = o, s.seg_id
        if not out[best] or len(out[best]) < max_per:
            out[best].append(d)
    for k in sids:
        if len(out[k]) > max_per:
            out[k] = out[k][:max_per]
    return out


def _ext_for(d: dict[str, Any], url: str) -> str:
    p = (url or "").lower().split("?", 1)[0]
    if p.endswith((".m3u8",)):
        return ".m3u8"
    if p.endswith((".mp4",)):
        return ".mp4"
    if p.endswith((".webm",)):
        return ".webm"
    if p.endswith((".mov",)):
        return ".mov"
    if p.endswith((".png",)):
        return ".png"
    if p.endswith((".jpg", ".jpeg",)):
        return ".jpg"
    if d.get("kind") == "image":
        return ".jpg"
    if d.get("kind") == "video":
        return ".mp4"
    return ".bin"


def _download_for_item(
    d: dict[str, Any], path: str, source: str, force: bool
) -> str:
    durl = str(d.get("download_url") or "").strip()
    if not durl:
        if source == "dvids":
            return ""
        durl = str(d.get("url", ""))  # fallback: should not for stock
    if not durl.startswith("http"):
        print(
            f"{LOG_PREFIX} skip download (no direct URL) source={source!r} title={d.get('title')!r}",
            flush=True,
        )
        return ""
    if source == "pexels":
        return mod_pexels.download(durl, path, force=force)  # type: ignore[call-arg]
    if source == "pixabay":
        return mod_pixabay.download(durl, path, force=force)  # type: ignore[call-arg]
    return mod_dvids.download(durl, path, force=force)  # type: ignore[call-arg]


def run_cli(args: argparse.Namespace) -> int:
    root = (args.root or default_repo_root()).resolve()
    load_env_from_dotenv_file(root)
    in_path = discover_input_file(
        root,
        args.topic,
        Path(args.input) if args.input else None,
    )
    segs = load_segments_for_path(in_path)
    srcs = [s.strip().lower() for s in (args.source or "pexels,pixabay,dvids").split(",")]
    srcs = [s for s in srcs if s in ("pexels", "pixabay", "dvids")]
    if not srcs:
        print(
            f"{LOG_PREFIX} error: no valid --source; use pexels,pixabay,dvids",
            flush=True,
        )
        return 1
    queries = build_smart_queries(
        args.topic, segs, max_queries=int(args.max_queries)
    )
    print(f"{LOG_PREFIX} topic={args.topic!r} input={in_path!s} queries={queries!r}", flush=True)
    all_items = _gather_all(queries, int(args.per_page), srcs)
    buckets = _assign_segments(
        all_items, segs, int(args.max_per_segment)
    )
    assets = root / "topics" / args.topic / "assets"
    raw_dir = assets / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    out_segments: dict[str, list[dict[str, Any]]] = {
        s.seg_id: [] for s in segs
    } or {"segment_01": []}
    for seg_id, items2 in sorted(buckets.items(), key=lambda x: x[0]):
        c = 0
        for d0 in items2:
            c += 1
            d = d0
            dsrc = str(
                d.get("source", d.get("_src", "unknown"))
            ).lower()
            ext = _ext_for(d, str(d.get("download_url", "")) or str(d.get("url", "")))
            fname = f"{safe_base_filename(f'{seg_id}_{dsrc}_{c:03d}')}{ext}"
            fpath = raw_dir / fname
            relp = f"topics/{args.topic}/assets/raw/{fname}"
            durl = str(d.get("download_url") or "").strip()
            entry: dict[str, Any] = {
                "source": dsrc,
                "kind": d.get("kind", "video"),
                "query": d.get("query", ""),
                "url": d.get("url", ""),
                "download_url": d.get("download_url", ""),
                "file": relp,
                "width": int(d.get("width", 0) or 0),
                "height": int(d.get("height", 0) or 0),
                "duration": int(d.get("duration", 0) or 0),
                "license": d.get("license", ""),
            }
            if d.get("author") and str(d.get("author", "")).strip():
                entry["author"] = d.get("author")
            if not durl or not durl.lower().startswith("http"):
                if dsrc == "dvids" and d.get("url"):
                    print(
                        f"{LOG_PREFIX} DVIDS page-only (no file URL) title={d.get('title', '')!r}",
                        flush=True,
                    )
                entry["file"] = ""
                out_segments.setdefault(seg_id, []).append(entry)
                continue
            if args.no_download:
                out_segments.setdefault(seg_id, []).append(entry)
                continue
            try:
                ret = _download_for_item(
                    d, str(fpath), dsrc, bool(args.force)
                )
                if ret and fpath.is_file() and fpath.stat().st_size > 0:
                    entry["file"] = relp
                else:
                    entry["file"] = ""
            except Exception as e:  # noqa: BLE001
                print(
                    f"{LOG_PREFIX} download error seg={seg_id!r} err={e!r}",
                    flush=True,
                )
                entry["file"] = ""
            out_segments.setdefault(seg_id, []).append(entry)
    for s in segs:
        if s.seg_id not in out_segments:
            out_segments[s.seg_id] = []
    try:
        _src_in = str(in_path.relative_to(root)) if in_path.is_relative_to(root) else str(in_path)
    except (ValueError, OSError):
        _src_in = str(in_path)
    manifest: dict[str, Any] = {
        "topic": args.topic,
        "generated_at": generated,
        "source_input": _src_in,
        "segments": out_segments,
    }
    mpath = assets / "media_manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"{LOG_PREFIX} wrote {mpath.relative_to(root) if mpath.is_relative_to(root) else mpath!s}",
        flush=True,
    )
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="StateVerge media fetcher: Pexels, Pixabay, DVIDS"
    )
    ap.add_argument("--topic", required=True, help="Topic slug under topics/")
    ap.add_argument("--root", type=Path, default=None, help="Repo root (StateVerge)")
    ap.add_argument(
        "--per-page",
        type=int,
        default=5,
        help="per query per source (capped in API)",
    )
    ap.add_argument("--max-queries", type=int, default=8)
    ap.add_argument("--max-per-segment", type=int, default=6)
    ap.add_argument(
        "--no-download",
        action="store_true",
        help="Write manifest only (no files)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if file exists and non-empty",
    )
    ap.add_argument(
        "--source",
        type=str,
        default="pexels,pixabay,dvids",
        help="Comma: pexels,pixabay,dvids",
    )
    ap.add_argument(
        "--input",
        type=str,
        default=None,
        help="Override script (txt or json)",
    )
    ns = ap.parse_args()
    raise SystemExit(run_cli(ns))


if __name__ == "__main__":
    main()
