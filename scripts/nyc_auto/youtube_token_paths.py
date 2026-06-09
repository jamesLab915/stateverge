"""Canonical YouTube OAuth paths (Pro-only MacBook: ~/StateVerge).

Long-form uploads must use ``data/youtube/token.json``. Shorts use ``token_shorts.json`` only.
Do not fall back from long to Shorts or vice versa in upload code paths.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from stateverge_paths import CODE_ROOT  # noqa: E402

# Official paths (STATEVERGE_ROOT / ziweizhang StateVerge repo)
OFFICIAL_LONG_TOKEN_PATH: Path = CODE_ROOT / "data" / "youtube" / "token.json"
OFFICIAL_SHORTS_TOKEN_PATH: Path = CODE_ROOT / "data" / "youtube" / "token_shorts.json"
# Repurposed: former Real NYC Shorts OAuth slot → 张子维 Ziwei Zhang 国语 MV 频道
OFFICIAL_ZHANG_ZIWEI_TOKEN_PATH: Path = OFFICIAL_SHORTS_TOKEN_PATH
OFFICIAL_MUSIC_TOKEN_PATH: Path = CODE_ROOT / "data" / "youtube" / "token_music.json"
OFFICIAL_CLIENT_SECRETS_PATH: Path = CODE_ROOT / ".secrets" / "youtube" / "client_secrets.json"
LEGACY_LONG_TOKEN_PATH: Path = CODE_ROOT / ".secrets" / "youtube" / "token.json"

# Back-compat re-exports used by youtube_upload / youtube_batch_upload imports
DEFAULT_TOKEN: Path = OFFICIAL_LONG_TOKEN_PATH
DEFAULT_SECRETS: Path = OFFICIAL_CLIENT_SECRETS_PATH


def resolve_long_form_upload_token(cli_token: Path | None) -> tuple[Path, list[str]]:
    """Resolve long-channel token: ``--token`` wins; else official; else legacy (with warning)."""
    warnings: list[str] = []
    if cli_token is not None:
        return cli_token.expanduser().resolve(), warnings
    official = OFFICIAL_LONG_TOKEN_PATH.expanduser().resolve()
    if official.is_file():
        return official, warnings
    legacy = LEGACY_LONG_TOKEN_PATH.expanduser().resolve()
    if legacy.is_file():
        warnings.append("legacy_token_path_used")
        return legacy, warnings
    return official, warnings


def resolve_music_upload_token(cli_token: Path | None) -> tuple[Path, list[str]]:
    """Resolve StateVerge Music channel token (``token_music.json`` only)."""
    warnings: list[str] = []
    if cli_token is not None:
        return cli_token.expanduser().resolve(), warnings
    official = OFFICIAL_MUSIC_TOKEN_PATH.expanduser().resolve()
    return official, warnings


def resolve_zhang_ziwei_upload_token(cli_token: Path | None) -> tuple[Path, list[str]]:
    """Resolve 张子维 channel token (``token_shorts.json`` repurposed slot)."""
    warnings: list[str] = []
    if cli_token is not None:
        return cli_token.expanduser().resolve(), warnings
    official = OFFICIAL_ZHANG_ZIWEI_TOKEN_PATH.expanduser().resolve()
    return official, warnings


def zhang_ziwei_token_invalid_grant_fix_command() -> str:
    return (
        "python3 scripts/nyc_auto/youtube_auth_zhang_ziwei.py "
        f"--client-secrets {OFFICIAL_CLIENT_SECRETS_PATH} "
        f"--token {OFFICIAL_ZHANG_ZIWEI_TOKEN_PATH}"
    )


def resolve_client_secrets(cli_secrets: Path | None) -> Path:
    if cli_secrets is not None:
        return cli_secrets.expanduser().resolve()
    return OFFICIAL_CLIENT_SECRETS_PATH.expanduser().resolve()


def long_token_invalid_grant_fix_command() -> str:
    return (
        "python3 scripts/nyc_auto/youtube_auth_init.py "
        f"--client-secrets {OFFICIAL_CLIENT_SECRETS_PATH} "
        f"--token {OFFICIAL_LONG_TOKEN_PATH}"
    )


def music_token_invalid_grant_fix_command() -> str:
    return (
        "python3 scripts/nyc_auto/youtube_auth_music.py "
        f"--client-secrets {OFFICIAL_CLIENT_SECRETS_PATH} "
        f"--token {OFFICIAL_MUSIC_TOKEN_PATH}"
    )
