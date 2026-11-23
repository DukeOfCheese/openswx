"""Signing in to a collaboration server, and remembering it.

Kept in `<home>/collab/` (home: OPENSWX_HOME, else ~/.openswx):
    config.json   {"server": "https://...", "user": {...}}
    token         the session token, readable by this user only (POSIX: mode 0600; Windows: an access list
                  that names this user alone, set with icacls)

`client_id` is one random string per run of openswx (made when it is first needed, kept across reconnects of
that run): the server uses it (with the user id) to recognise this app across a dropped connection, so locks
and reserved ids survive it. Per run, not per installation: the desktop app and an MCP process on the same
machine are two clients, and two apps with one id would keep disconnecting each other.

Browser sign-in (Google, GitHub): a one-off HTTP listener on 127.0.0.1 receives the code the server sends the
browser back with; the code is worth nothing without the `code_verifier` that only this process knows (PKCE:
the server was given its SHA-256, in lower-case hex).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class AuthError(ValueError):
    pass


def folder() -> Path:
    return Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx") / "collab"


def _config_path() -> Path:
    return folder() / "config.json"


def _token_path() -> Path:
    return folder() / "token"


def config() -> dict:
    try:
        d = json.loads(_config_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_config(d: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, p)


_RUN_ID: list[str] = []


def client_id() -> str:
    """This run's id (made once per process)."""
    if not _RUN_ID:
        _RUN_ID.append(secrets.token_hex(16))
    return _RUN_ID[0]


def server() -> str | None:
    s = config().get("server")
    return s if isinstance(s, str) and s else None


def user() -> dict | None:
    u = config().get("user")
    return u if isinstance(u, dict) else None


def token() -> str | None:
    try:
        t = _token_path().read_text(encoding="ascii").strip()
        return t or None
    except (OSError, UnicodeError):
        return None


def _save_token(tok: str) -> None:
    p = _token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.unlink(missing_ok=True)  # (os.open's mode applies to new files only)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        if os.name == "nt":
            _owner_only(p)  # (before the token is in it)
        fh.write(tok)


def _owner_only(p: Path) -> None:
    """Windows ignores the mode bits: give the file an access list of its own that lets this user alone in (the
    profile folder already keeps others out, but OPENSWX_HOME may be on a shared drive). Best effort: icacls
    is part of Windows; if it fails the file keeps the folder's permissions and a warning is logged."""
    import getpass
    import logging
    import subprocess
    try:
        user = os.environ.get("USERNAME") or getpass.getuser()
        domain = os.environ.get("USERDOMAIN")
        who = domain + "\\" + user if domain else user
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # /reset first: some machines give new files explicit entries (SYSTEM, Administrators, OWNER RIGHTS) that
        # removing inheritance would leave in place; after a reset every entry is inherited, and then goes
        for args in (["/reset"], ["/inheritance:r", "/grant:r", f"{who}:F"]):
            r = subprocess.run(["icacls", str(p), *args], capture_output=True, text=True, timeout=20, creationflags=flags)
            if r.returncode != 0:
                raise OSError(r.stdout.strip() or r.stderr.strip())
    except (OSError, KeyError, subprocess.SubprocessError) as exc:
        logging.getLogger("openswx.collab").warning("could not restrict access to %s: %s", p, exc)


def normalise(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise AuthError("give the collaboration server's address, e.g. https://collab.example.com")
    if "://" not in url:
        url = "https://" + url
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise AuthError(f"{url!r} is not a server address (http:// or https://)")
    return url


def _remember(url: str, got: dict) -> dict:
    if not isinstance(got, dict) or not isinstance(got.get("token"), str):
        raise AuthError("the server's answer has no session token")
    _save_token(got["token"])
    d = config()
    d.update(server=url, user=got.get("user") or {})
    _save_config(d)
    return {"server": url, "user": got.get("user") or {}}


def http(url: str | None = None, tok: str | None = None, transport=None):
    """An HTTP client for the server, signed in when there is a token."""
    import httpx
    url = url or server()
    if not url:
        raise AuthError("not signed in to a collaboration server (collab_sign_in)")
    tok = token() if tok is None else tok
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    kw = {"transport": transport} if transport is not None else {}
    return httpx.Client(base_url=url, headers=headers, timeout=30, **kw)


def error_text(r) -> str:
    try:
        d = r.json()
        msg = d.get("detail", d) if isinstance(d, dict) else d
        if isinstance(msg, dict):
            msg = msg.get("message") or json.dumps(msg)
        elif isinstance(msg, list):  # (validation errors)
            msg = "; ".join(str(e.get("msg", e)) if isinstance(e, dict) else str(e) for e in msg)
    except ValueError:
        msg = r.text[:200]
    return f"{msg} (HTTP {r.status_code})"


def sign_in_password(url: str, email: str, password: str, transport=None) -> dict:
    url = normalise(url)
    with http(url, "", transport) as c:
        r = c.post("/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        raise AuthError(f"sign-in refused: {error_text(r)}")
    return _remember(url, r.json())


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge): 64 random URL-safe characters, and their SHA-256 in lower-case hex."""
    verifier = secrets.token_urlsafe(48)
    return verifier, hashlib.sha256(verifier.encode("ascii")).hexdigest()


_PAGE = ("<!doctype html><meta charset=utf-8><title>openswx</title><body style='font-family:sans-serif;margin:3em'>"
         "<h2>{}</h2><p>You can close this window and go back to openswx.</p>")
ERRORS = {
    "invite_required": "this server needs an invitation to create an account",
    "account_exists_use_password": "an account with this address exists: sign in with its password, then add the "
                                   "provider from your account",
    "identity_in_use": "that Google / GitHub account is already linked to another openswx account",
    "no_verified_email": "the provider did not give a verified e-mail address",
    "provider_refused": "the provider refused the sign-in",
    "provider_unreachable": "the server could not reach the provider; try again",
    "cancelled": "the sign-in was cancelled",
}


def sign_in_browser(url: str, provider: str, invite: str | None = None, open_browser=None, timeout: float = 300.0,
                    transport=None) -> dict:
    """Sign in with Google or GitHub: opens the system browser, waits (up to `timeout` s) for it to come back."""
    import webbrowser
    url = normalise(url)
    if provider not in ("google", "github"):
        raise AuthError("provider must be google or github")
    verifier, challenge = pkce_pair()
    got: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if urllib.parse.urlsplit(self.path).path != "/callback" or not ({"code", "error"} & set(q)):
                self.send_response(404)
                self.end_headers()
                return
            if not done.is_set():
                got.update({k: v[0] for k, v in q.items()})
                done.set()
            ok = "code" in got
            body = _PAGE.format("Signed in" if ok else "Sign-in did not finish").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # (nothing on the console)
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, name="openswx-collab-signin", daemon=True)
    t.start()
    try:
        redirect = f"http://127.0.0.1:{srv.server_address[1]}/callback"
        q = {"redirect_uri": redirect, "code_challenge": challenge, **({"invite": invite} if invite else {})}
        start = f"{url}/api/auth/oauth/{provider}/start?{urllib.parse.urlencode(q)}"
        (open_browser or webbrowser.open)(start)
        if not done.wait(timeout):
            raise AuthError(f"no answer from the browser within {int(timeout)} s; try again")
    finally:
        srv.shutdown()
        srv.server_close()
    if "code" not in got:
        e = got.get("error", "")
        raise AuthError(f"sign-in failed: {ERRORS.get(e, e or 'unknown error')}")
    with http(url, "", transport) as c:
        r = c.post("/api/auth/exchange", json={"code": got["code"], "code_verifier": verifier, "label": "openswx"})
    if r.status_code != 200:
        raise AuthError(f"sign-in refused: {error_text(r)}")
    return _remember(url, r.json())


def sign_out(transport=None) -> bool:
    """Forget the token (and end the session on the server when it can be reached). True if one was saved."""
    tok, url = token(), server()
    if tok and url:
        try:
            with http(url, tok, transport) as c:
                c.post("/api/auth/logout")
        except Exception:  # noqa: BLE001 - signing out locally must work offline too
            pass
    _token_path().unlink(missing_ok=True)
    d = config()
    d.pop("user", None)
    _save_config(d)
    return tok is not None
