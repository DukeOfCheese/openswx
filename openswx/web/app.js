// openswx UI controller.
import { icon } from "./icons.js";
import { Viewport } from "./viewport.js";
import { PropertyManager, SPECS, UNITS, el, escapeHtml } from "./propman.js";
import { Sketcher } from "./sketcher.js";
import { DrawingEditor } from "./drawing.js";

const $ = (s) => document.querySelector(s);
const TYPE_ICON = { sketch: "sketch_item", extrude: "extrude", cut_extrude: "cut_extrude", revolve: "revolve", cut_revolve: "cut_revolve",
  fillet: "fillet", variable_fillet: "fillet", chamfer: "chamfer", shell: "shell", hole: "hole", linear_pattern: "linear_pattern", circular_pattern: "circular_pattern",
  sketch_pattern: "sketch_pattern", curve_pattern: "curve_pattern", table_pattern: "table_pattern", fill_pattern: "fill_pattern", mirror: "mirror", plane: "plane_item", ref_axis: "ref_axis", ref_point: "ref_point", csys: "csys", sketch3d: "sketch3d", helix: "helix", hem: "hem", sketched_bend: "sketched_bend",
  revolved_surface: "revolved_surface", swept_surface: "swept_surface", lofted_surface: "lofted_surface", boundary_surface: "boundary_surface", planar_surface: "planar_surface",
  filled_surface: "filled_surface", offset_surface: "offset_surface", knit_surface: "knit_surface", trim_surface: "trim_surface", end_cap: "end_cap", gusset: "gusset", vertex_chamfer: "vertex_chamfer", face_fillet: "face_fillet",
  full_round_fillet: "full_round_fillet", wrap: "wrap", jog: "jog", corner_relief: "corner_relief", dimple: "dimple", miter_flange: "miter_flange", louver: "louver", lance: "lance", extruded_flange: "extruded_flange", form_rib: "form_rib", auto_corner_relief: "corner_relief", import: "import", sweep: "sweep", cut_sweep: "cut_sweep", loft: "loft", cut_loft: "cut_loft",
  draft: "draft_feat", base_flange: "base_flange", edge_flange: "edge_flange", extruded_surface: "surface", thicken: "thicken",
  split: "cut_extrude", cut_with_surface: "cut_extrude", delete_face: "trash", move_face: "offset", replace_face: "surface",
  projected_curve: "intersection_curve", composite_curve: "sketch3d", xyz_curve: "spline", split_line: "intersection_curve", intersect: "knit_surface", indent: "dimple" };
const FEATURE_TO_SPEC = { extrude: "extrude", cut_extrude: "cut_extrude", revolve: "revolve", cut_revolve: "cut_revolve", fillet: "fillet", variable_fillet: "fillet",
  chamfer: "chamfer", shell: "shell", hole: "hole", linear_pattern: "linear_pattern", circular_pattern: "circular_pattern", sketch_pattern: "sketch_pattern", curve_pattern: "curve_pattern", table_pattern: "table_pattern", fill_pattern: "fill_pattern", mirror: "mirror", plane: "plane", ref_axis: "ref_axis", ref_point: "ref_point", csys: "csys", helix: "helix",
  hem: "hem", sketched_bend: "sketched_bend", revolved_surface: "revolved_surface", swept_surface: "swept_surface", lofted_surface: "lofted_surface",
  planar_surface: "planar_surface", filled_surface: "filled_surface", offset_surface: "offset_surface", knit_surface: "knit_surface", trim_surface: "trim_surface",
  end_cap: "end_cap", gusset: "gusset", vertex_chamfer: "vertex_chamfer", face_fillet: "face_fillet", full_round_fillet: "full_round_fillet", wrap: "wrap",
  jog: "jog", corner_relief: "corner_relief", dimple: "dimple", miter_flange: "miter_flange", louver: "louver", lance: "lance",
  extruded_flange: "extruded_flange", form_rib: "form_rib",
  sweep: "sweep", cut_sweep: "cut_sweep", loft: "loft", cut_loft: "cut_loft", draft: "draft",
  base_flange: "base_flange", edge_flange: "edge_flange", extruded_surface: "extruded_surface", thicken: "thicken",
  rib: "rib", thread: "thread", dome: "dome", split: "split_body", cut_with_surface: "cut_with_surface", delete_face: "delete_face",
  move_face: "move_face", replace_face: "replace_face", projected_curve: "projected_curve", composite_curve: "composite_curve", xyz_curve: "xyz_curve", split_line: "split_line", intersect: "intersect", indent: "indent" };
const CURVE_TYPES = ["sketch3d", "helix", "projected_curve", "composite_curve", "xyz_curve"];

class App {
  constructor() {
    this.graphics = $("#graphics");
    this.vp = new Viewport($("#view"), $("#overlay"), $("#triad"));
    this.overlayDims = el("div", ""); this.overlayDims.style.cssText = "position:absolute;inset:0;pointer-events:none"; $("#overlay").append(this.overlayDims);
    this.overlayLabels = el("div", ""); this.overlayLabels.style.cssText = "position:absolute;inset:0;pointer-events:none"; $("#overlay").append(this.overlayLabels);
    this.pm = new PropertyManager($("#pm"));
    this.sk = new Sketcher(this);
    this.dwg = new DrawingEditor(this);
    this.selection = []; this.selDims = new Set(); this.version = -1; this.model = null; this.mesh = null;
    this.showRelations = true; this.polygonSides = 6; this.tab = "Features"; this.expanded = new Set(); this.activity = [];
    this.filter = null; this.lastMouse = { x: 0, y: 0 }; this.hiddenComps = new Set(); this.skFilletR = 5; this.skChamferD = 5;
    this.status = { coords: (uv) => { $("#sb-coords").textContent = uv ? `${uv[0].toFixed(2)}mm  ${uv[1].toFixed(2)}mm  0mm` : ""; } };
    this.vp.frameHooks.push(() => this.renderPlaneLabels());
    this.overlayHandle = el("div", ""); this.overlayHandle.style.cssText = "position:absolute;inset:0;pointer-events:none"; $("#overlay").append(this.overlayHandle);
    this.vp.frameHooks.push(() => this.renderHandle());
    this.buildMenus(); this.buildQuickAccess(); this.buildHeadsUp(); this.buildLeftTabs(); this.buildTaskPane();
    this.bindViewport(); this.bindKeys(); this.bindSearch(); this.bindFileInput();
    this.renderCommandManager(); this.renderConfirm();
    this.connect();
  }

  // ================================================================== server
  async api(path, body) {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    return r.json();
  }

  async run(name, args = {}, { quiet = false } = {}) {
    const res = await this.api("/api/command", { name, args });
    if (!res.ok) { if (!quiet) this.toast(res.error, "err"); throw new Error(res.error); }
    await this.refresh();
    return res;
  }

  connect() {
    $("#sb-agent").textContent = `MCP: ${location.origin}/mcp`;
    const es = new EventSource("/api/events");
    es.onmessage = (m) => {
      const d = JSON.parse(m.data);
      if (d.version !== this.version) {
        if (d.last && d.last.origin === "agent" && this.version >= 0) this.onAgent(d.last);
        this.refresh();
      }
    };
    es.onerror = () => { $("#sb-agent").textContent = "Disconnected - retrying..."; };
    es.onopen = () => { $("#sb-agent").textContent = `MCP: ${location.origin}/mcp`; };
  }

  async refresh() {
    const [st, mesh] = await Promise.all([this.api("/api/state"), this.api("/api/mesh")]);
    const first = this.version < 0 || (this.model && this.model.name !== st.model.name);
    this.version = st.version; this.model = st.model; this.docs = st.documents; this.mesh = mesh;
    if (UNITS.name !== (this.model.units || "mm")) { UNITS.set(this.model.units || "mm"); if (this.pm?.active) this.pm.render(); }
    if (this.sk.active && !this.model.features.some((f) => f.name === this.sk.name)) this.exitSketch(true);
    if (first) { // another document: its own selection and display state
      this.selection = []; this.vp.setSelection([]); this.sk.showDims = null;
      this.hiddenComps.clear(); this.vp.hiddenComponents = new Set();
    }
    this.vp.setMesh(mesh);
    if (this.sk.active) await this.loadSketch();
    if (this.sk.showDims) await this.showFeatureDims(this.sk.showDims.feature, true);
    if (this.measureOpen) this.updateMeasure();
    if (this.lastKind !== (this.model.kind || "part")) { this.lastKind = this.model.kind || "part"; this.renderCommandManager(); }
    this.renderTree(); this.renderTitle(); this.renderStatus(); this.renderQuickAccessState(); this.renderTaskPane(); this.renderConfirm();
    if (this.model.drawing_document && this.openedDrawingDoc !== this.model.drawing_document) { // a .oswdrw was opened: show it
      this.openedDrawingDoc = this.model.drawing_document; this.dwg.open();
    } else if (this.model.show_drawing) this.dwg.open(); // a SOLIDWORKS drawing was opened
    if (first) requestAnimationFrame(() => { this.vp.resize(); this.vp.zoomFit(false); });
  }

  async loadSketch() {
    const res = await this.api("/api/command", { name: "get_feature", args: { name: this.sk.name } });
    if (!res.ok) return;
    const overlay = (this.mesh.sketches || []).find((s) => s.name === this.sk.name);
    this.sk.update(res.result, overlay);
    this.renderSketchPM();
  }

  onAgent(entry) {
    this.toast(`<b>Agent</b> ${escapeHtml(entry.command)}${entry.ok ? "" : ` failed: ${escapeHtml(entry.message)}`}`, entry.ok ? "agent" : "err");
    const dot = document.querySelector('.tp-tab[data-pane="agent"]');
    if (dot && $("#taskpane").classList.contains("collapsed") && !dot.querySelector(".dot")) dot.append(el("span", "dot"));
  }

  // ================================================================== title / quick access / menus
  renderTitle() {
    const m = this.model; const name = m.name + (m.dirty ? " *" : "");
    $("#doctitle").textContent = `${name}  —  openswx`;
    document.title = `${name} - openswx`;
    $("#docname-corner").textContent = this.sk.active ? `${m.name} (Default)  —  Editing ${this.sk.name}` : `${m.name} (Default)`;
  }

  buildQuickAccess() {
    const qa = $("#quickaccess");
    const items = [["new", "New (Ctrl+N)", () => this.newPart()], ["open", "Open (Ctrl+O)", () => this.openFile()], ["save", "Save (Ctrl+S)", () => this.save()],
      ["print", "Print / export image", () => window.open("/api/image?view=isometric&width=1600&height=1200")], null,
      ["undo", "Undo (Ctrl+Z)", () => this.run("undo")], ["redo", "Redo (Ctrl+Y)", () => this.run("redo")], null,
      ["select", "Select", () => this.cancelAll()], ["rebuild", "Rebuild (Ctrl+B)", () => this.run("rebuild")], ["options", "Options", () => this.optionsDialog()]];
    for (const it of items) {
      if (!it) { qa.append(el("span", "hu-sep")); continue; }
      const b = el("button", "qa", icon(it[0])); b.title = it[1]; b.onclick = it[2]; b.dataset.k = it[0]; qa.append(b);
    }
  }

  renderQuickAccessState() {
    const q = (k) => document.querySelector(`.qa[data-k="${k}"]`);
    q("undo").disabled = !this.model.can_undo; q("redo").disabled = !this.model.can_redo;
  }

  buildMenus() {
    const M = {
      File: [["New Part...", "Ctrl+N", () => this.newPart()], ["New Assembly...", "", () => this.newAssembly()], ["Open...", "Ctrl+O", () => this.openFile()],
        ["Import STEP...", "", () => this.openFile("import")], ["Make Drawing from Part/Assembly", "", () => this.makeDrawing()], "-",
        ["Save", "Ctrl+S", () => this.save()], ["Save As...", "", () => this.save(true)], ["Download .oswprt", "", () => window.open("/api/download?format=oswprt")], "-",
        ["Export STEP", "", () => this.exportAs("step")], ["Export Parasolid (x_t)", "", () => this.exportAs("x_t")], ["Export STL", "", () => this.exportAs("stl")],
        ["Export IGES", "", () => this.exportAs("iges")], ["Export 3MF (3D printing)", "", () => this.exportAs("3mf")], ["Export glTF (GLB)", "", () => this.exportAs("glb")],
        ["Export Image (PNG)", "", () => (this.desk ? this.deskCall("save_image") : window.open("/api/image?view=isometric&width=1600&height=1200"))],
        ["Import DXF into Sketch...", "", () => this.importDxf()], ["Export Sketch as DXF...", "", () => this.exportSketchDxf()],
        ["Export Flat Pattern DXF...", "", () => this.exportFlatDxf()],
        "-", ["Close", "", () => this.run("close_document")], ...(window.pywebview ? [["Exit", "Alt+F4", () => window.close()]] : [])],
      Edit: [["Undo", "Ctrl+Z", () => this.run("undo")], ["Redo", "Ctrl+Y", () => this.run("redo")], "-", ["Rebuild", "Ctrl+B", () => this.run("rebuild")],
        ["Delete", "Del", () => this.deleteSelection()], ["Suppress", "", () => this.suppressSelected(true)], ["Unsuppress", "", () => this.suppressSelected(false)]],
      View: [["Zoom to Fit", "F", () => this.vp.zoomFit()], ["Zoom to Area", "", () => this.startZoomArea()], ["Zoom to Selection", "", () => this.zoomToSelection()],
        ["Previous View", "Ctrl+Shift+Z", () => this.vp.previousView()], "-",
        ...["front", "back", "left", "right", "top", "bottom", "isometric", "trimetric"].map((v, i) => [cap(v), i < 7 ? `Ctrl+${[1, 2, 3, 4, 5, 6, 7][i]}` : "", () => this.vp.setView(v)]), "-",
        ["Shaded With Edges", "", () => this.vp.applyStyle("shaded_edges")], ["Shaded", "", () => this.vp.applyStyle("shaded")], ["Hidden Lines Removed", "", () => this.vp.applyStyle("hlr")],
        ["Wireframe", "", () => this.vp.applyStyle("wireframe")], "-", ["Toggle Sketch Relations", "", () => { this.showRelations = !this.showRelations; this.vp.dirty = true; }],
        ["Planes (show / hide all)", "", () => this.vp.togglePlanes()], "-",
        ["Perspective (toggle)", "", () => this.togglePerspective()], ["View Cube (show / hide)", "", () => document.querySelector(".vcube")?.classList.toggle("hidden")],
        ["New View...", "", () => this.newNamedView()]],
      Insert: [["Boss/Base > Extrude...", "", () => this.feature("extrude")], ["Boss/Base > Revolve...", "", () => this.feature("revolve")],
        ["Cut > Extrude...", "", () => this.feature("cut_extrude")], ["Cut > Revolve...", "", () => this.feature("cut_revolve")], "-",
        ["Features > Fillet...", "", () => this.feature("fillet")], ["Features > Chamfer...", "", () => this.feature("chamfer")], ["Features > Hole...", "", () => this.feature("hole")],
        ["Features > Shell...", "", () => this.feature("shell")], "-", ["Pattern/Mirror > Linear Pattern...", "", () => this.feature("linear_pattern")],
        ["Pattern/Mirror > Circular Pattern...", "", () => this.feature("circular_pattern")], ["Pattern/Mirror > Mirror...", "", () => this.feature("mirror")],
        ["Pattern/Mirror > Sketch Driven Pattern...", "", () => this.feature("sketch_pattern")], ["Pattern/Mirror > Curve Driven Pattern...", "", () => this.feature("curve_pattern")],
        ["Pattern/Mirror > Table Driven Pattern...", "", () => this.feature("table_pattern")], ["Pattern/Mirror > Fill Pattern...", "", () => this.feature("fill_pattern")], "-",
        ["Reference Geometry > Plane...", "", () => this.feature("plane")], ["Reference Geometry > Axis...", "", () => this.feature("ref_axis")],
        ["Reference Geometry > Coordinate System...", "", () => this.feature("csys")], ["Reference Geometry > Point...", "", () => this.feature("ref_point")],
        ["Curve > Helix/Spiral...", "", () => this.feature("helix")], ["Curve > Projected...", "", () => this.feature("projected_curve")],
        ["Curve > Composite...", "", () => this.feature("composite_curve")], ["Curve > Split Line...", "", () => this.feature("split_line")], ["Curve > Curve Through XYZ Points...", "", () => this.feature("xyz_curve")], ["3D Sketch...", "", () => this.sketch3dDialog()], ["Sketch", "", () => this.sketchCommand()], "-",
        ["Features > Combine...", "", () => this.bodyDialog("combine")], ["Features > Delete/Keep Body...", "", () => this.bodyDialog("delete")],
        ["Features > Move/Copy Body...", "", () => this.bodyDialog("move")], ["Features > Scale...", "", () => this.bodyDialog("scale")],
        ["Features > Split...", "", () => this.feature("split_body")], ["Features > Intersect...", "", () => this.feature("intersect")], ["Features > Indent...", "", () => this.feature("indent")], ["Cut > With Surface...", "", () => this.feature("cut_with_surface")], "-",
        ["Face > Delete...", "", () => this.feature("delete_face")], ["Face > Move...", "", () => this.feature("move_face")],
        ["Face > Replace...", "", () => this.feature("replace_face")]],
      Tools: [["Sketch Entities > Line", "", () => this.setSketchTool("line")], ["Sketch Entities > Rectangle", "", () => this.setSketchTool("rectangle")],
        ["Sketch Entities > Circle", "", () => this.setSketchTool("circle")], ["Dimensions > Smart", "", () => this.setSketchTool("smart_dim")], "-",
        ["Evaluate > Mass Properties", "", () => this.massProps()], ["Evaluate > Measure", "", () => this.measure()], ["Evaluate > Section Properties", "", () => this.sectionPropsDialog()], "-",
        ["Equations...", "", () => this.equationsDialog()], ["Design Table...", "", () => this.designTableDialog()], ["Material...", "", () => this.materialDialog()], ["Appearance...", "", () => this.appearanceDialog()], ["Properties...", "", () => this.propertiesDialog()], ["Options...", "", () => this.optionsDialog()]],
      Window: () => [...(this.docs?.documents || []).map((d) => [`${d.name === this.model.name ? "✓ " : ""}${d.name}`, "", () => this.switchDocument(d.name)]),
        "-", ["Next Document", "Ctrl+Tab", () => this.nextDocument()], ["Close", "Ctrl+W", () => this.run("close_document").then(() => this.vp.zoomFit())]],
      Help: [["Keyboard Shortcuts", "", () => this.showTask("help")], ["Agent (MCP) Setup", "", () => this.mcpDialog()], ["About openswx", "", () => this.about()]],
    };
    const bar = $("#menubar");
    for (const [name, items] of Object.entries(M)) {
      const root = el("div", "menu-root", name), drop = el("div", "menu-drop");
      const fill = () => {
        drop.innerHTML = "";
        for (const it of typeof items === "function" ? items() : items) {
          if (it === "-") { drop.append(el("div", "menu-sep")); continue; }
          const row = el("div", "menu-item", `<span>${escapeHtml(it[0])}</span><span class="key">${it[1]}</span>`);
          row.onclick = (e) => { e.stopPropagation(); this.closeMenus(); it[2](); }; drop.append(row);
        }
      };
      fill(); root.append(drop);
      root.onclick = (e) => { e.stopPropagation(); const open = root.classList.contains("open"); this.closeMenus(); if (!open) { fill(); root.classList.add("open"); bar.classList.add("open"); } };
      root.onmouseenter = () => { if (bar.querySelector(".menu-root.open")) { this.closeMenus(); fill(); root.classList.add("open"); bar.classList.add("open"); } };
      bar.append(root);
    }
    $("#brand").onclick = (e) => { e.stopPropagation(); bar.classList.toggle("open"); };
    document.addEventListener("click", () => { this.closeMenus(); $("#ctxmenu").classList.add("hidden"); document.querySelectorAll(".hu-drop.open").forEach((d) => d.classList.remove("open")); });
  }
  closeMenus() { document.querySelectorAll(".menu-root.open").forEach((r) => r.classList.remove("open")); if (!document.querySelector(".menu-root.open")) $("#menubar").classList.remove("open"); }

  // ================================================================== CommandManager
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
      ],
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
            B("knit_surface", "Intersect", () => this.feature("intersect"))] }]],
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
            B("trash", "Clear Thermal", () => this.run("thermal_clear", {}))] }]],
      Evaluate: [[B("measure", "Measure", () => this.measure()), B("mass", "Mass<br>Properties", () => this.massProps()), B("drawing", "Make<br>Drawing", () => this.makeDrawing())],
        [B("section", "Section<br>View", () => this.sectionDialog()), { stack: [B("draft_analysis", "Draft Analysis", () => this.draftDialog()), B("zebra", "Zebra Stripes", () => this.toggleZebra()),
          B("curvature", "Curvature", () => this.toggleCurvature())] },
          { stack: [B("check", "Check", () => this.checkGeometry()), B("interference", "Body Interference", () => this.bodyInterference())] },
          { stack: [B("measure", "Thickness Analysis", () => this.thicknessDialog()), B("draft_analysis", "Undercut Detection", () => this.undercutDialog()),
            B("section", "Section Properties", () => this.sectionPropsDialog())] }]],
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
          B("eye", "Show All Components", () => this.showAllComponents())] }]];
      tabs.Simulation = simTab;
      tabs.Evaluate = [[B("measure", "Measure", () => this.measure()), B("mass", "Mass<br>Properties", () => this.massProps()), B("drawing", "Make<br>Drawing", () => this.makeDrawing()),
        B("properties", "Bill of<br>Materials", () => this.bomDialog()), B("interference", "Clearance<br>Verification", () => this.clearanceDialog())]];
      if (!tabs[this.tab]) this.tab = "Assembly";
    } else if (!tabs[this.tab]) this.tab = "Features";
    const host = $("#cm-buttons"); host.innerHTML = "";
    const mk = (b, big) => {
      const btn = el("button", big ? "cm-big" : "cm-small", `${icon(b.k)}<span>${b.label}</span>`);
      btn.title = b.label.replace("<br>", " ");
      if (b.active || (b.tool && this.sk.active && this.sk.tool === b.tool)) btn.classList.add("active");
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
  }

  // ================================================================== heads-up toolbar, confirmation corner
  buildHeadsUp() {
    const hu = $("#headsup");
    const btn = (k, title, fn) => { const b = el("button", "hu", icon(k)); b.title = title; b.onclick = (e) => { e.stopPropagation(); fn(b); }; return b; };
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
  }

  renderConfirm() {
    const c = $("#confirm"); c.innerHTML = "";
    const mk = (k, title, fn) => { const b = el("button", "cc", icon(k)); b.title = title; b.onclick = fn; c.append(b); };
    if (this.pm.active) { mk("ok", "OK", () => this.pmOK()); mk("cancel", "Cancel", () => this.pmCancel()); }
    else if (this.sk.active) { mk("exit_sketch", "Exit Sketch", () => this.exitSketch()); mk("cancel", "Discard changes and exit", () => this.exitSketch(false, true)); }
    else if (this.model?.in_context) mk("assembly", `Return to Assembly (${this.model.in_context.assembly})`, () => this.run("return_to_assembly").then(() => this.vp.zoomFit()));
  }

  prompt(msg) { const p = $("#prompt-msg"); p.textContent = msg || ""; p.classList.toggle("show", !!msg); }

  // ================================================================== left pane / tree
  buildLeftTabs() {
    const keys = { tree: "tab_tree", pm: "tab_pm", config: "tab_config", display: "tab_display" };
    document.querySelectorAll(".lp-tab").forEach((b) => { b.innerHTML = icon(keys[b.dataset.pane]); b.onclick = () => this.showPane(b.dataset.pane); });
    $("#treefilter").oninput = () => this.renderTree();
    const rs = $("#lp-resize"); let start = null;
    rs.onpointerdown = (e) => { start = [e.clientX, $("#leftpane").offsetWidth]; rs.setPointerCapture(e.pointerId); };
    rs.onpointermove = (e) => { if (start) $("#workspace").style.gridTemplateColumns = `${Math.max(200, start[1] + e.clientX - start[0])}px 1fr auto`; };
    rs.onpointerup = () => { start = null; this.vp.resize(); };
  }

  showPane(p) {
    document.querySelectorAll(".lp-tab").forEach((b) => b.classList.toggle("active", b.dataset.pane === p));
    for (const id of ["tree", "pm", "config", "display"]) $(`#${id}`).classList.toggle("hidden", id !== p);
    $("#lp-filter").classList.toggle("hidden", p !== "tree");
  }

  renderTree() {
    const tree = $("#tree"), m = this.model; if (!m) return;
    tree.innerHTML = "";
    this.renderConfigPane();
    if (m.kind === "assembly") return this.renderAssemblyTree(tree, m);
    const filter = $("#treefilter").value.trim().toLowerCase();
    const row = (opts) => {
      const r = el("div", "t-row" + (opts.cls ? " " + opts.cls : ""));
      r.style.paddingLeft = `${4 + (opts.depth || 0) * 16}px`;
      const tw = el("span", "tw", opts.children ? (opts.open ? "&#9662;" : "&#9656;") : "");
      if (opts.children) tw.onclick = (e) => { e.stopPropagation(); opts.toggle(); };
      r.append(tw, el("span", "", icon(opts.icon)), el("span", "lbl", escapeHtml(opts.label)));
      if (opts.error) { const b = el("span", "badge", "!"); b.title = opts.error; r.append(b); }
      if (opts.title) r.title = opts.title;
      if (opts.selected) r.classList.add("selected");
      r.onclick = (e) => opts.click?.(e); r.ondblclick = (e) => opts.dbl?.(e); r.oncontextmenu = (e) => { e.preventDefault(); opts.ctx?.(e); };
      if (opts.hover) { r.onmouseenter = () => opts.hover(true); r.onmouseleave = () => opts.hover(false); }
      if (opts.feature) { r.dataset.feature = opts.feature; r.onpointerdown = (e) => this.treeDragStart(e, opts.feature); }
      if (opts.folder) r.dataset.folder = opts.folder;
      tree.append(r); return r;
    };
    const mat = m.material.name ? m.material.name : "<not specified>";
    row({ icon: "part", label: `${m.name}  (Default) <<Default>_Display State 1>`, cls: "t-root" });
    for (const [k, l] of [["history", "History"], ["sensors", "Sensors"], ["annotations", "Annotations"]]) row({ icon: k, label: l, depth: 0.6 });
    row({ icon: "material", label: `Material ${mat}`, depth: 0.6, dbl: () => this.materialDialog(), ctx: (e) => this.ctxMenu(e, [["material", "Edit Material", () => this.materialDialog()]]) });
    for (const p of ["Front", "Top", "Right"]) this.planeRow(row, `${p} Plane`, p, 0.6);
    row({ icon: "origin", label: "Origin", depth: 0.6 }).dataset.top = "1";

    const feats = m.features;
    const folderOf = new Map(), folderShown = new Set();
    for (const fd of m.folders || []) for (const n of fd.features) folderOf.set(n, fd);
    const rollIndex = m.rollback == null ? feats.length : m.rollback;
    let topIndex = 0;
    const visible = feats.filter((f) => !f.absorbed_by);
    const renderFeature = (f, depth) => {
      const kids = feats.filter((k) => k.absorbed_by === f.name);
      const open = this.expanded.has(f.name);
      let prefix = "";
      if (f.type === "sketch") prefix = f.sketch_state === "under_defined" ? "(-) " : f.sketch_state === "over_defined" ? "(+) " : "";
      const label = prefix + f.name;
      if (filter && !label.toLowerCase().includes(filter)) return;
      const cls = [f.suppressed ? "suppressed" : "", f.rolled_back ? "rolled" : "", f.status === "error" ? "error" : ""].join(" ");
      row({ icon: f.suppressed ? "suppress" : TYPE_ICON[f.type] || "part", label, depth, error: f.error, title: f.error || f.type,
        children: kids.length > 0, open, toggle: () => { open ? this.expanded.delete(f.name) : this.expanded.add(f.name); this.renderTree(); },
        selected: this.selection.some((s) => s.kind === "feature" && s.id === f.name), feature: depth < 1 ? f.name : null,
        cls: [cls, this.relatedFeatures?.has(f.name) ? "related" : ""].join(" "),
        hover: (on) => this.hoverFeature(on ? f.name : null),
        click: (e) => this.treeClick(f, e), dbl: () => this.showFeatureDims(f.name), ctx: (e) => this.featureMenu(e, f) });
      if (open) kids.forEach((k) => renderFeature(k, depth + 1));
    };
    for (const f of visible) {
      const idx = feats.indexOf(f);
      if (idx >= rollIndex && topIndex <= rollIndex && !tree.querySelector("#rollbar")) this.rollbar(tree, feats, rollIndex);
      const fd = folderOf.get(f.name);
      if (fd && !folderShown.has(fd.name)) { // a folder sits where its first feature is
        folderShown.add(fd.name);
        const key = `folder:${fd.name}`, open = this.expanded.has(key);
        row({ icon: "folder", label: fd.name, depth: 0.6, children: true, open, folder: fd.name,
          toggle: () => { open ? this.expanded.delete(key) : this.expanded.add(key); this.renderTree(); },
          ctx: (e) => this.ctxMenu(e, [["trash", "Dissolve Folder", () => this.run("remove_folder", { name: fd.name })]]) });
      }
      if (fd && !this.expanded.has(`folder:${fd.name}`)) { topIndex = idx; continue; }
      renderFeature(f, fd ? 1.4 : 0.6); topIndex = idx;
    }
    if (!tree.querySelector("#rollbar")) this.rollbar(tree, feats, rollIndex);
  }

  renderAssemblyTree(tree, m) {
    const row = (ic, label, depth, opts = {}) => {
      const r = el("div", "t-row" + (opts.cls ? " " + opts.cls : ""));
      r.style.paddingLeft = `${4 + depth * 16}px`;
      r.append(el("span", "tw", ""), el("span", "", icon(ic)), el("span", "lbl", escapeHtml(label)));
      if (opts.error) { const b = el("span", "badge", "!"); b.title = opts.error; r.append(b); }
      if (opts.selected) r.classList.add("selected");
      r.onclick = (e) => opts.click?.(e); r.ondblclick = (e) => opts.dbl?.(e); r.oncontextmenu = (e) => { e.preventDefault(); opts.ctx?.(e); };
      tree.append(r);
    };
    row("assembly", `${m.name}  (Default) <Display State-1>`, 0, { cls: "t-root" });
    for (const [k, l] of [["history", "History"], ["sensors", "Sensors"], ["annotations", "Annotations"]]) row(k, l, 0.6);
    for (const p of ["Front", "Top", "Right"]) row("plane_item", `${p} Plane`, 0.6);
    row("origin", "Origin", 0.6);
    for (const c of m.components) {
      const prefix = c.fixed ? "(f) " : c.derived ? "" : c.dof > 0 ? "(-) " : "";
      const it = { kind: "component", id: c.name };
      row(c.subassembly ? "assembly" : "component", `${prefix}${c.name}${c.derived ? `  [${c.derived.feature}]` : ""}`, 0.6, {
        cls: this.hiddenComps.has(c.name) ? "suppressed" : "",
        selected: this.selection.some((s) => s.kind === "component" && s.id === c.name),
        click: (e) => this.setSelection(e.ctrlKey ? (this.selection.some((s) => s.kind === "component" && s.id === c.name)
          ? this.selection.filter((s) => !(s.kind === "component" && s.id === c.name)) : [...this.selection, it]) : [it]),
        ctx: (e) => this.ctxMenu(e, [
          ...(!c.subassembly && !c.derived ? [["edit", "Edit Part (in context)", () => this.run("edit_in_context", { component: c.name }).then(() => this.vp.zoomFit())]] : []),
          ...(c.link ? [["edit", "Open Part", () => this.run("activate_document", { name: c.link }).then(() => this.vp.zoomFit())]] : []),
          [c.fixed ? "eye" : "options", c.fixed ? "Float" : "Fix", () => this.run("fix_component", { name: c.name, fixed: !c.fixed })],
          ...(!c.derived ? [["insert_comp", "Replace Component...", () => this.modal(`Replace ${c.name}`, `<div class="field"><label>With (an open part's name or a file path)</label><input id="rcs" value=""></div>
            <label class="pm-check"><input id="rca" type="checkbox"> All instances</label>`, () => this.run("replace_component", { name: c.name, source: $("#rcs").value.trim(), all_instances: $("#rca").checked }), "insert_comp")]] : []),
          ["circular_pattern", "Rotate Component...", () => this.rotateComponentDialog(c.name)],
          ["eye", this.hiddenComps.has(c.name) ? "Show Component" : "Hide Component", () => this.toggleComponents([c.name])],
          ["eye", "Isolate", () => this.isolateComponents([c.name])], ["eye", "Show All Components", () => this.showAllComponents()],
          ["trash", "Delete", () => this.run("delete_component", { name: c.name })]]),
      });
    }
    row("mate", "Mates", 0.6);
    for (const mt of m.mates) {
      row("mate", `${mt.name} (${mt.a.component}, ${mt.b.component})`, 1.4, { cls: mt.error ? "error" : "", error: mt.error ? "Mate not satisfied" : null,
        dbl: () => this.editMate(mt),
        ctx: (e) => this.ctxMenu(e, [["edit", "Edit Feature", () => this.editMate(mt)],
          ...(["distance", "angle"].includes(mt.type) ? [["edit", "Edit Value", () => this.editMateValue(mt)]] : []),
          ["reverse", "Flip Alignment", () => this.run("edit_mate", { name: mt.name, aligned: !mt.aligned })],
          ["trash", "Delete", () => this.run("delete_mate", { name: mt.name })]]) });
    }
    for (const f of m.assembly_features || []) {
      const icn = f.type === "cut" ? (f.kind === "hole" ? "hole" : "cut_extrude") : f.type === "mirror" ? "mirror" : f.type === "linear" ? "linear_pattern" : "circular_pattern";
      const what = f.type === "cut" ? (f.components?.length ? f.components.join(", ") : "all components") : (f.seeds || []).join(", ");
      row(icn, `${f.name} (${what})`, 0.6, {
        ctx: (e) => this.ctxMenu(e, [["trash", "Delete", () => this.run("delete_assembly_feature", { name: f.name })]]) });
    }
    if (m.explode?.steps?.length) {
      row("explode", `ExplView1${m.explode.active ? " (exploded)" : ""}`, 0.6, {
        ctx: (e) => this.ctxMenu(e, [["explode", m.explode.active ? "Collapse" : "Explode", () => this.run("set_exploded", { exploded: !m.explode.active })],
          ["trash", "Delete Exploded View", () => this.run("set_exploded", { exploded: false, clear: true })]]) });
      for (const st of m.explode.steps) row("explode", `${st.name}: ${st.components.join(", ")} ${st.distance} mm`, 1.4);
    }
  }

  // Edit Feature on a mate: the Mate PropertyManager with its type, selections and values; OK redefines it
  editMate(mt) {
    const lab = (r) => `${r.component}${r.plane ? `/${r.plane}` : r.edge ? "/edge" : r.face ? "/face" : ""}`;
    const it = (r) => r && { kind: r.edge ? "edge" : "face", label: lab(r), value: r };
    const order = { width: [mt.a, mt.a2, mt.b, mt.b2], hinge: [mt.a, mt.b, mt.a2, mt.b2], symmetric: [mt.a, mt.b, mt.a2], slot: [mt.a, mt.b, mt.a2] }[mt.type] || [mt.a, mt.b];
    const v = mt.value, t = mt.type;
    const initial = { ents: order.filter(Boolean).map(it), type: t, aligned: !!mt.aligned,
      ...(["distance", "angle"].includes(t) ? { value: v } : {}), ...(["gear", "linear_coupler"].includes(t) ? { ratio: v } : {}),
      ...(t === "screw" ? { pitch: v } : {}), ...(t === "rack_pinion" ? { pdia: v } : {}), ...(t.startsWith("limit") ? { lmin: v, lmax: mt.value2 } : {}) };
    const spec = { ...SPECS.mate, title: `Edit ${mt.name}`, command: "redefine_mate", args: (vv) => ({ name: mt.name, ...SPECS.mate.args(vv) }) };
    this.openPM(spec, { initial });
  }

  editMateValue(mt) {
    this.modal(`Edit ${mt.name}`, `<div class="field"><label>${mt.type === "angle" ? "Angle (°)" : "Distance (mm)"}</label><input id="mv" type="number" value="${mt.value}"></div>`,
      async () => { await this.run("edit_mate", { name: mt.name, value: parseFloat($("#mv").value) }); }, "mate");
  }

  selectedComponents() { return this.selection.filter((s) => s.kind === "component").map((s) => s.id); }

  componentPatternDialog(kind) {
    const comps = this.selectedComponents();
    if (!comps.length) return this.toast("Select the seed component(s) in the tree first (Ctrl+click for several).");
    const lin = kind === "linear";
    this.modal(lin ? "Linear Component Pattern" : "Circular Component Pattern", `<p>Seed: <b>${comps.map(escapeHtml).join(", ")}</b></p>
      ${lin ? `<div class="field"><label>Direction (x, y, z)</label><input id="cpd" value="1, 0, 0"></div><div class="field"><label>Spacing (mm)</label><input id="cps" type="number" value="50"></div>`
        : `<div class="field"><label>Axis origin (x, y, z)</label><input id="cpo" value="0, 0, 0"></div><div class="field"><label>Axis direction</label><input id="cpa" value="0, 0, 1"></div>
           <div class="field"><label>Total angle (°)</label><input id="cpg" type="number" value="360"></div>`}
      <div class="field"><label>Instances</label><input id="cpn" type="number" value="3" min="2"></div>`, async () => {
      const v = (id) => $(id).value.split(",").map(Number);
      await this.run("component_pattern", lin ? { components: comps, kind, count: parseInt($("#cpn").value), spacing: parseFloat($("#cps").value), direction: v("#cpd") }
        : { components: comps, kind, count: parseInt($("#cpn").value), axis_origin: v("#cpo"), axis: v("#cpa"), angle: parseFloat($("#cpg").value) });
    }, lin ? "linear_pattern" : "circular_pattern");
  }

  mirrorComponentsDialog() {
    const comps = this.selectedComponents();
    if (!comps.length) return this.toast("Select the component(s) to mirror in the tree first.");
    this.modal("Mirror Components", `<p>Components: <b>${comps.map(escapeHtml).join(", ")}</b> (opposite-hand copies)</p>
      <div class="field"><label>Mirror plane</label><select id="mcp"><option>Right</option><option>Front</option><option>Top</option></select></div>
      <div class="field"><label>Plane origin (x, y, z)</label><input id="mco" value="0, 0, 0"></div>`, async () => {
      await this.run("mirror_components", { components: comps, plane: $("#mcp").value, plane_origin: $("#mco").value.split(",").map(Number) });
    }, "mirror");
  }

  explodeDialog() {
    const comps = this.selectedComponents();
    if (!comps.length) return this.toast("Select the component(s) to move in this explode step.");
    this.modal("Explode Step", `<p>Components: <b>${comps.map(escapeHtml).join(", ")}</b></p>
      <div class="field"><label>Direction (x, y, z)</label><input id="exd" value="0, 1, 0"></div>
      <div class="field"><label>Distance (mm)</label><input id="exs" type="number" value="50"></div>`, async () => {
      await this.run("explode_step", { components: comps, direction: $("#exd").value.split(",").map(Number), distance: parseFloat($("#exs").value) });
    }, "explode");
  }

  async toolboxDialog() {
    const cat = (await this.run("list_fasteners", {}, { quiet: true }).catch(() => null))?.result || {};
    const names = { hex_bolt: "Hex bolt", socket_cap_screw: "Socket head cap screw", hex_nut: "Hex nut", washer: "Plain washer" };
    this.modal("Toolbox - ISO Hardware", `<div class="field"><label>Type</label><select id="tbk">${Object.entries(cat).map(([k, v]) => `<option value="${k}">${names[k] || k} (${v.standard})</option>`).join("")}</select></div>
      <div class="field"><label>Size</label><select id="tbs"></select></div>
      <div class="field" id="tblen"><label>Length (mm)</label><input id="tbl" type="number" value="20"></div>
      <div class="field"><label>Position X, Y, Z (mm)</label><input id="tbp" value="0, 0, 0"></div>`, async () => {
      const k = $("#tbk").value;
      await this.run("insert_fastener", { kind: k, size: $("#tbs").value, length: cat[k].needs_length ? parseFloat($("#tbl").value) : null, position: $("#tbp").value.split(",").map(Number) });
    }, "toolbox");
    const upd = () => { const c = cat[$("#tbk").value]; $("#tbs").innerHTML = c.sizes.map((s) => `<option ${s === "M8" ? "selected" : ""}>${s}</option>`).join(""); $("#tblen").style.display = c.needs_length ? "" : "none"; };
    $("#tbk").onchange = upd; upd();
  }

  motionDialog() {
    const mates = (this.model.mates || []).filter((m) => ["distance", "angle"].includes(m.type));
    if (!mates.length) return this.toast("Add a distance or angle mate to drive (the motor).");
    this.modal("Motion Study", `<div class="field"><label>Driven mate (motor)</label><select id="mom">${mates.map((m) => `<option value="${escapeHtml(m.name)}">${escapeHtml(m.name)} (${m.value})</option>`).join("")}</select></div>
      <div class="field"><label>From</label><input id="mo0" type="number" value="${mates[0].value}"></div>
      <div class="field"><label>To</label><input id="mo1" type="number" value="${mates[0].value + (mates[0].type === "angle" ? 90 : 50)}"></div>
      <div class="field"><label>Frames</label><input id="mof" type="number" value="48"></div>
      <label class="pm-check"><input id="mosave" type="checkbox"> Save as animation (AVI video or GIF)</label>`, async () => {
      if ($("#mosave").checked) {
        const path = await this.askPath("save", `${this.model.name}-motion.avi`, ["AVI video (*.avi)", "Animated GIF (*.gif)"], "Save Animation");
        if (path) { const g = await this.run("motion_export", { mate: $("#mom").value, start: parseFloat($("#mo0").value), end: parseFloat($("#mo1").value), frames: parseInt($("#mof").value), path }); this.toast(`Saved ${escapeHtml(g.result.path)}`); }
        return;
      }
      const r = await this.api("/api/command", { name: "motion_study", args: { mate: $("#mom").value, start: parseFloat($("#mo0").value), end: parseFloat($("#mo1").value), frames: parseInt($("#mof").value) } });
      if (!r.ok) return this.toast(r.error, "err");
      const cur = Object.fromEntries(this.model.components.map((c) => [c.name, c.pose]));
      const frames = r.result.frames; let i = 0;
      const step = () => { this.vp.setComponentPoses(frames[i].poses, cur); this.status.prompt?.(`Motion: ${frames[i].value.toFixed(2)}`); i++;
        if (i < frames.length) setTimeout(step, 40); else setTimeout(() => this.vp.setComponentPoses(null, cur), 600); };
      step();
    }, "motion");
  }

  async smartFasteners() {
    const comps = this.selectedComponents();
    if (!comps.length) return this.toast("Select the component with the holes, then Smart Fasteners.");
    for (const c of comps) {
      const r = await this.run("smart_fasteners", { component: c }).catch(() => null);
      if (r) this.toast(`Inserted ${r.result.fasteners.length} fastener(s): ${r.result.fasteners.map((f) => `${f.size}x${f.length}`).join(", ")}`);
    }
  }

  async newAssembly() { await this.run("new_assembly"); this.insertComponentDialog(); }

  insertComponentDialog() {
    const parts = (this.docs?.documents || []).filter((d) => d.name !== this.model.name);
    this.modal("Insert Components", `<p>Open documents:</p><div class="pm-list">${parts.map((d) => `<button class="pm-rel" data-doc="${escapeHtml(d.name)}">${icon("part")}<span>${escapeHtml(d.name)}</span></button>`).join("") || "<i>No other open parts</i>"}</div>
      <div class="field" style="margin-top:10px"><label>…or a file path (.oswprt / .step / .x_t / .sldprt)</label><input id="compfile" placeholder="C:\\path\\part.oswprt"></div>
      <div class="field"><label>Position X, Y, Z (mm): blank to click where it goes</label><input id="comppos" value=""></div>`,
      async () => { const f = $("#compfile").value.trim(); if (f) this.startPlacing(f, $("#comppos").value); }, "insert_comp");
    document.querySelectorAll("#modal [data-doc]").forEach((b) => { b.onclick = () => { const pos = $("#comppos").value; $("#modal").classList.add("hidden"); this.startPlacing(b.dataset.doc, pos); }; });
  }

  // Insert Components as in SOLIDWORKS: the first one goes at the origin (fixed); later ones follow the cursor
  // (a preview) and a click places them, on the face under the cursor or in the view plane
  startPlacing(source, typed = "") {
    const pos = typed.trim() ? typed.split(",").map((x) => parseFloat(x) || 0) : null;
    if (pos || !(this.model.components || []).length) return this.insertComponent(source, pos || [0, 0, 0]);
    this.placing = { source, busy: false };
    this.prompt(`Click in the graphics area to place ${source} (Esc cancels).`);
  }
  placeAt(e) {
    const pk = this.vp.pick(e.x, e.y, { kinds: ["face"] });
    const p = pk?.point || this.vp.screenToWorldOnTargetPlane(e.x, e.y).toArray();
    return p.map((v) => +v.toFixed(3));
  }
  async placingMove(e) {
    const pl = this.placing; if (pl.busy) { pl.next = e; return; }
    pl.busy = true;
    const res = await this.api("/api/preview", { name: "insert_component", args: { source: pl.source, position: this.placeAt(e) } });
    pl.busy = false;
    if (this.placing !== pl) return;
    if (res.ok) { // only the new component
      const known = new Set((this.model.components || []).map((c) => c.name)), mine = (id) => !known.has(String(id).split("/")[0]);
      this.vp.setPreview({ ...res, faces: res.faces.filter((f) => mine(f.id)), edges: res.edges.filter((x) => mine(x.id)), refs: [] });
    }
    if (pl.next) { const n = pl.next; pl.next = null; this.placingMove(n); }
  }
  async placingClick(e) {
    const pl = this.placing; this.placing = null; this.vp.setPreview(null); this.prompt("");
    await this.insertComponent(pl.source, this.placeAt(e));
  }
  cancelPlacing() { if (this.placing) { this.placing = null; this.vp.setPreview(null); this.prompt(""); } }

  async insertComponent(source, pos = [0, 0, 0]) {
    await this.run("insert_component", { source, position: pos });
    if ((this.model.components || []).length === 1) this.vp.zoomFit();
  }

  async asmCutDialog(kind) { // Assembly Hole / Assembly Cut: start from a picked face (its point, into the material)
    const sel = this.selection.find((s) => s.kind === "face");
    let p = [0, 0, 0], n = [0, 0, 1];
    if (sel) {
      const t = await this.api("/api/command", { name: "get_topology", args: {} });
      const f = t.ok && t.result.faces.find((x) => x.id === sel.id);
      if (f && f.normal) { p = f.point; n = f.normal; }
    }
    const v3 = (a) => a.map((x) => +x.toFixed(3)).join(", ");
    const comps = (this.model.components || []).map((c) => c.name);
    const scope = `<div class="field"><label>Components to cut (blank: all it meets)</label><input id="acc" value="" placeholder="${escapeHtml(comps.join(", "))}"></div>`;
    const num = (id) => parseFloat($(id).value);
    const vec = (id) => $(id).value.split(",").map((x) => parseFloat(x) || 0);
    const list = () => $("#acc").value.split(",").map((s) => s.trim()).filter(Boolean);
    if (kind === "hole") {
      this.modal("Assembly Hole", `<div class="field"><label>Start point (X, Y, Z)</label><input id="ahp" value="${v3(p)}"></div>
        <div class="field"><label>Direction</label><input id="ahd" value="${v3(n.map((x) => -x))}"></div>
        <div class="field"><label>Diameter</label><input id="ahs" type="number" value="8" step="0.5"></div>
        <div class="field"><label>Depth (blank: through all)</label><input id="ahz" type="number" value=""></div>${scope}`,
      () => this.run("assembly_hole", { position: vec("#ahp"), direction: vec("#ahd"), diameter: num("#ahs"), depth: num("#ahz") || null, components: list() }), "hole");
      return;
    }
    // a rectangle on the picked face's plane, cut into the material
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const unit = (a) => { const l = Math.hypot(...a) || 1; return a.map((x) => x / l); };
    const N = unit(n), U = unit(cross(N, Math.abs(N[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0])), V = cross(N, U);
    this.modal("Assembly Cut", `<div class="field"><label>Rectangle centre (X, Y, Z)</label><input id="acp" value="${v3(p)}"></div>
      <div class="field"><label>Width</label><input id="acw" type="number" value="20" step="1"></div>
      <div class="field"><label>Height</label><input id="ach" type="number" value="10" step="1"></div>
      <div class="field"><label>Depth (blank: through all)</label><input id="acz" type="number" value=""></div>${scope}`, () => {
      const c = vec("#acp"), w = num("#acw") / 2, h = num("#ach") / 2;
      const pts = [[-w, -h], [w, -h], [w, h], [-w, h]].map(([a, b]) => c.map((x, i) => x + U[i] * a + V[i] * b));
      return this.run("assembly_cut_extrude", { profile: pts, depth: num("#acz") || null, reverse: true, components: list() }); // U x V = N: reverse cuts inwards
    }, "cut_extrude");
  }

  async bomDialog() {
    const r = await this.api("/api/command", { name: "assembly_bom", args: {} });
    if (!r.ok) return this.toast(r.error, "err");
    const rows = r.result.bom;
    this.modal("Bill of Materials", `<table class="dw-bom"><tr><th>Item</th><th>Part number</th><th>Description</th><th>Qty</th></tr>${rows.map((x) =>
      `<tr><td>${x.item}</td><td>${escapeHtml(x.part_number)}</td><td>${escapeHtml(x.description)}</td><td>${x.qty}</td></tr>`).join("")}</table>`, async () => {
      if (this.desk) { // Save the BOM as a CSV file where the user picks
        const path = await this.askPath("save", `${this.model.name}-BOM.csv`, ["CSV (*.csv)"], "Save BOM");
        if (path) { const r2 = await this.run("export_bom", { path }); this.toast(`Saved ${escapeHtml(r2.result.path || path)}`); }
        return;
      }
      const q = (s) => `"${String(s).replace(/"/g, '""')}"`;
      const csv = ["ITEM NO.,PART NUMBER,DESCRIPTION,QTY.", ...rows.map((x) => [x.item, q(x.part_number), q(x.description), x.qty].join(","))].join("\r\n");
      const a = el("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = `${this.model.name}-BOM.csv`; a.click();
    }, "properties");
  }

  async interference() {
    const r = await this.api("/api/command", { name: "check_interference", args: {} });
    if (!r.ok) return this.toast(r.error, "err");
    const list = r.result.interferences;
    this.modal("Interference Detection", list.length ? `<div class="kv">${list.map((i) => `<div>${escapeHtml(i.a)} ↔ ${escapeHtml(i.b)}</div><div>${i.volume.toFixed(3)} mm³</div>`).join("")}</div>` : "<p>No interferences.</p>", null, "interference");
  }

  get desk() { return window.pywebview?.api; } // present when running as the desktop app (openswx.exe)

  // a file path from the native dialog (desktop) or typed in (browser, server-side path)
  async askPath(kind, name, types, label) {
    if (this.desk) { const r = await this.desk.pick_file(kind, name, types); return r?.path || null; }
    return new Promise((res) => this.modal(label, `<div class="field"><label>File path (on this computer)</label><input id="askp" value="${escapeHtml(name)}" style="width:100%"></div>`,
      () => res($("#askp").value.trim() || null), "open"));
  }

  async importDxf() {
    const path = await this.askPath("open", "", ["DXF/DWG drawing (*.dxf;*.dwg)"], "Import DXF/DWG");
    if (!path) return;
    const plane = this.selection.find((s) => s.kind === "plane")?.id || "Front";
    const r = await this.run("import_dxf", { path, plane, sketch: this.sk.active ? this.sk.name : null });
    this.toast(`Imported ${r?.result?.result?.entities?.length ?? 0} entities into ${r?.result?.result?.sketch}`);
    this.vp.zoomFit();
  }

  async exportSketchDxf() {
    const sk = this.sk.active ? this.sk.name : this.selectedSketch();
    if (!sk) return this.toast("Select a sketch (or edit one) to export.");
    const path = await this.askPath("save", `${sk}.dxf`, ["DXF drawing (*.dxf)", "DWG drawing (*.dwg)"], "Export Sketch as DXF/DWG");
    if (path) { const r = await this.run("export_sketch_dxf", { sketch: sk, path }); this.toast(`Saved ${escapeHtml(r.result.path)}`); }
  }

  async exportFlatDxf() {
    const path = await this.askPath("save", `${this.model.name}-flat.dxf`, ["DXF drawing (*.dxf)"], "Export Flat Pattern");
    if (path) { const r = await this.run("export_flat_pattern_dxf", { path }); this.toast(`Saved ${escapeHtml(r.result.path)}`); }
  }

  async deskCall(fn, ...args) {
    const r = await this.desk[fn](...args);
    if (r?.cancelled) return;
    if (r && !r.ok) return this.toast(r.error, "err");
    await this.refresh();
    if (r?.result?.saved || r?.result?.exported || r?.result?.path) this.toast(`Saved ${escapeHtml(r.result.saved || r.result.exported || r.result.path)}`);
  }

  exportAs(fmt) { if (this.desk) return this.deskCall("export", fmt); window.open(`/api/download?format=${fmt}`); }

  makeDrawing() { this.dwg.open(); }

  bodyDialog(kind) {
    const n = this.model.bodies || 0;
    const f = { combine: [`<div class="field"><label>Main body (0..${n - 1})</label><input id="b1" value="0"></div><div class="field"><label>Tool bodies (comma list)</label><input id="b2" value="1"></div>
        <div class="field"><label>Operation</label><select id="b3" style="height:26px"><option value="add">Add</option><option value="subtract">Subtract</option><option value="common">Common</option></select></div>`,
        () => this.run("combine_bodies", { main: +$("#b1").value, tools: $("#b2").value.split(",").map(Number), mode: $("#b3").value })],
      delete: [`<div class="field"><label>Bodies to delete (comma list, 0..${n - 1})</label><input id="b1" value="${n - 1}"></div>`,
        () => this.run("delete_body", { bodies: $("#b1").value.split(",").map(Number) })],
      move: [`<div class="field"><label>Bodies (comma list)</label><input id="b1" value="0"></div><div class="field"><label>Translate X, Y, Z (mm)</label><input id="b2" value="50, 0, 0"></div>
        <div class="field"><label>Rotate about Z (°)</label><input id="b3" type="number" value="0"></div><label class="pm-check"><input type="checkbox" id="b4"> Copy</label>`,
        () => this.run("move_copy_body", { bodies: $("#b1").value.split(",").map(Number), translate: $("#b2").value.split(",").map(Number), angle: +$("#b3").value, copy: $("#b4").checked })],
      scale: [`<div class="field"><label>Scale factor</label><input id="b1" type="number" value="2"></div>`, () => this.run("scale_body", { factor: +$("#b1").value })] }[kind];
    this.modal({ combine: "Combine", delete: "Delete/Keep Body", move: "Move/Copy Body", scale: "Scale" }[kind], f[0], f[1], "part");
  }

  async cutList() {
    const r = await this.api("/api/command", { name: "weldment_cut_list", args: {} });
    if (!r.ok) return this.toast(r.error, "err");
    this.modal("Cut List", `<div class="kv"><div><b>Item / description</b></div><div><b>Length × Qty</b></div>${r.result.items.map((i) =>
      `<div>${i.item}. ${escapeHtml(i.description)}</div><div>${i.length.toFixed(2)} mm × ${i.qty}</div>`).join("")}</div>`, null, "weldment");
  }

  rotateComponentDialog(name) {
    this.modal(`Rotate ${name}`, `<div class="field"><label>Axis (x, y, z)</label><input id="rax" value="0, 0, 1"></div>
      <div class="field"><label>Angle (°)</label><input id="rang" type="number" value="30"></div>`, async () => {
      await this.run("rotate_component", { name, axis: $("#rax").value.split(",").map(Number), angle_deg: parseFloat($("#rang").value) });
    }, "circular_pattern");
  }

  // ------------------------------------------------------------------ configurations / equations
  renderConfigPane() {
    const pane = $("#config"); const m = this.model; if (!pane || !m) return;
    pane.innerHTML = "";
    pane.append(el("div", "t-row t-root", `<span class="tw"></span>${icon("part")}<span class="lbl">${escapeHtml(m.name)} Configuration(s)</span>`));
    for (const c of m.configurations || ["Default"]) {
      const r = el("div", "t-row" + (c === m.active_config ? " selected" : ""), `<span class="tw"></span>${icon("config_item")}<span class="lbl">${escapeHtml(c)} [ ${escapeHtml(m.name)} ]${c === m.active_config ? "  ✓" : ""}</span>`);
      r.style.paddingLeft = "14px";
      r.ondblclick = () => this.run("activate_configuration", { name: c });
      r.oncontextmenu = (e) => { e.preventDefault(); this.ctxMenu(e, [["config_item", "Show Configuration", () => this.run("activate_configuration", { name: c })],
        ["edit", "Override a Dimension...", () => this.configValueDialog(c)], ...(c !== "Default" ? [["trash", "Delete", () => this.run("delete_configuration", { name: c })]] : [])]); };
      pane.append(r);
    }
    if (m.design_table) {
      const t = el("div", "t-row", `<span class="tw"></span>${icon("properties")}<span class="lbl">Design Table</span>`); t.style.paddingLeft = "14px";
      t.ondblclick = () => this.designTableDialog();
      t.oncontextmenu = (e) => { e.preventDefault(); this.ctxMenu(e, [["edit", "Edit Table...", () => this.designTableDialog()], ["trash", "Delete", () => this.run("delete_design_table", {})]]); };
      pane.append(t);
    }
    const dt = el("button", "btn", "Design Table..."); dt.style.margin = "8px 0 0 14px"; dt.onclick = () => this.designTableDialog(); pane.append(dt);
    const add = el("button", "btn", "Add Configuration..."); add.style.margin = "8px 14px";
    add.onclick = () => this.modal("Add Configuration", `<div class="field"><label>Configuration name</label><input id="cfgname" value="Config${(m.configurations || []).length}"></div>`,
      async () => { await this.run("add_configuration", { name: $("#cfgname").value.trim() }); }, "config_item");
    pane.append(add);
    pane.append(el("div", "pm-msg", "Double-click to show a configuration. Suppressing a feature or overriding a dimension applies to the active configuration."));
  }

  // Design Table: a row per configuration, a column per dimension or $STATE@Feature (S / U), as in SOLIDWORKS
  async designTableDialog() {
    const t = await this.evalCall("get_design_table"); if (!t) return;
    const eq = await this.evalCall("get_equations");
    const dims = Object.keys(eq?.dimensions || {});
    const feats = this.model.features.filter((f) => !f.absorbed_by && f.type !== "sketch").map((f) => f.name);
    const cols = t.columns.slice();
    const rows = t.rows.length ? t.rows.map((r) => ({ name: r.name, values: r.values.map((v) => (v ?? "")) })) : (this.model.configurations || ["Default"]).map((n) => ({ name: n, values: [] }));
    const render = () => {
      const head = `<tr><th>Configuration</th>${cols.map((c, j) => `<th>${escapeHtml(c)} <a href="#" data-delcol="${j}" title="Remove column">✕</a></th>`).join("")}<th></th></tr>`;
      const body = rows.map((r, i) => `<tr><td><input data-r="${i}" data-c="-1" value="${escapeHtml(r.name)}"></td>${cols.map((_, j) =>
        `<td><input data-r="${i}" data-c="${j}" value="${escapeHtml(String(r.values[j] ?? ""))}" size="6"></td>`).join("")}<td><a href="#" data-delrow="${i}" title="Remove row">✕</a></td></tr>`).join("");
      $("#dtgrid").innerHTML = `<table class="dt-grid">${head}${body}</table>`;
      document.querySelectorAll("#dtgrid input").forEach((inp) => { inp.oninput = () => {
        const r = rows[+inp.dataset.r], c = +inp.dataset.c; if (c < 0) r.name = inp.value; else r.values[c] = inp.value; }; });
      document.querySelectorAll("#dtgrid [data-delcol]").forEach((a) => { a.onclick = (e) => { e.preventDefault(); const j = +a.dataset.delcol; cols.splice(j, 1); rows.forEach((r) => r.values.splice(j, 1)); render(); }; });
      document.querySelectorAll("#dtgrid [data-delrow]").forEach((a) => { a.onclick = (e) => { e.preventDefault(); rows.splice(+a.dataset.delrow, 1); render(); }; });
    };
    this.modal("Design Table", `<div class="pm-msg">Each row is a configuration; columns are dimensions (D1@Sketch1, Boss-Extrude1.depth) or $STATE@Feature (S = suppressed, U = unsuppressed). Empty cells use the base value. Values may be numbers or =expressions.</div>
      <div id="dtgrid" style="overflow:auto;max-height:340px;margin:6px 0"></div>
      <div class="pm-row"><select id="dtcol">${dims.map((d) => `<option>${escapeHtml(d)}</option>`).join("")}${feats.map((f) => `<option>$STATE@${escapeHtml(f)}</option>`).join("")}</select>
        <button class="btn" id="dtaddc">Add Column</button><button class="btn" id="dtaddr">Add Row</button></div>
      <div class="pm-row" style="margin-top:6px"><button class="btn" id="dtcsv">Export CSV</button><label class="btn" style="cursor:pointer">Import CSV<input id="dtin" type="file" accept=".csv,text/csv" style="display:none"></label></div>`,
    async () => { await this.run("apply_design_table", { columns: cols, rows }); }, "properties");
    render();
    $("#dtaddc").onclick = () => { const c = $("#dtcol").value; if (c && !cols.includes(c)) { cols.push(c); rows.forEach((r) => r.values.push("")); render(); } };
    $("#dtaddr").onclick = () => { let n = rows.length + 1; while (rows.some((r) => r.name === `Config${n}`)) n++; rows.push({ name: `Config${n}`, values: cols.map(() => "") }); render(); };
    $("#dtcsv").onclick = () => {
      const q = (x) => `"${String(x ?? "").replace(/"/g, '""')}"`;
      const csv = [["Configuration", ...cols].map(q).join(","), ...rows.map((r) => [r.name, ...cols.map((_, j) => r.values[j])].map(q).join(","))].join("\r\n");
      const a = el("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = `${this.model.name}-design-table.csv`; a.click();
    };
    $("#dtin").onchange = async () => { // first column: configuration names; header row: the parameters
      const text = await $("#dtin").files[0].text();
      const parse = (line) => { const out = []; let cur = "", qd = false; for (let i = 0; i < line.length; i++) { const ch = line[i];
        if (qd) { if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') qd = false; else cur += ch; }
        else if (ch === '"') qd = true; else if (ch === ",") { out.push(cur); cur = ""; } else cur += ch; } out.push(cur); return out; };
      const lines = text.split(/\r?\n/).filter((l) => l.trim()); if (!lines.length) return;
      const head = parse(lines[0]); cols.splice(0, cols.length, ...head.slice(1));
      rows.splice(0, rows.length, ...lines.slice(1).map((l) => { const c = parse(l); return { name: c[0], values: c.slice(1) }; }));
      render();
    };
  }

  configValueDialog(cfg) {
    this.modal(`Override in ${cfg}`, `<div class="field"><label>Dimension (e.g. D1@Boss-Extrude1 or D2@Sketch1)</label><input id="cvt"></div>
      <div class="field"><label>Value (number or =expression)</label><input id="cvv"></div>`, async () => {
      const v = $("#cvv").value.trim(); await this.run("set_configuration_value", { configuration: cfg, target: $("#cvt").value.trim(), value: isNaN(+v) ? v : +v });
    }, "config_item");
  }

  async equationsDialog() {
    const r = await this.api("/api/command", { name: "get_equations", args: {} });
    if (!r.ok) return this.toast(r.error, "err");
    const eq = r.result;
    const f = (v) => (v === null || v === undefined ? "—" : (+v).toFixed(3).replace(/\.?0+$/, ""));
    const dims = Object.entries(eq.dimensions || {}).map(([k, v]) => `<option value="${escapeHtml(k)}">${escapeHtml(k)} = ${f(v)}</option>`).join("");
    this.modal("Equations, Global Variables, and Dimensions", `
      <div class="tp-h">Global Variables</div>
      <div class="kv">${eq.global_variables.map((g) => `<div><b>"${escapeHtml(g.name)}"</b> = ${escapeHtml(g.expression)}</div><div>${f(g.value)} <a href="#" class="gvdel" data-n="${escapeHtml(g.name)}" title="Delete">✕</a></div>`).join("") || "<div><i>none</i></div><div></div>"}</div>
      <div class="pm-row" style="margin:6px 0 12px"><input id="gvn" placeholder="name" style="max-width:110px"><input id="gve" placeholder="value or expression"><button class="btn" id="gvadd">Add</button></div>
      <div class="tp-h">Equations</div>
      <div class="kv">${eq.equations.map((e) => `<div><b>"${escapeHtml(e.target)}"</b> = ${escapeHtml(e.expression.replace(/^=/, ""))}</div><div>${f(e.value)}</div>`).join("") || "<div><i>none</i></div><div></div>"}</div>
      <div class="pm-row" style="margin-top:6px"><select id="eqt">${dims}</select></div>
      <div class="pm-row" style="margin:4px 0"><input id="eqe" placeholder='expression, e.g. "width"/2'><button class="btn" id="eqadd">Set</button><button class="btn" id="eqclr">Clear</button></div>
      <div class="pm-msg">SOLIDWORKS syntax: reference variables and dimensions in quotes ("width", "D1@Sketch1"); + - * / ^, sin/cos/tan (degrees), sqrt, min, max.</div>`, null, "equations");
    const reopen = () => this.equationsDialog();
    $("#gvadd").onclick = async () => { try { await this.run("set_global_variable", { name: $("#gvn").value.trim(), expression: $("#gve").value.trim() }); reopen(); } catch {} };
    $("#eqadd").onclick = async () => { try { await this.run("set_equation", { target: $("#eqt").value, expression: $("#eqe").value.trim() }); reopen(); } catch {} };
    $("#eqclr").onclick = async () => { try { await this.run("clear_equation", { target: $("#eqt").value }); reopen(); } catch {} };
    document.querySelectorAll("#modal .gvdel").forEach((a) => { a.onclick = async (ev) => { ev.preventDefault(); try { await this.run("delete_global_variable", { name: a.dataset.n }); reopen(); } catch {} }; });
  }

  // ------------------------------------------------------------------ sketch tools (offset / mirror / fillet)
  sketchSel() { return this.selection.filter((s) => s.kind === "sketch").map((s) => s.id); }

  // a PropertyManager for a sketch tool: the entity list starts with the selected sketch entities
  sketchPM(spec) {
    if (!this.sk.active) return this.toast("Edit a sketch first.");
    const ents = this.sketchSel().map((id) => ({ kind: "skent", label: id, value: id }));
    this.sk.setTool("select"); this.renderCommandManager();
    this.openPM(spec, { initial: { entities: ents, ...(spec.initial ? spec.initial(this) : {}) } });
  }
  skSpec(o) {
    const S = () => this.sk.name, ids = (v, k = "entities") => (v[k] || []).map((i) => i.value);
    const ent = { title: "Entities", fields: [{ key: "entities", type: "selection", accepts: ["skent"], label: "Sketch entities" }] };
    const specs = {
      offset: { title: "Offset Entities", icon: "offset", command: "sketch_offset", groups: [
        { title: "Parameters", fields: [{ key: "distance", type: "number", label: "Offset distance", icon: "offset", value: 5, step: 0.5 },
          { key: "reverse", type: "check", label: "Reverse", value: false }, { key: "bidirectional", type: "check", label: "Bi-directional", value: false }] }, ent],
        args: (v) => ({ sketch: S(), entities: ids(v), distance: v.distance, reverse: !!v.reverse, bidirectional: !!v.bidirectional }),
        message: "Select the entities to offset (Ctrl+click, or box-select) and set the distance." },
      mirror: { title: "Mirror Entities", icon: "sk_mirror", command: "sketch_mirror", groups: [
        { title: "Options", fields: [{ key: "entities", type: "selection", accepts: ["skent"], label: "Entities to mirror" },
          { key: "copy", type: "check", label: "Copy", value: true },
          { key: "about", type: "selection", accepts: ["skline"], single: true, label: "Mirror about (a line or centerline)" }] }],
        args: (v) => ({ sketch: S(), entities: ids(v).filter((x) => x !== v.about?.[0]?.value), about: v.about?.[0]?.value, copy: v.copy !== false }),
        message: "Select the entities to mirror, then the line to mirror about." },
      linear: { title: "Linear Sketch Pattern", icon: "sk_linear_pattern", command: "sketch_linear_pattern", groups: [
        { title: "Direction 1", fields: [{ key: "spacing", type: "number", label: "Spacing", icon: "depth", value: 20, step: 1 },
          { key: "count", type: "number", label: "Instances", value: 3, step: 1 }, { key: "angle", type: "number", label: "Angle (°)", value: 0, step: 5 }] },
        { title: "Direction 2", check: "use_dir2", value: false, fields: [{ key: "spacing2", type: "number", label: "Spacing", icon: "depth", value: 20, step: 1 },
          { key: "count2", type: "number", label: "Instances", value: 2, step: 1 }] }, ent],
        args: (v) => ({ sketch: S(), entities: ids(v), spacing: v.spacing, count: Math.max(1, Math.round(v.count)), angle: v.angle || 0,
          spacing2: v.use_dir2 ? v.spacing2 : 0, count2: v.use_dir2 ? Math.max(1, Math.round(v.count2)) : 1, angle2: (v.angle || 0) + 90 }),
        message: "Select the entities to pattern and set the spacing and instances." },
      circular: { title: "Circular Sketch Pattern", icon: "sk_circular_pattern", command: "sketch_circular_pattern", groups: [
        { title: "Parameters", fields: [{ key: "center", type: "selection", accepts: ["skpoint"], single: true, label: "Center point (default: Origin)" },
          { key: "count", type: "number", label: "Instances", value: 6, step: 1 }, { key: "angle", type: "number", label: "Total angle (°)", value: 360, step: 15 }] }, ent],
        args: (v) => ({ sketch: S(), entities: ids(v), center: v.center?.[0]?.value || "ORIGIN", count: Math.max(2, Math.round(v.count)), angle: v.angle }),
        message: "Select the entities to pattern and the centre point." },
      move: { title: "Move / Copy Entities", icon: "sk_linear_pattern", command: "sketch_transform", groups: [ent,
        { title: "Parameters", fields: [{ key: "dx", type: "number", label: "Delta X", value: 10, step: 1 }, { key: "dy", type: "number", label: "Delta Y", value: 0, step: 1 },
          { key: "copy", type: "check", label: "Copy", value: false }] }],
        args: (v) => ({ sketch: S(), entities: ids(v), move: [v.dx || 0, v.dy || 0], copy: !!v.copy }), message: "Select entities and set the move." },
      rotate: { title: "Rotate Entities", icon: "sk_circular_pattern", command: "sketch_transform", groups: [ent,
        { title: "Parameters", fields: [{ key: "center", type: "selection", accepts: ["skpoint"], single: true, label: "Center of rotation (default: Origin)" },
          { key: "angle", type: "number", label: "Angle (°)", value: 90, step: 15 }, { key: "copy", type: "check", label: "Copy", value: false }] }],
        args: (v) => ({ sketch: S(), entities: ids(v), rotate: v.angle || 0, center: v.center?.[0]?.value || "ORIGIN", copy: !!v.copy }), message: "Select entities, the centre and the angle." },
      scale: { title: "Scale Entities", icon: "offset", command: "sketch_transform", groups: [ent,
        { title: "Parameters", fields: [{ key: "center", type: "selection", accepts: ["skpoint"], single: true, label: "Scale about (default: Origin)" },
          { key: "factor", type: "number", label: "Scale factor", value: 2, step: 0.1 }, { key: "copy", type: "check", label: "Copy", value: false }] }],
        args: (v) => ({ sketch: S(), entities: ids(v), scale: v.factor || 1, center: v.center?.[0]?.value || "ORIGIN", copy: !!v.copy }), message: "Select entities, the point and the factor." },
      convert: { title: "Convert Entities", icon: "convert", command: "sketch_convert_entities", groups: [
        { title: "Entities to Convert", fields: [{ key: "items", type: "selection", accepts: ["edge", "face"], label: "Model edges, faces" }] }],
        args: (v) => ({ sketch: S(), edges: (v.items || []).filter((i) => i.kind === "edge").map((i) => i.value), faces: (v.items || []).filter((i) => i.kind === "face").map((i) => i.value) }),
        initial: (app) => ({ items: app.selection.filter((x) => x.kind === "edge" || x.kind === "face").map((x) => ({ kind: x.kind, label: x.id, value: x.id })) }),
        message: "Select model edges or faces to project onto the sketch plane." },
    };
    return specs[o];
  }

  sketchTransformDialog(kind) { return this.sketchPM(this.skSpec(kind)); }

  sketchOffset() { return this.sketchPM(this.skSpec("offset")); }

  sketchMirror() { // the last selected line, if any, is what to mirror about
    if (!this.sk.active) return this.toast("Edit a sketch first.");
    const sel = this.sketchSel(), E = this.sk.detail.sketch.entities;
    const about = [...sel].reverse().find((id) => E[id]?.type === "line" && E[id]?.construction) || (sel.length > 1 ? [...sel].reverse().find((id) => E[id]?.type === "line") : null);
    const spec = this.skSpec("mirror");
    this.sk.setTool("select"); this.renderCommandManager();
    this.openPM(spec, { initial: { entities: sel.filter((id) => id !== about).map((id) => ({ kind: "skent", label: id, value: id })),
      ...(about ? { about: [{ kind: "skline", label: about, value: about }] } : {}) } });
  }

  sketch3dDialog(name = null) { // 3D Sketch: one chain of [x, y, z] points per segment, blank line between segments
    const segs = name ? (this.model.features.find((f) => f.name === name)?.params?.segments || []) : [];
    const text = segs.map((g) => g.points.map((q) => q.join(", ")).join("\n")).join("\n\n") || "0, 0, 0\n0, 0, 50\n0, 40, 50";
    const kind = segs[0]?.kind || "line";
    this.modal(name ? `Edit ${name}` : "3D Sketch", `<div class="field"><label>Points (x, y, z per line; blank line starts a new segment)</label>
      <textarea id="s3p" rows="9" style="width:100%;font:12px monospace">${escapeHtml(text)}</textarea></div>
      <div class="field"><label>Segment type</label><select id="s3k"><option value="line">Line chain</option><option value="spline">Spline</option></select></div>
      <label class="pm-check"><input id="s3c" type="checkbox"> Closed</label>`, async () => {
      const groups = $("#s3p").value.trim().split(/\n\s*\n/).map((g) => g.split("\n").map((l) => l.split(/[,\s]+/).filter(Boolean).map(Number)).filter((r) => r.length === 3 && r.every((x) => !isNaN(x))));
      const k = $("#s3k").value, closed = $("#s3c").checked;
      if (name) {
        await this.run("edit_feature", { name, params: { segments: groups.map((points) => ({ points, kind: k, closed })) } });
      } else {
        const r = await this.run("create_3d_sketch", { points: groups[0], kind: k, closed });
        const sk = r?.result?.sketch;
        for (const g of groups.slice(1)) await this.run("add_3d_sketch_segment", { sketch: sk, points: g, kind: k, closed });
      }
    }, "sketch3d");
    setTimeout(() => { const k = $("#s3k"); if (k) k.value = kind; });
  }

  sketchPattern(kind) { return this.sketchPM(this.skSpec(kind === "linear" ? "linear" : "circular")); }

  convertEntities() { return this.sketchPM(this.skSpec("convert")); }

  async intersectionCurve() {
    if (!this.sk.active) return this.toast("Edit a sketch whose plane cuts the body.");
    await this.run("sketch_intersection_curve", { sketch: this.sk.name });
  }

  // Sketch Fillet / Chamfer, as in SOLIDWORKS: set the size, then click corners (or two entities); the tool stays on
  sketchChamfer() { this.sketchCornerTool("sk_chamfer"); }
  sketchFillet() { this.sketchCornerTool("sk_fillet"); }
  sketchCornerTool(tool) {
    if (!this.sk.active) return this.toast("Edit a sketch first.");
    const fillet = tool === "sk_fillet", corner = this.selection.find((s) => s.kind === "skpoint");
    this.sk.openModify(this.vp.w / 2, this.vp.h / 2, fillet ? "Fillet radius" : "Chamfer distance", fillet ? this.skFilletR : this.skChamferD, async (v) => {
      if (fillet) this.skFilletR = v; else this.skChamferD = v;
      if (corner && corner.id !== "ORIGIN") await this.sketchCorner(tool, corner.id);
      this.setSketchTool(tool);
    });
  }
  sketchCorner(tool, point) {
    return tool === "sk_fillet" ? this.run("sketch_fillet", { sketch: this.sk.name, point, radius: this.skFilletR })
      : this.run("sketch_chamfer", { sketch: this.sk.name, point, distance: this.skChamferD });
  }

  planeRow(row, label, name, depth) {
    row({ icon: "plane_item", label, depth, selected: this.selection.some((s) => s.kind === "plane" && s.id === name),
      click: (e) => this.treePick({ kind: "plane", id: name, label, value: name }, e),
      hover: (on) => { this.vp.setHover(on ? { kind: "plane", id: name } : null); },
      ctx: (e) => this.ctxMenu(e, [["sketch", "Sketch", () => this.createSketch(name)], ["normal_to", "Normal To", () => this.normalToPlane(name)], ["plane", "Offset Plane...", () => this.feature("plane", { reference: [{ label, value: name }] })],
        "-", ["eye", "Hide / Show", () => this.togglePlane(name)]]) });
  }

  togglePlane(name) {
    const p = (this.mesh?.planes || []).find((x) => x.name === name);
    return this.run("set_feature_visibility", { name, hidden: !(p && p.hidden) });
  }

  rollbar(tree, feats, rollIndex) {
    const bar = el("div", ""); bar.id = "rollbar"; bar.title = "Rollback bar: drag to roll back the model";
    tree.append(bar);
    bar.onpointerdown = (e) => {
      e.preventDefault(); bar.classList.add("dragging");
      const rows = [...tree.querySelectorAll(".t-row")].filter((r) => !r.classList.contains("t-root"));
      const names = feats.filter((f) => !f.absorbed_by).map((f) => f.name);
      const featureRows = rows.slice(rows.length - names.length - (rows.length - names.length - 8 > 0 ? 0 : 0)).slice(-names.length);
      const up = (ev) => {
        document.removeEventListener("pointerup", up); bar.classList.remove("dragging");
        let k = featureRows.findIndex((r) => ev.clientY < r.getBoundingClientRect().top + r.offsetHeight / 2);
        if (k < 0) k = names.length;
        const to = k === 0 ? null : names[k - 1];
        if (k === names.length) this.run("rollback", {}); else if (k === 0) this.toast("Cannot roll back above the first feature", "err"); else this.run("rollback", { to_feature: to });
      };
      document.addEventListener("pointerup", up);
    };
  }

  // FeatureManager drag: drop a feature on another to move it after that one (or on Origin: to the top),
  // or on a folder to put it in the folder
  treeDragStart(e, name) {
    if (e.button !== 0 || this.pm.active) return;
    const tree = $("#tree"), y0 = e.clientY; let target = null, dragging = false;
    const mark = el("div", "tree-drop");
    const mv = (m) => {
      if (!dragging && Math.abs(m.clientY - y0) < 6) return;
      dragging = true; tree.classList.add("dragging-feature");
      const r = document.elementFromPoint(m.clientX, m.clientY)?.closest(".t-row");
      target = r && r.dataset.feature !== name && (r.dataset.feature || r.dataset.folder || r.dataset.top) ? r : null;
      if (target) { tree.append(mark); mark.style.top = `${target.offsetTop + target.offsetHeight - 1}px`; } else mark.remove();
    };
    const up = async () => {
      document.removeEventListener("pointermove", mv); document.removeEventListener("pointerup", up);
      mark.remove(); tree.classList.remove("dragging-feature");
      if (!dragging || !target) return;
      this.treeDragged = true; setTimeout(() => { this.treeDragged = false; });
      if (target.dataset.folder) {
        const fd = (this.model.folders || []).find((x) => x.name === target.dataset.folder);
        await this.run("remove_folder", { name: fd.name }); await this.run("add_folder", { name: fd.name, features: [...fd.features, name] });
      } else await this.run("reorder_feature", { name, after: target.dataset.top ? null : target.dataset.feature }).catch(() => {});
    };
    document.addEventListener("pointermove", mv); document.addEventListener("pointerup", up);
  }

  // FeatureManager <-> model: a feature's faces light up when its row is hovered or selected, and a selected
  // face's feature is highlighted in the tree
  async faceOwners() {
    if (this.model.kind === "assembly") return {};
    if (this._owners?.version !== this.version) {
      const r = await this.api("/api/command", { name: "face_features", args: {} });
      this._owners = { version: this.version, map: r.ok ? r.result.faces : {} };
    }
    return this._owners.map;
  }
  async featureFaces(name) { const m = await this.faceOwners(); return Object.keys(m).filter((k) => m[k] === name); }
  async hoverFeature(name) { this.vp.setFeatureFaces(name ? await this.featureFaces(name) : []); }
  async syncFeatureHighlight() {
    if (this.model.kind === "assembly" || this.sk.active) { this.vp.setFeatureFaces(undefined, []); this.relatedFeatures = null; return; }
    const feats = this.selection.filter((s) => s.kind === "feature").map((s) => s.id);
    const faces = this.selection.filter((s) => s.kind === "face").map((s) => s.id);
    const owners = feats.length || faces.length ? await this.faceOwners() : {};
    this.vp.setFeatureFaces(undefined, Object.keys(owners).filter((k) => feats.includes(owners[k])));
    const rel = new Set(faces.map((f) => owners[f]).filter(Boolean));
    const changed = [...rel].join() !== [...(this.relatedFeatures || [])].join();
    this.relatedFeatures = rel;
    if (changed) this.renderTree();
  }

  // the context toolbar a click on a feature shows, as in SOLIDWORKS
  treeToolbar(f, e) {
    const tb = $("#minitb"); tb.innerHTML = "";
    const add = (k, t, fn) => { const b = el("button", "", icon(k)); b.title = t; b.onclick = (ev) => { ev.stopPropagation(); tb.classList.add("hidden"); fn(); }; tb.append(b); };
    const sk = f.type === "sketch" ? f.name : this.model.features.find((k) => k.absorbed_by === f.name && k.type === "sketch")?.name;
    if (f.type !== "sketch") add("edit", "Edit Feature", () => this.editFeature(f.name));
    if (sk) add("sketch", "Edit Sketch", () => this.enterSketch(sk));
    add(f.suppressed ? "eye" : "suppress", f.suppressed ? "Unsuppress" : "Suppress", () => this.run("suppress_feature", { name: f.name, suppressed: !f.suppressed }));
    if (f.type === "sketch") add("eye", "Hide / Show", () => this.toggleSketchVisibility(f.name));
    if (f.type === "plane") add("eye", "Hide / Show", () => this.togglePlane(f.name));
    add("rollback", "Rollback", () => this.rollbackAbove(f.name));
    const g = this.graphics.getBoundingClientRect(), r = e.currentTarget?.getBoundingClientRect?.() || { right: g.left, top: e.clientY };
    tb.style.left = `${Math.max(4, r.right - g.left + 6)}px`; tb.style.top = `${Math.max(4, r.top - g.top - 6)}px`; tb.classList.remove("hidden");
  }

  // double-click a feature: its dimensions appear on the model and can be edited (Modify box), as in SOLIDWORKS
  async showFeatureDims(name, quiet = false) {
    const f = this.model.features.find((x) => x.name === name);
    if (!f || this.sk.active) return;
    const skName = f.type === "sketch" ? f.name : this.model.features.find((k) => k.absorbed_by === f.name && k.type === "sketch")?.name;
    if (!skName) { if (!quiet) this.editFeature(name); return; }
    const res = await this.api("/api/command", { name: "get_feature", args: { name: skName } });
    const ov = (this.mesh.sketches || []).find((s) => s.name === skName);
    if (!res.ok || !ov) { this.sk.showDims = null; return; }
    if (!res.result.sketch?.dimensions?.length && !quiet) { this.sk.showDims = null; this.editFeature(name); return; }
    this.sk.showDims = { feature: name, name: skName, detail: res.result, overlay: ov };
    this.vp.dirty = true;
  }
  hideFeatureDims() { if (this.sk.showDims) { this.sk.showDims = null; this.vp.dirty = true; } }

  treeClick(f, e) {
    if (this.treeDragged) return;
    if (this.pm.active) {
      const kind = { sketch: "sketch", plane: "plane", csys: "plane", ref_axis: "axis", ref_point: "point", sketch3d: "sketch", helix: "sketch", projected_curve: "sketch", composite_curve: "sketch", xyz_curve: "sketch" }[f.type] || "feature";
      if (this.pm.accept({ kind, id: f.name, label: f.name, value: f.name })) return;
      if (kind !== "feature" && this.pm.accept({ kind: "feature", id: f.name, label: f.name, value: f.name })) return;
      return;
    }
    if (f.type === "plane") this.vp.showPlanes([f.name]);
    this.setSelection(e.ctrlKey ? [...this.selection, { kind: "feature", id: f.name, type: f.type }] : [{ kind: "feature", id: f.name, type: f.type }]);
    if (!e.ctrlKey) this.treeToolbar(f, e);
  }

  treePick(pick, e) {
    if (this.pm.active && this.pm.accept(pick)) return;
    this.vp.showPlanes([pick.id]);
    this.setSelection(e.ctrlKey ? [...this.selection, pick] : [pick]);
  }

  featureMenu(e, f) {
    const items = [];
    if (f.type === "sketch") items.push(["sketch", "Edit Sketch", () => this.enterSketch(f.name)]);
    else {
      items.push(["edit", "Edit Feature", () => this.editFeature(f.name)]);
      const sk = this.model.features.find((k) => k.absorbed_by === f.name);
      if (sk) items.push(["sketch", "Edit Sketch", () => this.enterSketch(sk.name)]);
    }
    if (f.type === "plane" || f.type === "csys") items.push(["sketch", "Sketch", () => this.createSketch(f.name)]);
    const skOf = f.type === "sketch" ? f.name : this.model.features.find((k) => k.absorbed_by === f.name && k.type === "sketch")?.name;
    if (skOf) items.push(["plane", "Edit Sketch Plane", () => this.editSketchPlane(skOf)]);
    items.push(["relation", "Parent/Child...", () => this.parentChild(f.name)]);
    if (!["sketch", "plane", "csys", "ref_axis", "ref_point"].includes(f.type)) items.push(["appearance", "Appearance...", () => { this.setSelection([{ kind: "feature", id: f.name, type: f.type }]); this.appearanceDialog(); }]);
    items.push("-", [f.suppressed ? "eye" : "suppress", f.suppressed ? "Unsuppress" : "Suppress", () => this.run("suppress_feature", { name: f.name, suppressed: !f.suppressed })]);
    items.push(["rollback", "Rollback", () => this.rollbackAbove(f.name)], ["rollback", "Roll to Previous", () => this.rollToPrevious()]);
    items.push(["folder", "Add to New Folder", () => this.addToNewFolder(f.name)]);
    if (this.model.rollback != null) items.push(["rollback", "Roll to End", () => this.run("rollback", {})]);
    if (f.type === "sketch") items.push(["eye", "Hide / Show", () => this.toggleSketchVisibility(f.name)]);
    if (f.type === "sketch3d") items.splice(0, 1, ["sketch3d", "Edit 3D Sketch", () => this.sketch3dDialog(f.name)]);
    if (f.type === "plane") items.push(["eye", "Hide / Show", () => this.togglePlane(f.name)]);
    if (["ref_axis", "ref_point", "csys", ...CURVE_TYPES].includes(f.type)) items.push(["eye", "Hide / Show", () => this.run("set_feature_visibility", { name: f.name, hidden: !(this.mesh.refs || []).find((r) => r.name === f.name)?.hidden })]);
    items.push(["rename", "Rename (F2)", () => this.renameFeature(f.name)], "-", ["trash", "Delete...", () => this.deleteFeature(f.name)]);
    this.ctxMenu(e, items);
  }

  editSketchPlane(sketch) {
    this.openPM({ title: `Edit Sketch Plane: ${sketch}`, icon: "plane", command: "edit_sketch_plane",
      groups: [{ title: "Sketch Plane/Face", fields: [{ key: "plane", type: "selection", accepts: ["plane", "face"], single: true, label: "Plane or planar face" }] }],
      args: (v) => ({ sketch, plane: v.plane?.[0]?.value }), message: "Select the new plane or planar face for the sketch." });
  }

  async parentChild(name) {
    const r = await this.evalCall("parent_child", { name }); if (!r) return;
    const list = (xs) => xs.length ? xs.map((x) => `<div>${icon(TYPE_ICON[this.model.features.find((f) => f.name === x)?.type] || "part")} ${escapeHtml(x)}</div>`).join("") : "<div><i>none</i></div>";
    this.modal(`Parent/Child Relationships: ${name}`, `<div class="pc-cols"><div><div class="tp-h">Parents</div>${list(r.parents)}</div><div><div class="tp-h">Children</div>${list(r.children)}</div></div>`, null, "relation");
  }

  rollToPrevious() { // the rollback bar steps back one feature
    const visible = this.model.features.filter((f) => !f.absorbed_by && !f.rolled_back);
    if (visible.length < 2) return this.toast("Cannot roll back above the first feature", "err");
    this.run("rollback", { to_feature: visible[visible.length - 2].name });
  }

  addToNewFolder(name) {
    const sel = this.selection.filter((s) => s.kind === "feature").map((s) => s.id);
    const names = sel.includes(name) ? sel : [name];
    let n = 1; while ((this.model.folders || []).some((f) => f.name === `Folder${n}`)) n++;
    this.expanded.add(`folder:Folder${n}`);
    this.run("add_folder", { name: `Folder${n}`, features: names }).catch(() => this.expanded.delete(`folder:Folder${n}`));
  }

  rollbackAbove(name) {
    const visible = this.model.features.filter((f) => !f.absorbed_by), i = visible.findIndex((f) => f.name === name);
    if (i <= 0) return this.toast("Cannot roll back above the first feature", "err");
    // put the bar just above the feature (after the previous top-level feature and its sketch)
    this.run("rollback", { to_feature: visible[i - 1].name });
  }

  async toggleSketchVisibility(name) {
    const f = (this.mesh.sketches || []).find((s) => s.name === name);
    await this.run("set_sketch_visibility", { sketch: name, hidden: !(f && f.hidden) });
  }

  ctxMenu(e, items) {
    const m = $("#ctxmenu"); m.innerHTML = "";
    for (const it of items) {
      if (it === "-") { m.append(el("div", "cx-sep")); continue; }
      const r = el("div", "cx-item", `${icon(it[0])}<span>${it[1]}</span>`); r.onclick = () => { m.classList.add("hidden"); it[2](); }; m.append(r);
    }
    m.style.left = `${Math.min(e.clientX, innerWidth - 220)}px`; m.style.top = `${Math.min(e.clientY, innerHeight - 30 * items.length)}px`;
    m.classList.remove("hidden");
    setTimeout(() => m.classList.remove("hidden"));
  }

  renameFeature(name) {
    this.modal("Rename", `<div class="field"><label>New name</label><input id="rn" value="${escapeHtml(name)}"></div>`, async () => {
      const v = $("#rn").value.trim(); if (v && v !== name) await this.run("rename_feature", { name, new_name: v });
    }, "rename");
    setTimeout(() => $("#rn")?.select());
  }

  deleteFeature(name) {
    const f = this.model.features.find((x) => x.name === name);
    const sk = this.model.features.find((k) => k.absorbed_by === name);
    this.modal("Confirm Delete", `<p>Delete <b>${escapeHtml(name)}</b>?</p>${sk ? `<label class="pm-check"><input type="checkbox" id="delabs" checked> Also delete absorbed feature <b>${escapeHtml(sk.name)}</b></label>` : ""}`,
      async () => { await this.run("delete_feature", { name, delete_absorbed_sketch: sk ? $("#delabs").checked : true }); this.setSelection([]); }, "trash");
    void f;
  }

  // ================================================================== selection
  setSelection(list) {
    this.selection = list;
    this.vp.setSelection(list.filter((s) => ["face", "edge", "sketch", "plane", "axis", "point", "csys", "curve"].includes(s.kind)));
    if (!this.sk.active) this.vp.setTemp(list.filter((s) => s.kind === "vertex").map((s) => ({ pts: s.point, point: true, color: 0x2f7be0, size: 10 })));
    this.renderTree();
    this.syncFeatureHighlight();
    if (this.sk.active) this.renderSketchPM();
    if (!list.length) $("#minitb").classList.add("hidden");
    if (this.measureOpen) this.updateMeasure();
  }

  selectDim(id, add) { if (!add) this.selDims.clear(); this.selDims.has(id) ? this.selDims.delete(id) : this.selDims.add(id); this.vp.dirty = true; }

  showMiniToolbar(x, y, pick) {
    const tb = $("#minitb"); tb.innerHTML = "";
    const add = (k, t, fn) => { const b = el("button", "", icon(k)); b.title = t; b.onclick = (e) => { e.stopPropagation(); tb.classList.add("hidden"); fn(); }; tb.append(b); };
    if (pick.kind === "face") {
      add("sketch", "Sketch", () => this.createSketch(pick.id));
      add("normal_to", "Normal To", () => this.normalToFace(pick));
      add("fillet", "Fillet", () => this.feature("fillet"));
      add("shell", "Shell", () => this.feature("shell"));
      add("hole", "Hole", () => this.feature("hole"));
    } else if (pick.kind === "edge") {
      add("fillet", "Fillet", () => this.feature("fillet"));
      add("chamfer", "Chamfer", () => this.feature("chamfer"));
    } else return;
    tb.style.left = `${x + 14}px`; tb.style.top = `${y - 34}px`; tb.classList.remove("hidden");
  }

  // ================================================================== viewport interaction
  bindViewport() {
    const vp = this.vp;
    vp.on("move", (e) => {
      this.lastMouse = { x: e.x, y: e.y };
      if (this.placing) return this.placingMove(e);
      if (this.box) return this.moveBox(e);
      if (this.gesture) return this.moveGesture(e);
      if (this.compDrag) return this.moveCompDrag(e);
      if (this.sk.active && this.sk.move(e)) return;
      if (e.buttons) return;
      const kinds = this.pm.active ? this.pickKinds() : this.sk.active ? ["sketch"] : this.filterKinds();
      vp.setHover(vp.pick(e.x, e.y, { kinds }));
      if (!this.sk.active) this.status.coords(null);
    });
    vp.on("leave", () => vp.setHover(null));
    vp.on("down", (e) => {
      if (this.placing) return;
      if (this.sk.active && this.sk.down(e)) return;
      if (e.button === 2 && !e.event.ctrlKey) { this.gesture = { x: e.x, y: e.y }; return; }
      if (e.button !== 0) return;
      if (this.zoomArea) { this.box = { x0: e.x, y0: e.y, x1: e.x, y1: e.y, zoom: true }; return; }
      if (this.model?.kind === "assembly" && !this.pm.active && !e.event.ctrlKey) {
        const pk = vp.pick(e.x, e.y, { kinds: ["face"] });
        if (pk) { this.startCompDrag(e, pk); return; }
      }
      if (!this.awaitPlane && (!this.sk.active || this.sk.tool === "select")) this.box = { x0: e.x, y0: e.y, x1: e.x, y1: e.y };
    });
    vp.on("up", async (e) => {
      if (this.box) return this.endBox(e);
      if (this.gesture) return this.endGesture(e);
      if (this.compDrag) return this.endCompDrag(e);
      if (this.sk.active) await this.sk.up(e);
    });
    vp.on("click", async (e) => {
      $("#minitb").classList.add("hidden"); this.hideFeatureDims();
      if (this.placing) return this.placingClick(e);
      if (this.sk.active && await this.sk.click(e)) return;
      if (this.awaitPlane) return this.planePicked(vp.pick(e.x, e.y, { kinds: ["face", "plane"] }));
      if (this.pm.active) return this.pmPick(e);
      const pick = vp.pick(e.x, e.y, { kinds: this.sk.active ? null : this.filterKinds() || ["sketch", "axis", "point", "csys", "curve", "edge", "face", "plane"] });
      if (this.sk.active) return this.sketchSelect(e, pick);
      if (!pick) return this.setSelection([]);
      const item = pick.kind === "sketch" ? { kind: "sketch", id: pick.id, sketch: pick.sketch, type: pick.type } : { ...pick };
      const same = (s) => s.kind === item.kind && s.id === item.id;
      const list = e.event.ctrlKey ? (this.selection.some(same) ? this.selection.filter((s) => !same(s)) : [...this.selection, item]) : [item];
      this.setSelection(list);
      if (!e.event.ctrlKey && this.model.kind !== "assembly") this.showMiniToolbar(e.x, e.y, item);
    });
    vp.on("dblclick", (e) => {
      if (this.sk.active) { this.sk.dblclick(); return; }
      const pick = vp.pick(e.x, e.y, { kinds: ["sketch"] });
      if (pick) this.enterSketch(pick.sketch);
    });
    vp.on("rightclick", (e) => {
      if (this.suppressContext) return;
      if (this.sk.active) {
        const ents = this.selection.filter((s) => s.kind === "sketch");
        this.ctxMenu(e.event, [["select", "Select", () => this.setSketchTool("select")], ["line", "Line", () => this.setSketchTool("line")], ["rectangle", "Corner Rectangle", () => this.setSketchTool("rectangle")],
          ["circle", "Circle", () => this.setSketchTool("circle")], ["smart_dim", "Smart Dimension", () => this.setSketchTool("smart_dim")], ["trim", "Trim Entities", () => this.setSketchTool("trim")], "-",
          ...(ents.length ? [["construction", "Construction Geometry", () => this.toggleConstruction()], ["relation", "Add Relation...", () => this.renderSketchPM()],
            ["select", "Select Chain", () => this.selectChain()], ["trash", "Delete", () => this.deleteSelection()], "-"] : []),
          ["delete_rel", "Display/Delete Relations", () => this.deleteRelationsPM()], ["zoom_fit", "Zoom to Fit", () => vp.zoomFit()], "-",
          ["exit_sketch", "Exit Sketch", () => this.exitSketch()]]);
        return;
      }
      const pick = vp.pick(e.x, e.y, { kinds: this.filterKinds() });
      const items = [["select", "Select Other", () => this.selectOther(e)], ["zoom_fit", "Zoom to Fit", () => vp.zoomFit()]];
      if (this.pm.active) items.unshift(["ok", "OK", () => this.pmOK()], ["cancel", "Cancel", () => this.pmCancel()], "-");
      if (pick?.kind === "face" && this.model.kind === "assembly") {
        const comp = pick.id.split("/")[0];
        items.push("-", ["eye", "Hide Component", () => this.toggleComponents([comp])], ["eye", "Isolate", () => this.isolateComponents([comp])]);
        if (this.hiddenComps.size) items.push(["eye", "Show All Components", () => this.showAllComponents()]);
      }
      if (pick?.kind === "face") items.unshift(["sketch", "Sketch", () => this.createSketch(pick.id)], ["normal_to", "Normal To", () => this.normalToFace(pick)], "-");
      if (pick?.kind === "sketch") items.unshift(["sketch", `Edit ${pick.sketch}`, () => this.enterSketch(pick.sketch)], "-");
      this.ctxMenu(e.event, items);
    });
  }

  // ------------------------------------------------------------------ box select / zoom to area
  moveBox(e) {
    const b = this.box; b.x1 = e.x; b.y1 = e.y;
    if (!b.moved && Math.hypot(b.x1 - b.x0, b.y1 - b.y0) < 5) return;
    b.moved = true;
    if (!this.boxEl) { this.boxEl = el("div", "selbox"); this.graphics.append(this.boxEl); }
    const crossing = !b.zoom && b.x1 < b.x0;
    Object.assign(this.boxEl.style, { left: `${Math.min(b.x0, b.x1)}px`, top: `${Math.min(b.y0, b.y1)}px`, width: `${Math.abs(b.x1 - b.x0)}px`, height: `${Math.abs(b.y1 - b.y0)}px` });
    this.boxEl.classList.toggle("crossing", crossing);
  }

  endBox(e) {
    const b = this.box; this.box = null; this.boxEl?.remove(); this.boxEl = null;
    if (!b.moved) return;
    const x0 = Math.min(b.x0, b.x1), x1 = Math.max(b.x0, b.x1), y0 = Math.min(b.y0, b.y1), y1 = Math.max(b.y0, b.y1);
    if (b.zoom) { this.zoomArea = false; this.prompt(""); return this.vp.zoomToRect(x0, y0, x1, y1); }
    // left to right: window (wholly inside); right to left: crossing (inside or crossing the box), as SOLIDWORKS
    const crossing = b.x1 < b.x0, inside = (q) => q[0] >= x0 && q[0] <= x1 && q[1] >= y0 && q[1] <= y1;
    const hit = (pts, step = 1) => {
      const q = []; for (let i = 0; i < pts.length; i += 3 * step) q.push(this.vp.toScreen([pts[i], pts[i + 1], pts[i + 2]]));
      if (!crossing) return q.every(inside);
      return q.some(inside) || q.some((a, i) => i > 0 && segHitsRect(q[i - 1], a, x0, y0, x1, y1));
    };
    const items = [];
    if (this.sk.active) {
      for (const o of this.vp.sketchEnts) if (o.userData.sketch === this.sk.name && o.userData.type !== "point" && hit(o.userData.pts))
        items.push({ kind: "sketch", id: o.userData.id, sketch: this.sk.name, type: o.userData.type });
      for (const [id, uv] of Object.entries(this.sk.pts())) if (id !== "ORIGIN" && inside(this.sk.scr(uv[0], uv[1]))) items.push({ kind: "skpoint", id, sketch: this.sk.name });
    } else {
      const f = this.filter;
      if (!f || f.has("edge")) for (const [id, l] of this.vp.edges) if (l.visible && this.vp.groups.edges.visible && hit(l.userData.pts)) items.push({ kind: "edge", id });
      if (f?.has("face")) for (const [id, m] of this.vp.faces) if (m.visible) {
        const a = m.geometry.getAttribute("position").array; if (hit(a, Math.max(1, Math.floor(a.length / 3 / 200)))) items.push({ kind: "face", id });
      }
    }
    if (this.pm.active) {
      for (const it of items) {
        if (it.kind === "sketch") this.pm.accept({ kind: "skent", label: it.id, value: it.id });
        else if (it.kind !== "skpoint") this.pm.accept({ kind: it.kind, label: `${cap(it.kind)}<${String(it.id).replace(/\D/g, "")}>`, value: it.id });
      }
      return;
    }
    const base = e.event.ctrlKey ? this.selection : [];
    this.setSelection([...base, ...items.filter((it) => !base.some((x) => x.kind === it.kind && x.id === it.id))]);
    if (this.sk.active) this.vp.setTemp(this.selection.filter((x) => x.kind === "skpoint").map((x) => { const uv = this.sk.pts()[x.id]; return { pts: this.sk.w(uv[0], uv[1]), point: true, color: 0x2f7be0, size: 10 }; }));
  }

  startZoomArea() { this.zoomArea = true; this.prompt("Zoom to Area: drag a box around the area to zoom to."); }
  zoomToSelection() {
    const items = this.selection.filter((s) => ["face", "edge", "sketch"].includes(s.kind));
    if (!items.length) return this.vp.zoomFit();
    this.vp.zoomToItems(items);
  }

  // ------------------------------------------------------------------ selection filter (F5; X faces, E edges, V vertices)
  filterHas(k) { return !!this.filter?.has(k); }
  toggleFilter(k) {
    if (!k) this.filter = null;
    else { const f = new Set(this.filter || []); f.has(k) ? f.delete(k) : f.add(k); this.filter = f.size ? f : null; }
    this.huFilter?.classList.toggle("on", !!this.filter);
    this.toast(this.filter ? `Selection filter: ${[...this.filter].map((x) => cap(x) + "s").join(", ")}` : "Selection filters cleared");
  }
  filterKinds() { return this.filter ? [...this.filter] : null; }

  // ------------------------------------------------------------------ Select Other (right-click): everything under the cursor
  selectOther(e) {
    const list = this.vp.pickAll(e.x, e.y).filter((p) => !this.filter || this.filter.has(p.kind));
    if (!list.length) return this.toast("Nothing else under the cursor.");
    const m = $("#ctxmenu"); m.innerHTML = "";
    m.append(el("div", "cx-item cx-title", "<span>Select Other</span>"));
    for (const p of list) {
      const r = el("div", "cx-item", `${icon(p.kind)}<span>${cap(p.kind)}&lt;${escapeHtml(String(p.id).replace(/^.*?(\d+)$/, "$1"))}&gt;${p.id.includes("/") ? ` (${escapeHtml(p.id.split("/")[0])})` : ""}</span>`);
      r.onmouseenter = () => this.vp.setHover(p);
      r.onclick = () => {
        m.classList.add("hidden");
        if (this.pm.active) return this.pm.accept({ kind: p.kind, label: `${cap(p.kind)}<${String(p.id).replace(/\D/g, "")}>`, value: p.id, point: p.point });
        this.setSelection(e.event.ctrlKey ? [...this.selection, p] : [p]);
      };
      m.append(r);
    }
    m.style.left = `${Math.min(e.event.clientX, innerWidth - 220)}px`; m.style.top = `${Math.min(e.event.clientY, innerHeight - 30 * (list.length + 1))}px`;
    setTimeout(() => m.classList.remove("hidden"));
  }

  // ------------------------------------------------------------------ mouse gestures (right-drag), S-key shortcut bar, repeat last command
  gestureItems() {
    if (this.sk.active) return { up: ["smart_dim", "Smart Dimension", () => this.setSketchTool("smart_dim")], right: ["line", "Line", () => this.setSketchTool("line")],
      down: ["rectangle", "Corner Rectangle", () => this.setSketchTool("rectangle")], left: ["circle", "Circle", () => this.setSketchTool("circle")] };
    return { up: ["v_top", "Top", () => this.vp.setView("top")], right: ["v_right", "Right", () => this.vp.setView("right")],
      down: ["v_bottom", "Bottom", () => this.vp.setView("bottom")], left: ["v_left", "Left", () => this.vp.setView("left")] };
  }
  moveGesture(e) {
    const g = this.gesture, dx = e.x - g.x, dy = e.y - g.y, d = Math.hypot(dx, dy);
    if (d < 12 && !g.el) return;
    if (!g.el) {
      g.el = el("div", "gesture"); g.el.style.left = `${g.x}px`; g.el.style.top = `${g.y}px`;
      for (const [dir, [k, label]] of Object.entries(this.gestureItems())) g.el.append(el("div", `g-item g-${dir}`, `${icon(k)}<span>${label}</span>`));
      this.graphics.append(g.el);
    }
    g.dir = d < 30 ? null : Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
    g.el.querySelectorAll(".g-item").forEach((x) => x.classList.toggle("on", x.classList.contains(`g-${g.dir}`)));
  }
  endGesture() {
    const g = this.gesture; this.gesture = null;
    g.el?.remove();
    if (g.dir) { this.suppressContext = true; setTimeout(() => { this.suppressContext = false; }, 50); this.gestureItems()[g.dir][2](); }
  }

  shortcutBar() {
    const x = this.lastMouse.x, y = this.lastMouse.y;
    const items = this.sk.active ? [["line", "Line", () => this.setSketchTool("line")], ["rectangle", "Corner Rectangle", () => this.setSketchTool("rectangle")],
      ["circle", "Circle", () => this.setSketchTool("circle")], ["arc", "Centerpoint Arc", () => this.setSketchTool("arc")], ["slot", "Straight Slot", () => this.setSketchTool("slot")],
      ["smart_dim", "Smart Dimension", () => this.setSketchTool("smart_dim")], ["trim", "Trim Entities", () => this.setSketchTool("trim")], ["sk_fillet", "Sketch Fillet", () => this.sketchFillet()],
      ["offset", "Offset Entities", () => this.sketchOffset()], ["sk_mirror", "Mirror Entities", () => this.sketchMirror()], ["construction", "Construction Geometry", () => this.toggleConstruction()],
      ["exit_sketch", "Exit Sketch", () => this.exitSketch()]]
      : this.model.kind === "assembly" ? [["insert_comp", "Insert Components", () => this.insertComponentDialog()], ["mate", "Mate", () => this.openPM(SPECS.mate)],
        ["explode", "Exploded View", () => this.explodeDialog()], ["interference", "Interference Detection", () => this.interference()], ["measure", "Measure", () => this.measure()]]
      : [["sketch", "Sketch", () => this.sketchCommand()], ["extrude", "Extruded Boss/Base", () => this.feature("extrude")], ["cut_extrude", "Extruded Cut", () => this.feature("cut_extrude")],
        ["revolve", "Revolved Boss/Base", () => this.feature("revolve")], ["fillet", "Fillet", () => this.feature("fillet")], ["chamfer", "Chamfer", () => this.feature("chamfer")],
        ["hole", "Hole Wizard", () => this.feature("hole_wizard")], ["shell", "Shell", () => this.feature("shell")], ["linear_pattern", "Linear Pattern", () => this.feature("linear_pattern")],
        ["mirror", "Mirror", () => this.feature("mirror")], ["plane", "Plane", () => this.feature("plane")], ["measure", "Measure", () => this.measure()]];
    $("#sbar")?.remove();
    const bar = el("div", "sbar"); bar.id = "sbar";
    for (const [k, t, fn] of items) { const b = el("button", "", icon(k)); b.title = t; b.onclick = (ev) => { ev.stopPropagation(); bar.remove(); fn(); }; bar.append(b); }
    bar.style.left = `${Math.min(x, this.vp.w - 6 * 34)}px`; bar.style.top = `${Math.max(4, y - 40)}px`;
    this.graphics.append(bar);
    const off = (ev) => { if (!bar.contains(ev.target)) { bar.remove(); document.removeEventListener("pointerdown", off); } };
    setTimeout(() => document.addEventListener("pointerdown", off));
  }

  remember(label, fn) { this.lastCommand = { label, fn }; }

  // ------------------------------------------------------------------ assembly: drag components, Hide/Show, Isolate
  startCompDrag(e, pick) {
    const name = pick.id.split("/")[0], c = (this.model.components || []).find((x) => x.name === name);
    if (!c) return;
    this.compDrag = { name, c, x0: e.x, y0: e.y, moved: false, fixed: c.fixed, cur: Object.fromEntries(this.model.components.map((k) => [k.name, k.pose])) };
  }
  moveCompDrag(e) {
    const d = this.compDrag;
    if (!d.moved && Math.hypot(e.x - d.x0, e.y - d.y0) < 4) return;
    if (d.fixed) { if (!d.warned) { d.warned = true; this.toast(`${d.name} is fixed: Float it to drag it.`); } return; }
    d.moved = true;
    const a = this.vp.screenToWorldOnTargetPlane(d.x0, d.y0), b = this.vp.screenToWorldOnTargetPlane(e.x, e.y);
    d.delta = [b.x - a.x, b.y - a.y, b.z - a.z];
    const pose = d.c.pose.slice(); for (let i = 0; i < 3; i++) pose[3 + i] += d.delta[i];
    this.vp.setComponentPoses({ [d.name]: pose }, d.cur);
  }
  async endCompDrag() {
    const d = this.compDrag; this.compDrag = null;
    if (!d.moved || !d.delta) return;
    this.vp.setComponentPoses(null, d.cur);
    const pos = d.c.pose.slice(3).map((v, i) => +(v + d.delta[i]).toFixed(4));
    await this.run("move_component", { name: d.name, position: pos }).catch(() => {});
  }
  selectedComponentsAny() {
    const names = new Set([...this.selectedComponents(), ...this.selection.filter((s) => s.kind === "face" || s.kind === "edge").map((s) => String(s.id).split("/")[0])]);
    return [...names];
  }
  toggleComponents(names) {
    if (!names?.length) return this.toast("Select components (in the tree or a face in the view) to hide or show.");
    for (const n of names) this.hiddenComps.has(n) ? this.hiddenComps.delete(n) : this.hiddenComps.add(n);
    this.vp.setHiddenComponents([...this.hiddenComps]); this.renderTree();
  }
  isolateComponents(names = this.selectedComponentsAny()) {
    if (!names.length) return this.toast("Select the components to isolate.");
    this.hiddenComps = new Set((this.model.components || []).map((c) => c.name).filter((n) => !names.includes(n)));
    this.vp.setHiddenComponents([...this.hiddenComps]); this.renderTree();
  }
  showAllComponents() { this.hiddenComps.clear(); this.vp.setHiddenComponents([]); this.renderTree(); }

  // ------------------------------------------------------------------ documents
  async switchDocument(name) { if (name !== this.model.name) { await this.run("activate_document", { name }); this.vp.zoomFit(); } }
  nextDocument() {
    const docs = (this.docs?.documents || []).map((d) => d.name); if (docs.length < 2) return;
    this.switchDocument(docs[(docs.indexOf(this.model.name) + 1) % docs.length]);
  }

  pickKinds() {
    const acc = this.pm.focusedAccepts();
    const k = new Set();
    for (const a of acc) { if (a === "sketchline" || a === "skent" || a === "skline") k.add("sketch"); else if (a === "skpoint") k.add("sketch"); else if (a !== "feature") k.add(a); }
    if (["hole", "hole_wizard"].includes(this.pm.spec?.command)) k.add("face");
    if (acc.includes("sketch")) k.add("sketch");
    return k.size ? [...k] : ["face", "edge"];
  }

  pmPick(e) {
    const spec = this.pm.spec;
    if (spec.command === "hole" || spec.command === "hole_wizard") { // face then positions on it
      const face = this.pm.values.face?.[0]?.value;
      const p = this.vp.pick(e.x, e.y, { kinds: ["face"] });
      if (!p) return;
      if (!face || (p.id !== face && this.pm.focusKey === "face")) { this.pm.focusKey = "face"; this.pm.accept({ kind: "face", id: p.id, label: p.id.replace("Face", "Face<") + ">", value: p.id }); this.holeFace = p; return; }
      if (p.id === face && p.normal) { const uv = facePlaneUV(p.normal, p.point, this.holeFace?.normal || p.normal); this.pm.addPoint(uv); }
      return;
    }
    const accs = this.pm.focusedAccepts();
    if (this.sk.active && (accs.includes("skpoint") || accs.includes("skent") || accs.includes("skline"))) { // the sketch being edited
      const sp = accs.includes("skpoint") ? this.sk.snap(e.x, e.y) : null;
      if (sp) return this.pm.accept({ kind: "skpoint", label: sp.id === "ORIGIN" ? "Origin" : `Point ${sp.id}`, value: sp.id });
      const pk = this.vp.pick(e.x, e.y, { sketchOnly: true }), E = this.sk.detail?.sketch?.entities || {};
      if (pk && pk.sketch === this.sk.name && E[pk.id]) {
        if (accs.includes("skline") && E[pk.id].type !== "line") return this.toast("Pick a line or centerline.");
        return this.pm.accept({ kind: accs.includes("skline") ? "skline" : "skent", label: pk.id, value: pk.id });
      }
      return;
    }
    if (this.pm.focusedAccepts().includes("vertex")) { // nearest model vertex (edge end point) in screen space
      let best = null, bd = 12;
      for (const ed of this.mesh.edges) for (const k of [0, ed.p.length - 3]) {
        const w = ed.p.slice(k, k + 3), [sx, sy] = this.vp.toScreen(w), d = Math.hypot(sx - e.x, sy - e.y);
        if (d < bd) { bd = d; best = w; }
      }
      if (best) this.pm.accept({ kind: "vertex", label: `Vertex (${best.map((v) => v.toFixed(1)).join(", ")})`, value: best });
      return;
    }
    const p = this.vp.pick(e.x, e.y, { kinds: this.pickKinds() });
    if (!p) return;
    let item;
    if (p.kind === "sketch") {
      const acc = this.pm.focusedAccepts();
      if (acc.includes("sketchline") && p.type === "line") item = { kind: "sketchline", label: `${p.sketch}/${p.id}`, value: `${p.sketch}/${p.id}` };
      else item = { kind: "sketch", label: p.sketch, value: p.sketch };
    } else if (p.kind === "plane") item = { kind: "plane", label: `${p.id} Plane`, value: p.id };
    else if (p.kind === "csys") item = { kind: this.pm.focusedAccepts().includes("plane") ? "plane" : "point", label: p.id, value: p.id };
    else if (p.kind === "axis" || p.kind === "point") item = { kind: p.kind, label: p.id, value: p.id };
    else if (p.kind === "curve") item = { kind: "sketch", label: p.id, value: p.id };
    else if (p.kind === "vertex") item = { kind: "vertex", label: `Vertex<${p.point.map((c) => +c.toFixed(2)).join(", ")}>`, value: p.point };
    else item = { kind: p.kind, label: p.id.includes("/") ? p.id : `${cap(p.kind)}<${p.id.replace(/\D/g, "")}>`, value: p.id, point: p.point };
    this.pm.accept(item);
  }

  // ================================================================== features / PropertyManager
  selectedSketch() {
    for (const s of this.selection) {
      if (s.kind === "feature" && s.type === "sketch") return s.id;
      if (s.kind === "sketch") return s.sketch;
    }
    if (this.sk.active) return this.sk.name;
    return null;
  }

  async feature(key, initial = {}) {
    const spec = SPECS[key];
    this.remember(spec?.title || key, () => this.feature(key));
    if (["extrude", "cut_extrude", "revolve", "cut_revolve"].includes(key)) {
      const sk = this.selectedSketch();
      if (this.sk.active) { await this.exitSketch(true); this.vp.setView("trimetric"); this.vp.zoomFit(); }
      if (!sk) { // SOLIDWORKS: pick a plane, sketch the profile, the feature continues on exit
        this.followUp = key; this.startPlanePick(); return;
      }
      initial = { sketch: [{ kind: "sketch", label: sk, value: sk }], ...initial };
    } else if (this.sk.active) await this.exitSketch(true);
    if (["fillet", "chamfer"].includes(key)) {
      initial = { items: this.selection.filter((s) => s.kind === "edge" || s.kind === "face").map((s) => ({ kind: s.kind, label: `${cap(s.kind)}<${s.id.replace(/\D/g, "")}>`, value: s.id })), ...initial };
    }
    if (key === "shell") initial = { faces: this.selection.filter((s) => s.kind === "face").map((s) => ({ kind: "face", label: `Face<${s.id.replace(/\D/g, "")}>`, value: s.id })), ...initial };
    if (["linear_pattern", "circular_pattern", "sketch_pattern", "curve_pattern", "table_pattern", "fill_pattern", "mirror"].includes(key)) {
      const feats = this.selection.filter((s) => s.kind === "feature" && !["sketch", "plane", "ref_axis", "ref_point", "csys"].includes(s.type));
      initial = { features: feats.map((s) => ({ kind: "feature", label: s.id, value: s.id })), ...initial };
      const ax = this.selection.find((s) => s.kind === "axis" || (s.kind === "feature" && s.type === "ref_axis"));
      if (ax && key === "circular_pattern") initial = { axis: [{ kind: "axis", label: ax.id, value: ax.id }], ...initial };
      if (ax && key === "linear_pattern") initial = { dir1: [{ kind: "axis", label: ax.id, value: ax.id }], ...initial };
    }
    if (key === "hole") { const f = this.selection.find((s) => s.kind === "face"); if (f) { initial = { face: [{ kind: "face", label: `Face<${f.id.replace(/\D/g, "")}>`, value: f.id }], ...initial }; this.holeFace = f; } }
    const sks = this.selection.filter((s) => (s.kind === "feature" && ["sketch", ...CURVE_TYPES].includes(s.type)) || s.kind === "sketch" || s.kind === "curve").map((s) => s.sketch || s.id);
    const skItems = [...new Set(sks)].map((n) => ({ kind: "sketch", label: n, value: n }));
    if (["sweep", "cut_sweep"].includes(key) && skItems.length) initial = { profile: skItems.slice(0, 1), path: skItems.slice(1, 2), ...initial };
    if (["loft", "cut_loft"].includes(key) && skItems.length) initial = { profiles: skItems, ...initial };
    if (["base_flange", "extruded_surface", "structural_member", "rib", "sketch_pattern"].includes(key) && skItems.length) initial = { sketch: skItems.slice(0, 1), ...initial };
    if (key === "curve_pattern" && skItems.length) initial = { path: skItems.slice(0, 1), ...initial };
    const face = this.selection.find((s) => s.kind === "face");
    if (["dome", "thread", "hole_wizard", "fill_pattern"].includes(key) && face) initial = { face: [{ kind: "face", label: face.id, value: face.id }], ...initial };
    const surf = this.selection.find((s) => s.kind === "feature" && s.type === "extruded_surface");
    if (key === "thicken" && surf) initial = { surface: [{ kind: "feature", label: surf.id, value: surf.id }], ...initial };
    const faceSel = this.selection.filter((s) => s.kind === "face").map((s) => ({ kind: "face", label: s.id, value: s.id }));
    if (["delete_face", "move_face", "replace_face"].includes(key) && faceSel.length) initial = { faces: faceSel, ...initial };
    if (["projected_curve", "split_line"].includes(key) && skItems.length) initial = { sketch: skItems.slice(0, 1), ...initial };
    const edge = this.selection.find((s) => s.kind === "edge");
    if (["edge_flange", "hem"].includes(key) && edge) initial = { edge: [{ kind: "edge", label: edge.id, value: edge.id }], ...initial };
    if (key === "gusset") initial = { faces: this.selection.filter((s) => s.kind === "face").slice(0, 2).map((s) => ({ kind: "face", label: s.id, value: s.id })), ...initial };
    if (["sketched_bend", "planar_surface", "revolved_surface"].includes(key) && skItems.length) initial = { sketch: skItems.slice(0, 1), ...initial };
    if (key === "filled_surface" && skItems.length) initial = { boundary: skItems.slice(0, 1), ...initial };
    if (key === "swept_surface" && skItems.length) initial = { profile: skItems.slice(0, 1), path: skItems.slice(1, 2), ...initial };
    if (key === "lofted_surface" && skItems.length) initial = { profiles: skItems, ...initial };
    const feats = this.selection.filter((s) => s.kind === "feature").map((s) => ({ kind: "feature", label: s.id, value: s.id }));
    if (key === "knit_surface" && feats.length) initial = { surfaces: feats, ...initial };
    if (["trim_surface", "offset_surface"].includes(key) && feats.length) initial = { surface: feats.slice(0, 1), ...initial };
    if (key === "end_cap" && feats.length) initial = { member: feats.slice(0, 1), ...initial };
    this.openPM(spec, { initial });
  }

  openPM(spec, { initial = {}, edit = null } = {}) {
    this.editing = edit;
    this.pm.open(spec, {
      initial, title: edit ? `${edit}` : spec.title,
      onChange: (args) => this.schedulePreview(args), onOK: (args) => this.pmOK(args), onCancel: () => this.pmCancel(),
    });
    this.showPane("pm"); this.renderConfirm();
    this.schedulePreview(this.pm.args());
  }

  schedulePreview(args) {
    clearTimeout(this.previewTimer);
    this.previewTimer = setTimeout(async () => {
      if (!this.pm.active) return;
      const spec = this.pm.spec;
      const cmd = spec.commandFor ? spec.commandFor(this.pm.values) : spec.command;
      const req = this.editing ? { name: "redefine_feature", args: { name: this.editing, command_name: cmd, args } } : { name: cmd, args };
      const res = await this.api("/api/preview", req);
      if (!this.pm.active) return;
      // like SOLIDWORKS: until something is picked the PropertyManager shows its prompt, not an error
      const selFields = (spec.groups || []).flatMap((g) => (g.fields || []).filter((f) => f.type === "selection"));
      const nothingPicked = selFields.length > 0 && selFields.every((f) => !(this.pm.values[f.key] || []).length);
      if (res.ok) { this.vp.setPreview(res); this.pm.setMessage(spec.message); }
      else if (nothingPicked) { this.vp.setPreview(null); this.pm.setMessage(spec.message); }
      else { this.vp.setPreview(null); this.pm.setMessage(res.error, true); }
    }, 220);
  }

  async pmOK(args = this.pm.args()) {
    const spec = this.pm.spec;
    try {
      const cmd = spec.commandFor ? spec.commandFor(this.pm.values) : spec.command;
      if (this.editing) await this.run("redefine_feature", { name: this.editing, command_name: cmd, args }, { quiet: true });
      else await this.run(cmd, args, { quiet: true });
    } catch (err) { this.pm.setMessage(String(err.message || err), true); return; }
    this.closePM();
  }

  pmCancel() { this.closePM(); }
  closePM() {
    clearTimeout(this.previewTimer);
    this.pm.close(); this.editing = null; this.vp.setPreview(null); this.showPane("tree"); this.renderConfirm(); this.setSelection([]);
  }

  async editFeature(name) {
    const f = this.model.features.find((x) => x.name === name);
    if (!f) return;
    if (f.type === "sketch") return this.enterSketch(name);
    const key = FEATURE_TO_SPEC[f.type];
    if (!key) return this.toast(`${f.type} features cannot be edited in the UI yet`, "err");
    const res = await this.api("/api/command", { name: "get_feature", args: { name } });
    if (!res.ok) return;
    const spec = SPECS[key];
    this.openPM(spec, { initial: spec.fromParams(res.result.params), edit: name });
  }

  cancelAll() {
    if (this.pm.active) this.closePM();
    if (this.awaitPlane) this.endPlanePick();
    if (this.sk.active) this.setSketchTool("select");
    this.setSelection([]); this.prompt("");
  }

  // ================================================================== sketch mode
  sketchCommand() {
    const s = this.selection[0];
    if (s && (s.kind === "face" || s.kind === "plane")) return this.createSketch(s.id);
    if (s && s.kind === "feature" && (s.type === "plane" || s.type === "csys")) return this.createSketch(s.id);
    if (s && s.kind === "feature" && s.type === "sketch") return this.enterSketch(s.id);
    this.followUp = null; this.startPlanePick();
  }

  startPlanePick() {
    this.awaitPlane = true; this.vp.showPlanes(["Front", "Top", "Right"]);
    this.vp.setView("trimetric"); this.vp.zoomFit();
    this.prompt("Select a plane on which to create a sketch for the entity (or a planar face).");
    this.renderConfirm();
  }
  endPlanePick() { this.awaitPlane = false; this.vp.showPlanes([]); this.prompt(""); }

  async planePicked(pick) {
    if (!pick) return;
    this.endPlanePick();
    await this.createSketch(pick.id);
  }

  async createSketch(plane) {
    try {
      const res = await this.run("create_sketch", { plane });
      this.newSketch = res.result.sketch;
      await this.enterSketch(res.result.sketch);
      const t = this.pendingTool || "select"; this.pendingTool = null;
      this.setSketchTool(t);
    } catch { /* toast already shown */ }
  }

  async enterSketch(name) {
    if (this.pm.active) this.closePM();
    this.hideFeatureDims();
    if (this.sk.active && this.sk.name !== name) await this.exitSketch(true);
    this.sk.enter(name); this.tab = "Sketch"; this.setSelection([]);
    this.vp.setHover(null); this.vp.showPlanes([]);
    await this.loadSketch();
    this.vp.setMesh(this.mesh);
    const ov = (this.mesh.sketches || []).find((s) => s.name === name);
    if (ov) this.vp.normalTo(ov.z, ov.x);
    $("#sb-editing").textContent = `Editing ${name}`;
    this.renderCommandManager(); this.renderConfirm(); this.renderTitle(); this.renderStatus();
  }

  async exitSketch(silent = false, discard = false) {
    if (!this.sk.active) return;
    const name = this.sk.name;
    const isNew = this.newSketch === name;
    this.sk.exit(); this.newSketch = null; this.prompt(""); this.status.coords(null);
    $("#sb-editing").textContent = "Editing Part"; this.tab = "Features";
    if (discard && isNew) await this.run("delete_feature", { name }).catch(() => {});
    this.vp.setMesh(this.mesh); this.showPane("tree");
    this.renderCommandManager(); this.renderConfirm(); this.renderTitle(); this.renderStatus(); this.setSelection([]);
    const follow = this.followUp; this.followUp = null;
    if (!silent && follow && !discard) {
      this.selection = [{ kind: "feature", id: name, type: "sketch" }];
      this.vp.setView("trimetric"); this.vp.zoomFit(); // SOLIDWORKS turns the view so the preview is visible
      this.feature(follow);
    }
  }

  setSketchTool(t) {
    if (!this.sk.active) {
      if (t === "select") return;
      const s = this.selectedSketch();
      if (s) { this.enterSketch(s).then(() => this.setSketchTool(t)); return; }
      this.pendingTool = t; this.followUp = null; this.startPlanePick();
      return;
    }
    this.sk.setTool(t); this.renderCommandManager();
    if (t !== "select") this.remember(t, () => this.setSketchTool(t));
  }

  sketchSelect(e, pick) {
    const s = this.sk.snap(e.x, e.y);
    let item = null;
    if (s && s.id !== "ORIGIN") item = { kind: "skpoint", id: s.id, sketch: this.sk.name };
    else if (s) item = { kind: "skpoint", id: "ORIGIN", sketch: this.sk.name };
    else if (pick && pick.kind === "sketch" && pick.sketch === this.sk.name) item = { kind: "sketch", id: pick.id, sketch: pick.sketch, type: pick.type };
    if (!item) { this.selDims.clear(); return this.setSelection([]); }
    const same = (x) => x.kind === item.kind && x.id === item.id;
    const list = e.event.ctrlKey || e.event.shiftKey ? (this.selection.some(same) ? this.selection.filter((x) => !same(x)) : [...this.selection, item]) : [item];
    this.setSelection(list);
    if (item.kind === "skpoint") this.vp.setTemp(list.filter((x) => x.kind === "skpoint").map((x) => { const uv = this.sk.pts()[x.id]; return { pts: this.sk.w(uv[0], uv[1]), point: true, color: 0x2f7be0, size: 10 }; }));
  }

  renderSketchPM() {
    if (!this.sk.active || this.pm.active) return;
    const sel = this.selection.filter((s) => s.kind === "sketch" || s.kind === "skpoint");
    const pm = $("#pm");
    if (!sel.length) { this.showPane("tree"); return; }
    pm.innerHTML = "";
    const E = this.sk.detail?.sketch?.entities || {};
    const kinds = sel.map((s) => s.kind === "skpoint" ? "point" : (E[s.id]?.type || s.type));
    const refs = sel.map((s) => s.id);
    pm.append(el("div", "pm-head", `${icon("sketch_item")}<span>Properties</span>`));
    const close = el("div", "pm-actions"); const ok = el("button", "pm-btn", icon("ok")); ok.onclick = () => this.setSelection([]); close.append(ok); pm.append(close);
    const g1 = el("div", "pm-group"); g1.append(el("div", "pm-gh", "Selected Entities"));
    const b1 = el("div", "pm-gb"); sel.forEach((s, i) => b1.append(el("div", "pm-sel-item", `<span>${escapeHtml(s.id)} (${kinds[i]})</span>`))); g1.append(b1); pm.append(g1);
    const rels = applicableRelations(kinds);
    const g2 = el("div", "pm-group"); g2.append(el("div", "pm-gh", "Add Relations"));
    const b2 = el("div", "pm-gb pm-list");
    if (!rels.length) b2.append(el("div", "", "<i>No relations for this selection</i>"));
    for (const r of rels) {
      const b = el("button", "pm-rel", `${icon("relation")}<span>${cap(r)}</span>`);
      b.onclick = async () => {
        const order = r === "symmetric" ? [...sel.filter((s) => s.kind === "skpoint").map((s) => s.id), ...sel.filter((s) => s.kind !== "skpoint").map((s) => s.id)] : refs;
        try { await this.run("sketch_relation", { sketch: this.sk.name, relation: r, refs: order }); this.setSelection([]); } catch {}
      };
      b2.append(b);
    }
    g2.append(b2); pm.append(g2);
    const ents = sel.filter((s) => s.kind === "sketch");
    if (ents.length) {
      const g3 = el("div", "pm-group"); g3.append(el("div", "pm-gh", "Options")); const b3 = el("div", "pm-gb");
      const lab = el("label", "pm-check"); const cb = el("input"); cb.type = "checkbox"; cb.checked = ents.every((s) => E[s.id]?.construction);
      cb.onchange = () => this.run("sketch_set_construction", { sketch: this.sk.name, entities: ents.map((s) => s.id), construction: cb.checked });
      lab.append(cb, document.createTextNode("For construction")); b3.append(lab);
      const del = el("button", "btn", "Delete"); del.onclick = () => this.deleteSelection(); b3.append(del);
      g3.append(b3); pm.append(g3);
    }
    this.showPane("pm");
  }

  selectChain() { // the connected chain of the selected sketch entities
    const E = this.sk.detail.sketch.entities, ends = (e) => [e.p1, e.p2, e.s, e.e].filter(Boolean);
    const chain = new Set(this.selection.filter((s) => s.kind === "sketch").map((s) => s.id));
    let grew = true;
    while (grew) {
      grew = false;
      const pts = new Set([...chain].flatMap((id) => ends(E[id] || {})));
      for (const [id, e] of Object.entries(E)) if (!chain.has(id) && ends(e).some((q) => pts.has(q))) { chain.add(id); grew = true; }
    }
    this.setSelection([...chain].map((id) => ({ kind: "sketch", id, sketch: this.sk.name, type: E[id].type })));
  }

  relationsHint() { if (!this.sk.active) return this.toast("Edit a sketch first, then select sketch entities to add relations."); this.setSketchTool("select"); this.prompt("Select sketch entities; applicable relations appear in the PropertyManager."); }

  async toggleConstruction() {
    if (!this.sk.active) return;
    const ents = this.selection.filter((s) => s.kind === "sketch");
    if (!ents.length) return this.toast("Select sketch entities first");
    const E = this.sk.detail.sketch.entities;
    await this.run("sketch_set_construction", { sketch: this.sk.name, entities: ents.map((s) => s.id), construction: !ents.every((s) => E[s.id]?.construction) });
  }

  deleteRelationsPM() {
    if (!this.sk.active) return this.toast("Edit a sketch first");
    const sk = this.sk.detail.sketch, pm = $("#pm"); pm.innerHTML = "";
    pm.append(el("div", "pm-head", `${icon("delete_rel")}<span>Display/Delete Relations</span>`));
    const acts = el("div", "pm-actions"); const ok = el("button", "pm-btn", icon("ok")); ok.onclick = () => this.showPane("tree"); acts.append(ok); pm.append(acts);
    const g = el("div", "pm-group"); g.append(el("div", "pm-gh", `Relations (${sk.relations.length})`)); const b = el("div", "pm-gb pm-list");
    const failing = new Set(sk.status?.failing || []);
    const dang = new Set(sk.status?.dangling || []), dangPts = new Set([...dang].flatMap((id) => { const e = sk.entities[id] || {}; return [e.p1, e.p2, e.c, e.s, e.e, e.p, ...(e.pts || [])].filter(Boolean); }));
    const isDangling = (r) => r.refs.some((x) => dang.has(x) || dangPts.has(x));
    const mode = this.relFilter || "all";
    const shown = mode === "over" ? sk.relations.filter((r) => failing.has(r.id)) : mode === "dangling" ? sk.relations.filter(isDangling) : sk.relations;
    const filt = el("select", ""); filt.innerHTML = `<option value="all">All in this sketch</option><option value="dangling">Dangling</option><option value="over">Over defining / not solved</option>`;
    filt.value = this.relFilter || "all"; filt.onchange = () => { this.relFilter = filt.value; this.deleteRelationsPM(); };
    const frow = el("div", "pm-row"); frow.append(filt); b.append(frow);
    const pts = this.sk.pts(), E = sk.entities;
    for (const r of shown) {
      const row = el("div", "pm-sel-item" + (failing.has(r.id) ? " err" : "") + (isDangling(r) ? " dangling" : ""), `<span>${cap(r.type)} — ${escapeHtml(r.refs.join(", "))}${isDangling(r) ? " (dangling)" : ""}</span>`); const x = el("span", "x", "&#10005;");
      x.onclick = async () => { await this.run("sketch_delete", { sketch: this.sk.name, item: r.id }); this.deleteRelationsPM(); };
      row.onmouseenter = () => { // show what the relation is on
        this.vp.setSelection(r.refs.filter((id) => E[id]).map((id) => ({ kind: "sketch", id, sketch: this.sk.name })));
        this.vp.setTemp(r.refs.filter((id) => pts[id]).map((id) => ({ pts: this.sk.w(...pts[id]), point: true, color: 0x2f7be0, size: 10 })));
      };
      row.onmouseleave = () => { this.vp.setSelection([]); this.vp.setTemp([]); };
      row.append(x); b.append(row);
    }
    const all = el("button", "btn", "Delete All"); all.style.margin = "6px 0";
    all.onclick = async () => { for (const r of shown) await this.run("sketch_delete", { sketch: this.sk.name, item: r.id }).catch(() => {}); this.deleteRelationsPM(); };
    b.append(all);
    g.append(b); pm.append(g); this.showPane("pm");
  }

  async deleteSelection() {
    if (this.sk.active) {
      for (const id of this.selDims) await this.run("sketch_delete", { sketch: this.sk.name, item: id }).catch(() => {});
      this.selDims.clear();
      for (const s of this.selection.filter((x) => x.kind === "sketch")) await this.run("sketch_delete", { sketch: this.sk.name, item: s.id }).catch(() => {});
      this.setSelection([]); return;
    }
    const f = this.selection.find((s) => s.kind === "feature");
    if (f) this.deleteFeature(f.id);
  }

  suppressSelected(on) { const f = this.selection.find((s) => s.kind === "feature"); if (f) this.run("suppress_feature", { name: f.id, suppressed: on }); }

  // ================================================================== view helpers
  normalTo() {
    if (this.sk.active) { const ov = this.mesh.sketches.find((s) => s.name === this.sk.name); if (ov) return this.vp.normalTo(ov.z, ov.x); }
    const s = this.selection[0];
    if (s?.kind === "face") return this.normalToFace(s);
    if (s?.kind === "plane" || (s?.kind === "feature" && s.type === "plane")) return this.normalToPlane(s.id);
    this.vp.setView("front");
  }
  normalToPlane(name) { const p = (this.mesh.planes || []).find((q) => q.name === name); if (p) this.vp.normalTo(p.z, p.x); }
  normalToFace(pick) {
    const n = pick.normal || [0, 0, 1];
    const { x } = planeBasis(n); this.vp.normalTo(n, x);
  }

  // Instant3D: an arrow handle on the extrude preview; dragging it sets the depth (as in SOLIDWORKS)
  renderHandle() {
    const host = this.overlayHandle; if (!host) return;
    host.innerHTML = "";
    const pm = this.pm, v = pm.values || {};
    if (!pm.active || !["extrude", "cut_extrude"].includes(pm.spec?.command) || !["Blind", "MidPlane"].includes(v.end_condition)) return;
    const ov = (this.mesh?.sketches || []).find((sk) => sk.name === v.sketch?.[0]?.value);
    const prev = this.vp.groups.preview.children.filter((o) => o.isMesh);
    if (!ov || !prev.length) return;
    const pts = ov.entities.flatMap((e) => e.p), n = pts.length / 3; if (!n) return;
    const base = [0, 1, 2].map((i) => pts.filter((_, k) => k % 3 === i).reduce((a, b) => a + b, 0) / n), z = ov.z;
    let tmin = 0, tmax = 0;
    for (const m of prev) { const a = m.geometry.getAttribute("position").array;
      for (let i = 0; i < a.length; i += 3) { const t = (a[i] - base[0]) * z[0] + (a[i + 1] - base[1]) * z[1] + (a[i + 2] - base[2]) * z[2]; tmin = Math.min(tmin, t); tmax = Math.max(tmax, t); } }
    const tEnd = Math.abs(tmax) >= Math.abs(tmin) ? tmax : tmin; if (Math.abs(tEnd) < 1e-6) return;
    const tip = base.map((b, i) => b + z[i] * tEnd), B = this.vp.toScreen(base), T = this.vp.toScreen(tip);
    const L = Math.hypot(T[0] - B[0], T[1] - B[1]); if (L < 4) return;
    const u = [(T[0] - B[0]) / L, (T[1] - B[1]) / L], hx = T[0] + u[0] * 26, hy = T[1] + u[1] * 26;
    host.insertAdjacentHTML("beforeend", `<svg style="position:absolute;inset:0;width:100%;height:100%;overflow:visible;pointer-events:none"><line x1="${T[0]}" y1="${T[1]}" x2="${hx}" y2="${hy}" stroke="#2f7be0" stroke-width="2"/>
      <path d="M${hx + u[0] * 9} ${hy + u[1] * 9} L${hx - u[1] * 6} ${hy + u[0] * 6} L${hx + u[1] * 6} ${hy - u[0] * 6}Z" fill="#2f7be0"/></svg>`);
    const knob = el("div", "i3d-handle"); knob.title = "Drag to set the depth";
    knob.style.left = `${hx}px`; knob.style.top = `${hy}px`;
    knob.onpointerdown = (e) => {
      e.stopPropagation(); e.preventDefault();
      const d0 = +v.depth || 0, x0 = e.clientX, y0 = e.clientY, perMM = L / Math.abs(tEnd), k = v.end_condition === "MidPlane" ? 2 : 1;
      const mv = (m) => {
        const along = ((m.clientX - x0) * u[0] + (m.clientY - y0) * u[1]) / perMM;
        v.depth = Math.max(0.1, Math.round((d0 + k * along) * 10) / 10); pm.render(); pm.changed();
        this.prompt(`Depth: ${UNITS.fmt(v.depth, 2)}`);
      };
      const up = () => { document.removeEventListener("pointermove", mv); document.removeEventListener("pointerup", up); this.prompt(""); };
      document.addEventListener("pointermove", mv); document.addEventListener("pointerup", up);
    };
    host.append(knob);
  }

  renderPlaneLabels() {
    const host = this.overlayLabels; host.innerHTML = "";
    for (const [name, g] of this.vp.planeObjs) {
      if (!g.visible) continue;
      const [x, y] = this.vp.toScreen(g.userData.corner);
      const l = el("div", "plane-label", ["Front", "Top", "Right"].includes(name) ? `${name} Plane` : name); l.style.left = `${x + 4}px`; l.style.top = `${y + 2}px`; host.append(l);
    }
  }

  // ================================================================== status / task pane
  renderStatus() {
    $("#sb-units").textContent = { mm: "MMGS", cm: "CGS", m: "MKS", in: "IPS", ft: "FPS" }[this.model?.units || "mm"] || "MMGS";
    if (!this.sk.active) $("#sb-editing").textContent = this.model?.kind === "assembly" ? "Editing Assembly" : "Editing Part";
    const st = $("#sb-state"); st.className = "";
    if (this.sk.active) {
      const s = this.sk.detail?.sketch?.status?.state || this.model.features.find((f) => f.name === this.sk.name)?.sketch_state;
      st.textContent = (s === "fully_defined" ? "Fully Defined" : s === "over_defined" ? "Over Defined" : "Under Defined") + (this.sk.detail?.sketch?.status?.dangling?.length ? " (Dangling)" : "");
      st.className = s === "fully_defined" ? "full" : s === "over_defined" ? "over" : "under";
    } else st.textContent = Object.keys(this.model.errors || {}).length ? `Rebuild errors: ${Object.keys(this.model.errors).length}` : "";
  }

  buildTaskPane() {
    const keys = { agent: "agent", props: "mass", help: "keyboard" };
    document.querySelectorAll(".tp-tab").forEach((b) => { b.innerHTML = icon(keys[b.dataset.pane]); b.onclick = () => this.showTask(b.dataset.pane); });
    $("#tp-help").innerHTML = `<div class="tp-h">${icon("keyboard")} Shortcuts</div><div class="kv">
      ${[["Rotate", "Middle drag / Alt+Left drag"], ["Pan", "Ctrl+Middle drag"], ["Zoom", "Wheel / Shift+Middle"], ["View orientation", "Space"], ["Front/Back/Left/Right", "Ctrl+1..4"],
        ["Top/Bottom/Iso", "Ctrl+5..7"], ["Normal To", "Ctrl+8"], ["Zoom to fit", "F"], ["Rebuild", "Ctrl+B / Ctrl+Q"], ["Undo / Redo", "Ctrl+Z / Ctrl+Y"], ["Save", "Ctrl+S"],
        ["Smart Dimension", "D (in sketch)"], ["Line / Rectangle / Circle", "L / R / C (in sketch)"], ["Shortcut bar", "S"], ["Repeat last command", "Enter"],
        ["Mouse gestures", "Right-drag"], ["Box select (window / crossing)", "Left-drag right / left"], ["Select Other", "Right-click"],
        ["Selection filter", "F5; X faces, E edges, V vertices"], ["Zoom out / in", "Z / Shift+Z"], ["Previous view", "Ctrl+Shift+Z"],
        ["Hide / show component", "Tab / Shift+Tab (assembly)"], ["Next document / Close", "Ctrl+Tab / Ctrl+W"], ["Cancel", "Esc"], ["Delete", "Del"]].map(([a, b]) => `<div>${a}</div><div>${b}</div>`).join("")}</div>`;
  }

  showTask(p) {
    const tp = $("#taskpane");
    const same = !tp.classList.contains("collapsed") && document.querySelector(`.tp-tab[data-pane="${p}"]`).classList.contains("active");
    tp.classList.toggle("collapsed", same);
    document.querySelectorAll(".tp-tab").forEach((b) => b.classList.toggle("active", !same && b.dataset.pane === p));
    document.querySelector(`.tp-tab[data-pane="${p}"] .dot`)?.remove();
    for (const id of ["agent", "props", "help"]) $(`#tp-${id}`).classList.toggle("hidden", id !== p);
    requestAnimationFrame(() => this.vp.resize()); this.renderTaskPane();
  }

  async renderTaskPane() {
    if ($("#taskpane").classList.contains("collapsed")) return;
    if (!$("#tp-agent").classList.contains("hidden")) {
      const res = await this.api("/api/command", { name: "get_activity", args: { limit: 60 } });
      const log = res.ok ? res.result.log.slice().reverse() : [];
      $("#tp-agent").innerHTML = `<div class="tp-h">${icon("agent")} Agent activity</div>
        <div class="pm-msg">Agents connect over MCP at <b>${location.origin}/mcp</b> (or stdio: <code>python -m openswx mcp</code>). Their actions appear here live.</div>` +
        log.map((a) => `<div class="act ${a.origin === "agent" ? "agent" : ""} ${a.ok ? "" : "fail"}"><div class="cmd">${escapeHtml(a.command)}</div>
          <div class="meta">${a.origin === "agent" ? "Agent (MCP)" : "UI"} · ${new Date(a.t * 1000).toLocaleTimeString()}${a.ok ? "" : " · " + escapeHtml(a.message)}</div>
          <div class="args">${escapeHtml(JSON.stringify(a.args))}</div></div>`).join("");
    }
    if (!$("#tp-props").classList.contains("hidden")) {
      const r = await this.api("/api/command", { name: "get_mass_properties", args: {} });
      $("#tp-props").innerHTML = `<div class="tp-h">${icon("mass")} Mass Properties</div>` + (r.ok ? massHtml(r.result) : "");
    }
  }

  // ------------------------------------------------------------------ simulation (linear static)
  async simFixture() {
    const faces = this.selection.filter((s) => s.kind === "face");
    if (!faces.length) return this.toast("Select the face(s) to fix, then Fixed Geometry.");
    for (const f of faces) await this.run("simulation_add_fixture", { face: f.id });
    this.toast(`Fixed geometry on ${faces.length} face(s). Study: ${this.model.study?.fixtures?.length || 0} fixture(s), ${this.model.study?.loads?.length || 0} load(s).`);
  }

  simLoad(kind) {
    const faces = this.selection.filter((s) => s.kind === "face");
    if (!faces.length) return this.toast(`Select the face(s) to load, then ${kind === "force" ? "Force" : "Pressure"}.`);
    this.modal(kind === "force" ? "Force/Torque" : "Pressure", kind === "force"
      ? `<label class="pm-check"><input id="sln" type="radio" name="sld" checked> Normal to face (pushing in)</label>
         <div class="field"><label>Magnitude (N)</label><input id="slm" type="number" value="100"></div>
         <label class="pm-check"><input id="slv" type="radio" name="sld"> Vector (N, total)</label>
         <div class="field"><label>Fx, Fy, Fz</label><input id="slx" value="0, -100, 0"></div>`
      : `<div class="field"><label>Pressure (MPa = N/mm²)</label><input id="slp" type="number" value="0.1" step="0.01"></div>`, async () => {
      for (const f of faces) {
        if (kind === "pressure") await this.run("simulation_add_pressure", { face: f.id, pressure: parseFloat($("#slp").value) });
        else if ($("#slv").checked) await this.run("simulation_add_force", { face: f.id, force: $("#slx").value.split(",").map(Number) });
        else await this.run("simulation_add_force", { face: f.id, magnitude: parseFloat($("#slm").value) });
      }
    }, kind === "force" ? "sim_force" : "sim_pressure");
  }

  async simRun() {
    this.toast("Meshing and solving...");
    const r = await this.evalCall("simulation_run"); if (!r) return;
    this.simLast = r;
    this.modal("Static Study - Results", `<div class="kv"><div>Mesh</div><div>${r.elements} hex elements, ${r.nodes} nodes (size ${r.element_size.toFixed(3)} mm)</div>
      <div>Max displacement</div><div><b>${r.max_displacement.toExponential(4)} mm</b></div>
      <div>Max von Mises stress</div><div><b>${r.max_von_mises.toFixed(3)} MPa</b></div>
      <div>Yield strength</div><div>${r.yield_strength || "—"} MPa</div>
      <div>Factor of safety</div><div><b style="color:${(r.factor_of_safety ?? 9) < 1 ? "#d0312d" : "#2f9e44"}">${r.factor_of_safety?.toFixed(3) ?? "—"}</b></div>
      <div>Reaction (N)</div><div>${r.reaction.map((v) => v.toFixed(2)).join(", ")}</div></div>`, () => this.simPlot("stress"), "sim_run");
  }

  simConnector(kind) { // two faces of separate bodies/components (Ctrl+click both)
    const faces = this.selection.filter((s) => s.kind === "face").map((s) => s.id);
    if (faces.length !== 2) return this.toast(kind === "bolt" ? "Select the two coaxial hole faces (Ctrl+click)." : "Select the two facing faces (Ctrl+click).");
    if (kind === "contact") return this.run("simulation_add_contact", { face1: faces[0], face2: faces[1] });
    this.modal(kind === "spring" ? "Spring Connector" : "Bolt Connector", kind === "spring"
      ? `<div class="field"><label>Normal stiffness (N/mm)</label><input id="ck" type="number" value="1000"></div>
         <div class="field"><label>Shear stiffness (N/mm)</label><input id="ckt" type="number" value="1000"></div>`
      : `<div class="field"><label>Bolt diameter (mm, blank = hole)</label><input id="cd" type="number"></div>
         <div class="field"><label>Preload (N)</label><input id="cp" type="number" value="0"></div>`, async () => {
      if (kind === "spring") await this.run("simulation_add_spring", { face1: faces[0], face2: faces[1], normal_stiffness: +$("#ck").value, shear_stiffness: +$("#ckt").value });
      else await this.run("simulation_add_bolt", { hole1: faces[0], hole2: faces[1], diameter: $("#cd").value ? +$("#cd").value : null, preload: +$("#cp").value || 0 });
    }, kind === "spring" ? "sim_spring" : "sim_bolt");
  }

  async modeStudy(kind) {
    this.toast(kind === "frequency" ? "Solving frequency study..." : "Solving buckling study...");
    const r = await this.evalCall(kind === "frequency" ? "frequency_run" : "buckling_run", { modes: 5 }); if (!r) return;
    const vals = kind === "frequency" ? r.frequencies_hz : r.load_factors;
    this.modal(kind === "frequency" ? "Frequency Study - Results" : "Buckling Study - Results",
      `<p>${r.elements} elements (size ${r.element_size.toFixed(3)} mm)${kind === "frequency" ? `, mass ${r.mass_kg.toFixed(4)} kg` : ""}</p>
      <div class="kv">${vals.map((v, i) => `<div>Mode ${i + 1}</div><div><a href="#" data-mode="${i + 1}">${kind === "frequency" ? `${v.toFixed(2)} Hz` : `load factor ${v.toFixed(3)}`}</a></div>`).join("")}</div>
      <p style="margin-top:8px">Click a mode to show its shape.</p>`, null, kind === "frequency" ? "sim_frequency" : "sim_buckling");
    document.querySelectorAll("#modal [data-mode]").forEach((a) => { a.onclick = (e) => { e.preventDefault(); this.showModeShape(kind, +a.dataset.mode); }; });
  }

  async showModeShape(kind, mode) {
    const r = await this.evalCall(kind === "frequency" ? "frequency_mode_shape" : "buckling_mode_shape", { mode }); if (!r) return;
    this.vp.setFaceGeometry(r.faces);
    const mags = Object.fromEntries(Object.entries(r.displacement).map(([k, v]) => [k, v.map((d) => Math.hypot(...d))]));
    const rgb = (t) => { t = Math.max(0, Math.min(1, t)); return t < 0.25 ? [0, 4 * t, 1] : t < 0.5 ? [0, 1, 2 - 4 * t] : t < 0.75 ? [4 * t - 2, 1, 0] : [1, 4 - 4 * t, 0]; };
    this.vp.setAnalysis(Object.fromEntries(Object.entries(mags).map(([id, v]) => [id, v.map((x) => rgb(x))])));
    const size = this.vp.bbox.getSize(new this.vp.bbox.max.constructor()).length();
    this.vp.setDeformation(r.displacement, 0.1 * size);
    this.vp.setLegend({ title: kind === "frequency" ? `Mode ${mode}: ${r.frequency_hz.toFixed(2)} Hz` : `Mode ${mode}: factor ${r.load_factor.toFixed(3)}`,
      unit: "relative amplitude", min: 0, max: 1, color: (t) => `rgb(${rgb(t).map((c) => Math.round(c * 255)).join(",")})` });
  }

  thermalBC(kind) {
    const faces = this.selection.filter((s) => s.kind === "face");
    if (!faces.length) return this.toast("Select the face(s) first.");
    const body = { temperature: `<div class="field"><label>Temperature (°C)</label><input id="tv" type="number" value="100"></div>`,
      heat: `<div class="field"><label>Heat power (W, total)</label><input id="tv" type="number" value="10"></div>`,
      convection: `<div class="field"><label>Film coefficient (W/m²·K)</label><input id="tv" type="number" value="25"></div>
        <div class="field"><label>Ambient temperature (°C)</label><input id="ta" type="number" value="20"></div>` }[kind];
    this.modal({ temperature: "Temperature", heat: "Heat Power", convection: "Convection" }[kind], body, async () => {
      const v = parseFloat($("#tv").value);
      for (const f of faces) {
        if (kind === "temperature") await this.run("thermal_add_temperature", { face: f.id, temperature: v });
        else if (kind === "heat") await this.run("thermal_add_heat_power", { face: f.id, watts: v / faces.length });
        else await this.run("thermal_add_convection", { face: f.id, coefficient: v, ambient: parseFloat($("#ta").value) });
      }
    }, { temperature: "thermal_temp", heat: "thermal_heat", convection: "thermal_conv" }[kind]);
  }

  thermalRun() {
    const o = this.thermalOpts || { transient: false, duration: 60, steps: 50, initial_temperature: 20 };
    this.modal("Thermal Study", `<div class="field"><label>Solution type</label><select id="tht">
        <option value="steady"${o.transient ? "" : " selected"}>Steady state</option><option value="transient"${o.transient ? " selected" : ""}>Transient</option></select></div>
      <div id="thtr"${o.transient ? "" : ` class="hidden"`}>
        <div class="field"><label>Total time (s)</label><input id="thd" type="number" value="${o.duration}"></div>
        <div class="field"><label>Time steps</label><input id="ths" type="number" value="${o.steps}"></div>
        <div class="field"><label>Initial temperature (°C)</label><input id="thi" type="number" value="${o.initial_temperature}"></div></div>`, async () => {
      const opts = { transient: $("#tht").value === "transient", duration: parseFloat($("#thd").value), steps: parseInt($("#ths").value, 10),
        initial_temperature: parseFloat($("#thi").value) };
      this.thermalOpts = opts;
      this.toast("Solving thermal study...");
      const r = await this.evalCall("thermal_run", opts.transient ? opts : {}); if (!r) throw new Error("failed");
      setTimeout(() => this.thermalResults(r), 0);
    }, "thermal_run");
    $("#tht").onchange = () => $("#thtr").classList.toggle("hidden", $("#tht").value !== "transient");
  }

  thermalResults(r) {
    const kv = `<div class="kv"><div>Mesh</div><div>${r.elements} elements (size ${r.element_size.toFixed(3)} mm)</div>
      <div>Max temperature</div><div><b>${r.max_temperature.toFixed(3)} °C</b></div><div>Min temperature</div><div><b>${r.min_temperature.toFixed(3)} °C</b></div>
      ${r.history ? `<div>Time step</div><div>${r.time_step.toPrecision(4)} s</div>` : (r.heat_in_per_temperature_boundary || []).map((q, i) => `<div>Heat in, temperature ${i + 1}</div><div>${q.toFixed(4)} W</div>`).join("")}</div>`;
    this.modal("Thermal Study - Results", kv + (r.history ? this.timeChart(r.history) : ""), () => this.thermalPlot(), "thermal_run");
  }

  timeChart(hist) {  // temperature vs time (max / mean / min), as in the SOLIDWORKS time-history graph
    const W = 360, H = 180, L = 44, B = 26, t1 = hist[hist.length - 1].time;
    const lo = Math.min(...hist.map((h) => h.min)), hi = Math.max(...hist.map((h) => h.max)), span = hi - lo || 1;
    const x = (t) => L + (W - L - 8) * t / (t1 || 1), y = (v) => H - B - (H - B - 8) * (v - lo) / span;
    const line = (k, c) => `<polyline fill="none" stroke="${c}" stroke-width="1.6" points="${hist.map((h) => `${x(h.time).toFixed(1)},${y(h[k]).toFixed(1)}`).join(" ")}"/>`;
    const ticks = [0, 0.5, 1].map((f) => `<text x="${L - 4}" y="${y(lo + f * span) + 4}" text-anchor="end">${(lo + f * span).toFixed(1)}</text>` +
      `<text x="${x(f * t1)}" y="${H - B + 14}" text-anchor="middle">${(f * t1).toPrecision(3)}</text>`).join("");
    return `<svg class="time-chart" viewBox="0 0 ${W} ${H}" width="100%" style="font:10px sans-serif;margin-top:8px">
      <rect x="${L}" y="8" width="${W - L - 8}" height="${H - B - 8}" fill="none" stroke="#999"/>${ticks}
      ${line("max", "#d33")}${line("mean", "#2a7")}${line("min", "#36c")}
      <text x="${(W + L) / 2}" y="${H - 2}" text-anchor="middle">time (s)</text>
      <text x="${L + 6}" y="20" fill="#d33">max</text><text x="${L + 34}" y="20" fill="#2a7">mean</text><text x="${L + 70}" y="20" fill="#36c">min</text></svg>`;
  }

  async thermalPlot() {
    const r = await this.evalCall("thermal_results"); if (!r) return;
    this.vp.setDeformation(null);
    this.vp.setFaceGeometry(r.faces);
    const all = Object.values(r.temperature).flat(), lo = Math.min(...all), hi = Math.max(...all);
    const rgb = (t) => { t = Math.max(0, Math.min(1, t)); return t < 0.25 ? [0, 4 * t, 1] : t < 0.5 ? [0, 1, 2 - 4 * t] : t < 0.75 ? [4 * t - 2, 1, 0] : [1, 4 - 4 * t, 0]; };
    this.vp.setAnalysis(Object.fromEntries(Object.entries(r.temperature).map(([id, v]) => [id, v.map((x) => rgb((x - lo) / (hi - lo || 1)))])));
    this.vp.setLegend({ title: "Temperature (°C)", unit: r.history ? `t = ${r.history[r.history.length - 1].time.toPrecision(4)} s` : "steady state", min: lo, max: hi, color: (t) => `rgb(${rgb(t).map((c) => Math.round(c * 255)).join(",")})` });
  }

  async simPlot(kind) {
    if (!kind) { this.vp.setDeformation(null); this.vp.setFaceGeometry(null); this.vp.setAnalysis(null); this.vp.setLegend(null); return; }
    const r = await this.evalCall("simulation_results"); if (!r) return;
    this.vp.setFaceGeometry(r.faces); // results live on the refined (element-size) triangulation
    const vals = kind === "stress" ? r.stress : Object.fromEntries(Object.entries(r.displacement).map(([k, v]) => [k, v.map((d) => Math.hypot(...d))]));
    const all = Object.values(vals).flat(), lo = Math.min(...all), hi = Math.max(...all) || 1;
    const rgb = (t) => { t = Math.max(0, Math.min(1, t)); return t < 0.25 ? [0, 4 * t, 1] : t < 0.5 ? [0, 1, 2 - 4 * t] : t < 0.75 ? [4 * t - 2, 1, 0] : [1, 4 - 4 * t, 0]; };
    this.vp.setAnalysis(Object.fromEntries(Object.entries(vals).map(([id, v]) => [id, v.map((x) => rgb((x - lo) / (hi - lo || 1)))])));
    const size = this.vp.bbox.getSize(new this.vp.bbox.max.constructor()).length();
    this.vp.setDeformation(r.displacement, r.max_displacement > 0 ? 0.08 * size / r.max_displacement : 1);
    this.vp.setLegend({ title: kind === "stress" ? "von Mises (N/mm²)" : "URES (mm)", unit: `Deformation scale ${(r.max_displacement > 0 ? 0.08 * size / r.max_displacement : 1).toFixed(1)}`,
      min: lo, max: hi, color: (t) => `rgb(${rgb(t).map((c) => Math.round(c * 255)).join(",")})` });
  }

  // ------------------------------------------------------------------ evaluate tools
  async evalCall(name, args = {}) { const r = await this.api("/api/command", { name, args }); if (!r.ok) { this.toast(r.error, "err"); return null; } return r.result; }

  sectionDialog() {
    const upd = async () => {
      const r = await this.evalCall("section_view", { plane: $("#svp").value, offset: parseFloat($("#svo").value) || 0, flip: $("#svf").checked });
      if (r) { this.vp.setSection(r); $("#sva").textContent = `Section area: ${r.area.toFixed(3)} mm²`; }
    };
    const bb = this.mesh?.faces?.length ? this.vp.bbox : null, ext = bb ? Math.ceil(bb.getSize(new this.vp.bbox.max.constructor()).length() / 2) : 100;
    this.modal("Section View", `<div class="field"><label>Section plane</label><select id="svp"><option>Front</option><option>Top</option><option>Right</option>
      ${(this.model.features || []).filter((f) => f.type === "plane").map((f) => `<option>${escapeHtml(f.name)}</option>`).join("")}</select></div>
      <div class="field"><label>Offset (mm)</label><input id="svo" type="range" min="${-ext}" max="${ext}" step="0.5" value="0" style="width:100%"></div>
      <label class="pm-check"><input id="svf" type="checkbox"> Flip side</label><p id="sva"></p>`, () => {}, "section");
    const foot = document.querySelector("#modal-box .mb-foot");
    if (foot) { const b = el("button", "", "Remove Section"); b.onclick = () => { this.vp.setSection(null); $("#modal").classList.add("hidden"); }; foot.prepend(b); }
    for (const id of ["#svp", "#svo", "#svf"]) $(id).oninput = upd;
    upd();
  }

  draftDialog() {
    if (this.vp.analysisOn) { this.vp.setAnalysis(null); return; }
    this.modal("Draft Analysis", `<div class="field"><label>Direction of pull</label><select id="dad"><option>Y</option><option>X</option><option>Z</option></select></div>
      <div class="field"><label>Draft angle (°)</label><input id="daa" type="number" value="1" step="0.5"></div>
      <p><span style="color:#2f9e44">■</span> positive &nbsp; <span style="color:#d0312d">■</span> negative &nbsp; <span style="color:#e3c31d">■</span> requires draft &nbsp; <span style="color:#1d6fd6">■</span> straddle</p>`, async () => {
      const r = await this.evalCall("draft_analysis", { direction: $("#dad").value, angle: parseFloat($("#daa").value) || 1 });
      if (!r) return;
      const col = { positive: 0x2f9e44, negative: 0xd0312d, requires_draft: 0xe3c31d, straddle: 0x1d6fd6 };
      this.vp.setAnalysis(Object.fromEntries(Object.entries(r.faces).map(([id, f]) => [id, col[f.class]])));
      this.toast(`Draft: ${r.counts.positive} positive, ${r.counts.negative} negative, ${r.counts.requires_draft} need draft, ${r.counts.straddle} straddle`);
    }, "draft_analysis");
  }

  thicknessDialog() {
    if (this.vp.analysisOn) { this.vp.setAnalysis(null); return; }
    this.modal("Thickness Analysis", `<div class="field"><label>Target thickness (mm)</label><input id="tat" type="number" value="1" step="0.25"></div>
      <p><span style="color:#d0312d">■</span> thinner than the target &nbsp; <span style="color:#2f9e44">■</span> at least the target</p>`, async () => {
      const r = await this.evalCall("thickness_analysis", { target: parseFloat($("#tat").value) || 1 });
      if (!r) return;
      this.vp.setAnalysis(Object.fromEntries(Object.entries(r.faces).map(([id, f]) => [id, f.thin ? 0xd0312d : 0x2f9e44])));
      this.toast(`Thickness: minimum ${r.min_thickness} mm; ${r.thin_faces.length} face(s) thinner than ${r.target} mm`);
    }, "measure");
  }

  undercutDialog() {
    if (this.vp.analysisOn) { this.vp.setAnalysis(null); return; }
    this.modal("Undercut Detection", `<div class="field"><label>Direction of pull</label><select id="uad"><option>Y</option><option>X</option><option>Z</option></select></div>
      <p><span style="color:#2f9e44">■</span> direction 1 &nbsp; <span style="color:#1d6fd6">■</span> direction 2 &nbsp; <span style="color:#e3c31d">■</span> side (free) &nbsp; <span style="color:#d0312d">■</span> undercut</p>`, async () => {
      const r = await this.evalCall("undercut_analysis", { direction: $("#uad").value });
      if (!r) return;
      const col = { direction1: 0x2f9e44, direction2: 0x1d6fd6, side: 0xe3c31d, undercut: 0xd0312d };
      this.vp.setAnalysis(Object.fromEntries(Object.entries(r.faces).map(([id, f]) => [id, col[f.class]])));
      this.toast(`Undercut: ${r.counts.undercut} face(s) blocked for this pull`);
    }, "draft_analysis");
  }

  async clearanceDialog() {
    this.modal("Clearance Verification", `<div class="field"><label>Minimum acceptable clearance (mm)</label><input id="cvm" type="number" value="1" step="0.5"></div>`, async () => {
      const r = await this.api("/api/command", { name: "clearance_verification", args: { min_clearance: parseFloat($("#cvm").value) || 0 } });
      if (!r.ok) return this.toast(r.error, "err");
      const v = r.result.violations;
      setTimeout(() => this.modal("Clearance Verification", v.length ? `<div class="kv">${v.map((p) => `<div>${escapeHtml(p.components.join(" ↔ "))}</div><div>${p.distance.toFixed(3)} mm</div>`).join("")}</div>`
        : `<p>No clearances below ${r.result.min_clearance} mm (closest: ${r.result.closest ? r.result.closest.distance.toFixed(3) + " mm" : "-"}).</p>`, null, "interference"));
    }, "interference");
  }

  toggleZebra() { this.vp.setZebra(!this.vp.zebraOn); }

  async toggleCurvature() {
    if (this.vp.analysisOn) { this.vp.setAnalysis(null); return; }
    const r = await this.evalCall("curvature_analysis"); if (!r) return;
    const kmax = r.max || 1;
    const ramp = (k) => { const t = Math.min(1, Math.log1p(9 * k / kmax) / Math.log(10)); // blue (flat) -> green -> red (tight)
      return t < 0.5 ? [0, 2 * t, 1 - 2 * t] : [2 * t - 1, 2 - 2 * t, 0]; };
    this.vp.setAnalysis(Object.fromEntries(Object.entries(r.faces).map(([id, ks]) => [id, ks.map(ramp)])));
    this.toast(`Curvature: max ${kmax.toFixed(4)} 1/mm (min radius ${(1 / kmax).toFixed(3)} mm)`);
  }

  async checkGeometry() {
    const r = await this.evalCall("check_geometry"); if (!r) return;
    this.modal("Check Entity", `<div class="kv"><div>Result</div><div><b style="color:${r.valid ? "#2f9e44" : "#d0312d"}">${r.valid ? "No invalid geometry found" : "Problems found"}</b></div>
      <div>Solids / faces / edges</div><div>${r.solids} / ${r.faces} / ${r.edges}</div>
      <div>Minimum edge length</div><div>${r.min_edge_length ?? "—"} mm</div><div>Minimum radius of curvature</div><div>${r.min_radius ?? "—"} mm</div></div>
      ${r.issues.length ? `<ul>${r.issues.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>` : ""}`, null, "check");
  }

  async bodyInterference() {
    const r = await this.evalCall("body_interference"); if (!r) return;
    this.modal("Body Interference", r.interferences.length ? `<div class="kv">${r.interferences.map((i) => `<div>Bodies ${i.bodies.join(" & ")}</div><div>${i.volume.toFixed(3)} mm³</div>`).join("")}</div>`
      : `<p>No interference between the ${r.bodies} bodies.</p>`, null, "interference");
  }

  massProps() {
    this.api("/api/command", { name: "get_mass_properties", args: {} }).then((r) => {
      if (r.ok) this.modal(`Mass Properties — ${this.model.name}`, massHtml(r.result, true), null, "mass");
    });
  }

  // Measure, as SOLIDWORKS: a panel that stays open and measures whatever is selected (one or two faces, edges
  // or vertices; Ctrl+click for two); the selection filter's V picks vertices
  measure() {
    if (this.measureOpen) return this.closeMeasure();
    this.measureOpen = true;
    const p = el("div", "measure-panel"); p.id = "measure-panel";
    p.innerHTML = `<div class="mp-head">${icon("measure")}<span>Measure</span><button class="mp-x" title="Close">✕</button></div><div class="mp-body"></div>`;
    p.querySelector(".mp-x").onclick = () => this.closeMeasure();
    this.graphics.append(p); this.updateMeasure();
  }
  closeMeasure() { this.measureOpen = false; $("#measure-panel")?.remove(); }
  async updateMeasure() {
    const body = $("#measure-panel .mp-body"); if (!body) return;
    const sel = this.selection.filter((x) => ["face", "edge", "vertex"].includes(x.kind)).slice(-2);
    if (!sel.length) { body.innerHTML = `<div class="pm-msg">Select one or two faces, edges or vertices (Ctrl+click). Use the selection filter (V) for vertices.</div>`; return; }
    const r = await this.api("/api/command", { name: "measure_items", args: { items: sel.map((x) => x.kind === "vertex" ? x.point : x.id) } });
    if (!r.ok) { body.innerHTML = `<div class="pm-msg err">${escapeHtml(r.error)}</div>`; return; }
    const L = (v) => UNITS.fmt(v, 3), rows = [];
    for (const it of r.result.items) {
      rows.push([`<b>${escapeHtml(it.id)}</b>`, escapeHtml(it.type || it.kind)]);
      if (it.point) rows.push(["X, Y, Z", it.point.map((v) => L(v)).join(", ")]);
      if (it.area != null) rows.push(["Area", `${(it.area / UNITS.f ** 2).toFixed(3)} ${UNITS.name}²`]);
      if (it.length != null) rows.push(["Length", L(it.length)]);
      if (it.diameter != null) rows.push(["Diameter", L(it.diameter)], ["Radius", L(it.radius)]);
    }
    const o = r.result;
    if (o.distance != null) rows.push(["<b>Distance</b>", `<b>${L(o.distance)}</b>`]);
    if (o.normal_distance != null) rows.push(["Normal distance", L(o.normal_distance)]);
    if (o.center_distance != null) rows.push(["Center distance", L(o.center_distance)]);
    if (o.point_distance != null) rows.push(["Point distance", L(o.point_distance)]);
    if (o.dx != null) rows.push(["dX, dY, dZ", [o.dx, o.dy, o.dz].map((v) => L(v)).join(", ")]);
    if (o.angle != null) rows.push(["Angle", `${o.angle.toFixed(3)}°`]);
    body.innerHTML = `<div class="kv">${rows.map(([a, b]) => `<div>${a}</div><div>${b}</div>`).join("")}</div>`;
  }

  async sectionPropsDialog() {
    const planes = ["Front", "Top", "Right", ...(this.model.features || []).filter((f) => f.type === "plane").map((f) => f.name)];
    this.modal("Section Properties", `<div class="field"><label>Plane</label><select id="spp">${planes.map((n) => `<option>${escapeHtml(n)}</option>`).join("")}</select></div>
      <div class="field"><label>Offset (mm)</label><input id="spo" type="number" value="0"></div><div id="spr"></div>`, async () => {
      const r = await this.api("/api/command", { name: "section_properties", args: { plane: $("#spp").value, offset: parseFloat($("#spo").value) || 0 } });
      if (!r.ok) { this.toast(r.error, "err"); throw new Error(r.error); }
      const kv = Object.entries(r.result).map(([k, v]) => `<div>${escapeHtml(cap(k))}</div><div>${Array.isArray(v) ? v.map((x) => (+x).toFixed(4)).join(", ") : typeof v === "number" ? v.toFixed(4) : escapeHtml(JSON.stringify(v))}</div>`).join("");
      setTimeout(() => this.modal("Section Properties", `<div class="kv">${kv}</div>`, null, "section"));
    }, "section");
  }

  async sheetMetalInfo() {
    const r = await this.evalCall("sheet_metal_info"); if (!r) return;
    const kv = Object.entries(r).map(([k, v]) => `<div>${escapeHtml(cap(k))}</div><div>${typeof v === "number" ? (+v).toFixed(4) : escapeHtml(typeof v === "object" ? JSON.stringify(v) : String(v))}</div>`).join("");
    this.modal("Sheet Metal", `<div class="kv">${kv}</div>`, null, "base_flange");
  }

  // ================================================================== files / dialogs
  async newPart() { await this.run("new_part"); this.vp.setView("trimetric"); this.vp.zoomFit(); }

  bindFileInput() {
    const fi = $("#fileinput");
    fi.onchange = async () => {
      const f = fi.files[0]; if (!f) return;
      const buf = new Uint8Array(await f.arrayBuffer()); let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
      const res = await this.api("/api/upload", { filename: f.name, data: btoa(bin), mode: this.fileMode });
      fi.value = "";
      if (!res.ok) return this.toast(res.error, "err");
      if (res.result?.solidworks_file) return this.modal("SOLIDWORKS file", `<p>Read the SOLIDWORKS container (${Object.keys(res.result.solidworks_file.streams).length} streams).</p><pre class="code">${escapeHtml(JSON.stringify(res.result.solidworks_file, null, 1)).slice(0, 4000)}</pre>`, null, "open");
      await this.refresh(); this.vp.zoomFit();
    };
  }
  openFile(mode = "open") {
    if (this.desk && mode === "open") return this.deskCall("open_file").then(() => this.vp.zoomFit());
    this.fileMode = mode; $("#fileinput").click();
  }

  save(as = false) {
    if (this.model.path && !as) return this.run("save_part", {}).then(() => this.toast(`Saved ${this.model.path}`));
    if (this.desk) return this.deskCall("save_as");
    const def = this.model.path || `${this.model.name}.oswprt`;
    this.modal("Save As", `<div class="field"><label>File path on this computer</label><input id="savepath" value="${escapeHtml(def)}"></div><div class="pm-msg">Saved as openswx part (.oswprt). Use File &gt; Export for STEP/STL.</div>`,
      async () => { const r = await this.run("save_part", { path: $("#savepath").value }); this.toast(`Saved ${r.result.saved}`); }, "save");
  }

  // Material dialog as in SOLIDWORKS: the library tree (categories, then the Custom Materials library) on the
  // left, the selected material's properties on the right; library values can be edited for this part, and
  // a material can be saved to Custom Materials for other parts
  async materialDialog() {
    const lib = (await this.run("list_materials", {}, { quiet: true }).catch(() => null))?.result || {};
    const cats = {}; for (const [n, m] of Object.entries(lib)) (cats[m.category] ||= []).push(n);
    const order = Object.keys(cats).sort((a, b) => (a === "Custom Materials") - (b === "Custom Materials") || a.localeCompare(b));
    const curM = this.model.material || {}, cur = curM.name;
    const F = [["density", "Mass density", "kg/m³"], ["elastic_modulus", "Elastic modulus", "N/mm²"], ["poisson", "Poisson's ratio", "N/A"],
      ["yield_strength", "Yield strength", "N/mm²"], ["tensile_strength", "Tensile strength", "N/mm²"], ["thermal_conductivity", "Thermal conductivity", "W/(m·K)"],
      ["specific_heat", "Specific heat", "J/(kg·K)"]];
    let sel = cur && lib[cur] ? cur : null;
    this.modal("Material", `<div class="mat-dlg"><div class="mat-tree" id="mattree"></div><div class="mat-right">
      <div class="field"><label>Name</label><input id="matname"></div><div class="field"><label>Category</label><input id="matcat" disabled></div>
      <table class="mat-props" id="matprops"></table>
      <div class="pm-row"><button class="btn" id="matsave">Save to Custom Materials</button><button class="btn" id="matdel">Delete</button></div>
      <div class="pm-msg" id="matmsg"></div></div></div>`, async () => {
      const props = {}; for (const [k] of F) { const v = parseFloat($(`#mp_${k}`).value); if (Number.isFinite(v)) props[k] = v; }
      const name = $("#matname").value.trim() || "Custom", base = lib[name];
      const edited = base ? Object.fromEntries(Object.entries(props).filter(([k, v]) => +base[k] !== v)) : props;
      await this.run("set_material", { name, density_kg_m3: null, properties: Object.keys(edited).length ? edited : null });
    }, "material");
    const show = (name) => {
      sel = name; const m = name ? lib[name] : curM;
      $("#matname").value = name || cur || "Custom"; $("#matcat").value = m?.category || "(custom, this part only)";
      $("#matprops").innerHTML = `<tr><th>Property</th><th>Value</th><th>Units</th></tr>` + F.map(([k, l, u]) =>
        `<tr><td>${l}</td><td><input id="mp_${k}" value="${m?.[k] ?? (name === cur ? curM[k] ?? "" : "")}"></td><td>${u}</td></tr>`).join("");
      $("#matdel").style.display = m?.category === "Custom Materials" ? "" : "none";
      document.querySelectorAll("#mattree .mt-mat").forEach((r) => r.classList.toggle("selected", r.dataset.n === name));
    };
    const tree = $("#mattree");
    tree.innerHTML = `<div class="mt-root">${icon("material")} openswx Materials</div>` + order.map((c) => `<div class="mt-cat" data-c="${escapeHtml(c)}">▸ ${escapeHtml(c)}</div>
      <div class="mt-list hidden">${cats[c].sort().map((n) => `<div class="mt-mat" data-n="${escapeHtml(n)}">${icon("material")} ${escapeHtml(n)}</div>`).join("")}</div>`).join("");
    tree.querySelectorAll(".mt-cat").forEach((c) => { c.onclick = () => { const l = c.nextElementSibling; l.classList.toggle("hidden"); c.textContent = `${l.classList.contains("hidden") ? "▸" : "▾"} ${c.dataset.c}`; }; });
    tree.querySelectorAll(".mt-mat").forEach((r) => { r.onclick = () => show(r.dataset.n); r.ondblclick = () => $("#modal-box .mb-foot .primary").click(); });
    if (sel) { const r = tree.querySelector(`.mt-mat[data-n="${CSS.escape(sel)}"]`); r?.parentElement.classList.remove("hidden"); r?.parentElement.previousElementSibling && (r.parentElement.previousElementSibling.textContent = `▾ ${lib[sel].category}`); }
    show(sel);
    $("#matsave").onclick = async () => {
      const name = $("#matname").value.trim(), props = { color: lib[sel]?.color };
      for (const [k] of F) { const v = parseFloat($(`#mp_${k}`).value); if (Number.isFinite(v)) props[k] = v; }
      const r = await this.api("/api/command", { name: "save_custom_material", args: { name, properties: props } });
      $("#matmsg").textContent = r.ok ? `Saved "${name}" to Custom Materials.` : r.error; $("#matmsg").classList.toggle("err", !r.ok);
      if (r.ok) { $("#modal").classList.add("hidden"); this.materialDialog(); }
    };
    $("#matdel").onclick = async () => { await this.run("delete_custom_material", { name: sel }); $("#modal").classList.add("hidden"); this.materialDialog(); };
  }

  appearanceDialog() { // Edit Appearance: part, selected feature or selected face
    const face = this.selection.find((s) => s.kind === "face"), feat = this.selection.find((s) => s.kind === "feature" && !["sketch", "plane"].includes(s.type));
    const target = face ? "face" : feat ? "feature" : "part";
    const ap = this.model.appearance || {}, cur = (target === "feature" ? ap.features?.[feat.id] : target === "part" ? ap.part : null) || this.model.material?.color || "#c4cad3";
    const swatches = ["#c4cad3", "#8e9399", "#b4b8bd", "#c9a94a", "#c17a4a", "#2b2b2b", "#f4f4f0", "#d0312d", "#e8792f", "#e3c31d", "#2f9e44", "#1d6fd6", "#6a3fb5", "#e05aa0"];
    this.modal("Appearance", `<p>Applies to: <b>${target === "face" ? `Face ${escapeHtml(face.id)}` : target === "feature" ? escapeHtml(feat.id) : escapeHtml(this.model.name)}</b></p>
      <div class="field"><label>Colour</label><input id="apcol" type="color" value="${cur}"></div>
      <div style="display:flex;flex-wrap:wrap;gap:4px;margin:6px 0">${swatches.map((c) => `<span class="swatch" data-c="${c}" style="width:20px;height:20px;border:1px solid #888;background:${c};cursor:pointer"></span>`).join("")}</div>
      <label class="pm-check"><input id="apclr" type="checkbox"> Remove appearance</label>`, async () => {
      await this.run("set_appearance", { color: $("#apclr").checked ? null : $("#apcol").value, target, feature: feat?.id ?? null, face: face?.id ?? null });
    }, "appearance");
    document.querySelectorAll(".swatch").forEach((sw) => { sw.onclick = () => { $("#apcol").value = sw.dataset.c; }; });
  }

  propertiesDialog() { // File > Properties: custom properties
    const cur = this.model.properties || {};
    const props = { Description: "", PartNo: "", Author: "", Revision: "", ...cur };
    this.modal("Summary Information - Custom Properties", `<table class="prop-tab" style="width:100%">${Object.entries(props).map(([k, v]) =>
      `<tr><td><input class="pk" value="${escapeHtml(k)}"></td><td><input class="pv" value="${escapeHtml(v)}" style="width:100%"></td></tr>`).join("")}
      <tr><td><input class="pk" placeholder="New property"></td><td><input class="pv" style="width:100%"></td></tr></table>`, async () => {
      const keys = [...document.querySelectorAll(".pk")], vals = [...document.querySelectorAll(".pv")];
      const gone = new Set(Object.keys(cur));
      for (let i = 0; i < keys.length; i++) {
        const k = keys[i].value.trim(), v = vals[i].value;
        if (!k) continue;
        gone.delete(k);
        if ((cur[k] ?? "") !== v) await this.run("set_custom_property", { name: k, value: v || null });
      }
      for (const k of gone) await this.run("set_custom_property", { name: k, value: null });
    }, "properties");
  }

  optionsDialog() {
    const u = this.model?.units || "mm";
    this.modal("System Options", `<div class="field"><label>Document units</label><select id="optunits">${[["mm", "MMGS (millimetre, gram, second)"], ["cm", "CGS (centimetre, gram, second)"], ["m", "MKS (metre, kilogram, second)"], ["in", "IPS (inch, pound, second)"], ["ft", "FPS (foot, pound, second)"]]
      .map(([k, l]) => `<option value="${k}" ${k === u ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="field"><label>Polygon sides (sketch tool)</label><input id="polys" type="number" min="3" value="${this.polygonSides}"></div>
      <label class="pm-check"><input type="checkbox" id="optrel" ${this.showRelations ? "checked" : ""}> Display sketch relations</label>`, () => {
      this.polygonSides = Math.max(3, parseInt($("#polys").value) || 6); this.showRelations = $("#optrel").checked; this.vp.dirty = true;
      if ($("#optunits").value !== u) this.run("set_units", { units: $("#optunits").value });
    }, "options");
  }

  mcpDialog() {
    const cfg = { mcpServers: { openswx: { type: "http", url: `${location.origin}/mcp` } } };
    const stdio = { mcpServers: { openswx: { command: "python", args: ["-m", "openswx", "mcp"] } } };
    this.modal("Connect an agent (MCP)", `<p>Streamable HTTP (this running app):</p><pre class="code">${escapeHtml(JSON.stringify(cfg, null, 1))}</pre>
      <p>Claude Code:</p><pre class="code">claude mcp add --transport http openswx ${location.origin}/mcp</pre>
      <p>Stdio (starts its own app + UI):</p><pre class="code">${escapeHtml(JSON.stringify(stdio, null, 1))}</pre>`, null, "agent");
  }

  about() { this.modal("About openswx", `<p><b>openswx</b> 0.1 — open-source parametric CAD with a SOLIDWORKS-style workflow and a built-in MCP server for agents.</p><p>Geometry: OpenCASCADE (via build123d/OCP). Not affiliated with Dassault Systèmes.</p>`, null, "help"); }

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
  }

  toast(msg, kind = "") {
    const t = el("div", `toast ${kind}`, kind === "err" ? escapeHtml(msg) : msg); $("#toasts").append(t);
    setTimeout(() => t.remove(), kind === "err" ? 6000 : 3500);
  }

  // ================================================================== keyboard / search
  bindKeys() {
    document.addEventListener("keydown", (e) => {
      if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
      if (!$("#modal").classList.contains("hidden")) return;
      const k = e.key.toLowerCase(), C = e.ctrlKey || e.metaKey;
      const views = { 1: "front", 2: "back", 3: "left", 4: "right", 5: "top", 6: "bottom", 7: "isometric" };
      if (C && views[k]) { e.preventDefault(); return this.vp.setView(views[k]); }
      if (C && k === "8") { e.preventDefault(); return this.normalTo(); }
      if (C && k === "z") { e.preventDefault(); return this.run("undo").catch(() => {}); }
      if (C && k === "y") { e.preventDefault(); return this.run("redo").catch(() => {}); }
      if (C && (k === "b" || k === "q")) { e.preventDefault(); return this.run("rebuild"); }
      if (C && k === "s") { e.preventDefault(); return this.save(); }
      if (C && k === "n") { e.preventDefault(); return this.newPart(); }
      if (C && k === "o") { e.preventDefault(); return this.openFile(); }
      if (C && e.shiftKey && k === "z") { e.preventDefault(); return this.vp.previousView(); }
      if (C && k === "w") { e.preventDefault(); return this.run("close_document").then(() => this.vp.zoomFit()).catch(() => {}); }
      if (C && e.key === "Tab") { e.preventDefault(); return this.nextDocument(); }
      if (e.key === "Escape") {
        $("#sbar")?.remove(); this.hideFeatureDims();
        if (this.placing) return this.cancelPlacing();
        if (this.zoomArea) { this.zoomArea = false; this.prompt(""); return; }
        if (this.sk.active && this.sk.key(e)) return; return this.cancelAll();
      }
      if (e.key === "Enter" && this.sk.active && !this.pm.active && this.sk.key(e)) return;
      if (e.key === "Delete") return this.dwg.isOpen() ? undefined : this.deleteSelection(); // (the drawing editor deletes its own selection)
      if (e.key === "Enter" && this.pm.active) return this.pmOK();
      if (e.key === "Enter" && this.lastCommand) return this.lastCommand.fn(); // SOLIDWORKS: Enter repeats the last command
      if (e.key === "F5") { e.preventDefault(); return this.huFilter?.click(); }
      if (e.key === "Tab" && this.model.kind === "assembly") { // Tab hides the component under the cursor, Shift+Tab shows all
        e.preventDefault();
        if (e.shiftKey) return this.showAllComponents();
        const pk = this.vp.pick(this.lastMouse.x, this.lastMouse.y, { kinds: ["face"] });
        if (pk) this.toggleComponents([pk.id.split("/")[0]]);
        return;
      }
      if (k === "s" && !C) return this.shortcutBar();
      if (k === "z" && !C) { const f = e.shiftKey ? 1 / 1.25 : 1.25; return this.vp.zoomAt(f, this.vp.w / 2, this.vp.h / 2); } // Z out, Shift+Z in
      if (!this.sk.active && !C && ["x", "e", "v"].includes(k)) return this.toggleFilter({ x: "face", e: "edge", v: "vertex" }[k]);
      if (k === "f" && !C) return this.vp.zoomFit();
      if (e.key === " ") { e.preventDefault(); return this.orientationMenu(); }
      if (e.key === "F2") { const f = this.selection.find((s) => s.kind === "feature"); if (f) this.renameFeature(f.id); return; }
      if (e.key.startsWith("Arrow")) {
        const a = 15 * Math.PI / 180, d = { ArrowLeft: [-a, 0], ArrowRight: [a, 0], ArrowUp: [0, -a], ArrowDown: [0, a] }[e.key];
        if (C) this.vp.panBy(-d[0] * 300, -d[1] * 300); else this.vp.rotateBy(...d);
        return;
      }
      if (this.sk.active && !C) {
        const map = { l: "line", r: "rectangle", c: "circle", a: "arc", d: "smart_dim", p: "point" };
        if (map[k]) this.setSketchTool(map[k]);
      }
    });
  }

  orientationMenu() {
    const m = $("#orient-menu"); m.innerHTML = "";
    const views = [["v_front", "Front", "front"], ["v_back", "Back", "back"], ["v_left", "Left", "left"], ["v_right", "Right", "right"], ["v_top", "Top", "top"], ["v_bottom", "Bottom", "bottom"], ["v_iso", "Isometric", "isometric"], ["v_tri", "Trimetric", "trimetric"]];
    for (const [ic, label, v] of views) { const b = el("button", "om", `${icon(ic)}<span>${label}</span>`); b.onclick = () => { m.classList.add("hidden"); this.vp.setView(v); }; m.append(b); }
    const n = el("button", "om", `${icon("normal_to")}<span>Normal To</span>`); n.onclick = () => { m.classList.add("hidden"); this.normalTo(); }; m.append(n);
    for (const [name, v] of Object.entries(this.model?.named_views || {})) { // saved views (right-click to delete)
      const b = el("button", "om", `${icon("view_orient")}<span>${escapeHtml(name)}</span>`); b.title = "Saved view (right-click to delete)";
      b.onclick = () => { m.classList.add("hidden"); this.vp.applyView(v); };
      b.oncontextmenu = (e) => { e.preventDefault(); m.classList.add("hidden"); this.run("delete_named_view", { name }); };
      m.append(b);
    }
    const nv = el("button", "om", `${icon("new")}<span>New View...</span>`); nv.onclick = () => { m.classList.add("hidden"); this.newNamedView(); }; m.append(nv);
    const pv = el("button", "om", `${icon("perspective")}<span>${this.vp.perspective ? "Orthographic" : "Perspective"}</span>`); pv.onclick = () => { m.classList.add("hidden"); this.togglePerspective(); }; m.append(pv);
    m.style.left = `${this.vp.w / 2 - 140}px`; m.style.top = `${this.vp.h / 2 - 90}px`; m.classList.remove("hidden");
    const off = (e) => { if (!m.contains(e.target)) { m.classList.add("hidden"); document.removeEventListener("pointerdown", off); } }; setTimeout(() => document.addEventListener("pointerdown", off));
  }

  togglePerspective() { this.vp.setPerspective(!this.vp.perspective); this.toast(this.vp.perspective ? "Perspective view" : "Orthographic view"); }
  newNamedView() {
    this.modal("New View", `<div class="field"><label>View name</label><input id="nvname" value="View${Object.keys(this.model?.named_views || {}).length + 1}"></div>`,
      () => this.run("save_named_view", { name: $("#nvname").value.trim(), ...this.vp.savedViewData() }), "view_orient");
  }

  bindSearch() {
    const cmds = [["Extruded Boss/Base", "extrude", () => this.feature("extrude")], ["Revolved Boss/Base", "revolve", () => this.feature("revolve")], ["Extruded Cut", "cut_extrude", () => this.feature("cut_extrude")],
      ["Revolved Cut", "cut_revolve", () => this.feature("cut_revolve")], ["Fillet", "fillet", () => this.feature("fillet")], ["Chamfer", "chamfer", () => this.feature("chamfer")], ["Hole Wizard", "hole", () => this.feature("hole")],
      ["Shell", "shell", () => this.feature("shell")], ["Linear Pattern", "linear_pattern", () => this.feature("linear_pattern")], ["Circular Pattern", "circular_pattern", () => this.feature("circular_pattern")],
      ["Mirror", "mirror", () => this.feature("mirror")], ["Sketch Driven Pattern", "sketch_pattern", () => this.feature("sketch_pattern")], ["Curve Driven Pattern", "curve_pattern", () => this.feature("curve_pattern")], ["Table Driven Pattern", "table_pattern", () => this.feature("table_pattern")], ["Fill Pattern", "fill_pattern", () => this.feature("fill_pattern")], ["Plane", "plane", () => this.feature("plane")], ["Axis", "ref_axis", () => this.feature("ref_axis")], ["Coordinate System", "csys", () => this.feature("csys")], ["Point", "ref_point", () => this.feature("ref_point")], ["Sketch", "sketch", () => this.sketchCommand()], ["Smart Dimension", "smart_dim", () => this.setSketchTool("smart_dim")],
      ["Line", "line", () => this.setSketchTool("line")], ["Circle", "circle", () => this.setSketchTool("circle")], ["Corner Rectangle", "rectangle", () => this.setSketchTool("rectangle")],
      ["Mass Properties", "mass", () => this.massProps()], ["Measure", "measure", () => this.measure()], ["Material", "material", () => this.materialDialog()], ["Agent (MCP) Setup", "agent", () => this.mcpDialog()]];
    const inp = $("#cmdsearch"), res = $("#searchresults");
    inp.oninput = () => {
      const q = inp.value.trim().toLowerCase(); res.innerHTML = "";
      if (!q) return res.classList.remove("open");
      for (const [label, ic, fn] of cmds.filter((c) => c[0].toLowerCase().includes(q))) {
        const r = el("div", "sr-item", `${icon(ic)}<span>${label}</span>`); r.onclick = () => { res.classList.remove("open"); inp.value = ""; fn(); }; res.append(r);
      }
      res.classList.toggle("open", !!res.children.length);
    };
    inp.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") res.firstChild?.click(); if (e.key === "Escape") { inp.value = ""; res.classList.remove("open"); inp.blur(); } };
  }
}

// ---------------------------------------------------------------------- helpers
function cap(s) { return s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, " ") : s; }
function segHitsRect(a, b, x0, y0, x1, y1) { // does the screen segment a-b cross the rectangle's edges?
  const cr = (p, q, r, t) => { const d = (q[0] - p[0]) * (t[1] - r[1]) - (q[1] - p[1]) * (t[0] - r[0]); if (!d) return false;
    const u = ((r[0] - p[0]) * (t[1] - r[1]) - (r[1] - p[1]) * (t[0] - r[0])) / d, v = ((r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])) / d;
    return u >= 0 && u <= 1 && v >= 0 && v <= 1; };
  const c = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  return c.some((p, i) => cr(a, b, p, c[(i + 1) % 4]));
}

function applicableRelations(kinds) {
  const c = (t) => kinds.filter((k) => k === t).length, n = kinds.length;
  const curves = c("circle") + c("arc");
  const out = [];
  if (n === 1 && c("line")) out.push("horizontal", "vertical");
  if (n === 2 && c("line") === 2) out.push("parallel", "perpendicular", "equal", "collinear");
  if (n === 2 && c("point") === 2) out.push("coincident", "horizontal", "vertical");
  if (n === 2 && c("point") === 1 && c("line") === 1) out.push("coincident", "midpoint");
  if (n === 2 && c("point") === 1 && curves === 1) out.push("coincident", "concentric");
  if (n === 2 && c("line") === 1 && curves === 1) out.push("tangent");
  if (n === 2 && curves === 2) out.push("equal", "concentric", "tangent");
  if (n === 3 && c("point") === 2 && c("line") === 1) out.push("symmetric");
  if (n >= 1) out.push("fix");
  return out;
}

function planeBasis(n) { // same convention as the server (SOLIDWORKS-like sketch axes)
  const N = norm(n);
  let y;
  if (Math.abs(N[1]) > 0.999) y = N[1] > 0 ? [0, 0, -1] : [0, 0, 1];
  else { const d = N[1]; y = norm([-N[0] * d, 1 - N[1] * d, -N[2] * d]); }
  const x = norm([y[1] * N[2] - y[2] * N[1], y[2] * N[0] - y[0] * N[2], y[0] * N[1] - y[1] * N[0]]);
  return { x, y, n: N };
}
function facePlaneUV(n, p, refNormal) {
  const { x, y, n: N } = planeBasis(refNormal || n);
  const d = p[0] * N[0] + p[1] * N[1] + p[2] * N[2];
  const O = [N[0] * d, N[1] * d, N[2] * d], v = [p[0] - O[0], p[1] - O[1], p[2] - O[2]];
  return [v[0] * x[0] + v[1] * x[1] + v[2] * x[2], v[0] * y[0] + v[1] * y[1] + v[2] * y[2]];
}
function norm(v) { const l = Math.hypot(...v) || 1; return v.map((x) => x / l); }

function massHtml(mp, full = false) {
  const f = (x, d = 3) => (+x).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  if (!mp.bodies) return "<p>No solid bodies.</p>";
  const u = UNITS.name, L = (x) => x / UNITS.f, M = (g) => g / UNITS.mf; // display in document units
  const c = mp.center_of_mass, I = mp.inertia_at_com;
  return `<div class="kv">
    <div>Density</div><div>${f(M(mp.density / 1e6) * UNITS.f ** 3, 5)} ${UNITS.mass}/${u}³</div>
    <div>Mass</div><div><b>${f(M(mp.mass), 4)} ${UNITS.mass}</b></div>
    <div>Volume</div><div>${f(mp.volume / UNITS.f ** 3, 4)} ${u}³</div>
    <div>Surface area</div><div>${f(mp.surface_area / UNITS.f ** 2, 4)} ${u}²</div>
    <div>Center of mass X</div><div>${f(L(c[0]))} ${u}</div><div>Center of mass Y</div><div>${f(L(c[1]))} ${u}</div><div>Center of mass Z</div><div>${f(L(c[2]))} ${u}</div>
    <div>Bodies / faces / edges</div><div>${mp.bodies} / ${mp.faces} / ${mp.edges}</div></div>` +
    (full ? `<p style="margin-top:10px"><b>Moments of inertia</b> (${UNITS.mass}·${u}², at the centre of mass, output coordinate system)</p><div class="kv">
      ${["X", "Y", "Z"].map((a, i) => `<div>L${a}x / L${a}y / L${a}z</div><div>${[0, 1, 2].map((j) => f(M(I[i][j]) / UNITS.f ** 2, 4)).join(" / ")}</div>`).join("")}</div>
      <p style="margin-top:8px"><b>Bounding box</b> (${u}): ${mp.bounding_box.min.map((x) => f(L(x), 3)).join(", ")} → ${mp.bounding_box.max.map((x) => f(L(x), 3)).join(", ")}</p>` : "");
}

window.app = new App();
