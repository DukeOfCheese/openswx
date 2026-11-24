// Overlays (dimensions, handles, plane labels), the task pane, simulation studies, and the review tools.

import { icon } from "../icons.js";
import { el, escapeHtml, UNITS } from "../propman.js";
import { SHORTCUTS, DEFAULT_KEYS } from "../keys.js";
import { $ } from "../lib/dom.js";
import { cap, massHtml } from "../lib/helpers.js";

const MIXIN = {
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
  },

  renderPlaneLabels() {
    const host = this.overlayLabels; host.innerHTML = "";
    for (const [name, g] of this.vp.planeObjs) {
      if (!g.visible) continue;
      const [x, y] = this.vp.toScreen(g.userData.corner);
      const l = el("div", "plane-label", ["Front", "Top", "Right"].includes(name) ? `${name} Plane` : name); l.style.left = `${x + 4}px`; l.style.top = `${y + 2}px`; host.append(l);
    }
  },

  renderStatus() {
    $("#sb-units").textContent = { mm: "MMGS", cm: "CGS", m: "MKS", in: "IPS", ft: "FPS" }[this.model?.units || "mm"] || "MMGS";
    if (!this.sk.active) $("#sb-editing").textContent = this.model?.kind === "assembly" ? "Editing Assembly" : "Editing Part";
    const st = $("#sb-state"); st.className = "";
    if (this.sk.active) {
      const s = this.sk.detail?.sketch?.status?.state || this.model.features.find((f) => f.name === this.sk.name)?.sketch_state;
      st.textContent = (s === "fully_defined" ? "Fully Defined" : s === "over_defined" ? "Over Defined" : "Under Defined") + (this.sk.detail?.sketch?.status?.dangling?.length ? " (Dangling)" : "");
      st.className = s === "fully_defined" ? "full" : s === "over_defined" ? "over" : "under";
    } else st.textContent = Object.keys(this.model.errors || {}).length ? `Rebuild errors: ${Object.keys(this.model.errors).length}` : "";
  },

  buildTaskPane() {
    const keys = { people: "people", agent: "agent", props: "mass", help: "keyboard" };
    document.querySelectorAll(".tp-tab").forEach((b) => { b.innerHTML = icon(keys[b.dataset.pane]); b.onclick = () => this.showTask(b.dataset.pane); });
    this.renderHelpPane();
  },

  // Task Pane > Shortcuts: the mouse and keys in use (User Settings, Tools > Customize Keyboard)
  renderHelpPane() {
    const mouse = { solidworks: [["Rotate", "Middle drag / Alt+Left drag"], ["Pan", "Ctrl+Middle drag"], ["Zoom", "Wheel / Shift+Middle drag"]],
      onshape: [["Rotate", "Right drag / Alt+Left drag"], ["Pan", "Middle drag / Ctrl+Right drag"], ["Zoom", "Wheel"]],
      fusion: [["Rotate", "Shift+Middle drag / Alt+Left drag"], ["Pan", "Middle drag"], ["Zoom", "Wheel"]],
      blender: [["Rotate", "Middle drag / Alt+Left drag"], ["Pan", "Shift+Middle drag"], ["Zoom", "Wheel / Ctrl+Middle drag"]] }[this.vp.mouse?.preset] || [];
    const keys = this.keys || DEFAULT_KEYS;
    const rows = [...mouse, ...SHORTCUTS.filter(([id]) => keys[id]).map(([id, label]) => [label, keys[id]]),
      ["Smart Dimension", "D (in sketch)"], ["Line / Rectangle / Circle", "L / R / C (in sketch)"], ["Repeat last command", "Enter"],
      ["Mouse gestures", this.vp.mouse?.preset === "onshape" ? "(off: the right button turns the view)" : "Right drag"],
      ["Box select (window / crossing)", "Left-drag right / left"], ["Select Other", "Right-click"],
      ["Hide / show component", "Tab / Shift+Tab (assembly)"], ["Turn / pan the view", "Arrows / Ctrl+Arrows"], ["Cancel", "Esc"], ["Delete", "Del"]];
    $("#tp-help").innerHTML = `<div class="tp-h">${icon("keyboard")} Shortcuts</div><div class="kv">
      ${rows.map(([a, b]) => `<div>${escapeHtml(a)}</div><div>${escapeHtml(b)}</div>`).join("")}</div>
      <p class="muted" style="margin-top:8px">Change them in Tools &gt; Customize Keyboard and Tools &gt; User Settings.</p>`;
  },

  showTask(p) {
    const tp = $("#taskpane");
    const same = !tp.classList.contains("collapsed") && document.querySelector(`.tp-tab[data-pane="${p}"]`).classList.contains("active");
    tp.classList.toggle("collapsed", same);
    document.querySelectorAll(".tp-tab").forEach((b) => b.classList.toggle("active", !same && b.dataset.pane === p));
    document.querySelector(`.tp-tab[data-pane="${p}"] .dot`)?.remove();
    document.querySelectorAll(".tp-tab").forEach((b) => b.setAttribute("aria-pressed", String(!same && b.dataset.pane === p)));
    for (const id of ["people", "agent", "props", "help"]) $(`#tp-${id}`).classList.toggle("hidden", id !== p);
    requestAnimationFrame(() => this.vp.resize()); this.renderTaskPane();
  },

  async renderTaskPane() {
    if ($("#taskpane").classList.contains("collapsed")) return;
    if (!$("#tp-people").classList.contains("hidden")) this.collabRenderPeople(this.collabDoc?.() || null);
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
  },

  async simFixture() {
    const faces = this.selection.filter((s) => s.kind === "face");
    if (!faces.length) return this.toast("Select the face(s) to fix, then Fixed Geometry.");
    for (const f of faces) await this.run("simulation_add_fixture", { face: f.id });
    this.toast(`Fixed geometry on ${faces.length} face(s). Study: ${this.model.study?.fixtures?.length || 0} fixture(s), ${this.model.study?.loads?.length || 0} load(s).`);
  },

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
  },

  async simRun() {
    this.toast("Meshing and solving...");
    const r = await this.evalCall("simulation_run"); if (!r) return;
    this.simLast = r;
    this.modal("Static Study - Results", `<div class="kv"><div>Mesh</div><div>${r.elements} ${(r.mesh || "").startsWith("tetra") ? "quadratic tetrahedra, curvature-based" : "hex elements (voxel)"}, ${r.nodes} nodes (size ${r.element_size.toFixed(3)} mm)</div>
      <div>Max displacement</div><div><b>${r.max_displacement.toExponential(4)} mm</b></div>
      <div>Max von Mises stress</div><div><b>${r.max_von_mises.toFixed(3)} MPa</b></div>
      <div>Yield strength</div><div>${r.yield_strength || "-"} MPa</div>
      <div>Factor of safety</div><div><b style="color:${(r.factor_of_safety ?? 9) < 1 ? "#d0312d" : "#2f9e44"}">${r.factor_of_safety?.toFixed(3) ?? "-"}</b></div>
      <div>Reaction (N)</div><div>${r.reaction.map((v) => v.toFixed(2)).join(", ")}</div></div>`, () => this.simPlot("stress"), "sim_run");
  },

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
  },

  async modeStudy(kind) {
    this.toast(kind === "frequency" ? "Solving frequency study..." : "Solving buckling study...");
    const r = await this.evalCall(kind === "frequency" ? "frequency_run" : "buckling_run", { modes: 5 }); if (!r) return;
    const vals = kind === "frequency" ? r.frequencies_hz : r.load_factors;
    this.modal(kind === "frequency" ? "Frequency Study - Results" : "Buckling Study - Results",
      `<p>${r.elements} elements (size ${r.element_size.toFixed(3)} mm)${kind === "frequency" ? `, mass ${r.mass_kg.toFixed(4)} kg` : ""}</p>
      <div class="kv">${vals.map((v, i) => `<div>Mode ${i + 1}</div><div><a href="#" data-mode="${i + 1}">${kind === "frequency" ? `${v.toFixed(2)} Hz` : `load factor ${v.toFixed(3)}`}</a></div>`).join("")}</div>
      <p style="margin-top:8px">Click a mode to show its shape.</p>`, null, kind === "frequency" ? "sim_frequency" : "sim_buckling");
    document.querySelectorAll("#modal [data-mode]").forEach((a) => { a.onclick = (e) => { e.preventDefault(); this.showModeShape(kind, +a.dataset.mode); }; });
  },

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
  },

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
  },

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
  },

  thermalResults(r) {
    const kv = `<div class="kv"><div>Mesh</div><div>${r.elements} elements (size ${r.element_size.toFixed(3)} mm)</div>
      <div>Max temperature</div><div><b>${r.max_temperature.toFixed(3)} °C</b></div><div>Min temperature</div><div><b>${r.min_temperature.toFixed(3)} °C</b></div>
      ${r.history ? `<div>Time step</div><div>${r.time_step.toPrecision(4)} s</div>` : (r.heat_in_per_temperature_boundary || []).map((q, i) => `<div>Heat in, temperature ${i + 1}</div><div>${q.toFixed(4)} W</div>`).join("")}</div>`;
    this.modal("Thermal Study - Results", kv + (r.history ? this.timeChart(r.history) : ""), () => this.thermalPlot(), "thermal_run");
  },

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
  },

  async thermalPlot() {
    const r = await this.evalCall("thermal_results"); if (!r) return;
    this.vp.setDeformation(null);
    this.vp.setFaceGeometry(r.faces);
    const all = Object.values(r.temperature).flat(), lo = Math.min(...all), hi = Math.max(...all);
    const rgb = (t) => { t = Math.max(0, Math.min(1, t)); return t < 0.25 ? [0, 4 * t, 1] : t < 0.5 ? [0, 1, 2 - 4 * t] : t < 0.75 ? [4 * t - 2, 1, 0] : [1, 4 - 4 * t, 0]; };
    this.vp.setAnalysis(Object.fromEntries(Object.entries(r.temperature).map(([id, v]) => [id, v.map((x) => rgb((x - lo) / (hi - lo || 1)))])));
    this.vp.setLegend({ title: "Temperature (°C)", unit: r.history ? `t = ${r.history[r.history.length - 1].time.toPrecision(4)} s` : "steady state", min: lo, max: hi, color: (t) => `rgb(${rgb(t).map((c) => Math.round(c * 255)).join(",")})` });
  },

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
  },

  async evalCall(name, args = {}) { const r = await this.commandOrJob(name, args); if (!r.ok) { this.toast(r.error, "err"); return null; } return r.result; },

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
  },

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
  },

  thicknessDialog() {
    if (this.vp.analysisOn) { this.vp.setAnalysis(null); return; }
    this.modal("Thickness Analysis", `<div class="field"><label>Target thickness (mm)</label><input id="tat" type="number" value="1" step="0.25"></div>
      <p><span style="color:#d0312d">■</span> thinner than the target &nbsp; <span style="color:#2f9e44">■</span> at least the target</p>`, async () => {
      const r = await this.evalCall("thickness_analysis", { target: parseFloat($("#tat").value) || 1 });
      if (!r) return;
      this.vp.setAnalysis(Object.fromEntries(Object.entries(r.faces).map(([id, f]) => [id, f.thin ? 0xd0312d : 0x2f9e44])));
      this.toast(`Thickness: minimum ${r.min_thickness} mm; ${r.thin_faces.length} face(s) thinner than ${r.target} mm`);
    }, "measure");
  },

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
  },

  async clearanceDialog() {
    this.modal("Clearance Verification", `<div class="field"><label>Minimum acceptable clearance (mm)</label><input id="cvm" type="number" value="1" step="0.5"></div>`, async () => {
      const r = await this.api("/api/command", { name: "clearance_verification", args: { min_clearance: parseFloat($("#cvm").value) || 0 } });
      if (!r.ok) return this.toast(r.error, "err");
      const v = r.result.violations;
      setTimeout(() => this.modal("Clearance Verification", v.length ? `<div class="kv">${v.map((p) => `<div>${escapeHtml(p.components.join(" ↔ "))}</div><div>${p.distance.toFixed(3)} mm</div>`).join("")}</div>`
        : `<p>No clearances below ${r.result.min_clearance} mm (closest: ${r.result.closest ? r.result.closest.distance.toFixed(3) + " mm" : "-"}).</p>`, null, "interference"));
    }, "interference");
  },

  toggleZebra() { this.vp.setZebra(!this.vp.zebraOn); },

  async toggleCurvature() {
    if (this.vp.analysisOn) { this.vp.setAnalysis(null); return; }
    const r = await this.evalCall("curvature_analysis"); if (!r) return;
    const kmax = r.max || 1;
    const ramp = (k) => { const t = Math.min(1, Math.log1p(9 * k / kmax) / Math.log(10)); // blue (flat) -> green -> red (tight)
      return t < 0.5 ? [0, 2 * t, 1 - 2 * t] : [2 * t - 1, 2 - 2 * t, 0]; };
    this.vp.setAnalysis(Object.fromEntries(Object.entries(r.faces).map(([id, ks]) => [id, ks.map(ramp)])));
    this.toast(`Curvature: max ${kmax.toFixed(4)} 1/mm (min radius ${(1 / kmax).toFixed(3)} mm)`);
  },

  async checkGeometry() {
    const r = await this.evalCall("check_geometry"); if (!r) return;
    this.modal("Check Entity", `<div class="kv"><div>Result</div><div><b style="color:${r.valid ? "#2f9e44" : "#d0312d"}">${r.valid ? "No invalid geometry found" : "Problems found"}</b></div>
      <div>Solids / faces / edges</div><div>${r.solids} / ${r.faces} / ${r.edges}</div>
      <div>Minimum edge length</div><div>${r.min_edge_length ?? "-"} mm</div><div>Minimum radius of curvature</div><div>${r.min_radius ?? "-"} mm</div></div>
      ${r.issues.length ? `<ul>${r.issues.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>` : ""}`, null, "check");
  },

  async bodyInterference() {
    const r = await this.evalCall("body_interference"); if (!r) return;
    this.modal("Body Interference", r.interferences.length ? `<div class="kv">${r.interferences.map((i) => `<div>Bodies ${i.bodies.join(" & ")}</div><div>${i.volume.toFixed(3)} mm³</div>`).join("")}</div>`
      : `<p>No interference between the ${r.bodies} bodies.</p>`, null, "interference");
  },

  massProps() {
    this.api("/api/command", { name: "get_mass_properties", args: {} }).then((r) => {
      if (r.ok) this.modal(`Mass Properties - ${this.model.name}`, massHtml(r.result, true), null, "mass");
    });
  },

  // Measure, as SOLIDWORKS: a panel that stays open and measures whatever is selected (one or two faces, edges
  // or vertices; Ctrl+click for two); the selection filter's V picks vertices
  measure() {
    if (this.measureOpen) return this.closeMeasure();
    this.measureOpen = true;
    const p = el("div", "measure-panel"); p.id = "measure-panel";
    p.innerHTML = `<div class="mp-head">${icon("measure")}<span>Measure</span><button class="mp-x" title="Close">✕</button></div><div class="mp-body"></div>`;
    p.querySelector(".mp-x").onclick = () => this.closeMeasure();
    this.graphics.append(p); this.updateMeasure();
  },

  closeMeasure() { this.measureOpen = false; $("#measure-panel")?.remove(); },

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
  },

  async sectionPropsDialog() {
    const planes = ["Front", "Top", "Right", ...(this.model.features || []).filter((f) => f.type === "plane").map((f) => f.name)];
    this.modal("Section Properties", `<div class="field"><label>Plane</label><select id="spp">${planes.map((n) => `<option>${escapeHtml(n)}</option>`).join("")}</select></div>
      <div class="field"><label>Offset (mm)</label><input id="spo" type="number" value="0"></div><div id="spr"></div>`, async () => {
      const r = await this.api("/api/command", { name: "section_properties", args: { plane: $("#spp").value, offset: parseFloat($("#spo").value) || 0 } });
      if (!r.ok) { this.toast(r.error, "err"); throw new Error(r.error); }
      const kv = Object.entries(r.result).map(([k, v]) => `<div>${escapeHtml(cap(k))}</div><div>${Array.isArray(v) ? v.map((x) => (+x).toFixed(4)).join(", ") : typeof v === "number" ? v.toFixed(4) : escapeHtml(JSON.stringify(v))}</div>`).join("");
      setTimeout(() => this.modal("Section Properties", `<div class="kv">${kv}</div>`, null, "section"));
    }, "section");
  },

  async sheetMetalInfo() {
    const r = await this.evalCall("sheet_metal_info"); if (!r) return;
    const kv = Object.entries(r).map(([k, v]) => `<div>${escapeHtml(cap(k))}</div><div>${typeof v === "number" ? (+v).toFixed(4) : escapeHtml(typeof v === "object" ? JSON.stringify(v) : String(v))}</div>`).join("");
    this.modal("Sheet Metal", `<div class="kv">${kv}</div>`, null, "base_flange");
  },
};

export function installPanels(App) {
  Object.assign(App.prototype, MIXIN);
}
