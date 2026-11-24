import { icon } from "../icons.js";
import { el, escapeHtml, SPECS } from "../propman.js";
import { $ } from "../lib/dom.js";
import { TYPE_ICON, CURVE_TYPES } from "../lib/constants.js";

const MIXIN = {
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
      const r = row({ icon: f.suppressed ? "suppress" : TYPE_ICON[f.type] || "part", label, depth, error: f.error, title: f.error || f.type,
        children: kids.length > 0, open, toggle: () => { open ? this.expanded.delete(f.name) : this.expanded.add(f.name); this.renderTree(); },
        selected: this.selection.some((s) => s.kind === "feature" && s.id === f.name), feature: depth < 1 ? f.name : null,
        cls: [cls, this.relatedFeatures?.has(f.name) ? "related" : ""].join(" "),
        hover: (on) => this.hoverFeature(on ? f.name : null),
        click: (e) => this.treeClick(f, e), dbl: () => this.showFeatureDims(f.name), ctx: (e) => this.featureMenu(e, f) });
      this.collabTreeBadge(r, f); // (cloud documents: who is editing it, who broke it)
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
  },

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
  },

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
  },

  editMateValue(mt) {
    this.modal(`Edit ${mt.name}`, `<div class="field"><label>${mt.type === "angle" ? "Angle (°)" : "Distance (mm)"}</label><input id="mv" type="number" value="${mt.value}"></div>`,
      async () => { await this.run("edit_mate", { name: mt.name, value: parseFloat($("#mv").value) }); }, "mate");
  },

  selectedComponents() { return this.selection.filter((s) => s.kind === "component").map((s) => s.id); },

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
  },

  mirrorComponentsDialog() {
    const comps = this.selectedComponents();
    if (!comps.length) return this.toast("Select the component(s) to mirror in the tree first.");
    this.modal("Mirror Components", `<p>Components: <b>${comps.map(escapeHtml).join(", ")}</b> (opposite-hand copies)</p>
      <div class="field"><label>Mirror plane</label><select id="mcp"><option>Right</option><option>Front</option><option>Top</option></select></div>
      <div class="field"><label>Plane origin (x, y, z)</label><input id="mco" value="0, 0, 0"></div>`, async () => {
      await this.run("mirror_components", { components: comps, plane: $("#mcp").value, plane_origin: $("#mco").value.split(",").map(Number) });
    }, "mirror");
  },

  explodeDialog() {
    const comps = this.selectedComponents();
    if (!comps.length) return this.toast("Select the component(s) to move in this explode step.");
    this.modal("Explode Step", `<p>Components: <b>${comps.map(escapeHtml).join(", ")}</b></p>
      <div class="field"><label>Direction (x, y, z)</label><input id="exd" value="0, 1, 0"></div>
      <div class="field"><label>Distance (mm)</label><input id="exs" type="number" value="50"></div>`, async () => {
      await this.run("explode_step", { components: comps, direction: $("#exd").value.split(",").map(Number), distance: parseFloat($("#exs").value) });
    }, "explode");
  },

  async toolboxDialog() {
    const cat = (await this.run("list_fasteners", {}, { quiet: true }).catch(() => null))?.result || {};
    const names = { hex_bolt: "Hex bolt", socket_cap_screw: "Socket head cap screw", button_head_screw: "Button head screw",
      countersunk_screw: "Countersunk screw", set_screw: "Set screw", hex_nut: "Hex nut", thin_nut: "Thin hex nut", washer: "Plain washer",
      lock_washer: "Spring lock washer", dowel_pin: "Dowel pin", ansi_hex_bolt: "Hex cap screw", ansi_socket_cap_screw: "Socket head cap screw",
      ansi_hex_nut: "Hex nut", ansi_washer: "Flat washer" };
    const fams = [...new Set(Object.values(cat).map((v) => v.family || "ISO"))];
    const opts = fams.map((f) => `<optgroup label="${f}">${Object.entries(cat).filter(([, v]) => (v.family || "ISO") === f)
      .map(([k, v]) => `<option value="${k}">${names[k] || k} (${v.standard})</option>`).join("")}</optgroup>`).join("");
    this.modal("Toolbox - Hardware", `<div class="field"><label>Type</label><select id="tbk">${opts}</select></div>
      <div class="field"><label>Size</label><select id="tbs"></select></div>
      <div class="field" id="tblen"><label id="tbll">Length (mm)</label><input id="tbl" type="number" value="20" step="any"></div>
      <div class="field"><label>Position X, Y, Z (mm)</label><input id="tbp" value="0, 0, 0"></div>`, async () => {
      const k = $("#tbk").value;
      await this.run("insert_fastener", { kind: k, size: $("#tbs").value, length: cat[k].needs_length ? parseFloat($("#tbl").value) : null, position: $("#tbp").value.split(",").map(Number) });
    }, "toolbox");
    const upd = () => {
      const c = cat[$("#tbk").value], inch = c.length_unit === "in";
      $("#tbs").innerHTML = c.sizes.map((s) => `<option ${["M8", "1/4-20", "6"].includes(s) ? "selected" : ""}>${s}</option>`).join("");
      $("#tblen").style.display = c.needs_length ? "" : "none";
      $("#tbll").textContent = `Length (${inch ? "in" : "mm"})`;
      $("#tbl").value = inch ? 1 : 20;
    };
    $("#tbk").onchange = upd; upd();
  },

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
  },

  async smartFasteners() {
    const comps = this.selectedComponents();
    if (!comps.length) return this.toast("Select the component with the holes, then Smart Fasteners.");
    for (const c of comps) {
      const r = await this.run("smart_fasteners", { component: c }).catch(() => null);
      if (r) this.toast(`Inserted ${r.result.fasteners.length} fastener(s): ${r.result.fasteners.map((f) => `${f.size}x${f.length}`).join(", ")}`);
    }
  },

  async newAssembly() { await this.run("new_assembly"); this.insertComponentDialog(); },

  insertComponentDialog() {
    const parts = (this.docs?.documents || []).filter((d) => d.name !== this.model.name);
    this.modal("Insert Components", `<p>Open documents:</p><div class="pm-list">${parts.map((d) => `<button class="pm-rel" data-doc="${escapeHtml(d.name)}">${icon("part")}<span>${escapeHtml(d.name)}</span></button>`).join("") || "<i>No other open parts</i>"}</div>
      <div class="field" style="margin-top:10px"><label>…or a file path (.oswprt / .step / .x_t / .sldprt)</label><input id="compfile" placeholder="C:\\path\\part.oswprt"></div>
      <div class="field"><label>Position X, Y, Z (mm): blank to click where it goes</label><input id="comppos" value=""></div>`,
      async () => { const f = $("#compfile").value.trim(); if (f) this.startPlacing(f, $("#comppos").value); }, "insert_comp");
    document.querySelectorAll("#modal [data-doc]").forEach((b) => { b.onclick = () => { const pos = $("#comppos").value; $("#modal").classList.add("hidden"); this.startPlacing(b.dataset.doc, pos); }; });
  },

  // Insert Components as in SOLIDWORKS: the first one goes at the origin (fixed); later ones follow the cursor
  // (a preview) and a click places them, on the face under the cursor or in the view plane
  startPlacing(source, typed = "") {
    const pos = typed.trim() ? typed.split(",").map((x) => parseFloat(x) || 0) : null;
    if (pos || !(this.model.components || []).length) return this.insertComponent(source, pos || [0, 0, 0]);
    this.placing = { source, busy: false };
    this.prompt(`Click in the graphics area to place ${source} (Esc cancels).`);
  },

  placeAt(e) {
    const pk = this.vp.pick(e.x, e.y, { kinds: ["face"] });
    const p = pk?.point || this.vp.screenToWorldOnTargetPlane(e.x, e.y).toArray();
    return p.map((v) => +v.toFixed(3));
  },

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
  },

  async placingClick(e) {
    const pl = this.placing; this.placing = null; this.vp.setPreview(null); this.prompt("");
    await this.insertComponent(pl.source, this.placeAt(e));
  },

  cancelPlacing() { if (this.placing) { this.placing = null; this.vp.setPreview(null); this.prompt(""); } },

  async insertComponent(source, pos = [0, 0, 0]) {
    await this.run("insert_component", { source, position: pos });
    if ((this.model.components || []).length === 1) this.vp.zoomFit();
  },

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
  },

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
  },

  async interference() {
    const r = await this.api("/api/command", { name: "check_interference", args: {} });
    if (!r.ok) return this.toast(r.error, "err");
    const list = r.result.interferences;
    this.modal("Interference Detection", list.length ? `<div class="kv">${list.map((i) => `<div>${escapeHtml(i.a)} ↔ ${escapeHtml(i.b)}</div><div>${i.volume.toFixed(3)} mm³</div>`).join("")}</div>` : "<p>No interferences.</p>", null, "interference");
  },

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
  },

  async cutList() {
    const r = await this.api("/api/command", { name: "weldment_cut_list", args: {} });
    if (!r.ok) return this.toast(r.error, "err");
    this.modal("Cut List", `<div class="kv"><div><b>Item / description</b></div><div><b>Length × Qty</b></div>${r.result.items.map((i) =>
      `<div>${i.item}. ${escapeHtml(i.description)}</div><div>${i.length.toFixed(2)} mm × ${i.qty}</div>`).join("")}</div>`, null, "weldment");
  },

  rotateComponentDialog(name) {
    this.modal(`Rotate ${name}`, `<div class="field"><label>Axis (x, y, z)</label><input id="rax" value="0, 0, 1"></div>
      <div class="field"><label>Angle (°)</label><input id="rang" type="number" value="30"></div>`, async () => {
      await this.run("rotate_component", { name, axis: $("#rax").value.split(",").map(Number), angle_deg: parseFloat($("#rang").value) });
    }, "circular_pattern");
  },

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
  },

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
  },

  configValueDialog(cfg) {
    this.modal(`Override in ${cfg}`, `<div class="field"><label>Dimension (e.g. D1@Boss-Extrude1 or D2@Sketch1)</label><input id="cvt"></div>
      <div class="field"><label>Value (number or =expression)</label><input id="cvv"></div>`, async () => {
      const v = $("#cvv").value.trim(); await this.run("set_configuration_value", { configuration: cfg, target: $("#cvt").value.trim(), value: isNaN(+v) ? v : +v });
    }, "config_item");
  },

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
  },

  planeRow(row, label, name, depth) {
    row({ icon: "plane_item", label, depth, selected: this.selection.some((s) => s.kind === "plane" && s.id === name),
      click: (e) => this.treePick({ kind: "plane", id: name, label, value: name }, e),
      hover: (on) => { this.vp.setHover(on ? { kind: "plane", id: name } : null); },
      ctx: (e) => this.ctxMenu(e, [["sketch", "Sketch", () => this.createSketch(name)], ["normal_to", "Normal To", () => this.normalToPlane(name)], ["plane", "Offset Plane...", () => this.feature("plane", { reference: [{ label, value: name }] })],
        "-", ["eye", "Hide / Show", () => this.togglePlane(name)]]) });
  },

  togglePlane(name) {
    const p = (this.mesh?.planes || []).find((x) => x.name === name);
    return this.run("set_feature_visibility", { name, hidden: !(p && p.hidden) });
  },

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
  },

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
  },

  // FeatureManager <-> model: a feature's faces light up when its row is hovered or selected, and a selected
  // face's feature is highlighted in the tree
  async faceOwners() {
    if (this.model.kind === "assembly") return {};
    if (this._owners?.version !== this.version) {
      const r = await this.api("/api/command", { name: "face_features", args: {} });
      this._owners = { version: this.version, map: r.ok ? r.result.faces : {} };
    }
    return this._owners.map;
  },

  async featureFaces(name) { const m = await this.faceOwners(); return Object.keys(m).filter((k) => m[k] === name); },

  async hoverFeature(name) { this.vp.setFeatureFaces(name ? await this.featureFaces(name) : []); },

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
  },

  // the context toolbar a click on a feature shows, as in SOLIDWORKS
  treeToolbar(f, e) {
    const tb = $("#minitb"); tb.innerHTML = "";
    const add = (k, t, fn) => { const b = el("button", "", icon(k)); b.title = t; b.setAttribute("aria-label", t); b.onclick = (ev) => { ev.stopPropagation(); tb.classList.add("hidden"); fn(); }; tb.append(b); };
    const sk = f.type === "sketch" ? f.name : this.model.features.find((k) => k.absorbed_by === f.name && k.type === "sketch")?.name;
    if (f.type !== "sketch") add("edit", "Edit Feature", () => this.editFeature(f.name));
    if (sk) add("sketch", "Edit Sketch", () => this.enterSketch(sk));
    add(f.suppressed ? "eye" : "suppress", f.suppressed ? "Unsuppress" : "Suppress", () => this.run("suppress_feature", { name: f.name, suppressed: !f.suppressed }));
    if (f.type === "sketch") add("eye", "Hide / Show", () => this.toggleSketchVisibility(f.name));
    if (f.type === "plane") add("eye", "Hide / Show", () => this.togglePlane(f.name));
    add("rollback", "Rollback", () => this.rollbackAbove(f.name));
    const g = this.graphics.getBoundingClientRect(), r = e.currentTarget?.getBoundingClientRect?.() || { right: g.left, top: e.clientY };
    tb.style.left = `${Math.max(4, r.right - g.left + 6)}px`; tb.style.top = `${Math.max(4, r.top - g.top - 6)}px`; tb.classList.remove("hidden");
  },

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
  },

  hideFeatureDims() { if (this.sk.showDims) { this.sk.showDims = null; this.vp.dirty = true; } },

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
  },

  treePick(pick, e) {
    if (this.pm.active && this.pm.accept(pick)) return;
    this.vp.showPlanes([pick.id]);
    this.setSelection(e.ctrlKey ? [...this.selection, pick] : [pick]);
  },

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
  },

  editSketchPlane(sketch) {
    this.openPM({ title: `Edit Sketch Plane: ${sketch}`, icon: "plane", command: "edit_sketch_plane",
      groups: [{ title: "Sketch Plane/Face", fields: [{ key: "plane", type: "selection", accepts: ["plane", "face"], single: true, label: "Plane or planar face" }] }],
      args: (v) => ({ sketch, plane: v.plane?.[0]?.value }), message: "Select the new plane or planar face for the sketch." });
  },

  async parentChild(name) {
    const r = await this.evalCall("parent_child", { name }); if (!r) return;
    const list = (xs) => xs.length ? xs.map((x) => `<div>${icon(TYPE_ICON[this.model.features.find((f) => f.name === x)?.type] || "part")} ${escapeHtml(x)}</div>`).join("") : "<div><i>none</i></div>";
    this.modal(`Parent/Child Relationships: ${name}`, `<div class="pc-cols"><div><div class="tp-h">Parents</div>${list(r.parents)}</div><div><div class="tp-h">Children</div>${list(r.children)}</div></div>`, null, "relation");
  },

  rollToPrevious() { // the rollback bar steps back one feature
    const visible = this.model.features.filter((f) => !f.absorbed_by && !f.rolled_back);
    if (visible.length < 2) return this.toast("Cannot roll back above the first feature", "err");
    this.run("rollback", { to_feature: visible[visible.length - 2].name });
  },

  addToNewFolder(name) {
    const sel = this.selection.filter((s) => s.kind === "feature").map((s) => s.id);
    const names = sel.includes(name) ? sel : [name];
    let n = 1; while ((this.model.folders || []).some((f) => f.name === `Folder${n}`)) n++;
    this.expanded.add(`folder:Folder${n}`);
    this.run("add_folder", { name: `Folder${n}`, features: names }).catch(() => this.expanded.delete(`folder:Folder${n}`));
  },

  rollbackAbove(name) {
    const visible = this.model.features.filter((f) => !f.absorbed_by), i = visible.findIndex((f) => f.name === name);
    if (i <= 0) return this.toast("Cannot roll back above the first feature", "err");
    // put the bar just above the feature (after the previous top-level feature and its sketch)
    this.run("rollback", { to_feature: visible[i - 1].name });
  },

  async toggleSketchVisibility(name) {
    const f = (this.mesh.sketches || []).find((s) => s.name === name);
    await this.run("set_sketch_visibility", { sketch: name, hidden: !(f && f.hidden) });
  },

  ctxMenu(e, items) {
    const m = $("#ctxmenu"); m.innerHTML = "";
    for (const it of items) {
      if (it === "-") { m.append(el("div", "cx-sep")); continue; }
      const r = el("div", "cx-item", `${icon(it[0])}<span>${it[1]}</span>`); r.onclick = () => { m.classList.add("hidden"); it[2](); }; m.append(r);
    }
    m.style.left = `${Math.min(e.clientX, innerWidth - 220)}px`; m.style.top = `${Math.min(e.clientY, innerHeight - 30 * items.length)}px`;
    m.classList.remove("hidden");
    setTimeout(() => m.classList.remove("hidden"));
  },

  renameFeature(name) {
    this.modal("Rename", `<div class="field"><label>New name</label><input id="rn" value="${escapeHtml(name)}"></div>`, async () => {
      const v = $("#rn").value.trim(); if (v && v !== name) await this.run("rename_feature", { name, new_name: v });
    }, "rename");
    setTimeout(() => $("#rn")?.select());
  },

  deleteFeature(name) {
    const f = this.model.features.find((x) => x.name === name);
    const sk = this.model.features.find((k) => k.absorbed_by === name);
    this.modal("Confirm Delete", `<p>Delete <b>${escapeHtml(name)}</b>?</p>${sk ? `<label class="pm-check"><input type="checkbox" id="delabs" checked> Also delete absorbed feature <b>${escapeHtml(sk.name)}</b></label>` : ""}`,
      async () => { await this.run("delete_feature", { name, delete_absorbed_sketch: sk ? $("#delabs").checked : true }); this.setSelection([]); }, "trash");
    void f;
  },
};

export function installTree(App) {
  Object.assign(App.prototype, MIXIN);
}
