"""
Load / save :file:`production_brief.json` (human-editable; drives scripts, LTX, packaging hints).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from .paths import topic_production_paths

DEFAULT_PRESENTER_FREQUENCY_SEC = 60.0


@dataclass
class ChapterBrief:
    name: str
    duration_sec: int
    focus: str = ""


@dataclass
class PresenterBlock:
    frequency_sec: float = DEFAULT_PRESENTER_FREQUENCY_SEC


@dataclass
class EnvatoBlock:
    music_mood: str = "dark cinematic tension"
    sfx_style: str = "news impact"
    title_style: str = "news documentary"


@dataclass
class ProductionBrief:
    title: str
    duration_target_sec: int
    style: str
    chapters: List[ChapterBrief] = field(default_factory=list)
    presenter: PresenterBlock = field(default_factory=PresenterBlock)
    envato: EnvatoBlock = field(default_factory=EnvatoBlock)
    notes: str = ""


def default_brief_template(title: str = "Untitled Production") -> ProductionBrief:
    return ProductionBrief(
        title=title,
        duration_target_sec=900,
        style="cinematic geopolitical explainer",
        chapters=[
            ChapterBrief(
                name="The System",
                duration_sec=180,
                focus="global structure, networks",
            ),
            ChapterBrief(
                name="Rupture",
                duration_sec=180,
                focus="crisis points, volatility",
            ),
        ],
    )


def brief_to_dict(b: ProductionBrief) -> dict[str, Any]:
    d: dict[str, Any] = {
        "title": b.title,
        "duration_target_sec": b.duration_target_sec,
        "style": b.style,
        "chapters": [asdict(c) for c in b.chapters],
        "presenter": asdict(b.presenter),
        "envato": asdict(b.envato),
    }
    if b.notes:
        d["notes"] = b.notes
    return d


def dict_to_brief(d: dict[str, Any]) -> ProductionBrief:
    chs: List[ChapterBrief] = []
    for c in d.get("chapters") or []:
        if not isinstance(c, dict):
            continue
        chs.append(
            ChapterBrief(
                name=str(c.get("name", "Chapter")),
                duration_sec=int(c.get("duration_sec", 120)),
                focus=str(c.get("focus", "")),
            )
        )
    p = d.get("presenter") or {}
    e = d.get("envato") or {}
    return ProductionBrief(
        title=str(d.get("title", "Untitled")),
        duration_target_sec=int(d.get("duration_target_sec", 600)),
        style=str(d.get("style", "cinematic explainer")),
        chapters=chs,
        presenter=PresenterBlock(
            frequency_sec=float(
                p.get("frequency_sec", DEFAULT_PRESENTER_FREQUENCY_SEC)
            )
        ),
        envato=EnvatoBlock(
            music_mood=str(e.get("music_mood", "dark cinematic tension")),
            sfx_style=str(e.get("sfx_style", "news impact")),
            title_style=str(e.get("title_style", "news documentary")),
        ),
        notes=str(d.get("notes", "")),
    )


def load_brief(path: Path) -> Optional[ProductionBrief]:
    path = Path(path)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        return None
    return dict_to_brief(d)


def save_brief(path: Path, brief: ProductionBrief) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(brief_to_dict(brief), f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_or_create_default(
    root: Path, topic_slug: str, *, title: Optional[str] = None
) -> tuple[Path, ProductionBrief, bool]:
    t = topic_production_paths(root, topic_slug)
    p = t["production_brief"]
    b = load_brief(p)
    if b is not None:
        return p, b, False
    b2 = default_brief_template(
        title or topic_slug.replace("-", " ").title(),
    )
    save_brief(p, b2)
    return p, b2, True


if __name__ == "__main__":
    b0 = default_brief_template("Smoke")
    b1 = dict_to_brief(brief_to_dict(b0))
    assert b1.title == b0.title
    print("brief_loader: OK", b0.title, len(b0.chapters), "chapters")
