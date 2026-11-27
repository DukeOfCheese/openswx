// Title bar, menu bar, quick access, CommandManager, the Heads-up View toolbar and the pane tabs.

import { icon } from "../icons.js";
import { el, escapeHtml, SPECS } from "../propman.js";
import { menuTree, renderMenu } from "../more.js";
import { $ } from "../lib/dom.js";
import { cap } from "../lib/helpers.js";

const MIXIN = {
  renderTitle() {
    const m = this.model; const name = m.name + (m.dirty ? " *" : "");
    $("#doctitle").textContent = `${name} - openswx`;
    document.title = `${name} - openswx`;
    $("#docname-corner").textContent = this.sk.active ? `${m.name} (Default) - Editing ${this.sk.name}` : `${m.name} (Default)`;
  },

  buildQuickAccess() {
    const qa = $("#quickaccess");
    const items = [["new", "New (Ctrl+N)", () => this.newPart()], ["open", "Open (Ctrl+O)", () => this.openFile()], ["save", "Save (Ctrl+S)", () => this.save()],
      ["print", "Print / export image", () => window.open("/api/image?view=isometric&width=1600&height=1200")], null,
      ["undo", "Undo (Ctrl+Z)", () => this.undoRedo("undo")], ["redo", "Redo (Ctrl+Y)", () => this.undoRedo("redo")], null,
      ["select", "Select", () => this.cancelAll()], ["rebuild", "Rebuild (Ctrl+B)", () => this.run("rebuild")], ["options", "Options", () => this.optionsDialog()]];
    for (const it of items) {
      if (!it) { qa.append(el("span", "hu-sep")); continue; }
      const b = el("button", "qa", icon(it[0])); b.title = it[1]; b.setAttribute("aria-label", it[1]); b.onclick = it[2]; b.dataset.k = it[0]; qa.append(b);
    }
  },

  renderQuickAccessState() {
    const q = (k) => document.querySelector(`.qa[data-k="${k}"]`);
    const cd = this.collabDoc(); // (a cloud document: undo takes back only your own changes, which the server keeps)
    q("undo").disabled = !(cd ? cd.can_undo : this.model.can_undo); q("redo").disabled = !(cd ? cd.can_redo : this.model.can_redo);
  },

  undoRedo(which) {
    const cd = this.collabDoc();
    if (cd && !cd[`can_${which}`]) return Promise.resolve();
    return this.run(which).catch(() => {});
  },

  buildMenus() {
    const asm = () => this.model?.kind === "assembly";
    const F = (k) => () => this.feature(k);
    const partInsert = [
      ["Boss/Base > Extrude...", "", F("extrude")], ["Boss/Base > Revolve...", "", F("revolve")], ["Boss/Base > Sweep...", "", F("sweep")],
      ["Boss/Base > Loft...", "", F("loft")], ["Boss/Base > Thicken...", "", F("thicken")],
      ["Cut > Extrude...", "", F("cut_extrude")], ["Cut > Revolve...", "", F("cut_revolve")], ["Cut > Sweep...", "", F("cut_sweep")],
      ["Cut > Loft...", "", F("cut_loft")], ["Cut > With Surface...", "", F("cut_with_surface")],
      ["Features > Fillet/Round...", "", F("fillet")], ["Features > Conic / Curvature Continuous Fillet...", "", F("fillet_profile")],
      ["Features > FilletXpert...", "", F("fillet_xpert")], ["Features > Chamfer...", "", F("chamfer")], "-",
      ["Features > Hole > Simple...", "", F("hole")], ["Features > Hole > Wizard...", "", F("hole_wizard")], ["Features > Thread...", "", F("thread")],
      ["Features > Shell...", "", F("shell")], ["Features > Rib...", "", F("rib")], ["Features > Draft...", "", F("draft")], ["Features > Dome...", "", F("dome")],
      ["Features > Wrap...", "", F("wrap")], ["Features > Flex...", "", F("flex")], ["Features > Deform...", "", F("deform")], ["Features > Freeform...", "", F("freeform")],
      ["Features > Combine...", "", () => this.bodyDialog("combine")], ["Features > Delete/Keep Body...", "", () => this.bodyDialog("delete")],
      ["Features > Move/Copy Body...", "", () => this.bodyDialog("move")], ["Features > Scale...", "", () => this.bodyDialog("scale")],
      ["Features > Split...", "", F("split_body")], ["Features > Intersect...", "", F("intersect")], ["Features > Indent...", "", F("indent")],
      ["Pattern/Mirror > Linear Pattern...", "", F("linear_pattern")], ["Pattern/Mirror > Circular Pattern...", "", F("circular_pattern")],
      ["Pattern/Mirror > Mirror...", "", F("mirror")], ["Pattern/Mirror > Sketch Driven Pattern...", "", F("sketch_pattern")],
      ["Pattern/Mirror > Curve Driven Pattern...", "", F("curve_pattern")], ["Pattern/Mirror > Table Driven Pattern...", "", F("table_pattern")],
      ["Pattern/Mirror > Fill Pattern...", "", F("fill_pattern")],
      ["Surface > Extruded...", "", F("extruded_surface")], ["Surface > Revolved...", "", F("revolved_surface")], ["Surface > Swept...", "", F("swept_surface")],
      ["Surface > Lofted...", "", F("lofted_surface")], ["Surface > Boundary...", "", F("boundary_surface")], ["Surface > Planar...", "", F("planar_surface")],
      ["Surface > Filled > From Sketch...", "", F("filled_surface")], ["Surface > Filled > From Edges...", "", F("filled_surface_edges")],
      ["Surface > Offset...", "", F("offset_surface")], ["Surface > Ruled...", "", F("ruled_surface")], ["Surface > Radiate...", "", F("radiate_surface")],
      ["Surface > Extend...", "", F("extend_surface")], ["Surface > Untrim...", "", F("untrim_surface")], ["Surface > Flatten...", "", F("flatten_surface")],
      ["Surface > Knit...", "", F("knit_surface")], ["Surface > Trim...", "", F("trim_surface")],
      ["Face > Delete...", "", F("delete_face")], ["Face > Move...", "", F("move_face")], ["Face > Replace...", "", F("replace_face")],
      ["Curve > Helix/Spiral...", "", F("helix")], ["Curve > Projected...", "", F("projected_curve")], ["Curve > Composite...", "", F("composite_curve")],
      ["Curve > Split Line...", "", F("split_line")], ["Curve > Curve Through XYZ Points...", "", F("xyz_curve")],
      ["Reference Geometry > Plane...", "", F("plane")], ["Reference Geometry > Axis...", "", F("ref_axis")], ["Reference Geometry > Coordinate System...", "", F("csys")],
      ["Reference Geometry > Point...", "", F("ref_point")], ["Reference Geometry > Mate Reference...", "", F("mate_reference")],
      ["Reference Geometry > Connection Point...", "", F("connection_point")],
      ["Sheet Metal > Base Flange/Tab...", "", F("base_flange")], ["Sheet Metal > Edge Flange...", "", F("edge_flange")], ["Sheet Metal > Miter Flange...", "", F("miter_flange")],
      ["Sheet Metal > Hem...", "", F("hem")], ["Sheet Metal > Unfold...", "", F("unfold")], ["Sheet Metal > Fold...", "", F("fold")], ["Sheet Metal > Rip...", "", F("rip")], ["Sheet Metal > Vent...", "", F("vent")], ["Sheet Metal > Jog...", "", F("jog")], ["Sheet Metal > Sketched Bend...", "", F("sketched_bend")],
      ["Sheet Metal > Flatten / Fold", "", () => this.run("flatten", { flat: !this.model?.flattened })],
      ["Weldments > Structural Member...", "", F("structural_member")], ["Weldments > End Cap...", "", F("end_cap")], ["Weldments > Gusset...", "", F("gusset")],
      ["Molds > Parting Line...", "", F("parting_line")], ["Molds > Shut-Off Surfaces...", "", F("shut_off_surfaces")],
      ["Molds > Parting Surface...", "", F("parting_surface")], ["Molds > Tooling Split...", "", F("tooling_split")], "-",
      ["Part...", "", F("insert_part")], ["Mirror Part...", "", F("mirror_part")], ["Library Feature...", "", () => this.designLibraryDialog()], "-",
      ["Annotations > Cosmetic Thread...", "", F("cosmetic_thread")], ["Annotations > DimXpert Auto Dimension Scheme...", "", () => this.dimxpertDialog()],
      ["Annotations > Show / Hide 3D Annotations", "", () => this.togglePMI()], ["Annotations > Delete DimXpert Annotations", "", () => this.run("dimxpert_clear")], "-",
      ["Sketch", "", () => this.sketchCommand()], ["3D Sketch...", "", () => this.sketch3dDialog()]];
    const asmInsert = [
      ["Component > Existing Part/Assembly...", "", () => this.insertComponentDialog()], ["Component > Toolbox...", "", () => this.toolboxDialog()],
      ["Component > Smart Fasteners", "", () => this.smartFasteners()], ["Component > From Design Library...", "", () => this.designLibraryDialog()],
      ["Mate...", "", () => this.openPM(SPECS.mate)], ["SmartMates...", "", () => this.feature("smart_mate")], ["Magnetic Mate (snap)", "", () => this.magneticSnap()], "-",
      ["Component Pattern > Linear...", "", () => this.componentPatternDialog("linear")], ["Component Pattern > Circular...", "", () => this.componentPatternDialog("circular")],
      ["Mirror Components...", "", () => this.mirrorComponentsDialog()],
      ["Assembly Feature > Hole...", "", () => this.asmCutDialog("hole")], ["Assembly Feature > Extruded Cut...", "", () => this.asmCutDialog("extrude")],
      ["Assembly Feature > Hole Series...", "", () => this.holeSeriesDialog()], ["Assembly Feature > Weld Bead...", "", () => this.feature("weld_bead")],
      ["Routing > Pipe (Standard)...", "", () => this.pipeRouteDialog()], ["Routing > Tube (Custom Size)...", "", () => this.routeDialog("pipe")],
      ["Routing > Insert Fitting...", "", () => this.fittingDialog()], ["Routing > Cable / Wire...", "", () => this.routeDialog("cable")],
      ["Routing > Electrical Connector...", "", () => this.connectorDialog()], ["Routing > Harness Route Segment...", "", () => this.harnessRouteDialog()],
      ["Routing > From-To Wire List...", "", () => this.fromToDialog()], ["Routing > Build Harness", "", () => this.harnessBuild()], "-",
      ["Exploded View...", "", () => this.explodeDialog()], ["Alternate Position...", "", () => this.altPositionsDialog()]];
    const M = {
      File: () => [["Home", "Ctrl+Shift+H", () => this.homeShow()], "-", ["New Part...", "Ctrl+N", () => this.newPart()], ["New Assembly...", "", () => this.newAssembly()], ["Open...", "Ctrl+O", () => this.openFile()],
        ["Import STEP...", "", () => this.openFile("import")], ["Make Drawing from Part/Assembly", "", () => this.makeDrawing()], "-",
        ["Save", "Ctrl+S", () => this.save()], ["Save As...", "", () => this.save(true)], ["Download .oswprt", "", () => window.open("/api/download?format=oswprt")],
        ...(asm() ? [["Pack and Go...", "", () => this.packAndGoDialog()]] : []), "-",
        ["Export > STEP", "", () => this.exportAs("step")], ["Export > Parasolid (x_t)", "", () => this.exportAs("x_t")], ["Export > STL", "", () => this.exportAs("stl")],
        ["Export > IGES", "", () => this.exportAs("iges")], ["Export > 3MF (3D printing)", "", () => this.exportAs("3mf")], ["Export > glTF (GLB)", "", () => this.exportAs("glb")],
        ...this.pluginExports().map((f) => [`Export > ${f.name.replace(/>/g, "›")}`, "", () => this.exportAs(f.id)]),
        ["Export > Image (PNG)", "", () => (this.desk ? this.deskCall("save_image") : window.open("/api/image?view=isometric&width=1600&height=1200"))],
        ["Export > Flat Pattern DXF...", "", () => this.exportFlatDxf()], ["Export > Sketch as DXF...", "", () => this.exportSketchDxf()],
        ["Import DXF into Sketch...", "", () => this.importDxf()], ["Publish eDrawings File...", "", () => this.publishEDrawing()], "-",
        ["PDM > Vault...", "", () => this.pdmDialog()], ["PDM > Check In...", "", () => this.pdmDialog()], "-",
        ["Close", "", () => this.run("close_document")], ...(window.pywebview ? [["Exit", "Alt+F4", () => window.close()]] : [])],
      Edit: [["Undo", "Ctrl+Z", () => this.undoRedo("undo")], ["Redo", "Ctrl+Y", () => this.undoRedo("redo")], "-", ["Rebuild", "Ctrl+B", () => this.run("rebuild")],
        ["Delete", "Del", () => this.deleteSelection()], ["Suppress", "", () => this.suppressSelected(true)], ["Unsuppress", "", () => this.suppressSelected(false)]],
      View: [["Zoom to Fit", "F", () => this.vp.zoomFit()], ["Zoom to Area", "", () => this.startZoomArea()], ["Zoom to Selection", "", () => this.zoomToSelection()],
        ["Previous View", "Ctrl+Shift+Z", () => this.vp.previousView()], "-",
        ...["front", "back", "left", "right", "top", "bottom", "isometric", "trimetric"].map((v, i) => [`Orientation > ${cap(v)}`, i < 7 ? `Ctrl+${i + 1}` : "", () => this.vp.setView(v)]),
        ["New View...", "", () => this.newNamedView()],
        ["Display > Shaded With Edges", "", () => this.vp.applyStyle("shaded_edges")], ["Display > Shaded", "", () => this.vp.applyStyle("shaded")],
        ["Display > Hidden Lines Removed", "", () => this.vp.applyStyle("hlr")], ["Display > Wireframe", "", () => this.vp.applyStyle("wireframe")],
        ["Display > Perspective", "", () => this.togglePerspective()], "-",
        ["Hide/Show > Sketch Relations", "", () => { this.showRelations = !this.showRelations; this.vp.dirty = true; }],
        ["Hide/Show > Planes", "", () => this.vp.togglePlanes()], ["Hide/Show > 3D Annotations (PMI)", "", () => this.togglePMI()],
        ["Hide/Show > View Cube", "", () => document.querySelector(".vcube")?.classList.toggle("hidden")]],
      Insert: () => (asm() ? asmInsert : partInsert),
      Tools: () => [["Sketch Entities > Line", "", () => this.setSketchTool("line")], ["Sketch Entities > Rectangle", "", () => this.setSketchTool("rectangle")], ["Sketch Entities > Equation Driven Curve...", "", () => this.equationCurve()],
        ["Sketch Entities > Circle", "", () => this.setSketchTool("circle")], ["Dimensions > Smart", "", () => this.setSketchTool("smart_dim")], "-",
        ["Evaluate > Measure", "", () => this.measure()], ["Evaluate > Mass Properties", "", () => this.massProps()], ["Evaluate > Section Properties", "", () => this.sectionPropsDialog()],
        ...(asm() ? [["Evaluate > Interference Detection", "", () => this.interference()]] : [["Evaluate > Check", "", () => this.checkGeometry()],
          ["Evaluate > Draft Analysis", "", () => this.draftDialog()], ["Evaluate > Undercut Detection", "", () => this.undercutDialog()]]),
        ["Evaluate > Costing...", "", () => this.costingDialog()], ["Evaluate > Sustainability...", "", () => this.sustainabilityDialog()],
        ["Simulation > Static > Run", "", () => this.simRun()], ["Simulation > Static > Stress Plot", "", () => this.simPlot("stress")],
        ["Simulation > Frequency", "", () => this.modeStudy("frequency")], ["Simulation > Buckling", "", () => this.modeStudy("buckling")],
        ["Simulation > Thermal", "", () => this.thermalRun()], ["Simulation > Nonlinear...", "", () => this.nonlinearDialog()],
        ["Simulation > Dynamic > Harmonic...", "", () => this.harmonicDialog()], ["Simulation > Dynamic > Transient...", "", () => this.transientDialog()],
        ["Simulation > Drop Test...", "", () => this.dropTestDialog()], ["Simulation > Fatigue...", "", () => this.fatigueDialog()],
        ["Simulation > Hide Results", "", () => this.simPlot(null)],
        ["Flow Simulation...", "", () => this.flowDialog()], "-",
        ...(asm() ? [["Electrical > Connector...", "", () => this.connectorDialog()], ["Electrical > Route Segment...", "", () => this.harnessRouteDialog()],
          ["Electrical > From-To List...", "", () => this.fromToDialog()], ["Electrical > Build Harness", "", () => this.harnessBuild()],
          ["Electrical > Formboard Drawing...", "", () => this.formboardSave()], ["Electrical > Clear", "", () => this.run("electrical_clear", {})],
          ["Composer > Steps from Exploded View...", "", () => this.composerAuto()], ["Composer > Add Step...", "", () => this.composerStep()],
          ["Composer > Publish Instructions...", "", () => this.composerExport()], ["Composer > Clear Steps", "", () => this.run("composer_clear", {})]]
          : [["CAM > Generate Toolpaths...", "", () => this.camDialog()], ["CAM > Add 3D Finishing...", "", () => this.camFinish3d()], ["CAM > Show Toolpaths", "", () => this.camShow()],
            ["CAM > Hide Toolpaths", "", () => this.camHide()], ["CAM > Save G-code...", "", () => this.camGcode()],
            ["Plastics > Fill Analysis...", "", () => this.plasticsDialog()], ["Plastics > Fill Time", "", () => this.plasticsPlot("fill_time")],
            ["Plastics > Pressure at End of Fill", "", () => this.plasticsPlot("pressure")], ["Plastics > Wall Thickness", "", () => this.plasticsPlot("thickness")],
            ["Plastics > Weld Lines", "", () => this.plasticsPlot("weld")], ["Plastics > Sink Marks", "", () => this.plasticsPlot("sink")],
            ["Plastics > Hide Results", "", () => this.plasticsPlot(null)], ["Inspection...", "", () => this.inspectionDialog()]]), "-",
        ["DimXpert > Auto Dimension Scheme...", "", () => this.dimxpertDialog()], ["DimXpert > Show / Hide", "", () => this.togglePMI()],
        ["DimXpert > Delete Scheme", "", () => this.run("dimxpert_clear")],
        ["Render Tools > Render...", "", () => this.renderDialog()], ["Render Tools > Decal...", "", () => this.feature("add_decal")],
        ...(asm() ? [["Assembly > Envelope (selected)", "", () => this.compToggle("set_envelope", "envelope", "Envelope")],
          ["Assembly > SpeedPak (selected)", "", () => this.compToggle("set_speedpak", "speedpak", "SpeedPak")],
          ["Assembly > Large Assembly Mode", "", () => this.run("large_assembly_mode", { on: !this.model.large_assembly_mode })],
          ["Assembly > Defeature...", "", () => this.defeatureDialog()]] : [["Defeature...", "", () => this.defeatureDialog()]]),
        ["Design Library...", "", () => this.designLibraryDialog()], ["PDM Vault...", "", () => this.pdmDialog()],
        ["Macro > Record / Stop", "", () => this.macroRecord()], ["Macro > Stop and Save...", "", () => this.macroStop()], ["Macro > Run...", "", () => this.macroRun()], "-",
        ["Equations...", "", () => this.equationsDialog()], ["Dimension Tolerances...", "", () => this.toleranceDialog()], ["Requirements...", "", () => this.requirementsDialog()], ["Design Review", "", () => this.reviewDialog()], ["Export Script (.py)...", "", () => this.exportScript()], ["Hand-off Pack...", "", () => this.handoffPack()], "-", ["Assembly > Display States...", "", () => this.displayStatesDialog()], ["Assembly > Configurations...", "", () => this.asmConfigDialog()], ["User Settings...", "", () => this.userSettingsDialog()], ["Customize Keyboard...", "", () => this.customizeKeyboardDialog()], ["Design Table...", "", () => this.designTableDialog()], ["Material...", "", () => this.materialDialog()],
        ["Appearance...", "", () => this.appearanceDialog()], ["Properties...", "", () => this.propertiesDialog()], ["Options...", "", () => this.optionsDialog()]],
      Collaborate: () => this.collabMenu(),
      Window: () => [...(this.docs?.documents || []).map((d) => [`${d.name === this.model.name ? "✓ " : ""}${d.name}`.replace(/ > /g, " › "), "", () => this.switchDocument(d.name)]),
        "-", ["Next Document", "Ctrl+Tab", () => this.nextDocument()], ["Close", "Ctrl+W", () => this.run("close_document").then(() => this.vp.zoomFit())]],
      Help: [["Keyboard Shortcuts", "", () => this.showTask("help")], ["Tutorials...", "", () => this.tutorialDialog()], ["Agent (MCP) Setup", "", () => this.mcpDialog()], ["Save Diagnostic Report...", "", () => this.diagnosticReport()], ["Check for Updates", "", () => this.checkUpdates()], ["About openswx", "", () => this.about()]],
    };
    const bar = $("#menubar");
    for (const [name, items] of Object.entries(M)) {
      const root = el("div", "menu-root", name), drop = el("div", "menu-drop");
      // "A > B" labels become cascading submenus that open on hover
      const fill = () => renderMenu(drop, menuTree((typeof items === "function" ? items() : items).map((it) => Array.isArray(it) ? [it[0], this.menuKeyLabel(it[1]), it[2]] : it)), (fn) => { this.closeMenus(); fn(); });
      fill(); root.append(drop);
      root.onclick = (e) => { e.stopPropagation(); const open = root.classList.contains("open"); this.closeMenus(); if (!open) { fill(); root.classList.add("open"); bar.classList.add("open"); } };
      root.onmouseenter = () => { if (bar.querySelector(".menu-root.open") && !root.classList.contains("open")) { this.closeMenus(); fill(); root.classList.add("open"); bar.classList.add("open"); } };
      bar.append(root);
    }
    document.addEventListener("click", () => { this.closeMenus(); $("#ctxmenu").classList.add("hidden"); document.querySelectorAll(".hu-drop.open").forEach((d) => d.classList.remove("open")); });
    this.menuDefs = M;
    this.syncNativeMenu();
  },

  // macOS desktop app: the menus go in the system menu bar at the top of the screen instead of the window (the
  // desktop shell builds them from this tree; each item carries an id that menuInvoke runs). Items show no key
  // equivalents there: the keys stay with the page, so typing in a text box is never taken as a command.
  nativeMenuTree() {
    const fns = [];
    const walk = (nodes) => nodes.map((n) => (n === "-" ? "-" : n.children ? { title: n.label, items: walk(n.children) }
      : { title: n.label, id: fns.push(n.fn) - 1 }));
    const menus = Object.entries(this.menuDefs || {}).map(([title, items]) => ({ title, items: walk(menuTree(typeof items === "function" ? items() : items)) }));
    return { menus, fns };
  },

  // Desktop app: the window has no title bar of its own; this page's is it (shell/desktop.py,
  // integrate_title_bar). On Windows the page draws minimise / maximise / close and hands a press on the bare bar
  // to the system as a window drag (a double click maximises); on macOS the window's own buttons sit over the
  // bar's left end and the shell moves the window when the bar is dragged.
  async windowChrome() {
    const api = this.desk;
    if (this.chromeDone || !api?.window_chrome) return;
    this.chromeDone = true;
    const kind = await api.window_chrome().catch(() => "");
    if (kind === "mac") {
      document.body.classList.add("mac-chrome");
      for (const s of ["#titlebar", "#doctitle"]) $(s).classList.add("pywebview-drag-region");
      return;
    }
    if (kind !== "windows") return;
    document.body.classList.add("win-chrome");
    const box = el("div", ""); box.id = "winctl";
    for (const [act, title, d] of [["minimize", "Minimize", "M1 5.5h8"], ["maximize", "Maximize / Restore", "M1.5 1.5h7v7h-7z"], ["close", "Close", "M1 1l8 8M9 1l-8 8"]]) {
      const b = el("button", `wc wc-${act}`, `<svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1" aria-hidden="true"><path d="${d}"/></svg>`);
      b.title = title; b.setAttribute("aria-label", title); b.onclick = () => api.window_control(act); box.append(b);
    }
    document.body.append(box);
    // the bare bar: the top 30px of the title bar (or of Home, which covers it), not a menu, button or field on it
    const bare = (e) => e.button === 0 && e.clientY <= 30 && e.target.closest("#titlebar, #home") && !e.target.closest("button, input, a, .menu-root, #searchresults");
    document.addEventListener("mousedown", (e) => { if (bare(e) && e.detail === 1) api.window_control("drag"); });
    document.addEventListener("dblclick", (e) => { if (bare(e)) api.window_control("maximize"); });
  },

  async syncNativeMenu() {
    this.windowChrome();
    const api = this.desk;
    if (!api?.set_native_menu || !/Mac/i.test(navigator.platform || navigator.userAgent)) return;
    const { menus, fns } = this.nativeMenuTree();
    this.nativeMenuFns = fns; // (always: the Window menu's items change with the documents)
    const json = JSON.stringify(menus);
    if (json === this.nativeMenuJson) return;
    this.nativeMenuJson = json;
    const ok = await api.set_native_menu(menus).catch(() => false);
    document.body.classList.toggle("native-menu", !!ok); // (if the shell could not, the window keeps its own menu bar)
  },

  menuInvoke(id) { const fn = this.nativeMenuFns?.[id]; if (fn) { this.closeMenus(); fn(); } },

  closeMenus() { document.querySelectorAll(".menu-root.open").forEach((r) => r.classList.remove("open")); if (!document.querySelector(".menu-root.open")) $("#menubar").classList.remove("open"); },

  renderCommandManager() {
    const sketching = this.sk.active;
    const B = (k, label, fn, opts = {}) => ({ k, label, fn, ...opts });
    const tabs = {
      Features: [
        [B("extrude", "Extruded<br>Boss/Base", () => this.feature("extrude")), B("revolve", "Revolved<br>Boss/Base", () => this.feature("revolve"))],
        [B("cut_extrude", "Extruded<br>Cut", () => this.feature("cut_extrude")), B("hole", "Hole<br>Wizard", () => this.feature("hole_wizard")), B("cut_revolve", "Revolved<br>Cut", () => this.feature("cut_revolve")),
          { stack: [B("hole", "Simple Hole", () => this.feature("hole")), B("thread", "Thread", () => this.feature("thread")), B("rib", "Rib", () => this.feature("rib"))] },
          { stack: [B("revolve", "Dome", () => this.feature("dome"))] }],
        [{ stack: [B("sweep", "Swept Boss/Base", () => this.feature("sweep")), B("loft", "Lofted Boss/Base", () => this.feature("loft"))] },
          { stack: [B("cut_sweep", "Swept Cut", () => this.feature("cut_sweep")), B("cut_loft", "Lofted Cut", () => this.feature("cut_loft"))] }],
        [B("fillet", "Fillet", () => this.feature("fillet")), { stack: [B("chamfer", "Chamfer", () => this.feature("chamfer")), B("vertex_chamfer", "Vertex Chamfer", () => this.feature("vertex_chamfer")),
          B("wrap", "Wrap", () => this.feature("wrap"))] },
          { stack: [B("face_fillet", "Face Fillet", () => this.feature("face_fillet")), B("full_round_fillet", "Full Round Fillet", () => this.feature("full_round_fillet"))] }],
        [B("linear_pattern", "Linear<br>Pattern", () => this.feature("linear_pattern")), { stack: [B("circular_pattern", "Circular Pattern", () => this.feature("circular_pattern")), B("mirror", "Mirror", () => this.feature("mirror"))] },
          { stack: [B("sketch_pattern", "Sketch Driven Pattern", () => this.feature("sketch_pattern")), B("curve_pattern", "Curve Driven Pattern", () => this.feature("curve_pattern"))] },
          { stack: [B("table_pattern", "Table Driven Pattern", () => this.feature("table_pattern")), B("fill_pattern", "Fill Pattern", () => this.feature("fill_pattern"))] }],
        [B("shell", "Shell", () => this.feature("shell")), { stack: [B("draft_feat", "Draft", () => this.feature("draft")), B("cut_extrude", "Split", () => this.feature("split_body")), B("intersection_curve", "Split Line", () => this.feature("split_line"))] },
          { stack: [B("trash", "Delete Face", () => this.feature("delete_face")), B("offset", "Move Face", () => this.feature("move_face")),
            B("surface", "Replace Face", () => this.feature("replace_face"))] }],
        [B("plane", "Reference<br>Geometry", () => this.feature("plane")), { stack: [B("ref_axis", "Axis", () => this.feature("ref_axis")), B("csys", "Coordinate System", () => this.feature("csys")), B("ref_point", "Point", () => this.feature("ref_point"))] },
          { stack: [B("helix", "Helix and Spiral", () => this.feature("helix")), B("sketch3d", "3D Sketch", () => this.sketch3dDialog())] },
          { stack: [B("intersection_curve", "Projected Curve", () => this.feature("projected_curve")), B("sketch3d", "Composite Curve", () => this.feature("composite_curve")),
            B("spline", "Curve Through XYZ Points", () => this.feature("xyz_curve"))] },
          { stack: [B("equations", "Equations", () => this.equationsDialog()), B("properties", "Design Table", () => this.designTableDialog())] }, { stack: [B("appearance", "Edit Appearance", () => this.appearanceDialog()), B("material", "Edit Material", () => this.materialDialog()),
            B("properties", "Properties", () => this.propertiesDialog())] }],
        [{ stack: [B("revolve", "Flex", () => this.feature("flex")), B("dimple", "Deform", () => this.feature("deform")), B("dimple", "Freeform", () => this.feature("freeform"))] },
          { stack: [B("fillet", "Conic / G2 Fillet", () => this.feature("fillet_profile")), B("fillet", "FilletXpert", () => this.feature("fillet_xpert")),
            B("thread", "Cosmetic Thread", () => this.feature("cosmetic_thread"))] },
          { stack: [B("import", "Insert Part", () => this.feature("insert_part")), B("mirror", "Mirror Part", () => this.feature("mirror_part")),
            B("folder", "Design Library", () => this.designLibraryDialog())] }],
      ],
      "Mold Tools": [[B("intersection_curve", "Parting<br>Line", () => this.feature("parting_line")), B("planar_surface", "Shut-off<br>Surfaces", () => this.feature("shut_off_surfaces")),
          B("surface", "Parting<br>Surface", () => this.feature("parting_surface")), B("cut_extrude", "Tooling<br>Split", () => this.feature("tooling_split"))],
        [{ stack: [B("draft_analysis", "Draft Analysis", () => this.draftDialog()), B("draft_analysis", "Undercut Detection", () => this.undercutDialog()), B("draft_feat", "Draft", () => this.feature("draft"))] },
          { stack: [B("planar_surface", "Planar Surface", () => this.feature("planar_surface")), B("knit_surface", "Knit Surface", () => this.feature("knit_surface")),
            B("filled_surface", "Filled Surface (edges)", () => this.feature("filled_surface_edges"))] }]],
      Sketch: [
        [B(sketching ? "exit_sketch" : "sketch", sketching ? "Exit<br>Sketch" : "Sketch", () => (sketching ? this.exitSketch() : this.sketchCommand()), { active: sketching }),
          B("sketch3d", "3D<br>Sketch", () => this.sketch3dDialog()),
          B("smart_dim", "Smart<br>Dimension", () => this.setSketchTool("smart_dim"), { tool: "smart_dim" })],
        [{ stack: [B("line", "Line", () => this.setSketchTool("line"), { tool: "line" }), B("centerline", "Centerline", () => this.setSketchTool("centerline"), { tool: "centerline" }), B("point", "Point", () => this.setSketchTool("point"), { tool: "point" })] },
          { stack: [B("rectangle", "Corner Rectangle", () => this.setSketchTool("rectangle"), { tool: "rectangle" }), B("center_rectangle", "Center Rectangle", () => this.setSketchTool("center_rectangle"), { tool: "center_rectangle" }), B("slot", "Straight Slot", () => this.setSketchTool("slot"), { tool: "slot" })] },
          { stack: [B("circle", "Circle", () => this.setSketchTool("circle"), { tool: "circle" }), B("arc", "Centerpoint Arc", () => this.setSketchTool("arc"), { tool: "arc" }), B("arc3", "3 Point Arc", () => this.setSketchTool("arc3"), { tool: "arc3" })] },
          { stack: [B("circle", "Perimeter Circle", () => this.setSketchTool("circle3"), { tool: "circle3" }), B("arc", "Tangent Arc", () => this.setSketchTool("arc_tangent"), { tool: "arc_tangent" })] },
          { stack: [B("polygon", "Polygon", () => this.setSketchTool("polygon"), { tool: "polygon" }), B("ellipse", "Ellipse", () => this.setSketchTool("ellipse"), { tool: "ellipse" }),
            B("spline", "Spline", () => this.setSketchTool("spline"), { tool: "spline" })] },
          { stack: [B("sk_text", "Text", () => this.setSketchTool("text"), { tool: "text" }), B("spline", "Conic", () => this.setSketchTool("conic"), { tool: "conic" }),
            B("sk_fillet", "Sketch Chamfer", () => this.sketchChamfer(), { tool: "sk_chamfer" })] }],
        [{ stack: [B("trim", "Trim Entities", () => this.setSketchTool("trim"), { tool: "trim" }), B("extend", "Extend Entities", () => this.setSketchTool("extend"), { tool: "extend" }),
          B("sk_fillet", "Sketch Fillet", () => this.sketchFillet(), { tool: "sk_fillet" }), B("trim", "Split Entities", () => this.setSketchTool("split"), { tool: "split" })] },
          { stack: [B("sk_linear_pattern", "Move / Copy Entities", () => this.sketchTransformDialog("move")), B("sk_circular_pattern", "Rotate Entities", () => this.sketchTransformDialog("rotate")),
            B("offset", "Scale Entities", () => this.sketchTransformDialog("scale"))] },
          { stack: [B("offset", "Offset Entities", () => this.sketchOffset()), B("sk_mirror", "Mirror Entities", () => this.sketchMirror()),
            B("convert", "Convert Entities", () => this.convertEntities())] },
          { stack: [B("sk_linear_pattern", "Linear Sketch Pattern", () => this.sketchPattern("linear")), B("sk_circular_pattern", "Circular Sketch Pattern", () => this.sketchPattern("circular")),
            B("intersection_curve", "Intersection Curve", () => this.intersectionCurve())] }],
        [B("relation", "Add<br>Relation", () => this.relationsHint()), { stack: [B("construction", "Construction Geometry", () => this.toggleConstruction()), B("delete_rel", "Delete Relations", () => this.deleteRelationsPM())] }],
      ],
      "Sheet Metal": [[B("base_flange", "Base<br>Flange/Tab", () => this.feature("base_flange")), B("edge_flange", "Edge<br>Flange", () => this.feature("edge_flange")),
          { stack: [B("hem", "Hem", () => this.feature("hem")), B("sketched_bend", "Sketched Bend", () => this.feature("sketched_bend")),
            B("jog", "Jog", () => this.feature("jog"))] },
          { stack: [B("unfold", "Unfold", () => this.feature("unfold")), B("fold", "Fold", () => this.feature("fold")), B("rip", "Rip", () => this.feature("rip"))] },
          { stack: [B("vent", "Vent", () => this.feature("vent"))] },
          { stack: [B("miter_flange", "Miter Flange", () => this.feature("miter_flange")), B("corner_relief", "Corner Relief", () => this.feature("corner_relief")),
            B("dimple", "Forming Tool (Dimple)", () => this.feature("dimple"))] },
          { stack: [B("louver", "Louver", () => this.feature("louver")), B("lance", "Bridge Lance", () => this.feature("lance")),
            B("corner_relief", "Corner Relief (all corners)", () => this.run("corner_relief_auto", {}))] },
          { stack: [B("extruded_flange", "Extruded Flange", () => this.feature("extruded_flange")), B("form_rib", "Rib (forming tool)", () => this.feature("form_rib"))] }],
        [B("flatten", this.model?.flattened ? "Fold" : "Flatten", () => this.run("flatten", { flat: !this.model?.flattened })), B("properties", "Sheet Metal<br>Info", () => this.sheetMetalInfo())]],
      Surfaces: [[B("surface", "Extruded<br>Surface", () => this.feature("extruded_surface")),
          { stack: [B("revolved_surface", "Revolved Surface", () => this.feature("revolved_surface")), B("swept_surface", "Swept Surface", () => this.feature("swept_surface")),
            B("lofted_surface", "Lofted Surface", () => this.feature("lofted_surface")), B("boundary_surface", "Boundary Surface", () => this.feature("boundary_surface"))] },
          { stack: [B("planar_surface", "Planar Surface", () => this.feature("planar_surface")), B("filled_surface", "Filled Surface", () => this.feature("filled_surface")),
            B("offset_surface", "Offset Surface", () => this.feature("offset_surface"))] }],
        [{ stack: [B("knit_surface", "Knit Surface", () => this.feature("knit_surface")), B("trim_surface", "Trim Surface", () => this.feature("trim_surface"))] },
          B("thicken", "Thicken", () => this.feature("thicken")),
          { stack: [B("cut_extrude", "Cut With Surface", () => this.feature("cut_with_surface")), B("surface", "Replace Face", () => this.feature("replace_face")),
            B("knit_surface", "Intersect", () => this.feature("intersect"))] }],
        [{ stack: [B("surface", "Ruled Surface", () => this.feature("ruled_surface")), B("surface", "Radiate Surface", () => this.feature("radiate_surface")),
          B("offset_surface", "Extend Surface", () => this.feature("extend_surface"))] },
          { stack: [B("trim_surface", "Untrim Surface", () => this.feature("untrim_surface")), B("flatten", "Surface Flatten", () => this.feature("flatten_surface")),
            B("filled_surface", "Filled Surface (edges)", () => this.feature("filled_surface_edges"))] }]],
      Weldments: [[B("weldment", "Structural<br>Member", () => this.feature("structural_member")),
          { stack: [B("end_cap", "End Cap", () => this.feature("end_cap")), B("gusset", "Gusset", () => this.feature("gusset"))] }, B("measure", "Cut<br>List", () => this.cutList())]],
      Simulation: [[B("sim_fixture", "Fixed<br>Geometry", () => this.simFixture()), { stack: [B("sim_force", "Force", () => this.simLoad("force")), B("sim_pressure", "Pressure", () => this.simLoad("pressure")),
          B("trash", "Clear Fixtures/Loads", () => this.run("simulation_clear", {}))] }],
        [B("sim_run", "Run<br>Study", () => this.simRun()), { stack: [B("sim_stress", "Stress (von Mises)", () => this.simPlot("stress")), B("sim_disp", "Displacement", () => this.simPlot("displacement")),
          B("eye", "Hide Results", () => this.simPlot(null))] },
          { stack: [B("sim_frequency", "Frequency", () => this.modeStudy("frequency")), B("sim_buckling", "Buckling", () => this.modeStudy("buckling"))] },
          { stack: [B("sim_contact", "Contact (No Penetration)", () => this.simConnector("contact")), B("sim_spring", "Spring Connector", () => this.simConnector("spring")),
            B("sim_bolt", "Bolt Connector", () => this.simConnector("bolt"))] }],
        [{ stack: [B("thermal_temp", "Temperature", () => this.thermalBC("temperature")), B("thermal_heat", "Heat Power", () => this.thermalBC("heat")),
          B("thermal_conv", "Convection", () => this.thermalBC("convection"))] },
          { stack: [B("thermal_run", "Run Thermal", () => this.thermalRun()), B("thermal_plot", "Temperature Plot", () => this.thermalPlot()),
            B("trash", "Clear Thermal", () => this.run("thermal_clear", {}))] }],
        [{ stack: [B("sim_run", "Nonlinear", () => this.nonlinearDialog()), B("sim_frequency", "Dynamic - Harmonic", () => this.harmonicDialog()),
          B("sim_frequency", "Dynamic - Transient", () => this.transientDialog())] },
          { stack: [B("sim_run", "Drop Test", () => this.dropTestDialog()), B("sim_stress", "Fatigue", () => this.fatigueDialog()), B("sim_run", "Flow Simulation", () => this.flowDialog())] }]],
      Evaluate: [[B("measure", "Measure", () => this.measure()), B("mass", "Mass<br>Properties", () => this.massProps()), B("drawing", "Make<br>Drawing", () => this.makeDrawing())],
        [B("section", "Section<br>View", () => this.sectionDialog()), { stack: [B("draft_analysis", "Draft Analysis", () => this.draftDialog()), B("zebra", "Zebra Stripes", () => this.toggleZebra()),
          B("curvature", "Curvature", () => this.toggleCurvature())] },
          { stack: [B("check", "Check", () => this.checkGeometry()), B("interference", "Body Interference", () => this.bodyInterference())] },
          { stack: [B("measure", "Thickness Analysis", () => this.thicknessDialog()), B("draft_analysis", "Undercut Detection", () => this.undercutDialog()),
            B("section", "Section Properties", () => this.sectionPropsDialog())] },
          { stack: [B("mass", "Costing", () => this.costingDialog()), B("material", "Sustainability", () => this.sustainabilityDialog())] }]],
      MBD: [[B("gdt", "Auto Dimension<br>Scheme", () => this.dimxpertDialog()), { stack: [B("eye", "Show / Hide 3D Annotations", () => this.togglePMI()),
        B("trash", "Delete Scheme", () => this.run("dimxpert_clear"))] }], [B("export", "Publish<br>eDrawings", () => this.publishEDrawing())]],
      "Render Tools": [[B("appearance", "Final<br>Render", () => this.renderDialog()), B("appearance", "Edit<br>Decal", () => this.feature("add_decal")),
        B("appearance", "Edit<br>Appearance", () => this.appearanceDialog())]],
      Manufacturing: [[B("cut_extrude", "CAM<br>Toolpaths", () => this.camDialog()), { stack: [B("eye", "Show Toolpaths", () => this.camShow()),
          B("eye", "Hide Toolpaths", () => this.camHide()), B("export", "Save G-code", () => this.camGcode())] }],
        [B("sim_run", "Plastics<br>Fill", () => this.plasticsDialog()), { stack: [B("sim_stress", "Fill Time", () => this.plasticsPlot("fill_time")),
          B("sim_stress", "Pressure", () => this.plasticsPlot("pressure")), B("measure", "Wall Thickness", () => this.plasticsPlot("thickness"))] },
        { stack: [B("sim_stress", "Weld Lines", () => this.plasticsPlot("weld")), B("sim_stress", "Sink Marks", () => this.plasticsPlot("sink")),
          B("eye", "Hide Results", () => this.plasticsPlot(null))] }],
        [B("check", "Inspection", () => this.inspectionDialog()), B("drawing", "Make<br>Drawing", () => this.makeDrawing())]],
    };
    if (this.model?.kind === "assembly") {
      const simTab = tabs.Simulation.slice(0, 2); // structural study works on bonded assemblies too
      for (const k of Object.keys(tabs)) delete tabs[k];
      tabs.Assembly = [[B("insert_comp", "Insert<br>Components", () => this.insertComponentDialog()), B("mate", "Mate", () => this.openPM(SPECS.mate))],
        [{ stack: [B("linear_pattern", "Linear Component Pattern", () => this.componentPatternDialog("linear")), B("circular_pattern", "Circular Component Pattern", () => this.componentPatternDialog("circular")),
          B("mirror", "Mirror Components", () => this.mirrorComponentsDialog())] }],
        [{ stack: [B("toolbox", "Toolbox", () => this.toolboxDialog()), B("smart_fasteners", "Smart Fasteners", () => this.smartFasteners())] },
          B("explode", "Exploded<br>View", () => this.explodeDialog()),
          { stack: [B("explode", "Explode / Collapse", () => this.run("set_exploded", { exploded: !this.model.explode?.active })),
            B("motion", "Motion Study", () => this.motionDialog())] }],
        [{ stack: [B("hole", "Assembly Hole", () => this.asmCutDialog("hole")), B("cut_extrude", "Assembly Cut", () => this.asmCutDialog("extrude"))] }],
        [B("interference", "Interference<br>Detection", () => this.interference())],
        [{ stack: [B("eye", "Hide / Show Components", () => this.toggleComponents(this.selectedComponentsAny())), B("eye", "Isolate", () => this.isolateComponents()),
          B("eye", "Show All Components", () => this.showAllComponents())] }],
        [{ stack: [B("mate", "SmartMates", () => this.feature("smart_mate")), B("mate", "Magnetic Snap", () => this.magneticSnap()),
          B("explode", "Alternate Position", () => this.altPositionsDialog())] },
          { stack: [B("eye", "Envelope", () => this.compToggle("set_envelope", "envelope", "Envelope")), B("part", "SpeedPak", () => this.compToggle("set_speedpak", "speedpak", "SpeedPak")),
            B("part", "Large Assembly Mode", () => this.run("large_assembly_mode", { on: !this.model.large_assembly_mode }))] },
          { stack: [B("trash", "Defeature", () => this.defeatureDialog()), B("save", "Pack and Go", () => this.packAndGoDialog()), B("folder", "Design Library", () => this.designLibraryDialog())] }],
        [{ stack: [B("sweep", "Route Pipe", () => this.pipeRouteDialog()), B("sweep", "Insert Fitting", () => this.fittingDialog()), B("sweep", "Route Cable", () => this.routeDialog("cable"))] },
          { stack: [B("weldment", "Weld Bead", () => this.feature("weld_bead")), B("hole", "Hole Series", () => this.holeSeriesDialog())] }]];
      tabs.Simulation = simTab;
      tabs.Electrical = [[B("sweep", "Connector", () => this.connectorDialog()), B("sweep", "Route<br>Segment", () => this.harnessRouteDialog()),
        B("properties", "From-To<br>List", () => this.fromToDialog())], [B("sweep", "Build<br>Harness", () => this.harnessBuild()),
        { stack: [B("drawing", "Formboard Drawing", () => this.formboardSave()), B("trash", "Clear Electrical", () => this.run("electrical_clear", {}))] }]];
      tabs.Composer = [[B("explode", "Exploded<br>View", () => this.explodeDialog()), B("explode", "Steps from<br>Explode", () => this.composerAuto()),
        B("properties", "Add<br>Step", () => this.composerStep())], [B("export", "Publish<br>Instructions", () => this.composerExport()),
        B("trash", "Clear<br>Steps", () => this.run("composer_clear", {}))]];
      tabs.Evaluate = [[B("measure", "Measure", () => this.measure()), B("mass", "Mass<br>Properties", () => this.massProps()), B("drawing", "Make<br>Drawing", () => this.makeDrawing()),
        B("properties", "Bill of<br>Materials", () => this.bomDialog()), B("interference", "Clearance<br>Verification", () => this.clearanceDialog())],
        [B("export", "Publish<br>eDrawings", () => this.publishEDrawing()), B("appearance", "Final<br>Render", () => this.renderDialog())]];
      if (!tabs[this.tab]) this.tab = "Assembly";
    } else if (!tabs[this.tab]) this.tab = "Features";
    const host = $("#cm-buttons"); host.innerHTML = "";
    const mk = (b, big) => {
      const btn = el("button", big ? "cm-big" : "cm-small", `${icon(b.k)}<span>${b.label}</span>`);
      btn.title = b.label.replace("<br>", " "); btn.setAttribute("aria-label", btn.title);
      if (b.active || (b.tool && this.sk.active && this.sk.tool === b.tool)) btn.classList.add("active");
      if (b.tool) btn.setAttribute("aria-pressed", btn.classList.contains("active") ? "true" : "false");
      if (b.tool && !this.sk.active) btn.disabled = false;
      btn.onclick = b.fn; return btn;
    };
    for (const group of tabs[this.tab]) {
      const g = el("div", "cm-group");
      for (const b of group) {
        if (b.stack) { const s = el("div", "cm-stack"); b.stack.forEach((x) => s.append(mk(x, false))); g.append(s); } else g.append(mk(b, true));
      }
      host.append(g);
    }
    const tabsEl = $("#cm-tabs"); tabsEl.innerHTML = "";
    for (const t of Object.keys(tabs)) {
      const b = el("button", "cm-tab" + (t === this.tab ? " active" : ""), t); b.onclick = () => { this.tab = t; this.renderCommandManager(); }; tabsEl.append(b);
    }
  },

  buildHeadsUp() {
    const hu = $("#headsup");
    const btn = (k, title, fn) => { const b = el("button", "hu", icon(k)); b.title = title; b.setAttribute("aria-label", title); b.onclick = (e) => { e.stopPropagation(); fn(b); }; return b; };
    const drop = (b, opts) => {
      let d = b.querySelector(".hu-drop");
      if (!d) { d = el("div", "hu-drop"); b.append(d); }
      d.innerHTML = "";
      for (const [k, label, fn] of opts) { const o = el("div", "hu-opt", `${icon(k)}<span>${label}</span>`); o.onclick = (e) => { e.stopPropagation(); d.classList.remove("open"); fn(); }; d.append(o); }
      document.querySelectorAll(".hu-drop.open").forEach((x) => x !== d && x.classList.remove("open"));
      d.classList.toggle("open");
    };
    hu.append(btn("zoom_fit", "Zoom to Fit (F)", () => this.vp.zoomFit()), btn("zoom_sel", "Zoom to Area", () => this.startZoomArea()),
      btn("zoom_fit", "Zoom to Selection", () => this.zoomToSelection()),
      btn("prev_view", "Previous View (Ctrl+Shift+Z)", () => this.vp.previousView()), el("span", "hu-sep"),
      btn("view_orient", "View Orientation (Space)", (b) => drop(b, [["v_front", "Front  Ctrl+1", () => this.vp.setView("front")], ["v_back", "Back  Ctrl+2", () => this.vp.setView("back")],
        ["v_left", "Left  Ctrl+3", () => this.vp.setView("left")], ["v_right", "Right  Ctrl+4", () => this.vp.setView("right")], ["v_top", "Top  Ctrl+5", () => this.vp.setView("top")],
        ["v_bottom", "Bottom  Ctrl+6", () => this.vp.setView("bottom")], ["v_iso", "Isometric  Ctrl+7", () => this.vp.setView("isometric")], ["v_tri", "Trimetric", () => this.vp.setView("trimetric")],
        ["normal_to", "Normal To  Ctrl+8", () => this.normalTo()]])),
      btn("shaded_edges", "Display Style", (b) => drop(b, [["shaded_edges", "Shaded With Edges", () => this.vp.applyStyle("shaded_edges")], ["shaded", "Shaded", () => this.vp.applyStyle("shaded")],
        ["hlr", "Hidden Lines Removed", () => this.vp.applyStyle("hlr")], ["wireframe", "Wireframe", () => this.vp.applyStyle("wireframe")]])),
      btn("perspective", "Perspective", () => this.togglePerspective()),
      btn("hide_show", "Hide/Show Items", (b) => drop(b, [["plane_item", "View Planes", () => this.vp.togglePlanes()],
        ["relation", "View Sketch Relations", () => { this.showRelations = !this.showRelations; this.vp.dirty = true; }],
        ["sketch_item", "View Sketches", () => { this.vp.hiddenSketches = this.vp.hiddenSketches.size ? new Set() : new Set((this.mesh?.sketches || []).map((s) => s.name)); this.vp.setMesh(this.mesh); }]])),
      btn("section", "Section View", () => this.sectionDialog()),
      btn("select", "Selection Filter (F5)", (b) => drop(b, [["face", `${this.filterHas("face") ? "✓ " : ""}Filter Faces (X)`, () => this.toggleFilter("face")],
        ["edge", `${this.filterHas("edge") ? "✓ " : ""}Filter Edges (E)`, () => this.toggleFilter("edge")],
        ["point", `${this.filterHas("vertex") ? "✓ " : ""}Filter Vertices (V)`, () => this.toggleFilter("vertex")],
        ["cancel", "Clear All Filters", () => this.toggleFilter(null)]])));
    this.huFilter = hu.lastChild;
    this.huFilter.setAttribute("aria-pressed", "false");
  },

  renderConfirm() {
    const c = $("#confirm"); c.innerHTML = "";
    const mk = (k, title, fn) => { const b = el("button", "cc", icon(k)); b.title = title; b.setAttribute("aria-label", title); b.onclick = fn; c.append(b); };
    if (this.pm.active) { mk("ok", "OK", () => this.pmOK()); mk("cancel", "Cancel", () => this.pmCancel()); }
    else if (this.sk.active) { mk("exit_sketch", "Exit Sketch", () => this.exitSketch()); mk("cancel", "Discard changes and exit", () => this.exitSketch(false, true)); }
    else if (this.model?.in_context) mk("assembly", `Return to Assembly (${this.model.in_context.assembly})`, () => this.run("return_to_assembly").then(() => this.vp.zoomFit()));
  },

  prompt(msg) { const p = $("#prompt-msg"); p.textContent = msg || ""; p.classList.toggle("show", !!msg); },

  buildLeftTabs() {
    const keys = { tree: "tab_tree", pm: "tab_pm", config: "tab_config", display: "tab_display" };
    document.querySelectorAll(".lp-tab").forEach((b) => {
      b.innerHTML = icon(keys[b.dataset.pane]); b.setAttribute("aria-pressed", b.classList.contains("active") ? "true" : "false");
      b.onclick = () => this.showPane(b.dataset.pane);
    });
    $("#treefilter").oninput = () => this.renderTree();
    const rs = $("#lp-resize"); let start = null;
    rs.onpointerdown = (e) => { start = [e.clientX, $("#leftpane").offsetWidth]; rs.setPointerCapture(e.pointerId); };
    rs.onpointermove = (e) => { if (start) $("#workspace").style.gridTemplateColumns = `${Math.max(200, start[1] + e.clientX - start[0])}px 1fr auto`; };
    rs.onpointerup = () => { start = null; this.vp.resize(); };
  },

  showPane(p) {
    document.querySelectorAll(".lp-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.pane === p);
      b.setAttribute("aria-pressed", b.dataset.pane === p ? "true" : "false");
    });
    for (const id of ["tree", "pm", "config", "display"]) $(`#${id}`).classList.toggle("hidden", id !== p);
    $("#lp-filter").classList.toggle("hidden", p !== "tree");
  },

  modal(title, body, onOK, ic = "help") {
    const box = $("#modal-box");
    box.innerHTML = `<div class="mb-title">${icon(ic)}<span>${escapeHtml(title)}</span></div><div class="mb-body">${body}</div><div class="mb-foot"></div>`;
    const foot = box.querySelector(".mb-foot");
    const close = () => $("#modal").classList.add("hidden");
    if (onOK) { const ok = el("button", "btn primary", "OK"); ok.onclick = async () => { try { await onOK(); close(); } catch { /* toast shown */ } }; foot.append(ok); }
    const c = el("button", "btn", onOK ? "Cancel" : "Close"); c.onclick = close; foot.append(c);
    $("#modal").classList.remove("hidden");
    box.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter" && onOK) foot.querySelector(".primary").click(); if (e.key === "Escape") close(); };
    setTimeout(() => box.querySelector("input")?.focus());
  },

  // msg is HTML, except error messages, which are escaped (they may carry text from files or the server) unless
  // the caller built and escaped the HTML itself ({ html: true })
  toast(msg, kind = "", { html = false } = {}) {
    const t = el("div", `toast ${kind}`, kind === "err" && !html ? escapeHtml(msg) : msg); $("#toasts").append(t);
    setTimeout(() => t.remove(), kind === "err" ? 6000 : 3500);
  },
};

export function installUi(App) {
  Object.assign(App.prototype, MIXIN);
}
