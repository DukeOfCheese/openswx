# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change file
formats or commands; files are migrated automatically and the change is listed here).

## Unreleased

### Added
- Static, frequency, buckling and steady thermal studies (bonded assemblies too) mesh with curvature-based
  quadratic tetrahedra (Netgen, optional: `pip install openswx[mesh]`): peak stresses at holes and fillets
  within a few percent of closed-form results; frequencies with a consistent mass matrix. Each study takes
  `mesh=` tetrahedral, voxel or auto (tetrahedral when installed and the study allows it).
- About 70 more library materials: carbon, alloy, tool and stainless steels, aluminium, copper, nickel,
  magnesium and zinc alloys, engineering plastics, rubbers, composites, woods and ceramics.

## 0.1.0 (2026-11-23)

The first public version: parts, assemblies and drawings with a familiar layout; analysis, CAM and rendering;
SOLIDWORKS, Parasolid, STEP, IGES, STL, 3MF and DXF interchange; an MCP server for agents. See the
[README](https://github.com/DukeOfCheese/openswx#readme).

Highlights of the last pass before the release:

- Path-traced rendering in Render Tools (Quality: 64, 256 or 1024 samples), with progress and Cancel.
- macOS app (`openswx.app`) and `.pkg` installer for Apple silicon and Intel; Windows installer and portable zip;
  a release workflow that builds them all from a version tag.
- Python package for PyPI, built and checked on every tag (publishing it is not switched on yet).
- SOLIDWORKS parts: the other configurations are imported too.
- Help > Check for Updates looks at this project's GitHub releases by default.
- `tools/survey.py`: `--jobs N` rebuilds several SOLIDWORKS parts at once, and parts are keyed by their path in
  the corpus (sample folders reuse file names).
- Public repository files: issue forms, pull request template, contributing guide, security policy, code of
  conduct, notes for coding agents, and the demo video with its recorder (`tools/demo/`).
