#!/usr/bin/env python3
"""NYC long publish schedule v1: daily 1h + every-N-days 3h, music-first roots."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _REPO / "config" / "nyc_long_publish_schedule_v1.json"
_HOME_STATE = Path.home() / "StateVerge" / "data" / "long_runtime" / "nyc_long_schedule_state.json"
_CACHE_STATE = Path("/Volumes/SV_CACHE/review_reports/nyc_long_schedule_state.json")

_MUSIC_EXTS = {".m4a", ".mp3", ".aac", ".wav", ".flac", ".aiff", ".aif"}
_MUSIC_SCAN_MAX = 4000


def _utc_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _local_date_str() -> str:
    return date.today().isoformat()


def load_schedule_config() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_state_path() -> Path:
    if _CACHE_STATE.parent.is_dir() or os.environ.get("NYC_LONG_SCHEDULE_STATE", "").strip():
        env = os.environ.get("NYC_LONG_SCHEDULE_STATE", "").strip()
        if env:
            return Path(env).expanduser()
        try:
            _CACHE_STATE.parent.mkdir(parents=True, exist_ok=True)
            return _CACHE_STATE
        except OSError:
            pass
    _HOME_STATE.parent.mkdir(parents=True, exist_ok=True)
    return _HOME_STATE


def load_schedule_state() -> dict[str, Any]:
    p = resolve_state_path()
    if not p.is_file():
        return {"today_date": "", "produced_today": {"1h": 0, "3h": 0}, "last_3h_date": "", "days_since_3h": 0}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"today_date": "", "produced_today": {"1h": 0, "3h": 0}, "last_3h_date": "", "days_since_3h": 0}
    if not isinstance(data, dict):
        return {"today_date": "", "produced_today": {"1h": 0, "3h": 0}, "last_3h_date": "", "days_since_3h": 0}
    return data


def save_schedule_state(state: dict[str, Any]) -> Path:
    p = resolve_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def _roll_state_for_today(state: dict[str, Any], today: str) -> dict[str, Any]:
    if state.get("today_date") != today:
        state["today_date"] = today
        state["produced_today"] = {"1h": 0, "3h": 0}
    pt = state.get("produced_today")
    if not isinstance(pt, dict):
        state["produced_today"] = {"1h": 0, "3h": 0}
    else:
        state["produced_today"] = {"1h": int(pt.get("1h") or 0), "3h": int(pt.get("3h") or 0)}
    last_3h = str(state.get("last_3h_date") or "").strip()
    if last_3h:
        try:
            d0 = date.fromisoformat(last_3h)
            d1 = date.fromisoformat(today)
            state["days_since_3h"] = max(0, (d1 - d0).days)
        except ValueError:
            state["days_since_3h"] = 0
    else:
        state["days_since_3h"] = 999
    return state


def _is_extended_day(d: date, every_n: int) -> bool:
    if every_n < 1:
        every_n = 3
    epoch = date(1970, 1, 1)
    return ((d - epoch).days % every_n) == 0


def get_today_plan(*, on_date: date | None = None) -> dict[str, Any]:
    cfg = load_schedule_config()
    d = on_date or date.today()
    every_n = int(cfg.get("every_n_days_3h") or 3)
    td = cfg.get("target_duration_sec") or {}
    sec_1h = int(td.get("standard") or 3600)
    sec_3h = int(td.get("extended") or 10800)
    jobs: list[dict[str, Any]] = []
    if bool(cfg.get("daily_1h_enabled", True)):
        jobs.append({"kind": "1h", "duration_sec": sec_1h})
    state = _roll_state_for_today(load_schedule_state(), d.isoformat())
    extended_by_epoch = _is_extended_day(d, every_n)
    extended_by_state = int(state.get("days_since_3h") or 0) >= every_n
    if extended_by_epoch or extended_by_state:
        jobs.append({"kind": "3h", "duration_sec": sec_3h})
    return {
        "version": str(cfg.get("version") or "nyc_long_publish_schedule_v1"),
        "date": d.isoformat(),
        "jobs": jobs,
        "extended_day": bool(extended_by_epoch or extended_by_state),
        "extended_by_epoch": extended_by_epoch,
        "extended_by_state": extended_by_state,
        "every_n_days_3h": every_n,
        "state_snapshot": {
            "last_3h_date": state.get("last_3h_date"),
            "days_since_3h": state.get("days_since_3h"),
            "produced_today": state.get("produced_today"),
        },
    }


def _collect_music_candidates(root: Path, *, recursive: bool = True) -> list[Path]:
    """Bounded scan of audio under ``root``; suno inbox includes all subdirs."""
    if not root.is_dir():
        return []
    out: list[Path] = []
    try:
        it = root.rglob("*") if recursive else root.glob("*")
        for p in it:
            if len(out) >= _MUSIC_SCAN_MAX:
                break
            if not p.is_file() or p.name.startswith("._"):
                continue
            if p.suffix.lower() in _MUSIC_EXTS:
                try:
                    if p.stat().st_size > 4096:
                        out.append(p)
                except OSError:
                    continue
    except OSError:
        return out
    return out


def _newest(cands: list[Path]) -> Path | None:
    if not cands:
        return None
    try:
        cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass
    return cands[0]


def _load_suno_cfg(c: dict[str, Any]) -> dict[str, Any]:
    rel = str(c.get("suno_categories_config") or "config/stateverge_suno_music_categories_v1.json")
    p = Path(rel).expanduser()
    if not p.is_file():
        p = _REPO / "config" / "stateverge_suno_music_categories_v1.json"
    try:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def pick_long_music_file(
    *,
    cfg: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    theme: str = "driving",
    time_bucket: str = "",
) -> tuple[Path | None, str, list[str]]:
    """Search Suno categories (scene-aware) then music_roots_priority."""
    w = warnings if warnings is not None else []
    c = cfg if cfg is not None else load_schedule_config()
    if theme == "ferry":
        w.append("suno_skipped_ferry_real_sound_only")
        return None, "ferry_no_bgm", w

    try:
        scripts_dir = Path(__file__).resolve().parents[1]
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from music_selector import (  # noqa: WPS433
            load_suno_categories_config,
            pick_suno_track_for_categories,
            resolve_suno_categories_for_scene,
        )

        suno_cfg = _load_suno_cfg(c) or load_suno_categories_config()
        blob = time_bucket or ""
        signals = {"is_night": time_bucket in ("night", "evening", "rainy_night")}
        if "rain" in blob.lower():
            signals["is_night"] = True
        cats = resolve_suno_categories_for_scene(theme=theme, signals=signals, blob=blob, cfg=suno_cfg)
        if cats:
            pick, cat, sw = pick_suno_track_for_categories(cats, cfg=suno_cfg, theme=theme)
            w.extend(sw)
            if pick:
                return pick, f"suno:{cat}", w
        if theme in ("skyline_sequence", "rain_night", "skyline", "rain", "night", "unknown"):
            from music_selector import select_nyc_long_music  # noqa: WPS433

            sel = select_nyc_long_music(media_entries=[], xfer=None, dry_run=True)
            pth = str(sel.get("selected_track_path") or "").strip()
            if pth and Path(pth).is_file():
                w.append("suno_empty_envato_fallback")
                return Path(pth), f"envato:{sel.get('selected_category')}", w
    except Exception as exc:  # noqa: BLE001
        w.append(f"suno_category_pick_failed:{type(exc).__name__}")

    roots = c.get("music_roots_priority") or [
        "/Volumes/SV_CACHE/inbox/suno",
        "/Volumes/SV_TRANSFER/04_AUDIO/music/stateverge_suno",
        "/Volumes/SV_CACHE/inbox/souno",
        "/Volumes/SV_TRANSFER/04_AUDIO/music/nyc_long",
    ]
    for raw in roots:
        root = Path(str(raw)).expanduser()
        label = root.name or str(root)
        if not root.is_dir():
            w.append(f"music_root_missing:{label}")
            continue
        pick = _newest(_collect_music_candidates(root, recursive=True))
        if pick:
            return pick, label, w
    return None, "", w


def primary_music_root(cfg: dict[str, Any] | None = None) -> str:
    c = cfg if cfg is not None else load_schedule_config()
    roots = c.get("music_roots_priority") or []
    for raw in roots:
        p = Path(str(raw)).expanduser()
        if p.is_dir():
            return str(p)
    return str(roots[0]) if roots else "/Volumes/SV_CACHE/inbox/suno"


def record_production(kind: str) -> dict[str, Any]:
    today = _local_date_str()
    state = _roll_state_for_today(load_schedule_state(), today)
    k = "1h" if kind in ("1h", "standard") else "3h" if kind in ("3h", "extended") else kind
    pt = state.setdefault("produced_today", {"1h": 0, "3h": 0})
    if isinstance(pt, dict) and k in ("1h", "3h"):
        pt[k] = int(pt.get(k) or 0) + 1
    if k == "3h":
        state["last_3h_date"] = today
        state["days_since_3h"] = 0
    path = save_schedule_state(state)
    return {"state_path": str(path), "produced_today": state.get("produced_today"), "kind": k}


def evaluate_publish_schedule_gate(
    *,
    kind: str = "1h",
    force: bool = False,
) -> dict[str, Any]:
    """Quota-guaranteed gate: block only when today's successful uploads meet daily cap."""
    plan = get_today_plan()
    today = _local_date_str()
    state = _roll_state_for_today(load_schedule_state(), today)
    pt = state.get("produced_today") or {}
    produced_1h = int(pt.get("1h") or 0)
    produced_3h = int(pt.get("3h") or 0)
    k = "1h" if kind in ("1h", "standard", "") else "3h"
    allowed = True
    reason = "ok"
    quota_gate: dict[str, Any] = {}
    try:
        _scripts = Path(__file__).resolve().parent.parent
        if str(_scripts) not in sys.path:
            sys.path.insert(0, str(_scripts))
        from always_publish.quota_guard import long_upload_allowed  # noqa: WPS433

        quota_gate = long_upload_allowed(kind=k, force=force)
        allowed = bool(quota_gate.get("allowed"))
        reason = str(quota_gate.get("reason") or "ok")
    except Exception as exc:  # noqa: BLE001
        quota_gate = {"fail_open": True, "error": repr(exc)}
        if not force:
            if k == "1h" and produced_1h >= 1:
                allowed = False
                reason = "schedule_gate_1h_already_produced_today"
            elif k == "3h" and produced_3h >= 1:
                allowed = False
                reason = "schedule_gate_3h_already_produced_today"
    cfg = load_schedule_config()
    music_path, music_label, mw = pick_long_music_file(cfg=cfg)
    return {
        "allowed": allowed,
        "reason": reason,
        "schedule_kind": k,
        "schedule_mode": str(quota_gate.get("schedule_mode") or "quota_guaranteed"),
        "force": bool(force),
        "today_plan": plan,
        "produced_today": {"1h": produced_1h, "3h": produced_3h},
        "quota_gate": quota_gate,
        "long_uploaded": int(quota_gate.get("long_uploaded") or 0),
        "long_required": int(quota_gate.get("long_required") or 1),
        "long_remaining": int(quota_gate.get("long_remaining") or 0),
        "state_path": str(resolve_state_path()),
        "music_root_primary": primary_music_root(cfg),
        "picked_music_path": str(music_path) if music_path else "",
        "picked_music_label": music_label,
        "music_warnings": mw,
        "config_path": str(_CONFIG_PATH),
        "audio_mode_default": str(cfg.get("audio_mode_default") or "music_first_low_ambient"),
        "ambient_volume_db": float(cfg.get("ambient_volume_db") or -24),
        "music_volume_relative": float(cfg.get("music_volume_relative") or 0.5),
    }


def prefer_source_date_metadata(cli: str | None = None) -> dict[str, Any]:
    """Expose prefer-date for manifests (picking lives in nyc_ferry_prefer_date_v1 / master scripts)."""
    try:
        from nyc_ferry_prefer_date_v1 import ENV_PREFER_SOURCE_DATE, resolve_prefer_dates  # noqa: WPS433

        dates = resolve_prefer_dates(cli)
        return {
            "prefer_source_dates": [d.isoformat() for d in dates],
            "prefer_source_date_env": ENV_PREFER_SOURCE_DATE,
        }
    except Exception:
        return {}


def schedule_metadata_for_job(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = get_today_plan()
    gate = evaluate_publish_schedule_gate(kind="1h", force=False)
    out = {
        "nyc_long_schedule_version": plan.get("version"),
        "nyc_long_schedule_date": plan.get("date"),
        "nyc_long_today_plan": plan,
        "nyc_long_schedule_gate": {
            "allowed": gate.get("allowed"),
            "reason": gate.get("reason"),
            "produced_today": gate.get("produced_today"),
        },
        "nyc_long_music_root_primary": gate.get("music_root_primary"),
        "nyc_long_audio_mode_default": gate.get("audio_mode_default"),
        **prefer_source_date_metadata(),
    }
    if extra:
        out.update(extra)
    return out


def min_master_duration_for_kind(kind: str) -> float:
    cfg = load_schedule_config()
    td = cfg.get("target_duration_sec") or {}
    if kind in ("3h", "extended"):
        target = float(td.get("extended") or 10800)
        return max(10500.0, target - 300.0)
    target = float(td.get("standard") or 3600)
    return max(3500.0, target - 100.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Print today's plan and gate status.")
    ap.add_argument("--force", action="store_true", help="Bypass daily production gate.")
    ap.add_argument("--schedule-kind", choices=("1h", "3h"), default="1h")
    ap.add_argument("--record", metavar="KIND", help="Record production of 1h or 3h (updates state).")
    args = ap.parse_args()

    if args.record:
        rec = record_production(args.record)
        print(json.dumps({"ok": True, "recorded": rec}, indent=2, ensure_ascii=False))
        return 0

    plan = get_today_plan()
    gate = evaluate_publish_schedule_gate(kind=args.schedule_kind, force=bool(args.force))
    music_path, music_label, _mw = pick_long_music_file()

    lines = [
        f"NYC_LONG_SCHEDULE_V1_READY=true",
        f"TODAY_PLAN={json.dumps(plan, ensure_ascii=False)}",
        f"MUSIC_ROOT_PRIMARY={primary_music_root()}",
        f"SCHEDULE_GATE_ALLOWED={str(gate.get('allowed')).lower()}",
        f"SCHEDULE_GATE_REASON={gate.get('reason')}",
    ]
    if music_path:
        lines.append(f"PICKED_MUSIC={music_path}")
        lines.append(f"PICKED_MUSIC_LABEL={music_label}")

    print("\n".join(lines))
    if args.dry_run:
        print(json.dumps({"plan": plan, "gate": gate}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
