#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

_AGENT = Path(__file__).resolve().parents[1]
_SCRIPTS = _AGENT.parent
for p in (_SCRIPTS, _AGENT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _patch_delivery_queue(tmp_path: Path):
    dq = tmp_path / "dq"
    (dq / "long").mkdir(parents=True)
    (dq / "shorts").mkdir(parents=True)
    return (
        mock.patch("davinci_production_agent.delivery_queue.DELIVERY_QUEUE_ROOT", dq),
        mock.patch("davinci_production_agent.delivery_queue.DELIVERY_QUEUE_LONG", dq / "long"),
        mock.patch("davinci_production_agent.delivery_queue.DELIVERY_QUEUE_SHORTS", dq / "shorts"),
        mock.patch("davinci_production_agent.paths.DELIVERY_QUEUE_ROOT", dq),
        mock.patch("davinci_production_agent.paths.DELIVERY_QUEUE_LONG", dq / "long"),
        mock.patch("davinci_production_agent.paths.DELIVERY_QUEUE_SHORTS", dq / "shorts"),
    )


def test_build_job_fails_without_fake_upload_ready(tmp_path: Path):
    src = tmp_path / "clip.mov"
    src.write_bytes(b"x" * 5000)
    patches = _patch_delivery_queue(tmp_path)
    for p in patches:
        p.start()
    try:
        with mock.patch(
            "davinci_production_agent.agent_v1.probe_davinci_detailed",
            return_value={"ready": False, "checks": {}},
        ):
            with mock.patch(
                "davinci_production_agent.agent_v1.run_auto_detect_cut_list",
                return_value={"ok": True},
            ):
                from davinci_production_agent.agent_v1 import build_job

                result = build_job(kind="long", source=src, count=1)
    finally:
        for p in patches:
            p.stop()
    assert result.get("ok") is False
    assert result.get("block_reason") == "davinci_api_unavailable"
    jobs = result.get("jobs") or []
    assert jobs and jobs[0].get("upload_ready") is False
