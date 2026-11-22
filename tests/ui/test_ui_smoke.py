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
               OPENSWX_AUTOSAVE="off", OPENSWX_LOG="off", OPENSWX_PLUGINS="off")
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
        ["File", "Edit", "View", "Insert", "Tools", "Window", "Help"]
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


def test_no_script_errors(page):
    assert page.errors == []
