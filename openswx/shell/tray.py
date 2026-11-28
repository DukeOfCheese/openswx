"""A system tray icon for `openswx mcp`, the process MCP clients (Claude Desktop, ...) start in the background.

Such a client often starts with the computer and runs `openswx-cli mcp` without a window, so without this the
process could only be ended from the Task Manager. While it runs, the tray (the Windows notification area, a
Linux tray) shows the openswx mark with a tooltip saying what it is, and a menu to open the live UI and to quit.

The icon needs the optional `tray` extra (pip install openswx[tray]: pystray and Pillow). It is left out, and the
MCP server runs exactly as before, when that is not installed, when there is no desktop to show it on (no
DISPLAY / WAYLAND_DISPLAY on Linux), on macOS (pystray must own the main thread there, which the stdio server
needs), or when `OPENSWX_TRAY=off` (the tests set that).
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from typing import Callable


def unavailable_reason() -> str | None:
    """Why no tray icon can be shown here, or None when it can."""
    if os.environ.get("OPENSWX_TRAY", "").strip().lower() in ("off", "0", "no", "false"):
        return "turned off (OPENSWX_TRAY=off)"
    if sys.platform == "darwin":
        return "not on macOS yet (the menu bar icon would need the main thread)"
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return "no desktop session (no DISPLAY or WAYLAND_DISPLAY)"
    return None


def _load_pystray():
    import pystray  # (optional: the `tray` extra)
    return pystray


def _image(size: int = 64):
    from PIL import Image

    from openswx.shell.brand import hexagon
    return Image.fromarray(hexagon(size), "RGBA")


class Tray:
    """A running tray icon. `quit()` is what its Quit item does; `stop()` only removes the icon."""

    def __init__(self, icon, on_quit: Callable[[], None]):
        self.icon = icon
        self._on_quit = on_quit
        self.thread: threading.Thread | None = None

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001 - the icon may already be gone
            pass

    def quit(self) -> None:
        self.stop()
        self._on_quit()


def start(title: str, on_quit: Callable[[], None] | None = None, url: str | None = None) -> Tray | None:
    """Show the tray icon (tooltip `title`) on a background thread and return it, or None when no icon can be
    shown here (see unavailable_reason; a missing pystray / Pillow counts too). Its menu: `Open openswx` (when
    `url`, the live UI, is given) and `Quit openswx`, which calls `on_quit` (default: shutdown())."""
    if unavailable_reason():
        return None
    try:
        pystray = _load_pystray()
        image = _image()
    except Exception:  # noqa: BLE001 - not installed (or a broken install): run without the icon
        return None
    on_quit = on_quit or shutdown
    tray: Tray | None = None

    def do_quit(icon=None, item=None):
        tray.quit()

    items = [pystray.MenuItem(title, None, enabled=False)]
    if url:
        items.append(pystray.MenuItem("Open openswx", lambda icon=None, item=None: webbrowser.open(url), default=True))
    items.append(pystray.MenuItem("Quit openswx", do_quit))
    try:
        icon = pystray.Icon("openswx", image, title, menu=pystray.Menu(*items))
    except Exception:  # noqa: BLE001 - no usable backend on this desktop
        return None
    tray = Tray(icon, on_quit)

    def run():
        try:
            icon.run()
        except Exception as exc:  # noqa: BLE001 - a tray that cannot start must not take the server down
            print(f"openswx: no tray icon ({exc})", file=sys.stderr)

    tray.thread = threading.Thread(target=run, name="openswx-tray", daemon=True)
    tray.thread.start()
    return tray


def shutdown(app=None, exit: Callable[[int], object] = os._exit) -> None:
    """End this openswx process from the tray. Documents with unsaved changes are kept in the recovery folder
    (the next start offers them back, as after a crash); with none, the recovery folder is removed as on a normal
    exit. Then the process ends: the MCP server's main thread is blocked reading the client's stdin, which
    cannot be interrupted portably, so this exits directly instead of unwinding it."""
    from openswx.system import recovery
    if app is None and "openswx.session" in sys.modules:  # (not imported, as when relaying: no documents here)
        app = sys.modules["openswx.session"].APP
    try:
        dirty = False
        if app is not None:
            with app.lock:
                dirty = any(getattr(d, "dirty", False) for d in app.documents.values())
        if dirty:
            recovery.save_now(app)
            recovery._state["stop"].set()  # (no more autosaves while exiting)
        else:
            recovery.clean_exit()
    except Exception as exc:  # noqa: BLE001 - quitting must work even if recovery cannot be written
        print(f"openswx: recovery copies not written ({exc})", file=sys.stderr)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass
    exit(0)
