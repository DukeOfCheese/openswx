import * as THREE from "three";
import { SPECS, el, escapeHtml } from "./propman.js";
import { icon } from "./icons.js";

const $ = (s) => document.querySelector(s);
const PRESENCE_MS = 50; // presence out: ~20 a second at most
const HOME_REFRESH_MS = 10000; // Home's Cloud tab: who is in each part, asked for again this often while it is up
const GLIDE_MS = 90; // a pointer eases toward where its owner's pointer now is (time constant)
const ARROW_SVG = '<svg width="18" height="22" viewBox="0 0 18 22"><path d="M2 2 L2 18 L6.5 14 L9.5 20.5 L12.5 19.2 L9.6 12.8 L15.5 12.5 Z" fill="var(--c)" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>';
// readable text on a person's colour: dark on light colours
const inkOn = (c) => { const n = hex(c), l = 0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255); return l > 165 ? "#1b1f24" : "#fff"; };
const reducedMotion = () => { try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; } };
const DRAFT_MS = 300; // someone's feature draft: a preview at most every 300 ms per person
const PREVIEWS_KEY = "openswx.collab.previews";
const GHOST_EXTRA = ["variable_fillet"]; // (a panel's other command: Fillet's commandFor)
const BIG_COUNT = /count|number|instances|copies|segments|steps|^n\d?$/i; // (pattern sizes: a ghost stays cheap)
const STATE_LABEL = { online: "Online", connecting: "Connecting…", offline: "Offline", signed_out: "Signed out: sign in again",
  gone: "Removed from the server", error: "Not connected", closed: "Stopped", protocol: "Needs a newer openswx",
  bad_hello: "Refused by the server", replaced: "Opened elsewhere with this account" };
const LOCK_SVG = '<svg viewBox="0 0 12 12"><rect x="2.5" y="5.5" width="7" height="5" rx="1" fill="#fff"/><path d="M4 5.5V4a2 2 0 0 1 4 0v1.5" stroke="#fff" stroke-width="1.4" fill="none"/></svg>';
const esc = (s) => escapeHtml(String(s ?? ""));
const colour = (c) => (typeof c === "string" && /^#[0-9a-f]{6}$/i.test(c) ? c : "#7a8594"); // (a person's colour, from the server)
const hex = (c) => parseInt(colour(c).slice(1), 16);
const r3 = (v) => v.map((x) => Math.round(x * 1000) / 1000);
const initials = (name) => (String(name || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("") || "?").toUpperCase();
const words = (s) => String(s || "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
const when = (t) => { const d = typeof t === "number" ? new Date(t * 1000) : new Date(t); return isNaN(d) ? "" : d.toLocaleString(); };
const finite = (v, n) => Array.isArray(v) && v.length === n && v.every((x) => typeof x === "number" && Number.isFinite(x));
// a draft's arguments: plain JSON (no prototypes, no functions), small, and no huge pattern counts
function plainArgs(v, key = "", depth = 0) {
  if (depth > 6) return false;
  if (v === null || typeof v === "boolean") return true;
  if (typeof v === "number") return Number.isFinite(v) && !(BIG_COUNT.test(key) && Math.abs(v) > 100);
  if (typeof v === "string") return v.length <= 500;
  if (Array.isArray(v)) return v.length <= 500 && v.every((x) => plainArgs(x, key, depth + 1));
  if (typeof v === "object" && Object.getPrototypeOf(v) === Object.prototype) {
    const keys = Object.keys(v);
    return keys.length <= 100 && keys.every((k) => plainArgs(v[k], k, depth + 1));
  }
  return false;
}
const storage = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* (no storage) */ } } };

const MIXIN = {
  initCollab() {
    const groups = {};
    for (const k of ["sel", "drafts", "ghost"]) { groups[k] = new THREE.Group(); this.vp.scene.add(groups[k]); }
    this.cl = { rev: -1, all: [], groups, markers: new Map(), ghosts: new Map(), held: { sketch: null, pm: null },
      page: (crypto.randomUUID?.() || `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`), // (this page: its locks)
      pointer: null, mouse: null, mouseMoved: false, skDraft: null, pmDraft: null, sentP: "", sentD: "null", inFlight: false,
      follow: null, followCam: null, seen: {}, primed: new Set(), drops: {}, texts: new Map(),
      previews: storage.get(PREVIEWS_KEY) !== "off" };
    const bar = el("div", "hidden"); bar.id = "collabbar"; this.graphics.append(bar); this.cl.bar = bar;
    bar.onclick = (e) => {
      const av = e.target.closest(".cl-av"); if (av) return this.collabFollow(this.cl.follow === av.dataset.client ? null : av.dataset.client);
      if (e.target.closest(".cl-stop")) this.collabFollow(null);
    };
    const layer = el("div", ""); layer.id = "collab-layer"; this.graphics.append(layer); this.cl.layer = layer;
    this.vp.frameHooks.push(() => this.collabPlaceMarkers());
    this.vp.on("move", (e) => { this.cl.mouse = { x: e.x, y: e.y }; this.cl.mouseMoved = true; });
    this.vp.on("leave", () => { this.cl.mouse = null; this.cl.mouseMoved = true; });
    // any camera input of your own stops following someone's view
    for (const m of ["rotateBy", "panBy", "zoomAt", "setView", "normalTo", "zoomFit", "zoomToRect", "zoomToItems", "applyView", "previousView"]) {
      const orig = this.vp[m];
      this.vp[m] = (...a) => { if (this.cl.follow && !this.cl.applyingCam) this.collabFollow(null); return orig.apply(this.vp, a); };
    }
    // a refused change: the conflict toast says who and what, so the plain error toast of the same text goes
    new MutationObserver((recs) => {
      for (const r of recs) for (const n of r.addedNodes) {
        if (n.classList?.contains("err") && !n.classList.contains("cl-conflict") && this.cl.texts.has(n.textContent)) n.remove();
      }
    }).observe($("#toasts"), { childList: true });
    // leaving the page frees its locks (and should the page crash, openswx frees them when its event stream ends)
    window.addEventListener("pagehide", () => {
      if (this.cl.noPagehide) return; // (tests: as if the tab crashed)
      for (const h of Object.values(this.cl.held)) {
        if (h) fetch("/api/command", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: "collab_unlock", args: { features: [h.target], document: h.doc, owner: this.cl.page } }) }).catch(() => {});
      }
    });
    setInterval(() => this.collabTick(), PRESENCE_MS);
    this.refreshCollab();
  },

  // the collaboration state of the active document (null: it is not a cloud document)
  collabDoc() {
    const name = this.model?.name;
    return (name && this.cl?.all.find((d) => d.document === name)) || null;
  },

  async refreshCollab() {
    const cl = this.cl;
    if (cl.fetching) { cl.again = true; return; }
    cl.fetching = true;
    try {
      do {
        cl.again = false;
        const st = await this.api("/api/collab");
        cl.rev = st.revision; cl.all = st.documents || [];
        this.collabApply(); // (each time: what is shown never lags behind cl.all)
      } while (cl.again);
    } catch { /* (the event stream says when to try again) */ } finally { cl.fetching = false; }
  },

  // everything on screen that comes from the collaboration state
  collabApply() {
    if (!this.cl) return;
    const d = this.collabDoc();
    this.collabRenderBar(d);
    if (this.model) this.renderQuickAccessState();
    const sig = d ? JSON.stringify([d.locks, d.broken, (d.peers || []).map((p) => [p.client_id, p.colour])]) : "";
    if (sig !== this.cl.treeSig) { this.cl.treeSig = sig; if (this.model) this.renderTree(); }
    this.collabConflicts(d);
    if (d) this.collabNames().then((names) => this.collabDrawPeers(this.collabDoc(), names)).catch(() => {});
    else this.collabDrawPeers(null, null);
    if (this.cl.follow) {
      const p = d?.peers.find((x) => x.client_id === this.cl.follow);
      if (!p) this.collabFollow(null);
      else if (p.presence?.camera) { this.cl.followCam = p.presence.camera; this.collabFollowStep(); }
    }
  },

  // persistent face and edge names of the shown model (they stay valid across edits; Face3 does not)
  collabNames() {
    const cl = this.cl, v = this.version;
    if (cl.names?.version === v) return Promise.resolve(cl.names);
    if (cl.namesP?.version === v) return cl.namesP.p;
    const p = this.api("/api/command", { name: "get_topology", args: {} }).then((r) => {
      const f2n = new Map(), n2f = new Map();
      for (const x of r.ok ? [...r.result.faces, ...r.result.edges] : []) if (x.name) { f2n.set(x.id, x.name); n2f.set(x.name, x.id); }
      cl.names = { version: v, f2n, n2f };
      return cl.names;
    });
    cl.namesP = { version: v, p };
    return p;
  },

  collabRenderBar(d) {
    const cl = this.cl, bar = cl.bar;
    if (!d) { bar.classList.add("hidden"); cl.barHtml = ""; return; }
    const st = d.state;
    let state = STATE_LABEL[st] || st;
    if (d.pending) state += st === "online" ? ` · sending ${d.pending}` : ` · ${d.pending} change${d.pending > 1 ? "s" : ""} waiting`;
    const peers = (d.peers || []).filter((p) => !p.you);
    const followed = peers.find((p) => p.client_id === cl.follow);
    const html = `<span class="cl-state cl-${esc(st)}" title="${esc(d.error || `${d.cloud_name}: live collaboration`)}"><i></i>${esc(state)}</span>`
      + (d.read_only ? `<span class="cl-ro" title="You can watch, not change">Read-only${d.read_only_reason ? `: ${esc(d.read_only_reason)}` : ""}</span>` : "")
      + `<span class="cl-peers">${peers.map((p) => {
        const tool = p.presence?.tool || "";
        return `<button class="cl-av${p.client_id === cl.follow ? " following" : ""}" data-client="${esc(p.client_id)}" style="--c:${colour(p.colour)}"
          title="${esc(p.name)}${tool ? ` — ${esc(tool)}` : ""}${p.read_only ? " (viewing)" : ""}&#10;Click to ${p.client_id === cl.follow ? "stop following" : "follow their view"}">${esc(initials(p.name))}</button>`
          + (tool ? `<span class="cl-tool" data-client="${esc(p.client_id)}">${esc(tool)}</span>` : "");
      }).join("")}</span>`
      + (peers.length ? "" : `<span class="cl-alone">Nobody else here</span>`)
      + (followed ? `<span class="cl-following" style="--c:${colour(followed.colour)}">Following ${esc(followed.name)} <button class="cl-stop">Stop</button></span>` : "");
    if (html !== cl.barHtml) { bar.innerHTML = html; cl.barHtml = html; }
    bar.classList.remove("hidden");
  },

  collabFollow(clientId) {
    const cl = this.cl;
    cl.follow = clientId || null; cl.followCam = null;
    if (cl.follow) {
      const p = this.collabDoc()?.peers.find((x) => x.client_id === cl.follow);
      if (p?.presence?.camera) { cl.followCam = p.presence.camera; this.collabFollowStep(); }
      else if (p) this.toast(`${esc(p.name)} has not moved their view yet: it follows when they do`);
    }
    this.collabRenderBar(this.collabDoc());
  },

  // ease the camera towards the followed person's (a fifth of the way each frame)
  collabFollowStep() {
    const cl = this.cl;
    if (cl.followRaf) return;
    const step = () => {
      cl.followRaf = null;
      const c = cl.followCam, vp = this.vp;
      if (!cl.follow || !c || !finite(c.q, 4) || !finite(c.t, 3) || !(Number.isFinite(c.h) && c.h > 0)) return; // (a bad camera: not followed)
      const q = new THREE.Quaternion(...c.q).normalize(), t = new THREE.Vector3(...c.t), k = 0.2;
      cl.applyingCam = true;
      try {
        if (!!c.perspective !== !!vp.perspective) vp.setPerspective(!!c.perspective);
        vp.camera.quaternion.slerp(q, k); vp.target.lerp(t, k); vp.viewHeight += (c.h - vp.viewHeight) * k;
        vp._updateProjection(); vp._placeCamera();
      } finally { cl.applyingCam = false; }
      const done = vp.camera.quaternion.angleTo(q) < 1e-4 && vp.target.distanceTo(t) < c.h * 1e-4 && Math.abs(vp.viewHeight - c.h) < c.h * 1e-4;
      if (!done) cl.followRaf = requestAnimationFrame(step);
    };
    cl.followRaf = requestAnimationFrame(step);
  },

  collabToolLabel() {
    if (this.sk.active) return this.sk.tool && this.sk.tool !== "select" ? `${words(this.sk.tool)} in ${this.sk.name}` : `Editing ${this.sk.name}`;
    if (this.pm.active) return this.editing ? `Editing ${this.editing}` : this.pm.spec?.title || null;
    return null;
  },

  async collabTick() {
    const cl = this.cl, d = this.collabDoc();
    if (!d || !d.connected || cl.inFlight) return;
    cl.inFlight = true; // (one at a time: a tick while one is on its way does nothing)
    try { await this.collabSendPresence(d); } catch { /* (the next tick tries again) */ } finally { cl.inFlight = false; }
  },

  async collabSendPresence(d) {
    const cl = this.cl;
    if (cl.sentDoc !== d.document_id) { cl.sentDoc = d.document_id; cl.sentP = ""; cl.sentD = "null"; } // (another cloud document: all of it again)
    const names = await this.collabNames();
    if (cl.mouseMoved) {
      cl.mouseMoved = false;
      const m = cl.mouse; let ptr = null;
      if (m) {
        const pk = this.vp.pick(m.x, m.y, { kinds: ["face"] });
        if (pk) ptr = { point: r3(pk.point), face: names.f2n.get(pk.id) || pk.id };
        else if (this.sk.active && this.sk.O) {
          const uv = this.sk.uvAt(m.x, m.y);
          if (uv) ptr = { point: r3(this.sk.w(uv[0], uv[1])), sketch: this.sk.name };
        }
        if (!ptr) { // over empty background: where the ray meets the plane through the view's centre, facing you
          const n = this.vp.camera.getWorldDirection(new THREE.Vector3());
          const hit = this.vp.ray(m.x, m.y).ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(n, this.vp.target), new THREE.Vector3());
          if (hit && [hit.x, hit.y, hit.z].every(Number.isFinite)) ptr = { point: r3(hit.toArray()), plane: true };
        }
      }
      cl.pointer = ptr;
    }
    const v = this.vp.savedViewData();
    const p = {
      pointer: cl.pointer || undefined,
      selection: this.selection.filter((s) => ["face", "edge", "feature"].includes(s.kind)).map((s) => names.f2n.get(s.id) || s.id),
      tool: this.collabToolLabel() || undefined,
      camera: { q: v.quaternion.map((x) => Math.round(x * 1e5) / 1e5), t: r3(v.target), h: Math.round(v.height * 1000) / 1000, perspective: v.perspective },
    };
    const draft = cl.skDraft || cl.pmDraft || null;
    const pj = JSON.stringify(p), dj = JSON.stringify(draft);
    if (pj === cl.sentP && dj === cl.sentD) return; // (nothing changed: nothing is sent)
    const args = JSON.parse(pj);
    if (dj !== cl.sentD) { if (draft) args.draft = draft; else args.clear_draft = true; }
    const r = await this.api("/api/collab/presence", { args }); // (not through the command lock: never waits on a rebuild)
    if (r.ok) { cl.sentP = pj; cl.sentD = dj; }
  },

  // the sketch tool's rubber band as a draft for the others: polylines in sketch coordinates (null: none)
  collabSketchDraft(items) {
    const cl = this.cl;
    if (!cl) return;
    if (!items || !this.sk.active || !this.sk.O || !this.collabDoc()) { cl.skDraft = null; return; }
    const O = this.sk.O, X = this.sk.X, Y = this.sk.Y;
    const lines = items.filter((it) => !it.point && it.color === undefined).map((it) => { // (not the snap and inference hints)
      const p = [];
      for (let i = 0; i + 2 < it.pts.length; i += 3) {
        const d = [it.pts[i] - O[0], it.pts[i + 1] - O[1], it.pts[i + 2] - O[2]];
        p.push(Math.round((d[0] * X[0] + d[1] * X[1] + d[2] * X[2]) * 1000) / 1000, Math.round((d[0] * Y[0] + d[1] * Y[1] + d[2] * Y[2]) * 1000) / 1000);
      }
      return { p, dashed: !!it.dashed };
    }).filter((l) => l.p.length >= 4);
    cl.skDraft = lines.length ? { kind: "sketch", sketch: this.sk.name, tool: this.sk.tool, lines } : null;
  },

  // an open feature panel's command and parameters, not yet confirmed, as a draft for the others
  collabPanelDraft(args) {
    const cl = this.cl, spec = this.pm.spec;
    if (!cl || !this.collabDoc() || !spec || spec.noPreview) { if (cl) cl.pmDraft = null; return; }
    const command = spec.commandFor ? spec.commandFor(this.pm.values) : spec.command;
    let small = null;
    try { small = JSON.parse(JSON.stringify(args ?? {})); if (JSON.stringify(small).length > 60000) small = null; } catch { small = null; }
    cl.pmDraft = small ? { kind: "feature", command, args: small, editing: this.editing || null, title: spec.title || command } : null;
  },

  collabPanelClosed() {
    if (!this.cl) return;
    this.cl.pmDraft = null;
    this.collabRelease("pm");
  },

  // Opening a sketch or a feature's panel on a cloud document locks it; when someone else holds the lock it
  // does not open (a toast says who), as SOLIDWORKS PDM does with a checked-out file.
  async collabLock(name, slot) {
    const d = this.collabDoc();
    if (!d) return true;
    const f = this.model.features.find((x) => x.name === name);
    if (!f) return true;
    const target = `f:${f.id}`, held = this.cl.held[slot];
    if (held && held.target === target && held.doc === d.document_id) return true;
    if (d.read_only) { this.toast(`This cloud document is read-only for you${d.read_only_reason ? ` (${d.read_only_reason})` : ""}: ${name} cannot be changed`, "err"); return false; }
    const r = await this.api("/api/command", { name: "collab_lock", args: { features: [target], document: d.document_id, owner: this.cl.page } })
      .catch((e) => ({ ok: false, error: String(e) }));
    if (!r.ok) { this.toast(`${name} cannot be opened for editing: ${r.error}`, "err"); return false; }
    const who = r.result.denied?.[target];
    if (who) {
      this.toast(`<b>${esc(name)}</b> is being edited by <b>${esc(who)}</b>: it can be opened when they close it`, "cl-locked");
      return false;
    }
    this.collabRelease(slot);
    this.cl.held[slot] = { target, doc: d.document_id, name };
    this.refreshCollab();
    return true;
  },

  // free the lock held for a sketch or a panel (only if it is the one for `name`, when given), on the cloud
  // document it was taken on, whichever document is active now
  collabRelease(slot, name = null) {
    const cl = this.cl, h = cl?.held[slot];
    if (!h || (name !== null && h.name !== name)) return;
    cl.held[slot] = null;
    if (Object.values(cl.held).some((o) => o && o.target === h.target && o.doc === h.doc)) return;
    if (!cl.all.some((d) => d.document_id === h.doc)) return; // (no longer open: its locks went with the connection)
    this.api("/api/command", { name: "collab_unlock", args: { features: [h.target], document: h.doc, owner: cl.page } }).catch(() => {});
  },

  // FeatureManager row decorations: who is editing it (their colour; a folded row also shows its absorbed
  // sketch's), and who broke it
  collabTreeBadge(row, f) {
    const d = this.collabDoc();
    if (!d || !f.id) return;
    const kids = this.expanded.has(f.name) ? [] : (this.model.features || []).filter((k) => k.absorbed_by === f.name);
    for (const g of [f, ...kids]) {
      const lk = d.locks?.[`f:${g.id}`];
      if (!lk || lk.mine) continue;
      const peer = (d.peers || []).find((p) => p.client_id === lk.client_id);
      const b = el("span", "cl-lock", LOCK_SVG);
      b.style.setProperty("--c", colour(peer?.colour));
      b.dataset.who = lk.name || ""; b.dataset.target = g.name;
      b.title = `${lk.name || "Someone"} is editing ${g.name}${lk.detached ? " (their connection dropped: the lock lapses within a minute)" : ""}`;
      row.append(b); row.classList.add("cl-locked");
      break;
    }
    const br = d.broken?.[f.id];
    if (br) {
      const who = br.by_name || "someone";
      const b = el("span", "cl-broken", `broken by ${esc(who)}`);
      b.title = `${f.name} broken by ${who}'s ${br.command || "change"}${br.error ? `: ${br.error}` : ""}`;
      row.append(b); row.title = b.title; row.classList.add("error");
    }
  },

  collabDrawPeers(d, names) {
    const cl = this.cl;
    const peers = d ? (d.peers || []).filter((p) => !p.you) : [];
    // pointers: an arrow in their colour with a name bubble (and the tool), gliding to their point on the model
    const live = new Set();
    for (const p of peers) {
      live.add(p.client_id);
      let m = cl.markers.get(p.client_id);
      if (!m) {
        m = el("div", "cl-ptr gone", `${ARROW_SVG}<span><b></b><em></em></span>`);
        cl.layer.append(m); cl.markers.set(p.client_id, m);
      }
      m.style.setProperty("--c", colour(p.colour));
      m.style.setProperty("--ink", inkOn(p.colour));
      const tool = p.presence?.tool;
      m.querySelector("b").textContent = p.name;
      m.querySelector("em").textContent = tool ? ` · ${tool}` : "";
      m.dataset.client = p.client_id;
      const ptr = p.presence?.pointer, pt = ptr?.point;
      if (finite(pt, 3)) {
        if (!m.target || !m.shown) m.shown = new THREE.Vector3(...pt); // (first sight: appears where it is)
        m.target = new THREE.Vector3(...pt);
        m.plane = ptr.plane === true;
      } else m.target = null;
    }
    for (const [id, m] of cl.markers) if (!live.has(id)) { m.remove(); cl.markers.delete(id); }
    this.collabDrawSelections(peers, names);
    this.collabDrawSketchDrafts(peers);
    this.collabGhosts(peers);
    this.vp.dirty = true;
  },

  collabPlaceMarkers() {
    const now = performance.now(), cl = this.cl;
    const dt = Math.min(now - (cl.glideAt || now), 100); cl.glideAt = now;
    const snap = reducedMotion(), k = 1 - Math.exp(-dt / GLIDE_MS);
    let moving = false;
    for (const m of cl.markers.values()) {
      if (!m.target) { m.classList.add("gone"); continue; } // (fades out: their pointer left the view)
      m.classList.remove("gone");
      // glide in 3D (so orbiting your own view never makes it lag); a long jump snaps
      const gap = m.shown.distanceTo(m.target);
      if (snap || gap > this.vp.viewHeight * 0.5) m.shown.copy(m.target);
      else if (gap > this.vp.viewHeight * 1e-4) { m.shown.lerp(m.target, k); moving = true; }
      else m.shown.copy(m.target);
      const [x, y, z] = this.vp.toScreen(m.shown.toArray());
      const off = z > 1 || x < -20 || y < -20 || x > this.vp.w + 20 || y > this.vp.h + 20;
      m.style.display = off ? "none" : "";
      if (off) continue;
      m.style.transform = `translate(${(x - 2).toFixed(1)}px, ${(y - 2).toFixed(1)}px)`; // (the arrow's tip on the point)
      if (m.plane) { m.classList.remove("behind"); continue; } // (in the air in front of the model: never behind it)
      if (now - (m.occAt || 0) > 150) { // behind the model (from here): faint (a ray at most every 150 ms each)
        m.occAt = now;
        const ray = this.vp.ray(x, y), hit = ray.intersectObjects([...this.vp.faces.values()].filter((o) => o.visible), false)[0];
        const far = ray.ray.origin.distanceTo(m.shown);
        m.classList.toggle("behind", !!hit && hit.distance < far - Math.max(this.vp.viewHeight * 0.01, 1e-3));
      }
    }
    if (moving) this.vp.dirty = true; // (another frame, until every pointer has arrived)
    else cl.glideAt = 0;
  },

  collabClear(g) {
    while (g.children.length) { const o = g.children.pop(); o.traverse?.((x) => { x.geometry?.dispose(); x.material?.dispose?.(); }); }
  },

  // their selected faces (tinted and outlined) and edges, in their colour
  collabDrawSelections(peers, names) {
    const cl = this.cl;
    const sig = JSON.stringify([this.version, this.mesh?.revision, peers.map((p) => [p.colour, p.presence?.selection || []])]);
    if (sig === cl.selSig) return;
    cl.selSig = sig;
    const g = cl.groups.sel; this.collabClear(g);
    for (const p of peers) {
      const col = hex(p.colour);
      for (const n of p.presence?.selection || []) {
        const id = names?.n2f.get(n) || n;
        const face = this.vp.faces.get(id), edge = this.vp.edges.get(id);
        if (face) {
          const fill = new THREE.Mesh(face.geometry.clone(), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.3,
            depthWrite: false, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }));
          const out = new THREE.LineSegments(new THREE.EdgesGeometry(face.geometry, 30), new THREE.LineBasicMaterial({ color: col }));
          fill.renderOrder = 6; out.renderOrder = 7; fill.userData = out.userData = { client: p.client_id, name: n };
          g.add(fill, out);
        } else if (edge) {
          const l = new THREE.Line(edge.geometry.clone(), new THREE.LineBasicMaterial({ color: col, depthTest: false }));
          l.renderOrder = 7; l.userData = { client: p.client_id, name: n }; g.add(l);
        }
      }
    }
  },

  // what they are drawing in a sketch: dashed, in their colour, on that sketch's plane
  collabDrawSketchDrafts(peers) {
    const cl = this.cl;
    const drafts = peers.map((p) => [p, cl.previews && p.draft?.kind === "sketch" && typeof p.draft.sketch === "string" && Array.isArray(p.draft.lines) ? p.draft : null]);
    const sig = JSON.stringify([this.mesh?.revision, this.version, drafts.map(([p, d]) => [p.colour, d])]);
    if (sig === cl.draftSig) return;
    cl.draftSig = sig;
    const g = cl.groups.drafts; this.collabClear(g);
    const dash = Math.max(this.vp.viewHeight / 90, 0.05);
    for (const [p, d] of drafts) {
      const ov = d && (this.mesh?.sketches || []).find((s) => s.name === d.sketch);
      if (!ov) continue;
      const O = ov.origin, X = ov.x, Z = ov.z, Y = [Z[1] * X[2] - Z[2] * X[1], Z[2] * X[0] - Z[0] * X[2], Z[0] * X[1] - Z[1] * X[0]];
      for (const ln of d.lines.slice(0, 200)) {
        if (!ln || !Array.isArray(ln.p) || ln.p.length > 20000) continue; // (malformed: skipped, the rest still drawn)
        const pts = [];
        for (let i = 0; i + 1 < ln.p.length; i += 2) {
          const u = ln.p[i], v = ln.p[i + 1];
          if (typeof u !== "number" || typeof v !== "number") { pts.length = 0; break; }
          pts.push(...[0, 1, 2].map((k) => O[k] + X[k] * u + Y[k] * v));
        }
        if (pts.length < 6 || pts.some((x) => !isFinite(x))) continue;
        const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
        const line = new THREE.Line(geo, new THREE.LineDashedMaterial({ color: hex(p.colour), dashSize: dash, gapSize: dash * 0.7, depthTest: false }));
        line.computeLineDistances(); line.renderOrder = 10; line.userData = { client: p.client_id, sketch: d.sketch };
        g.add(line);
      }
    }
  },

  // their open feature panel: the feature as it would come out, translucent in their colour, through the same
  // preview as your own panel. Only a feature command the UI's own panels preview, with plain small arguments, is
  // ever run (a draft comes from someone else); one preview at a time per person, at most every 300 ms, and none
  // while openswx is busy with a command of yours (it gives way at once and tries again).
  collabGhostRequest(d) {
    const args = d?.args ?? {};
    if (!d || typeof d.command !== "string" || typeof args !== "object" || Array.isArray(args) || !plainArgs(args) || JSON.stringify(args).length > 20000) return null;
    const allowed = new Set([...Object.values(SPECS).filter((s) => s && !s.noPreview && typeof s.command === "string").map((s) => s.command), ...GHOST_EXTRA]);
    if (!allowed.has(d.command)) return null;
    if (d.editing != null && !(typeof d.editing === "string" && this.model?.features.some((f) => f.name === d.editing && f.type !== "sketch"))) return null;
    return d.editing ? { name: "redefine_feature", args: { name: d.editing, command_name: d.command, args: d.args ?? {} }, if_idle: true }
      : { name: d.command, args: d.args ?? {}, if_idle: true };
  },

  collabGhosts(peers) {
    const cl = this.cl, live = new Set();
    for (const p of peers) {
      live.add(p.client_id);
      const req = cl.previews && p.draft?.kind === "feature" ? this.collabGhostRequest(p.draft) : null;
      let g = cl.ghosts.get(p.client_id);
      if (!g) { g = { key: "", req: null, last: 0, timer: null, busy: false, group: new THREE.Group() }; cl.groups.ghost.add(g.group); cl.ghosts.set(p.client_id, g); }
      g.colour = hex(p.colour); g.client = p.client_id;
      const key = req ? JSON.stringify([req, this.version]) : "";
      if (key === g.key) continue;
      g.key = key; g.req = req;
      if (!key) { clearTimeout(g.timer); g.timer = null; this.collabClear(g.group); this.vp.dirty = true; continue; }
      this.collabGhostSchedule(g);
    }
    for (const [id, g] of cl.ghosts) if (!live.has(id)) { clearTimeout(g.timer); g.key = ""; this.collabClear(g.group); cl.groups.ghost.remove(g.group); cl.ghosts.delete(id); }
  },

  collabGhostSchedule(g, wait = 0) {
    if (g.busy || g.timer) return; // (the one on its way, or the one waiting, picks up the latest)
    g.timer = setTimeout(() => this.collabGhostRun(g), Math.max(wait, DRAFT_MS - (performance.now() - g.last)));
  },

  async collabGhostRun(g) {
    g.timer = null;
    const key = g.key, req = g.req;
    if (!key || !req) return;
    g.busy = true;
    let res = null;
    try { res = await this.api("/api/preview", req); } catch { res = null; } finally { g.busy = false; g.last = performance.now(); }
    if (res?.busy) { this.collabGhostSchedule(g, 1000); return; } // (openswx is busy with your command: later)
    if (g.key !== key) { if (g.key) this.collabGhostSchedule(g); return; } // (they changed it meanwhile: the latest)
    this.collabClear(g.group);
    for (const f of res?.ok ? res.faces || [] : []) {
      const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(f.v, 3));
      geo.setIndex(f.t); geo.computeVertexNormals();
      const m = new THREE.Mesh(geo, new THREE.MeshPhongMaterial({ color: g.colour, transparent: true, opacity: 0.16, depthWrite: false, depthTest: false, side: THREE.DoubleSide }));
      m.renderOrder = 8; g.group.add(m);
    }
    for (const e of res?.ok ? res.edges || [] : []) { // (its edges, so the shape reads through the tint)
      const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(e.p, 3));
      const l = new THREE.Line(geo, new THREE.LineBasicMaterial({ color: g.colour, transparent: true, opacity: 0.85, depthTest: false }));
      l.renderOrder = 9; g.group.add(l);
    }
    g.group.userData = { client: g.client, command: req.name, ok: !!res?.ok };
    this.vp.dirty = true;
  },

  collabTogglePreviews() {
    const cl = this.cl;
    cl.previews = !cl.previews; storage.set(PREVIEWS_KEY, cl.previews ? "on" : "off");
    this.toast(cl.previews ? "Others' drafts are shown: the sketch lines they draw, the features they have open" : "Others' drafts are hidden");
    this.collabApply();
  },

  collabConflicts(d) {
    const cl = this.cl;
    if (!d) return;
    const key = d.document_id, seen = (cl.seen[key] ||= new Set());
    const fresh = (d.conflicts || []).filter((c) => c.change_id && !seen.has(c.change_id));
    for (const c of fresh) seen.add(c.change_id);
    if (!cl.primed.has(key)) { cl.primed.add(key); return; } // (refusals from before this page opened: not news)
    if (d.state !== "online" && !cl.drops[key]) cl.drops[key] = { refused: [] };
    const drop = cl.drops[key];
    if (drop) { // offline: what the server refuses when the queued changes go in is listed once, after
      drop.refused.push(...fresh);
      if (d.state === "online" && !d.pending) { delete cl.drops[key]; if (drop.refused.length) this.collabRefusedDialog(drop.refused); }
      return;
    }
    for (const c of fresh) this.collabConflictToast(c);
  },

  collabConflictToast(c) {
    const cl = this.cl, text = c.text || c.message || "";
    cl.texts.set(text, Date.now());
    for (const [t, at] of cl.texts) if (Date.now() - at > 60000) cl.texts.delete(t);
    for (const t of document.querySelectorAll("#toasts .toast.err:not(.cl-conflict)")) if (t.textContent === text) t.remove();
    const who = (c.who || []).join(" and ") || "Someone", what = (c.features || []).join(", ") || "the same thing";
    const head = c.code === "locked" ? `${who} is editing ${what}` : c.code === "read_only" ? "This document is read-only for you" : `${who} changed ${what} first`;
    const t = el("div", "toast err cl-conflict", `<b>Not applied: ${esc(head)}</b><div>${esc(text)}</div>`);
    $("#toasts").append(t);
    setTimeout(() => t.remove(), 9000);
  },

  collabRefusedDialog(list) {
    const rows = list.map((c) => `<tr><td>${esc(c.command || "change")}</td><td>${esc((c.who || []).join(", ") || "—")}</td><td>${esc(c.text || c.message)}</td></tr>`).join("");
    this.modal("Changes not applied", `<p>While you were offline, others changed the same things. These changes of yours were not applied;
      the model shows their version:</p><table class="dw-bom cl-refused"><tr><th>Your change</th><th>Changed first by</th><th>Why</th></tr>${rows}</table>`, null, "warning");
  },

  // (signing in, opening and creating cloud parts, and the team are on Home's Cloud tab)
  collabMenu() {
    const d = this.collabDoc();
    const peers = d ? (d.peers || []).filter((p) => !p.you) : [];
    const label = (s) => String(s || "?").replace(/>/g, "›").slice(0, 60); // (" > " makes a submenu)
    return [
      ...(d ? [["Stop Sharing...", "", () => this.collabStopDialog()], "-",
        ["History and Versions...", "", () => this.collabHistoryDialog()], ["Name Version...", "", () => this.collabNameVersionDialog()],
        ...peers.map((p) => [`Follow > ${this.cl.follow === p.client_id ? "✓ " : ""}${label(p.name)}`, "",
          () => this.collabFollow(this.cl.follow === p.client_id ? null : p.client_id)])]
        : [["Move to Cloud...", "", () => this.collabShareDialog()]]),
      "-", ["Cloud Files...", "", () => this.homeShow("cloud")],
      [`${this.cl?.previews ? "✓ " : ""}Show Others' Drafts`, "", () => this.collabTogglePreviews()]];
  },

  collabNameVersionDialog() {
    const d = this.collabDoc();
    if (!d) return;
    this.modal("Name Version", `<div class="field"><label>Name</label><input id="cl-vname" placeholder="e.g. Rev A" maxlength="200"></div>
      <div class="field"><label>Note</label><input id="cl-vnote" placeholder="Optional" maxlength="2000"></div>
      <p class="muted">Names the current state of <b>${esc(d.cloud_name || d.document)}</b> for everyone: assemblies can use it, and History can go back to it.</p>`, async () => {
      const name = $("#cl-vname").value.trim();
      if (!name) { this.toast("Give the version a name", "err"); throw new Error("no name"); }
      const v = await this.collabCmd("collab_name_version", { name, note: $("#cl-vnote").value.trim() });
      if (!v) throw new Error("refused");
      this.toast(`Version <b>${esc(v.name)}</b> is the state after change ${esc(v.seq)}`);
    }, "history");
  },

  // Save on a cloud part: there is nothing to save (the server keeps every change); Save As writes a local copy
  collabSave(as) {
    if (!as) return this.toast("Cloud parts save automatically: every change goes to the server. File › Save As saves a local copy.");
    if (this.desk) return this.deskCall("save_as").then((r) => { if (r?.result?.saved) this.toast(`Saved a local copy: ${esc(r.result.saved)}`); });
    this.modal("Save a Local Copy", `<div class="field"><label>File path on this computer</label><input id="savepath" value="${esc(`${this.model.name}.oswprt`)}"></div>
      <p class="muted">The copy is an ordinary local part. This part stays the cloud part, shared with the others.</p>`, async () => {
      const r = await this.run("save_part", { path: $("#savepath").value });
      this.toast(`Saved a local copy: ${esc(r.result.saved)}`);
    }, "save");
  },

  // Home's Cloud tab, when it is the one shown: draw it again (after signing in or out, say)
  collabHomeRefresh() {
    if (this.homeOpen && this.homeTab === "cloud") this.homeRender();
  },

  // What the tab last loaded (organisations and projects, each project's parts, each team) is kept for the server
  // and account it came from: coming back to the tab draws it at once, then asks the server again behind it and
  // draws again only what changed. Signing in as someone else, or on another server, starts afresh.
  collabHomeCache(key) {
    const cl = this.cl;
    if (!cl.hc || (key !== undefined && cl.hc.key !== key)) cl.hc = { key: key ?? null, m: new Map() };
    return cl.hc;
  },

  // draw what is kept under `k` at once, then fetch(); draw again only when the answer differs. fail(error,
  // shown): the fetch failed (shown: something kept is on screen). True when the fetch worked.
  async collabFresh(k, fetch, draw, fail) {
    const m = this.collabHomeCache().m, had = m.get(k);
    if (had) draw(had.value);
    const r = await fetch().catch((e) => ({ ok: false, error: String(e) }));
    if (!r?.ok) { fail(r?.error || "no answer", !!had); return false; }
    const sig = JSON.stringify(r.result);
    if (!had || had.sig !== sig) { m.set(k, { value: r.result, sig }); draw(r.result); }
    this.collabHomeOffline(null);
    return true;
  },

  // the server could not be reached: say so next to the account, over what was last loaded (null: it can)
  collabHomeOffline(error) {
    const n = document.querySelector("#home .cl-stale");
    if (!n) return;
    n.classList.toggle("hidden", !error);
    n.textContent = error ? "Offline: showing what was last loaded" : "";
    n.title = error || "";
  },

  async collabHome(panel) {
    const head = `<h1>Cloud files</h1>`;
    clearInterval(this.cl.homeTimer);
    if (!this.cl.hc?.m.size) panel.innerHTML = `${head}<p class="hm-empty">Loading…</p>`;
    const r = await this.api("/api/command", { name: "collab_status", args: {} }).catch(() => null);
    if (!panel.isConnected) return;
    if (!r?.ok) { panel.innerHTML = `${head}<p class="hm-empty">${esc(r?.error || "Could not read the collaboration status.")}</p>`; return; }
    const st = r.result;
    if (st.available === false) { panel.innerHTML = `${head}<p class="hm-empty">${esc(st.message)}</p>`; return; }
    if (!st.signed_in) { this.cl.hc = null; return this.collabHomeSignIn(panel, st); }
    const u = st.user || {};
    this.collabHomeCache(`${st.server}\n${u.id || u.email || ""}`);
    panel.innerHTML = `${head}
      <div class="cl-acct"><span>Signed in as <b>${esc(u.name || u.email || "you")}</b> <span class="muted">${esc(u.email && u.name ? `${u.email} · ` : "")}${esc(st.server)}</span></span>
        <span class="cl-stale hidden"></span>
        <span class="hm-fill"></span><button class="btn" id="cl-hmeth">Sign-in Methods…</button><button class="btn" id="cl-hout">Sign Out</button></div>
      <div class="cl-home"><p class="hm-empty">Loading…</p></div>
      <h2>Join with an invitation</h2>
      <div class="cl-accept"><input id="cl-code" placeholder="Invitation code or link" autocomplete="off" spellcheck="false"><button class="btn" id="cl-join">Join</button></div>`;
    panel.querySelector("#cl-hmeth").onclick = () => this.collabMethodsDialog();
    panel.querySelector("#cl-hout").onclick = async () => { await this.collabSignOut(); this.collabHomeRefresh(); };
    panel.querySelector("#cl-join").onclick = async () => {
      const code = panel.querySelector("#cl-code").value.trim();
      if (!code) return this.toast("Paste the invitation code or link", "err");
      const j = await this.collabCmd("collab_accept_invite", { code });
      if (!j) return;
      this.toast(`You joined <b>${esc(j.org?.name)}</b> as ${esc(j.role)}`);
      this.cl.homeOrg = j.org?.id; this.cl.homeProject = null; this.collabHomeRefresh();
    };
    const box = panel.querySelector(".cl-home");
    await this.collabFresh("orgs", () => this.api("/api/command", { name: "collab_projects", args: {} }),
      (v) => { if (box.isConnected) this.collabHomeOrgs(box, v.orgs || [], u); },
      (error, shown) => {
        if (!box.isConnected) return;
        if (shown) return this.collabHomeOffline(error);
        box.innerHTML = `<p class="hm-empty">${esc(error)}</p><button class="btn" id="cl-retry">Try Again</button>`;
        box.querySelector("#cl-retry").onclick = () => this.collabHomeRefresh();
      });
    // who is in each part: the shown project's list is asked for again every 10 s while the tab is up
    clearInterval(this.cl.homeTimer);
    this.cl.homeTimer = setInterval(() => {
      if (!panel.isConnected || !this.homeOpen) { clearInterval(this.cl.homeTimer); return; }
      const docs = box.querySelector(".cl-docs");
      if (docs && this.cl.homeProject && this.cl.homeDocOpts) this.collabDocTable(docs, this.cl.homeProject, this.cl.homeDocOpts);
    }, this.cl.homeRefreshMs || HOME_REFRESH_MS); // (tests make it shorter)
  },

  collabHomeSignIn(panel, st) {
    panel.innerHTML = `<h1>Cloud files</h1>
      <p class="muted">Sign in to a collaboration server to open and create parts that your team edits with you, live.</p>
      <div class="cl-signin">
        <div class="field"><label>Server</label><input id="cl-srv" value="${esc(st.server || "")}" placeholder="https://collab.example.com"></div>
        <div id="cl-oauth" class="cl-oauth"></div>
        <div class="field"><label>Email</label><input id="cl-email" type="email" autocomplete="username" value="${esc(st.user?.email || "")}"></div>
        <div class="field"><label>Password</label><input id="cl-pw" type="password" autocomplete="current-password"></div>
        <div><button class="btn primary" id="cl-hsign">Sign In</button></div>
      </div>`;
    const go = async () => {
      const r = await this.run("collab_sign_in", { server: $("#cl-srv").value.trim(), email: $("#cl-email").value.trim(), password: $("#cl-pw").value }).catch(() => null);
      if (!r) return;
      this.toast(`Signed in as <b>${esc(r.result.user?.name || r.result.user?.email || "you")}</b> on ${esc(r.result.server)}`);
      this.collabHomeRefresh();
    };
    panel.querySelector("#cl-hsign").onclick = go;
    panel.querySelector("#cl-pw").onkeydown = (e) => { if (e.key === "Enter") go(); };
    let timer = null;
    $("#cl-srv").oninput = () => { clearTimeout(timer); timer = setTimeout(() => this.collabServerOptions($("#cl-srv")?.value.trim()), 400); };
    if (st.server) this.collabServerOptions(st.server);
  },

  // organisations and their projects on the left; the picked project's parts, and the team, on the right
  collabHomeOrgs(box, orgs, me) {
    const cl = this.cl;
    const newOrg = `<button class="btn cl-neworg">New Organisation…</button>`;
    if (!orgs.length) {
      box.innerHTML = `<p class="hm-empty">Your account is not in any organisation on this server yet. Ask its owner for an invitation (and join below), or start one.</p>${newOrg}`;
      box.querySelector(".cl-neworg").onclick = () => this.collabNewOrgDialog();
      return;
    }
    const all = orgs.flatMap((o) => (o.projects || []).map((p) => ({ ...p, org: o })));
    let proj = all.find((p) => p.id === cl.homeProject) || null;
    let org = orgs.find((o) => o.id === (proj?.org.id || cl.homeOrg)) || orgs[0];
    if (!proj) proj = all.find((p) => p.org.id === org.id) || null;
    cl.homeOrg = org.id; cl.homeProject = proj?.id || null;
    box.innerHTML = `<aside class="cl-orgs">${orgs.map((o) => `
        <div class="cl-org${o.id === org.id ? " open" : ""}"><button class="cl-org-b" data-o="${esc(o.id)}"><b>${esc(o.name)}</b><span class="muted">${esc(o.role)}</span></button>
        ${(o.projects || []).map((p) => `<button class="cl-proj-b${p.id === proj?.id ? " active" : ""}" data-p="${esc(p.id)}" title="${esc(p.role || "")}">${icon("folder")}<span>${esc(p.name)}</span></button>`).join("")}
        <button class="cl-proj-b cl-newproj" data-o="${esc(o.id)}">+ New project…</button></div>`).join("")}
        ${newOrg}</aside>
      <div class="cl-main">
        ${proj ? `<div class="hm-head"><h2>${esc(proj.name)}</h2><span class="muted">${esc(proj.role || "")}</span>
          ${["owner", "editor"].includes(proj.role) ? `<button class="btn primary" id="cl-newcloud">New Cloud Part…</button>` : ""}</div>
          <div class="cl-docs"></div>` : `<p class="hm-empty">No projects in ${esc(org.name)} that you can open yet.</p>`}
        <div class="cl-team"></div>
      </div>`;
    for (const b of box.querySelectorAll(".cl-org-b")) b.onclick = () => { cl.homeOrg = b.dataset.o; cl.homeProject = null; this.collabHomeOrgs(box, orgs, me); };
    for (const b of box.querySelectorAll(".cl-proj-b[data-p]")) b.onclick = () => { cl.homeProject = b.dataset.p; this.collabHomeOrgs(box, orgs, me); };
    for (const b of box.querySelectorAll(".cl-newproj")) b.onclick = () => this.collabNewProjectDialog(orgs.find((o) => o.id === b.dataset.o));
    box.querySelector(".cl-neworg").onclick = () => this.collabNewOrgDialog();
    if (proj) {
      box.querySelector("#cl-newcloud")?.addEventListener("click", () => this.collabNewCloudPartDialog(proj));
      cl.homeDocOpts = { open: (id) => this.collabHomeOpen(id),
        empty: `No parts in ${esc(proj.name)} yet.${["owner", "editor"].includes(proj.role) ? " Make one with New Cloud Part." : ""}` };
      this.collabDocTable(box.querySelector(".cl-docs"), proj.id, cl.homeDocOpts);
    }
    this.collabHomeTeam(box.querySelector(".cl-team"), org, proj, me);
  },

  async collabHomeOpen(id) {
    const r = await this.run("collab_open", { document_id: id }).catch(() => null);
    if (!r) return; // (the error is shown; Home stays)
    this.homeHide(); this.vp.zoomFit(false); this.refreshCollab();
  },

  // a project's parts: name, who is in it now, last change; pick (one click) and open (double click)
  async collabDocTable(box, project, opts) {
    const k = `docs\n${project}`;
    if (!this.collabHomeCache().m.has(k) && !box.querySelector(".cl-doc")) box.innerHTML = `<p class="muted">Loading…</p>`;
    await this.collabFresh(k, () => this.api("/api/command", { name: "collab_documents", args: { project } }),
      (v) => { if (box.isConnected) this.collabDrawDocs(box, v.documents || [], opts); },
      (error, shown) => { if (!box.isConnected) return; if (shown) this.collabHomeOffline(error); else box.innerHTML = `<p class="hm-empty">${esc(error)}</p>`; });
  },

  collabDrawDocs(box, all, { open, pick = null, empty = "No parts in this project yet." }) {
    const picked = box.querySelector(".cl-doc.picked")?.dataset.id; // (kept across a redraw)
    const docs = all.filter((x) => (x.kind || "part") === "part");
    if (!docs.length) { box.innerHTML = `<p class="hm-empty">${empty}</p>`; return; }
    const N = (v) => (Number.isFinite(Number(v)) ? Number(v) : "");
    const people = (x) => {
      const on = Array.isArray(x.online) ? x.online.slice(0, 8) : [];
      const more = Math.max(0, N(x.online_count) - on.length);
      return on.map((p) => `<span class="cl-av cl-dot" style="--c:${colour(p.colour)}" title="${esc(p.name)}">${esc(initials(p.name))}</span>`).join("")
        + (more ? `<span class="muted">+${more}</span>` : "");
    };
    box.innerHTML = `<table class="hm-files cl-doctable"><thead><tr><th>Part</th><th>Here now</th><th>Last changed</th><th>Changes</th><th></th></tr></thead><tbody>${docs.map((x) =>
      `<tr class="cl-doc hm-file" data-id="${esc(x.id)}"><td><span class="hm-name">${icon("part")}${esc(x.name)}</span></td><td><div class="cl-here">${people(x)}</div></td>
        <td class="hm-when">${esc(when(x.updated_at))}</td><td>${esc(N(x.seq ?? x.head_seq))}</td><td><button class="btn cl-open">Open</button></td></tr>`).join("")}</tbody></table>`;
    for (const tr of box.querySelectorAll(".cl-doc")) {
      if (tr.dataset.id === picked) tr.classList.add("picked");
      tr.onclick = (e) => {
        if (e.target.closest(".cl-open")) return open(tr.dataset.id);
        box.querySelectorAll(".cl-doc").forEach((x) => x.classList.toggle("picked", x === tr)); pick?.(tr.dataset.id);
      };
      tr.ondblclick = () => open(tr.dataset.id);
    }
  },

  // the organisation's people, invitations and the project's roles (owners and admins change them; the others see them)
  async collabHomeTeam(box, org, proj, me) {
    const k = `team\n${org.id}\n${proj?.id || ""}`, title = `<div class="hm-head"><h2>Team · ${esc(org.name)}</h2></div>`;
    if (!this.collabHomeCache().m.has(k)) box.innerHTML = `${title}<p class="muted">Loading…</p>`;
    await this.collabFresh(k, async () => {
      const [mr, pr] = await Promise.all([
        this.api("/api/command", { name: "collab_members", args: { org: org.id } }).catch((e) => ({ ok: false, error: String(e) })),
        proj ? this.api("/api/command", { name: "collab_project_members", args: { project: proj.id } }).catch(() => null) : null]);
      return mr.ok ? { ok: true, result: { ...mr.result, project_members: pr?.ok ? pr.result.members : null } } : mr;
    }, (v) => { if (box.isConnected) this.collabDrawTeam(box, org, proj, me, v); },
    (error, shown) => { if (!box.isConnected) return; if (shown) this.collabHomeOffline(error); else box.innerHTML = `${title}<p class="hm-empty">${esc(error)}</p>`; });
  },

  collabDrawTeam(box, org, proj, me, data) {
    const { members, invites } = data;
    const mine = org.role, admin = ["owner", "admin"].includes(mine), owner = mine === "owner";
    const roleSel = (m) => {
      const may = admin && (owner || m.role !== "owner");
      if (!may) return esc(m.role);
      const opts = (owner ? ["owner", "admin", "member"] : ["admin", "member"]).map((r) => `<option${r === m.role ? " selected" : ""}>${r}</option>`).join("");
      return `<select class="cl-role" data-u="${esc(m.id)}">${opts}</select>`;
    };
    const you = (m) => m.id === me?.id;
    const rows = members.map((m) => `<tr><td><b>${esc(m.name)}</b>${you(m) ? ' <span class="muted">(you)</span>' : ""}</td><td class="hm-folder">${esc(m.email || "")}</td>
      <td>${roleSel(m)}</td><td>${you(m) ? `<button class="btn cl-rm" data-u="${esc(m.id)}" data-you="1">Leave</button>`
        : admin && (owner || m.role !== "owner") ? `<button class="btn cl-rm" data-u="${esc(m.id)}">Remove</button>` : ""}</td></tr>`).join("");
    const inv = admin ? `<h3>Invitations</h3>${invites.length ? `<table class="hm-files"><tbody>${invites.map((i) => `<tr><td>${esc(i.email || "Anyone with the link")}</td><td>${esc(i.role)}</td>
        <td class="hm-when">until ${esc(when(i.expires_at))}</td><td><button class="btn cl-uninv" data-i="${esc(i.id)}">Cancel</button></td></tr>`).join("")}</tbody></table>` : `<p class="muted">None waiting.</p>`}
      <div class="cl-invite"><input id="cl-iemail" type="email" placeholder="Their email (optional)" maxlength="320">
        <select id="cl-irole"><option value="member">member</option><option value="admin">admin</option></select>
        ${proj ? `<label class="cl-ip"><input type="checkbox" id="cl-iproj" checked> and ${esc(proj.name)} as <select id="cl-iprole"><option>editor</option><option>viewer</option><option>owner</option></select></label>` : ""}
        <button class="btn primary" id="cl-ibtn">Invite</button></div>` : "";
    const pmembers = data.project_members, powner = proj?.role === "owner";
    const inProj = new Set((pmembers || []).map((m) => m.id));
    const prole = (m) => powner ? `<select class="cl-prole" data-u="${esc(m.id)}">${["owner", "editor", "viewer"].map((r) => `<option${r === m.role ? " selected" : ""}>${r}</option>`).join("")}</select>` : esc(m.role);
    const proj_ = proj && pmembers ? `<h3>Who can open ${esc(proj.name)}</h3><table class="hm-files"><tbody>${pmembers.map((m) => `<tr><td><b>${esc(m.name)}</b>${you(m) ? ' <span class="muted">(you)</span>' : ""}</td>
        <td>${prole(m)}</td><td>${powner && !you(m) ? `<button class="btn cl-prm" data-u="${esc(m.id)}">Remove</button>` : ""}</td></tr>`).join("")}</tbody></table>
      ${powner && members.some((m) => !inProj.has(m.id)) ? `<div class="cl-invite"><select id="cl-padd">${members.filter((m) => !inProj.has(m.id)).map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("")}</select>
        <select id="cl-paddrole"><option>editor</option><option>viewer</option><option>owner</option></select><button class="btn" id="cl-paddb">Add to ${esc(proj.name)}</button></div>` : ""}` : "";
    box.innerHTML = `<div class="hm-head"><h2>Team · ${esc(org.name)}</h2><span class="muted">you are ${esc(mine)}</span></div>
      <table class="hm-files cl-members"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th></th></tr></thead><tbody>${rows}</tbody></table>${inv}${proj_}`;
    const again = () => this.collabHomeTeam(box, org, proj, me);
    for (const s of box.querySelectorAll(".cl-role")) s.onchange = async () => { if (await this.collabCmd("collab_set_role", { org: org.id, user: s.dataset.u, role: s.value })) this.toast("Role changed"); again(); };
    for (const b of box.querySelectorAll(".cl-rm")) {
      b.onclick = () => {
        const m = members.find((x) => x.id === b.dataset.u), leaving = !!b.dataset.you;
        this.modal(leaving ? "Leave Organisation" : "Remove Member", `<p>${leaving ? `Leave <b>${esc(org.name)}</b>? You lose its projects at once.` : `Remove <b>${esc(m?.name)}</b> from <b>${esc(org.name)}</b>? They lose its projects and their open cloud parts at once; their past work stays.`}</p>`, async () => {
          if (!(await this.collabCmd("collab_remove_member", { org: org.id, user: b.dataset.u }))) throw new Error("refused");
          if (leaving) { this.cl.homeOrg = null; this.cl.homeProject = null; this.collabHomeRefresh(); } else again();
        }, "cancel");
      };
    }
    for (const b of box.querySelectorAll(".cl-uninv")) b.onclick = async () => { if (await this.collabCmd("collab_cancel_invite", { org: org.id, invite: b.dataset.i })) again(); };
    box.querySelector("#cl-ibtn")?.addEventListener("click", async () => {
      const args = { org: org.id, email: box.querySelector("#cl-iemail").value.trim() || null, role: box.querySelector("#cl-irole").value };
      if (proj && box.querySelector("#cl-iproj")?.checked) { args.project = proj.id; args.project_role = box.querySelector("#cl-iprole").value; }
      const r = await this.collabCmd("collab_invite", args);
      if (!r) return;
      this.collabInviteShown(r);
      again();
    });
    for (const s of box.querySelectorAll(".cl-prole")) s.onchange = async () => { await this.collabCmd("collab_set_project_role", { project: proj.id, user: s.dataset.u, role: s.value }); again(); };
    for (const b of box.querySelectorAll(".cl-prm")) b.onclick = async () => { if (await this.collabCmd("collab_remove_project_member", { project: proj.id, user: b.dataset.u })) again(); };
    box.querySelector("#cl-paddb")?.addEventListener("click", async () => {
      if (await this.collabCmd("collab_set_project_role", { project: proj.id, user: box.querySelector("#cl-padd").value, role: box.querySelector("#cl-paddrole").value })) again();
    });
  },

  // a new invitation: its link and code, shown once, to send to the person
  collabInviteShown(r) {
    const copy = (id) => `<button class="btn cl-copy" data-for="${id}">Copy</button>`;
    this.modal("Invitation", `<p>${r.emailed ? `Emailed to <b>${esc(r.email)}</b>. You can also send them the link yourself:` : `Send ${r.email ? `<b>${esc(r.email)}</b>` : "them"} the link, or the code to paste on Home › Cloud:`}</p>
      <div class="field"><label>Link</label><input id="cl-ilink" readonly value="${esc(r.url || "")}">${copy("cl-ilink")}</div>
      <div class="field"><label>Code</label><input id="cl-icode" readonly value="${esc(r.code || "")}">${copy("cl-icode")}</div>
      <p class="muted">Joins ${esc(r.org)} as ${esc(r.role)}${r.email ? ` (only for ${esc(r.email)})` : ""}, until ${esc(when(r.expires_at))}. They are shown only now.</p>`, null, "open");
    for (const b of document.querySelectorAll("#modal-box .cl-copy")) {
      b.onclick = () => { const i = $(`#${b.dataset.for}`); i.select(); navigator.clipboard?.writeText(i.value).then(() => this.toast("Copied"), () => document.execCommand?.("copy")); };
    }
  },

  collabNewOrgDialog() {
    this.modal("New Organisation", `<div class="field"><label>Name</label><input id="cl-oname" maxlength="200"></div>
      <p class="muted">You become its owner, and invite the others.</p>`, async () => {
      const name = $("#cl-oname").value.trim();
      if (!name) { this.toast("Name the organisation", "err"); throw new Error("no name"); }
      const o = await this.collabCmd("collab_create_org", { name });
      if (!o) throw new Error("refused");
      this.cl.homeOrg = o.id; this.cl.homeProject = null; this.collabHomeRefresh();
    }, "folder");
  },

  collabNewProjectDialog(org) {
    if (!org) return;
    this.modal("New Project", `<div class="field"><label>Name</label><input id="cl-pname2" maxlength="200"></div>
      <p class="muted">In ${esc(org.name)}. You become its owner; add people to it on the Team list.</p>`, async () => {
      const name = $("#cl-pname2").value.trim();
      if (!name) { this.toast("Name the project", "err"); throw new Error("no name"); }
      const p = await this.collabCmd("collab_create_project", { name, org: org.id });
      if (!p) throw new Error("refused");
      this.cl.homeOrg = org.id; this.cl.homeProject = p.id; this.collabHomeRefresh();
    }, "folder");
  },

  collabNewCloudPartDialog(proj) {
    this.modal("New Cloud Part", `<div class="field"><label>Name</label><input id="cl-cname" value="Part" maxlength="200"></div>
      <p class="muted">Made in <b>${esc(proj.name)}</b> on the server: everyone in the project can open it and edit it with you, whether or not you are online.</p>`, async () => {
      await this.run("collab_new_part", { project: proj.id, name: $("#cl-cname").value.trim() || "Part" });
      this.homeHide(); this.vp.setView("trimetric"); this.vp.zoomFit(); this.refreshCollab();
    }, "part");
  },

  async collabChooseNewPart() {
    const r = await this.api("/api/command", { name: "collab_status", args: {} }).catch(() => null);
    const st = r?.ok ? r.result : null;
    if (!st || st.available === false || !st.server) return false; // (collaboration never used here: New Part as ever)
    const s = await this.api("/api/command", { name: "get_settings", args: {} }).catch(() => null);
    const last = s?.ok && s.result.settings.new_part_location === "cloud" ? "cloud" : "local";
    const pr = st.signed_in ? await this.api("/api/command", { name: "collab_projects", args: {} }).catch(() => null) : null;
    const orgs = pr?.ok ? pr.result.orgs || [] : [];
    const usable = orgs.map((o) => ({ ...o, projects: (o.projects || []).filter((p) => ["owner", "editor"].includes(p.role)) })).filter((o) => o.projects.length);
    const where = !st.signed_in ? `<p class="muted">Sign in on Home › Cloud to make cloud parts (choosing Cloud takes you there).</p>`
      : !pr?.ok ? `<p class="muted">${esc(pr?.error || "The collaboration server cannot be reached")}: cloud parts can be made only while connected.</p>`
        : !usable.length ? `<p class="muted">You are not an editor in any project yet: make one on Home › Cloud.</p>`
          : `<div class="field"><label>Project</label><select id="cl-nproj">${this.collabProjectOptions(usable)}</select></div>
             <div class="field"><label>Name</label><input id="cl-nname" value="Part" maxlength="200"></div>`;
    this.modal("New Part", `<div class="cl-where">
        <label><input type="radio" name="cl-where" value="local"${last === "local" ? " checked" : ""}> <b>Local</b> <span class="muted">a file on this computer</span></label>
        <label><input type="radio" name="cl-where" value="cloud"${last === "cloud" ? " checked" : ""}> <b>Cloud</b> <span class="muted">on ${esc(st.server)}, for the team to open and edit live</span></label></div>
      <div id="cl-nwhere"${last === "cloud" ? "" : ' class="hidden"'}>${where}</div>`, async () => {
      const pick = document.querySelector("input[name=cl-where]:checked")?.value || "local";
      this.api("/api/command", { name: "set_settings", args: { values: { new_part_location: pick } } }).catch(() => {});
      if (pick === "local") { await this.run("new_part"); this.vp.setView("trimetric"); this.vp.zoomFit(); return; }
      if (!st.signed_in || !usable.length || !pr?.ok) { setTimeout(() => this.homeShow("cloud")); return; }
      await this.run("collab_new_part", { project: $("#cl-nproj").value, name: $("#cl-nname").value.trim() || "Part" });
      this.vp.setView("trimetric"); this.vp.zoomFit(); this.refreshCollab();
    }, "part");
    for (const b of document.querySelectorAll("input[name=cl-where]")) b.onchange = () => $("#cl-nwhere").classList.toggle("hidden", b.value !== "cloud" || !b.checked);
    return true;
  },

  async collabCmd(name, args = {}) { // a query: its result, or null after a toast
    const r = await this.api("/api/command", { name, args }).catch((e) => ({ ok: false, error: String(e) }));
    if (!r.ok) { this.toast(r.error, "err"); return null; }
    return r.result;
  },

  async collabSignInDialog() {
    const st = await this.collabCmd("collab_status");
    if (!st) return;
    if (st.available === false) return this.modal("Collaborate", `<p>${esc(st.message)}</p>`, null, "help");
    const u = st.user || {};
    const who = st.signed_in ? `<p class="muted">Signed in as <b>${esc(u.name || u.email || "you")}</b>${u.email && u.name ? ` (${esc(u.email)})` : ""} on ${esc(st.server)}. Signing in again replaces it.</p>` : "";
    this.modal("Sign In to a Collaboration Server", `${who}
      <div class="field"><label>Server</label><input id="cl-srv" value="${esc(st.server || "")}" placeholder="https://collab.example.com"></div>
      <div id="cl-oauth" class="cl-oauth"></div>
      <div class="field"><label>Email</label><input id="cl-email" type="email" autocomplete="username" value="${esc(u.email || "")}"></div>
      <div class="field"><label>Password</label><input id="cl-pw" type="password" autocomplete="current-password"></div>`, async () => {
      const r = await this.run("collab_sign_in", { server: $("#cl-srv").value.trim(), email: $("#cl-email").value.trim(), password: $("#cl-pw").value });
      this.toast(`Signed in as <b>${esc(r.result.user?.name || r.result.user?.email || "you")}</b> on ${esc(r.result.server)}`);
      this.collabHomeRefresh();
    }, "open");
    let timer = null;
    const check = () => { clearTimeout(timer); timer = setTimeout(() => this.collabServerOptions($("#cl-srv")?.value.trim()), 400); };
    $("#cl-srv").oninput = check;
    if (st.server) this.collabServerOptions(st.server);
  },

  // the browser sign-in buttons the server offers (Google, GitHub)
  async collabServerOptions(server) {
    const box = $("#cl-oauth");
    if (!box) return;
    if (!server) { box.innerHTML = ""; return; }
    const r = await this.api("/api/command", { name: "collab_server_info", args: { server } }).catch(() => null);
    if ($("#cl-srv")?.value.trim() !== server) return; // (typed on meanwhile)
    if (!r?.ok) { box.innerHTML = `<p class="muted">${esc(r?.error || "No answer from that address.")}</p>`; return; }
    const names = { google: "Google", github: "GitHub" };
    box.innerHTML = (r.result.oauth || []).map((p) => `<button class="btn cl-provider" data-p="${esc(p)}">Sign in with ${esc(names[p] || p)}</button>`).join("")
      + `<p class="muted">${esc(r.result.name)} ${esc(r.result.version || "")}${(r.result.oauth || []).length ? " — or with email and password:" : ""}</p>`;
    for (const b of box.querySelectorAll(".cl-provider")) {
      b.onclick = async () => {
        $("#modal").classList.add("hidden");
        this.toast(`Finish signing in with ${esc(names[b.dataset.p] || b.dataset.p)} in the browser window that opened`);
        const res = await this.run("collab_sign_in", { server, provider: b.dataset.p }).catch(() => null);
        if (res) { this.toast(`Signed in as <b>${esc(res.result.user?.name || "you")}</b> on ${esc(res.result.server)}`); this.collabHomeRefresh(); }
      };
    }
  },

  // add or remove Google / GitHub on the signed-in account (the server asks for the password, if there is one)
  async collabMethodsDialog() {
    const st = await this.collabCmd("collab_status");
    if (!st) return;
    if (!st.signed_in) { this.toast("Sign in to a collaboration server first"); return this.collabSignInDialog(); }
    const [mine, info] = await Promise.all([this.collabCmd("collab_sign_in_methods"),
      this.api("/api/command", { name: "collab_server_info", args: { server: st.server } }).catch(() => null)]);
    if (!mine) return;
    const names = { google: "Google", github: "GitHub" };
    const offered = info?.ok ? (info.result.oauth || []) : [];
    const linked = new Set(mine.providers || []);
    const rows = [...new Set([...offered, ...linked])].map((p) => `<div class="field cl-method"><label>${esc(names[p] || p)}</label>
      <span>${linked.has(p) ? "Signs in to this account" : "Not added"}</span>
      <button class="btn cl-method-btn" data-p="${esc(p)}" data-act="${linked.has(p) ? "remove" : "add"}">${linked.has(p) ? "Remove" : "Add"}</button></div>`).join("")
      || `<p class="muted">This server does not offer Google or GitHub sign-in.</p>`;
    const pw = mine.has_password
      ? `<div class="field"><label>Your password</label><input id="cl-mpw" type="password" autocomplete="current-password"></div>
         <p class="muted">Adding or removing a sign-in method needs your password.</p>`
      : `<p class="muted">This account has no password: adding or removing a method needs a sign-in from the last ten minutes.</p>`;
    this.modal("Sign-in Methods", `${rows}${pw}`, null, "open");
    for (const b of document.querySelectorAll(".cl-method-btn")) {
      b.onclick = async () => {
        const p = b.dataset.p, password = $("#cl-mpw")?.value || null;
        $("#modal").classList.add("hidden");
        if (b.dataset.act === "add") {
          this.toast(`Finish adding ${esc(names[p] || p)} in the browser window that opened`);
          const r = await this.run("collab_link_provider", { provider: p, password }).catch(() => null);
          if (r) this.toast(`${esc(names[p] || p)} now signs in to this account`);
        } else {
          const r = await this.run("collab_unlink_provider", { provider: p, password }).catch(() => null);
          if (r) this.toast(`${esc(names[p] || p)} removed`);
        }
      };
    }
  },

  async collabSignOut() {
    const r = await this.run("collab_sign_out").catch(() => null);
    if (!r) return;
    this.cl.hc = null; // (nothing of this account stays on screen)
    const stopped = r.result.stopped || [];
    this.toast(`Signed out${stopped.length ? `; stopped syncing ${stopped.map(esc).join(", ")} (still open here as local copies)` : ""}`);
    this.refreshCollab();
  },

  // organisations and projects, or null (with the sign-in dialog when that is what is missing)
  async collabProjects() {
    const r = await this.api("/api/command", { name: "collab_projects", args: {} }).catch((e) => ({ ok: false, error: String(e) }));
    if (r.ok) return r.result.orgs || [];
    if (/sign in|collab_sign_in/.test(r.error || "")) { this.toast("Sign in to a collaboration server first"); this.collabSignInDialog(); } else this.toast(r.error, "err");
    return null;
  },

  collabProjectOptions(orgs, extra = "") {
    return orgs.map((o) => `<optgroup label="${esc(o.name)}">${(o.projects || []).map((p) => `<option value="${esc(p.id)}">${esc(p.name)}${p.role ? ` (${esc(p.role)})` : ""}</option>`).join("")}</optgroup>`).join("") + extra;
  },

  async collabShareDialog() {
    if (this.collabDoc()) return this.toast(`${esc(this.model.name)} is already shared`);
    if ((this.model?.kind || "part") !== "part") return this.toast("Only parts can be shared for now (assemblies come later)", "err");
    const orgs = await this.collabProjects();
    if (!orgs) return;
    if (!orgs.length) return this.modal("Share This Part", "<p>Your account is not in any organisation on this server yet: ask its owner for an invitation.</p>", null, "help");
    const orgSel = orgs.length > 1 ? `<div class="field"><label>Organisation</label><select id="cl-org">${orgs.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join("")}</select></div>` : "";
    this.modal("Share This Part", `
      <div class="field"><label>Project</label><select id="cl-proj">${this.collabProjectOptions(orgs, '<option value="__new">New project...</option>')}</select></div>
      <div id="cl-newp" class="hidden"><div class="field"><label>New project name</label><input id="cl-pname"></div>${orgSel}</div>
      <div class="field"><label>Name in the cloud</label><input id="cl-dname" value="${esc(this.model.name)}"></div>
      <p class="muted">Everyone in the project can open it and edit it at the same time. Changes go to the server as you make them;
        a sketch or feature you have open is locked for the others.</p>`, async () => {
      let project = $("#cl-proj").value;
      if (project === "__new") {
        const name = $("#cl-pname").value.trim();
        if (!name) { this.toast("Name the new project", "err"); throw new Error("no name"); }
        const made = await this.api("/api/command", { name: "collab_create_project", args: { name, org: $("#cl-org")?.value || null } });
        if (!made.ok) { this.toast(made.error, "err"); throw new Error(made.error); }
        project = made.result.id;
      }
      const r = await this.run("collab_enable", { project, name: $("#cl-dname").value.trim() || null });
      this.toast(`<b>${esc(r.result.name)}</b> is shared in ${esc(r.result.project)}`);
      this.refreshCollab();
    }, "open");
    const sel = $("#cl-proj"), syncNew = () => $("#cl-newp").classList.toggle("hidden", sel.value !== "__new");
    sel.onchange = syncNew; syncNew();
  },

  async collabOpenDialog() {
    const orgs = await this.collabProjects();
    if (!orgs) return;
    const all = orgs.flatMap((o) => o.projects || []);
    if (!all.length) return this.modal("Open Cloud Part", "<p>You are not in any project on this server yet.</p>", null, "help");
    let picked = null;
    const open = async (id) => {
      await this.run("collab_open", { document_id: id });
      $("#modal").classList.add("hidden"); this.vp.zoomFit(false); this.refreshCollab();
    };
    this.modal("Open Cloud Part", `<div class="field"><label>Project</label><select id="cl-proj">${this.collabProjectOptions(orgs)}</select></div>
      <div id="cl-docs" class="cl-list"></div>`, async () => {
      if (!picked) { this.toast("Pick a part to open", "err"); throw new Error("nothing picked"); }
      await open(picked);
    }, "open");
    const load = async () => {
      const box = $("#cl-docs"); picked = null;
      picked = null;
      await this.collabDocTable(box, $("#cl-proj").value, { open: (id) => open(id).catch(() => {}), pick: (id) => { picked = id; },
        empty: "No parts in this project yet: make one on Home › Cloud, or move one there with Collaborate › Move to Cloud." });
    };
    $("#cl-proj").onchange = load; load();
  },

  collabStopDialog() {
    const d = this.collabDoc();
    if (!d) return;
    this.modal("Stop Sharing", `<p>Stop live collaboration on <b>${esc(d.document)}</b>? It stays open here as an ordinary local part (save it to keep it);
      the cloud document stays on the server for the others.</p>${d.pending ? `<p class="cl-warn">${d.pending} change${d.pending > 1 ? "s have" : " has"} not reached the server yet and will not be sent.</p>` : ""}`, async () => {
      await this.run("collab_disable"); this.refreshCollab();
    }, "cancel");
  },

  async collabHistoryDialog() {
    const d = this.collabDoc();
    if (!d) return this.toast("The active document is not a cloud document", "err");
    const h = await this.collabCmd("collab_history", { limit: 100 });
    if (!h) return;
    const N = (v) => (Number.isFinite(Number(v)) ? Number(v) : "?"); // (numbers from the server: only numbers go in)
    const rows = (h.history || []).map((x) => `<tr><td>${N(x.seq)}</td><td>${esc(when(x.time))}</td><td>${esc(x.author?.name || "")}</td>
      <td>${esc(x.command || "")}${x.reverts ? ` <span class="muted">(undoes ${N(x.reverts)})</span>` : ""}${x.reverted_by ? ` <span class="muted">(undone by ${N(x.reverted_by)})</span>` : ""}${x.broke && Object.keys(x.broke).length ? ' <span class="cl-warn">broke features</span>' : ""}</td>
      <td>${N(x.seq) < h.seq ? `<button class="btn cl-restore" data-seq="${N(x.seq)}" data-label="change ${N(x.seq)} (${esc(x.command || "")} by ${esc(x.author?.name || "")})">Restore</button>` : '<span class="muted">now</span>'}</td></tr>`).join("");
    const vers = (h.versions || []).map((v) => `<tr><td><b>${esc(v.name)}</b>${v.note ? `<div class="muted">${esc(v.note)}</div>` : ""}</td><td>${N(v.seq)}</td><td>${esc(v.author?.name || "")}</td><td>${esc(when(v.created_at))}</td>
      <td><button class="btn cl-restore" data-version="${esc(v.name)}" data-label="version ${esc(v.name)}">Restore</button></td></tr>`).join("");
    this.modal(`History and Versions — ${d.cloud_name}`, `
      <div class="cl-hist"><table class="dw-bom"><tr><th>#</th><th>When</th><th>Who</th><th>Change</th><th></th></tr>${rows || '<tr><td colspan="5" class="muted">No changes yet</td></tr>'}</table></div>
      <h4>Named versions</h4>
      <table class="dw-bom">${vers ? `<tr><th>Version</th><th>State</th><th>By</th><th>Named</th><th></th></tr>${vers}` : '<tr><td class="muted">None yet</td></tr>'}</table>
      <div class="cl-name-version"><input id="cl-vname" placeholder="Name the current state, e.g. Rev A"><input id="cl-vnote" placeholder="Note (optional)">
        <button class="btn" id="cl-vbtn">Name Version</button></div>`, null, "history");
    $("#cl-vbtn").onclick = async () => {
      const name = $("#cl-vname").value.trim();
      if (!name) return this.toast("Give the version a name", "err");
      const v = await this.collabCmd("collab_name_version", { name, note: $("#cl-vnote").value.trim() });
      if (v) { this.toast(`Version <b>${esc(v.name)}</b> is the state after change ${esc(v.seq)}`); this.collabHistoryDialog(); }
    };
    for (const b of document.querySelectorAll("#modal-box .cl-restore")) {
      b.onclick = () => this.modal("Restore", `<p>Put <b>${esc(d.cloud_name)}</b> back to ${esc(b.dataset.label)}, for everyone?</p>
        <p class="muted">Nothing is lost: the restore is itself a new change in the history, and can be restored away. It is refused while someone else has a sketch or feature open.</p>`, async () => {
        await this.run("collab_restore", b.dataset.version ? { version: b.dataset.version } : { seq: Number(b.dataset.seq) });
        this.toast(`Restored ${esc(b.dataset.label)}`);
      }, "rollback");
    }
  },
};

export function installCollab(App) {
  for (const [k, fn] of Object.entries(MIXIN)) App.prototype[k] = fn;
  // Home: a Cloud tab next to Local; New Part: local or cloud
  App.prototype.homeTabs.push({ id: "cloud", label: "Cloud", icon: "cloud", render(panel) { return this.collabHome(panel); } });
  App.prototype.newPartChoosers.push(function () { return this.collabChooseNewPart(); });
}
