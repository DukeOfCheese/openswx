from __future__ import annotations

import hmac
import json
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

COOKIE = "openswx_token"
VIEW_COOKIE = "openswx_view"
# what a view-only link may ask for: the page, the model as shown, live updates, pictures - and queries
VIEW_GET = ("/api/state", "/api/mesh", "/api/mesh.bin", "/api/events", "/api/image", "/api/commands",
            "/api/drawing", "/api/health")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
OPEN_PATHS = {"/api/health"}  # (liveness only: says whether a command is running)


def is_loopback(host: str) -> bool:
    return host.strip("[]").lower() in LOOPBACK


def token_path() -> Path:
    return Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx") / "token"


def user_token() -> str:
    """The user's token: OPENSWX_TOKEN, else the saved one (made on first use, readable by the user only)."""
    env = os.environ.get("OPENSWX_TOKEN", "").strip()
    if env:
        return env
    p = token_path()
    try:
        saved = p.read_text(encoding="ascii").strip()
        if len(saved) >= 32:
            return saved
    except (OSError, UnicodeError):
        pass
    tok = secrets.token_urlsafe(32)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(tok)
    except OSError:  # (no home folder to write to: the token then lasts for this run)
        os.environ["OPENSWX_TOKEN"] = tok
    return tok


def view_token() -> str:
    """The token of view-only links (serve --share): separate from the user's own, so a link can be handed out
    and later invalidated (delete the file) without touching agents' access."""
    p = token_path().with_name("view_token")
    try:
        saved = p.read_text(encoding="ascii").strip()
        if len(saved) >= 32:
            return saved
    except (OSError, UnicodeError):
        pass
    tok = secrets.token_urlsafe(32)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(tok)
    except OSError:
        pass
    return tok


def _hostname(netloc: str) -> str:
    """'127.0.0.1:8765' -> '127.0.0.1', '[::1]:8765' -> '::1'."""
    netloc = netloc.strip().lower()
    if netloc.startswith("["):
        return netloc[1:].split("]", 1)[0]
    return netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc


NO_TOKEN_PAGE = """<!doctype html><meta charset="utf-8"><title>openswx</title>
<body style="font:15px system-ui;max-width:40em;margin:4em auto;line-height:1.5">
<h2>openswx</h2><p>This page needs your openswx token. Open the link that openswx printed when it started
(it ends in <code>/?token=...</code>), or run <code>openswx token</code> and open
<code>http://%s/?token=&lt;the token&gt;</code>.</p>"""


class Guard:
    """ASGI middleware applying the rules above. token=None: no token is asked for (the other checks stay)."""

    def __init__(self, app, token: str | None, loopback_only: bool = True, view_token: str | None = None):
        self.app, self.token, self.loopback_only, self.view_token = app, token, loopback_only, view_token

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        path, method = scope.get("path", ""), scope.get("method", "GET")
        host = headers.get("host", "")

        async def refuse(status: int, message: str, html: str | None = None, extra=()):
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            body = (html if html is not None else json.dumps({"ok": False, "error": message})).encode()
            ctype = b"text/html; charset=utf-8" if html is not None else b"application/json"
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode()),
                                    (b"cache-control", b"no-store"), *extra]})
            await send({"type": "http.response.body", "body": body})

        if self.loopback_only and not is_loopback(_hostname(host)):
            return await refuse(403, "openswx only answers requests addressed to this computer (bad Host header)")
        origin = headers.get("origin")
        if origin is not None and urlsplit(origin).netloc.lower() != host.lower():
            return await refuse(403, "request from another origin refused")
        if headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return await refuse(403, "request from another site refused")
        api = path.startswith("/api/")
        if api and method == "POST" and headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return await refuse(415, "POST bodies must be application/json")
        if self.token is None:
            return await self.app(scope, receive, send)

        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if path == "/" and "view" in query and self.view_token:  # a view-only link: its own cookie
            if not hmac.compare_digest(query["view"][0].encode(), self.view_token.encode()):
                return await refuse(401, "wrong link", NO_TOKEN_PAGE % host)
            cookie = f"{VIEW_COOKIE}={self.view_token}; Path=/; HttpOnly; SameSite=Strict"
            return await refuse(303, "", "", extra=[(b"location", b"/"), (b"set-cookie", cookie.encode())])
        if path == "/" and "token" in query:  # the link openswx prints: keep the token as a cookie, drop it from the URL
            if not self._ok(query["token"][0]):
                return await refuse(401, "wrong token", NO_TOKEN_PAGE % host)
            cookie = f"{COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Strict"
            return await refuse(303, "", "", extra=[(b"location", b"/"), (b"set-cookie", cookie.encode())])
        if path in OPEN_PATHS or not (api or path == "/" or path == "/mcp" or path.startswith("/mcp/")):
            return await self.app(scope, receive, send)  # (static files of the UI)
        supplied = [headers.get("x-openswx-token", "")]
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied.append(auth[7:].strip())
        for part in headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                supplied.append(value)
        if any(self._ok(s) for s in supplied):
            return await self.app(scope, receive, send)
        if self.view_token:  # a viewer: reads, and the commands web_server lets a viewer run
            viewer = [headers.get("x-openswx-view", "")]
            for part in headers.get("cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == VIEW_COOKIE:
                    viewer.append(value)
            if any(v and hmac.compare_digest(v.encode(), self.view_token.encode()) for v in viewer):
                allowed = (method == "GET" and (path == "/" or path in VIEW_GET)) or (method == "POST" and path == "/api/command")
                if not allowed:
                    return await refuse(403, "this is a view-only link")
                scope["openswx.view_only"] = True
                return await self.app(scope, receive, send)
        if path == "/":
            return await refuse(401, "token needed", NO_TOKEN_PAGE % host)
        return await refuse(401, "openswx token needed: send 'Authorization: Bearer <token>' (the token: `openswx token`)",
                            extra=[(b"www-authenticate", b"Bearer")])

    def _ok(self, supplied: str) -> bool:
        return bool(supplied) and hmac.compare_digest(supplied.encode(), self.token.encode())
