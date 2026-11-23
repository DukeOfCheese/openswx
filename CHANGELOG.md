# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change file
formats or commands; files are migrated automatically and the change is listed here).

## Unreleased

### Added
- Path-traced rendering in Render Tools (Quality: 64, 256 or 1024 samples), with progress and Cancel.
- macOS app (`openswx.app`) and `.pkg` installer for Apple silicon and Intel; release workflow for Windows and macOS.
- SOLIDWORKS parts: the other configurations are imported too.
- `tools/survey.py run --jobs N` rebuilds several SOLIDWORKS parts at once.
- Public repository files: issue forms, pull request template, contributing guide, security policy, code of
  conduct, notes for coding agents.

## 0.1.0 (first release, not yet tagged)

The first public version: parts, assemblies and drawings with a familiar layout; analysis, CAM and rendering;
SOLIDWORKS, Parasolid, STEP, IGES, STL, 3MF and DXF interchange; an MCP server for agents. See the
[README](README.md).
