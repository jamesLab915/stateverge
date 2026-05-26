#!/usr/bin/env python3
"""Controlled OpenAI client: whitelist tasks, budgets, redaction, cache, ledger."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Literal

STATEVERGE = Path.home() / "StateVerge"
POLICY_PATH = STATEVERGE / "config" / "ai_agent_policy.json"
# Canonical Pro key path (must match ops docs); also search HOME-based path if distinct.
STANDARD_OPENAI_KEY_FILE = Path("/Users/ziweizhang/StateVerge/.secrets/openai/openai_api_key")
KEY_FILE = STANDARD_OPENAI_KEY_FILE
LEDGER_PATH = STATEVERGE / "data" / "ai_usage" / "ai_usage_ledger.json"
CACHE_DIR = STATEVERGE / "data" / "ai_cache"

_REDACT_PATTERNS = (
    (re.compile(r"(?i)OPENAI_API_KEY\s*=\s*\S+"), "OPENAI_API_KEY=[REDACTED]"),
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)Authorization:\s*[^\n]+"), "Authorization: [REDACTED]"),
    (re.compile(r"(?i)refresh_token[\"']?\s*[:=]\s*[^\s,}\]]+"), "refresh_token=[REDACTED]"),
    (re.compile(r"(?i)access_token[\"']?\s*[:=]\s*[^\s,}\]]+"), "access_token=[REDACTED]"),
    (re.compile(r"(?i)private_key[\"']?\s*[:=]\s*[^\n]+"), "private_key=[REDACTED]"),
    (re.compile(r"/Users/[^\s/]+/StateVerge/\.secrets/\S+"), "[SECRETS_PATH]"),
)


def _load_policy() -> dict[str, Any]:
    if not POLICY_PATH.is_file():
        return {"enabled": False}
    try:
        return json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"enabled": False}


def _openai_key_file_candidates() -> list[Path]:
    """Ordered key file locations: canonical Pro path, then ~/StateVerge mirror."""
    home_path = Path.home() / "StateVerge" / ".secrets" / "openai" / "openai_api_key"
    ordered = [STANDARD_OPENAI_KEY_FILE, home_path]
    seen: set[str] = set()
    out: list[Path] = []
    for p in ordered:
        try:
            sig = str(p.resolve())
        except OSError:
            sig = str(p)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(p)
    return out


def resolve_openai_api_key() -> str | None:
    """Return the real API key string, or None if unset/unreadable. Never logs the key."""
    env = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if env:
        return env
    for path in _openai_key_file_candidates():
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if raw:
                return raw
        except OSError:
            continue
    return None


def _openai_key_source_for_ledger(resolved: str | None) -> Literal["env", "file", "none"]:
    if not resolved:
        return "none"
    env = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if env and resolved == env:
        return "env"
    return "file"


def get_openai_api_key_status() -> dict[str, Any]:
    """Non-secret key detection for diagnostics and API status (no key material)."""
    standard = STANDARD_OPENAI_KEY_FILE
    key_file_exists = standard.is_file()
    key_file_size_gt_20 = False
    if key_file_exists:
        try:
            st = standard.stat()
            if st.st_size > 20:
                key_file_size_gt_20 = True
            else:
                content = standard.read_text(encoding="utf-8").strip()
                key_file_size_gt_20 = len(content) > 20
        except OSError:
            key_file_size_gt_20 = False
    key = resolve_openai_api_key()
    api_key_present = bool(key)
    key_length_gt_20 = bool(key and len(key) > 20)
    env_raw = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key_present:
        src: Literal["env", "file", "none"] = "none"
    elif env_raw and key == env_raw:
        src = "env"
    else:
        src = "file"
    return {
        "api_key_present": api_key_present,
        "api_key_source": src,
        "key_file_exists": key_file_exists,
        "key_file_size_gt_20": key_file_size_gt_20,
        "key_length_gt_20": key_length_gt_20,
    }


def sanitize_prompt(text: str) -> str:
    out = text or ""
    for rx, rep in _REDACT_PATTERNS:
        out = rx.sub(rep, out)
    out = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[EMAIL]",
        out,
    )
    if len(out) > 200_000:
        out = out[:200_000] + "\n...[truncated]"
    return out


def _today() -> str:
    return date.today().isoformat()


def _ledger_append(entry: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    if LEDGER_PATH.is_file():
        try:
            prev = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
            if isinstance(prev, list):
                rows = prev
        except (OSError, json.JSONDecodeError):
            rows = []
    rows.append(entry)
    try:
        LEDGER_PATH.write_text(json.dumps(rows[-4000:], indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _count_today(task: str) -> tuple[int, int]:
    if not LEDGER_PATH.is_file():
        return 0, 0
    try:
        rows = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, 0
    if not isinstance(rows, list):
        return 0, 0
    d = _today()
    total = 0
    per_task = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("date") or "") != d:
            continue
        total += 1
        if str(r.get("task") or "") == task:
            per_task += 1
    return total, per_task


def _cache_path(task: str, text: str) -> Path:
    h = hashlib.sha256(f"{task}:{text}".encode("utf-8", errors="replace")).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{h}.json"


def ai_request(
    task: str,
    user_payload: str,
    *,
    system_hint: str = "Return compact JSON only when asked.",
) -> dict[str, Any]:
    """Returns dict with ok, and either parsed JSON in data or fallback flags."""
    policy = _load_policy()
    if not policy.get("enabled", True):
        return {"ok": False, "fallback_required": True, "error_type": "policy_disabled", "safe_message": "AI disabled"}

    allowed = set(policy.get("allowed_tasks") or [])
    blocked = set(policy.get("blocked_tasks") or [])
    if task in blocked or task not in allowed:
        return {"ok": False, "fallback_required": True, "error_type": "task_not_allowed", "safe_message": task}

    max_day = int(policy.get("max_requests_per_day") or 200)
    max_task = int(policy.get("max_requests_per_task_per_day") or 50)
    max_in = int(policy.get("max_input_chars_per_request") or 20_000)
    max_out = int(policy.get("max_output_chars") or 1200)

    total, per_t = _count_today(task)
    if total >= max_day or per_t >= max_task:
        return {"ok": False, "fallback_required": True, "error_type": "budget_exceeded", "safe_message": "daily_limit"}

    text = sanitize_prompt(user_payload)
    if len(text) > max_in:
        text = text[:max_in]

    if policy.get("cache_enabled", True):
        cp = _cache_path(task, text)
        if cp.is_file():
            try:
                cached = json.loads(cp.read_text(encoding="utf-8"))
                _ledger_append(
                    {
                        "date": _today(),
                        "task": task,
                        "model": str(cached.get("model") or ""),
                        "estimated_input_chars": len(text),
                        "estimated_output_chars": int(cached.get("out_chars") or 0),
                        "request_count": 1,
                        "success": True,
                        "fallback_used": False,
                        "cache_hit": True,
                    }
                )
                return {"ok": True, "data": cached.get("parsed"), "cache_used": True, "fallback_used": False}
            except (OSError, json.JSONDecodeError, TypeError):
                pass

    key = resolve_openai_api_key()
    if not key:
        _ledger_append(
            {
                "date": _today(),
                "task": task,
                "model": "",
                "estimated_input_chars": len(text),
                "estimated_output_chars": 0,
                "request_count": 0,
                "success": False,
                "fallback_used": True,
                "error": "ai_key_missing",
            }
        )
        return {"ok": False, "fallback_required": True, "error_type": "ai_key_missing", "safe_message": "no_key"}

    key_src = _openai_key_source_for_ledger(key)

    model = str(policy.get("default_model") or "gpt-4o-mini")
    fb_model = str(policy.get("fallback_model") or "").strip()
    model_candidates = [model]
    if fb_model and fb_model != model:
        model_candidates.append(fb_model)

    last_exc: Exception | None = None
    for model_use in model_candidates:
        body = {
            "model": model_use,
            "messages": [
                {"role": "system", "content": system_hint},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
            "max_tokens": min(800, max_out // 2 + 200),
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
            txt = (((raw.get("choices") or [{}])[0] or {}).get("message") or {}).get("content") or ""
            txt = (txt or "")[:max_out]
            parsed: Any = None
            m = re.search(r"\{[\s\S]*\}", txt)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except json.JSONDecodeError:
                    parsed = {"raw_tail": txt[-400:]}
            else:
                parsed = {"summary": txt}
            entry = {
                "date": _today(),
                "task": task,
                "model": model_use,
                "estimated_input_chars": len(text),
                "estimated_output_chars": len(txt),
                "request_count": 1,
                "success": True,
                "fallback_used": False,
                "key_source": key_src,
            }
            _ledger_append(entry)
            if policy.get("cache_enabled", True):
                try:
                    _cache_path(task, text).write_text(
                        json.dumps({"parsed": parsed, "model": model_use, "out_chars": len(txt)}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    pass
            return {
                "ok": True,
                "data": parsed,
                "fallback_used": False,
                "cache_used": False,
                "model_used": model_use,
            }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_exc = exc
            continue

    _ledger_append(
        {
            "date": _today(),
            "task": task,
            "model": model_candidates[-1] if model_candidates else "",
            "estimated_input_chars": len(text),
            "estimated_output_chars": 0,
            "request_count": 1,
            "success": False,
            "fallback_used": True,
            "error": type(last_exc).__name__ if last_exc else "unknown",
        }
    )
    return {
        "ok": False,
        "fallback_required": True,
        "error_type": type(last_exc).__name__ if last_exc else "request_failed",
        "safe_message": "request_failed",
    }


def usage_today_summary() -> dict[str, Any]:
    if not LEDGER_PATH.is_file():
        return {"date": _today(), "requests_today": 0}
    try:
        rows = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"date": _today(), "requests_today": 0}
    d = _today()
    c = 0
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and str(r.get("date")) == d and r.get("success"):
                c += 1
    return {"date": d, "requests_today": c}
