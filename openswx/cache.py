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
from pathlib import Path

LIMIT = 1 << 30  # bytes kept before the oldest entries are removed
_TAG: list = [None]
_MODULES = ("parasolid", "sldprt", "sw_rebuild", "sw_sketch", "sw_tree", "features", "document", "sketch", "naming", "geom")


def folder() -> Path | None:
    env = os.environ.get("OPENSWX_CACHE")
    if env and env.lower() in ("off", "0", "no", "false"):
        return None
    if env:
        base = Path(env)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "openswx" / "cache"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "openswx"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "openswx"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return base


def code_tag() -> str:
    """openswx's version and the bytes of the modules whose output is cached (a frozen build: the program)."""
    if _TAG[0] is None:
        from . import __version__
        h = hashlib.sha1(__version__.encode())
        here = Path(__file__).resolve().parent
        found = False
        for m in _MODULES:
            src = here / f"{m}.py"
            if src.exists():
                h.update(src.read_bytes())
                found = True
        if not found:  # (a frozen build has no sources: its executable stands for the code)
            try:
                st = Path(sys.executable).stat()
                h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
            except OSError:
                pass
        _TAG[0] = h.hexdigest()[:16]
    return _TAG[0]


def key(path: str, *what) -> str | None:
    """The cache key for `what` of the file at `path` (None when the file cannot be read or the cache is off)."""
    if folder() is None:
        return None
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    h = hashlib.sha1(data)
    h.update(json.dumps([code_tag(), *what], default=str).encode())
    return h.hexdigest()


def _path(kind: str, k: str | None, ext: str) -> Path | None:
    base = folder() if k else None
    return None if base is None else base / f"{kind}-{k}{ext}"


def get_json(kind: str, k: str | None):
    p = _path(kind, k, ".json")
    if p is None or not p.exists():
        return None
    try:
        out = json.loads(p.read_text(encoding="utf-8"))
        os.utime(p)  # (recently used: kept longest)
        return out
    except (OSError, ValueError):
        return None


def put_json(kind: str, k: str | None, value) -> None:
    p = _path(kind, k, ".json")
    if p is None:
        return
    try:
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        tmp.replace(p)
        _prune(p.parent)
    except (OSError, TypeError, ValueError):
        pass


def get_shape(kind: str, k: str | None):
    """(shape, extra) stored by put_shape, or None."""
    p = _path(kind, k, ".brep")
    if p is None or not p.exists():
        return None
    meta = get_json(kind + "-meta", k)
    if meta is None:
        return None
    try:
        from build123d import import_brep
        shape = import_brep(str(p))
        os.utime(p)
        return shape, meta
    except Exception:
        return None


def put_shape(kind: str, k: str | None, shape, extra) -> None:
    p = _path(kind, k, ".brep")
    if p is None:
        return
    try:
        from build123d import export_brep
        tmp = p.with_name(p.stem + ".part.brep")
        export_brep(shape, str(tmp))
        tmp.replace(p)
        put_json(kind + "-meta", k, extra)
    except Exception:
        pass


def _prune(base: Path) -> None:
    try:
        files = [(f.stat().st_mtime, f.stat().st_size, f) for f in base.iterdir() if f.is_file()]
    except OSError:
        return
    total = sum(s for _, s, _ in files)
    if total <= LIMIT:
        return
    for _, s, f in sorted(files):
        try:
            f.unlink()
        except OSError:
            continue
        total -= s
        if total <= 0.8 * LIMIT:
            break


def clear() -> int:
    """Remove every cache entry; returns how many files went."""
    base = folder()
    n = 0
    if base is None:
        return 0
    for f in base.iterdir():
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n
