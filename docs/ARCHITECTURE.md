# Architecture

A contributor's map of the code in this repository: where things live, what happens when a command runs, and
where to add code. It describes the code as it is, not as it should be.

## One command registry

Every action is a Python function decorated with `@command` from `openswx/session.py`.

```python
from openswx.session import command

@command(category="features")
def extrude(sketch: str, depth: float = 10.0) -> dict:
    """Docstring: what it does, its arguments and units, what it returns."""
    ...
```

The decorator wraps the function and records it in `session.COMMANDS[name]` with its `mutates`, `category`,
`mcp` flags and its docstring (`doc`). The docstring is the MCP tool description, so it is part of the
interface, not a comment.

The same registry feeds four surfaces:

| Surface | Entry point | Origin recorded in the log |
|---|---|---|
| Browser UI | `POST /api/command` in `openswx/server/web_server.py` | `ui` |
| MCP over HTTP | `/mcp`, mounted in `web_server.create_app` | `agent` |
| MCP over stdio | `openswx mcp` (`__main__.py` -> `mcp_server.py`, relaying through `mcp_bridge.py`) | `agent` |
| Python / CLI / tests | `openswx.api.Part`, `openswx.shell.cli`, `session.call` | `agent` / `ui` |

The command modules are imported by `openswx/commands/__init__.py`; `session.py` imports that package at the
bottom, then `openswx/collab/commands_collab.py`, then calls `openswx.system.plugins.load_all()`. Importing
`openswx.session` therefore registers every built-in command exactly once.

## Command lifecycle

1. **UI request.** `openswx/web/js/app.js` (`App.api`, `App.run`, `App.commandOrJob`) posts
   `{"name": ..., "args": {...}}` to `/api/command`. Long-running commands go through the `start_job` command
   instead (see [Threading and jobs](#threading-and-jobs)).
2. **Server.** `web_server.create_app` installs `openswx/server/security.py`'s `Guard` middleware (token,
   loopback/origin checks, view-only links), then `run_command` rejects writes on a view-only link and calls
   `await asyncio.to_thread(S.call, name, args, "ui")` so the event loop is not blocked.
3. **Dispatch.** `session.call` checks that the command exists and that the arguments fit its signature
   (`argument_problem`), sets the thread-local origin (`ui` or `agent`), and invokes the decorated wrapper.
4. **Wrapper.** The `@command` wrapper in `session.py`:
   - confines file arguments to `OPENSWX_WORKSPACE` for agent calls (`FILE_ARGS`, `_check_workspace`);
   - takes `APP.lock` (a 30 s timeout, not a wait forever) and records the command in `APP.running`, which
     `/api/health` reports as `busy`;
   - runs collaboration hooks (`COMMAND_HOOKS`, `before` / `after`) for the outermost mutating command;
   - watches a clean document and marks it dirty if a command changed it without setting `dirty` itself
     (`_fingerprint`, `_mark_dirty`);
   - runs the function body;
   - maps `DocumentError`, `FeatureError`, `SketchError`, `ValueError`, `KeyError` and `FileNotFoundError`
     to `CommandError` (the message a user or agent sees); unexpected exceptions keep their traceback and are
     logged as defects;
   - calls `APP.bump`, which increments `APP.version`, appends to `APP.log` (last 200 entries) and is what the
     SSE stream watches;
   - records the command in the active document's journal (`_journal`, skipped for `NO_JOURNAL` and `pdm_`,
     `macro_`, `collab_` commands) and starts the autosave thread on first change.
5. **Document change.** A command such as `extrude` (`openswx/commands/features.py`) calls
   `APP.doc.add_feature(...)`. `openswx/core/document.py` takes an undo checkpoint, inserts the feature and
   calls `rebuild()`.
6. **Rebuild.** `PartDocument.rebuild` walks the active feature list, evaluates equations and configuration
   overrides, and calls the builder for each type from `core/features/`'s `BUILDERS`. A content hash of the
   feature chain lets `_rb_cache` resume from the first changed feature. `core/sketch.py` solves sketches,
   `core/naming.py` gives faces persistent names, and each feature's result or error lands in `document.state`.
   The document's `revision` goes up, `dirty` is set.
7. **Mesh.** `PartDocument.mesh()` (`core/document.py`) tessellates the body via `core/geom.tessellate` and adds
   sketch, plane and reference overlays. The UI fetches it after a version change, not on every command: the
   binary `GET /api/mesh.bin` it prefers (typed-array buffers, decoded by `viewport.decodeMeshBinary`), falling
   back to `GET /api/mesh` JSON.
8. **Live update.** `GET /api/events` in `web_server.py` is a server-sent-event stream: it polls `APP.version`
   (and the collaboration revision) every 50-150 ms and sends `data: {"version": ..., "last": ...}`. `app.js`
   reloads `GET /api/state` and the mesh when the version differs, and shows agent-origin commands in the
   activity pane.

`POST /api/preview` is the exception to the mutation path: `session.preview` (same file) runs a whitelisted
feature command (`preview_refusal`) on a throw-away copy of the active document and returns its mesh without
touching the real document, the undo stack or the journal.

## Package layout

| Path | Contents |
|---|---|
| `openswx/session.py` | `APP` (the `Application` object: documents, active document, lock, version, log, running commands), the `@command` registry, `call`, `preview`, workspace rules, command hooks, journal helpers, the `Application` lifecycle. `COMMANDS` is the single source of truth. |
| `openswx/api.py` | `Part` / `Sketch`: a small object API over `session.call`, for notebooks and scripts. |
| `openswx/core/` | The model. `document.py` (`PartDocument`: features, rebuild, undo, save/load), `assembly.py` (`AssemblyDocument`: components, mates, solver), `sketch.py` (constraint solver), `features/` (`BUILDERS`, mass properties, shape encoding), `geom.py` (tessellation and references), `naming.py` (persistent face names), `equations.py`, `materials.py`, `advanced.py`, `sheet_more.py`, `hardware.py`. |
| `openswx/commands/` | The commands, grouped by area: `document.py`, `sketch.py`, `features.py`, `assembly.py`, `asm.py`, `drawing.py`, `simulation.py`, `more/`, `mfg.py`, `app.py`, `tol.py`, `review.py`, `settings.py`, `blocks.py`, `simreport.py`, `std.py`. `support.py` holds the shared helpers (selection specs, foreign-file readers, drawing/simulation edits) that command modules import; `session.py` re-exports the moved names. |
| `openswx/exchange/` | File formats: `sldprt.py` (SOLIDWORKS reader), `sw_tree.py`, `sw_sketch.py`, `sw_rebuild/`, `sw_drawing.py`, `parasolid.py` / `parasolid_write.py`, `dxf_io.py`, `fcstd.py`, `migrate.py` (native format versions). |
| `openswx/drawing/` | `drawing.py`, `drawing_sheets/`, `drawing_more.py`: sheets, views, annotations, tables, and export. |
| `openswx/analysis/` | `fea.py`, `fea_tet.py` (optional Netgen mesher), `cfd.py`, `tolerance.py`, `inspection.py`, `render.py`, `evaluate.py`, `analysis_more.py`, `fea_more.py`. |
| `openswx/manufacturing/` | `cam.py`, `plastics.py`, `piping.py`, `electrical.py`, `ecosystem.py`: CAM, G-code, fill analysis and routing. |
| `openswx/system/` | `settings.py`, `recovery.py` (autosave), `snapshots.py` (shared undo storage), `jobs.py` (background jobs), `isolated.py` (child-process file readers), `cache.py`, `plugins.py`, `applog.py`, `security`-adjacent helpers, `recent.py`, `updates.py`, `fileassoc.py`. |
| `openswx/server/` | `web_server.py` (FastAPI app, REST, SSE), `mcp_server.py` (tools and toolsets), `mcp_bridge.py` (stdio-to-HTTP relay), `security.py` (token, origin, view-only links). |
| `openswx/shell/` | `cli.py` (headless one-shot commands: `convert`, `render`, `massprops`, `check`, `drawing`, `script`, `diff`), `desktop.py` (pywebview window around the same server). |
| `openswx/web/` | The UI: `index.html`, plain ES modules in `js/` (`app.js` plus mixins in `js/mixins/`, `more.js`, `mfg.js`, `home.js`, `collab.js`, `drawing.js`, `sketcher.js`, `viewport.js`, `propman.js`, `keys.js`), styles in `css/` (one file per widget group, imported by `style.css`), SVGs in `svgs/`, vendored libraries in `vendor/`. No build step. |
| `tools/` | `gen_command_docs.py` (generates `docs/commands.md`), conformance cases, benchmarks, SOLIDWORKS survey. |
| `packaging/` | `entry.py`, `openswx.spec` (PyInstaller), `build.py`, `build_linux.sh`, smoke tests. |

`session.py`, `api.py` and `icons.py` stay at the top level because they are shared entry points.

## Threading and jobs

- **Command lock.** `APP.lock` is an `RLock` held for the whole outermost command; nested commands
  (a command calling another) reuse it. This serialises model changes with undo, autosave and collaboration
  view updates. `session.unlocked()` releases it around a long read-only computation (used by solvers), then
  takes it back, so the UI can still orbit and measure.
- **Busy state.** `APP.running` maps thread id to `(command, start time)`; `APP.busy` reports the longest
  running command, exposed by `GET /api/health`. The UI uses it to warn instead of hanging.
- **Jobs.** `openswx/system/jobs.py` runs a long command on a worker thread, with `jobs.progress()` (which
  raises `jobs.Cancelled` after a cancel) and `jobs.start` / `get` / `cancel` / `wait`. The commands
  `start_job`, `job_status` and `cancel_job` (`openswx/commands/app.py`) expose them; `app.js`'s
  `commandOrJob` shows a progress toast for the commands listed in `web/js/lib/constants.js` (`LONG_JOBS`).
- **Autosave.** `openswx/system/recovery.py` starts a daemon thread when the first change bumps the
  application version. It writes dirty documents to `<home>/recovery/<pid>-<time>/` with a heartbeat, so a
  later start can offer them back; a clean exit removes the folder.
- **Isolated readers.** `openswx/system/isolated.py` reads foreign CAD files in a child process (so a kernel
  crash or hang costs one file, not the application); `OPENSWX_ISOLATE=off` keeps it in-process for tests.
- **Collaboration.** Each cloud document has a network thread and a view thread in `openswx/collab/client.py`
  (see below).
- **Tests.** `tests/conftest.py` sets `OPENSWX_ISOLATE=off`, `OPENSWX_PLUGINS=off`, `OPENSWX_AUTOSAVE=off`,
  `OPENSWX_LOG=off`, `OPENSWX_TRAY=off` (no tray icon from `openswx mcp`) and a temporary `OPENSWX_HOME`, and
  clears `APP.documents` before each test.

## Documents, persistence, undo and snapshots

- **Documents.** `Application.documents` is `{name: document}` and `Application.active` names the current one.
  `active_doc` creates a throw-away `Part1` on demand; `open_part`, `new_part` and `new_assembly` manage the
  active document. Assemblies are `core/assembly.py`'s `AssemblyDocument`; they use `live_docs` to resolve
  component parts by name.
- **Native format.** `.oswprt` / `.oswasm` are JSON written by `PartDocument.to_dict` and read by
  `PartDocument.load_dict`, with `openswx/exchange/migrate.py` upgrading older versions; `.oswdrw` is the
  drawing document written by `save_drawing_document`. A file holds the
  feature tree, parameters, sketches, configurations, variables, properties, appearances and, when known, the
  command journal that rebuilds the document (`export_script` replays it). `save` / `open` are in
  `core/document.py`; `save_part` / `open_part` are in `commands/document.py`.
- **Rebuild is the source of truth.** Geometry is never serialised as the model: it is recomputed from features
  by `rebuild()`. The imported B-rep of a foreign file is one of the feature parameters (`encode_shape`).
- **Undo / redo.** Every mutating document operation calls `_checkpoint()`, which packs `to_dict()` into
  `openswx/system/snapshots.py` and keeps the last 100 entries. `snapshots.pack` replaces long strings (an
  imported body, an embedded component) with content-addressed references to one shared store, so a hundred
  undo steps hold the body once. `undo` / `redo` unpack a snapshot and `load_dict` it.
- **Dirty tracking.** `PartDocument.dirty` is set by rebuilds; the `@command` wrapper additionally compares a
  fingerprint of a previously clean document to catch property-only edits (`_mark_dirty`).
- **Journal.** `session._journal` appends the command name and arguments to `document.journal` after each
  mutating command; commands about files and session state are excluded (`NO_JOURNAL`). This is what
  `export_script` and `openswx script` turn back into Python.
- **Recovery.** `recovery.save_now` writes dirty documents (and their metadata) using the same `to_dict`
  format; `recovery.recovered` lists copies whose process is gone, and the UI offers them on start
  (`list_recovered`, `recover_document`, `discard_recovered`).
- **Display state.** The mesh is derived on request (`PartDocument.mesh`) and carries a `revision`; nothing
  about tessellation is stored in the document.

## MCP

`openswx/server/mcp_server.py` builds the MCP server from the same `COMMANDS` registry.

- **Toolsets.** There are more commands than fit in an agent's context, so tools are grouped by command
  `category` in `TOOLSETS`. A connection starts with `core` (the list in `CORE`) plus `list_toolsets`,
  `enable_toolset` and `batch`. `enable_toolset` adds an area and notifies the client that the list changed.
  `OPENSWX_TOOLSETS` / `openswx mcp --toolsets` / `serve --toolsets` choose the starting sets.
- **Tool descriptions and hints.** A tool's description is the command docstring (`tool_description`); MCP
  annotations come from `mutates` and `DESTRUCTIVE` (`_annotations`). A command with `mcp=False` is not a tool.
- **Resources.** `openswx://model`, `openswx://commands` and `openswx://feature/{name}` are resources in
  `build_mcp`.
- **HTTP and stdio.** `python -m openswx serve` mounts the streamable-HTTP app at `/mcp`. `python -m openswx
  mcp` either serves MCP over stdio itself (and starts a UI) or, when an openswx is already running on the
  port, relays JSON-RPC through `openswx/server/mcp_bridge.py` so the agent edits the open window.
- **Plugins.** Commands that came from a plugin carry a `plugin` marker in `COMMANDS`; they appear in the
  `plugins` toolset (`toolset_commands("plugins")`), which also shows up in `list_toolsets` when non-empty.
- **Access control.** `openswx/server/security.py` requires the per-user token for `/api/` and `/mcp` unless
  started with `--no-token`, checks origins, confines remote access unless `--allow-remote`, and supports a
  view-only share token that the server rejects non-query commands for. `OPENSWX_WORKSPACE` additionally
  confines agent file arguments (`session._check_workspace`).

## Collaboration

Live collaboration lives in `openswx/collab/` and is optional (`openswx[collab]`, protocol package
`openswx_collab_protocol`).

- `commands_collab.py` adds the `collab_*` commands (sign in, share, open, lock, history, presence).
- `client.py` holds one `DocClient` per open cloud document. A network thread keeps `confirmed`, the document
  as the server has it; a view thread applies `confirmed` plus local pending changes to the open document under
  `APP.lock`, so another user's change never lands mid-command.
- `session.COMMAND_HOOKS` is the integration point: hooks are registered while a cloud document is open and
  see the outermost mutating commands (`before` / `after`, plus optional `redirect` used by undo). The client
  diffs `to_dict()` before and after and sends one change; the command waits for the server's answer.
- Feature ids and names for cloud documents are allocated by the server through `PartDocument.allocator`
  (`take_id` / `take_name`).
- The web server exposes `GET /api/collab` and `POST /api/collab/presence`; presence is handled without the
  command lock so moving a pointer does not wait for a rebuild. The SSE stream carries a `collab` revision.
- `auth.py` handles accounts/tokens, `backup.py` local copies; the protocol and server live in the separate
  `openswx-collab` repository.

## Web UI

The UI is served statically from `openswx/web/`. `index.html` is a single page; `web_server.index` injects the
SVG sprite (`openswx/icons.py`, built from `web/svgs/`) and, when appropriate, marks the Home page as the
start page. `js/app.js` is the `App` core: it owns the command call, the SSE connection, the refresh loop
(`refreshOnce` fetches `/api/state` and the mesh: `/api/mesh.bin`, or `/api/mesh` JSON when that fails) and
document-recovery prompt. Mixins in `js/mixins/`
(`ui`, `tree`, `sketch-pm`, `input`, `features`, `panels`, `files`, `settings`) and `more.js`, `mfg.js`,
`home.js`, `collab.js` add methods to `App`; they must not reuse an `App` method name
(`tests/test_web_mixins.py`).
`viewport.js`/`sketcher.js`/`drawing.js`/`propman.js` are the 3D viewport, sketch editor, drawing editor and
property manager. Styles live in `web/css/`, one file per widget group imported in order by `style.css`.

## Where to add code

- **A new command:** write a `@command` function in the module for its area under `openswx/commands/`, give it
  a real docstring (arguments, units, return value), add it to that module's `__all__`, and make sure a test
  names it. Run `python tools/gen_command_docs.py`; `tests/test_docs.py` checks the generated doc, the
  description length and the test coverage. If you add a built-in category, add it to `TOOLSETS` in
  `openswx/server/mcp_server.py` (otherwise only `all` reaches it; plugin commands reach agents through the
  `plugins` toolset).
- **A new document feature type:** implement the builder in `openswx/core/features/` (`BUILDERS`), store
  plain JSON parameters, and expose it through a command. Add a test with an independent check (analytic
  volume/area or a stored reference), not a number copied from the code's output.
- **A new file format:** add the reader/writer under `openswx/exchange/`, call it from `session._import_file` or
  `commands/document.py`'s export path, and keep format facts in `docs/re/` before implementation.
- **A new module:** it is collected into the desktop build automatically by `collect_submodules` in
  `packaging/openswx.spec`; a new package directory still has to be added to `[tool.setuptools] packages` in
  `pyproject.toml`.
- **An extension without touching the tree:** publish a package that declares an `openswx.plugins` entry
  point; see `examples/openswx-gears/` and `openswx/system/plugins.py`.
- **A UI feature:** add a method to a mixin (never shadow `App`), a CSS file under `web/css/` if it is a new
  widget group, and an SVG in `web/svgs/` for a new pictogram; no build step is involved.
