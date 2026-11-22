"""The local server's guard: token, Host, Origin and content type (openswx/security.py)."""
import json

from fastapi.testclient import TestClient

from openswx import security
from openswx.web_server import create_app

BASE = "http://127.0.0.1:8765"
TOKEN = "t" * 40
CMD = {"name": "list_documents", "args": {}}


def client(token=TOKEN, base=BASE, **kw):
    return TestClient(create_app(mount_mcp=False, token=token, **kw), base_url=base, follow_redirects=False)


def test_api_needs_the_token():
    with client() as c:
        assert c.post("/api/command", json=CMD).status_code == 401
        assert c.get("/api/state").status_code == 401
        assert c.get("/api/events").status_code == 401
        assert c.post("/api/command", json=CMD, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/health").status_code == 200  # liveness stays open
        assert c.get("/static/app.js").status_code == 200  # the UI's files are not secret
        for headers in ({"Authorization": f"Bearer {TOKEN}"}, {"X-OpenSWX-Token": TOKEN}, {"Cookie": f"{security.COOKIE}={TOKEN}"}):
            r = c.post("/api/command", json=CMD, headers=headers)
            assert r.status_code == 200 and r.json()["ok"]


def test_token_link_sets_the_cookie_and_the_page_then_works():
    with client() as c:
        assert c.get("/").status_code == 401  # no cookie yet: the page explains, it does not load
        assert c.get("/?token=wrong").status_code == 401
        r = c.get(f"/?token={TOKEN}")
        assert r.status_code == 303 and r.headers["location"] == "/"
        cookie = r.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
        c.cookies.set(security.COOKIE, TOKEN)
        assert c.get("/").status_code == 200
        assert c.post("/api/command", json=CMD).json()["ok"]


def test_cross_site_requests_are_refused_even_with_the_token():
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with client() as c:
        # a page on another site, or another port of this machine, posting to openswx
        assert c.post("/api/command", json=CMD, headers={**auth, "Origin": "https://evil.example"}).status_code == 403
        assert c.post("/api/command", json=CMD, headers={**auth, "Origin": "http://127.0.0.1:3000"}).status_code == 403
        assert c.post("/api/command", json=CMD, headers={**auth, "Origin": "null"}).status_code == 403
        assert c.get("/api/state", headers={**auth, "Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert c.get("/api/state", headers={**auth, "Sec-Fetch-Site": "same-site"}).status_code == 403
        # the form a cross-site page can send without a preflight
        r = c.post("/api/command", content=json.dumps(CMD), headers={**auth, "Content-Type": "text/plain"})
        assert r.status_code == 415
        # the page itself
        ok = c.post("/api/command", json=CMD, headers={**auth, "Origin": BASE, "Sec-Fetch-Site": "same-origin"})
        assert ok.status_code == 200


def test_host_header_must_name_this_computer():
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with client(base="http://attacker.example:8765") as c:  # DNS rebinding: a foreign name resolving to 127.0.0.1
        assert c.get("/api/state", headers=auth).status_code == 403
        assert c.get("/", headers=auth).status_code == 403
    with client(base="http://localhost:8765") as c:
        assert c.get("/api/state", headers=auth).status_code == 200
    with client(base="http://cad-box:8765", host="0.0.0.0") as c:  # serve --allow-remote: any name, token still needed
        assert c.get("/api/state").status_code == 401
        assert c.get("/api/state", headers=auth).status_code == 200


def test_no_token_mode_keeps_the_other_checks():
    with client(token=None) as c:
        assert c.post("/api/command", json=CMD).json()["ok"]
        assert c.post("/api/command", json=CMD, headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.post("/api/command", content=json.dumps(CMD), headers={"Content-Type": "text/plain"}).status_code == 415


def test_mcp_over_http_needs_the_token():
    with TestClient(create_app(token=TOKEN), base_url=BASE) as c:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
        accept = {"Accept": "application/json, text/event-stream"}
        assert c.post("/mcp/", json=init, headers=accept).status_code == 401
        r = c.post("/mcp/", json=init, headers={**accept, "Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200 and "openswx" in r.text


def test_user_token_is_saved_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    monkeypatch.delenv("OPENSWX_TOKEN", raising=False)
    first = security.user_token()
    assert len(first) >= 32 and security.user_token() == first
    assert (tmp_path / "token").read_text() == first
    monkeypatch.setenv("OPENSWX_TOKEN", "from-the-environment")
    assert security.user_token() == "from-the-environment"
