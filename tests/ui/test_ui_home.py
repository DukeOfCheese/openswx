"""The Home page in a real browser (show_home on): shown at start, hidden by every way out, back from
File > Home, and opening a recent file leaves no stray empty part. Needs Playwright, like test_ui_smoke.py."""
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
TOKEN = "ui-home-token-0123456789abcdefghijklmnopqrstuvwx"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, OPENSWX_HOME=str(tmp_path_factory.mktemp("home")), OPENSWX_TOKEN=TOKEN,
               OPENSWX_AUTOSAVE="off", OPENSWX_LOG="off", OPENSWX_PLUGINS="off", OPENSWX_SHOW_HOME="1")
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
  docs: async () => (await window.app.api('/api/state')).documents.documents.map(d => d.name),
};
"""
SHOWN = "!document.querySelector('#home').classList.contains('hidden')"
HIDDEN = "document.querySelector('#home').classList.contains('hidden')"


def load(page, url):
    page.goto(url)
    page.wait_for_function("window.app && window.app.version >= 0")
    page.evaluate(HELPERS)


def test_home_shows_at_start(server, page):
    load(page, f"{server}/?token={TOKEN}")
    page.wait_for_function(SHOWN)
    page.wait_for_function("document.querySelector('#home .hm-recent').innerText.includes('No recent files yet')")
    assert page.evaluate("[...document.querySelectorAll('#home .hm-action b')].map(e => e.textContent)") == \
        ["New Part", "New Assembly", "Open…"]
    assert page.evaluate("document.querySelectorAll('#home .hm-tut').length") > 0


def test_escape_hides_and_file_home_brings_it_back(page):
    page.keyboard.press("Escape")
    page.wait_for_function(HIDDEN)
    page.click("#menubar .menu-root:has-text('File')")
    page.click("#menubar .menu-item:has-text('Home')")
    page.wait_for_function(SHOWN)
    page.keyboard.press("Escape")
    page.wait_for_function(HIDDEN)
    page.keyboard.press("Control+Shift+H")
    page.wait_for_function(SHOWN)


def test_new_part_hides_home_and_leaves_one_document(page):
    page.click("#home .hm-action[data-act='part']")
    page.wait_for_function(HIDDEN)
    page.wait_for_function("window.app.model && !window.app.model.throwaway")
    assert len(page.evaluate("T.docs()")) == 1  # (the part made on demand gave way)


def test_open_recent_file_leaves_no_part1(server, page, tmp_path):
    block, ghost = tmp_path / "Block.oswprt", tmp_path / "Ghost.oswprt"
    page.evaluate("""async ([block, ghost]) => {
        await T.run('new_part', {name: 'Block'});
        const sk = (await T.run('create_sketch', {plane: 'Top'})).result.sketch;
        await T.run('sketch_rectangle', {sketch: sk, corner1: [0, 0], corner2: [20, 10]});
        await T.run('extrude', {sketch: sk, depth: 5});
        await T.run('save_part', {path: block});
        await T.run('new_part', {name: 'Ghost'});
        await T.run('save_part', {path: ghost});
        for (const n of await T.docs()) await T.run('close_document', {name: n});
    }""", [str(block), str(ghost)])
    ghost.unlink()
    load(page, server + "/")  # (a fresh start: Home again, now with the recent files)
    page.wait_for_function(SHOWN)
    page.wait_for_function("document.querySelectorAll('#home .hm-file').length === 2")
    assert page.evaluate("[...document.querySelectorAll('#home .hm-file .hm-name')].map(e => e.textContent)") == \
        ["Ghost.oswprt", "Block.oswprt"]
    assert page.evaluate("document.querySelector('#home .hm-file.missing').innerText").count("Not found") == 1
    page.click("#home .hm-file:not(.missing)")
    page.wait_for_function(HIDDEN)
    page.wait_for_function("window.app.model.name === 'Block'")
    assert page.evaluate("T.docs()") == ["Block"]


def test_remove_from_recent_list(page):
    page.keyboard.press("Control+Shift+H")
    page.wait_for_function(SHOWN)
    page.wait_for_function("document.querySelectorAll('#home .hm-file').length === 2")
    page.click("#home .hm-file.missing .hm-remove")
    page.wait_for_function("document.querySelectorAll('#home .hm-file').length === 1")
    assert page.evaluate("document.querySelector('#home .hm-file .hm-name').textContent") == "Block.oswprt"


def test_an_agent_at_work_hides_home(page):
    page.wait_for_function(SHOWN)
    page.evaluate("window.app.onAgent({command: 'extrude', ok: true})")
    page.wait_for_function(HIDDEN)


def test_no_script_errors(page):
    assert page.errors == []
