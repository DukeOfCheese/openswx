# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change file
formats or commands; files are migrated automatically and the change is listed here).

## Unreleased

## 0.1.0 (2026-11-23)

The first public version: parts, assemblies and drawings with a familiar layout; analysis, CAM and rendering;
SOLIDWORKS, Parasolid, STEP, IGES, STL, 3MF and DXF interchange; an MCP server for agents. See the
[README](README.md).

Highlights of the last pass before the release:

- Path-traced rendering in Render Tools (Quality: 64, 256 or 1024 samples), with progress and Cancel.
- macOS app (`openswx.app`) and `.pkg` installer for Apple silicon and Intel; Windows installer and portable zip;
  a release workflow that builds them all from a version tag.
- Python package for PyPI (`pip install openswx`), built and checked on every tag.
- SOLIDWORKS parts: the other configurations are imported too.
- Help > Check for Updates looks at this project's GitHub releases by default.
- `tools/survey.py`: `--jobs N` rebuilds several SOLIDWORKS parts at once, and parts are keyed by their path in
  the corpus (sample folders reuse file names).
- Public repository files: issue forms, pull request template, contributing guide, security policy, code of
  conduct, notes for coding agents, and the demo video with its recorder (`tools/demo/`).
