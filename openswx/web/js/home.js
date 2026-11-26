// The Home page: a full-window page over the editor where you start (New Part, New Assembly, Open, recent
// files, tutorials). It shows at start-up (User Settings > show_home) when openswx opened without a file, and
// from File > Home. It never blocks anything: it hides when you pick something on it, press Escape, when the
// active document changes (features, a file, another document) or when an agent acts.
// Start-up order: the server serves index.html with #home already up (class "starting") when Home will show, so
// it is the first thing painted, never the empty graphics area. New Part / New Assembly / Open / a recent file
// leave Home up while the document loads: it goes once refresh() has the new document in the view.
// Other code adds tabs with this.homeTabs.push({ id, label, icon, render(panel) }); render fills the panel.
// Mixed into the App class by installHome().

import { icon } from "./icons.js";
import { escapeHtml } from "./propman.js";
import { TUTORIALS } from "./more.js";

const $ = (s) => document.querySelector(s);
const KIND_ICON = { part: "part", assembly: "assembly", drawing: "drawing" };

function when(t) {
  if (!t) return "";
  const d = new Date(t * 1000), now = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `Today ${time}`;
  if (d.toDateString() === new Date(now - 864e5).toDateString()) return `Yesterday ${time}`;
  return d.toLocaleDateString([], { year: d.getFullYear() === now.getFullYear() ? undefined : "numeric", month: "short", day: "numeric" });
}

const LOCAL_TAB = {
  id: "local", label: "Local", icon: "open",
  render(panel) { return this.homeLocal(panel); },
};

const MIXIN = {
  homeShow(tab = null) {
    if (tab) this.homeTab = tab;
    this.homeAt = this.model ? this.homeSnapshot() : null; // (what was active when it opened; at start-up: the first one shown)
    this.homeOpen = true;
    $("#home").classList.remove("hidden");
    this.homeRender();
  },

  homeHide() {
    this.homeDismissed = true; // (start-up does not bring it back once it went away)
    if (!this.homeOpen) return;
    this.homeOpen = false;
    $("#home").classList.add("hidden");
    requestAnimationFrame(() => this.vp.resize());
  },

  // the page hides itself once the document behind it is no longer the one it opened over, or has content.
  // (Called by refresh() once the new mesh is in the viewport: the view is fitted to it first, so the first frame
  // after Home is the document as it will be shown, not the empty part it opened over.)
  homeCheck() {
    if (!this.homeOpen || !this.model) return;
    const now = this.homeSnapshot(), at = this.homeAt;
    if (!at) { this.homeAt = now; return; }
    if (now.name !== at.name || now.features !== at.features || now.path !== at.path) {
      this.vp.zoomFit(false);
      this.homeHide();
    }
  },

  homeSnapshot() {
    const m = this.model;
    return { name: m.name, features: (m.features || []).length, path: m.path || null };
  },

  // start-up: Home unless switched off, or openswx opened with a file (the active document is then not the
  // untouched part made on demand)
  // untouched part made on demand). The server already decided when it served the page (#home.starting): take
  // that over at once, before the first frame, then check it against the state as it is now.
  async homeStart() {
    const box = $("#home");
    $("#brand").onclick = () => this.homeShow(); // the logo in the title bar opens Home, as File > Home does
    if (box.classList.contains("starting")) { box.classList.remove("starting"); this.homeShow("local"); }
    const [s, st] = await Promise.all([
      this.api("/api/command", { name: "get_settings", args: {} }).catch(() => null),
      this.api("/api/state").catch(() => null),
    ]);
    if (this.homeDismissed) return; // (e.g. an agent acted while this was loading)
    const want = !!(s?.ok && s.result.settings.show_home && st?.model?.throwaway && (st.documents?.documents || []).length <= 1);
    if (want && !this.homeOpen) this.homeShow("local");
    else if (!want && this.homeOpen) this.homeHide();
  },

  homeRender() {
    const box = $("#home");
    const tabs = this.homeTabs;
    if (!tabs.some((t) => t.id === this.homeTab)) this.homeTab = tabs[0].id;
    box.innerHTML = `<div class="hm-side">
        <div class="hm-brand"><span class="brand-cube"></span><span class="brand-name">open<b>swx</b></span></div>
        <nav class="hm-tabs">${tabs.map((t) => `<button class="hm-tab${t.id === this.homeTab ? " active" : ""}" data-tab="${escapeHtml(t.id)}">${icon(t.icon || "folder")}<span>${escapeHtml(t.label)}</span></button>`).join("")}</nav>
        <div class="hm-fill"></div>
        <button class="hm-tab hm-close" title="Close Home (Esc)">${icon("cancel")}<span>Close</span></button>
      </div>
      <section class="hm-panel"></section>`;
    for (const b of box.querySelectorAll(".hm-tab[data-tab]")) b.onclick = () => { this.homeTab = b.dataset.tab; this.homeRender(); };
    box.querySelector(".hm-close").onclick = () => this.homeHide();
    const tab = tabs.find((t) => t.id === this.homeTab);
    const panel = box.querySelector(".hm-panel");
    Promise.resolve(tab.render.call(this, panel)).catch((e) => { panel.innerHTML = `<p class="hm-empty">${escapeHtml(String(e?.message || e))}</p>`; });
  },

  async homeLocal(panel) {
    panel.innerHTML = `<h1>Local files</h1>
      <div class="hm-actions">
        <button class="hm-action" data-act="part">${icon("part")}<b>New Part</b><span>An empty part</span></button>
        <button class="hm-action" data-act="assembly">${icon("assembly")}<b>New Assembly</b><span>Put parts together</span></button>
        <button class="hm-action" data-act="open">${icon("open")}<b>Open…</b><span>A part, assembly, drawing or CAD file</span></button>
      </div>
      <div class="hm-head"><h2>Recent</h2><button class="btn hm-clear hidden">Clear list</button></div>
      <div class="hm-recent"><p class="hm-empty">Loading…</p></div>
      <h2>Tutorials</h2>
      <div class="hm-tuts">${TUTORIALS.map((t, i) => `<button class="hm-tut" data-i="${i}">${icon("help")}<span>${escapeHtml(t.name)}</span></button>`).join("")}</div>`;
    // (Home stays up until the new or opened document is in: homeCheck hides it then. Cancelling Open keeps it.)
    const act = { part: () => this.newPart(), assembly: () => this.newAssembly(), open: () => this.openFile() };
    for (const b of panel.querySelectorAll(".hm-action")) b.onclick = () => act[b.dataset.act]();
    for (const b of panel.querySelectorAll(".hm-tut")) b.onclick = () => { this.homeHide(); this.tutorialDialog(+b.dataset.i); };
    panel.querySelector(".hm-clear").onclick = async () => { await this.api("/api/command", { name: "clear_recent_files", args: {} }); this.homeRecent(panel); };
    await this.homeRecent(panel);
  },

  async homeRecent(panel) {
    const box = panel.querySelector(".hm-recent");
    const r = await this.api("/api/command", { name: "recent_files", args: {} }).catch(() => null);
    const list = r?.ok ? r.result.recent : [];
    panel.querySelector(".hm-clear").classList.toggle("hidden", !list.length);
    if (!list.length) { box.innerHTML = `<p class="hm-empty">No recent files yet. Files you open or save appear here.</p>`; return; }
    box.innerHTML = `<table class="hm-files"><thead><tr><th>Name</th><th>Folder</th><th>Opened</th><th></th></tr></thead><tbody>${list.map((f, i) => `
      <tr class="hm-file${f.exists ? "" : " missing"}" data-i="${i}" title="${escapeHtml(f.exists ? f.path : `Not found: ${f.path}`)}">
        <td><span class="hm-name">${icon(KIND_ICON[f.kind] || "part")}${escapeHtml(f.name)}</span></td>
        <td class="hm-folder">${escapeHtml(f.folder)}</td><td class="hm-when">${escapeHtml(f.exists ? when(f.opened_at) : "Not found")}</td>
        <td><button class="btn hm-remove" data-i="${i}" title="Remove from the list (the file is not deleted)">Remove</button></td></tr>`).join("")}</tbody></table>`;
    for (const row of box.querySelectorAll(".hm-file")) {
      const f = list[+row.dataset.i];
      row.onclick = (e) => { if (!e.target.closest(".hm-remove") && f.exists) this.homeOpenRecent(f.path); };
    }
    for (const b of box.querySelectorAll(".hm-remove")) {
      b.onclick = async () => { await this.api("/api/command", { name: "clear_recent_files", args: { path: list[+b.dataset.i].path } }); this.homeRecent(panel); };
    }
  },

  async homeOpenRecent(path) {
    const r = await this.run("open_part", { path }).catch(() => null);
    if (!r) return; // (the error is shown; Home stays)
    this.homeHide();
    if (r.result?.solidworks_file && !r.result.model) {
      this.modal("SOLIDWORKS file", `<p>Read the SOLIDWORKS container, but no geometry could be opened from it.</p>`, null, "open");
      return;
    }
    this.vp.zoomFit();
  },
};

export function installHome(App) {
  for (const [k, fn] of Object.entries(MIXIN)) App.prototype[k] = fn;
  // tabs of the Home page: others add theirs with this.homeTabs.push({ id, label, icon, render })
  App.prototype.homeTabs = [LOCAL_TAB];
}
