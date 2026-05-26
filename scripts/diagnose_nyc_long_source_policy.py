#!/usr/bin/env python3
"""NYC long source policy diagnose — read-only; scans pools + media index + latest dry-run hints."""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent
_NYC = _REPO / "nyc_auto"
for _p in (_REPO, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from utils.storage_paths import get_sv_cache, get_sv_transfer  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")


from nyc_long_source_policy import LONG_SOURCE_POLICY_VERSION, is_valid_nyc_long_source  # noqa: E402

CONTROL = Path.home() / "StateVerge_Control_Center" / "logs"
OUT_JSON = CONTROL / "nyc_long_source_policy_diagnose.json"
OUT_MD = CONTROL / "nyc_long_source_policy_diagnose.md"
VIDEO_EXT = {".mp4", ".mov", ".m4v"}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ffprobe_json(path: Path) -> dict[str, Any] | None:
    import shutil
    import subprocess

    exe = shutil.which("ffprobe") or "ffprobe"
    cmd = [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "{}")
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
        return None


def _iter_videos(roots: list[Path], limit: int) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file() or p.name.startswith("._"):
                    continue
                if p.suffix.lower() not in VIDEO_EXT:
                    continue
                out.append(p)
                if len(out) >= limit:
                    return out
        except OSError:
            continue
    return out


def main() -> int:
    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)
    roots = [
        xfer / "00_INBOX" / "iphone",
        cache / "inbox",
        xfer / "ready_to_upload",
    ]
    media_index = xfer / "media_index" / "media_index.json"

    stats: dict[str, int] = Counter()
    rejected_samples: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []

    files = _iter_videos(roots, limit=400)
    stats["total_videos_scanned_roots"] = len(files)
    for p in files:
        j = _ffprobe_json(p)
        pol = is_valid_nyc_long_source(p, ffprobe_meta=j)
        rrs = [str(x) for x in (pol.get("reject_reasons") or [])]
        if pol.get("long_allowed"):
            stats["accepted_long_candidates"] += 1
        else:
            stats["rejected_long"] += 1
            if pol.get("long_candidate_uncertain"):
                uncertain.append({"path": str(p), "reason": pol.get("reason")})
            if "portrait" in str(pol.get("orientation") or "").lower() or "rejected_vertical_video" in rrs:
                stats["rejected_portrait"] += 1
            if any("walking" in x for x in rrs):
                stats["rejected_walking"] += 1
            if any("shorts" in x or "rejected_shorts_path" in x for x in rrs):
                stats["rejected_shorts_assets"] += 1
            if "image" in str(pol.get("reason") or ""):
                stats["rejected_images"] += 1
            if "timelapse" in str(pol.get("reason") or ""):
                stats["rejected_timelapse_whole"] += 1
            if len(rejected_samples) < 80:
                rejected_samples.append(
                    {
                        "path": str(p),
                        "reason": pol.get("reason"),
                        "width": pol.get("width"),
                        "height": pol.get("height"),
                        "aspect_ratio": pol.get("aspect_ratio"),
                        "reject_reasons": rrs,
                    }
                )

    mi_total = mi_accepted = 0
    if media_index.is_file():
        try:
            d = json.loads(media_index.read_text(encoding="utf-8", errors="replace"))
            items = d.get("items") if isinstance(d, dict) else []
            if isinstance(items, list):
                mi_total = len(items)
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    if str(it.get("media_type") or "") != "video":
                        continue
                    if it.get("long_allowed") is True:
                        mi_accepted += 1
        except (OSError, json.JSONDecodeError):
            stats["media_index_read_error"] = 1

    dry_hint: dict[str, Any] = {}
    status = "ok"
    long_pack = xfer / "publish_pack" / "nyc_long_uploads"
    if long_pack.is_dir():
        try:
            cands = sorted(long_pack.rglob("long_job_result.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:3]
            for cp in cands:
                dj = json.loads(cp.read_text(encoding="utf-8", errors="replace"))
                if not isinstance(dj, dict):
                    continue
                dry_hint = {
                    "path": str(cp),
                    "selected_orientation": dj.get("selected_orientation"),
                    "selected_aspect_ratio": dj.get("selected_aspect_ratio"),
                    "selected_source_type": dj.get("selected_source_type"),
                    "selected_aspect_policy": dj.get("selected_aspect_policy"),
                }
                sor = str(dj.get("selected_orientation") or "").lower()
                sst = str(dj.get("selected_source_type") or "").lower()
                try:
                    sar = float(dj.get("selected_aspect_ratio") or 0)
                except (TypeError, ValueError):
                    sar = 0.0
                if sor == "portrait" or sst == "walking" or (sar > 0 and (sar < 1.55 or sar > 1.9)):
                    status = "needs_fix"
                break
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    rnd = rejected_samples[:]
    random.shuffle(rnd)
    sample20 = sorted(rnd[:20], key=lambda x: str(x.get("path") or ""))

    summary = {
        "generated_at": _utc(),
        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "status": status,
        "stats": dict(stats),
        "media_index_video_rows": mi_total,
        "media_index_long_allowed_rows": mi_accepted,
        "uncertain_landscape_candidates_count": len(uncertain),
        "latest_long_job_hint": dry_hint,
        "rejected_sample_20": sample20,
    }
    CONTROL.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"summary": summary}, indent=2, ensure_ascii=False), encoding="utf-8")
    OUT_MD.write_text(
        "\n".join(
            [
                f"# NYC long source policy diagnose ({summary['generated_at']})",
                "",
                f"- status: **{status}**",
                f"- policy: `{LONG_SOURCE_POLICY_VERSION}`",
                "",
                "## stats",
                "",
                "```json",
                json.dumps(dict(stats), indent=2, ensure_ascii=False),
                "```",
                "",
                "## latest long_job_result hint",
                "",
                "```json",
                json.dumps(dry_hint, indent=2, ensure_ascii=False),
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "wrote_json": str(OUT_JSON), "status": status}, indent=2))
    return 0 if status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
