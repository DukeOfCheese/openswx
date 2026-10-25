"""MCP server exposing every openswx command as a tool for agents.

Run standalone over stdio (`python -m openswx mcp`, which also serves the web UI so you can watch the
agent work) or over streamable HTTP mounted at /mcp by `python -m openswx serve`.
"""
from __future__ import annotations

import base64

import functools

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import session as S


def _agent_errors(fn):
    """Surface command errors to the agent verbatim (MCP hides messages of unexpected exceptions)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except S.CommandError as exc:
            raise ToolError(str(exc)) from exc
    return wrapper

INSTRUCTIONS = """openswx is an open-source parametric part modeler with a SOLIDWORKS-style workflow.
Units are millimetres and degrees. Standard planes: Front (XY, normal +Z), Top (XZ, normal +Y),
Right (YZ, normal +X).

Typical workflow (same as SOLIDWORKS):
1. create_sketch(plane) -> sketch name, e.g. 'Sketch1'. Planes: 'Front'|'Top'|'Right', 'Plane1', or a
   planar face id from get_topology ('Face5').
2. Add geometry: sketch_rectangle / sketch_circle / sketch_line / sketch_arc / sketch_polygon / sketch_slot.
   Results return entity ids ('Line3', 'Circle5') and point ids ('P2'); 'ORIGIN' is the sketch origin.
3. Constrain: sketch_relation (coincident, horizontal, ...) and sketch_dimension (Smart Dimension).
   get_feature('Sketch1') shows solve status: under_defined / fully_defined / over_defined.
4. Features: extrude, cut_extrude, revolve, cut_revolve, fillet, chamfer, shell, hole,
   linear_pattern, circular_pattern, mirror, reference_plane.
5. Selecting faces/edges: call get_topology() for current ids (Face#, Edge#) with points, normals and
   sizes; ids change after every model edit, so re-query before selecting.
6. Change design intent with set_dimension('D1@Sketch1', value) or edit_feature(name, params);
   suppress_feature, rollback, delete_feature, undo/redo are available.
7. Verify with get_mass_properties() and look at the part with get_view_image().
Every change appears live in the web UI (default http://127.0.0.1:8765).
"""


def build_mcp() -> MCPServer:
    from . import __version__
    mcp = MCPServer("openswx", title="openswx CAD", instructions=INSTRUCTIONS, version=__version__, log_level="WARNING")
    for name, meta in S.COMMANDS.items():
        if not meta["mcp"] or name == "get_view_image":
            continue
        mcp.add_tool(_agent_errors(meta["fn"]), name=name, description=meta["doc"])

    @mcp.tool(name="get_view_image", description=S.COMMANDS["get_view_image"]["doc"])
    def get_view_image(view: str = "isometric", width: int = 800, height: int = 600, show_edges: bool = True) -> Image:
        try:
            res = S.get_view_image(view=view, width=width, height=height, show_edges=show_edges)
        except S.CommandError as exc:
            raise ToolError(str(exc)) from exc
        return Image(data=base64.b64decode(res["png_base64"]), format="png")

    @mcp.resource("openswx://model", description="Current part model summary (feature tree)", mime_type="application/json")
    def model_resource() -> dict:
        return S.get_model()

    return mcp
