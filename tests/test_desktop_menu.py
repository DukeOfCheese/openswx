"""macOS desktop app: the page's menus as native menu-bar menus (openswx/desktop.py)."""
import sys

import pytest

webview = pytest.importorskip("webview", reason="pywebview (the desktop extra) is not installed")


def test_menu_tree_becomes_pywebview_menus():
    from webview.menu import Menu, MenuAction, MenuSeparator
    from openswx.desktop import install_native_menu, menu_objects
    called = []
    tree = [{"title": "File", "items": [{"title": "New Part...", "id": 0}, "-",
                                        {"title": "Export", "items": [{"title": "STEP...", "id": 1}]}]},
            {"title": "Help", "items": [{"title": "About openswx", "id": 2}]}]
    menus = menu_objects(tree, called.append)
    assert [m.title for m in menus] == ["File", "Help"]
    new, sep, export = menus[0].items
    assert isinstance(new, MenuAction) and new.title == "New Part..." and isinstance(sep, MenuSeparator)
    assert isinstance(export, Menu) and export.items[0].title == "STEP..."
    new.function()
    export.items[0].function()
    menus[1].items[0].function()
    assert called == [0, 1, 2]  # each item runs its own id in the page
    if sys.platform != "darwin":
        assert install_native_menu(None, tree) is False  # elsewhere the window keeps its own menu bar


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS system menu bar")
def test_menus_reach_the_macos_menu_bar():
    """A real (hidden) pywebview window on macOS: the page's menus become the system menu bar after the app menu,
    with no second Edit menu, and Edit starts with Cut, Copy, Paste and Select All for text fields."""
    import json
    import subprocess
    from pathlib import Path
    probe = Path(__file__).with_name("macos_menu_probe.py")
    r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=120,
                       cwd=Path(__file__).resolve().parent.parent)
    line = [x for x in r.stdout.splitlines() if x.startswith("{")]
    assert line, r.stdout + r.stderr
    out = json.loads(line[-1])
    assert out["ok"] is True
    assert out["bar"][1:] == ["File", "Edit", "Help"]  # (the first is the app menu)
    assert out["edit"][:4] == ["Cut", "Copy", "Paste", "Select All"] and out["edit"][5] == "Undo"
