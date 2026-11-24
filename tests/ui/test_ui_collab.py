import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")
pytest.importorskip("openswx_collab_protocol")
pytest.importorskip("websockets")
httpx = pytest.importorskip("httpx")

ROOT = Path(__file__).resolve().parent.parent.parent
SERVER_REPO = Path(os.environ.get("OPENSWX_COLLAB_SERVER_REPO") or ROOT.parent / "openswx-collab")
SERVER_PY = SERVER_REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
OPEN_CORE = ("import sys; import openswx_collab_server.hooks as h; h.load_hooks = h.Hooks; "
             "from openswx_collab_server.__main__ import main; main(sys.argv[1:])")
TOKEN = "ui-collab-token-0123456789abcdefghijklmnopqrstuv"
PASSWORD = "correct horse"
SHOTS = os.environ.get("OPENSWX_COLLAB_SHOTS")

pytestmark = pytest.mark.skipif(not SERVER_PY.exists(), reason=f"no openswx-collab server at {SERVER_REPO}")

HELPERS = """
window.T = {
  run: (n, a) => window.app.api('/api/command', {name: n, args: a || {}}),
  open: () => !document.querySelector('#modal').classList.contains('hidden'),
  text: () => document.querySelector('#modal-box').innerText,
  ok: () => document.querySelector('#modal-box .mb-foot .primary').click(),
  peer: (name) => (window.app.collabDoc()?.peers || []).find((p) => p.name === name),
};
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, proc, what: str) -> None:
    for _ in range(480):
        if proc.poll() is not None:
            raise RuntimeError(f"{what} stopped")
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"{what} did not start")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import openswx_collab_protocol
    procs = []
    try:
        work = tmp_path_factory.mktemp("collab-server")
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        env = dict(os.environ, OSWX_COLLAB_DATABASE_URL=f"sqlite+aiosqlite:///{(work / 'collab.db').as_posix()}",
                   OSWX_COLLAB_STORAGE_URL=str(work / "storage"), OSWX_COLLAB_PUBLIC_URL=url)
        env.pop("PYTHONPATH", None)  # (the server runs on its own environment)
        log = open(work / "server.log", "a")
        procs.append(subprocess.Popen([str(SERVER_PY), "-c", OPEN_CORE, "serve", "--port", str(port)], env=env, cwd=str(work),
                                      stdout=log, stderr=log))
        wait_http(url + "/api/health", procs[-1], "the collaboration server")
        proto = str(Path(openswx_collab_protocol.__file__).resolve().parent.parent)
        apps = {}
        for who in ("ada", "bob"):
            p = free_port()
            env = dict(os.environ, OPENSWX_HOME=tempfile.mkdtemp(prefix=f"openswx-ui-{who}-"), OPENSWX_TOKEN=TOKEN,
                       OPENSWX_AUTOSAVE="off", OPENSWX_LOG="off", OPENSWX_PLUGINS="off", OPENSWX_UI_EVAL="1", OPENSWX_COLLAB_RETRY_MAX="1", OPENSWX_COLLAB_PAGE_GRACE="1",
                       PYTHONPATH=os.pathsep.join([str(ROOT), proto, os.environ.get("PYTHONPATH", "")]))
            procs.append(subprocess.Popen([sys.executable, "-m", "openswx", "serve", "--port", str(p)], cwd=ROOT, env=env,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            apps[who] = (f"http://127.0.0.1:{p}", procs[-1])
        for who, (base, proc) in apps.items():
            wait_http(base + "/api/health", proc, f"{who}'s openswx")
        http = httpx.Client(base_url=url, timeout=30)
        ada = http.post("/api/auth/register", json={"email": "ada@example.com", "password": PASSWORD, "name": "Ada"}).json()["token"]
        h = {"Authorization": f"Bearer {ada}"}
        org = http.post("/api/orgs", json={"name": "Robotics"}, headers=h).json()["id"]
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # noqa: BLE001 - no browser binary on this machine
                pytest.skip(f"no browser to drive: {exc}")
            pages = {}
            for who, (base, _) in apps.items():
                page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
                page.errors = []
                page.on("pageerror", lambda e, errs=page.errors: errs.append(str(e)))
                page.goto(f"{base}/?token={TOKEN}")
                page.wait_for_function("window.app && window.app.version >= 0 && window.app.cl")
                page.evaluate(HELPERS)
                pages[who] = page
            yield {"url": url, "http": http, "ada_h": h, "org": org, "a": pages["ada"], "b": pages["bob"], "state": {}}
            for page in pages.values():
                assert page.errors == [], page.errors
            browser.close()
    finally:
        for p in procs:
            p.kill()
            p.wait(20)


def shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def canvas_centre(page) -> tuple[float, float]:
    box = page.locator("#view").bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


# ---------------------------------------------------------------------- the steps, in order
def test_sign_in_through_the_dialog(world):
    a = world["a"]
    a.evaluate("window.app.collabSignInDialog()")
    a.wait_for_function("T.open() && document.querySelector('#cl-srv')")
    a.fill("#cl-srv", world["url"])
    a.wait_for_function("document.querySelector('#cl-oauth').innerText.includes('openswx-collab')")  # (asked the server)
    a.fill("#cl-email", "ada@example.com")
    a.fill("#cl-pw", PASSWORD)
    a.evaluate("T.ok()")
    a.wait_for_function("!T.open()")
    assert "Signed in as" in a.inner_text("#toasts")
    st = a.evaluate("async () => (await T.run('collab_status')).result")
    assert st["signed_in"] and st["user"]["name"] == "Ada"


def test_share_a_part_into_a_new_project(world):
    a = world["a"]
    a.evaluate("""async () => {
        await T.run('new_part', {name: 'Bracket'});
        await T.run('create_sketch', {plane: 'Top'});
        await T.run('sketch_rectangle', {sketch: 'Sketch1', corner1: [-50, -30], corner2: [50, 30]});
        await T.run('extrude', {sketch: 'Sketch1', depth: 20});
        await window.app.refresh(); window.app.vp.zoomFit(false);
    }""")
    a.evaluate("window.app.collabShareDialog()")
    a.wait_for_function("T.open() && document.querySelector('#cl-proj')")
    a.select_option("#cl-proj", "__new")
    a.fill("#cl-pname", "Rover")
    a.evaluate("T.ok()")
    a.wait_for_function("!T.open() && window.app.collabDoc() && window.app.collabDoc().state === 'online'", timeout=30000)
    a.wait_for_function("document.querySelector('#collabbar:not(.hidden)')?.innerText.includes('Online')")
    assert a.evaluate("window.app.collabDoc().role") == "owner"


def test_bob_opens_it_from_the_open_dialog(world):
    a, b, http = world["a"], world["b"], world["http"]
    project = a.evaluate("async () => (await T.run('collab_projects')).result.orgs[0].projects[0].id")
    inv = http.post(f"/api/orgs/{world['org']}/invites", headers=world["ada_h"], json={
        "email": "bob@example.com", "projects": [{"project_id": project, "role": "editor"}]}).json()["token"]
    http.post("/api/auth/register", json={"email": "bob@example.com", "password": PASSWORD, "name": "Bob",
                                          "invite": inv}).raise_for_status()
    r = b.evaluate(f"async () => T.run('collab_sign_in', {{server: '{world['url']}', email: 'bob@example.com', password: '{PASSWORD}'}})")
    assert r["ok"], r
    b.evaluate("window.app.collabOpenDialog()")
    b.wait_for_selector("#modal-box .cl-doc")
    b.click("#modal-box .cl-doc")
    b.evaluate("T.ok()")
    b.wait_for_function("!T.open() && window.app.collabDoc() && window.app.model.name === 'Bracket'", timeout=30000)
    assert b.evaluate("window.app.model.features.map((f) => f.name)") == ["Sketch1", "Boss-Extrude1"]


def test_each_sees_the_other_as_an_avatar(world):
    a, b = world["a"], world["b"]
    b.wait_for_selector("#collabbar .cl-av[title^='Ada']")
    a.wait_for_selector("#collabbar .cl-av[title^='Bob']")
    assert b.inner_text("#collabbar .cl-av") == "A"
    colour = b.evaluate("T.peer('Ada').colour")
    assert colour.lower() in b.get_attribute("#collabbar .cl-av", "style").lower()
    world["state"]["ada_colour"] = int(colour[1:], 16)


def test_ada_s_pointer_and_selection_show_on_bob_s_model(world):
    a, b = world["a"], world["b"]
    x, y = canvas_centre(a)
    a.mouse.move(x - 20, y - 10)
    a.mouse.move(x, y)
    b.wait_for_function("(T.peer('Ada')?.presence?.pointer?.face || '').length > 0")
    b.wait_for_function("[...document.querySelectorAll('.cl-ptr')].some((m) => m.style.display !== 'none' && m.innerText.startsWith('Ada'))")
    face = b.evaluate("T.peer('Ada').presence.pointer.face")
    assert not face.startswith("Face"), f"the pointer names a face by its persistent name, not {face}"
    a.mouse.click(x, y)
    a.wait_for_function("window.app.selection.some((s) => s.kind === 'face')")
    b.wait_for_function("window.app.cl.groups.sel.children.length >= 2")  # (the face, tinted, and its outline)
    assert b.evaluate("window.app.cl.groups.sel.children.every((o) => o.material.color.getHex() === "
                      f"{world['state']['ada_colour']})")
    shot(b, "pointer-and-selection")
    a.evaluate("window.app.setSelection([])")
    b.wait_for_function("window.app.cl.groups.sel.children.length === 0")


def test_ada_s_pointer_is_an_arrow_that_glides_and_stays_over_empty_background(world):
    a, b = world["a"], world["b"]
    box = a.locator("#view").bounding_box()
    a.mouse.move(box["x"] + box["width"] - 60, box["y"] + box["height"] / 2 + 20)
    a.mouse.move(box["x"] + box["width"] - 50, box["y"] + box["height"] / 2)  # (right of the part: empty background)
    a.wait_for_function("window.app.cl.pointer?.plane === true", timeout=5000)
    b.wait_for_function("T.peer('Ada')?.presence?.pointer?.plane === true")
    marker = "[...document.querySelectorAll('.cl-ptr')].find((m) => m.innerText.startsWith('Ada'))"
    assert b.evaluate(f"!!{marker}.querySelector('svg path')"), "the pointer is drawn as an arrow"
    # it arrives where Ada's pointer is (eased, not stuck part-way) and shows, not faded out
    b.wait_for_function(f"""() => {{ const m = {marker}, app = window.app;
        if (!m || m.classList.contains('gone') || m.style.display === 'none' || !m.target) return false;
        const [x, y] = app.vp.toScreen(m.target.toArray());
        const t = /translate\\(([-\\d.]+)px, ([-\\d.]+)px\\)/.exec(m.style.transform);
        return t && Math.hypot(+t[1] + 2 - x, +t[2] + 2 - y) < 1.5; }}""", timeout=5000)
    assert not b.evaluate(f"{marker}.classList.contains('behind')"), "a pointer in the air is never behind the model"
    a.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    b.wait_for_function("(T.peer('Ada')?.presence?.pointer?.face || '').length > 0")


def test_ada_s_tool_shows_next_to_her_name_and_her_panel_as_a_ghost(world):
    a, b = world["a"], world["b"]
    a.evaluate("window.app.editFeature('Boss-Extrude1')")
    a.wait_for_function("window.app.pm.active && window.app.cl.held.pm")
    a.evaluate("""() => { const app = window.app; app.pm.values.depth = 45; app.schedulePreview(app.pm.args()); }""")
    b.wait_for_function("[...document.querySelectorAll('#collabbar .cl-tool')].some((t) => t.innerText === 'Editing Boss-Extrude1')")
    assert "Editing Boss-Extrude1" in b.inner_text(".cl-ptr")
    b.wait_for_function("T.peer('Ada')?.draft?.kind === 'feature'")
    b.wait_for_function("[...window.app.cl.ghosts.values()].some((g) => g.group.children.length > 0)", timeout=20000)
    world["state"]["panel_open"] = True


def test_a_feature_open_on_one_side_is_locked_on_the_other(world):
    b = world["b"]
    b.wait_for_selector("#tree .t-row[data-feature='Boss-Extrude1'] .cl-lock[data-who='Ada']")
    assert "Ada is editing Boss-Extrude1" in b.get_attribute("#tree .t-row[data-feature='Boss-Extrude1'] .cl-lock", "title")
    b.evaluate("window.app.editFeature('Boss-Extrude1')")  # (Bob tries to open it too: it does not open)
    b.wait_for_function("document.querySelector('#toasts').innerText.includes('being edited by Ada')")
    assert not b.evaluate("!!window.app.pm.active")
    shot(b, "lock-badge-and-ghost")


def test_a_change_refused_by_a_lock_says_who_in_a_toast(world):
    b = world["b"]
    r = b.evaluate("async () => { try { await window.app.run('edit_feature', {name: 'Boss-Extrude1', params: {depth: 33}}); return 'applied'; }"
                   " catch (e) { return String(e.message || e); } }")
    assert "Ada" in r and "locked" in r
    b.wait_for_selector("#toasts .cl-conflict")
    text = b.inner_text("#toasts .cl-conflict")
    assert "Ada is editing Boss-Extrude1" in text
    assert b.evaluate("[...document.querySelectorAll('#toasts .toast.err:not(.cl-conflict)')]"
                      ".every((t) => !t.innerText.includes('locked'))")  # (one toast for it, not two)
    shot(b, "conflict-toast")


def test_closing_the_panel_frees_the_lock(world):
    a, b = world["a"], world["b"]
    a.evaluate("window.app.closePM()")
    b.wait_for_function("!document.querySelector(\"#tree .t-row[data-feature='Boss-Extrude1'] .cl-lock\")")
    b.wait_for_function("[...window.app.cl.ghosts.values()].every((g) => g.group.children.length === 0)")
    b.wait_for_function("!document.querySelector('#collabbar .cl-tool')")


def test_sketch_lines_being_drawn_show_dashed_in_the_drawer_s_colour(world):
    a, b = world["a"], world["b"]
    a.evaluate("window.app.enterSketch('Sketch1')")
    a.wait_for_function("window.app.sk.active && window.app.cl.held.sketch")
    # (Sketch1 is folded under Boss-Extrude1: the folded row shows its lock)
    b.wait_for_selector("#tree .t-row[data-feature='Boss-Extrude1'] .cl-lock[data-who='Ada'][data-target='Sketch1']")
    a.evaluate("window.app.setSketchTool('line')")
    time.sleep(0.5)  # (Normal To turns the view)
    x, y = canvas_centre(a)
    a.mouse.move(x - 150, y - 80)
    a.mouse.click(x - 150, y - 80)
    a.mouse.move(x + 60, y + 40, steps=5)
    b.wait_for_function("T.peer('Ada')?.draft?.kind === 'sketch'")
    b.wait_for_function("window.app.cl.groups.drafts.children.length > 0")
    line = b.evaluate("(() => { const l = window.app.cl.groups.drafts.children[0]; return {dashed: !!l.material.isLineDashedMaterial,"
                      " colour: l.material.color.getHex(), sketch: l.userData.sketch}; })()")
    assert line == {"dashed": True, "colour": world["state"]["ada_colour"], "sketch": "Sketch1"}
    assert "Line in Sketch1" in b.inner_text("#collabbar")
    shot(b, "sketch-draft")
    a.keyboard.press("Escape")
    a.evaluate("window.app.exitSketch(true)")
    b.wait_for_function("window.app.cl.groups.drafts.children.length === 0")
    b.wait_for_function("!document.querySelector('#tree .cl-lock')")


def test_following_ada_s_view(world):
    a, b = world["a"], world["b"]
    b.click("#collabbar .cl-av[title^='Ada']")
    assert b.evaluate("window.app.cl.follow") == b.evaluate("T.peer('Ada').client_id")
    a.evaluate("window.app.vp.setView('top')")
    angle = ("(() => { const q = T.peer('Ada')?.presence?.camera?.q; if (!q) return 9;"
             " const c = window.app.vp.camera.quaternion; return 2 * Math.acos(Math.min(1, Math.abs(c.x*q[0] + c.y*q[1] + c.z*q[2] + c.w*q[3]))); })()")
    b.wait_for_function(f"{angle} < 0.01 && Math.abs(T.peer('Ada').presence.camera.q[0] + 0.7071) < 0.01", timeout=15000)
    assert "Following Ada" in b.inner_text("#collabbar")
    x, y = canvas_centre(b)
    b.mouse.move(x, y)
    b.mouse.wheel(0, -200)  # (any camera input of his own stops following)
    b.wait_for_function("window.app.cl.follow === null")
    assert "Following" not in b.inner_text("#collabbar")


def test_undo_follows_the_collaboration_state(world):
    a, b = world["a"], world["b"]
    b.evaluate("async () => { await window.app.run('edit_feature', {name: 'Boss-Extrude1', params: {depth: 25}}); }")
    b.wait_for_function("window.app.collabDoc().can_undo && !document.querySelector('.qa[data-k=undo]').disabled")
    a.wait_for_function("window.app.model.features.length === 2 && window.app.collabDoc().seq >= 1")
    assert a.evaluate("document.querySelector('.qa[data-k=undo]').disabled")  # (nothing of Ada's to take back)
    b.click(".qa[data-k=undo]")
    b.wait_for_function("!window.app.collabDoc().can_undo && window.app.collabDoc().can_redo")


def test_a_feature_broken_by_someone_else_s_change_says_whose(world):
    a, b = world["a"], world["b"]
    a.evaluate("""async () => {
        const t = (await T.run('get_topology')).result;
        const e = t.edges.reduce((m, x) => (x.length > m.length ? x : m));
        await window.app.run('fillet', {radius: 3, edges: [e.name || e.id]}); }""")
    b.wait_for_function("window.app.model.features.some((f) => f.type === 'fillet')")
    b.evaluate("async () => { await window.app.run('edit_feature', {name: 'Boss-Extrude1', params: {depth: 1.5}}); }")  # (a 3 mm fillet no longer fits)
    a.wait_for_selector("#tree .t-row .cl-broken", timeout=20000)
    assert a.inner_text("#tree .t-row .cl-broken") == "broken by Bob"
    assert "broken by Bob's edit_feature" in a.get_attribute("#tree .t-row .cl-broken", "title")
    shot(a, "broken-by")
    b.click(".qa[data-k=undo]")
    a.wait_for_function("!document.querySelector('#tree .cl-broken')", timeout=20000)


def test_history_dialog_names_a_version(world):
    a = world["a"]
    a.evaluate("window.app.collabHistoryDialog()")
    a.wait_for_function("T.open() && T.text().includes('edit_feature') && T.text().includes('Bob')")
    a.fill("#cl-vname", "Rev A")
    a.click("#cl-vbtn")
    a.wait_for_function("T.open() && T.text().includes('Rev A')")
    assert a.locator("#modal-box .cl-restore").count() >= 2
    a.evaluate("document.querySelector('#modal').classList.add('hidden')")


def test_changes_refused_after_a_reconnect_are_listed_once(world):
    """Offline, changes queue up; what the server refuses when they go in is listed in one dialog, not toasted.
    (Driven with states as GET /api/collab gives them: the servers stay up.)"""
    b = world["b"]
    refused = {"change_id": "c1", "command": "edit_feature", "who": ["Ada"], "features": ["Fillet1"], "code": "stale",
               "text": "Ada changed Fillet1 while you were editing it: your edit_feature was not applied"}
    b.evaluate("""(c) => { const app = window.app, d = {document_id: 'elsewhere', state: 'online', pending: 0, conflicts: []};
        app.collabConflicts(d);
        app.collabConflicts({...d, state: 'offline', pending: 1});
        app.collabConflicts({...d, state: 'online', pending: 1, conflicts: [c]});
        if (T.open()) throw new Error('listed before the queued changes were all answered');
        app.collabConflicts({...d, state: 'online', pending: 0, conflicts: [c]});
        app.collabConflicts({...d, state: 'online', pending: 0, conflicts: [c]}); }""", refused)
    assert b.evaluate("T.open()") and "Changes not applied" in b.evaluate("T.text()")
    assert "Ada changed Fillet1" in b.evaluate("T.text()")
    assert "Fillet1" not in b.inner_text("#toasts")
    b.evaluate("document.querySelector('#modal').classList.add('hidden')")


def _watch_previews(page) -> None:
    page.evaluate("""() => { window.__previews = []; const app = window.app, api = app.api.bind(app);
        app.api = (path, body) => { if (path === '/api/preview') window.__previews.push(body); return api(path, body); }; }""")


def _ada_draft(world, draft, presence=None) -> None:
    """What Ada's openswx relays as her draft (and presence), as any client of the server could send it."""
    args = {"draft": draft} if draft is not None else {"clear_draft": True}
    r = world["a"].evaluate("(a) => T.run('collab_presence', a)", {**args, **(presence or {})})
    assert r["ok"], r


def test_hostile_drafts_never_reach_a_preview(world):
    """A draft comes from someone else: only a feature command the UI's panels preview, with plain small arguments,
    is ever previewed - never run_macro, save_part or anything else."""
    b = world["b"]
    _watch_previews(b)
    for draft in ({"kind": "feature", "command": "run_macro", "args": {"path": "//evil/share/evil.py"}},
                  {"kind": "feature", "command": "save_part", "args": {"path": "C:/Users/Public/x.oswprt"}},
                  {"kind": "feature", "command": "batch", "args": {"commands": [{"name": "run_macro"}]}},
                  {"kind": "feature", "command": "run_macro", "editing": "Boss-Extrude1", "args": {"path": "x.py"}},
                  {"kind": "feature", "command": "extrude", "editing": "NoSuchFeature", "args": {"depth": 5}},
                  {"kind": "feature", "command": "linear_pattern", "args": {"features": ["Boss-Extrude1"], "count1": 100000}},
                  {"kind": "feature", "command": "extrude", "args": "not an object"}):
        _ada_draft(world, draft)
        b.wait_for_function("(c) => JSON.stringify(T.peer('Ada')?.draft) === c", arg=json.dumps(draft, separators=(",", ":")))
        time.sleep(0.5)  # (longer than a ghost's 300 ms)
        assert b.evaluate("window.__previews") == [], draft
    _ada_draft(world, {"kind": "feature", "command": "extrude", "editing": "Boss-Extrude1",
                       "args": {"sketch": "Sketch1", "depth": 35}})  # (a real one still shows)
    b.wait_for_function("window.__previews.length > 0 && [...window.app.cl.ghosts.values()].some((g) => g.group.children.length > 0)")
    assert b.evaluate("window.__previews[0].name") == "redefine_feature" and b.evaluate("window.__previews[0].if_idle")
    _ada_draft(world, None)
    b.wait_for_function("[...window.app.cl.ghosts.values()].every((g) => g.group.children.length === 0)")


def test_a_bad_draft_or_camera_does_not_stop_drawing(world):
    b = world["b"]
    _ada_draft(world, {"kind": "sketch", "sketch": "Sketch1", "lines": "oops"})
    b.wait_for_function("T.peer('Ada')?.draft?.lines === 'oops'")
    _ada_draft(world, {"kind": "sketch", "sketch": "Sketch1", "lines": [{"p": [0, 0, "x", 1]}, {"p": None}, {"p": [0, 0, 30, 20]}]})
    b.wait_for_function("window.app.cl.groups.drafts.children.length === 1")  # (the good line, the others skipped)
    _ada_draft(world, None)
    b.wait_for_function("window.app.cl.groups.drafts.children.length === 0")
    b.click("#collabbar .cl-av[title^='Ada']")
    _ada_draft(world, None, {"camera": {"q": ["a", 0, 0], "t": [1, 2], "h": "big"}})
    b.wait_for_function("T.peer('Ada')?.presence?.camera?.h === 'big'")
    time.sleep(0.3)
    cam = b.evaluate("(() => { const v = window.app.vp; return [...v.camera.quaternion.toArray(), ...v.target.toArray(), v.viewHeight]; })()")
    assert all(isinstance(x, (int, float)) and x == x for x in cam), cam  # (nothing NaN)
    b.click("#collabbar .cl-av[title^='Ada']")
    assert b.evaluate("window.app.cl.follow") is None


def test_going_straight_to_another_sketch_keeps_that_one_locked(world):
    a, b = world["a"], world["b"]
    a.evaluate("async () => { await window.app.run('create_sketch', {plane: 'Front'}); }")
    b.wait_for_function("window.app.model.features.some((f) => f.name === 'Sketch2')")
    a.evaluate("window.app.enterSketch('Sketch1')")
    b.wait_for_selector("#tree .cl-lock[data-target='Sketch1']")
    a.evaluate("window.app.enterSketch('Sketch2')")  # (from Sketch1 straight into Sketch2)
    a.wait_for_function("window.app.sk.name === 'Sketch2'")
    b.wait_for_selector("#tree .t-row[data-feature='Sketch2'] .cl-lock[data-who='Ada']")
    b.wait_for_function("!document.querySelector(\"#tree .cl-lock[data-target='Sketch1']\")")
    time.sleep(0.5)
    assert b.locator("#tree .t-row[data-feature='Sketch2'] .cl-lock").count() == 1  # (still held)
    a.evaluate("window.app.exitSketch(true)")
    b.wait_for_function("!document.querySelector('#tree .cl-lock')")


def test_a_lock_is_freed_on_its_document_after_switching_away(world):
    a, b = world["a"], world["b"]
    a.evaluate("window.app.editFeature('Boss-Extrude1')")
    b.wait_for_selector("#tree .t-row[data-feature='Boss-Extrude1'] .cl-lock")
    a.evaluate("async () => { await window.app.run('new_part', {name: 'Scratch'}); window.app.closePM(); }")  # (another document is active)
    b.wait_for_function("!document.querySelector('#tree .cl-lock')")
    a.evaluate("async () => { await window.app.switchDocument('Bracket'); }")
    a.wait_for_function("window.app.collabDoc()")


def test_the_collaborate_menu_has_only_what_belongs_to_the_open_document(world):
    b = world["b"]
    items = b.evaluate("window.app.collabMenu().filter((x) => x !== '-').map((x) => x[0])")
    assert items[:3] == ["Stop Sharing...", "History and Versions...", "Name Version..."]
    assert "Follow > Ada" in items and "Cloud Files..." in items
    assert not any(i.startswith(("Sign", "Open Cloud", "Share")) for i in items)  # (those are on Home's Cloud tab)


def test_home_s_cloud_tab_shows_the_project_its_parts_and_who_is_in_them(world):
    b = world["b"]
    b.evaluate("window.app.homeShow('cloud')")
    b.wait_for_selector("#home .hm-tab.active[data-tab='cloud']")
    b.wait_for_selector("#home .cl-proj-b.active:has-text('Rover')")
    b.wait_for_selector("#home .cl-doc:has-text('Bracket') .cl-dot[title='Ada']")
    b.wait_for_selector("#home .cl-members td:has-text('Ada')")
    assert b.locator("#home .cl-acct").inner_text().startswith("Signed in as Bob")
    assert b.locator("#home #cl-ibtn").count() == 0  # (a member sees the team; owners and admins invite)
    shot(b, "home-cloud-bob")
    if SHOTS:  # (and in the dark theme)
        b.evaluate("document.documentElement.dataset.theme = 'dark'")
        shot(b, "home-cloud-bob-dark")
        b.evaluate("delete document.documentElement.dataset.theme")
    b.keyboard.press("Escape")
    b.wait_for_function("!window.app.homeOpen")


def test_ada_invites_from_the_team_list_and_withdraws_it(world):
    a = world["a"]
    a.evaluate("window.app.homeShow('cloud')")
    a.wait_for_selector("#home #cl-ibtn")
    a.fill("#cl-iemail", "carol@example.com")
    a.click("#cl-ibtn")
    a.wait_for_function("T.open() && document.querySelector('#cl-ilink')?.value.includes('/invite/')")
    assert a.input_value("#cl-icode") and a.input_value("#cl-ilink").endswith(a.input_value("#cl-icode"))
    a.click("#modal-box .mb-foot .btn")  # (Close)
    a.wait_for_selector("#home .cl-uninv")
    assert "carol@example.com" in a.inner_text("#home .cl-team")
    a.click("#home .cl-uninv")
    a.wait_for_function("!document.querySelector('#home .cl-uninv')")


def test_a_new_cloud_part_from_home_opens_at_once_and_bob_sees_ada_in_it(world):
    a, b = world["a"], world["b"]
    a.click("#home #cl-newcloud")
    a.wait_for_function("T.open() && document.querySelector('#cl-cname')")
    a.fill("#cl-cname", "Plate")
    a.evaluate("T.ok()")
    a.wait_for_function("!window.app.homeOpen && window.app.collabDoc()?.document === 'Plate'", timeout=30000)
    b.evaluate("window.app.homeShow('cloud')")
    b.wait_for_selector("#home .cl-doc:has-text('Plate') .cl-dot[title='Ada']", timeout=15000)
    b.keyboard.press("Escape")


def test_the_cloud_tab_shows_what_it_last_loaded_at_once_and_refreshes_behind_it(world):
    b = world["b"]
    # the server answers slowly from now on: what was loaded before still shows straight away
    b.evaluate("""() => { const app = window.app; app._api = app.api;
        app.api = async (url, body) => { if (/collab_(projects|documents|members|project_members)/.test(body?.name || ''))
            await new Promise((r) => setTimeout(r, 3000)); return app._api(url, body); }; }""")
    b.evaluate("window.app.homeShow('cloud')")
    b.wait_for_selector("#home .cl-doc:has-text('Plate')", timeout=1000)
    b.wait_for_selector("#home .cl-members td:has-text('Ada')", timeout=1000)
    # the server cannot be reached: the last lists stay, with a note
    b.evaluate("""() => { const app = window.app;
        app.api = async (url, body) => /collab_(projects|documents|members|project_members)/.test(body?.name || '')
            ? {ok: false, error: 'cannot reach the collaboration server'} : app._api(url, body); }""")
    b.evaluate("window.app.homeRender()")
    b.wait_for_selector("#home .cl-stale:not(.hidden)")
    assert b.locator("#home .cl-doc:has-text('Plate')").count() == 1
    b.evaluate("window.app.api = window.app._api; window.app.homeRender()")
    b.wait_for_selector("#home .cl-stale.hidden", state="attached")
    # another account or server: nothing of the last one is kept
    kept = b.evaluate("() => { const hc = window.app.cl.hc; return hc.m.size; }")
    assert kept > 0
    assert b.evaluate("() => window.app.collabHomeCache('elsewhere|someone').m.size") == 0
    b.keyboard.press("Escape")


def test_who_is_in_each_part_follows_while_the_tab_is_up(world):
    a, b = world["a"], world["b"]
    b.evaluate("window.app.cl.homeRefreshMs = 300; window.app.homeShow('cloud')")
    b.wait_for_selector("#home .cl-doc:has-text('Plate') .cl-dot[title='Ada']")
    a.evaluate("async () => { await window.app.switchDocument('Plate'); await window.app.run('collab_disable'); }")  # (Ada leaves Plate)
    b.wait_for_function("""() => { const row = [...document.querySelectorAll('#home .cl-doc')].find((r) => r.innerText.includes('Plate'));
        return row && !row.querySelector(".cl-dot[title='Ada']"); }""", timeout=15000)
    b.evaluate("window.app.cl.homeRefreshMs = 0")
    b.keyboard.press("Escape")
    a.evaluate("async () => { await window.app.switchDocument('Bracket'); }")
    a.wait_for_function("window.app.collabDoc()?.document === 'Bracket'")


def test_new_part_asks_local_or_cloud_and_remembers_the_choice(world):
    a = world["a"]
    a.evaluate("window.app.newPart()")
    a.wait_for_function("T.open() && document.querySelector('input[name=cl-where]')")
    assert a.evaluate("document.querySelector('input[name=cl-where]:checked').value") == "local"
    a.check("input[name=cl-where][value=cloud]")
    a.wait_for_selector("#cl-nproj")
    a.fill("#cl-nname", "Gear")
    a.evaluate("T.ok()")
    a.wait_for_function("window.app.collabDoc()?.document === 'Gear'", timeout=30000)
    a.evaluate("window.app.newPart()")
    a.wait_for_function("T.open() && document.querySelector('input[name=cl-where]')")
    assert a.evaluate("document.querySelector('input[name=cl-where]:checked').value") == "cloud"  # (remembered)
    a.check("input[name=cl-where][value=local]")
    a.evaluate("T.ok()")
    a.wait_for_function("!T.open() && window.app.model.name.startsWith('Part') && !window.app.collabDoc()")
    a.evaluate("async () => { await window.app.switchDocument('Bracket'); }")
    a.wait_for_function("window.app.collabDoc()?.document === 'Bracket'")


def test_save_on_a_cloud_part_says_it_saves_itself(world):
    a = world["a"]
    a.evaluate("window.app.save()")
    a.wait_for_function("document.querySelector('#toasts').innerText.includes('save automatically')")


def test_a_crashed_page_s_locks_are_freed(world):
    a, b = world["a"], world["b"]
    a.evaluate("window.app.editFeature('Boss-Extrude1')")
    b.wait_for_selector("#tree .t-row[data-feature='Boss-Extrude1'] .cl-lock")
    a.evaluate("window.app.cl.noPagehide = true")  # (no goodbye: as if the tab crashed)
    a.close()
    b.wait_for_function("!document.querySelector('#tree .cl-lock')", timeout=20000)  # (once its event stream is gone)
