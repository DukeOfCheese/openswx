"""FastAPI server: SOLIDWORKS-style web UI, JSON command API, live events (SSE) and MCP over HTTP."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from openswx import session as S
from openswx.icons import sprite as icon_sprite
from openswx.server.mesh_binary import encode_mesh

_log = logging.getLogger("openswx.web")

WEB = Path(__file__).parent.parent / "web"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}'{eval}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
VENDOR_CACHE = "public, max-age=31536000, immutable"
UI_CACHE = "no-cache"
_UI_EVAL = os.environ.get("OPENSWX_UI_EVAL", "").strip().lower() not in ("", "0", "false", "no")
_INLINE_SCRIPT = re.compile(r'<script(?=[\s>])(?![^>]*\bsrc=)(?![^>]*\bnonce=)', re.I)


def create_app(mount_mcp: bool = True, token: str | None = "auto", host: str = "127.0.0.1",
               view_token: str | None = None) -> FastAPI:
    """The server application. `token`: what /api/ and /mcp ask for ("auto": the user's saved token; None: no
    token, for tests and --no-token). `host`: the address being served; anything but loopback also accepts
    requests addressed to other names (see security.py)."""
    from openswx.server import security
    if token == "auto":
        token = security.user_token()
    mcp = None
    if mount_mcp:
        from openswx.server.mcp_server import build_mcp
        mcp = build_mcp()
        mcp_app = mcp.streamable_http_app(streamable_http_path="/", host=host)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if mcp is not None:
            async with mcp.session_manager.run():
                yield
        else:
            yield

    app = FastAPI(title="openswx", lifespan=lifespan)
    app.add_middleware(security.Guard, token=token, loopback_only=security.is_loopback(host), view_token=view_token)
    # large JSON (the mesh, the model) about 6x smaller on the wire; the event stream is left uncompressed
    from starlette.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=4096)

    @app.middleware("http")
    async def ui_headers(request: Request, call_next):
        """Every response gets the headers above; the UI's modules are revalidated each load, while the vendored
        libraries are cached for good. The per-response nonce is what the inline import map in the page carries."""
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        response.headers["Content-Security-Policy"] = CSP.format(nonce=nonce, eval=" 'unsafe-eval'" if _UI_EVAL else "")
        path = request.url.path
        if path.startswith("/static/vendor/"):
            response.headers["Cache-Control"] = VENDOR_CACHE
        elif path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = UI_CACHE
        return response

    if mcp is not None:
        app.mount("/mcp", mcp_app)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def index(request: Request):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        nonce = request.state.csp_nonce
        html = _INLINE_SCRIPT.sub(lambda m: f'{m.group(0)} nonce="{nonce}"', html)  # the inline import map: allowed
        html = html.replace("<!-- icon-sprite -->", icon_sprite(), 1)  # one page request, no icon requests
        if S.home_at_start():  # Home up from the first paint: no frame of the empty graphics area before it
            html = html.replace('<div id="home" class="hidden">', '<div id="home" class="starting">', 1)
        return HTMLResponse(html)

    @app.get("/api/health")
    def health():
        """Lock-free status: which command (if any) is running and for how long."""
        import time as _t
        busy = getattr(S.APP, "busy", None)
        return {"app": "openswx", "version": S.APP.version, "busy": busy[0] if busy else None,
                "busy_seconds": round(_t.time() - busy[1], 2) if busy else 0.0}

    @app.get("/api/commands")
    def commands():
        return {n: {"doc": m["doc"], "category": m["category"], "mutates": m["mutates"]} for n, m in S.COMMANDS.items()}

    @app.post("/api/command")
    async def run_command(req: Request):
        body = await req.json()
        if req.scope.get("openswx.view_only"):  # a view-only link: queries only, and none that writes a file
            meta = S.COMMANDS.get(body.get("name"))
            if meta is None or meta["mutates"] or meta["category"] != "query" or body.get("name") in S.FILE_ARGS:
                return JSONResponse({"ok": False, "error": "this is a view-only link: the model cannot be changed from it"},
                                    status_code=403)
        try:
            result = await asyncio.to_thread(S.call, body["name"], body.get("args") or {}, "ui")
        except S.CommandError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except TypeError:  # (arguments are checked before the call: this is a fault inside the command)
            _log.exception("command %s failed", body.get("name"))
            return JSONResponse({"ok": False, "error": f"{body.get('name')} failed inside openswx (details in the log)"},
                                status_code=500)
        return {"ok": True, "result": result, "version": S.APP.version}

    @app.post("/api/preview")
    async def run_preview(req: Request):
        body = await req.json()
        try:
            return await asyncio.to_thread(S.preview, body.get("name"), body.get("args") or {}, not body.get("if_idle"))
        except TypeError:
            _log.exception("preview of %s failed", body.get("name"))
            return {"ok": False, "error": f"no preview for {body.get('name')}"}

    @app.get("/api/state")
    def state():
        return {"version": S.APP.version, "model": S.get_model(), "documents": S.list_documents()}

    @app.get("/api/collab")
    def collab():
        """Live collaboration state for the UI, without waiting for a running command: {"revision", "documents":
        [per cloud document: state, seq, role, peers (presence, draft), locks, broken, conflicts, recent, ...]}.
        `revision` goes up whenever any of it changes (the event stream carries it as "collab")."""
        mod = sys.modules.get("openswx.collab.client")  # (loaded only once a collab_* command ran)
        return mod.status() if mod is not None else {"revision": 0, "documents": []}

    @app.post("/api/collab/presence")
    async def collab_presence(req: Request):
        """collab_presence for the UI's pointer and drafts (about 20 a second): it reads no document, so it does
        not queue behind the command lock (a rebuild would otherwise freeze your pointer on the others' screens)."""
        if req.scope.get("openswx.view_only"):
            return JSONResponse({"ok": False, "error": "this is a view-only link"}, status_code=403)
        body = await req.json()
        args = body.get("args") if isinstance(body, dict) else None
        if not isinstance(args, dict):
            return JSONResponse({"ok": False, "error": "give args"}, status_code=400)
        mod = sys.modules.get("openswx.collab.commands_collab")
        if mod is None:
            return JSONResponse({"ok": False, "error": "live collaboration is not running"}, status_code=400)
        problem = _presence_problem(args, mod.PRESENCE_ARGS)
        if problem:
            return JSONResponse({"ok": False, "error": problem}, status_code=400)
        try:
            return {"ok": True, "result": await asyncio.to_thread(mod.send_presence, **args)}
        except S.DocumentError:  # (not a cloud document, or collaboration not installed): the reason goes to the log
            _log.info("presence not sent", exc_info=True)
            return JSONResponse({"ok": False, "error": "presence not sent: the active document is not a live cloud "
                                                       "document (details in the log)"}, status_code=400)

    @app.get("/api/mesh")
    def mesh():
        return S.get_mesh()

    @app.get("/api/mesh.bin")
    def mesh_binary():
        """The same mesh as /api/mesh, with its big number arrays as little-endian typed-array buffers."""
        return Response(encode_mesh(S.get_mesh()), media_type="application/octet-stream")

    def collab_revision() -> int:
        mod = sys.modules.get("openswx.collab.client")
        return mod.REVISION[0] if mod is not None else 0

    def collab_live() -> bool:
        """Is a cloud document open (others' pointers may move)?"""
        mod = sys.modules.get("openswx.collab.client")
        return mod is not None and any(not c.closed for c in list(mod.CLIENTS.values()))

    pages: dict[str, int] = {}  # a page's id -> its open event streams
    gone_tasks: set = set()

    async def page_gone(page: str) -> None:
        """A page's event stream ended: once it has not come back for a while (a closed or crashed tab, not a
        reconnect), the collaboration locks it took are freed."""
        try:
            grace = float(os.environ.get("OPENSWX_COLLAB_PAGE_GRACE", "15"))
        except ValueError:
            grace = 15.0
        await asyncio.sleep(max(0.0, grace))
        if pages.get(page, 0) == 0:
            pages.pop(page, None)
            mod = sys.modules.get("openswx.collab.client")
            if mod is not None:
                await asyncio.to_thread(mod.release_owner, page)

    @app.get("/api/events")
    async def events(request: Request, page: str | None = None):
        page = page if page and re.fullmatch(r"[A-Za-z0-9-]{8,64}", page) else None

        async def stream():
            last, last_collab = -1, 0
            if page:
                pages[page] = pages.get(page, 0) + 1
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    rev = collab_revision()
                    if S.APP.version != last or rev != last_collab:
                        last, last_collab = S.APP.version, rev
                        entry = S.APP.log[-1] if S.APP.log else None
                        event = {"version": last, "last": entry, **({"collab": rev} if rev else {})}
                        yield f"data: {json.dumps(event)}\n\n"
                    await asyncio.sleep(0.05 if collab_live() else 0.15)  # (others' pointers: ~20 a second)
            finally:
                if page:
                    pages[page] -= 1
                    task = asyncio.get_running_loop().create_task(page_gone(page))
                    gone_tasks.add(task)
                    task.add_done_callback(gone_tasks.discard)
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/upload")
    async def upload(req: Request):
        """Open/import a file sent by the browser: {"filename": ..., "data": base64}."""
        body = await req.json()
        tmp = _temp_path(str(body.get("filename") or ""), "upload")
        tmp.write_bytes(base64.b64decode(body["data"]))
        cmd = "import_file" if body.get("mode") == "import" else "open_part"
        try:
            result = await asyncio.to_thread(S.call, cmd, {"path": str(tmp)})
        except S.CommandError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return {"ok": True, "result": result}

    @app.get("/api/download")
    def download(format: str = "oswprt"):
        if format == "oswprt":
            doc = S.APP.doc
            return Response(json.dumps(doc.to_dict(), indent=1), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{doc.name}.oswprt"'})
        if format not in ("step", "stl", "brep", "x_t", "iges", "3mf", "glb"):
            raise HTTPException(400, "format must be oswprt, step, iges, stl, 3mf, glb, brep or x_t")
        doc = S.APP.active_doc  # assemblies export too
        ext = "igs" if format == "iges" else format
        tmp = _temp_path(f"{doc.name}.{ext}", f"model.{ext}")
        try:
            S.export_file(str(tmp), format)
        except S.CommandError as exc:
            raise HTTPException(400, str(exc)) from exc
        return FileResponse(tmp, filename=tmp.name)

    @app.get("/api/drawing")
    def drawing(sheet: str = "A3", projection: str = "third"):
        try:
            res = S.create_drawing(sheet=sheet, projection=projection)
        except S.CommandError as exc:
            raise HTTPException(400, str(exc)) from exc
        return Response(res["svg"], media_type="image/svg+xml")

    @app.get("/api/drawing_export")
    def drawing_export(format: str = "pdf", inline: bool = False):
        fmt = format.lower() if format.lower() in ("pdf", "dxf", "dwg", "svg") else "pdf"
        tmp = _temp_path(f"{S.APP.active_doc.name}.{fmt}", f"drawing.{fmt}")
        try:
            S.call("export_drawing", {"path": str(tmp), "format": fmt}, "ui")
        except S.CommandError as exc:
            raise HTTPException(400, str(exc)) from exc
        return FileResponse(tmp, filename=tmp.name, content_disposition_type="inline" if inline else "attachment")

    @app.get("/api/image")
    def image(view: str = "isometric", width: int = 800, height: int = 600):
        res = S.get_view_image(view=view, width=width, height=height)
        return Response(base64.b64decode(res["png_base64"]), media_type="image/png")

    return app


def _presence_problem(args: dict, allowed: dict[str, type]) -> str | None:
    """What is wrong with presence `args` (None: nothing). Checked before the call; the message is built from
    the allowed names and types only, never from what the client sent."""
    if any(k not in allowed for k in args):
        return f"collab_presence takes only {', '.join(allowed)}"
    for name, kind in allowed.items():
        v = args.get(name)
        if v is not None and not isinstance(v, kind):
            return f"collab_presence: {name} must be a {kind.__name__}"
    return None


_SAFE_CHARS = re.compile(r"[^\w .()+-]")  # letters and digits of any script survive


def _temp_path(filename: str, fallback: str) -> Path:
    """A path for `filename` in a new private temporary folder. Only the name's last part is kept and odd
    characters become "_", so a name sent by a client (or a document name) cannot point anywhere else."""
    name = _SAFE_CHARS.sub("_", os.path.basename(filename.replace("\\", "/"))).strip(" .") or fallback
    folder = os.path.realpath(tempfile.mkdtemp(prefix="openswx_"))
    full = os.path.normpath(os.path.join(folder, name[:180]))
    if not full.startswith(folder + os.sep):
        raise HTTPException(400, "bad file name")
    return Path(full)


def run(host: str = "127.0.0.1", port: int = 8765, mount_mcp: bool = True, log_level: str = "warning",
        token: str | None = "auto", view_token: str | None = None) -> None:
    import uvicorn
    uvicorn.run(create_app(mount_mcp, token=token, host=host, view_token=view_token), host=host, port=port, log_level=log_level)
