#!/usr/bin/env python3
"""Upload unlisted from delivery_queue only — gated by ffprobe, QC, doctor, channel_guard, dedupe."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from davinci_production_agent.config import load_config
from davinci_production_agent.delivery_queue import list_manifests, prefer_delivery_queue_for_upload, read_manifest
from davinci_production_agent.paths import DELIVERY_QUEUE_ROOT, FINISHED_FOR_YOUTUBE_ROOT, REPORTS_ROOT
from davinci_production_agent.qc_gate import validate_render


def _doctor_allowed() -> tuple[bool, dict[str, Any]]:
    try:
        from doctor_gate_client import check_real_upload_allowed_by_doctor  # noqa: WPS433

        allowed, detail, warnings = check_real_upload_allowed_by_doctor(dry_run=True)
        return bool(allowed), {"detail": detail, "warnings": warnings}
    except Exception as exc:  # noqa: BLE001
        return False, {"error": repr(exc)}


def _channel_guard(kind: str) -> tuple[bool, str]:
    try:
        if kind == "shorts":
            from channel_guard import validate_shorts_channel_token  # noqa: WPS433

            ok, reason = validate_shorts_channel_token()
            return bool(ok), str(reason or "")
        from channel_guard import validate_long_channel_token  # noqa: WPS433

        ok, reason = validate_long_channel_token()
        return bool(ok), str(reason or "")
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)


def _load_render_report(job_id: str) -> dict[str, Any] | None:
    p = REPORTS_ROOT / job_id / "render_report.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def preflight_upload(manifest_path: Path, *, dry_run: bool = True) -> dict[str, Any]:
    """Gate upload: never public; prefer delivery_queue; full validation chain."""
    cfg = load_config()
    out: dict[str, Any] = {
        "manifest": str(manifest_path),
        "dry_run": dry_run,
        "allowed": False,
        "privacy": "unlisted",
        "block_reason": "",
    }

    if cfg.get("public_upload_disabled", True):
        out["public_upload_disabled"] = True

    if not cfg.get("enable_upload_from_queue", False) and not dry_run:
        out["block_reason"] = "upload_from_queue_disabled_in_config"
        return out

    if not prefer_delivery_queue_for_upload():
        out["warnings"] = ["prefer_delivery_queue=false"]

    if not str(manifest_path).startswith(str(DELIVERY_QUEUE_ROOT)):
        out["block_reason"] = "manifest_not_in_delivery_queue"
        return out

    manifest = read_manifest(manifest_path)
    if not manifest:
        out["block_reason"] = "manifest_invalid"
        return out

    kind = str(manifest.get("kind") or "long")
    render_path = Path(str(manifest.get("render_path") or ""))
    job_id = str(manifest.get("job_id") or "")

    if cfg.get("public_upload_disabled") and manifest.get("upload_privacy") == "public":
        out["block_reason"] = "public_upload_disabled"
        return out

    if not render_path.is_file():
        out["block_reason"] = "render_missing"
        return out

    qc = validate_render(render_path, kind=kind)
    out["qc"] = qc
    if not qc.get("ok"):
        out["block_reason"] = "qc_failed"
        return out

    rr = _load_render_report(job_id) if job_id else None
    out["render_report_present"] = rr is not None
    if job_id and not rr:
        out["warnings"] = (out.get("warnings") or []) + ["render_report_missing"]

    meta = manifest.get("metadata") or {}
    if meta.get("title_truth_failed"):
        out["block_reason"] = "metadata_title_truth_failed"
        return out

    doc_ok, doc_detail = _doctor_allowed()
    out["doctor"] = doc_detail
    if not doc_ok:
        out["block_reason"] = "doctor_gate_block_upload"
        return out

    ch_ok, ch_reason = _channel_guard(kind)
    out["channel_guard_ok"] = ch_ok
    if not ch_ok:
        out["block_reason"] = "channel_guard_failed"
        out["channel_guard_detail"] = ch_reason
        return out

    out["allowed"] = True
    out["note"] = "v1 scaffold: invoke auto_publish_queue when wired; dry_run only unless enable_upload_from_queue"
    if dry_run:
        out["would_upload"] = str(render_path)
        out["privacy"] = str(manifest.get("upload_privacy") or cfg.get("upload_privacy") or "unlisted")
    return out


def next_manifest_for_upload(*, kind: str | None = None) -> Path | None:
    manifests = list_manifests(kind=kind)  # type: ignore[arg-type]
    for m in manifests:
        pf = preflight_upload(m, dry_run=True)
        if pf.get("allowed"):
            return m
    return None


def execute_upload(manifest_path: Path, *, dry_run: bool = True) -> dict[str, Any]:
    """Upload unlisted from delivery_queue when enable_upload_from_queue and all gates pass."""
    pf = preflight_upload(manifest_path, dry_run=dry_run)
    out: dict[str, Any] = {
        "manifest": str(manifest_path),
        "preflight": pf,
        "dry_run": dry_run,
        "status": "blocked",
        "video_type": "short",
        "channel": "SHORTS",
        "privacy": "unlisted",
        "public_upload_disabled": True,
    }
    manifest = read_manifest(manifest_path)
    if manifest:
        kind = str(manifest.get("kind") or "long")
        out["video_type"] = "short" if kind == "shorts" else "long"
        out["channel"] = "SHORTS" if kind == "shorts" else "NYC_LONG"

    if not pf.get("allowed"):
        out["block_reason"] = pf.get("block_reason") or "preflight_failed"
        out["status"] = "blocked"
        return out

    cfg = load_config()
    if dry_run:
        out["status"] = "dry_run"
        out["would_upload"] = pf.get("would_upload")
        return out

    if not cfg.get("enable_upload_from_queue", False):
        out["block_reason"] = "upload_from_queue_disabled_in_config"
        out["status"] = "blocked"
        return out

    render_path = Path(str((manifest or {}).get("render_path") or ""))
    kind = str((manifest or {}).get("kind") or "long")
    privacy = str((manifest or {}).get("upload_privacy") or cfg.get("upload_privacy") or "unlisted")
    if privacy == "public":
        out["block_reason"] = "public_upload_disabled"
        out["status"] = "blocked"
        return out

    try:
        from always_publish.failure_recovery import should_retry_now  # noqa: WPS433

        retry_ok, retry_reason = should_retry_now()
        if not retry_ok:
            out["block_reason"] = retry_reason
            out["status"] = "blocked"
            return out
    except Exception:
        pass

    try:
        import tempfile

        nyc_auto = _SCRIPTS / "nyc_auto"
        if nyc_auto.is_dir() and str(nyc_auto) not in sys.path:
            sys.path.insert(0, str(nyc_auto))

        pkg = Path(tempfile.mkdtemp(prefix="sv_delivery_queue_"))
        selected = pkg / "selected_video_path.txt"
        selected.write_text(str(render_path.resolve()) + "\n", encoding="utf-8")
        meta = (manifest or {}).get("metadata") or {}
        title = str(meta.get("title") or render_path.stem.replace("_", " ")[:90])
        (pkg / "title.txt").write_text(title + "\n", encoding="utf-8")
        (pkg / "description.txt").write_text(str(meta.get("description") or "StateVerge") + "\n", encoding="utf-8")

        if kind == "shorts":
            from channel_guard import validate_shorts_channel_token  # noqa: WPS433
            from youtube_token_paths import token_shorts_path  # noqa: WPS433
            from youtube_upload import upload_from_package_directory  # noqa: WPS433

            ok_tok, _ = validate_shorts_channel_token(token_shorts_path())
            if not ok_tok:
                out["block_reason"] = "channel_guard_failed"
                return out
            res = upload_from_package_directory(
                pkg,
                privacy_status=privacy,
                video_type="short",
                no_public=True,
            )
        else:
            from channel_guard import validate_long_channel_token  # noqa: WPS433
            from youtube_token_paths import resolve_long_form_upload_token  # noqa: WPS433
            from youtube_upload import upload_from_package_directory  # noqa: WPS433

            tok, _ = resolve_long_form_upload_token(None)
            ok_tok, _ = validate_long_channel_token(tok)
            if not ok_tok:
                out["block_reason"] = "channel_guard_failed"
                return out
            res = upload_from_package_directory(
                pkg,
                privacy_status=privacy,
                video_type="long",
                no_public=True,
            )
        out["upload_result"] = res
        if res.get("uploaded") or res.get("video_id"):
            out["status"] = "uploaded"
            out["uploaded"] = True
            from davinci_production_agent.delivery_queue import update_manifest  # noqa: WPS433

            update_manifest(manifest_path, {"status": "uploaded", "upload_ready": False})
        else:
            out["status"] = "upload_failed"
            out["block_reason"] = str(res.get("error") or res.get("block_reason") or "upload_failed")
            from davinci_production_agent.delivery_queue import mark_manifest_failed  # noqa: WPS433

            mark_manifest_failed(manifest_path, block_reason=out["block_reason"])
    except Exception as exc:  # noqa: BLE001
        out["status"] = "upload_failed"
        out["block_reason"] = repr(exc)
        try:
            from davinci_production_agent.delivery_queue import mark_manifest_failed  # noqa: WPS433

            mark_manifest_failed(manifest_path, block_reason=out["block_reason"])
        except Exception:
            pass

    out["exit_code"] = 0 if out.get("status") in ("uploaded", "dry_run", "blocked") else 1
    return out


def send_to_review(render_path: Path, *, kind: str) -> Path:
    """Copy manifest pointer to finished_for_youtube review area (never overwrites source)."""
    FINISHED_FOR_YOUTUBE_ROOT.mkdir(parents=True, exist_ok=True)
    sub = FINISHED_FOR_YOUTUBE_ROOT / ("shorts" if kind == "shorts" else "long")
    sub.mkdir(parents=True, exist_ok=True)
    marker = sub / f"{render_path.stem}.review.json"
    body = {
        "version": "review_queue_v1",
        "render_path": str(render_path),
        "kind": kind,
        "review_before_public": True,
        "note": "Human review required before any public upload.",
    }
    marker.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return marker
