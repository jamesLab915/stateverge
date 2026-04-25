"""
Future: generate extra 5s/10s **same-host** b-roll to reduce pattern fatigue.

**V1 (stub):** no external API. Validates ``host_master.png``; updates manifest
``generated_variants`` and enforces *policy* in code and comments — prompts must never
re-specify face, hair, clothes, age, or gender; only scene tone, action scale, camera,
mood, light.

Outputs (future) go under ``assets/presenter_generated/{intro,insert,outro}/`` and are
consumed as **secondary** to ``presenter_masters`` in :mod:`.builder`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .config import PipelineConfig
from .logutil import log_presenter

LOG = logging.getLogger("presenter.variant_generator")

# Public policy constants for future API wiring (do not use for runtime branching beyond docs)
# Prompts for generation MUST NOT target these axes:
IDENTITY_FORBIDDEN_PROMPT_AXES = (
    "face identity",
    "hairstyle",
    "clothing / wardrobe",
    "perceived age",
    "gender expression",
    "overall host look",
)
# MAY adjust:
IDENTITY_ALLOWED_PROMPT_AXES = (
    "topic context",
    "action intensity",
    "camera distance",
    "emotional tone",
    "lighting / atmosphere",
)


def _ref_path(config: PipelineConfig) -> Path:
    return config.host_reference_image()


def run_variant_generation_stub(
    config: PipelineConfig, topic: str, manifest: dict[str, Any]
) -> None:
    """
    Placeholder: record intent in ``manifest``; if ``host_master.png`` is absent, log error
    and set ``generated_variants`` to a skipped state (does **not** stop base / masters).
    """
    ref = _ref_path(config)
    if not ref.is_file():
        log_presenter(
            LOG,
            topic,
            "--",
            "variant_generate",
            f"aborted: missing {ref} (v1 stub; no API call)",
            level=logging.ERROR,
        )
        manifest.setdefault("errors", []).append("variant_module_skipped: host_master.png missing")
        manifest["generated_variants"] = [
            {
                "status": "skipped",
                "reason": "reference_image_missing",
                "reference_path": "assets/presenter_reference/host_master.png",
            }
        ]
        return

    log_presenter(
        LOG,
        topic,
        "--",
        "variant_generate",
        "stub: identity locked to host_master.png; v1 does not call Runway/Gen API; "
        f"forbidden prompt axes: {IDENTITY_FORBIDDEN_PROMPT_AXES}; allowed: {IDENTITY_ALLOWED_PROMPT_AXES}",
    )
    manifest["generated_variants"] = [
        {
            "status": "stub_no_api",
            "reference_used": "assets/presenter_reference/host_master.png",
            "message": "v1: placeholder only. Future: generate into assets/presenter_generated/* after API wiring.",
        }
    ]
