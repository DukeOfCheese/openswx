"""The local server's guard: token, Host, Origin and content type (openswx/server/security.py)."""
import json
import re

from fastapi.testclient import TestClient

from openswx.server import security
from openswx.server.web_server import create_app

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
        assert c.get("/static/js/app.js").status_code == 200  # the UI's files are not secret
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


def test_the_page_carries_the_icon_sprite():
    with client() as c:
        c.cookies.set(security.COOKIE, TOKEN)
        page = c.get("/").text
    assert 'id="icons"' in page and 'id="icon-part"' in page  # one inline sprite, no request per pictogram
    assert "<!-- icon-sprite -->" not in page


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


def test_view_only_link(tmp_path, monkeypatch):
    from openswx import session as S
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    view = security.view_token()
    assert len(view) >= 32 and security.view_token() == view and view != TOKEN
    S.new_part("Shared")
    with client(view_token=view) as c:
        assert c.get("/?view=wrong").status_code == 401
        r = c.get(f"/?view={view}")
        assert r.status_code == 303 and security.VIEW_COOKIE in r.headers["set-cookie"]
        c.cookies.set(security.VIEW_COOKIE, view)
        assert c.get("/").status_code == 200 and c.get("/api/state").json()["model"]["name"] == "Shared"
        assert c.get("/api/mesh").status_code == 200
        assert c.post("/api/command", json={"name": "get_topology", "args": {}}).json()["ok"]  # a query
        for name, args in (("create_sketch", {"plane": "Top"}), ("save_part", {"path": str(tmp_path / "x")}),
                           ("export_script", {"path": str(tmp_path / "x.py")}), ("run_macro", {"path": "x.py"}),
                           ("save_diagnostic_report", {"path": str(tmp_path / "r.zip")}), ("no_such", {})):
            r = c.post("/api/command", json={"name": name, "args": args})
            assert r.status_code == 403 and "view-only" in r.json()["error"], name
        assert c.post("/api/preview", json={"name": "extrude", "args": {}}).status_code == 403
        assert c.post("/api/upload", json={"filename": "a.step", "data": ""}).status_code == 403
        assert c.get("/api/download?format=step").status_code == 403  # the model itself is not handed out
        assert not list(tmp_path.glob("x*")) and not S.get_model()["features"]
    with client() as c:  # without --share there are no view links
        c.cookies.set(security.VIEW_COOKIE, view)
        assert c.get("/api/state").status_code == 401
    with TestClient(create_app(token=TOKEN, view_token=view), base_url=BASE) as c:  # agents' endpoint stays closed to viewers
        c.cookies.set(security.VIEW_COOKIE, view)
        assert c.post("/mcp/", json={}).status_code == 403


def test_every_response_carries_the_security_headers():
    """Page, static file, API answer and even a refusal: one policy everywhere (no site may embed or sniff it)."""
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with client() as c:
        page = c.get(f"/?token={TOKEN}")
        c.cookies.set(security.COOKIE, TOKEN)
        loaded = c.get("/")
        static = c.get("/static/js/app.js")
        api = c.get("/api/state", headers=auth)
    with client() as anon:  # no cookie and no token: the guard's own 401 goes through the same middleware
        refused = anon.get("/api/state")
    assert refused.status_code == 401
    for r in (page, loaded, static, api, refused):
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert r.headers["X-Frame-Options"] == "DENY"
        csp = r.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp


def test_the_policy_fits_the_ui():
    with client() as c:
        c.cookies.set(security.COOKIE, TOKEN)
        first, second = c.get("/"), c.get("/")
    csp = first.headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
    assert "style-src 'self' 'unsafe-inline'" in csp  # the UI sets style attributes
    assert "img-src 'self' data: blob:" in csp  # a canvas' picture shown in a dialog
    assert "connect-src 'self'" in csp  # the API and the event stream
    assert "worker-src 'self' blob:" in csp
    assert "object-src 'none'" in csp
    nonce = csp.split("'nonce-", 1)[1].split("'", 1)[0]
    assert len(nonce) >= 16
    # the page's one inline script (the import map) carries that nonce, and it is fresh for each page load
    importmap = re.search(r'<script[^>]*type="importmap"[^>]*>', first.text).group(0)
    assert f'nonce="{nonce}"' in importmap
    assert nonce not in second.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in second.headers["Content-Security-Policy"].split("style-src")[0]


def test_the_ui_and_the_vendored_libraries_are_revalidated():
    """Every UI file, the vendored libraries included, is revalidated on each load (a 304 when unchanged): the
    vendored files have no version in their names, so a long-lived cache would keep an old three.js after an
    upgrade (the desktop app always serves the same address)."""
    with client() as c:
        c.cookies.set(security.COOKIE, TOKEN)
        page = c.get("/")
        vendor = c.get("/static/vendor/three.module.min.js")
        vendor_two = c.get("/static/vendor/three-gpu-pathtracer.module.js")
        ui = c.get("/static/js/app.js")
        css = c.get("/static/css/style.css")
        again = c.get("/static/vendor/three.module.min.js", headers={"If-None-Match": vendor.headers["etag"]})
    assert page.status_code == vendor.status_code == vendor_two.status_code == 200
    assert ui.status_code == css.status_code == 200
    for r in (page, vendor, vendor_two, ui, css):  # a changed file shows up on the next load, index.html included
        assert r.headers["Cache-Control"] == "no-cache"
    assert again.status_code == 304  # (unchanged: nothing is sent again)
