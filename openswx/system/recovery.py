"""Autosave and document recovery.

While openswx runs, every document with unsaved changes is written to the recovery folder
(`<home>/recovery/<session>/`) at most `interval` seconds after it changed. A session that ends normally
removes its folder; one that crashed or was killed leaves it, and the next start offers those documents back
(commands list_recovered / recover_document / discard_recovered; the UI asks on start).

`OPENSWX_AUTOSAVE`: seconds between checks (default 60), `off` / `0` to disable.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

HEARTBEAT = "alive"
STALE = 180.0  # a session whose heartbeat is older than this is over, whatever its process id says now

_state = {"dir": None, "thread": None, "written": {}, "version": -1, "stop": threading.Event()}


def home() -> Path:
    return Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx")


def root() -> Path:
    return home() / "recovery"


def interval() -> float:
    v = os.environ.get("OPENSWX_AUTOSAVE")
    if v is None:  # (the environment wins; else Tools > Options)
        from openswx.system import settings
        return float(settings.get("autosave_seconds"))
    v = v.strip().lower()
    if v in ("off", "no", "false", ""):
        return 0.0
    try:
        return max(0.0, float(v))
    except ValueError:
        return 60.0


def _pid_alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "document"


def session_dir() -> Path:
    if _state["dir"] is None:
        d = root() / f"{os.getpid()}-{int(time.time())}"
        d.mkdir(parents=True, exist_ok=True)
        (d / HEARTBEAT).write_text(str(os.getpid()), encoding="ascii")
        _state["dir"] = d
        atexit.register(clean_exit)
    return _state["dir"]


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_now(app) -> list[str]:
    """Write every document with unsaved changes that differs from its last recovery copy; drop the copies of
    documents that were saved or closed since. Returns the names written."""
    d = session_dir()
    (d / HEARTBEAT).write_text(str(os.getpid()), encoding="ascii")
    written, seen = [], set()
    with app.lock:
        docs = [(n, doc) for n, doc in app.documents.items() if getattr(doc, "dirty", False)]
        snaps = []
        for name, doc in docs:
            kind = getattr(doc, "kind", "part")
            if kind == "assembly":  # as save does: carry the linked parts' current state
                doc.live_docs = app.documents
                for c in doc.components:
                    if not (c.derived and "mirror" in c.derived):
                        try:
                            c.part = c.doc(doc.live_docs).to_dict()
                        except Exception:  # noqa: BLE001 - a component that cannot be read keeps its last state
                            pass
            snaps.append((name, kind, getattr(doc, "path", None), json.dumps(doc.to_dict())))
    for name, kind, path, text in snaps:
        slug = _slug(name)
        seen.add(slug)
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if _state["written"].get(slug) == digest:
            continue
        _write(d / f"{slug}.{'oswasm' if kind == 'assembly' else 'oswprt'}", text)
        _write(d / f"{slug}.json", json.dumps({"name": name, "kind": kind, "path": path, "saved": time.time()}))
        _state["written"][slug] = digest
        written.append(name)
    for slug in [s for s in _state["written"] if s not in seen]:  # saved or closed: nothing to recover
        for f in d.glob(slug + ".*"):
            f.unlink(missing_ok=True)
        del _state["written"][slug]
    return written


def _loop(app) -> None:
    while not _state["stop"].wait(max(1.0, min(interval(), 15.0))):
        try:
            due = time.time() - _state.get("last", 0.0) >= interval()
            if app.version != _state["version"] and due:
                _state["version"], _state["last"] = app.version, time.time()
                save_now(app)
            elif _state["dir"] is not None:
                os.utime(_state["dir"] / HEARTBEAT)
        except Exception:  # noqa: BLE001 - autosave must never take the application down
            pass


def start(app) -> None:
    """Start the autosave thread (once; no-op when autosave is off)."""
    if _state["thread"] is None and interval() > 0:
        _state["stop"].clear()
        t = threading.Thread(target=_loop, args=(app,), name="openswx-autosave", daemon=True)
        _state["thread"] = t
        t.start()


def clean_exit() -> None:
    """The session ended normally: nothing of it needs recovering."""
    _state["stop"].set()
    d, _state["dir"] = _state["dir"], None
    _state["written"].clear()
    _state["thread"] = None
    if d is not None:
        shutil.rmtree(d, ignore_errors=True)


def recovered() -> list[dict]:
    """Documents left behind by sessions that did not end normally, newest first."""
    out = []
    r = root()
    if not r.is_dir():
        return out
    for d in r.iterdir():
        if not d.is_dir() or d == _state["dir"]:
            continue
        try:
            pid = int(d.name.split("-")[0])
            beat = (d / HEARTBEAT).stat().st_mtime
        except (ValueError, OSError):
            pid, beat = -1, 0.0
        if _pid_alive(pid) and time.time() - beat < STALE:
            continue  # another openswx that is still running
        metas = list(d.glob("*.json"))
        if not metas:
            shutil.rmtree(d, ignore_errors=True)
            continue
        for m in metas:
            try:
                meta = json.loads(m.read_text(encoding="utf-8"))
                f = m.with_suffix(".oswasm" if meta.get("kind") == "assembly" else ".oswprt")
                if f.is_file():
                    out.append({"id": f"{d.name}/{m.stem}", "name": meta["name"], "kind": meta.get("kind", "part"),
                                "path": meta.get("path"), "saved": meta.get("saved", 0.0), "file": str(f)})
            except (OSError, ValueError, KeyError):
                continue
    return sorted(out, key=lambda e: -e["saved"])


def discard(rid: str | None = None) -> int:
    """Delete one recovered document (by id), or all of them."""
    n = 0
    for e in recovered():
        if rid is None or e["id"] == rid:
            f = Path(e["file"])
            f.unlink(missing_ok=True)
            f.with_suffix(".json").unlink(missing_ok=True)
            n += 1
            if not list(f.parent.glob("*.json")):
                shutil.rmtree(f.parent, ignore_errors=True)
    return n
