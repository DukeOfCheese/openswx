"""Live collaboration commands (collab_*): sign in to an openswx-collab server, share a part as a cloud document,
open one, and work with the others in it (locks, history, versions, presence).

They import nothing of the optional collab extra until they run, so they are listed (and documented) whether or
not it is installed; without it they say how to install it.
"""
from __future__ import annotations

import contextlib
import time
from typing import Any, Literal

from ..document import DocumentError
from ..session import APP, command, unlocked
from . import MISSING, available

__all__ = ["collab_sign_in", "collab_server_info", "collab_sign_out", "collab_status", "collab_projects", "collab_create_project",
           "collab_documents", "collab_enable", "collab_open", "collab_disable", "collab_lock", "collab_unlock",
           "collab_history", "collab_name_version", "collab_restore", "collab_presence"]


def _need():
    if not available():
        raise DocumentError(MISSING)
    from . import auth, client
    return auth, client


@contextlib.contextmanager
def _server(url: str | None = None):
    """An HTTP client signed in to the saved server; network trouble becomes a plain error."""
    import httpx
    auth, _ = _need()
    url = url or auth.server()
    if not url or not auth.token():
        raise DocumentError("not signed in to a collaboration server: run collab_sign_in first")
    try:
        with auth.http(url) as c:
            yield c
    except httpx.HTTPError as exc:
        raise DocumentError(f"cannot reach the collaboration server at {url}: {exc}") from exc


def _ok(r, what: str):
    if r.status_code >= 300:
        auth, _ = _need()
        if r.status_code == 401:
            raise DocumentError(f"{what}: the server asks you to sign in again (collab_sign_in)")
        raise DocumentError(f"{what}: {auth.error_text(r)}")
    return r.json() if r.content else None


def _client():
    _, client = _need()
    c = client.client_for(APP.active_doc)
    if c is None:
        raise DocumentError("the active document is not a cloud document: share it with collab_enable, or open one "
                            "with collab_open")
    return c


# ---------------------------------------------------------------------- account
@command(mutates=False, category="collab")
def collab_sign_in(server: str, email: str | None = None, password: str | None = None,
                   provider: Literal["google", "github"] | None = None, invite: str | None = None) -> dict:
    """Sign in to an openswx-collab server for live collaboration (shared cloud documents). Give the server's
    address (e.g. https://collab.example.com) and either email + password, or provider 'google' / 'github' to sign
    in through the system browser (the command waits up to 5 minutes for the browser to come back; invite: an
    invitation token, for a first sign-in on a server that needs one). The server and a session token are kept in
    the openswx home folder, so this is needed once per machine. Returns {server, user}."""
    auth, _ = _need()
    if provider is not None:
        with unlocked():  # (the user is busy in the browser: openswx stays usable meanwhile)
            return auth.sign_in_browser(server, provider, invite=invite)
    if not email or not password:
        raise DocumentError("give email and password, or provider='google' / 'github'")
    try:
        return auth.sign_in_password(server, email, password)
    except Exception as exc:  # noqa: BLE001 - network trouble, said plainly
        if isinstance(exc, (DocumentError, ValueError)):
            raise
        raise DocumentError(f"cannot reach the collaboration server at {server}: {exc}") from exc


@command(mutates=False, category="collab")
def collab_server_info(server: str) -> dict:
    """What a collaboration server offers before anyone signs in, as a sign-in dialog needs it: its name and
    version, the protocol it speaks, how accounts are made (registration: e.g. 'open' or 'invite') and the
    browser sign-in providers it has set up (oauth: e.g. ['google', 'github']; empty: email and password only).
    server: its address, e.g. https://collab.example.com. Returns that, with the address as used (server)."""
    import httpx
    auth, _ = _need()
    try:
        url = auth.normalise(server)
        with auth.http(url, "") as c:
            r = c.get("/api/server")
    except httpx.HTTPError as exc:
        raise DocumentError(f"cannot reach the collaboration server at {server}: {exc}") from exc
    info = _ok(r, "asking the server what it offers")
    if not isinstance(info, dict):
        raise DocumentError(f"{server} did not answer like an openswx-collab server")
    return {**info, "server": url}


@command(mutates=False, category="collab")
def collab_sign_out() -> dict:
    """Sign out of the collaboration server: every cloud document stops syncing (each stays open as a local copy)
    and the saved session token is deleted."""
    auth, client = _need()
    stopped = [c.doc.name for c in list(client.CLIENTS.values())]
    for c in list(client.CLIENTS.values()):
        c.close()
    return {"signed_out": auth.sign_out(), "stopped": stopped}


@command(mutates=False, category="collab")
def collab_status() -> dict:
    """Live collaboration status: the server and who is signed in, and for every open cloud document its
    connection state, sequence number (seq), role, the other people in it (peers, with their presence and
    drafts), locks (who is editing which feature), features broken by someone's change (broken: feature id ->
    {error, by_name, command}), refused changes (conflicts: who changed what first), recent changes from others,
    and how many of your changes are still waiting for the server (pending)."""
    if not available():
        return {"available": False, "message": MISSING, "server": None, "user": None, "documents": []}
    auth, client = _need()
    active = client.client_for(APP.documents.get(APP.active)) if APP.active else None
    return {"available": True, "server": auth.server(), "user": auth.user(), "signed_in": auth.token() is not None,
            "active": active.document_id if active else None, **client.status()}


@command(mutates=False, category="collab")
def collab_projects() -> dict:
    """The organisations and projects you can use on the collaboration server: [{id, name, role, projects:
    [{id, name, role}]}]. A project is where collab_enable puts a shared document."""
    with _server() as s:
        orgs = _ok(s.get("/api/orgs"), "listing organisations")
        for o in orgs:
            o["projects"] = _ok(s.get(f"/api/orgs/{o['id']}/projects"), "listing projects")
    return {"orgs": orgs}


@command(mutates=False, category="collab")
def collab_create_project(name: str, org: str | None = None) -> dict:
    """Create a project on the collaboration server (you become its owner). org: organisation id or name; default:
    your only organisation. Returns the project {id, name, org_id, role}."""
    with _server() as s:
        orgs = _ok(s.get("/api/orgs"), "listing organisations")
        pick = [o for o in orgs if org is None or org in (o["id"], o["name"])]
        if len(pick) != 1:
            raise DocumentError(f"name the organisation (org=) among: {[o['name'] for o in orgs]}" if pick or org is None
                                else f"no organisation {org!r}; yours: {[o['name'] for o in orgs]}")
        return _ok(s.post(f"/api/orgs/{pick[0]['id']}/projects", json={"name": name}), "creating the project")


def _project(s, project: str) -> dict:
    found = []
    for o in _ok(s.get("/api/orgs"), "listing organisations"):
        for p in _ok(s.get(f"/api/orgs/{o['id']}/projects"), "listing projects"):
            if project == p["id"] or project.lower() == p["name"].lower():
                found.append({**p, "org_name": o["name"]})
    if not found:
        raise DocumentError(f"no project {project!r} that you can use (collab_projects lists them)")
    if len(found) > 1:
        raise DocumentError(f"several projects are called {project!r}: give its id ({[p['id'] for p in found]})")
    return found[0]


@command(mutates=False, category="collab")
def collab_documents(project: str) -> dict:
    """The cloud documents in a project (project id or name): [{id, name, kind, seq, updated_at, ...}]. Open one
    with collab_open(id)."""
    with _server() as s:
        p = _project(s, project)
        return {"project": p, "documents": _ok(s.get(f"/api/projects/{p['id']}/documents"), "listing documents")}


# ---------------------------------------------------------------------- sharing
@command(category="collab")
def collab_enable(project: str, name: str | None = None) -> dict:
    """Turn live collaboration on for the active part: it becomes a cloud document in the given project (id or
    name; see collab_projects), which others open with collab_open and edit at the same time. From then on every
    change is sent to the server as you make it; changes from others appear in the model as they come. New
    features need the connection (their ids come from the server), undo takes back only your own changes, and a
    change that clashes with someone else's is refused and rolled back with the reason. name: the cloud
    document's name (default: the part's). Parts only for now. Returns the document id and status."""
    auth, client = _need()
    doc = APP.active_doc
    if getattr(doc, "kind", "part") != "part":
        raise DocumentError("only parts can be shared for now (assemblies come later)")
    if client.client_for(doc) is not None:
        raise DocumentError(f"{doc.name} is already a cloud document")
    url, tok = auth.server(), auth.token()
    with _server() as s:
        p = _project(s, project)
        content = client.plain(doc.to_dict())
        from openswx_collab_protocol import ops as O
        sink: dict[str, bytes] = {}
        wire = O.externalize(content, sink)
        if sink:
            missing = _ok(s.post(f"/api/projects/{p['id']}/blobs/missing", json=sorted(sink)), "uploading")
            for sha in missing:
                _ok(s.put(f"/api/projects/{p['id']}/blobs/{sha}", content=sink[sha]), "uploading")
        info = _ok(s.post(f"/api/projects/{p['id']}/documents", json={"name": name or doc.name, "content": wire}),
                   "creating the cloud document")
    c = client.attach(doc, info["id"], url, tok, info)
    c.blobs.update(sink)
    return {"document_id": info["id"], "name": info["name"], "project": p["name"], **_brief(c)}


def _brief(c) -> dict:
    st = c.status()
    return {k: st[k] for k in ("document", "seq", "role", "read_only", "read_only_reason", "state")} | \
        {"peers": [p["name"] for p in st["peers"]]}


@command(category="collab")
def collab_open(document_id: str) -> dict:
    """Open a cloud document (live collaboration) by its id (collab_documents lists them): it opens as a new part
    showing the server's current state, and stays in sync with everyone editing it. If it is already open it is
    made active. Returns its status (role, read_only and why, peers)."""
    auth, client = _need()
    c = client.CLIENTS.get(document_id)
    if c is not None and not c.closed:
        for n, d in APP.documents.items():
            if d is c.doc:
                APP.active = n
        return {"document_id": document_id, **_brief(c)}
    url, tok = auth.server(), auth.token()
    with _server() as s:
        info = _ok(s.get(f"/api/documents/{document_id}"), "opening the cloud document")
    if info.get("kind", "part") != "part":
        raise DocumentError("only parts can be opened for now (assemblies come later)")
    doc = APP.new_document(info["name"])
    key = APP.active
    try:
        c = client.attach(doc, document_id, url, tok, info)
    except Exception:
        APP.documents.pop(key, None)
        APP.active = next(iter(APP.documents), None)
        raise
    doc.dirty = False
    return {"document_id": document_id, "name": info["name"], **_brief(c)}


@command(category="collab")
def collab_disable() -> dict:
    """Stop live collaboration for the active document: it stops syncing and stays open as an ordinary local part
    (save it with save_part to keep it). The cloud document itself stays on the server for the others. Changes
    still waiting for the server are not sent (pending_dropped says how many)."""
    c = _client()
    pending = len(c.pending)
    c.close()
    return {"stopped": c.document_id, "document": c.doc.name, "pending_dropped": pending}


# ---------------------------------------------------------------------- working together
@command(mutates=False, category="collab")
def collab_lock(features: list[str]) -> dict:
    """Lock features or sketches of the active cloud document (by name) so nobody else can change them while you
    work on them, as opening a sketch or a feature's panel does. 'structure' locks the feature tree's shape
    (deleting and reordering). Locks last while you are connected (and 60 s after a dropped connection); free
    them with collab_unlock. Returns {granted: [names], denied: {name: who holds it}}."""
    c = _client()
    targets = {c.feature_target(n): n for n in features}
    res = c.lock(list(targets))
    return {"granted": [targets.get(t, t) for t in res["granted"]],
            "denied": {targets.get(t, t): (h.get("name") or "not lockable") for t, h in res["denied"].items()}}


@command(mutates=False, category="collab")
def collab_unlock(features: list[str]) -> dict:
    """Release locks taken with collab_lock (feature or sketch names, or 'structure')."""
    c = _client()
    targets = [c.feature_target(n) for n in features]
    c.unlock(targets)
    return {"released": features}


@command(mutates=False, category="collab")
def collab_history(limit: int = 50) -> dict:
    """The edit history of the active cloud document, newest first: [{seq, author: {name}, time, command, reverts,
    reverted_by, broke}], plus its named versions. Use a seq or version name with collab_restore."""
    c = _client()
    with _server(c.server) as s:
        hist = _ok(s.get(f"/api/documents/{c.document_id}/history", params={"limit": max(1, min(limit, 500))}),
                   "reading the history")
        versions = _ok(s.get(f"/api/documents/{c.document_id}/versions"), "reading the versions")
    return {"seq": c.seq, "history": hist, "versions": versions}


@command(mutates=False, category="collab")
def collab_name_version(name: str, note: str = "", seq: int | None = None) -> dict:
    """Name the current state of the active cloud document (or the state after change `seq`) as a version, e.g.
    'Rev A'. Named versions are what assemblies pin and what collab_restore can go back to."""
    c = _client()
    body: dict[str, Any] = {"name": name, "note": note}
    if seq is not None:
        body["seq"] = seq
    with _server(c.server) as s:
        return _ok(s.post(f"/api/documents/{c.document_id}/versions", json=body), "naming the version")


@command(category="collab")
def collab_restore(seq: int | None = None, version: str | None = None) -> dict:
    """Put the active cloud document back to an earlier state, for everyone: the state after change `seq`, or a
    named version. Nothing is lost: the restore is itself a new change in the history (and can be restored
    away). Refused while someone else holds a lock (your own locks do not stand in the way)."""
    c = _client()
    if (seq is None) == (version is None):
        raise DocumentError("give seq or version (one of them)")
    with _server(c.server) as s:
        r = s.post(f"/api/documents/{c.document_id}/restore", json={"seq": seq} if seq is not None else {"version": version})
    if r.status_code == 409:
        auth, _ = _need()
        try:
            d = r.json().get("detail") or {}
            who = sorted({h.get("name", "") for h in (d.get("conflicts") or {}).values() if isinstance(h, dict)})
        except (ValueError, AttributeError):
            d, who = {}, []
        raise DocumentError(f"cannot restore: {d.get('message') or auth.error_text(r)}" + (f" ({', '.join(who)})" if who else ""))
    new = _ok(r, "restoring")["seq"]
    from .client import wait_seconds
    end = time.time() + max(wait_seconds(), 5.0)
    with c.cond:
        while (c.seq or 0) < new and c.connected and time.time() < end:
            c.cond.wait(0.2)
    c.reload_view_locked()
    return {"seq": new, **APP.active_doc.summary()}


@command(mutates=False, category="collab")
def collab_presence(pointer: dict | None = None, selection: list[str] | None = None, tool: str | None = None,
                    camera: dict | None = None, draft: dict | None = None, clear_draft: bool = False) -> dict:
    """Tell the others in the active cloud document where you are: pointer ({point: [x, y, z], face: persistent
    face name}), selection (persistent names), tool (the command or panel in use), camera (for follow-my-view);
    draft: unfinished work to preview on their screens (sketch entities, panel parameters), clear_draft to remove
    it. Relayed as given, never stored. Returns {sent}."""
    c = _client()
    data = {k: v for k, v in (("pointer", pointer), ("selection", selection), ("tool", tool), ("camera", camera))
            if v is not None}
    sent = c.presence(data) if data else True
    if draft is not None or clear_draft:
        sent = c.draft(None if clear_draft else draft) and sent
    return {"sent": bool(sent)}
