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


def test_probe_davinci_detailed_checks_shape():
    fake_base = {
        "api_available": True,
        "resolve_connected": True,
        "warnings": [],
    }
    with mock.patch("davinci_production_agent.resolve_api._resolve_running", return_value=True):
        with mock.patch(
            "davinci_production_agent.resolve_api.probe_resolve_api",
            return_value=fake_base,
        ):
            with mock.patch(
                "davinci_production_agent.resolve_api._get_resolve",
                side_effect=RuntimeError("no resolve in test"),
            ):
                from davinci_production_agent.resolve_api import probe_davinci_detailed

                out = probe_davinci_detailed(run_timeline_test=False)
    assert "checks" in out
    assert "resolve_app_running" in out["checks"]
    assert "resolve_api_connected" in out["checks"]


def test_run_production_render_api_unavailable():
    with mock.patch(
        "davinci_production_agent.resolve_api.probe_davinci_detailed",
        return_value={"ready": False, "checks": {}},
    ):
        from davinci_production_agent.resolve_api import run_production_render

        r = run_production_render(
            job_id="t1",
            kind="long",
            preset_id="long_driving",
            source_path=Path("/tmp/nonexistent.mov"),
        )
    assert r.get("block_reason") == "davinci_api_unavailable"
    assert r.get("render_success") is False
