from __future__ import annotations

import contextlib
import re
import time
from typing import Any, Literal

from openswx.core.document import DocumentError
from openswx.session import APP, command, unlocked
from openswx.collab import MISSING, available

__all__ = ["collab_sign_in", "collab_server_info", "collab_sign_out", "collab_status", "collab_projects", "collab_create_project",
           "collab_documents", "collab_enable", "collab_open", "collab_disable", "collab_lock", "collab_unlock",
           "collab_history", "collab_name_version", "collab_restore", "collab_presence", "collab_new_part",
           "collab_create_org", "collab_members", "collab_invite", "collab_cancel_invite", "collab_set_role",
           "collab_remove_member", "collab_accept_invite", "collab_project_members", "collab_set_project_role",
           "collab_remove_project_member"]


def _need():
    if not available():
        raise DocumentError(MISSING)
    from openswx.collab import auth, client
    return auth, client


@contextlib.contextmanager
def _server(url: str | None = None, unlock: bool = True):
    """An HTTP client signed in to the saved server (kept open between commands); network trouble becomes a plain
    error. unlock: other commands run while this waits for the server (the block must only talk to the server,
    not read or change open documents; collab_enable, which sends a document, keeps the lock)."""
    import httpx
    auth, _ = _need()
    url = url or auth.server()
    if not url or not auth.token():
        raise DocumentError("not signed in to a collaboration server: run collab_sign_in first")
    try:
        with (unlocked() if unlock else contextlib.nullcontext()), auth.shared(url) as c:
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


def _client(document: str | None = None):
    _, client = _need()
    if document:  # (a cloud document by id, active or not)
        c = client.CLIENTS.get(document)
        if c is None or c.closed:
            raise DocumentError(f"no cloud document {document!r} is open")
        return c
    c = client.client_for(APP.active_doc)
    if c is None:
        raise DocumentError("the active document is not a cloud document: share it with collab_enable, or open one "
                            "with collab_open")
    return c


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


def _auth_call(fn, *args, **kw):
    """Run an auth call, saying network and server refusals plainly."""
    auth, _ = _need()
    try:
        return fn(*args, **kw)
    except auth.AuthError as exc:
        raise DocumentError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - network trouble
        if isinstance(exc, (DocumentError, ValueError)):
            raise
        raise DocumentError(f"cannot reach the collaboration server: {exc}") from exc


@command(mutates=False, category="collab")
def collab_sign_in_methods() -> dict:
    """The ways into the signed-in collaboration account: which of Google and GitHub are linked (providers) and
    whether it has a password (has_password)."""
    auth, _ = _need()
    return _auth_call(auth.providers)


@command(mutates=False, category="collab")
def collab_link_provider(provider: Literal["google", "github"], password: str | None = None) -> dict:
    """Add Google or GitHub sign-in to the signed-in collaboration account, through the system browser (the
    command waits up to 5 minutes for it). password: the account's password (needed if it has one; an account
    without one needs a sign-in from the last ten minutes instead). Afterwards that provider signs in to this
    account. Returns {linked, user}."""
    auth, _ = _need()
    with unlocked():  # (the user is busy in the browser: openswx stays usable meanwhile)
        return _auth_call(auth.link_provider, provider, password)


@command(mutates=False, category="collab")
def collab_unlink_provider(provider: Literal["google", "github"], password: str | None = None) -> dict:
    """Stop Google or GitHub signing in to the collaboration account. password: the account's password if it
    has one. The last way into an account cannot be removed. Returns the remaining sign-in methods."""
    auth, _ = _need()
    return _auth_call(auth.unlink_provider, provider, password)


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
        if _ID.fullmatch(project):  # (an id: straight to its documents, no listing of every organisation)
            r = s.get(f"/api/projects/{project}/documents")
            if r.status_code != 404:
                return {"project": {"id": project}, "documents": _ok(r, "listing documents")}
        p = _project(s, project)
        return {"project": p, "documents": _ok(s.get(f"/api/projects/{p['id']}/documents"), "listing documents")}


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
    with _server(unlock=False) as s:  # (the document is read and sent: nobody may change it meanwhile)
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


_ID = re.compile(r"[0-9a-f]{32}")  # (the server's ids)


def _offline(url: str | None) -> DocumentError:
    return DocumentError(f"cannot reach {url or 'the collaboration server'}; cloud parts open only while connected")


@contextlib.contextmanager
def _online(url: str | None):
    """Network trouble inside becomes the plain 'cannot reach; cloud parts open only while connected'."""
    import httpx
    try:
        yield
    except DocumentError as exc:
        if isinstance(exc.__cause__, httpx.HTTPError):
            raise _offline(url) from exc
        raise


def _open(document_id: str, info: dict | None = None) -> dict:
    """Open a cloud document as a new part, or fail leaving nothing behind (a cloud part never exists offline)."""
    auth, client = _need()
    document_id = _id(document_id, "document")
    url, tok = auth.server(), auth.token()
    if info is None:
        with _online(url), _server() as s:
            info = _ok(s.get(f"/api/documents/{document_id}"), "opening the cloud document")
    if info.get("kind", "part") != "part":
        raise DocumentError("only parts can be opened for now (assemblies come later)")
    before = APP.active
    doc = APP.new_document(info["name"])
    key = APP.active
    try:
        c = client.attach(doc, document_id, url, tok, info)
    except Exception:
        APP.documents.pop(key, None)
        APP.active = before if before in APP.documents else next(iter(APP.documents), None)
        raise
    doc.dirty = False
    return {"document_id": document_id, "name": info["name"], **_brief(c)}


@command(category="collab")
def collab_open(document_id: str) -> dict:
    """Open a cloud document (live collaboration) by its id (collab_documents lists them): it opens as a new part
    showing the server's current state, and stays in sync with everyone editing it. If it is already open it is
    made active. A cloud part opens only while the server can be reached: otherwise this fails and opens nothing.
    Returns its status (role, read_only and why, peers)."""
    _, client = _need()
    c = client.CLIENTS.get(document_id)
    if c is not None and not c.closed:
        for n, d in APP.documents.items():
            if d is c.doc:
                APP.active = n
        return {"document_id": document_id, **_brief(c)}
    return _open(document_id)


@command(category="collab")
def collab_new_part(project: str, name: str = "Part") -> dict:
    """Create a new, empty part straight in the cloud, in a project (id or name; see collab_projects), and open
    it: everyone in the project can open it and edit it with you, at any time, whether or not you are online.
    Nothing is made if the server cannot be reached. Returns the document id and status."""
    from openswx.core.document import PartDocument
    auth, client = _need()
    name = (name or "").strip()[:200] or "Part"
    url = auth.server()
    with _online(url), _server() as s:
        p = _project(s, project)
        info = _ok(s.post(f"/api/projects/{p['id']}/documents",
                          json={"name": name, "content": client.plain(PartDocument(name).to_dict())}),
                   "creating the cloud part")
    return {**_open(info["id"], info), "project": p["name"]}


def _org(s, org: str | None) -> dict:
    orgs = _ok(s.get("/api/orgs"), "listing organisations")
    pick = [o for o in orgs if org is None or org == o["id"] or org.lower() == o["name"].lower()]
    if len(pick) == 1:
        return pick[0]
    if not pick and org is not None:
        raise DocumentError(f"no organisation {org!r} that you are in; yours: {[o['name'] for o in orgs]}")
    raise DocumentError(f"name the organisation (org=) among: {[o['name'] for o in orgs]}" if orgs
                        else "you are not in any organisation on this server yet")


def _id(v: str, what: str) -> str:
    """An id from the server, as it may go into an address: letters, digits, '-' and '_'."""
    if not isinstance(v, str) or not v or len(v) > 100 or not all(ch.isalnum() or ch in "-_" for ch in v):
        raise DocumentError(f"not a valid {what} id: {v!r}"[:200])
    return v


@command(mutates=False, category="collab")
def collab_create_org(name: str) -> dict:
    """Create an organisation on the collaboration server (you become its owner). Organisations hold projects
    and the people who work in them. Servers may allow only their admin to do this. Returns {id, name, role}."""
    name = name.strip()
    if not name:
        raise DocumentError("give the organisation a name")
    with _server() as s:
        return _ok(s.post("/api/orgs", json={"name": name[:200]}), "creating the organisation")


@command(mutates=False, category="collab")
def collab_members(org: str | None = None) -> dict:
    """The people in an organisation (id or name; default: your only one) with their roles (owner, admin,
    member), and, for owners and admins, the invitations not yet accepted. Returns {org, members, invites}."""
    with _server() as s:
        o = _org(s, org)
        members = _ok(s.get(f"/api/orgs/{o['id']}/members"), "listing the members")
        invites = []
        if o.get("role") in ("owner", "admin"):
            invites = _ok(s.get(f"/api/orgs/{o['id']}/invites"), "listing the invitations")
    return {"org": o, "members": members, "invites": invites}


@command(mutates=False, category="collab")
def collab_invite(org: str | None = None, email: str | None = None, role: Literal["admin", "member"] = "member",
                  project: str | None = None, project_role: Literal["owner", "editor", "viewer"] = "editor",
                  days: int = 7) -> dict:
    """Invite someone to an organisation (owners and admins). email: their address (the server emails them the
    link if it can; without an address, anyone with the link can join). project: also give them a role in this
    project (id or name). Returns the invite with its link (url) and code, to send them yourself: they are shown
    only now. They join with collab_accept_invite or by opening the link."""
    body: dict[str, Any] = {"role": role, "days": max(1, min(int(days), 90))}
    if email and email.strip():
        body["email"] = email.strip()[:320]
    with _server() as s:
        o = _org(s, org)
        if project:
            p = _project(s, project)
            if p["org_id"] != o["id"]:
                raise DocumentError(f"project {p['name']!r} is not in {o['name']}")
            body["projects"] = [{"project_id": p["id"], "role": project_role}]
        inv = _ok(s.post(f"/api/orgs/{o['id']}/invites", json=body), "inviting")
    return {"org": o["name"], **{k: inv.get(k) for k in ("id", "email", "role", "projects", "expires_at", "url", "emailed")},
            "code": inv.get("token")}


@command(mutates=False, category="collab")
def collab_cancel_invite(invite: str, org: str | None = None) -> dict:
    """Withdraw an invitation that has not been accepted (its id, from collab_members)."""
    invite = _id(invite, "invitation")
    with _server() as s:
        o = _org(s, org)
        _ok(s.delete(f"/api/orgs/{o['id']}/invites/{invite}"), "withdrawing the invitation")
    return {"cancelled": invite}


@command(mutates=False, category="collab")
def collab_set_role(user: str, role: Literal["owner", "admin", "member"], org: str | None = None) -> dict:
    """Change someone's role in an organisation (user: their id, from collab_members). Admins manage members and
    invitations; only an owner can make or unmake owners, and an organisation keeps at least one owner."""
    user = _id(user, "user")
    with _server() as s:
        o = _org(s, org)
        return _ok(s.patch(f"/api/orgs/{o['id']}/members/{user}", json={"role": role}), "changing the role")


@command(mutates=False, category="collab")
def collab_remove_member(user: str, org: str | None = None) -> dict:
    """Remove someone from an organisation (user: their id; your own id leaves it). They lose its projects and
    their open cloud documents at once; their past work stays."""
    user = _id(user, "user")
    with _server() as s:
        o = _org(s, org)
        _ok(s.delete(f"/api/orgs/{o['id']}/members/{user}"), "removing the member")
    return {"removed": user, "org": o["name"]}


@command(mutates=False, category="collab")
def collab_accept_invite(code: str) -> dict:
    """Join an organisation with the code (or the whole link) from an invitation, as the signed-in account.
    Returns {org, role}."""
    code = code.strip().rstrip("/").rsplit("/", 1)[-1]
    if not code or len(code) > 200:
        raise DocumentError("give the invitation code or link")
    with _server() as s:
        return _ok(s.post("/api/invites/accept", json={"token": code}), "accepting the invitation")


@command(mutates=False, category="collab")
def collab_project_members(project: str) -> dict:
    """Who has a role in a project (id or name): owner, editor or viewer. Returns {project, members}."""
    with _server() as s:
        p = _project(s, project)
        return {"project": p, "members": _ok(s.get(f"/api/projects/{p['id']}/members"), "listing the project's members")}


@command(mutates=False, category="collab")
def collab_set_project_role(project: str, user: str, role: Literal["owner", "editor", "viewer"]) -> dict:
    """Give someone in the organisation a role in a project, or change it (project owners): owner, editor (can
    change its documents) or viewer (can only look). user: their id."""
    user = _id(user, "user")
    with _server() as s:
        p = _project(s, project)
        return _ok(s.put(f"/api/projects/{p['id']}/members/{user}", json={"role": role}), "setting the project role")


@command(mutates=False, category="collab")
def collab_remove_project_member(project: str, user: str) -> dict:
    """Take someone's role in a project away (project owners). user: their id."""
    user = _id(user, "user")
    with _server() as s:
        p = _project(s, project)
        _ok(s.delete(f"/api/projects/{p['id']}/members/{user}"), "removing them from the project")
    return {"removed": user, "project": p["name"]}


@command(category="collab")
def collab_disable() -> dict:
    """Stop live collaboration for the active document: it stops syncing and stays open as an ordinary local part
    (save it with save_part to keep it). The cloud document itself stays on the server for the others. Changes
    still waiting for the server are not sent (pending_dropped says how many)."""
    c = _client()
    pending = len(c.pending)
    c.close()
    return {"stopped": c.document_id, "document": c.doc.name, "pending_dropped": pending}


@command(mutates=False, category="collab")
def collab_lock(features: list[str], document: str | None = None, owner: str | None = None) -> dict:
    """Lock features or sketches of the active cloud document (by name) so nobody else can change them while you
    work on them, as opening a sketch or a feature's panel does. 'structure' locks the feature tree's shape
    (deleting and reordering). Locks last while you are connected (and 60 s after a dropped connection); free
    them with collab_unlock. document: a cloud document id instead of the active one. owner: who in this app took
    them (the UI passes its page's id, so the locks are freed when that page goes away). Returns {granted:
    [names], denied: {name: who holds it}}."""
    c = _client(document)
    targets = {c.feature_target(n): n for n in features}
    res = c.lock(list(targets))
    if owner:
        _need()[1].own(owner, c.document_id, res["granted"])
    return {"granted": [targets.get(t, t) for t in res["granted"]],
            "denied": {targets.get(t, t): (h.get("name") or "not lockable") for t, h in res["denied"].items()}}


@command(mutates=False, category="collab")
def collab_unlock(features: list[str], document: str | None = None, owner: str | None = None) -> dict:
    """Release locks taken with collab_lock (feature or sketch names, or 'structure'). document: a cloud document id
    instead of the active one; owner: as given to collab_lock."""
    c = _client(document)
    targets = [c.feature_target(n) for n in features]
    c.unlock(targets)
    if owner:
        _need()[1].disown(owner, c.document_id, targets)
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
    from openswx.collab.client import wait_seconds
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
    return send_presence(pointer=pointer, selection=selection, tool=tool, camera=camera, draft=draft,
                         clear_draft=clear_draft)


PRESENCE_ARGS = {"pointer": dict, "selection": list, "tool": str, "camera": dict, "draft": dict, "clear_draft": bool}


def send_presence(**args) -> dict:
    """collab_presence without the command lock (the UI sends it about 20 times a second; it reads no document,
    so it never waits behind a rebuild). Arguments as collab_presence's, checked here."""
    for k, v in args.items():
        if k not in PRESENCE_ARGS:
            raise DocumentError(f"collab_presence has no argument {k!r}")
        if v is not None and not isinstance(v, PRESENCE_ARGS[k]):
            raise DocumentError(f"collab_presence: {k} must be a {PRESENCE_ARGS[k].__name__}")
    c = _client()
    data = {k: args[k] for k in ("pointer", "selection", "tool", "camera") if args.get(k) is not None}
    sent = c.presence(data) if data else True
    if args.get("draft") is not None or args.get("clear_draft"):
        sent = c.draft(None if args.get("clear_draft") else args["draft"]) and sent
    return {"sent": bool(sent)}
