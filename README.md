# openswx

Open-source parametric part modeling with a SOLIDWORKS-style workflow — and a built-in
**MCP server** so AI agents can design parts in it while you watch.

- **Parts**: constrained 2D and 3D sketches; extrude / revolve / sweep / loft (boss and cut, with the SOLIDWORKS
  end conditions), fillets and chamfers of every kind, shell, draft, rib, dome, wrap, Hole Wizard, threads,
  patterns (linear, circular, curve, sketch, table, fill), mirror, multibody tools, reference geometry.
  Full parametric rebuild, rollback bar, suppress, reorder, folders, undo / redo.
- **Design intent**: equations and global variables in SOLIDWORKS syntax (`"D1@Sketch1" = "width"/2`),
  configurations, design tables, materials, custom properties.
- **Sheet metal, surfaces, weldments, mold tools**: flanges, hems, jogs, forming tools and flat patterns;
  the surface toolbox (extrude to boundary, knit, trim, thicken); structural members with cut lists;
  parting lines and tooling split.
- **Assemblies**: linked parts, standard / advanced / mechanical mates with DOF status, patterns, exploded
  views, motion studies, interference and clearance checks, Toolbox fasteners, pipe and cable routing.
- **Drawings**: sheets with model, projected, section, detail, auxiliary, broken and cropped views; smart
  dimensions, GD&T, hole tables, BOM and balloons; PDF / DXF / SVG export (DWG through the ODA converter).
- **Analysis**: static, frequency, buckling, thermal, nonlinear, fatigue and drop studies (finite-cell voxel
  mesh); lattice-Boltzmann flow; plastics fill; 3-axis CAM with G-code; costing; inspection reports.
- **Familiar UI** (desktop app or browser): CommandManager, FeatureManager design tree, PropertyManager with
  live preview, SOLIDWORKS mouse navigation and shortcuts.
- **Agents**: <!-- counts -->423 commands, 405 of them MCP tools ([full list](docs/commands.md))<!-- /counts -->, over stdio or
  streamable HTTP. Agent actions show up live in the UI's Agent activity pane.
- **Interop**: opens SOLIDWORKS parts, assemblies and drawings (`.sldprt`, `.sldasm`, `.slddrw`; 2015 and
  later) natively, with no SOLIDWORKS installation - see *Status* for how much of the feature history comes
  across. Clean-room Parasolid reader (`.x_t`, `.x_b`) and writer (`.x_t`). STEP and IGES in and out; STL, 3MF,
  glTF, BREP out; DXF in and out. Native `.oswprt` / `.oswasm` / `.oswdrw` (JSON, versioned).
- **Safety**: autosave with document recovery after a crash, an application log, a token and origin check on
  the local server, and an optional folder agents are confined to (`--workspace`).

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
python -m openswx serve --open             # prints the UI link (http://127.0.0.1:8765/?token=...); MCP at /mcp
```

The server only answers this computer and asks for your token (a per-user secret kept in `~/.openswx/token`):
open the UI through the link `serve` prints, which stores it as a cookie. `--no-token` turns the token off for
local development; `--host` with an address other computers can reach needs `--allow-remote`. `--share` also prints a
view-only link: whoever opens it sees the model update live but cannot change it or save files. The rules are in
[`openswx/security.py`](openswx/security.py).

Your first part (same as SOLIDWORKS): **Extruded Boss/Base** → pick *Top Plane* → draw a rectangle (R) →
**Exit Sketch** (top-right corner) → set *Depth* → **OK**. Click a face → mini toolbar → *Sketch* to sketch
on it. Double-click a dimension while editing a sketch to change it.

## Connect an agent (MCP)

**HTTP** (while `openswx serve` runs):

```bash
claude mcp add --transport http openswx http://127.0.0.1:8765/mcp --header "Authorization: Bearer $(python -m openswx token)"
```

(The token stays the same between runs, so this is done once. With the desktop app: `openswx-cli token`.)

**stdio** (starts its own app and the live UI on port 8765):

```json
{ "mcpServers": { "openswx": { "command": "python", "args": ["-m", "openswx", "mcp"] } } }
```

Then ask, e.g. *"Make a 120×60×10 mm mounting plate with four Ø8 holes 12 mm from the edges and a
Ø36 boss with a counterbored hole, fillet the corners R6, material 6061."* See
[`tools/agent_demo.py`](tools/agent_demo.py) for a scripted agent that does exactly that.

A connection lists the `core` tools first (under 50: part modelling essentials, plus `batch` for several
commands in one atomic call). `list_toolsets` shows the other areas and `enable_toolset("assembly")` adds one;
`--toolsets core,drawing` or `--toolsets all` (or `OPENSWX_TOOLSETS`) sets what is listed from the start.
`get_topology` gives each face and edge an id and a persistent `name` that survives later edits.
The full list with arguments is in [`docs/commands.md`](docs/commands.md).
Start with `new_part`, `create_sketch`, `sketch_rectangle` / `sketch_circle` / `sketch_line`, `sketch_dimension`,
`extrude` / `cut_extrude`, `fillet`, `get_topology` (face and edge ids for selection), `get_mass_properties` and
`get_view_image`. End conditions accept names or SOLIDWORKS `swEndConditions_e` values (Blind=0, ThroughAll=1,
MidPlane=6, ThroughAllBoth=9).

To keep an agent's file access to one folder, start openswx with `--workspace DIR` (or set
`OPENSWX_WORKSPACE`): commands from agents may then only read and write files there.

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

Licence: Apache-2.0 ([`LICENSE`](LICENSE), third-party notices in [`NOTICE`](NOTICE)). Contributing, including the
clean-room rule: [`CONTRIBUTING.md`](CONTRIBUTING.md). Known gaps and their specs: [`docs/GAPS.md`](docs/GAPS.md).

## Status / limitations

- **SOLIDWORKS feature history**: of the 841 sample parts that ship with SOLIDWORKS 2025, 525 rebuild to exactly
  the stored body and 363 of those are fully parametric (survey of 2026-10-29). Sweeps, lofts, sheet metal and
  most surface features are not decoded yet; such parts open as an imported body. `.sldprt` is read-only:
  export goes through Parasolid, STEP or IGES.
- **Simulation** uses a finite-cell voxel mesh, so stresses at curved boundaries are approximate. Flow results
  at high Reynolds numbers carry a warning. CAM is 3-axis with flat end mills.
- **Autosave** keeps the last copy of each unsaved document (`~/.openswx/recovery`); set `OPENSWX_AUTOSAVE`
  to a number of seconds or `off`. The log is in `~/.openswx/logs` (Help > Save Diagnostic Report).

Where things stand, on one screen: [`docs/STATUS.md`](docs/STATUS.md). What is missing, with a spec for each item:
[`docs/GAPS.md`](docs/GAPS.md). History of the build:
[`docs/PLAN.md`](docs/PLAN.md).
