# openswx: where things stand

One screen. The detail is in three other places: every command in [`commands.md`](commands.md) (generated),
what is missing with a spec for each item in [`GAPS.md`](GAPS.md) (its table at the top is the current
state), and how the program got here, pass by pass, in [`PLAN.md`](PLAN.md) (a log: its "Architecture" and
"Next steps" sections describe earlier stages, not the present).

## What runs

- **Modelling**: parts (sketches, features, surfaces, sheet metal, weldments, mold tools), assemblies
  (mates, patterns, configurations, display states, routing), drawings (views, dimensions with tolerances,
  tables, PDF / DXF / SVG).
- **Analysis**: static, frequency, buckling, thermal, nonlinear, fatigue, drop; lattice-Boltzmann flow;
  plastics fill; 3-axis CAM. Long solves run as background jobs.
- **Rendering**: real-time PBR, or path traced (Render Tools > Quality) for soft shadows and reflections.
- **Review**: design review, requirements checked like tests, tolerance stack-up, design studies, document
  compare, 3D-print orientation.
- **Files**: native `.oswprt` / `.oswasm` / `.oswdrw` (versioned JSON, with the commands that built the
  document); reads SOLIDWORKS `.sldprt` / `.sldasm` / `.slddrw`, Parasolid, STEP, IGES, STL, 3MF, DXF;
  writes Parasolid, STEP, IGES, STL, 3MF, glTF, DXF, PDF.
- **Agents**: MCP over stdio or HTTP, in toolsets; atomic `batch`; persistent face and edge names.
- **Command line**: `openswx convert | render | massprops | check | drawing | script | diff`.

## Layout of the code

| | |
|---|---|
| `session.py` | the application object, the `@command` registry and dispatcher, shared helpers |
| `commands_*.py` | the commands, by area (document, sketch, features, assembly, drawing, simulation, review, ...) |
| `document.py`, `assembly.py` | the part and assembly models: rebuild, undo, save / load |
| `sketch.py`, `features.py`, `geom.py`, `naming.py` | constraint solver, feature builders, references, persistent names |
| `drawing*.py`, `inspection.py`, `tolerance.py` | drawings, inspection reports, tolerances and ISO 286 fits |
| `fea*.py`, `cfd.py`, `plastics.py`, `cam.py` | the solvers |
| `sldprt.py`, `sw_*.py`, `parasolid*.py` | SOLIDWORKS and Parasolid readers / writer (clean-room; see `re/README.md`) |
| `web_server.py`, `mcp_server.py`, `security.py`, `desktop.py`, `web/` | REST + SSE server, MCP server, access rules, desktop shell, the UI |
| `recovery.py`, `migrate.py`, `snapshots.py`, `jobs.py`, `isolated.py`, `applog.py`, `settings.py`, `plugins.py` | autosave, file versions, undo storage, background jobs, isolated file reading, log, settings, plugins |
| `tools/` | command reference generator, conformance cases, benchmarks, SOLIDWORKS survey, differential tests |

## Known limits

- SOLIDWORKS feature history: 526 of the 841 sample parts rebuilt exactly (351 fully parametric) in the survey
  of 2026-11-23 (`docs/survey/2026-11-23-samples-841.json`; 2026-10-29: 525 / 363). The rest open as an
  imported body. `.sldprt` is read-only.
- Simulation: static, frequency, buckling and steady thermal studies (bonded assemblies too) use
  curvature-based quadratic tetrahedra when the optional Netgen mesher is installed (`openswx[mesh]`; bundled in
  the Windows build, not yet in the macOS one). Contact, connectors between separate bodies, transient thermal, nonlinear and drop
  studies use the voxel mesh, whose stresses at curved boundaries are approximate.
- The desktop window has not been exercised with the changes of the last pass (the browser UI has, in CI).
- CI (`.github/workflows/test.yml`) runs the suite on Linux, Windows and macOS and the UI smoke tests in a headless browser.

## Working on it

`python -m pytest` (about five minutes), `python tools/gen_command_docs.py` after adding or changing a
command, `python tools/conformance.py run`, `python tools/bench.py`. Rules for contributions, including the
clean-room rule, are in [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## Parts in Git

A part's history is text: `openswx script FILE` prints the commands that built it. To see readable diffs of
`.oswprt` files in `git diff` and `git log -p`:

```
# .gitattributes
*.oswprt diff=openswx
*.oswasm diff=openswx
```

```
git config diff.openswx.textconv "openswx script"
```

`openswx diff A B` gives the feature-level comparison (added, removed and changed features, and the change
in volume and mass).
