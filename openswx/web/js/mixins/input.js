// Viewport input: mouse gestures, box/area zoom, component dragging, picking and selection.
import { icon } from "../icons.js";
import { el, escapeHtml, SPECS } from "../propman.js";
import { $ } from "../lib/dom.js";
import { cap, segHitsRect, facePlaneUV } from "../lib/helpers.js";

const MIXIN = {
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
  },

  // ------------------------------------------------------------------ box select / zoom to area
  moveBox(e) {
    const b = this.box; b.x1 = e.x; b.y1 = e.y;
    if (!b.moved && Math.hypot(b.x1 - b.x0, b.y1 - b.y0) < 5) return;
    b.moved = true;
    if (!this.boxEl) { this.boxEl = el("div", "selbox"); this.graphics.append(this.boxEl); }
    const crossing = !b.zoom && b.x1 < b.x0;
    Object.assign(this.boxEl.style, { left: `${Math.min(b.x0, b.x1)}px`, top: `${Math.min(b.y0, b.y1)}px`, width: `${Math.abs(b.x1 - b.x0)}px`, height: `${Math.abs(b.y1 - b.y0)}px` });
    this.boxEl.classList.toggle("crossing", crossing);
  },

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
  },

  startZoomArea() { this.zoomArea = true; this.prompt("Zoom to Area: drag a box around the area to zoom to."); },

  zoomToSelection() {
    const items = this.selection.filter((s) => ["face", "edge", "sketch"].includes(s.kind));
    if (!items.length) return this.vp.zoomFit();
    this.vp.zoomToItems(items);
  },

  // ------------------------------------------------------------------ selection filter (F5; X faces, E edges, V vertices)
  filterHas(k) { return !!this.filter?.has(k); },

  toggleFilter(k) {
    if (!k) this.filter = null;
    else { const f = new Set(this.filter || []); f.has(k) ? f.delete(k) : f.add(k); this.filter = f.size ? f : null; }
    this.huFilter?.classList.toggle("on", !!this.filter);
    this.toast(this.filter ? `Selection filter: ${[...this.filter].map((x) => cap(x) + "s").join(", ")}` : "Selection filters cleared");
  },

  filterKinds() { return this.filter ? [...this.filter] : null; },

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
  },

  // ------------------------------------------------------------------ mouse gestures (right-drag), S-key shortcut bar, repeat last command
  gestureItems() {
    if (this.sk.active) return { up: ["smart_dim", "Smart Dimension", () => this.setSketchTool("smart_dim")], right: ["line", "Line", () => this.setSketchTool("line")],
      down: ["rectangle", "Corner Rectangle", () => this.setSketchTool("rectangle")], left: ["circle", "Circle", () => this.setSketchTool("circle")] };
    return { up: ["v_top", "Top", () => this.vp.setView("top")], right: ["v_right", "Right", () => this.vp.setView("right")],
      down: ["v_bottom", "Bottom", () => this.vp.setView("bottom")], left: ["v_left", "Left", () => this.vp.setView("left")] };
  },

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
  },

  endGesture() {
    const g = this.gesture; this.gesture = null;
    g.el?.remove();
    if (g.dir) { this.suppressContext = true; setTimeout(() => { this.suppressContext = false; }, 50); this.gestureItems()[g.dir][2](); }
  },

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
  },

  remember(label, fn) { this.lastCommand = { label, fn }; },

  // ------------------------------------------------------------------ assembly: drag components, Hide/Show, Isolate
  startCompDrag(e, pick) {
    const name = pick.id.split("/")[0], c = (this.model.components || []).find((x) => x.name === name);
    if (!c) return;
    this.compDrag = { name, c, x0: e.x, y0: e.y, moved: false, fixed: c.fixed, cur: Object.fromEntries(this.model.components.map((k) => [k.name, k.pose])) };
  },

  moveCompDrag(e) {
    const d = this.compDrag;
    if (!d.moved && Math.hypot(e.x - d.x0, e.y - d.y0) < 4) return;
    if (d.fixed) { if (!d.warned) { d.warned = true; this.toast(`${d.name} is fixed: Float it to drag it.`); } return; }
    d.moved = true;
    const a = this.vp.screenToWorldOnTargetPlane(d.x0, d.y0), b = this.vp.screenToWorldOnTargetPlane(e.x, e.y);
    d.delta = [b.x - a.x, b.y - a.y, b.z - a.z];
    const pose = d.c.pose.slice(); for (let i = 0; i < 3; i++) pose[3 + i] += d.delta[i];
    this.vp.setComponentPoses({ [d.name]: pose }, d.cur);
  },

  async endCompDrag() {
    const d = this.compDrag; this.compDrag = null;
    if (!d.moved || !d.delta) return;
    this.vp.setComponentPoses(null, d.cur);
    const pos = d.c.pose.slice(3).map((v, i) => +(v + d.delta[i]).toFixed(4));
    await this.run("move_component", { name: d.name, position: pos }).catch(() => {});
  },

  selectedComponentsAny() {
    const names = new Set([...this.selectedComponents(), ...this.selection.filter((s) => s.kind === "face" || s.kind === "edge").map((s) => String(s.id).split("/")[0])]);
    return [...names];
  },

  toggleComponents(names) {
    if (!names?.length) return this.toast("Select components (in the tree or a face in the view) to hide or show.");
    for (const n of names) this.hiddenComps.has(n) ? this.hiddenComps.delete(n) : this.hiddenComps.add(n);
    this.vp.setHiddenComponents([...this.hiddenComps]); this.renderTree();
  },

  isolateComponents(names = this.selectedComponentsAny()) {
    if (!names.length) return this.toast("Select the components to isolate.");
    this.hiddenComps = new Set((this.model.components || []).map((c) => c.name).filter((n) => !names.includes(n)));
    this.vp.setHiddenComponents([...this.hiddenComps]); this.renderTree();
  },

  showAllComponents() { this.hiddenComps.clear(); this.vp.setHiddenComponents([]); this.renderTree(); },

  // ------------------------------------------------------------------ documents
  async switchDocument(name) { if (name !== this.model.name) { await this.run("activate_document", { name }); this.vp.zoomFit(); } },

  nextDocument() {
    const docs = (this.docs?.documents || []).map((d) => d.name); if (docs.length < 2) return;
    this.switchDocument(docs[(docs.indexOf(this.model.name) + 1) % docs.length]);
  },

  pickKinds() {
    const acc = this.pm.focusedAccepts();
    const k = new Set();
    for (const a of acc) { if (a === "sketchline" || a === "skent" || a === "skline") k.add("sketch"); else if (a === "skpoint") k.add("sketch"); else if (a !== "feature") k.add(a); }
    if (["hole", "hole_wizard"].includes(this.pm.spec?.command)) k.add("face");
    if (acc.includes("sketch")) k.add("sketch");
    return k.size ? [...k] : ["face", "edge"];
  },

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
  },

  // ================================================================== features / PropertyManager
  selectedSketch() {
    for (const s of this.selection) {
      if (s.kind === "feature" && s.type === "sketch") return s.id;
      if (s.kind === "sketch") return s.sketch;
    }
    if (this.sk.active) return this.sk.name;
    return null;
  },

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
  },

  selectDim(id, add) { if (!add) this.selDims.clear(); this.selDims.has(id) ? this.selDims.delete(id) : this.selDims.add(id); this.vp.dirty = true; },

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
  },
};

export function installInput(App) {
  Object.assign(App.prototype, MIXIN);
}
