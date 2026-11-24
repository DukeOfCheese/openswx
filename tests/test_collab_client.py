"""The collaboration client without a server: what it sends for a command, how it takes the server's answers
(accepted, rejected, someone else's change, undo), the feature id pool, sign-in and local backups.

A stand-in socket answers the way the server would (docs/protocol.md in openswx-collab). The same scenarios
against the real server, with two openswx processes, are in test_collab_live.py."""
import json
import os
import urllib.parse
import urllib.request

import pytest

pytest.importorskip("openswx_collab_protocol")
pytest.importorskip("websockets")

from openswx_collab_protocol import ops as O  # noqa: E402

from openswx import session as S  # noqa: E402
from openswx.collab import auth, backup  # noqa: E402
from openswx.collab import client as C  # noqa: E402

W, H = 40.0, 20.0  # the block: 40 x 20 mm rectangle extruded `depth`


class FakeSocket:
    """Records what the client sends; `answer(message)` may return the server's reply (handled at once)."""

    def __init__(self, c, answer=None):
        self.c, self.answer, self.sent = c, answer, []

    def send(self, text):
        m = json.loads(text)
        self.sent.append(m)
        reply = self.answer(m) if self.answer else None
        for r in reply if isinstance(reply, list) else [reply] if reply else []:
            self.c._handle(r)

    def close(self):
        pass

    def of(self, kind):
        return [m for m in self.sent if m["type"] == kind]


def accept(c):
    def answer(m):
        if m["type"] == "change":
            return {"type": "change.accepted", "change_id": m["change_id"], "seq": c.seq + 1}
        if m["type"] == "ids.reserve":
            n = 100 + len([x for x in c.ws.sent if x["type"] == "ids.reserve"]) * 10
            return {"type": "ids.reserved", "request_id": m["request_id"],
                    "ids": [f"f{n + i}" for i in range(m["features"])],
                    "names": [f"{p}{n + i}" for i, p in enumerate(m["names"])]}
    return answer


@pytest.fixture
def cloud():
    """The active part (a 40 x 20 x 10 block: Sketch1 f1, Boss-Extrude1 f2) as a cloud document at seq 3."""
    S.call("new_part", {"name": "Block"})
    sk = S.call("create_sketch", {"plane": "Top"})["sketch"]
    S.call("sketch_rectangle", {"sketch": sk, "corner1": [0, 0], "corner2": [W, H]})
    S.call("extrude", {"sketch": sk, "depth": 10})
    doc = S.APP.doc
    c = C.DocClient(doc, "doc1", "http://127.0.0.1:9", "tok", {"project_id": "p1", "name": "Block"})
    c.confirmed = {k: v for k, v in C.plain(doc.to_dict()).items() if k not in O.LOCAL_ONLY}
    c.seq, c.gen, c.view_gen, c.view_seq = 3, 1, 1, 3
    c.you, c.role, c.state = {"client_id": "u1.me", "name": "Me"}, "editor", "online"
    c.ws = FakeSocket(c, accept(c))
    c.pool_ids, c.pool_names = ["f50", "f51"], {"Sketch": ["Sketch8"]}
    doc._collab = doc.allocator = c
    C.CLIENTS["doc1"] = c
    S.add_command_hook(C.HOOK)
    yield c
    c.close()
    assert S.COMMAND_HOOKS == []


def volume():
    return S.call("get_mass_properties")["volume"]


def test_a_command_becomes_one_change_and_the_answer_moves_confirmed_on(cloud):
    S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 25}})
    (m,) = cloud.ws.of("change")
    assert m["base_seq"] == 3 and m["command"] == "edit_feature"
    assert m["ops"] == [{"op": "feature.update", "id": "f2", "set": [[["params", "depth"], 25]], "unset": []}]
    assert cloud.seq == 4 and cloud.mine == [4] and not cloud.pending
    assert cloud.confirmed["features"][1]["params"]["depth"] == 25
    assert O.fingerprint(C.plain(S.APP.doc.to_dict())) == O.fingerprint(cloud.confirmed)
    # a batch is one change; a query or a failed command is none
    S.call("batch", {"commands": [{"name": "edit_feature", "args": {"name": "Boss-Extrude1", "params": {"depth": 12}}},
                                  {"name": "suppress_feature", "args": {"name": "Boss-Extrude1", "suppressed": False}},
                                  {"name": "edit_feature", "args": {"name": "Boss-Extrude1", "params": {"depth": 14}}}]})
    S.call("get_model")
    with pytest.raises(S.CommandError):
        S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": -1e9, "end_condition": "nonsense"}})
    changes = cloud.ws.of("change")
    assert len(changes) == 2 and changes[1]["command"] == "batch" and changes[1]["base_seq"] == 4
    assert changes[1]["ops"][0]["set"] == [[["params", "depth"], 14]]
    assert volume() == pytest.approx(W * H * 14, rel=1e-6)


def test_new_features_take_ids_and_names_from_the_server(cloud):
    sk = S.call("create_sketch", {"plane": "Front"})["sketch"]
    assert sk == "Sketch8" and S.APP.doc.find(sk)["id"] == "f50"  # (from the pool: no round trip)
    S.call("sketch_circle", {"sketch": sk, "center": [10, 10], "radius": 3})
    cut = S.call("cut_extrude", {"sketch": sk, "depth": 5})["feature"]  # (no Cut-Extrude name in the pool: asked)
    reserve = cloud.ws.of("ids.reserve")
    assert any(r["names"][:1] == ["Cut-Extrude"] and r["request_id"].startswith("now:") for r in reserve)
    assert cut.startswith("Cut-Extrude") and S.APP.doc.find(cut)["id"] == "f51"
    adds = [o for m in cloud.ws.of("change") for o in m["ops"] if o["op"] == "feature.add"]
    assert [(o["feature"]["id"], o["feature"]["name"]) for o in adds] == [("f50", "Sketch8"), ("f51", cut)]


def test_without_a_connection_features_cannot_be_created_but_edits_queue(cloud):
    cloud.pool_ids, cloud.pool_names = [], {}
    cloud.state = "offline"
    n = len(S.APP.doc.features)
    with pytest.raises(S.CommandError, match="needs a connection"):
        S.call("create_sketch", {"plane": "Front"})
    assert len(S.APP.doc.features) == n
    S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 30}})  # (an edit goes in the queue)
    assert len(cloud.pending) == 1 and not cloud.ws.of("change")
    (cid, p), = cloud.pending.items()
    # back online: the welcome brings nothing new; the change goes out again with its original id and base
    cloud._welcome({"type": "welcome", "seq": 3, "you": cloud.you, "role": "editor", "peers": [], "locks": {},
                    "broken": {}, "changes": []})
    (m,) = cloud.ws.of("change")
    assert (m["change_id"], m["base_seq"]) == (cid, 3) and cloud.seq == 4 and not cloud.pending


def test_a_failed_feature_gives_its_reserved_id_back(cloud):
    with pytest.raises(S.CommandError):
        S.call("fillet", {"edges": ["Edge0"], "radius": 50})  # (far too big for a 10 mm block)
    assert cloud.pool_ids[:2] == ["f50", "f51"] and not cloud.ws.of("change")
    assert cloud.pool_names["Fillet"][0].startswith("Fillet")  # (the name it asked for, unused, kept for later)


def test_a_rejected_change_rolls_back_and_says_who(cloud):
    def answer(m):
        if m["type"] == "change":
            return [  # Ada's edit of the same extrude came first, then ours is refused
                {"type": "change", "seq": 4, "author": "u2", "author_name": "Ada", "client_id": "u2.x",
                 "change_id": "a1", "ops": [{"op": "feature.update", "id": "f2", "set": [[["params", "depth"], 33]],
                                             "unset": []}], "command": "edit_feature"},
                {"type": "change.rejected", "change_id": m["change_id"], "code": "stale",
                 "message": "someone changed the same thing while you were editing",
                 "conflicts": {"f:f2": {"seq": 4, "client_id": "u2.x", "user_id": "u2", "name": "Ada"}}}]
    cloud.ws.answer = answer
    S.APP.doc.dirty = False  # (as a cloud document is, with nothing of its own to save)
    with pytest.raises(S.CommandError, match="Ada changed Boss-Extrude1"):
        S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 25}})
    assert S.APP.doc.find("Boss-Extrude1")["params"]["depth"] == 33  # (the server's version, not ours)
    assert volume() == pytest.approx(W * H * 33, rel=1e-6)
    assert not S.APP.doc.dirty  # (the refused change left nothing to save)
    (conflict,) = C.status()["documents"][0]["conflicts"]
    assert conflict["code"] == "stale" and conflict["who"] == ["Ada"] and conflict["features"] == ["Boss-Extrude1"]


def test_changes_from_others_reach_the_open_document_between_commands(cloud):
    cloud._handle({"type": "change", "seq": 4, "author": "u2", "author_name": "Ada", "client_id": "u2.x",
                   "change_id": "a1", "command": "edit_feature",
                   "ops": [{"op": "feature.update", "id": "f2", "set": [[["params", "depth"], 7]], "unset": []}]})
    assert cloud.seq == 4 and S.APP.doc.find("Boss-Extrude1")["params"]["depth"] == 10  # (not yet shown)
    with S.APP.lock:
        assert cloud.reload_view_locked()
    assert volume() == pytest.approx(W * H * 7, rel=1e-6)
    S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 8}})
    assert cloud.ws.of("change")[-1]["base_seq"] == 4  # (made on top of what the screen showed)
    assert C.status()["documents"][0]["recent"][0]["author_name"] == "Ada"


def test_undo_takes_back_only_your_own_last_change(cloud):
    S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 25}})
    inverse = [{"op": "feature.update", "id": "f2", "set": [[["params", "depth"], 10]], "unset": []}]

    def answer(m):
        if m["type"] == "change.revert":
            assert (m["seq"], m["mode"]) == (4, "undo")
            return {"type": "change.revert.result", "request_id": m["request_id"], "ok": True, "seq": 5, "ops": inverse}
    cloud.ws.answer = answer
    out = S.call("undo")
    assert out["undone"] == 4 and cloud.seq == 5 and cloud.redo_stack == [5]
    assert volume() == pytest.approx(W * H * 10, rel=1e-6)
    assert len(S.APP.doc._undo) > 0  # (the document's own undo stack is not what undo used)

    def refuse(m):
        if m["type"] == "change.revert":
            return {"type": "change.revert.result", "request_id": m["request_id"], "ok": False, "code": "changed_since",
                    "message": "cannot undo: Ada changed the same thing afterwards",
                    "conflicts": {"f:f2": {"seq": 6, "client_id": "u2.x", "user_id": "u2", "name": "Ada"}}}
    cloud.ws.answer = refuse
    with pytest.raises(S.CommandError, match="Ada changed Boss-Extrude1 after you"):
        S.call("redo")
    cloud.mine.clear()
    with pytest.raises(S.CommandError, match="nothing of yours to undo"):
        S.call("undo")


def test_reconnect_catch_up_recognises_our_own_changes(cloud):
    cloud.state = "offline"
    S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 30}})
    (cid, p), = cloud.pending.items()
    # the change got through just before the connection dropped: the catch-up has it, and someone else's after it
    other = [{"op": "feature.update", "id": "f2", "set": [[["params", "draft"], 0]], "unset": []}]
    cloud._welcome({"type": "welcome", "seq": 5, "you": cloud.you, "role": "editor", "peers": [], "locks": {},
                    "broken": {}, "changes": [
                        {"type": "change", "seq": 4, "author": "u1", "client_id": "u1.me", "change_id": cid,
                         "ops": p["ops"]},
                        {"type": "change", "seq": 5, "author": "u2", "author_name": "Ada", "client_id": "u2.x",
                         "change_id": "a9", "ops": other}]})
    assert not cloud.pending and cloud.mine == [4] and cloud.seq == 5 and not cloud.ws.of("change")
    assert cloud.confirmed["features"][1]["params"]["depth"] == 30


def test_read_only_documents_refuse_changes(cloud):
    cloud.read_only, cloud.read_only_reason = True, "you have view access to this project"
    with pytest.raises(S.CommandError, match="read-only"):
        S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 25}})
    assert S.APP.doc.find("Boss-Extrude1")["params"]["depth"] == 10 and not cloud.ws.of("change")


def test_rebuild_reports_and_locks(cloud):
    with S.APP.lock:
        cloud._report_locked()
    (r,) = cloud.ws.of("rebuild.report")
    assert r == {"type": "rebuild.report", "seq": 3, "errors": {}}
    cloud.ws.answer = lambda m: {"type": "lock.result", "request_id": m["request_id"], "granted": ["f:f2"],
                                 "denied": {"f:f1": {"client_id": "u2.x", "user_id": "u2", "name": "Ada"}}} \
        if m["type"] == "lock.acquire" else None
    got = S.call("collab_lock", {"features": ["Boss-Extrude1", "Sketch1"]})
    assert got == {"granted": ["Boss-Extrude1"], "denied": {"Sketch1": "Ada"}}
    S.call("collab_unlock", {"features": ["Boss-Extrude1"]})
    assert cloud.ws.of("lock.release")[-1]["targets"] == ["f:f2"]
    S.call("collab_presence", {"pointer": {"point": [1, 2, 3]}, "tool": "fillet", "draft": {"radius": 2}})
    assert cloud.ws.of("presence")[-1]["data"] == {"pointer": {"point": [1, 2, 3]}, "tool": "fillet"}
    assert cloud.ws.of("draft")[-1]["data"] == {"radius": 2}


def test_backups_keep_the_newest(cloud, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    monkeypatch.setenv("OPENSWX_COLLAB_BACKUPS", "2")
    monkeypatch.setattr(backup.time, "strftime", lambda fmt: f"2026{cloud.seq:04d}")
    paths = [backup.save(cloud)]
    assert backup.save(cloud) is None  # (nothing new)
    for seq in (4, 5):
        cloud.seq = seq
        paths.append(backup.save(cloud))
    folder = tmp_path / "cloud-backups" / "127.0.0.1_9" / "doc1"
    assert sorted(p.name for p in folder.iterdir()) == ["20260004-seq4.oswprt", "20260005-seq5.oswprt"]
    S.call("open_part", {"path": paths[-1]})
    assert volume() == pytest.approx(W * H * 10, rel=1e-6)
    monkeypatch.setenv("OPENSWX_AUTOSAVE", "off")
    assert backup._state["thread"] is not None or backup.start() is False


def test_password_sign_in_keeps_the_server_and_token(monkeypatch, tmp_path):
    import httpx
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))

    def server(request):
        if request.url.path == "/api/auth/login":
            body = json.loads(request.content)
            if body == {"email": "ada@example.com", "password": "correct horse"}:
                return httpx.Response(200, json={"token": "t0k3n", "user": {"name": "Ada"}})
            return httpx.Response(401, json={"detail": "wrong e-mail address or password"})
        return httpx.Response(204)
    mock = httpx.MockTransport(server)
    with pytest.raises(auth.AuthError, match="wrong e-mail"):
        auth.sign_in_password("collab.example.com", "ada@example.com", "nope", transport=mock)
    got = auth.sign_in_password("collab.example.com", "ada@example.com", "correct horse", transport=mock)
    assert got == {"server": "https://collab.example.com", "user": {"name": "Ada"}}
    assert auth.token() == "t0k3n" and auth.server() == "https://collab.example.com" and len(auth.client_id()) == 32
    if os.name == "posix":
        assert (tmp_path / "collab" / "token").stat().st_mode & 0o077 == 0
    assert auth.sign_out(transport=mock) and auth.token() is None and auth.client_id()


def test_browser_sign_in_uses_a_loopback_address_and_pkce(monkeypatch, tmp_path):
    import hashlib

    import httpx
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    seen = {}

    def browser(url):  # the provider and the server, as far as the app can tell: back to the loopback with a code
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        seen.update(q, path=urllib.parse.urlsplit(url).path)
        urllib.request.urlopen(q["redirect_uri"] + "?code=abc123", timeout=10).read()

    def server(request):
        body = json.loads(request.content)
        ok = body["code"] == "abc123" and hashlib.sha256(body["code_verifier"].encode()).hexdigest() == seen["code_challenge"]
        return httpx.Response(200, json={"token": "gh-token", "user": {"name": "Ada"}}) if ok else httpx.Response(400)

    got = auth.sign_in_browser("http://127.0.0.1:8787", "github", open_browser=browser, timeout=20,
                               transport=httpx.MockTransport(server))
    assert seen["path"] == "/api/auth/oauth/github/start" and seen["redirect_uri"].startswith("http://127.0.0.1:")
    assert len(seen["code_challenge"]) == 64 and got["user"]["name"] == "Ada" and auth.token() == "gh-token"

    def refused(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        urllib.request.urlopen(q["redirect_uri"] + "?error=invite_required", timeout=10).read()
    with pytest.raises(auth.AuthError, match="needs an invitation"):
        auth.sign_in_browser("http://127.0.0.1:8787", "google", open_browser=refused, timeout=20)


def test_a_preview_does_not_look_like_the_document_was_closed(cloud):
    """A preview (a feature panel's ghost) puts a throw-away copy in the document's place while it runs: syncing
    must not take that for the document having been closed."""
    import threading
    import time
    t = threading.Thread(target=cloud._viewer, daemon=True)
    cloud._threads.append(t)
    t.start()
    key, real = S.APP.active, S.APP.doc
    with S.APP.lock:  # (as S.preview holds it, with the copy in place, for as long as the rebuild takes)
        S.APP.documents[key] = type(real)(real.name)
        try:
            time.sleep(2.8)
        finally:
            S.APP.documents[key] = real
    time.sleep(0.3)
    assert not cloud.closed and C.client_for(real) is cloud


def test_the_ui_reads_collaboration_state_over_rest(cloud):
    from fastapi.testclient import TestClient

    from openswx.web_server import create_app
    cloud.peers = {"u2.x": {"client_id": "u2.x", "user_id": "u2", "name": "Ada", "colour": "#e33",
                            "presence": {"tool": "fillet"}, "draft": None}}
    cloud.locks = {"f:f2": {"client_id": "u2.x", "user_id": "u2", "name": "Ada", "detached": False}}
    with TestClient(create_app(mount_mcp=False, token=None), base_url="http://127.0.0.1:8765") as http:
        got = http.get("/api/collab").json()
    (d,) = got["documents"]
    assert got["revision"] == C.REVISION[0] and d["document_id"] == "doc1" and d["seq"] == 3
    assert d["peers"][0]["presence"] == {"tool": "fillet"}
    assert d["locks"]["f:f2"]["target"] == "Boss-Extrude1" and d["locks"]["f:f2"]["mine"] is False


# ---------------------------------------------------------------------- regressions (review of the first version)
def block_cloud(name: str, did: str):
    """Another cloud document (a 40 x 20 x 10 block) open beside the fixture's; closed by the caller."""
    S.call("new_part", {"name": name})
    sk = S.call("create_sketch", {"plane": "Top"})["sketch"]
    S.call("sketch_rectangle", {"sketch": sk, "corner1": [0, 0], "corner2": [W, H]})
    S.call("extrude", {"sketch": sk, "depth": 10})
    doc = S.APP.doc
    c = C.DocClient(doc, did, "http://127.0.0.1:9", "tok", {"project_id": "p1", "name": name})
    c.confirmed = {k: v for k, v in C.plain(doc.to_dict()).items() if k not in O.LOCAL_ONLY}
    c.seq, c.gen, c.view_gen, c.view_seq = 3, 1, 1, 3
    c.you, c.role, c.state = {"client_id": "u1.me", "name": "Me"}, "editor", "online"
    c.ws = FakeSocket(c, accept(c))
    doc._collab = doc.allocator = c
    C.CLIENTS[did] = c
    return c


def depth(c):
    return c.doc.find("Boss-Extrude1")["params"]["depth"], c.confirmed["features"][1]["params"]["depth"]


def test_a_batch_that_switches_to_a_cloud_document_sends_its_edit(cloud):
    name = S.APP.active
    S.call("new_part", {"name": "Local"})  # (the batch starts on an ordinary part)
    S.call("batch", {"commands": [{"name": "activate_document", "args": {"name": name}},
                                  {"name": "edit_feature", "args": {"name": "Boss-Extrude1", "params": {"depth": 33}}}]})
    (m,) = cloud.ws.of("change")
    assert m["command"] == "batch" and depth(cloud) == (33, 33)
    other = block_cloud("Other", "doc2")  # a batch that starts on one cloud document and edits another
    try:
        S.call("activate_document", {"name": name})
        S.call("batch", {"commands": [{"name": "activate_document", "args": {"name": "Other"}},
                                      {"name": "edit_feature", "args": {"name": "Boss-Extrude1", "params": {"depth": 21}}}]})
        assert len(cloud.ws.of("change")) == 1 and len(other.ws.of("change")) == 1 and depth(other) == (21, 21)
    finally:
        other.close()


def test_a_failed_upload_keeps_the_edit_queued_or_undoes_it(cloud):
    def offline(obj):
        raise ConnectionError("the blob store is unreachable")
    real = cloud._externalize
    cloud._externalize = offline
    S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 44}})  # (queued, not lost)
    assert len(cloud.pending) == 1 and not cloud.ws.of("change") and depth(cloud) == (44, 10)
    cloud._handle({"type": "change", "seq": 4, "author": "u2", "client_id": "u2.x", "change_id": "r1",
                   "ops": [{"op": "doc.patch", "set": [[["units"], "in"]], "unset": []}]})
    with S.APP.lock:
        cloud.reload_view_locked()
    assert depth(cloud) == (44, 10)  # (someone else's change does not wipe it: it is still queued)
    cloud._externalize = real
    cloud._flush_after = 0.0
    cloud._flush()
    assert len(cloud.ws.of("change")) == 1 and not cloud.pending and depth(cloud) == (44, 44)

    def refused(obj):
        raise C._UploadRefused("could not upload to the collaboration server: too large (HTTP 413)")
    cloud._externalize = refused
    with pytest.raises(S.CommandError, match="could not be sent.*too large"):
        S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 55}})
    assert depth(cloud) == (44, 44) and not cloud.pending
    conflict = cloud.conflicts[-1]
    assert conflict["code"] == "upload_refused" and conflict["ops"][0]["set"] == [[["params", "depth"], 55]]


def test_any_other_failure_while_sending_undoes_the_edit(cloud):
    def broken(ops, command, args):
        raise RuntimeError("a defect")
    cloud.submit = broken
    with pytest.raises(S.CommandError, match="could not be sent.*undone"):
        S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 66}})
    assert depth(cloud) == (10, 10)


def test_a_message_that_does_not_apply_changes_nothing_and_resynchronises(cloud):
    before = json.dumps(cloud.confirmed, sort_keys=True)
    bad = {"type": "change", "seq": 4, "author": "u2", "client_id": "u2.x", "change_id": "r1",
           "ops": [{"op": "feature.update", "id": "f2", "set": [[["params", "depth"], 7]], "unset": []},
                   {"op": "feature.remove", "id": "f99"}]}  # (the second does not apply: the first must not stay)
    with pytest.raises(O.OpError):
        cloud._handle(bad)
    assert json.dumps(cloud.confirmed, sort_keys=True) == before and cloud.seq == 3
    with pytest.raises(C._Resync):  # (what the network thread then does: drop the connection, ask for everything)
        cloud._resync(O.OpError("does not apply"))
    assert cloud.seq is None
    S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 30}})  # (still made on the shown state)
    assert cloud.ws.of("change")[-1]["base_seq"] == 3


def test_welcome_trims_the_pools_to_what_the_server_still_reserves(cloud):
    cloud.state = "offline"
    cloud.ws.sent.clear()
    cloud._welcome({"type": "welcome", "seq": 3, "you": cloud.you, "role": "editor", "peers": [], "locks": {},
                    "broken": {}, "changes": [], "project_id": "p1", "reserved": {"ids": ["f51"], "names": []}})
    assert cloud.pool_ids[0] == "f51" and "f50" not in cloud.pool_ids
    assert "Sketch8" not in cloud.pool_names.get("Sketch", [])
    (r,) = cloud.ws.of("ids.reserve")  # (and refilled)
    assert r["features"] == C.POOL_IDS - 1 and "Sketch" in r["names"]


def test_resent_changes_go_out_before_anything_new(cloud):
    import threading
    import time
    cloud.state = "offline"
    S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 11}})
    S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 12}})
    old = list(cloud.pending)
    racer = []

    def answer(m):  # a command comes in just as the first queued change goes out again
        if m["type"] == "change" and not racer:
            t = threading.Thread(target=S.call, args=("edit_feature", {"name": "Boss-Extrude1",
                                                                       "params": {"depth": 13}}))
            racer.append(t)
            t.start()
            time.sleep(0.3)
        return accept(cloud)(m)
    cloud.ws.answer = answer
    cloud._welcome({"type": "welcome", "seq": 3, "you": cloud.you, "role": "editor", "peers": [], "locks": {},
                    "broken": {}, "changes": [], "reserved": {"ids": ["f50", "f51"], "names": ["Sketch8"]}})
    racer[0].join(30)
    sent = cloud.ws.of("change")
    assert [m["change_id"] for m in sent[:2]] == old and len(sent) == 3
    assert [m["ops"][0]["set"][0][1] for m in sent] == [11, 12, 13] and depth(cloud) == (13, 13)


def test_refused_reservations_fail_only_their_own_request(cloud):
    cloud.requests.update({"now:a": None, "now:b": None})
    cloud._handle({"type": "error", "code": "bad_reserve", "message": "too many", "request_id": "now:a"})
    assert cloud.requests["now:a"]["code"] == "bad_reserve" and cloud.requests["now:b"] is None


def test_client_ids_are_per_run_and_a_replaced_connection_is_not_fatal(monkeypatch, tmp_path):
    import subprocess
    import sys
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    assert auth.client_id() == auth.client_id() and "client_id" not in auth.config()
    code = "from openswx.collab import auth; print(auth.client_id())"
    ids = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ)).stdout
           for _ in range(2)}
    assert len(ids) == 2 and "" not in ids  # (the desktop app and an MCP process are two clients)
    assert 4409 not in C.FATAL_CLOSE


@pytest.mark.skipif(os.name != "nt", reason="Windows access lists")
def test_the_token_file_is_for_this_user_alone_on_windows(monkeypatch, tmp_path):
    import subprocess
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    auth._save_token("secret-token")
    acl = subprocess.run(["icacls", str(tmp_path / "collab" / "token")], capture_output=True, text=True).stdout
    user = os.environ["USERNAME"].lower()
    grants = [line.rsplit(":(", 1)[0].strip().lower() for line in acl.splitlines() if ":(" in line]
    grants = [g.split(" ", 1)[1].strip() if g.startswith(str(tmp_path).lower()) else g for g in grants]
    assert grants and all(g == user or g.endswith("\\" + user) for g in grants), acl
    assert auth.token() == "secret-token"


def test_backup_folders_stay_inside_the_backup_folder():
    for bad in ("..", ".", "../..", "", "/", "\\"):
        s = backup._slug(bad)
        assert s not in (".", "..", "") and "/" not in s and "\\" not in s and not s.startswith(".")
    assert backup._slug("a1b2") == "a1b2" and backup._slug("127.0.0.1_9") == "127.0.0.1_9"


def test_locks_a_page_took_are_freed_when_it_goes(cloud):
    """The UI passes its page's id as owner (and the cloud document's id): when the page is closed or crashes,
    what it still holds is released, even if another document is active by then."""
    cloud.ws.answer = lambda m: ({"type": "lock.result", "request_id": m["request_id"], "granted": m["targets"],
                                  "denied": {}} if m["type"] == "lock.acquire" else None)
    got = S.call("collab_lock", {"features": ["f:f1", "f:f2"], "document": "doc1", "owner": "page-12345678"})
    assert got["granted"] == ["f:f1", "f:f2"]
    S.call("collab_unlock", {"features": ["f:f1"], "document": "doc1", "owner": "page-12345678"})
    assert C.OWNED == {"page-12345678": {("doc1", "f:f2")}}
    S.call("new_part", {"name": "Elsewhere"})  # (another document active now)
    assert C.release_owner("page-12345678") == 1 and C.OWNED == {}
    assert cloud.ws.of("lock.release")[-1]["targets"] == ["f:f2"]
    with pytest.raises(S.CommandError, match="no cloud document 'nope'"):
        S.call("collab_unlock", {"features": ["f:f2"], "document": "nope"})


@pytest.mark.skipif(os.name != "nt", reason="Windows access lists")
def test_explicit_entries_on_the_token_file_are_removed_too(tmp_path):
    """Some machines (GitHub's Windows runners, for one) give new files explicit entries for SYSTEM and
    Administrators, which removing inheritance alone leaves in place."""
    import subprocess
    p = tmp_path / "token"
    p.write_text("x")
    subprocess.run(["icacls", str(p), "/grant", "*S-1-5-18:F", "*S-1-5-32-544:F"], capture_output=True, check=True)
    auth._owner_only(p)
    acl = subprocess.run(["icacls", str(p)], capture_output=True, text=True).stdout
    user = os.environ["USERNAME"].lower()
    grants = [line.rsplit(":(", 1)[0].strip().lower() for line in acl.splitlines() if ":(" in line]
    grants = [g.split(" ", 1)[1].strip() if g.startswith(str(tmp_path).lower()) else g for g in grants]
    assert grants and all(g == user or g.endswith("\\" + user) for g in grants), acl


def test_adding_a_provider_goes_through_the_signed_in_app(monkeypatch, tmp_path):
    import hashlib

    import httpx
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    auth._remember("http://127.0.0.1:8787", {"token": "pw-session", "user": {"name": "Ada"}})
    seen = {"linked": []}

    def server(request):
        assert request.headers["authorization"] == "Bearer pw-session"  # (always as the signed-in account)
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/api/auth/oauth/github/link":
            if body.get("current_password") != "correct horse":
                return httpx.Response(403, json={"detail": "the current password is wrong"})
            seen.update(body)
            return httpx.Response(200, json={"url": "https://github.example/authorize?state=s"})
        if path == "/api/auth/oauth/link/finish":
            ok = body["code"] == "linkcode" and \
                hashlib.sha256(body["code_verifier"].encode()).hexdigest() == seen["code_challenge"]
            if not ok:
                return httpx.Response(400, json={"detail": "this code was not issued to this app and account"})
            seen["linked"].append("github")
            return httpx.Response(200, json={"linked": "github", "user": {"name": "Ada", "email_verified": True}})
        if path == "/api/me/providers":
            return httpx.Response(200, json={"providers": seen["linked"], "has_password": True})
        if path == "/api/me/providers/github/unlink":
            seen["linked"].remove("github")
            return httpx.Response(204)
        return httpx.Response(404)

    def browser(url):  # GitHub, approving: back to the loopback address the app gave the server
        assert url == "https://github.example/authorize?state=s"
        urllib.request.urlopen(seen["redirect_uri"] + "?code=linkcode", timeout=10).read()

    mock = httpx.MockTransport(server)
    with pytest.raises(auth.AuthError, match="password is wrong"):
        auth.link_provider("github", "nope", open_browser=browser, timeout=20, transport=mock)
    got = auth.link_provider("github", "correct horse", open_browser=browser, timeout=20, transport=mock)
    assert got["linked"] == "github" and seen["redirect_uri"].startswith("http://127.0.0.1:")
    assert auth.config()["user"]["email_verified"] is True and auth.token() == "pw-session"
    assert auth.providers(transport=mock) == {"providers": ["github"], "has_password": True}
    assert auth.unlink_provider("github", "correct horse", transport=mock) == {"providers": [], "has_password": True}

    def refused(url):
        urllib.request.urlopen(seen["redirect_uri"] + "?error=identity_in_use", timeout=10).read()
    with pytest.raises(auth.AuthError, match="already linked to another"):
        auth.link_provider("github", "correct horse", open_browser=refused, timeout=20, transport=mock)
    auth.sign_out(transport=mock)
    with pytest.raises(auth.AuthError, match="not signed in"):
        auth.link_provider("github", "correct horse", open_browser=browser, timeout=20, transport=mock)


def test_sign_in_method_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "providers", lambda: {"providers": ["github"], "has_password": True})
    monkeypatch.setattr(auth, "link_provider", lambda p, pw: calls.append(("link", p, pw)) or {"linked": p, "user": {}})

    def unlink(p, pw):
        raise auth.AuthError("cannot remove github: this is the only way into the account")
    monkeypatch.setattr(auth, "unlink_provider", unlink)
    assert S.call("collab_sign_in_methods") == {"providers": ["github"], "has_password": True}
    assert S.call("collab_link_provider", {"provider": "google", "password": "pw"}) == {"linked": "google", "user": {}}
    assert calls == [("link", "google", "pw")]
    with pytest.raises(S.CommandError, match="only way into the account"):
        S.call("collab_unlink_provider", {"provider": "github"})


# ---------------------------------------------------------------------- performance (review of 0.4.0)
def test_presence_does_not_wait_for_the_command_lock(cloud):
    import threading

    from fastapi.testclient import TestClient

    from openswx.collab import commands_collab  # noqa: F401 (as after any collab_* command)
    from openswx.web_server import create_app
    held, release = threading.Event(), threading.Event()

    def busy():  # (a long rebuild, say)
        with S.APP.lock:
            held.set()
            release.wait(20)
    t = threading.Thread(target=busy)
    t.start()
    held.wait(5)
    try:
        with TestClient(create_app(mount_mcp=False, token=None), base_url="http://127.0.0.1:8765") as http:
            r = http.post("/api/collab/presence", json={"args": {"pointer": {"point": [1, 2, 3]}, "tool": "fillet"}})
            assert r.json() == {"ok": True, "result": {"sent": True}}
            bad = http.post("/api/collab/presence", json={"args": {"pointer": "x"}})
            assert bad.status_code == 400 and "pointer must be a dict" in bad.json()["error"]
            assert http.post("/api/collab/presence", json={"args": {"run": "evil"}}).status_code == 400
    finally:
        release.set()
        t.join()
    assert cloud.ws.of("presence")[-1]["data"] == {"pointer": {"point": [1, 2, 3]}, "tool": "fillet"}


def test_one_http_client_is_kept_per_server_and_token(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    auth._POOL.clear()
    with auth.shared("http://127.0.0.1:9", "a") as c1, auth.shared("http://127.0.0.1:9", "a") as c2:
        assert c1 is c2 and not c1.is_closed
    with auth.shared("http://127.0.0.1:9", "b") as c3:
        assert c3 is not c1 and c3.headers["Authorization"] == "Bearer b"
    assert not c1.is_closed  # (leaving the block keeps it open for the next command)
    auth._POOL.clear()


def test_status_leaves_out_a_refused_change_s_operations(cloud):
    cloud.conflicts = [{"change_id": "c1", "command": "fillet", "who": ["Ada"], "ops": [{"op": "set"}] * 1000}]
    (d,) = C.status()["documents"]
    assert d["conflicts"] == [{"change_id": "c1", "command": "fillet", "who": ["Ada"]}]
    assert cloud.conflicts[0]["ops"]  # (kept, just not sent to every screen)


def test_the_blob_cache_keeps_the_newest_within_its_limit(cloud, monkeypatch):
    monkeypatch.setattr(C, "BLOB_CACHE_BYTES", 10)
    cloud.blobs = {"a": b"12345", "b": b"12345", "c": b"12345"}
    cloud._trim_blobs()
    assert list(cloud.blobs) == ["b", "c"]
    cloud.blobs = {"big": b"x" * 50}
    cloud._trim_blobs()
    assert list(cloud.blobs) == ["big"]  # (never empty: the one in use stays)


def test_a_cloud_document_is_never_fingerprinted_as_unsaved(cloud, monkeypatch):
    calls = []
    real = S._fingerprint
    monkeypatch.setattr(S, "_fingerprint", lambda d: calls.append(d) or real(d))
    S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 12}})
    assert calls == [] and S.APP.doc.dirty is False
