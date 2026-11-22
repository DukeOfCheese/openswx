"""Application-level commands (same registry as session.py, which re-exports them): document recovery."""
from __future__ import annotations

import json
from pathlib import Path

from . import recovery
from .document import DocumentError, PartDocument
from .session import APP, command

__all__ = ["list_recovered", "recover_document", "discard_recovered", "save_diagnostic_report", "batch"]


@command(mutates=False, category="document")
def list_recovered() -> dict:
    """Document Recovery: documents with unsaved changes left by an openswx session that crashed or was killed
    (autosaved copies). Each has an id for recover_document / discard_recovered."""
    return {"recovered": [{k: e[k] for k in ("id", "name", "kind", "path", "saved")} for e in recovery.recovered()]}


@command(category="document")
def recover_document(id: str) -> dict:
    """Open a recovered document (id from list_recovered). It opens with unsaved changes under its old name and
    file path, so Save writes it back to where it belongs; the autosaved copy is then removed."""
    entry = next((e for e in recovery.recovered() if e["id"] == id), None)
    if entry is None:
        raise DocumentError(f"no recovered document {id!r}; see list_recovered")
    data = json.loads(Path(entry["file"]).read_text(encoding="utf-8"))
    name, n = entry["name"], 1
    while name in APP.documents:
        n += 1
        name = f"{entry['name']} (recovered {n})" if n > 2 else f"{entry['name']} (recovered)"
    if entry["kind"] == "assembly":
        from .assembly import AssemblyDocument
        doc = AssemblyDocument(name)
        doc.live_docs = APP.documents
    else:
        doc = PartDocument(name)
    data["name"] = name
    doc.load_dict(data)
    doc.path, doc.dirty = entry["path"], True
    doc._undo.clear()
    APP.documents[name] = doc
    APP.active = name
    recovery.discard(id)
    return {"recovered": name, "path": entry["path"], "model": doc.summary()}


@command(category="document")
def discard_recovered(id: str | None = None) -> dict:
    """Delete a recovered document's autosaved copy (id from list_recovered), or all of them when no id is given."""
    return {"discarded": recovery.discard(id)}


@command(mutates=False, category="document")
def save_diagnostic_report(path: str, include_documents: bool = False) -> dict:
    """Help > Save Diagnostic Report: a .zip with the application log (commands, timings, error tracebacks),
    the program and library versions and the recent command history - what a bug report needs. The open
    documents are added only with include_documents=True (they are your design data)."""
    from . import applog
    extra = {"versions.json": json.dumps(applog.versions(), indent=1),
             "activity.json": json.dumps(APP.log[-200:], indent=1, default=str),
             "documents.json": json.dumps([{"name": n, "kind": getattr(d, "kind", "part"), "dirty": d.dirty,
                                            "features": len(getattr(d, "features", []))} for n, d in APP.documents.items()], indent=1)}
    if include_documents:
        for n, d in APP.documents.items():
            ext = "oswasm" if getattr(d, "kind", "part") == "assembly" else "oswprt"
            extra[f"documents/{n}.{ext}"] = json.dumps(d.to_dict(), indent=1)
    return {"saved": applog.write_report(path, extra), "contents": sorted(extra)}


@command(category="edit")
def batch(commands: list[dict], atomic: bool = True) -> dict:
    """Run several commands in one call: `commands` is a list of {"name": ..., "args": {...}}. With atomic
    (default) the first failure puts every open document back as it was before the batch and reports which
    command failed; on success the whole batch is one Undo step. Returns each command's result in order.
    Much faster for an agent than one round trip per sketch entity."""
    from . import session as S
    if not commands:
        raise DocumentError("batch needs at least one command")
    for i, c in enumerate(commands):
        if not isinstance(c, dict) or c.get("name") not in S.COMMANDS:
            raise DocumentError(f"command {i + 1}: unknown command {c.get('name') if isinstance(c, dict) else c!r}")
        if c["name"] in ("batch", "undo", "redo"):
            raise DocumentError(f"command {i + 1}: {c['name']} cannot be used inside a batch")
    before = {n: (d, json.dumps(d.to_dict()), len(d._undo), d.dirty, getattr(d, "path", None)) for n, d in APP.documents.items()}
    active = APP.active
    results = []
    try:
        for i, c in enumerate(commands):
            try:
                results.append(S.COMMANDS[c["name"]]["fn"](**(c.get("args") or {})))
            except (S.CommandError, TypeError) as exc:
                raise DocumentError(f"command {i + 1} ({c['name']}) failed: {exc}"
                                    + ("; nothing was changed" if atomic else f"; the {i} before it were kept")) from exc
    except DocumentError:
        if atomic:
            APP.documents.clear()
            for n, (d, text, n_undo, dirty, path) in before.items():
                if json.dumps(d.to_dict()) != text:
                    d.load_dict(json.loads(text))
                del d._undo[n_undo:]
                d._redo.clear()
                d.dirty, d.path = dirty, path
                APP.documents[n] = d
            APP.active = active
        raise
    for n, (d, _text, n_undo, _dirty, _path) in before.items():  # one Undo step: back to before the batch
        if APP.documents.get(n) is d and len(d._undo) > n_undo + 1:
            del d._undo[n_undo + 1:]
    return {"results": results, "count": len(results)}
