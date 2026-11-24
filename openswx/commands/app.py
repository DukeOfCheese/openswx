"""Application-level commands (same registry as session.py, which re-exports them): document recovery."""
from __future__ import annotations

import json
from pathlib import Path

from openswx.system import recovery
from openswx.core.document import DocumentError, PartDocument
from openswx.session import APP, command

__all__ = ["list_recovered", "recover_document", "discard_recovered", "save_diagnostic_report", "batch", "start_job", "job_status", "cancel_job", "import_sw_configurations", "agent_connection_info"]


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
        from openswx.core.assembly import AssemblyDocument
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
    from openswx.system import applog
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
    from openswx import session as S
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


@command(mutates=False, category="tools")
def start_job(name: str, args: dict | None = None) -> dict:
    """Start a long command (simulation_run, cfd_external, cfd_internal, plastics_fill, cam_generate, ...) in the
    background and return at once with a job id. Poll job_status(id) for its progress and, when it is done,
    its result; cancel_job(id) stops it. The application stays usable meanwhile. Any command can be started
    this way; the solvers named above also let other commands run while they compute."""
    from openswx.system import jobs
    from openswx import session as S
    if name not in S.COMMANDS:
        raise DocumentError(f"unknown command {name!r}")
    if name in ("start_job", "batch"):
        raise DocumentError(f"{name} cannot run as a job")
    origin = getattr(S._TL, "origin", "agent")
    jid = jobs.start(name, lambda: S.call(name, args or {}, origin))
    return jobs.public(jobs.get(jid))


@command(mutates=False, category="tools")
def job_status(id: str | None = None) -> dict:
    """A background job's state (running / done / error / cancelled), progress (0-1, when the solver reports
    it), seconds so far and - once done - its result. Without an id: every job of this session."""
    from openswx.system import jobs
    if id is None:
        return {"jobs": [jobs.public(j, with_result=False) for j in jobs.all_jobs()]}
    job = jobs.get(id)
    if job is None:
        raise DocumentError(f"no job {id!r}")
    return jobs.public(job)


@command(mutates=False, category="tools")
def cancel_job(id: str) -> dict:
    """Cancel a running job. Solvers that report progress stop at their next step; others finish their
    current computation and the result is discarded."""
    from openswx.system import jobs
    job = jobs.get(id)
    if job is None:
        raise DocumentError(f"no job {id!r}")
    return {**jobs.public(job, with_result=False), "cancel_requested": jobs.cancel(id)}


@command(category="configurations")
def import_sw_configurations(path: str, limit: int = 12) -> dict:
    """Bring the other configurations of a SOLIDWORKS part (.sldprt) into the active part, which was opened
    from that file. Each configuration that has geometry stored in the file becomes an openswx configuration
    of the same name showing that geometry as an imported body (the editable history, where it was rebuilt,
    belongs to the configuration the file was saved in, and is suppressed in the others). Configurations
    SOLIDWORKS had not rebuilt before saving carry no geometry and are listed under `without_geometry` in the
    file's own tools; they cannot be imported. limit: at most this many configurations (each is read and
    stored in the part)."""
    from pathlib import Path as _P

    from openswx.commands.support import _read_foreign
    from openswx.core.features import encode_shape
    from openswx.exchange.sldprt import configurations
    doc = APP.doc
    p = _P(path)
    if p.suffix.lower() != ".sldprt" or not p.is_file():
        raise DocumentError(f"{path!r} is not a SOLIDWORKS part file")
    cfgs = configurations(str(p))
    others = [c for c in cfgs if not c["active"]]
    if not cfgs:
        raise DocumentError("the file lists no configuration with stored geometry")
    if not others:
        return {"configurations": list(doc.configurations), "imported": [], "note": "the file stores geometry for its active configuration only"}
    base = [f["name"] for f in doc.features]
    doc._checkpoint()
    n_undo = len(doc._undo)
    imported, failed = [], []
    try:
        for c in others[:limit]:
            name = c["name"]
            while name in doc.configurations:
                name += " (SW)"
            try:
                shape, _notes = _read_foreign(str(p), c["id"])
                if not shape.solids():
                    raise ValueError("no solid body in this configuration")
            except Exception as exc:  # noqa: BLE001 - one configuration that cannot be read does not lose the others
                failed.append({"configuration": c["name"], "reason": str(exc)[:200]})
                continue
            feat = doc.add_feature("import", {"brep": encode_shape(shape), "source": f"{p.name} [{c['name']}]"})
            for other in doc.configurations.values():  # this body shows in its own configuration only
                other.setdefault("suppressed", {})[feat["name"]] = True
            doc.configurations[name] = {"suppressed": {**{b: True for b in base}, **{i["feature"]: True for i in imported},
                                                       feat["name"]: False}, "values": {}}
            for i in imported:  # ... and the bodies of the configurations imported before it are hidden here too
                doc.configurations[i["name"]]["suppressed"][feat["name"]] = True
            imported.append({"name": name, "feature": feat["name"], "volume": round(float(shape.volume), 6)})
    finally:
        del doc._undo[n_undo:]  # one Undo step for the lot
    doc.rebuild()
    doc.dirty = True
    return {"configurations": list(doc.configurations), "active": doc.active_config, "imported": imported, "failed": failed,
            "skipped_over_limit": [c["name"] for c in others[limit:]]}


@command(mutates=False, category="document", mcp=False)
def agent_connection_info(port: int = 8765) -> dict:
    """Help > Agent (MCP) Setup: how agents connect to this installation. `stdio` is the command a chat app such
    as Claude Desktop starts (with openswx open it relays to the open window, so the edits show live there);
    `http` is the endpoint for clients that can reach this computer (Claude Code); `claude_desktop_config` is
    where Claude Desktop keeps its MCP servers on this system; `token` is this user's secret for the HTTP endpoint
    and `token_command` the full command that prints it (shown only in the app's own window: not an MCP tool, and
    refused on view-only links)."""
    import os
    import sys
    from openswx.server import security
    if getattr(sys, "frozen", False):  # desktop build: the console executable sits next to the app
        exe = Path(sys.executable).with_name("openswx-cli.exe" if os.name == "nt" else "openswx-cli")
        command, args = str(exe), ["mcp"]
        token_command = f'"{exe}" token' if " " in str(exe) else f"{exe} token"
    else:
        command, args = sys.executable, ["-m", "openswx", "mcp"]
        token_command = (f'"{sys.executable}"' if " " in sys.executable else sys.executable) + " -m openswx token"
    if port != 8765:
        args += ["--port", str(port)]
    if sys.platform == "darwin":
        cfg = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif os.name == "nt":
        cfg = Path(os.environ.get("APPDATA", Path.home())) / "Claude" / "claude_desktop_config.json"
    else:
        cfg = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    return {"stdio": {"command": command, "args": args}, "http": f"http://127.0.0.1:{port}/mcp",
            "claude_desktop_config": str(cfg), "token": security.user_token(), "token_command": token_command}
