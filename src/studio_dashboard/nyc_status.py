from __future__ import annotations

from datetime import datetime
from pathlib import Path

from utils.storage_paths import get_stateverge_volume

ROOT = get_stateverge_volume()


def read_log_lines(path: Path, n: int = 200) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.splitlines()[-n:]


def today_filter(lines: list[str]) -> list[str]:
    today = datetime.now().strftime("%Y-%m-%d")
    return [l for l in lines if today in l]


def get_status() -> dict:
    logs = read_log_lines(ROOT / "07_AUTOMATION/logs/launchd.out")
    logs = today_filter(logs)

    return {
        "long": sum("SUCCESS long" in l for l in logs),
        "short": sum("SUCCESS short" in l for l in logs),
        "generated": sum("SHORT_CREATED" in l for l in logs),
        "errors": sum(("ERROR" in l or "FAIL" in l) for l in logs),
        "logs": logs[-50:],
    }


def volume_mount_ok() -> bool:
    return ROOT.is_dir()
