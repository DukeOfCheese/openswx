# openswx: missing pieces, specs and feature suggestions

Audit of the repository as of 2026-11-22 (branch `master`, HEAD `c7f58f3`, plus uncommitted macOS packaging
work). Method: read `README.md`, `docs/PLAN.md`, the command registry (`session.COMMANDS`), the servers, the
packaging and CI files, and cross-checked every command name against the web UI sources and the tests.
Nothing was run except the registry import; the test suite and the SOLIDWORKS survey were not re-run.

Snapshot

| | |
|---|---|
| Python source | ~32,900 lines; `session.py` alone 4,713 |
| Commands / MCP tools | 381 / 366 |
| Tests | 162 test functions, 158 of them in one 4,128-line file |
| Commands never named in a test | 47 |
| Commands not reachable from the web UI | 15 (11 are agent / query only by design) |
| SOLIDWORKS sample parts rebuilt exactly | 584 of 841 (352 fully parametric), survey of 2026-11-23 |
| SOLIDWORKS sample mates imported | 125 of 155 on 40 small assemblies |

## Status after the first pass (2026-11-23)

The specs below are as written on 2026-11-22; this table says what has been done about each since
(commits `df95ff6` onwards, 308 tests). "Done" means implemented with tests that pass on Windows;
since 2026-11-23 the suite also runs green in CI on Linux and macOS; the desktop window has not been launched.

| Item | State | What was done / what is left |
|---|---|---|
| A1 licence | done | `LICENSE`, `NOTICE`, `CONTRIBUTING.md`; in the wheel, the Windows installer, the Linux tarball and the macOS app bundle (not yet seen in a built macOS package); About box |
| A2 remote, branch, commits | done | Private GitHub repository `DukeOfCheese/openswx`, branch `main` (to be made public later; the commit author address is a noreply one and no tracked file holds a user name or path). The macOS packaging work is committed, and the release workflow `build.yml` ran green on 2026-11-23: Windows installer and zip, macOS `.pkg` and zip for Apple silicon and Intel (the macOS jobs install the `.pkg` and run `openswx-cli`). Builds are unsigned (I2) |
| A3 CI on push | done | `.github/workflows/test.yml` runs green on Linux (Python 3.11 and 3.13), Windows and macOS. The first run found one platform-dependent failure (a requirement exactly at its limit), fixed |
| A4 docs | done | `docs/commands.md` generated and checked by a test; README rewritten around it; `docs/STATUS.md` is the one-screen current state and `PLAN.md` is marked as the log |
| B1 server guard | done | `security.py`: token, Host / Origin / Sec-Fetch-Site checks, JSON-only POST, `--allow-remote`. The token is per user and persistent (the spec said per launch) so agent configurations survive restarts. Macros: agents cannot run them (not an MCP tool) and `macro_stop` obeys the workspace |
| B2 agent workspace | done | `--workspace` / `OPENSWX_WORKSPACE`. **Left:** an `overwrite` flag on file commands |
| B3 parsers isolated | mostly | `.sldprt`, Parasolid and IGES geometry is read in a child process with a time limit (`isolated.py`); a fuzz test opens damaged copies of every format openswx writes (it found the IGES reader crashing, which is why IGES is isolated). **Left:** STEP / DXF / mesh and the parts of an `.sldasm` still read in-process |
| C1 autosave, recovery | done | `recovery.py`, recovery dialog, close guards. The desktop close prompt is untested (the window was not launched). One recovery copy per document, not five |
| C2 file versions | done | `migrate.py`, golden files, unknown keys kept. **Left:** a published JSON Schema |
| C3 undo memory | done | `snapshots.py`: long strings stored once. Not a structural diff: the small per-step JSON is still a full copy |
| C4 jobs, lock | partly | `jobs.py`, `start_job` / `job_status` / `cancel_job`; the static study, flow, plastics and CAM release the lock while computing; UI progress and Cancel. Every study releases the lock while it solves. **Left:** commands still act on the one active document; only the flow solver reports progress |
| C5 logging | done | `applog.py`, `save_diagnostic_report` |
| D1 untested commands | done | Every command is named in a test; a test enforces it for new ones. Found and fixed on the way: component numbers reused after a delete, PDM where-used missing linked parts, property commands not marking the document changed |
| D2 test file split | done | Nine files by area + `core_helpers.py` |
| D3 UI tests | done | `tests/ui/test_ui_smoke.py` (Playwright) passes in the `ui` CI job: token link, a part, every dialog added in this pass, a study as a job, assembly dialogs, no script errors. Also driven by hand in a browser, including Document Recovery after a simulated crash. **Left:** the desktop window, screenshot comparison |
| D4 conformance | done | `tests/conformance/*.json` (7 cases), `tools/conformance.py record / run` |
| D5 static checks, deps | partly | ruff (errors only) in CI and `pyproject.toml`; `ezdxf` declared. **Left:** a lock file, type checking, the `sklearn` stub |
| D6 survey in the repo | partly | `tools/survey.py` run on the installed SOLIDWORKS samples: the 300 smallest files (277 distinct parts) give 243 exact, 224 fully parametric; table in `docs/survey/2026-11-23-smallest-300.json`. The rest of the corpus (the slow, large parts) is still running at the time of writing |
| E1 split `session.py` | done | 273 commands moved into `commands_document`, `_sketch`, `_features`, `_assembly`, `_drawing`, `_simulation` (AST-driven, no behaviour change); `session.py` keeps the dispatcher and helpers (1,270 lines). `commands_more.py` and `commands_mfg.py` were left as they are |
| E2 MCP toolsets | done | Core set under 50 tools, `list_toolsets` / `enable_toolset`, annotations, `batch`, persistent names in `get_topology`, resources. **Left:** machine-readable error codes, output schemas, resource-changed notifications |
| E3 settings, i18n, a11y | partly | `settings.py` + Tools > User Settings (units, autosave, toolsets, close prompt, tolerance class, picture size, view colours, mouse preset and wheel direction); Tools > Customize Keyboard (31 actions, `web/keys.js`, saved as `shortcuts`); keyboard navigation of the FeatureManager tree (Alt+T) and the menu bar (F10); the Task Pane lists the keys in use. **Left:** translation, keyboard navigation of the CommandManager and the PropertyManager beyond Tab, screen-reader labels |
| F SOLIDWORKS interop | partly | Custom properties from `.sldprt`; `import_sw_configurations`: every configuration with stored geometry becomes an openswx configuration (checked on sample parts: volumes match, the saved configuration keeps its editable history). The owner has allowed the development oracle and survey runs (2026-11-23). **Left:** feature-history coverage by feature class (sweep and loft as base features first), remaining mate types, large-assembly loading, `.slddrw` dimension positions - each needs SOLIDWORKS-made test files |
| F neutral formats | partly | `.stl` / `.3mf` import as faceted solids. **Left:** STEP AP242 with colours and structure, OBJ, binary Parasolid writer |
| G sketch | partly | Equation-driven curves; sketch blocks (make / insert / list / delete); dimension tolerances and fits. **Left:** sketch picture, style / fit splines, sketch dimension chains, spline relations |
| G part | partly | Derived configurations (a child follows its parent), display states (saved appearances), requirements in place of sensors, sheet-metal gauge tables. **Left:** Instant3D beyond the depth handle, freeze bar, and the sheet-metal (lofted bend, unfold / fold, tab and slot, convert to sheet metal, vents), weldment, surface and mesh items listed |
| G assembly | partly | Assembly configurations (component suppression and mate values per configuration) and display states, with dialogs. **Left:** flexible subassemblies, lightweight components, belt / chain, collision and physics while dragging, physics-based motion, mate controller, layout sketches, exploded line sketch |
| G drawings | partly | Ordinate / baseline / chain dimensions; tolerance display. **Left:** the rest of the list |
| H1 C++ port decision | decided | Parked (2026-11-23): the owner's reason for the port was macOS support, which the Python application already has through `packaging/build.py`. Recorded in `PLAN.md`. Hot loops go to compiled extensions case by case if the benchmarks call for it |
| H2 simulation | partly | Result probes, an HTML study report and a mesh-convergence study (`simulation_probe`, `simulation_report`, `simulation_convergence`). Curvature-based quadratic tetrahedra (Netgen, optional `mesh` extra, `fea_tet.py`) for static, frequency, buckling and thermal (steady and transient) studies and bonded assemblies, each checked against a closed form. Contact, springs and bolts too (bodies meshed apart; a spring ties each node to the point straight across on the other face). Nonlinear, drop, harmonic and step-response studies too. **Left:**  mesh controls, remote and bearing loads, shells and beams |
| H3 sketch solver | partly | No search when every relation already holds; grouped-column Jacobian from 12 unknowns; one SVD: 2.9 s -> 1.3 s on the benchmark. Checked against the previous solver (`OPENSWX_SKETCH_SOLVER=legacy`) on 277 SOLIDWORKS sample parts: identical outcomes, none gained or lost. **Left:** decomposition into rigid clusters, minimal conflicting sets |
| H4 CAM | partly | 3D parallel finishing with ball, bull-nose and flat tools on a drop-cutter surface (`cam_finish_3d`). **Left:** rest machining, adaptive roughing, 2D operations from picked faces, tabs, lead-in / out, setup sheets, turning |
| H5 flow, plastics | partly | `plastics_gate_advisor`: ranks candidate gate positions by injection pressure, weld lines and air traps. **Left:** non-isothermal fill with a frozen layer, packing; flow: wall refinement, rotating regions, conjugate heat transfer |
| H6 rendering | partly | Render Tools has a path-traced quality (vendored three-gpu-pathtracer 0.0.23 + three-mesh-bvh 0.8.3, MIT): soft shadows, reflections and indirect light from the same scene as the fast renderer. Checked in a browser: the first render of a session compiles the tracer's shader (about 15 s here), later ones take seconds; one renderer is kept for the session. A UI test runs it in CI (skipped if a software GPU cannot compile it in 4 minutes). **Left:** HDRI import, an appearance library with textures, turntables |
| I1 Linux build | mostly | `packaging/build_linux.sh` and the manual `build-linux` workflow run on Ubuntu 22.04: the tarball builds and its packed command-line program opens a part and exports STEP. **Left:** the window itself has not been opened on Linux; not an AppImage; not yet in the release workflow |
| I2 code signing | not planned | Decided 2026-11-23: the certificates cost more than the project wants to spend. Builds stay unsigned; the README tells users how to get past Gatekeeper and SmartScreen. The signing hooks in `packaging/build.py` (`OPENSWX_CODESIGN_IDENTITY`, `OPENSWX_INSTALLER_IDENTITY`) stay for anyone who has a certificate |
| I3 update check | done | Help > Check for Updates (`updates.py`): asks the GitHub releases of the repository in the `update_repository` setting, only when the user asks; nothing is downloaded. **Left:** set the repository once there is one |
| I4 benchmarks | partly | `tools/bench.py`. **Left:** tracking per release, lazy imports, bundle audit |
| I5 PyPI | done | Published: openswx 0.2.0 on PyPI by trusted publishing from `publish.yml` on each version tag; listed in the MCP Registry as `io.github.DukeOfCheese/openswx` (`server.json`, published with `mcp-publisher`) |
| I6 stray build folders | done | `build/`, `build-new/`, the two build logs and the stack dump deleted on the owner's say. Builds now go to one folder, `dist/` (`packaging/build.py`, which stops with a message if openswx is running from it); the old `dist/` and `dist-new/` builds were deleted 2026-11-23 |
| J1 review loop | done | `review_model` |
| J2 design as text | done | `export_script` (documents record their commands); run it as a macro or with `python` |
| J3 semantic diff | partly | `compare_documents`, `openswx diff`, and a Git textconv driver (`openswx script`, see `STATUS.md`). **Left:** coloured 3D overlay, merge |
| J4 requirements as tests | done | `add_requirement` / `check_requirements`, `openswx check`. **Left:** factor-of-safety requirements tied to a study |
| J5 design studies | partly | `design_study` (sweeps, best feasible run). **Left:** optimisers, Pareto plots, topology optimisation |
| J6 headless CLI | done | `convert`, `render`, `massprops`, `check`, `drawing`, `script`, `diff` |
| J7 library API | partly | `openswx.api.Part` / `Sketch`: a small chained object API over the commands, with a PNG representation for notebooks. **Left:** assemblies and drawings, full typing, an interactive viewer widget |
| J8 plugins | done | `openswx.plugins` entry points. CAM posts, pipe standards and Toolbox tables were not moved out of the core |
| J15 3D-print workflow | partly | `print_orientation` (overhang, support estimate, bed fit for the six ways up); STL / 3MF in and out. **Left:** lattice infill, per-body print settings |
| J16 standard parts | partly | `standard_part`: ball bearings (ISO 15), parallel keys (DIN 6885), O-rings, generic T-slot extrusions, with BOM properties. **Left:** retaining rings, gears and pulleys, real extrusion profiles as weldment sections |
| J9 bridges | partly | FreeCAD `.FCStd`: the visible bodies come in as imported geometry (`fcstd.py`; tested with archives in FreeCAD's documented layout, not with files saved by FreeCAD). **Left:** FreeCAD history, Onshape, Fusion |
| J12 sharing | partly | `serve --share` prints a view-only link (its own token): the page, the model, live updates and queries, nothing that changes the model or writes a file. The UI is not adapted for viewers (editing attempts show the refusal); no comments pinned to faces |
| J17 onboarding | partly | Help > Tutorials: three step lists ticked off from the model. No highlighting of the next button, no sample parts |
| J10 sketch from image | partly | `sketch_from_image`: the dark shapes of a picture become closed outlines of lines at a given scale (threshold, tolerance, speck removal). **Left:** fitting arcs and circles instead of line chains, inferred relations |
| J11 command palette | exists | The search box already covers commands; "find what references this" not added |
| J13 hand-off pack | done | `export_handoff_pack` |
| J14 tolerance stack-up | done | `tolerance_stack` along a chain of model dimensions (not across assembly mates) |

Each item below has: what is missing, the evidence, a spec, and an acceptance check. Priorities: **P0** blocks
a public release, **P1** is a correctness or data-loss risk, **P2** is parity, **P3** is polish.

---

## A. Release blockers (P0)

### A1. No licence file
- Evidence: `pyproject.toml` says `Apache-2.0`; there is no `LICENSE`, `NOTICE`, `CONTRIBUTING`, `SECURITY`
  or `CHANGELOG` in the tree. Only `openswx/web/vendor/three.LICENSE` exists.
- Spec: add `LICENSE` (Apache-2.0 text) and `NOTICE` listing bundled / linked components with their licences
  (three.js MIT, OpenCASCADE LGPL-2.1 with exception, build123d Apache-2.0, ezdxf MIT, pywebview BSD, and
  whatever the PyInstaller bundle pulls in). Add `license-files` to `pyproject.toml`. The installer and
  the macOS `.pkg` must ship both files, and Help > About must show them.
- Add `CONTRIBUTING.md` with the clean-room rule that today lives only in `docs/PLAN.md`: no decompiled or
  copied SOLIDWORKS code or artwork, no Dassault sample files, no `.SLDPRT` with a user name in `docProps`.
- Acceptance: `pip wheel .` output contains the licence; the built installer contains `LICENSE` and `NOTICE`.

### A2. No remote, wrong default branch, uncommitted work
- Evidence: `git remote -v` is empty; the branch is `master` while the build workflow and the docs assume a
  GitHub repository; `.github/` and `packaging/openswx.icns` are untracked; seven files are modified
  (the macOS packaging pass). Six commits in total, each a very large pass.
- Spec: commit the macOS packaging work, rename the branch to `main`, create the GitHub repository, push.
  From then on one topic per commit / PR so a regression in the 841-part survey can be bisected.
- Acceptance: the `build` workflow runs green on all three runners from a clean clone.

### A3. CI only runs on a tag or by hand
- Evidence: `.github/workflows/build.yml` triggers on `workflow_dispatch` and `v*` tags only. Tests run as a
  step of the release build, on Windows and macOS only.
- Spec: add `.github/workflows/test.yml` on `push` and `pull_request`: matrix `ubuntu-latest`,
  `windows-latest`, `macos-15` x Python 3.11 and 3.13 (the declared floor and the build version);
  `pip install -e .[dev]`, `pytest -q -x --durations=20`; pip cache. Add `ruff check` (start with `E9,F`
  only, so it catches real errors without a reformat). Mark slow tests `@pytest.mark.slow` and run them in a
  second job so the fast job stays under about five minutes.
- Acceptance: a PR with a syntax error in any module or web file fails within minutes.

### A4. README and plan describe an older program
- Evidence: README says "90 MCP tools" (366 now), lists a tool overview from phase 1, and its feature bullets
  stop before simulation, CAM, PDM, drawings as documents. `docs/PLAN.md` "Architecture" names files that
  do not exist (`sketch/solver.py`, `model/refs.py`, `app/commands.py`, `io/exchange.py`); the plan is 747
  lines of chronological log in which the current state has to be dug out.
- Spec: README = what it is, install, first part, MCP connection, a generated capability table, honest
  limits. Generate the command reference (`docs/commands.md`) from `session.COMMANDS` with a script in
  `tools/`, and check in CI that it is current. Split the plan: `docs/STATUS.md` (current state, one
  screen), `docs/ROADMAP.md` (open items, this file can seed it), `docs/HISTORY.md` (the log, unchanged).
- Acceptance: no number in README is typed by hand; CI fails when `docs/commands.md` is stale.

---

## B. Security (P0 for anything other people install)

### B1. The local HTTP API has no authentication and no origin check
- Evidence (`openswx/web_server.py`): `POST /api/command` runs any registered command, including ones marked
  `mcp=False`, and parses the body with `req.json()` whatever the `Content-Type`. There is no token, no
  `Origin` / `Host` check and no CORS policy. A page open in the user's browser can therefore send a
  cross-site "simple" POST to `http://127.0.0.1:8765/api/command` and drive the app: `save_part`,
  `export_file`, `pack_and_go`, `macro_stop(path=...)` write to paths the caller chooses, and `run_macro`
  executes a `.py` file. `/mcp` is equally open, and `serve --host 0.0.0.0` exposes all of it to the
  network. I read the code; I did not build an exploit.
- Spec:
  1. Per-launch random token. The desktop shell and `serve --open` pass it to the page (cookie with
     `SameSite=Strict`, or injected into `index.html`); every `/api/*` request must carry it in a header.
     Requests without it get 401.
  2. Reject requests whose `Host` is not `127.0.0.1:<port>` / `localhost:<port>` (DNS rebinding) and whose
     `Origin`, when present, is not the app's own.
  3. Require `Content-Type: application/json` on POST.
  4. MCP over HTTP: same `Host` / `Origin` checks; token as a bearer header, printed at start-up and shown
     in a "Connect an agent" dialog with the ready `claude mcp add ... --header` line. stdio is unaffected.
  5. Binding to a non-loopback host requires `--allow-remote` and always requires the token.
  6. `run_macro` and `macro_stop(path)` are refused over HTTP unless the request came from the desktop
     shell's file dialog (the shell passes a one-use grant); macros otherwise run from
     `~/.openswx/macros` only.
- Acceptance: tests post to `/api/command` without a token, with a foreign `Origin`, with a foreign `Host`
  and as `text/plain`; all four are refused. `tools/agent_demo.py` still works with the token.

### B2. File commands accept any path from an agent
- Evidence: `save_part`, `export_*`, `pack_and_go`, `pdm_set_vault`, `import_file`, `open_part` take
  absolute paths; MCP clients are usually less trusted than the user.
- Spec: an optional workspace setting (`OPENSWX_WORKSPACE` / `--workspace DIR`). When set, commands with
  origin `agent` may read and write only under it (resolved, symlinks followed); the UI origin is not
  limited. Overwriting an existing file from an agent returns an error unless `overwrite=true`.
- Acceptance: an agent `save_part("C:/Windows/x.oswprt")` with a workspace set is refused with a message
  naming the workspace.

### B3. Parsers of untrusted files run in-process
- Evidence: `.sldprt` history rebuild is isolated in a child (`rebuild_isolated`); the Parasolid reader,
  DXF / STEP / IGES import and the `.sldasm` reader are not, and PLAN notes hangs in ShapeFix (ball valve)
  and OCC booleans.
- Spec: run every foreign-file read in the same child-process harness with a time limit and a memory cap,
  returning the body as BREP bytes. Add a small fuzz job (truncated and bit-flipped copies of the files in
  `tests/data/`) asserting "error or result, never a hang or crash of the parent".
- Acceptance: opening the ball-valve assembly reports the part that timed out and opens the rest.

---

## C. Data safety and robustness (P1)

### C1. No autosave, no crash recovery, no unsaved-changes guard
- Evidence: no `autosave`, `recover`, `beforeunload` or `localStorage` anywhere in `openswx/web/`;
  `desktop.py` has no close handler. A kernel crash (PLAN records several classes of them) or closing the
  window loses the session.
- Spec: every N commands or 60 s, write each dirty document to
  `~/.openswx/recovery/<doc-id>/<timestamp>.osw*` (atomic rename, keep the last 5). On start, list recovery
  sets newer than their saved file in a Document Recovery pane (open / discard). Closing the window or tab
  with dirty documents asks Save / Discard / Cancel (`window.events.closing` in pywebview, `beforeunload`
  in the browser). Clean exit removes the recovery set.
- Acceptance: kill the process mid-session; on restart the part is offered back with at most 60 s lost.

### C2. Native files have a version number but no migration path
- Evidence: `.oswprt` / `.oswasm` write `"version": 1`; `load_dict` has no version dispatch, and feature
  parameter shapes changed across passes.
- Spec: `openswx/migrate.py` with ordered `vN -> vN+1` functions over the JSON; load runs them, refuses
  files newer than the program with a clear message, and keeps unknown keys on round trip. Publish a JSON
  Schema (`docs/format/oswprt.schema.json`) generated from the feature builders. Keep a golden file per
  released version in `tests/data/native/` that must still open to the same volume.
- Acceptance: a v1 golden file opens after any future format change; a v999 file is refused, not corrupted.

### C3. Undo stores a full JSON copy of the document per step
- Evidence: `document.py` `_undo.append(json.dumps(self.to_dict()))`, capped at 100. A part with an
  imported body or a large history holds 100 copies; redo and assembly undo follow the same pattern.
- Spec: store a structural diff (JSON patch) against the previous state, with imported geometry
  content-addressed and stored once. Undo across documents in an assembly (edit in context) becomes one
  entry.
- Acceptance: 100 edits on a part with a 20 MB imported body keep undo memory under 2x the document size.

### C4. One global session, one lock
- Evidence: `session.APP` is a module global; `S.call` takes `APP.lock` (30 s wait, then a "busy" error).
  The UI, every MCP client and long solves (FEA, CFD, CAM, history rebuild) all queue on it, and two agents
  share one active document.
- Spec: (1) long-running commands become jobs: `start` returns a job id, progress and cancel are polled or
  streamed over SSE, and the lock is held only to read inputs and to commit results; (2) commands take an
  optional `document` argument instead of relying on the active one, so two clients can work on different
  documents; (3) per-document locks.
- Acceptance: the UI stays responsive (rotate, measure) while a CFD run is in progress and shows its
  progress with a working Cancel.

### C5. No logging or diagnostics
- Evidence: no `logging` use in `session.py` / `document.py`; rebuild diagnostics exist only as
  `OPENSWX_REBUILD_LOG=1` prints.
- Spec: `logging` to `~/.openswx/logs/openswx.log` (rotating): command name, origin, duration, error and
  traceback. Help > "Save diagnostic report" zips the log, version info and (opt-in) the active document.
- Acceptance: any error toast in the UI has a matching traceback in the log.

---

## D. Tests and quality (P1)

### D1. 47 commands have no test at all
`cfd_external cfd_results clear_equation composer_clear cut_loft cut_revolve cut_sweep
delete_alternate_position delete_assembly_feature delete_body delete_component delete_configuration
delete_global_variable delete_library_item delete_mate drawing_delete_sheet drawing_delete_table
drawing_move_table drawing_pick drawing_projected_view drawing_sheet_properties electrical_clear
electrical_import_wires electrical_info get_activity list_documents list_end_conditions measure
pdm_set_vault pdm_where_used plastics_materials plastics_results set_sketch_visibility sheet_metal_info
show_alternate_position sketch_arc sketch_delete sketch_extend sketch_fillet sketch_mirror sketch_move_point
sketch_offset sketch_polygon sketch_slot sketch_trim swept_surface`
(by name match against `tests/`; some may be exercised through a document method instead).
- Spec: a registry test that fails when a command has no test naming it, with an explicit allow-list that
  can only shrink. Priority: the cut variants (`cut_revolve`, `cut_sweep`, `cut_loft`), the sketch edit
  tools (`sketch_trim`, `sketch_extend`, `sketch_offset`, `sketch_fillet`, `sketch_mirror`), the delete
  commands (each must leave a document that rebuilds and round-trips through save / open).

### D2. One 4,128-line test file
- Spec: split `tests/test_core.py` by area (`test_sketch.py`, `test_features.py`, `test_assembly.py`,
  `test_drawing.py`, `test_sheet_metal.py`, `test_simulation.py`, `test_interop_parasolid.py`,
  `test_interop_solidworks.py`, ...) with shared builders in `conftest.py`. Enables `pytest -n auto`.

### D3. The UI has no automated test
- Evidence: `tests/test_web_syntax.py` only parses the JS. PLAN records manual / CDP-driven sessions, none
  kept as tests. 7,600 lines of JS.
- Spec: Playwright suite under `tests/ui/` against `openswx serve` on a free port: first part (plane,
  rectangle, exit, extrude), Smart Dimension + Modify, fillet via edge pick, edit feature, undo / redo,
  save / reopen, assembly with two mates, drawing with three views exported to PDF. Screenshot comparison
  of the viewport for five reference models with a tolerance. Runs in CI on Linux.

### D4. The conformance suite the porting plan depends on does not exist
- Evidence: PLAN "Porting strategy" rule 2 names `tests/conformance/*.json`; the directory is absent.
- Spec: JSON cases `{commands: [...], expect: {volume, area, faces, edges, bbox, tolerance}}`, a runner in
  `tests/test_conformance.py`, and a converter that records an existing pytest scenario through the macro
  recorder. `tools/difftest` replays the same files against SOLIDWORKS. Decide first whether the C++ port
  is still the plan (see H1); if not, the suite is still the right regression format.

### D5. No static checks, no declared dependency bounds
- Evidence: no ruff / mypy / pre-commit config; `numpy`, `scipy`, `fastapi`, `uvicorn` are unpinned;
  `ezdxf` is imported for DXF but not declared in `pyproject.toml` (it arrives through build123d);
  `openswx/__init__.py` installs a stub `sklearn` module to satisfy an import.
- Spec: ruff + a lock file (`uv.lock` or `requirements.lock`) used by CI and the release build; declare
  `ezdxf` and any other direct import; document why the `sklearn` stub exists and test that it is still
  needed.

### D6. The SOLIDWORKS survey is not reproducible from the repository
- Evidence: the 841-part survey scripts (`survey.py`, `sv2.py`) and results (`survey_v4.json`) live in a
  session scratch directory; only `tools/difftest/` is in the tree. The headline number (525 / 841) cannot
  be re-measured by anyone else, and regressions are caught by hand.
- Spec: `tools/survey/` with the chunked, resumable runner, taking a corpus directory (the user's own
  SOLIDWORKS install; no sample files in the repo), writing `results.jsonl`, and a `compare` command
  listing gained / lost parts against a previous result. Check in the per-part result table (names and
  outcomes only) as `docs/survey/2026-10-29.json`.

---

## E. Architecture (P2)

### E1. `session.py` is a 4,700-line module holding 272 commands
- Spec: package `openswx/commands/` with one module per category (the category names already exist in the
  registry: document, sketch, features, surfaces, sheet_metal, weldments, assembly, drawing, simulation,
  ...). `session.py` keeps `Application`, `call`, `preview`, the `@command` decorator and re-exports the
  names, so `S.extrude` and the MCP tool names do not change. `commands_more.py` / `commands_mfg.py` fold
  into it. Mechanical move, no behaviour change, one category per commit.

### E2. 366 tools in one flat MCP list
- Evidence: `mcp_server.py` registers every command; most clients load all tool schemas into context, and
  several cap the tool count well below this. `INSTRUCTIONS` still describes the phase-1 workflow and says
  ids "change after every model edit" although `naming.py` now gives persistent names.
- Spec:
  1. Toolsets: `--toolsets core,sketch,features` (default `core` = about 40 tools) plus two meta tools,
     `list_toolsets` and `enable_toolset`, using MCP `tools/list_changed`.
  2. `get_topology` returns the persistent name beside each `Face#` / `Edge#`, and every selection argument
     accepts either; instructions updated to prefer names.
  3. Tool annotations from the registry (`readOnlyHint` from `mutates=False`, `destructiveHint` for
     delete / overwrite commands) and output schemas for the query tools.
  4. `batch(commands=[...], atomic=true)`: one undo step, rolled back on the first error, one rebuild.
  5. Resources: `openswx://model` exists; add `openswx://feature/{name}`, `openswx://drawing/{sheet}.svg`,
     `openswx://view/{name}.png`, and resource-changed notifications on document version change.
  6. Errors carry a machine-readable code and, for rebuild failures, the failing feature and a suggestion.
- Acceptance: a default connection lists fewer than 50 tools and can still build the README bracket.

### E3. UI state is not persisted and the UI is English, mouse-only
- Evidence: no `localStorage` in `web/`; no `aria-` attributes; all strings inline.
- Spec: Tools > Options dialog (units default, view colours, mouse mapping incl. a "middle button absent"
  trackpad mode, autosave interval, tessellation quality) stored in `~/.openswx/settings.json` through two
  commands (`get_settings`, `set_settings`); customisable shortcuts; a string table so the UI can be
  translated; roles / labels on the ribbon, tree and PropertyManager so it is keyboard-navigable.

---

## F. SOLIDWORKS interoperability (P2; R5 in the plan is the one unticked roadmap item)

| Gap | State | Spec |
|---|---|---|
| Feature history | 584 / 841 exact (2026-11-23); sweeps, lofts, sheet metal, most surface features, multibody, cavity not decoded; ~90 body mismatches, 48 over time; reader: the plastics samples wrench (an extra LocalBodies transmit SOLIDWORKS does not count as a body) and handle / brush_handle (a transmit the reader cannot parse), see docs/re/README.md; base features of shelled parts (filter: the decoded Base-Revolve is right, 27.9e6 mm3, but is scored against the finished hollow body, so section candidates are tried first and the part needs 142 s, past the survey's 115 s): score features before a Shell against the outer envelope. Tried 2026-11-24: taking a pre-Shell feature's decoded sketch as soon as it has its tagged faces makes Base-Revolve, Boss-Revolve1 and Boss-Revolve2 parametric with no section search, but the time saved goes to trying Mirror1 ("tagged faces are not on the body") and then a kernel call that did not return in 20 minutes, so the part still times out; reverted. Needs the Mirror1 selection decoded and the hanging trial found (run it isolated) before this pays | Work the survey's failure table by feature class, each backed by oracle pairs from `tools/difftest/feature_samples.py`: (1) sweep as base feature (7 of the 25 remaining base failures), (2) loft, (3) mirror and pattern selections decoded instead of searched, (4) fillet / chamfer / shell selections decoded (removes most candidate search and with it most timeouts), (5) sheet-metal base / edge flange |
| Partial open | A part either rebuilds or falls back to one imported body | Always open with the longest exactly rebuilt prefix, then one imported feature for the rest, and a per-feature report in the tree ("imported: reason") |
| Configurations of SW parts | The active configuration's body is read | Read every `Config-<n>` partition into openswx configurations (bodies as imports when the history does not cover them) |
| Assembly mates | 81 % on small assemblies; width, gear, cam, in-place, pattern / mirror faces unsupported | Decode the remaining mate classes with oracle assemblies; import unsupported ones as suppressed mates with their name and type so nothing vanishes silently |
| Large assemblies | `foodprocessor` runs past 120 s | Load component bodies lazily and in parallel worker processes; show bounding boxes first |
| `.slddrw` | Views, sheets, notes, FCFs; no dimension positions, balloons, datums, sheet-format content | Needs SOLIDWORKS-made test drawings (ask before launching SOLIDWORKS). Until then mark imported drawings "approximate layout" in the title bar |
| Writing SOLIDWORKS files | None; export is Parasolid `.x_t`, STEP, IGES | Out of reach as a native history; spec a "Send to SOLIDWORKS" export = `.x_t` + a sidecar JSON of feature names, dimensions and custom properties, and state plainly in the README that `.sldprt` is read-only |
| Appearances, custom properties, materials from SW files | Not carried over | `docProps` custom properties and the material name are cheap wins: map to `set_custom_property` / `set_material` |
| Neutral formats | STEP / IGES in and out, 3MF / glTF / STL out, DXF, DWG through an external converter | STEP AP242 with colours, names and assembly structure both ways; STL / OBJ / 3MF import as a mesh body with mesh-to-solid for small meshes; JT is not worth it |
| Parasolid | PE (foreign) surfaces unreadable; writer is text-only, schema 13006 | Binary `.x_b` writer; report PE surfaces per face and keep the rest of the body as a sheet |

---

## G. CAD functionality still absent (P2)

Checked against the command registry; none of these has a command today.

**Sketch**
- Sketch blocks (make / insert / explode); sketch picture; equation-driven and parametric curves;
  style spline (control-polygon); fit spline; make path; rapid sketch dimension chains
  (baseline / chain / ordinate inside sketches); equal-curvature and torsion-continuity relations on
  splines; arc-length and path-length dimensions (the solver has 12 relation and 7 dimension types).
- Dimension tolerances and fits on sketch and feature dimensions (`+0.1 / -0.0`, `H7`), flowing to drawings,
  the PMI and Inspection. Today tolerances exist only as GD&T frames and Inspection overrides.
- Spec for tolerances: every dimension record gains `tol: {type: none|symmetric|bilateral|limit|fit,
  plus, minus, fit}`; `set_dimension_tolerance(dim, ...)`; drawings render it; Inspection reads it before
  falling back to ISO 2768; ISO 286 tables for holes and shafts up to 500 mm.

**Part**
- Sensors (mass, dimension, interference, simulation result with alerts); display states; derived
  configurations; configuration-specific materials, colours and custom properties; Instant3D beyond the
  extrude depth handle (drag faces, on-model dimension edit for every feature); equations with `$PRP`
  file-property links, units and suppress-by-equation (check which of these the evaluator already takes;
  it has functions and an if-expression); freeze bar.
- Sheet metal: lofted bend, swept flange, unfold / fold, tab and slot, convert solid to sheet metal, rip,
  vent, cross break, gauge and bend tables, normal cut, flat-pattern bend notes and bounding box.
- Weldments: trim / extend as a command, custom profile library from sketches, cut-list properties per
  body, weld table.
- Surfaces: mid-surface, face curves, surface from mesh.
- Mesh / 3D-print: mesh bodies, print-bed check, overhang analysis, slicing preview.

**Assembly**
- Assembly configurations and display states; flexible subassemblies; lightweight components; belt / chain;
  collision detection and physical dynamics while dragging; physics-based motion (motors, springs, gravity,
  contact) with force and torque plots; interference in motion; mate controller; virtual-component save-out;
  top-down layout sketches; exploded line sketch; assembly-level equations across components.

**Drawings**
- Ordinate, baseline and chain dimensions; dimension tolerance display; revision clouds; general,
  weldment cut-list, bend and punch tables; view-specific display states / configurations; relative and
  removed-section views; empty and predefined views; linked view labels and `$PRP` note links; drawing
  templates (`.oswdrt`) and drafting standards (ISO / ANSI / DIN / JIS) as a setting that changes arrows,
  text and view labels; detached drawings (open without the model).

---

## H. Engine limits worth lifting (P2 / P3)

### H1. Decide the C++ port
`docs/PLAN.md` commits to "Python is the executable specification, C++ is the shipping core" but nothing
has started, the conformance suite is absent, and the program has since tripled. Either schedule the first
port step (the sketch solver behind pybind11, the smallest self-contained piece) or delete the section. A
cheaper route to the same goal (start time, 400 MB bundle, solver speed): keep Python, move hot loops to
compiled extensions case by case, measured by a benchmark suite (see I4).

### H2. Simulation
Voxel finite-cell hexes: stresses at curved boundaries are approximate. Spec: a body-fitted second-order
tetrahedral mesher (gmsh or netgen as an optional dependency, with the voxel solver as the fallback),
mesh controls per face, convergence (h-adaptive) plots, remote loads, bearing loads, cyclic symmetry,
shells for sheet metal and beams for weldments (both fall out of data the model already has), result
probes and section clipping of results, a study report (HTML / PDF), and a verification manual generated
from the analytic test cases already in the suite.

### H3. Sketch solver
`scipy` least squares with a time box. Spec: decompose the constraint graph into rigid clusters and solve
them in order (most sketches fall apart into small pieces), keep the unknowns of unaffected clusters fixed
while dragging, and report over-definition as the minimal conflicting set rather than "red". Benchmark:
the 58-unknown `slot_base` sketch from 19 s to under 1 s.

### H4. CAM
Flat end mills, 3-axis, raster precision. Spec in order of value: tool library with ball and bull
mills and drills / taps, rest machining, adaptive (constant-engagement) roughing, 3D parallel and
scallop finishing, 2D profile / pocket / face operations from selected faces rather than the height map,
tabs, lead-in / out, setup sheets, and a turning module for revolved parts.

### H5. CFD and plastics
State the validity range in the UI, not only in the result's `warning`. CFD: grid refinement near walls,
rotating regions, conjugate heat transfer with the existing thermal solver. Plastics: non-isothermal fill
with a frozen layer, packing, gate-location advisor.

### H6. Rendering
Real-time PBR only. Spec: an offline path tracer through the browser (three.js path-tracing renderer in
the UI) for stills and turntables; HDRI import; appearance library with textures.

---

## I. Platform and distribution (P2 / P3)

1. **Linux**: no build. Spec: AppImage from the same `packaging/build.py` (pywebview on GTK / Qt), plus
   `pipx install openswx` documented as the supported route; add the Linux runner to the build matrix.
2. **Code signing**: macOS is ad-hoc signed (Gatekeeper blocks first launch); the Windows installer is
   unsigned (SmartScreen warning). Spec: Developer ID + notarisation in CI through secrets; Windows signing
   through a certificate or SignPath's open-source programme.
3. **Updates**: none. Spec: check the GitHub releases feed at start (opt-out), show "version X available",
   download the installer on request. No silent self-update.
4. **Size and start time**: ~400 MB unpacked, ~40 s first start, ~3 s after. Spec: a benchmark job
   (`tools/bench.py`: cold import, first extrude, 100-feature rebuild, tessellation of a 1,000-face body,
   open of five reference `.sldprt` files) with results tracked per release; lazy imports for simulation,
   CAM, CFD and the SOLIDWORKS readers; audit the PyInstaller bundle for unused OCP / VTK / IPython parts.
5. **PyPI**: `pyproject.toml` is ready but nothing is published; `packages = ["openswx"]` with
   `package-data` for `web/` works, add classifiers, URLs and the long description.
6. **Stray files**: `bash.exe.stackdump`, `build-new.log`, `build-rel.log`, `build/`, `build-new/`,
   `dist/`, `dist-new/` sit in the repository root (ignored, about 2 GB together). Move build output under
   one ignored `out/` directory.
7. **Windows `.oswdrw` association and macOS document icons**: check both installers register all three
   native types the same way (the Windows uninstaller already cleans all three keys).

---

## J. Feature suggestions (beyond parity)

Ordered by how much they lean on what openswx already does better than the program it imitates.

1. **Agent-native review loop.** A `review_model` tool that returns a structured critique an agent can act
   on: under-defined sketches, features with fallback references, thin walls, missing draft, unreachable
   CAM regions, interference, FoS below a target. Each finding names the feature and the command that fixes
   it. The pieces (check_geometry, thickness, draft, undercut, FEA, CAM simulation) all exist; this joins
   them.
2. **Design intent as text.** `export_script` writes the current document as a readable Python macro
   (the recorder already emits one per session); `import_script` rebuilds it. Gives diffable, reviewable,
   version-controlled CAD and a second native format that agents can read and edit directly.
3. **Semantic diff and merge.** `compare_documents(a, b)` at feature level (added / removed / changed
   parameters, volume and mass delta, a coloured 3D overlay of added and removed material). With (2) this
   makes Git a workable PDM, including a `git difftool` driver.
4. **Requirements as tests.** Sensors plus assertions stored in the part: `mass < 250 g`,
   `min_wall >= 1.5`, `FoS >= 2 under study "Load case 1"`, `fits_in(200, 100, 50)`. `check_requirements`
   runs them all; the tree shows red / green; `openswx check part.oswprt` runs them in CI.
5. **Parametric studies and optimisation.** `design_study(variables, objective, constraints)` sweeping or
   optimising dimensions against mass / stress / frequency / cost, on top of the incremental rebuild and
   the existing solvers; results as a table and a Pareto plot. Topology optimisation on the voxel grid is a
   natural extension of the FEA solver's data structures.
6. **Headless CLI.** `openswx convert in.sldprt out.step`, `openswx render part.oswprt --view iso -o x.png`,
   `openswx drawing part.oswprt -o part.pdf`, `openswx massprops`, `openswx check`. All are one command
   call each today; they make openswx useful in pipelines without the UI.
7. **Python package as a library.** A documented, typed public API (`import openswx as osw;
   part = osw.Part(); part.sketch("Top")...`) over the command registry, with a Jupyter viewer widget. The
   macro format then becomes the same API.
8. **Plugin system.** Entry-point group `openswx.plugins`: a plugin registers commands (which become MCP
   tools and get a ribbon button from metadata), materials, hole and profile standards, CAM posts and
   sheet formats. Moves CAM posts, pipe standards and Toolbox tables out of the core.
9. **Onshape / FreeCAD / Fusion bridges.** FreeCAD `.FCStd` import (open format, history readable) and
   Onshape import through its public API would widen the "open anything" story beyond SOLIDWORKS at far
   lower cost per format than the reverse-engineered readers.
10. **Sketch from image or scan.** Trace a photo or a DXF-less drawing into constrained sketch geometry
    (line / arc fitting, inferred relations already exist for imported SOLIDWORKS sketches).
11. **Natural-language feature search and command palette.** Ctrl+K over all 381 commands with their
    docstrings, plus "find features that reference this face / use this variable".
12. **Collaboration, the cheap way.** Read-only live share: the SSE stream and the single-file viewer
    already exist; a `share` command serving a view-only page with a token gives design reviews with
    comments pinned to faces (persistent names make the pins stable).
13. **Manufacturing hand-off pack.** One command producing drawing PDF, STEP, flat-pattern DXF, BOM CSV,
    inspection form and G-code in a zip with a manifest, for a part or a whole assembly.
14. **Tolerance stack-up.** Once dimension tolerances exist (section G): worst-case and RSS stack between
    two faces across an assembly's mates.
15. **3D-print workflow.** Orientation advisor (support volume, overhang area), lattice infill for a
    selected region, direct 3MF with per-body settings.
16. **Standard-parts catalogue.** Bearings, retaining rings, keys, O-rings, extrusions (2020 / 4040
    profiles as weldment sections), timing pulleys and gears generated from parameters, as a plugin data
    set.
17. **Onboarding.** Three in-app tutorials driven by the same event stream that shows agent activity
    (highlight the next button, verify the step), and sample parts under Help.

---

## Suggested order

1. A1-A3, B1 (a week of work; nothing public before these).
2. C1, C2, D1, D2, A4 (data safety, then a test base that makes the next steps safe).
3. E1, E2, D6 (structure; agent ergonomics; reproducible survey).
4. Dimension tolerances (G) and ordinate / baseline dimensions: the largest everyday drawing gap.
5. F in survey order (sweep base features first), in parallel with J1, J2, J6, which are cheap and
   distinctive.
6. C4 (jobs) before any further solver work in H.
