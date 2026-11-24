import { Viewport, decodeMeshBinary } from "./viewport.js";
import { PropertyManager, UNITS, el, escapeHtml } from "./propman.js";
import { Sketcher } from "./sketcher.js";
import { DrawingEditor } from "./drawing.js";
import { installMore } from "./more.js";
import { installMfg } from "./mfg.js";
import { installHome } from "./home.js";
import { installCollab } from "./collab.js";
import { installUi } from "./mixins/ui.js";
import { installTree } from "./mixins/tree.js";
import { installSketchPm } from "./mixins/sketch-pm.js";
import { installInput } from "./mixins/input.js";
import { installFeatures } from "./mixins/features.js";
import { installPanels } from "./mixins/panels.js";
import { installFiles } from "./mixins/files.js";
import { installSettings } from "./mixins/settings.js";
import { LONG_JOBS } from "./lib/constants.js";
import { $ } from "./lib/dom.js";

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
    this.initCollab();
    this.connect();
    this.loadUserSettings();
    this.homeStart();
    this.checkRecovery();
    // leaving with unsaved changes asks first (the desktop window has its own check on close)
    window.addEventListener("beforeunload", (e) => { if (!this.desk && (this.docs?.documents || []).some((d) => d.dirty)) { e.preventDefault(); e.returnValue = ""; } });
  }

  // Document Recovery: autosaved copies left by a session that crashed or was killed
  async checkRecovery() {
    const r = await this.api("/api/command", { name: "list_recovered", args: {} }).catch(() => null);
    const list = r?.ok ? r.result.recovered : [];
    if (!list.length) return;
    const rows = list.map((d) => `<tr><td><input type="checkbox" class="rcv" value="${escapeHtml(d.id)}" checked></td><td><b>${escapeHtml(d.name)}</b></td><td>${d.kind}</td><td>${new Date(d.saved * 1000).toLocaleString()}</td><td>${escapeHtml(d.path || "(never saved)")}</td></tr>`).join("");
    this.modal("Document Recovery", `<p>openswx did not close normally last time. These documents had unsaved changes:</p>
      <table class="dw-bom"><tr><th></th><th>Document</th><th>Type</th><th>Autosaved</th><th>File</th></tr>${rows}</table>
      <p>OK opens the ticked ones (save them to keep the changes) and deletes the autosaved copies of the others.</p>`, async () => {
      for (const c of document.querySelectorAll(".rcv")) {
        await this.api("/api/command", { name: c.checked ? "recover_document" : "discard_recovered", args: { id: c.value } });
      }
      await this.refresh();
    }, "open");
  }

  async api(path, body) {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    return r.json();
  }

  // The mesh travels as one binary document (/api/mesh.bin): the big number arrays are typed-array buffers, so
  // they skip JSON's text on the wire and its parsing here. An older server, or a document this build cannot
  // read, falls back to the JSON route, which stays unchanged.
  async fetchMesh() {
    try {
      const r = await fetch("/api/mesh.bin");
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const mesh = decodeMeshBinary(await r.arrayBuffer());
      if (!mesh || !Array.isArray(mesh.faces)) throw new Error("no faces in the document");
      return mesh;
    } catch (err) {
      console.warn("binary mesh unavailable, using JSON:", err);
      return this.api("/api/mesh");
    }
  }

  // Long solves run as background jobs (start_job): a progress toast with Cancel, and the UI stays usable.
  async commandOrJob(name, args = {}) {
    if (!LONG_JOBS.has(name)) return this.api("/api/command", { name, args });
    const started = await this.api("/api/command", { name: "start_job", args: { name, args } });
    if (!started.ok) return started;
    const id = started.result.id;
    const t = el("div", "toast"); t.style.minWidth = "260px";
    t.innerHTML = `<div><b>${escapeHtml(LONG_JOBS.get(name))}</b> <span class="jb-msg"></span></div>
      <div style="height:6px;background:#0002;border-radius:3px;margin:6px 0"><div class="jb-bar" style="height:6px;width:0;background:#2b7de9;border-radius:3px"></div></div>
      <button class="btn jb-cancel">Cancel</button>`;
    $("#toasts").append(t);
    t.querySelector(".jb-cancel").onclick = () => { this.api("/api/command", { name: "cancel_job", args: { id } }); t.querySelector(".jb-msg").textContent = "cancelling..."; };
    try {
      for (;;) {
        await new Promise((r) => setTimeout(r, 400));
        const st = await this.api("/api/command", { name: "job_status", args: { id } });
        if (!st.ok) return st;
        const j = st.result;
        t.querySelector(".jb-bar").style.width = `${Math.round((j.progress ?? 0) * 100)}%`;
        if (j.state === "running") { if (!t.querySelector(".jb-msg").textContent.startsWith("cancel")) t.querySelector(".jb-msg").textContent = `${Math.round(j.seconds)} s`; continue; }
        if (j.state === "done") return { ok: true, result: j.result };
        return { ok: false, error: j.state === "cancelled" ? `${LONG_JOBS.get(name)} cancelled` : j.error };
      }
    } finally { t.remove(); }
  }

  async run(name, args = {}, { quiet = false } = {}) {
    const res = await this.commandOrJob(name, args);
    if (!res.ok) { if (!quiet) this.toast(res.error, "err"); throw new Error(res.error); }
    await this.refresh();
    return res;
  }

  connect() {
    $("#sb-agent").textContent = `MCP: ${location.origin}/mcp`;
    const es = new EventSource(`/api/events?page=${encodeURIComponent(this.cl.page)}`); // (the page: its collaboration locks)
    es.onmessage = (m) => {
      const d = JSON.parse(m.data);
      if (d.version !== this.version) {
        if (d.last && d.last.origin === "agent" && this.version >= 0) this.onAgent(d.last);
        this.refresh();
      }
      if (d.collab !== undefined && d.collab !== this.cl.rev) this.refreshCollab(); // (peers, locks: no document change)
    };
    es.onerror = () => { $("#sb-agent").textContent = "Disconnected - retrying..."; };
    es.onopen = () => { $("#sb-agent").textContent = `MCP: ${location.origin}/mcp`; };
  }

  // One refresh at a time: a command's own refresh and the event stream's for the same change used to fetch and
  // rebuild everything twice. Calls while one runs are merged into one more pass, and the mesh is fetched and
  // rebuilt only when the model's version has moved since it was last shown.
  refresh() {
    if (this.refreshing) { this.refreshAgain = true; return this.refreshing; }
    this.refreshing = (async () => {
      do { this.refreshAgain = false; await this.refreshOnce(); } while (this.refreshAgain);
    })().finally(() => { this.refreshing = null; });
    return this.refreshing;
  }

  async refreshOnce() {
    const st = await this.api("/api/state");
    const meshFresh = this.mesh && st.version === this.meshVersion && this.model && this.model.name === st.model.name;
    const mesh = meshFresh ? this.mesh : await this.fetchMesh();
    const first = this.version < 0 || (this.model && this.model.name !== st.model.name);
    this.version = st.version; this.model = st.model; this.docs = st.documents; this.mesh = mesh; this.meshVersion = st.version;
    if (UNITS.name !== (this.model.units || "mm")) { UNITS.set(this.model.units || "mm"); if (this.pm?.active) this.pm.render(); }
    if (this.sk.active && !this.model.features.some((f) => f.name === this.sk.name)) this.exitSketch(true);
    if (first) { // another document: its own selection and display state
      this.selection = []; this.vp.setSelection([]); this.sk.showDims = null;
      this.hiddenComps.clear(); this.vp.hiddenComponents = new Set();
    }
    if (!meshFresh) this.vp.setMesh(mesh); // (unchanged geometry: the scene, its colours and selection stay as they are)
    if ((this.model.kind || "part") === "assembly" && (this.model.display_state || null) !== (this.shownDisplayState ?? null)) {
      this.shownDisplayState = this.model.display_state || null; // a display state was activated: show what it shows
      this.hiddenComps = new Set(this.model.hidden_components || []); this.vp.setHiddenComponents([...this.hiddenComps]);
    }
    this.afterRefreshMore();
    if (this.sk.active) await this.loadSketch();
    if (this.sk.showDims) await this.showFeatureDims(this.sk.showDims.feature, true);
    if (this.measureOpen) this.updateMeasure();
    if (this.lastKind !== (this.model.kind || "part")) { this.lastKind = this.model.kind || "part"; this.renderCommandManager(); }
    this.renderTree(); this.renderTitle(); this.renderStatus(); this.renderQuickAccessState(); this.renderTaskPane(); this.renderConfirm();
    this.homeCheck();
    this.syncNativeMenu(); // (the Window menu lists the open documents)
    if (this.model.drawing_document && this.openedDrawingDoc !== this.model.drawing_document) { // a .oswdrw was opened: show it
      this.openedDrawingDoc = this.model.drawing_document; this.dwg.open();
    } else if (this.model.show_drawing) this.dwg.open(); // a SOLIDWORKS drawing was opened
    if (first) requestAnimationFrame(() => { this.vp.resize(); this.vp.zoomFit(false); });
    this.collabApply();
  }

  async loadSketch() {
    const res = await this.api("/api/command", { name: "get_feature", args: { name: this.sk.name } });
    if (!res.ok) return;
    const overlay = (this.mesh.sketches || []).find((s) => s.name === this.sk.name);
    this.sk.update(res.result, overlay);
    this.renderSketchPM();
  }

  onAgent(entry) {
    this.homeHide(); // (an agent at work: show what it does)
    const tab = document.querySelector('.tp-tab[data-pane="agent"]');
    const watching = !$("#taskpane").classList.contains("collapsed") && tab?.classList.contains("active");
    if (watching) return; // (the Agent activity pane is open and already lists it: no toast on top)
    const what = String(entry.command || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()); // sketch_circle -> Sketch circle
    const msg = entry.ok ? `<b>Agent</b>&ensp;${escapeHtml(what)}` : `<b>Agent</b>&ensp;${escapeHtml(what)} failed: ${escapeHtml(entry.message || "")}`;
    this.toast(msg, entry.ok ? "agent" : "err", { html: true });
    if (tab && !tab.querySelector(".dot")) tab.append(el("span", "dot")); // (unread, until the pane is opened)
  }

  get desk() { return window.pywebview?.api; } // present when running as the desktop app (openswx.exe)
}

// install each mixin on the App class, then the ones that add menus, manufacturing, Home and collaboration
installUi(App);
installTree(App);
installSketchPm(App);
installInput(App);
installFeatures(App);
installPanels(App);
installFiles(App);
installSettings(App);
App.prototype.newPartChoosers = [];
installMore(App);
installMfg(App);
installHome(App);
installCollab(App);
window.app = new App();
