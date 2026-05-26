"""
Check connectivity / configuration for external APIs used by StateVerge.

Run::

    export PYTHONPATH="$PWD"   # repo root: ~/StateVerge
    python -m src.integrations.healthcheck
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# repo root: .../StateVerge
_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_dotenv() -> None:
    p = _ROOT / ".env"
    if not p.is_file():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(p, override=False)
    except Exception:
        pass


def _line(title: str, status: str, body: str = "") -> None:
    print(f"\n[{title}]  {status}")
    if body.strip():
        for ln in body.strip().splitlines():
            print(f"  {ln}")


def _check_openai() -> None:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    model = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
    if not key:
        _line(
            "OpenAI",
            "FAIL",
            "missing: OPENAI_API_KEY\nnext: add OPENAI_API_KEY to .env and export or use dotenv",
        )
        return
    try:
        import requests
    except ImportError as e:
        _line("OpenAI", "FAIL", f"import requests: {e}")
        return
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                "max_tokens": 8,
            },
            timeout=30,
        )
    except requests.RequestException as e:
        _line("OpenAI", "FAIL", f"request error: {e}")
        return
    if r.status_code not in (200, 201):
        _line(
            "OpenAI",
            "FAIL",
            f"HTTP {r.status_code}: {(r.text or '')[:500]}",
        )
        return
    _line("OpenAI", "OK", f"model hint: {model} (min chat completion succeeded)")


def _check_elevenlabs() -> None:
    key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    voice = (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
    if not key:
        _line(
            "ElevenLabs",
            "FAIL",
            "missing: ELEVENLABS_API_KEY\nnext: set ELEVENLABS_API_KEY in .env",
        )
        return
    if not voice:
        _line(
            "ElevenLabs",
            "FAIL",
            "missing: ELEVENLABS_VOICE_ID\nnext: set ELEVENLABS_VOICE_ID in .env",
        )
        return
    try:
        import requests
    except ImportError as e:
        _line("ElevenLabs", "FAIL", f"import requests: {e}")
        return
    try:
        r = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": key},
            timeout=20,
        )
    except requests.RequestException as e:
        _line("ElevenLabs", "FAIL", f"request error: {e}")
        return
    if r.status_code not in (200, 201):
        _line(
            "ElevenLabs",
            "FAIL",
            f"HTTP {r.status_code}: {(r.text or '')[:500]}",
        )
        return
    _line("ElevenLabs", "OK", "GET /v1/voices succeeded (key valid)")


def _check_ltx() -> None:
    key = (os.environ.get("LTX_API_KEY") or "").strip()
    base = (os.environ.get("LTX_API_BASE_URL") or "").strip()
    if not key:
        _line(
            "LTX",
            "FAIL",
            "missing: LTX_API_KEY\nnext: set LTX_API_KEY in .env",
        )
        if not base:
            _line("LTX", "NOTE", "also set LTX_API_BASE_URL for optional connectivity ping")
        return
    if not base:
        _line(
            "LTX",
            "PARTIAL (key only)",
            "LTX_API_BASE_URL is empty — cannot run HTTP ping; add the official base URL when you have it",
        )
        return
    try:
        import requests
    except ImportError as e:
        _line("LTX", "FAIL", f"import requests: {e}")
        return
    url = base.rstrip("/") + "/"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
    except requests.RequestException as e:
        _line("LTX", "FAIL (ping)", f"request error: {e}\n  tried: {url}")
        return
    sc = r.status_code
    if 200 <= sc < 500:
        _line("LTX", "OK (ping)", f"GET {url} -> HTTP {sc} (reachable)")
    else:
        _line("LTX", "WARN (ping)", f"GET {url} -> HTTP {sc} — server responded; verify path/auth if 404")


def _check_runway() -> None:
    key = (os.environ.get("RUNWAY_API_KEY") or "").strip()
    if not key:
        _line(
            "Runway",
            "FAIL",
            "missing: RUNWAY_API_KEY\n"
            "next: set RUNWAY_API_KEY in .env (no generation task submitted in healthcheck)",
        )
        return
    base = (os.environ.get("RUNWAY_API_BASE_URL") or "").strip()
    if not base:
        _line(
            "Runway",
            "PARTIAL (key only)",
            "RUNWAY_API_BASE_URL is empty — set the official base URL in .env for a "
            "connectivity check (and configure RUNWAY_LIPSYNC_ENDPOINT for API Lip Sync, "
            "or use the manual web upload). No task is submitted in healthcheck.",
        )
        return
    try:
        import requests
    except ImportError as e:
        _line("Runway", "FAIL", f"import requests: {e!s}")
        return
    base = base.rstrip("/")
    ver = (os.environ.get("RUNWAY_API_VERSION") or "").strip().strip("/")
    if ver:
        url = f"{base}/{ver}"
    else:
        url = base
    # Light GET: root or version-scoped path; do not call Lip Sync or task create.
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
    except Exception as e:
        _line(
            "Runway",
            "FAIL (ping)",
            f"request error: {e!s}\n  tried: {url}\n  (set RUNWAY_API_VERSION if the API lives under a version prefix)",
        )
        return
    if 200 <= r.status_code < 500:
        _line(
            "Runway",
            "OK (ping)",
            f"GET {url} -> HTTP {r.status_code} (reachable; no task submitted)",
        )
    else:
        _line(
            "Runway",
            "WARN (ping)",
            f"GET {url} -> HTTP {r.status_code} — verify path/auth; Lip Sync may still require manual flow",
        )


def _check_fmp() -> None:
    key = (os.environ.get("FMP_API_KEY") or "").strip()
    if not key:
        _line(
            "FMP (Financial Modeling Prep)",
            "SKIP (optional)",
            "missing: FMP_API_KEY\n"
            "next: fundamentals / earnings — see src/integrations/fmp_client.py\n"
            "smoke:  PYTHONPATH=. python -m src.integrations.fmp_client --profile AAPL",
        )
        return
    try:
        from src.integrations.fmp_client import FMPClient, FMPError
    except Exception as e:
        _line("FMP", "FAIL (import)", str(e))
        return
    try:
        c = FMPClient()
        rows = c.profile("AAPL")
        if not rows:
            _line("FMP", "WARN", "profile/AAPL returned empty — check plan / key tier")
            return
        row0 = rows[0] if isinstance(rows[0], dict) else {}
        label = row0.get("companyName") or row0.get("symbol") or "?"
        _line(
            "FMP",
            "OK (profile)",
            f"GET profile/AAPL -> {label!r}",
        )
    except FMPError as e:
        _line(
            "FMP",
            "FAIL (API)",
            f"{e}\n  snippet: {(e.body_snippet or '')[:200]}",
        )
    except Exception as e:
        _line("FMP", "FAIL", str(e)[:400])


def _check_envato() -> None:
    key = (os.environ.get("ENVATO_API_KEY") or "").strip()
    if not key:
        _line(
            "Envato",
            "FAIL (optional)",
            "missing: ENVATO_API_KEY\n"
            "next: set if you use Envato API; v1 pipeline uses local files under assets/envato/ without API",
        )
        return
    _line(
        "Envato",
        "OK (key present)",
        "v1: prefer packaging from local assets/envato/; API key is for future use",
    )


def run() -> int:
    _load_dotenv()
    print("=== StateVerge API / integration health ===")
    print(f"project root (for .env): {_ROOT}")
    _check_openai()
    _check_elevenlabs()
    _check_ltx()
    _check_runway()
    _check_fmp()
    _check_envato()
    print("\n--- summary: missing or unset ---")
    missing: list[str] = []
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        missing.append("OPENAI_API_KEY")
    if not (os.environ.get("ELEVENLABS_API_KEY") or "").strip():
        missing.append("ELEVENLABS_API_KEY")
    if not (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip():
        missing.append("ELEVENLABS_VOICE_ID")
    if not (os.environ.get("LTX_API_KEY") or "").strip():
        missing.append("LTX_API_KEY")
    if (os.environ.get("LTX_API_KEY") or "").strip() and not (
        os.environ.get("LTX_API_BASE_URL") or ""
    ).strip():
        missing.append("LTX_API_BASE_URL (recommended if LTX key set)")
    if not (os.environ.get("RUNWAY_API_KEY") or "").strip():
        missing.append("RUNWAY_API_KEY (optional; manual Lip Sync works without it)")
    if (os.environ.get("RUNWAY_API_KEY") or "").strip() and not (
        os.environ.get("RUNWAY_API_BASE_URL") or ""
    ).strip():
        missing.append(
            "RUNWAY_API_BASE_URL (recommended if RUNWAY_API_KEY is set, for health ping)"
        )
    if not (os.environ.get("ENVATO_API_KEY") or "").strip():
        missing.append("ENVATO_API_KEY (optional; local assets in v1)")
    if not (os.environ.get("FMP_API_KEY") or "").strip():
        missing.append("FMP_API_KEY (optional; finance / earnings data)")
    if not missing:
        print("  (none of the required keys for your workflow are empty)")
    else:
        for m in missing:
            print(f"  - {m}")
    print("\nnext steps:")
    print("  1. Copy .env.example -> .env and fill keys you need")
    print("  2. Re-run: python -m src.integrations.healthcheck")
    return 0


if __name__ == "__main__":
    sys.exit(run())
