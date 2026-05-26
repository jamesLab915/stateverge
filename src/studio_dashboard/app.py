"""
StateVerge Studio Dashboard — FastAPI app.

Pages:
    GET  /                   总览       (overview)
    GET  /tools              工具箱     (功能索引 + 命令备忘)
    GET  /tracking           证据档案   (IRS / EB-1·NIW · docs/tracking)
    GET  /tracking/view/{slug}
    GET  /tracking/raw/{slug}
    GET  /create             创作工作台  (script + voice generation)
    GET  /assets/picker      素材选择   (per-topic visual picker)
    GET  /render             渲染中心   (per-topic Shorts renderer)
    GET  /shorts             Shorts工厂  (per-topic shorts/ pipeline)
    GET  /finance            财报分析    (per-topic finance/ pipeline)
    GET  /assets             素材库     (asset library + ~/Downloads)
    GET  /nyc                NYC 发布监控（launchd 日志今日汇总）

JSON:
    GET  /api/topics
    GET  /api/shorts
    GET  /api/finance
    GET  /api/assets
    POST /api/create/script
    POST /api/create/voice
    GET  /api/assets/picker?topic=<slug>
    POST /api/assets/select
    POST /api/render/short
    GET  /api/health
    GET  /api/nyc
    GET  /api/tools/catalog

Run:
    cd ~/StateVerge && source .venv/bin/activate
    uvicorn src.studio_dashboard.app:app --host 127.0.0.1 --port 8765 --reload
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from utils.storage_paths import get_stateverge_volume

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .nyc_status import get_status, volume_mount_ok
from .scanner import (
    STAGES,
    scan_all,
    scan_assets,
    scan_finance_all,
    scan_shorts_all,
    summarize,
    summarize_finance,
    summarize_shorts,
)
from .services import asset_picker, eleven_tts, ffmpeg_renderer, openai_writer
from .services.paths import UnsafePathError
from .services.xhs_workshop import (
    XHS_DEFAULT_ROOT_REL,
    get_job,
    list_jobs,
    load_note_detail,
    parse_urls,
    scan_xhs_notes,
    start_job,
)
from .services.xhs_video_stitcher import (
    DEFAULT_CLIPS_SUBDIR,
    StitcherError,
    list_clips as stitcher_list_clips,
    resolve_clips_folder,
    stitch as stitcher_stitch,
    thumb_for_clip,
)
from .services import xhs_voice_mux
from .tools_catalog import catalog_dict
from .tracking_hub_catalog import hub_context, resolve_view_path, slug_index


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv_into_env() -> None:
    """
    Load <repo>/.env into os.environ at process start so OPENAI_API_KEY,
    ELEVENLABS_API_KEY etc. are available to the /api/create/* endpoints.

    Existing env vars (e.g. set by the shell that launched uvicorn) win — we
    never overwrite a value that's already set.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]

        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass
    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv_into_env()

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Workaround: jinja2 LRUCache hits a TypeError on Python 3.14 in some setups;
# disabling the template cache is cheap (every render re-reads the file)
# and avoids the error entirely. Templates are tiny.
templates.env.cache = None

app = FastAPI(title="StateVerge Studio Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Nav configuration (single source of truth, used on every page)
# ---------------------------------------------------------------------------

NAV_ITEMS = [
    {"id": "toolbox", "name": "工具箱", "url": "/tools"},
    {"id": "nyc_launch", "name": "NYC 发布监控", "url": "/nyc"},
]


def _nav(active_id: str) -> list[dict]:
    return [{**item, "active": item["id"] == active_id} for item in NAV_ITEMS]


TRACKING_ROW_CAP = 500
TRACKING_CHAR_CAP = 120_000


STATUS_COLORS = {
    # Overview pipeline
    "Ready":            "green",
    "Need Package":     "blue",
    "Need Mix":         "blue",
    "Missing Assets":   "yellow",
    "Missing Voice":    "red",
    "Missing Script":   "red",
    # Shorts pipeline
    "Missing Subtitles":"yellow",
    "Need Render":      "blue",
    # Finance pipeline
    "Missing Data":     "red",
    "Missing Analysis": "yellow",
}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def _shared_ctx(active_id: str) -> dict:
    return {
        "nav": _nav(active_id),
        "status_colors": STATUS_COLORS,
        "repo_root": str(REPO_ROOT),
    }


@app.get("/", response_class=HTMLResponse)
def page_overview(request: Request) -> HTMLResponse:
    statuses = scan_all(REPO_ROOT)
    summary = summarize(statuses)
    ctx = {
        **_shared_ctx("overview"),
        "stages": [{"id": st["id"], "name": st["name"]} for st in STAGES],
        "stage_stats": summary["stage_stats"],
        "panel": summary["panel"],
        "recent": summary["recent"],
        "suggestion": summary["suggestion"],
        "topics": statuses,
    }
    return templates.TemplateResponse(
        request=request, name="index.html", context=ctx
    )


@app.get("/shorts", response_class=HTMLResponse)
def page_shorts(request: Request) -> HTMLResponse:
    shorts = scan_shorts_all(REPO_ROOT)
    summary = summarize_shorts(shorts)
    ctx = {
        **_shared_ctx("shorts"),
        "shorts": shorts,
        "panel": summary["panel"],
        "suggestion": summary["suggestion"],
    }
    return templates.TemplateResponse(
        request=request, name="shorts.html", context=ctx
    )


@app.get("/finance", response_class=HTMLResponse)
def page_finance(request: Request) -> HTMLResponse:
    finance = scan_finance_all(REPO_ROOT)
    summary = summarize_finance(finance)
    ctx = {
        **_shared_ctx("earnings"),
        "finance": finance,
        "panel": summary["panel"],
        "suggestion": summary["suggestion"],
    }
    return templates.TemplateResponse(
        request=request, name="finance.html", context=ctx
    )


@app.get("/assets", response_class=HTMLResponse)
def page_assets(request: Request) -> HTMLResponse:
    snap = scan_assets(REPO_ROOT)
    # Pre-format mtime for the table — keeps Jinja simple
    for r in snap["recent"]:
        try:
            r["mtime_str"] = datetime.fromtimestamp(r["mtime"]).strftime("%m-%d %H:%M")
        except (OSError, ValueError, OverflowError):
            r["mtime_str"] = ""
    ctx = {
        **_shared_ctx("library"),
        "sources": snap["sources"],
        "recent": snap["recent"],
        "downloads_uncat": snap["downloads_uncat"],
        "missing_assets_topics": snap["missing_assets_topics"],
        "totals": snap["totals"],
    }
    return templates.TemplateResponse(
        request=request, name="assets.html", context=ctx
    )


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


@app.get("/api/topics")
def api_topics() -> JSONResponse:
    statuses = scan_all(REPO_ROOT)
    summary = summarize(statuses)
    return JSONResponse({
        "topics": [asdict(s) for s in statuses],
        "stage_stats": summary["stage_stats"],
        "panel": summary["panel"],
        "recent": summary["recent"],
        "suggestion": summary["suggestion"],
        "repo_root": str(REPO_ROOT),
    })


@app.get("/api/shorts")
def api_shorts() -> JSONResponse:
    shorts = scan_shorts_all(REPO_ROOT)
    summary = summarize_shorts(shorts)
    return JSONResponse({
        "shorts": [asdict(s) for s in shorts],
        "panel": summary["panel"],
        "suggestion": summary["suggestion"],
        "repo_root": str(REPO_ROOT),
    })


@app.get("/api/finance")
def api_finance() -> JSONResponse:
    finance = scan_finance_all(REPO_ROOT)
    summary = summarize_finance(finance)
    return JSONResponse({
        "finance": [asdict(s) for s in finance],
        "panel": summary["panel"],
        "suggestion": summary["suggestion"],
        "repo_root": str(REPO_ROOT),
    })


@app.get("/api/assets")
def api_assets() -> JSONResponse:
    snap = scan_assets(REPO_ROOT)
    return JSONResponse({
        "sources": [asdict(s) for s in snap["sources"]],
        "recent": snap["recent"],
        "downloads_uncat": snap["downloads_uncat"],
        "missing_assets_topics": snap["missing_assets_topics"],
        "totals": snap["totals"],
        "repo_root": str(REPO_ROOT),
    })


@app.get("/api/health")
def api_health() -> JSONResponse:
    return JSONResponse({"ok": True, "service": "stateverge-studio-dashboard"})


# ---------------------------------------------------------------------------
# 创作工作台 — /create
# ---------------------------------------------------------------------------


def _existing_topic_slugs() -> list[str]:
    topics_root = REPO_ROOT / "topics"
    if not topics_root.is_dir():
        return []
    out: list[str] = []
    for p in sorted(topics_root.iterdir()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        out.append(p.name)
    return out


@app.get("/create", response_class=HTMLResponse)
def page_create(request: Request) -> HTMLResponse:
    ctx = {
        **_shared_ctx("studio"),
        "openai_env": openai_writer.env_status(),
        "eleven_env": eleven_tts.env_status(),
        "existing_topics": _existing_topic_slugs(),
        "length_options": [
            {"value": "short", "label": "Short  (≈60-90s)"},
            {"value": "long",  "label": "Long   (≈8-12 min)"},
        ],
        "language_options": [
            {"value": "zh", "label": "中文"},
            {"value": "en", "label": "English"},
        ],
        "style_options": [
            {"value": "finance",     "label": "Finance"},
            {"value": "documentary", "label": "Documentary"},
            {"value": "social",      "label": "Social"},
            {"value": "funny",       "label": "Funny"},
        ],
    }
    return templates.TemplateResponse(
        request=request, name="create.html", context=ctx
    )


class ScriptRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=64)
    title: str = Field("", max_length=300)
    length: str = Field("long")
    language: str = Field("zh")
    style: str = Field("documentary")


class VoiceRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=64)
    max_chars: int = Field(2500, ge=200, le=5000)


@app.post("/api/create/script")
def api_create_script(req: ScriptRequest) -> JSONResponse:
    try:
        result = openai_writer.generate_script(
            slug=req.topic,
            title=req.title,
            length=req.length,
            language=req.language,
            style=req.style,
            repo_root=REPO_ROOT,
        )
    except UnsafePathError as e:
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=400,
        )
    return JSONResponse(
        result.to_dict(),
        status_code=200 if result.ok else 400,
    )


@app.post("/api/create/voice")
def api_create_voice(req: VoiceRequest) -> JSONResponse:
    try:
        result = eleven_tts.generate_voice(
            slug=req.topic,
            repo_root=REPO_ROOT,
            max_chars=req.max_chars,
        )
    except UnsafePathError as e:
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=400,
        )
    return JSONResponse(
        result.to_dict(),
        status_code=200 if result.ok else 400,
    )


# ---------------------------------------------------------------------------
# 小红书工坊 — /xhs
#
# Paste an XHS share link, get one folder per note with images + caption +
# 1-min Chinese narration + 6 x 10s Runway prompts.
# ---------------------------------------------------------------------------


@app.get("/xhs", response_class=HTMLResponse)
def page_xhs(request: Request) -> HTMLResponse:
    notes = scan_xhs_notes(REPO_ROOT)
    note_dicts = [asdict(n) for n in notes]
    for n in note_dicts:
        try:
            n["age_str"] = datetime.fromtimestamp(n["mtime"]).strftime("%m-%d %H:%M")
        except (OSError, ValueError, OverflowError):
            n["age_str"] = ""
    ctx = {
        **_shared_ctx("xhs"),
        "notes": note_dicts,
        "note_count": len(note_dicts),
        "openai_env": openai_writer.env_status(),
        "eleven_env": xhs_voice_mux.env_status(),
        "xhs_root": XHS_DEFAULT_ROOT_REL,
    }
    return templates.TemplateResponse(
        request=request, name="xhs.html", context=ctx
    )


class XhsRunRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000,
                      description="paste any share-card text or one URL per line")
    storyboard: bool = True
    force_storyboard: bool = False


@app.post("/api/xhs/run")
def api_xhs_run(req: XhsRunRequest) -> JSONResponse:
    urls = parse_urls(req.text)
    if not urls:
        return JSONResponse(
            {"ok": False, "error": "no XHS URLs found in input"},
            status_code=400,
        )
    job = start_job(
        repo_root=REPO_ROOT,
        urls=urls,
        storyboard=req.storyboard,
        force_storyboard=req.force_storyboard,
    )
    return JSONResponse({"ok": True, "job": job.to_dict()})


@app.get("/api/xhs/jobs")
def api_xhs_jobs() -> JSONResponse:
    return JSONResponse({"jobs": [j.to_dict() for j in list_jobs(limit=20)]})


@app.get("/api/xhs/jobs/{job_id}")
def api_xhs_job(job_id: str) -> JSONResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse({"ok": True, "job": job.to_dict()})


@app.get("/api/xhs/notes")
def api_xhs_notes() -> JSONResponse:
    notes = scan_xhs_notes(REPO_ROOT)
    return JSONResponse({"notes": [asdict(n) for n in notes]})


@app.get("/api/xhs/notes/{note_id}")
def api_xhs_note_detail(note_id: str) -> JSONResponse:
    if not _safe_note_id(note_id):
        raise HTTPException(status_code=400, detail="invalid note_id")
    detail = load_note_detail(REPO_ROOT, note_id)
    if not detail:
        raise HTTPException(status_code=404, detail="note not found")
    return JSONResponse({"ok": True, "note": detail})


@app.get("/xhs-asset/{note_id}/{filename}")
def xhs_asset(note_id: str, filename: str):
    """Serve a single file from assets/xhs/<note_id>/ (images, json, etc)."""
    if not _safe_note_id(note_id) or not _safe_filename(filename):
        raise HTTPException(status_code=400, detail="invalid path")
    folder = (REPO_ROOT / XHS_DEFAULT_ROOT_REL / note_id).resolve()
    target = (folder / filename).resolve()
    try:
        target.relative_to(folder)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes note folder")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(str(target))


@app.get("/xhs-asset/{note_id}/_thumbs/{filename}")
def xhs_asset_thumb(note_id: str, filename: str):
    """Serve a cached video thumbnail."""
    if not _safe_note_id(note_id) or not _safe_filename(filename):
        raise HTTPException(status_code=400, detail="invalid path")
    folder = (REPO_ROOT / XHS_DEFAULT_ROOT_REL / note_id / "_thumbs").resolve()
    target = (folder / filename).resolve()
    try:
        target.relative_to(folder)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes thumb folder")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="thumb not found")
    return FileResponse(str(target))


# ---- video stitcher endpoints ----------------------------------------------


@app.get("/api/xhs/notes/{note_id}/clips")
def api_xhs_clips(note_id: str, folder: str | None = None) -> JSONResponse:
    """List video clips in a folder, plus generate cached thumbnails."""
    if not _safe_note_id(note_id):
        raise HTTPException(status_code=400, detail="invalid note_id")
    folder_path = resolve_clips_folder(REPO_ROOT, note_id, folder)
    clips = stitcher_list_clips(folder_path, REPO_ROOT)
    out: list[dict] = []
    for c in clips:
        d = c.to_dict()
        thumb = thumb_for_clip(REPO_ROOT, note_id, Path(c.path))
        if thumb is not None:
            d["thumb_url"] = f"/xhs-asset/{note_id}/_thumbs/{thumb.name}"
        out.append(d)
    default_folder = REPO_ROOT / XHS_DEFAULT_ROOT_REL / note_id / DEFAULT_CLIPS_SUBDIR
    return JSONResponse({
        "ok": True,
        "folder": str(folder_path),
        "folder_exists": folder_path.is_dir(),
        "default_folder": str(default_folder),
        "clips": out,
        "clip_count": len(out),
    })


class XhsStitchRequest(BaseModel):
    clips: list[str] = Field(..., min_length=1, max_length=24,
                             description="ordered list of absolute clip paths")
    output_name: str = Field("stitched_60s.mp4", max_length=80)


@app.post("/api/xhs/notes/{note_id}/stitch")
def api_xhs_stitch(note_id: str, req: XhsStitchRequest) -> JSONResponse:
    if not _safe_note_id(note_id):
        raise HTTPException(status_code=400, detail="invalid note_id")
    note_dir = REPO_ROOT / XHS_DEFAULT_ROOT_REL / note_id
    if not note_dir.is_dir():
        raise HTTPException(status_code=404, detail="note folder not found")

    clip_paths: list[Path] = []
    for s in req.clips:
        if not s or not s.strip():
            continue
        p = Path(os.path.expanduser(s.strip())).resolve()
        if not p.is_file():
            raise HTTPException(status_code=400, detail=f"clip not found: {p}")
        clip_paths.append(p)
    if not clip_paths:
        raise HTTPException(status_code=400, detail="no clips selected")

    try:
        result = stitcher_stitch(
            repo_root=REPO_ROOT,
            note_id=note_id,
            ordered_clip_paths=clip_paths,
            output_name=req.output_name,
        )
    except StitcherError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    payload = result.to_dict()
    if result.ok:
        payload["asset_url"] = f"/xhs-asset/{note_id}/{Path(result.output_path).name}"
    return JSONResponse(payload, status_code=200 if result.ok else 500)


class XhsVoiceRequest(BaseModel):
    mux_into: str = Field("", max_length=80,
                          description="filename inside the note folder to mux audio onto; "
                                      "e.g. 'stitched_60s.mp4'. Leave empty for TTS-only.")
    output_name: str = Field("", max_length=80)


@app.post("/api/xhs/notes/{note_id}/voice")
def api_xhs_voice(note_id: str, req: XhsVoiceRequest) -> JSONResponse:
    if not _safe_note_id(note_id):
        raise HTTPException(status_code=400, detail="invalid note_id")
    mux_target = (req.mux_into or "").strip()
    if mux_target and not _safe_filename(mux_target):
        raise HTTPException(status_code=400, detail="invalid mux_into filename")
    out_name = (req.output_name or "").strip()
    if out_name and not _safe_filename(out_name):
        raise HTTPException(status_code=400, detail="invalid output_name")

    result = xhs_voice_mux.generate_voice_and_optional_mux(
        repo_root=REPO_ROOT,
        note_id=note_id,
        mux_into=mux_target or None,
        output_name=out_name or None,
    )

    payload = result.to_dict()
    if result.voice_mp3_rel:
        payload["voice_mp3_url"] = f"/xhs-asset/{note_id}/{Path(result.voice_mp3_rel).name}"
    if result.muxed_video_rel:
        payload["muxed_video_url"] = f"/xhs-asset/{note_id}/{Path(result.muxed_video_rel).name}"
    return JSONResponse(payload, status_code=200 if result.ok else 500)


_NOTE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _safe_note_id(s: str) -> bool:
    return bool(_NOTE_ID_RE.match(s or ""))


def _safe_filename(s: str) -> bool:
    return bool(_SAFE_NAME_RE.match(s or ""))


# ---------------------------------------------------------------------------
# 素材选择 — /assets/picker  +  渲染中心 — /render
# ---------------------------------------------------------------------------


def _topic_chooser_ctx(active_id: str, action_url: str) -> dict:
    """Shared context for the no-topic landing page on picker / render."""
    return {
        **_shared_ctx(active_id),
        "existing_topics": _existing_topic_slugs(),
        "action_url": action_url,
    }


@app.get("/assets/picker", response_class=HTMLResponse)
def page_assets_picker(request: Request, topic: str | None = None) -> HTMLResponse:
    if not topic:
        ctx = {
            **_topic_chooser_ctx("picker", "/assets/picker"),
            "page_label": "素材选择",
            "page_help": "选择一个 topic 来打开它的素材选择器。",
        }
        return templates.TemplateResponse(
            request=request, name="topic_chooser.html", context=ctx
        )
    try:
        snap = asset_picker.list_assets(slug=topic, repo_root=REPO_ROOT)
    except UnsafePathError as e:
        raise HTTPException(status_code=400, detail=str(e))

    existing_sel = asset_picker.load_selection(topic, REPO_ROOT) or {}
    selected_paths: set[str] = set()
    for it in (existing_sel.get("items") or []):
        if isinstance(it, dict) and it.get("abs_path"):
            selected_paths.add(it["abs_path"])

    # Pre-decorate items for the template
    for it in snap["items"]:
        it["selected"] = it["abs_path"] in selected_paths
        dur = it["duration_sec"]
        it["dur_str"] = (
            f"{int(dur // 60)}:{int(dur % 60):02d}" if dur >= 60
            else f"{dur:.1f}s"
        )
        it["res_str"] = (
            f"{it['width']}×{it['height']}" if it["width"] and it["height"]
            else "—"
        )
        it["size_mb"] = (
            round(it["size_bytes"] / (1024 * 1024), 1)
            if it["size_bytes"] else 0.0
        )

    ctx = {
        **_shared_ctx("picker"),
        "topic": topic,
        "sources": snap["sources"],
        "items": snap["items"],
        "totals": snap["totals"],
        "existing_topics": _existing_topic_slugs(),
        "selected_paths": list(selected_paths),
        "selected_count": len(selected_paths),
    }
    return templates.TemplateResponse(
        request=request, name="asset_picker.html", context=ctx
    )


@app.get("/tracking", response_class=HTMLResponse)
def page_tracking_hub(request: Request) -> HTMLResponse:
    ctx = {**_shared_ctx("tracking"), **hub_context(REPO_ROOT)}
    return templates.TemplateResponse(
        request=request, name="tracking_hub.html", context=ctx
    )


@app.get("/tracking/view/{slug}", response_class=HTMLResponse)
def page_tracking_view(request: Request, slug: str) -> HTMLResponse:
    if slug != "monthly-latest" and slug not in slug_index():
        raise HTTPException(status_code=404, detail="unknown slug")
    path, title = resolve_view_path(REPO_ROOT, slug)
    base_ctx = {
        **_shared_ctx("tracking"),
        "missing": False,
        "title": title,
        "slug": slug,
        "filename": "",
        "is_csv": False,
        "rows": [],
        "truncated": False,
        "body": "",
        "row_cap": TRACKING_ROW_CAP,
        "char_cap": TRACKING_CHAR_CAP,
    }
    if path is None or not path.is_file():
        base_ctx["missing"] = True
        base_ctx["title"] = title or "未找到"
        return templates.TemplateResponse(
            request=request, name="tracking_view.html", context=base_ctx
        )
    base_ctx["filename"] = path.name
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=500, detail="无法读取文件") from None
    suffix = path.suffix.lower()
    if suffix == ".csv":
        reader = csv.reader(raw_text.splitlines())
        rows: list[list[str]] = []
        truncated = False
        for i, row in enumerate(reader):
            if i >= TRACKING_ROW_CAP:
                truncated = True
                break
            rows.append(row)
        base_ctx.update({"is_csv": True, "rows": rows, "truncated": truncated})
    else:
        truncated = len(raw_text) > TRACKING_CHAR_CAP
        body = raw_text[:TRACKING_CHAR_CAP] if truncated else raw_text
        base_ctx.update({"is_csv": False, "body": body, "truncated": truncated})
    return templates.TemplateResponse(
        request=request, name="tracking_view.html", context=base_ctx
    )


@app.get("/tracking/raw/{slug}")
def tracking_raw_file(slug: str) -> FileResponse:
    if slug != "monthly-latest" and slug not in slug_index():
        raise HTTPException(status_code=404, detail="unknown slug")
    path, _title = resolve_view_path(REPO_ROOT, slug)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(
        path,
        filename=path.name,
        media_type="text/plain; charset=utf-8",
    )


@app.get("/nyc", response_class=HTMLResponse)
def page_nyc_launch(request: Request) -> HTMLResponse:
    data = get_status()
    log_path = str(get_stateverge_volume() / "07_AUTOMATION/logs/launchd.out")
    ctx = {
        **_shared_ctx("nyc_launch"),
        "data": data,
        "mount_ok": volume_mount_ok(),
        "log_path": log_path,
    }
    return templates.TemplateResponse(
        request=request, name="nyc.html", context=ctx
    )


@app.get("/api/nyc")
def api_nyc_status() -> JSONResponse:
    return JSONResponse({"status": get_status()})


@app.get("/tools", response_class=HTMLResponse)
def page_tools_hub(request: Request) -> HTMLResponse:
    ctx = {
        **_shared_ctx("toolbox"),
        **catalog_dict(),
        "repo_commands_hint": str(REPO_ROOT / "StateVerge_COMMANDS.txt"),
    }
    return templates.TemplateResponse(
        request=request, name="tools_hub.html", context=ctx
    )


@app.get("/api/tools/catalog")
def api_tools_catalog() -> JSONResponse:
    payload = {"ok": True, "repo_root": str(REPO_ROOT), **catalog_dict()}
    return JSONResponse(payload)


@app.get("/render", response_class=HTMLResponse)
def page_render(request: Request, topic: str | None = None) -> HTMLResponse:
    if not topic:
        ctx = {
            **_topic_chooser_ctx("render", "/render"),
            "page_label": "渲染中心",
            "page_help": "选择一个 topic 来打开它的渲染面板。",
        }
        return templates.TemplateResponse(
            request=request, name="topic_chooser.html", context=ctx
        )
    try:
        readiness = ffmpeg_renderer.get_readiness(topic, REPO_ROOT)
    except UnsafePathError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # All three preconditions satisfied?
    can_render = (
        readiness.voice_exists
        and readiness.selection_exists
        and readiness.selection_count > 0
        and readiness.voice_duration_sec > 0.5
    )

    ctx = {
        **_shared_ctx("render"),
        "topic": topic,
        "readiness": readiness,
        "can_render": can_render,
        "existing_topics": _existing_topic_slugs(),
    }
    return templates.TemplateResponse(
        request=request, name="render.html", context=ctx
    )


# ---------------------------------------------------------------------------
# JSON / actions for picker + render
# ---------------------------------------------------------------------------


@app.get("/api/assets/picker")
def api_assets_picker(topic: str) -> JSONResponse:
    try:
        snap = asset_picker.list_assets(slug=topic, repo_root=REPO_ROOT)
    except UnsafePathError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    snap["ok"] = True
    return JSONResponse(snap)


class SelectItemPayload(BaseModel):
    source: str = Field("", max_length=64)
    abs_path: str = Field(..., min_length=1, max_length=1024)
    rel_path: str = Field("", max_length=1024)
    duration_sec: float = Field(0.0, ge=0.0, le=24 * 3600)
    width: int = Field(0, ge=0, le=16384)
    height: int = Field(0, ge=0, le=16384)
    fps: float = Field(0.0, ge=0.0, le=240.0)


class SelectRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=64)
    items: list[SelectItemPayload] = Field(default_factory=list)
    per_clip_sec: float = Field(6.0, ge=1.0, le=30.0)
    shuffle: bool = Field(False)


@app.post("/api/assets/select")
def api_assets_select(req: SelectRequest) -> JSONResponse:
    try:
        result = asset_picker.save_selection(
            slug=req.topic,
            items=[it.model_dump() for it in req.items],
            repo_root=REPO_ROOT,
            options={"per_clip_sec": req.per_clip_sec, "shuffle": req.shuffle},
        )
    except UnsafePathError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return JSONResponse(
        result.to_dict(),
        status_code=200 if result.ok else 400,
    )


class RenderRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=64)


@app.post("/api/render/short")
def api_render_short(req: RenderRequest) -> JSONResponse:
    try:
        result = ffmpeg_renderer.render_short(
            slug=req.topic, repo_root=REPO_ROOT,
        )
    except UnsafePathError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return JSONResponse(
        result.to_dict(),
        status_code=200 if result.ok else 400,
    )
