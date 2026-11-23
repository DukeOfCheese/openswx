# openswx build plan (living document — update checkboxes as work lands)

> This file is the build log. For the current state see [STATUS.md](STATUS.md); for what is open, [GAPS.md](GAPS.md).
> The Architecture section below describes the first layout, not today's files.

Goal: clean-room, open-source parametric CAD that replicates the SOLIDWORKS look, feel and core
part-modeling features, with a built-in MCP server so agents can drive it, plus a differential
test harness that compares results against a licensed SOLIDWORKS via its public COM API.

Rules: no decompiled/copied SOLIDWORKS code or artwork. UI is recreated from observed behaviour;
icons are original. Interop facts come from docs/re/README.md.

## Architecture

```
openswx/
  sketch/solver.py      constraint solver (scipy least squares, DOF/status via Jacobian nullspace)
  sketch/model.py       sketch entities, relations, dimensions, profile (wire/face) extraction
  model/refs.py         geometric references to faces/edges (point+normal signatures, survive rebuild)
  model/features.py     feature types: Sketch, Extrude (boss/cut), Revolve (boss/cut), Fillet,
                        Chamfer, Shell, Hole, LinearPattern, CircularPattern, Mirror, RefPlane, Import
  model/document.py     PartDocument: tree, rebuild, rollback, suppress, errors, undo/redo, save/load
  geom/tess.py          tessellation -> mesh JSON (per-face ids, edges) for the viewer
  app/commands.py       single command registry (used by REST + MCP + tests)
  app/session.py        Application: documents, active doc, event bus
  server/web.py         FastAPI: static UI, /api/command, /api/doc, /api/mesh, SSE /api/events
  server/mcp_server.py  FastMCP tools wrapping the command registry (stdio + streamable HTTP)
  io/sldprt.py          SOLIDWORKS container reader (streams, preview, properties, Parasolid XT)
  io/exchange.py        STEP/STL export, STEP import
  web/                  SOLIDWORKS-style UI (three.js): menu bar, CommandManager, FeatureManager,
                        PropertyManager, heads-up view toolbar, confirmation corner, sketcher, status bar
tools/difftest/         same cases on openswx and SOLIDWORKS (COM), compare mass properties
tests/                  pytest
```

Conventions (match SOLIDWORKS): mm (MMGS) in UI/MCP; Front=XY(+Z), Top=XZ(+Y), Right=YZ(+X);
feature names `Boss-Extrude1`, `Cut-Extrude1`, `Sketch1`, `Fillet1`...; end conditions by name or
swEndConditions_e integer (Blind=0, ThroughAll=1, MidPlane=6, ThroughAllBoth=9).
Sketch colours: blue under-defined, black fully defined, red over-defined/unsolved.

## Milestones
- [x] M1 core: solver, sketch, features, document (files: sketch.py, features.py, document.py, geom.py)
- [x] M2 command registry + session + save/load/export/import (session.py; render.py = headless PNG)
  - verified analytically: extrude/cut/fillet/revolve/circular pattern volumes exact; edit+undo ok
  - topological naming: refs = point+normal/tangent+type, fallback to nearest same-type aligned entity
- [x] tests/test_core.py + tests/test_server.py + tests/test_web_syntax.py (pytest): 169 tests
- [x] M3 MCP server (mcp 2.3 `MCPServer`; 50+ tools from COMMANDS; get_view_image returns PNG; resource openswx://model)
- [x] M4 web server + UI written (web_server.py; web/{index.html,style.css,icons.js,viewport.js,propman.js,sketcher.js,app.js});
      backend extras: /api/preview (S.preview clones doc), redefine_feature (Edit Feature), origin tagging ui/agent,
      sketch_set_construction. NEXT: run `python -m openswx serve`, test in browser, fix bugs.
- [x] M5 SLDPRT container reader (openswx/sldprt.py; `python -m openswx sldinfo FILE --extract DIR`)
- [x] M6 differential harness (tools/difftest/difftest.py --solidworks): 7/7 cases match SOLIDWORKS 2025
      (volume/area ~1e-15 rel). COM notes: late-bound zero-arg methods are property reads (call0 uses
      inspect.ismethod); SelectByID2 only picks visible entities -> use SelectByRay for edges.
- [x] tests: 21 pytest (tests/test_core.py, tests/test_server.py) all passing
- [x] tools/agent_demo.py: MCP-over-HTTP agent builds bracket; volume matches analytic exactly
- [x] M7 README/pyproject/.gitignore; UI walkthrough verified in Chrome: Extrude via plane-pick→sketch→PM,
      face mini-toolbar, edge pick→Fillet PM with preview, line chain + H/V inference + loop close, Smart
      Dimension + Modify box, Edit Sketch dims, Edit Feature (redefine), Cut-Extrude, Mass Properties,
      agent activity pane with live MCP updates.

## Phase 2 (in progress) — close the "not done" list
- [x] P2.1 end conditions UpToVertex / UpToSurface / OffsetFromSurface / UpToNext (features.py build_extrude)
- [x] P2.2 Swept boss/cut, Lofted boss/cut, Draft (consumed_sketches() in document.py handles path/profiles)
- [x] P2.3 sketch tools: offset/mirror/fillet (sketch.py; cmds sketch_offset/sketch_mirror/sketch_fillet).
      Solver: over_defined only when constraints fail; redundant dims (no DOF drop) -> driven. UI buttons TODO.
- [x] P2.4 equations.py (Evaluator, SW syntax "D1@Sketch1", FEATURE_DIMS D1..), document.variables /
      configurations / active_config; cmds set_global_variable, set_equation, clear_equation, get_equations,
      add/activate/delete_configuration, set_configuration_value, suppress_feature(configuration=). UI TODO.
      25 pytest passing.
- [x] P2.5 assemblies backend: assembly.py (AssemblyDocument, Component x=rotvec+t, mates LSQ, DOF/component,
      linked parts via live_docs, .oswasm). session: APP.active_doc / APP.doc (part only) / APP.asm; cmds
      new_assembly, insert_component, add_mate, edit_mate, delete_mate, move_component, fix_component,
      delete_component, check_interference. Face ids in assemblies: 'Comp-1/Face3'. UI TODO.
- [x] P2.6 drawings: drawing.py make_drawing (third/first angle, iso w/o hidden lines, overall dims, title
      block) -> cmd create_drawing, GET /api/drawing. Verified visually (headless Chrome screenshot).
- [x] P2.7 SOLIDWORKS bridge (removed 2026-10-20: openswx must not depend on SOLIDWORKS; .sldprt/.sldasm are read natively). Was: OpenDoc + SaveAs3 STEP; open_part(.sldprt)
      imports geometry; tutorial part matches SW mass props within 2e-5.
- [x] P2.9 UI for phase 2 (done in phases 3-4): buttons (sweep/loft/draft, offset/mirror/sketch fillet), up-to selection in extrude PM,
      Equations dialog, ConfigurationManager pane, assembly mode (tree, Insert Components, Mate PM,
      interference), Make Drawing menu. preview() now clones any document type.
- [x] P2.8 tests: 27 pytest (incl. up-to, sweep/loft/draft, sketch tools, equations/configs, assembly mates,
      drawing, REST assembly/drawing); difftest 7/7 vs SOLIDWORKS after phase 2; 80 MCP tools; README updated.
- [x] P2.9 UI written (CommandManager sweep/loft/draft/equations/offset/mirror/fillet/drawing, Up-To fields +
      vertex picking in extrude PM, Equations dialog, ConfigurationManager pane, assembly tree/Insert
      Components/Mate PM/Interference). JS syntax-checked with node; NOT yet clicked through in a browser
      (server was stopped by the system for low memory — restart only when the user asks).

## Phase 2 UI verification (done, in Chrome, server restarted with user's OK)
Sweep PM (preselected profile/path, preview, OK), Equations dialog (global var + equation drives D1), Configuration
pane (add config, per-config suppress, double-click switch back), assembly mode (Insert Components dialog, (f)/(-)
tree prefixes, Mate PM face picks + preview, Interference), drawing of assembly, sketch Offset/Mirror/Fillet buttons.
Fix found: anti-aligned mates need flips -> assembly.solve retries with 180-degree seeds.

## Phase 3 (in progress)
- [x] P3.1 sketch trim/extend (sketch.trim/extend, cmds sketch_trim/sketch_extend, UI tools trim/extend)
- [x] P3.2 Up To Surface on curved faces (_extrude_to_curved_face via Shape.split(face, Keep.ALL))
- [x] P3.3 drawing: SECTION A-A (hatched cut faces) + cutting line + hole callouts — verified visually
- [x] P3.4 mates: tangent, lock (stored relative pose), width (4 faces) + Mate PM options
- [x] P3.5 sheet metal: base_flange, edge_flange (exact bend geometry), flatten (BA = phi*(r+K*t)),
      sheet_metal_info — exact-volume test. UI: Sheet Metal tab (Base Flange, Edge Flange, Flatten) verified in Chrome.
- [x] P3.6 surfaces: extruded_surface (ctx.surfaces, shown in viewport), thicken; Surfaces tab.
- Phase 3 totals: 32 pytest, difftest 7/7 vs SOLIDWORKS, 88 MCP tools.
- [x] P3.7 weldments: structural_member (square/rect tube, pipe, angle, bar along sketch lines); gear mate +
      rotate_component; drawings: DETAIL B (2:1, clipped) + BOM table for assemblies (verified visually).
      33 pytest, 90 MCP tools.
- [x] P3.8 weldment cut list (weldment_cut_list), BOM balloons on assembly drawings; UI: Weldments tab
      (Structural Member PM, Cut List), Gear option in Mate PM, Rotate Component (component context menu) —
      verified in Chrome (frame + cut list 100x2/200x2; gear 90 deg on A -> -45 deg on B). 91 MCP tools.

## Phase 4 — desktop .exe + feature parity backlog (user: "should become an .exe", "build out all the other features")
Desktop: openswx/desktop.py (pywebview native window + embedded server/MCP, native file dialogs via
window.pywebview.api: open_file/save_as/export/save_drawing/save_image); `openswx` with no args = desktop;
packaging/{entry.py,make_icon.py,openswx.spec} -> dist/openswx/openswx.exe (windowed) + openswx-cli.exe (console,
for `openswx-cli mcp` stdio + sldinfo). Status: [x] built and verified — openswx.exe opens window "openswx",
in-process engine (extrude volume exact), MCP initialize 200. Spec must collect all build123d deps (IPython
not excludable; lib3mf dll etc). dist ~400 MB. TODO: zip/installer (Inno Setup), file association .oswprt.

Backlog in priority order (tick as done; each needs backend cmd + MCP + UI button/PM + test):
- [x] B1 (backend+tests) rib, hole_wizard (ISO M2-M24: clearance/tapped/counterbore/countersink, features.ISO_HOLES),
      thread (helix sweep ISO 60°), combine_bodies/delete_body/move_copy_body/scale_body (feature type body_op).
      Also fixed Sketch.profiles: plane split by all curves + even-odd regions (shared edges were broken).
      UI for B1 done (Hole Wizard/Thread verified in Chrome against the exe's server; Rib/Dome PMs; Insert>Body
      dialogs). Also: dome (spherical cap), ANSI inch hole sizes (features.ANSI_HOLES). 35 pytest.
      + variable_fillet (BRepFilletAPI linear radius). Rib/Dome PMs click-tested (rib +337.5 exact, dome exact),
      Hole Wizard lists 20 sizes. Exe freeze (exe window + Chrome both connected, low RAM): commands now wait at
      most 30 s for the lock (busy error instead of hang), APP.busy + lock-free GET /api/health name a stuck
      command. 36 pytest. Exe rebuilt + relaunched.
      B1 complete: Wrap (emboss/deboss/scribe onto cylinders via pcurves on the cylindrical surface), Face Fillet,
      Full Round Fillet, Vertex Chamfer (viewport vertex picking added), Helix (B4 pass). Exact-volume tests. Then B2..B11.
- [x] B1 (orig) Part features (all in features.py / session.py): Rib, Hole Wizard ISO/ANSI size tables (M2-M24 clearance/tap, cbore/csk std),
      Helix/Spiral curve + threads (cosmetic + modelled), Dome, Wrap, Combine/Delete-Keep/Split/Move-Copy bodies,
      Mirror body (exists), Scale, Variable/face/full-round fillet, vertex chamfer
- [x] B2 Patterns: curve-driven, sketch-driven, table-driven (dX,dY,dZ rows), fill pattern (square/staggered, margin, angle) — commands, PM panels, icons, exact-volume tests (fill: 45 holes on 100x60 face)
- [x] B3 Reference geometry: Axis (edge/cyl face/sketch line/two planes/two points), Point (coords/arc centre/face centre/offset), Coordinate System (origin + X/Y; usable as sketch plane + table-pattern frame); drawn in viewport (dash axis, point star, RGB triad), pickable, previewed, hide/show; axes accepted by linear/circular pattern. Mates still use part faces/edges only.
- [x] B4 Sketch: ellipse (solver var b), spline (open/closed, through points), sketch text (font outlines,
      counters as holes), linear/circular sketch pattern, Convert Entities (faces/edges -> fixed geometry),
      Intersection Curve (body section), 3D Sketch (line chains/splines in XYZ, usable as sweep path).
      Also Helix/Spiral curve (B1 item). Sketch tools, toolbar, dialogs, viewport curves; 4 new tests.
      NOTE: `node --check x.js` silently passes ES modules with syntax errors -> tests/test_web_syntax.py
      checks .mjs copies instead.
- [x] B5 Material library (26 materials: density, E, nu, yield/UTS, k, colour; materials.py), Appearance (part/feature/face colours, face > feature > part > material), custom properties (title block TITLE/DWG NO./DRAWN), document units setting (stored + status bar only so far; dimension/value display conversion still TODO; API stays mm). Dialogs + toolbar.
- [x] B6 Assembly: linear/circular component patterns (driven by seed), Mirror Components (opposite hand,
      driven), subassemblies (.oswasm / open assembly as rigid component), exploded views (steps, explode/
      collapse), motion study (drive a distance/angle mate, poses per frame, UI playback), Toolbox hardware
      (ISO 4017/4762/4032/7089 M3-M24). Limitations: mates on pattern instances use the instance pose from the
      previous solve; no "smart" auto-sizing of fasteners to holes yet; no motion export to video.
- [x] B7 Drawings: in-app sheet editor (web/drawing.js): drag views (projected views stay aligned), notes
      (multi-line, leaders), datum feature symbols, GD&T feature control frames (14 characteristics, dia zone,
      MMC/LMC, 3 datums), Model Items (driving sketch dims projected into the matching view), sheet size /
      projection / scale; export PDF (own writer, no deps; GD&T glyphs -> ASCII fallback in base-14 font),
      DXF (ezdxf, layers), SVG. Drawing state saved in .oswprt/.oswasm. MCP: drawing_* + export_drawing.
- [x] B8 (part) Sheet metal: Hem (closed/open), Sketched Bend (volume-conserving, flat pattern unbends);
      Surfaces: revolved/swept/lofted/planar/filled (N-sided, non-planar)/offset/knit (+form solid)/trim;
      Weldments: End Cap, Gusset. Exact-value tests for each.
- [x] B8 remainder (closed in gap-closing passes 2-3): miter flange, jog, corner relief, forming tools, boundary surface (= loft today),
      weldment corner trim (members currently fuse at corners)
- [x] B9 Evaluate (evaluate.py): Section View (clipping plane + capped cross-section, live offset slider,
      section area / section_properties), Draft Analysis (positive/negative/requires/straddle face colours),
      Curvature (per-vertex max principal curvature colour map), Zebra Stripes (striped matcap), Check
      (BRepCheck, open shells, short edges, min radius), multibody Body Interference. Assembly interference
      already existed.
- [x] B10 Simulation (fea.py, clean-room): linear static on a voxel hex mesh (columns ray-cast through the
      B-rep), 8-node hexes with Wilson-Taylor incompatible modes (no bending lock; one shared 24x24 Ke ->
      vectorised assembly), SuperLU with MMD_AT_PLUS_A ordering (10x faster than default) / CG fallback.
      Fixtures, forces (vector or normal), pressure; results: max displacement, von Mises, FoS vs library yield,
      reactions; UI Simulation tab: stress/displacement plots on an element-size refined display mesh, colour
      legend, exaggerated deformed shape. Validated: cantilever deflection within 0.5% of beam theory.
      Limits: stair-stepped boundaries (stress at curved faces is approximate), linear static only, single
      body, no contact/bolts/remote loads.
- [x] B11 (part) DXF: import into a sketch (LINE/CIRCLE/ARC/(LW)POLYLINE bulges/SPLINE/ELLIPSE/POINT, merged
      end points), sketch -> DXF, sheet-metal flat pattern -> DXF (developed length verified), drawing -> DXF/PDF
      (B7). File menu items with native dialogs in the exe.
- [x] B11 remainder (reader): native Parasolid XT reader (openswx/parasolid.py, written from the published
      2006 XT Format Reference): text .x_t, neutral/bare binary .x_b, embedded schema edit scripts (V14+),
      partition files; analytic, B-spline, swept, spun, offset surfaces, B-/SP-/trimmed/intersection curves,
      void shells; metres -> mm. .sldprt geometry is read natively from Config-<n>-Partition (no SOLIDWORKS). Writer: see pass 7.
      Verified against SOLIDWORKS 2025 (Parasolid 36.1): 16/16 parts (incl. a B_SURFACE with intersection
      edges) exact in volume/area from .x_t, .x_b and
      .sldprt. Writer and blends: see passes 7-8.
## Porting strategy (parked 2026-11-23)
> The owner's reason for the port was to run on macOS as well. The Python application already does: the same
> code is packaged for macOS by packaging/build.py (openswx.app and a .pkg, Apple silicon and Intel), with
> nothing Windows-only in the engine. A C++ core is therefore not needed for that goal and is not scheduled.
> If start-up time or solver speed call for it later, single hot loops move to compiled extensions, measured
> with tools/bench.py and checked with tests/conformance. The rules below are kept as the record of the
> earlier direction (the conformance cases and the frozen command contract are in place regardless).

Earlier direction: develop in Python, then port to C++.
Python = executable specification; C++ = shipping core. Rules from now on:
1. Kernel stays OpenCASCADE (build123d/OCP calls map ~1:1 to OCCT C++), so geometry needs no re-design.
2. Language-neutral conformance tests: tests/conformance/*.json = command sequences + expected volume/area/topology;
   runnable by Python and C++ (and difftest vs SOLIDWORKS). Convert key pytest cases to this format.
3. Frozen contracts: command names/args (session.COMMANDS = MCP tools), .oswprt/.oswasm JSON, REST/SSE API.
4. Core logic avoids Python-only tricks; numerics via patterns portable to Eigen (scipy LSQ -> Eigen/LM).
5. Port order via pybind11 modules, tests green after each: (1) sketch solver, (2) mate solver, (3) tessellation/mesh
   streaming, (4) rebuild engine + document model, (5) command layer + MCP, (6) app shell (C++ + WebView2/Qt
   WebEngine hosting the same web UI). UI (HTML/three.js) is not ported.

## Next steps (not done) - updated after the third gap-closing pass
Closed in passes 2-3: Jog, Miter Flange (edge chains, mitred corners), Corner Relief (manual + all corners),
forming tools Dimple / Louver / Bridge Lance, Boundary Surface, weldment end miters, symmetric/slot/limit/cam/
path mates, Smart Fasteners, motion export AVI (MJPEG) + GIF, units in the UI, finite-cell FEA (round
cantilever within 2%), steady-state thermal study (exact vs 1D conduction / series resistance), bonded
multi-material assembly simulation (springs-in-series within 3%), tributary-area load distribution, DWG via
the ODA File Converter, difftest vs SOLIDWORKS 11/11.
Pass 4 (simulation): frequency study (lumped mass, first cantilever mode within 0.2%), linear buckling
(geometric stiffness from static stresses, Euler column within 0.15%), spring and bolt connectors (EA/L,
GA/L, preload), frictionless no-penetration contact (per-body meshes + compression-only active set; axial
result within 0.1%), UI for all of these. Parasolid: user approved the 2006 XT reference (archived copy);
SOLIDWORKS-exported .x_t/.x_b test corpus with ground truth via tools/difftest/xt_samples.py (Parasolid v36
files with embedded schema deltas).
Pass 5 (interop): native Parasolid reader (.x_t/.x_b) and native .sldprt geometry (embedded Parasolid
partition) - open_part / import_file / insert_component, no SOLIDWORKS required; 15/15 SOLIDWORKS 2025 parts
exact (torus, sphere, cone, B-spline extrude/revolve included). Mass properties integrate freeform faces
adaptively (SOLIDWORKS parity on B-spline parts).
Pass 6: transient thermal (BDF2 time stepping, lumped capacity from the new specific-heat material column;
lumped-capacitance cooling within 0.05% at 50 steps; study options + temperature-vs-time chart in the UI),
forming tools Extruded Flange (flanged hole) and Rib (embossed bead), miter-flange profiles bent with the sheet
bend radius (inner r, outer r + t; exact volumes), B_SURFACE + INTERSECTION-curve parts read exactly (16/16).
Pass 7: Parasolid XT writer (openswx/parasolid_write.py; export_file format x_t, File > Export Parasolid):
base schema 13006 text files, analytic + B-spline geometry, seams/degenerate edges dropped, cavities and
multiple lumps. tools/difftest/xt_roundtrip.py imports the written files into SOLIDWORKS 2025: 23/23 match
(primitives, cavity, two lumps, B-spline loft, and all 13 difftest parts built with openswx commands).
Pass 8: reader handles SOLIDWORKS rolling-ball fillets on freeform faces: BLENDED_EDGE = exact tube around
the spine (spine = intersection of the offset supports on naturally extended B-surfaces), BLEND_BOUND contact
curves = spine feet on the supports, intersection curves oriented by chart order; fillet between a plane and a
B-surface matches SOLIDWORKS volume within 1.4e-5 (17 SOLIDWORKS parts in all).
Pass 9: native .sldasm: the published XML summary swXmlContents/COMPINSTANCETREE gives component files,
configurations and 4x4 transforms (row-major; verified: reproduces SOLIDWORKS' stored assembly bounding boxes
exactly on sample assemblies, column-major is off by up to 87 mm); parts come from their embedded Parasolid
data; subassemblies (inline or separate files) are flattened. Mates: Config-<n>-MatesList gives each mate's
class, name and (face reference, component) pairs; references resolve through the FACE_ID_2001 attribute
SOLIDWORKS puts on every Parasolid face (records of code, feature id, time stamp, 0, payload; matched by
feature id + payload, shortest id wins). Coincident/concentric/parallel/perpendicular/tangent/distance/angle
are rebuilt (alignment and values measured at the saved pose) and mated components are freed when solving
keeps every part in place; otherwise they stay fixed. Pass 10: component reference planes are mate entities
('Comp/Front Plane', also in the Mate PropertyManager); SOLIDWORKS plane mates import through them, mates
anchoring a component to the assembly's planes keep it fixed; edge/point mates are detected (non-parallel
faces at the saved pose) and reported; every imported mate is checked to hold at the saved pose; untagged
mates (MFC class written once) take their type from the default name; the reader projects vertices robustly
at curve ends (two pliers parts that failed now read). Measured on 40 SOLIDWORKS sample assemblies with the final code: 101 of
186 mates imported (54%); about 25 more anchor parts to the assembly planes (kept fixed); the rest are
reported (27 on edges/points, 18 unsupported types such as in-context/width/gear, 10 unresolvable faces).
Still open:
- PE (foreign-geometry) surfaces in the reader (need the third-party evaluator; not possible in principle);
  SOLIDWORKS mates on other reference kinds (pattern / mirror / sweep faces, edges, planes) and mate types
  (width, gear, cam, ...): reported as not imported
- (done, pass 11) corpus run over the 841 installed SOLIDWORKS sample parts, single-process and resumable:
  814 read (96.8%), 736 valid solids. Fixes it drove: periodic B-surfaces, closed intersection curves,
  SP-curve edge trimming, helix schemas 163/184, multi-chunk partitions, LocalBodies fallback, null knots,
  C0 offset bases, inside-out solids, a sanity check that rejects runaway geometry, sheet volumes = 0.
  Remaining: 9 timeouts, 6 rejected runaway bodies, a dozen edge cases.
- (done, pass 11) native assembly open speed: rebuilt parts cached per file version, face-id geometry
  computed lazily (only faces that mates name), mate entities cached per component body, summary volume
  from fixed-order integration (exact values via get_mass_properties): pliers 55 s -> 15 s.
- (done, pass 11) native reads checked against SOLIDWORKS' own mass properties on 40 random sample parts
  (opened read-only via the API): 37 within 1e-4 in volume, 38 within 1e-3 in area; two gross misses were the
  wrong configuration, fixed by defaulting to the active one (swMostRecentConfiguration -> Config-<swID>);
  three stay 0.01-0.09% off (geometry as saved vs SOLIDWORKS' rebuilt model, unexplained).
- (done, pass 9) miter-flange flat pattern: one strip per chain edge, developed width = straight parts + BA per
  profile bend (K = 0.5 flattening preserves volume exactly); corners left open (relief)
- Persistent naming beyond geometric signatures (feature-generated face ids)

## Parity roadmap (2026-10-24, user: "this app needs to be completed as a solidworks alternative completely")
Audit: 197 commands (35 features, 33 sketch, 20 assembly, 16 sheet metal, 11 surfaces, 19 simulation, 9
drawing, ...), 182 reachable from the UI (the rest are query / API commands for agents). Gaps against
SOLIDWORKS, in the order they will be worked:
- [x] R1 Drawings as documents (2026-10-24; openswx/drawing_sheets.py). Sheets with model / projected /
      section / detail / auxiliary views placed by clicking, view properties, cascading delete, multi-page PDF
      (part 1). Smart Dimension in views: drawing_pick / drawing_add_dimension (auto: horizontal / vertical /
      aligned by placement, diameter / radius; picks snap to corners and face-on circles; values are model
      sizes, also across breaks; dimensions follow the model). Crop View, Break View (the band is closed up by
      a warp in placement, so view coordinates stay model coordinates), Broken-out Section (cut to a depth,
      floors hatched). Hole Table (internal cylinders seen end-on, tagged by size, THRU / depth, from a picked
      origin). BOM editing (part number, description, item order), Title Block fields, tables draggable.
      UI driven end to end in headless Edge over CDP; tests test_drawing_sheets_and_views,
      test_drawing_break_crop_broken_out, test_drawing_hole_table_bom_title_block.
      Drawing documents (2026-10-24): save_drawing_document writes .oswdrw (the drawing + the model's path
      relative to it, like .slddrw); opening one opens / reuses the model and shows the drawing; file type
      registered (test_drawing_document). DWG export: each sheet's DXF converted by the free ODA File
      Converter when it is installed, else a clear message (test_drawing_dwg_export).
      Annotations (2026-10-24): angle between edges (the sector the placement is in), distance between
      parallel edges, Hole Callout (size + THRU / depth), automatic center marks and centerlines (view
      options), balloons numbered from the BOM (manual: the component under the click by ray casting along
      the view; Auto Balloon: one per BOM item, around the view; they move with the view). Test
      test_drawing_annotations; tools driven in headless Edge.
- [x] R2 Part modelling (2026-10-24). Done: Split (plane / surface; all pieces or one side), Cut With
      Surface, Delete Face (patch via OCC defeaturing, or leave a surface body), Move Face (per-face offset,
      neighbours extended), Replace Face (faces pushed past the surface, trimmed back to it), curves
      (projected, composite, through XYZ points; usable as sweep paths), sweep twist and guide curve (a
      smooth loft through transported / turned / scaled sections: exact volume for twist, exact frustum for
      a guide), loft with a centreline (OCC pipe shell), sketch chamfer (distance / two distances / angle),
      conic (exact rational quadratic). Edit Feature now resolves selections on the model before the feature.
      Tests test_split_delete_move_replace_face, test_curves_sweep_twist_guide_loft_centerline_sketch_chamfer_conic;
      forms driven in headless Edge. Split Line (projection, OCC splitter). Intersect (OCC MakerVolume: regions
      of bodies + surfaces, exclude / merge; test_intersect_regions). Loft with guide curves (profiles cut where
      the guides cross them, one Gordon curve-network patch per stretch between guides - a closed profile in
      one network only follows the guide at its seam - sewn with the end profiles; test_loft_guide_curves).
      Indent (tool + clearance cut from the target; a wall round the part of the tool sticking out, the side
      picked by a point; OCC offset with the simple algorithm as fallback for spheres; test_indent).
- [x] R3 Assemblies (2026-10-24). Mechanical mates: rack-and-pinion (travel = turn x pitch radius), screw
      (concentric + advance = turns x pitch), hinge (concentric + coincident), universal joint (exact Cardan
      relation, on the branch that follows the input shaft); turns are unwrapped past half turns, committed
      only from a solve's final solution, and Rotate Component turns in 30-degree steps holding just the
      driven part's orientation (so a screw driven by its bolt still advances). Edit Part in context
      (edit_in_context / return_to_assembly: the other components ghosted in the part's frame; changes
      update the assembly). Assembly Hole / Extruded Cut (assembly features applied in each component's
      frame: parts unchanged, mates can use the cut faces). BOM (assembly_bom, export_bom CSV, dialog with
      CSV download). Tests test_mechanical_mates, test_assembly_cuts_bom_and_edit_in_context; UI driven in
      headless Edge.
- [x] R6 Everyday gaps (2026-10-24): sketch Perimeter Circle, Tangent Arc (tangent relation), Split Entities,
      Move / Copy / Rotate / Scale Entities (test_sketch_tangent_arc_circle_3pt_split_transform); IGES import
      (solids, or surface-only IGES sewn into solids) and export, 3MF and glTF / GLB export (test_iges_3mf_gltf);
      tools driven in headless Edge. Evaluate: Thickness Analysis (ray through the material from every face),
      Undercut Detection (faces the part blocks for a +/- pull), Clearance Verification (assemblies). Linear
      Coupler mate (a dragged part leads: solve with its pose held, then settle), Replace Components
      (test_thickness_undercut_clearance_coupler_replace).
- [x] R4 UI completeness and distribution (2026-10-24). The configuration pane and equations dialog were
      already in place. File associations: openswx/fileassoc.py (per user, HKCU\Software\Classes: .oswprt /
      .oswasm open in openswx with its icon; Open with for .sldprt/.sldasm/.step/.stp/.x_t/.x_b without taking
      them over; unregister removes exactly that) via `openswx-cli register-file-types` /
      `unregister-file-types`; test with an in-memory registry. Installer: packaging/openswx.iss (Inno Setup:
      Start menu / desktop shortcuts, the same associations machine- or user-wide, uninstall) and
      packaging/build.py (PyInstaller, then the installer when Inno Setup is installed, else a portable zip:
      dist-new/openswx-<version>-win64.zip, 292 MB, built 2026-10-24). 2026-10-25: Inno Setup 6.7.3 installed
      (per user; build.py finds it there too) and dist-new/openswx-setup-0.1.0.exe built (241 MB; jedi/parso
      left out of the bundle: unused, 22 MB, and its paths broke installs into deep folders). Checked: silent
      per-user install, Start menu entry, the app serving and running commands (first start after install
      ~40 s while Windows scans the new files, then ~3 s), uninstall removing the folder, shortcuts and
      registry entries.
- [x] R7 UI behaviour parity (2026-10-25, user: "fix all behaviours to ensure parity with solidworks"). Planes
      hidden while a sketch is open. Smart Dimension: preview follows the cursor after the picks; placement picks
      horizontal / vertical / aligned (and an angle's side: `supplement`); labels keep their place (`pos`,
      sketch_move_dimension, drag); Modify box takes units ("1in") and "=" equations. Sketching: snaps to
      midpoints, quadrants and onto entities with automatic midpoint / coincident relations (rectangle,
      polygon, slot corners too), dotted alignment inference, inference glyph; drag entities (circles/arcs
      resize: sketch_drag_entity); box select (window / crossing) in sketches and on model edges/faces;
      Sketch Fillet / Chamfer as tools that stay on; Offset (reverse, bi-directional), Mirror (Copy option),
      Linear / Circular Pattern, Move / Rotate / Scale and Convert Entities as PropertyManagers with a live
      sketch preview; Select Chain; Display/Delete Relations with a filter, highlight and Delete All.
      Selection: Select Other (right-click), selection filter (F5, X/E/V), Zoom to Area / Selection, a
      Previous View history. FeatureManager: drag to reorder (reorder_feature, refused when a reference would
      break), folders (add_folder / remove_folder, saved), context toolbar on click, double-click shows the
      sketch dimensions on the model for editing, Roll to Previous, Edit Sketch Plane, Parent/Child,
      Appearance. Extrude: Instant3D depth handle. Shortcuts: S shortcut bar, right-drag mouse gestures,
      Enter repeats the last command, Z / Shift+Z, Ctrl+Tab / Ctrl+W and a Window menu. Assemblies: drag
      components, Hide/Show (Tab / Shift+Tab), Isolate. Measure: a live panel for one or two faces / edges /
      vertices, parts and assemblies (measure_items). Also exposed: Variable Size Fillet, delete global
      variables, Section Properties, Sheet Metal info, BOM saved through the server, drawing Delete key.
      Tests: test_smart_dimension_placement_and_supplementary_angle, test_feature_reorder_folders_and_entity_drag,
      test_measure_items, test_parent_child_and_edit_sketch_plane; UI driven in Chrome.
      Then the remaining differences (2026-10-25, user: "continue until all "still different" items are in"):
      perspective view (View > Perspective, heads-up button; a perspective camera sized so the target plane keeps
      the view height); view cube (turns with the camera, click a face; Iso button) and named views
      (save_named_view / delete_named_view, kept in parts and assemblies; Space menu New View / saved views);
      middle-click a face or vertex to rotate about it (marker; middle-click nothing clears it); FeatureManager <->
      model cross-highlighting (face_features: the feature that made each face, from the bodies the rebuild
      records; hover a feature lights its faces, selecting one shows them, selecting a face marks its feature);
      dangling geometry (converted entities keep their model edge, "ext"; a change that removes it makes them
      dangling: brown, status "(Dangling)", Display/Delete Relations filter); design tables (apply / get /
      delete_design_table: rows are configurations, columns dimensions or $STATE@Feature; grid editor with CSV
      import/export); material dialog with the library tree and editable properties, a Custom Materials library
      (save_custom_material, ~/.openswx/custom_materials.json) and properties on set_material; full mate editing
      (redefine_mate: Edit Feature on a mate opens the Mate PropertyManager); Insert Components placed with a
      click (preview follows the cursor; the first goes to the origin); the Modify box's configuration scope
      (set_dimension configurations= this / all / list); deleting single views of the automatic drawing
      (hidden_views). Tests: test_delete_view_of_automatic_drawing, test_converted_entity_dangles_when_its_edge_goes,
      test_design_table, test_custom_materials, test_redefine_mate, test_dimension_configuration_scope,
      test_face_features, test_named_views; UI driven in headless Edge over the DevTools protocol.
- [ ] R5 SOLIDWORKS file fidelity: feature history coverage (489/841 sample parts exact), .slddrw drawings.
      .slddrw (2026-10-24, openswx/sw_drawing.py): the referenced model (the one named like the drawing, else the
      first named in the view records; found at its stored path or beside the drawing) is opened natively and
      the sheets and views recreated - model views in their named orientations (*Front, *Isometric, ...), views
      without one projected from the model view before them, each on the sheet whose record precedes it, at a
      standard scale fitting the model. 18 of the 20 sample drawings open (7550-021 has no model: an AutoCAD
      import; foodprocessor, a large assembly, runs past 120 s). Also read now: each view's sheet position
      and scale (its record's 3x3 orientation doubles, then x, y, z in metres from the sheet's lower left, then
      the scale; a model view's first record has an all-ones flag block in place of the orientation) - found
      for every view of the samples with a model - and the sheet size (height, width doubles; all 20 samples:
      ANSI A-C, A0-A3, A4 portrait, now openswx sheet sizes too). Views out of line with the view before them
      come in as model views. The stored position is where the model origin falls on the sheet (box.slddrw
      lays out as in SOLIDWORKS); when that would put a view off the sheet (assemblies: origins differ) the
      sheet's views are centred there instead. Dimensions: a view record's display texts (<Border type=14>
      value</Border>) are placed by value - new command drawing_dimension_value (UI: Dimension by Value) puts
      a diameter / radius on the circle of that size (side-on: the width across), a length between vertices
      that far apart (an edge first, the outline next); box 3/3, bolt-assembly 7/7, can 3/6 (its model
      differs from the drawing's). Fixed with it: diameter dimensions on concentric circles snapped to the
      innermost on redraw; the BOM now defaults above the title block (SOLIDWORKS' anchor) instead of over
      the top-left views. Notes (2026-10-25): `DetailItemN` records after the first view record hold a note's
      lines, its font (name or NNPTS) and then its position (x, y, 0 doubles, metres from the lower left);
      <MOD-DEG>/<MOD-DIAM>/<MOD-PM> become the characters, a <FONT size=> the text height. Sheet-format
      notes (the first DetailItems, $PRP properties, title-block labels, anything in the title-block corner
      or border strip), balloons and BOM rows (component names), view labels, bend tags and hole tables are
      left out: the samples give cabinet_bath's valve note on its sheet and 7550-021's callouts (that drawing
      has no model). Not read yet: other annotations (GD&T, balloons as such), sheet format contents,
      dimension positions (test_open_solidworks_drawing, test_solidworks_drawing_placement,
      test_dimension_by_value_concentric, test_solidworks_drawing_notes).
      2026-10-25: part rebuild robustness. Booleans OpenCascade marks invalid (fusing along faces that only
      nearly coincide, e.g. a stored-body piece against an extrude) are repaired with ShapeFix in apply_op
      (later booleans on them came out inside out: reducing outlet cross, volume -0.002). The tagged-face
      check ignores face points beyond the candidate's bounding box (a later mirror merges its copy into the
      same face: the cross's run cylinder spans both halves), each face still needing points inside: the
      outlet cross rebuilds again as editable extrudes + mirror (8 features, 2 imported), and the pan cross
      heads (5 files) now rebuild too; the 41-part regression set all pass (test_tagged_faces_extended_by_mirror).
      Open: aw_spider, bowl bottom, clamp1 decode with invalid faces from the Parasolid reader (aw_spider: a
      zero-height cylinder band bounded twice by one circle), so their stored-body booleans fail.
      2026-10-25 (later): the reader's runaway-geometry check measured the part by its vertices only, so a
      spline profile bulging past them (cam2, bottle) or full-circle edges with no vertex at all (5.5 m pipes)
      were rejected as runaway: the reach now includes the edges' circles (centre + radius; construction
      circles no edge uses stay out - a sample bat has 500 m ones) and the 3D B-curve poles
      (test_parasolid_extent_counts_edge_circles); Mirrorknife_guard and Baseball Bat are still (rightly)
      rejected. Parts of sketches and planes only (no body stored) rebuild against an empty body. Zero-volume
      solids left by booleans are dropped from the body (wheel_hub: right volume, 6.6x the area).
      Measured: of the 17 "no bodies found" parts 9 now rebuild exactly; of the 77 old "base feature"
      failures 3 rebuild, 52 now get past the base (27 differ later, 18 other errors, 4 time out) and 25
      still fail there (7 of them sweeps as the base feature: next target). The 42-part regression set
      passes. No full 841-part re-survey yet (hours of run time): at least +17 on 489.
      Timeouts (138 in the last survey) are mostly trial count x boolean cost: Jet (sprinkler) spends its
      budget on 4 frames x 64 cut-revolve variants that all cut nothing (a decoded sliver region); merging
      the regions' revolutions as a compound / glued fuse was tried and gained nothing (reverted).
      2026-10-24: sketch solver speed. Imported sketches whose inferred relations barely conflict made the
      solver crawl for minutes (slot_base: one 58-unknown sketch). Now: a Jacobian from grouped columns of
      the constraints' sparsity pattern for sketches over 40 unknowns, and a time box per solve (4 s + 20 ms
      per unknown) keeping the closest point, the rest reported failing. slot_base: timeout -> exact in 19 s.
      Re-run of the 20 smallest timed-out parts stopped early (system low on memory): 1 of 16 fixed, the
      others still over 115 s - their time goes elsewhere (to profile next).
      Profiled next (foreground, sequential: the earlier batch numbers were taken under memory pressure):
      scoring booleans against some stored bodies take ~1 s each and often fail into sampling anyway, so
      after two slow ones (> 0.5 s) or three failures the candidates are ranked by sampling (4000 points; the
      final match is still checked exactly); the inside-mesh test is vectorised (cells built with array
      operations, checked against brute force); PartDocument.rebuild is incremental (each feature's state is
      kept under a hash of the history up to it, so edits rebuild from the first changed feature; the test
      suite runs ~20 % faster); rebuild_isolated keeps the child's pipe end open until it exits and detects
      crashes through the process sentinel; unbounded candidate bodies score 0. Re-time of 80 of the 138
      timed-out parts (sequential, as the application runs them): 7 now exact (slot_base, centerlink, flange,
      motor_frame, cover_pllug, camtest, clip-male_qmodel), 3 rebuilt but not exact, 32 now finish in time
      with a definite failure (15 body mismatch, the rest a base feature not rebuilt), 38 still over 115 s
      (mostly the larger ones; the 58 largest not re-timed).
      Base features (the 16 "base feature could not be rebuilt"): candidates are now ranked by whether the
      faces SOLIDWORKS tagged with the feature are on the result, before agreement with the stored body
      (holdarm: a 10 mm mid-plane cylinder lost to a larger wrong section that overlapped material later
      features add). Hole Wizard holes: a cone as wide as the hole is a blind hole's drill point (built with
      drill_point, entered from the other end), no longer a countersink that cannot be built (center:
      Hole1); variants are grouped per hole. When the retry gets past the base feature its failure is the
      one reported. Result: 13 of the 16 now rebuild past the base feature and stop on a later feature (2
      others now time out instead - face_plate_ads_geo, clamp1; sm1-remove-edges' base flange still fails)
      (pan cross head: same volume, 0.39 mm off; nafems_ic43: same volume, area differs; aw_spider: an
      inverted (negative volume) body; bowl bottom: Cavity not supported; center: fillets after imported
      bosses); none exact yet. A 29-part spread of exact parts is unchanged (all exact).
Out of scope at the time: PDM, rendering (Visualize), CAM, costing, plastics / flow simulation (all added later,
see the passes below).

## Feature history from .sldprt (standalone, no SOLIDWORKS at runtime)
Goal: rebuild an editable openswx feature tree from a part's `Contents/Config-<n>-ResolvedFeatures` stream.
Method: clean-room format analysis; SOLIDWORKS is only a development oracle (tools/difftest/feature_samples.py
builds pairs differing in one parameter; diff the streams). Every milestone is verified by rebuilding and
comparing with the part's own Parasolid body (read exactly by openswx/parasolid.py).
- [x] F1 tree scan: tree objects in order (class, name, feature id), tagged and untagged; the object class
      word (`04 80` / `05 80`) is learned per stream (openswx/sw_tree.py)
- [x] F2 dimensions: length / angle (double) and integer (u32) parameters, attached to their feature;
      end conditions from the end spec
- [x] F3 sketches (openswx/sw_sketch.py): frames (plane record, face planes, older layouts), points, lines,
      arcs / circles (shared centres), splines (exact B-spline poles / knots). Open: construction flags
      (lone lines are taken as centerlines), relations, dimensions placement, converted / external entities
- [~] F4 features: extrude / cut (Blind, Through All, Mid Plane, Through All Both; up-to conditions as Blind
      to the stored extent), draft, revolve / cut-revolve, fillet, chamfer (edge + vertex), shell, linear /
      circular pattern, planar surface, scale, split line (reported, left out). Open: extrude direction flag,
      fillet / chamfer / shell selections (found from the stored body instead), sweep, loft, hole wizard,
      mirror, sheet metal, weldments, surface features, multi-body
- [x] F5 rebuild in openswx (openswx/sw_rebuild.py); what is not decoded yet is settled against the stored
      Parasolid body (direction, boss vs cut for moICE_c, arc sweep side, face-sketch plane, revolve axis,
      fillet edges, shell faces); accepted only when volume / area or a two-way edge sampling match.
      open_part(history=True) uses it and falls back to the imported body
      The rebuild runs in a child process (sw_rebuild.rebuild_isolated): trying candidates can crash the
      OCC kernel, which then only costs the history, not the application.
      Since: sketches whose segments do not build take their profile from a section of the stored body
      (exact lines / arcs / circles / rational B-splines), sketch planes may be Top / Right / user planes,
      candidate search stops at the first exact candidate, freeform bodies are scored by mesh sampling (OCC
      booleans can hang on them), each rebuild has a 150 s budget. Re-survey of the failures (557 of 841
      re-checked): 281 rebuilt.
      Then: features not decoded (hole wizard, sheet metal, weldment members, dome, thin features, base bodies)
      or not rebuilt exactly come in as their exact contribution cut from the stored body (an imported feature
      next to the faces FACE_ID tags with the feature's id), and every rebuilt feature is checked against those
      tagged faces. Survey (665 smallest re-checked; the 176 largest mostly exceed the time budget): 440 of 841
      exact, 283 of them fully parametric, 157 with some imported features.
      Rule (user's choice, 2026-10-20): the feature that makes the first solid must be editable (an imported
      base body excepted), else the part opens as its stored body. Full survey of all 841 parts with it: 338
      exact (283 fully parametric, 55 with some imported features); 167 exceed 120 s (the app allows 300 s),
      130 have a base feature that is not rebuilt exactly, 71 mismatch, 21 crash the kernel (isolated in the
      app), 14 have no solid in the partition.
      2026-10-21: fixed the search stopping at the first consistent candidate (now it must also reproduce the
      feature's tagged faces), OCC booleans silently returning nothing (cross-checked by sampling), relative
      mesh tolerances (speed), the first valid sketch frame record, base flanges. Re-survey (753 of 841
      re-checked; the 88 slowest not re-run): 398 exact, 317 of them fully parametric.
      Full re-survey of all 841: 398 exact (317 fully parametric); 177 base feature not rebuilt, 132 over
      120 s, 31 mismatch, 14 no solid, 13 kernel crash. Candidates are now evaluated on a copy of the build
      state (no document regeneration per candidate) and scored with a plain boolean first; former
      timeouts now mostly fail fast instead. Further gains need decoding (sketch segment records / newer
      layout, extrude direction, fillet edges, hole wizard data), which needs new SOLIDWORKS test parts.
      Also fixed: arcs whose two candidate centres are equidistant from both ends (slots) take the nearer one;
      the XT reader rebuilds faces that come out with negative area on periodic surfaces (half cylinders
      whose boundary runs along the seam) on the surface turned half a revolution, so the stored body is
      valid (pipe.sldprt: 1930 -> 5791.67 mm3, exact). OPENSWX_REBUILD_LOG=1 prints why features fall back.
      Decoded: the end spec's reverse-direction flag (tried first). New in openswx: thin extrude / cut
      (extrude(..., thin_thickness, thin_side one|other|mid), sharp corners); SOLIDWORKS thin extrudes
      (moExtruThin_c / moBossThin_c / moCutThin_c) map to it, wall thickness from their dimensions.
      Decoded: sketch segment records (end points = indices into the sketch's point records in file order;
      construction flag) fill the segments the point references miss and mark construction lines (exact
      centerlines). Full survey: 411 of 841 exact (326 fully parametric); 168 base feature not rebuilt,
      127 over 120 s, 26 mismatch, 19 kernel crash, 14 no solid.
      Then: full ellipses (sgEllipseHandle; axis end points are point records of kind 3) rebuild natively;
      Hole Wizard features rebuild as openswx holes (simple / counterbore / countersink, read off the faces
      FACE_ID tags with the feature: coaxial cylinders and cones) instead of imported geometry.
      Full survey after these: 412 of 841 exact (327 fully parametric).
      Weldment structural members (moWeldMemberFeat_c) on straight path lines rebuild as extrudes of their
      profile (the stored body's section at the start of each line): 4 of the first 12 weldment samples now
      rebuild exactly; frames with mitred corners / many members still fall back.
      Sketch dimensions decoded (each written twice after the sketch: value, then references to point /
      line / arc handles); kept when the geometry has that value, so they drive the rebuilt sketch (edit
      the SOLIDWORKS width -> the part resizes). Geometric relations are not stored as classes and are not
      decoded; horizontal / vertical relations are inferred from the exact geometry instead.
      Tangent relations (line ending on an arc along its tangent) are inferred too. Features before a later
      scale are compared with the stored body scaled back; segment records only fill sketches whose point
      references give nothing (filling single gaps turned arcs into lines). Survey 2026-10-21 (835 of 841;
      the rest over 120 s): 419 exact, 339 fully parametric; 162 base feature not rebuilt, 134 over 120 s,
      27 mismatch, 13 no solid, 11 surface mismatch.
      Pass 2026-10-21 evening (base retry, drafts, two-direction / mid-plane extrudes, thin auto-fillet,
      older formats, chords, closing arcs, weldment members with mitres, fillet radius from faces; plane
      sections approximated: freeform sections were 1000-point polylines that made booleans take minutes):
      partial survey (436 of 841, smallest first, stopped for low memory): 358 exact vs 336 before on the
      same parts (26 gained, 4 lost: ns87_rigid fixed since, Yoke_female near the time limit under load,
      lwrsway_lnk x2 open: base extrude now fails its tagged-face check). Since: ns87_rigid and Yoke_female
      pass again (alternative sketch readings tried on the best plane only: 152 s -> 108 s); lwrsway_lnk
      now exact and fully parametric: its fillets meet across a face (full round), which the kernel rejects;
      a radius 1e-6 smaller leaves a sliver within tolerance.
      Inferred relations now also: perpendicular (slanted lines meeting at a point), arc-arc tangent,
      concentric (separate centre points). Relations are now also decoded from the stream (type code +
      references): horizontal, vertical, tangent, parallel, perpendicular, concentric, midpoint, equal are
      carried over; inferred ones fill in where none was decoded (53 of 60 tutorial parts carry relations).
      Survey 2026-10-22 (822 of 841, the 19 largest not reached: stopped for low memory): 456 exact, 351
      fully parametric, against 417 / 337 on the same parts before this pass (55 gained). Failures: 137 over
      120 s, 76 base feature, 31 mismatch, 15 no solid, 107 other. Of the 16 lost, 5 (decoded midpoints
      without their point), holder (relation on an arc) and upprsway_lnk x2 (fillet taking a later fillet's
      edges: edges now chosen next to the fillet's own tagged faces) are fixed since; the rest are timeouts.
      Full survey 2026-10-23 (all 841, failures and unreached re-run on the final code): 472 exact, 361 fully
      parametric (was 419 / 339). Failures: 155 over 120 s, 76 base feature not editable, 35 mismatch,
      17 no solid, 86 other.
      Then a score cache (a candidate building the same body as one already scored reuses its score: on
      wheel_hub 311 scorings were 107 bodies, 152 s -> 75 s); the 155 timeouts re-run: 483 exact, 361 fully
      parametric; failures 108 over 120 s, 91 base feature, 37 mismatch, 17 no solid, 105 other.
      Derived and mirrored parts (`moStock_c`, `moMirrorStock_c`) now count their base body as fixed, like an
      imported one (it is driven by another file in SOLIDWORKS too): +4 exact (mirrormulticonfig-part, arm
      right, Right Knife Sleeve, externalreferencedpart). Body-Delete rebuilt (bodies outside the stored part).
      On the retry, a decoded profile that fits well but not exactly (a slot read as its construction geometry)
      gives way to a section that matches exactly: base-feature group re-run, +8.
      Kernel crashes (28 parts crashed the whole rebuild): the child reports the feature it is trying; after a
      crash the rebuild runs again with that feature taken from the stored body (up to 3). The survey now
      measures rebuild_isolated, as the application runs it. Survey total so far: 491 exact, 365 fully parametric.
      Helix (`moHelix_c`) rebuilt on its sketch circle (height / pitch the larger / smaller length; handedness
      and direction: the variant running inside the stored body); a profile swept along a helix is placed at its
      start. Default sweep names recognised. The sprinkler springs come within 3-6 % (end hooks still open).
      Patterns: copies go to the kernel as one compound tool (24 rib copies: 88 s -> 16 s per variant), and the
      rebuild ranks pattern variants by their copies alone (each inside / outside the stored body) and builds
      only the best (shaft gear insert: over 150 s -> 22 s). The slowest remaining parts still exceed 120 s.
      Full survey 2026-10-24 on the final code (all 841 in one consistent run, isolated rebuild as the
      application runs it, 115 s per part): 489 exact, 362 fully parametric (start of this goal: 419 / 339).
      Failures: 138 over time, 79 other, 77 base feature not editable, 41 mismatch, 17 no solid.
      3D sketches (lines) decoded and rebuilt as openswx 3D sketches; structural members run along them and
      share path sketches between members. Open: members of different features trimmed against each other
      (static_27 within 1.3% volume). Curved members (path arcs) rebuilt as the stored section revolved about
      the arc's axis (nafems_freq_test6 exact).
      openswx structural members can be trimmed back by bodies already there (`trim`). static_27 still open:
      its second member group is untagged, two of its path lines fit no section tried, and the stored area is
      three times the rebuilt one (hollow sections not reproduced); trimming as a rebuild variant ran out of
      time, so it is not tried yet.
      Survey 2026-10-20 over the 841 SOLIDWORKS 2025 sample parts: 273 rebuilt exactly (32%); the rest open
      as the imported body. Largest remaining groups: sketches whose segments are not decoded (139: newer
      layout where points carry a count but no inline references and segments are separate records; ellipses,
      polygons, converted entities), result mismatches (118), rebuild over 120 s (60), fillets whose edges
      are not found (45), moBaseBody_c / moSheetMetal_c / moHoleWzd_c (24 each), weldments (16).
      Next: segment records (after the points: u16 end-point indices where a point has its tag and
      coordinates; construction = bit 0x08 of the u16 after `80 BF 00 00`), ellipses (`sgEllipseHandle`,
      axis points of kind 3), hole wizard data, sheet metal, thin features, faster candidate search.

## UI spec (M4) — recreated from observed SOLIDWORKS behaviour, original icons
- Top: brand + menu flyout (File/Edit/View/Insert/Tools/Help), quick-access (New/Open/Save/Undo/Redo/Rebuild),
  document title with `*` when dirty, search box.
- CommandManager ribbon with tabs Features | Sketch | Evaluate; big icon+label buttons, small stacked ones.
- Left pane tabs: FeatureManager tree (Part name, History, Sensors, Annotations, Material, Front/Top/Right
  Plane, Origin, features with nested absorbed sketches, `(-)` under-defined / `(+)` over-defined prefixes,
  error/suppressed styling, draggable rollback bar, right-click menu: Edit Feature/Edit Sketch/Suppress/
  Rollback/Rename/Delete/Hide-Show) and PropertyManager (OK/Cancel, group boxes) that replaces it while a
  command runs.
- Graphics: gradient background, orthographic camera, shaded-with-edges default, heads-up view toolbar
  (zoom fit, orientation menu, display style), confirmation corner (OK/Cancel; Exit Sketch), triad,
  hover = orange, selection = blue, yellow live preview (POST /api/preview), mini context toolbar on face pick.
- Mouse/keys: middle-drag rotate, Ctrl+middle pan, Shift+middle zoom, wheel zoom at cursor, arrows rotate,
  Space orientation menu, Ctrl+1..8 standard views/normal-to, F fit, Ctrl+Z/Y, Ctrl+S, Ctrl+Q/B rebuild, Esc, Del.
- Sketcher: line chain, centerline, corner/center rectangle, circle, centre/3-pt arc, polygon, slot, point;
  point snapping (reuses point ids), auto horizontal/vertical inference, Smart Dimension with Modify box,
  relations PM (applicable relations for selection), drag points, status colours, dims as overlay labels.
- Right task pane: Agent activity (MCP actions live, origin tagged) + Mass properties.
- Status bar: app, cursor coords, sketch state, editing context, units MMGS.

## Progress log
- 2026-10-19: RE findings (docs/re/README.md). venv with build123d 0.13 / OCP, mcp, fastapi.
- 2026-10-19: Phase 4 backlog pass: B1-B11 implemented (see Phase 4 list; remainders listed in Next steps).

## Remaining-parity pass (2026-10-26, user: "continue and implement all")
Done (backend + tests + UI; commands in `commands_more.py`, builders in `advanced.py`):
- [x] Part: Flex / Deform / Freeform (NURBS pole mapping), conic and curvature-continuous fillets, FilletXpert,
      mold tools (parting line, shut-off and parting surfaces, tooling split), Insert Part / Mirror Part,
      cosmetic threads, Ruled / Radiate / Extend / Untrim / Flatten / Filled-from-edges surfaces,
      library features and the Design Library, Hole Series.
- [x] Assembly: SmartMates, connection points + Magnetic Mates, Mate References, Alternate Positions,
      Envelopes, SpeedPak, Large Assembly Mode, Defeature, Pack and Go, routing (pipe, cable), weld beads.
- [x] Drawings: surface finish and weld symbols, stacked balloons, cosmetic thread display, layers,
      editable / saved sheet formats, alternate position views.
- [x] Analysis (`fea_more.py`, `analysis_more.py`): nonlinear (bilinear plasticity), harmonic and transient
      dynamics (modal superposition), drop test (energy method), fatigue (S-N, Goodman, Miner), flow
      (potential flow on a voxel grid: Cp, lift, skin-friction drag; no separation / pressure drag),
      Costing, Sustainability, DimXpert auto dimension scheme (PMI saved with the part), photo rendering
      (PBR, procedural environments, floor, shadows) and decals.
- [x] Ecosystem (`ecosystem.py`): PDM vault (check in/out, versions, revisions, WIP / approval / released
      workflow, search, where used), eDrawings-style single-file HTML viewer with section / measure / markup,
      Toolbox ISO + ANSI inch (14 kinds), macro record / run as Python.
- [x] UI: menu bar uses cascading hover submenus; PropertyManagers for the new features; Mold Tools, MBD and
      Render Tools tabs.
- [x] Fixed: sliver cut cells (< 10% inside) carried mass with under-integrated stiffness, giving spurious
      low modes in frequency studies (and runaway nodes under inertial loads) on parts with holes.
Limits (honest, updated below): costing / sustainability use generic rates; flow was ideal-flow only and routing had no
fittings library; PDM is a shared-folder vault (no server, no permissions); rendering is real-time PBR,
not path traced.
- [x] Installer layout (2026-10-26, user: "not have the _internal folder at all?"): the installer is per
      user and puts only two launchers (packaging/launcher.cs, ~13 KB each, compiled by build.py with the
      Windows .NET Framework csc) in the install folder; the PyInstaller runtime (exe + _internal) goes to
      %LOCALAPPDATA%\openswx\runtime (HKCU\Software\openswx\RuntimeDir; OPENSWX_RUNTIME overrides). The
      CLI launcher passes arguments, exit code and stdio through (MCP works through it). One-file
      PyInstaller builds were rejected: they unpack ~400 MB to temp on every start. The portable zip keeps
      the plain onedir layout. Verified: silent install, launch from AppData, MCP initialize, clean uninstall.

## Follow-up pass (2026-10-26, user approved: more exact rebuilds, .slddrw, a real flow solver, fittings)
- [x] Navier-Stokes flow (openswx/cfd.py; cfd_external / cfd_internal / cfd_results; Flow Simulation dialog
      asks for external NS, internal NS or the old potential flow): lattice Boltzmann D3Q19 on the voxel grid,
      He-Luo incompressible equilibrium (a long passage's pressure drop no longer changes the velocities),
      TRT collision with the magic parameter 3/16 for laminar flow, regularised collision + Smagorinsky
      (Hou) when the grid is under-resolved (plain TRT went unstable at Re 1e4), half-way bounce-back walls,
      momentum-exchange forces, inlet velocity trimmed until the measured entry flux matches (an equilibrium
      inlet loses ~10 % along the wall), convergence = window means half a flow-through apart (six close
      readings stopped runs while the drag was still drifting). Kernel by explicit direction sums and
      slice streaming: ~10x faster than the first version. Measured: pipe Re 10 within 1.5-3 % of
      Hagen-Poiseuille + entrance loss (umax / U = 2.00); sphere Cd at Re 20 +5 % (Clift 2.73), Re 100 +10 %;
      open (zero-gradient) sides beat free-stream sides (blockage: +40 % at 1 D margins). At Re 1e4 the
      coarse LES gives sphere Cd 0.7-0.9 for 0.4: results carry a `warning` there. Under 10 cells across the
      wall is too coarse (Cd 0.8 at 8 cells). test_cfd_pipe_flow_and_sphere_drag.
- [x] Piping library (openswx/piping.py; list_pipe_standards, route_pipe_standard, insert_fitting; Insert >
      Routing > Pipe (Standard) / Insert Fitting): NPS 1/4-12 sch 40 / 80 (B36.10), B16.9 long / short radius
      elbows at any corner angle, equal tees, concentric reducers, caps, B16.5 class 150 weld-neck flanges
      (nominal values, for layout and BOM). A route cuts its runs to length between the fittings (corner:
      R tan(angle / 2) each side; flange: its length) and refuses runs too short for them; every part is a
      virtual component with Size / Schedule / Length / Angle properties. test_piping_library_route_and_fittings.
- [x] Parasolid reader: a face whose loops are vertex-less rings on a closed but not periodic B-spline
      surface (an extruded closed spline, e.g. foot_board's 0.38 mm wall) lost a loop in ShapeFix and came out
      with zero area, so the whole body was invalid; such surfaces are made periodic first, and ShapeFix adds
      the seam (test_parasolid_band_of_ring_loops_on_closed_bspline). Corpus re-read: 752 of 841 stored
      bodies valid (736 in the first read); bearing.sldprt's new volume (1.06e6 mm3) is confirmed by voxel
      classification - the first read's 1.67e5 was the wrong one.
- [~] Feature history: annotation-only feature types (sketch pictures, weld symbols, plane sections, sheet
      metal tables / templates) are skipped instead of failing; the parts whose stored body became valid
      re-run: +2 exact (nafems_freq_test14, tutor1). The rest of those failures are individual (foot_board:
      later bosses / patterns misplaced under a Scale; wheel, cam2: other features). No full re-survey.
- [ ] .slddrw: dimension positions, balloons, GD&T not decoded. The samples hold 16 dimensions in 3
      drawings; the doubles near the display texts are model-space values (5, 95, 100 for box.slddrw), so
      positions need SOLIDWORKS-made test drawings (dev oracle) to pin down. Not started.

## Manufacturing and documentation pass (2026-10-27, user: "continue with any remaining features")
Commands in `commands_mfg.py`; UI in `web/mfg.js` (Tools > CAM / Plastics / Inspection on parts, Tools > Electrical /
Composer and Insert > Routing on assemblies; Manufacturing, Electrical and Composer ribbon tabs).
- [x] CAM (`cam.py`; cam_generate / cam_gcode / cam_toolpaths): 3-axis, setup from any of +-X/Y/Z. The part is a
      height map from the tessellation; Z-level roughing (zig-zag + contour, `leave` on walls and floors), wall and
      floor finishing (floors from the upward planar B-rep faces, so they are cut exactly), drilling of B-rep holes
      along the tool axis (faces grouped by axis + radius, reachable from above; G81 / G83 peck past 3 D; the mill
      leaves them alone). Tool centre kept r + leave + one cell from material: the raster cell is the precision.
      Marching-squares contours (climb), Douglas-Peucker, greedy linking with feed links that stay in the cleared
      area. Posts: fanuc, linuxcnc, grbl. Feeds from material cutting speed / chip load. Stock-removal simulation on
      the raster: no gouge on the test block; only the pocket's inside corners keep material (the tool radius).
      Limits: flat end mills only, no rest machining, adaptive clearing, 3D finishing of curved floors (they are
      stepped at the levels), 4/5-axis or turning.
- [x] Plastics (`plastics.py`; plastics_fill / plastics_results): voxel Hele-Shaw / Darcy fill with permeability
      t^2 / 12 eta from the local wall thickness (exact B-rep chords on the three axes through each cell centre,
      1/t^2 = sum 1/chord^2 - the first version used the voxel fractions and read a 2 mm wall as 1.75 mm), power-law
      melt with eta at the wall shear rate (reproduces power-law slit flow exactly), front advanced by the fluxes into
      it, time from the volume in the cavity (dropping overflow first made a 1 s fill take 1.64 s). Measured: line-
      gated 100 x 20 x 2 plate within 0.2 % of 12 eta Q L / W t^3 (Newtonian) and of the power-law formula, fill time
      0.99 s for 1 s. Weld lines: neighbours on either side flowing at each other (> ~45 deg); air traps: small
      pieces of unfilled space the melt closes in, and the last place to fill when it is inside the outline (a thick
      rim racing round a thin panel). Clamp force, cooling time (slab), sink risk (> 1.5 x nominal), short shot over
      the machine limit, freeze-time warning. 8 generic materials. Limits: isothermal (no frozen layer, no packing,
      warpage or fibre orientation); the chord thickness over-reads wall junctions.
- [x] Inspection (`inspection.py`; inspection_characteristics / _set / _settings / _report): numbered
      characteristics from the drawing's dimensions, GD&T frames and surface finishes plus the PMI (sizes, H7 holes,
      positions), ISO 2768 f/m/c/v general tolerances for untoleranced ones (linear, radius, angle by leg length),
      overrides, measured values with pass / fail, stable numbering, sheet zones (ISO 5457), inspection balloons on
      the drawing sheets, AS9102-style forms 1-3 (HTML) and CSV.
- [x] Electrical (`electrical.py`; electrical_connector / _route_segment / _wires / _import_wires / _harness /
      _formboard / _clear / _info): connectors on components (connection point, else centre), routes joined into a
      network, wires routed by shortest path with breakouts, length = path x (1 + slack) + end allowance, resistance and
      voltage drop (AWG and metric copper tables), bundle diameters (1.15 sqrt(sum OD^2)), the harness as one virtual
      component, wire BOM, pin checks, flattened formboard SVG with the wire and connector tables. Limits: no
      schematic editor, no splices / multi-core cables, no terminal or connector library.
- [x] Composer (composer_auto / _add_step / _steps / _clear / _export): steps from the exploded view (assembly
      instructions or disassembly) or by hand (offsets, hidden, see-through, view, note), published in the
      single-file HTML viewer with Play / previous / next animation (`viewer_payload` / `write_viewer` now shared
      with eDrawings export).
- Not done: Cloud (3DEXPERIENCE-style collaboration). Live collaboration is being built separately in openswx-collab
  (server and wire protocol; self-hostable); the openswx side will be an optional module, developed on the branch
  dev/openswx-collab. Until then PDM covers check-in / out.

## Interop, naming and speed pass (2026-10-29, user: "commit, fix the rebuilding of feature history, opening solidworks drawings, importing assemblies, persistent naming, opening speed then fix the installer")
- [x] Feature history (sw_rebuild.py): the child's budget now follows the isolation timeout (it was 150 s inside a
      115 s kill, so the budget never acted); past 60 % of it the remaining features come in as their stored
      contribution, past 85 % the rest of the part at once, so a slow part opens with its rebuilt features editable.
      An imported contribution that is every difference between the part and the stored body carries the stored
      body as its result (`result` + `base` key: volume, face / edge counts, bounding box - not the area, which
      OCC integrates differently once a shape is written and read); it is used while the body before it is
      unchanged, sparing fuses that took the kernel minutes (Locklever: timeout -> 70 s). The child regenerates
      the saved history and requires the same body (a sketch edited for a later feature had left an earlier one
      built on a state the history no longer gives); open_part checks the regenerated volume too. Inside-out
      bodies are rejected (the mesh volume is unsigned). Arcs whose two possible sweeps both lie on the stored
      boundary (one on a face in the sketch plane) are decided a little way off the plane (slots: holder,
      straight / angular slot). Full survey of all 841 parts (isolated rebuild, 115 s, as the app runs it):
      525 exact, 363 fully parametric (previous full survey on the code before this pass: 473 / 360; 489 / 362
      was the best partial count). Failures: ~90 body mismatch, 48 over time, 20 inexact, the rest individual.
- [x] Persistent naming (naming.py): after every feature the body's faces are named - a face on the surface of an
      earlier face keeps its name (booleans trim faces, not surfaces), new faces of sketch features are named
      after the sketch entity they were swept from or as caps, others (fillets, shells) after the named faces
      they meet; split faces get #k in a fixed order. face_ref / edge_ref record the names (an edge: the faces on
      either side) and resolve_face / resolve_edge find them by name before geometry. Test: a fillet on the high
      step of a stepped block stays on it when the steps swap heights (by geometry alone it moved to the other
      block). About 1 ms per face.
- [x] Assembly import: mates on straight edges and vertices (new mate entities `line` and `vertex`, residuals for
      coincident / distance / parallel / perpendicular with planes, axes, lines and vertices) chosen from the saved
      pose - the file names a face for them, so its edges and vertices are candidates and the first pair on which
      the mate holds is taken; distance / angle values must be among the doubles in the mate's record. Mates to
      the assembly's own planes / origin import against a fixed `(assembly)` frame instead of fixing the part.
      40 small sample assemblies: 125 of 155 mates imported (81 %; 54 % before). Left: in-place mates (no
      entities), a few unresolvable faces, ball valve (a part's ShapeFix hangs in the reader).
- [x] Opening speed: disk cache (cache.py; %LOCALAPPDATA%\openswx\cache, 1 GB, OPENSWX_CACHE=off) of part bodies
      and rebuilt histories keyed by the file bytes and the reader code: reopening Jet (sprinkler) 13.7 s -> 0.5 s.
      Persistent-name strings no longer nest (an imported 300-face body named in 7 s instead of hanging). Blend
      seam placement uses ShapeAnalysis_Surface on ~16 points per curve.
- [~] .slddrw: feature control frames (<IGTOL-..> tag, tolerance, diameter zone, position) come in with the sheets
      (one sample has one with a tolerance). Not decoded: datum references (no sample has one), balloons (no
      sample has one), dimension text positions (need SOLIDWORKS-made test drawings; not run: the user stopped
      the SOLIDWORKS oracle).
- [x] Installer: rebuilt with the new modules; silent install, MCP (364 tools, a .sldprt opened with its history
      through the installed runtime) and uninstall verified. Uninstall now also removes the emptied
      .oswprt / .oswasm / .oswdrw keys (uninsdeletekeyifempty). Desktop launch not re-tested here.
