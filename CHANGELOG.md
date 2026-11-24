# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change file
formats or commands; files are migrated automatically and the change is listed here).

## Unreleased

### Added

- A Home page when openswx starts without a file: New Part, New Assembly, Open, the recent files (a missing file
  is greyed out and can be removed from the list) and the tutorials. File > Home or Ctrl+Shift+H brings it back;
  Escape, any choice on it, or an agent at work hides it. User Settings > show_home turns it off
  (`OPENSWX_SHOW_HOME=0` for one launch).
- Commands `recent_files` and `clear_recent_files`: the last 15 documents opened or saved (`<home>/recent.json`).
- Opening or creating a document closes the empty Part1 made at start-up if it was never touched, so no stray
  Part1 is left behind.
- Tools > Customize Keyboard: change or clear the keys of 32 actions (views, zoom, undo, save, selection filters and
  more); clashes are flagged before saving, and the menus and the Task Pane show the keys in use.
- User Settings: graphics area background, body, edge, highlight and selection colours; a mouse preset
  (SOLIDWORKS-style, Onshape-style, Fusion-style or Blender-style buttons for rotate, pan and zoom); reversing the wheel.
- Sheet Metal > Rip: open a slot along the corner edges of a hollow part so it can be unfolded.
- Sheet Metal > Unfold and Fold: lay chosen bends flat, cut across them, and fold them back; the cuts are
  wrapped onto the bends. A cut made before a Sketched Bend across its line is wrapped the same way.
- Sheet Metal > Vent: an opening cut through the sheet from a sketched outline, with ribs left across it.
- The flat-pattern DXF has each bend's centreline (layer BEND), a note such as "UP 90° R2" (layer NOTES) and
  the bounding box with its size (layer BBOX).
- Keyboard navigation: Alt+T moves to the FeatureManager tree (arrows, Enter, F2, Shift+F10); F10 opens the menu
  bar (arrows, Enter, Escape).
- A dark theme for the interface, following the operating system's light / dark setting. The graphics area
  stays light in both.

### Changed

- A cleaner look for the same layout: tool buttons without outline boxes or gradients (hover lifts, the active
  tool is pressed in), ribbon groups and panes as rounded panels, pill-shaped ribbon tabs.

### Fixed

- Ctrl+Shift+Z (Previous View) ran Undo.
- Leaving the browser page with unsaved changes did not ask first.

## 0.2.0 (2026-11-23)

Simulation on curvature-based tetrahedra for every study, Claude Desktop working in the open window, about 100
library materials, and 584 of the 841 SOLIDWORKS sample parts rebuilding exactly (525 in 0.1.0).

### Added
- Static, frequency, buckling and thermal (steady and transient) studies (bonded assemblies too) mesh with curvature-based
  quadratic tetrahedra (Netgen, optional: `pip install openswx[mesh]`): peak stresses at holes and fillets
  within a few percent of closed-form results; frequencies with a consistent mass matrix. Each study takes
  `mesh=` tetrahedral, voxel or auto (tetrahedral when installed). Frictionless contact, springs and bolts
  between separate bodies, and the nonlinear, drop, harmonic and step-response studies, work on tetrahedra
  too.
- About 70 more library materials: carbon, alloy, tool and stainless steels, aluminium, copper, nickel,
  magnesium and zinc alloys, engineering plastics, rubbers, composites, woods and ceramics.
- The Windows and macOS desktop builds include the tetrahedral mesher; the release workflow checks it with a
  study in each built app (`packaging/smoke_study.py`).
- Release descriptions come from this changelog (`tools/release_notes.py`).
- `tools/survey.py run --parts LIST` surveys only the listed parts.
- Claude Desktop (and other chat apps that start local MCP servers) can drive openswx: `openswx mcp` relays to an
  openswx that is already open, so the agent works in that window and its edits show live. Help > Agent (MCP)
  Setup shows the exact entry for this installation; claude.ai on the web cannot reach a local app.

### Changed
- The menus (File, Edit, View, ...) are always shown in the title bar; the logo no longer toggles them.

### Fixed
- Intel macOS desktop build: its server could not start (cryptography, built from source there, clashed with
  the app's OpenSSL); cryptography now stays at a version with Intel wheels.
- SOLIDWORKS and Parasolid files: faces on a cylinder, torus or other periodic surface that a split line cuts
  along the seam were read with zero area, so the stored body's volume was wrong (part3: 512,932 mm³ instead of
  SOLIDWORKS' 448,221 mm³) and correct rebuilds were rejected.
- SOLIDWORKS feature history: when the finished body does not match the stored one, the history is rebuilt
  once more with the base feature tried and every later feature taken from the stored body, so the part opens
  exact with an editable base instead of only as an imported body (50 more sample parts).
- SOLIDWORKS feature history: the base feature is always tried, however long earlier steps took. Under load it
  could be imported once the trial time ran out, and an imported base feature fails the whole part.

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
