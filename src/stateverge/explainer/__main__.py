"""``PYTHONPATH=src python3 -m stateverge.explainer cloud-run [--root data/explainer]``

data/explainer/requests.json entries:
    {"status": "pending", "action": "fetch", "urls": [...]}      → sources/*.txt
    {"status": "pending", "action": "render", "episode": "ep001"} → video (release asset)
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .sources import fetch


def run(root: Path) -> list[str]:
    path = root / "requests.json"
    queue = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    log = []
    for item in queue:
        if item.get("status", "pending") != "pending":
            continue
        item["processed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        try:
            if item.get("action") == "fetch":
                done, failed = [], []
                for url in item.get("urls", []):
                    try:
                        done.append(str(fetch(url, root / "sources").relative_to(root)))
                    except Exception as e:  # noqa: BLE001 - record every failure
                        failed.append(f"{url}: {type(e).__name__}: {e}"[:300])
                item.update(status="done" if not failed else "partial", files=done, failures=failed)
            elif item.get("action") == "youtube":
                from .youtube import report

                res = report(item.get("handles", []))
                (root / "youtube_check.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                                                         encoding="utf-8")
                item.update(status="done", file="youtube_check.json")
            elif item.get("action") == "render":
                from .render import render_episode

                item.update(render_episode(root, item["episode"], item))
            else:
                item.update(status="error", failures=[f"unknown action {item.get('action')}"])
        except Exception as e:  # noqa: BLE001
            item.update(status="error", failures=[f"{type(e).__name__}: {e}"[:500]])
        log.append(f"{item.get('action')}: {item['status']}")
    if queue:
        path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stateverge.explainer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cr = sub.add_parser("cloud-run")
    cr.add_argument("--root", default="data/explainer")
    args = ap.parse_args(argv)
    print("\n".join(run(Path(args.root))) or "nothing pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
