"""
Runway Lip Sync: submit / poll / download **skeleton** for
``lipsync_input/<segment>/part_xx/`` folders.

The public Runway HTTP surface for Lip Sync may require a **paid** or **allowlisted**
account. If the endpoint is missing or the API returns 4xx, this module returns
structured ``status=manual_required`` (or error dicts) so the **manual** upload
flow in the presenter pipeline remains valid.

Run from repo root (``~/StateVerge``)::

    export PYTHONPATH="$PWD"
    python -m src.integrations.runway_lipsync --topic hidden-rules --submit
    python -m src.integrations.runway_lipsync --topic hidden-rules --poll
    python -m src.integrations.runway_lipsync --topic hidden-rules --download
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import argparse
import json
import os
import re
from typing import Any, Iterator, Optional

from . import runway_client

_ROOT = _REPO


def _load_env() -> None:
    runway_client.load_dotenv()
    p = _ROOT / ".env"
    if p.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(p, override=False)
        except Exception:
            pass


def _default_root() -> Path:
    return Path(os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge"))


def _lipsync_endpoint() -> str:
    return (os.environ.get("RUNWAY_LIPSYNC_ENDPOINT") or "").strip()


def _task_get_url(job_id: str) -> str:
    """
    Best-effort task status URL. Official Runway task paths may differ; set
    :envvar:`RUNWAY_API_BASE_URL` to include a path prefix if needed, or leave
    ``RUNWAY_API_VERSION`` empty to use ``.../tasks/{id}`` under the base.
    """
    base = (os.environ.get("RUNWAY_API_BASE_URL") or "").strip().rstrip("/")
    ver = (os.environ.get("RUNWAY_API_VERSION") or "").strip().strip("/")
    if not base:
        return ""
    if ver:
        return f"{base}/{ver}/tasks/{job_id}"
    return f"{base}/tasks/{job_id}"


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


def _extract_job_id(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    if isinstance(obj, str) and obj.strip():
        return obj.strip()
    if not isinstance(obj, dict):
        return None
    for k in (
        "id",
        "taskId",
        "task_id",
        "jobId",
        "job_id",
        "uuid",
    ):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    data = obj.get("data")
    if isinstance(data, dict):
        return _extract_job_id(data)
    task = obj.get("task")
    if isinstance(task, dict):
        return _extract_job_id(task)
    return None


def _pick_download_url(obj: Any) -> Optional[str]:
    if isinstance(obj, str) and (
        obj.startswith("http://") or obj.startswith("https://")
    ):
        return obj
    if not isinstance(obj, dict):
        return None
    for k in (
        "output_url",
        "url",
        "resultUrl",
        "downloadUrl",
        "assetUrl",
    ):
        v = obj.get(k)
        u = _pick_download_url(v)
        if u:
            return u
    for k in ("outputs", "output", "artifacts", "files", "assets", "data"):
        v = obj.get(k)
        if isinstance(v, list) and v:
            for item in v:
                u = _pick_download_url(item)
                if u:
                    return u
        if isinstance(v, dict):
            u = _pick_download_url(v)
            if u:
                return u
    return None


def _response_json(r: Any) -> Any:
    try:
        return r.json()
    except Exception:
        return {}


def submit_lipsync_job(
    input_dir: Path, *, announce_if_manual: bool = True
) -> dict[str, Any]:
    """
    If :envvar:`RUNWAY_LIPSYNC_ENDPOINT` is **empty**, print a one-line notice and
    return ``status=manual_required`` (no network).

    If set, ``POST`` multipart to that URL (relative paths are joined with
    :envvar:`RUNWAY_API_BASE_URL`) with fields ``base_video``, ``audio``,
    and form field ``metadata`` (JSON string of ``meta.json``).

    The real Runway field names may differ; adjust env / this function when
    the official spec is available.
    """
    input_dir = Path(input_dir)
    _load_env()
    endp = _lipsync_endpoint()
    if not endp:
        if announce_if_manual:
            print(
                "Runway Lip Sync API endpoint not configured; use manual upload",
                flush=True,
            )
        return {
            "status": "manual_required",
            "runway_error": "RUNWAY_LIPSYNC_ENDPOINT not set",
        }

    try:
        video, audio, meta = _read_inputs(input_dir)
    except FileNotFoundError as e:
        return {"status": "error", "runway_error": str(e)}

    # Multipart: generic field names; tune to official API when known.
    try:
        with open(video, "rb") as fv, open(audio, "rb") as fa:
            r = runway_client.request(
                "POST",
                endp,
                files={
                    "base_video": (video.name, fv, "video/mp4"),
                    "audio": (audio.name, fa, "audio/wav"),
                },
                data={"metadata": json.dumps(meta, ensure_ascii=False)},
                extra_headers={},
                timeout=600.0,
            )
    except runway_client.RunwayError as e:
        msg = str(e)[:2000]
        print(msg, file=sys.stderr, flush=True)
        if isinstance(e, runway_client.RunwayRequestError) and e.status_code in (
            400,
            401,
            403,
            404,
        ):
            print(
                "If Lip Sync is not enabled for this API key, use manual upload; "
                "Runway product/API surfaces change frequently.",
                file=sys.stderr,
                flush=True,
            )
        return {
            "status": "error",
            "runway_error": msg,
        }

    body: Any = _response_json(r)
    jid = _extract_job_id(body) or _extract_job_id(
        body.get("data") if isinstance(body, dict) else None
    )
    return {
        "status": "submitted",
        "runway_job_id": jid,
        "task_id": jid,
        "raw": body,
    }


def poll_lipsync_job(job_id: str) -> dict[str, Any]:
    _load_env()
    jid = (job_id or "").strip()
    if not jid:
        return {"status": "error", "error": "empty job_id"}
    if not (os.environ.get("RUNWAY_API_KEY") or "").strip():
        return {
            "status": "error",
            "error": "RUNWAY_API_KEY not set",
        }
    url = _task_get_url(jid)
    if not url:
        return {
            "status": "error",
            "error": "RUNWAY_API_BASE_URL not set; cannot build poll URL",
        }
    try:
        r = runway_client.request("GET", url, timeout=60.0)
    except runway_client.RunwayError as e:
        return {
            "status": "error",
            "error": str(e)[:2000],
        }
    body: Any = _response_json(r)
    out = {
        "status": "ok",
        "http_status": r.status_code,
        "raw": body,
    }
    if isinstance(body, dict):
        for k in ("status", "state", "taskStatus"):
            v = body.get(k)
            if isinstance(v, str) and v:
                out["task_state"] = v
                break
    return out


def download_lipsync_result(job_id: str, output_path: Path) -> dict[str, Any]:
    output_path = Path(output_path)
    st = poll_lipsync_job(job_id)
    if st.get("status") != "ok":
        return {**st, "ok": False}
    body = st.get("raw")
    u = _pick_download_url(body)
    if not u:
        return {
            "ok": False,
            "status": "error",
            "error": "No video URL in poll response; check raw payload or use manual download",
            "raw": body,
        }
    try:
        import requests
    except ImportError as e:
        return {"ok": False, "error": f"requests required: {e!s}"}

    k = (os.environ.get("RUNWAY_API_KEY") or "").strip()
    h = {"Authorization": f"Bearer {k}"} if k else {}
    try:
        r = requests.get(u, headers=h, timeout=600, stream=True)
    except Exception as e:
        return {"ok": False, "error": f"download failed: {e!s}"}
    if r.status_code != 200:
        return {
            "ok": False,
            "error": f"GET {u} -> HTTP {r.status_code}: {(r.text or '')[:500]}",
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as w:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                w.write(chunk)
    return {"ok": True, "path": str(output_path), "url": u}


# --- manifest helpers (per-part ``runway_lipsync`` map) ---


def _get_seg(manifest: dict[str, Any], segment_name: str) -> Optional[dict[str, Any]]:
    for s in manifest.get("segments") or []:
        if isinstance(s, dict) and s.get("name") == segment_name:
            return s
    return None


def _get_part_state(seg: dict[str, Any], part: str) -> dict[str, Any]:
    m = seg.setdefault("runway_lipsync", {})
    if not isinstance(m, dict):
        m = {}
        seg["runway_lipsync"] = m
    st = m.get(part)
    if not isinstance(st, dict):
        st = {}
        m[part] = st
    return st


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
    raise ValueError("expected_output_path missing in meta.json")


def _do_submit_all(root: Path, topic: str, segment: Optional[str]) -> int:
    from src.presenter_pipeline import fs_utils, manifest as mf

    root = Path(root)
    tpaths = fs_utils.topic_paths(root, topic)
    mp = tpaths["manifest"]
    m = mf.load_or_init(mp, topic, None)
    n_ok = 0
    n_manual = 0
    n_err = 0
    n_seen = 0
    parts = list(_iter_lipsync_input_dirs(root, topic, segment))
    manual_batched = not _lipsync_endpoint()
    if manual_batched and parts:
        print(
            "Runway Lip Sync API endpoint not configured; use manual upload",
            flush=True,
        )
    for input_dir, seg, part in parts:
        n_seen += 1
        segd = _get_seg(m, seg)
        if not segd:
            n_err += 1
            print(
                f"[{seg}/{part}] no manifest segment row; run presenter pipeline",
                file=sys.stderr,
            )
            continue
        st = _get_part_state(segd, part)
        res = submit_lipsync_job(
            input_dir, announce_if_manual=not manual_batched
        )
        status = str(res.get("status", "error"))
        st["runway_submit_status"] = status
        st["runway_error"] = res.get("runway_error")

        # Intended packaged output (used after manual or API download)
        try:
            _v, _a, meta = _read_inputs(input_dir)
            exp = _resolve_expected_output(root, topic, meta)
            st["runway_output_path"] = fs_utils.relposix(root, exp)
        except Exception as e:  # noqa: BLE001
            st["runway_output_path"] = st.get("runway_output_path")
            prev = (st.get("runway_error") or "").strip()
            extra = f" output_path_error={e!s}"
            st["runway_error"] = (prev + extra).strip() if prev else extra.strip()

        jid = res.get("runway_job_id") or res.get("task_id")
        if status == "submitted":
            if jid:
                st["runway_job_id"] = str(jid)
                n_ok += 1
            else:
                st["runway_job_id"] = None
                st["runway_error"] = (
                    "submitted but no job id in response; check raw payload or API docs"
                )
                n_err += 1
        elif status == "manual_required":
            st["runway_job_id"] = None
            n_manual += 1
        else:
            st["runway_job_id"] = None
            n_err += 1

    mf.save(mp, m)
    if n_seen == 0:
        print(
            f"no lipsync_input parts under {root / 'topics' / topic / 'presenter' / 'lipsync_input'}"
            f"{(' (segment ' + segment + ')') if segment else ''}",
            flush=True,
        )
    print(
        f"submit: parts={n_seen} ok_submitted={n_ok} manual_required={n_manual} errors={n_err}",
        flush=True,
    )
    return 0 if n_err == 0 else 1


def _cmd_poll(root: Path, topic: str, segment: Optional[str]) -> int:
    from src.presenter_pipeline import fs_utils, manifest as mf

    tpaths = fs_utils.topic_paths(Path(root), topic)
    mp = tpaths["manifest"]
    m = mf.load_or_init(mp, topic, None)
    n = 0
    for sego in m.get("segments") or []:
        if not isinstance(sego, dict):
            continue
        if segment and sego.get("name") != segment:
            continue
        rl = sego.get("runway_lipsync")
        if not isinstance(rl, dict):
            continue
        sname = str(sego.get("name", ""))
        for part, pst in sorted(rl.items()):
            if not isinstance(pst, dict):
                continue
            jid = (pst.get("runway_job_id") or "").strip()
            if not jid:
                continue
            p = poll_lipsync_job(jid)
            print(
                f"[{sname}/{part}] job={jid!r} poll={json.dumps(p, ensure_ascii=False)[:1500]}",
                flush=True,
            )
            n += 1
    if n == 0:
        print("no runway_job_id entries to poll; run --submit first", flush=True)
    return 0


def _cmd_download(root: Path, topic: str, segment: Optional[str]) -> int:
    from src.presenter_pipeline import fs_utils, manifest as mf

    root = Path(root)
    tpaths = fs_utils.topic_paths(root, topic)
    mp = tpaths["manifest"]
    m = mf.load_or_init(mp, topic, None)
    errs = 0
    for sego in m.get("segments") or []:
        if not isinstance(sego, dict):
            continue
        if segment and sego.get("name") != segment:
            continue
        rl = sego.get("runway_lipsync")
        if not isinstance(rl, dict):
            continue
        sname = str(sego.get("name", ""))
        for part, pst in sorted(rl.items()):
            if not isinstance(pst, dict):
                continue
            jid = (pst.get("runway_job_id") or "").strip()
            if not jid:
                continue
            pdir = (
                root
                / "topics"
                / topic
                / "presenter"
                / "lipsync_input"
                / sname
                / part
            )
            meta_path = pdir / "meta.json"
            if not meta_path.is_file():
                print(
                    f"[{sname}/{part}] missing meta; skip",
                    file=sys.stderr,
                )
                errs += 1
                continue
            with open(meta_path, encoding="utf-8") as f:
                meta: dict[str, Any] = json.load(f)
            out = _resolve_expected_output(root, topic, meta)
            d = download_lipsync_result(jid, out)
            rel = out
            try:
                rel_s = str(rel.relative_to(root))
            except Exception:
                rel_s = str(rel)
            if d.get("ok"):
                pst["runway_output_path"] = rel_s
                print(f"[{sname}/{part}] saved {rel_s}", flush=True)
            else:
                pst["runway_error"] = d.get("error", str(d))[:2000]
                print(
                    f"[{sname}/{part}] download failed: {d!r}",
                    file=sys.stderr,
                )
                errs += 1
    mf.save(mp, m)
    return 0 if errs == 0 else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Runway Lip Sync helper (skeleton).")
    ap.add_argument(
        "--topic",
        required=True,
        help="Topic slug, e.g. hidden-rules",
    )
    ap.add_argument(
        "--root",
        type=Path,
        default=None,
        help="StateVerge repo root (default: STATEVERGE_ROOT or ~/StateVerge).",
    )
    ap.add_argument(
        "--segment",
        default=None,
        help="Only this segment (folder name), e.g. intro",
    )
    ap.add_argument(
        "--submit",
        action="store_true",
        help="Submit each part_* to Runway (or manual_required).",
    )
    ap.add_argument(
        "--poll",
        action="store_true",
        help="GET poll status for stored runway_job_id values.",
    )
    ap.add_argument(
        "--download",
        action="store_true",
        help="Download completed result to expected_output_path from meta.json",
    )
    args = ap.parse_args(argv)
    n_act = int(args.submit) + int(args.poll) + int(args.download)
    if n_act != 1:
        ap.print_help()
        return 1

    _load_env()
    root = Path(args.root) if args.root is not None else _default_root()
    if not (root / "src").is_dir():
        print(
            f"--root does not look like StateVerge (no src/): {root}",
            file=sys.stderr,
        )
        return 1
    if args.submit:
        return _do_submit_all(root, args.topic, args.segment)
    if args.poll:
        return _cmd_poll(root, args.topic, args.segment)
    if args.download:
        return _cmd_download(root, args.topic, args.segment)
    return 1


if __name__ == "__main__":
    sys.exit(main())
