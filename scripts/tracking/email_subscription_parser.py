#!/usr/bin/env python3
r"""
Parse subscription / billing .eml files → docs/tracking/email_subscription_log.csv.

Does not save full message bodies, credentials, or personal communication content.
Gmail IMAP: not implemented (TODO) — use export to .eml to avoid account risk.

Python standard library only.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional

_PR = Path(__file__).resolve().parent
if str(_PR) not in sys.path:
    sys.path.insert(0, str(_PR))
from _paths import REPO_ROOT, TRACK

KEYWORDS = (
    "receipt",
    "invoice",
    "payment",
    "subscription",
    "renewal",
)
OUT_CSV = TRACK / "email_subscription_log.csv"

_AMT = re.compile(
    r"(\$|USD|EUR|GBP|JPY|CNY)?\s*"
    r"([0-9][0-9,]*\.?[0-9]+)[\s,]*"
    r"(\$|USD|EUR|GBP|JPY|CNY)?",
    re.IGNORECASE,
)
_CURR_MAP = {
    "usd": "USD",
    "eur": "EUR",
    "gbp": "GBP",
    "jpy": "JPY",
    "cny": "CNY",
    "$": "USD",
}
_SUBJ_HINT = re.compile(
    r"(?i)subscription|plan|receipt|invoice|renewal|billing|payment|membership|premium"
)
_PERIOD = re.compile(
    r"(?i)(?P<p>(?:from|for)\s+[\d\-/ ]+\s+(?:to|through|until|–|-)\s+[\d\-/ ]+|"
    r"annual|monthly|quarterly|per\s*month|yearly)"
)


def _date_iso(msg: Any) -> str:
    raw = msg.get("Date")
    if raw:
        try:
            d = parsedate_to_datetime(str(raw).strip())
            if d and d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            if d is not None:
                return d.date().isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _vendor_name(msg: Any) -> str:
    from_ = str(msg.get("From") or "")
    addrs = getaddresses([from_])
    if not addrs:
        return from_.strip()[:200] or "Unknown"
    name, addr = addrs[0]
    if "@" in addr:
        dom = addr.split("@")[-1].lower()
        if name and len(name) < 80:
            return f"{name} ({dom})"[:200]
        return dom[:200]
    return (addr or name or "Unknown")[:200]


def _subj(msg: Any) -> str:
    return str(msg.get("Subject") or "").replace("\n", " ").strip()[:500]


def _is_keyword_hits(s: str) -> bool:
    t = s.lower()
    if any(w in t for w in KEYWORDS):
        return True
    if _SUBJ_HINT.search(s):
        return True
    return False


def _body_snippet(msg: Any) -> str:
    if msg.is_multipart():
        chunks: list[str] = []
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "text/html") and "attachment" not in (part.get("Content-Disposition") or ""):
                try:
                    p = part.get_content()
                except Exception:  # noqa: BLE001
                    p = b""
                if isinstance(p, str):
                    chunks.append(p)
                else:
                    chunks.append(p.decode("utf-8", errors="replace"))
        return " ".join(chunks)[:8000]
    try:
        b = msg.get_content()
    except Exception:  # noqa: BLE001
        b = b""
    if isinstance(b, str):
        return b[:8000]
    return b.decode("utf-8", errors="replace")[:8000]


def _parse_amount_currency(body: str) -> tuple[str, str]:
    m = _AMT.search(body)
    if not m:
        return ("", "USD")
    a = (m.group(2) or "").replace(",", "")
    c = (m.group(1) or m.group(3) or "").strip()
    cur = "USD"
    c_lower = c.lower() if c else ""
    for k, v in _CURR_MAP.items():
        if k in c_lower:
            cur = v
            break
    return (a, cur)


def _period_snip(body: str) -> str:
    m = _PERIOD.search(body)
    if m:
        g = m.group(0) or m.group("p")
        return re.sub(r"\s+", " ", g)[:200] if g else ""
    return ""


def _sub_name(msg: Any, body: str) -> str:
    sub = _subj(msg)
    if "subscription" in sub.lower() or "plan" in sub.lower():
        return sub[:200]
    pm = _period_snip(body)
    if pm:
        return pm[:200]
    return "Subscription"[:200]


def _period(body: str) -> str:
    s = _period_snip(body)
    return s[:200] if s else ""


def _iter_eml(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(d.glob("**/*.eml"), key=lambda p: p.as_posix())


def _load_eml(p: Path) -> Any:
    return BytesParser(policy=policy.default).parsebytes(p.read_bytes())


def _row_for_msg(msg: Any, email_source: str) -> Optional[dict[str, str]]:
    if not _is_keyword_hits(_subj(msg) + " " + str(msg.get("From", ""))):
        return None
    body = _body_snippet(msg)
    if not _subj(msg) and not body.strip():
        return None
    amt, cur = _parse_amount_currency(body)
    return {
        "Date": _date_iso(msg),
        "Vendor": _vendor_name(msg),
        "Amount": amt,
        "Currency": cur,
        "Subscription Name": _sub_name(msg, body)[:200],
        "Billing Period": _period(body)[:200],
        "Email Source": email_source[:200],
    }


def _write_rows(rows: list[dict[str, str]], dry_run: bool) -> None:
    header = [
        "Date",
        "Vendor",
        "Amount",
        "Currency",
        "Subscription Name",
        "Billing Period",
        "Email Source",
    ]
    TRACK.mkdir(parents=True, exist_ok=True)
    if dry_run:
        for r in rows:
            print(f"[dry-run] {r!r}", flush=True)
        return
    f = OUT_CSV
    need_header = (not f.is_file()) or f.stat().st_size == 0
    with f.open("a" if f.is_file() else "w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=header, extrasaction="ignore")
        if need_header:
            w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[email_subscription_parser] rows_appended={len(rows)} out={f}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Parse billing-related .eml (keywords only) → email_subscription_log.csv. "
        "Gmail IMAP: TODO (export to .eml from mail client).",
    )
    ap.add_argument(
        "--eml-dir",
        type=Path,
        default=None,
        help="Directory to scan for *.eml (recursive).",
    )
    ap.add_argument(
        "--eml-file",
        type=Path,
        action="append",
        default=[],
        help="Single .eml (repeat for multiple).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
    )
    args = ap.parse_args()
    print(
        "[email_subscription_parser] Gmail IMAP: not implemented (export receipts to .eml).",
        flush=True,
    )
    rows: list[dict[str, str]] = []
    for f in args.eml_file:
        f = f.resolve()
        if not f.is_file():
            print(f"[error] not a file: {f}", file=sys.stderr)
            return 0
        m = _load_eml(f)
        try:
            rels = f.relative_to(REPO_ROOT)
        except ValueError:
            rels = f
        r = _row_for_msg(m, f"file:{rels!s}"[:200])
        if r:
            rows.append(r)
    if args.eml_dir:
        p = args.eml_dir.resolve()
        if not p.is_dir():
            print(
                f"[email_subscription_parser] not a directory (skip): {p}. Exit 0.",
                flush=True,
            )
            return 0
        all_eml = _iter_eml(p)
        if not all_eml:
            print(
                f"[email_subscription_parser] no .eml files under {p} — normal exit, nothing to import.",
                flush=True,
            )
        for f in all_eml:
            m = _load_eml(f)
            try:
                rel = f.relative_to(REPO_ROOT)
            except ValueError:
                rel = f
            r = _row_for_msg(m, f"eml_path:{rel!s}"[:200])
            if r:
                rows.append(r)
    if not args.eml_file and not args.eml_dir:
        if not rows:
            print(
                "Usage: --eml-dir <path> or --eml-file <file.eml>  (see --help).",
                flush=True,
            )
    if rows:
        _write_rows(rows, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
