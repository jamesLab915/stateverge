"""
xhs_grab.py — download images & videos from Xiaohongshu (RED) note URLs.

Workflow
--------
1) One-time login (saves session to ~/.cache/stateverge/xhs_state.json):

    ./.venv/bin/python scripts/xhs_grab.py --login

   A real Chromium window opens. Log in to your XHS account, then press
   ENTER in the terminal. Session is reusable across runs.

2) Grab one or more notes:

    ./.venv/bin/python scripts/xhs_grab.py \\
        "https://www.xiaohongshu.com/explore/<note_id>?xsec_token=..." \\
        "https://www.xiaohongshu.com/discovery/item/<note_id>" \\
        "https://xhslink.com/<short>"

   Or batch from a file (one URL per line, blank lines & '#' comments OK):

    ./.venv/bin/python scripts/xhs_grab.py --urls-file urls.txt

Output layout (one self-contained folder per note):

    assets/xhs/<note_id>/
        img_001.jpg
        img_002.jpg
        ...
        video_01.mp4
        caption.txt   (note title + desc + tags as plain text, the 文案)
        _meta.json    (source URL, file list, title, desc, tags, author)

If you also pass --storyboard, the script will then call xhs_storyboard.py
on each freshly-grabbed note to add:

        narration_zh.txt      (1-min Chinese narration, ~280-320 chars)
        runway_prompts.json   (6 x 10-second Runway image-to-video prompts)
        storyboard.md         (human-readable bundle of the above)

Notes
-----
- Login-walled posts WILL fail without `--login` first.
- XHS structure changes frequently; if you see "no __INITIAL_STATE__" or
  "no media found", run with --headful to see what's happening.
- Be reasonable: rate-limit, don't hammer; XHS bans aggressively.
- Personal/research use only. Respect creators' rights and XHS ToS.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = Path.home() / ".cache" / "stateverge" / "xhs_state.json"
DEFAULT_OUT = REPO_ROOT / "assets" / "xhs"

LOG_PREFIX = "[xhs_grab]"

NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item)/([0-9a-f]+)", re.IGNORECASE)


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        sys.stderr.write(
            f"{LOG_PREFIX} Playwright is not installed.\n"
            f"  Install in your venv:\n"
            f"    ./.venv/bin/pip install playwright\n"
            f"    ./.venv/bin/python -m playwright install chromium\n"
        )
        raise SystemExit(1)
    from playwright.sync_api import sync_playwright as _sp
    return _sp


# ---------------------------------------------------------------------------
# State extraction
# ---------------------------------------------------------------------------

_GET_STATE_JS = r"""
() => {
  // Return every plausible state container as a single bundle so Python can
  // fall through them. XHS keeps moving the data around between releases.
  const out = { sources: {}, window_keys: [] };
  try {
    out.window_keys = Object.keys(window).filter(k => /state|data|store|nuxt|next|apollo|red|note/i.test(k));
  } catch (e) {}
  const tryPick = (name, val) => {
    if (val == null) return;
    try { out.sources[name] = JSON.parse(JSON.stringify(val)); } catch (e) {}
  };
  tryPick("__INITIAL_STATE__", window.__INITIAL_STATE__);
  tryPick("__INITIAL_SSR_STATE__", window.__INITIAL_SSR_STATE__);
  tryPick("__NUXT__", window.__NUXT__);
  tryPick("__APOLLO_STATE__", window.__APOLLO_STATE__);
  // Next.js SSR JSON is typically embedded as <script id="__NEXT_DATA__">.
  try {
    const tag = document.getElementById("__NEXT_DATA__");
    if (tag && tag.textContent) {
      out.sources["__NEXT_DATA__"] = JSON.parse(tag.textContent);
    }
  } catch (e) {}
  // Some XHS releases shove the note into an inline <script> as a JS literal.
  try {
    const scripts = Array.from(document.querySelectorAll("script"));
    for (const s of scripts) {
      const t = s.textContent || "";
      if (t.includes("noteDetailMap") || t.includes("\"imageList\"") || t.includes("\"noteId\"")) {
        out.sources["script_inline_" + (out.sources_inline_count = (out.sources_inline_count || 0) + 1)] = t.slice(0, 500000);
      }
    }
  } catch (e) {}
  return out;
}
"""


def _looks_like_note(d) -> bool:
    """Heuristic: this dict looks like an XHS note detail object."""
    if not isinstance(d, dict):
        return False
    has_images = isinstance(d.get("imageList"), list) and d["imageList"]
    has_video = isinstance(d.get("video"), dict)
    has_id = bool(d.get("noteId") or d.get("id"))
    has_title_or_desc = ("title" in d) or ("desc" in d)
    return (has_images or has_video) and has_id and has_title_or_desc


def _deep_find_note(obj, _depth: int = 0):
    """Walk any nested JSON-y structure looking for a note-shaped dict."""
    if _depth > 8:
        return None
    if isinstance(obj, dict):
        if _looks_like_note(obj):
            return obj
        # Common containers seen across XHS releases.
        nd = obj.get("noteDetailMap")
        if isinstance(nd, dict):
            for _nid, container in nd.items():
                if isinstance(container, dict):
                    cand = container.get("note")
                    if _looks_like_note(cand):
                        return cand
                    cand = _deep_find_note(container, _depth + 1)
                    if cand:
                        return cand
        for v in obj.values():
            cand = _deep_find_note(v, _depth + 1)
            if cand:
                return cand
    elif isinstance(obj, list):
        for v in obj:
            cand = _deep_find_note(v, _depth + 1)
            if cand:
                return cand
    return None


def find_note_data(bundle) -> dict | None:
    """Try every state source we collected, in order of likelihood."""
    if not isinstance(bundle, dict):
        return None
    sources = bundle.get("sources") if "sources" in bundle else bundle
    if not isinstance(sources, dict):
        return None
    # Preferred order — most stable XHS shapes first.
    preferred = [
        "__INITIAL_STATE__",
        "__INITIAL_SSR_STATE__",
        "__NEXT_DATA__",
        "__NUXT__",
        "__APOLLO_STATE__",
    ]
    for key in preferred:
        if key in sources:
            cand = _deep_find_note(sources[key])
            if cand:
                return cand
    # Inline-script fallback: extract the first JSON object that contains
    # something note-like.
    for key, val in sources.items():
        if not key.startswith("script_inline_") or not isinstance(val, str):
            continue
        # Cheap JSON object scanner: find balanced {...} chunks containing a noteId.
        text = val
        i = 0
        while True:
            idx = text.find("\"noteId\"", i)
            if idx < 0:
                break
            # Walk back to nearest "{" and forward to matching "}".
            start = text.rfind("{", 0, idx)
            if start < 0:
                break
            depth = 0
            end = -1
            for j in range(start, len(text)):
                c = text[j]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
            if end < 0:
                break
            try:
                obj = json.loads(text[start:end])
                cand = _deep_find_note(obj)
                if cand:
                    return cand
            except Exception:  # noqa: BLE001
                pass
            i = end if end > 0 else idx + 8
    # Absolute last resort: deep-search every source.
    for val in sources.values():
        cand = _deep_find_note(val)
        if cand:
            return cand
    return None


def collect_media_urls(note: dict | None) -> tuple[list[str], list[str], str]:
    """Return (image_urls, video_urls, title)."""
    if not note:
        return [], [], ""
    title = (note.get("title") or "").strip()

    images: list[str] = []
    for img in note.get("imageList") or []:
        if not isinstance(img, dict):
            continue
        u = (
            img.get("urlDefault")
            or img.get("urlSizeLarge")
            or img.get("urlPre")
            or img.get("url")
        )
        if isinstance(u, str) and u.startswith("http"):
            images.append(u)

    videos: list[str] = []
    video = note.get("video") or {}
    media = (video.get("media") or {}) if isinstance(video, dict) else {}
    stream = (media.get("stream") or {}) if isinstance(media, dict) else {}
    for codec in ("h264", "h265", "av1"):
        arr = stream.get(codec) if isinstance(stream, dict) else None
        if not arr:
            continue
        try:
            best = max(arr, key=lambda d: int(d.get("avgBitrate") or 0))
        except (TypeError, ValueError):
            best = arr[0] if arr else None
        if not isinstance(best, dict):
            continue
        url = best.get("masterUrl")
        if not url:
            backups = best.get("backupUrls") or []
            if backups:
                url = backups[0]
        if isinstance(url, str) and url.startswith("http"):
            videos.append(url)
            break

    return images, videos, title


def collect_text_data(note: dict | None) -> dict:
    """Extract the textual side of an XHS note: title, desc, tags, author.

    XHS reshuffles its state shape every few months; we read defensively and
    return empty strings/lists when fields are missing rather than failing.
    """
    if not isinstance(note, dict):
        return {"title": "", "desc": "", "tags": [], "author": ""}

    title = (note.get("title") or "").strip()

    desc = note.get("desc")
    if not isinstance(desc, str):
        desc = ""
    desc = desc.strip()

    tags: list[str] = []
    for tag in note.get("tagList") or []:
        if isinstance(tag, dict):
            name = tag.get("name") or tag.get("title")
            if isinstance(name, str) and name.strip():
                tags.append(name.strip())
        elif isinstance(tag, str) and tag.strip():
            tags.append(tag.strip())

    author = ""
    user = note.get("user") if isinstance(note, dict) else None
    if isinstance(user, dict):
        author = (user.get("nickname") or user.get("nickName") or "").strip()

    return {"title": title, "desc": desc, "tags": tags, "author": author}


def render_caption(text_data: dict) -> str:
    """Render the note's textual content as a single human-readable file."""
    lines: list[str] = []
    title = text_data.get("title") or ""
    if title:
        lines.append(f"# {title}")
        lines.append("")
    desc = text_data.get("desc") or ""
    if desc:
        lines.append(desc.rstrip())
        lines.append("")
    tags = text_data.get("tags") or []
    if tags:
        lines.append("Tags: " + " ".join(f"#{t}" for t in tags))
    author = text_data.get("author") or ""
    if author:
        lines.append(f"Author: {author}")
    return ("\n".join(lines).rstrip() + "\n") if lines else ""


def extract_note_id_from_url(url: str) -> str | None:
    m = NOTE_ID_RE.search(url)
    return m.group(1) if m else None


def guess_ext(url: str, fallback: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic",
                ".mp4", ".mov", ".m4v", ".webm"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    if "video" in url:
        return ".mp4"
    return fallback


def expand_short_url(short_url: str, page) -> str:
    page.goto(short_url, wait_until="domcontentloaded", timeout=30000)
    return page.url


# ---------------------------------------------------------------------------
# Per-note grab
# ---------------------------------------------------------------------------


def grab_note(url: str, context, out_root: Path) -> dict:
    page = context.new_page()
    src_url = url
    try:
        if "xhslink.com" in url:
            url = expand_short_url(url, page)
            log(f"  short url resolved -> {url}")

        # XHS keeps long-poll connections open forever, so "networkidle" almost
        # never fires. Use domcontentloaded and then explicitly wait for any
        # known state container OR an inline script that smells like the note.
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_function(
                """
                () => {
                  if (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.note) return true;
                  if (window.__INITIAL_SSR_STATE__) return true;
                  if (document.getElementById('__NEXT_DATA__')) return true;
                  const scripts = Array.from(document.querySelectorAll('script'));
                  return scripts.some(s => (s.textContent || '').includes('"noteId"'));
                }
                """,
                timeout=25000,
            )
        except Exception:  # noqa: BLE001
            log("  WARN no note-state appeared within 25s "
                "(captcha, login wall, or new layout); continuing anyway")
        page.wait_for_timeout(1000)

        bundle = page.evaluate(_GET_STATE_JS)
        note = find_note_data(bundle)

        note_id = (
            (note or {}).get("noteId")
            or extract_note_id_from_url(page.url)
            or extract_note_id_from_url(url)
            or "unknown"
        )

        if not note:
            # Dump everything we saw to disk so we can iterate without re-grabbing.
            debug_dir = out_root / note_id / "_debug"
            try:
                debug_dir.mkdir(parents=True, exist_ok=True)
                (debug_dir / "page_url.txt").write_text(page.url, encoding="utf-8")
                try:
                    html = page.content()
                    (debug_dir / "page.html").write_text(html, encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    page.screenshot(path=str(debug_dir / "page.png"), full_page=True)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    safe_bundle = bundle if isinstance(bundle, dict) else {"raw": str(bundle)}
                    src_keys = list((safe_bundle.get("sources") or {}).keys())
                    (debug_dir / "state_keys.json").write_text(
                        json.dumps({
                            "window_keys": safe_bundle.get("window_keys", []),
                            "source_keys": src_keys,
                        }, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    (debug_dir / "state_bundle.json").write_text(
                        json.dumps(safe_bundle, ensure_ascii=False, indent=2)[:5_000_000],
                        encoding="utf-8",
                    )
                except Exception as e:  # noqa: BLE001
                    log(f"  WARN debug dump partial: {e}")
                log(f"  debug bundle written -> {debug_dir.relative_to(REPO_ROOT)}/")
            except Exception as e:  # noqa: BLE001
                log(f"  WARN could not write debug bundle: {e}")
            return {
                "src_url": src_url,
                "ok": False,
                "error": "no note state found (login wall, captcha, or layout changed) — "
                         f"see {debug_dir.relative_to(REPO_ROOT)}/",
                "note_id": note_id,
            }

        images, videos, title = collect_media_urls(note)
        text_data = collect_text_data(note)
        if title and not text_data.get("title"):
            text_data["title"] = title
        if not images and not videos:
            return {
                "src_url": src_url,
                "ok": False,
                "error": "no media URLs in note state",
                "note_id": note_id,
                "title": title,
            }

        target_dir = out_root / note_id
        target_dir.mkdir(parents=True, exist_ok=True)

        caption_text = render_caption(text_data)
        if caption_text:
            (target_dir / "caption.txt").write_text(caption_text, encoding="utf-8")
            log(f"  + caption.txt ({len(caption_text)} chars)")

        downloaded: list[str] = []
        for i, u in enumerate(images, start=1):
            ext = guess_ext(u, ".jpg")
            dest = target_dir / f"img_{i:03d}{ext}"
            _download(u, dest, context)
            downloaded.append(str(dest.relative_to(REPO_ROOT)))
            log(f"  + img {i:03d} {dest.stat().st_size / 1024:.1f} KiB  {dest.name}")

        for i, u in enumerate(videos, start=1):
            ext = guess_ext(u, ".mp4")
            dest = target_dir / f"video_{i:02d}{ext}"
            _download(u, dest, context)
            downloaded.append(str(dest.relative_to(REPO_ROOT)))
            log(f"  + video {i:02d} {dest.stat().st_size / (1024 * 1024):.2f} MiB  "
                f"{dest.name}")

        meta = {
            "src_url": src_url,
            "resolved_url": url,
            "note_id": note_id,
            "title": text_data.get("title") or title,
            "desc": text_data.get("desc", ""),
            "tags": text_data.get("tags", []),
            "author": text_data.get("author", ""),
            "image_urls": images,
            "video_urls": videos,
            "downloaded": downloaded,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z") or
                          time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        (target_dir / "_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "src_url": src_url,
            "ok": True,
            "note_id": note_id,
            "title": text_data.get("title") or title,
            "images": len(images),
            "videos": len(videos),
            "out_dir": str(target_dir.relative_to(REPO_ROOT)),
            "target_dir": str(target_dir),
        }
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass


def _download(url: str, dest: Path, context) -> None:
    """Cookies-aware download via Playwright's APIRequest context."""
    response = context.request.get(url, timeout=60000)
    if response.status >= 400:
        raise RuntimeError(f"HTTP {response.status} fetching {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(response.body())


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------


def cmd_login(state_path: Path) -> int:
    sp = _load_playwright()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with sp() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        page = ctx.new_page()
        page.goto("https://www.xiaohongshu.com/explore",
                  wait_until="domcontentloaded")
        log("a Chromium window has opened. Log in to xiaohongshu, "
            "then come back here and press ENTER...")
        try:
            input()
        except EOFError:
            pass
        ctx.storage_state(path=str(state_path))
        log(f"saved session -> {state_path}")
        browser.close()
    return 0


def _run_storyboard(target_dir: str, force: bool) -> None:
    """Chain xhs_storyboard.py for a freshly-grabbed note folder."""
    storyboard_script = Path(__file__).with_name("xhs_storyboard.py")
    if not storyboard_script.is_file():
        log(f"  WARN storyboard script not found at {storyboard_script}; skipping")
        return
    cmd = [sys.executable, str(storyboard_script), target_dir]
    if force:
        cmd.append("--force")
    try:
        subprocess.run(cmd, check=False)
    except Exception as exc:  # noqa: BLE001
        log(f"  WARN storyboard generation failed: {exc}")


def cmd_grab(
    urls: list[str],
    state_path: Path,
    out_root: Path,
    headful: bool,
    pause_sec: float,
    storyboard: bool = False,
    storyboard_force: bool = False,
) -> int:
    sp = _load_playwright()
    has_state = state_path.is_file()
    if not has_state:
        log(f"WARN no saved session at {state_path}; "
            f"login-walled posts will fail. Run with --login first.")
    out_root.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    with sp() as p:
        browser = p.chromium.launch(headless=not headful)
        ctx_kwargs: dict = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1280, "height": 900},
            "locale": "zh-CN",
        }
        if has_state:
            ctx_kwargs["storage_state"] = str(state_path)
        context = browser.new_context(**ctx_kwargs)
        try:
            for i, url in enumerate(urls, start=1):
                log(f"[{i}/{len(urls)}] -> {url}")
                try:
                    r = grab_note(url.strip(), context, out_root)
                except Exception as e:  # noqa: BLE001
                    r = {"src_url": url, "ok": False, "error": str(e)}
                if r.get("ok"):
                    log(f"  OK  note={r['note_id']}  "
                        f"images={r['images']}  videos={r['videos']}  "
                        f"-> {r['out_dir']}/")
                    if storyboard and r.get("target_dir"):
                        _run_storyboard(r["target_dir"], storyboard_force)
                else:
                    log(f"  FAIL  note={r.get('note_id', '?')}  "
                        f"reason={r.get('error', '?')}")
                results.append(r)
                if i < len(urls) and pause_sec > 0:
                    time.sleep(pause_sec)
        finally:
            browser.close()

    ok = sum(1 for r in results if r.get("ok"))
    fail = len(results) - ok
    log(f"done  total={len(results)}  ok={ok}  fail={fail}")
    return 0 if fail == 0 else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Xiaohongshu (RED) image/video grabber for known note URLs.",
    )
    ap.add_argument("urls", nargs="*",
                    help="XHS note URLs. Mix of /explore/, /discovery/item/, xhslink.com.")
    ap.add_argument("--urls-file", type=Path,
                    help="text file with one URL per line ('#' comments OK)")
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE,
                    help=f"session storage file (default: {DEFAULT_STATE})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"output root (default: {DEFAULT_OUT.relative_to(REPO_ROOT)})")
    ap.add_argument("--login", action="store_true",
                    help="open browser headful for one-time login + session save")
    ap.add_argument("--headful", action="store_true",
                    help="show browser window during grab (debug)")
    ap.add_argument("--pause-sec", type=float, default=2.0,
                    help="seconds to wait between notes (default: 2.0)")
    ap.add_argument("--storyboard", action="store_true",
                    help="after each successful grab, also run xhs_storyboard.py "
                         "to generate narration_zh.txt + runway_prompts.json + "
                         "storyboard.md in the same note folder")
    ap.add_argument("--storyboard-force", action="store_true",
                    help="when --storyboard is on, regenerate even if outputs exist")
    ns = ap.parse_args(argv)

    if ns.login:
        return cmd_login(ns.state)

    urls: list[str] = list(ns.urls)
    if ns.urls_file:
        if not ns.urls_file.is_file():
            ap.error(f"--urls-file not found: {ns.urls_file}")
        for line in ns.urls_file.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                urls.append(s)

    if not urls:
        ap.error("no URLs given (positional args or --urls-file)")

    return cmd_grab(
        urls=urls,
        state_path=ns.state,
        out_root=ns.out,
        headful=ns.headful,
        pause_sec=ns.pause_sec,
        storyboard=ns.storyboard,
        storyboard_force=ns.storyboard_force,
    )


if __name__ == "__main__":
    raise SystemExit(main())
