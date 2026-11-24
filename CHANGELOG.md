# Changelog

User-visible changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) (before 1.0, a minor version may change file
formats or commands; files are migrated automatically and the change is listed here).

## Unreleased

### Security

- The live-collaboration presence endpoint (`/api/collab/presence`) checks its arguments before sending and
  answers a failure with a fixed message; the details go to the log instead of back to the page
  (code-scanning alert 12).

### Changed

- In a cloud part, who is online is now a **People** pane in the Task Pane on the right, beside Agent activity,
  instead of a bar over the top-left of the view. Its tab shows only in a cloud part, with how many are in it.
  The pane lists everyone in their pointer colour, with their role, the tool they are using, the features they
  have open (locked for others) and whether they are idle. Click someone, or press Enter on them, to follow their
  view.

### Fixed

- Save on a document that has never been saved to a file opens Save As, as in other programs, wherever Save is
  started: the key, File › Save, the toolbar or the macOS menu bar. Cancelling leaves the document unsaved and
  shows no error, and later Saves go to the chosen file without asking.
  - Ctrl+S now works while you are typing in a text box. Before, in the desktop app it did nothing there.
  - A Save that fails in the desktop app (a folder that cannot be written, say) now shows the error. Before,
    nothing happened.
  - `save_part` with no `path` on a never-saved document returns an error that names the `path` argument.

## 0.4.1 (2026-11-24)

Faster throughout: pictures for agents about 15x quicker, the 3D view refreshes once per change from a cached
mesh, big cuts and sketches with many profiles several times faster, and frequency studies about twice as fast.
Home's Cloud tab and live collaboration are lighter on the app.

### Changed

- Home's Cloud tab shows what it last loaded straight away when you come back to it, then checks the server
  behind it and redraws only what changed. Who is in each part refreshes every 10 seconds while the tab is open.
  When the server cannot be reached, the last lists stay up with an "Offline" note.
- Live collaboration is lighter:
  - Commands that only talk to the server (projects, the team, history) no longer hold openswx up while they wait.
  - Requests to the server reuse one connection.
  - Opening a project's parts takes one request instead of a listing of every organisation.
  - Your pointer reaches the others even while a rebuild is running.
- Feature previews (yours, and others' drafts in a cloud part) reuse the part's rebuild cache: they start from the
  feature being changed, not from scratch. On a 26-feature part a preview takes about a quarter of the time.
- Faster throughout, with the same results (each checked against the old way):
  - Pictures for agents (`get_view_image`) about 15x faster: 1.5 s to 0.1 s for an 80-feature part.
  - The 3D mesh is made for the whole part at once (2-3x faster) and kept until the part changes. The window
    fetches it once per change instead of twice, and it travels gzip-compressed.
  - Volume and area were recomputed on every model summary (their cache never hit).
  - Frequency and buckling studies factorise the stiffness matrix once (16 s to 9 s at 47,500 unknowns).
  - Sketch regions are found in numpy: a sketch with 60 circles in 0.6 s instead of 2.8 s.
  - Edge selections look up the faces beside each edge in a map instead of scanning every face.
  - A cut with many profiles is one boolean: 60 holes in 0.9 s instead of 4.0 s.

### Fixed

- A cloud part was checked for unsaved changes after every command, although it is never unsaved.
- The large imported bodies kept for a cloud part had no limit; at most 256 MB is kept now (anything older is
  fetched again when needed).

## 0.4.0 (2026-11-24)

Live collaboration: edit a part with your team at the same time through an openswx-collab server, with cloud parts
and team management on Home's Cloud tab, and others' cursors live in the view.

### Added

- Live collaboration (optional, `pip install openswx[collab]`, with an
  [openswx-collab](https://github.com/DukeOfCheese/openswx-collab) server): several people edit one part at once.
  Each change goes to the server as it is made, and the others see it straight away. A sketch or feature you have
  open is locked for the others. Undo takes back only your own changes. History, named versions and restore are
  there too.
- Home's Cloud tab: sign in (email and password, Google or GitHub), then browse your organisations, projects and
  their parts, with who is in each part right now. New Cloud Part makes a part straight on the server, so anyone
  in the project can open it at any time, whether or not you are online. The team list (members, roles,
  invitations, who can open each project) is there too. You can also join with an invitation code. The "who is
  here" avatars need openswx-collab 0.1.1 or newer; against 0.1.0 they stay empty.
- New Part asks Local or Cloud once a collaboration server is set up, and remembers the last choice
  (`new_part_location`).
- Others' pointers are arrows in their colour with a name bubble that glide to where the person points, also over
  empty background.
- Commands `collab_new_part`, `collab_create_org`, `collab_members`, `collab_invite`, `collab_cancel_invite`,
  `collab_set_role`, `collab_remove_member`, `collab_accept_invite`, `collab_project_members`,
  `collab_set_project_role`, `collab_remove_project_member`, alongside the other `collab_*` commands.

### Changed

- A cloud part is never "unsaved": the server keeps it. Save on one says so, and Save As writes a local copy
  (the part stays the cloud part). A cloud part opens only while the server can be reached.
- The Collaborate menu has only what belongs to the open document: Move to Cloud, Stop Sharing, History,
  Name Version and Follow. Signing in, opening and the team are on Home's Cloud tab.

### Security

- Passwords, invitation codes and tokens given to a command are never written to the log.

## 0.3.1 (2026-11-24)

The macOS app uses the system menu bar and no longer hangs on quit; the Home page shows first at start-up; agent
setup shows your token and the Windows installer can put openswx on PATH; tidier agent notifications.

### Added

- macOS desktop app: File, Edit, View, Insert, Tools, Window and Help are in the system menu bar at the top of the
  screen instead of the window (Edit has the standard Cut, Copy, Paste and Select All). Elsewhere, or if the menu
  bar cannot be used, the window keeps its own menus.
- Help > Agent (MCP) Setup shows your token (Show token / Copy command) and the full command that prints it.
- The Windows installer can add openswx to PATH (ticked by default), so `openswx-cli token` works in a terminal;
  uninstalling removes it again.

### Changed

- Help > Agent (MCP) Setup: every command and config snippet has a Copy button (the Claude Code command is copied
  with your token filled in, though it is shown masked), and the Claude Desktop path is Settings > Desktop App >
  Extensions > Manage > Edit Config.

### Fixed

- The CommandManager keeps one height on every tab: the tabs and the workspace no longer move up and down when you
  switch tabs (a tab with four buttons in a stack, a three-line label, or a horizontal scrollbar made it taller).
- The Home page is the first thing the window shows at start-up, not the empty graphics area with the planes and
  triad. New Part, New Assembly, Open or a recent file keep Home up until the document is loaded, so it goes
  straight to the document with no flash of the empty part (cancelling Open stays on Home).
- macOS: quitting with unsaved changes hung the app (the "Close without saving?" question waited on the thread
  that had to show it).
- Agent notifications showed raw markup on failures; they now read cleanly with readable command names, and none
  appear while the Agent activity pane is open (a dot on its tab marks new activity instead).
- Dialogs no longer need sideways scrolling: long commands, JSON and paths wrap.
- Some chat apps (which show the tools but not the server's instructions) still called the program SOLIDWORKS:
  the first tools an agent uses now say openswx is not SOLIDWORKS.

## 0.3.0 (2026-11-24)

A new look with a dark theme, a Home page with recent files, customisable keyboard shortcuts, mouse presets and
view colours, and sheet metal Unfold / Fold, Rip and Vent with bend notes in the flat-pattern DXF.

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
- Agents called the program SOLIDWORKS: the MCP instructions now say it is openswx (and not SOLIDWORKS), and tool
  descriptions no longer compare themselves with SOLIDWORKS tools.

### Security

- Files sent to or from the browser go into a private temporary folder under a cleaned name (no path parts).
- Command arguments are checked before a command runs; problems are reported in words, and an error inside a
  command no longer sends the interpreter's message to the client.
- Workflows run with read-only repository access unless a job needs more.

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
