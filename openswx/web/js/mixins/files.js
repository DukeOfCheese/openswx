// Documents and files (new, open, save, export, import), materials, appearances, options, MCP and About.

import { icon } from "../icons.js";
import { el, escapeHtml } from "../propman.js";
import { $ } from "../lib/dom.js";

const MIXIN = {
  // New Part: a chooser registered in newPartChoosers (e.g. local or cloud) may take it over by returning true
  async newPart() {
    for (const choose of this.newPartChoosers) if (await choose.call(this) === true) return;
    await this.run("new_part"); this.vp.setView("trimetric"); this.vp.zoomFit();
  },

  bindFileInput() {
    const fi = $("#fileinput");
    this.loadFileFormats();
    fi.onchange = async () => {
      const f = fi.files[0]; if (!f) return;
      const buf = new Uint8Array(await f.arrayBuffer()); let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
      const res = await this.api("/api/upload", { filename: f.name, data: btoa(bin), mode: this.fileMode });
      fi.value = "";
      if (!res.ok) return this.toast(res.error, "err");
      if (res.result?.solidworks_file) return this.modal("SOLIDWORKS file", `<p>Read the SOLIDWORKS container (${Object.keys(res.result.solidworks_file.streams).length} streams).</p><pre class="code">${escapeHtml(JSON.stringify(res.result.solidworks_file, null, 1)).slice(0, 4000)}</pre>`, null, "open");
      await this.refresh(); this.vp.zoomFit();
    };
  },

  // The formats the server opens and exports (list_file_formats: the built-in ones and those plugins added). The
  // Open dialog's filter, the Export menu and Save As follow it; until it arrives the page's own list stands.
  async loadFileFormats() {
    const r = await this.api("/api/command", { name: "list_file_formats", args: {} }).catch(() => null);
    if (!r?.ok) return;
    this.fileFormats = r.result;
    $("#fileinput").accept = r.result.open.join(",");
  },

  pluginExports() { return (this.fileFormats?.formats || []).filter((f) => f.plugin && f.export); },

  // the export format a Save As path names by its extension (null: an openswx document)
  exportFormatFor(path) {
    const ext = (/\.[^./\\]+$/.exec(path || "")?.[0] || "").toLowerCase();
    return (this.fileFormats?.formats || []).find((f) => f.export && f.extensions.includes(ext)) || null;
  },

  openFile(mode = "open") {
    if (this.desk && mode === "open") return this.deskCall("open_file").then(() => this.vp.zoomFit());
    this.fileMode = mode; $("#fileinput").click();
  },

  // Save: to the document's file; a document never saved to a file (no path yet) gets Save As instead, as in
  // any desktop program. Cancelling the dialog leaves it unsaved, without a message.
  async save(as = false) {
    if (this.collabDoc?.()) return this.collabSave(as); // (a cloud part: kept by the server; Save As writes a local copy)
    if (this.model?.path && !as) {
      const res = await this.commandOrJob("save_part", {});
      if (res.ok) { await this.refresh(); return this.toast(`Saved ${escapeHtml(res.result.saved)}`); }
      await this.refresh();
      if (this.model?.path) return this.toast(res.error, "err"); // (a real failure: a read-only file, say)
      // (the page's model was behind: the document has no file after all, so this is a Save As)
    }
    if (this.desk) return this.deskCall("save_as");
    const def = this.model.path || `${this.model.name}.oswprt`;
    const exts = (this.fileFormats?.export || [".step", ".stl"]).join(" ");
    this.modal("Save As", `<div class="field"><label>File path on this computer</label><input id="savepath" value="${escapeHtml(def)}"></div><div class="pm-msg">Saved as an openswx document (.oswprt / .oswasm), or exported when the name ends in ${escapeHtml(exts)}.</div>`,
      async () => {
        const path = $("#savepath").value, fmt = this.exportFormatFor(path);
        const r = fmt ? await this.run("export_file", { path, format: fmt.id }) : await this.run("save_part", { path });
        if (r?.ok) this.toast(`Saved ${escapeHtml(r.result.saved || r.result.exported)}`);
      }, "save");
  },

  // a file path from the native dialog (desktop) or typed in (browser, server-side path)
  async askPath(kind, name, types, label) {
    if (this.desk) { const r = await this.desk.pick_file(kind, name, types); return r?.path || null; }
    return new Promise((res) => this.modal(label, `<div class="field"><label>File path (on this computer)</label><input id="askp" value="${escapeHtml(name)}" style="width:100%"></div>`,
      () => res($("#askp").value.trim() || null), "open"));
  },

  async importDxf() {
    const path = await this.askPath("open", "", ["DXF/DWG drawing (*.dxf;*.dwg)"], "Import DXF/DWG");
    if (!path) return;
    const plane = this.selection.find((s) => s.kind === "plane")?.id || "Front";
    const r = await this.run("import_dxf", { path, plane, sketch: this.sk.active ? this.sk.name : null });
    this.toast(`Imported ${r?.result?.result?.entities?.length ?? 0} entities into ${r?.result?.result?.sketch}`);
    this.vp.zoomFit();
  },

  async exportSketchDxf() {
    const sk = this.sk.active ? this.sk.name : this.selectedSketch();
    if (!sk) return this.toast("Select a sketch (or edit one) to export.");
    const path = await this.askPath("save", `${sk}.dxf`, ["DXF drawing (*.dxf)", "DWG drawing (*.dwg)"], "Export Sketch as DXF/DWG");
    if (path) { const r = await this.run("export_sketch_dxf", { sketch: sk, path }); this.toast(`Saved ${escapeHtml(r.result.path)}`); }
  },

  async exportFlatDxf() {
    const path = await this.askPath("save", `${this.model.name}-flat.dxf`, ["DXF drawing (*.dxf)"], "Export Flat Pattern");
    if (path) { const r = await this.run("export_flat_pattern_dxf", { path }); this.toast(`Saved ${escapeHtml(r.result.path)}`); }
  },

  async deskCall(fn, ...args) {
    let r;
    try { r = await this.desk[fn](...args); } catch (err) { // (the shell raised: say so, never fail silently)
      return this.toast(String(err?.message || err), "err");
    }
    if (r?.cancelled) return;
    if (r && !r.ok) return this.toast(r.error, "err");
    await this.refresh();
    if (r?.result?.saved || r?.result?.exported || r?.result?.path) this.toast(`Saved ${escapeHtml(r.result.saved || r.result.exported || r.result.path)}`);
  },

  exportAs(fmt) { if (this.desk) return this.deskCall("export", fmt); window.open(`/api/download?format=${fmt}`); },

  makeDrawing() { this.dwg.open(); },

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
  },

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
  },

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
  },

  optionsDialog() {
    const u = this.model?.units || "mm";
    this.modal("System Options", `<div class="field"><label>Document units</label><select id="optunits">${[["mm", "MMGS (millimetre, gram, second)"], ["cm", "CGS (centimetre, gram, second)"], ["m", "MKS (metre, kilogram, second)"], ["in", "IPS (inch, pound, second)"], ["ft", "FPS (foot, pound, second)"]]
      .map(([k, l]) => `<option value="${k}" ${k === u ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="field"><label>Polygon sides (sketch tool)</label><input id="polys" type="number" min="3" value="${this.polygonSides}"></div>
      <label class="pm-check"><input type="checkbox" id="optrel" ${this.showRelations ? "checked" : ""}> Display sketch relations</label>`, () => {
      this.polygonSides = Math.max(3, parseInt($("#polys").value) || 6); this.showRelations = $("#optrel").checked; this.vp.dirty = true;
      if ($("#optunits").value !== u) this.run("set_units", { units: $("#optunits").value });
    }, "options");
  },

  async mcpDialog() {
    const port = parseInt(location.port || "80", 10);
    const r = await this.api("/api/command", { name: "agent_connection_info", args: { port } });
    const info = r.ok ? r.result : { stdio: { command: "openswx-cli", args: ["mcp"] }, http: `${location.origin}/mcp`, claude_desktop_config: "claude_desktop_config.json" };
    const desktop = { mcpServers: { openswx: info.stdio } };
    this.modal("Connect an agent (MCP)", `<p><b>Claude Desktop</b> (and other chat apps that start local MCP servers): add this to
      <code>${escapeHtml(info.claude_desktop_config)}</code> (Claude Desktop: Settings &gt; Desktop App &gt; Extensions &gt; Manage &gt; Edit Config), then restart it.
      While openswx is open, the agent works in this window and you watch its edits live.</p>
      <pre class="code">${escapeHtml(JSON.stringify(desktop, null, 1))}</pre>
      <p><b>Claude Code</b> (and clients that reach this computer over HTTP):</p>
      <pre class="code" id="mcp-cc">claude mcp add --transport http openswx ${escapeHtml(location.origin)}/mcp --header "Authorization: Bearer <span id="mcp-tok">••••••••</span>"</pre>
      ${info.token ? `<div style="display:flex;gap:8px;margin:6px 0 10px"><button class="btn" id="mcp-show">Show token</button><button class="btn" id="mcp-copy">Copy command</button></div>` : ""}
      <p>The token is your openswx secret (keep it to yourself): requests without it are refused. In a terminal,
      <code>${escapeHtml(info.token_command || "openswx token")}</code> prints it.</p>
      <p><b>Claude on the web (claude.ai)</b> connects to MCP servers from Anthropic's servers, which cannot reach an app on
      this computer: use Claude Desktop or Claude Code.</p>`, null, "agent");
    // the Claude Code block shows the token masked; its Copy button copies the command with the real token
    const tok = $("#mcp-tok"), show = $("#mcp-show"), cc = $("#mcp-cc");
    if (info.token) cc.dataset.copy = `claude mcp add --transport http openswx ${location.origin}/mcp --header "Authorization: Bearer ${info.token}"`;
    else tok.textContent = "<token>";
    const reveal = (on) => { tok.textContent = on ? info.token : "••••••••"; show.textContent = on ? "Hide token" : "Show token"; };
    if (show) show.onclick = () => reveal(tok.textContent !== info.token);
    this.addCopyButtons($("#modal-box .mb-body"), (c) => { if (c === cc && show) reveal(true); }); // (copy blocked: show the token to select)
    const copy = $("#mcp-copy"); // (the same copy as the block's own button, on the row under it)
    if (copy) copy.onclick = () => cc.parentElement.querySelector(".copy-btn").click();
  },

  // a Copy button on every code block and inline code in `root` (dialogs that show commands or config to paste).
  // It copies the element's data-copy when it has one (e.g. a command whose secret is shown masked), else its text;
  // onFail(element) runs when the clipboard refuses, so the dialog can help the user select the text instead.
  addCopyButtons(root, onFail = null) {
    for (const c of root.querySelectorAll("pre.code, code")) {
      const b = el("button", "copy-btn", "Copy"); b.type = "button"; b.title = "Copy to the clipboard";
      b.onclick = async () => {
        const ok = await this.copyText(c.dataset.copy ?? c.textContent);
        if (!ok) onFail?.(c);
        b.textContent = ok ? "Copied" : "Select and copy"; b.classList.toggle("done", ok);
        setTimeout(() => { b.textContent = "Copy"; b.classList.remove("done"); }, 1500);
      };
      if (c.tagName === "PRE") { const w = el("div", "copy-wrap"); c.replaceWith(w); w.append(c, b); } else c.after(b);
    }
  },

  async copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* (no clipboard API here: the old way) */ }
    const t = el("textarea", ""); t.value = text; t.style.cssText = "position:fixed;opacity:0"; document.body.append(t); t.select();
    try { return document.execCommand("copy"); } catch { return false; } finally { t.remove(); }
  },

  async checkUpdates() {
    const r = await this.api("/api/command", { name: "check_for_updates", args: {} });
    if (!r.ok) return this.toast(r.error, "err");
    const u = r.result;
    this.modal("Check for Updates", `<p>This is openswx ${escapeHtml(u.current)}.</p><p>${escapeHtml(u.error || u.message)}</p>${u.update_available && u.url ? `<p><a href="${escapeHtml(u.url)}" target="_blank" rel="noopener">${escapeHtml(u.url)}</a></p>` : ""}`, null, "help");
  },

  async diagnosticReport() {
    const path = await this.askPath("save", "openswx-report.zip", ["Zip archive (*.zip)"], "Save Diagnostic Report");
    if (!path) return;
    const r = await this.api("/api/command", { name: "save_diagnostic_report", args: { path } });
    if (r.ok) this.toast(`Saved ${escapeHtml(r.result.saved)} (log, versions, recent commands; no documents).`); else this.toast(r.error, "err");
  },

  about() { this.modal("About openswx", `<p><b>openswx</b> 0.1, open-source parametric CAD with a familiar layout and a built-in MCP server for agents.</p><p>Geometry: OpenCASCADE (via build123d/OCP). Not affiliated with Dassault Systèmes.</p><p>Licensed under the Apache License 2.0; third-party notices are in the NOTICE file that comes with openswx.</p>`, null, "help"); },
};

export function installFiles(App) {
  Object.assign(App.prototype, MIXIN);
}
