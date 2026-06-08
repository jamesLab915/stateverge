#!/usr/bin/env python3
"""Upload NYC_AUTO package to YouTube. Default private; public needs --allow-public."""
from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import (
    CODE_ROOT,
    LOCAL_LOG_DIR,
    NYC_LOGS,
    NYC_ROOT,
    log_lines,
    path_matches_test_asset_marker,
    read_project_source_path,
)
from youtube_scopes import SCOPES
from publish_review_queue import write_private_upload_review
from youtube_token_paths import (
    OFFICIAL_LONG_TOKEN_PATH,
    long_token_invalid_grant_fix_command,
    resolve_client_secrets,
    resolve_long_form_upload_token,
)

DEFAULT_SECRETS = resolve_client_secrets(None)
DEFAULT_TOKEN = OFFICIAL_LONG_TOKEN_PATH
LOCAL_HISTORY = CODE_ROOT / "data" / "youtube" / "upload_history.csv"
NYC_HISTORY = NYC_ROOT / "logs" / "youtube_upload_history.csv"

TITLE_MAX = 100
RETRYABLE_HTTP = {500, 502, 503, 504}
MAX_UPLOAD_ATTEMPTS = 5
MANUAL_LONG_THRESHOLD_SEC = 3300.0

HISTORY_FIELDS = [
    "uploaded_at",
    "project_id",
    "package_dir",
    "video_path",
    "video_id",
    "privacy",
    "title",
    "status",
    "error",
]


@dataclass
class UploadResult:
    ok: bool
    status: str
    video_id: str = ""
    error: str = ""
    title: str = ""
    privacy: str = ""
    error_type: str = ""
    token_path_used: str = ""
    client_secrets_path_used: str = ""
    upload_channel_title: str = ""
    upload_channel_id: str = ""
    metadata_used: bool = False
    metadata_path: str = ""
    title_used: str = ""
    description_used: str = ""
    tags_used: list[str] = field(default_factory=list)
    metadata_generator_version: str = ""
    audio_mode: str = ""
    audio_policy: Optional[dict[str, Any]] = None
    privacy_used: str = ""
    channel_guard_status: str = ""
    dedupe_status: str = ""
    review_queue_path: str = ""
    review_queue_md_path: str = ""
    agent_uploaded: bool = False
    human_review_required: bool = False
    forced_private: bool = False
    agent_warnings: list[str] = field(default_factory=list)
    thumbnail_text: str = ""
    content_hash: str = ""
    dedupe_key: str = ""
    upload_result_path: str = ""
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0


def _today_logs() -> tuple[Path, Path]:
    d = datetime.now().strftime("%Y-%m-%d")
    nyc_daily = NYC_LOGS / f"youtube_upload_{d}.log"
    local_daily = LOCAL_LOG_DIR / f"youtube_upload_{d}.log"
    return nyc_daily, local_daily


def nyc_daily_log_optional(p: Path) -> Optional[Path]:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if NYC_ROOT.is_dir():
            return p
    except OSError:
        pass
    return None


def _youtube_log(messages: list[str]) -> None:
    nyc_d, _ = _today_logs()
    log_lines("youtube_upload", messages, nyc_daily_log_optional(nyc_d))


def _write_detail_logs(block: str) -> None:
    LOCAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    nyc_d, loc_d = _today_logs()
    for target in (loc_d, nyc_d):
        try:
            if target is nyc_d and not NYC_ROOT.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(block + "\n")
        except OSError:
            continue


def normalize_privacy(privacy: str, allow_public: bool) -> tuple[Optional[str], Optional[str]]:
    p = (privacy or "private").strip().lower()
    if p not in ("private", "unlisted", "public"):
        return None, "invalid_privacy"
    if p == "public" and not allow_public:
        return None, "public_requires_allow_public_flag"
    return p, None


def _resolve_video_path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def load_successful_video_paths() -> set[str]:
    out: set[str] = set()
    for hist in (LOCAL_HISTORY, NYC_HISTORY):
        if not hist.is_file():
            continue
        try:
            with hist.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if (row.get("status") or "").strip().lower() != "success":
                        continue
                    vp = (row.get("video_path") or "").strip()
                    if vp:
                        out.add(vp)
        except OSError:
            continue
    return out


def append_history_row(row: dict[str, str]) -> None:
    paths: list[Path] = [LOCAL_HISTORY]
    if NYC_ROOT.is_dir():
        paths.append(NYC_HISTORY)
    for hp in paths:
        try:
            hp.parent.mkdir(parents=True, exist_ok=True)
            new_file = not hp.is_file()
            with hp.open("a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
                if new_file:
                    w.writeheader()
                w.writerow({k: row.get(k, "") for k in HISTORY_FIELDS})
        except OSError:
            continue


def _ffprobe_wh_duration(path: Path) -> tuple[int, int, float]:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if r.returncode != 0:
            return 0, 0, 0.0
        data = json.loads(r.stdout or "{}")
        streams = data.get("streams") or [{}]
        w = int((streams[0] or {}).get("width") or 0)
        h = int((streams[0] or {}).get("height") or 0)
        fmt = data.get("format") or {}
        dur = float((fmt.get("duration") or 0) or 0.0)
        return w, h, dur
    except (json.JSONDecodeError, ValueError, subprocess.TimeoutExpired, OSError):
        return 0, 0, 0.0


def _ffprobe_duration_video(path: Path) -> float:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if r.returncode != 0:
            return 0.0
        return float((r.stdout or "").strip() or 0.0)
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return 0.0


def read_first_nonempty_line(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s:
                return s
    except OSError:
        pass
    return ""


def read_full_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def parse_tags_file(path: Path) -> list[str]:
    raw = read_full_text(path)
    if not raw:
        return []
    parts = [x.strip() for x in raw.replace("\n", ",").split(",")]
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if not p or p.lower() in seen:
            continue
        seen.add(p.lower())
        out.append(p)
    return out


def _load_youtube_metadata_json(package_dir: Path) -> dict[str, Any]:
    p = package_dir / "youtube_metadata.json"
    try:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def resolve_package_snippet(
    package_dir: Path,
    video_path: Path,
    *,
    title: Optional[str],
    description: Optional[str],
    tags_str: Optional[str],
    project_id: str,
    use_ai_metadata: bool = False,
) -> tuple[str, str, list[str], dict[str, Any]]:
    """Resolve title, description, tags. CLI non-empty overrides metadata file."""
    info: dict[str, Any] = {
        "metadata_used": False,
        "metadata_path": str((package_dir / "youtube_metadata.json").resolve()),
        "metadata_generator_version": "",
        "metadata_warnings": [],
    }
    meta_path = package_dir / "youtube_metadata.json"
    yt_meta = _load_youtube_metadata_json(package_dir)

    def tags_from_meta(m: dict[str, Any]) -> list[str]:
        t = m.get("tags")
        if not isinstance(t, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for x in t:
            s = str(x).strip()
            if not s or s.lower() in seen:
                continue
            seen.add(s.lower())
            out.append(s)
        return out

    missing_core = (not meta_path.is_file()) or (
        not (str(yt_meta.get("title") or "").strip()) and not (str(yt_meta.get("description") or "").strip())
    )
    if missing_core:
        try:
            from metadata_generator import ensure_youtube_metadata_file, infer_package_video_type

            vtype = infer_package_video_type(package_dir)
            yt_meta, outp = ensure_youtube_metadata_file(
                package_dir,
                video_path,
                video_type=vtype,
                use_ai_metadata=use_ai_metadata,
                dry_run=False,
            )
            info["metadata_path"] = str(outp)
            yt_meta = _load_youtube_metadata_json(package_dir)
        except Exception as exc:  # noqa: BLE001 — fail-open
            info["metadata_warnings"].append(f"metadata_generation_failed:{type(exc).__name__}")
            yt_meta = {}

    if isinstance(yt_meta, dict) and yt_meta.get("metadata_generated"):
        info["metadata_used"] = True
        info["metadata_generator_version"] = str(yt_meta.get("metadata_generator_version") or "")
    elif isinstance(yt_meta, dict) and (
        (str(yt_meta.get("title") or "").strip()) or (str(yt_meta.get("description") or "").strip())
    ):
        info["metadata_used"] = True

    if title is not None and str(title).strip():
        tit = str(title).strip()
    else:
        tit = (yt_meta.get("title") or "").strip() if isinstance(yt_meta, dict) else ""
        if not tit:
            tit = read_first_nonempty_line(package_dir / "title_options.txt")
        if not tit:
            tit = f"NYC_AUTO {project_id}"[:TITLE_MAX]

    if not tit.strip():
        tit = "StateVerge NYC Video"[:TITLE_MAX]

    if description is not None and str(description).strip():
        desc = str(description).strip()
    else:
        desc = (yt_meta.get("description") or "").strip() if isinstance(yt_meta, dict) else ""
        if not desc:
            desc = read_full_text(package_dir / "description.txt")

    if tags_str is not None and str(tags_str).strip():
        raw_tags = [x.strip() for x in str(tags_str).split(",") if x.strip()]
        seen2: set[str] = set()
        tags: list[str] = []
        for t in raw_tags:
            if t.lower() in seen2:
                continue
            seen2.add(t.lower())
            tags.append(t)
    else:
        tags = tags_from_meta(yt_meta) if isinstance(yt_meta, dict) else []
        if not tags:
            tags = parse_tags_file(package_dir / "tags.txt")

    if isinstance(yt_meta, dict):
        info["audio_mode"] = str(yt_meta.get("audio_mode") or "")
        ap0 = yt_meta.get("audio_policy")
        info["audio_policy"] = ap0 if isinstance(ap0, dict) else None

    return tit, desc, tags, info


def pick_thumbnail(package_dir: Path, explicit: Optional[Path]) -> Optional[Path]:
    if explicit:
        e = explicit.expanduser().resolve()
        return e if e.is_file() else None
    for name in ("thumbnail_text.jpg", "thumbnail_base.jpg"):
        p = package_dir / name
        if p.is_file():
            return p
    return None


def _load_credentials(token_path: Path, client_secrets: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        from google.auth.exceptions import RefreshError
    except ImportError:  # pragma: no cover
        RefreshError = type("RefreshError", (Exception,), {})  # type: ignore[misc,assignment]

    token_path = token_path.expanduser().resolve()
    client_secrets = client_secrets.expanduser().resolve()
    if not client_secrets.is_file():
        raise FileNotFoundError(f"Missing client secrets: {client_secrets}")
    if not token_path.is_file():
        raise FileNotFoundError(f"Missing token. Run youtube_auth_init.py first: {token_path}")
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
    except RefreshError as exc:
        low = str(exc).lower()
        if "invalid_grant" in low:
            raise RuntimeError(
                "YOUTUBE_TOKEN_INVALID_GRANT\n"
                f"token_path_used={token_path}\n"
                f"fix_command: {long_token_invalid_grant_fix_command()}"
            ) from exc
        raise
    except Exception as exc:
        low = str(exc).lower()
        if "invalid_grant" in low:
            raise RuntimeError(
                "YOUTUBE_TOKEN_INVALID_GRANT\n"
                f"token_path_used={token_path}\n"
                f"fix_command: {long_token_invalid_grant_fix_command()}"
            ) from exc
        raise
    if not creds.valid:
        raise RuntimeError("Invalid OAuth credentials; re-run youtube_auth_init.py")
    return creds


def _mine_channel_title_id(youtube: Any) -> tuple[str, str]:
    resp = youtube.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items") or []
    if not items:
        return "", ""
    it0 = items[0]
    cid = str(it0.get("id") or "")
    sn = it0.get("snippet") or {}
    title = str(sn.get("title") or "")
    return title, cid


def _execute_video_insert_with_retries(
    youtube: Any,
    body: dict[str, Any],
    media_path: Path,
    notify_subscribers: bool,
) -> dict[str, Any]:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    last_err: Optional[BaseException] = None
    for attempt in range(1, MAX_UPLOAD_ATTEMPTS + 1):
        try:
            media = MediaFileUpload(str(media_path), chunksize=-1, resumable=True, mimetype="video/mp4")
            try:
                request = youtube.videos().insert(
                    part="snippet,status",
                    body=body,
                    media_body=media,
                    notifySubscribers=notify_subscribers,
                )
            except TypeError:
                request = youtube.videos().insert(
                    part="snippet,status",
                    body=body,
                    media_body=media,
                )
            response = None
            while response is None:
                try:
                    _, response = request.next_chunk()
                except HttpError as exc:
                    st = int(exc.resp.status) if exc.resp else 0
                    if st in RETRYABLE_HTTP:
                        time.sleep(min(2 ** (attempt - 1) + random.random(), 30))
                        continue
                    raise
            if response and response.get("id"):
                return response
            raise RuntimeError("videos.insert returned empty id")
        except HttpError as exc:
            last_err = exc
            st = int(exc.resp.status) if exc.resp else 0
            if st in RETRYABLE_HTTP and attempt < MAX_UPLOAD_ATTEMPTS:
                time.sleep(min(2 ** (attempt - 1) + random.random(), 30))
                continue
            raise
        except (ConnectionError, TimeoutError, OSError) as exc:
            last_err = exc
            if attempt < MAX_UPLOAD_ATTEMPTS:
                time.sleep(min(2 ** (attempt - 1) + random.random(), 30))
                continue
            raise
    raise RuntimeError(f"upload failed after retries: {last_err}")


def set_thumbnail(youtube: Any, video_id: str, thumb: Path) -> None:
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(thumb), mimetype="image/jpeg")
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()


def add_to_playlist(youtube: Any, playlist_id: str, video_id: str) -> None:
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }
    youtube.playlistItems().insert(part="snippet", body=body).execute()


def upload_from_package_directory(
    package_dir: Path,
    *,
    privacy: str = "private",
    allow_public: bool = False,
    title: Optional[str] = None,
    description: Optional[str] = None,
    tags_str: Optional[str] = None,
    category_id: str = "22",
    made_for_kids: bool = False,
    thumbnail: Optional[Path] = None,
    dry_run: bool = False,
    token_path: Path | None = None,
    client_secrets: Path | None = None,
    playlist_id: Optional[str] = None,
    notify_subscribers: bool = False,
    force_reupload: bool = False,
    allow_test_assets: bool = False,
    token_path_used: str = "",
    client_secrets_path_used: str = "",
    use_ai_metadata: bool = False,
    agent_upload: bool = False,
    review_queue: bool = True,
    force_private: bool = False,
    channel_type: str = "long",
    channel_guard_status: str = "",
    dedupe_status: str = "",
    dedupe_key: str = "",
    automation_job_id: str = "",
) -> UploadResult:
    package_dir = package_dir.expanduser().resolve()
    project_id = package_dir.name
    agent_warnings: list[str] = []
    orig_privacy = (privacy or "private").strip().lower()
    if agent_upload or force_private:
        if orig_privacy not in ("private", "unlisted", "public"):
            privacy = "private"
        elif orig_privacy != "private":
            agent_warnings.append("forced_private_for_agent_upload")
            privacy = "private"
        else:
            privacy = "private"
        allow_public = False
    tok = (token_path or DEFAULT_TOKEN).expanduser().resolve()
    sec = (client_secrets or DEFAULT_SECRETS).expanduser().resolve()
    token_path_used = token_path_used or str(tok)
    client_secrets_path_used = client_secrets_path_used or str(sec)
    priv, err = normalize_privacy(privacy, allow_public)
    if err:
        msg = f"REJECT privacy: {err}"
        _youtube_log([f"[{project_id}] {msg}"])
        return UploadResult(
            False,
            "rejected_privacy",
            error=err,
            privacy=privacy,
            token_path_used=token_path_used,
            client_secrets_path_used=client_secrets_path_used,
        )

    sel = package_dir / "selected_video_path.txt"
    if not sel.is_file():
        msg = "missing selected_video_path.txt"
        _youtube_log([f"[{project_id}] {msg}"])
        return UploadResult(
            False,
            "error",
            error=msg,
            token_path_used=token_path_used,
            client_secrets_path_used=client_secrets_path_used,
        )

    video_line = read_first_nonempty_line(sel)
    if not video_line:
        msg = "empty selected_video_path.txt"
        _youtube_log([f"[{project_id}] {msg}"])
        return UploadResult(
            False,
            "error",
            error=msg,
            token_path_used=token_path_used,
            client_secrets_path_used=client_secrets_path_used,
        )

    video_path = Path(video_line.strip()).expanduser()
    if not video_path.is_file():
        msg = f"video not found: {video_path}"
        _youtube_log([f"[{project_id}] {msg}"])
        return UploadResult(
            False,
            "error",
            error=msg,
            token_path_used=token_path_used,
            client_secrets_path_used=client_secrets_path_used,
        )

    pkg_s = str(package_dir).replace("\\", "/")
    is_manual_long_pkg = "manual_uploads" in pkg_s or "publish_pack/manual_uploads" in pkg_s
    manual_policy: dict[str, Any] = {}
    if is_manual_long_pkg:
        manual_policy = {
            "manual_long_upload_allowed": True,
            "min_asset_count_required": False,
            "duration_based_validation": True,
            "long_duration_threshold_sec": MANUAL_LONG_THRESHOLD_SEC,
        }
        has_manifest = (
            (package_dir / "youtube_metadata.json").is_file()
            or (package_dir / "source_assets.json").is_file()
            or (package_dir / "source_manifest.json").is_file()
        )
        if not has_manifest:
            manual_policy["warnings"] = ["manual_upload_no_source_manifest"]
            print("WARNING: manual_upload_no_source_manifest")
        vd_manual = _ffprobe_duration_video(video_path)
        manual_policy["output_duration_sec"] = round(vd_manual, 3)
        manual_policy["duration_validation_passed"] = vd_manual >= MANUAL_LONG_THRESHOLD_SEC
        print(json.dumps({"manual_long_upload_policy": manual_policy}, indent=2, ensure_ascii=False))
        if vd_manual < MANUAL_LONG_THRESHOLD_SEC:
            msg = f"long_duration_too_short: video={vd_manual:.1f}s < {MANUAL_LONG_THRESHOLD_SEC:.0f}s"
            _youtube_log([f"[{project_id}] {msg}"])
            return UploadResult(
                False,
                "error",
                error=msg,
                token_path_used=token_path_used,
                client_secrets_path_used=client_secrets_path_used,
            )

    source_hint = read_project_source_path(project_id)
    ct_norm = str(channel_type or "").strip().lower()
    is_short_upload = ct_norm in ("short", "shorts", "short_form", "nyc_short")
    if is_short_upload:
        try:
            from channel_guard import validate_youtube_shorts_file

            ok_sf, sf_reason, sf_probe = validate_youtube_shorts_file(video_path)
        except Exception as exc:  # noqa: BLE001
            ok_sf, sf_reason, sf_probe = False, f"shorts_format_check_failed:{exc!r}", {}
        if not ok_sf:
            msg = f"shorts_format_rejected:{sf_reason}"
            _youtube_log([f"[{project_id}] {msg} probe={sf_probe}"])
            return UploadResult(
                False,
                "blocked_shorts_format",
                error=msg,
                token_path_used=token_path_used,
                client_secrets_path_used=client_secrets_path_used,
            )

    if not allow_test_assets and path_matches_test_asset_marker(
        str(package_dir),
        project_id,
        str(video_path),
        source_hint,
    ):
        print("BLOCKED_TEST_OR_PLACEHOLDER_ASSET")
        _youtube_log([f"[{project_id}] BLOCKED_TEST_OR_PLACEHOLDER_ASSET"])
        return UploadResult(
            False,
            "blocked_test_asset",
            error="BLOCKED_TEST_OR_PLACEHOLDER_ASSET",
            token_path_used=token_path_used,
            client_secrets_path_used=client_secrets_path_used,
        )

    vkey = _resolve_video_path_key(video_path)
    if not force_reupload and vkey in load_successful_video_paths():
        msg = "skip duplicate (already in upload_history as success)"
        _youtube_log([f"[{project_id}] {msg} {vkey}"])
        return UploadResult(
            False,
            "skipped_duplicate",
            error=msg,
            token_path_used=token_path_used,
            client_secrets_path_used=client_secrets_path_used,
        )

    tit, desc, tags, finfo = resolve_package_snippet(
        package_dir,
        video_path,
        title=title,
        description=description,
        tags_str=tags_str,
        project_id=project_id,
        use_ai_metadata=use_ai_metadata,
    )
    if finfo.get("metadata_warnings") and tit.startswith("NYC_AUTO "):
        tit = "StateVerge NYC Video"[:TITLE_MAX]
    if is_short_upload and "#shorts" not in tit.lower():
        suffix = " #Shorts"
        if len(tit) + len(suffix) <= TITLE_MAX:
            tit = f"{tit}{suffix}"
    if is_short_upload and "#shorts" not in desc.lower():
        desc = f"{desc.rstrip()}\n\n#Shorts".strip()
    if len(tit) > TITLE_MAX:
        _youtube_log([f"[{project_id}] WARN title_truncated {len(tit)}->{TITLE_MAX}"])
        tit = tit[:TITLE_MAX]

    thumb = pick_thumbnail(package_dir, thumbnail)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detail = (
        f"[{ts}] package={package_dir}\n"
        f"  video={video_path}\n"
        f"  privacy={priv} dry_run={dry_run}\n"
        f"  title={tit}\n"
        f"  tags({len(tags)}) thumbnail={thumb}\n"
    )
    _write_detail_logs(detail)
    print(detail.strip())

    if dry_run:
        return UploadResult(
            True,
            "dry_run",
            title=tit,
            privacy=priv or "",
            token_path_used=token_path_used,
            client_secrets_path_used=client_secrets_path_used,
            metadata_used=bool(finfo.get("metadata_used")),
            metadata_path=str(finfo.get("metadata_path") or ""),
            title_used=tit,
            description_used=desc,
            tags_used=list(tags[:500]),
            metadata_generator_version=str(finfo.get("metadata_generator_version") or ""),
            audio_mode=str(finfo.get("audio_mode") or ""),
            audio_policy=finfo.get("audio_policy") if isinstance(finfo.get("audio_policy"), dict) else None,
            privacy_used=priv or "",
            agent_uploaded=bool(agent_upload),
            forced_private=any("forced_private" in x for x in agent_warnings),
            agent_warnings=list(agent_warnings),
        )

    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        msg = "missing google API libs; pip install -r requirements-youtube.txt"
        _youtube_log([f"[{project_id}] {msg}"])
        return UploadResult(
            False,
            "error",
            error=msg,
            token_path_used=token_path_used,
            client_secrets_path_used=client_secrets_path_used,
        )

    try:
        creds = _load_credentials(tok, sec)
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        ch_title, ch_id = _mine_channel_title_id(youtube)
        body = {
            "snippet": {
                "title": tit,
                "description": desc,
                "tags": tags[:500],
                "categoryId": str(category_id),
            },
            "status": {
                "privacyStatus": priv,
                "selfDeclaredMadeForKids": bool(made_for_kids),
            },
        }
        resp = _execute_video_insert_with_retries(youtube, body, video_path, notify_subscribers)
        vid = str(resp.get("id") or "")

        if thumb and thumb.is_file():
            try:
                set_thumbnail(youtube, vid, thumb)
            except HttpError as exc:
                _youtube_log([f"[{project_id}] WARN thumbnail: {exc}"])
            except OSError as exc:
                _youtube_log([f"[{project_id}] WARN thumbnail: {exc}"])

        if playlist_id:
            try:
                add_to_playlist(youtube, playlist_id, vid)
            except HttpError as exc:
                _youtube_log([f"[{project_id}] WARN playlist: {exc}"])

        append_history_row(
            {
                "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                "project_id": project_id,
                "package_dir": str(package_dir),
                "video_path": vkey,
                "video_id": vid,
                "privacy": priv or "",
                "title": tit,
                "status": "success",
                "error": "",
            }
        )
        _youtube_log([f"[{project_id}] OK video_id={vid}"])
        fw, fh, fdur = _ffprobe_wh_duration(video_path)
        ymeta = _load_youtube_metadata_json(package_dir)
        thumb_txt = str(ymeta.get("thumbnail_text") or finfo.get("thumbnail_text") or "")
        raw_tags = ymeta.get("hashtags")
        if not isinstance(raw_tags, list):
            raw_tags = ymeta.get("hash_tags") if isinstance(ymeta.get("hash_tags"), list) else []
        review_json_path = ""
        review_md_path = ""
        rwarn: list[str] = list(agent_warnings)
        jid = (automation_job_id or "").strip()
        if review_queue and vid and jid:
            try:
                from review_queue import add_review_item

                ct = str(channel_type or "").lower()
                video_type = "long" if ct in ("long", "long_form", "nyc_long") else "short"
                channel_label = "NYC_LONG" if video_type == "long" else "SHORTS"
                qpath = add_review_item(
                    video_type=video_type,
                    channel=channel_label,
                    local_video_path=str(video_path),
                    youtube_video_id=vid,
                    youtube_url=f"https://www.youtube.com/watch?v={vid}",
                    privacy_status=str(priv or "unlisted"),
                    title=tit,
                    job_id=jid,
                    result_path=str(package_dir / "upload_result.json"),
                    warnings=rwarn,
                )
                review_json_path = str(qpath)
            except OSError as exc:
                rwarn.append(f"review_queue_write_failed:{exc!r}")
            except Exception as exc:  # noqa: BLE001
                rwarn.append(f"review_queue_write_failed:{type(exc).__name__}:{exc!r}")
        elif agent_upload and review_queue and vid:
            try:
                payload = {
                    "review_status": "needs_human_review",
                    "privacy": "private",
                    "youtube_video_id": vid,
                    "youtube_url": f"https://www.youtube.com/watch?v={vid}",
                    "channel_type": channel_type,
                    "channel_title": ch_title,
                    "channel_id": ch_id,
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    "source_video": str(video_path),
                    "package_dir": str(package_dir),
                    "title": tit,
                    "description": desc,
                    "tags": list(tags[:80]),
                    "hashtags": raw_tags[:40] if isinstance(raw_tags, list) else [],
                    "thumbnail_text": thumb_txt,
                    "duration_sec": float(fdur),
                    "width": int(fw),
                    "height": int(fh),
                    "audio_mode": str(finfo.get("audio_mode") or ""),
                    "metadata_path": str(finfo.get("metadata_path") or str(package_dir / "youtube_metadata.json")),
                    "upload_result_path": str(package_dir / "upload_result.json"),
                    "dedupe_key": dedupe_key or "",
                    "dedupe_status": dedupe_status or "",
                    "content_hash": "",
                    "agent_uploaded": True,
                    "human_review_required": True,
                    "allowed_next_actions": [
                        "approve_in_youtube_studio",
                        "edit_title",
                        "edit_description",
                        "replace_thumbnail",
                        "keep_private",
                        "delete_manually_if_needed",
                    ],
                    "warnings": rwarn,
                }
                qjp, qmp = write_private_upload_review(vid, payload, warnings=rwarn)
                review_json_path, review_md_path = str(qjp), str(qmp)
            except OSError as exc:
                rwarn.append(f"review_queue_write_failed:{exc!r}")

        return UploadResult(
            True,
            "success",
            video_id=vid,
            title=tit,
            privacy=priv or "",
            token_path_used=token_path_used,
            client_secrets_path_used=client_secrets_path_used,
            upload_channel_title=ch_title,
            upload_channel_id=ch_id,
            metadata_used=bool(finfo.get("metadata_used")),
            metadata_path=str(finfo.get("metadata_path") or ""),
            title_used=tit,
            description_used=desc,
            tags_used=list(tags[:500]),
            metadata_generator_version=str(finfo.get("metadata_generator_version") or ""),
            audio_mode=str(finfo.get("audio_mode") or ""),
            audio_policy=finfo.get("audio_policy") if isinstance(finfo.get("audio_policy"), dict) else None,
            privacy_used=str(priv or "private"),
            channel_guard_status=channel_guard_status or "not_evaluated_in_youtube_upload",
            dedupe_status=dedupe_status or "not_evaluated_in_youtube_upload",
            review_queue_path=review_json_path,
            review_queue_md_path=review_md_path,
            agent_uploaded=bool(agent_upload),
            human_review_required=bool(bool(review_json_path or review_md_path) and bool(vid)),
            forced_private=any("forced_private" in x for x in agent_warnings),
            agent_warnings=list(rwarn),
            thumbnail_text=thumb_txt,
            duration_sec=float(fdur),
            width=int(fw),
            height=int(fh),
            upload_result_path=str(package_dir / "upload_result.json"),
        )
    except Exception as exc:
        err = str(exc)
        if "YOUTUBE_TOKEN_INVALID_GRANT" in err or "invalid_grant" in err.lower():
            print(err)
        _youtube_log([f"[{project_id}] FAIL {err}"])
        et = "invalid_grant" if ("invalid_grant" in err.lower() or "YOUTUBE_TOKEN_INVALID_GRANT" in err) else "upload_error"
        append_history_row(
            {
                "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                "project_id": project_id,
                "package_dir": str(package_dir),
                "video_path": vkey,
                "video_id": "",
                "privacy": priv or "",
                "title": tit,
                "status": "error",
                "error": err[:500],
            }
        )
        return UploadResult(
            False,
            "error",
            error=err,
            title=tit,
            privacy=priv or "",
            error_type=et,
            token_path_used=token_path_used,
            client_secrets_path_used=client_secrets_path_used,
            metadata_used=bool(finfo.get("metadata_used")),
            metadata_path=str(finfo.get("metadata_path") or ""),
            title_used=tit,
            description_used=desc,
            tags_used=list(tags[:500]),
            metadata_generator_version=str(finfo.get("metadata_generator_version") or ""),
            audio_mode=str(finfo.get("audio_mode") or ""),
            audio_policy=finfo.get("audio_policy") if isinstance(finfo.get("audio_policy"), dict) else None,
        )


def _write_package_upload_artifact(
    package_dir: Path,
    res: UploadResult,
    *,
    video_path: str = "",
    suggested_fix: str = "",
) -> None:
    pkg = package_dir.expanduser().resolve()
    try:
        if res.ok and res.status == "success":
            payload = {
                "ok": True,
                "video_id": res.video_id,
                "youtube_url": f"https://www.youtube.com/watch?v={res.video_id}" if res.video_id else "",
                "title": res.title,
                "privacy": res.privacy,
                "privacy_used": res.privacy_used or res.privacy,
                "token_path_used": res.token_path_used,
                "client_secrets_path_used": res.client_secrets_path_used,
                "upload_channel_title": res.upload_channel_title,
                "upload_channel_id": res.upload_channel_id,
                "metadata_used": res.metadata_used,
                "metadata_path": res.metadata_path,
                "title_used": res.title_used or res.title,
                "description_used": res.description_used,
                "tags_used": res.tags_used,
                "metadata_generator_version": res.metadata_generator_version,
                "audio_mode": res.audio_mode,
                "audio_policy": res.audio_policy,
                "channel_guard_status": res.channel_guard_status,
                "dedupe_status": res.dedupe_status,
                "dedupe_key": res.dedupe_key,
                "review_queue_path": res.review_queue_path,
                "review_queue_md_path": res.review_queue_md_path,
                "agent_uploaded": res.agent_uploaded,
                "human_review_required": res.human_review_required,
                "forced_private": res.forced_private,
                "agent_warnings": res.agent_warnings,
            }
            (pkg / "upload_result.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (pkg / "upload_error.json").unlink(missing_ok=True)
        elif not res.ok and res.status not in (
            "dry_run",
            "skipped_duplicate",
            "blocked_test_asset",
            "rejected_privacy",
        ):
            payload = {
                "ok": False,
                "error_type": res.error_type or ("invalid_grant" if "invalid_grant" in res.error.lower() else "error"),
                "token_path_used": res.token_path_used,
                "message": res.error,
                "suggested_fix": suggested_fix
                or (
                    long_token_invalid_grant_fix_command()
                    if (res.error_type == "invalid_grant" or "invalid_grant" in res.error.lower())
                    else "Review logs and package contents."
                ),
                "video_path": video_path,
            }
            (pkg / "upload_error.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package-dir", type=Path, required=True)
    ap.add_argument("--privacy", default="private", choices=("private", "unlisted", "public"))
    ap.add_argument("--allow-public", action="store_true")
    ap.add_argument("--title", default=None)
    ap.add_argument("--description", default=None)
    ap.add_argument("--tags", default=None)
    ap.add_argument("--category-id", default="22")
    ap.add_argument("--made-for-kids", action="store_true")
    ap.add_argument("--thumbnail", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--token", type=Path, default=None, help="OAuth token JSON (highest priority if set).")
    ap.add_argument(
        "--client-secrets",
        type=Path,
        default=None,
        help="OAuth client secrets JSON (default: .secrets/youtube/client_secrets.json).",
    )
    ap.add_argument("--playlist-id", default=None)
    ap.add_argument("--notify-subscribers", action="store_true")
    ap.add_argument("--force-reupload", action="store_true")
    ap.add_argument("--allow-test-assets", action="store_true")
    ap.add_argument(
        "--use-ai-metadata",
        action="store_true",
        help="Optional OpenAI refinement for title/description (requires OPENAI_API_KEY; fail-open).",
    )
    ap.add_argument(
        "--agent-upload",
        action="store_true",
        help="Agent-controlled upload: forces private, optional review queue artifacts.",
    )
    ap.add_argument(
        "--review-queue",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="With --agent-upload, write review queue JSON/MD (default: true).",
    )
    ap.add_argument(
        "--force-private",
        action="store_true",
        help="Force privacyStatus private regardless of --privacy.",
    )
    args = ap.parse_args()

    agent_upload = bool(args.agent_upload)
    review_queue = bool(args.review_queue)
    force_private = bool(args.force_private) or agent_upload
    if agent_upload and args.allow_public:
        print("ERROR: --agent-upload cannot be combined with --allow-public", file=sys.stderr)
        return 3
    if agent_upload and args.privacy != "private":
        print("WARNING: --agent-upload forces privacy=private", file=sys.stderr)
        args.privacy = "private"

    token_path, path_warnings = resolve_long_form_upload_token(args.token)
    client_secrets = resolve_client_secrets(args.client_secrets)
    token_used_str = str(token_path)
    secrets_used_str = str(client_secrets)
    pkg = args.package_dir.expanduser().resolve()
    sel_line = ""
    selp = pkg / "selected_video_path.txt"
    if selp.is_file():
        sel_line = read_first_nonempty_line(selp)

    print("=== youtube_upload status ===")
    print(f"package={pkg}")
    print(f"video={sel_line or '(see selected_video_path.txt)'}")
    print(f"privacy={args.privacy}")
    print(f"title={(args.title or '').strip() or '(from package)'}")
    print(f"token_path_used={token_used_str}")
    print(f"client_secrets_path_used={secrets_used_str}")
    print(f"dry_run={bool(args.dry_run)}")
    for w in path_warnings:
        print(f"WARNING: {w}")
    if not args.dry_run:
        try:
            ch_t, ch_i = _fetch_channel_via_token(token_path, client_secrets)
            print(f"upload_channel_title={ch_t}")
            print(f"upload_channel_id={ch_i}")
        except Exception as exc:
            err = str(exc)
            print(f"upload_channel=(unavailable: {type(exc).__name__})")
            if "YOUTUBE_TOKEN_INVALID_GRANT" in err or "invalid_grant" in err.lower():
                print(err)
                _write_package_upload_artifact(
                    pkg,
                    UploadResult(
                        False,
                        "error",
                        error=err,
                        error_type="invalid_grant",
                        token_path_used=token_used_str,
                        client_secrets_path_used=secrets_used_str,
                    ),
                    video_path=sel_line,
                    suggested_fix=long_token_invalid_grant_fix_command(),
                )
                return 2

    res = upload_from_package_directory(
        args.package_dir,
        privacy=args.privacy,
        allow_public=args.allow_public,
        title=args.title,
        description=args.description,
        tags_str=args.tags,
        category_id=args.category_id,
        made_for_kids=args.made_for_kids,
        thumbnail=args.thumbnail,
        dry_run=args.dry_run,
        token_path=token_path,
        client_secrets=client_secrets,
        playlist_id=args.playlist_id,
        notify_subscribers=args.notify_subscribers,
        force_reupload=args.force_reupload,
        allow_test_assets=args.allow_test_assets,
        token_path_used=token_used_str,
        client_secrets_path_used=secrets_used_str,
        use_ai_metadata=bool(args.use_ai_metadata),
        agent_upload=agent_upload,
        review_queue=review_queue,
        force_private=force_private,
        channel_type="long",
    )
    if args.dry_run and (res.ok or res.status == "dry_run"):
        preview = {
            "ok": res.ok,
            "status": res.status,
            "dry_run": True,
            "metadata_used": res.metadata_used,
            "metadata_path": res.metadata_path,
            "title_used": res.title_used or res.title,
            "description_used": res.description_used,
            "tags_used": res.tags_used,
            "metadata_generator_version": res.metadata_generator_version,
            "audio_mode": res.audio_mode,
            "audio_policy": res.audio_policy,
        }
        try:
            (pkg / "upload_result.json").write_text(
                json.dumps(preview, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
        print(
            json.dumps(
                {k: preview[k] for k in ("metadata_used", "title_used", "metadata_path", "metadata_generator_version", "audio_mode")},
                indent=2,
                ensure_ascii=False,
            )
        )
    if not args.dry_run:
        _write_package_upload_artifact(pkg, res, video_path=sel_line)
    if res.status == "rejected_privacy":
        return 3
    if res.ok or res.status == "dry_run":
        return 0
    if res.status == "skipped_duplicate":
        return 4
    if res.status == "blocked_test_asset":
        return 5
    return 1


def _fetch_channel_via_token(token_path: Path, client_secrets: Path) -> tuple[str, str]:
    from googleapiclient.discovery import build

    creds = _load_credentials(token_path, client_secrets)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return _mine_channel_title_id(youtube)


if __name__ == "__main__":
    raise SystemExit(main())
