"""Aggregate expense JSON files under ``tracking/expenses/``.

Reads every ``*.json`` file in ``tracking/expenses/`` (relative to the
StateVerge repo root) and prints a plain-text summary:

    [expenses]
    total=<amount> USD

    by_category:
    <category>=<amount>
    ...

Each expense file is expected to look like::

    {
      "category": "legal_setup",
      "items": [{"amount": 205, "currency": "USD"}, ...],
      "total": 880
    }

Tolerance rules:
    - JSON files that fail to parse are skipped with a stderr warning.
    - Files missing the required fields (``category`` plus either a numeric
      ``total`` or an ``items`` list with numeric ``amount`` values) are
      skipped with a stderr warning.

Stdlib only (``pathlib`` + ``json``). Read-only; does not modify any
existing tracking artifact.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
EXPENSES_DIR: Path = REPO_ROOT / "tracking" / "expenses"


def _warn(msg: str) -> None:
    print(f"[sum_expenses][warn] {msg}", file=sys.stderr)


def _coerce_amount(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _file_total(data: dict, source: Path) -> float | None:
    """Return the dollar total for one expense file, or ``None`` if invalid."""
    explicit = _coerce_amount(data.get("total"))
    if explicit is not None:
        return explicit

    items = data.get("items")
    if not isinstance(items, list) or not items:
        _warn(f"{source.name}: missing 'total' and 'items'; skipped")
        return None

    running = 0.0
    counted = 0
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            _warn(f"{source.name}: items[{idx}] not an object; skipped")
            continue
        amount = _coerce_amount(item.get("amount"))
        if amount is None:
            _warn(f"{source.name}: items[{idx}] missing numeric 'amount'; skipped")
            continue
        running += amount
        counted += 1

    if counted == 0:
        _warn(f"{source.name}: no usable items; skipped")
        return None
    return running


def _iter_expense_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.glob("*.json") if p.is_file())


def collect() -> tuple[float, dict[str, float], int]:
    """Walk ``tracking/expenses/`` and aggregate totals.

    Returns ``(grand_total, by_category, file_count)`` where ``file_count`` is
    the number of files that contributed to the totals.
    """
    grand_total = 0.0
    by_category: dict[str, float] = {}
    counted = 0

    for path in _iter_expense_files(EXPENSES_DIR):
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            _warn(f"{path.name}: invalid JSON ({exc.msg} at line {exc.lineno}); skipped")
            continue
        except OSError as exc:
            _warn(f"{path.name}: read failed ({exc}); skipped")
            continue

        if not isinstance(data, dict):
            _warn(f"{path.name}: top-level value is not an object; skipped")
            continue

        category = data.get("category")
        if not isinstance(category, str) or not category.strip():
            _warn(f"{path.name}: missing string 'category'; skipped")
            continue

        total = _file_total(data, path)
        if total is None:
            continue

        category = category.strip()
        grand_total += total
        by_category[category] = by_category.get(category, 0.0) + total
        counted += 1

    return grand_total, by_category, counted


def _fmt(amount: float) -> str:
    if abs(amount - round(amount)) < 1e-9:
        return str(int(round(amount)))
    return f"{amount:.2f}"


def render(grand_total: float, by_category: dict[str, float]) -> str:
    lines = ["[expenses]", f"total={_fmt(grand_total)} USD", "", "by_category:"]
    for cat in sorted(by_category):
        lines.append(f"{cat}={_fmt(by_category[cat])}")
    return "\n".join(lines)


def main() -> int:
    if not EXPENSES_DIR.exists():
        _warn(f"expenses directory not found: {EXPENSES_DIR}")
    grand_total, by_category, _counted = collect()
    print(render(grand_total, by_category))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
