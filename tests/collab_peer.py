"""One openswx for test_collab_live.py: a separate process (each has its own APP), driven over stdin/stdout.

Requests are JSON lines:
    {"call": name, "args": {...}}       run a command as the UI would; -> {"result": ...} or {"error": "..."}
    {"settle": seq, "timeout": s}       wait until the active cloud document is up to date with the server (at
                                        least at `seq`, nothing pending, shown on screen); -> {"digest": ...}
    {"digest": true}                    -> {"digest": ...} now
    {"offline": s}                      wait until the connection is seen to be lost; -> {"state": ...}
Every answer is one JSON line.
"""
from __future__ import annotations

import faulthandler
import hashlib
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("OPENSWX_HOME", tempfile.mkdtemp(prefix="openswx-collab-peer-"))
for _k in ("OPENSWX_PLUGINS", "OPENSWX_AUTOSAVE", "OPENSWX_LOG", "OPENSWX_CACHE"):
    os.environ.setdefault(_k, "off")
os.environ.setdefault("OPENSWX_ISOLATE", "off")

from openswx import session as S  # noqa: E402

OUT = sys.stdout
sys.stdout = sys.stderr  # (only answers on the real stdout)


def _client():
    from openswx.collab import client
    return client.client_for(S.APP.documents.get(S.APP.active)) if S.APP.active else None


def digest() -> dict:
    from openswx_collab_protocol import ops as O
    from openswx.collab import client
    with S.APP.lock:
        doc = S.APP.active_doc
        c = client.client_for(doc)
        live = client.plain(doc.to_dict())
        topo = doc.topology()
        names = sorted(str(f.get("name")) for f in topo["faces"]) + sorted(str(e.get("name")) for e in topo["edges"])
        st = c.status() if c else {}
        return {"seq": c.seq if c else None, "doc": O.fingerprint(live),
                "confirmed": O.fingerprint(c.confirmed) if c else None,
                "names": hashlib.sha1("\n".join(names).encode()).hexdigest(), "faces": len(topo["faces"]),
                "volume": round(S.call("get_mass_properties")["volume"], 6) if doc.body is not None else 0.0,
                "features": [[f["id"], f["name"]] for f in doc.features],
                "errors": {f["name"]: (doc.state.get(f["name"]) or {}).get("error") for f in doc.features
                           if (doc.state.get(f["name"]) or {}).get("error")},
                "pending": st.get("pending"), "state": st.get("state")}


def settle(seq: int | None, timeout: float) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        c = _client()
        if c is not None and c.settled() and (seq is None or (c.seq or 0) >= seq):
            with S.APP.lock:  # (the view thread may be about to show a change: take it between commands)
                if c.settled() and c.view_gen == c.gen:
                    return digest()
        time.sleep(0.05)
    c = _client()
    raise TimeoutError(f"not settled after {timeout} s: {c.status() if c else 'no cloud document'}")


def main() -> None:
    OUT.write(json.dumps({"ready": True}) + "\n")
    OUT.flush()
    for line in sys.stdin:
        req = json.loads(line)
        faulthandler.dump_traceback_later(150, file=sys.stderr)  # (stuck: show where, for the test's report)
        try:
            if "call" in req:
                try:
                    out = {"result": S.call(req["call"], req.get("args") or {}, origin="ui")}
                except S.CommandError as exc:
                    out = {"error": str(exc)}
            elif "settle" in req:
                out = {"digest": settle(req["settle"], req.get("timeout", 60))}
            elif "offline" in req:
                end = time.time() + req["offline"]
                while time.time() < end and _client() is not None and _client().connected:
                    time.sleep(0.05)
                out = {"state": _client().state if _client() else None}
            else:
                out = {"digest": digest()}
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            out = {"crash": f"{type(exc).__name__}: {exc}"}
        faulthandler.cancel_dump_traceback_later()
        OUT.write(json.dumps(out, default=str) + "\n")
        OUT.flush()


if __name__ == "__main__":
    main()
