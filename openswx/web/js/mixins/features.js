// Feature dialogs and the PropertyManager lifecycle, sketch modes and view commands for the selection.
import { icon } from "../icons.js";
import { el, escapeHtml, SPECS } from "../propman.js";
import { $ } from "../lib/dom.js";
import { FEATURE_TO_SPEC, CURVE_TYPES } from "../lib/constants.js";
import { cap, applicableRelations, planeBasis } from "../lib/helpers.js";

const MIXIN = {
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
    if (["unfold", "fold"].includes(key)) { // bends picked in the tree before the command
      const bends = this.selection.filter((s) => s.kind === "feature" && ["edge_flange", "hem", "sketched_bend", "jog"].includes(s.type));
      initial = { bends: bends.map((s) => ({ kind: "feature", label: s.id, value: s.id })), ...initial };
    }
    if (key === "gusset") initial = { faces: this.selection.filter((s) => s.kind === "face").slice(0, 2).map((s) => ({ kind: "face", label: s.id, value: s.id })), ...initial };
    if (["sketched_bend", "planar_surface", "revolved_surface"].includes(key) && skItems.length) initial = { sketch: skItems.slice(0, 1), ...initial };
    if (key === "filled_surface" && skItems.length) initial = { boundary: skItems.slice(0, 1), ...initial };
    if (key === "swept_surface" && skItems.length) initial = { profile: skItems.slice(0, 1), path: skItems.slice(1, 2), ...initial };
    if (key === "lofted_surface" && skItems.length) initial = { profiles: skItems, ...initial };
    const feats = this.selection.filter((s) => s.kind === "feature").map((s) => ({ kind: "feature", label: s.id, value: s.id }));
    if (key === "knit_surface" && feats.length) initial = { surfaces: feats, ...initial };
    if (["trim_surface", "offset_surface"].includes(key) && feats.length) initial = { surface: feats.slice(0, 1), ...initial };
    if (key === "end_cap" && feats.length) initial = { member: feats.slice(0, 1), ...initial };
    if (spec?.prefill) initial = { ...spec.prefill(this.selection), ...initial };
    this.openPM(spec, { initial });
  },

  openPM(spec, { initial = {}, edit = null } = {}) {
    this.editing = edit;
    this.pm.open(spec, {
      initial, title: edit ? `${edit}` : spec.title,
      onChange: (args) => this.schedulePreview(args), onOK: (args) => this.pmOK(args), onCancel: () => this.pmCancel(),
      browse: (types) => this.askPath("open", "", types, "Open"),
    });
    this.showPane("pm"); this.renderConfirm();
    this.schedulePreview(this.pm.args());
  },

  schedulePreview(args) {
    this.collabPanelDraft(args); // (a cloud document: the others see it as a ghost)
    clearTimeout(this.previewTimer);
    this.previewTimer = setTimeout(async () => {
      if (!this.pm.active) return;
      const spec = this.pm.spec;
      if (spec.noPreview) return; // commands that are not model features (or add several) have no preview
      const cmd = spec.commandFor ? spec.commandFor(this.pm.values) : spec.command;
      const req =this.editing ? { name: "redefine_feature", args: { name: this.editing, command_name: cmd, args } } : { name: cmd, args };
      const res = await this.api("/api/preview", req);
      if (!this.pm.active) return;
      // like SOLIDWORKS: until something is picked the PropertyManager shows its prompt, not an error
      const selFields = (spec.groups || []).flatMap((g) => (g.fields || []).filter((f) => f.type === "selection"));
      const nothingPicked = selFields.length > 0 && selFields.every((f) => !(this.pm.values[f.key] || []).length);
      if (res.ok) { this.vp.setPreview(res); this.pm.setMessage(spec.message); }
      else if (nothingPicked) { this.vp.setPreview(null); this.pm.setMessage(spec.message); }
      else { this.vp.setPreview(null); this.pm.setMessage(res.error, true); }
    }, 220);
  },

  async pmOK(args = this.pm.args()) {
    const spec = this.pm.spec;
    try {
      const cmd = spec.commandFor ? spec.commandFor(this.pm.values) : spec.command;
      if (this.editing) await this.run("redefine_feature", { name: this.editing, command_name: cmd, args }, { quiet: true });
      else await this.run(cmd, args, { quiet: true });
    } catch (err) { this.pm.setMessage(String(err.message || err), true); return; }
    this.closePM();
  },

  pmCancel() { this.closePM(); },

  closePM() {
    clearTimeout(this.previewTimer); this.collabPanelClosed();
    this.pm.close(); this.editing = null; this.vp.setPreview(null); this.showPane("tree"); this.renderConfirm(); this.setSelection([]);
  },

  async editFeature(name) {
    const f = this.model.features.find((x) => x.name === name);
    if (!f) return;
    if (f.type === "sketch") return this.enterSketch(name);
    const key = FEATURE_TO_SPEC[f.type];
    if (!key) return this.toast(`${f.type} features cannot be edited in the UI yet`, "err");
    if (!(await this.collabLock(name, "pm"))) return; // (a cloud document: the panel is yours alone while open)
    const res = await this.api("/api/command", { name: "get_feature", args: { name } });
    if (!res.ok) { this.collabRelease("pm"); return; }
    const spec = SPECS[key];
    this.openPM(spec, { initial: spec.fromParams(res.result.params), edit: name });
  },

  cancelAll() {
    if (this.pm.active) this.closePM();
    if (this.awaitPlane) this.endPlanePick();
    if (this.sk.active) this.setSketchTool("select");
    this.setSelection([]); this.prompt("");
  },

  // ================================================================== sketch mode
  sketchCommand() {
    const s = this.selection[0];
    if (s && (s.kind === "face" || s.kind === "plane")) return this.createSketch(s.id);
    if (s && s.kind === "feature" && (s.type === "plane" || s.type === "csys")) return this.createSketch(s.id);
    if (s && s.kind === "feature" && s.type === "sketch") return this.enterSketch(s.id);
    this.followUp = null; this.startPlanePick();
  },

  startPlanePick() {
    this.awaitPlane = true; this.vp.showPlanes(["Front", "Top", "Right"]);
    this.vp.setView("trimetric"); this.vp.zoomFit();
    this.prompt("Select a plane on which to create a sketch for the entity (or a planar face).");
    this.renderConfirm();
  },

  endPlanePick() { this.awaitPlane = false; this.vp.showPlanes([]); this.prompt(""); },

  async planePicked(pick) {
    if (!pick) return;
    this.endPlanePick();
    await this.createSketch(pick.id);
  },

  async createSketch(plane) {
    try {
      const res = await this.run("create_sketch", { plane });
      this.newSketch = res.result.sketch;
      await this.enterSketch(res.result.sketch);
      const t = this.pendingTool || "select"; this.pendingTool = null;
      this.setSketchTool(t);
    } catch { /* toast already shown */ }
  },

  async enterSketch(name) {
    if (!(this.sk.active && this.sk.name === name) && !(await this.collabLock(name, "sketch"))) return; // (a cloud document: yours alone while open)
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
  },

  async exitSketch(silent = false, discard = false) {
    if (!this.sk.active) return;
    const name = this.sk.name;
    const isNew = this.newSketch === name;
    this.sk.exit(); this.newSketch = null; this.prompt(""); this.status.coords(null); this.collabRelease("sketch", name); // (only this sketch's lock: another may be taken already)
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
  },

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
  },

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
  },

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
  },

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
  },

  relationsHint() { if (!this.sk.active) return this.toast("Edit a sketch first, then select sketch entities to add relations."); this.setSketchTool("select"); this.prompt("Select sketch entities; applicable relations appear in the PropertyManager."); },

  async toggleConstruction() {
    if (!this.sk.active) return;
    const ents = this.selection.filter((s) => s.kind === "sketch");
    if (!ents.length) return this.toast("Select sketch entities first");
    const E = this.sk.detail.sketch.entities;
    await this.run("sketch_set_construction", { sketch: this.sk.name, entities: ents.map((s) => s.id), construction: !ents.every((s) => E[s.id]?.construction) });
  },

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
  },

  async deleteSelection() {
    if (this.sk.active) {
      for (const id of this.selDims) await this.run("sketch_delete", { sketch: this.sk.name, item: id }).catch(() => {});
      this.selDims.clear();
      for (const s of this.selection.filter((x) => x.kind === "sketch")) await this.run("sketch_delete", { sketch: this.sk.name, item: s.id }).catch(() => {});
      this.setSelection([]); return;
    }
    const f = this.selection.find((s) => s.kind === "feature");
    if (f) this.deleteFeature(f.id);
  },

  suppressSelected(on) { const f = this.selection.find((s) => s.kind === "feature"); if (f) this.run("suppress_feature", { name: f.id, suppressed: on }); },

  // ================================================================== view helpers
  normalTo() {
    if (this.sk.active) { const ov = this.mesh.sketches.find((s) => s.name === this.sk.name); if (ov) return this.vp.normalTo(ov.z, ov.x); }
    const s = this.selection[0];
    if (s?.kind === "face") return this.normalToFace(s);
    if (s?.kind === "plane" || (s?.kind === "feature" && s.type === "plane")) return this.normalToPlane(s.id);
    this.vp.setView("front");
  },

  normalToPlane(name) { const p = (this.mesh.planes || []).find((q) => q.name === name); if (p) this.vp.normalTo(p.z, p.x); },

  normalToFace(pick) {
    const n = pick.normal || [0, 0, 1];
    const { x } = planeBasis(n); this.vp.normalTo(n, x);
  },
};

export function installFeatures(App) {
  Object.assign(App.prototype, MIXIN);
}
