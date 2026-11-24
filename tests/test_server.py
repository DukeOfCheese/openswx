import asyncio
import json

from fastapi.testclient import TestClient

from openswx import session as S
from openswx.server.mcp_server import build_mcp
from openswx.server.web_server import create_app


def test_mcp_tools_and_calls():
    server = build_mcp()

    async def run():
        tools = {t.name for t in await server.list_tools()}
        for name in ("create_sketch", "sketch_rectangle", "extrude", "fillet", "get_topology", "get_view_image",
                     "set_dimension", "get_mass_properties", "undo"):
            assert name in tools
        await server.call_tool("new_part", {})
        r = await server.call_tool("create_sketch", {"plane": "Top"})
        assert json.loads(r.content[0].text)["sketch"] == "Sketch1"
        await server.call_tool("sketch_circle", {"sketch": "Sketch1", "center": [0, 0], "radius": 10})
        await server.call_tool("extrude", {"sketch": "Sketch1", "depth": 5})
        img = await server.call_tool("get_view_image", {"width": 120, "height": 90})
        assert img.content[0].type == "image"
        try:
            bad = await server.call_tool("extrude", {"sketch": "Nope", "depth": 5})
            text = bad.content[0].text
            assert getattr(bad, "is_error", getattr(bad, "isError", False))
        except Exception as exc:  # in-process calls raise the ToolError directly
            text = str(exc)
        assert "unknown sketch" in text  # the agent sees the real reason
    asyncio.run(run())
    assert abs(S.get_mass_properties()["volume"] - 3.141592653589793 * 100 * 5) < 1e-6


def test_rest_api_and_origin_tagging():
    with TestClient(create_app(mount_mcp=False, token=None), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/").status_code == 200
        r = c.post("/api/command", json={"name": "new_part", "args": {}})
        assert r.json()["ok"]
        c.post("/api/command", json={"name": "create_sketch", "args": {"plane": "Front"}})
        c.post("/api/command", json={"name": "sketch_rectangle", "args": {"sketch": "Sketch1", "corner1": [0, 0], "corner2": [10, 10]}})
        pv = c.post("/api/preview", json={"name": "extrude", "args": {"sketch": "Sketch1", "depth": 4}}).json()
        assert pv["ok"] and pv["faces"]
        assert c.post("/api/command", json={"name": "extrude", "args": {"sketch": "Sketch1", "depth": 4}}).json()["ok"]
        st = c.get("/api/state").json()
        assert st["model"]["volume_mm3"] == 400
        assert S.APP.log[-1]["origin"] == "ui"
        err = c.post("/api/command", json={"name": "fillet", "args": {"radius": 99, "edges": ["Edge0"]}})
        assert err.status_code == 400 and not err.json()["ok"]
        assert c.get("/api/mesh").json()["faces"]
        assert c.get("/api/image?width=100&height=80").headers["content-type"] == "image/png"
        assert c.get("/api/download?format=step").status_code == 200
        d = c.get("/api/drawing?sheet=A4")
        assert d.status_code == 200 and d.headers["content-type"].startswith("image/svg+xml")
        # assemblies through the same API the UI uses
        part = st["model"]["name"]
        assert c.post("/api/command", json={"name": "new_assembly", "args": {"name": "A1"}}).json()["ok"]
        r = c.post("/api/command", json={"name": "insert_component", "args": {"source": part}}).json()
        assert r["ok"] and r["result"]["component"] == f"{part}-1"
        c.post("/api/command", json={"name": "insert_component", "args": {"source": part, "position": [30, 0, 0]}})
        mesh = c.get("/api/mesh").json()
        assert mesh["faces"] and all("/" in f["id"] for f in mesh["faces"])
        pv = c.post("/api/preview", json={"name": "add_mate", "args": {"type": "coincident", "entity1": f"{part}-1/Face0",
                                                                        "entity2": f"{part}-2/Face0"}}).json()
        assert "ok" in pv
        assert c.get("/api/state").json()["model"]["kind"] == "assembly"


def test_bad_arguments_and_file_names_are_handled_safely(tmp_path):
    """Arguments are checked against the command's signature before the call (the message names the
    problem, not the interpreter's error), and file names sent by a client cannot leave the temporary folder."""
    from openswx.server.web_server import _temp_path
    with TestClient(create_app(mount_mcp=False, token=None), base_url="http://127.0.0.1:8765") as c:
        r = c.post("/api/command", json={"name": "new_part", "args": {"nmae": "X"}})
        assert r.status_code == 400
        assert r.json()["error"] == "bad arguments for new_part: unknown argument nmae (it takes name)"
        r = c.post("/api/command", json={"name": "extrude", "args": {}})
        assert "missing sketch" in r.json()["error"]
    for evil in ("../../etc/passwd", r"..\..\Windows\win.ini", "/abs/path.step", r"C:\x\y.step"):
        p = _temp_path(evil, "upload")
        assert ".." not in p.parts and p.parent.name.startswith("openswx_") and p.name in ("passwd", "win.ini", "path.step", "y.step")
    assert _temp_path("...", "upload").name == "upload"
    assert _temp_path("a*b?.step", "x").name == "a_b_.step"
    assert _temp_path("Träger.step", "x").name == "Träger.step"  # letters of any script stay
