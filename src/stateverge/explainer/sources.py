"""Fetch official pages / PDFs and keep them as plain text for fact-checking."""

from __future__ import annotations

import re
import subprocess
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

UA = "Mozilla/5.0 (compatible; StatevergeResearch/1.0)"


class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        elif tag in ("p", "li", "h1", "h2", "h3", "h4", "tr", "br", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def slug(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", url.lower().split("://", 1)[-1]).strip("-")[:90]


def fetch(url: str, out_dir: Path, get=None) -> Path:
    get = get or (lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=60).read())
    raw = get(url)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug(url)}.txt"
    if url.lower().endswith(".pdf"):
        pdf = out.with_suffix(".pdf")
        pdf.write_bytes(raw)
        subprocess.run(["pdftotext", "-layout", str(pdf), str(out)], check=True)
        pdf.unlink()
    else:
        out.write_text(f"SOURCE: {url}\n\n" + html_to_text(raw.decode("utf-8", errors="replace")), encoding="utf-8")
    return out
