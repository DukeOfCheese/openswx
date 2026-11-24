import { icon } from "../icons.js";
import { el, escapeHtml } from "../propman.js";
import { SHORTCUTS, DEFAULT_KEYS, comboOf, effectiveKeys, keyTable, conflicts } from "../keys.js";
import { $ } from "../lib/dom.js";

const MIXIN = {
  bindKeys() {
    this.keys = effectiveKeys({}); this.keyMap = keyTable(this.keys);
    const views = { view_front: "front", view_back: "back", view_left: "left", view_right: "right", view_top: "top", view_bottom: "bottom", view_isometric: "isometric" };
    const filters = { filter_faces: "face", filter_edges: "edge", filter_vertices: "vertex" };
    // what each action in keys.js does (the keys themselves come from User Settings > shortcuts)
    this.keyActions = {
      ...Object.fromEntries(Object.entries(views).map(([id, v]) => [id, () => this.vp.setView(v)])),
      ...Object.fromEntries(Object.entries(filters).map(([id, f]) => [id, () => { if (this.sk.active) return false; this.toggleFilter(f); }])),
      normal_to: () => this.normalTo(), undo: () => this.undoRedo("undo"), redo: () => this.undoRedo("redo"),
      rebuild: () => this.run("rebuild"), force_rebuild: () => this.run("rebuild"), save: () => this.save(), new_part: () => this.newPart(),
      open: () => this.openFile(), previous_view: () => this.vp.previousView(), next_document: () => this.nextDocument(),
      close: () => this.run("close_document").then(() => this.vp.zoomFit()).catch(() => {}),
      filter_toolbar: () => this.huFilter?.click(), shortcut_bar: () => this.shortcutBar(), zoom_fit: () => this.vp.zoomFit(),
      zoom_out: () => this.vp.zoomAt(1.25, this.vp.w / 2, this.vp.h / 2), zoom_in: () => this.vp.zoomAt(1 / 1.25, this.vp.w / 2, this.vp.h / 2),
      view_orientation: () => this.orientationMenu(),
      rename: () => { const f = this.selection.find((x) => x.kind === "feature"); if (f) this.renameFeature(f.id); },
      command_search: () => { const i = $("#cmdsearch"); i?.focus(); i?.select(); },
      focus_tree: () => this.focusTree(), open_menu: () => this.menuKeyOpen(0), home: () => this.homeShow(),
    };
    this.bindTreeKeys();
    document.addEventListener("keydown", (e) => {
      if (this.menuKey(e)) return; // (an open menu takes the arrows, Enter and Escape)
      if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
      if (!$("#modal").classList.contains("hidden")) return;
      if (this.homeOpen && e.key === "Escape") return this.homeHide();
      const k = e.key.toLowerCase(), C = e.ctrlKey || e.metaKey;
      // keys with a fixed meaning that depends on what is going on: these come before the customisable ones
      if (e.key === "Escape") {
        $("#sbar")?.remove(); this.hideFeatureDims();
        if (this.placing) return this.cancelPlacing();
        if (this.zoomArea) { this.zoomArea = false; this.prompt(""); return; }
        if (this.sk.active && this.sk.key(e)) return; return this.cancelAll();
      }
      if (e.key === "Enter" && this.sk.active && !this.pm.active && this.sk.key(e)) return;
      if (e.key === "Delete") return this.dwg.isOpen() ? undefined : this.deleteSelection(); // (the drawing editor deletes its own selection)
      if (e.key === "Enter" && this.pm.active) return this.pmOK();
      if (e.key === "Enter" && !C && !e.shiftKey && this.lastCommand) return this.lastCommand.fn(); // SOLIDWORKS: Enter repeats the last command
      if (e.key === "Tab" && !C && this.model?.kind === "assembly") { // Tab hides the component under the cursor, Shift+Tab shows all
        e.preventDefault();
        if (e.shiftKey) return this.showAllComponents();
        const pk = this.vp.pick(this.lastMouse.x, this.lastMouse.y, { kinds: ["face"] });
        if (pk) this.toggleComponents([pk.id.split("/")[0]]);
        return;
      }
      const action = this.keyMap.get(comboOf(e));
      if (action && this.keyActions[action]) {
        e.preventDefault();
        if (this.keyActions[action]() !== false) return; // (false: not for now, e.g. a selection filter while sketching)
      }
      if (e.key.startsWith("Arrow") && !e.altKey) { // arrows turn the view, Ctrl + arrows pan it
        const a = 15 * Math.PI / 180, d = { ArrowLeft: [-a, 0], ArrowRight: [a, 0], ArrowUp: [0, -a], ArrowDown: [0, a] }[e.key];
        if (C) this.vp.panBy(-d[0] * 300, -d[1] * 300); else this.vp.rotateBy(...d);
        return;
      }
      if (this.sk.active && !C && !e.altKey) {
        const map = { l: "line", r: "rectangle", c: "circle", a: "arc", d: "smart_dim", p: "point" };
        if (map[k]) this.setSketchTool(map[k]);
      }
    });
  },

  // User Settings that the window applies itself: view colours, the mouse and the keyboard
  async loadUserSettings() {
    const r = await this.api("/api/command", { name: "get_settings", args: {} }).catch(() => null);
    if (r?.ok) this.applyUserSettings(r.result.settings);
  },

  applyUserSettings(s) {
    if (!s) return;
    const themed = s.view_background_top === "#cfdae8" && s.view_background_bottom === "#ffffff"; // (the settings' defaults)
    this.graphics.style.background = themed ? "" : `linear-gradient(${s.view_background_top}, ${s.view_background_bottom})`;
    this.vp.setColors({ body: s.view_body_color, edge: s.view_edge_color, hover: s.view_highlight_color, sel: s.view_selection_color });
    this.vp.mouse = { preset: s.mouse_preset || "solidworks", zoomReverse: !!s.mouse_zoom_reverse };
    this.keys = effectiveKeys(s.shortcuts); this.keyMap = keyTable(this.keys);
    this.renderHelpPane();
  },

  menuKeyLabel(label) {
    if (!label || !this.keys) return label;
    const id = Object.keys(DEFAULT_KEYS).find((a) => DEFAULT_KEYS[a] === label);
    return id ? this.keys[id] : label;
  },

  focusTree() {
    const rows = [...document.querySelectorAll("#tree .t-row")];
    if (!rows.length) return;
    (rows.find((r) => r.classList.contains("selected")) || rows[0]).focus();
  },

  bindTreeKeys() {
    const tree = $("#tree");
    tree.addEventListener("keydown", (e) => {
      const row = e.target.closest?.(".t-row"); if (!row) return;
      const rows = [...tree.querySelectorAll(".t-row")], i = rows.indexOf(row);
      const go = (j) => { const r = rows[Math.max(0, Math.min(rows.length - 1, j))]; r?.focus(); r?.scrollIntoView({ block: "nearest" }); };
      const fire = (type) => { const b = row.getBoundingClientRect(); row.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: b.left + 20, clientY: b.top + b.height / 2 })); };
      const k = e.key;
      if (k === "ArrowDown") go(i + 1); else if (k === "ArrowUp") go(i - 1);
      else if (k === "Home") go(0); else if (k === "End") go(rows.length - 1);
      else if (k === "ArrowRight" || k === "ArrowLeft") {
        const tw = row.querySelector(".tw"), open = tw?.textContent === "▾";
        if (tw?.textContent && open === (k === "ArrowLeft")) {
          const label = row.querySelector(".lbl")?.textContent; tw.click();
          requestAnimationFrame(() => [...tree.querySelectorAll(".t-row")].find((r) => r.querySelector(".lbl")?.textContent === label)?.focus());
        }
      } else if (k === "Enter" || k === " ") fire("click");
      else if (k === "F2") { const f = row.dataset.feature; if (f) this.renameFeature(f); }
      else if (k === "ContextMenu" || (k === "F10" && e.shiftKey)) fire("contextmenu");
      else if (k === "Escape") row.blur();
      else return;
      e.preventDefault(); e.stopPropagation();
    });
    // rows are rebuilt on every change: make them focusable as they appear
    new MutationObserver(() => tree.querySelectorAll(".t-row:not([tabindex])").forEach((r) => { r.tabIndex = -1; })).observe(tree, { childList: true, subtree: true });
  },

  menuKeyOpen(n) {
    const roots = [...document.querySelectorAll("#menubar .menu-root")];
    if (!roots.length) return;
    const root = roots[(n + roots.length) % roots.length];
    this.closeMenus(); root.click(); // (opens it, filled afresh)
    this.menuKbd = { root };
    this.menuKeyMark(root.querySelector(":scope > .menu-drop > .menu-item"));
  },

  menuKeyMark(item) {
    document.querySelectorAll("#menubar .kfocus").forEach((x) => x.classList.remove("kfocus"));
    if (item) { item.classList.add("kfocus"); item.scrollIntoView({ block: "nearest" }); }
  },

  menuKey(e) {
    const st = this.menuKbd;
    if (!st || !st.root.classList.contains("open")) { this.menuKbd = null; return false; }
    const cur = st.root.querySelector(".kfocus");
    const list = cur ? cur.parentElement : st.root.querySelector(":scope > .menu-drop");
    const items = [...list.children].filter((x) => x.classList.contains("menu-item") && !x.classList.contains("disabled"));
    const i = items.indexOf(cur), k = e.key;
    const into = (row) => { row.classList.add("kopen"); this.menuKeyMark(row.querySelector(":scope > .menu-sub > .menu-item")); };
    if (k === "ArrowDown" || k === "ArrowUp") this.menuKeyMark(items[(i + (k === "ArrowDown" ? 1 : -1) + items.length) % items.length]);
    else if (k === "ArrowRight" && cur?.classList.contains("has-sub")) into(cur);
    else if (k === "ArrowLeft" && list.classList.contains("menu-sub")) { const up = list.parentElement; up.classList.remove("kopen"); this.menuKeyMark(up); }
    else if (k === "ArrowLeft" || k === "ArrowRight") { const roots = [...document.querySelectorAll("#menubar .menu-root")]; this.menuKeyOpen(roots.indexOf(st.root) + (k === "ArrowRight" ? 1 : -1)); }
    else if (k === "Enter" || k === " ") { if (cur?.classList.contains("has-sub")) into(cur); else { this.menuKbd = null; cur?.click(); } }
    else if (k === "Escape" || k === "F10" || k === "Tab") { this.menuKbd = null; this.closeMenus(); }
    else return false;
    e.preventDefault(); e.stopPropagation();
    return true;
  },

  // Tools > Customize Keyboard: every action with its keys; click a box and press the new keys
  customizeKeyboardDialog() {
    const keys = { ...this.keys };
    const groups = [...new Set(SHORTCUTS.map((x) => x[3]))];
    const body = groups.map((g) => `<tr><th colspan="3" style="text-align:left;padding-top:8px">${escapeHtml(g)}</th></tr>` + SHORTCUTS.filter((x) => x[3] === g).map(([id, label]) =>
      `<tr><td>${escapeHtml(label)}</td><td><input class="kb-in" data-id="${id}" readonly value="${escapeHtml(keys[id])}" placeholder="(none)" style="width:130px"></td>
       <td><button class="btn kb-clr" data-id="${id}" title="No shortcut">Clear</button> <button class="btn kb-def" data-id="${id}" title="${escapeHtml(DEFAULT_KEYS[id] || "none")}">Default</button></td></tr>`).join("")).join("");
    this.modal("Customize Keyboard", `<p class="muted">Click a box and press the keys. Some keys are fixed: Escape, Enter, Delete, Tab (assemblies), the arrows, and the sketch tools' letters while sketching.</p>
      <div id="kb-warn" style="color:var(--cancel)"></div>
      <div style="max-height:52vh;overflow:auto"><table class="dw-bom" style="width:100%">${body}</table></div>
      <p><button class="btn kb-reset">Reset all to defaults</button></p>`, async () => {
      const bad = conflicts(keys);
      if (bad.length) { const m = `Two actions share ${bad.map(([k]) => k).join(", ")}: change one first.`; this.toast(m, "err"); throw new Error(m); }
      const shortcuts = Object.fromEntries(Object.entries(keys).filter(([id, k]) => k !== DEFAULT_KEYS[id]));
      const r = await this.api("/api/command", { name: "set_settings", args: { values: { shortcuts } } });
      if (!r.ok) { this.toast(r.error, "err"); throw new Error(r.error); }
      this.applyUserSettings(r.result.settings); this.toast("Keyboard shortcuts saved.");
    }, "options");
    const warn = () => {
      const label = (id) => SHORTCUTS.find((x) => x[0] === id)?.[1] || id;
      $("#kb-warn").textContent = conflicts(keys).map(([k, ids]) => `${k} is used by ${ids.map(label).join(" and ")}.`).join(" ");
    };
    const set = (id, k) => { keys[id] = k; document.querySelector(`.kb-in[data-id="${id}"]`).value = k; warn(); };
    document.querySelectorAll(".kb-in").forEach((inp) => {
      inp.onkeydown = (e) => {
        if (e.key === "Tab") return; // (moves on through the dialog)
        e.preventDefault(); e.stopPropagation();
        const c = comboOf(e); if (c) set(inp.dataset.id, c);
      };
    });
    document.querySelectorAll(".kb-clr").forEach((b) => { b.onclick = () => set(b.dataset.id, ""); });
    document.querySelectorAll(".kb-def").forEach((b) => { b.onclick = () => set(b.dataset.id, DEFAULT_KEYS[b.dataset.id]); });
    document.querySelector(".kb-reset").onclick = () => { for (const id of Object.keys(keys)) set(id, DEFAULT_KEYS[id]); };
    warn();
  },

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
  },

  togglePerspective() { this.vp.setPerspective(!this.vp.perspective); this.toast(this.vp.perspective ? "Perspective view" : "Orthographic view"); },

  newNamedView() {
    this.modal("New View", `<div class="field"><label>View name</label><input id="nvname" value="View${Object.keys(this.model?.named_views || {}).length + 1}"></div>`,
      () => this.run("save_named_view", { name: $("#nvname").value.trim(), ...this.vp.savedViewData() }), "view_orient");
  },

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
  },
};

export function installSettings(App) {
  Object.assign(App.prototype, MIXIN);
}
