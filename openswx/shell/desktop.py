"""Desktop application: the openswx UI in a native window (Edge WebView2 via pywebview), with the CAD
engine and the MCP endpoint running in-process. This is what `openswx.exe` starts."""

from __future__ import annotations

import functools
import socket
import sys
import threading
import time



class _Session:
    """The CAD session module, imported on first use: loading the geometry kernel takes seconds, so the window
    opens first (with a splash) and the kernel loads behind it."""

    def __getattr__(self, name):
        from openswx import session
        return getattr(session, name)


S = _Session()
SPLASH = """<!doctype html><html><body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#F2F3F5;font:14px 'Segoe UI',sans-serif;color:#333"><div style="width:420px;text-align:center">
<div style="font-size:26px;font-weight:600;letter-spacing:.5px"><span style="color:#c8102e">open</span>swx</div>
<div style="margin:18px 0 6px;display:flex;justify-content:space-between"><span id="msg">Starting&hellip;</span>
<span id="pct" style="font-variant-numeric:tabular-nums">0%</span></div>
<div style="height:6px;background:#dcdfe4;border-radius:3px;overflow:hidden">
<div id="bar" style="height:100%;width:0;background:#c8102e;transition:width .2s"></div></div>
<div id="detail" style="margin-top:6px;height:16px;font-size:11px;color:#888;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis;text-align:left"></div></div>
<button id="x" title="Close" style="display:none;position:fixed;top:0;right:0;width:46px;height:30px;border:0;
background:none;font:16px 'Segoe UI',sans-serif;color:#333">&#x2715;</button>
<script>
// the window has no title bar of its own (see integrate_title_bar): until the page with one loads, the splash can
// be dragged anywhere and, on Windows, closed with its own button (macOS keeps the window's buttons)
addEventListener("pywebviewready", async () => {
  const kind = await pywebview.api.window_chrome();
  if (kind === "mac") document.body.classList.add("pywebview-drag-region");
  if (kind !== "windows") return;
  const x = document.getElementById("x"); x.style.display = "block"; x.onclick = () => pywebview.api.window_control("close");
  addEventListener("mousedown", (e) => { if (e.button === 0 && e.target !== x) pywebview.api.window_control("drag"); });
});
function progress(p, msg, detail, error) {
  document.getElementById("pct").textContent = Math.round(p) + "%";
  document.getElementById("bar").style.width = p + "%";
  const m = document.getElementById("msg"); m.textContent = msg; m.style.color = error ? "#c8102e" : "";
  const d = document.getElementById("detail"); d.textContent = detail || "";
  d.style.whiteSpace = error ? "pre-wrap" : "nowrap"; if (error) d.style.height = "auto";
}</script></body></html>"""

# Startup progress: the boot imports about this many modules (counted from a source checkout; a frozen build
# loads a similar set), so the share loaded so far is the bar's position up to IMPORT_SHARE percent.
BOOT_MODULES = 1950
IMPORT_SHARE = 90
_STAGES = {"OCP": "Loading the OpenCascade geometry kernel", "build123d": "Loading the modelling library",
           "numpy": "Loading numerical libraries", "scipy": "Loading numerical libraries",
           "ezdxf": "Loading drawing export", "openswx": "Loading openswx"}
_SERVER_PKGS = ("mcp", "mcp_types", "pydantic", "pydantic_core", "starlette", "fastapi", "uvicorn", "anyio", "httpx", "sse_starlette")


class _ImportWatch:
    """A meta-path entry that only notes the module being imported (it never finds anything itself)."""

    last = ""

    def find_spec(self, name, path=None, target=None):
        self.last = name
        return None


def _stage(module: str) -> str:
    top = module.split(".")[0]
    if top in _SERVER_PKGS:
        return "Loading the web and MCP server"
    return _STAGES.get(top, "Loading libraries")


def _free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free local port")


class DesktopApi:
    """Native dialogs exposed to the UI as window.pywebview.api.* (replaces browser downloads/uploads)."""

    _window = None  # (private: pywebview walks every public attribute of the js_api object)
    _chrome = ""  # "windows" / "mac" once integrate_title_bar took the window's title bar over, else ""
    _hwnd = 0

    def window_chrome(self):
        """Which platform's title bar the page stands in for: "windows" (the page draws minimise, maximise and
        close and asks for window drags), "mac" (the window's own buttons sit over the page's bar) or "" (the
        window kept its title bar)."""
        return self._chrome

    def window_control(self, action: str = ""):
        """Windows, with the page's title bar: "minimize", "maximize" (or restore, when maximised), "close" or
        "drag" (start moving the window; called on mouse-down on the bar). False where it does not apply."""
        if self._chrome != "windows" or not self._hwnd:
            return False
        import ctypes
        user32 = ctypes.windll.user32
        if action == "minimize":
            user32.ShowWindow(self._hwnd, 6)  # SW_MINIMIZE
        elif action == "maximize":
            user32.ShowWindow(self._hwnd, 9 if user32.IsZoomed(self._hwnd) else 3)  # SW_RESTORE / SW_MAXIMIZE
        elif action == "close":
            user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE (asks about unsaved changes as usual)
        elif action == "drag":
            user32.PostMessageW(self._hwnd, _WM_START_DRAG, 0, 0)
        else:
            return False
        return True

    def set_native_menu(self, menus):
        """macOS: show the page's menus in the system menu bar; True if they are there (the page then hides its own)."""
        return install_native_menu(self._window, menus or [])

    def _dialog(self, kind, name="", types=()):
        import webview
        mode = {"open": webview.FileDialog.OPEN, "save": webview.FileDialog.SAVE}[kind]
        res = self._window.create_file_dialog(mode, save_filename=name, file_types=types)
        if not res:
            return None
        return res if isinstance(res, str) else res[0]

    def pick_file(self, kind: str = "open", name: str = "", types=("All files (*.*)",)):
        """Generic native open/save dialog: returns {"path": ...} or {"cancelled": True}."""
        path = self._dialog(kind, name, tuple(types))
        return {"path": path} if path else {"cancelled": True}

    def open_file(self):
        path = self._dialog("open", types=("openswx and CAD files (*.oswprt;*.oswasm;*.oswdrw;*.step;*.stp;*.igs;*.iges;*.x_t;*.x_b;*.stl;*.3mf;*.FCStd;*.sldprt;*.sldasm;*.slddrw)",
                                           "All files (*.*)"))
        if not path:
            return {"cancelled": True}
        try:
            return {"ok": True, "result": S.call("open_part", {"path": path}, "ui")}
        except S.CommandError as exc:
            return {"ok": False, "error": str(exc)}

    def save_as(self):
        d = S.APP.active_doc
        ext = ".oswasm" if getattr(d, "kind", "part") == "assembly" else ".oswprt"
        path = self._dialog("save", f"{d.name}{ext}", (f"openswx document (*{ext})",))
        if not path:
            return {"cancelled": True}
        try:
            return {"ok": True, "result": S.call("save_part", {"path": path}, "ui")}
        except (S.CommandError, OSError) as exc:  # (OSError: a folder that cannot be written, say)
            return {"ok": False, "error": str(exc)}

    def export(self, fmt):
        d = S.APP.active_doc
        ext = "igs" if fmt == "iges" else fmt
        path = self._dialog("save", f"{d.name}.{ext}", (f"{fmt.upper()} file (*.{ext})",))
        if not path:
            return {"cancelled": True}
        try:
            return {"ok": True, "result": S.call("export_file", {"path": path, "format": fmt}, "ui")}
        except S.CommandError as exc:
            return {"ok": False, "error": str(exc)}

    def save_drawing(self):
        d = S.APP.active_doc
        path = self._dialog("save", f"{d.name}.svg", ("SVG drawing (*.svg)",))
        if not path:
            return {"cancelled": True}
        try:
            return {"ok": True, "result": S.call("create_drawing", {"path": path}, "ui")}
        except S.CommandError as exc:
            return {"ok": False, "error": str(exc)}

    def export_drawing(self, fmt: str = "pdf"):
        d = S.APP.active_doc
        types = {"pdf": "PDF (*.pdf)", "dxf": "DXF (*.dxf)", "dwg": "DWG (*.dwg)", "svg": "SVG drawing (*.svg)"}
        path = self._dialog("save", f"{d.name}.{fmt}", (types.get(fmt, types["pdf"]),))
        if not path:
            return {"cancelled": True}
        try:
            return {"ok": True, "result": S.call("export_drawing", {"path": path, "format": fmt}, "ui")}
        except S.CommandError as exc:
            return {"ok": False, "error": str(exc)}

    def save_image(self):
        import base64
        d = S.APP.active_doc
        path = self._dialog("save", f"{d.name}.png", ("PNG image (*.png)",))
        if not path:
            return {"cancelled": True}
        png = S.get_view_image("isometric", 1600, 1200)["png_base64"]
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(png))
        return {"ok": True, "result": {"saved": path}}


def menu_objects(menus: list, invoke) -> list:
    """pywebview Menu objects for the page's menu tree ([{"title", "items": [...]}], an item being "-", a
    submenu {"title", "items"} or a command {"title", "id"}); a command calls invoke(id)."""
    from webview.menu import Menu, MenuAction, MenuSeparator

    def build(items):
        out = []
        for it in items:
            if it == "-":
                out.append(MenuSeparator())
            elif isinstance(it, dict) and "items" in it:
                out.append(Menu(str(it["title"]), build(it["items"])))
            elif isinstance(it, dict) and "id" in it:
                out.append(MenuAction(str(it["title"]), functools.partial(invoke, int(it["id"]))))
        return out
    return [Menu(str(m["title"]), build(m.get("items") or [])) for m in menus]


def install_native_menu(window, menus: list) -> bool:
    """macOS: put the page's menus in the system menu bar (the app menu stays first; Edit gets the standard Cut,
    Copy, Paste and Select All for text fields). Returns False (the window keeps its own menu bar) elsewhere or
    if pywebview's Cocoa backend has changed under us."""
    if sys.platform != "darwin":
        return False
    try:
        import AppKit
        from PyObjCTools import AppHelper
        from webview import settings as webview_settings
        from webview.platforms import cocoa
    except Exception:  # noqa: BLE001 - not the Cocoa backend
        return False

    def invoke(item_id: int) -> None:
        try:
            window.evaluate_js(f"window.app && window.app.menuInvoke({int(item_id)})")
        except Exception:  # noqa: BLE001 - the page is reloading
            pass

    native = menu_objects(menus, invoke)
    done, ok = threading.Event(), [False]

    def apply():  # (on the main thread: AppKit)
        try:
            inst = next(i for i in cocoa.BrowserView.instances.values() if i.pywebview_window is window)
            webview_settings["SHOW_DEFAULT_MENUS"] = False  # (our Edit menu takes the text-editing items)
            inst.menu = native
            main = inst._recreate_menus(native)
            edit = main.itemWithTitle_("Edit")
            if edit is not None and edit.submenu() is not None:
                sub = edit.submenu()
                for k, (title, action, key) in enumerate([("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                                                          ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")]):
                    sub.insertItem_atIndex_(AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key), k)
                sub.insertItem_atIndex_(AppKit.NSMenuItem.separatorItem(), 4)
            cocoa.BrowserView.current_menu = native
            cocoa.BrowserView.app.setMainMenu_(main)
            ok[0] = True
        except Exception as exc:  # noqa: BLE001 - keep the in-window menus
            print(f"openswx: could not use the macOS menu bar ({exc}); the menus stay in the window", file=sys.stderr)
        finally:
            done.set()

    AppHelper.callAfter(apply)
    done.wait(5)
    return ok[0]


_WM_START_DRAG = 0x8000 + 0x51  # (WM_APP + n: ours, handled by the window procedure below)
_KEEP = []  # the window procedure must outlive the window (ctypes frees a callback nobody references)


def client_top(window_top: int, maximized: bool, frame: int) -> int:
    """Where the page starts in a window whose title bar the page replaces: at the window's very top, except
    maximised, when Windows hangs the frame's thickness off every screen edge and the page must start below it."""
    return window_top + (frame if maximized else 0)


def integrate_title_bar(window, api) -> str:
    """Make the page's title bar the window's: no second bar above it. Called on the GUI thread just before the
    window shows. Returns what the page then has to do ("windows", "mac") or "" when the window keeps its own
    title bar (other platforms, or the toolkit is not as expected).

    Windows: the window keeps its frame (resize borders, snapping, shadow, animations); only the caption is
    taken out of the non-client area, and a drag on the page's bar is handed to the system as a caption drag.
    macOS: the window is created frameless (content under a transparent title bar); the close, minimise and zoom
    buttons pywebview hides are shown again."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            hwnd = int(window.native.Handle.ToInt64())
            user32, comctl32 = ctypes.windll.user32, ctypes.windll.comctl32
            lresult = ctypes.c_ssize_t
            proc_type = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           ctypes.c_size_t, ctypes.c_size_t)
            comctl32.DefSubclassProc.restype = lresult
            comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, proc_type, ctypes.c_size_t, ctypes.c_size_t]
            user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.IsZoomed.argtypes = user32.GetDpiForWindow.argtypes = [wintypes.HWND]

            def frame(h) -> int:  # the sizing frame's thickness at the window's DPI
                dpi = user32.GetDpiForWindow(h) or 96
                return user32.GetSystemMetricsForDpi(33, dpi) + user32.GetSystemMetricsForDpi(92, dpi)

            def proc(h, msg, wparam, lparam, _id, _ref):
                if msg == 0x0083 and wparam:  # WM_NCCALCSIZE: the client area, from the proposed window rectangle
                    rect = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT))  # (the first RECT of the structure)
                    top = rect[0].top
                    comctl32.DefSubclassProc(h, msg, wparam, lparam)  # the borders left, right and below
                    rect[0].top = client_top(top, bool(user32.IsZoomed(h)), frame(h))
                    return 0
                if msg == _WM_START_DRAG:
                    user32.ReleaseCapture()
                    user32.SendMessageW(h, 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN on HTCAPTION: the system moves the window
                    return 0
                return comctl32.DefSubclassProc(h, msg, wparam, lparam)

            callback = proc_type(proc)
            _KEEP.append(callback)
            if not comctl32.SetWindowSubclass(hwnd, callback, 1, 0):
                return ""
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010)  # frame changed
            api._hwnd, api._chrome = hwnd, "windows"
        elif sys.platform == "darwin":
            import AppKit
            for button in (AppKit.NSWindowCloseButton, AppKit.NSWindowMiniaturizeButton, AppKit.NSWindowZoomButton):
                window.native.standardWindowButton_(button).setHidden_(False)
            api._chrome = "mac"
    except Exception as exc:  # noqa: BLE001 - the window then simply keeps (or lacks) its own title bar
        print(f"openswx: could not integrate the title bar ({exc})", file=sys.stderr)
    return api._chrome


def confirm_close(window, message: str) -> bool:
    """Ask before closing with unsaved changes; True closes. On macOS pywebview runs the closing handlers on the
    main thread (inside Cmd+Q / the close button), and its create_confirmation_dialog queues the dialog onto that
    same thread and waits for it: a deadlock that hung the app on quit. There, the alert is shown directly."""
    if sys.platform == "darwin" and threading.current_thread() is threading.main_thread():
        try:
            from webview.platforms.cocoa import BrowserView
            return bool(BrowserView.display_confirmation_dialog("Close", "Cancel", message))
        except Exception as exc:  # noqa: BLE001 - never hang the quit: close (autosave keeps a recovery copy)
            print(f"openswx: could not ask before closing ({exc})", file=sys.stderr)
            return True
    return bool(window.create_confirmation_dialog("openswx", message))


def run(port: int = 8765, file: str | None = None) -> int:
    import webview

    api = DesktopApi()
    mac = sys.platform == "darwin"  # (there the content goes under a transparent title bar; see integrate_title_bar)
    if mac:
        webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True  # (the bar itself drags; its menus and buttons do not)
    window = webview.create_window("openswx", html=SPLASH, js_api=api, width=1600, height=960, frameless=mac,
                                   easy_drag=False, min_size=(1000, 640), maximized=True, background_color="#F2F3F5")
    api._window = window
    window.events.before_show += lambda *_: integrate_title_bar(window, api)

    def shown():  # Windows: changing the frame before the window shows loses its start-up "maximised": put it back
        if api._chrome == "windows":
            import ctypes
            if not ctypes.windll.user32.IsZoomed(api._hwnd):
                ctypes.windll.user32.ShowWindow(api._hwnd, 3)  # SW_MAXIMIZE

    window.events.shown += shown

    def closing():  # unsaved changes: ask before the window goes (False keeps it open)
        session = sys.modules.get("openswx.session")  # (not loaded yet while the splash shows: nothing to lose)
        if session is None:
            return True
        dirty = [n for n, d in session.APP.documents.items() if getattr(d, "dirty", False)]
        if not dirty:
            return True
        from openswx.system import settings
        if not settings.get("confirm_close"):
            return True
        names = ", ".join(dirty[:5]) + (" ..." if len(dirty) > 5 else "")
        return confirm_close(window, f"Close without saving the changes to {names}?")

    window.events.closing += closing
    servers = []

    def show(pct, msg, detail="", error=False):
        import json
        try:
            window.evaluate_js(f"progress({pct:.1f}, {json.dumps(msg)}, {json.dumps(detail)}, {json.dumps(error)})")
        except Exception:  # noqa: BLE001 - the splash may not be ready yet, or already replaced
            pass

    def boot():  # (on pywebview's worker thread, once the window is up)
        watch, base, done = _ImportWatch(), len(sys.modules), threading.Event()
        sys.meta_path.insert(0, watch)

        def report():  # while the imports run, show how far through them the boot is
            while not done.wait(0.15):
                share = min((len(sys.modules) - base) / BOOT_MODULES, 0.99)
                show(share * IMPORT_SHARE, _stage(watch.last), watch.last)

        reporter = threading.Thread(target=report, daemon=True)
        reporter.start()
        try:
            import uvicorn
            from openswx.server import security
            from openswx.server.web_server import create_app  # imports the session: the geometry kernel
            token = security.user_token()
            app = create_app(token=token)
        except Exception as exc:  # noqa: BLE001 - shown on the splash instead of hanging on it
            import traceback
            done.set()
            reporter.join()
            show(0, "openswx could not start", traceback.format_exc(limit=3) or str(exc), error=True)
            return
        finally:
            done.set()
            reporter.join()
            sys.meta_path.remove(watch)
        if file:
            show(IMPORT_SHARE, "Opening " + file.replace("\\", "/").rsplit("/", 1)[-1])
            try:
                S.call("open_part", {"path": file}, "ui")  # (the file the app was opened with: the user's)
            except Exception as exc:  # noqa: BLE001 - the app still opens; the error is reported
                print(f"openswx: could not open {file}: {exc}", file=sys.stderr)
        show(IMPORT_SHARE + 5, "Starting the user interface")
        p = _free_port(port)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=p, log_level="warning"))
        servers.append(server)
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(400):  # wait for the embedded server
            if server.started:
                break
            time.sleep(0.05)
        show(100, "Starting the user interface")
        window.load_url(f"http://127.0.0.1:{p}/?token={token}")  # (kept as a cookie; the page reloads without it)
        print(f"openswx: agents can connect over MCP at http://127.0.0.1:{p}/mcp "
              "(header Authorization: Bearer <token>; the token: `openswx-cli token`)", file=sys.stderr)

    webview.start(boot, private_mode=False)
    for server in servers:
        server.should_exit = True
    from openswx.system import recovery
    recovery.clean_exit()  # (the window was closed by the user: nothing to recover next time)
    return 0
