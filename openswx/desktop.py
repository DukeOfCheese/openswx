"""Desktop application: the openswx UI in a native window (Edge WebView2 via pywebview), with the CAD
engine and the MCP endpoint running in-process. This is what `openswx.exe` starts."""
from __future__ import annotations

import socket
import sys
import threading
import time



class _Session:
    """The CAD session module, imported on first use: loading the geometry kernel takes seconds, so the window
    opens first (with a splash) and the kernel loads behind it."""

    def __getattr__(self, name):
        from . import session
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
<script>function progress(p, msg, detail, error) {
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
        except S.CommandError as exc:
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


def run(port: int = 8765, file: str | None = None) -> int:
    import webview

    api = DesktopApi()
    window = webview.create_window("openswx", html=SPLASH, js_api=api, width=1600, height=960,
                                   min_size=(1000, 640), maximized=True, background_color="#F2F3F5")
    api._window = window

    def closing():  # unsaved changes: ask before the window goes (False keeps it open)
        session = sys.modules.get("openswx.session")  # (not loaded yet while the splash shows: nothing to lose)
        if session is None:
            return True
        dirty = [n for n, d in session.APP.documents.items() if getattr(d, "dirty", False)]
        if not dirty:
            return True
        from . import settings
        if not settings.get("confirm_close"):
            return True
        names = ", ".join(dirty[:5]) + (" ..." if len(dirty) > 5 else "")
        return bool(window.create_confirmation_dialog("openswx", f"Close without saving the changes to {names}?"))

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
            from . import security
            from .web_server import create_app  # imports the session: the geometry kernel
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
                S.open_part(file)
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
    from . import recovery
    recovery.clean_exit()  # (the window was closed by the user: nothing to recover next time)
    return 0
