"""Render the EP01 sample cut with OpenAI Sora (GitHub Actions, OPENAI_API_KEY).

    python3 scripts/redchamber/sora_sample.py [--shots 001,003] [--model sora-2] [--out media/redchamber]

Each shot prompt = style + character bible line(s) + shot + negatives, from
ep01_sample_shots.json. Shots render in parallel; finished clips are cropped
to 2.39:1, cut together with ffmpeg and closed on 2s of black.
Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://api.openai.com/v1/videos"
HERE = Path(__file__).resolve().parent
W, H = 1280, 536  # 2.39:1 crop of 1280x720


def _req(method: str, url: str, key: str, body: bytes | None = None, ctype: str | None = None) -> bytes:
    headers = {"Authorization": f"Bearer {key}"}
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items()]
    return ("".join(parts) + f"--{b}--\r\n").encode(), f"multipart/form-data; boundary={b}"


def build_prompt(spec: dict, shot: dict) -> str:
    chars = " ".join(spec["characters"][c] for c in shot.get("chars", []))
    text = shot["prompt"]
    for c in shot.get("chars", []):
        text = text.replace("{" + c + "}", spec["characters"][c])
    if "{" not in shot["prompt"]:
        text = f"{text} {chars}".strip()
    return f"{spec['style']}\n\n{text}\n\n{spec['negative']}"


def render(shot: dict, prompt: str, key: str, model: str, size: str, out: Path) -> dict:
    sid = shot["id"]
    try:
        body, ctype = _multipart({"model": model, "prompt": prompt, "seconds": str(shot["seconds"]), "size": size})
        job = json.loads(_req("POST", API, key, body, ctype))
        vid = job["id"]
        deadline = time.time() + 25 * 60
        while job.get("status") not in ("completed", "failed") and time.time() < deadline:
            time.sleep(15)
            job = json.loads(_req("GET", f"{API}/{vid}", key))
        if job.get("status") != "completed":
            return {"id": sid, "ok": False, "error": json.dumps(job.get("error") or job.get("status"))[:300]}
        path = out / f"shot_{sid}.mp4"
        path.write_bytes(_req("GET", f"{API}/{vid}/content", key))
        return {"id": sid, "ok": True, "file": str(path), "video_id": vid}
    except urllib.error.HTTPError as e:
        return {"id": sid, "ok": False, "error": f"HTTP {e.code} {e.read()[:300].decode(errors='replace')}"}
    except Exception as e:  # noqa: BLE001 - report every failure per shot
        return {"id": sid, "ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


def _probe(path: str) -> str:
    """ffmpeg's own stream listing (no ffprobe needed)."""
    return subprocess.run(["ffmpeg", "-hide_banner", "-i", path], capture_output=True, text=True).stderr


def has_audio(path: str) -> bool:
    return "Audio:" in _probe(path)


def _duration(path: str) -> float:
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", _probe(path))
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 8.0


def assemble(files: list[str], dst: Path) -> None:
    inputs, chains, pairs = [], [], ""
    for f in files:
        vi = len(inputs)
        inputs += [["-i", f]]
        chains.append(f"[{vi}:v]crop=iw:trunc(iw/2.39/2)*2,scale={W}:{H},setsar=1,fps=24,format=yuv420p[v{vi}]")
        if has_audio(f):
            chains.append(f"[{vi}:a]aresample=48000,aformat=channel_layouts=stereo[a{vi}]")
        else:
            ai = len(inputs)
            inputs += [["-f", "lavfi", "-t", f"{_duration(f):.3f}", "-i", "anullsrc=r=48000:cl=stereo"]]
            chains.append(f"[{ai}:a]anull[a{vi}]")
        pairs += f"[v{vi}][a{vi}]"
    bv, ba = len(inputs), len(inputs) + 1
    inputs += [["-f", "lavfi", "-t", "2", "-i", f"color=c=black:s={W}x{H}:r=24"],
               ["-f", "lavfi", "-t", "2", "-i", "anullsrc=r=48000:cl=stereo"]]
    chains.append(f"[{bv}:v]format=yuv420p,setsar=1[vb]")
    chains.append(f"[{ba}:a]anull[ab]")
    chains.append(f"{pairs}[vb][ab]concat=n={len(files) + 1}:v=1:a=1[v][a]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *[x for i in inputs for x in i],
                    "-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(dst)], check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default=str(HERE / "ep01_sample_shots.json"))
    ap.add_argument("--shots", default="")
    ap.add_argument("--model", default=os.environ.get("SORA_MODEL", "sora-2"))
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--out", default="media/redchamber")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    wanted = {s.strip() for s in a.shots.split(",") if s.strip()}
    shots = [s for s in spec["shots"] if not wanted or s["id"] in wanted]
    prompts = {s["id"]: build_prompt(spec, s) for s in shots}
    if a.dry_run:
        for sid, p in prompts.items():
            print(f"--- SHOT {sid} ---\n{p}\n")
        return 0
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("OPENAI_API_KEY missing")
        return 1
    out = Path(a.out)
    clips = out / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=len(shots)) as pool:
        results = list(pool.map(lambda s: render(s, prompts[s["id"]], key, a.model, a.size, clips), shots))
    report = {"model": a.model, "size": a.size, "shots": results}
    ok = [r["file"] for r in results if r["ok"]]
    if ok:
        final = out / "ep01_sample_cut.mp4"
        assemble(ok, final)
        report["final"] = str(final)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
