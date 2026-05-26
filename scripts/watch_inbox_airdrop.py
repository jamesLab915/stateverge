#!/usr/bin/env python3
"""
监控 00_INBOX/airdrop：文件稳定后触发 stateverge_asset_library_rebuild。

仅用文件名/路径跳过 macOS 副文件与「私人别碰」，不按扩展名过滤媒体。
"""

from __future__ import annotations

import datetime
import json
import logging
import signal
import subprocess
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from stateverge_paths import SSD_ROOT as VOL  # noqa: E402
PRIVATE = "私人别碰"
INBOX_AIRDROP = VOL / "00_INBOX" / "airdrop"
AUTOMATION_LOGS = VOL / "07_AUTOMATION" / "logs"
LOG_ON_VOLUME = AUTOMATION_LOGS / "watch_inbox_airdrop.log"
# 用户指定：主日志在 ~/Library/Logs/StateVerge/
LOG_PRIMARY = Path.home() / "Library/Logs/StateVerge/watch_inbox_airdrop.log"

_audit_log: Path | None = None
_state_path: Path | None = None

REPO_SCRIPTS = Path(__file__).resolve().parent
REBUILD_SCRIPT = REPO_SCRIPTS / "stateverge_asset_library_rebuild.py"

SCAN_INTERVAL_SEC = 30

TEMP_NAME_SUFFIXES = (
    ".tmp",
    ".temp",
    ".part",
    ".download",
    ".crdownload",
    "~",
)

_stop = False
_scan_eperm_announced = False
_sidecar_announced: set[str] = set()
_private_announced: set[str] = set()


def _on_term(*_: object) -> None:
    global _stop
    _stop = True


def path_private(p: Path) -> bool:
    return PRIVATE in p.parts or PRIVATE in str(p)


def is_macos_sidecar_or_junk(p: Path) -> bool:
    n = p.name
    if n == ".DS_Store":
        return True
    if n.startswith("._"):
        return True
    if n.startswith(".Spotlight"):
        return True
    if "Trashes" in p.parts:
        return True
    nl = n.lower()
    for suf in TEMP_NAME_SUFFIXES:
        if nl.endswith(suf):
            return True
    return False


def ensure_watch_paths() -> Path:
    """主日志：~/Library/Logs/StateVerge/；若可写则镜像到卷上路径。"""
    global _audit_log, _state_path
    if _audit_log is not None:
        return _audit_log
    for candidate in (LOG_PRIMARY, LOG_ON_VOLUME):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with open(candidate, "a", encoding="utf-8"):
                pass
            _audit_log = candidate
            _state_path = candidate.parent / "watch_airdrop_state.json"
            return _audit_log
        except OSError:
            continue
    raise SystemExit(
        f"watch_inbox_airdrop: cannot write log (tried {LOG_PRIMARY} and {LOG_ON_VOLUME})"
    )


def audit(line: str) -> None:
    path = _audit_log if _audit_log is not None else ensure_watch_paths()
    with open(path, "a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")
        f.flush()
    if path.resolve() != LOG_ON_VOLUME.resolve():
        try:
            LOG_ON_VOLUME.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_ON_VOLUME, "a", encoding="utf-8") as f:
                f.write(line.rstrip() + "\n")
        except OSError:
            pass


def setup_logging() -> None:
    path = _audit_log if _audit_log is not None else ensure_watch_paths()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def load_state() -> dict:
    sp = _state_path if _state_path is not None else ensure_watch_paths()
    assert _state_path is not None
    if not sp.is_file():
        return {"processed": []}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"processed": []}


def save_state(processed: list[str]) -> None:
    sp = _state_path if _state_path is not None else ensure_watch_paths()
    assert _state_path is not None
    sp.parent.mkdir(parents=True, exist_ok=True)
    tail = processed[-8000:]
    sp.write_text(
        json.dumps({"processed": tail}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def file_fingerprint(p: Path) -> str | None:
    try:
        st = p.stat()
        return f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return None


def scan_candidates() -> list[Path]:
    global _scan_eperm_announced, _sidecar_announced, _private_announced
    if not INBOX_AIRDROP.is_dir():
        return []
    out: list[Path] = []
    try:
        for p in INBOX_AIRDROP.iterdir():
            if not p.is_file():
                continue
            key = str(p.resolve())
            if path_private(p):
                if key not in _private_announced:
                    audit(f"SKIP_PROTECTED_PRIVATE {p}")
                    _private_announced.add(key)
                continue
            if is_macos_sidecar_or_junk(p):
                if key not in _sidecar_announced:
                    audit(f"SKIP_MACOS_SIDECAR {p}")
                    _sidecar_announced.add(key)
                continue
            out.append(p)
    except OSError as e:
        if getattr(e, "errno", None) == 1 and not _scan_eperm_announced:
            _scan_eperm_announced = True
            audit(
                "INBOX_SCAN_EPERM grant Full Disk Access (and Removable Volumes if shown) "
                "to /usr/bin/python3 so LaunchAgent can read the SSD inbox"
            )
        logging.warning("scan failed: %s", e)
    return out


def run_rebuild() -> bool:
    if not REBUILD_SCRIPT.is_file():
        logging.error("missing script: %s", REBUILD_SCRIPT)
        return False
    cmd = [sys.executable, str(REBUILD_SCRIPT), "--airdrop-only"]
    audit("RUN_REBUILD")
    logging.info("RUN asset_library_rebuild: %s", " ".join(cmd))
    try:
        r = subprocess.run(
            cmd,
            cwd=str(REPO_SCRIPTS.parent),
            capture_output=True,
            text=True,
            timeout=7200,
        )
    except subprocess.TimeoutExpired:
        logging.error("asset_library_rebuild: timeout")
        return False
    except OSError as e:
        logging.error("asset_library_rebuild: %s", e)
        return False
    for line in (r.stdout or "").splitlines():
        s = line.strip()
        if s.startswith("INGEST_VIDEO ") or s.startswith("INGEST_PHOTO "):
            audit(s)
        elif s.startswith("ERROR_METADATA"):
            audit(s)
    if r.stdout:
        logging.info("[rebuild stdout]\n%s", r.stdout[-4000:])
    if r.stderr:
        logging.warning("[rebuild stderr]\n%s", r.stderr[-4000:])
    if r.returncode != 0:
        logging.error("asset_library_rebuild exit=%s", r.returncode)
        return False
    logging.info("asset_library_rebuild OK")
    return True


def main() -> int:
    global _stop
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    ensure_watch_paths()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    audit(f"WATCHER_STARTED {ts}")
    if _audit_log is not None and _audit_log.resolve() != LOG_PRIMARY.resolve():
        audit(f"AUDIT_LOG_PATH {_audit_log}")

    setup_logging()
    INBOX_AIRDROP.mkdir(parents=True, exist_ok=True)

    if PRIVATE in str(INBOX_AIRDROP.resolve()):
        logging.error("inbox path invalid (private segment)")
        return 1

    logging.info(
        "watch_inbox_airdrop started; inbox=%s interval=%ss",
        INBOX_AIRDROP,
        SCAN_INTERVAL_SEC,
    )

    state = load_state()
    processed: set[str] = set(state.get("processed") or [])

    last_size: dict[str, int] = {}
    same_streak: dict[str, int] = {}
    stable_announced: set[str] = set()

    while not _stop:
        if not VOL.is_dir():
            logging.warning("volume not mounted: %s", VOL)
            time.sleep(SCAN_INTERVAL_SEC)
            continue

        candidates = scan_candidates()
        audit(f"WATCH_SCAN files={len(candidates)}")
        ready: list[Path] = []
        seen_keys: set[str] = set()

        for p in candidates:
            key = str(p.resolve())
            seen_keys.add(key)
            fp = file_fingerprint(p)
            if fp is None:
                continue
            if fp in processed:
                last_size.pop(key, None)
                same_streak.pop(key, None)
                stable_announced.discard(key)
                continue
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if sz <= 0:
                continue

            prev = last_size.get(key)
            if prev == sz:
                same_streak[key] = same_streak.get(key, 1) + 1
            else:
                last_size[key] = sz
                same_streak[key] = 1

            if same_streak[key] >= 2:
                ready.append(p)
                if key not in stable_announced:
                    audit(f"WATCH_STABLE {p}")
                    stable_announced.add(key)

        for k in list(last_size.keys()):
            if k not in seen_keys:
                last_size.pop(k, None)
                same_streak.pop(k, None)
                stable_announced.discard(k)
                _sidecar_announced.discard(k)
                _private_announced.discard(k)

        if ready:
            ready = list(dict.fromkeys(ready))
            logging.info("STABLE_FILES: %s", [x.name for x in ready])
            ok_rebuild = run_rebuild()
            if ok_rebuild:
                for p in ready:
                    fp = file_fingerprint(p)
                    if fp:
                        processed.add(fp)
                save_state(sorted(processed))
                logging.info("marked processed: %d fingerprints", len(ready))
            else:
                logging.warning("rebuild failed; not marking processed")

        time.sleep(SCAN_INTERVAL_SEC)

    logging.info("watch_inbox_airdrop stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
