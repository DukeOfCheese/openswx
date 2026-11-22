"""Application-level commands (same registry as session.py, which re-exports them): document recovery."""
from __future__ import annotations

import json
from pathlib import Path

from . import recovery
from .document import DocumentError, PartDocument
from .session import APP, command

__all__ = ["list_recovered", "recover_document", "discard_recovered", "save_diagnostic_report"]


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
