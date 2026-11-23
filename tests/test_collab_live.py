"""Live collaboration end to end: a real openswx-collab server process and two openswx processes (Ada and Bob,
tests/collab_peer.py) editing one cloud document. After every step both models must be the same: the same
document, volume and persistent face names, and the same as the server's copy.

Needs a checkout of openswx-collab with its virtual environment, by default beside this repository
(../openswx-collab/.venv; OPENSWX_COLLAB_SERVER_REPO points elsewhere). Skipped without it. The tests run in
order and share the server and the two peers.
"""
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("openswx_collab_protocol")
pytest.importorskip("websockets")
httpx = pytest.importorskip("httpx")

from openswx_collab_protocol.ops import fingerprint  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SERVER_REPO = Path(os.environ.get("OPENSWX_COLLAB_SERVER_REPO") or ROOT.parent / "openswx-collab")
SERVER_PY = SERVER_REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
SAME = ("doc", "names", "faces", "volume", "features", "seq")

# The open-core server, whatever extension (a commercial layer) is installed in its environment: this is about
# the protocol, not seats or billing.
OPEN_CORE = ("import sys; import openswx_collab_server.hooks as h; h.load_hooks = h.Hooks; "
             "from openswx_collab_server.__main__ import main; main(sys.argv[1:])")

pytestmark = pytest.mark.skipif(not SERVER_PY.exists(), reason=f"no openswx-collab server at {SERVER_REPO}")


class Peer:
    def __init__(self, label: str, seed: str):
        import openswx_collab_protocol
        proto = str(Path(openswx_collab_protocol.__file__).resolve().parent.parent)
        env = dict(os.environ, PYTHONHASHSEED=seed, OPENSWX_HOME=tempfile.mkdtemp(prefix=f"openswx-{label}-"),
                   OPENSWX_COLLAB_RETRY_MAX="1", OPENSWX_COLLAB_WAIT="20",
                   PYTHONPATH=os.pathsep.join([str(ROOT), proto, os.environ.get("PYTHONPATH", "")]))
        self.label = label
        self.err = tempfile.TemporaryFile("w+")
        self.p = subprocess.Popen([sys.executable, str(ROOT / "tests" / "collab_peer.py")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=self.err, text=True, env=env)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        if not self._line(120):
            raise RuntimeError(f"peer {label} did not start:\n{self.stderr()}")

    def _pump(self) -> None:
        for line in self.p.stdout:
            self.lines.put(line)
        self.lines.put("")

    def _line(self, timeout: float) -> str:
        try:
            return self.lines.get(timeout=timeout)
        except queue.Empty:  # (never hang the test run: say where the peer is stuck instead)
            raise RuntimeError(f"peer {self.label} gave no answer in {timeout} s:\n{self.stderr()}") from None

    def stderr(self) -> str:
        self.err.seek(0)
        return self.err.read()[-4000:]

    def send(self, **req) -> None:
        self.p.stdin.write(json.dumps(req) + "\n")
        self.p.stdin.flush()

    def read(self) -> dict:
        line = self._line(180)
        out = json.loads(line) if line else {"crash": "the peer process died"}
        if "crash" in out:
            raise RuntimeError(f"{self.label}: {out['crash']}\n{self.stderr()}")
        return out

    def ask(self, **req) -> dict:
        self.send(**req)
        return self.read()

    def call(self, command: str, /, **args):
        """Run a command; its result (a refused command fails the test)."""
        out = self.ask(call=command, args=args)
        assert "error" not in out, f"{self.label} {command}: {out['error']}"
        return out["result"]

    def fails(self, command: str, /, **args) -> str:
        out = self.ask(call=command, args=args)
        assert "error" in out, f"{self.label} {command} should have been refused: {out}"
        return out["error"]

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.wait(timeout=20)
        except Exception:  # noqa: BLE001
            self.p.kill()


class Server:
    def __init__(self):
        self.work = Path(tempfile.mkdtemp(prefix="openswx-collab-server-"))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.p = None

    def start(self) -> None:
        env = dict(os.environ, OSWX_COLLAB_DATABASE_URL=f"sqlite+aiosqlite:///{(self.work / 'collab.db').as_posix()}",
                   OSWX_COLLAB_STORAGE_URL=str(self.work / "storage"), OSWX_COLLAB_PUBLIC_URL=self.url)
        env.pop("PYTHONPATH", None)  # (the server runs on its own environment)
        self.log = open(self.work / "server.log", "a")
        self.p = subprocess.Popen([str(SERVER_PY), "-c", OPEN_CORE, "serve", "--port", str(self.port)],
                                  env=env, stdout=self.log, stderr=self.log, cwd=str(self.work))
        for _ in range(300):
            try:
                if httpx.get(self.url + "/api/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError(f"the server did not start: {(self.work / 'server.log').read_text()[-3000:]}")

    def stop(self) -> None:
        if self.p is not None:
            self.p.kill()
            self.p.wait(timeout=20)
            self.p = None
            self.log.close()


@pytest.fixture(scope="module")
def world():
    server = Server()
    server.start()
    peers = []
    try:
        http = httpx.Client(base_url=server.url, timeout=30)
        ada = http.post("/api/auth/register", json={"email": "ada@example.com", "password": "correct horse",
                                                    "name": "Ada"}).json()["token"]
        h = {"Authorization": f"Bearer {ada}"}
        org = http.post("/api/orgs", json={"name": "Robotics"}, headers=h).json()["id"]
        peers = [Peer("ada", "1"), Peer("bob", "4242")]  # (different hash seeds: set order must not reach geometry)
        a, b = peers
        a.call("collab_sign_in", server=server.url, email="ada@example.com", password="correct horse")
        project = a.call("collab_create_project", name="Rover")
        inv = http.post(f"/api/orgs/{org}/invites", headers=h, json={
            "email": "bob@example.com", "projects": [{"project_id": project["id"], "role": "editor"}]}).json()["token"]
        http.post("/api/auth/register", json={"email": "bob@example.com", "password": "correct horse", "name": "Bob",
                                              "invite": inv}).raise_for_status()
        b.call("collab_sign_in", server=server.url, email="bob@example.com", password="correct horse")
        yield {"server": server, "a": a, "b": b, "http": http, "ada": h, "project": project, "state": {}}
    finally:
        for p in peers:
            p.close()
        server.stop()


def server_fp(w) -> str:
    doc = w["state"]["doc"]
    return fingerprint(w["http"].get(f"/api/documents/{doc}/content", headers=w["ada"]).json()["content"])


def agree(w, seq: int | None = None) -> tuple[dict, dict]:
    """Both peers up to date: the same model as each other and as the server."""
    da = w["a"].ask(settle=seq)["digest"]
    db = w["b"].ask(settle=seq)["digest"]
    for k in SAME:
        assert da[k] == db[k], f"{k}: Ada {da[k]!r} / Bob {db[k]!r}"
    assert da["doc"] == da["confirmed"] and db["doc"] == db["confirmed"]
    assert da["doc"] == server_fp(w), "the server has another document"
    return da, db


def status(peer) -> dict:
    return peer.call("collab_status")["documents"][0]


# ---------------------------------------------------------------------- the steps, in order
def test_server_info_before_signing_in(world):
    info = world["a"].call("collab_server_info", server=world["server"].url + "/")
    assert info["name"] == "openswx-collab" and info["server"] == world["server"].url
    assert info["oauth"] == []  # (no Google / GitHub set up on this server: email and password only)
    assert "cannot reach" in world["a"].fails("collab_server_info", server="http://127.0.0.1:9")


def test_enable_and_open(world):
    a, b = world["a"], world["b"]
    projects = a.call("collab_projects")["orgs"][0]["projects"]
    assert [p["name"] for p in projects] == ["Rover"]
    a.call("new_part", name="Bracket")
    sk = a.call("create_sketch", plane="Top")["sketch"]
    a.call("sketch_rectangle", sketch=sk, corner1=[-50, -30], corner2=[50, 30])
    ext = a.call("extrude", sketch=sk, depth=20)["feature"]
    on = a.call("collab_enable", project="Rover")
    assert on["role"] == "owner" and not on["read_only"]
    docs = b.call("collab_documents", project="Rover")["documents"]
    assert [d["name"] for d in docs] == ["Bracket"]
    opened = b.call("collab_open", document_id=docs[0]["id"])
    assert opened["role"] == "editor" and opened["peers"] == ["Ada"]
    world["state"].update(doc=on["document_id"], ext=ext, sketch=sk)
    da, _ = agree(world, 0)
    assert da["volume"] == pytest.approx(100 * 60 * 20, rel=1e-6)


def test_edits_in_turn(world):
    a, b, st = world["a"], world["b"], world["state"]
    edges = b.call("get_topology")["edges"]
    e = max(edges, key=lambda x: x.get("length", 0))
    st["fillet"] = b.call("fillet", radius=3, edges=[e.get("name") or e["id"]])["feature"]
    agree(world, 1)
    a.call("edit_feature", name=st["ext"], params={"depth": 32})
    agree(world, 2)
    faces = b.call("get_topology")["faces"]
    top = max((f for f in faces if f["normal"][1] > 0.99), key=lambda f: f["point"][1])
    sk2 = b.call("create_sketch", plane=top.get("name") or top["id"])["sketch"]
    b.call("sketch_circle", sketch=sk2, center=[10, 5], radius=8)
    cut = b.call("cut_extrude", sketch=sk2, depth=10)["feature"]
    da, _ = agree(world, 5)
    # (names come from the server; each app keeps a few reserved ahead, so numbers can skip: Fillet2 with no Fillet1)
    assert [n for _, n in da["features"]] == ["Sketch1", "Boss-Extrude1", st["fillet"], sk2, cut]
    assert st["fillet"].startswith("Fillet") and cut.startswith("Cut-Extrude")


def test_simultaneous_edits_of_different_features_both_go_in(world):
    a, b, st = world["a"], world["b"], world["state"]
    a.send(call="edit_feature", args={"name": st["ext"], "params": {"depth": 40}})
    b.send(call="edit_feature", args={"name": st["fillet"], "params": {"radius": 2}})
    ra, rb = a.read(), b.read()
    assert "error" not in ra and "error" not in rb, (ra.get("error"), rb.get("error"))
    agree(world, 7)


def test_simultaneous_edits_of_one_feature_one_is_refused_and_rolled_back(world):
    a, b, st = world["a"], world["b"], world["state"]
    a.send(call="edit_feature", args={"name": st["fillet"], "params": {"radius": 4}})
    b.send(call="edit_feature", args={"name": st["fillet"], "params": {"radius": 1}})
    ra, rb = a.read(), b.read()
    refused = [(who, r["error"]) for who, r in (("Ada", ra), ("Bob", rb)) if "error" in r]
    assert len(refused) == 1, (ra, rb)
    loser, text = refused[0]
    winner = "Bob" if loser == "Ada" else "Ada"
    assert f"{winner} changed {st['fillet']}" in text
    da, _ = agree(world, 8)
    conflicts = status(a if loser == "Ada" else b)["conflicts"]
    assert conflicts[-1]["code"] == "stale" and conflicts[-1]["who"] == [winner]
    assert conflicts[-1]["features"] == [st["fillet"]]


def test_a_lock_keeps_the_other_out(world):
    a, b, st = world["a"], world["b"], world["state"]
    assert a.call("collab_lock", features=[st["fillet"]]) == {"granted": [st["fillet"]], "denied": {}}
    assert b.call("collab_lock", features=[st["fillet"]])["denied"] == {st["fillet"]: "Ada"}
    before = b.ask(settle=8)["digest"]
    text = b.fails("edit_feature", name=st["fillet"], params={"radius": 2.5})
    assert "being edited by Ada" in text
    assert b.ask(settle=8)["digest"]["doc"] == before["doc"]  # (Bob's model is the server's again)
    locks = status(b)["locks"]
    assert [(lk["target"], lk["name"], lk["mine"]) for lk in locks.values()] == [(st["fillet"], "Ada", False)]
    a.call("collab_unlock", features=[st["fillet"]])
    deadline = time.time() + 10
    while status(b)["locks"] and time.time() < deadline:
        time.sleep(0.1)
    b.call("edit_feature", name=st["fillet"], params={"radius": 2.5})
    agree(world, 9)


def test_features_created_at_once_get_distinct_ids_from_the_server(world):
    a, b = world["a"], world["b"]
    a.send(call="create_sketch", args={"plane": "Front"})
    b.send(call="create_sketch", args={"plane": "Right"})
    ra, rb = a.read(), b.read()
    assert "error" not in ra and "error" not in rb, (ra.get("error"), rb.get("error"))
    assert ra["result"]["sketch"] != rb["result"]["sketch"]
    da, _ = agree(world, 11)
    ids = [i for i, _ in da["features"]]
    assert len(ids) == len(set(ids)) and {ra["result"]["sketch"], rb["result"]["sketch"]} <= {n for _, n in da["features"]}


def test_reconnect_after_a_server_restart_resends_pending_changes(world):
    a, b, st, server = world["a"], world["b"], world["state"], world["server"]
    server.stop()
    assert a.ask(offline=15)["state"] != "online"
    a.call("edit_feature", name=st["ext"], params={"depth": 36})  # (no server: it waits in the queue)
    assert status(a)["pending"] == 1
    server.start()
    da, _ = agree(world, 12)
    assert da["volume"] < 100 * 60 * 36 and status(a)["pending"] == 0 and status(b)["state"] == "online"


def test_undo_takes_back_your_own_change_unless_someone_changed_it_since(world):
    a, b, st = world["a"], world["b"], world["state"]
    v0 = agree(world, 12)[0]["volume"]
    a.call("edit_feature", name=st["ext"], params={"depth": 25})
    v1 = agree(world, 13)[0]["volume"]
    undone = a.call("undo")
    assert undone["undone"] == 13
    da, _ = agree(world, 14)
    assert da["volume"] == pytest.approx(v0, rel=1e-6) and v1 != v0
    redone = a.call("redo")  # (redo: the undo of her undo)
    assert redone["redone"] == 14
    da, _ = agree(world, 15)
    assert da["volume"] == pytest.approx(v1, rel=1e-6)
    b.call("edit_feature", name=st["fillet"], params={"radius": 2})  # Bob's
    agree(world, 16)
    a.call("edit_feature", name=st["fillet"], params={"radius": 1.5})  # Ada changes the same fillet after him
    agree(world, 17)
    assert f"cannot undo: Ada changed {st['fillet']} after you" in b.fails("undo")
    assert "nothing to redo" in a.fails("redo")  # (a new change ends what redo can bring back)


def test_a_rebuild_failure_is_reported_and_blamed(world):
    a, b, st = world["a"], world["b"], world["state"]
    a.call("edit_feature", name=st["fillet"], params={"radius": 3})
    agree(world, 18)
    a.call("edit_feature", name=st["ext"], params={"depth": 1.5})  # (a 3 mm fillet no longer fits)
    da, db = agree(world, 19)
    assert st["fillet"] in da["errors"] and st["fillet"] in db["errors"]
    fid = next(i for i, n in da["features"] if n == st["fillet"])
    deadline, broken = time.time() + 15, {}
    while time.time() < deadline and fid not in broken:
        broken = status(b)["broken"]
        time.sleep(0.1)
    assert broken[fid]["feature"] == st["fillet"] and broken[fid]["by_name"] == "Ada" and broken[fid]["by_seq"] == 19
    a.call("undo")
    agree(world, 20)
    deadline = time.time() + 15
    while time.time() < deadline and status(b)["broken"]:
        time.sleep(0.1)
    assert status(b)["broken"] == {}


def test_history_versions_restore_presence(world):
    a, b, st = world["a"], world["b"], world["state"]
    rev = b.call("collab_name_version", name="Rev A")
    assert rev["seq"] == 20
    v_rev = agree(world, 20)[0]["volume"]
    a.call("edit_feature", name=st["ext"], params={"depth": 50})
    assert agree(world, 21)[0]["volume"] != v_rev
    out = a.call("collab_restore", version="Rev A")
    assert out["seq"] == 22
    assert agree(world, 22)[0]["volume"] == pytest.approx(v_rev, rel=1e-6)
    hist = b.call("collab_history", limit=100)
    assert [h["seq"] for h in hist["history"]] == list(range(22, 0, -1))
    assert {h["author"]["name"] for h in hist["history"]} == {"Ada", "Bob"}
    assert [v["name"] for v in hist["versions"]] == ["Rev A"]
    assert a.call("collab_presence", tool="fillet", selection=["Boss-Extrude1:cap+"])["sent"]
    deadline, ada = time.time() + 10, {}
    while time.time() < deadline and not (ada.get("presence") or {}).get("tool"):
        ada = next(p for p in status(b)["peers"] if p["name"] == "Ada")
        time.sleep(0.1)
    assert ada["presence"] == {"selection": ["Boss-Extrude1:cap+"], "tool": "fillet"}


def test_disable_and_sign_out(world):
    a, b = world["a"], world["b"]
    out = b.call("collab_disable")
    assert out["pending_dropped"] == 0 and b.call("collab_status")["documents"] == []
    b.call("edit_feature", name=world["state"]["ext"], params={"depth": 7})  # (a local copy now: nothing is sent)
    assert agree_alone(a)["seq"] == 22
    assert a.call("collab_sign_out")["signed_out"] is True
    st = a.call("collab_status")
    assert st["documents"] == [] and st["signed_in"] is False
    assert "collab_sign_in" in a.ask(call="collab_projects")["error"]


def agree_alone(peer) -> dict:
    return peer.ask(settle=None)["digest"]
