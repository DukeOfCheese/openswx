# openswx

Open-source parametric part modeling with a SOLIDWORKS-style workflow — and a built-in
**MCP server** so AI agents can design parts in it while you watch.

- **Feature-based modeling**: constrained 2D sketches → Extruded Boss/Cut (Blind, Through All, Mid Plane,
  Up To Next/Vertex/Surface, Offset From Surface, draft, two directions), Revolved Boss/Cut, Swept Boss/Cut,
  Lofted Boss/Cut, Fillet, Chamfer, Shell, Draft, Hole (simple / counterbore / countersink), Linear &
  Circular Pattern, Mirror, Reference Planes. Full parametric rebuild, rollback bar, suppress, rename, undo/redo.
- **Design intent**: Equations and Global Variables in SOLIDWORKS syntax (`"D1@Sketch1" = "width"/2`),
  Configurations with per-configuration suppression and dimension overrides.
- **Sketcher**: line, centerline, corner/center rectangle, circle, centre-point & 3-point arc, polygon,
  slot, point; Trim / Extend / Offset / Mirror Entities, Sketch Fillet; 12 relation types; Smart Dimension with a Modify box;
  under/fully/over-defined status (blue/black/red), point dragging, construction geometry.
- **Sheet metal**: Base Flange (thickness, bend radius, K-factor), Edge Flange (any angle, exact bend),
  Flatten with bend allowance. **Surfaces**: Extruded Surface, Thicken. **Weldments**: Structural Members
  (square/rectangular tube, pipe, angle, bar) along layout sketches.
- **Assemblies**: insert parts (linked: editing the part updates the assembly), mates (coincident, parallel,
  perpendicular, tangent, concentric, distance, angle, lock, width, gear) with DOF status, Rotate Component,
  interference detection, `.oswasm`.
- **Drawings**: Make Drawing from Part/Assembly — third/first-angle standard views + isometric, Section A-A with
  hatching, Detail view, hole callouts, BOM for assemblies, overall dimensions and a title block (SVG).
- **Familiar UI** (browser): CommandManager, FeatureManager design tree, PropertyManager with live yellow
  preview, heads-up view toolbar, confirmation corner, standard views (Ctrl+1…8, Space), SOLIDWORKS
  mouse navigation, mini context toolbar, mass properties, measure.
- **Agents**: 90 MCP tools (sketching, features, equations, configurations, assemblies, drawings, topology
  queries, mass properties, rendered view images), over stdio or streamable HTTP. Agent actions show up live
  in the UI's Agent activity pane.
- **Interop**: STEP/STL/BREP/Parasolid export, STEP import, native `.oswprt`/`.oswasm` (JSON). Clean-room Parasolid
  reader (`.x_t`, `.x_b`) and writer (`.x_t`, verified by importing into SOLIDWORKS). Opens SOLIDWORKS 2015+
  parts (`.sldprt`) natively, with no SOLIDWORKS installation: the feature history (sketches, extrudes, cuts,
  revolves, fillets, chamfers, shells, patterns, ...) is decoded and rebuilt as editable openswx features when
  the result matches the B-rep stored in the file; otherwise that B-rep is imported as one feature. Opens
  `.sldasm` assemblies natively too: component files, configurations, positions and the common mates.
- **Differential testing** against a licensed SOLIDWORKS through its public COM API.

Geometry kernel: [OpenCASCADE](https://dev.opencascade.org/) via [build123d](https://github.com/gumyr/build123d).
Not affiliated with or endorsed by Dassault Systèmes. SOLIDWORKS is a trademark of Dassault Systèmes.

## Desktop app (Windows .exe)

```bash
.venv\Scripts\pip install pywebview pyinstaller
.venv\Scripts\python packaging\make_icon.py
.venv\Scripts\pyinstaller packaging\openswx.spec --noconfirm     # -> dist\openswx\
dist\openswx\openswx.exe                                          # native window, CAD engine + MCP in-process
dist\openswx\openswx-cli.exe mcp                                  # stdio MCP server for agents (console build)
```

`openswx.exe` opens documents passed on the command line (`openswx.exe part.oswprt`), uses native Open/Save/Export
dialogs, and exposes MCP at `http://127.0.0.1:8765/mcp` while running. The `dist\openswx` folder (~400 MB) is
self-contained and can be zipped and copied to another Windows PC (needs the Edge WebView2 runtime, built into Windows 11).

## Quick start (development, browser UI)

```bash
python -m venv .venv
.venv\Scripts\pip install -e .[dev]        # Windows   (Linux/macOS: .venv/bin/pip)
python -m openswx serve --open             # UI at http://127.0.0.1:8765, MCP at http://127.0.0.1:8765/mcp
```

Your first part (same as SOLIDWORKS): **Extruded Boss/Base** → pick *Top Plane* → draw a rectangle (R) →
**Exit Sketch** (top-right corner) → set *Depth* → **OK**. Click a face → mini toolbar → *Sketch* to sketch
on it. Double-click a dimension while editing a sketch to change it.

## Connect an agent (MCP)

**HTTP** (while `openswx serve` runs):

```bash
claude mcp add --transport http openswx http://127.0.0.1:8765/mcp
```

**stdio** (starts its own app and the live UI on port 8765):

```json
{ "mcpServers": { "openswx": { "command": "python", "args": ["-m", "openswx", "mcp"] } } }
```

Then ask, e.g. *"Make a 120×60×10 mm mounting plate with four Ø8 holes 12 mm from the edges and a
Ø36 boss with a counterbored hole, fillet the corners R6, material 6061."* See
[`tools/agent_demo.py`](tools/agent_demo.py) for a scripted agent that does exactly that.

Tool overview: `new_part`, `create_sketch`, `sketch_line|rectangle|circle|arc|three_point_arc|polygon|slot|point|centerline`,
`sketch_relation`, `sketch_dimension`, `set_dimension`, `extrude`, `cut_extrude`, `revolve`, `cut_revolve`,
`fillet`, `chamfer`, `shell`, `hole`, `linear_pattern`, `circular_pattern`, `mirror`, `reference_plane`,
`edit_feature`, `suppress_feature`, `rollback`, `delete_feature`, `undo`/`redo`, `get_model`, `get_feature`,
`get_topology` (face/edge ids for selection), `get_mass_properties`, `measure`, `get_view_image` (PNG),
`save_part`, `open_part`, `import_file`, `export_file`, `set_material`. End conditions accept names or
SOLIDWORKS `swEndConditions_e` values (Blind=0, ThroughAll=1, MidPlane=6, ThroughAllBoth=9).

## Other commands

```bash
python -m openswx sldinfo part.SLDPRT --extract out/   # inspect a SOLIDWORKS file, dump streams + Parasolid .x_b
python -m pytest                                        # test suite (analytic volume checks)
python tools/difftest/difftest.py --solidworks          # compare against SOLIDWORKS (needs a licence + pywin32)
```

## Design

| Layer | File |
|---|---|
| Constraint solver, sketch entities, profiles | `openswx/sketch.py` |
| Features (rebuild builders, mass properties) | `openswx/features.py` |
| Part document (tree, rebuild, rollback, undo, save) | `openswx/document.py` |
| Persistent face/edge references, tessellation | `openswx/geom.py` |
| Command registry shared by UI, MCP and tests | `openswx/session.py` |
| MCP server | `openswx/mcp_server.py` |
| Web server (REST, SSE live updates, preview) | `openswx/web_server.py` |
| UI | `openswx/web/` (three.js) |
| SOLIDWORKS container reader | `openswx/sldprt.py` |
| SOLIDWORKS feature history (tree, sketches, rebuild) | `openswx/sw_tree.py`, `openswx/sw_sketch.py`, `openswx/sw_rebuild.py` |
| Parasolid XT reader (.x_t/.x_b) / writer (.x_t) | `openswx/parasolid.py`, `openswx/parasolid_write.py` |

Interoperability research notes: [`docs/re/README.md`](docs/re/README.md). Build plan and status:
[`docs/PLAN.md`](docs/PLAN.md). openswx is a clean-room implementation: it contains no SOLIDWORKS code or
artwork; compatibility comes from documented APIs, file-format analysis and observed behaviour.

## Status / limitations

SOLIDWORKS feature history: common part features are rebuilt (see docs/PLAN.md, F1-F5); sweeps, lofts,
sheet metal, weldments and most surface features are not decoded yet, and such parts open as an imported body.
Simulation uses a finite-cell voxel mesh, so stresses at curved
boundaries are approximate. Full list: `docs/PLAN.md` "Next steps".
