# Notes for coding agents

Read this together with [CONTRIBUTING.md](CONTRIBUTING.md); the rules there apply to agents too. This file
adds what an agent needs that a person picks up by asking.

## Hard rules

- **Clean room.** Never add decompiled, disassembled or copied SOLIDWORKS code, SOLIDWORKS artwork, Dassault
  sample files, or `.SLDPRT` / `.SLDASM` / `.SLDDRW` saved by anyone. Format knowledge is written as facts in
  `docs/re/README.md` first, then implemented from those facts.
- **No secrets or personal data** in code, tests, logs or commit messages: no tokens, user names or absolute
  paths from your machine.
- **Never commit generated output**: `build/`, `dist/`, `.oswprt` files outside `tests/data/`, logs.

## Setup and checks

```bash
python -m venv .venv && .venv/bin/pip install -e .[dev]    # Windows: .venv\Scripts\pip
python -m pytest -q tests/test_<area>.py                   # the file for what you changed, while working
python -m pytest -q                                        # everything (about five minutes) before you finish
```

Tests run with `OPENSWX_ISOLATE=off` (set in `tests/conftest.py`); set `OPENSWX_LOG=off OPENSWX_AUTOSAVE=off
OPENSWX_PLUGINS=off` when you run the program from a script.

## How the code fits together

- Every action is a command: a function decorated with `@command` in `openswx/commands/*.py`, registered in
  `openswx/session.py`. The same registry feeds the UI (`POST /api/command`), the MCP server and the tests.
  Call commands in tests through `session.call(name, args)`.
- The package is grouped by domain: `core/` (document, features, sketch, assembly, geom), `commands/`,
  `exchange/` (SOLIDWORKS/Parasolid/DXF and other formats), `drawing/`, `analysis/` (FEA, CFD, tolerance,
  inspection, render), `manufacturing/` (CAM, plastics, routing, ecosystem), `system/` (settings, recovery,
  jobs, plugins, cache), `server/` (web/MCP/security) and `shell/` (CLI, desktop). `session.py`, `api.py`
  and `icons.py` stay at the top level because they are the shared entry points.
- A command's **docstring is its MCP tool description**. Write it for an agent that has never seen the code:
  what it does, what the arguments mean and their units (millimetres, degrees), and what it returns.
- After adding or changing a command run `python tools/gen_command_docs.py`. `tests/test_docs.py` fails if
  `docs/commands.md` or the README counts are stale, if a docstring is too short to describe the tool, or if
  no test names the command.
- MCP toolsets are defined by command `category` in `openswx/server/mcp_server.py` (`TOOLSETS`). A new category must
  be added to a toolset, or its commands are not reachable by agents outside `all`.
- The UI is plain ES modules in `openswx/web/js/` (no build step). `app.js` is the `App` core; the files in
  `js/mixins/` add methods to it, and `more.js`, `mfg.js`, `home.js` and `collab.js` do the same from `js/`.
  A mixin method must not reuse an `App` method name (`tests/test_web_mixins.py`). Styles live in
  `web/css/` (one file per widget group, imported in order by `style.css`). Icons are one plain SVG per
  pictogram in `web/svgs/`; `openswx/server/web_server.py` inlines them as the `#icons` sprite on the page, so
  `js/icons.js` needs no name list (just drop the file in).
- A new Python module must be listed in `hiddenimports` in `packaging/openswx.spec`, or the desktop build
  will not include it.
- Vendored JavaScript goes in `openswx/web/vendor/` with its licence file and an entry in `NOTICE`.

## Checking results

Compare against something independent: an analytic volume or area, a closed-form solution, or the body stored
in a SOLIDWORKS file. Don't assert a value that was copied from the code's own output. Geometry
comparisons use a relative tolerance (about 1e-6 for volumes).

For changes to `openswx/exchange/sw_rebuild.py`, `sw_sketch.py` or `sw_tree.py`, run the survey before and after
(`tools/survey.py run CORPUS -o before.jsonl --jobs 3`, then `compare`) and report parts gained and lost. The
corpus is the user's own SOLIDWORKS installation; the result files hold names and outcomes only.

## Working on an issue

Issues come from forms, so each has the same headings: *Reproduction as commands*, *Expected vs actual*,
*Acceptance checks*. A typical loop:

1. Turn the reproduction into a failing test (a `batch` JSON list maps directly onto `session.call` calls).
2. Fix the cause, not the symptom; keep the change to one topic.
3. Make the acceptance checks into assertions.
4. Run the area's tests, then the whole suite.
5. Open a pull request with the template filled in and `Fixes #N`.

CI runs on Linux, Windows and macOS. Batch your pushes; don't push once per small fix.
