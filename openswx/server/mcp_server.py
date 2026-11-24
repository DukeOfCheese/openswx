from __future__ import annotations

import base64
import functools
import os

from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from openswx import session as S


def _agent_errors(fn):
    """Surface command errors to the agent verbatim (MCP hides messages of unexpected exceptions)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except S.CommandError as exc:
            raise ToolError(str(exc)) from exc
    return wrapper


INSTRUCTIONS = """These tools drive openswx, an open-source parametric CAD application (parts, assemblies,
drawings). When you talk to the user, call it openswx. It is not SOLIDWORKS and is not connected to SOLIDWORKS
or Dassault Systemes; it has its own geometry kernel (OpenCASCADE). Its menus and feature names will look
familiar to SOLIDWORKS users, and it can open SOLIDWORKS part files, but nothing here runs in SOLIDWORKS.

Units are millimetres and degrees. Standard planes: Front (XY, normal +Z), Top (XZ, normal +Y),
Right (YZ, normal +X).

Typical part workflow:
1. create_sketch(plane) -> sketch name, e.g. 'Sketch1'. Planes: 'Front'|'Top'|'Right', 'Plane1', or a
   planar face from get_topology.
2. Add geometry: sketch_rectangle / sketch_circle / sketch_line / sketch_arc / sketch_polygon / sketch_slot.
   Results return entity ids ('Line3', 'Circle5') and point ids ('P2'); 'ORIGIN' is the sketch origin.
3. Constrain: sketch_relation (coincident, horizontal, ...) and sketch_dimension (Smart Dimension).
   get_feature('Sketch1') shows solve status: under_defined / fully_defined / over_defined.
4. Features: extrude, cut_extrude, revolve, cut_revolve, fillet, chamfer, shell, hole,
   linear_pattern, circular_pattern, mirror, reference_plane.
5. Selecting faces/edges: get_topology() lists them with an id ('Face3', 'Edge7') and, where it has one, a
   persistent `name`. Ids change with every model edit; names do not, so prefer the name when you will
   refer to the same face or edge again later. Either is accepted wherever a face or edge is asked for.
6. Change design intent with set_dimension('D1@Sketch1', value) or edit_feature(name, params);
   suppress_feature, delete_feature, undo/redo are available.
7. Verify with get_mass_properties() and look at the part with get_view_image().

Send several commands at once with batch([{"name": ..., "args": {...}}, ...]): it is one Undo step and, if a
command fails, nothing is changed.

Only the core tools are listed at first. list_toolsets() shows the other areas (sketch tools, more features,
surfaces, sheet metal, weldments, assemblies, drawings, simulation, manufacturing, ...) and
enable_toolset(name) adds an area's tools to this connection.
Every change appears live in the openswx window.
"""

CORE = [
    "new_part", "open_part", "save_part", "import_file", "export_file", "list_documents",
    "get_model", "get_feature", "get_topology", "get_mass_properties", "measure",
    "undo", "redo", "edit_feature", "delete_feature", "suppress_feature", "set_material", "set_dimension",
    "create_sketch", "sketch_line", "sketch_centerline", "sketch_rectangle", "sketch_circle", "sketch_arc",
    "sketch_polygon", "sketch_slot", "sketch_relation", "sketch_dimension", "sketch_delete",
    "extrude", "cut_extrude", "revolve", "cut_revolve", "fillet", "chamfer", "shell", "hole",
    "linear_pattern", "circular_pattern", "mirror", "reference_plane", "set_global_variable", "set_equation",
]
# toolset -> (description, command categories it takes in)
TOOLSETS = {
    "sketch": ("Every sketch tool: splines, ellipses, text, trim / extend / offset / mirror, patterns, 3D sketches, DXF",
               ("sketch",)),
    "features": ("More part features: sweep, loft, draft, rib, dome, wrap, Hole Wizard, threads, patterns, face tools, "
                 "multibody tools, reference geometry", ("features", "bodies")),
    "surfaces": ("Surface modelling: extruded / revolved / swept / lofted / boundary / filled surfaces, knit, trim, thicken",
                 ("surfaces",)),
    "sheet_metal": ("Sheet metal: flanges, hems, jogs, bends, forming tools, corner relief, flat pattern", ("sheet_metal",)),
    "weldments": ("Weldments, mold tools and the Design Library", ("weldments", "mold", "library")),
    "document": ("Documents, history, configurations, design tables, equations, appearance, custom properties, named views",
                 ("document", "edit", "configurations", "equations", "view", "query")),
    "assembly": ("Assemblies: components, mates, patterns, exploded views, motion, interference, fasteners, routing",
                 ("assembly", "routing")),
    "drawing": ("Drawings: sheets, views, dimensions, annotations, tables, export; DimXpert and inspection reports",
                ("drawing", "mbd", "inspection")),
    "simulation": ("Simulation and evaluation: static / frequency / buckling / thermal / nonlinear / fatigue studies, "
                   "flow, draft / thickness / curvature analysis, costing", ("simulation", "evaluate")),
    "manufacturing": ("CAM toolpaths and G-code, plastics fill analysis", ("cam", "plastics")),
    "ecosystem": ("PDM vault, rendering scenes and decals, Composer steps, eDrawings export, macro recording",
                  ("pdm", "render", "composer", "file", "tools")),
    "collab": ("Live collaboration: sign in to an openswx-collab server, share or open cloud documents, locks, "
               "history, named versions, presence", ("collab",)),
}
# commands that remove or overwrite something the user may not get back
DESTRUCTIVE = {"close_document", "delete_custom_material", "discard_recovered", "delete_library_item",
               "save_part", "export_file", "pack_and_go", "pdm_undo_check_out"}
ALWAYS = ("batch",)  # (listed whatever the toolsets)

# Some MCP clients show the model the tool list but not the server's instructions; the tools an agent meets first
# then say themselves what the program is (the command docstrings and docs/commands.md stay as they are)
NOT_SOLIDWORKS = ("openswx is its own open-source CAD program (OpenCASCADE kernel). It is not SOLIDWORKS and does "
                  "not run or connect to SOLIDWORKS; call it openswx when you talk to the user.")
NOTED = ("new_part", "open_part", "import_file", "get_model", "batch")


def tool_description(name: str) -> str:
    """A command's MCP tool description: its docstring, plus the note on what openswx is for the NOTED tools."""
    doc = S.COMMANDS[name]["doc"] or ""
    return f"{doc}\n\n{NOT_SOLIDWORKS}" if name in NOTED else doc


def toolset_commands(name: str) -> list[str]:
    if name == "core":
        return [n for n in CORE if S.COMMANDS[n]["mcp"]]
    if name == "all":
        return [n for n, m in S.COMMANDS.items() if m["mcp"]]
    if name == "plugins":
        return [n for n, m in S.COMMANDS.items() if m["mcp"] and m.get("plugin")]
    if name not in TOOLSETS:
        raise KeyError(name)
    cats = TOOLSETS[name][1]
    return [n for n, m in S.COMMANDS.items() if m["mcp"] and m["category"] in cats]


def starting_toolsets(toolsets: str | list[str] | None = None) -> list[str]:
    raw = toolsets if toolsets is not None else os.environ.get("OPENSWX_TOOLSETS")
    if raw is None:  # (the environment wins; else Tools > Options)
        from openswx.system import settings
        raw = settings.get("mcp_toolsets")
        try:
            return starting_toolsets(raw)
        except ValueError:  # a setting typed wrongly must not stop agents connecting
            return ["core"]
    names = [t.strip() for t in (raw.split(",") if isinstance(raw, str) else raw) if t.strip()] or ["core"]
    bad = [t for t in names if t not in ("core", "all", "plugins", *TOOLSETS)]
    if bad:
        raise ValueError(f"unknown toolset(s) {bad}; expected core, all or one of {sorted(TOOLSETS)}")
    return names


def _annotations(name: str, meta: dict) -> ToolAnnotations:
    return ToolAnnotations(read_only_hint=not meta["mutates"], open_world_hint=False,
                           destructive_hint=(name.startswith("delete_") or name in DESTRUCTIVE) if meta["mutates"] else False)


def build_mcp(toolsets: str | list[str] | None = None) -> MCPServer:
    from openswx import __version__
    mcp = MCPServer("openswx", title="openswx CAD", instructions=INSTRUCTIONS, version=__version__, log_level="WARNING")
    enabled: set[str] = set()
    listed: set[str] = set()

    def add(names) -> list[str]:
        new = []
        for name in names:
            meta = S.COMMANDS[name]
            if name in listed or not meta["mcp"] or name == "get_view_image":
                continue
            mcp.add_tool(_agent_errors(meta["fn"]), name=name, description=tool_description(name), annotations=_annotations(name, meta))
            listed.add(name)
            new.append(name)
        return new

    for ts in starting_toolsets(toolsets):
        enabled.add(ts)
        add(toolset_commands(ts))
    add(ALWAYS)

    @mcp.tool(name="get_view_image", description=S.COMMANDS["get_view_image"]["doc"],
              annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def get_view_image(view: str = "isometric", width: int = 800, height: int = 600, show_edges: bool = True) -> Image:
        try:
            res = S.get_view_image(view=view, width=width, height=height, show_edges=show_edges)
        except S.CommandError as exc:
            raise ToolError(str(exc)) from exc
        return Image(data=base64.b64decode(res["png_base64"]), format="png")

    @mcp.tool(name="list_toolsets", annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def list_toolsets() -> dict:
        """The areas of openswx whose tools can be added to this connection with enable_toolset, with what each
        covers, how many tools it has and whether it is already enabled."""
        out = {"core": {"description": "Part modelling essentials", "tools": len(toolset_commands("core")),
                        "enabled": "core" in enabled or "all" in enabled}}
        sets = dict(TOOLSETS)
        if toolset_commands("plugins"):
            sets["plugins"] = ("Commands added by installed plugins", ())
        for name, (desc, _cats) in sets.items():
            cmds = toolset_commands(name)
            out[name] = {"description": desc, "tools": len(cmds), "enabled": all(c in listed or c == "get_view_image" for c in cmds)}
        return {"toolsets": out, "listed_tools": len(listed) + 3}

    @mcp.tool(name="enable_toolset", annotations=ToolAnnotations(read_only_hint=False, open_world_hint=False))
    async def enable_toolset(name: str, ctx: Context) -> dict:
        """Add an area's tools to this connection (name from list_toolsets, or 'all'). Returns the names of
        the tools added; their descriptions arrive with the refreshed tool list."""
        try:
            cmds = toolset_commands(name)
        except KeyError:
            raise ToolError(f"unknown toolset {name!r}; expected one of {['core', *TOOLSETS, 'plugins', 'all']}") from None
        new = add(cmds)
        enabled.add(name)
        if new:
            try:
                await ctx.notify_tools_changed()
            except Exception:  # noqa: BLE001 - a client without list-changed support re-lists on its own
                pass
        return {"toolset": name, "added": new, "listed_tools": len(listed) + 3}

    @mcp.resource("openswx://model", description="Current document summary (feature tree, volume, state)",
                  mime_type="application/json")
    def model_resource() -> dict:
        return S.get_model()

    @mcp.resource("openswx://commands", description="Every command by toolset, with its one-line description",
                  mime_type="application/json")
    def commands_resource() -> dict:
        def line(n):
            return (S.COMMANDS[n]["doc"] or "").strip().splitlines()[0] if S.COMMANDS[n]["doc"] else ""
        return {ts: {n: line(n) for n in toolset_commands(ts)} for ts in ("core", *TOOLSETS)}

    @mcp.resource("openswx://feature/{name}", description="One feature of the active part: parameters, sketch, status",
                  mime_type="application/json")
    def feature_resource(name: str) -> dict:
        return S.get_feature(name)

    return mcp
