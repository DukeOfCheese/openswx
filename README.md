# openswx

<!-- mcp-name: io.github.DukeOfCheese/openswx -->

**Open-source parametric CAD with a familiar layout, and a built-in MCP server so AI agents can design
parts in it while you watch.**

[![tests](https://github.com/DukeOfCheese/openswx/actions/workflows/test.yml/badge.svg)](https://github.com/DukeOfCheese/openswx/actions/workflows/test.yml)
[![licence: Apache-2.0](https://img.shields.io/badge/licence-Apache--2.0-blue.svg)](https://github.com/DukeOfCheese/openswx/blob/main/LICENSE)
![platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![status: alpha](https://img.shields.io/badge/status-alpha-orange)

https://github.com/user-attachments/assets/2c19c47d-aeae-4146-8f85-38fad6df28fa

<sub>30 s ([4K original](https://github.com/DukeOfCheese/openswx/blob/main/docs/media/demo.mp4)). Recorded in
openswx: every feature is built by a real MCP tool call, the same calls Claude makes through Claude Code or any
other MCP client. The chat panel illustrates the conversation and is replayed by a script
([`tools/demo/`](https://github.com/DukeOfCheese/openswx/blob/main/tools/demo)). Claude is made by
[Anthropic](https://www.anthropic.com/).</sub>

openswx is an alternate CAD program for engineers. Its screen is laid out the way many mechanical engineers
already know from SOLIDWORKS: a command bar across the top, a design tree on the left, a property panel for
the active command, the same mouse navigation and shortcuts. If you can model a part there, you can model one
here without retraining. Underneath, it is a different program, built on different foundations; the section
[How it differs from SOLIDWORKS](#how-it-differs-from-solidworks) lists what that means in practice.

> **Not affiliated with Dassault Systèmes.** openswx is an independent open-source project. It is not
> affiliated with, endorsed by, sponsored by or connected to Dassault Systèmes or SOLIDWORKS Corporation.
> SOLIDWORKS is a registered trademark of Dassault Systèmes SolidWorks Corporation; Parasolid is a
> trademark of Siemens Industry Software; Claude is a trademark of Anthropic, which is not affiliated with
> openswx either. The names are used only to describe compatibility and the familiar layout. openswx is a clean-room implementation: it contains no SOLIDWORKS code, icons or artwork, and it
> does not need SOLIDWORKS installed to run or to open SOLIDWORKS files. See [NOTICE](https://github.com/DukeOfCheese/openswx/blob/main/NOTICE).

## Contents

- [What it does](#what-it-does)
- [Install](#install)
- [Your first part](#your-first-part)
- [Agents: how they get access](#agents-how-they-get-access)
- [How it differs from SOLIDWORKS](#how-it-differs-from-solidworks)
- [Status and limitations](#status-and-limitations)
- [Contributing](#contributing)
- [Project layout](#project-layout)
- [Licence](#licence)

## What it does

- **Parts**: constrained 2D and 3D sketches; extrude / revolve / sweep / loft (boss and cut, with the
  familiar end conditions), fillets and chamfers of every kind, shell, draft, rib, dome, wrap, Hole Wizard,
  threads, patterns (linear, circular, curve, sketch, table, fill), mirror, multibody tools, reference
  geometry. Full parametric rebuild, rollback bar, suppress, reorder, folders, undo / redo.
- **Design intent**: equations and global variables (`"D1@Sketch1" = "width"/2`), configurations, design
  tables, materials, custom properties.
- **Sheet metal, surfaces, weldments, mold tools**: flanges, hems, jogs, forming tools and flat patterns;
  the surface toolbox (extrude to boundary, knit, trim, thicken); structural members with cut lists;
  parting lines and tooling split.
- **Assemblies**: linked parts, standard / advanced / mechanical mates with DOF status, patterns, exploded
  views, motion studies, interference and clearance checks, a fastener library, pipe and cable routing.
- **Drawings**: sheets with model, projected, section, detail, auxiliary, broken and cropped views; smart
  dimensions, GD&T, hole tables, BOM and balloons; PDF / DXF / SVG export (DWG through the ODA converter).
- **Analysis**: static (on curvature-based tetrahedra), frequency, buckling, thermal, nonlinear, fatigue and
  drop studies; lattice-Boltzmann flow; plastics fill; 3-axis CAM with G-code; costing; inspection reports.
  Long solves run as background jobs.
- **Rendering**: real-time PBR, or path traced for soft shadows and reflections.
- **Review**: design review, requirements checked like tests, tolerance stack-up, design studies, document
  compare.
- **Agents**: <!-- counts -->433 commands, 413 of them MCP tools ([full list](https://github.com/DukeOfCheese/openswx/blob/main/docs/commands.md))<!-- /counts -->,
  over stdio or streamable HTTP, shown live in the UI's Agent activity pane.
- **Files**: native `.oswprt` / `.oswasm` / `.oswdrw` (versioned JSON). Reads SOLIDWORKS `.sldprt` / `.sldasm` /
  `.slddrw` (2015 and later) natively; clean-room Parasolid reader (`.x_t`, `.x_b`) and writer (`.x_t`); STEP and
  IGES in and out; STL, 3MF, glTF, BREP out; DXF in and out.
- **Safety**: autosave with crash recovery, an application log, a per-user token and origin checks on the
  local server, and an optional folder that agents are confined to.

Geometry kernel: [OpenCASCADE](https://dev.opencascade.org/) through
[build123d](https://github.com/gumyr/build123d) and OCP.

## Install

### Desktop app (Windows, macOS)

Installers are attached to each [release](https://github.com/DukeOfCheese/openswx/releases):

| | Installer | Portable |
|---|---|---|
| Windows 10/11 (x64) | `openswx-setup-<ver>.exe` | `openswx-<ver>-win64.zip` → `openswx.exe` |
| macOS (Apple silicon / Intel) | `openswx-<ver>-macos-<arch>.pkg` → `/Applications/openswx.app` and `openswx-cli` | `openswx-<ver>-macos-<arch>.zip` |

The builds are not code-signed yet. Windows SmartScreen asks for confirmation on first launch (*More info →
Run anyway*). On macOS, right-click `openswx.app` → *Open*, or run
`xattr -dr com.apple.quarantine /Applications/openswx.app`.

### From source (any platform, browser UI)

Python 3.11 or newer:

```bash
git clone https://github.com/DukeOfCheese/openswx.git
cd openswx
python -m venv .venv
.venv/bin/pip install -e .[dev]            # Windows: .venv\Scripts\pip install -e .[dev]
.venv/bin/python -m openswx serve --open   # prints the UI link and opens it; MCP is at /mcp
```

The server answers only this computer and asks for your token, a per-user secret kept in
`~/.openswx/token`. Open the UI through the link `serve` prints, which stores the token as a cookie.

To build the desktop app yourself: `pip install -e .[desktop]`, then `python packaging/build.py --zip`
(builds for the platform it runs on, into `dist/`).

## Your first part

**Extruded Boss/Base** → pick *Top Plane* → draw a rectangle (R) → **Exit Sketch** (top-right corner) → set
*Depth* → **OK**. Click a face → mini toolbar → *Sketch* to sketch on it. Double-click a dimension while
editing a sketch to change it.

## Agents: how they get access

Every action in openswx is a **command**: a typed Python function registered once and exposed in four ways
from the same list. Whatever the UI can do, an agent can do, and what it does appears live in the open
window.

| Route | For | Access control |
|---|---|---|
| MCP over HTTP, `http://127.0.0.1:8765/mcp` | agents that reach this computer over HTTP (Claude Code, Cursor) while the app or `openswx serve` runs | `Authorization: Bearer <token>`; local connections only unless started with `--allow-remote` |
| MCP over stdio, `openswx mcp` | chat apps that start local programs (Claude Desktop); with openswx open it relays to that window | relays with your token; on its own, the UI it starts is token-protected |
| REST, `POST /api/command` | scripts and other programs | same token as MCP over HTTP |
| Python, `openswx.api` | notebooks and scripts in the same process | none (your own process) |

**Claude Code** (HTTP, while openswx runs; the token stays the same between runs, so this is done once):

```bash
claude mcp add --transport http openswx http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $(python -m openswx token)"
```

With the desktop app, use `openswx-cli token` in place of `python -m openswx token`.

**Claude Desktop** (and other chat apps that start local MCP servers): add openswx to its config (Claude
Desktop: *Settings → Developer → Edit Config*) and restart it. While openswx is open, `openswx mcp` relays to
the open window, so you watch the agent's edits live; without it, it starts its own copy and UI.

```json
{ "mcpServers": { "openswx": { "command": "openswx-cli", "args": ["mcp"] } } }
```

`openswx-cli` is the desktop app's command-line program (on Windows give its full path: *Help → Agent (MCP)
Setup* shows the exact entry for your installation); from source use `"command": "python", "args": ["-m",
"openswx", "mcp"]`.

**Claude on the web (claude.ai)** cannot connect: its connectors call MCP servers from Anthropic's servers,
which cannot reach an app running on your computer. Use Claude Desktop or Claude Code.

**Python**:

```python
from openswx.api import Part

part = Part("Bracket")
part.sketch("Top").rectangle([0, 0], [80, 40]).circle([20, 20], 5).extrude(10)
part.fillet(2, part.edges(type="circle"))
print(part.volume, part.mass)
part.save("bracket.oswprt"); part.export("bracket.step")
```

What an agent sees and can do:

- **Toolsets.** A connection starts with the `core` tools (under 50: part-modelling essentials) plus `batch`,
  `list_toolsets` and `enable_toolset`. `enable_toolset("assembly")` adds an area (sketch, features, surfaces,
  sheet_metal, weldments, document, assembly, drawing, simulation, manufacturing, ecosystem, plugins);
  `--toolsets core,drawing`, `--toolsets all` or `OPENSWX_TOOLSETS` sets the starting list.
- **Atomic batches.** `batch` runs several commands in one call: if one fails, every open document goes back
  to how it was; if all succeed, the batch is a single Undo step.
- **Stable references.** `get_topology` gives every face and edge an id and a persistent name that survives
  later edits, so an agent can select geometry reliably.
- **Seeing the model.** `get_view_image` returns a picture; `get_mass_properties` and the `openswx://model`
  and `openswx://feature/{name}` resources return numbers and the feature tree.
- **Tool hints.** Each tool carries MCP annotations (read-only, destructive) so clients can ask before
  saving over or deleting something.
- **Limits you can set.** `--workspace DIR` (or `OPENSWX_WORKSPACE`) confines agents' file reads and writes to
  one folder. `--share` prints a separate view-only link for people watching. The rules are in
  [`openswx/security.py`](https://github.com/DukeOfCheese/openswx/blob/main/openswx/security.py).
- **Your own tools.** A Python package can add commands through the `openswx.plugins` entry point; they become
  MCP tools in the `plugins` toolset ([`openswx/plugins.py`](https://github.com/DukeOfCheese/openswx/blob/main/openswx/plugins.py)).

Try: *"Make a 120×60×10 mm mounting plate with four Ø8 holes 12 mm from the edges and a Ø36 boss with a
counterbored hole, fillet the corners R6, material 6061."* [`tools/agent_demo.py`](https://github.com/DukeOfCheese/openswx/blob/main/tools/agent_demo.py) is a
scripted agent that does exactly that. Every command and its arguments: [`docs/commands.md`](https://github.com/DukeOfCheese/openswx/blob/main/docs/commands.md).

## How it differs from SOLIDWORKS

openswx is alternate CAD software, not a copy. It borrows the layout because engineers already know it;
everything else is its own. If you are coming from SOLIDWORKS, these are the differences that matter.

**Different by design**

| | SOLIDWORKS | openswx |
|---|---|---|
| Licence and cost | commercial, per seat | open source (Apache-2.0), free |
| Platforms | Windows | Windows, macOS, Linux; desktop app or any browser |
| Geometry kernel | Parasolid | OpenCASCADE. Fillets, shells and boolean edge cases can resolve differently, and very small or tangent features may fail where Parasolid succeeds (or the other way round) |
| Native files | binary `.sldprt` / `.sldasm` / `.slddrw` | versioned JSON `.oswprt` / `.oswasm` / `.oswdrw` that hold the commands that built the model. Diffable in Git; `openswx script` prints a part as a script and `openswx diff` compares two |
| Automation | COM API, VBA / VSTA macros, add-ins | every command is an MCP tool, a REST call and a Python call; macros record commands; plugins are ordinary Python packages |
| AI agents | none built in | MCP server built in, with toolsets, atomic batches and persistent face and edge names |
| Headless use | needs the full application | `openswx convert`, `render`, `massprops`, `check`, `drawing`, `script` and `diff` run without a window, for example in CI |
| Requirements | not part of the model | requirements live in the document and are checked like tests (`openswx check` exits non-zero on failure) |
| Collaboration | PDM server | today file-based: a local vault with check-in / check-out, and a live view-only share link. Live multi-user editing is in development (see below) |
| Rendering | PhotoView 360 / Visualize | in the browser: real-time PBR, or path traced on the GPU |

**Compatibility with SOLIDWORKS files**

- `.sldprt`, `.sldasm` and `.slddrw` (2015 and later) open without SOLIDWORKS installed. openswx reads the stored
  body and, where it can decode it, the feature history, and rebuilds that history to check it against the stored
  body. In the survey of 2026-11-23, 584 of the 841 sample parts that ship with SOLIDWORKS 2025 rebuilt exactly
  (352 fully parametric). Parts whose features are not decoded yet (many sweeps, lofts, sheet-metal and
  surface features) open as an imported solid that you can still measure, mate, draw and add features to.
- SOLIDWORKS files are **read-only**. Save as openswx files, or exchange through STEP, Parasolid `.x_t` or IGES.
- Equations, end conditions (by name or by `swEndConditions_e` number) and dimension names (`D1@Sketch1`) use
  the same syntax, so existing design intent carries over.

**Not the same yet (see [Status](#status-and-limitations))**

- Simulation: every structural and thermal study (static, frequency, buckling, thermal, nonlinear, drop,
  harmonic and step response; bonded assemblies, contact, springs and bolts) meshes with curvature-based
  quadratic tetrahedra when the optional mesher is installed (`pip install openswx[mesh]`; included in the
  Windows and macOS installers): peak stresses at holes and fillets within a few percent of
  closed-form results. Without it the finite-cell voxel mesh is used, whose stresses at curved boundaries are
  approximate. Flow is lattice-Boltzmann with a large-eddy turbulence model, without wall functions.
- CAM is 3-axis: roughing, flat floors and drilling, and 3D finishing with ball, bull-nose or flat mills; no
  4- or 5-axis.
- DWG needs the free ODA File Converter installed. Models are shared as a self-contained HTML viewer
  (eDrawings-style, opens in any browser) rather than in a separate viewer application.
- Toolbox and the Design Library are openswx's own, smaller sets (ISO metric and ANSI inch fasteners, pins
  and washers). The material library has about 100 common grades with typical handbook values; add your own
  as custom materials.
- Icons are original, and some commands sit in slightly different places.
- Live collaboration is not in openswx yet: it is being built in a separate repository,
  [openswx-collab](https://github.com/DukeOfCheese/openswx-collab) (Apache-2.0, no release yet): several people
  editing the same part at once with live cursors and feature locks, full edit
  history with named versions and restore, assemblies that reference parts pinned to a version, and a server
  you can host yourself (SQLite or PostgreSQL). The server never runs a geometry kernel: each person's openswx
  applies the edits locally and sends the change. The server and its protocol are built and tested; the part
  inside openswx (sharing a file, the sync client, cursors) has not started, and will be an optional module
  that is off by default.

## Status and limitations

openswx is **alpha**: usable for real parts, with rough edges. Save often (autosave keeps a recovery copy;
`OPENSWX_AUTOSAVE` sets the interval in seconds, or `off`).

- **SOLIDWORKS feature history**: see the numbers above; `.sldprt` is read-only.
- **Simulation and CAM**: see the list above.
- **Builds** are not code-signed yet.
- CI runs the test suite on Linux, Windows and macOS, and UI smoke tests in a headless browser.

Where things stand on one screen: [`docs/STATUS.md`](https://github.com/DukeOfCheese/openswx/blob/main/docs/STATUS.md). What is missing, with a spec for each
item: [`docs/GAPS.md`](https://github.com/DukeOfCheese/openswx/blob/main/docs/GAPS.md). Release notes: [`CHANGELOG.md`](https://github.com/DukeOfCheese/openswx/blob/main/CHANGELOG.md).

## Contributing

Contributions from people and from coding agents are both welcome. Read
[`CONTRIBUTING.md`](https://github.com/DukeOfCheese/openswx/blob/main/CONTRIBUTING.md) first; above all, the **clean-room rule**: no SOLIDWORKS code, artwork or
sample files, ever. Bugs, SOLIDWORKS files that don't open correctly, and feature requests each have an issue
form that asks for exactly what is needed to act on them. Coding agents should also read
[`AGENTS.md`](https://github.com/DukeOfCheese/openswx/blob/main/AGENTS.md). Security problems: [`SECURITY.md`](https://github.com/DukeOfCheese/openswx/blob/main/SECURITY.md), never a public issue.

## Project layout

| | |
|---|---|
| `openswx/session.py` | the application object, the `@command` registry and dispatcher |
| `openswx/commands_*.py` | the commands, by area |
| `openswx/document.py`, `assembly.py` | part and assembly models: rebuild, undo, save / load |
| `openswx/sketch.py`, `features.py`, `geom.py`, `naming.py` | constraint solver, feature builders, references, persistent names |
| `openswx/drawing*.py` | drawings |
| `openswx/fea*.py`, `cfd.py`, `plastics.py`, `cam.py` | solvers |
| `openswx/sldprt.py`, `sw_*.py`, `parasolid*.py` | SOLIDWORKS and Parasolid readers / writer (clean-room; notes in [`docs/re/`](https://github.com/DukeOfCheese/openswx/blob/main/docs/re/README.md)) |
| `openswx/web_server.py`, `mcp_server.py`, `security.py` | REST + live-update server, MCP server, access rules |
| `openswx/web/` | the UI (three.js) |
| `tools/` | command reference generator, conformance cases, benchmarks, SOLIDWORKS survey |
| `tests/` | the test suite (`python -m pytest`) |

## Licence

Apache License 2.0: see [`LICENSE`](https://github.com/DukeOfCheese/openswx/blob/main/LICENSE). Third-party components and their licences: [`NOTICE`](https://github.com/DukeOfCheese/openswx/blob/main/NOTICE).
