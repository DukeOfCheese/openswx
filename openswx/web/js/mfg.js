// Manufacturing and documentation dialogs: CAM (toolpaths, G-code), Plastics (fill analysis and plots),
// Inspection (characteristics, results, reports), Electrical (connectors, routes, from-to list, harness,
// formboard) and Composer (animated instructions). Mixed into the App class by installMfg().

import { escapeHtml } from "./propman.js";
import { css, kv, lineChart, rgbRamp } from "./more.js";

const $ = (s) => document.querySelector(s);
const OP_COLOR = { rough: 0xf59e0b, finish: 0x16a34a, drill: 0xdc2626 };
const PLASTICS = ["ABS", "PC", "PP", "PA66", "POM", "HDPE", "PS", "PC/ABS"];
const FIELDS = { fill_time: ["Fill time", "s"], pressure: ["Pressure at end of fill", "MPa"], thickness: ["Wall thickness", "mm"],
  weld: ["Weld lines", "1 = weld line"], sink: ["Sink mark risk", "1 = thick section"] };
const table = (heads, rows) => `<table class="dw-bom" style="margin-top:8px"><tr>${heads.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr>${rows.join("")}</table>`;
const td = (v, right = false) => `<td${right ? ' style="text-align:right"' : ""}>${escapeHtml(String(v ?? ""))}</td>`;
const warnings = (list) => (list || []).map((w) => `<p class="muted">⚠ ${escapeHtml(w)}</p>`).join("");
const download = (name, text, type = "text/plain") => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); };

const MIXIN = {
  async camFinish3d() {
    const r = await this.cmdForm("CAM - 3D Parallel Finishing", "cam_finish_3d", [
      ["tool", "Tool", "select", "ball", [["ball", "Ball end mill"], ["bull", "Bull nose end mill"], ["flat", "Flat end mill"]]],
      ["tool_diameter", "Tool diameter (mm)", "number", 6], ["corner_radius", "Corner radius (bull nose; blank: D/8)", "number", ""],
      ["stepover", "Stepover (mm, blank: 0.15 D ball / 0.4 D)", "number", ""],
      ["direction", "Passes along", "select", "x", [["x", "X"], ["y", "Y"]]], ["leave", "Stock to leave (mm)", "number", 0]],
    { ic: "cut_extrude", note: "Adds to the generated toolpaths: the tool follows curved and sloped faces instead of stepping down them." });
    if (!r) return;
    this.toast(`${escapeHtml(r.operation.name)}: ${r.operation.length_mm} mm, ${r.operation.time_min} min${r.operation.scallop_mm ? `, scallop ${r.operation.scallop_mm} mm` : ""}.`);
    if (this.camShow) this.camShow();
  },

  async camDialog() {
    const r = await this.cmdForm("CAM - Generate Toolpaths", "cam_generate", [
      ["setup", "Setup (the side the tool comes from)", "select", "+Z", ["+Z", "-Z", "+X", "-X", "+Y", "-Y"]],
      ["material", "Stock material", "select", "aluminum", [["aluminum", "Aluminium"], ["brass", "Brass"], ["plastic", "Plastic"], ["mild_steel", "Mild steel"],
        ["stainless", "Stainless steel"], ["titanium", "Titanium"], ["cast_iron", "Cast iron"]]],
      ["tool_diameter", "Flat end mill diameter (mm)", "number", 6], ["flutes", "Flutes", "number", 3],
      ["stepdown", "Stepdown (mm, blank: D/2)", "number", ""], ["stepover", "Stepover (mm, blank: 0.45 D)", "number", ""],
      ["leave", "Stock to leave for finishing (mm)", "number", 0.3], ["stock_margin", "Stock margin on the sides (mm)", "number", 1],
      ["stock_top", "Stock above the part (mm)", "number", 1], ["drill", "Drill holes along the tool axis", "check", true],
      ["tolerance", "Precision (mm, blank: auto)", "number", ""]],
    { ic: "cut_extrude", note: "Z-level roughing, wall and floor finishing and drilling, checked by a stock-removal simulation." });
    if (!r) return;
    const ops = table(["Operation", "Tool", "Length (mm)", "Time (min)"], r.operations.map((o) =>
      `<tr>${td(o.name)}${td(`T${o.tool} ⌀${r.tools[o.tool - 1].diameter}`)}${td(o.length_mm.toFixed(0), true)}${td(o.time_min.toFixed(2), true)}</tr>`));
    const tools = table(["Tool", "Type", "⌀", "S (rpm)", "F (mm/min)"], r.tools.map((t) => `<tr>${td("T" + t.number)}${td(t.type)}${td(t.diameter)}${td(t.rpm, true)}${td(t.feed, true)}</tr>`));
    const v = r.verification;
    this.showResult("CAM - Toolpaths", { setup: r.setup, stock: r.stock.size.join(" × ") + " mm", holes_drilled: r.holes.length, levels: r.levels.length,
      cycle_time_min: r.cycle_time_min, gouge_mm: v.gouge_mm, material_left_mm3: v.material_left_mm3, precision_mm: r.precision_mm },
    ops + tools + warnings(r.warnings) + '<p class="muted">Material left is mostly the tool radius in inside corners. Feeds and speeds are a starting point.</p>',
    "cut_extrude", () => this.camShow());
  },

  async camShow() {
    const r = await this.evalCall("cam_toolpaths"); if (!r) return;
    const items = [];
    for (const op of r.paths) {
      for (const p of op.feed) items.push({ pts: p, color: OP_COLOR[op.kind] ?? 0x2563eb });
      for (const p of op.rapid) items.push({ pts: p, color: 0x9ca3af, dashed: true });
    }
    this.vp.setOverlay(items);
    this.toast("Toolpaths: roughing orange, finishing green, drilling red, rapids dashed. Tools > CAM > Hide Toolpaths clears them.");
  },

  camHide() { this.vp.setOverlay([]); },

  camGcode() {
    const posts = [["fanuc", "Fanuc (G43, canned cycles)"], ["linuxcnc", "LinuxCNC"], ["grbl", "GRBL (hobby machines)"]];
    this.modal("CAM - Save G-code", `<div class="field"><label>Post processor</label><select id="cam_post">${posts.map(([v, l]) => `<option value="${v}">${escapeHtml(l)}</option>`).join("")}</select></div>`, () => {
      const post = $("#cam_post").value;
      setTimeout(async () => {
        if (this.desk) {
          const path = await this.askPath("save", `${this.model.name}.nc`, ["G-code (*.nc;*.tap;*.gcode)"], "Save G-code");
          if (!path) return;
          const r = await this.run("cam_gcode", { post, path }).catch(() => null);
          if (r) this.toast(`Saved ${r.result.lines} lines to ${escapeHtml(r.result.path)}`);
          return;
        }
        const r = await this.evalCall("cam_gcode", { post }); if (r) download(`${this.model.name}.nc`, r.gcode);
      });
    }, "export");
  },

  async plasticsDialog() {
    const faces = this.selectedIds("face");
    const fields = [["material", "Plastic", "select", "ABS", PLASTICS]];
    if (!faces.length) fields.unshift(["gate", "Gate point x, y, z (or select gate faces first)", "vec", ""]);
    fields.push(["melt_temperature", "Melt temperature (°C, blank: the material's)", "number", ""], ["mold_temperature", "Mold temperature (°C, blank: the material's)", "number", ""],
      ["fill_time", "Fill time (s, blank: auto)", "number", ""], ["max_pressure", "Machine pressure limit (MPa)", "number", 150],
      ["element_size", "Element size (mm, blank: auto)", "number", ""], ["clamp_axis", "Mold opens along", "select", "Z", ["X", "Y", "Z"]]);
    const xf = (a) => { const gates = faces.length ? faces : (a.gate ? [a.gate] : []); delete a.gate; return { ...a, gates }; };
    const r = await this.cmdForm("Plastics - Fill Analysis", "plastics_fill", fields,
      { ic: "sim_run", xf, note: faces.length ? `Gates: ${faces.join(", ")}.` : "Injection-moulding fill: flow front, pressure, weld lines, air traps." });
    if (!r) return;
    const chart = r.pressure_history?.length > 1 ? lineChart([{ points: r.pressure_history }], { xlabel: "time (s)", ylabel: "injection pressure (MPa)" }) : "";
    this.showResult("Plastics - Results", { material: r.material, fill_time_s: r.fill_time_s, filled_pct: r.filled_pct, short_shot: r.short_shot,
      max_injection_pressure_MPa: r.max_injection_pressure_MPa, clamp_force_tonne: r.clamp_force_tonne, cooling_time_s: r.cooling_time_s,
      nominal_wall_mm: r.nominal_wall_mm, weld_line_cells: r.weld_line_cells, air_traps: r.air_traps.length, sink_mark_cells: r.sink_mark_cells, cells: r.cells },
    warnings(r.warnings) + chart + '<p class="muted">Generic material data; isothermal fill (freeze-off is checked, not simulated).</p>', "sim_run", () => this.plasticsPlot("fill_time"));
  },

  async plasticsPlot(field) {
    if (!field) { this.vp.setAnalysis(null); this.vp.setLegend(null); this.vp.setOverlay([]); return; }
    const r = await this.evalCall("plastics_results", { field }); if (!r) return;
    const lo = r.min, hi = r.max, grey = [0.55, 0.55, 0.55];
    const col = (x) => (x === null ? grey : rgbRamp((x - lo) / ((hi - lo) || 1)));
    this.vp.setDeformation(null); this.vp.setFaceGeometry(null);
    this.vp.setAnalysis(Object.fromEntries(Object.entries(r.values).map(([id, v]) => [id, v.map(col)])));
    const [title, unit] = FIELDS[field];
    this.vp.setLegend({ title, unit, min: lo, max: hi, color: (t) => css(rgbRamp(t)) });
    const marks = [];
    if (r.weld_lines.length) marks.push({ pts: r.weld_lines.flat(), point: true, color: 0xd946ef, size: 5, onTop: true });
    if (r.air_traps.length) marks.push({ pts: r.air_traps.flat(), point: true, color: 0x0ea5e9, size: 12, onTop: true });
    if (r.gates.length) marks.push({ pts: r.gates.flat(), point: true, color: 0xfacc15, size: 12, onTop: true });
    this.vp.setOverlay(marks);
    this.toast("Markers: weld lines magenta, air traps blue, gates yellow.");
  },

  async inspectionDialog() {
    const r = await this.evalCall("inspection_characteristics"); if (!r) return;
    const s = r.summary;
    const res = (c) => (c.result ? `<b style="color:${c.result === "pass" ? "#16a34a" : "#dc2626"}">${c.result.toUpperCase()}</b>` : "");
    const rows = r.characteristics.map((c) => `<tr>${td(c.number)}${td([c.sheet, c.zone].filter(Boolean).join(" "))}${td(c.designator)}${td(c.requirement)}`
      + `${td(c.min ?? "", true)}${td(c.max ?? "", true)}<td><input data-ic="${escapeHtml(c.source)}" value="${escapeHtml(c.measured.join(" "))}" style="width:90px" placeholder="value(s)"></td><td>${res(c)}</td></tr>`);
    const gen = ["f", "m", "c", "v"].map((k) => `<option ${k === r.general_tolerance ? "selected" : ""}>${k}</option>`).join("");
    const html = `<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap"><label>General tolerance ISO 2768-<select id="ins_gen">${gen}</select></label>
      <label class="pm-check"><input id="ins_bal" type="checkbox" ${r.balloons ? "checked" : ""}> Balloons on the drawing</label>
      <button class="btn" id="ins_rep">Save Report...</button><button class="btn" id="ins_csv">Save CSV...</button></div>
      <p class="muted">${s.characteristics} characteristics · ${s.measured} measured · ${s.passed} pass · ${s.failed} fail - ${escapeHtml(s.status)}.
      Enter measured values (several: space separated) and press OK.</p>
      <div style="max-height:55vh;overflow:auto">${table(["#", "Location", "Characteristic", "Requirement", "Min", "Max", "Measured", "Result"], rows)}</div>`;
    const before = Object.fromEntries(r.characteristics.map((c) => [c.source, c.measured.join(" ")]));
    this.modal("Inspection - Characteristics", html, () => {
      const gen2 = $("#ins_gen").value, bal = $("#ins_bal").checked;
      const edits = [...document.querySelectorAll("[data-ic]")].filter((e) => e.value.trim() !== before[e.dataset.ic]).map((e) => [e.dataset.ic, e.value]);
      setTimeout(async () => {
        const settings = gen2 !== r.general_tolerance || bal !== r.balloons;
        if (settings) await this.run("inspection_settings", { general_tolerance: gen2, balloons: bal }).catch(() => null);
        for (const [src, v] of edits) {
          const measured = v.split(/[\s,;]+/).filter(Boolean).map(Number).filter((x) => !isNaN(x));
          await this.run("inspection_set", { characteristic: src, measured }).catch(() => null);
        }
        if (edits.length || settings) this.inspectionDialog();
      });
    }, "check");
    const save = async (format) => {
      const path = await this.askPath("save", `${this.model.name}-inspection.${format}`, [format === "html" ? "Inspection report (*.html)" : "CSV (*.csv)"], "Save Inspection Report");
      if (!path) return;
      const r2 = await this.run("inspection_report", { path, format }).catch(() => null);
      if (r2) this.toast(`Saved ${escapeHtml(r2.result.path)}`);
    };
    $("#ins_rep").onclick = () => save("html");
    $("#ins_csv").onclick = () => save("csv");
  },

  async connectorDialog() {
    const comps = (this.model.components || []).map((c) => c.name);
    if (!comps.length) return this.toast("Insert the connector components first.", "err");
    const picked = (this.selectedComponentsAny?.() || [])[0];
    const r = await this.cmdForm("Electrical - Connector", "electrical_connector", [["name", "Connector name (e.g. J1)", "text", ""],
      ["component", "Component", "select", picked || comps[0], comps], ["pins", "Pins", "number", 4],
      ["point", "Wire attach point x, y, z (blank: connection point, else centre)", "vec", ""]], { ic: "sweep" });
    if (r) this.toast(`Connector ${escapeHtml(r.connector)} on ${escapeHtml(r.component)}.`);
  },

  async harnessRouteDialog() {
    const r = await this.cmdForm("Electrical - Route Segment", "electrical_route_segment", [["points", "Points x, y, z (one per line or ; separated)", "points", "0, 0, 0; 100, 0, 0"]],
      { ic: "sweep", note: "Routes that share a point join: draw the trunk, then each branch from a point on it." });
    if (r) this.toast(`${r.routes} route segment(s); this one ${r.length.toFixed(1)} mm.`);
  },

  async fromToDialog() {
    const info = await this.evalCall("electrical_info"); if (!info) return;
    const lines = info.wires.map((w) => [w.id, w.from, w.to, w.gauge, w.color].join(", ")).join("\n");
    this.modal("Electrical - From-To List", `<p class="muted">One wire per line: id, from (connector-pin), to, gauge (e.g. 22 AWG, 0.5 mm2), colour (RD, BK, BU...).
      Connectors: ${escapeHtml(Object.keys(info.connectors).join(", ") || "none yet")}.</p>
      <textarea id="ft_list" rows="12" style="width:100%;font-family:monospace">${escapeHtml(lines || "W1, J1-1, J2-1, 22 AWG, RD")}</textarea>`, () => {
      const wires = $("#ft_list").value.split("\n").map((l) => l.split(",").map((x) => x.trim())).filter((p) => p.length >= 3 && p[1])
        .map(([id, from, to, gauge, color]) => ({ id, from, to, gauge: gauge || "22 AWG", color: color || "" }));
      setTimeout(async () => { const r = await this.run("electrical_wires", { wires, replace: true }).catch(() => null); if (r) this.toast(`${r.result.wires.length} wires in the from-to list.`); });
    }, "properties");
  },

  async harnessBuild() {
    const r = await this.run("electrical_harness", { build: true }).catch(() => null); if (!r) return;
    const h = r.result;
    const wires = table(["Wire", "From", "To", "Gauge", "Colour", "Length (mm)", "Drop V @1 A"], h.wires.map((w) =>
      `<tr>${td(w.id)}${td(w.from)}${td(w.to)}${td(w.gauge)}${td(w.color)}${td(w.length.toFixed(0), true)}${td(w.voltage_drop_V.toFixed(4), true)}</tr>`));
    const segs = table(["Segment", "Length (mm)", "Wires", "Bundle ⌀ (mm)"], h.segments.map((s, i) => `<tr>${td(i + 1)}${td(s.length.toFixed(0), true)}${td(s.wires.join(" "))}${td(s.diameter.toFixed(2), true)}</tr>`));
    this.modal("Electrical - Harness", kv([["Wires", h.wires.length], ["Total wire (m)", h.total_wire_m], ["Harness component", h.component || "-"]]) + warnings(h.warnings) + wires + segs, null, "sweep");
  },

  async formboardSave() {
    const path = await this.askPath("save", `${this.model.name}-formboard.svg`, ["SVG drawing (*.svg)"], "Save Formboard Drawing");
    if (!path) return;
    const r = await this.run("electrical_formboard", { path }).catch(() => null);
    if (r) this.toast(`Saved ${escapeHtml(r.result.path)}`);
  },

  composerAuto() {
    this.modal("Composer - Steps from Exploded View", `<label class="pm-check"><input type="radio" name="cmo" value="assembly" checked> Assembly instructions (parts laid out, then put together)</label>
      <label class="pm-check"><input type="radio" name="cmo" value="disassembly"> Disassembly (assembled, then taken apart)</label>`, () => {
      const order = document.querySelector("input[name=cmo]:checked").value;
      setTimeout(async () => { const r = await this.run("composer_auto", { order }).catch(() => null); if (r) this.toast(`${r.result.steps.length} steps: ${escapeHtml(r.result.steps.join(" › "))}`); });
    }, "explode");
  },

  async composerStep() {
    const comps = (this.model.components || []).map((c) => c.name);
    const list = (s) => String(s || "").split(",").map((x) => x.trim()).filter(Boolean);
    const r = await this.cmdForm("Composer - Add Step", "composer_add_step", [["name", "Step name", "text", "Step"],
      ["explode_steps", "Use the first N explode steps (blank: none)", "number", ""], ["hidden", `Hide components (comma separated: ${comps.join(", ")})`, "text", ""],
      ["transparent", "See-through components", "text", ""], ["view", "View", "select", "iso", ["iso", "front", "top", "right"]], ["note", "Instruction", "text", ""]],
    { ic: "explode", xf: (a) => ({ ...a, hidden: list(a.hidden), transparent: list(a.transparent), note: a.note || "" }) });
    if (r) this.toast(`Step ${r.step}: ${escapeHtml(r.name)}`);
  },

  async composerExport() {
    const path = await this.askPath("save", `${this.model.name}-instructions.html`, ["Interactive instructions (*.html)"], "Publish Composer Instructions");
    if (!path) return;
    const r = await this.run("composer_export", { path }).catch(() => null);
    if (r) this.toast(`Published ${r.result.steps} steps to ${escapeHtml(r.result.path)}`);
  },
};

export function installMfg(App) {
  for (const [k, fn] of Object.entries(MIXIN)) App.prototype[k] = fn;
}
