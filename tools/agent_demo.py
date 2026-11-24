from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/mcp"


async def main() -> None:
    home = Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx")  # (where `openswx token` keeps it)
    token = os.environ.get("OPENSWX_TOKEN") or (home / "token").read_text().strip()
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=300) as http, \
            streamable_http_client(URL, http_client=http) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            print(f"connected: {len(tools.tools)} tools")

            async def call(_tool, **args):
                r = await s.call_tool(_tool, args)
                if getattr(r, "is_error", None) or getattr(r, "isError", None):
                    raise RuntimeError(f"{_tool}: {r.content[0].text}")
                c = r.content[0]
                return json.loads(c.text) if getattr(c, "type", "") == "text" else c

            await call("new_part", name="AgentBracket")
            sk = (await call("create_sketch", plane="Top"))["sketch"]
            rect = (await call("sketch_rectangle", sketch=sk, corner1=[0, 0], corner2=[80, 50], centered=True))["result"]
            lines = rect["entities"]
            await call("sketch_dimension", sketch=sk, kind="length", refs=[lines[0]], value=120)
            await call("sketch_dimension", sketch=sk, kind="length", refs=[lines[1]], value=60)
            st = (await call("get_feature", name=sk))["sketch"]["status"]
            print("base sketch:", st["state"], "dof", st["dof"])
            await call("extrude", sketch=sk, depth=10)

            topo = await call("get_topology")
            top = max((f for f in topo["faces"] if f["normal"][1] > 0.99), key=lambda f: f["point"][1])
            boss = (await call("create_sketch", plane=top["id"]))["sketch"]
            await call("sketch_circle", sketch=boss, center=[0, 0], radius=18)
            await call("extrude", sketch=boss, depth=25)

            topo = await call("get_topology")
            cap = max((f for f in topo["faces"] if f["normal"][1] > 0.99), key=lambda f: f["point"][1])
            await call("hole", face=cap["id"], points=[[0, 0]], diameter=16, end_condition="ThroughAll",
                       type="counterbore", cbore_diameter=26, cbore_depth=6)
            topo = await call("get_topology")
            plate_top = [f for f in topo["faces"] if f["normal"][1] > 0.99 and abs(f["point"][1] - 10) < 1e-6][0]
            await call("hole", face=plate_top["id"], points=[[-48, -18]], diameter=8, end_condition="ThroughAll")
            await call("linear_pattern", features=["Hole2"], direction="X", spacing=96, count=2,
                       direction2="Z", spacing2=36, count2=2, reverse2=True)  # face y axis is -Z
            topo = await call("get_topology")
            vertical = [e["id"] for e in topo["edges"] if e["type"] == "line" and abs(e["length"] - 10) < 1e-6]
            await call("fillet", radius=6, edges=vertical)
            await call("set_material", name="6061 Alloy", density_kg_m3=2700)
            mp = await call("get_mass_properties")
            print(f"volume {mp['volume']:.1f} mm^3, mass {mp['mass']:.1f} g, bodies {mp['bodies']}")
            img = await call("get_view_image", view="isometric", width=900, height=650)
            out = "agent_bracket.png"
            with open(out, "wb") as fh:
                fh.write(base64.b64decode(img.data))
            print("model:", [f["name"] for f in (await call("get_model"))["features"]])
            print("image saved to", out)


if __name__ == "__main__":
    asyncio.run(main())
