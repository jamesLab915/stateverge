#!/usr/bin/env python3
"""
StateVerge YouTube Shorts 自动成片：竖屏 1080x1920，主持人 + 素材 + 大字幕 + ffprobe 校验。

独立脚本：不修改 `src/`，不调用长视频 `auto_video` 管线。依赖本机 `ffmpeg` / `ffprobe`。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

# --- layout & constants ---

W, H, FPS, AR = 1080, 1920, 30, 48_000
CJK_RE = re.compile(r"[\u4e00-\u9fff]")

MAC_FONT_CANDS: list[tuple[str, str]] = [
    ("/System/Library/Fonts/PingFang.ttc", "PingFang SC"),
    ("/System/Library/Fonts/STHeiti Light.ttc", "Heiti"),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "Arial Unicode MS"),
]


def _root() -> Path:
    return Path(
        os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")
    ).resolve()


def log(msg: str) -> None:
    print(f"[shorts_factory] {msg}", flush=True)


def run_ffmpeg(
    args: list[str], *, label: str = "ffmpeg", cwd: Path | None = None
) -> None:
    p = subprocess.run(
        list(args), cwd=str(cwd) if cwd else None, capture_output=True, text=True
    )
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "")[:12000]
        log(f"FAIL: {label}\n$ {' '.join(args)[:2000]}\n{err}")
        raise RuntimeError(f"{label} failed (exit={p.returncode})")
    if label:
        log(f"ok: {label}")


def cjk_count(s: str) -> int:
    return len(CJK_RE.findall(s or ""))


def ffprobe_duration(p: Path) -> float:
    p2 = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(p),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if p2.returncode != 0:
        return 0.0
    try:
        return max(0.0, float((p2.stdout or "0").strip() or 0.0))
    except ValueError:
        return 0.0


def ffprobe_info(p: Path) -> dict[str, Any]:
    p2 = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(p),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if p2.returncode != 0:
        return {}
    try:
        return json.loads(p2.stdout or "{}")
    except json.JSONDecodeError:
        return {}


# --- templates (内置关键词 → 短文案) ---

T_AI = """{title}，已经开始淘汰普通人。
效率提升了，但岗位减少了。
问题不是你不努力。
而是重复劳动，正在被机器接管。
未来最危险的，是没有新技能的人。"""

T_YOUTH = """年轻人不躺，也得算账。
你拼的是时间，别人算的是系统。
看懂了规则，再谈内卷不迟。"""

T_HOUSE = """房价不骗人，情绪才骗人。
现金流、利率、城市分化——三个词拆开看。"""

T_US = """美国生活成本不只看汇率。
吃住行税，全都要换成人话。"""

T_MONEY = """存钱，不是抠门。
是先把支出摊在阳光下。"""

T_GEN = """{title}，先拆三段。
你听到的，是立场还是数据？
下结论前，看约束条件。"""


def pick_template(title: str) -> str:
    t = title
    if any(k in t for k in ("AI", "人工智能", "工作", "失业", "ai")) or "ai" in t.lower():
        return T_AI
    if any(k in t for k in ("年轻人", "躺平", "内卷")):
        return T_YOUTH
    if any(k in t for k in ("房价", "房产", "中介")):
        return T_HOUSE
    if "美国" in t or "生活成本" in t:
        return T_US
    if any(k in t for k in ("存钱", "消费")):
        return T_MONEY
    return T_GEN


def autogen_script(title: str) -> str:
    b = pick_template(title)
    s = (b.format(title=title) if "{title}" in b else b).strip()
    out: list[str] = []
    n = 0
    for line in s.splitlines():
        l = line.strip()
        if not l:
            continue
        if n + cjk_count(l) > 60 and out:
            break
        if len(l) > 1:
            out.append(l)
            n += cjk_count(l)
    if not out:
        out = [f"{title}。", "说三点。"]
    return "\n".join(out) + "\n"


# --- 关键词 (中英) ---

def keywords_for_title(title: str) -> list[str]:
    tl = title.lower()
    if "ai" in tl or any(
        k in title for k in ("人工智能", "淘汰", "工作", "AI", "ai")
    ):
        return [
            "artificial intelligence office",
            "people working office",
            "robot technology",
            "unemployment worker",
            "city commuting",
        ]
    if "躺平" in title or "内卷" in title or "年轻人" in title:
        return [
            "young people city",
            "factory worker",
            "subway crowd",
            "office overtime",
            "apartment buildings",
        ]
    if any(k in title for k in ("房价", "房产", "中介")):
        return [
            "real estate agent",
            "apartment building",
            "mortgage contract",
            "city skyline",
            "housing market",
        ]
    b = re.sub(
        r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", title, flags=re.U
    ).strip()
    return [
        f"{b} city documentary",
        "night traffic street asia",
        "crowd walking slow motion",
        "data screen office",
        "aerial city skyline 4k",
    ][:5]


# --- media pool ---

def rglob_videos(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    ex = {".mp4", ".mov", ".webm", ".m4v"}
    out: list[Path] = []
    for p in d.rglob("*"):
        if p.is_file() and p.suffix.lower() in ex and p.stat().st_size > 2000:
            out.append(p)
    return sorted(set(p.resolve() for p in out), key=lambda x: str(x).lower())


def collect_broll(root: Path, slug: str) -> list[Path]:
    t = root / "topics" / slug
    cands = [t / "assets" / "raw", root / "assets" / "envato", t / "envato"]
    s: set[Path] = set()
    for d in cands:
        for p in rglob_videos(d):
            s.add(p)
    return sorted(s, key=lambda x: str(x).lower())


def host_mp4s(host_dir: Path) -> list[Path]:
    if not host_dir.is_dir():
        return []
    return [p for p in rglob_videos(host_dir) if p.suffix.lower() == ".mp4"]


# --- 在线抓取（不中断） ---

def run_selector_nofail(root: Path, slug: str, script: Path) -> None:
    sel = root / "src" / "integrations" / "media_sources" / "selector.py"
    if not sel.is_file():
        log("INFO: 未找到 src.integrations.media_sources.selector，跳过网络抓取。")
        return
    env = {**os.environ, "PYTHONPATH": f"{str(root)}" + os.pathsep + os.environ.get("PYTHONPATH", "")}
    cmd = [
        sys.executable,
        "-m",
        "src.integrations.media_sources.selector",
        "--topic",
        slug,
        "--root",
        str(root),
        "--input",
        str(script),
        "--per-page",
        "3",
        "--max-queries",
        "4",
    ]
    if (os.environ.get("USE_DVIDS", "") or "").strip().lower() not in ("1", "true", "yes"):
        cmd += ["--source", "pexels,pixabay"]
    r = subprocess.run(
        cmd, cwd=str(root), env=env, capture_output=True, text=True, check=False
    )
    if (r.stdout or ""):
        print(r.stdout, end="", flush=True)
    if r.returncode != 0:
        log(
            f"WARN: selector exit={r.returncode}（继续用本地素材/Envato 兜底） stderr={(r.stderr or '')[:500]!r}"
        )
    if not (os.environ.get("PEXELS_API_KEY") or "").strip():
        log("WARN: 未设置 PEXELS_API_KEY（Pexels 结果会为空，属预期，非静默）")
    if not (os.environ.get("PIXABAY_API_KEY") or "").strip():
        log("WARN: 未设置 PIXABAY_API_KEY（Pixabay 结果会为空，属预期，非静默）")


# --- timeline ---

@dataclass
class TSeg:
    type: str
    start: float
    duration: float
    text: str
    query: str = ""


def parse_lines(txt: str) -> list[str]:
    ls = [x.strip() for x in (txt or "").splitlines() if x.strip()]
    return ls or ["点进来。", "收工。"]


def build_timeline(lines: list[str], target: float, queries: list[str]) -> list[TSeg]:
    L = (lines + ["。"] * 5)[:5]
    base = [0.10, 0.24, 0.20, 0.24, 0.22]
    d = [round(target * w, 2) for w in base]
    d[-1] = round(d[-1] + (target - sum(d)), 2)
    out: list[TSeg] = []
    qj = 0
    for i in range(5):
        kind = "host" if i % 2 == 0 else "broll"
        q = ""
        if kind == "broll" and queries:
            q = queries[qj % len(queries)]
            qj += 1
        dur = d[i]
        out.append(
            TSeg("host" if kind == "host" else "broll", 0.0, dur, L[i], q)
        )
    acc = 0.0
    for s in out:
        s.start = round(acc, 3)
        acc += s.duration
    return out


def recompute_starts(segs: list[TSeg]) -> None:
    t = 0.0
    for s in segs:
        s.start = round(t, 3)
        t += float(s.duration)


# --- ffmpeg: one vertical segment ---

VVF = (
    f"scale={W}:{H}:force_original_aspect_ratio=increase,"
    f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2,setsar=1,fps={FPS},format=yuv420p"
)

FI, FO = 0.12, 0.12


def vchain(dur: float) -> str:
    return f"{VVF},fade=t=in:st=0:d={FI},fade=t=out:st={max(0.0, dur - FO - 0.02)}:d={FO}"


def has_audio(p: Path) -> bool:
    d = ffprobe_info(p)
    for s in d.get("streams") or []:
        if s.get("codec_type") == "audio":
            return True
    return False


def render_seg(
    src: Path, dur: float, out: Path, *, keep_audio: bool, ss: float, tag: str
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    ha = has_audio(src) and keep_audio
    pre = [
        "ffmpeg",
        "-hide_banner",
        "-y",
    ]
    if ss > 0.05:
        pre += ["-ss", f"{ss:.2f}"]
    pre += ["-i", str(src), "-t", f"{dur:.3f}", "-vf", vchain(dur), "-map", "0:v:0"]
    if ha:
        pre += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2"]
    else:
        pre += [
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={AR}",
            "-map",
            "1:a:0",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            str(AR),
            "-ac",
            "2",
        ]
    pre += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        str(out),
    ]
    run_ffmpeg(pre, label=tag)


def concat_parts(parts: list[Path], out: Path, work: Path) -> None:
    lst = work / "concat.txt"
    lines: list[str] = []
    for p in parts:
        s = p.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{s}'\n")
    lst.write_text("".join(lines), encoding="utf-8")
    try:
        run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(lst),
                "-c",
                "copy",
                str(out),
            ],
            label="concat_copy",
        )
    except RuntimeError:
        run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(lst),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(out),
            ],
            label="concat_reencode",
        )


def mix_music(vid: Path, mus: Optional[Path], out: Path) -> None:
    if mus and mus.is_file():
        d = ffprobe_duration(vid)
        run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-i",
                str(vid),
                "-stream_loop",
                "-1",
                "-i",
                str(mus),
                "-filter_complex",
                (
                    f"[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=0:{max(0.2, d)},asetpts=PTS-STARTPTS,aresample={AR},volume=0.1[bed];"
                    f"[0:a]aresample={AR}[a0];[a0][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
                ),
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                str(AR),
                str(out),
            ],
            label="bed_mix",
        )
    else:
        run_ffmpeg(
            ["ffmpeg", "-hide_banner", "-y", "-i", str(vid), "-c", "copy", str(out)],
            label="no_bed",
        )


def ass_escape(s: str) -> str:
    return s.replace("{", r"\{").replace("}", r"\}")


def t_ass(sec: float) -> str:
    s = max(0.0, float(sec))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    s2 = s - 3600 * h - 60 * m
    return f"{h}:{m:02d}:{s2:05.2f}"


def write_ass(
    out: Path, segs: list[TSeg], family: str, size: int, fontsdir: str
) -> None:
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
ScaledBorderAndShadow: yes
WrapStyle: 2
[V4+ Styles]
Format: Name,Fontname,FontSize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,UnderLine,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{family},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4,0,2,20,20,100,0
[Events]
Format: Layer, Start, End, Style, Text
"""
    ev: list[str] = []
    for c in segs:
        t0 = c.start
        t1 = c.start + c.duration
        body = textwrap.fill(
            c.text.replace("\n", " "), width=9, break_long_words=False
        )
        for chunk in (body or "…").splitlines():
            ev.append(
                f"Dialogue: 0,{t_ass(t0)},{t_ass(t1)},Default,{ass_escape(chunk)}"
            )
    out.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


def burn_subs(v: Path, ass: Path, fontsdir: str, o: Path) -> None:
    ap = ass.resolve().as_posix().replace(":", r"\:")
    fd = Path(fontsdir).resolve().as_posix().replace(":", r"\:")
    run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(v),
            "-vf",
            f"ass={ap}:fontsdir={fd}",
            "-c:a",
            "copy",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            str(o),
        ],
        label="ass_burn",
    )


def find_font() -> tuple[str, str, str]:
    for path, name in MAC_FONT_CANDS:
        p = Path(path)
        if p.is_file():
            return str(p), p.parent.as_posix(), name
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts", "DejaVu Sans"


def find_music(root: Path) -> Optional[Path]:
    d = root / "assets" / "envato" / "music"
    if not d.is_dir():
        return None
    for e in (".mp3", ".m4a", ".aac", ".wav", ".flac"):
        for f in sorted(d.glob(f"*{e}")):
            if f.is_file():
                return f
    return None


# --- 分配素材 ---

def build_render_plan(
    segs: list[TSeg],
    host: list[Path],
    broll: list[Path],
) -> tuple[list[dict[str, Any]], bool]:
    if not broll:
        raise SystemExit(2)
    h_use: dict[Path, int] = {p: 0 for p in host} if host else {}
    bi = 0
    h_i = 0
    out: list[dict[str, Any]] = []
    host_fb = not bool(host)
    n_br = 0
    for s in segs:
        d = float(s.duration)
        if s.type == "host":
            if not host:
                p = broll[bi % len(broll)]
                bi += 1
                out.append(
                    {
                        "src": p,
                        "dur": d,
                        "keep": True,
                        "ss": 0.0,
                    }
                )
            else:
                p = host[h_i % len(host)]
                h_i += 1
                if h_use and h_use.get(p, 0) >= 2 and len(host) > 1:
                    p = host[(h_i + 1) % len(host)]
                h_use[p] = h_use.get(p, 0) + 1
                d_in = ffprobe_duration(p) or 5.0
                want = 10.0 if d_in >= 8.0 else 5.0
                d_use = min(d, want, d_in) if d_in else d
                out.append({"src": p, "dur": d_use, "keep": True, "ss": 0.0})
        else:
            a = broll[bi % len(broll)]
            n_br += 1
            src = a
            ss = 0.0
            if len(broll) == 1:
                base = max(1.0, (ffprobe_duration(broll[0]) or 6.0) * 0.2)
                ss = min(
                    n_br * 0.6,
                    max(0.0, (ffprobe_duration(broll[0]) or 6.0) - d - 0.2),
                ) % (base * 2 + 0.01)
            else:
                b = broll[(bi + 1) % len(broll)]
                if b != a and n_br % 2:
                    src = b
            bi += 1
            out.append({"src": src, "dur": d, "keep": False, "ss": ss})
    if len(broll) == 1 and len([x for x in segs if x.type == "broll"]) > 1:
        log("WARN: 单素材 b-roll 多次取不同起剪点，以满足「不全片一段撑满」的约束。")
    return out, host_fb


def write_youtube_md(
    path: Path, title: str, segs: list[TSeg], kws: list[str]
) -> None:
    tags = "AI, 职场, 财经, 纪录片, 中文"
    t5 = [
        f"「{title}」一句话把事说清",
        f"{title} 真相，30 秒说穿",
        f"拆「{title}」：不灌鸡汤",
        f"别用情绪看「{title}」",
        f"{title} 之后，你怎么选？",
    ]
    md = f"""# {title} — YouTube Shorts 元信息（机器生成，可手改）

## 标题候选（5）
{chr(10).join('- ' + t for t in t5)}

## 描述
- 用数据与约束条件拆解「{title}」。
- 不替代专业建议，欢迎理性讨论。

## Tags
{tags}

## Hashtags
#知识分享 #社会观察 #经济常识 #个人成长

## 置顶评论（可粘贴）
- 你认同一句话里哪个数字最重要？我下期拆给你看。

## 长视频引流 CTA
- 评论区置顶：长视频/深度稿见频道简介链接（StateVerge 长视频主栏目）。

## 本支素材关键词
{", ".join(kws)}
"""
    path.write_text(md, encoding="utf-8")


def verify(p: Path, t_target: float) -> int:
    st = ffprobe_info(p)
    w = h = 0
    vok = aok = False
    for s in st.get("streams", []) or []:
        if s.get("codec_type") == "video":
            vok = True
            w = int(s.get("width") or 0)
            h = int(s.get("height") or 0)
        if s.get("codec_type") == "audio":
            aok = True
    d = float((st.get("format") or {}).get("duration") or 0.0)
    if not (vok and aok and w == W and h == H and 20.0 <= d <= 35.0):
        log(
            f"VERIFY_FAIL: v={vok} a={aok} {w}x{h} need {W}x{H} dur={d:.3f} need[20,35] target~{t_target}"
        )
        return 1
    log(
        f"VERIFY_OK: {W}x{H} dur={d:.2f}s streams ok"
    )
    return 0


# --- main ---

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--host-dir", default="assets/host/runway")
    ap.add_argument("--minutes", type=float, default=0.5)
    ap.add_argument("--script-file", type=Path, default=None)
    args = ap.parse_args(argv)
    root = _root()
    os.chdir(root)
    slug = args.slug.strip()
    title = (args.title or "").strip() or "未命名"
    tsec = max(20.0, min(35.0, float(args.minutes) * 60.0))
    tdir = root / "topics" / slug
    sdir = tdir / "shorts"
    outd = sdir / "output"
    assd = sdir / "assets"
    work = assd / "_work"
    (sdir, outd, assd, work, tdir / "assets" / "raw", tdir / "brief").mkdir(
        parents=True, exist_ok=True
    )
    if args.script_file and args.script_file.is_file():
        script = args.script_file.read_text(encoding="utf-8", errors="replace")
    else:
        script = autogen_script(title)
    sdir.joinpath("script.txt").write_text(script, encoding="utf-8")
    # 供 selector 用
    fbrief = tdir / "brief" / "narration_script.txt"
    if not fbrief.is_file():
        fbrief.write_text(script, encoding="utf-8")
    sfile = sdir / "script.txt"
    if not (os.environ.get("PEXELS_API_KEY") or "").strip():
        log("WARN: PEXELS_API_KEY 未设置：网络素材可能为空，将用本地/Envato。")
    if not (os.environ.get("PIXABAY_API_KEY") or "").strip():
        log("WARN: PIXABAY_API_KEY 未设置：网络素材可能为空，将用本地/Envato。")
    run_selector_nofail(root, slug, sfile)
    broll = collect_broll(root, slug)
    log(f"资源池：{len(broll)} 个视频（raw/Envato）")
    host_dir = (root / args.host_dir) if not Path(args.host_dir).is_absolute() else Path(args.host_dir)
    hlist = host_mp4s(host_dir)
    if not hlist:
        log("HOST_FALLBACK: 未在 host-dir 发现 mp4，主持人段将用 b-roll 代替。")
    lines = parse_lines(script)
    if cjk_count(script) > 60:
        log("WARN: 文案超过 60 字限制（用户示例除外）；仍以 timeline 5 行切片为主。")
    kws = keywords_for_title(title)
    tl = build_timeline(lines, tsec, kws)
    try:
        plan, _hf = build_render_plan(tl, hlist, broll)
    except SystemExit:
        log(
            "没有可用视频素材：请在 topics/<slug>/assets/raw/ 或 assets/envato/ 下放置 mp4 "
            "（网络失败不中断，但最终仍需至少 1 个视频文件作素材池）。"
        )
        return 2
    for i, s in enumerate(tl):
        s.duration = float(plan[i]["dur"])
    recompute_starts(tl)
    sdir.joinpath("timeline.json").write_text(
        json.dumps([asdict(x) for x in tl], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for i, (seg, p) in enumerate(zip(tl, plan)):
        render_seg(
            p["src"],
            p["dur"],
            work / f"seg_{i:02d}.mp4",
            keep_audio=bool(p.get("keep")),
            ss=float(p.get("ss") or 0.0),
            tag=f"seg_{i:02d}",
        )
    parts = sorted(work.glob("seg_*.mp4"))
    c1 = work / "concat1.mp4"
    concat_parts(parts, c1, work)
    mus = find_music(root)
    c2 = work / "with_music.mp4"
    mix_music(c1, mus, c2)
    fpath, fontsdir, family = find_font()
    if not Path(fpath).is_file():
        log("FATAL: 无可用中文字体")
        return 1
    as_f = work / "subs.ass"
    write_ass(as_f, tl, family, 56, fontsdir)
    final = outd / "final_short.mp4"
    burn_subs(c2, as_f, fontsdir, final)
    write_youtube_md(sdir / "youtube_metadata.md", title, tl, kws)
    v = verify(final, tsec)
    return v


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
