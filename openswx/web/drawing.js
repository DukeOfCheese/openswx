// Drawing sheet editor: shows the live drawing SVG, drag views / annotations, add notes, datums and GD&T,
// sheet settings and PDF / DXF / SVG export. All edits go through drawing_* commands (shared with the MCP).
// A drawing with sheets (drawing_new) adds the View Layout tools: Model View, Projected, Section, Detail and
// Auxiliary views placed by clicking on the sheet, sheet tabs, view properties, the revision table and Smart
// Dimension (picks snap to the view's vertices and circles on the server: drawing_pick).
import { icon } from "./icons.js";
import { el, escapeHtml } from "./propman.js";

const GDT = [["position", "⌖ Position"], ["flatness", "⏥ Flatness"], ["perpendicularity", "⟂ Perpendicularity"], ["parallelism", "∥ Parallelism"],
  ["straightness", "⏤ Straightness"], ["circularity", "○ Circularity"], ["cylindricity", "⌭ Cylindricity"], ["angularity", "∠ Angularity"],
  ["profile_line", "⌒ Profile of a line"], ["profile_surface", "⌓ Profile of a surface"], ["concentricity", "◎ Concentricity"],
  ["symmetry", "⌯ Symmetry"], ["circular_runout", "↗ Circular runout"], ["total_runout", "⌰ Total runout"]];
const ORIENT = [["front", "Front"], ["back", "Back"], ["top", "Top"], ["bottom", "Bottom"], ["left", "Left"], ["right", "Right"],
  ["isometric", "Isometric"], ["dimetric", "Dimetric"], ["trimetric", "Trimetric"]];
const SHEET_SIZES = ["A4", "A3", "A2", "A1", "A0", "A4 portrait", "ANSI A", "ANSI B", "ANSI C", "ANSI D", "ANSI E"];
const HINT = "Drag views or annotations to move them; right-click a view or annotation for its options.";
const TOOL_HINT = {
  note: "Click where the note goes (Shift+click a second point first to add a leader).", datum: "Click where the datum symbol goes.",
  gdt: "Click where the feature control frame goes.", model: "Click where the model view goes.",
  finish: "Click where the surface finish symbol's tip goes (Shift+click a leader point first).",
  weld: "Shift+click the joint (arrow point), then click where the reference line starts.",
  stacked: "Click a part in a view, then where the balloon stack goes.", altpos: "Click an assembly view.",
  projected: "Click a view, then click beside it (right, left, above or below) where the projected view goes.",
  section: "Click the start of the cutting line on a view, then its end.",
  detail: "Click the centre of the detail circle on a view, then a point on the circle.",
  auxiliary: "Click two points along an inclined edge in a view.",
  crop: "Click two opposite corners of the rectangle to keep, on a view.",
  break: "Click where the first break line goes on a view, then the second (side by side: vertical break lines).",
  broken: "Click the centre of the region to cut away on a view, then a point on its edge.",
  holes: "Click the corner of a view to measure the holes from (the table goes top left; drag it anywhere).",
  dim: "Click two corners or two edges (angle / distance) in a view, or a circle (Shift: radius); then click where it goes.",
  callout: "Click a hole (seen end-on) in a view, then where the callout goes.",
  dimvalue: "Click the view to dimension.",
  balloon: "Click a part in an assembly view, then where the balloon goes.",
  autoballoon: "Click a view to balloon every part in it.",
};

export class DrawingEditor {
  constructor(app) { this.app = app; this.pane = null; this.tool = null; this.drag = null; this.pick = null; }

  async open() {
    if (!this.pane) this.build();
    this.pane.classList.remove("hidden");
    await this.load();
  }

  close() { this.pane?.classList.add("hidden"); this.setTool(null); }

  build() {
    const p = el("div", "dw-pane hidden"); p.id = "drawing-pane";
    const bar = el("div", "dw-bar");
    const B = (ic, label, fn, title = "", parent = bar) => { const b = el("button", "dw-btn", `${icon(ic)}<span>${label}</span>`); b.title = title || label; b.onclick = fn; parent.append(b); return b; };
    this.newBtn = B("drawing", "New Drawing", () => this.newDrawing(), "Start a drawing with sheets and views you place (Front, Top, Right and Isometric to begin)");
    // View Layout (only for drawings with sheets)
    this.layout = el("span", "dw-group");
    B("v_front", "Model View", () => this.modelView(), "Place a view of the model from a standard orientation", this.layout);
    B("v_right", "Projected View", () => this.setTool("projected"), "Project a view from an existing view", this.layout);
    B("section", "Section View", () => this.setTool("section"), "Cut a view along a line", this.layout);
    B("zoom_sel", "Detail View", () => this.setTool("detail"), "Magnify a circular region of a view", this.layout);
    B("normal_to", "Auxiliary View", () => this.setTool("auxiliary"), "Look square to an inclined edge", this.layout);
    B("trim", "Crop View", () => this.setTool("crop"), "Keep only a rectangle of a view", this.layout);
    B("jog", "Break View", () => this.setTool("break"), "Shorten a long view between two break lines", this.layout);
    B("cut_extrude", "Broken-out Section", () => this.setTool("broken"), "Cut away part of a view to a depth to show the inside", this.layout);
    B("smart_dim", "Smart Dimension", () => this.setTool("dim"), "Dimension between corners, or a circle's diameter / radius, in a view", this.layout);
    B("smart_dim", "Dimension by Value", () => this.setTool("dimvalue"), "Type a value (100, ⌀16, R5): what in a view measures it gets the dimension", this.layout);
    B("hole", "Hole Table", () => this.setTool("holes"), "Tabulate the holes seen end-on in a view", this.layout);
    B("hole", "Hole Callout", () => this.setTool("callout"), "Label a hole with its size and depth", this.layout);
    B("note", "Balloon", () => this.setTool("balloon"), "Label a part with its BOM item number", this.layout);
    B("note", "Auto Balloon", () => this.setTool("autoballoon"), "Balloon every part in a view", this.layout);
    B("properties", "Revision", () => this.revisionDialog(), "Add a row to the revision table", this.layout);
    B("edit", "Title Block", () => this.titleDialog(), "Fill in the sheet format's title block", this.layout);
    bar.append(this.layout, el("span", "dw-sep"));
    B("note", "Note", () => this.setTool("note"), "Click the sheet to place a note");
    B("datum", "Datum Feature", () => this.setTool("datum"), "Click to place a datum symbol");
    B("gdt", "Geometric Tolerance", () => this.setTool("gdt"), "Click to place a feature control frame");
    B("finish", "Surface Finish", () => this.setTool("finish"), "Click to place a surface finish symbol (Shift+click first for a leader)");
    B("weldment", "Weld Symbol", () => this.setTool("weld"), "Shift+click the joint, then click where the reference line goes");
    B("note", "Stacked Balloon", () => this.setTool("stacked"), "Click a part in a view, then where the balloon stack goes", this.layout);
    B("explode", "Alt. Position View", () => this.setTool("altpos"), "Click an assembly view to show an alternate position over it in phantom lines", this.layout);
    B("properties", "Layers", () => this.layersDialog(), "Drawing layers: colour and visibility of annotations");
    B("edit", "Sheet Format", () => this.sheetFormatDialog(), "Use, reset or save the sheet format (title block)", this.layout);
    this.miBtn = B("smart_dim", "Model Items", () => this.toggleModelItems(), "Show the driving sketch dimensions");
    bar.append(el("span", "dw-sep"));
    this.sheetSel = el("select", "dw-sel"); for (const s of SHEET_SIZES) this.sheetSel.append(new Option(`Sheet ${s}`, s));
    this.sheetSel.onchange = () => this.sheetProp({ size: this.sheetSel.value }, { sheet: this.sheetSel.value });
    this.projSel = el("select", "dw-sel"); this.projSel.append(new Option("Third angle", "third"), new Option("First angle", "first"));
    this.projSel.onchange = () => this.sheetProp({ projection: this.projSel.value }, { projection: this.projSel.value });
    this.scaleSel = el("select", "dw-sel");
    for (const [v, l] of [[0, "Scale: auto"], [5, "5:1"], [2, "2:1"], [1, "1:1"], [0.5, "1:2"], [0.2, "1:5"], [0.1, "1:10"], [0.05, "1:20"]]) this.scaleSel.append(new Option(l, v));
    this.scaleSel.onchange = () => { const v = parseFloat(this.scaleSel.value); this.sheetProp(v ? { scale: v } : null, { scale: v }); };
    bar.append(this.sheetSel, this.projSel, this.scaleSel, el("span", "dw-sep"));
    B("save", "Save Drawing", () => this.saveDocument(), "Save the drawing as its own document (.oswdrw) next to its part / assembly");
    for (const f of ["pdf", "dxf", "dwg", "svg"]) B("export", f.toUpperCase(), () => this.export(f), `Export the drawing as ${f.toUpperCase()} (PDF: every sheet; DWG needs the free ODA File Converter)`);
    bar.append(el("span", "dw-grow"));
    this.hint = el("span", "dw-hint", HINT);
    bar.append(this.hint);
    B("exit_sketch", "Close", () => this.close());
    const host = el("div", "dw-host"); this.host = host;
    this.tabs = el("div", "dw-tabs");
    p.append(bar, host, this.tabs); document.body.append(p); this.pane = p;
    host.addEventListener("pointerdown", (e) => this.down(e));
    host.addEventListener("pointermove", (e) => this.move(e));
    host.addEventListener("pointerup", (e) => this.up(e));
    host.addEventListener("contextmenu", (e) => this.context(e));
    document.addEventListener("keydown", (e) => {
      if (!this.isOpen() || ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
      if (e.key === "Escape") { if (this.tool) this.setTool(null); else if (this.sel) this.select(null); else this.close(); }
      if (e.key === "Delete" && this.sel) this.deleteSelected(); // as in SOLIDWORKS: select, then Delete
    });
  }

  get sheeted() { return !!(this.state && this.state.sheets && this.state.sheets.length); }
  get sheet() { return this.sheeted ? this.state.sheets[Math.min(this.state.active || 0, this.state.sheets.length - 1)] : this.state; }

  setTool(t) {
    this.tool = t; this.pick = null; this.leader = null;
    if (this.host) this.host.style.cursor = t ? "crosshair" : "";
    if (this.hint) this.hint.textContent = TOOL_HINT[t] || HINT;
    this.svg?.querySelector(".dw-rubber")?.remove();
  }

  async cmd(name, args) { try { await this.app.run(name, args); } catch { /* toast shown */ } await this.load(); }

  async load() {
    const st = (await this.app.api("/api/state")).model;
    this.state = (st && st.drawing) || {};
    const r = await fetch(`/api/drawing?t=${Date.now()}`);
    if (!r.ok) { this.host.innerHTML = `<div class="dw-err">${escapeHtml(await r.text())}</div>`; this.svg = null; return; }
    this.host.innerHTML = await r.text();
    this.svg = this.host.querySelector("svg");
    this.svg.removeAttribute("width"); this.svg.removeAttribute("height");
    const sh = this.sheet || {};
    this.sheetSel.value = (this.sheeted ? sh.size : this.state.sheet) || "A3"; this.projSel.value = sh.projection || "third";
    this.scaleSel.value = String((this.sheeted ? sh.scale : this.state.scale) ?? 0);
    this.miBtn.classList.toggle("on", this.sheeted ? (sh.views || []).some((v) => v.dims) : !!this.state.model_items);
    this.layout.classList.toggle("hidden", !this.sheeted);
    this.newBtn.classList.toggle("hidden", this.sheeted);
    this.svg.querySelectorAll("[data-view]").forEach((g) => g.classList.add("dw-view"));
    this.svg.querySelectorAll("[data-ann], [data-dim], [data-table]").forEach((a) => a.classList.add("dw-ann"));
    this.renderTabs();
  }

  renderTabs() {
    this.tabs.innerHTML = "";
    this.tabs.classList.toggle("hidden", !this.sheeted);
    if (!this.sheeted) return;
    this.state.sheets.forEach((s, i) => {
      const t = el("button", "dw-tab" + (i === (this.state.active || 0) ? " on" : ""), escapeHtml(s.name));
      t.onclick = () => this.cmd("drawing_activate_sheet", { sheet: s.name });
      t.oncontextmenu = (e) => { e.preventDefault(); this.app.ctxMenu(e, [
        ["edit", "Rename...", () => this.app.modal("Rename Sheet", `<div class="field"><label>Name</label><input id="dwsn" value="${escapeHtml(s.name)}"></div>`,
          async () => { await this.app.run("drawing_activate_sheet", { sheet: s.name }).catch(() => {}); return this.cmd("drawing_sheet_properties", { name: document.getElementById("dwsn").value }); }, "edit")],
        ["trash", "Delete Sheet", () => this.cmd("drawing_delete_sheet", { sheet: s.name })]]); };
      this.tabs.append(t);
    });
    const add = el("button", "dw-tab dw-add", "+"); add.title = "Add Sheet"; add.onclick = () => this.cmd("drawing_add_sheet", {});
    this.tabs.append(add);
  }

  sheetProp(sheetArgs, autoArgs) {
    if (this.sheeted) { if (sheetArgs) this.cmd("drawing_sheet_properties", sheetArgs); return; }
    this.cmd("drawing_settings", autoArgs);
  }

  toggleModelItems() {
    const on = this.sheeted ? !(this.sheet.views || []).some((v) => v.dims) : !this.state.model_items;
    this.cmd("drawing_settings", { model_items: on });
  }

  newDrawing() {
    this.app.modal("New Drawing", `<div class="field"><label>Sheet size</label><select id="dwns">${SHEET_SIZES.map((s) => `<option${s === "A3" ? " selected" : ""}>${s}</option>`).join("")}</select></div>
      <div class="field"><label>Projection</label><select id="dwnp"><option value="third">Third angle</option><option value="first">First angle</option></select></div>
      <label class="pm-check"><input id="dwnv" type="checkbox" checked> Standard views (Front, Top, Right, Isometric)</label>`, () => {
      const q = (id) => document.getElementById(id);
      return this.cmd("drawing_new", { sheet: q("dwns").value, projection: q("dwnp").value, standard_views: q("dwnv").checked });
    }, "drawing");
  }

  modelView() {
    this.app.modal("Model View", `<div class="field"><label>Orientation</label><select id="dwo">${ORIENT.map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select></div>`,
      () => { this.orient = document.getElementById("dwo").value; this.setTool("model"); }, "v_front");
  }

  revisionDialog() {
    const revs = this.sheet.revisions || []; const last = revs.length ? revs[revs.length - 1].rev : "";
    const next = last ? (/^[A-Y]$/.test(last) ? String.fromCharCode(last.charCodeAt(0) + 1) : last + "1") : "A";
    this.app.modal("Revision", `<div class="field"><label>Revision</label><input id="dwrv" value="${next}" maxlength="4"></div>
      <div class="field"><label>Description</label><input id="dwrd" value="${revs.length ? "" : "Initial release"}"></div>
      <div class="field"><label>Approved</label><input id="dwra" value=""></div>`, () => {
      const q = (id) => document.getElementById(id).value;
      return this.cmd("drawing_add_revision", { rev: q("dwrv"), description: q("dwrd"), approved: q("dwra") });
    }, "properties");
  }

  mm(e) { // screen -> sheet millimetres
    const pt = this.svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    const p = pt.matrixTransform(this.svg.getScreenCTM().inverse()); return [p.x, p.y];
  }

  viewAt(e) { const g = e.target.closest("[data-view]"); return g ? g.getAttribute("data-view") : null; }

  findView(id) { for (const s of this.state.sheets || []) for (const v of s.views) if (v.id === id) return v; return null; }

  down(e) {
    if (!this.svg || e.button !== 0) return;
    const at = this.mm(e);
    if (this.tool) {
      if (["note", "datum", "gdt", "finish", "weld"].includes(this.tool)) {
        if (e.shiftKey) { this.leader = at; this.hint.textContent = "Leader point set; now click where the text goes."; return; }
        return this.place(this.tool, at);
      }
      if (this.tool === "stacked") return this.balloonClick(e, at, true);
      if (this.tool === "altpos") {
        const id = this.viewAt(e);
        if (!id) { this.hint.textContent = "Click on an assembly view."; return; }
        this.setTool(null); return this.altPositionView(id);
      }
      if (this.tool === "dim") return this.dimClick(e, at);
      if (this.tool === "callout") return this.calloutClick(e, at);
      if (this.tool === "balloon") return this.balloonClick(e, at);
      if (this.tool === "autoballoon") {
        const id = this.viewAt(e);
        if (!id) { this.hint.textContent = "Click on a view. " + TOOL_HINT.autoballoon; return; }
        this.setTool(null); return this.cmd("drawing_auto_balloon", { view: id });
      }
      return this.layoutClick(e, at);
    }
    const dimEl = e.target.closest("[data-dim]");
    if (dimEl) return this.select({ kind: "dim", id: dimEl.getAttribute("data-dim"), el: dimEl }); // (right-click for the options)
    const ann = e.target.closest("[data-ann]"), view = e.target.closest("[data-view]"), table = e.target.closest("[data-table]");
    if (table) this.drag = { kind: "table", id: table.getAttribute("data-table"), start: at, el: table };
    else if (ann) this.drag = { kind: "ann", id: ann.getAttribute("data-ann"), start: at, el: ann };
    else if (view) this.drag = { kind: "view", id: view.getAttribute("data-view"), start: at, el: view };
    if (this.drag) { this.host.setPointerCapture(e.pointerId); this.drag.el.classList.add("dw-dragging"); }
  }

  layoutClick(e, at) {
    const t = this.tool;
    if (t === "model") { this.setTool(null); return this.cmd("drawing_add_view", { orientation: this.orient || "front", x: at[0], y: at[1] }); }
    if (t === "dimvalue") {
      const id = this.viewAt(e);
      if (!id) { this.hint.textContent = TOOL_HINT.dimvalue; return; }
      this.setTool(null);
      return this.app.modal("Dimension by Value", `<div class="field"><label>Value</label><input id="dwdv" placeholder="100, ⌀16, 2X ⌀9, R5"></div>
        <div class="field"><label>Units</label><select id="dwdu"><option value="auto">mm, else inches</option><option value="mm">mm</option><option value="in">inches</option></select></div>`,
        () => this.cmd("drawing_dimension_value", { view: id, text: document.getElementById("dwdv").value, units: document.getElementById("dwdu").value }), "smart_dim");
    }
    if (t === "holes") {
      const id = this.viewAt(e);
      if (!id) { this.hint.textContent = "Click on a view. " + TOOL_HINT.holes; return; }
      this.setTool(null); return this.cmd("drawing_hole_table", { view: id, origin: at });
    }
    if (!this.pick) { // first click: on a view
      const id = this.viewAt(e);
      if (!id) { this.hint.textContent = "Click on a view first. " + TOOL_HINT[t]; return; }
      this.pick = { view: id, at };
      this.hint.textContent = { projected: "Now click beside the view where the projected view goes.", section: "Now click the end of the cutting line.",
        detail: "Now click a point on the detail circle.", auxiliary: "Now click a second point along the edge.",
        crop: "Now click the opposite corner.", break: "Now click where the second break line goes.", broken: "Now click a point on the edge of the region." }[t];
      return;
    }
    const { view, at: a } = this.pick; this.setTool(null);
    if (t === "projected") {
      const v = this.findView(view); const dx = at[0] - v.x, dy = at[1] - v.y;
      const side = Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "bottom" : "top");
      return this.cmd("drawing_projected_view", { parent: view, side, ...(side === "left" || side === "right" ? { x: at[0] } : { y: at[1] }) });
    }
    if (t === "section") return this.cmd("drawing_section_view", { parent: view, start: a, end: at });
    if (t === "detail") return this.cmd("drawing_detail_view", { parent: view, center: a, radius: Math.max(2, Math.hypot(at[0] - a[0], at[1] - a[1])) });
    if (t === "auxiliary") return this.cmd("drawing_auxiliary_view", { parent: view, edge_start: a, edge_end: at });
    if (t === "crop") return this.cmd("drawing_crop_view", { view, corner1: a, corner2: at });
    if (t === "break") return this.cmd("drawing_break_view", { view, start: a, end: at,
      orientation: Math.abs(at[0] - a[0]) >= Math.abs(at[1] - a[1]) ? "vertical" : "horizontal" });
    if (t === "broken") {
      const radius = Math.max(1, Math.hypot(at[0] - a[0], at[1] - a[1]));
      return this.app.modal("Broken-out Section", `<div class="field"><label>Depth (mm, from the nearest face)</label><input id="dwbd" type="number" step="1" value="5"></div>`,
        () => this.cmd("drawing_broken_out_section", { view, center: a, radius, depth: parseFloat(document.getElementById("dwbd").value) || 5 }), "cut_extrude");
    }
  }

  async dimClick(e, at) {
    const p = this.pick;
    if (p && p.place) { this.setTool("dim"); return this.placeDim(p, at); } // last click: where it goes
    const view = p ? p.view : this.viewAt(e);
    if (!view) { this.hint.textContent = "Click on a view first. " + TOOL_HINT.dim; return; }
    const r = await this.app.api("/api/command", { name: "drawing_pick", args: { view, p: at } });
    const hit = r.ok ? r.result : {};
    if (!p) {
      if (hit.vertex) { this.pick = { view, at: hit.vertex_sheet, v: hit.vertex }; this.hint.textContent = "Now click a second corner."; return; }
      if (hit.circle) {
        this.pick = { view, at, circle: hit.circle, kind: e.shiftKey ? "radius" : "diameter", place: true };
        this.hint.textContent = "Now click where the dimension goes."; return;
      }
      if (hit.line) { this.pick = { view, at, line: hit.line }; this.hint.textContent = "Now click a second edge (angle, or distance if parallel)."; return; }
      this.hint.textContent = "Nothing to dimension there: click a corner, an edge or a circle. " + TOOL_HINT.dim; return;
    }
    if (p.line) { // edge, edge, then where it goes
      if (!hit.line) { this.hint.textContent = "Click a second straight edge of the same view."; return; }
      this.pick = { ...p, at2: at, place: true };
      this.hint.textContent = "Now click where the dimension goes."; return;
    }
    if (!hit.vertex || (hit.vertex[0] === p.v[0] && hit.vertex[1] === p.v[1])) { this.hint.textContent = "Click a different corner of the same view."; return; }
    this.pick = { ...p, b: hit.vertex_sheet, place: true };
    this.svg.querySelector(".dw-rubber")?.remove();
    this.hint.textContent = "Now click where the dimension goes (above / below: horizontal, left / right: vertical).";
  }

  placeDim(p, at) {
    if (p.line) return this.cmd("drawing_add_dimension", { view: p.view, kind: "auto", p1: p.at, p2: p.at2, at, offset: 8 });
    if (p.circle) {
      const [cx, cy] = p.circle.center_sheet, dx = at[0] - cx, dy = at[1] - cy;
      return this.cmd("drawing_add_dimension", { view: p.view, kind: p.kind, p1: p.at, angle: Math.atan2(-dy, dx) * 180 / Math.PI,
        offset: Math.max(2, Math.hypot(dx, dy) - p.circle.radius_sheet) });
    }
    let a = p.at, b = p.b;
    const [x0, x1] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])], [y0, y1] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
    const kind = (at[1] < y0 || at[1] > y1) && at[0] >= x0 && at[0] <= x1 ? "horizontal"
      : (at[0] < x0 || at[0] > x1) && at[1] >= y0 && at[1] <= y1 ? "vertical" : "linear";
    if (kind === "horizontal") b = [b[0], a[1]]; else if (kind === "vertical") b = [a[0], b[1]];
    // the signed offset as drawing._dim_aligned measures it (positive: above / left of the feature)
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1, ux = (b[0] - a[0]) / L, uy = (b[1] - a[1]) / L;
    let nx = -uy, ny = ux; if (ny > 0 || (Math.abs(ny) < 1e-9 && nx > 0)) { nx = -nx; ny = -ny; }
    const offset = (at[0] - a[0]) * nx + (at[1] - a[1]) * ny;
    return this.cmd("drawing_add_dimension", { view: p.view, kind, p1: p.at, p2: p.b, offset: Math.abs(offset) < 2 ? 8 : offset });
  }

  async calloutClick(e, at) {
    const p = this.pick;
    if (p) { // where the callout goes
      this.setTool("callout");
      const [cx, cy] = p.circle.center_sheet, dx = at[0] - cx, dy = at[1] - cy;
      return this.cmd("drawing_add_dimension", { view: p.view, kind: "callout", p1: p.at, angle: Math.atan2(-dy, dx) * 180 / Math.PI,
        offset: Math.max(2, Math.hypot(dx, dy) - p.circle.radius_sheet) });
    }
    const view = this.viewAt(e);
    if (!view) { this.hint.textContent = "Click on a view first. " + TOOL_HINT.callout; return; }
    const r = await this.app.api("/api/command", { name: "drawing_pick", args: { view, p: at } });
    if (!r.ok || !r.result.circle) { this.hint.textContent = "Click on a hole's edge. " + TOOL_HINT.callout; return; }
    this.pick = { view, at, circle: r.result.circle };
    this.hint.textContent = "Now click where the callout goes.";
  }

  balloonClick(e, at, stacked = false) {
    const p = this.pick;
    if (p && stacked) {
      this.pick = null; this.setTool(null);
      const comps = (this.app.model?.components || []).map((c) => c.name);
      this.app.modal("Stacked Balloon", `<div class="field"><label>More items in the stack (component names or text, comma separated)</label>
        <input id="dwst" value="" list="dwstl" style="width:100%"><datalist id="dwstl">${comps.map((c) => `<option value="${escapeHtml(c)}">`).join("")}</datalist></div>
        <div class="field"><label>Stack direction</label><select id="dwsd"><option value="down">Down</option><option value="right">Right</option></select></div>`, () =>
        this.cmd("drawing_add_stacked_balloon", { view: p.view, point: p.at, x: at[0], y: at[1], direction: document.getElementById("dwsd").value,
          items: document.getElementById("dwst").value.split(",").map((s) => s.trim()).filter(Boolean) }), "note");
      return;
    }
    if (p) { this.setTool("balloon"); return this.cmd("drawing_add_balloon", { view: p.view, point: p.at, x: at[0], y: at[1] }); }
    const view = this.viewAt(e);
    if (!view) { this.hint.textContent = "Click on a part in a view first. " + TOOL_HINT.balloon; return; }
    this.pick = { view, at };
    this.hint.textContent = "Now click where the balloon goes.";
  }

  findDim(id) { for (const s of this.state.sheets || []) for (const d of s.dims || []) if (d.id === id) return d; return null; }

  dimDialog(d) {
    this.app.modal(d.id, `<div class="field"><label>Text (blank: the measured value)</label><input id="dwdt" value="${escapeHtml(d.text || "")}"></div>
      <div class="field"><label>Offset (mm)</label><input id="dwdo" type="number" step="1" value="${+(d.offset ?? 8).toFixed(2)}"></div>`, () => {
      const q = (id) => document.getElementById(id).value;
      return this.cmd("drawing_edit_dimension", { id: d.id, text: q("dwdt"), offset: parseFloat(q("dwdo")) || 0 });
    }, "smart_dim");
  }

  move(e) {
    if (this.pick && !this.pick.place && this.svg && this.tool === "dim") { // rubber band to the second corner
      const at = this.mm(e), a = this.pick.at;
      let r = this.svg.querySelector(".dw-rubber");
      if (!r) { r = document.createElementNS("http://www.w3.org/2000/svg", "line"); r.setAttribute("class", "dw-rubber"); this.svg.append(r); }
      r.setAttribute("x1", a[0]); r.setAttribute("y1", a[1]); r.setAttribute("x2", at[0]); r.setAttribute("y2", at[1]);
      return;
    }
    if (this.pick && this.svg && ["section", "detail", "auxiliary", "crop", "break", "broken"].includes(this.tool)) { // rubber band
      const at = this.mm(e), a = this.pick.at, round = this.tool === "detail" || this.tool === "broken";
      let r = this.svg.querySelector(".dw-rubber");
      if (!r) { r = document.createElementNS("http://www.w3.org/2000/svg", round ? "circle" : this.tool === "crop" ? "rect" : "line"); r.setAttribute("class", "dw-rubber"); this.svg.append(r); }
      if (round) { r.setAttribute("cx", a[0]); r.setAttribute("cy", a[1]); r.setAttribute("r", Math.hypot(at[0] - a[0], at[1] - a[1])); }
      else if (this.tool === "crop") {
        r.setAttribute("x", Math.min(a[0], at[0])); r.setAttribute("y", Math.min(a[1], at[1]));
        r.setAttribute("width", Math.abs(at[0] - a[0])); r.setAttribute("height", Math.abs(at[1] - a[1]));
      } else { r.setAttribute("x1", a[0]); r.setAttribute("y1", a[1]); r.setAttribute("x2", at[0]); r.setAttribute("y2", at[1]); }
      return;
    }
    if (!this.drag) return;
    const [x, y] = this.mm(e), [x0, y0] = this.drag.start;
    let dx = x - x0, dy = y - y0;
    if (this.drag.kind === "view") { // projected views stay aligned with their parent
      const v = this.sheeted ? this.findView(this.drag.id) : null;
      const side = v ? (v.kind === "projected" && v.aligned !== false ? v.side : null) : ({ top: "top", right: "right", left: "left" }[this.drag.id] || null);
      if (side === "top" || side === "bottom") dx = 0;
      if (side === "left" || side === "right") dy = 0;
    }
    this.drag.dx = dx; this.drag.dy = dy;
    const target = this.drag.kind === "ann" ? this.svg.querySelectorAll(`[data-ann="${this.drag.id}"]`) : [this.drag.el];
    target.forEach((t) => t.setAttribute("transform", `translate(${dx} ${dy})`));
  }

  async up() {
    const d = this.drag; this.drag = null;
    if (!d || (!d.dx && !d.dy)) { d?.el.classList.remove("dw-dragging"); if (d) this.select({ kind: d.kind, id: d.id, el: d.el }); return; }
    if (d.kind === "view") await this.cmd("drawing_move_view", { view: d.id, dx: d.dx, dy: d.dy });
    else if (d.kind === "table") {
      const r = d.el.querySelector("rect");
      await this.cmd("drawing_move_table", { table: d.id, x: parseFloat(r.getAttribute("x")) + d.dx, y: parseFloat(r.getAttribute("y")) + d.dy });
    }
    else {
      const a = this.findAnn(d.id);
      if (a) await this.cmd("drawing_edit_annotation", { id: d.id, x: a.x + d.dx, y: a.y + d.dy });
    }
  }

  isOpen() { return !!this.pane && !this.pane.classList.contains("hidden"); }
  select(item) {
    this.sel?.el?.classList.remove("dw-selected");
    this.sel = item; item?.el?.classList.add("dw-selected");
    if (item) this.hint.textContent = `${item.id} selected: Delete removes it, drag moves it, right-click for options.`;
  }
  deleteSelected() {
    const { kind, id } = this.sel; this.select(null);
    const cmd = { view: ["drawing_delete_view", { view: id }], ann: ["drawing_delete_annotation", { id }], table: ["drawing_delete_table", { table: id }],
      dim: ["drawing_delete_dimension", { id }] }[kind];
    if (cmd) this.cmd(...cmd);
  }

  findAnn(id) { const s = this.sheet || {}; for (const k of ["notes", "datums", "gdt", "balloons"]) { const a = (s[k] || []).find((x) => x.id === id); if (a) return a; } return null; }

  context(e) {
    const table = e.target.closest("[data-table]");
    if (table) {
      e.preventDefault();
      const id = table.getAttribute("data-table"), items = [];
      if (id === "BOM") items.push(["edit", "Edit BOM...", () => this.bomDialog()]);
      items.push(["trash", "Delete Table", () => this.cmd("drawing_delete_table", { table: id })]);
      return this.app.ctxMenu(e, items);
    }
    const dim = e.target.closest("[data-dim]");
    if (dim) {
      e.preventDefault();
      const d = this.findDim(dim.getAttribute("data-dim")); if (!d) return;
      const items = [["edit", "Properties...", () => this.dimDialog(d)]];
      if (d.kind === "radius" || d.kind === "diameter") {
        const other = d.kind === "radius" ? "diameter" : "radius";
        items.push(["circle", `Show as ${other}`, () => this.cmd("drawing_edit_dimension", { id: d.id, kind: other })]);
      }
      items.push(["trash", "Delete", () => this.cmd("drawing_delete_dimension", { id: d.id })]);
      return this.app.ctxMenu(e, items);
    }
    const ann = e.target.closest("[data-ann]");
    if (ann) {
      e.preventDefault();
      const id = ann.getAttribute("data-ann"), a = this.findAnn(id);
      const items = [];
      if (id.startsWith("Note")) items.push(["edit", "Edit Text...", () => this.noteDialog(null, a)]);
      if (id.startsWith("Balloon")) items.push(["edit", "Balloon Text...", () => this.app.modal(id, `<div class="field"><label>Text (blank: the item number)</label><input id="dwbt" value="${escapeHtml(a?.text || "")}"></div>`,
        () => this.cmd("drawing_edit_annotation", { id, text: document.getElementById("dwbt").value }), "note")]);
      items.push(["trash", "Delete", () => this.cmd("drawing_delete_annotation", { id })]);
      return this.app.ctxMenu(e, items);
    }
    const id = this.sheeted ? this.viewAt(e) : null; if (!id) return;
    e.preventDefault();
    const v = this.findView(id);
    const items = [["edit", "Properties...", () => this.viewDialog(v)],
      ["v_right", "Projected View", () => { this.setTool("projected"); this.pick = { view: id, at: [v.x, v.y] }; this.hint.textContent = "Click beside the view where the projected view goes."; }]];
    if (v.kind === "projected") items.push([v.aligned === false ? "relation" : "delete_rel", v.aligned === false ? "Align with Parent" : "Break Alignment", () => this.cmd("drawing_view_properties", { view: id, aligned: v.aligned === false })]);
    for (const [k, label] of [["crop", "Remove Crop"], ["breaks", "Un-Break View"], ["broken_out", "Remove Broken-out Section"]])
      if (v[k]) items.push(["delete_rel", label, () => this.cmd("drawing_remove_view_effect", { view: id, effect: k })]);
    items.push(["trash", "Delete View", () => this.cmd("drawing_delete_view", { view: id })]);
    this.app.ctxMenu(e, items);
  }

  viewDialog(v) {
    const sc = v.scale || 0;
    this.app.modal(`${v.id}`, `<div class="field"><label>Scale</label><select id="dwvs">${[[0, "Use sheet / parent scale"], [5, "5:1"], [2, "2:1"], [1, "1:1"], [0.5, "1:2"], [0.2, "1:5"], [0.1, "1:10"]]
      .map(([k, l]) => `<option value="${k}"${Math.abs(k - sc) < 1e-9 ? " selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="field"><label>Hidden lines</label><select id="dwvh"><option value="visible">Visible (dashed)</option><option value="removed"${v.hidden === "removed" ? " selected" : ""}>Removed</option></select></div>
      <label class="pm-check"><input id="dwvd" type="checkbox"${v.dims ? " checked" : ""}> Show model dimensions</label>
      <label class="pm-check"><input id="dwvc" type="checkbox"${v.center_marks ? " checked" : ""}> Center marks on circles</label>
      <label class="pm-check"><input id="dwvl2" type="checkbox"${v.centerlines ? " checked" : ""}> Centerlines on cylinders</label>
      <div class="field"><label>Label (blank: automatic)</label><input id="dwvl" value="${escapeHtml(v.label || "")}"></div>`, () => {
      const q = (id) => document.getElementById(id);
      return this.cmd("drawing_view_properties", { view: v.id, scale: parseFloat(q("dwvs").value), hidden: q("dwvh").value, dims: q("dwvd").checked, label: q("dwvl").value,
        center_marks: q("dwvc").checked, centerlines: q("dwvl2").checked });
    }, "edit");
  }

  async titleDialog() {
    const tf = (await this.app.api("/api/command", { name: "drawing_info", args: {} })).result?.title_block || {};
    const F = [["company", "Company"], ["title", "Title (blank: the model's description or name)"], ["dwg_no", "Drawing number"],
      ["material", "Material (blank: the part's)"], ["drawn", "Drawn by"], ["drawn_date", "Date (blank: today)"], ["checked", "Checked by"], ["approved", "Approved by"]];
    this.app.modal("Title Block", F.map(([k, l]) => `<div class="field"><label>${l}</label><input id="dwtb_${k}" value="${escapeHtml(tf[k] || "")}"></div>`).join(""),
      () => this.cmd("drawing_title_block", Object.fromEntries(F.map(([k]) => [k, document.getElementById(`dwtb_${k}`).value]))), "edit");
  }

  async bomDialog() {
    const info = (await this.app.api("/api/command", { name: "drawing_info", args: {} })).result || {};
    const tb = (info.sheets || []).flatMap((s) => s.tables || []).find((x) => x.type === "bom");
    const rows = (tb && tb.rows) || [];
    this.app.modal("Bill of Materials", `<table class="dw-bom"><tr><th>Item</th><th>Part number</th><th>Description</th><th>Qty</th></tr>${rows.map((r, i) =>
      `<tr><td><input id="dwbi${i}" type="number" min="1" value="${r.item}" style="width:4em"></td><td><input id="dwbp${i}" value="${escapeHtml(r.part_number)}"></td>
      <td><input id="dwbd${i}" value="${escapeHtml(r.description)}"></td><td>${r.qty}</td></tr>`).join("")}</table>`, async () => {
      const q = (id) => document.getElementById(id).value;
      const order = rows.map((r, i) => [parseInt(q(`dwbi${i}`)) || r.item, i]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      for (const [, i] of order) {
        const r = rows[i];
        await this.app.run("drawing_edit_bom", { part: r.part, part_number: q(`dwbp${i}`), description: q(`dwbd${i}`) }).catch(() => {});
      }
      for (const [k, [, i]] of order.entries()) await this.app.run("drawing_edit_bom", { part: rows[i].part, item: k + 1 }).catch(() => {});
      await this.load();
    }, "properties");
  }

  place(tool, at) {
    const leader = this.leader || null; this.leader = null;
    if (tool === "note") this.noteDialog(at, null, leader);
    else if (tool === "datum") {
      const used = new Set(((this.sheet || {}).datums || []).map((d) => d.label)); const next = "ABCDEFGHJKLMNP".split("").find((c) => !used.has(c)) || "A";
      this.app.modal("Datum Feature", `<div class="field"><label>Label</label><input id="dwlab" value="${next}" maxlength="2"></div>`,
        () => this.cmd("drawing_add_datum", { label: document.getElementById("dwlab").value || next, x: at[0], y: at[1], leader }), "datum");
    } else if (tool === "gdt") {
      this.app.modal("Geometric Tolerance", `<div class="field"><label>Characteristic</label><select id="dwc">${GDT.map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select></div>
        <div class="field"><label>Tolerance</label><input id="dwt" type="number" step="0.01" value="0.1"></div>
        <label class="pm-check"><input id="dwd" type="checkbox" checked> Diameter zone (⌀)</label>
        <div class="field"><label>Material condition</label><select id="dwm"><option value="">None (RFS)</option><option>MMC</option><option>LMC</option></select></div>
        <div class="field"><label>Datums (primary, secondary, tertiary)</label><input id="dwr" value="A, B, C"></div>`, () => {
        const q = (id) => document.getElementById(id);
        return this.cmd("drawing_add_gdt", { characteristic: q("dwc").value, tolerance: parseFloat(q("dwt").value), x: at[0], y: at[1], leader,
          diameter: q("dwd").checked, modifier: q("dwm").value || null, datums: q("dwr").value.split(",").map((s) => s.trim()).filter(Boolean) });
      }, "gdt");
    } else if (tool === "finish") {
      this.app.modal("Surface Finish", `<div class="field"><label>Symbol</label><select id="dwfp"><option value="required">Machining required</option>
        <option value="any">Basic (any process)</option><option value="prohibited">Machining prohibited</option></select></div>
        <div class="field"><label>Roughness (Ra, e.g. 1.6 or Rz 6.3)</label><input id="dwfr" value="1.6"></div>
        <div class="field"><label>Lay direction</label><select id="dwfl"><option value="">None</option>${["=", "⊥", "X", "M", "C", "R", "P"].map((s) => `<option>${s}</option>`).join("")}</select></div>`, () => {
        const q = (id) => document.getElementById(id).value;
        return this.cmd("drawing_add_surface_finish", { x: at[0], y: at[1], process: q("dwfp"), ra: q("dwfr") || null, lay: q("dwfl") || null, leader });
      }, "finish");
    } else if (tool === "weld") {
      const arrow = leader || [at[0] - 15, at[1] + 12];
      this.app.modal("Weld Symbol", `<div class="field"><label>Weld</label><select id="dwwt">${[["fillet", "Fillet"], ["v_groove", "V groove"], ["square", "Square groove"], ["bevel", "Bevel groove"],
        ["u_groove", "U groove"], ["j_groove", "J groove"], ["plug", "Plug / slot"], ["spot", "Spot"]].map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select></div>
        <div class="field"><label>Side</label><select id="dwws"><option value="arrow">Arrow side</option><option value="other">Other side</option><option value="both">Both sides</option></select></div>
        <div class="field"><label>Size</label><input id="dwwz" value="5"></div><div class="field"><label>Length (- pitch)</label><input id="dwwl" value=""></div>
        <label class="pm-check"><input id="dwwa" type="checkbox"> Weld all around</label><label class="pm-check"><input id="dwwf" type="checkbox"> Field weld</label>
        <div class="field"><label>Process (tail)</label><input id="dwwp" value=""></div>`, () => {
        const q = (id) => document.getElementById(id);
        return this.cmd("drawing_add_weld_symbol", { x: at[0], y: at[1], leader: arrow, type: q("dwwt").value, side: q("dwws").value, size: q("dwwz").value || null,
          length: q("dwwl").value || null, all_around: q("dwwa").checked, field: q("dwwf").checked, process: q("dwwp").value || null });
      }, "weldment");
    }
    this.setTool(null);
  }

  async altPositionView(view) {
    const names = Object.keys(this.app.model?.alternate_positions || {});
    if (!names.length) return this.app.toast("The assembly has no alternate positions (Insert > Alternate Position).", "err");
    this.app.modal("Alternate Position View", `<div class="field"><label>Position</label><select id="dwap"><option value="">(none)</option>${names.map((n) => `<option>${escapeHtml(n)}</option>`).join("")}</select></div>`,
      () => this.cmd("drawing_view_alternate_position", { view, position: document.getElementById("dwap").value || null }), "explode");
  }

  layersDialog() {
    const layers = this.state?.layers || {};
    const rows = Object.entries(layers).map(([n, l]) => `<tr><td>${escapeHtml(n)}</td><td><input type="color" data-lc="${escapeHtml(n)}" value="${l.color}"></td>
      <td><input type="checkbox" data-lv="${escapeHtml(n)}" ${l.visible ? "checked" : ""}></td></tr>`).join("");
    this.app.modal("Layers", `<table class="dw-bom"><tr><th>Layer</th><th>Colour</th><th>Show</th></tr>${rows || "<tr><td colspan=3>No layers yet.</td></tr>"}</table>
      <div class="field"><label>New layer</label><input id="dwln" placeholder="Name"></div>
      <div class="field"><label>Put annotations on a layer (ids: Note1, Dim2, Weld1 ...)</label><input id="dwli" placeholder="Note1, Note2"> <select id="dwll"><option value="">(no layer)</option>${Object.keys(layers).map((n) => `<option>${escapeHtml(n)}</option>`).join("")}</select></div>`, async () => {
      const q = (id) => document.getElementById(id);
      for (const [n] of Object.entries(layers)) {
        await this.app.run("drawing_add_layer", { name: n, color: document.querySelector(`[data-lc="${CSS.escape(n)}"]`).value, visible: document.querySelector(`[data-lv="${CSS.escape(n)}"]`).checked }).catch(() => {});
      }
      if (q("dwln").value.trim()) await this.app.run("drawing_add_layer", { name: q("dwln").value.trim() }).catch(() => {});
      const items = q("dwli").value.split(",").map((s) => s.trim()).filter(Boolean);
      if (items.length) await this.app.run("drawing_set_layer", { items, layer: q("dwll").value || null }).catch(() => {});
      await this.load();
    }, "properties");
  }

  async sheetFormatDialog() {
    const f = (await this.app.api("/api/command", { name: "list_sheet_formats", args: {} })).result || { presets: {}, saved: {} };
    const opts = [...Object.keys(f.presets || {}).map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)} (preset)</option>`),
      ...Object.keys(f.saved || {}).map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`)].join("");
    this.app.modal("Sheet Format", `<div class="field"><label>Use</label><select id="dwsf"><option value="">Standard title block</option>${opts}</select></div>
      <div class="field"><label>Save the current format as (optional)</label><input id="dwss" placeholder="Name"></div>`, async () => {
      const v = document.getElementById("dwsf").value, save = document.getElementById("dwss").value.trim();
      await this.app.run("drawing_set_sheet_format", v ? { preset: v } : {}).catch(() => {});
      if (save) await this.app.run("drawing_save_sheet_format", { name: save }).catch(() => {});
      await this.load();
    }, "edit");
  }

  noteDialog(at, note = null, leader = null) {
    this.app.modal(note ? `Edit ${note.id}` : "Note", `<div class="field"><label>Text</label><textarea id="dwn" rows="4" style="width:100%">${escapeHtml(note?.text || "")}</textarea></div>
      <div class="field"><label>Height (mm)</label><input id="dws" type="number" value="${note?.size || 3.5}" step="0.5"></div>`, () => {
      const text = document.getElementById("dwn").value;
      if (note) return this.cmd("drawing_edit_annotation", { id: note.id, text });
      return this.cmd("drawing_add_note", { text, x: at[0], y: at[1], size: parseFloat(document.getElementById("dws").value) || 3.5, leader });
    }, "note");
    setTimeout(() => document.getElementById("dwn")?.focus());
  }

  async saveDocument() {
    const m = this.app.model || {};
    if (!m.path) return this.app.toast("Save the part / assembly first: the drawing refers to its file.", "err");
    const def = m.drawing_document || m.path.replace(/\.[^.\\/]+$/, ".oswdrw");
    const path = await this.app.askPath("save", def, ["openswx drawing (*.oswdrw)"], "Save Drawing");
    if (!path) return;
    try { const r = await this.app.run("save_drawing_document", { path }); this.app.toast(`Saved ${r.result.saved}`); } catch { /* toast shown */ }
  }

  async export(fmt) {
    if (this.app.desk) return this.app.deskCall("export_drawing", fmt);
    window.open(`/api/drawing_export?format=${fmt}`, "_blank");
  }
}
