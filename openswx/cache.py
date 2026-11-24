"""Disk cache for work that depends only on a file's contents: the B-rep read from a SOLIDWORKS part and its
rebuilt feature history. Opening the same file again (or the same part in another assembly) then takes moments
instead of a full read or rebuild.

Entries are keyed by the file's bytes (SHA-1), what was asked of it (configuration, kind) and the code that
produced them (openswx's version plus the reader modules' own bytes, so a changed reader never serves an old
result). They live in OPENSWX_CACHE, else the user's cache folder (%LOCALAPPDATA%\\openswx\\cache on Windows);
OPENSWX_CACHE=off turns the cache off. The oldest entries go once the folder passes 1 GB.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

LIMIT = 1 << 30
_PRUNE_EVERY = 60.0
_TOUCH_EVERY = 3600.0
_RACY_NS = 2_000_000_000
_DIGEST_MAX = 256
_TAG: list = [None]
_PRUNE_AT = 0.0
_DIGESTS: OrderedDict[tuple, str] = OrderedDict()
_TOUCHED: dict[str, float] = {}


@lru_cache(maxsize=8)
def _folder(env: str | None, platform: str, home: str, local: str | None, xdg: str | None) -> Path | None:
    if env and env.lower() in ("off", "0", "no", "false"):
        return None
    if env:
        base = Path(env).expanduser().resolve()
    elif platform == "win32":
        base = Path(local or (Path(home) / "AppData" / "Local")) / "openswx" / "cache"
    elif platform == "darwin":
        base = Path(home) / "Library" / "Caches" / "openswx"
    else:
        base = Path(xdg or (Path(home) / ".cache")) / "openswx"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return base


def folder() -> Path | None:
    return _folder(os.environ.get("OPENSWX_CACHE"), sys.platform, str(Path.home()),
                   os.environ.get("LOCALAPPDATA"), os.environ.get("XDG_CACHE_HOME"))


def code_tag() -> str:
    """openswx's version and the bytes of the modules whose output is cached (a frozen build: the program)."""
    if _TAG[0] is None:
        from openswx import __version__
        h = hashlib.sha1(__version__.encode())
        here = Path(__file__).resolve().parent
        found = False
        for src in sorted(here.rglob("*.py")):
            h.update(src.relative_to(here).as_posix().encode())
            h.update(src.read_bytes())
            found = True
        if not found:
            try:
                st = Path(sys.executable).stat()
                h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
            except OSError:
                pass
        _TAG[0] = h.hexdigest()[:16]
    return _TAG[0]


def _digest(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    memo = (str(path), st.st_size, st.st_mtime_ns, st.st_ctime_ns, getattr(st, "st_ino", 0))
    hit = _DIGESTS.get(memo)
    if hit is not None:
        _DIGESTS.move_to_end(memo)
        return hit
    try:
        with path.open("rb") as f:
            digest = hashlib.file_digest(f, "sha1").hexdigest()
    except OSError:
        return None
    # only store a digest once the file is old enough that a same-size rewrite cannot keep its timestamps
    if st.st_mtime_ns <= time.time_ns() - _RACY_NS:
        _DIGESTS[memo] = digest
        if len(_DIGESTS) > _DIGEST_MAX:
            _DIGESTS.popitem(last=False)
    return digest


def key(path: str, *what) -> str | None:
    """The cache key for `what` of the file at `path` (None when the file cannot be read or the cache is off)."""
    if folder() is None:
        return None
    digest = _digest(Path(path))
    if digest is None:
        return None
    h = hashlib.sha1(digest.encode())
    h.update(json.dumps([code_tag(), *what], default=str).encode())
    return h.hexdigest()


def _path(kind: str, k: str | None, ext: str) -> Path | None:
    base = folder() if k else None
    return None if base is None else base / f"{kind}-{k}{ext}"


def _touch(p: Path) -> None:
    now = time.monotonic()
    last = _TOUCHED.get(str(p))
    if last is not None and now - last < _TOUCH_EVERY:
        return
    try:
        os.utime(p)
        _TOUCHED[str(p)] = now
    except OSError:
        pass
    if len(_TOUCHED) > 4096:
        _TOUCHED.clear()


def _write_atomic(p: Path, data: bytes) -> None:
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def get_json(kind: str, k: str | None):
    p = _path(kind, k, ".json")
    if p is None:
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            out = json.load(f)
    except (OSError, ValueError):
        return None
    _touch(p)
    return out


def put_json(kind: str, k: str | None, value) -> None:
    p = _path(kind, k, ".json")
    if p is None:
        return
    try:
        _write_atomic(p, json.dumps(value, separators=(",", ":")).encode())
        _maybe_prune(p.parent)
    except (OSError, TypeError, ValueError):
        pass


def get_shape(kind: str, k: str | None):
    """(shape, extra) stored by put_shape, or None."""
    p = _path(kind, k, ".brep")
    if p is None:
        return None
    meta = get_json(kind + "-meta", k)
    if meta is None:
        return None
    try:
        from build123d import import_brep
        shape = import_brep(str(p))
        _touch(p)
        return shape, meta
    except Exception:
        return None


def put_shape(kind: str, k: str | None, shape, extra) -> None:
    p = _path(kind, k, ".brep")
    if p is None:
        return
    tmp = p.with_name(f"{p.stem}.{os.getpid()}.{uuid.uuid4().hex}.part.brep.tmp")
    try:
        from build123d import export_brep
        export_brep(shape, str(tmp))
        os.replace(tmp, p)
        put_json(kind + "-meta", k, extra)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass


def _maybe_prune(base: Path) -> None:
    global _PRUNE_AT
    now = time.monotonic()
    if now - _PRUNE_AT < _PRUNE_EVERY:
        return
    _PRUNE_AT = now
    _prune(base)


def _prune(base: Path) -> None:
    entries = []
    total = 0
    try:
        with os.scandir(base) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False) or entry.name.endswith(".tmp"):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, entry.path))
                total += st.st_size
    except OSError:
        return
    if total <= LIMIT:
        return
    for _, size, path in sorted(entries):
        try:
            os.unlink(path)
        except OSError:
            continue
        total -= size
        if total <= 0.8 * LIMIT:
            break


def clear() -> int:
    """Remove every cache entry; returns how many files went."""
    base = folder()
    n = 0
    if base is None:
        return 0
    try:
        with os.scandir(base) as it:
            for entry in it:
                try:
                    os.unlink(entry.path)
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n
