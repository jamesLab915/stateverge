"""Queue runner (GitHub Actions): ``data/finance/requests.json``.

    {"status": "pending"}            → render today's episode
    → status "rendered", "episode": "episodes/2026-10-09.json",
      "video_url": <GitHub release asset>, "problems": [...]

Optional per entry: "anchor": false (skip Runway, use the still portrait),
"voice": "onyx" (OpenAI TTS voice).

Runway setup is automatic on first use: a fictional presenter portrait is
generated once (data/finance/anchor_portrait.jpg) and turned into a Runway
custom avatar whose id is kept in data/finance/avatar.json.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import market, news
from .assemble import Shot, assemble
from .runway import RunwayClient, RunwayError, generate_portrait
from .script import Script, ScriptWriter
from .tts import Speaker
from . import visuals

WATERMARK = Path("branding/youtube/watermark_S_150.png")


def _read(path: Path, default: Any) -> Any:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _download(url: str, out: Path) -> Path:
    with urllib.request.urlopen(url, timeout=300) as resp:
        out.write_bytes(resp.read())
    return out


def publish_release(video: Path, tag: str, title: str, body: str) -> str:
    """Upload the episode as a (pre)release asset; returns its download URL."""
    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", "")
    if not (repo and token):
        return ""
    head = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    def call(method: str, url: str, data: bytes | None, ctype: str = "application/json") -> dict:
        req = urllib.request.Request(url, data=data, method=method, headers={**head, "Content-Type": ctype})
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read() or b"{}")

    rel = call("POST", f"https://api.github.com/repos/{repo}/releases", json.dumps({
        "tag_name": tag, "target_commitish": os.environ.get("GITHUB_SHA", ""), "name": title,
        "body": body, "prerelease": True}).encode())
    asset = call("POST", f"https://uploads.github.com/repos/{repo}/releases/{rel['id']}/assets?name={video.name}",
                 video.read_bytes(), "video/mp4")
    return asset.get("browser_download_url", "")


class FinanceRunner:
    def __init__(self, root: Path, work: Path, quotes_fn=None, news_fn=None, writer=None, speaker=None,
                 runway=None, portrait_fn=None, publish=publish_release) -> None:
        self.root, self.work = Path(root), Path(work)
        self.quotes_fn = quotes_fn or market.snapshot
        self.news_fn = news_fn or news.headlines
        self.writer = writer or ScriptWriter()
        self.speaker = speaker or Speaker()
        self.runway = runway if runway is not None else RunwayClient()
        self.portrait_fn = portrait_fn or generate_portrait
        self.publish = publish
        self.log: list[str] = []

    # --- presenter ---
    def portrait(self) -> Path:
        p = self.root / "anchor_portrait.jpg"
        if not p.exists():
            self.portrait_fn(p)
            self.log.append("生成虚拟主播形象 anchor_portrait.jpg")
        return p

    def avatar_id(self) -> str:
        state_path = self.root / "avatar.json"
        state = _read(state_path, {})
        if state.get("avatar_id"):
            return state["avatar_id"]
        avatar = self.runway.create_avatar("Stateverge Finance Anchor", self.portrait())
        self.runway.wait_avatar(avatar)
        _write(state_path, {"avatar_id": avatar, "created_at": datetime.now(timezone.utc).isoformat()})
        self.log.append(f"Runway 虚拟主播已创建:{avatar}")
        return avatar

    def anchor_clips(self, audios: dict[int, Path], use_runway: bool) -> tuple[dict[int, Path], str]:
        if not (use_runway and self.runway.available):
            return {}, "" if not use_runway else "RUNWAYML_API_SECRET 未设置,主播用静态形象"
        try:
            avatar = self.avatar_id()
            clips = {}
            for i, audio in audios.items():
                url = self.runway.avatar_video(avatar, audio)
                clips[i] = _download(url, self.work / f"anchor_{i:02d}.mp4")
            return clips, ""
        except (RunwayError, OSError, KeyError) as e:
            return {}, f"Runway 失败,主播用静态形象:{e}"[:300]

    # --- episode ---
    def episode(self, item: dict) -> None:
        self.work.mkdir(parents=True, exist_ok=True)
        quotes, heads = self.quotes_fn(), self.news_fn()
        if len(quotes) < 3:
            item.update(status="error", failures=[f"行情数据不足({len(quotes)} 个品种)"])
            return
        script: Script = self.writer.write(quotes, heads)
        idx = [q.as_of for q in quotes if q.symbol.startswith("^")] or [q.as_of for q in quotes]
        day = max(idx)  # last stock-market session (crypto trades on weekends)
        if item.get("voice"):
            self.speaker.voice = item["voice"]
        audios = {i: self.speaker.speak(s.text, self.work / f"seg_{i:02d}.mp3") for i, s in enumerate(script.segments)}
        anchors = {i: a for i, a in audios.items() if script.segments[i].kind == "anchor"}
        clips, anchor_note = self.anchor_clips(anchors, item.get("anchor", True))
        portrait = self.portrait() if len(clips) < len(anchors) else None

        by_symbol = {q.symbol: q for q in quotes}
        lower = visuals.lower_third(self.work / "lower_third.png")
        shots = [Shot(None, image=visuals.title_card(day, script.title, self.work / "title.png"), seconds=2.5)]
        for i, seg in enumerate(script.segments):
            frame = self.work / f"frame_{i:02d}.png"
            v = seg.visual or {}
            if seg.kind == "anchor":
                shots.append(Shot(audios[i], image=None if i in clips else portrait, video=clips.get(i),
                                  overlay=lower, text=seg.text))
                continue
            if v.get("type") == "chart" and v.get("symbol") in by_symbol:
                visuals.chart(day, by_symbol[v["symbol"]], frame)
            elif v.get("type") == "headline" and isinstance(v.get("index"), int) and v["index"] < len(heads):
                visuals.headline(day, heads[v["index"]], frame)
            else:
                visuals.board(day, quotes, frame)
            shots.append(Shot(audios[i], image=frame, text=seg.text))
        shots.append(Shot(None, image=visuals.end_card(day, self.work / "end.png"), seconds=4.0))

        video = assemble(shots, self.work / "build", self.work / f"stateverge_finance_{day}.mp4", WATERMARK)
        problems = list(script.problems) + ([anchor_note] if anchor_note else [])
        ep = {"day": day, "script": script.to_dict(), "quotes": [q.to_dict() for q in quotes],
              "headlines": [h.to_dict() for h in heads], "problems": problems,
              "anchor_clips": len(clips), "video_file": video.name}
        ep_path = self.root / "episodes" / f"{day}.json"
        _write(ep_path, ep)
        sources = "\n".join(f"- {h.source}: {h.title} {h.url}" for h in heads[:10])
        body = (f"{script.description}\n\n本节目由 AI 虚拟主播播报,内容由 AI 辅助整理,不构成投资建议。\n\n"
                f"新闻来源:\n{sources}" + ("\n\n⚠️ 待核对:\n- " + "\n- ".join(problems) if problems else ""))
        try:
            url = self.publish(video, f"finance-{day}-{datetime.now(timezone.utc):%H%M%S}", script.title, body)
        except OSError as e:
            url, problems = "", problems + [f"上传 release 失败:{e}"]
        item.update(status="rendered", episode=str(ep_path.relative_to(self.root)), video_url=url,
                    title=script.title, problems=problems, anchor_clips=len(clips))
        self.log.append(f"成片:{script.title} → {url or video}")

    def run(self) -> list[str]:
        path = self.root / "requests.json"
        queue = _read(path, [])
        for item in queue:
            if item.get("status", "pending") != "pending":
                continue
            item["processed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            try:
                self.episode(item)
            except RuntimeError as e:
                item.update(status="error", failures=[str(e)[:500]])
        if queue:
            _write(path, queue)
        return self.log
