"""The tray icon of `openswx mcp` (openswx/shell/tray.py): shown only where it can be, its Quit item ends the
process, and quitting keeps unsaved work for recovery. pystray itself is replaced by a stand-in here, so these
tests run the same on every platform and never put an icon on the test machine's desktop."""

import sys
import types

import numpy as np
import pytest

from openswx import session as S
from openswx.shell import brand, tray
from openswx.system import recovery


class _Item:
    def __init__(self, text, action, enabled=True, default=False):
        self.text, self.action, self.enabled, self.default = text, action, enabled, default


class _Icon:
    def __init__(self, name, image, title, menu=None):
        self.name, self.image, self.title, self.menu = name, image, title, menu
        self.ran, self.stopped = False, False

    def run(self):
        self.ran = True

    def stop(self):
        self.stopped = True


def _fake_pystray():
    return types.SimpleNamespace(Icon=_Icon, MenuItem=_Item, Menu=lambda *items: list(items))


@pytest.fixture
def desktop(monkeypatch):
    """A desktop the icon can be shown on: tray turned on, Windows, a pystray stand-in."""
    monkeypatch.delenv("OPENSWX_TRAY", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(tray, "_load_pystray", _fake_pystray)


def _item(t, text):
    return next(i for i in t.icon.menu if i.text == text)


def test_tray_is_off_in_tests_and_when_turned_off(monkeypatch):
    assert tray.unavailable_reason() is not None  # (tests/conftest.py sets OPENSWX_TRAY=off)
    assert tray.start("openswx MCP server running") is None
    monkeypatch.setenv("OPENSWX_TRAY", "0")
    assert "OPENSWX_TRAY" in tray.unavailable_reason()


def test_no_tray_without_a_desktop_or_on_macos(monkeypatch):
    monkeypatch.delenv("OPENSWX_TRAY", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert "DISPLAY" in tray.unavailable_reason()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert tray.unavailable_reason() is None
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "macOS" in tray.unavailable_reason()
    monkeypatch.setattr(sys, "platform", "win32")
    assert tray.unavailable_reason() is None


def test_server_runs_without_the_icon_when_pystray_is_missing(desktop, monkeypatch):
    def missing():
        raise ImportError("No module named 'pystray'")
    monkeypatch.setattr(tray, "_load_pystray", missing)
    assert tray.start("openswx MCP server running") is None


def test_icon_shows_what_is_running_and_quit_ends_it(desktop):
    quits = []
    pytest.importorskip("PIL")
    t = tray.start("openswx MCP server running", on_quit=lambda: quits.append(1), url="http://127.0.0.1:8765/")
    t.thread.join(5)
    assert t.icon.ran and t.icon.title == "openswx MCP server running"  # (the tooltip)
    assert t.icon.image.size == (64, 64) and t.icon.image.mode == "RGBA"
    texts = [i.text for i in t.icon.menu]
    assert texts == ["openswx MCP server running", "Open openswx", "Quit openswx"]
    assert not t.icon.menu[0].enabled  # (a label, not an action)
    _item(t, "Quit openswx").action(t.icon, None)
    assert quits == [1] and t.icon.stopped


def test_no_open_item_without_the_live_ui(desktop):
    pytest.importorskip("PIL")
    t = tray.start("openswx MCP server running", on_quit=lambda: None)
    assert [i.text for i in t.icon.menu] == ["openswx MCP server running", "Quit openswx"]


def test_quit_with_nothing_unsaved_ends_cleanly():
    S.call("new_part", {})
    for d in S.APP.documents.values():
        d.dirty = False
    folder = recovery.session_dir()
    codes = []
    tray.shutdown(exit=codes.append)
    assert codes == [0]
    assert not folder.exists()  # (a normal exit: nothing to recover next time)


def test_quit_keeps_unsaved_work_for_recovery():
    S.call("new_part", {})
    name = S.APP.active
    S.APP.documents[name].dirty = True
    codes = []
    try:
        tray.shutdown(exit=codes.append)
        assert codes == [0]
        assert name in [e["name"] for e in _recovered_here()]
    finally:
        recovery.clean_exit()


def _recovered_here():
    d = recovery._state["dir"]
    import json
    return [json.loads(m.read_text(encoding="utf-8")) for m in d.glob("*.json")]


def test_quit_when_relaying_needs_no_documents(monkeypatch):
    monkeypatch.delitem(sys.modules, "openswx.session")  # (the relay never imports the session)
    codes = []
    tray.shutdown(exit=codes.append)
    assert codes == [0]


def test_brand_mark_is_a_hexagon():
    img = brand.hexagon(64)
    assert img.shape == (64, 64, 4) and img.dtype == np.uint8
    assert img[0, 0, 3] == 0 and img[32, 32, 3] == 255  # transparent corner, opaque centre
    area = (img[..., 3] > 0).mean()
    r = 0.46  # (circumradius / size, as drawn)
    assert area == pytest.approx(3 * np.sqrt(3) / 2 * r * r, rel=0.03)  # a regular hexagon's area
