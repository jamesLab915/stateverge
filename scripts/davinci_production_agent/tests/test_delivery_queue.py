#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

_AGENT = Path(__file__).resolve().parents[1]
_SCRIPTS = _AGENT.parent
for p in (_SCRIPTS, _AGENT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def test_write_manifest_unlisted():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        with mock.patch("davinci_production_agent.delivery_queue.DELIVERY_QUEUE_ROOT", root):
            from davinci_production_agent.delivery_queue import read_manifest, write_manifest

            p = write_manifest(kind="long", render_path="/tmp/x.mp4", preset="long_driving", job_id="t1")
            data = read_manifest(p)
            assert data is not None
            assert data["upload_privacy"] == "unlisted"
            assert data["public_upload_allowed"] is False
            assert "qc_passed" in data
            assert "render_report" in data


def test_mark_failed_never_upload_ready(tmp_path: Path):
    root = tmp_path / "dq"
    (root / "long").mkdir(parents=True)
    with mock.patch("davinci_production_agent.delivery_queue.DELIVERY_QUEUE_ROOT", root):
        with mock.patch("davinci_production_agent.delivery_queue.DELIVERY_QUEUE_LONG", root / "long"):
            from davinci_production_agent.delivery_queue import (
                mark_manifest_failed,
                read_manifest,
                write_manifest,
            )

            p = write_manifest(
                kind="long",
                render_path="/tmp/x.mp4",
                preset="long_driving",
                job_id="fail1",
                upload_ready=True,
                qc_passed=True,
            )
            mark_manifest_failed(p, block_reason="resolve_render_failed")
            data = read_manifest(p)
            assert data is not None
            assert data["upload_ready"] is False
            assert data["status"] == "failed"
