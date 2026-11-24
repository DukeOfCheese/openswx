from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

LIMIT = 15
_KINDS = {".oswasm": "assembly", ".sldasm": "assembly", ".oswdrw": "drawing", ".slddrw": "drawing"}


def path() -> Path:
    return Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx") / "recent.json"


def _read() -> list[dict]:
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(d, list):
        return []
    return [e for e in d if isinstance(e, dict) and isinstance(e.get("path"), str) and e["path"]
            and isinstance(e.get("opened_at", 0), (int, float))][:LIMIT]


def _write(entries: list[dict]) -> None:
    p = path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(entries[:LIMIT], indent=1), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _key(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _uploaded(p: Path) -> bool:
    """A copy the web UI's upload wrote to a temporary folder: not a file the user can open again."""
    try:
        return p.parent.name.startswith("openswx_") and p.parent.parent.resolve() == Path(tempfile.gettempdir()).resolve()
    except OSError:
        return False


def add(file: str | os.PathLike) -> None:
    """Put `file` at the top of the list (once: an earlier entry for the same file is removed)."""
    p = Path(file).resolve()
    if _uploaded(p):
        return
    k = _key(str(p))
    entries = [e for e in _read() if _key(e["path"]) != k]
    entries.insert(0, {"path": str(p), "kind": _KINDS.get(p.suffix.lower(), "part"), "opened_at": time.time()})
    _write(entries)


def remove(file: str) -> list[dict]:
    k = _key(file)
    entries = [e for e in _read() if _key(e["path"]) != k]
    _write(entries)
    return entries


def clear() -> None:
    try:
        path().unlink(missing_ok=True)
    except OSError:
        pass


def entries() -> list[dict]:
    """The list, newest first, each with `name`, `folder` and whether the file still `exists`."""
    out = []
    for e in _read():
        p = Path(e["path"])
        out.append({"path": e["path"], "name": p.name, "folder": str(p.parent), "kind": e.get("kind") or "part",
                    "opened_at": e.get("opened_at", 0), "exists": p.is_file()})
    return out
