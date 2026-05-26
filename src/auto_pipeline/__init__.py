"""Future documentary automation pipeline for StateVerge."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def topic_dir(topic: str, root: Path | None = None) -> Path:
    """Return ``topics/<topic>`` and keep all writes under the repository root."""
    base = (root or REPO_ROOT).resolve()
    return base / "topics" / topic


def ensure_topic_dirs(topic: str, root: Path | None = None) -> dict[str, Path]:
    """Create the standard auto-pipeline topic directory layout."""
    tdir = topic_dir(topic, root)
    paths = {
        "topic": tdir,
        "script": tdir / "script",
        "visuals": tdir / "visuals",
        "assets": tdir / "assets",
        "final": tdir / "final",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def log_step(step: str, topic: str, output: Path | str) -> None:
    print(f"[auto_pipeline] step={step}", flush=True)
    print(f"[auto_pipeline] topic={topic}", flush=True)
    print(f"[auto_pipeline] output={output}", flush=True)
