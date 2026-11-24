# Contributing to openswx

Thank you for helping. This file covers the rules everyone follows, how to report things so they can be
acted on quickly, and how to send a change. Much of the triage and implementation here is done with coding
agents. Each issue form asks for exactly what an agent (or a person) needs to reproduce and fix a problem
without a round of follow-up questions.

- [The clean-room rule](#the-clean-room-rule) (read this first)
- [Reporting: issues](#reporting-issues)
- [Labels](#labels)
- [Sending a change: pull requests](#sending-a-change-pull-requests)
- [Development](#development)
- [Security](#security)
- [Licence](#licence)

## The clean-room rule

openswx is compatible with SOLIDWORKS files and has a familiar layout. It contains none of SOLIDWORKS' code
or artwork, and it must stay that way. A pull request that breaks any of these rules is closed, whatever else
it contains:

- **No decompiled, disassembled or copied SOLIDWORKS code**, in any form: not translated, not paraphrased line
  by line, not as verbatim API metadata dumps.
- **No SOLIDWORKS artwork.** Icons and UI graphics are original.
- **No Dassault sample files**, in the repository, in issues or in pull requests. Dassault holds their copyright.
- **No `.SLDPRT` / `.SLDASM` / `.SLDDRW` files saved by a user** in the repository: they carry the Windows user
  name in their properties. Test data under `tests/data/sw/` is raw feature and partition streams that have
  been checked to be free of names and paths.
- Format knowledge goes in as **facts** in [`docs/re/README.md`](docs/re/README.md): what a record holds and
  how that was established. The implementation is written from those facts.
- openswx never depends on SOLIDWORKS. Only `tools/difftest/` talks to a licensed installation, as a
  development oracle; `test_no_solidworks_dependency` guards this.

## Reporting: issues

Open an issue with one of the forms at
[New issue](https://github.com/DukeOfCheese/openswx/issues/new/choose). Blank issues are turned off so that
every report has the same headings.

| Form | Use it for |
|---|---|
| **Bug report** | something gives a wrong result, an error or a crash |
| **SOLIDWORKS file** | a `.sldprt` / `.sldasm` / `.slddrw` that does not open, opens as an imported body, or comes in wrong |
| **Agent / MCP** | a tool that misbehaves for an agent: wrong result, unclear error, missing tool, confusing description |
| **Feature request** | something openswx should do; include a SOLIDWORKS equivalent if there is one |
| Question or idea | [Discussions](https://github.com/DukeOfCheese/openswx/discussions), not an issue |
| Security problem | [SECURITY.md](SECURITY.md); never a public issue |

What makes a report quick to act on:

1. **One problem per issue.** Two bugs in one issue get fixed at different times, and the issue can only be
   closed once.
2. **A reproduction as commands.** This is the most useful thing you can give. Every action in openswx is a
   command, so a reproduction can be data instead of a description:
   - for a saved part, the output of `openswx script part.oswprt` (the commands that built it), or
   - a JSON list for the `batch` command:
     ```json
     [{"name": "new_part", "args": {"name": "Repro"}},
      {"name": "create_sketch", "args": {"plane": "Top"}},
      {"name": "sketch_rectangle", "args": {"sketch": "Sketch1", "corner1": [0, 0], "corner2": [40, 20]}},
      {"name": "extrude", "args": {"sketch": "Sketch1", "depth": 6}},
      {"name": "fillet", "args": {"edges": ["Edge3"], "radius": 10}}]
     ```
   - or the smallest `.oswprt` that shows the problem (attach it zipped).

   Click-by-click steps in the UI are fine when the bug is in the UI itself.
3. **Expected vs actual, with numbers.** "Volume 4800 mm³, expected 4752.4 mm³" beats "the volume is wrong".
4. **The diagnostic report** for crashes and errors: *Help → Save Diagnostic Report* writes a zip with the
   log, version information and recent commands. It leaves your documents out unless you ask for them, but
   the log can show file paths, so look through it before attaching. Paste error text as text, not a
   screenshot.
5. **Version and platform**: from *Help → About openswx* (or `pip show openswx`), and how you run it
   (desktop app, `serve` in a browser, or `mcp`).

For SOLIDWORKS files, only attach files you made and have the right to share. Never attach the sample files
that ship with SOLIDWORKS, whose names and the output of `openswx sldinfo` are enough. A saved
SOLIDWORKS file contains your Windows user name; if that matters, describe the part instead.

### Writing a feature request that can be built

A good request reads like a small spec, the same shape as the items in [`docs/GAPS.md`](docs/GAPS.md):

- **Problem**: what you are trying to do, and what stops you today.
- **Behaviour**: what the command or UI should do: inputs, outputs, edge cases.
- **Acceptance**: checks that show it works, ideally ones a test can run ("a 10 mm cube with a 2 mm
  chamfer on one edge has volume 980 mm³").
- **Agent use**: whether the feature should be an MCP tool, and in which toolset.

## Labels

Every issue gets one label from each of the first two groups; the others are added during triage.

| Group | Labels |
|---|---|
| Type | `bug`, `feature`, `sw-compat`, `agent`, `docs`, `question` |
| Area | `area:sketch`, `area:features`, `area:assembly`, `area:drawing`, `area:simulation`, `area:cam`, `area:ui`, `area:mcp`, `area:files`, `area:packaging` |
| Priority | `P0` (data loss, crash, security), `P1` (wrong result, blocks common work), `P2` (workaround exists), `P3` (polish) |
| Status | `needs-repro`, `confirmed`, `in-progress`, `blocked` |
| Help | `good first issue`, `help wanted` |

The full list with colours is in [`.github/labels.yml`](.github/labels.yml).

## Sending a change: pull requests

- **One topic per pull request**, small enough to review in one sitting. Refactors go in their own PR.
- Link the issue (`Fixes #123`). For anything bigger than a bug fix, agree the approach in the issue first.
- **Tests come with the change.** Check results against something independent: an analytic volume or area, a
  closed-form solution, or the body stored in a SOLIDWORKS file. A test that only repeats what the code
  printed is not a check.
- A new or changed **command** needs: typed arguments, a docstring (it becomes the MCP tool description, so
  write it for an agent that has never seen the code), a test, a UI entry where one makes sense, and
  `python tools/gen_command_docs.py` re-run so `docs/commands.md` and the counts in the README stay current.
- Changes to the **SOLIDWORKS history rebuild** (`sw_rebuild.py`, `sw_sketch.py`, `sw_tree.py`) must include
  a survey comparison (`tools/survey.py compare before.jsonl after.jsonl`): parts gained and, especially,
  parts lost.
- Run the fast loop while working (`python -m pytest -q -m "not slow"`), then the full suite before pushing
(`python -m pytest -q -n auto --dist loadscope`). CI must pass on Linux, Windows and macOS, and the UI smoke tests.
- Fill in the pull request template; the checklist is the review checklist.

Commit messages: a short summary line in the imperative or as a noun phrase ("Path-traced rendering in Render
Tools"), a blank line, then what changed and why. Pull requests are squash-merged, so the PR title becomes the
commit summary.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e .[dev]      # Windows: .venv\Scripts\pip
python -m pytest -q                  # the whole suite, about five minutes
python -m pytest -q tests/test_sketch.py   # one file while you work
python -m openswx serve --open       # the UI against your working copy
```

The UI smoke tests need Playwright: `pip install playwright && playwright install chromium`, then
`python -m pytest tests/ui`.

How the code is organised: [`docs/STATUS.md`](docs/STATUS.md). Rules specific to coding agents:
[`AGENTS.md`](AGENTS.md).

## Security

See [`SECURITY.md`](SECURITY.md). Please report vulnerabilities privately, never in a public issue.

## Conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

Contributions are accepted under the Apache License 2.0 (see [`LICENSE`](LICENSE)). By opening a pull request
you confirm that you wrote the change, or have the right to submit it under that licence, and that it follows
the clean-room rule.
