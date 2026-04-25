"""
Write ``mix/timeline.json`` from ``video/*.mp4``, ``envato/*.mp4``,
and ``sources/audio/*.{wav,mp3,m4a}`` (ffprobe for interview length).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import deque
from pathlib import Path
from typing import Any, Literal, Optional, Sequence, cast

from .build_video import _root, probe_format_duration, topic_mix_paths

_DEF_INTERVIEW = 10.0

K = Literal["L", "E"]

_WIDE_HINT = re.compile(
    r"wide|establish|aerial|drone|pano|master|sweep|orbit|大|全|广|景|epic|overview",
    re.IGNORECASE,
)
_DETAIL_HINT = re.compile(
    r"detail|close|macro|tight|细节|特写|近|微|bokeh",
    re.IGNORECASE,
)


def _rel(root: Path, topic: str, p: Path) -> str:
    base = (root / "topics" / topic).resolve()
    return p.resolve().relative_to(base).as_posix()


def _mp4s(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(
        [p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"],
        key=lambda x: x.name,
    )


def _audios(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    o: list[Path] = []
    for p in d.iterdir():
        if p.is_file() and p.suffix.lower() in (".wav", ".mp3", ".m4a"):
            o.append(p)
    return sorted(o, key=lambda x: x.name)


def _pick_ltx_opening(
    ltxs: list[Path], rng: random.Random
) -> tuple[Path, Path]:
    """LTX(大场景), LTX(细节); 前两个 L 位用这两个文件（关键词或顺序回退）。"""
    if len(ltxs) < 2:
        raise ValueError(
            "smart_mode: need at least 2 LTX mp4s under video/ (first two: 大场景 + 细节)."
        )
    s = list(ltxs)
    w_score = {p: _WIDE_HINT.search(p.name) is not None for p in s}
    d_score = {p: _DETAIL_HINT.search(p.name) is not None for p in s}
    wide = max(
        s,
        key=lambda p: (w_score.get(p, False) and not d_score.get(p, False), s.index(p)),
    )
    s_wo = [p for p in s if p != wide]
    detail = max(
        s_wo, key=lambda p: (d_score.get(p, False) and not w_score.get(p, False), s.index(p))
    )
    if not (w_score[wide] or d_score[wide]) and not (w_score[detail] or d_score[detail]):
        wide, detail = s[0], s[1] if len(s) > 1 and s[1] != s[0] else s[0]
    if wide == detail and len(s) > 1:
        detail = s[1] if s[0] == wide else s[0]
    return wide, detail


def _can_append_kind(seq: list[K], t: K) -> bool:
    if not seq:
        return t == "L"
    run = 0
    last = seq[-1]
    for j in range(len(seq) - 1, -1, -1):
        if seq[j] == last:
            run += 1
        else:
            break
    if t != last:
        return True
    if t == "L" and run >= 3:
        return False
    if t == "E" and run >= 2:
        return False
    return True


def _kinds_is_valid(kinds: Sequence[K], n_l: int, n_e: int) -> bool:
    if len(kinds) != n_l + n_e:
        return False
    if tuple(kinds[0:3]) != ("L", "L", "E"):
        return False
    if sum(1 for k in kinds if k == "L") != n_l or sum(1 for k in kinds if k == "E") != n_e:
        return False
    seq: list[K] = []
    for t in kinds:
        if not _can_append_kind(seq, t):
            return False
        seq.append(t)
    return True


def _dfs_kinds(
    nlr: int,
    ner: int,
    prefix: list[K],
    rng: random.Random,
    *,
    n_l: int,
    n_e: int,
    prefer_e: bool = False,
) -> Optional[list[K]]:
    if nlr < 0 or ner < 0:
        return None
    if nlr == 0 and ner == 0:
        return list(prefix)
    n_total = n_l + n_e
    n_done = len(prefix)
    pos_frac = n_done / max(1, n_total)
    high_zone = pos_frac >= 0.7
    opts: list[K] = []
    if nlr:
        opts.append("L")
    if ner:
        opts.append("E")
    if not opts:
        return None
    if high_zone and prefer_e and "E" in opts and "L" in opts and rng.random() < 0.7:
        opts = ["E", "L"] if rng.random() < 0.5 else ["L", "E"]
    else:
        rng.shuffle(opts)
    for t in opts:
        if not _can_append_kind(prefix, t):
            continue
        p2 = list(prefix)
        p2.append(t)
        r = _dfs_kinds(
            nlr - (1 if t == "L" else 0),
            ner - (1 if t == "E" else 0),
            p2,
            rng,
            n_l=n_l,
            n_e=n_e,
            prefer_e=prefer_e,
        )
        if r is not None:
            return r
    return None


def _count_e_in_tail(s: list[K], frac: float = 0.3) -> int:
    n = len(s)
    if n == 0:
        return 0
    start = int((1.0 - frac) * n)
    return sum(1 for j in range(start, n) if s[j] == "E")


def _solve_kinds(n_l: int, n_e: int, rng: random.Random) -> list[K]:
    """L,L,E 开头; 连续 L≤3、E≤2；尽量把 E 放进后 30%（在合法序列里小扰动）。"""
    nlr, ner = n_l - 2, n_e - 1
    if nlr < 0 or ner < 0:
        raise ValueError("need at least 2 ltx and 1 env for smart open (rule 1).")
    base: list[K] | None = None
    for _ in range(20000):
        p = bool(rng.random() < 0.55)
        t = _dfs_kinds(nlr, ner, ["L", "L", "E"], rng, n_l=n_l, n_e=n_e, prefer_e=p)
        if t is not None:
            base = cast(list[K], t)
            break
    if not base:
        raise ValueError(
            "smart_mode: 无法满足「连续 LTX≤3、连续 Envato≤2」的排列；请增删 LTX/Envato 数量后重试。"
        )
    best, best_t = _count_e_in_tail(base, 0.3), list(base)
    for _ in range(120):
        i, j = rng.randrange(3, len(base)), rng.randrange(3, len(base))
        if i == j:
            continue
        cand = list(base)
        cand[i], cand[j] = cand[j], cand[i]
        if not _kinds_is_valid(cand, n_l, n_e):
            continue
        sc = _count_e_in_tail(cand, 0.3)
        if sc >= best:
            best, best_t = sc, cand
    return best_t


def _intensity_for_index(i: int, n: int) -> Literal["low", "mid", "high"]:
    if n <= 0:
        return "low"
    p = (i + 0.0) / float(n)
    if p < 0.3:
        return "low"
    if p < 0.7:
        return "mid"
    return "high"


def _ltx_dur(iz: str, rng: random.Random) -> float:
    if iz == "high":
        a, b = 4.0, 5.5
    else:
        a, b = 4.0, 7.0
    return round(rng.uniform(a, b), 2)


def _env_dur(iz: str, rng: random.Random) -> float:
    if iz == "high":
        a, b = 3.0, 4.2
    else:
        a, b = 3.0, 6.0
    return round(rng.uniform(a, b), 2)


def _rhythm_dur(rng: random.Random) -> float:
    return round(rng.uniform(2.0, 3.0), 2)


def build_entries(
    root: Path,
    topic: str,
    *,
    ltx_d: float,
    env_d: float,
    interview_every: int,
) -> list[dict[str, Any]]:
    tdir = root / "topics" / topic
    ltxs = _mp4s(tdir / "video")
    envs = _mp4s(tdir / "envato")
    ints = _audios(tdir / "sources" / "audio")
    if not ltxs:
        raise FileNotFoundError(
            f"no .mp4 under {tdir / 'video'}. Add LTX outputs there first."
        )
    # pattern: every 3 ltx then 1 envato
    pat: list[tuple[str, Path]] = []
    i = 0
    e_j = 0
    n_l = len(ltxs)
    n_e = len(envs)
    while i < n_l:
        for _ in range(3):
            if i < n_l:
                pat.append(("ltx", ltxs[i]))
                i += 1
        if e_j < n_e:
            pat.append(("envato", envs[e_j]))
            e_j += 1
    if i < n_l:
        while i < n_l:
            pat.append(("ltx", ltxs[i]))
            i += 1
    if e_j < n_e:
        for k in range(e_j, n_e):
            pat.append(("envato", envs[k]))
    clips: list[dict[str, Any]] = []
    last_ltx: Optional[str] = None
    it_i = 0
    for npos, (kind, fp) in enumerate(pat, start=1):
        relp = _rel(root, topic, fp)
        if kind == "ltx":
            last_ltx = relp
            clips.append(
                {
                    "type": "ltx",
                    "file": relp,
                    "duration": ltx_d,
                }
            )
        else:
            clips.append(
                {
                    "type": "envato",
                    "file": relp,
                    "duration": env_d,
                }
            )
        if (
            ints
            and last_ltx
            and it_i < len(ints)
            and npos > 0
            and npos % interview_every == 0
        ):
            a = ints[it_i]
            try:
                d_ap = float(probe_format_duration(a))
            except Exception:  # noqa: BLE001
                d_ap = _DEF_INTERVIEW
            clips.append(
                {
                    "type": "interview",
                    "file": _rel(root, topic, a),
                    "visual": last_ltx,
                    "duration": d_ap,
                }
            )
            it_i += 1
    if not clips:
        raise ValueError("empty clip list (internal error)")
    return clips


def build_entries_smart(
    root: Path, topic: str, *, seed: int | None = None
) -> list[dict[str, Any]]:
    """
    smart_mode：开头 L,L,E；5–8 个非 interview 出 1 interview；L/E 连续限；
    第 10,20,… 条为 2–3s 快切；duration 与 high 区偏短、Envato 在尾段更密；每条 clip 带 ``intensity``。
    """
    tdir = root / "topics" / topic
    ltxs = _mp4s(tdir / "video")
    envs = _mp4s(tdir / "envato")
    ints = _audios(tdir / "sources" / "audio")
    if not ltxs:
        raise FileNotFoundError(
            f"no .mp4 under {tdir / 'video'}. Add LTX outputs there first."
        )
    if not envs:
        raise FileNotFoundError(
            f"no .mp4 under {tdir / 'envato'}. smart_mode 第 3 位需要真实 Envato 镜头."
        )
    n_l, n_e = len(ltxs), len(envs)
    rng = random.Random(seed)
    wide_p, det_p = _pick_ltx_opening(ltxs, rng)
    env0 = envs[0]
    ltx_others = [p for p in ltxs if p not in (wide_p, det_p)]
    env_others = list(envs[1:])

    kinds = _solve_kinds(n_l, n_e, rng)
    ltx_deque = deque([wide_p, det_p] + sorted(ltx_others, key=lambda x: x.name))
    env_deque = deque([env0] + sorted(env_others, key=lambda x: x.name))
    vis: deque[tuple[Literal["ltx", "envato"], Path]] = deque()
    for k in kinds:
        if k == "L":
            vis.append(("ltx", ltx_deque.popleft()))
        else:
            vis.append(("envato", env_deque.popleft()))

    n_vis = n_l + n_e
    n_iv_est = min(
        len(ints),
        max(0, (n_vis + 4) // 6) + 1,
    )
    n_ref = max(1, n_vis + n_iv_est)

    clips: list[dict[str, Any]] = []
    last_ltx: Optional[str] = None
    it_i = 0
    since_iv = 0
    next_iv = rng.randint(5, 8)

    def _iv_audio() -> float:
        a = ints[it_i]
        try:
            return float(probe_format_duration(a))
        except Exception:  # noqa: BLE001
            return _DEF_INTERVIEW

    while True:
        nxt1 = len(clips) + 1
        iz0 = _intensity_for_index(len(clips), n_ref)
        # 每 10 条为快切 2–3s（有剩余画面队列时，优先于采访）
        if nxt1 >= 10 and nxt1 % 10 == 0 and vis:
            kind, fp = vis.popleft()
            relp = _rel(root, topic, fp)
            if kind == "ltx":
                last_ltx = relp
            clips.append(
                {
                    "type": kind,
                    "file": relp,
                    "duration": _rhythm_dur(rng),
                    "note": "rhythm_punch",
                    "intensity": iz0,
                }
            )
            since_iv += 1
            continue
        if (
            bool(ints)
            and it_i < len(ints)
            and last_ltx is not None
            and since_iv >= next_iv
        ):
            clips.append(
                {
                    "type": "interview",
                    "file": _rel(root, topic, ints[it_i]),
                    "visual": last_ltx,
                    "duration": _iv_audio(),
                    "intensity": _intensity_for_index(len(clips), n_ref),
                }
            )
            it_i += 1
            since_iv = 0
            next_iv = rng.randint(5, 8)
            continue
        if not vis:
            break
        kind, fp = vis.popleft()
        relp = _rel(root, topic, fp)
        iz1 = _intensity_for_index(len(clips), n_ref)
        if kind == "ltx":
            last_ltx = relp
        d = _ltx_dur(iz1, rng) if kind == "ltx" else _env_dur(iz1, rng)
        clips.append(
            {
                "type": kind,
                "file": relp,
                "duration": d,
                "intensity": iz1,
            }
        )
        since_iv += 1

    ntot = len(clips)
    for i, c in enumerate(clips):
        c["intensity"] = _intensity_for_index(i, ntot)
    if not clips:
        raise ValueError("empty clip list (internal error)")
    return clips


def generate(
    root: Path,
    topic: str,
    *,
    force: bool = False,
    ltx_duration: float = 6.0,
    envato_duration: float = 5.0,
    interview_every: int = 7,
    smart_mode: bool = False,
    seed: int | None = None,
) -> Path:
    p = topic_mix_paths(root, topic)
    tpath = p["timeline"]
    tpath.parent.mkdir(parents=True, exist_ok=True)
    if tpath.is_file() and not force:
        raise RuntimeError(
            f"timeline already exists: {tpath} (use --force to overwrite)"
        )
    if smart_mode:
        clips = build_entries_smart(root, topic, seed=seed)
    else:
        clips = build_entries(
            root,
            topic,
            ltx_d=ltx_duration,
            env_d=envato_duration,
            interview_every=max(1, int(interview_every)),
        )
    with tpath.open("w", encoding="utf-8") as f:
        json.dump(clips, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return tpath


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate mix/timeline.json (does not run ffmpeg).",
    )
    ap.add_argument("--topic", required=True)
    ap.add_argument(
        "--root", type=Path, default=None, help="StateVerge root (default: env/~/StateVerge)"
    )
    ap.add_argument("--force", action="store_true", help="Overwrite mix/timeline.json")
    ap.add_argument(
        "--smart",
        action="store_true",
        help="Constrained LTX/Envato + interviews + intensity + fast cuts (see source).",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed for --smart (reproducible).",
    )
    ap.add_argument("--ltx-duration", type=float, default=6.0, dest="ltx_d")
    ap.add_argument("--envato-duration", type=float, default=5.0, dest="env_d")
    ap.add_argument(
        "--interview-every",
        type=int,
        default=7,
        help="After this many ltx+env pattern clips, insert 1 interview (if audio exists).",
    )
    args = ap.parse_args(argv)
    r = args.root or _root()
    try:
        out = generate(
            r,
            args.topic,
            force=bool(args.force),
            ltx_duration=float(args.ltx_d),
            envato_duration=float(args.env_d),
            interview_every=int(args.interview_every),
            smart_mode=bool(args.smart),
            seed=args.seed,
        )
        print(f"[generate_timeline] wrote {out}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
