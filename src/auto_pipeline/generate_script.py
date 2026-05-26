"""Generate a 10-12 minute future-tech documentary narration script."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import REPO_ROOT, ensure_topic_dirs, log_step

REQUIRED_SECTIONS = (
    "hook",
    "current reality",
    "tech breakdown",
    "future speculation",
    "geopolitical impact",
    "conclusion",
)


def _read_project_env() -> dict[str, str]:
    env = dict(os.environ)
    p = REPO_ROOT / ".env"
    if not p.is_file():
        return env
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        env.setdefault(key.strip(), value.strip().strip("'\""))
    return env


def _prompt(topic: str) -> str:
    return (
        "Write a 10-12 minute narration script for a cinematic future technology "
        f"documentary about: {topic}.\n\n"
        "Required structure, with exact markdown headings:\n"
        "## hook\n"
        "## current reality\n"
        "## tech breakdown\n"
        "## future speculation\n"
        "## geopolitical impact\n"
        "## conclusion\n\n"
        "Tone: serious, visually rich, globally aware, suitable for LTX cinematic "
        "visuals, stock footage, FFmpeg assembly, and short HeyGen presenter inserts. "
        "Avoid bullet lists; write narration-ready paragraphs."
    )


def _openai_script(topic: str) -> str | None:
    env = _read_project_env()
    key = env.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    payload = {
        "model": env.get("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [
            {
                "role": "system",
                "content": "You write long-form cinematic technology documentary narration.",
            },
            {"role": "user", "content": _prompt(topic)},
        ],
        "temperature": 0.7,
    }
    req = Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"[auto_pipeline] step=generate_script openai=fallback reason={exc}", flush=True)
        return None
    choices = data.get("choices") or []
    if not choices:
        return None
    content = choices[0].get("message", {}).get("content", "")
    return content.strip() or None


def _fallback_script(topic: str) -> str:
    clean = topic.replace("-", " ").strip() or "future technology"
    blocks = {
        "hook": (
            f"What happens when {clean} stops being a distant prediction and becomes "
            "the hidden operating system of everyday life? The next decade will not "
            "arrive as a single invention. It will arrive as factories, interfaces, "
            "supply chains, and cities quietly reorganized around machine intelligence."
        ),
        "current reality": (
            f"Today, {clean} already exists in fragments. Sensors observe production "
            "lines, cloud systems coordinate decisions, and AI models compress years "
            "of operational knowledge into software. The public usually sees the final "
            "product, but the deeper story is the infrastructure beneath it."
        ),
        "tech breakdown": (
            "The system combines robotics, perception models, predictive maintenance, "
            "simulation, scheduling software, and automated quality control. Cameras "
            "become measurement devices. Industrial robots become adaptive workers. "
            "Data networks become the nervous system of the entire operation."
        ),
        "future speculation": (
            "In the future, production may become more local, faster, and stranger. "
            "A design could be generated in the morning, simulated by noon, and built "
            "by autonomous cells before night. The factory becomes less like a building "
            "and more like a programmable organism."
        ),
        "geopolitical impact": (
            "The countries that control compute, energy, chips, industrial robots, "
            "and logistics data will control more than manufacturing. They will control "
            "strategic resilience. The competition will not only be over cheap labor. "
            "It will be over automated capacity and the ability to reconfigure quickly."
        ),
        "conclusion": (
            f"The future of {clean} is not just about machines replacing people. It is "
            "about the redesign of production itself. The decisive question is whether "
            "these systems become closed instruments of concentration, or open tools "
            "for building more resilient societies."
        ),
    }
    repeated: list[str] = []
    for heading in REQUIRED_SECTIONS:
        text = blocks[heading]
        repeated.append(f"## {heading}\n\n{text}\n\n{text}\n\n{text}")
    return "\n\n".join(repeated).strip() + "\n"


def generate_script(topic: str, root: Path | None = None) -> Path:
    paths = ensure_topic_dirs(topic, root)
    out = paths["script"] / "narration_script.txt"
    script = _openai_script(topic) or _fallback_script(topic)
    for heading in REQUIRED_SECTIONS:
        if f"## {heading}" not in script.lower():
            script += f"\n\n## {heading}\n\n{_fallback_script(topic)}"
            break
    out.write_text(script.strip() + "\n", encoding="utf-8")
    log_step("generate_script", topic, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    args = ap.parse_args(argv)
    generate_script(args.topic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
