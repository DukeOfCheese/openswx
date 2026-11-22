"""Document snapshots for undo / redo that do not copy what did not change.

A snapshot is the document's dictionary as JSON, with every long string (the B-rep of an imported body, an
embedded part of an assembly's component) replaced by a reference into one shared, content-addressed store.
A hundred undo steps of a part with a 20 MB imported body then hold that body once, not a hundred times. A
blob is dropped from the store when the last snapshot naming it is.
"""
from __future__ import annotations

import hashlib
import json

BLOB_MIN = 2048  # strings at least this long are stored once and referenced
_KEY = "\u0000blob"
_store: dict[str, list] = {}  # sha -> [text, reference count]


class Snapshot:
    """An opaque undo entry: `snapshots.unpack(s)` gives the dictionary back."""
    __slots__ = ("text", "refs")

    def __init__(self, text: str, refs: tuple[str, ...]):
        self.text, self.refs = text, refs
        for r in refs:
            _store[r][1] += 1

    def __del__(self):
        try:
            for r in self.refs:
                e = _store.get(r)
                if e is not None:
                    e[1] -= 1
                    if e[1] <= 0:
                        del _store[r]
        except Exception:  # noqa: BLE001 - interpreter shutdown: the store may be gone already
            pass

    def __len__(self) -> int:
        return len(self.text)


def _intern(v, refs: list):
    if isinstance(v, str):
        if len(v) < BLOB_MIN:
            return v
        sha = hashlib.sha1(v.encode("utf-8", "surrogatepass")).hexdigest()
        if sha not in _store:
            _store[sha] = [v, 0]
        refs.append(sha)
        return {_KEY: sha}
    if isinstance(v, dict):
        return {k: _intern(x, refs) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_intern(x, refs) for x in v]
    return v


def pack(d: dict) -> Snapshot:
    refs: list[str] = []
    # (references are counted by the Snapshot; blobs first seen here sit at count 0 until it is made)
    body = _intern(d, refs)
    return Snapshot(json.dumps(body), tuple(refs))


def _resolve(v):
    if isinstance(v, dict):
        if len(v) == 1 and _KEY in v:
            return _store[v[_KEY]][0]
        return {k: _resolve(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve(x) for x in v]
    return v


def unpack(s: Snapshot) -> dict:
    return _resolve(json.loads(s.text))


def stored_bytes() -> int:
    """Characters held in the shared store (for tests and diagnostics)."""
    return sum(len(e[0]) for e in _store.values())
