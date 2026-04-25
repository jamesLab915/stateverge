"""
Single-line log format: [presenter] topic=... segment=... action=... details=...
"""

from __future__ import annotations

import logging


def log_presenter(
    logger: logging.Logger,
    topic: str,
    segment: str,
    action: str,
    details: str,
    level: int = logging.INFO,
) -> None:
    """
    Log one pipeline event. ``segment`` can be -- when not segment-scoped.
    """
    msg = f"[presenter] topic={topic} segment={segment} action={action} details={details}"
    logger.log(level, msg)
