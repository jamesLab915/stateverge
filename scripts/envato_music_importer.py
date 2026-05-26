#!/usr/bin/env python3
"""Envato music importer: strict split between nyc_long and shorts channels (no cross-fallback)."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_SCRIPT_DIR = Path(__file__).resolve().parent
_SV_SRC = Path.home() / "StateVerge" / "src"
for _p in (_SCRIPT_DIR, _SV_SRC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

AUDIO_EXTS = {".mp3", ".wav", ".aiff", ".flac", ".m4a"}
ARCHIVE_EXTS = {".zip"}
LICENSE_NAME_HINTS = ("license", "licence", "eula", "envato", "audiojungle", "project", "certificate")
PREVIEW_SUBSTRINGS = (
    "preview",
    "demo",
    "watermark",
    "audiojungle-preview",
    "audiojungle_preview",
    "_prv",
    "-prv",
)

NYC_LONG_CATEGORIES = frozenset(
    {"ambient", "calm_piano", "cinematic", "dark_documentary", "night_drive", "archive"}
)
SHORTS_CATEGORIES = frozenset({"upbeat", "cinematic", "urban", "dramatic", "luxury", "cyberpunk", "archive"})


def trusted_envato_inbox_root() -> Path:
    return _cache() / "inbox" / "envato"


def is_trusted_envato_inbox_source(p: Path) -> bool:
    """Claim Clear Ready downloads under SV_CACHE/inbox/envato/ are trusted (no in-zip license required)."""
    try:
        base = trusted_envato_inbox_root().resolve()
        rp = p.expanduser().resolve()
        base_s = base.as_posix().rstrip("/") + "/"
        rp_s = rp.as_posix().rstrip("/")
        if rp_s == base.as_posix().rstrip("/"):
            return True
        return rp_s.startswith(base_s)
    except OSError:
        return False


def _ffmpeg_audio_to_mp3(src: Path, dst: Path, *, timeout_sec: float = 7200.0) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(dst),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
    if r.returncode != 0 or not dst.is_file():
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"ffmpeg_mp3_failed rc={r.returncode} err={err[:500]}")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _xfer() -> Path:
    try:
        from utils.storage_paths import get_sv_transfer  # type: ignore

        return get_sv_transfer(verbose=False)
    except Exception:
        return Path("/Volumes/SV_TRANSFER")


def _cache() -> Path:
    try:
        from utils.storage_paths import get_sv_cache  # type: ignore

        return get_sv_cache(verbose=False)
    except Exception:
        return Path("/Volumes/SV_CACHE")


def channel_paths(channel: str) -> dict[str, Path]:
    if channel not in ("nyc_long", "shorts"):
        raise ValueError(channel)
    inbox = _cache() / "inbox" / "envato" / channel
    music = _xfer() / "04_AUDIO" / "music" / channel
    return {
        "channel": channel,
        "inbox": inbox,
        "music_root": music,
        "metadata_dir": music / "metadata",
        "licenses_dir": music / "licenses",
        "archive_dir": music / "archive",
        "rejected_dir": music / "rejected",
        "logs_dir": music / "logs",
        "index_path": music / "metadata" / "music_index.json",
    }


def ensure_tree(channel: str) -> dict[str, Path]:
    cp = channel_paths(channel)
    cats = (
        ("ambient", "calm_piano", "cinematic", "dark_documentary", "night_drive")
        if channel == "nyc_long"
        else ("upbeat", "cinematic", "urban", "dramatic", "luxury", "cyberpunk")
    )
    for d in (*cats, "licenses", "metadata", "archive", "rejected", "logs"):
        (cp["music_root"] / d).mkdir(parents=True, exist_ok=True)
    cp["metadata_dir"].mkdir(parents=True, exist_ok=True)
    return cp


def is_preview_path(p: Path) -> bool:
    s = p.as_posix().lower()
    n = p.name.lower()
    if any(x in n for x in PREVIEW_SUBSTRINGS):
        return True
    if "/preview/" in s or "\\preview\\" in s:
        return True
    return False


def is_stem_path(p: Path) -> bool:
    parts = [x.lower() for x in p.parts]
    if any(x in ("stems", "stem", "multitrack", "multitracks") for x in parts):
        return True
    n = p.name.lower()
    if "stem" in n and p.suffix.lower() in AUDIO_EXTS:
        return True
    if re.search(r"[_-]stem[_-]", n):
        return True
    return False


def looks_like_license_file(p: Path) -> bool:
    if not p.is_file():
        return False
    n = p.name.lower()
    ext = p.suffix.lower()
    if ext not in {".pdf", ".txt", ".rtf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}:
        return False
    return any(h in n for h in LICENSE_NAME_HINTS)


def collect_licenses(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        for fn in filenames:
            p = dp / fn
            if p.name.startswith("._"):
                continue
            if looks_like_license_file(p):
                out.append(p)
    return out


def collect_audio_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        for fn in filenames:
            p = dp / fn
            if p.name.startswith("._"):
                continue
            if p.suffix.lower() not in AUDIO_EXTS:
                continue
            out.append(p)
    return out


def classify_nyc_long(blob: str) -> str:
    t = blob.lower()
    pairs: list[tuple[str, tuple[str, ...]]] = [
        ("dark_documentary", ("documentary", "drama", "investigative", "dark")),
        ("calm_piano", ("piano", "emotional", "minimal")),
        ("ambient", ("ambient", "chill", "atmosphere")),
        ("cinematic", ("cinematic", "film")),
        ("night_drive", ("night", "drive", "cyber")),
    ]
    for cat, keys in pairs:
        if any(k in t for k in keys):
            return cat
    return "archive"


def classify_shorts(blob: str) -> str:
    t = blob.lower()
    pairs: list[tuple[str, tuple[str, ...]]] = [
        ("upbeat", ("upbeat", "energetic", "fun")),
        ("cinematic", ("cinematic", "film")),
        ("urban", ("urban", "city", "street")),
        ("dramatic", ("dramatic", "drama")),
        ("luxury", ("luxury", "fashion", "lounge")),
        ("cyberpunk", ("cyberpunk", "neon")),
    ]
    for cat, keys in pairs:
        if any(k in t for k in keys):
            return cat
    if "cyber" in t or " night" in t or t.startswith("night"):
        return "cyberpunk"
    return "archive"


def classify_for_channel(channel: str, blob: str) -> str:
    if channel == "nyc_long":
        c = classify_nyc_long(blob)
        return c if c in NYC_LONG_CATEGORIES else "archive"
    c = classify_shorts(blob)
    return c if c in SHORTS_CATEGORIES else "archive"


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", name)[:180] or "item"


def _next_index(channel: str, music_root: Path) -> int:
    pat = re.compile(rf"^sv_{re.escape(channel)}_[a-z0-9_]+_(\d+)\.mp3$", re.I)
    best = 0
    cats = (
        ("ambient", "calm_piano", "cinematic", "dark_documentary", "night_drive", "archive")
        if channel == "nyc_long"
        else ("upbeat", "cinematic", "urban", "dramatic", "luxury", "cyberpunk", "archive")
    )
    for cat in cats:
        d = music_root / cat
        if not d.is_dir():
            continue
        try:
            for p in d.iterdir():
                if not p.is_file():
                    continue
                m = pat.match(p.name)
                if m:
                    best = max(best, int(m.group(1)))
        except OSError:
            continue
    return best + 1


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suf = dest.stem, dest.suffix
    for i in range(2, 10_000):
        cand = dest.with_name(f"{stem}__{i}{suf}")
        if not cand.exists():
            return cand
    return dest.with_name(f"{stem}__{uuid.uuid4().hex[:8]}{suf}")


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_index(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"tracks": [], "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict) and isinstance(data.get("tracks"), list):
            return data
    except Exception:
        pass
    return {"tracks": [], "updated_at": None}


def pick_main_audio(audios: list[Path]) -> Path | None:
    mains = [
        p
        for p in audios
        if not is_preview_path(p) and not is_stem_path(p)
    ]
    if not mains:
        return None

    def score(p: Path) -> tuple[int, int, str]:
        n = p.name.lower()
        tag = 0
        for kw, w in (
            ("full", 4),
            ("main", 3),
            ("master", 3),
            ("mix", 2),
            ("full mix", 5),
        ):
            if kw in n:
                tag = max(tag, w)
        try:
            sz = p.stat().st_size
        except OSError:
            sz = 0
        return (tag, sz, p.name)

    mains.sort(key=score, reverse=True)
    return mains[0]


def stem_audios(audios: list[Path], main: Path | None) -> list[Path]:
    out: list[Path] = []
    for p in audios:
        if main and p.resolve() == main.resolve():
            continue
        if is_preview_path(p):
            continue
        if is_stem_path(p):
            out.append(p)
    return out


def extract_zip(zip_path: Path, dest: Path) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for m in zf.infolist():
            if m.is_dir():
                continue
            fn = m.filename.replace("\\", "/")
            if fn.startswith("/") or ".." in fn.split("/"):
                continue
            parts = Path(fn).parts
            if any(x.startswith("__MACOSX") for x in parts):
                continue
            out_path = (dest / fn).resolve()
            if dest not in out_path.parents and out_path != dest:
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m, "r") as src, open(out_path, "wb") as out_f:
                shutil.copyfileobj(src, out_f)


def reject_move(src: Path, rejected_dir: Path, reason: str) -> Path:
    rejected_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = _safe_name(src.name)
    dest = rejected_dir / f"{ts}__{base}"
    dest = _unique_dest(dest)
    shutil.move(str(src), str(dest))
    try:
        (dest.parent / f"{dest.name}.reason.txt").write_text(reason, encoding="utf-8")
    except OSError:
        pass
    return dest


@dataclass
class ImportStats:
    imported: int = 0
    rejected: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)


def _process_extracted_tree(
    *,
    channel: str,
    work_root: Path,
    project_name: str,
    original_zip: str | None,
    original_source: str,
    cp: dict[str, Path],
    stats: ImportStats,
) -> None:
    src_anchor = Path(original_source).expanduser()
    if not is_trusted_envato_inbox_source(src_anchor):
        raise RuntimeError(f"untrusted_source_not_envato_inbox:{original_source}")

    licenses = collect_licenses(work_root)
    audios = collect_audio_files(work_root)
    main = pick_main_audio(audios)
    if not main:
        raise ValueError("no_main_audio_or_only_preview")

    blob = f"{project_name} {work_root.as_posix()}"
    category = classify_for_channel(channel, blob)
    if channel == "nyc_long" and category == "archive":
        category = "ambient"

    idx = _next_index(channel, cp["music_root"])
    base_name = f"sv_{channel}_{category}_{idx:04d}.mp3"
    dest_audio = _unique_dest(cp["music_root"] / category / base_name)
    if main.suffix.lower() == ".mp3":
        shutil.copy2(main, dest_audio)
    else:
        _ffmpeg_audio_to_mp3(main, dest_audio)

    lic_saved: list[str] = []
    primary_license: str | None = None
    if licenses:
        for li, lic in enumerate(licenses):
            lex = lic.suffix.lower() or ".pdf"
            lname = f"sv_{channel}_{category}_{idx:04d}_license" + (f"_{li}" if li else "") + lex
            dest_lic = _unique_dest(cp["licenses_dir"] / _safe_name(lname))
            shutil.copy2(lic, dest_lic)
            lic_saved.append(str(dest_lic))
            if primary_license is None:
                primary_license = str(dest_lic)

    stems = stem_audios(audios, main)
    stem_paths: list[str] = []
    for si, sp in enumerate(stems):
        stem_slug = _safe_name(sp.stem)[:60]
        sext = sp.suffix.lower()
        sname = f"sv_{channel}_{category}_{idx:04d}_stem_{si:02d}_{stem_slug}{sext}"
        sdest = _unique_dest(cp["music_root"] / category / sname)
        shutil.copy2(sp, sdest)
        stem_paths.append(str(sdest))

    track_id = f"{channel}_{category}_{idx:04d}_{uuid.uuid4().hex[:10]}"
    entry = {
        "track_id": track_id,
        "music_channel": channel,
        "license_valid": True,
        "license_mode": "trusted_envato_claim_clear",
        "filename": dest_audio.name,
        "category": category,
        "source": "Envato",
        "license_file": primary_license,
        "license_files": lic_saved,
        "original_source": original_source,
        "original_zip": original_zip,
        "has_stems": bool(stem_paths),
        "stem_files": stem_paths,
        "imported_at": _utc_iso(),
        "project_name": project_name,
    }

    index = load_index(cp["index_path"])
    index["music_channel"] = channel
    index["license_valid"] = True
    index["license_mode"] = "trusted_envato_claim_clear"
    index["tracks"].append(entry)
    index["updated_at"] = _utc_iso()
    _atomic_write_json(cp["index_path"], index)
    stats.imported += 1


def process_zip(channel: str, zip_path: Path, cp: dict[str, Path], stats: ImportStats) -> None:
    project_name = zip_path.stem
    tmp = Path(tempfile.mkdtemp(prefix="envato_unzip_", dir=cp["music_root"] / "logs"))
    try:
        extract_zip(zip_path, tmp)
        _process_extracted_tree(
            channel=channel,
            work_root=tmp,
            project_name=project_name,
            original_zip=str(zip_path),
            original_source=str(zip_path),
            cp=cp,
            stats=stats,
        )
        arch_name = _safe_name(zip_path.name)
        arch_dst = _unique_dest(cp["archive_dir"] / arch_name)
        shutil.move(str(zip_path), str(arch_dst))
    except ValueError as exc:
        stats.rejected += 1
        try:
            reject_move(zip_path, cp["rejected_dir"], str(exc))
        except Exception as exc2:  # noqa: BLE001
            stats.errors += 1
            stats.error_messages.append(f"{zip_path}: reject_move_failed {exc2!r}")
    except RuntimeError as exc:
        stats.rejected += 1
        stats.errors += 1
        stats.error_messages.append(f"{zip_path}: {exc!r}")
        try:
            reject_move(zip_path, cp["rejected_dir"], repr(exc))
        except Exception as exc2:  # noqa: BLE001
            stats.error_messages.append(f"{zip_path}: reject_move_failed {exc2!r}")
    except Exception as exc:  # noqa: BLE001
        stats.rejected += 1
        stats.errors += 1
        stats.error_messages.append(f"{zip_path}: {exc!r}")
        try:
            reject_move(zip_path, cp["rejected_dir"], repr(exc))
        except Exception as exc2:  # noqa: BLE001
            stats.error_messages.append(f"{zip_path}: reject_move_failed {exc2!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def process_loose_audio(channel: str, audio: Path, cp: dict[str, Path], stats: ImportStats) -> None:
    parent = audio.parent
    licenses = collect_licenses(parent)
    tmp = Path(tempfile.mkdtemp(prefix="envato_loose_", dir=cp["music_root"] / "logs"))
    try:
        shutil.copy2(audio, tmp / audio.name)
        for lic in licenses:
            shutil.copy2(lic, tmp / lic.name)
        _process_extracted_tree(
            channel=channel,
            work_root=tmp,
            project_name=audio.stem,
            original_zip=None,
            original_source=str(audio),
            cp=cp,
            stats=stats,
        )
        shutil.unlink(audio)
    except ValueError as exc:
        stats.rejected += 1
        try:
            reject_move(audio, cp["rejected_dir"], str(exc))
        except Exception as exc2:  # noqa: BLE001
            stats.errors += 1
            stats.error_messages.append(f"{audio}: reject_move_failed {exc2!r}")
    except RuntimeError as exc:
        stats.rejected += 1
        stats.errors += 1
        stats.error_messages.append(f"{audio}: {exc!r}")
        try:
            reject_move(audio, cp["rejected_dir"], repr(exc))
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        stats.rejected += 1
        stats.errors += 1
        stats.error_messages.append(f"{audio}: {exc!r}")
        try:
            reject_move(audio, cp["rejected_dir"], repr(exc))
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def process_directory(channel: str, dir_path: Path, cp: dict[str, Path], stats: ImportStats) -> None:
    project_name = dir_path.name
    tmp = Path(tempfile.mkdtemp(prefix="envato_dir_", dir=cp["music_root"] / "logs"))
    try:
        shutil.copytree(dir_path, tmp / "bundle", dirs_exist_ok=True)
        _process_extracted_tree(
            channel=channel,
            work_root=tmp / "bundle",
            project_name=project_name,
            original_zip=None,
            original_source=str(dir_path),
            cp=cp,
            stats=stats,
        )
        arch_dst = _unique_dest(cp["archive_dir"] / _safe_name(dir_path.name))
        shutil.move(str(dir_path), str(arch_dst))
    except ValueError as exc:
        stats.rejected += 1
        try:
            reject_move(dir_path, cp["rejected_dir"], str(exc))
        except Exception as exc2:  # noqa: BLE001
            stats.errors += 1
            stats.error_messages.append(f"{dir_path}: reject_move_failed {exc2!r}")
    except RuntimeError as exc:
        stats.rejected += 1
        stats.errors += 1
        stats.error_messages.append(f"{dir_path}: {exc!r}")
        try:
            reject_move(dir_path, cp["rejected_dir"], repr(exc))
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        stats.rejected += 1
        stats.errors += 1
        stats.error_messages.append(f"{dir_path}: {exc!r}")
        try:
            reject_move(dir_path, cp["rejected_dir"], repr(exc))
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_channel(channel: str) -> ImportStats:
    cp = ensure_tree(channel)
    inbox = cp["inbox"]
    stats = ImportStats()
    if not inbox.is_dir():
        return stats
    try:
        entries = sorted(inbox.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return stats
    for item in entries:
        if item.name.startswith("."):
            continue
        if item.name.startswith("._"):
            continue
        if item.name.endswith(".reason.txt"):
            continue
        try:
            if item.is_file() and item.suffix.lower() == ".zip":
                process_zip(channel, item, cp, stats)
            elif item.is_file() and item.suffix.lower() in AUDIO_EXTS:
                if is_preview_path(item):
                    stats.rejected += 1
                    try:
                        reject_move(item, cp["rejected_dir"], "preview_or_demo_file")
                    except Exception as exc:  # noqa: BLE001
                        stats.errors += 1
                        stats.error_messages.append(f"{item}: {exc!r}")
                    continue
                process_loose_audio(channel, item, cp, stats)
            elif item.is_dir():
                process_directory(channel, item, cp, stats)
        except Exception as exc:  # noqa: BLE001
            stats.errors += 1
            stats.error_messages.append(f"{item}: outer {exc!r}")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Envato music importer (nyc_long / shorts split).")
    ap.add_argument(
        "--channel",
        choices=("nyc_long", "shorts", "all"),
        required=True,
        help="Which inbox/music tree to process (no cross-fallback).",
    )
    ns = ap.parse_args()
    order: Iterable[str] = ("nyc_long", "shorts") if ns.channel == "all" else (ns.channel,)
    total = ImportStats()
    for ch in order:
        st = run_channel(ch)
        total.imported += st.imported
        total.rejected += st.rejected
        total.errors += st.errors
        total.error_messages.extend(st.error_messages)
    print(
        json.dumps(
            {
                "ok": True,
                "channel": ns.channel,
                "imported": total.imported,
                "rejected": total.rejected,
                "errors": total.errors,
                "error_messages": total.error_messages[:50],
            },
            ensure_ascii=False,
        )
    )
    return 0 if total.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
