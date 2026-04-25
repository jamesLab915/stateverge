"""
Load and validate presenter_script.json; detect placeholder vs. real copy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from . import fs_utils

LOG = logging.getLogger("presenter.script")


@dataclass
class InsertItem:
    anchor_sec: float
    text: str


@dataclass
class PresenterScript:
    has_file: bool
    intro_text: str
    outro_text: str
    inserts: List[InsertItem] = field(default_factory=list)
    raw: Optional[dict[str, Any]] = None

    @property
    def is_placeholder(self) -> bool:
        if not self.has_file:
            return True
        if self.intro_text.strip() or self.outro_text.strip():
            return False
        if any(i.text.strip() for i in self.inserts):
            return False
        return True


def load_presenter_script(path: Path) -> PresenterScript:
    path = Path(path)
    d = fs_utils.read_json(path)
    if d is None:
        return PresenterScript(
            has_file=False, intro_text="", outro_text="", inserts=[], raw=None
        )
    intro = (d.get("intro") or {}) or {}
    outro = (d.get("outro") or {}) or {}
    intro_text = str(intro.get("text") or "")
    outro_text = str(outro.get("text") or "")
    ins_list: list[InsertItem] = []
    for i, it in enumerate(d.get("inserts") or []):
        try:
            a = float((it or {}).get("anchor_sec", 0))
        except (TypeError, ValueError):
            a = 0.0
        tx = str((it or {}).get("text") or "")
        ins_list.append(InsertItem(anchor_sec=a, text=tx))
    return PresenterScript(
        has_file=True, intro_text=intro_text, outro_text=outro_text, inserts=ins_list, raw=d
    )
