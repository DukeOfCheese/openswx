"""UI smoke tests: the real page in a real browser against a real server.

Needs Playwright (`pip install playwright && playwright install chromium`); skipped where it is not
installed. CI installs it (see .github/workflows/test.yml). The steps are the ones first driven by hand:
open through the token link, build a part, and open each dialog that is not covered by any other test.
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")

ROOT = Path(__file__).resolve().parent.parent.parent
TOKEN = "ui-smoke-token-0123456789abcdefghijklmnopqrstuv"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, OPENSWX_HOME=str(tmp_path_factory.mktemp("home")), OPENSWX_TOKEN=TOKEN,
               OPENSWX_AUTOSAVE="off", OPENSWX_LOG="off", OPENSWX_PLUGINS="off",
               OPENSWX_SHOW_HOME="0", OPENSWX_UI_EVAL="1")  # (Home has its own tests: test_ui_home.py)
    proc = subprocess.Popen([sys.executable, "-m", "openswx", "serve", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(240):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1).read()
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError("the server did not start")
        yield base
    finally:
        proc.kill()
        proc.wait(10)


@pytest.fixture(scope="module")
def page(server):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - no browser binary on this machine
            pytest.skip(f"no browser to drive: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.errors = errors
        yield page
        browser.close()


HELPERS = """
window.T = {
  run: (n, a) => window.app.api('/api/command', {name: n, args: a || {}}),
  open: () => !document.querySelector('#modal').classList.contains('hidden'),
  text: () => document.querySelector('#modal-box').innerText,
  close: () => document.querySelector('#modal').classList.add('hidden'),
  ok: () => document.querySelector('#modal-box .mb-foot .primary').click(),
};
"""


def test_page_needs_the_token_link(server, page):
    assert page.goto(server + "/").status == 401
    page.goto(f"{server}/?token={TOKEN}")
    page.wait_for_function("window.app && window.app.version >= 0")
    assert page.url.rstrip("/") == server  # the token left the address bar
    assert page.evaluate("[...document.querySelectorAll('#menubar .menu-root')].map(e => e.firstChild.textContent)") == \
        ["File", "Edit", "View", "Insert", "Tools", "Collaborate", "Window", "Help"]
    # the menus are always shown (not behind a click on the logo)
    assert page.evaluate("[...document.querySelectorAll('#menubar .menu-root')].every(e => e.getBoundingClientRect().width > 0)")
    page.evaluate(HELPERS)


def test_build_a_part_and_see_it(page):
    vol = page.evaluate("""async () => {
        await T.run('new_part', {name: 'UiPart'});
        await T.run('create_sketch', {plane: 'Top'});
        const r = await T.run('sketch_rectangle', {sketch: 'Sketch1', corner1: [0, 0], corner2: [40, 20]});
        await T.run('sketch_dimension', {sketch: 'Sketch1', kind: 'length', refs: [r.result.result.entities[0]], value: 40});
        await T.run('extrude', {sketch: 'Sketch1', depth: 6});
        await window.app.refresh();
        return window.app.model.volume_mm3;
    }""")
    assert abs(vol - 40 * 20 * 6) < 1e-6
    assert "UiPart" in page.title()
    assert page.evaluate("[...document.querySelectorAll('#tree *')].some(e => e.textContent.trim() === 'Boss-Extrude1')") or \
        "Boss-Extrude1" in page.inner_text("body")


@pytest.mark.parametrize("method, title", [
    ("optionsDialog", "System Options"), ("userSettingsDialog", "User Settings"), ("toleranceDialog", "Dimension Tolerances"),
    ("reviewDialog", "Design Review"), ("requirementsDialog", "Requirements"), ("mcpDialog", "Connect an agent"),
    ("about", "About openswx"), ("equationsDialog", "Equation"),
])
def test_dialogs_open(page, method, title):
    page.evaluate(f"async () => {{ T.close(); await window.app.{method}(); }}")
    page.wait_for_function("T.open()")
    assert title in page.evaluate("T.text()")
    page.evaluate("T.close()")


def test_tolerance_dialog_saves(page):
    page.evaluate("async () => { await window.app.toleranceDialog(); }")
    page.wait_for_function("T.open()")
    page.evaluate("""() => { const row = document.querySelector('#modal-box tr[data-i]');
        row.querySelector('.tt').value = 'symmetric'; row.querySelector('.tp').value = '0.1'; T.ok(); }""")
    page.wait_for_function("!T.open()")
    texts = page.evaluate("async () => (await T.run('get_dimension_tolerances')).result.dimensions.map(d => d.text)")
    assert texts == ["40 ±0.1"]


def test_a_study_runs_as_a_job_with_a_progress_toast(page):
    result = page.evaluate("""async () => {
        await T.run('set_material', {name: 'Plain Carbon Steel'});
        await T.run('simulation_add_fixture', {face: 'Face0'});
        await T.run('simulation_add_force', {face: 'Face1', magnitude: 100});
        const p = window.app.commandOrJob('simulation_run', {});
        await new Promise(r => setTimeout(r, 450));
        const toast = document.querySelector('#toasts').innerText;
        const sim = await p;
        return {toast, ok: sim.ok, keys: sim.ok ? Object.keys(sim.result) : sim.error, after: document.querySelector('#toasts').innerText};
    }""")
    assert result["ok"] and "elements" in result["keys"]
    assert "Static study" in result["toast"] and "Cancel" in result["toast"] and result["after"] == ""


def test_assembly_dialogs(page):
    page.evaluate("""async () => {
        await T.run('new_assembly', {name: 'UiAsm'});
        await T.run('insert_component', {source: 'UiPart'});
        await T.run('insert_component', {source: 'UiPart', position: [60, 0, 0]});
        await window.app.refresh();
        await window.app.asmConfigDialog();
    }""")
    page.wait_for_function("T.open()")
    assert "UiPart-1" in page.evaluate("T.text()") and "UiPart-2" in page.evaluate("T.text()")
    page.evaluate("async () => { T.close(); await window.app.displayStatesDialog(); }")
    assert "No display states yet" in page.evaluate("T.text()")
    page.evaluate("T.close()")


def test_keyboard_shortcuts_can_be_changed(page):
    """Tools > Customize Keyboard: a new key for Zoom to Fit works at once, the old one no longer does, and
    the menus show the keys in use."""
    page.evaluate("async () => { T.close(); await T.run('new_part', {name: 'Keys'}); await window.app.refresh(); window.app.customizeKeyboardDialog(); }")
    page.wait_for_function("T.open()")
    assert "Zoom to fit" in page.evaluate("T.text()")
    page.click('.kb-in[data-id="zoom_fit"]')
    page.keyboard.press("Shift+F")
    assert page.input_value('.kb-in[data-id="zoom_fit"]') == "Shift+F"
    page.click('.kb-in[data-id="undo"]')
    page.keyboard.press("Shift+F")  # the same keys twice: a warning, and OK refuses
    assert "Shift+F is used by" in page.inner_text("#kb-warn")
    page.click('.kb-def[data-id="undo"]')
    assert page.inner_text("#kb-warn") == ""
    page.evaluate("T.ok()")
    page.wait_for_function("!T.open()")
    saved = page.evaluate("async () => (await T.run('get_settings')).result.settings.shortcuts")
    assert saved == {"zoom_fit": "Shift+F"}
    page.evaluate("""() => { window.__fits = 0; const f = window.app.vp.zoomFit.bind(window.app.vp);
        window.app.vp.zoomFit = (...a) => { window.__fits++; return f(...a); }; document.activeElement?.blur(); }""")
    page.keyboard.press("f")
    assert page.evaluate("window.__fits") == 0
    page.keyboard.press("Shift+F")
    assert page.evaluate("window.__fits") == 1
    assert page.evaluate("window.app.menuKeyLabel('Ctrl+S')") == "Ctrl+S"
    page.evaluate("async () => { await T.run('set_settings', {values: {shortcuts: {save: 'Ctrl+Alt+S'}}}); await window.app.loadUserSettings(); }")
    assert page.evaluate("window.app.menuKeyLabel('Ctrl+S')") == "Ctrl+Alt+S"
    page.evaluate("async () => { await T.run('set_settings', {reset: true}); await window.app.loadUserSettings(); }")


def test_view_colours_and_mouse_settings_apply(page):
    page.evaluate("""async () => {
        await T.run('create_sketch', {plane: 'Top'});
        await T.run('sketch_rectangle', {sketch: 'Sketch1', corner1: [0, 0], corner2: [30, 20]});
        await T.run('extrude', {sketch: 'Sketch1', depth: 5});
        await window.app.refresh();
        await T.run('set_settings', {values: {view_body_color: '#aa2200', view_background_top: '#101820',
                                              mouse_preset: 'onshape', mouse_zoom_reverse: true}});
        await window.app.loadUserSettings(); }""")
    got = page.evaluate("""() => {
        const vp = window.app.vp, faces = [...vp.faces.values()].filter((m) => !m.userData.own);
        return {bg: document.querySelector('#graphics').style.background, mouse: vp.mouse,
                faces: faces.length, colours: [...new Set(faces.map((m) => m.material.color.getHex()))]}; }""")
    assert "rgb(16, 24, 32)" in got["bg"]
    assert got["mouse"] == {"preset": "onshape", "zoomReverse": True}
    assert got["faces"] > 0 and got["colours"] == [0xAA2200]
    # onshape: a right drag turns the view; a right click without moving is still a right click
    turned = page.evaluate("""() => {
        const vp = window.app.vp, c = document.querySelector('#view'), b = c.getBoundingClientRect();
        let clicks = 0; vp.on('rightclick', () => clicks++);
        const ev = (type, x, buttons) => c.dispatchEvent(new PointerEvent(type, {bubbles: true, button: 2, buttons, pointerId: 1,
            clientX: b.left + x, clientY: b.top + 100}));
        const q0 = vp.camera.quaternion.clone();
        ev('pointerdown', 100, 2); ev('pointermove', 160, 2); ev('pointerup', 160, 0);
        const moved = !vp.camera.quaternion.equals(q0);
        ev('pointerdown', 100, 2); ev('pointerup', 100, 0);
        return {moved, clicks}; }""")
    assert turned == {"moved": True, "clicks": 1}
    page.evaluate("async () => { await T.run('set_settings', {reset: true}); await window.app.loadUserSettings(); }")
    assert page.evaluate("window.app.vp.mouse") == {"preset": "solidworks", "zoomReverse": False}
    assert page.evaluate("document.querySelector('#graphics').style.background") == ""  # back to the theme's own


def test_keyboard_navigation(page):
    """Alt+T goes to the FeatureManager tree and the arrows move through it; F10 opens the menu bar, the arrows
    move through it and Escape closes it."""
    page.evaluate("document.activeElement?.blur()")
    page.keyboard.press("Alt+T")
    first = page.evaluate("document.activeElement.classList.contains('t-row') && document.activeElement.innerText")
    assert first
    page.keyboard.press("ArrowDown")
    second = page.evaluate("document.activeElement.innerText")
    assert second and second != first
    page.keyboard.press("Escape")
    page.keyboard.press("F10")
    assert page.evaluate("document.querySelector('#menubar .menu-root.open').firstChild.textContent") == "File"
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.querySelector('#menubar .menu-root.open').firstChild.textContent") == "Edit"
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.querySelector('#menubar .kfocus').innerText").startswith("Redo")
    page.keyboard.press("Escape")
    assert page.evaluate("document.querySelector('#menubar .menu-root.open')") is None


def test_macos_desktop_menus_go_to_the_menu_bar(page):
    """In the macOS desktop app the page hands its menus to the shell (window.pywebview.api.set_native_menu) and
    hides its own menu bar once they are in the system menu bar; each item runs by id."""
    got = page.evaluate("""async () => {
        const real = Object.getOwnPropertyDescriptor(Navigator.prototype, 'platform');
        Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel', configurable: true});
        let sent = null;
        window.pywebview = {api: {set_native_menu: async (m) => { sent = m; return true; }}};
        window.app.nativeMenuJson = null;
        await window.app.syncNativeMenu();
        const hidden = getComputedStyle(document.querySelector('#menubar')).display === 'none';
        const help = sent.find((m) => m.title === 'Help').items.find((i) => i.title === 'Keyboard Shortcuts');
        window.app.menuInvoke(help.id);
        const shown = !document.querySelector('#tp-help').classList.contains('hidden');
        window.app.showTask('help');  // (close it again)
        // the shell could not: the window keeps its own menu bar
        window.pywebview.api.set_native_menu = async () => false;
        window.app.nativeMenuJson = null;
        await window.app.syncNativeMenu();
        const back = getComputedStyle(document.querySelector('#menubar')).display !== 'none';
        delete window.pywebview; delete navigator.platform;
        return {titles: sent.map((m) => m.title), hidden, shown, back, hasSub: JSON.stringify(sent).includes('"items"')};
    }""")
    assert got["titles"] == ["File", "Edit", "View", "Insert", "Tools", "Collaborate", "Window", "Help"]
    assert got["hidden"] and got["shown"] and got["back"] and got["hasSub"]

def test_connect_an_agent_dialog_copies_every_code_section(page, server):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=server)
    page.evaluate("async () => { T.close(); await window.app.mcpDialog(); }")
    page.wait_for_function("T.open()")
    assert "Settings > Desktop App > Extensions > Manage > Edit Config" in page.evaluate("T.text()")
    # (what each Copy button must copy: the shown text, except the Claude Code block, whose token is shown masked)
    codes = page.evaluate("[...document.querySelectorAll('#modal-box pre.code, #modal-box code')].map(c => c.dataset.copy ?? c.textContent)")
    assert len(codes) >= 4 and page.evaluate("document.querySelectorAll('#modal-box .copy-btn').length") == len(codes)
    for i, text in enumerate(codes):
        page.evaluate("i => document.querySelectorAll('#modal-box .copy-btn')[i].click()", i)
        page.wait_for_function("i => document.querySelectorAll('#modal-box .copy-btn')[i].textContent === 'Copied'", arg=i)
        assert page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n") == text  # (Windows: CRLF)
    assert '"mcpServers"' in codes[1] and codes[2].startswith("claude mcp add")
    assert codes[2].endswith(f'"Authorization: Bearer {TOKEN}"')  # (the real token, not the dots shown)
    assert TOKEN not in page.evaluate("T.text()")
    info = page.evaluate("async () => (await T.run('agent_connection_info', {port: 8765})).result")
    assert info["token_command"] in codes  # (the command that prints the token, not a fixed name)
    page.evaluate("navigator.clipboard.writeText('')")
    page.click("#mcp-copy")  # (the button row under the command: the same copy, real token)
    page.wait_for_function("async () => (await navigator.clipboard.readText()).length > 0")
    assert page.evaluate("navigator.clipboard.readText()") == codes[2]
    page.evaluate("T.close()")


def test_command_manager_keeps_its_height_on_every_tab(page):
    tops = page.evaluate("""async () => {
        const out = {};
        for (const t of [...document.querySelectorAll('#cm-tabs .cm-tab')].map(b => b.textContent)) {
            [...document.querySelectorAll('#cm-tabs .cm-tab')].find(b => b.textContent === t).click();
            await new Promise(requestAnimationFrame);
            out[t] = [document.querySelector('#cm-tabs').getBoundingClientRect().top, document.querySelector('#workspace').getBoundingClientRect().top];
        }
        document.querySelector('#cm-tabs .cm-tab').click();
        return out;
    }""")
    assert len(tops) > 5 and len({tuple(v) for v in tops.values()}) == 1, tops  # (the tabs and the workspace never move)


def test_agent_toasts(page):
    """An agent's command shows a tidy toast (no markup shown as text, readable command name), failures included;
    with the Agent activity pane open there is no toast (the pane lists it)."""
    got = page.evaluate("""async () => {
        document.querySelector('#toasts').innerHTML = '';
        const tp = document.querySelector('#taskpane');
        if (!tp.classList.contains('collapsed')) window.app.showTask('agent');  // (closed to start with)
        tp.classList.add('collapsed');
        window.app.onAgent({command: 'sketch_circle', ok: true});
        window.app.onAgent({command: 'extrude', ok: false, message: 'unknown sketch <Sketch9>'});
        const shown = [...document.querySelectorAll('#toasts .toast')].map((t) => t.innerText);
        const dot = !!document.querySelector('.tp-tab[data-pane="agent"] .dot');
        document.querySelector('#toasts').innerHTML = '';
        window.app.showTask('agent');
        window.app.onAgent({command: 'fillet', ok: true});
        const whileOpen = document.querySelectorAll('#toasts .toast').length;
        window.app.showTask('agent');  // (close it again)
        return {shown, dot, whileOpen};
    }""")
    assert got["shown"][0].replace(" ", " ") == "Agent Sketch circle"
    assert got["shown"][1].replace(" ", " ") == "Agent Extrude failed: unknown sketch <Sketch9>"
    assert "<b>" not in "".join(got["shown"]) and got["dot"]
    assert got["whileOpen"] == 0

def test_agent_setup_dialog_fits_without_sideways_scrolling(page):
    """Help > Agent (MCP) Setup: long paths, the JSON config and the claude mcp add command wrap inside the dialog."""
    wide = page.evaluate("""async () => {
        T.close(); await window.app.mcpDialog(); document.querySelector('#mcp-show')?.click();
        const box = document.querySelector('#modal-box');
        const right = box.getBoundingClientRect().right;
        const out = [...box.querySelectorAll('*')].filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'visible'
            || e.getBoundingClientRect().right > right + 1).map((e) => e.tagName);
        T.close();
        return out;
    }""")
    assert wide == []


def test_no_script_errors(page):
    assert page.errors == []


def test_path_traced_render(page):
    """Render Tools' path tracer (vendored three-gpu-pathtracer) on the current part: a few samples at a
    quarter of the screen size, then checked to be a picture of something, not the background alone.
    Headless CI browsers emulate the GPU in software, where the tracer's first shader compile can take
    minutes: past the time limit the test is skipped rather than failed."""
    page.evaluate("""() => {
        window.__pt = {state: 'running'};
        (async () => {
            await T.run('activate_document', {name: 'UiPart'}); await window.app.refresh();
            const url = await window.app.renderPathTraced({scene: 'studio', floor: true, shadows: true, exposure: 1, background: null},
                                                          0.25, 4, (n, phase) => { window.__pt.phase = phase; });
            const img = new Image(); await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = url; });
            const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
            const g = c.getContext('2d'); g.drawImage(img, 0, 0); const d = g.getImageData(0, 0, c.width, c.height).data;
            let differ = 0, n = 0;
            for (let i = 0; i < d.length; i += 4 * 7) { n++; if (Math.abs(d[i] - d[0]) + Math.abs(d[i + 1] - d[1]) + Math.abs(d[i + 2] - d[2]) > 30) differ++; }
            window.__pt = {state: 'done', width: img.width, different: differ / n};
        })().catch(e => { window.__pt = {state: 'error', error: String(e)}; });
    }""")
    deadline = time.time() + 240
    while time.time() < deadline and page.evaluate("window.__pt.state") == "running":
        time.sleep(1)
    r = page.evaluate("window.__pt")
    if r["state"] == "running" or "no WebGL2" in r.get("error", ""):
        pytest.skip(f"path tracing not possible in this browser in time ({r})")
    assert r["state"] == "done", r
    assert r["width"] > 50 and r["different"] > 0.02  # the part is in the picture
