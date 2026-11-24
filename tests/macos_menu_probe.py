"""Run by tests/test_desktop_menu.py on macOS: open a hidden pywebview window, install a menu tree with
openswx.desktop.install_native_menu and print the system menu bar's titles (and the Edit menu's) as JSON."""
import json
import threading

import webview

from openswx.desktop import install_native_menu

TREE = [{"title": "File", "items": [{"title": "New Part...", "id": 0}]},
        {"title": "Edit", "items": [{"title": "Undo", "id": 1}, "-", {"title": "Rebuild", "id": 2}]},
        {"title": "Help", "items": [{"title": "About openswx", "id": 3}]}]

if __name__ == "__main__":
    win = webview.create_window("menu probe", html="<p>probe</p>", hidden=True)
    out = {}

    def check():
        try:
            out["ok"] = install_native_menu(win, TREE)
            import AppKit
            from PyObjCTools import AppHelper
            ev = threading.Event()

            def read():
                m = AppKit.NSApp.mainMenu()
                out["bar"] = [str(m.itemAtIndex_(i).title()) for i in range(m.numberOfItems())]
                edit = m.itemWithTitle_("Edit")
                sub = edit.submenu() if edit is not None else None
                out["edit"] = [str(sub.itemAtIndex_(i).title()) for i in range(sub.numberOfItems())] if sub else None
                ev.set()
            AppHelper.callAfter(read)
            ev.wait(10)
        finally:
            print(json.dumps(out), flush=True)
            win.destroy()

    webview.start(check)
