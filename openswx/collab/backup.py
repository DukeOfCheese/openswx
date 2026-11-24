"""Local backups of cloud documents.

The server is where a cloud document lives; this keeps copies on this machine as well, in case the server is
lost or unreachable: every autosave interval (OPENSWX_AUTOSAVE / Tools > Options, like recovery.py; `off`: no
backups), each open cloud document that changed since its last backup is written as an ordinary .oswprt to

    <home>/cloud-backups/<server host>/<document id>/<date>-<time>-seq<N>.oswprt

and only the newest KEEP (OPENSWX_COLLAB_BACKUPS, default 20) are kept. A backup is the document as the
server had it (changes not yet answered are not in it), and opens with open_part like any other file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path

from openswx.system import recovery

_state = {"thread": None, "stop": threading.Event()}


def keep() -> int:
    try:
        return max(1, int(os.environ.get("OPENSWX_COLLAB_BACKUPS", "20")))
    except ValueError:
        return 20


def _slug(text: str) -> str:
    """A safe folder name: never '.', '..' or empty, whatever the server sent (dots alone would climb out)."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text))[:80].lstrip(".")
    if not re.search(r"[A-Za-z0-9]", s):
        s = "id-" + hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]
    return s


def folder(server: str, document_id: str) -> Path:
    p = urllib.parse.urlsplit(server)
    host = (p.hostname or "server") + (f"_{p.port}" if p.port else "")
    return recovery.home() / "cloud-backups" / _slug(host) / _slug(document_id)


def save(client) -> str | None:
    """Write a backup of one cloud document if it changed since the last one; returns the file written."""
    snap = client.backup_snapshot()
    if snap is None:
        return None
    seq, content = snap
    if client.backed_up_seq == seq:
        return None
    d = folder(client.server, client.document_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{time.strftime('%Y%m%d-%H%M%S')}-seq{seq}.oswprt"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(content, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    client.backed_up_seq = seq
    for old in sorted(d.glob("*.oswprt"))[:-keep()]:  # (names sort by time)
        old.unlink(missing_ok=True)
    return str(path)


def save_all() -> list[str]:
    from openswx.collab.client import CLIENTS
    out = []
    for c in list(CLIENTS.values()):
        try:
            p = save(c)
        except OSError:
            continue
        if p:
            out.append(p)
    return out


def _loop() -> None:
    last = 0.0
    while not _state["stop"].wait(max(1.0, min(recovery.interval() or 15.0, 15.0))):
        iv = recovery.interval()
        if iv <= 0 or time.time() - last < iv:
            continue
        last = time.time()
        try:
            save_all()
        except Exception:  # noqa: BLE001 - a backup must never take the application down
            pass


def start() -> bool:
    """Start the backup thread (once; not when autosave is off). True if it runs."""
    if _state["thread"] is None and recovery.interval() > 0:
        _state["stop"].clear()
        t = threading.Thread(target=_loop, name="openswx-cloud-backup", daemon=True)
        _state["thread"] = t
        t.start()
    return _state["thread"] is not None
