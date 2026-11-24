"""Relay MCP between stdio and a running openswx's HTTP endpoint (`openswx mcp`, when openswx is already open).

Chat apps such as Claude Desktop start MCP servers as local programs over stdio; they cannot always reach
an HTTP address, and Claude on the web cannot reach this computer's at all. When an openswx window (or
`openswx serve`) is already running, `openswx mcp` relays each JSON-RPC message from stdin to its
http://127.0.0.1:<port>/mcp, with the user's token, and writes the replies (plain JSON or a server-sent event
stream) back to stdout. The agent then works in the open window, and the edits show live. Without a running
openswx, `openswx mcp` serves MCP itself, as before.
"""
from __future__ import annotations

import json
import sys
import threading


def find_running(port: int = 8765, host: str = "127.0.0.1") -> str | None:
    """The base URL of an openswx answering on this computer at `port`, or None."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1.0) as r:
            info = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - nothing there, or something else on the port
        return None
    return f"http://{host}:{port}" if isinstance(info, dict) and info.get("app") == "openswx" else None


def _sse_messages(lines):
    """JSON-RPC messages from a server-sent event stream (data: lines; an empty line ends an event)."""
    data: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line == "" and data:
            yield json.loads("\n".join(data))
            data = []
    if data:
        yield json.loads("\n".join(data))


class Bridge:
    def __init__(self, url: str, token: str, out=None):
        import httpx
        self.url = url.rstrip("/") + "/"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"}
        self.client = httpx.Client(timeout=httpx.Timeout(10.0, read=None))  # (a long solve may take minutes)
        self.out = out or sys.stdout
        self.lock = threading.Lock()

    def write(self, msg: dict) -> None:
        with self.lock:
            self.out.write(json.dumps(msg, separators=(",", ":")) + "\n")
            self.out.flush()

    def send(self, msg: dict) -> None:
        """POST one message; write whatever comes back. Errors are answered as JSON-RPC errors."""
        try:
            with self.client.stream("POST", self.url, headers=self.headers, content=json.dumps(msg)) as r:
                sid = r.headers.get("mcp-session-id")
                if sid:
                    self.headers["Mcp-Session-Id"] = sid
                if r.status_code == 202:
                    return
                if r.status_code >= 400:
                    r.read()
                    raise RuntimeError(f"openswx answered HTTP {r.status_code}: {r.text[:200]}")
                if "text/event-stream" in r.headers.get("content-type", ""):
                    for m in _sse_messages(r.iter_lines()):
                        self._note(m)
                        self.write(m)
                else:
                    r.read()
                    if r.content.strip():
                        m = json.loads(r.content)
                        for one in (m if isinstance(m, list) else [m]):
                            self._note(one)
                            self.write(one)
        except Exception as exc:  # noqa: BLE001 - the client must get an answer, not a hang
            if "id" in msg and msg.get("method"):
                self.write({"jsonrpc": "2.0", "id": msg["id"],
                            "error": {"code": -32603, "message": f"openswx relay: {exc}"}})

    def _note(self, m: dict) -> None:
        version = (m.get("result") or {}).get("protocolVersion") if isinstance(m, dict) else None
        if version:  # later requests name the protocol version agreed at initialisation
            self.headers["MCP-Protocol-Version"] = version

    def close(self) -> None:
        if "Mcp-Session-Id" in self.headers:
            try:
                self.client.delete(self.url, headers=self.headers)
            except Exception:  # noqa: BLE001
                pass
        self.client.close()

    def run(self, inp=None) -> None:
        """Relay until stdin closes. initialize is relayed first, on its own (it opens the session); after
        that each message goes in its own thread, so a long tool call does not hold up pings or cancels."""
        inp = inp or sys.stdin
        threads = []
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and (msg.get("method") == "initialize" or "Mcp-Session-Id" not in self.headers):
                self.send(msg)
                continue
            t = threading.Thread(target=self.send, args=(msg,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=5)
        self.close()


def run(url: str, token: str) -> None:
    Bridge(url, token).run()
