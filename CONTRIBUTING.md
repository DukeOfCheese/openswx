# Contributing to openswx

## Clean-room rule

openswx is compatible with SOLIDWORKS files and workflow, and contains none of its code. To keep it that way:

- **No decompiled, disassembled or copied SOLIDWORKS code**, in any form, including "translated" code and
  verbatim API metadata dumps.
- **No SOLIDWORKS artwork.** Icons and UI graphics are original.
- **No Dassault sample files** in the repository, and no `.SLDPRT` / `.SLDASM` / `.SLDDRW` saved by a user
  (they carry the Windows user name in `docProps`). Test data under `tests/data/sw/` is raw feature and
  partition streams that have been checked to be free of names and paths.
- Format knowledge goes in as **facts** in [`docs/re/README.md`](docs/re/README.md) (what a record holds and
  how it was established), and the implementation is written from those facts.
- openswx itself never depends on SOLIDWORKS. Only `tools/difftest/` talks to a licensed installation, as a
  development oracle; `test_no_solidworks_dependency` guards this.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e .[dev]      # Windows: .venv\Scripts\pip
python -m pytest -q
python -m openswx serve --open
```

- Every command is a function registered with `@command` in `openswx/session.py` (or `commands_*.py`). The
  same registry feeds the UI, the REST API and the MCP server, so a new command needs: the function with
  typed arguments and a docstring (it becomes the MCP tool description), a test, and a UI entry.
- Check results against something independent: an analytic volume or area, a closed-form solution, or the
  body stored in a SOLIDWORKS file.
- Changes to the SOLIDWORKS history rebuild (`sw_rebuild.py`, `sw_sketch.py`, `sw_tree.py`) must be compared
  with the previous survey result part by part; a gain on some parts often costs others.
- One topic per pull request.

## Security

The local server's rules are in `openswx/security.py`. Please report vulnerabilities privately to the
maintainers rather than in a public issue.

## Licence

Contributions are accepted under the Apache License 2.0 (see `LICENSE`).
