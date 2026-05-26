#!/usr/bin/env python3
"""
StateVerge 卷根目录迁移规划（仅 dry-run）。

绝不扫描、读取「私人别碰」目录内部；该名称若出现在路径任何位置亦跳过。

输出：
  /Volumes/StateVerge/07_AUTOMATION/migration_plan.md
  /Volumes/StateVerge/07_AUTOMATION/migration_plan.json
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from stateverge_paths import SSD_ROOT as VOL  # noqa: E402
OUT_DIR = VOL / "07_AUTOMATION"

# 永久排除：路径任意片段包含「私人别碰」或以下子串即跳过
PRIVATE_SEGMENT = "私人别碰"
SKIP_SUBSTRINGS = (
    PRIVATE_SEGMENT,
    ".Trash",
    ".TemporaryItems",
    ".Spotlight-V100",
    ".fseventsd",
    ".DS_Store",
    "node_modules",
    ".git",
    ".venv",
)

# 顶层目录名（或文件）→ 建议相对 VOL 的目标路径
TOP_LEVEL_DESTINATION: dict[str, str] = {
    "NYC_AUTO": "02_PROJECTS/NYC_AUTO",
    "NYC": "02_PROJECTS/NYC",
    "NYC_LIVE": "02_PROJECTS/NYC_LIVE",
    "NYC_UPLOADER": "07_AUTOMATION/NYC_UPLOADER",
    "04_ASSETS": "04_ASSETS",
    "05_CACHE": "05_CACHE",
    "03_OUTPUT": "03_OUTPUT",
    "06_ARCHIVE": "06_ARCHIVE",
    "07_AUTOMATION": "07_AUTOMATION",
    "00_INBOX": "00_INBOX",
    "01_ASSET_LIBRARY": "01_ASSET_LIBRARY",
    "02_PROJECTS": "02_PROJECTS",
}


def path_should_skip(p: Path) -> bool:
    s = str(p)
    if PRIVATE_SEGMENT in s:
        return True
    for sub in SKIP_SUBSTRINGS:
        if sub in s:
            return True
    if "/._" in s or p.name.startswith("._"):
        return True
    return False


def is_under_private(path: Path) -> bool:
    return PRIVATE_SEGMENT in path.parts


def main() -> int:
    if not VOL.is_dir():
        print(f"ERROR: volume not found: {VOL}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    proposals: list[dict] = []
    skipped: list[str] = []
    notes: list[str] = [
        "后续建议：桌面 / Downloads / AirDrop 新素材统一放入 00_INBOX（本脚本不访问本机 Desktop）。",
        "本计划仅处理卷根目录下一层条目；不递归、不进入子目录内容。",
        "「私人别碰」：不扫描、不索引、不提议移动其内部任何项。",
    ]

    try:
        children = sorted(VOL.iterdir(), key=lambda x: x.name.lower())
    except OSError as e:
        print(f"ERROR: cannot list {VOL}: {e}")
        return 1

    for child in children:
        if child.name == PRIVATE_SEGMENT:
            skipped.append(str(child))
            continue
        if path_should_skip(child):
            skipped.append(str(child))
            continue

        name = child.name
        dest_rel = TOP_LEVEL_DESTINATION.get(name)
        if dest_rel is None:
            proposals.append(
                {
                    "source": str(child.relative_to(VOL)),
                    "destination": None,
                    "action": "review_manual",
                    "type": "dir" if child.is_dir() else "file",
                    "note": "根目录下未在规则表中的项，请人工决定是否移入 02_PROJECTS / 06_ARCHIVE / 00_INBOX。",
                }
            )
            continue

        dest_abs = VOL / dest_rel
        if child.resolve() == dest_abs.resolve():
            proposals.append(
                {
                    "source": name,
                    "destination": dest_rel,
                    "action": "no_op",
                    "type": "dir" if child.is_dir() else "file",
                    "note": "已在目标布局位置。",
                }
            )
        else:
            proposals.append(
                {
                    "source": name,
                    "destination": dest_rel,
                    "action": "mv",
                    "type": "dir" if child.is_dir() else "file",
                    "note": f"建议: mv {VOL}/{name} -> {dest_abs}",
                }
            )

    payload = {
        "volume": str(VOL),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "private_folder_respected": PRIVATE_SEGMENT,
        "skipped_paths": skipped,
        "proposals": proposals,
        "notes": notes,
    }

    json_path = OUT_DIR / "migration_plan.json"
    md_path = OUT_DIR / "migration_plan.md"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# StateVerge 迁移规划（dry-run）",
        "",
        f"- 生成时间（UTC）：`{payload['generated_at']}`",
        f"- 卷：`{VOL}`",
        f"- **永不触碰**：路径含 `{PRIVATE_SEGMENT}` 的条目（根目录下列名跳过，不读内部）。",
        "",
        "## 跳过的根条目",
        "",
    ]
    if skipped:
        for sp in skipped:
            lines.append(f"- `{sp}`")
    else:
        lines.append("- （无）")
    lines.extend(["", "## 迁移建议", ""])
    for pr in proposals:
        lines.append(f"### `{pr['source']}`")
        lines.append(f"- **类型**: {pr['type']}")
        lines.append(f"- **动作**: `{pr['action']}`")
        if pr.get("destination"):
            lines.append(f"- **目标**: `{pr['destination']}`")
        lines.append(f"- {pr.get('note', '')}")
        lines.append("")

    lines.extend(["## 备注", ""])
    for n in notes:
        lines.append(f"- {n}")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"WROTE: {json_path}")
    print(f"WROTE: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
