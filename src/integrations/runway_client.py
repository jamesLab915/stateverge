"""
Minimal HTTP client for Runway: Bearer auth, GET/POST, .env support.

Official Lip Sync / task URL shapes vary by product version; this module
only provides transport. Callers handle endpoint paths and response parsing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

# Repo root: .../StateVerge
_ROOT = Path(__file__).resolve().parent.parent.parent


class RunwayError(Exception):
    """Base for Runway client errors (configuration or HTTP)."""


class RunwayConfigurationError(RunwayError):
    """Missing or invalid environment / URL configuration."""


class RunwayRequestError(RunwayError):
    """Non-success HTTP or transport failure."""

    def __init__(
        self, message: str, *, status_code: Optional[int] = None, body: str = ""
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def load_dotenv() -> None:
    """Load ``.env`` from repo root if ``python-dotenv`` is available."""
    p = _ROOT / ".env"
    if not p.is_file():
        return
    try:
        from dotenv import load_dotenv as _ld

        _ld(p, override=False)
    except Exception:
        pass


def get_api_key() -> str:
    k = (os.environ.get("RUNWAY_API_KEY") or "").strip()
    if not k:
        raise RunwayConfigurationError(
            "RUNWAY_API_KEY is not set. Add it to .env (or the environment) "
            "or continue with the manual Runway web upload flow."
        )
    return k


def get_base_url() -> str:
    return (os.environ.get("RUNWAY_API_BASE_URL") or "").strip().rstrip("/")


def get_api_version() -> str:
    return (os.environ.get("RUNWAY_API_VERSION") or "").strip().strip("/")


def _is_absolute_url(path_or_url: str) -> bool:
    try:
        u = urlparse(path_or_url)
        return u.scheme in ("http", "https")
    except Exception:
        return False


def _join_base_path(base: str, path: str) -> str:
    path = path.lstrip("/")
    if not base:
        if _is_absolute_url(path):
            return path
        raise RunwayConfigurationError("RUNWAY_API_BASE_URL is empty; cannot build URL.")
    if _is_absolute_url(path):
        return path
    return urljoin(base + "/", path)


def request(
    method: str,
    path_or_url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json: Any = None,
    data: Any = None,
    files: Any = None,
    extra_headers: Optional[dict[str, str]] = None,
    timeout: float = 120.0,
) -> Any:
    """
    Run an authenticated request.

    * *path_or_url* — If it starts with ``http``/``https``, it is used as the full URL.
      Otherwise it is joined with :envvar:`RUNWAY_API_BASE_URL`.

    Returns the ``requests`` ``Response`` on success (2xx). Raises
    :exc:`RunwayRequestError` or :exc:`RunwayConfigurationError` on failure.
    """
    try:
        import requests
    except ImportError as e:  # pragma: no cover
        raise RunwayConfigurationError(
            f"The 'requests' package is required: {e!s}"
        ) from e

    method_u = (method or "GET").upper()
    if method_u not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"):
        raise RunwayConfigurationError(f"Unsupported HTTP method: {method!r}")

    key = get_api_key()
    base = get_base_url()
    url = _join_base_path(base, path_or_url)

    headers: dict[str, str] = {"Authorization": f"Bearer {key}"}
    if extra_headers:
        headers.update(extra_headers)
    if json is not None and files is None:
        headers.setdefault("Content-Type", "application/json")
    if files is not None:
        # Let requests set multipart Content-Type.
        headers.pop("Content-Type", None)

    try:
        r = requests.request(
            method_u,
            url,
            headers=headers,
            params=params,
            json=json,
            data=data,
            files=files,
            timeout=timeout,
        )
    except Exception as e:
        raise RunwayRequestError(
            f"Runway request failed ({method_u} {url}): {e!s}",
        ) from e

    if not (200 <= r.status_code < 300):
        body = (r.text or "")[:5000]
        msg = f"Runway API error: HTTP {r.status_code} for {method_u} {url}\n{body}"
        err = RunwayRequestError(
            msg,
            status_code=r.status_code,
            body=body,
        )
        raise err
    return r
