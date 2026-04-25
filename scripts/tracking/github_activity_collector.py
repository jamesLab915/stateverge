#!/usr/bin/env python3
"""
GitHub / local git activity for EB-1/NIW & IRS evidence.
Stdlib: urllib, subprocess, csv. GITHUB_TOKEN + GITHUB_REPO in .env for --mode fetch.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_PR = Path(__file__).resolve().parent
if str(_PR) not in sys.path:
    sys.path.insert(0, str(_PR))
from _paths import REPO_ROOT, TRACK

OUTPUT_CSV = TRACK / "github_activity_log.csv"
HEADERS = [
    "Date",
    "Repo",
    "Type",
    "Message",
    "Files Changed",
    "Lines Added",
    "Lines Deleted",
    "Area",
    "Evidence Value",
    "URL",
]

PATH_AREA: list[tuple[str, str]] = [
    ("src/production", "Production pipeline"),
    ("src/presenter_pipeline", "Presenter pipeline"),
    ("src/integrations", "Integrations"),
    ("docs", "Documentation"),
    ("scripts", "Automation scripts"),
    ("topics", "Topic production"),
    ("assets", "Asset management"),
    ("output", "General"),
]


def _read_env() -> dict[str, str]:
    p = REPO_ROOT / ".env"
    o: dict[str, str] = {}
    if not p.is_file():
        return o
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            o[k.strip()] = v.strip().strip("'\"")
    return o


def _api_get(path: str, token: str) -> Any:
    u = f"https://api.github.com{path}"
    h = {"Accept": "application/vnd.github+json", "User-Agent": "StateVerge-Tracking/1.0"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = Request(u, headers=h)
    try:
        with urlopen(r, timeout=30) as res:
            b = res.read()
            if not b:
                return None
            return json.loads(b.decode("utf-8"))
    except (URLError, HTTPError, OSError, json.JSONDecodeError, ValueError):
        return None


def _evidence_for_paths(files: list[str]) -> str:
    for f in files:
        fp = f.replace("\\", "/")
        for p, _ in PATH_AREA:
            if fp == p or fp.startswith(p + "/") or f"/{p}/" in f"/{fp}/":
                if p in ("src/production", "src/presenter_pipeline", "src/integrations", "docs", "scripts"):
                    return "High"
                if p in ("topics", "assets", "output"):
                    return "Medium"
    for f in files:
        fp = f.replace("\\", "/")
        if fp.startswith("src/") or fp.startswith("scripts/") or fp.startswith("docs/"):
            return "High"
        if fp.startswith("assets/") or fp.startswith("topics/") or fp.startswith("output/"):
            return "Medium"
    return "Low"


def _area_for_paths(files: list[str], message: str) -> str:
    m = (message or "").lower()
    for f in files:
        p = f.replace("\\", "/")
        for pre, name in PATH_AREA:
            if p == pre or p.startswith(pre + "/") or f"/{pre}/" in f"/{p}/":
                return name
    if "doc" in m or "readme" in m:
        return "Documentation"
    if "script" in m or "track" in m:
        return "Automation scripts"
    return "General"


def _local_commit_details(sha: str) -> tuple[str, str, int, int, int, list[str], str]:
    p = [
        "git",
        "-C",
        str(REPO_ROOT),
        "show",
        sha,
        "--name-only",
        "--format=%H|%cI|%s|",
    ]
    try:
        out = subprocess.check_output(
            p,
            text=True,
            errors="replace",
            timeout=60,
        )
    except (subprocess.CalledProcessError, OSError) as e:
        return (sha, "", 0, 0, 0, [], f"git error: {e}")
    rest = out.splitlines()
    if not rest:
        return (sha, "", 0, 0, 0, [], "empty show")  # last item is subj; caller skips
    line0 = rest[0]
    parts = line0.split("|", 2)
    fsha = parts[0] if len(parts) > 0 else sha
    cdate = parts[1] if len(parts) > 1 else ""
    subj = parts[2] if len(parts) > 2 else ""
    files: list[str] = []
    for ln in rest[1:]:
        t = ln.strip()
        if t and not t.startswith("commit "):
            files.append(t)
    p2 = ["git", "-C", str(REPO_ROOT), "show", sha, "--numstat", "--format="]
    try:
        nout = subprocess.check_output(p2, text=True, errors="replace", timeout=60)
    except (subprocess.CalledProcessError, OSError):
        nout = ""
    add = dlt = 0
    for ln in nout.splitlines():
        ps = ln.split("\t", 2)
        if len(ps) >= 3 and ps[0] != "-":
            try:
                a = 0 if ps[0] == "-" else int(ps[0])
                b = 0 if ps[1] == "-" else int(ps[1])
                add += a
                dlt += b
            except ValueError:
                pass
    return fsha, cdate, len(files), add, dlt, files, (subj or "")


def _iter_local_shas(n: int) -> list[str]:
    rc, out = _git_quiet(
        ["git", "-C", str(REPO_ROOT), "log", f"-n{n}", "--format=%H"]
    )
    if rc != 0:
        return []
    return [l.strip() for l in out.splitlines() if l.strip()]


def _records_local() -> list[dict[str, str]]:
    shas = _iter_local_shas(30)
    if not shas:
        return []
    env = _read_env()
    rep = (env.get("GITHUB_REPO") or "local/StateVerge").strip() or "local/StateVerge"
    if "/" not in rep:
        rep = f"local/{rep}" if not rep.lower().startswith("local") else rep
    out: list[dict[str, str]] = []
    for sha in shas:
        fsha, cdate, nfiles, add, dlt, files, subj = _local_commit_details(sha)
        if subj.startswith("git error:") or subj in ("empty show",):
            print(f"[github_activity_collector] skip: {subj!s} {sha!s}", file=sys.stderr)
            continue
        msg = (subj or "(no message)")[:500]
        area = _area_for_paths(files, msg)
        ev = _evidence_for_paths(files)
        u = f"local:{fsha}"
        out.append(
            {
                "Date": cdate[:19] if cdate else datetime.now().astimezone().replace(microsecond=0).isoformat()[:19],
                "Repo": rep,
                "Type": "commit",
                "Message": msg,
                "Files Changed": str(nfiles),
                "Lines Added": str(add),
                "Lines Deleted": str(dlt),
                "Area": area,
                "Evidence Value": ev,
                "URL": u,
            }
        )
    return out


def _load_existing() -> dict[str, dict[str, str]]:
    o: dict[str, dict[str, str]] = {}
    if not OUTPUT_CSV.is_file():
        return o
    with OUTPUT_CSV.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            u = (row.get("URL") or "").strip()
            if u:
                o[u] = {h: (row.get(h) or "") for h in HEADERS}
    return o


def _dedup_merge(new: list[dict[str, str]], ex: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    for r in new:
        u = (r.get("URL") or "").strip()
        if u:
            ex[u] = {h: (r.get(h) or "") for h in HEADERS}
    o = list(ex.values())
    o.sort(key=lambda row: (row.get("Date") or ""), reverse=True)
    return o


def _write_all(rows: list[dict[str, str]]) -> None:
    TRACK.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        for row in rows:
            w.writerow({h: row.get(h, "") for h in HEADERS})


def _api_fetch() -> list[dict[str, str]]:
    env = _read_env()
    tok = (env.get("GITHUB_TOKEN") or "").strip()
    full = (env.get("GITHUB_REPO") or "").strip()
    if not full or "/" not in full:
        print(
            "[github_activity_collector] --mode fetch: set GITHUB_REPO=owner/repo in .env. Skipping. Exit 0.",
            flush=True,
        )
        return []
    if not tok:
        print(
            "[github_activity_collector] --mode fetch: GITHUB_TOKEN not set. "
            "Add token, or use --mode local-log. Exit 0.",
            flush=True,
        )
        return []
    own, rname = full.split("/", 1)
    since = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    coms = _api_get(f"/repos/{own}/{rname}/commits?per_page=50&since={since}", tok)
    if not isinstance(coms, list):
        print("[github_activity_collector] API: no commits. Exit 0.", flush=True)
        return []
    out: list[dict[str, str]] = []
    for c in coms:
        d = c.get("commit", {}) or {}
        a0 = d.get("author", {}) or {}
        dt = str(a0.get("date") or "")[:19]
        msg = str((d.get("message") or "commit").split("\n")[0])[:500]
        u = c.get("html_url") or ""
        sha = (c.get("sha") or "")[:40]
        if not sha:
            continue
        det = _api_get(f"/repos/{own}/{rname}/commits/{sha}", tok)
        files: list[str] = []
        add = dlt = 0
        if isinstance(det, dict):
            for f in det.get("files", []) or []:
                if isinstance(f, dict):
                    files.append(str(f.get("filename") or ""))
                    add += int(f.get("additions") or 0)
                    dlt += int(f.get("deletions") or 0)
        out.append(
            {
                "Date": dt,
                "Repo": f"{own}/{rname}",
                "Type": "commit",
                "Message": msg,
                "Files Changed": str(len(files)),
                "Lines Added": str(add),
                "Lines Deleted": str(dlt),
                "Area": _area_for_paths(files, msg),
                "Evidence Value": _evidence_for_paths(files),
                "URL": u,
            }
        )
    prs = _api_get(
        f"/repos/{own}/{rname}/pulls?state=all&per_page=20&sort=updated",
        tok,
    )
    if isinstance(prs, list):
        for pr in prs:
            if not isinstance(pr, dict):
                continue
            t = "pr" if (pr.get("state") or "").lower() == "open" else "pr_closed"
            odt = (pr.get("closed_at") or pr.get("updated_at") or pr.get("created_at") or "")[:19]
            if not odt and pr.get("merged_at"):
                odt = (pr.get("merged_at") or "")[:19]
            out.append(
                {
                    "Date": odt,
                    "Repo": f"{own}/{rname}",
                    "Type": t,
                    "Message": (pr.get("title") or "PR")[:500],
                    "Files Changed": "0",
                    "Lines Added": "0",
                    "Lines Deleted": "0",
                    "Area": "General",
                    "Evidence Value": "High",
                    "URL": str(pr.get("html_url") or pr.get("url") or ""),
                }
            )
    return out


def _in_git() -> bool:
    p = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
    )
    return p.returncode == 0


def _git_quiet(args: list[str]) -> tuple[int, str]:
    """Run git swallowing stderr (used so missing-repo noise never reaches the user)."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, p.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description="GitHub API or local git → github_activity_log.csv")
    ap.add_argument("--mode", choices=("fetch", "local-log"), default="local-log")
    args = ap.parse_args()
    ex = _load_existing()
    new: list[dict[str, str]] = []
    if args.mode == "local-log":
        if not _in_git():
            print(
                "[github_activity_collector] not a git repo at REPO_ROOT. Run: git init (or clone). Exit 0.",
                flush=True,
            )
        new = _records_local()
        print(
            f"[github_activity_collector] local-log: {len(new)} row(s) from last 30 commits.",
            flush=True,
        )
    else:
        new = _api_fetch()
    if not new and args.mode == "local-log" and not ex:
        _write_all([])
    merged = _dedup_merge(new, ex)
    merged.sort(key=lambda r: (r.get("Date") or ""), reverse=True)
    _write_all(merged)
    print(f"[github_activity_collector] total_rows={len(merged)} out={OUTPUT_CSV}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
