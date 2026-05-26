#!/usr/bin/env python3
"""
Index NYC long vs Shorts candidates from formal inbox roots only.
Outputs JSON/CSV under SV_TRANSFER/media_index/ and a markdown report under Control Center logs.
Does not move or delete files.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".dng"}
MIN_VIDEO_BYTES = 1 * 1024 * 1024
SKIP_NAME_PREFIXES = ("._",)

FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"
EXIFTOOL = shutil.which("exiftool")

_SCRIPTS_DIR = Path(__file__).resolve().parent
_NYC_AUTO = _SCRIPTS_DIR / "nyc_auto"
for _p in (_SCRIPTS_DIR, _NYC_AUTO):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from storage_paths import (  # noqa: E402
        get_cache_inbox_dir,
        get_control_center_home,
        get_iphone_inbox_dir,
        get_media_index_dir,
    )

    FORMAL_ROOTS = (get_iphone_inbox_dir(), get_cache_inbox_dir())
    OUT_DIR = get_media_index_dir()
    REPORT_MD = get_control_center_home() / "logs" / "nyc_source_routing_policy_v1_report.md"
except Exception:
    FORMAL_ROOTS = (
        Path("/Volumes/SV_TRANSFER/00_INBOX/iphone"),
        Path("/Volumes/SV_CACHE/inbox"),
    )
    OUT_DIR = Path("/Volumes/SV_TRANSFER/media_index")
    REPORT_MD = Path.home() / "StateVerge_Control_Center" / "logs" / "nyc_source_routing_policy_v1_report.md"

INDEX_JSON = OUT_DIR / "nyc_source_policy_index.json"
INDEX_CSV = OUT_DIR / "nyc_source_policy_index.csv"
LONG_JSON = OUT_DIR / "nyc_long_driving_sources.json"
SHORTS_JSON = OUT_DIR / "nyc_shorts_sources.json"

try:
    from nyc_long_source_policy import (  # type: ignore[import-not-found]
        LONG_SOURCE_POLICY_VERSION,
        is_valid_nyc_long_source,
        policy_header_counts,
        summarize_rejections,
    )
except Exception:  # noqa: BLE001

    def is_valid_nyc_long_source(*_a: Any, **_k: Any) -> dict[str, Any]:  # type: ignore[misc]
        return {"long_allowed": True, "reject_reasons": [], "long_source_policy_version": "fallback"}

    LONG_SOURCE_POLICY_VERSION = "nyc_long_channel_source_policy_v1_fallback"  # type: ignore[misc]

    def policy_header_counts(*, accepted: int, rejected: int, by_reason: dict[str, int]) -> dict[str, Any]:  # type: ignore[misc]
        return {"long_candidates_count": accepted, "rejected_long_candidates_count": rejected, "rejected_by_reason": by_reason}

    def summarize_rejections(_rows: list[dict[str, Any]]) -> dict[str, int]:  # type: ignore[misc]
        return {}


def _ffprobe_json(path: Path) -> dict[str, Any] | None:
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "{}")
    except Exception:
        return None


def _parse_meta_datetime(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(s.replace("Z", ""), fmt.replace("Z", ""))
        except ValueError:
            continue
    try:
        if re.match(r"^\d{8}_\d{6}", s):
            return datetime.strptime(s[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        pass
    return None


def _extract_time_from_ffprobe(j: dict[str, Any]) -> tuple[datetime | None, str]:
    fmt = j.get("format") or {}
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    for key in ("creation_time", "com.apple.quicktime.creationdate"):
        if key in tags:
            dt = _parse_meta_datetime(tags[key])
            if dt:
                return dt, f"ffprobe:{key}"
    for st in j.get("streams") or []:
        if st.get("codec_type") != "video":
            continue
        for k, v in (st.get("tags") or {}).items():
            lk = str(k).lower()
            if lk in ("creation_time", "com.apple.quicktime.creationdate"):
                dt = _parse_meta_datetime(str(v))
                if dt:
                    return dt, f"ffprobe:stream:{lk}"
    return None, ""


def _exiftool_datetime(path: Path) -> tuple[datetime | None, str]:
    if not EXIFTOOL:
        return None, ""
    try:
        cmd = [
            EXIFTOOL,
            "-s",
            "-s",
            "-DateTimeOriginal",
            "-CreateDate",
            "-MediaCreateDate",
            str(path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        if r.returncode != 0:
            return None, ""
        for line in (r.stdout or "").splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            val = val.strip()
            dt = _parse_meta_datetime(val.replace(":", "-", 2) if ":" in val[:10] else val)
            if dt:
                return dt, f"exiftool:{key.strip()}"
            dt = _parse_meta_datetime(val)
            if dt:
                return dt, f"exiftool:{key.strip()}"
    except Exception:
        pass
    return None, ""


def _weak_filename_time(path: Path) -> tuple[datetime | None, str]:
    name = path.name
    m = re.search(r"(20\d{2})(\d{2})(\d{2})[_-]?(\d{2})(\d{2})(\d{2})", name)
    if m:
        try:
            return datetime(*map(int, m.groups())), "filename_pattern_ymdhms"
        except ValueError:
            pass
    return None, ""


def _resolve_captured_at(path: Path, j: dict[str, Any] | None) -> tuple[datetime | None, str]:
    if j:
        dt, src = _extract_time_from_ffprobe(j)
        if dt:
            return dt, src
    dt, src = _exiftool_datetime(path)
    if dt:
        return dt, src
    try:
        mt = path.stat().st_mtime
        return datetime.fromtimestamp(mt), "filesystem_mtime"
    except OSError:
        pass
    dt, src = _weak_filename_time(path)
    if dt:
        return dt, src
    return None, "unreadable"


def _parse_iso_datetime(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _local_wall(dt: datetime | None) -> tuple[int, int, str] | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        local = dt
    else:
        local = dt.astimezone()
    return local.weekday(), local.hour, local.isoformat()


def _orientation_wh(w: int, h: int) -> str:
    if w <= 0 or h <= 0:
        return "unknown"
    if h > w * 1.05:
        return "portrait"
    if w > h * 1.05:
        return "landscape"
    return "square"


def _timelapse_heuristic(j: dict[str, Any] | None, tag_blob: str) -> bool:
    blob = tag_blob.lower()
    if "timelapse" in blob or "time-lapse" in blob or "time lapse" in blob:
        return True
    if j:
        fmt = j.get("format") or {}
        tags = json.dumps(fmt.get("tags") or {}).lower()
        if "timelapse" in tags:
            return True
    return False


def _iphone_likely(tag_blob: str, path: Path) -> bool:
    b = tag_blob.lower()
    return "iphone" in b or "iphone" in path.as_posix().lower()


def _quality_hint(w: int, h: int, br: int) -> str:
    if min(w, h) >= 2160:
        return "premium"
    if br > 40_000_000:
        return "high_bitrate"
    return "normal"


def _should_skip_path(path: Path) -> bool:
    parts = path.parts
    if any(p in (".Trash", "Library", "Photo Booth Library") for p in parts):
        return True
    skip_fragments = ("私人别碰", "protected", "do not touch", "DONOTTOUCH")
    full = str(path)
    return any(x.lower() in full.lower() for x in skip_fragments)


@dataclass
class Row:
    path: str
    source_root: str
    media_type: str
    suffix: str
    size_bytes: int
    duration_sec: float
    width: int
    height: int
    orientation: str
    captured_at: str
    captured_weekday: str
    captured_hour: int
    captured_time_source: str
    captured_date: str
    route_group: str
    sequence_index: int
    sort_key: str
    route_order_source: str
    classification: str
    usable_for: str
    reason: str
    ffprobe_ok: bool
    iphone_likely: bool
    quality_hint: str


def _long_row_sort_key_and_source(row: Row, path: Path) -> tuple[str, str]:
    ca = (row.captured_at or "").strip()
    dt = _parse_iso_datetime(ca) if ca else None
    if dt is not None:
        local = dt.astimezone() if dt.tzinfo else dt
        sk = f"{local.strftime('%Y-%m-%dT%H:%M:%S.%f')}|{path}"
        return sk, "captured_at"
    try:
        mt = path.stat().st_mtime
        local = datetime.fromtimestamp(mt)
        sk = f"{local.strftime('%Y-%m-%dT%H:%M:%S.%f')}|{path}"
        return sk, "mtime"
    except OSError:
        pass
    sk = f"1970-01-01T00:00:00.000000|{path}"
    return sk, "path_fallback"


def _finalize_long_rows_chronological(long_rows: list[Row]) -> list[Row]:
    enriched: list[tuple[str, Row]] = []
    for r in long_rows:
        p = Path(r.path)
        sk, ros = _long_row_sort_key_and_source(r, p)
        enriched.append((sk, replace(r, sort_key=sk, route_order_source=ros)))
    enriched.sort(key=lambda x: x[0])
    return [replace(r2, sequence_index=i) for i, (_sk, r2) in enumerate(enriched)]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)

    rows: list[Row] = []
    skipped = 0
    unreadable_time = 0
    rejected_long_candidates: list[dict[str, Any]] = []
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for root in FORMAL_ROOTS:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob("*"):
                try:
                    if not path.is_file():
                        continue
                    name = path.name
                    if name.startswith(".") or name.startswith("._"):
                        skipped += 1
                        continue
                    if _should_skip_path(path):
                        skipped += 1
                        continue
                    suf = path.suffix.lower()
                    if suf not in VIDEO_EXT | IMAGE_EXT:
                        continue
                    try:
                        sz = path.stat().st_size
                    except OSError:
                        skipped += 1
                        continue

                    media_type = "video" if suf in VIDEO_EXT else "image"
                    duration_sec = 0.0
                    width = height = 0
                    ffprobe_ok = False
                    j: dict[str, Any] | None = None
                    tag_blob = ""

                    if media_type == "video":
                        if sz < MIN_VIDEO_BYTES:
                            skipped += 1
                            continue
                        j = _ffprobe_json(path)
                        if not j:
                            skipped += 1
                            continue
                        ffprobe_ok = True
                        fmt = j.get("format") or {}
                        tag_blob = json.dumps(fmt.get("tags") or {})
                        try:
                            duration_sec = float(fmt.get("duration") or 0.0)
                        except Exception:
                            duration_sec = 0.0
                        for st in j.get("streams") or []:
                            if st.get("codec_type") == "video":
                                width = int(st.get("width") or 0)
                                height = int(st.get("height") or 0)
                                tag_blob += json.dumps(st.get("tags") or {})
                                break
                        if duration_sec <= 0:
                            skipped += 1
                            continue
                    else:
                        j = _ffprobe_json(path)
                        if j:
                            ffprobe_ok = True
                            for st in j.get("streams") or []:
                                if st.get("codec_type") == "video":
                                    width = int(st.get("width") or 0)
                                    height = int(st.get("height") or 0)
                                    break

                    cap_dt, cap_src = _resolve_captured_at(path, j if media_type == "video" else None)
                    wall = _local_wall(cap_dt)
                    if wall is None:
                        unreadable_time += 1
                        wday_name = "unknown"
                        hour = -1
                        cap_iso = ""
                    else:
                        wd, hour, cap_iso = wall
                        wday_name = weekday_names[wd]

                    captured_date = ""
                    route_group = ""
                    if cap_dt is not None:
                        local_cap = cap_dt.astimezone() if cap_dt.tzinfo else cap_dt
                        captured_date = local_cap.date().isoformat()
                        route_group = captured_date

                    orientation = _orientation_wh(width, height)
                    tl = _timelapse_heuristic(j if media_type == "video" else None, tag_blob)
                    iphone = _iphone_likely(tag_blob + path.name, path)

                    br = 0
                    if j:
                        try:
                            br = int(float((j.get("format") or {}).get("bit_rate") or 0))
                        except Exception:
                            br = 0
                    qh = _quality_hint(width, height, br)

                    pol_v = is_valid_nyc_long_source(
                        path,
                        ffprobe_meta=j if media_type == "video" else None,
                        source_info={"_timelapse_heuristic": bool(tl)} if media_type == "video" else None,
                    )

                    long_ok = (
                        media_type == "video"
                        and wall is not None
                        and wall[0] <= 5
                        and 5 <= wall[1] < 18
                        and duration_sec > 30
                        and ffprobe_ok
                        and bool(pol_v.get("long_allowed"))
                    )

                    if media_type == "video" and not bool(pol_v.get("long_allowed")):
                        rejected_long_candidates.append(
                            {
                                "path": str(path.resolve()),
                                "reason": pol_v.get("reason"),
                                "reject_reasons": pol_v.get("reject_reasons") or [],
                                "width": pol_v.get("width") or width,
                                "height": pol_v.get("height") or height,
                                "aspect_ratio": pol_v.get("aspect_ratio") or 0.0,
                                "source_type": pol_v.get("source_type"),
                                "shorts_allowed": bool(pol_v.get("shorts_allowed")),
                                "time_window_ok": bool(wall and wall[0] <= 5 and 5 <= wall[1] < 18),
                            }
                        )

                    if long_ok:
                        classification = "nyc_long_driving_candidate"
                        usable = "nyc_long_music_ambient"
                        reason = "weekday_mon_sat_05_18_landscape_video_gt30s_nyc_long_channel_source_policy_v1"
                    else:
                        classification = "nyc_shorts_candidate"
                        reasons = []
                        if media_type == "image":
                            reasons.append("image")
                        if wall is None:
                            reasons.append("bad_or_missing_time")
                        elif wall[0] == 6:
                            reasons.append("sunday")
                        elif not (5 <= wall[1] < 18):
                            reasons.append("outside_05_18_window")
                        if media_type == "video":
                            if duration_sec <= 30:
                                reasons.append("duration_le_30")
                            if not bool(pol_v.get("long_allowed")):
                                for rr in pol_v.get("reject_reasons") or []:
                                    reasons.append(str(rr))
                                if pol_v.get("long_candidate_uncertain"):
                                    reasons.append("long_candidate_uncertain")
                            if tl:
                                reasons.append("timelapse_heuristic")
                        reason = ";".join(reasons) if reasons else "non_long_policy"

                    rows.append(
                        Row(
                            path=str(path.resolve()),
                            source_root=str(root),
                            media_type=media_type,
                            suffix=suf,
                            size_bytes=sz,
                            duration_sec=round(duration_sec, 3),
                            width=width,
                            height=height,
                            orientation=orientation,
                            captured_at=cap_iso,
                            captured_weekday=wday_name,
                            captured_hour=hour,
                            captured_time_source=cap_src,
                            captured_date=captured_date,
                            route_group=route_group,
                            sequence_index=-1,
                            sort_key="",
                            route_order_source="",
                            classification=classification,
                            usable_for="shorts_pool" if classification == "nyc_shorts_candidate" else usable,
                            reason=reason,
                            ffprobe_ok=ffprobe_ok,
                            iphone_likely=iphone,
                            quality_hint=qh,
                        )
                    )
                except OSError:
                    skipped += 1
        except OSError:
            continue

    long_rows = [r for r in rows if r.classification == "nyc_long_driving_candidate"]
    shorts_rows = [r for r in rows if r.classification == "nyc_shorts_candidate"]
    long_final = _finalize_long_rows_chronological(long_rows)
    long_by_path = {r.path: r for r in long_final}

    chron_sorted = len(long_final) <= 1 or all(
        long_final[i].sort_key <= long_final[i + 1].sort_key for i in range(len(long_final) - 1)
    )
    first_long = long_final[0] if long_final else None
    last_long = long_final[-1] if long_final else None

    rej_by = summarize_rejections(rejected_long_candidates)
    long_header = policy_header_counts(
        accepted=len(long_final),
        rejected=len(rejected_long_candidates),
        by_reason=rej_by,
    )

    index_items: list[dict[str, Any]] = []
    for x in rows:
        if x.classification == "nyc_long_driving_candidate":
            index_items.append(asdict(long_by_path[x.path]))
        else:
            index_items.append(asdict(x))

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
        **long_header,
        "scan_roots": [str(r) for r in FORMAL_ROOTS],
        "total": len(rows),
        "long_candidates": len(long_final),
        "shorts_candidates": len(shorts_rows),
        "rejected_long_candidates_count": len(rejected_long_candidates),
        "rejected_by_reason": rej_by,
        "skipped": skipped,
        "unreadable_time_count": unreadable_time,
        "long_sources_sorted_chronologically": chron_sorted,
        "first_long_source_captured_at": first_long.captured_at if first_long else "",
        "last_long_source_captured_at": last_long.captured_at if last_long else "",
        "first_long_source_path": first_long.path if first_long else "",
        "last_long_source_path": last_long.path if last_long else "",
        "items": index_items,
    }
    INDEX_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    LONG_JSON.write_text(
        json.dumps(
            {
                "generated_at": payload["generated_at"],
                **long_header,
                "rejected_long_candidates": rejected_long_candidates[:500],
                "items": [asdict(x) for x in long_final],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    SHORTS_JSON.write_text(
        json.dumps(
            {"generated_at": payload["generated_at"], "items": [asdict(x) for x in shorts_rows]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if index_items:
        fieldnames = sorted({k for it in index_items for k in it.keys()})
        with INDEX_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for it in index_items:
                w.writerow({k: it.get(k, "") for k in fieldnames})

    by_root: dict[str, int] = {}
    for r in rows:
        by_root[r.source_root] = by_root.get(r.source_root, 0) + 1
    by_wd: dict[str, int] = {}
    for r in rows:
        by_wd[r.captured_weekday] = by_wd.get(r.captured_weekday, 0) + 1
    by_cls: dict[str, int] = {}
    for r in rows:
        by_cls[r.classification] = by_cls.get(r.classification, 0) + 1

    videos = sum(1 for r in rows if r.media_type == "video")
    images = sum(1 for r in rows if r.media_type == "image")

    report = f"""# NYC source routing policy v1 — classification report

- **Generated**: {payload["generated_at"]}
- **Scan roots**: {", ".join(str(r) for r in FORMAL_ROOTS)}

## Counts

| Metric | Value |
|--------|------:|
| Total indexed | {len(rows)} |
| Videos | {videos} |
| Images | {images} |
| Long driving candidates | {len(long_final)} |
| Shorts candidates | {len(shorts_rows)} |
| Skipped | {skipped} |
| Unreadable capture time | {unreadable_time} |

## Long video route order

- **long sources are sorted chronologically**: {chron_sorted}
- **sort rule**: `captured_at` → filesystem `mtime` → `path_fallback` (unified `sort_key` per row)
- **first_long_source_captured_at**: `{first_long.captured_at if first_long else ""}`
- **last_long_source_captured_at**: `{last_long.captured_at if last_long else ""}`
- **first_long_source_path**: `{first_long.path if first_long else ""}`
- **last_long_source_path**: `{last_long.path if last_long else ""}`

## By source_root

{chr(10).join(f"- `{k}`: {v}" for k, v in sorted(by_root.items()))}

## By weekday

{chr(10).join(f"- {k}: {v}" for k, v in sorted(by_wd.items()))}

## By classification

{chr(10).join(f"- {k}: {v}" for k, v in sorted(by_cls.items()))}

## NYC Long Channel Source Policy v1

- **Policy version**: `{LONG_SOURCE_POLICY_VERSION}`
- **Long accepted (written to nyc_long_driving_sources.json)**: {len(long_final)}
- **Long rejected (policy / geometry / shorts path / walking)**: {len(rejected_long_candidates)}
- **Rejected by reason (top)**: `{json.dumps(rej_by, ensure_ascii=False)[:1200]}`
- **Accepted preview (first paths)**: `{", ".join(x.path for x in long_final[:6]) or "(none)"}`
- **Accepted-but-suspicious path tokens (walk/vertical/shorts/portrait in path)**: `{json.dumps([x.path for x in long_final if any(t in x.path.lower() for t in ("walk_", "_walk", "/walk", "vertical", "shorts_cl", "portrait"))][:10], ensure_ascii=False)}`

## Outputs

- `{INDEX_JSON}`
- `{INDEX_CSV}`
- `{LONG_JSON}`
- `{SHORTS_JSON}`

## Rules (v1)

- **Long NYC**: video only; Mon–Sat local; 05:00 ≤ time < 18:00; duration > 30s; landscape; not timelapse heuristic; ffprobe OK; roots: formal iphone inbox + SV_CACHE/inbox only.
- **Long NYC JSON order**: `nyc_long_driving_sources.json` items are sorted by capture time (then mtime, then path); each row has `sequence_index`, `sort_key`, `route_order_source`, `captured_date`, `route_group` (YYYY-MM-DD).
- **Shorts**: everything else (images, Sunday, night/morning outside window, portrait, timelapse, short clips, bad time).
- No files moved or deleted.

"""
    REPORT_MD.write_text(report, encoding="utf-8")
    print(f"Wrote {INDEX_JSON}")
    print(f"Wrote {LONG_JSON} ({len(long_final)} items)")
    print(f"Wrote {SHORTS_JSON} ({len(shorts_rows)} items)")
    print(f"Report {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
