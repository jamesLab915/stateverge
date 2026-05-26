#!/usr/bin/env python3
"""Asset lifecycle intelligence v1 — cross-reference index vs publish/upload/runtime (fail-open)."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO = _SCRIPT_DIR.parent
_SRC = _REPO / "src"
for _p in (_SCRIPT_DIR, _SRC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from utils.storage_paths import get_sv_transfer, get_transfer_ready_to_upload  # type: ignore
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER/ready_to_upload")


try:
    from stateverge_paths import SSD_ROOT  # type: ignore
except Exception:  # noqa: BLE001
    SSD_ROOT = Path(os.environ.get("STATEVERGE_VOL", "/Volumes/StateVerge")).expanduser()

HOME = Path.home()
STATEVERGE = HOME / "StateVerge"
DATA_MEDIA_INDEX = STATEVERGE / "data" / "media_index"
YT_DIR = STATEVERGE / "data" / "youtube"
SHORTS_RT = STATEVERGE / "data" / "shorts_runtime"
LONG_RT = STATEVERGE / "data" / "long_runtime"
SV_PRIMARY = Path("/Volumes/SV_TRANSFER")

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
CAP_LIST = 40


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(p: str) -> str:
    t = (p or "").strip()
    if not t:
        return ""
    try:
        return str(Path(t).expanduser().resolve())
    except OSError:
        return str(Path(t).expanduser())


def _asset_id_for_path(path: str) -> str:
    c = _canonical(path) or path.strip()
    return hashlib.sha256(c.encode("utf-8", errors="replace")).hexdigest()[:20]


def _iter_index_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("items", "entries", "media", "records"):
            v = raw.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _index_path_for_item(it: dict[str, Any]) -> str:
    for k in ("path", "file_path", "source_path", "resolved_path"):
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _media_index_dir(warnings: list[str]) -> Path:
    try:
        return get_sv_transfer(verbose=False) / "media_index"
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"get_sv_transfer_failed:{exc!r}")
    if SV_PRIMARY.exists():
        return SV_PRIMARY / "media_index"
    DATA_MEDIA_INDEX.mkdir(parents=True, exist_ok=True)
    warnings.append("media_index_dir_fallback:~/StateVerge/data/media_index")
    return DATA_MEDIA_INDEX


def _pick_writable_output_dir(preferred: Path, warnings: list[str]) -> Path:
    candidates = [preferred, DATA_MEDIA_INDEX]
    for i, candidate in enumerate(candidates):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".sv_media_index_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            if i > 0:
                warnings.append(f"output_dir_fallback:{candidate}")
            return candidate
        except OSError as exc:
            warnings.append(f"output_dir_not_writable:{candidate}:{exc!r}")
            continue
    return preferred


def _load_index(mid: Path, warnings: list[str]) -> tuple[list[dict[str, Any]], str]:
    for name in ("media_index_v3.json", "media_index.json", "media_scene_signals.json"):
        p = mid / name
        try:
            if p.is_file():
                raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                return _iter_index_items(raw), str(p)
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"index_read_failed:{p}:{exc!r}")
    return [], ""


def _load_published_paths(warnings: list[str]) -> set[str]:
    out: set[str] = set()
    pub = SSD_ROOT / "05_INDEX" / "published_assets.jsonl"
    try:
        if not pub.is_file():
            warnings.append("published_assets_jsonl_missing")
            return out
        for line in pub.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(o, dict):
                continue
            for k in ("path", "source_path", "asset_path", "file_path", "media_path"):
                v = o.get(k)
                if isinstance(v, str) and v.strip():
                    out.add(_canonical(v))
    except OSError as exc:
        warnings.append(f"published_assets_read_failed:{exc!r}")
    return out


def _paths_from_upload_history(warnings: list[str]) -> set[str]:
    out: set[str] = set()
    p = YT_DIR / "upload_history.csv"
    try:
        if not p.is_file():
            warnings.append("upload_history_csv_missing")
            return out
        with p.open(encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return out
            for row in reader:
                if not isinstance(row, dict):
                    continue
                for k in reader.fieldnames or ():
                    lk = (k or "").lower()
                    if "path" in lk or "file" in lk or "source" in lk:
                        v = row.get(k)
                        if isinstance(v, str) and v.strip():
                            out.add(_canonical(v))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"upload_history_scan_failed:{exc!r}")
    return out


def _scan_json_under(root: Path, warnings: list[str], *, label: str) -> set[str]:
    found: set[str] = set()
    if not root.is_dir():
        return found
    try:
        for jp in root.rglob("*.json"):
            if jp.name.startswith("._"):
                continue
            try:
                if jp.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue
            try:
                data = json.loads(jp.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            for k in (
                "source_path",
                "source_video",
                "selected_video",
                "video_path",
                "path",
                "resolved_path",
                "upload_video",
            ):
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    found.add(_canonical(v))
            sel = data.get("selected")
            if isinstance(sel, dict):
                v = sel.get("path") or sel.get("source_path")
                if isinstance(v, str) and v.strip():
                    found.add(_canonical(v))
            dj = data.get("decision_json") or data.get("output")
            if isinstance(dj, dict):
                for k2 in ("source_path", "path", "upload_video"):
                    v2 = dj.get(k2)
                    if isinstance(v2, str) and v2.strip():
                        found.add(_canonical(v2))
    except OSError as exc:
        warnings.append(f"json_scan_failed:{label}:{exc!r}")
    return found


def _review_queue_paths(warnings: list[str]) -> set[str]:
    roots = [
        SV_PRIMARY / "publish_pack" / "review_queue",
        STATEVERGE / "data" / "review_queue",
    ]
    found: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for jp in root.iterdir():
                if jp.suffix.lower() != ".json" or jp.name.startswith("._"):
                    continue
                try:
                    data = json.loads(jp.read_text(encoding="utf-8", errors="replace"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                for k in ("source_path", "path", "local_path", "video_path"):
                    v = data.get(k)
                    if isinstance(v, str) and v.strip():
                        found.add(_canonical(v))
        except OSError as exc:
            warnings.append(f"review_queue_scan_failed:{root}:{exc!r}")
    return found


def _ready_to_upload_basenames(warnings: list[str]) -> set[str]:
    names: set[str] = set()
    try:
        root = get_transfer_ready_to_upload(verbose=False)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"ready_to_upload_resolve_failed:{exc!r}")
        root = SV_PRIMARY / "ready_to_upload"
    if not root.is_dir():
        warnings.append("ready_to_upload_dir_missing")
        return names
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"]
            for fn in filenames:
                if Path(fn).suffix.lower() in VIDEO_EXT:
                    names.add(fn.lower())
    except OSError as exc:
        warnings.append(f"ready_to_upload_walk_failed:{exc!r}")
    return names


def _quality_score(it: dict[str, Any]) -> int:
    score = 55
    try:
        w = int(it.get("video_width") or it.get("width") or 0)
        h = int(it.get("video_height") or it.get("height") or 0)
        if w >= 1920 and h >= 1080:
            score += 25
        elif w >= 1280:
            score += 15
    except (TypeError, ValueError):
        pass
    if str(it.get("embedding_text") or "").strip():
        score += 10
    if it.get("has_gps") is True or _gps_present(it):
        score += 5
    return max(0, min(100, score))


def _gps_present(it: dict[str, Any]) -> bool:
    for k in ("latitude", "longitude", "lat", "lon"):
        if it.get(k) is not None:
            return True
    g = it.get("gps")
    return isinstance(g, dict) and any(g.get(x) is not None for x in ("latitude", "lat", "longitude", "lon"))


def _semantic_scene_type(it: dict[str, Any]) -> str:
    st = it.get("scene_type")
    if isinstance(st, list) and st:
        return str(st[0])
    if isinstance(st, str):
        return st
    return str(it.get("likely_source_type") or it.get("source_type") or "")


def _music_fit(it: dict[str, Any]) -> str:
    for k in ("music_fit", "music_bin", "audio_mode"):
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unknown"


def run() -> int:
    warnings: list[str] = []
    errors: list[str] = []
    error_count = 0
    mid = DATA_MEDIA_INDEX

    try:
        mid = _media_index_dir(warnings)
        items, idx_src = _load_index(mid, warnings)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"init_failed:{exc!r}")
        error_count += 1
        items, idx_src = [], ""

    published = set()
    try:
        published = _load_published_paths(warnings)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"published_section:{exc!r}")

    hist_paths = set()
    try:
        hist_paths = _paths_from_upload_history(warnings)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"history_section:{exc!r}")

    long_used: set[str] = set()
    shorts_used: set[str] = set()
    try:
        long_used |= _scan_json_under(LONG_RT / "long_uploads", warnings, label="long_runtime")
        long_used |= _scan_json_under(SV_PRIMARY / "publish_pack" / "nyc_long_uploads", warnings, label="sv_long_uploads")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"long_runtime_section:{exc!r}")
    try:
        shorts_used |= _scan_json_under(SHORTS_RT, warnings, label="shorts_runtime")
        shorts_used |= _scan_json_under(SV_PRIMARY / "publish_pack" / "shorts_uploads", warnings, label="sv_shorts_uploads")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"shorts_runtime_section:{exc!r}")

    review_paths: set[str] = set()
    try:
        review_paths = _review_queue_paths(warnings)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"review_queue_section:{exc!r}")

    ready_names = set()
    try:
        ready_names = _ready_to_upload_basenames(warnings)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"ready_section:{exc!r}")

    use_counts: dict[str, int] = defaultdict(int)
    for s in (long_used, shorts_used, hist_paths, review_paths):
        for p in s:
            if p:
                use_counts[p] += 1

    db: dict[str, Any] = {}
    assets: list[dict[str, Any]] = []

    for it in items:
        try:
            path = _index_path_for_item(it)
            aid = str(it.get("asset_id") or "").strip() or _asset_id_for_path(path)
            cpath = _canonical(path) if path else ""
            base = Path(path).name.lower() if path else ""

            used_long = cpath in long_used if cpath else False
            used_shorts = cpath in shorts_used if cpath else False
            if not used_long and not used_shorts and base and base in ready_names:
                used_shorts = True  # heuristic: staged in ready pool

            pub = (cpath in published) if cpath else False
            if not pub and path:
                for pv in published:
                    if pv.endswith(base) or base in pv:
                        pub = True
                        break

            publish_count = 1 if pub else 0
            if cpath in hist_paths:
                publish_count = max(publish_count, 1)

            uc = int(use_counts.get(cpath, 0))
            if uc >= 3:
                dup_risk = min(100, 40 + uc * 10)
            elif uc == 2:
                dup_risk = 35
            else:
                dup_risk = max(0, 15 - int(_quality_score(it) / 5))

            q = _quality_score(it)
            reusability = max(0, min(100, q - min(40, uc * 12)))

            stage = "indexed"
            if pub or publish_count:
                stage = "published"
            elif used_long or used_shorts:
                stage = "used"
            elif not path:
                stage = "new"
            if str(it.get("archived") or "").lower() in ("1", "true", "yes"):
                stage = "archived"

            row = {
                "asset_id": aid,
                "path": path,
                "used_in_long": bool(used_long),
                "used_in_shorts": bool(used_shorts),
                "published": bool(pub),
                "publish_count": int(publish_count),
                "last_used_at": str(it.get("indexed_at") or it.get("mtime_iso") or ""),
                "quality_score": q,
                "gps_missing": not _gps_present(it),
                "semantic_scene_type": _semantic_scene_type(it),
                "music_fit": _music_fit(it),
                "lifecycle_stage": stage,
                "reusability_score": int(reusability),
                "duplicate_risk": int(dup_risk),
            }
            assets.append(row)
            db[aid] = row
        except Exception as exc:  # noqa: BLE001
            error_count += 1
            errors.append(f"asset_row:{exc!r}")

    unused_hq: list[str] = []
    overused: list[str] = []
    gps_missing: list[str] = []
    cinematic_cand: list[str] = []
    doc_cand: list[str] = []
    unused_hq_total = 0
    overused_total = 0

    for row in assets:
        pid = str(row.get("asset_id") or "")
        pth = str(row.get("path") or "")
        ref = pid or pth
        q = int(row.get("quality_score") or 0)
        st = str(row.get("semantic_scene_type") or "").lower()
        if row.get("gps_missing") and pth:
            if len(gps_missing) < CAP_LIST:
                gps_missing.append(ref)
        if (
            q >= 80
            and not row.get("used_in_long")
            and not row.get("used_in_shorts")
            and not row.get("published")
        ):
            unused_hq_total += 1
            if len(unused_hq) < CAP_LIST:
                unused_hq.append(ref)
        uc = int(use_counts.get(_canonical(pth), 0)) if pth else 0
        if (row.get("used_in_long") and row.get("used_in_shorts")) or uc >= 3:
            overused_total += 1
            if len(overused) < CAP_LIST:
                overused.append(ref)
        if "skyline" in st or "cinematic" in st or "ferry" in st:
            if len(cinematic_cand) < CAP_LIST:
                cinematic_cand.append(ref)
        if "documentary" in st or "walking" in st or "street" in st:
            if len(doc_cand) < CAP_LIST:
                doc_cand.append(ref)

    out_dir = _pick_writable_output_dir(mid, warnings)
    status = {
        "generated_at": _utc_iso(),
        "index_source": idx_src,
        "media_index_dir": str(mid),
        "asset_count": len(assets),
        "unused_high_quality_assets": unused_hq,
        "unused_high_quality_count": unused_hq_total,
        "overused_assets": overused,
        "overused_count": overused_total,
        "gps_missing_assets": gps_missing,
        "gps_missing_count": len(gps_missing),
        "cinematic_candidates": cinematic_cand,
        "cinematic_candidates_count": len(cinematic_cand),
        "documentary_candidates": doc_cand,
        "documentary_candidates_count": len(doc_cand),
        "warnings": warnings,
        "errors": errors,
        "error_count": error_count,
        "output_dir": str(out_dir),
    }

    out_path = out_dir / "asset_lifecycle_db.json"
    st_path = out_dir / "asset_lifecycle_status.json"
    try:
        out_path.write_text(
            json.dumps({"schema": "asset_lifecycle_v1", "generated_at": _utc_iso(), "assets": assets, "by_id": db}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        st_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        error_count += 1
        errors.append(f"write_failed:{exc!r}")

    print("ASSET_LIFECYCLE_INTELLIGENCE_V1_DONE", flush=True)
    print(f"UNUSED_HIGH_QUALITY_ASSETS={status['unused_high_quality_count']}", flush=True)
    print(f"OVERUSED_ASSETS={status['overused_count']}", flush=True)
    print(f"ERROR_COUNT={error_count}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
