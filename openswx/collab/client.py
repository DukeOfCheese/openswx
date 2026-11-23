"""The connection of one cloud document to the collaboration server (docs/protocol.md of openswx-collab).

Each cloud document has a `DocClient`, with two threads of its own:
  * the network thread connects, reads every message and keeps `confirmed`, the document as the server has
    it (it never touches the openswx document, so it never waits for a command);
  * the view thread brings the open document up to date (confirmed + this user's changes the server has not
    answered yet) whenever `confirmed` moved on. It does that under `APP.lock`, the lock every command holds,
    so a change from someone else never lands in the middle of a command.

Local changes come from `Hook`, registered with session.py's command hooks while any cloud document is open:
the document is written out (to_dict) before and after each outermost mutating command, and the difference
(`ops.diff`) goes to the server as one change. The command then waits (up to OPENSWX_COLLAB_WAIT seconds,
default 15) for the answer, so a change the server refuses fails the command, the document goes back to the
server's state, and the reason ("Ada changed Fillet1 first") is the command's error. Without a connection
changes queue up and are sent again, with their original change ids, on reconnecting.

New features take their id and name from the server (`ids.reserve`): the client keeps a small pool and
`PartDocument` draws on it through `take_id` / `take_name` (document.py's allocator).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import random
import threading
import time
import uuid

from openswx_collab_protocol import ops as O
from openswx_collab_protocol.messages import PROTOCOL_VERSION

from .. import session as S
from ..document import VERSION, DocumentError
from . import auth

log = logging.getLogger("openswx.collab")

CLIENTS: dict[str, "DocClient"] = {}  # cloud document id -> its connection
REVISION = [0]  # goes up whenever anything a screen shows about collaboration changes (peers, locks, ...)
PING_EVERY = 15.0
# (4409, "the same client connected again", is not among them: client ids are per run, so it is our own
# earlier connection being replaced, and reconnecting is right)
FATAL_CLOSE = {4400: "bad_hello", 4401: "signed_out", 4404: "gone", 4426: "protocol"}
POOL_IDS = 8  # feature ids kept in reserve
COMMON = ("Sketch", "Boss-Extrude", "Cut-Extrude", "Fillet", "Chamfer", "Plane")  # names reserved ahead
NEED_CONNECTION = ("creating features in a cloud document needs a connection to the collaboration server (their ids "
                   "and names come from it); it is not reachable now - try again when it is back")


def wait_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("OPENSWX_COLLAB_WAIT", "15")))
    except ValueError:
        return 15.0


def _retry_max() -> float:
    try:
        return max(0.2, float(os.environ.get("OPENSWX_COLLAB_RETRY_MAX", "10")))
    except ValueError:
        return 10.0


def plain(d: dict) -> dict:
    """The document as it travels: JSON, nothing else (tuples become lists)."""
    return json.loads(json.dumps(d, default=str))


def changed() -> None:
    REVISION[0] += 1


def client_for(doc) -> "DocClient | None":
    c = getattr(doc, "_collab", None)
    return c if isinstance(c, DocClient) and not c.closed else None


class _Resync(ConnectionError):
    """A message could not be applied: drop the connection and fetch the whole document again."""


class _UploadRefused(DocumentError):
    """The server refused a blob upload (not a network failure: sending again will not help)."""


def _upload_ok(r) -> None:
    """A refusal (too large, no access) is final; a server error or a busy server is worth trying again."""
    if r.status_code < 300:
        return
    text = f"could not upload to the collaboration server: {auth.error_text(r)}"
    if r.status_code >= 500 or r.status_code in (401, 408, 429):
        raise ConnectionError(text)
    raise _UploadRefused(text)


class _Fatal(Exception):
    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state


class DocClient:
    def __init__(self, doc, document_id: str, server: str, token: str, info: dict):
        self.doc = doc
        self.document_id = document_id
        self.server = server.rstrip("/")
        self.token = token
        self.project_id = info.get("project_id")
        self.cloud_name = info.get("name") or doc.name
        self.client_id = auth.client_id()
        self.mu = threading.RLock()
        self.cond = threading.Condition(self.mu)
        self.send_mu = threading.Lock()
        self.ws = None
        self.state = "connecting"  # connecting, online, offline, signed_out, gone, closed, error
        self.error: str | None = None
        self.closed = False
        self.stop = threading.Event()
        self.view_due = threading.Event()
        self.welcomes = 0
        # the shared document
        self.confirmed: dict | None = None
        self.seq: int | None = None
        self.pending: dict[str, dict] = {}  # change id -> {"ops", "wire", "base_seq", "command", "args"} (in order)
        self.gen = 0  # goes up whenever confirmed or pending changes
        self.view_gen = -1  # the gen the open document shows (== gen: it is up to date)
        self.view_seq: int | None = None  # the confirmed state the open document is built on: the base of changes
        self.answers: dict[str, dict] = {}  # change id -> {"ok", "seq" | "code", "message", "text"}
        self.requests: dict[str, dict | None] = {}  # request id -> the answer, once it came
        self.n = 0
        self.busy = False  # a command on this document is between its before and after
        self.last_report = -1
        self.report_due = False
        self.backed_up_seq: int | None = None
        # who and what
        self.you: dict = {}
        self.role: str | None = None
        self.read_only = False
        self.read_only_reason: str | None = None
        self.peers: dict[str, dict] = {}
        self.locks: dict[str, dict] = {}
        self.broken: dict[str, dict] = {}
        self.conflicts: list[dict] = []
        self.recent: list[dict] = []  # the last changes from others
        self.versions: list[dict] = []  # named while this document was open
        self.mine: list[int] = []  # sequence numbers of this app's accepted changes (undo takes the last)
        self.redo_stack: list[int] = []  # sequence numbers of the undos (redo undoes the last)
        # ids and names from the server
        self.pool_ids: list[str] = []
        self.pool_names: dict[str, list[str]] = {}
        self.taken: list[tuple[str, str, str]] = []  # ("id" | "name", value, prefix) drawn during this command
        self.topping: set[str] = set()  # pool requests on their way ("ids", or a name prefix)
        self.blobs: dict[str, bytes] = {}
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ life
    def start(self) -> None:
        for target, name in ((self._run, "net"), (self._viewer, "view")):
            t = threading.Thread(target=target, name=f"openswx-collab-{name}-{self.document_id[:8]}", daemon=True)
            self._threads.append(t)
            t.start()

    def close(self) -> None:
        """Stop syncing. The open document stays, as a local copy."""
        self.closed = True
        self.stop.set()
        self.view_due.set()
        ws = self.ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        with self.cond:
            self.state = "closed"
            self.cond.notify_all()
        if getattr(self.doc, "_collab", None) is self:
            self.doc._collab = None
            self.doc.allocator = None
        if CLIENTS.get(self.document_id) is self:
            del CLIENTS[self.document_id]
        if not CLIENTS:
            S.remove_command_hook(HOOK)
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=5)
        changed()

    @property
    def connected(self) -> bool:
        return self.state == "online" and self.ws is not None

    def wait_welcome(self, timeout: float) -> None:
        """Until the first welcome (or a refusal): raise DocumentError when it did not come."""
        end = time.time() + timeout
        with self.cond:
            while self.welcomes == 0 and self.state not in ("signed_out", "gone", "error", "closed"):
                left = end - time.time()
                if left <= 0:
                    break
                self.cond.wait(left)
            if self.welcomes == 0:
                raise DocumentError(f"could not open the cloud document: {self.error or 'the server did not answer'}")

    # ------------------------------------------------------------------ network thread
    def _url(self) -> str:
        base = self.server.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{base}/api/documents/{self.document_id}/ws"

    def _run(self) -> None:
        delay = 0.5
        while not self.stop.is_set():
            try:
                self._session()
                delay = 0.5
            except _Fatal as exc:
                with self.cond:
                    self.state, self.error = exc.state, str(exc)
                    self.cond.notify_all()
                changed()
                log.warning("collaboration on %s stopped: %s", self.document_id, exc)
                return
            except Exception as exc:  # noqa: BLE001 - any network trouble: try again
                with self.cond:
                    if not self.stop.is_set():
                        self.state, self.error = "offline", f"{type(exc).__name__}: {exc}"
                    self.cond.notify_all()
                changed()
                log.info("collaboration connection to %s lost: %s", self.document_id, exc)
            finally:
                self.ws = None
            if self.stop.wait(delay * (0.75 + 0.5 * random.random())):
                break
            delay = min(delay * 2, _retry_max())

    def _session(self) -> None:
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect
        ws = connect(self._url(), max_size=64 * 2**20, open_timeout=15, close_timeout=2)
        try:
            ws.send(json.dumps({"type": "hello", "protocol": PROTOCOL_VERSION, "token": self.token,
                                "client_id": self.client_id, "format_version": VERSION, "last_seq": self.seq,
                                "app": "openswx"}))
            try:
                first = json.loads(ws.recv(timeout=30))
            except ConnectionClosed as exc:
                self._closed_by_server(exc)
                raise
            if first.get("type") != "welcome":
                code = first.get("code", "")
                state = {"unauthorized": "signed_out", "not_found": "gone", "protocol": "protocol",
                         "bad_hello": "bad_hello"}.get(code)
                msg = first.get("message") or code or "refused"
                if state:
                    raise _Fatal(state, msg)
                raise ConnectionError(msg)
            self.ws = ws
            try:
                self._welcome(first)
            except _Fatal:
                raise
            except Exception as exc:  # noqa: BLE001 - the catch-up could not be applied: the whole document, then
                self._resync(exc)
            last_ping = time.time()
            while not self.stop.is_set():
                if time.time() - last_ping >= PING_EVERY:
                    self._send({"type": "ping"})
                    last_ping = time.time()
                try:
                    raw = ws.recv(timeout=1.0)
                except TimeoutError:
                    continue
                except ConnectionClosed as exc:
                    self._closed_by_server(exc)
                    raise
                try:
                    self._handle(json.loads(raw))
                except _Fatal:
                    raise
                except Exception as exc:  # noqa: BLE001 - nothing was half-applied (see _handle): start again clean
                    self._resync(exc)
                if self._unsent():  # (a change whose upload failed for want of a network: try again)
                    self._flush()
        finally:
            self.ws = None
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _resync(self, exc: Exception) -> None:
        """A message from the server could not be applied (a blob that would not download, operations that do not
        fit): `confirmed` is as it was before that message, but it is now behind the server with a gap. Drop the
        connection and ask for the whole document (last_seq None) when reconnecting."""
        log.warning("resynchronising %s with the server: %s", self.document_id, exc, exc_info=True)
        with self.cond:
            self.seq = None
            self.error = f"resynchronising: {type(exc).__name__}: {exc}"
        raise _Resync(str(exc)) from exc

    def _closed_by_server(self, exc) -> None:
        rcvd = getattr(exc, "rcvd", None)
        code = getattr(rcvd, "code", None)
        if code in FATAL_CLOSE:
            reason = getattr(rcvd, "reason", "") or FATAL_CLOSE[code]
            raise _Fatal(FATAL_CLOSE[code], f"{reason} (close code {code})")

    def _send(self, msg: dict) -> None:
        ws = self.ws
        if ws is None:
            raise ConnectionError("not connected")
        with self.send_mu:
            ws.send(json.dumps(msg))

    def _try_send(self, msg: dict) -> bool:
        try:
            self._send(msg)
            return True
        except Exception:  # noqa: BLE001 - the network thread notices and reconnects
            return False

    # ------------------------------------------------------------------ messages
    def _welcome(self, w: dict) -> None:
        if w.get("project_id"):  # (blobs in the content are fetched through it)
            self.project_id = w["project_id"]
        content = self._internalize(w["content"]) if w.get("content") is not None else None
        changes = [dict(ev, ops=self._internalize(ev["ops"])) for ev in (w.get("changes") or [])]
        with self.cond:
            self.you = w.get("you") or {}
            self.role = w.get("role")
            self.read_only = bool(self.you.get("read_only"))
            self.read_only_reason = w.get("read_only_reason")
            self.peers = {p["client_id"]: p for p in w.get("peers") or []}
            self.locks = dict(w.get("locks") or {})
            self.broken = dict(w.get("broken") or {})
            if content is not None:
                O.absorb_ids(content)
                self.confirmed = content
                # (own changes already in it are answered "accepted" with a seq this state includes)
            else:
                new = copy.deepcopy(self.confirmed)  # (all of the catch-up, or none of it)
                for ev in changes:
                    self._apply_event(ev, new)
                self.confirmed = new
            self.seq = w["seq"]
            self.gen += 1
            first = self.welcomes == 0
            self.welcomes += 1
            check = self._trim_pools(w.get("reserved"))
            for p in self.pending.values():  # (a new connection: everything unanswered goes again)
                p["sent"] = False
            self.state, self.error = "online", None
            self.cond.notify_all()
        self._flush_after = 0.0
        # Unanswered changes go again, in order, with their original ids and bases (the server answers each one
        # once), before anything new: _flush sends every change in queue order, whoever calls it first.
        self._flush()
        if not self.read_only:
            with self.mu:
                short = POOL_IDS - len(self.pool_ids)
                names = [p for p in COMMON if not self.pool_names.get(p)] if first else []
            if check:  # (an older server, which does not say what we hold: did it keep our reservations?)
                self._reserve_async(1, [], tag="check")
            elif short > POOL_IDS // 2 or names:
                self._reserve_async(short if short > POOL_IDS // 2 else 0, names, tag="pool")
        self.view_due.set()
        changed()

    def _trim_pools(self, reserved) -> bool:
        """Keep only the ids and names the server says this client still holds (it forgets them after a long
        disconnect or a restart). Call with the lock held. True: the server did not say (check another way)."""
        self.topping.clear()
        if not isinstance(reserved, dict):
            return bool(self.pool_ids)
        ids, names = set(reserved.get("ids") or []), set(reserved.get("names") or [])
        self.pool_ids = [i for i in self.pool_ids if i in ids]
        self.pool_names = {k: [n for n in v if n in names] for k, v in self.pool_names.items()}
        return False

    def _apply_event(self, m: dict, target: dict) -> None:
        """A change from the history, applied to `target` (a copy of confirmed; call with the lock held, ops
        already internalized)."""
        O.apply(target, m["ops"])
        O.absorb_ids(target)
        own = m.get("client_id") == self.you.get("client_id") and m.get("change_id") in self.pending
        if own:  # (ours, accepted while we were away: the catch-up after a reconnect)
            self.pending.pop(m["change_id"])
            self.answers[m["change_id"]] = {"ok": True, "seq": m["seq"]}
            self.mine.append(m["seq"])
        else:
            self.recent.append({"seq": m["seq"], "author": m.get("author"), "author_name": m.get("author_name", ""),
                                "command": m.get("command", ""), "time": m.get("time"), "reverts": m.get("reverts")})
            self.recent = self.recent[-50:]

    def _applied(self, ops: list) -> dict:
        """confirmed with `ops` applied, as a new dict: confirmed itself is only ever replaced whole, so a change
        that does not apply leaves it as it was."""
        new = copy.deepcopy(self.confirmed)
        O.apply(new, ops)
        O.absorb_ids(new)
        return new

    def _handle(self, m: dict) -> None:
        t = m.get("type")
        if t == "pong":
            return
        if t in ("change", "change.revert.result") and m.get("ops"):
            m = dict(m, ops=self._internalize(m["ops"]))
        with self.cond:
            if t == "change":
                new = copy.deepcopy(self.confirmed)
                self._apply_event(m, new)
                self.confirmed, self.seq = new, m["seq"]
                self.gen += 1
                self.view_due.set()
            elif t == "change.accepted":
                cid = m["change_id"]
                p = self.pending.get(cid)
                if p is not None:
                    fresh = self.view_gen == self.gen
                    if self.seq is None or m["seq"] > self.seq:
                        self.confirmed = self._applied(p["ops"])  # (first: if it does not apply, nothing moved)
                        self.seq = m["seq"]
                    self.pending.pop(cid, None)
                    self.gen += 1
                    if fresh:
                        self.view_gen, self.view_seq = self.gen, self.seq
                    self.mine.append(m["seq"])
                    self.report_due = True
                    self.view_due.set()
                self.answers[cid] = {"ok": True, "seq": m["seq"]}
            elif t == "change.rejected":
                cid = m["change_id"]
                p = self.pending.pop(cid, None)
                self._refused(cid, p, m)
            elif t == "change.revert.result":
                if m.get("ok") and m.get("ops") is not None:
                    self.confirmed = self._applied(m["ops"])
                    self.seq = m["seq"]
                    self.gen += 1
                    self.view_due.set()
                self.requests[m.get("request_id", "")] = m
            elif t == "ids.reserved":
                rid = m.get("request_id", "")
                tag = rid.split(":", 1)[0]
                if tag == "check":
                    nums = [O.split_number(i)[1] for i in self.pool_ids if O.split_number(i)]
                    got = [O.split_number(i)[1] for i in m.get("ids", []) if O.split_number(i)]
                    if nums and got and min(got) <= max(nums):  # forgotten: start the pool again
                        self.pool_ids, self.pool_names = [], {}
                        self.topping.clear()
                        self._reserve_async(POOL_IDS, list(COMMON), tag="pool")
                if rid in self.requests:
                    self.requests[rid] = m
                else:
                    self.pool_ids += [i for i in m.get("ids", []) if i not in self.pool_ids]
                    for name in m.get("names", []):
                        sp = O.split_number(name)
                        if sp:
                            self.pool_names.setdefault(sp[0], []).append(name)
                    self.topping.discard("ids" if m.get("ids") else "")
                    for name in m.get("names", []):
                        sp = O.split_number(name)
                        if sp:
                            self.topping.discard(sp[0])
            elif t == "lock.result":
                self.requests[m.get("request_id", "")] = m
            elif t == "lock.changed":
                self.locks.update(m.get("acquired") or {})
                for target in m.get("released") or []:
                    self.locks.pop(target, None)
            elif t == "peer.joined":
                p = m["peer"]
                self.peers[p["client_id"]] = p
            elif t == "peer.left":
                self.peers.pop(m.get("client_id"), None)
            elif t in ("presence", "draft"):
                p = self.peers.get(m.get("client_id"))
                if p is not None:
                    p[t] = m.get("data")
            elif t == "rebuild.status":
                self.broken = dict(m.get("broken") or {})
            elif t == "version.created":
                self.versions.append({k: m.get(k) for k in ("id", "seq", "name", "author")})
            elif t == "error":
                self.error = f"{m.get('code')}: {m.get('message', '')}"
                rid = m.get("request_id") or ""
                if rid in self.requests:  # (a refused request says which: only that one fails)
                    self.requests[rid] = {**m, "type": "error"}
                elif rid.startswith("pool:"):
                    self.topping.clear()
                elif not rid and m.get("code") in ("bad_reserve", "read_only"):  # (an older server: no request id)
                    for k, v in self.requests.items():
                        if v is None and k.startswith("now:"):
                            self.requests[k] = {**m, "type": "error"}
                    self.topping.clear()
            self.cond.notify_all()
        changed()

    def _refused(self, cid: str, p: dict | None, m: dict) -> None:
        """A change that will not go in (call with the lock held): answer whoever waits for it, keep it among the
        conflicts (with its operations, so it can be made again as a copy), and show the server's state."""
        text = self._explain(m, p["command"] if p else "")
        self.answers[cid] = {"ok": False, "code": m.get("code"), "message": m.get("message", ""), "text": text}
        self.conflicts.append({"time": time.time(), "change_id": cid, "command": p["command"] if p else "",
                               "code": m.get("code"), "message": m.get("message", ""), "text": text,
                               "conflicts": m.get("conflicts") or {}, "who": self._who(m.get("conflicts")),
                               "features": self._features_of(m.get("conflicts")),
                               "base_seq": p["base_seq"] if p else None, "ops": p["ops"] if p else None})
        self.conflicts = self.conflicts[-50:]
        if p is not None:
            self.gen += 1
            self.view_due.set()
        self.cond.notify_all()

    # ------------------------------------------------------------------ explaining refusals
    def _feature_name(self, fid: str) -> str:
        for f in (self.confirmed or {}).get("features") or []:
            if f.get("id") == fid:
                return f.get("name", fid)
        for f in getattr(self.doc, "features", []):
            if f.get("id") == fid:
                return f.get("name", fid)
        return fid

    def _target_label(self, target: str) -> str:
        if target.startswith("f:"):
            return self._feature_name(target[2:])
        if target.startswith("k:"):
            return f"the document's {target[2:]}"
        return {"order": "the feature order", "structure": "the feature tree"}.get(target, target)

    def _who(self, conflicts) -> list[str]:
        return sorted({(v.get("name") or "someone") for v in (conflicts or {}).values() if isinstance(v, dict)
                       and "name" in v} or set())

    def _features_of(self, conflicts) -> list[str]:
        return [self._target_label(t) for t in (conflicts or {}) if not t.startswith("name:")]

    def _explain(self, m: dict, command: str) -> str:
        code, conflicts = m.get("code"), m.get("conflicts") or {}
        who = " and ".join(self._who(conflicts)) or "someone"
        what = ", ".join(self._features_of(conflicts)) or "the same thing"
        cmd = command or "the change"
        if code == "stale":
            return (f"{who} changed {what} while you were editing it: your {cmd} was not applied, and the document "
                    f"shows {who}'s version")
        if code == "locked":
            return f"{what} is being edited by {who} (locked): your {cmd} was not applied"
        if code == "upload_refused":
            return f"your {cmd} could not be sent: {m.get('message', '')}; it was undone"
        if code == "read_only":
            return f"this cloud document is read-only for you ({m.get('message', '')}): your {cmd} was not applied"
        return f"the collaboration server refused your {cmd}: {m.get('message') or code} ({code})"

    # ------------------------------------------------------------------ blobs (long strings travel separately)
    def _http(self):
        return auth.http(self.server, self.token)

    def _internalize(self, obj):
        refs = O.blob_refs(obj)
        if not refs:
            return obj
        missing = [s for s in refs if s not in self.blobs]
        if missing:
            with self._http() as c:
                for sha in missing:
                    r = c.get(f"/api/projects/{self.project_id}/blobs/{sha}")
                    r.raise_for_status()
                    self.blobs[sha] = r.content
        return O.internalize(obj, lambda sha: self.blobs[sha])

    def _externalize(self, obj):
        sink: dict[str, bytes] = {}
        wire = O.externalize(obj, sink)
        if sink:
            with self._http() as c:
                r = c.post(f"/api/projects/{self.project_id}/blobs/missing", json=sorted(sink))
                _upload_ok(r)
                for sha in r.json():
                    _upload_ok(c.put(f"/api/projects/{self.project_id}/blobs/{sha}", content=sink[sha]))
            self.blobs.update(sink)
        return wire

    # ------------------------------------------------------------------ the open document
    def reload_view_locked(self, force: bool = False) -> bool:
        """Show confirmed + pending in the open document (call with APP.lock held). True if it changed."""
        with self.mu:
            if self.confirmed is None or (not force and self.view_gen == self.gen):
                return False
            base, seq, g = copy.deepcopy(self.confirmed), self.seq, self.gen
            pend = [p["ops"] for p in self.pending.values()]
        for ops in pend:  # (one the server will refuse may no longer apply: it is left out until it answers)
            trial = copy.deepcopy(base)
            try:
                O.apply(trial, ops)
                base = trial
            except O.OpError:
                pass
        live = plain(self.doc.to_dict())
        # (this user's view state stays; the journal does not: commands run elsewhere are not in it, so it no
        # longer says how the document was built)
        mine = {k: v for k, v in live.items() if k in O.LOCAL_ONLY and k != "journal"}
        target = {**base, **mine}
        moved = O.fingerprint(live) != O.fingerprint(target)
        if moved:
            self.doc.load_dict(target)
        with self.mu:
            self.view_gen, self.view_seq = g, seq
        return moved

    def _open_here(self) -> bool:
        return any(d is self.doc for d in list(S.APP.documents.values()))

    def _viewer(self) -> None:
        """Bring the open document up to date with everyone's changes, between commands."""
        checked = time.time()
        while not self.stop.is_set():
            self.view_due.wait(0.5)
            if self.stop.is_set():
                return
            if time.time() - checked > 2.0 and S.APP.lock.acquire(timeout=0.5):
                # (under the lock: a preview swaps a throw-away copy in for the document while it runs)
                checked = time.time()
                try:
                    gone = not self._open_here()
                finally:
                    S.APP.lock.release()
                if gone:  # the document was closed (or replaced): stop syncing it
                    threading.Thread(target=self.close, daemon=True).start()
                    return
            with self.mu:
                due = self.confirmed is not None and (self.view_gen != self.gen or self.report_due)
            if not due:
                self.view_due.clear()
                continue
            if not S.APP.lock.acquire(timeout=0.5):
                continue
            try:
                if self.busy:  # (a command on this document let go of the lock for a while: not now)
                    time.sleep(0.05)
                    continue
                if not self._open_here():  # the document was closed (or replaced): stop syncing it
                    threading.Thread(target=self.close, daemon=True).start()
                    return
                self.view_due.clear()
                if self.reload_view_locked():
                    S.APP.bump("collab_sync", {"document": self.doc.name, "seq": self.view_seq}, True, "", "collab")
                self._report_locked()
            except Exception:  # noqa: BLE001 - keep syncing; the next change tries again
                log.exception("could not update %s from the server", self.document_id)
                time.sleep(0.5)
            finally:
                S.APP.lock.release()

    def _report_locked(self) -> None:
        """rebuild.report for the state the open document shows, when it is exactly a server state."""
        with self.mu:
            ok = (not self.pending and self.view_gen == self.gen and self.view_seq is not None
                  and self.view_seq > self.last_report and not self.read_only and self.connected)
            self.report_due = False
            seq = self.view_seq
        if not ok:
            return
        ids = {f["name"]: f["id"] for f in self.doc.features}
        errors = {ids[k]: str(v["error"])[:500] for k, v in (getattr(self.doc, "state", {}) or {}).items()
                  if isinstance(v, dict) and v.get("error") and k in ids}
        if self._try_send({"type": "rebuild.report", "seq": seq, "errors": errors}):
            with self.mu:
                self.last_report = max(self.last_report, seq)

    # ------------------------------------------------------------------ local changes (command thread, APP.lock held)
    def submit(self, ops: list, command: str, args: dict | None) -> str:
        """Queue a change and send it (with the changes queued before it, in order); the open document already
        shows it. Its long strings are uploaded when it is sent: offline it simply waits in the queue."""
        try:
            small = plain(args or {})
            small = small if len(json.dumps(small)) <= 4096 else None
        except (TypeError, ValueError):
            small = None
        with self.cond:
            self.n += 1
            cid = f"{uuid.uuid4().hex[:16]}-{self.n}"  # (unique for good: the server remembers every id it saw)
            fresh = self.view_gen == self.gen
            self.pending[cid] = {"ops": ops, "wire": None, "sent": False, "base_seq": self.view_seq,
                                 "command": command, "args": small}
            self.gen += 1
            if fresh:
                self.view_gen = self.gen
            self.redo_stack.clear()
        if self.connected:
            self._flush()
        changed()
        return cid

    def _unsent(self) -> bool:
        return any(not p.get("sent") for p in list(self.pending.values()))

    def _flush(self) -> None:
        """Send every queued change not yet sent on this connection, in queue order, uploading its blobs first.
        One whose upload fails for want of a network stops the queue (nothing overtakes it) until the next try;
        one the server refuses to take is dropped as refused."""
        if time.time() < getattr(self, "_flush_after", 0.0) and threading.current_thread() in self._threads:
            return  # (the network thread retries a failed upload every few seconds, not every message)
        refused = []
        with self.send_mu:  # (never take self.mu inside: _handle sends while holding it)
            ws = self.ws
            if ws is None:
                return
            for cid, p in list(self.pending.items()):
                if p.get("sent"):
                    continue
                if p.get("wire") is None:
                    try:
                        p["wire"] = self._externalize(p["ops"])
                    except _UploadRefused as exc:
                        refused.append((cid, str(exc)))
                        continue
                    except Exception as exc:  # noqa: BLE001 - the network: later, and in order
                        log.info("could not upload for %s yet: %s", self.document_id, exc)
                        self._flush_after = time.time() + 3.0
                        break
                try:
                    ws.send(json.dumps({"type": "change", "change_id": cid, "base_seq": p["base_seq"],
                                        "ops": p["wire"], "command": p["command"], "args": p.get("args")}))
                except Exception:  # noqa: BLE001 - the connection went: the network thread reconnects
                    break
                p["sent"] = True
        for cid, text in refused:
            with self.cond:
                self._refused(cid, self.pending.pop(cid, None), {"code": "upload_refused", "message": text})
            changed()

    def wait_answer(self, cid: str, timeout: float) -> dict | None:
        end = time.time() + timeout
        with self.cond:
            while cid not in self.answers and self.connected:
                left = end - time.time()
                if left <= 0:
                    break
                self.cond.wait(left)
            got = self.answers.pop(cid, None)
            if len(self.answers) > 500:  # (answers nobody waited for: keep the newest)
                for k in list(self.answers)[:250]:
                    del self.answers[k]
            return got

    def commit_local(self, ops: list, command: str, args: dict | None) -> None:
        if self.read_only:
            self.reload_view_locked(force=True)
            raise DocumentError(f"this cloud document is read-only for you ({self.read_only_reason or 'viewer'}); the "
                                f"change was undone")
        cid = self.submit(ops, command, args)
        p = self.pending.get(cid)
        if p is not None and (not self.connected or not p.get("sent")):
            return  # (queued: sent when the connection, or the upload, works again)
        ans = self.wait_answer(cid, wait_seconds())
        if ans is not None and not ans["ok"]:
            self.reload_view_locked(force=True)
            raise DocumentError(ans["text"])

    def _request(self, msg: dict, tag: str, timeout: float) -> dict | None:
        rid = f"{tag}:{uuid.uuid4().hex[:12]}"
        with self.cond:
            self.requests[rid] = None
        try:
            if not self._try_send({**msg, "request_id": rid}):
                return None
            end = time.time() + timeout
            with self.cond:
                while self.requests.get(rid) is None and self.connected:
                    left = end - time.time()
                    if left <= 0:
                        break
                    self.cond.wait(left)
                return self.requests.get(rid)
        finally:
            with self.cond:
                self.requests.pop(rid, None)

    # ------------------------------------------------------------------ ids and names
    def _reserve_async(self, features: int, names: list[str], tag: str = "pool") -> None:
        with self.mu:
            if features and tag == "pool":
                if "ids" in self.topping:
                    features = 0
                else:
                    self.topping.add("ids")
            names = [n for n in names if n not in self.topping] if tag == "pool" else names
            self.topping.update(names)
        if features or names:
            self._try_send({"type": "ids.reserve", "request_id": f"{tag}:{uuid.uuid4().hex[:8]}",
                            "features": features, "names": names})

    def _reserve_now(self, features: int, names: list[str]) -> None:
        if self.read_only:
            raise DocumentError(f"this cloud document is read-only for you ({self.read_only_reason or 'viewer'})")
        if not self.connected:
            raise DocumentError(NEED_CONNECTION)
        got = self._request({"type": "ids.reserve", "features": features, "names": names}, "now", 10.0)
        if got is None or got.get("type") != "ids.reserved":
            raise DocumentError(NEED_CONNECTION if got is None else
                                f"the collaboration server would not reserve feature ids: {got.get('message', '')}")
        with self.mu:
            self.pool_ids += got.get("ids", [])
            for name in got.get("names", []):
                sp = O.split_number(name)
                if sp:
                    self.pool_names.setdefault(sp[0], []).append(name)

    def take_id(self, doc) -> str:
        used = {f["id"] for f in doc.features}
        for attempt in range(2):
            with self.mu:
                while self.pool_ids:
                    fid = self.pool_ids.pop(0)
                    if fid not in used:
                        self.taken.append(("id", fid, ""))
                        low = len(self.pool_ids) < POOL_IDS // 2
                        break
                else:
                    fid = None
            if fid is not None:
                if low and self.connected:
                    self._reserve_async(POOL_IDS, [])
                return fid
            if attempt == 0:
                self._reserve_now(POOL_IDS, [])
        raise DocumentError(NEED_CONNECTION)

    def take_name(self, doc, prefix: str) -> str:
        used = {f["name"] for f in doc.features}
        if not 0 < len(prefix) <= 40 or prefix[-1].isdigit():  # (the server reserves only such stems)
            n = 1
            while f"{prefix}{n}" in used:
                n += 1
            return f"{prefix}{n}"
        for attempt in range(2):
            with self.mu:
                names = self.pool_names.get(prefix) or []
                name = None
                while names:
                    cand = names.pop(0)
                    if cand not in used:
                        name = cand
                        self.taken.append(("name", cand, prefix))
                        break
                empty = not names
            if name is not None:
                if empty and self.connected:
                    self._reserve_async(0, [prefix])
                return name
            if attempt == 0:
                self._reserve_now(0, [prefix, prefix])
        raise DocumentError(NEED_CONNECTION)

    def give_back_unused(self, doc) -> None:
        """Ids and names drawn by a command that did not keep them (it failed, or threw a feature away) go back."""
        with self.mu:
            taken, self.taken = self.taken, []
            if not taken:
                return
            ids = {f["id"] for f in doc.features}
            names = {f["name"] for f in doc.features}
            for kind, value, prefix in reversed(taken):
                if kind == "id" and value not in ids:
                    self.pool_ids.insert(0, value)
                elif kind == "name" and value not in names:
                    self.pool_names.setdefault(prefix, []).insert(0, value)

    # ------------------------------------------------------------------ undo / redo (command thread)
    def _settle_pending(self) -> None:
        end = time.time() + wait_seconds()
        with self.cond:
            while self.pending and self.connected and time.time() < end:
                self.cond.wait(max(0.0, end - time.time()))
            if self.pending:
                raise DocumentError("your last change has not reached the collaboration server yet; undo it when it "
                                    "has (the server is not reachable now)" if not self.connected else
                                    "your last change has not been answered by the server yet; try again")

    def _revert(self, seq: int) -> dict:
        if not self.connected:
            raise DocumentError("undo in a cloud document needs the collaboration server, which is not reachable now")
        res = self._request({"type": "change.revert", "seq": seq, "mode": "undo"}, "revert", wait_seconds() or 15.0)
        if res is None:
            raise DocumentError("the collaboration server did not answer the undo; try again")
        if not res.get("ok"):
            code = res.get("code")
            who = " and ".join(self._who(res.get("conflicts"))) or "someone"
            what = ", ".join(self._features_of(res.get("conflicts"))) or "the same thing"
            if code == "changed_since":
                raise DocumentError(f"cannot undo: {who} changed {what} after you ({res.get('message', '')})")
            raise DocumentError(f"cannot undo: {res.get('message') or code}")
        return res

    def undo(self) -> dict:
        self._settle_pending()
        with self.mu:
            if not self.mine:
                raise DocumentError("nothing of yours to undo in this cloud document (undo takes back only your own "
                                    "changes made since you opened it)")
            seq = self.mine[-1]
        try:
            res = self._revert(seq)
        except DocumentError as exc:
            if "already undone" in str(exc) or "no change" in str(exc):
                with self.mu:
                    self.mine.remove(seq)
            raise
        with self.mu:
            self.mine.remove(seq)
            self.redo_stack.append(res["seq"])
        self.reload_view_locked()
        return {**self.doc.summary(), "undone": seq, "seq": res["seq"]}

    def redo(self) -> dict:
        self._settle_pending()
        with self.mu:
            if not self.redo_stack:
                raise DocumentError("nothing to redo in this cloud document")
            seq = self.redo_stack[-1]
        res = self._revert(seq)
        with self.mu:
            self.redo_stack.remove(seq)
            self.mine.append(res["seq"])
        self.reload_view_locked()
        return {**self.doc.summary(), "redone": seq, "seq": res["seq"]}

    # ------------------------------------------------------------------ locks, presence
    def feature_target(self, name: str) -> str:
        if name == "structure" or name.startswith(("f:", "k:", "i:")):
            return name
        for f in self.doc.features:
            if f["name"] == name or f["id"] == name:
                return f"f:{f['id']}"
        raise DocumentError(f"no feature named {name!r} (or give a lock target: f:<id>, k:<key>, structure)")

    def lock(self, targets: list[str]) -> dict:
        if not self.connected:
            raise DocumentError("locking needs the collaboration server, which is not reachable now")
        res = self._request({"type": "lock.acquire", "targets": targets}, "lock", 10.0)
        if res is None:
            raise DocumentError("the collaboration server did not answer the lock request; try again")
        return {"granted": res.get("granted", []), "denied": res.get("denied", {})}

    def unlock(self, targets: list[str]) -> None:
        if self.connected:
            self._try_send({"type": "lock.release", "targets": targets})

    def presence(self, data: dict) -> bool:
        return self.connected and self._try_send({"type": "presence", "data": data})

    def draft(self, data: dict | None) -> bool:
        return self.connected and self._try_send({"type": "draft", "data": data})

    # ------------------------------------------------------------------ reading
    def settled(self) -> bool:
        with self.mu:
            return self.connected and not self.pending and self.view_gen == self.gen

    def backup_snapshot(self) -> tuple[int, dict] | None:
        with self.mu:
            if self.confirmed is None or self.seq is None:
                return None
            return self.seq, {**copy.deepcopy(self.confirmed), "name": self.doc.name}

    def status(self) -> dict:
        with self.mu:
            me = self.you.get("client_id")
            return {
                "document_id": self.document_id, "document": self.doc.name, "cloud_name": self.cloud_name,
                "project_id": self.project_id, "server": self.server, "state": self.state, "error": self.error,
                "connected": self.connected, "seq": self.seq, "role": self.role, "read_only": self.read_only,
                "read_only_reason": self.read_only_reason, "you": self.you, "pending": len(self.pending),
                "up_to_date": self.connected and not self.pending and self.view_gen == self.gen,
                "peers": [{**p, "you": p.get("client_id") == me} for p in self.peers.values()],
                "locks": {t: {**h, "target": self._target_label(t), "mine": h.get("client_id") == me}
                          for t, h in self.locks.items()},
                "broken": {fid: {**b, "feature": self._feature_name(fid)} for fid, b in self.broken.items()},
                "conflicts": list(self.conflicts), "recent": list(self.recent[-20:]),
                "versions": list(self.versions), "can_undo": bool(self.mine), "can_redo": bool(self.redo_stack),
                "pool": {"ids": len(self.pool_ids), "names": {k: len(v) for k, v in self.pool_names.items() if v}},
            }


# ---------------------------------------------------------------------- the command hook
class Hook:
    """Turns each outermost mutating command into one change per cloud document it changed (see
    session.COMMAND_HOOKS). Every open cloud document is written out before the command, not only the active one:
    a command (a batch, a macro) may switch documents or change another one."""
    SKIP = {"undo", "redo"}

    def before(self, name: str, arguments):
        if name in self.SKIP or name.startswith("collab_"):
            return None
        snaps = []
        for c in list(CLIENTS.values()):
            if c.closed or not c._open_here():  # (called with APP.lock held: a preview's copy is not in place)
                continue
            snaps.append((c, c.doc, plain(c.doc.to_dict()), c.doc.dirty))
        for c, *_ in snaps:
            with c.mu:
                c.taken = []
            c.busy = True
        return (snaps, name, dict(arguments)) if snaps else None

    def after(self, token, ok: bool) -> None:
        if token is None:
            return
        snaps, name, args = token
        first: DocumentError | None = None
        for c, doc, before, dirty in snaps:
            try:
                if c.closed:
                    continue
                c.give_back_unused(doc)
                ops = O.diff(before, plain(doc.to_dict()))
                if not ops:
                    continue
                if not ok:  # (a failed command that left something changed: back to the shared state)
                    c.reload_view_locked(force=True)
                    doc.dirty = dirty
                    continue
                c.commit_local(ops, name, args)
            except DocumentError as exc:  # (refused: commit_local already put the server's state back)
                doc.dirty = dirty  # (and nothing of it is left to save)
                first = first or exc
            except Exception as exc:  # noqa: BLE001 - never leave an edit on screen that is not on its way
                log.exception("could not send a change to %s", c.document_id)
                try:
                    c.reload_view_locked(force=True)
                except Exception:  # noqa: BLE001
                    log.exception("could not restore %s", c.document_id)
                first = first or DocumentError(f"the change to {doc.name} could not be sent to the collaboration "
                                               f"server ({type(exc).__name__}: {exc}) and was undone")
            finally:
                c.busy = False
        if first is not None and ok:
            raise first

    def redirect(self, name: str, doc):
        c = client_for(doc)
        if c is None or name not in ("undo", "redo"):
            return None
        return c.undo() if name == "undo" else c.redo()


HOOK = Hook()


def attach(doc, document_id: str, server: str, token: str, info: dict, timeout: float = 30.0) -> DocClient:
    """Connect an open document to its cloud document (call with APP.lock held, from a command) and show the
    server's state in it."""
    old = CLIENTS.get(document_id)
    if old is not None:
        old.close()
    c = DocClient(doc, document_id, server, token, info)
    CLIENTS[document_id] = c
    doc._collab = c
    doc.allocator = c
    S.add_command_hook(HOOK)
    c.start()
    try:
        c.wait_welcome(timeout)
    except DocumentError:
        c.close()
        raise
    c.reload_view_locked(force=True)
    from . import backup
    backup.start()
    changed()
    return c


# ---------------------------------------------------------------------- locks a page of the UI holds
OWNED: dict[str, set[tuple[str, str]]] = {}  # owner (a browser page's id) -> {(cloud document id, lock target)}
_OWNED_MU = threading.Lock()


def own(owner: str, document_id: str, targets) -> None:
    with _OWNED_MU:
        OWNED.setdefault(owner, set()).update((document_id, t) for t in targets)


def disown(owner: str, document_id: str, targets) -> None:
    with _OWNED_MU:
        held = OWNED.get(owner)
        if held is not None:
            held.difference_update((document_id, t) for t in targets)
            if not held:
                del OWNED[owner]


def release_owner(owner: str) -> int:
    """Free every lock `owner` still holds (its page closed or crashed). Returns how many."""
    with _OWNED_MU:
        held = OWNED.pop(owner, set())
    for document_id, target in held:
        c = CLIENTS.get(document_id)
        if c is not None and not c.closed:
            c.unlock([target])
    return len(held)


def status() -> dict:
    """Everything a screen needs about collaboration, without taking the command lock."""
    return {"revision": REVISION[0], "documents": [c.status() for c in list(CLIENTS.values())]}
