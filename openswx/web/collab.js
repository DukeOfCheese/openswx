// Live collaboration in the UI (cloud documents shared through an openswx-collab server). Mixed into the App
// class by installCollab():
//   * the Collaborate menu's dialogs: sign in, share this part, open a cloud part, stop sharing, history and
//     versions;
//   * the bar over the view while a cloud document is active: connection state, the others as coloured avatars
//     with their tool (click one to follow their view);
//   * presence out (pointer, selection as persistent names, tool, camera, drafts), at most ~10 a second and only
//     when something changed; presence in: the others' pointers, selections, sketch drafts and feature ghosts
//     (drafts can be switched off in the menu);
//   * locks taken while a sketch or a feature's panel is open, lock and "broken by" badges in the FeatureManager,
//     conflict toasts, and the list of changes refused after a reconnect.
// The state comes from GET /api/collab, fetched when the "collab" revision on the event stream moves.
import * as THREE from "three";
import { SPECS, el, escapeHtml } from "./propman.js";

const $ = (s) => document.querySelector(s);
const PRESENCE_MS = 100; // presence out: ~10 a second at most
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
  // ---------------------------------------------------------------- set-up and state
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

  // ---------------------------------------------------------------- the bar: state, avatars, follow
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

  // ---------------------------------------------------------------- presence out
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
    const r = await this.api("/api/command", { name: "collab_presence", args });
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

  // ---------------------------------------------------------------- locks (a sketch or a feature's panel)
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

  // ---------------------------------------------------------------- presence in
  collabDrawPeers(d, names) {
    const cl = this.cl;
    const peers = d ? (d.peers || []).filter((p) => !p.you) : [];
    // pointers: a dot and a name tag (with the tool) at their point on the model
    const live = new Set();
    for (const p of peers) {
      live.add(p.client_id);
      let m = cl.markers.get(p.client_id);
      if (!m) { m = el("div", "cl-ptr", "<i></i><span></span>"); cl.layer.append(m); cl.markers.set(p.client_id, m); }
      m.style.setProperty("--c", colour(p.colour));
      const tool = p.presence?.tool;
      m.querySelector("span").textContent = tool ? `${p.name} · ${tool}` : p.name;
      m.dataset.client = p.client_id;
      const pt = p.presence?.pointer?.point;
      m.point = Array.isArray(pt) && pt.length === 3 ? pt : null;
    }
    for (const [id, m] of cl.markers) if (!live.has(id)) { m.remove(); cl.markers.delete(id); }
    this.collabDrawSelections(peers, names);
    this.collabDrawSketchDrafts(peers);
    this.collabGhosts(peers);
    this.vp.dirty = true;
  },

  collabPlaceMarkers() {
    const now = performance.now();
    for (const m of this.cl.markers.values()) {
      if (!m.point) { m.style.display = "none"; continue; }
      const [x, y, z] = this.vp.toScreen(m.point);
      const off = z > 1 || x < -20 || y < -20 || x > this.vp.w + 20 || y > this.vp.h + 20;
      m.style.display = off ? "none" : "";
      if (off) continue;
      m.style.transform = `translate(${Math.round(x) - 6}px, ${Math.round(y) - 6}px)`;
      if (now - (m.occAt || 0) > 150) { // behind the model (from here): faint (a ray at most every 150 ms each)
        m.occAt = now;
        const ray = this.vp.ray(x, y), hit = ray.intersectObjects([...this.vp.faces.values()].filter((o) => o.visible), false)[0];
        const far = ray.ray.origin.distanceTo(new THREE.Vector3(...m.point));
        m.classList.toggle("behind", !!hit && hit.distance < far - Math.max(this.vp.viewHeight * 0.01, 1e-3));
      }
    }
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

  // ---------------------------------------------------------------- conflicts
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

  // ---------------------------------------------------------------- the Collaborate menu
  collabMenu() {
    const d = this.collabDoc();
    return [["Sign In...", "", () => this.collabSignInDialog()], ["Sign Out", "", () => this.collabSignOut()], "-",
      ["Share This Part...", "", () => this.collabShareDialog()], ["Open Cloud Part...", "", () => this.collabOpenDialog()],
      ...(d ? [["Stop Sharing...", "", () => this.collabStopDialog()], "-", ["History and Versions...", "", () => this.collabHistoryDialog()]] : []), "-",
      [`${this.cl?.previews ? "✓ " : ""}Show Others' Drafts`, "", () => this.collabTogglePreviews()]];
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
        if (res) this.toast(`Signed in as <b>${esc(res.result.user?.name || "you")}</b> on ${esc(res.result.server)}`);
      };
    }
  },

  async collabSignOut() {
    const r = await this.run("collab_sign_out").catch(() => null);
    if (!r) return;
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
      box.innerHTML = `<p class="muted">Loading...</p>`;
      const r = await this.collabCmd("collab_documents", { project: $("#cl-proj").value });
      const docs = (r?.documents || []).filter((x) => (x.kind || "part") === "part");
      if (!docs.length) { box.innerHTML = `<p class="muted">No parts in this project yet: share one with Collaborate › Share This Part.</p>`; return; }
      box.innerHTML = `<table class="dw-bom"><tr><th>Part</th><th>Changes</th><th>Last changed</th></tr>${docs.map((x) =>
        `<tr class="cl-doc" data-id="${esc(x.id)}"><td><b>${esc(x.name)}</b></td><td>${esc(x.seq ?? x.head_seq ?? "")}</td><td>${esc(when(x.updated_at))}</td></tr>`).join("")}</table>`;
      for (const tr of box.querySelectorAll(".cl-doc")) {
        tr.onclick = () => { box.querySelectorAll(".cl-doc").forEach((x) => x.classList.toggle("picked", x === tr)); picked = tr.dataset.id; };
        tr.ondblclick = () => open(tr.dataset.id).catch(() => {});
      }
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
}
