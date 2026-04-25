"""
ElevenLabs **Sync Lipsync**-style integration for
``lipsync_input/<segment>/part_xx/`` folders (``base_video.mp4``, ``audio.wav``).

If :envvar:`ELEVEN_LIPSYNC_ENDPOINT` is unset, the tool writes
``presenter/manual_upload_manifest.json`` and does **not** call the network.

When configured, it POSTs multipart (``video``, ``audio``, ``model``), then polls
``{ELEVEN_LIPSYNC_BASE_URL}/{ELEVEN_LIPSYNC_POLL_PREFIX or v1/.../jobs}/{job_id}`` —
adjust ``ELEVEN_LIPSYNC_POLL_PREFIX`` in the environment to match the official API.

Run (repo root)::

    export PYTHONPATH="$PWD"
    python -m src.integrations.eleven_lipsync --topic hidden-rules --prepare-manual
    python -m src.integrations.eleven_lipsync --topic hidden-rules --submit
    python -m src.integrations.eleven_lipsync --topic hidden-rules --poll
    python -m src.integrations.eleven_lipsync --topic hidden-rules --download
    python -m src.integrations.eleven_lipsync --topic hidden-rules --run-all
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlparse

# Repo root (…/StateVerge)
_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_ROOT = _REPO

# Default poll path segment (override with ELEVEN_LIPSYNC_POLL_PREFIX).
_DEFAULT_POLL_PREFIX = "v1/lip-sync/jobs"


def _load_env() -> None:
    p = _ROOT / ".env"
    if p.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(p, override=False)
        except Exception:
            pass


def _default_root() -> Path:
    return Path(os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge"))


def _api_key() -> str:
    k = (os.environ.get("ELEVEN_LIPSYNC_API_KEY") or "").strip()
    if not k:
        k = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    return k


def _base_url() -> str:
    return (os.environ.get("ELEVEN_LIPSYNC_BASE_URL") or "").strip().rstrip("/")


def _endpoint() -> str:
    return (os.environ.get("ELEVEN_LIPSYNC_ENDPOINT") or "").strip()


def _model() -> str:
    return (os.environ.get("ELEVEN_LIPSYNC_MODEL") or "sync-lipsync-2-pro").strip()


def _poll_prefix() -> str:
    return (
        os.environ.get("ELEVEN_LIPSYNC_POLL_PREFIX", _DEFAULT_POLL_PREFIX)
        or _DEFAULT_POLL_PREFIX
    ).strip().strip("/")


def _is_http(url: str) -> bool:
    try:
        return urlparse(url).scheme in ("http", "https")
    except Exception:
        return False


def _create_url() -> str:
    endp = _endpoint()
    if not endp:
        return ""
    if _is_http(endp):
        return endp
    base = _base_url()
    if not base:
        return ""
    return f"{base}/{endp.lstrip('/')}"


def _poll_url(job_id: str) -> str:
    base = _base_url()
    if not base:
        return ""
    pfx = _poll_prefix()
    return f"{base}/{pfx}/{job_id.lstrip('/')}"


def _iter_lipsync_input_dirs(
    root: Path, topic: str, segment: Optional[str] = None
) -> Iterator[tuple[Path, str, str]]:
    base = root / "topics" / topic / "presenter" / "lipsync_input"
    if not base.is_dir():
        return
    for seg_dir in sorted(base.iterdir()):
        if not seg_dir.is_dir() or seg_dir.name.startswith("."):
            continue
        if segment and seg_dir.name != segment:
            continue
        for part_dir in sorted(seg_dir.iterdir()):
            if not part_dir.is_dir() or part_dir.name.startswith("."):
                continue
            if not re.match(r"^part_\d+$", part_dir.name):
                continue
            yield part_dir, seg_dir.name, part_dir.name


def _read_inputs(input_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    input_dir = Path(input_dir)
    v = input_dir / "base_video.mp4"
    a = input_dir / "audio.wav"
    m = input_dir / "meta.json"
    if not v.is_file():
        raise FileNotFoundError(f"Missing base video: {v}")
    if not a.is_file():
        raise FileNotFoundError(f"Missing audio: {a}")
    if not m.is_file():
        raise FileNotFoundError(f"Missing meta: {m}")
    with m.open("r", encoding="utf-8") as f:
        meta: dict[str, Any] = json.load(f)
    return v, a, meta


def _response_json(r: Any) -> Any:
    try:
        return r.json()
    except Exception:
        return {}


def _extract_job_id(obj: Any) -> Optional[str]:
    if isinstance(obj, str) and obj.strip():
        return obj.strip()
    if not isinstance(obj, dict):
        return None
    for k in (
        "job_id",
        "id",
        "task_id",
        "jobId",
        "lipsync_job_id",
    ):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and v == v:
            return str(int(v))
    data = obj.get("data", obj.get("job", obj.get("result")))
    if isinstance(data, dict):
        return _extract_job_id(data)
    return None


def _pick_status(body: Any) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    for k in (
        "status",
        "state",
        "job_status",
    ):
        v = body.get(k)
        if isinstance(v, str) and v:
            return v.lower()
    return None


def _pick_download_url(body: Any) -> Optional[str]:
    if isinstance(body, str) and (body.startswith("http://") or body.startswith("https://")):
        return body
    if not isinstance(body, dict):
        return None
    for k in (
        "output_video_url",
        "result_url",
        "video_url",
        "url",
        "output_url",
        "download_url",
    ):
        v = body.get(k)
        u = _pick_download_url(v)
        if u:
            return u
    for k in (
        "output",
        "result",
        "video",
    ):
        v = body.get(k)
        u = _pick_download_url(v)
        if u:
            return u
    return None


def _request(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    data: Any = None,
    files: Any = None,
    timeout: float = 300.0,
) -> Any:
    try:
        import requests
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(f"Need requests: {e!s}") from e
    k = _api_key()
    headers: dict[str, str] = {}
    if k:
        headers["xi-api-key"] = k
    if files is None and json_body is not None:
        headers["Content-Type"] = "application/json"
    r = requests.request(
        method.upper(),
        url,
        headers=headers,
        json=json_body,
        data=data,
        files=files,
        timeout=timeout,
    )
    return r


def submit_lipsync_part(
    input_dir: Path, *, announce_if_manual: bool = True
) -> dict[str, Any]:
    _load_env()
    input_dir = Path(input_dir)
    if not _endpoint():
        if announce_if_manual:
            print(
                "Eleven Lipsync endpoint not configured; manual upload required",
                flush=True,
            )
        return {
            "eleven_lipsync_status": "manual_required",
            "eleven_lipsync_error": "ELEVEN_LIPSYNC_ENDPOINT not set",
        }
    if not _api_key():
        return {
            "eleven_lipsync_status": "error",
            "eleven_lipsync_error": "ELEVEN_LIPSYNC_API_KEY (or ELEVENLABS_API_KEY) not set",
        }

    url = _create_url()
    if not url:
        return {
            "eleven_lipsync_status": "error",
            "eleven_lipsync_error": "Could not build URL (set ELEVEN_LIPSYNC_BASE_URL and/or full ELEVEN_LIPSYNC_ENDPOINT)",
        }
    try:
        video, audio, meta = _read_inputs(input_dir)
    except OSError as e:
        return {"eleven_lipsync_status": "error", "eleven_lipsync_error": str(e)}

    model = _model()
    data: dict[str, str] = {
        "model": model,
        "metadata": json.dumps(meta, ensure_ascii=False),
    }
    try:
        with open(video, "rb") as fv, open(audio, "rb") as fa:
            r = _request(
                "POST",
                url,
                data=data,
                files={
                    "video": (video.name, fv, "video/mp4"),
                    "audio": (audio.name, fa, "audio/wav"),
                },
                timeout=600.0,
            )
    except Exception as e:
        return {
            "eleven_lipsync_status": "error",
            "eleven_lipsync_error": str(e)[:4000],
        }
    if not (200 <= r.status_code < 300):
        return {
            "eleven_lipsync_status": "error",
            "eleven_lipsync_error": f"HTTP {r.status_code} {(r.text or '')[:2000]}",
        }
    body: Any = _response_json(r)
    jid = _extract_job_id(body)
    out: dict[str, Any] = {
        "eleven_lipsync_status": "submitted" if jid else "error",
        "eleven_lipsync_error": None,
    }
    if jid:
        out["eleven_lipsync_job_id"] = str(jid)
    else:
        out["eleven_lipsync_error"] = "No job id in response; check API path and response shape"
    return out


def poll_lipsync_job(job_id: str) -> dict[str, Any]:
    _load_env()
    jid = (job_id or "").strip()
    if not jid:
        return {"ok": False, "error": "empty job_id"}
    if not _api_key():
        return {"ok": False, "error": "ELEVEN_LIPSYNC_API_KEY (or ELEVENLABS_API_KEY) not set"}
    u = _poll_url(jid)
    if not u:
        return {"ok": False, "error": "ELEVEN_LIPSYNC_BASE_URL not set; cannot build poll URL"}
    try:
        r = _request("GET", u, timeout=120.0)
    except Exception as e:
        return {"ok": False, "error": str(e)[:4000]}
    if r.status_code != 200:
        return {
            "ok": False,
            "error": f"HTTP {r.status_code} {(r.text or '')[:2000]}",
        }
    body: Any = _response_json(r)
    st = _pick_status(body)
    return {
        "ok": True,
        "raw": body,
        "state": st,
    }


def download_lipsync_result(job_id: str, output_path: Path) -> dict[str, Any]:
    p = poll_lipsync_job(job_id)
    if not p.get("ok"):
        return {**p, "saved": False}
    u = _pick_download_url(p.get("raw"))
    if not u:
        return {
            "saved": False,
            "error": "no download URL in poll body; set ELEVEN_LIPSYNC_POLL_PREFIX to match the API, or download manually",
            "raw": p.get("raw"),
        }
    try:
        import requests
    except ImportError as e:
        return {"saved": False, "error": f"need requests: {e!s}"}
    h = {"xi-api-key": _api_key()} if _api_key() else {}
    try:
        r = requests.get(u, headers=h, timeout=600, stream=True)
    except Exception as e:
        return {"saved": False, "error": str(e)[:2000]}
    if r.status_code != 200:
        try:
            r2 = requests.get(u, timeout=600, stream=True)
        except Exception as e2:
            return {
                "saved": False,
                "error": f"GET {u!s} -> {r.status_code}; retry failed: {e2!s}",
            }
        r = r2
        if r.status_code != 200:
            return {
                "saved": False,
                "error": f"GET {u!s} -> HTTP {r.status_code} {(r.text or '')[:500]}",
            }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as w:
        for chunk in r.iter_content(1024 * 256):
            if chunk:
                w.write(chunk)
    return {"saved": True, "path": str(output_path), "url": u}


def _resolve_expected_output(
    root: Path, topic: str, meta: dict[str, Any]
) -> Path:
    p = (meta or {}).get("expected_output_path")
    if isinstance(p, str) and p.strip():
        if Path(p).is_absolute():
            return Path(p)
        return (root / p).resolve()
    sn = (meta or {}).get("segment_name", "")
    pn = (meta or {}).get("part_name", "part_01")
    if isinstance(sn, str) and sn and isinstance(pn, str) and pn:
        return (
            root
            / "topics"
            / topic
            / "presenter"
            / "lipsync_output"
            / sn
            / f"{pn}.mp4"
        )
    raise ValueError("expected_output_path or segment_name/part_name missing in meta.json")


# --- manifest (per part under ``eleven_lipsync``) ---


def _get_seg(m: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
    for s in m.get("segments") or []:
        if isinstance(s, dict) and s.get("name") == name:
            return s
    return None


def _get_eleven_part(seg: dict[str, Any], part: str) -> dict[str, Any]:
    m = seg.setdefault("eleven_lipsync", {})
    if not isinstance(m, dict):
        m = {}
        seg["eleven_lipsync"] = m
    st = m.get(part)
    if not isinstance(st, dict):
        st = {}
        m[part] = st
    return st


def _write_manual_manifest(
    root: Path, topic: str, segment: Optional[str]
) -> list[dict[str, Any]]:
    from src.presenter_pipeline import fs_utils

    out: list[dict[str, Any]] = []
    for input_dir, seg, part in _iter_lipsync_input_dirs(
        root, topic, segment
    ):
        try:
            v, a, meta = _read_inputs(input_dir)
        except OSError as e:
            out.append(
                {
                    "segment": seg,
                    "part": part,
                    "error": str(e),
                }
            )
            continue
        try:
            exp = _resolve_expected_output(root, topic, meta)
        except Exception as e:  # noqa: BLE001
            out.append(
                {
                    "segment": seg,
                    "part": part,
                    "error": f"output path: {e!s}",
                }
            )
            continue
        out.append(
            {
                "segment": seg,
                "part": part,
                "video_path": fs_utils.relposix(root, v),
                "audio_path": fs_utils.relposix(root, a),
                "expected_output_path": fs_utils.relposix(root, exp),
            }
        )
    pres = (
        root
        / "topics"
        / topic
        / "presenter"
        / "manual_upload_manifest.json"
    )
    pres.parent.mkdir(parents=True, exist_ok=True)
    with pres.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "topic": topic,
                "generator": "eleven_lipsync",
                "items": out,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")
    return out


def _terminal_state(state: Optional[str]) -> str:
    if not state:
        return "running"
    s = state.lower()
    if s in ("complete", "completed", "succeeded", "success", "done", "ready"):
        return "done"
    if s in ("failed", "error", "canceled", "cancelled"):
        return "failed"
    return "running"


def _prepare_manual(
    root: Path, topic: str, segment: Optional[str], print_notice: bool
) -> int:
    _load_env()
    if not _endpoint() and print_notice:
        print(
            "Eleven Lipsync endpoint not configured; manual upload required",
            flush=True,
        )
    items = _write_manual_manifest(root, topic, segment)
    n = len(items)
    rel = (
        Path("topics")
        / topic
        / "presenter"
        / "manual_upload_manifest.json"
    )
    print(f"Wrote {n} part(s) to {rel.as_posix()}", flush=True)
    return 0


def _submit_all(root: Path, topic: str, segment: Optional[str]) -> int:
    from src.presenter_pipeline import fs_utils, manifest as mf

    _load_env()
    root = Path(root)
    mp = fs_utils.topic_paths(root, topic)["manifest"]
    m = mf.load_or_init(mp, topic, None)
    parts = list(_iter_lipsync_input_dirs(root, topic, segment))
    if not _endpoint():
        if parts:
            print(
                "Eleven Lipsync endpoint not configured; manual upload required",
                flush=True,
            )
        _write_manual_manifest(root, topic, segment)
        n_ok = 0
        n_manual = 0
        n_err = 0
        for input_dir, seg, part in parts:
            segd = _get_seg(m, seg)
            if not segd:
                n_err += 1
                print(
                    f"[{seg}/{part}] no manifest row for segment",
                    file=sys.stderr,
                )
                continue
            st = _get_eleven_part(segd, part)
            st["eleven_lipsync_status"] = "manual_required"
            st["eleven_lipsync_error"] = "ELEVEN_LIPSYNC_ENDPOINT not set"
            st["eleven_lipsync_job_id"] = None
            try:
                v, a, meta = _read_inputs(input_dir)
                exp = _resolve_expected_output(root, topic, meta)
                st["output_path"] = fs_utils.relposix(root, exp)
            except Exception as e:  # noqa: BLE001
                st["output_path"] = st.get("output_path")
                st["eleven_lipsync_error"] = str(e)[:2000]
            n_manual += 1
        mf.save(mp, m)
        print(
            f"submit: parts={len(parts)} manual={n_manual} (no API endpoint)",
            flush=True,
        )
        return 0
    n_ok = 0
    n_err = 0
    for input_dir, seg, part in parts:
        segd = _get_seg(m, seg)
        if not segd:
            n_err += 1
            print(f"[{seg}/{part}] no segment in manifest; skip", file=sys.stderr)
            continue
        st = _get_eleven_part(segd, part)
        res = submit_lipsync_part(input_dir, announce_if_manual=False)
        for k, v in res.items():
            if k == "raw":
                continue
            st[k] = v
        try:
            v, a, meta = _read_inputs(input_dir)
            exp = _resolve_expected_output(root, topic, meta)
            st["output_path"] = fs_utils.relposix(root, exp)
        except Exception as e:  # noqa: BLE001
            st["output_path"] = st.get("output_path")
            st["eleven_lipsync_error"] = str(e)[:2000]
        status = (res.get("eleven_lipsync_status") or "error").lower()
        if status == "submitted" and res.get("eleven_lipsync_job_id"):
            n_ok += 1
        else:
            n_err += 1
    mf.save(mp, m)
    if not parts:
        print("no part_* under lipsync_input; nothing to do", flush=True)
    print(f"submit: parts={len(parts)} ok={n_ok} err={n_err}", flush=True)
    return 0 if n_err == 0 else 1


def _cmd_poll(root: Path, topic: str, segment: Optional[str]) -> int:
    from src.presenter_pipeline import fs_utils, manifest as mf

    root = Path(root)
    mp = fs_utils.topic_paths(root, topic)["manifest"]
    m = mf.load_or_init(mp, topic, None)
    n = 0
    for sego in m.get("segments") or []:
        if not isinstance(sego, dict):
            continue
        if segment and sego.get("name") != segment:
            continue
        el = sego.get("eleven_lipsync")
        if not isinstance(el, dict):
            continue
        sname = str(sego.get("name", ""))
        for ptk, st in sorted(el.items()):
            if not isinstance(st, dict):
                continue
            jid = (st.get("eleven_lipsync_job_id") or "").strip()
            if not jid:
                continue
            pr = poll_lipsync_job(jid)
            st["eleven_lipsync_last_poll"] = pr
            n += 1
            print(
                f"[{sname}/{ptk}] {json.dumps(pr, ensure_ascii=False)[:2000]}",
                flush=True,
            )
    mf.save(mp, m)
    if n == 0:
        print("no eleven_lipsync_job_id in manifest to poll", flush=True)
    return 0


def _cmd_download(root: Path, topic: str, segment: Optional[str]) -> int:
    from src.presenter_pipeline import fs_utils, manifest as mf

    root = Path(root)
    mp = fs_utils.topic_paths(root, topic)["manifest"]
    m = mf.load_or_init(mp, topic, None)
    errs = 0
    for sego in m.get("segments") or []:
        if not isinstance(sego, dict):
            continue
        if segment and sego.get("name") != segment:
            continue
        el = sego.get("eleven_lipsync")
        if not isinstance(el, dict):
            continue
        sname = str(sego.get("name", ""))
        for ptk, st in sorted(el.items()):
            if not isinstance(st, dict):
                continue
            jid = (st.get("eleven_lipsync_job_id") or "").strip()
            if not jid:
                continue
            pdir = (
                root
                / "topics"
                / topic
                / "presenter"
                / "lipsync_input"
                / sname
                / ptk
            )
            mp_ = pdir / "meta.json"
            if not mp_.is_file():
                print(f"[{sname}/{ptk}] no meta; skip", file=sys.stderr)
                errs += 1
                continue
            with open(mp_, encoding="utf-8") as f:
                meta: dict[str, Any] = json.load(f)
            out = _resolve_expected_output(root, topic, meta)
            d = download_lipsync_result(jid, out)
            rpath = fs_utils.relposix(root, out)
            if d.get("saved"):
                st["output_path"] = rpath
                st["eleven_lipsync_status"] = "downloaded"
                print(f"[{sname}/{ptk}] {rpath}", flush=True)
            else:
                st["eleven_lipsync_error"] = str(
                    d.get("error", d) if isinstance(d, dict) else d
                )[:2000]
                errs += 1
                print(f"[{sname}/{ptk}] {d!r}", file=sys.stderr, flush=True)
    mf.save(mp, m)
    return 0 if errs == 0 else 1


def _run_all(root: Path, topic: str, segment: Optional[str]) -> int:
    from src.presenter_pipeline import fs_utils, manifest as mf

    _load_env()
    root = Path(root)
    mp = fs_utils.topic_paths(root, topic)["manifest"]
    m = mf.load_or_init(mp, topic, None)
    parts = list(_iter_lipsync_input_dirs(root, topic, segment))
    if not _endpoint():
        if parts:
            print(
                "Eleven Lipsync endpoint not configured; manual upload required",
                flush=True,
            )
        _write_manual_manifest(root, topic, segment)
        for _input_dir, seg, part in parts:
            segd = _get_seg(m, seg)
            if not segd:
                continue
            st = _get_eleven_part(segd, part)
            st["eleven_lipsync_status"] = "manual_required"
            st["eleven_lipsync_error"] = "ELEVEN_LIPSYNC_ENDPOINT not set"
            st["eleven_lipsync_job_id"] = None
        mf.save(mp, m)
        return 0
    n_err = 0
    for input_dir, seg, part in parts:
        segd = _get_seg(m, seg)
        if not segd:
            n_err += 1
            continue
        st = _get_eleven_part(segd, part)
        try:
            r = submit_lipsync_part(input_dir, announce_if_manual=False)
            for k, v in r.items():
                if k == "raw":
                    continue
                st[k] = v
        except Exception as e:  # noqa: BLE001
            st["eleven_lipsync_status"] = "error"
            st["eleven_lipsync_error"] = str(e)[:2000]
            n_err += 1
            mf.save(mp, m)
            continue
        jid = (r.get("eleven_lipsync_job_id") or "").strip()
        if not jid:
            n_err += 1
            mf.save(mp, m)
            continue
        max_wait = int(os.environ.get("ELEVEN_LIPSYNC_MAX_WAIT_S", "3600") or 3600)
        deadline = time.monotonic() + max(60, max_wait)
        st["eleven_lipsync_status"] = "processing"
        mf.save(mp, m)
        poll_interval = float(os.environ.get("ELEVEN_LIPSYNC_POLL_INTERVAL", "4") or 4)
        ready = False
        poll_failed = False
        while time.monotonic() < deadline:
            p = poll_lipsync_job(jid)
            if not p.get("ok"):
                st["eleven_lipsync_error"] = str(p.get("error", p))[:2000]
                st["eleven_lipsync_status"] = "error"
                poll_failed = True
                n_err += 1
                break
            raw: Any = p.get("raw") if isinstance(p, dict) else {}
            s = _pick_status(raw)
            term = _terminal_state(s)
            if term == "failed":
                st["eleven_lipsync_error"] = (
                    json.dumps(raw, ensure_ascii=False) if raw else "failed"
                )[:2000]
                st["eleven_lipsync_status"] = "error"
                poll_failed = True
                n_err += 1
                break
            if term == "done" or _pick_download_url(raw):
                ready = True
                break
            time.sleep(poll_interval)
        else:
            if not poll_failed and not ready:
                st["eleven_lipsync_status"] = "timeout"
                st["eleven_lipsync_error"] = f"not finished within {max_wait}s"
                n_err += 1
        if poll_failed or st.get("eleven_lipsync_status") == "timeout":
            mf.save(mp, m)
            continue
        if not ready:
            st["eleven_lipsync_status"] = "error"
            st["eleven_lipsync_error"] = "poll ended without ready state"
            n_err += 1
            mf.save(mp, m)
            continue
        v, a, meta = _read_inputs(input_dir)
        out = _resolve_expected_output(root, topic, meta)
        d = download_lipsync_result(jid, out)
        if d.get("saved"):
            st["output_path"] = fs_utils.relposix(root, out)
            st["eleven_lipsync_status"] = "downloaded"
        else:
            st["eleven_lipsync_error"] = str(
                d.get("error", d) if isinstance(d, dict) else d
            )[:2000]
            n_err += 1
        mf.save(mp, m)
    print(f"run-all finished; errors (parts)={n_err}", flush=True)
    return 0 if n_err == 0 else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="ElevenLabs Sync Lipsync (multipart) for lipsync_input parts.",
    )
    ap.add_argument("--topic", required=True)
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--segment", default=None)
    ap.add_argument(
        "--prepare-manual",
        action="store_true",
        help="Write manual_upload_manifest.json; print notice if endpoint not set",
    )
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--poll", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--run-all", dest="run_all", action="store_true")
    args = ap.parse_args(argv)
    n = (
        int(args.prepare_manual)
        + int(args.submit)
        + int(args.poll)
        + int(args.download)
        + int(args.run_all)
    )
    if n != 1:
        ap.print_help()
        return 1
    _load_env()
    root = Path(args.root) if args.root is not None else _default_root()
    if not (root / "src").is_dir():
        print(
            f"--root does not look like StateVerge: {root}",
            file=sys.stderr,
        )
        return 1
    seg: Optional[str] = args.segment
    t = str(args.topic)
    if args.prepare_manual:
        return _prepare_manual(root, t, seg, print_notice=True)
    if args.submit:
        return _submit_all(root, t, seg)
    if args.poll:
        return _cmd_poll(root, t, seg)
    if args.download:
        return _cmd_download(root, t, seg)
    if args.run_all:
        return _run_all(root, t, seg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
