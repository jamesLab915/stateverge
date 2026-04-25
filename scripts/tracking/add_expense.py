#!/usr/bin/env python3
"""Append one row to docs/tracking/irs_expense_log.csv (stdlib only)."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CSV_PATH = REPO_ROOT / "docs" / "tracking" / "irs_expense_log.csv"

HEADER = [
    "Date",
    "Vendor",
    "Amount",
    "Currency",
    "Category",
    "Payment Method",
    "Project",
    "Tool/Asset",
    "Description",
    "Business Purpose",
    "Receipt Path",
    "Deductibility Notes",
    "Reviewed",
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Append expense row to irs_expense_log.csv")
    ap.add_argument("--date", required=True)
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--amount", default="")
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--category", default="")
    ap.add_argument("--payment-method", default="", dest="payment_method")
    ap.add_argument("--project", default="StateVerge")
    ap.add_argument("--tool", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--business-purpose", default="", dest="business_purpose")
    ap.add_argument("--receipt-path", default="", dest="receipt_path")
    ap.add_argument("--notes", default="")
    args = ap.parse_args()

    deduct = args.notes or "Consult CPA"
    reviewed = "No"
    row = [
        args.date,
        args.vendor,
        args.amount,
        args.currency,
        args.category,
        args.payment_method,
        args.project,
        args.tool,
        args.description,
        args.business_purpose,
        args.receipt_path,
        deduct,
        reviewed,
    ]

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not CSV_PATH.is_file()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        w.writerow(row)

    print(f"[tracking] action=append file={CSV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
