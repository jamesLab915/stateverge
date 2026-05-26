"""
XHS Workshop service for the Studio Dashboard.

Two responsibilities:

1) Scan ``assets/xhs/<note_id>/`` folders produced by ``scripts/xhs_grab.py``
   and surface them as a structured list (thumbnails, narration preview,
   scene count, age) so the dashboard can render a gallery.

2) Run the grab+storyboard pipeline as a background subprocess. Each run
   gets a job_id and a streaming stdout buffer the dashboard can poll.

Kept dependency-free on purpose — no Celery, no Redis. State lives in
process memory; sufficient for a single-user studio dashboard.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


XHS_DEFAULT_ROOT_REL = "assets/xhs"
SCRIPTS_REL = "scripts"

XHS_URL_RE = re.compile(
    # URL chars only (RFC 3986 unreserved + reserved sub-delims + percent-encoded).
    # This stops at any CJK / whitespace / quote glued onto the tail.
    r"https?://(?:www\.)?(?:xiaohongshu\.com|xhslink\.com)/[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Folder scanning
# ---------------------------------------------------------------------------


@dataclass
class XhsNote:
    note_id: str
    folder: str           # repo-relative path
    title: str
    desc_preview: str     # first ~120 chars of caption/desc
    author: str
    tags: list[str]
    src_url: str
    image_count: int
    video_count: int
    has_caption: bool
    has_narration: bool
    has_runway: bool
    has_storyboard: bool
    narration_preview: str
    narration_chars: int
    scene_count: int
    fetched_at: str
    mtime: float
    thumbnails: list[str]   # repo-relative image paths (max 6)


def _read_json_safe(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_text_safe(path: Path, limit: int = 0) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text[:limit] if (limit and len(text) > limit) else text


def _is_note_folder(p: Path) -> bool:
    if not p.is_dir() or p.name.startswith("_"):
        return False
    if (p / "_meta.json").is_file():
        return True
    return any(p.glob("img_*.*"))


def _list_images(folder: Path) -> list[Path]:
    out: list[Path] = []
    for ext in ("jpg", "jpeg", "png", "webp"):
        out.extend(folder.glob(f"img_*.{ext}"))
    return sorted(out)


def _summarize_note(folder: Path, repo_root: Path) -> XhsNote:
    meta = _read_json_safe(folder / "_meta.json")
    runway = _read_json_safe(folder / "runway_prompts.json")

    caption = _read_text_safe(folder / "caption.txt")
    narration = _read_text_safe(folder / "narration_zh.txt")

    images = _list_images(folder)
    videos = sorted(folder.glob("video_*.*"))

    title = (meta.get("title") or "").strip() or folder.name
    desc = (meta.get("desc") or "").strip()
    if not desc and caption:
        # First non-title paragraph of caption.txt
        for block in caption.split("\n\n"):
            b = block.strip()
            if b and not b.startswith("# ") and not b.lower().startswith("tags:") \
                    and not b.lower().startswith("author:"):
                desc = b
                break
    desc_preview = desc.replace("\n", " ").strip()[:160]

    narration_clean = narration.strip()
    narration_preview = narration_clean[:140].replace("\n", " ")

    cjk_chars = sum(1 for ch in narration_clean if "\u4e00" <= ch <= "\u9fff")

    thumbs: list[str] = []
    for img in images[:6]:
        try:
            thumbs.append(str(img.relative_to(repo_root)).replace(os.sep, "/"))
        except ValueError:
            thumbs.append(str(img))

    try:
        rel_folder = str(folder.relative_to(repo_root)).replace(os.sep, "/")
    except ValueError:
        rel_folder = str(folder)

    return XhsNote(
        note_id=folder.name,
        folder=rel_folder,
        title=title,
        desc_preview=desc_preview,
        author=(meta.get("author") or "").strip(),
        tags=meta.get("tags") or [],
        src_url=(meta.get("src_url") or "").strip(),
        image_count=len(images),
        video_count=len(videos),
        has_caption=(folder / "caption.txt").is_file(),
        has_narration=(folder / "narration_zh.txt").is_file(),
        has_runway=(folder / "runway_prompts.json").is_file(),
        has_storyboard=(folder / "storyboard.md").is_file(),
        narration_preview=narration_preview,
        narration_chars=cjk_chars,
        scene_count=int(runway.get("scene_count") or len(runway.get("scenes") or [])),
        fetched_at=(meta.get("fetched_at") or "").strip(),
        mtime=folder.stat().st_mtime,
        thumbnails=thumbs,
    )


def scan_xhs_notes(repo_root: Path) -> list[XhsNote]:
    root = repo_root / XHS_DEFAULT_ROOT_REL
    if not root.is_dir():
        return []
    notes = [
        _summarize_note(p, repo_root)
        for p in root.iterdir()
        if _is_note_folder(p)
    ]
    notes.sort(key=lambda n: n.mtime, reverse=True)
    return notes


def load_note_detail(repo_root: Path, note_id: str) -> dict | None:
    """Full payload for a single note: meta + caption + narration + runway."""
    folder = repo_root / XHS_DEFAULT_ROOT_REL / note_id
    if not _is_note_folder(folder):
        return None
    meta = _read_json_safe(folder / "_meta.json")
    runway = _read_json_safe(folder / "runway_prompts.json")
    caption = _read_text_safe(folder / "caption.txt")
    narration = _read_text_safe(folder / "narration_zh.txt")
    storyboard_md = _read_text_safe(folder / "storyboard.md")
    images = _list_images(folder)
    rel_imgs = [
        str(p.relative_to(repo_root)).replace(os.sep, "/") for p in images
    ]
    return {
        "note_id": note_id,
        "folder": str(folder.relative_to(repo_root)).replace(os.sep, "/"),
        "meta": meta,
        "caption": caption,
        "narration_zh": narration,
        "runway": runway,
        "storyboard_md": storyboard_md,
        "images": rel_imgs,
    }


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------


@dataclass
class XhsJob:
    job_id: str
    urls: list[str]
    storyboard: bool
    force_storyboard: bool
    status: str = "queued"  # queued | running | ok | failed
    started_at: float = 0.0
    finished_at: float = 0.0
    log_lines: list[str] = field(default_factory=list)
    note_ids: list[str] = field(default_factory=list)  # populated as grab logs them
    exit_code: int | None = None
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "urls": self.urls,
            "storyboard": self.storyboard,
            "force_storyboard": self.force_storyboard,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_sec": (self.finished_at or time.time()) - self.started_at
                            if self.started_at else 0.0,
            "log": self.log_lines[-500:],   # cap returned size
            "log_total": len(self.log_lines),
            "note_ids": self.note_ids,
            "exit_code": self.exit_code,
            "error": self.error,
        }


_JOBS: dict[str, XhsJob] = {}
_JOBS_LOCK = threading.Lock()
_NOTE_ID_LOG_RE = re.compile(r"note=([0-9a-f]{16,})", re.IGNORECASE)


def _save_job(job: XhsJob) -> None:
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job


def get_job(job_id: str) -> XhsJob | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def list_jobs(limit: int = 20) -> list[XhsJob]:
    with _JOBS_LOCK:
        jobs = list(_JOBS.values())
    jobs.sort(key=lambda j: j.started_at or 0, reverse=True)
    return jobs[:limit]


def parse_urls(blob: str) -> list[str]:
    """Pull XHS URLs out of arbitrary pasted text (one per line, or messy)."""
    if not blob:
        return []
    # Match anything that looks like a XHS URL, even in noisy share-card text
    matches = XHS_URL_RE.findall(blob)
    # De-dupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for m in matches:
        m = m.rstrip(".,)】」")
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


def start_job(
    repo_root: Path,
    urls: Iterable[str],
    storyboard: bool = True,
    force_storyboard: bool = False,
    python_exe: str | None = None,
) -> XhsJob:
    job = XhsJob(
        job_id=uuid.uuid4().hex[:12],
        urls=list(urls),
        storyboard=storyboard,
        force_storyboard=force_storyboard,
        started_at=time.time(),
        status="queued",
    )
    _save_job(job)

    if not job.urls:
        job.status = "failed"
        job.error = "no XHS URLs given"
        job.finished_at = time.time()
        return job

    py = python_exe or _find_python(repo_root)
    grab = repo_root / SCRIPTS_REL / "xhs_grab.py"
    if not grab.is_file():
        job.status = "failed"
        job.error = f"missing {grab}"
        job.finished_at = time.time()
        return job

    cmd = [py, str(grab), *job.urls]
    if job.storyboard:
        cmd.append("--storyboard")
    if job.force_storyboard:
        cmd.append("--storyboard-force")

    job.log_lines.append(f"$ {shlex.join(cmd)}")
    thread = threading.Thread(
        target=_run_job_thread,
        args=(job, cmd, repo_root),
        daemon=True,
    )
    thread.start()
    return job


def _find_python(repo_root: Path) -> str:
    candidate = repo_root / ".venv" / "bin" / "python"
    if candidate.is_file():
        return str(candidate)
    return os.environ.get("PYTHON", "python3")


def _run_job_thread(job: XhsJob, cmd: list[str], repo_root: Path) -> None:
    job.status = "running"
    env = dict(os.environ)
    # Make sure Playwright finds the user's installed browsers regardless of
    # what shell launched the dashboard.
    env.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(Path.home() / "Library" / "Caches" / "ms-playwright"),
    )
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except OSError as exc:
        job.status = "failed"
        job.error = f"failed to launch grab subprocess: {exc}"
        job.finished_at = time.time()
        return

    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        job.log_lines.append(line)
        # Best-effort: pick out note ids the grab logs (note=<id>) so the UI
        # can immediately show the result card the moment the grab finishes.
        for m in _NOTE_ID_LOG_RE.finditer(line):
            nid = m.group(1)
            if nid != "?" and nid not in job.note_ids:
                job.note_ids.append(nid)

    rc = proc.wait()
    job.exit_code = rc
    job.finished_at = time.time()
    job.status = "ok" if rc == 0 else "failed"
    if rc != 0 and not job.error:
        job.error = f"grab exited with code {rc}"
