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
    with pytest.raises(S.CommandError, match="Ada changed Boss-Extrude1"):
        S.call("edit_feature", {"name": "Boss-Extrude1", "params": {"depth": 25}})
    assert S.APP.doc.find("Boss-Extrude1")["params"]["depth"] == 33  # (the server's version, not ours)
    assert volume() == pytest.approx(W * H * 33, rel=1e-6)
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
                         "ops": p["wire"]},
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
