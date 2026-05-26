#!/usr/bin/env python3
"""
批量运行 stateverge_shorts_factory.py 生成多条 YouTube Shorts。

用法：
    python3 scripts/run_shorts_batch.py
    python3 scripts/run_shorts_batch.py --only ai_jobs_short_01,housing_truth_01
    python3 scripts/run_shorts_batch.py --host-dir assets/host/runway --max-parallel 1

行为：
- 内置 10 条主题（slug + title + 5 行中文短文案）
- 为每条写出 topics/<slug>/shorts/script.txt
- 调用 scripts/stateverge_shorts_factory.py 生成 final_short.mp4
- 渲染完成后检查 topics/<slug>/shorts/output/final_short.mp4 是否存在
- 打印成功/失败汇总
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List


def _root() -> Path:
    return Path(
        os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")
    ).resolve()


@dataclass
class Topic:
    slug: str
    title: str
    script: str


TOPICS: List[Topic] = [
    Topic(
        slug="ai_jobs_short_01",
        title="AI正在淘汰普通人",
        script=(
            "AI，已经开始淘汰普通人。\n"
            "效率提升了，但岗位减少了。\n"
            "问题不是你不努力。\n"
            "而是重复劳动，正在被机器接管。\n"
            "未来最危险的，是没有新技能的人。\n"
        ),
    ),
    Topic(
        slug="young_people_lie_flat_01",
        title="年轻人为什么开始躺平",
        script=(
            "年轻人不是懒，是算清了账。\n"
            "房价、加班、彩礼，三座山压着。\n"
            "拼命三年，买不起一个厕所。\n"
            "躺平不是堕落，是止损。\n"
            "真正的问题，不是人，是规则。\n"
        ),
    ),
    Topic(
        slug="housing_truth_01",
        title="房价真相",
        script=(
            "房价不骗人，情绪才骗人。\n"
            "看现金流，别看朋友圈。\n"
            "利率涨一个点，月供翻一倍。\n"
            "城市分化，比你想的还狠。\n"
            "别用过去的剧本，押未来的筹码。\n"
        ),
    ),
    Topic(
        slug="middle_class_disappear_01",
        title="中产正在消失",
        script=(
            "中产，正在悄悄消失。\n"
            "上不去，也回不到普通。\n"
            "工资涨了，购买力却跌了。\n"
            "一场大病，掏空十年积蓄。\n"
            "中产从来不是阶层，是幻觉。\n"
        ),
    ),
    Topic(
        slug="save_money_truth_01",
        title="为什么你存不到钱",
        script=(
            "你不是赚得少，是漏得多。\n"
            "外卖、订阅、打车，一月偷走三千。\n"
            "工资到手，就被预定走了。\n"
            "存钱不是抠门，是夺回主动权。\n"
            "先看支出，再谈财富。\n"
        ),
    ),
    Topic(
        slug="city_escape_01",
        title="为什么年轻人逃离一线城市",
        script=(
            "年轻人正在逃离一线城市。\n"
            "不是认输，是算明白了。\n"
            "北上广给机会，也吃掉一切。\n"
            "房租吞工资，通勤偷青春。\n"
            "回去不是失败，是换个赛道。\n"
        ),
    ),
    Topic(
        slug="work_exchange_01",
        title="打工的本质",
        script=(
            "打工的本质，是时间换钱。\n"
            "老板不是付工资，是租你人生。\n"
            "你越熟练，越容易被替代。\n"
            "真正值钱的，是不可复制的能力。\n"
            "别只卖时间，要造资产。\n"
        ),
    ),
    Topic(
        slug="ai_skill_gap_01",
        title="未来最危险的人群",
        script=(
            "未来最危险的，不是失业的人。\n"
            "是不会用 AI 的人。\n"
            "工具进化了，人却没跟上。\n"
            "同样岗位，效率能差十倍。\n"
            "不学新技能，淘汰只是时间问题。\n"
        ),
    ),
    Topic(
        slug="us_living_cost_01",
        title="美国普通人生活真相",
        script=(
            "美国月薪一万，听起来很美。\n"
            "房租三千，医保一千。\n"
            "一次小手术，账单两万起。\n"
            "看着高薪，实际月光。\n"
            "数字不骗人，汇率才骗人。\n"
        ),
    ),
    Topic(
        slug="effort_is_not_enough_01",
        title="努力为什么没用",
        script=(
            "努力，越来越不值钱。\n"
            "因为你在错的赛道上拼命。\n"
            "选择大于努力，方向决定结果。\n"
            "用战术的勤奋，掩盖战略的懒。\n"
            "先看清局，再下场拼命。\n"
        ),
    ),
]


def log(msg: str) -> None:
    print(f"[batch] {msg}", flush=True)


def write_script(root: Path, t: Topic) -> Path:
    sdir = root / "topics" / t.slug / "shorts"
    sdir.mkdir(parents=True, exist_ok=True)
    p = sdir / "script.txt"
    p.write_text(t.script, encoding="utf-8")
    return p


def _diagnose(root: Path, slug: str) -> tuple[str, list[str]]:
    """调用 diagnose_shorts_outputs.py --json 拿单条 verdict + notes。"""
    diag = root / "scripts" / "diagnose_shorts_outputs.py"
    if not diag.is_file():
        return "UNKNOWN", ["缺少 scripts/diagnose_shorts_outputs.py"]
    r = subprocess.run(
        [sys.executable, str(diag), "--only", slug, "--json"],
        cwd=str(root), capture_output=True, text=True, check=False,
    )
    try:
        data = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return "UNKNOWN", [f"diagnose 输出无法解析：{(r.stdout or '')[-200:]}"]
    for item in data:
        if item.get("slug") == slug:
            return item.get("verdict", "UNKNOWN"), list(item.get("notes") or [])
    return "UNKNOWN", ["diagnose 没返回该 slug"]


def run_one(root: Path, t: Topic, host_dir: str, factory: Path) -> tuple[bool, float, str]:
    sfile = write_script(root, t)
    out_mp4 = root / "topics" / t.slug / "shorts" / "output" / "final_short.mp4"
    cmd = [
        sys.executable,
        str(factory),
        "--slug", t.slug,
        "--title", t.title,
        "--host-dir", host_dir,
        "--script-file", str(sfile),
    ]
    log(f"▶ {t.slug}  ({t.title})")
    log(f"    $ {' '.join(cmd)}")
    t0 = time.time()
    p = subprocess.run(
        cmd, cwd=str(root), capture_output=True, text=True, check=False
    )
    elapsed = time.time() - t0
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "")[-1500:]
        log(f"  ✗ {t.slug} factory exit={p.returncode} ({elapsed:.1f}s)\n{err}")
        return False, elapsed, f"FAIL_FACTORY_EXIT_{p.returncode}"
    if not out_mp4.is_file():
        log(f"  ✗ {t.slug} 渲染完成但 final_short.mp4 不存在 ({elapsed:.1f}s)")
        return False, elapsed, "FAIL_MISSING_FINAL"

    verdict, notes = _diagnose(root, t.slug)
    sz = out_mp4.stat().st_size / 1024 / 1024
    if verdict == "READY":
        log(f"  ✓ {t.slug}  {out_mp4.relative_to(root)}  {sz:.2f} MB  ({elapsed:.1f}s) verdict=READY")
        return True, elapsed, str(out_mp4.relative_to(root))
    log(f"  ✗ {t.slug}  verdict={verdict}  ({elapsed:.1f}s, {sz:.2f} MB)")
    for n in notes:
        log(f"      └─ {n}")
    return False, elapsed, verdict


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-dir", default="assets/host/runway")
    ap.add_argument(
        "--only",
        default="",
        help="逗号分隔的 slug 子集（默认全部）",
    )
    ap.add_argument(
        "--continue-on-error",
        action="store_true",
        default=True,
    )
    args = ap.parse_args(argv)

    root = _root()
    factory = root / "scripts" / "stateverge_shorts_factory.py"
    if not factory.is_file():
        log(f"FATAL: 找不到 {factory}")
        return 2

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    topics = [t for t in TOPICS if (not only or t.slug in only)]
    if not topics:
        log("没有可运行的主题（--only 过滤后为空）")
        return 2

    log(f"开始批量生成：{len(topics)} 条 (root={root})")
    log(f"host-dir = {args.host_dir}")

    results: list[tuple[Topic, bool, float, str]] = []
    for t in topics:
        ok, dur, info = run_one(root, t, args.host_dir, factory)
        results.append((t, ok, dur, info))
        if not ok and not args.continue_on_error:
            break

    print()
    print("=" * 72)
    print(f" 批量结果汇总  (共 {len(results)} 条)")
    print("=" * 72)
    ok_n = sum(1 for _, ok, _, _ in results if ok)
    fail_n = len(results) - ok_n
    total_dur = sum(d for _, _, d, _ in results)
    for t, ok, d, info in results:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {t.slug:<32} {d:6.1f}s  {t.title}")
        if not ok:
            print(f"      原因：{info}")
        else:
            print(f"      输出：{info}")
    print("-" * 72)
    print(f" 成功 {ok_n} / 失败 {fail_n} / 总耗时 {total_dur:.1f}s")
    print("=" * 72)

    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
