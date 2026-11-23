"""FastAPI server: SOLIDWORKS-style web UI, JSON command API, live events (SSE) and MCP over HTTP."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import session as S

WEB = Path(__file__).parent / "web"


def create_app(mount_mcp: bool = True, token: str | None = "auto", host: str = "127.0.0.1",
               view_token: str | None = None) -> FastAPI:
    """The server application. `token`: what /api/ and /mcp ask for ("auto": the user's saved token; None: no
    token, for tests and --no-token). `host`: the address being served; anything but loopback also accepts
    requests addressed to other names (see security.py)."""
    from . import security
    if token == "auto":
        token = security.user_token()
    mcp = None
    if mount_mcp:
        from .mcp_server import build_mcp
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

    @app.middleware("http")
    async def no_stale_ui(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # always revalidate UI modules
        return response

    if mcp is not None:
        app.mount("/mcp", mcp_app)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

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
        except TypeError as exc:
            return JSONResponse({"ok": False, "error": f"bad arguments: {exc}"}, status_code=400)
        return {"ok": True, "result": result, "version": S.APP.version}

    @app.post("/api/preview")
    async def run_preview(req: Request):
        body = await req.json()
        try:
            return await asyncio.to_thread(S.preview, body.get("name"), body.get("args") or {}, not body.get("if_idle"))
        except TypeError as exc:
            return {"ok": False, "error": f"bad arguments: {exc}"}

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

    @app.get("/api/mesh")
    def mesh():
        return S.get_mesh()

    def collab_revision() -> int:
        mod = sys.modules.get("openswx.collab.client")
        return mod.REVISION[0] if mod is not None else 0

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
                    await asyncio.sleep(0.15)
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
        name = Path(body["filename"]).name
        tmp = Path(tempfile.mkdtemp(prefix="openswx_")) / name
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
        tmp = Path(tempfile.mkdtemp(prefix="openswx_")) / f"{doc.name}.{ext}"
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
        import tempfile
        fmt = format.lower() if format.lower() in ("pdf", "dxf", "dwg", "svg") else "pdf"
        tmp = Path(tempfile.mkdtemp()) / f"{S.APP.active_doc.name}.{fmt}"
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


def run(host: str = "127.0.0.1", port: int = 8765, mount_mcp: bool = True, log_level: str = "warning",
        token: str | None = "auto", view_token: str | None = None) -> None:
    import uvicorn
    uvicorn.run(create_app(mount_mcp, token=token, host=host, view_token=view_token), host=host, port=port, log_level=log_level)
