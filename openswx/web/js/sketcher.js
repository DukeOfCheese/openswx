// Sketch mode: drawing tools, snapping, inference, Smart Dimension, Modify box, relation glyphs, dragging.
import { icon } from "./icons.js";
import { el, escapeHtml, UNITS } from "./propman.js";

const SNAP_PX = 9, INFER_DEG = 3;
const GLYPH = { horizontal: "—", vertical: "|", parallel: "∥", perpendicular: "⊥", equal: "=", tangent: "◠", concentric: "◎",
  coincident: "•", midpoint: "▾", fix: "⚓", collinear: "⋯", symmetric: "⇹" };

export class Sketcher {
  constructor(app) {
    this.app = app; this.vp = app.vp; this.name = null; this.tool = "select";
    this.clicks = []; this.chainStart = null; this.mouse = null; this.dimPick = [];
    this.vp.frameHooks.push(() => this.renderOverlay());
  }

  get active() { return !!this.name; }

  enter(name) {
    this.name = name; this.tool = "select"; this.reset();
    this.app.vp.activeSketch = name;
  }

  exit() { this.name = null; this.reset(); this.vp.activeSketch = null; this.vp.setTemp([]); this.closeModify(); this.app.overlayDims.innerHTML = ""; }

  reset() { this.gen = (this.gen || 0) + 1; this.clicks = []; this.chainStart = null; this.dimPick = []; this.dimPreview = null; this.cue = null; this.arcSweep = 0; this.vp.setTemp([]); this.vp.dirty = true; this.app.collabSketchDraft?.(null); }

  setTool(t) { this.tool = t; this.reset(); this.app.prompt(TOOL_HINT[t] || ""); }

  update(detail, overlay) {
    this.detail = detail; this.overlay = overlay;
    this.vp.dirty = true; // (the dimensions and relations are redrawn with the next frame)
    if (!overlay) return;
    this.O = overlay.origin; this.X = overlay.x; this.Z = overlay.z;
    this.Y = cross(this.Z, this.X);
  }

  w(u, v) { return [0, 1, 2].map((i) => this.O[i] + this.X[i] * u + this.Y[i] * v); }
  uvAt(sx, sy) { return this.vp.screenToPlane(sx, sy, this.O, this.X, this.Z); }
  scr(u, v) { return this.vp.toScreen(this.w(u, v)); }
  pts() { const p = { ORIGIN: [0, 0], ...(this.detail?.sketch?.points || {}) }; return p; }

  snap(sx, sy) {
    let best = null, bd = SNAP_PX;
    for (const [id, [u, v]] of Object.entries(this.pts())) {
      const [x, y] = this.scr(u, v); const d = Math.hypot(x - sx, y - sy);
      if (d < bd) { bd = d; best = { id, uv: [u, v] }; }
    }
    return best;
  }

  // position under the cursor with snapping and inference, as SOLIDWORKS does: existing points, line midpoints,
  // circle/arc quadrants, onto an entity, and dotted alignment with existing points. Returns {uv, ref, snapped,
  // rel?, cue?, align?}: ref = a point id or [u, v]; rel = the relation the new point gets ({type, target}).
  locate(sx, sy) {
    const s = this.snap(sx, sy);
    if (s) return { uv: s.uv, ref: s.id, snapped: true, cue: "coincident" };
    const E = this.detail?.sketch?.entities || {}, P = this.pts();
    let best = null, bd = SNAP_PX;
    const tryPt = (q, extra) => { const [x, y] = this.scr(q[0], q[1]), d = Math.hypot(x - sx, y - sy); if (d < bd) { bd = d; best = { uv: q.map(r4), ...extra }; } };
    for (const [id, e] of Object.entries(E)) {
      if (e.type === "line" && P[e.p1] && P[e.p2]) tryPt(mid(P[e.p1], P[e.p2]), { rel: { type: "midpoint", target: id }, cue: "midpoint" });
      if ((e.type === "circle" || e.type === "arc") && P[e.c]) {
        const c = P[e.c], R = e.r ?? dist(c, P[e.s]);
        for (let k = 0; k < 4; k++) {
          const q = [c[0] + R * Math.cos(k * Math.PI / 2), c[1] + R * Math.sin(k * Math.PI / 2)];
          if (e.type !== "arc" || onArc(c, P[e.s], P[e.e], q)) tryPt(q, { rel: { type: "coincident", target: id }, cue: "quadrant" });
        }
      }
    }
    if (best) return { ...best, ref: best.uv, snapped: true };
    const uv = this.uvAt(sx, sy); if (!uv) return null;
    const pk = this.vp.pick(sx, sy, { sketchOnly: true }), e = pk && pk.sketch === this.name ? E[pk.id] : null;
    if (e && (e.type === "line" || e.type === "circle" || e.type === "arc")) { // onto the entity: coincident with it
      let q;
      if (e.type === "line") { const a = P[e.p1], d = sub(P[e.p2], a), L2 = d[0] * d[0] + d[1] * d[1] || 1, t = Math.max(0, Math.min(1, ((uv[0] - a[0]) * d[0] + (uv[1] - a[1]) * d[1]) / L2)); q = [a[0] + t * d[0], a[1] + t * d[1]]; }
      else { const c = P[e.c], R = e.r ?? dist(c, P[e.s]), v = sub(uv, c), L = Math.hypot(...v) || 1; q = [c[0] + v[0] / L * R, c[1] + v[1] / L * R]; }
      return { uv: q.map(r4), ref: q.map(r4), snapped: true, rel: { type: "coincident", target: pk.id }, cue: "coincident" };
    }
    // dotted inference lines (no relation): in line horizontally or vertically with an existing point
    const wpp = this.vp.worldPerPixel || 1, skip = new Set(this.clicks.map((k) => k.ref).filter((x) => typeof x === "string"));
    let u = uv[0], v = uv[1], au = null, av = null, bu = SNAP_PX * 0.7, bv = SNAP_PX * 0.7;
    for (const [id, q] of Object.entries(P)) {
      if (skip.has(id)) continue;
      const du = Math.abs(uv[0] - q[0]) / wpp, dv = Math.abs(uv[1] - q[1]) / wpp;
      if (du < bu) { bu = du; u = q[0]; au = q; }
      if (dv < bv) { bv = dv; v = q[1]; av = q; }
    }
    const at = [u, v].map(r4), align = [au && { from: au, to: at }, av && { from: av, to: at }].filter(Boolean);
    return { uv: at, ref: at, snapped: false, align };
  }

  // relations the clicks inferred, added once the entity exists: onto an entity or a midpoint, or coincident with
  // an existing point that the tool took only as a position (rectangle corners, polygon and slot centres...)
  async attach(clicks, before) {
    const P = this.pts(), fresh = Object.keys(P).filter((id) => !before.has(id));
    const at = (uv) => fresh.find((id) => Math.abs(P[id][0] - uv[0]) < 1e-3 && Math.abs(P[id][1] - uv[1]) < 1e-3);
    for (const c of clicks) {
      const rel = typeof c.ref === "string" ? { type: "coincident", target: c.ref } : c.rel;
      const np = rel && at(c.uv); if (!np) continue;
      await this.app.run("sketch_relation", { sketch: this.name, relation: rel.type, refs: [np, rel.target] }).catch(() => {});
    }
  }

  infer(a, b) { // horizontal / vertical inference like SOLIDWORKS' yellow inference lines
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const ang = Math.abs(Math.atan2(dy, dx) * 180 / Math.PI);
    if (Math.hypot(dx, dy) < 1e-9) return { uv: b, rel: null };
    if (ang < INFER_DEG || ang > 180 - INFER_DEG) return { uv: [b[0], a[1]], rel: "horizontal" };
    if (Math.abs(ang - 90) < INFER_DEG) return { uv: [a[0], b[1]], rel: "vertical" };
    return { uv: b, rel: null };
  }

  async click(e) {
    const t = this.tool;
    if (t === "select") return false;
    if (t === "smart_dim") { await this.dimClick(e); return true; }
    if (t === "sk_fillet" || t === "sk_chamfer") { // a corner point, or two lines that meet; the tool stays on
      const s = this.snap(e.x, e.y);
      if (s && s.id !== "ORIGIN") { this.cornerPick = null; await this.app.sketchCorner(t, s.id).catch(() => {}); return true; }
      const pk = this.vp.pick(e.x, e.y, { sketchOnly: true }), E = this.detail?.sketch?.entities || {};
      if (pk && pk.sketch === this.name && E[pk.id]?.type === "line") {
        if (!this.cornerPick || this.cornerPick === pk.id) { this.cornerPick = pk.id; this.vp.setSelection([{ kind: "sketch", id: pk.id, sketch: this.name }]); return true; }
        const a = E[this.cornerPick], b = E[pk.id], shared = [a.p1, a.p2].find((q) => q === b.p1 || q === b.p2);
        this.cornerPick = null; this.vp.setSelection([]);
        if (shared) await this.app.sketchCorner(t, shared).catch(() => {}); else this.app.toast("Those lines do not meet at an end point.");
      }
      return true;
    }
    if (t === "trim" || t === "extend" || t === "split") { // pick the entity segment under the cursor
      const pk = this.vp.pick(e.x, e.y, { sketchOnly: true });
      const uv = this.uvAt(e.x, e.y);
      if (pk && pk.sketch === this.name && uv) {
        if (t === "split") await this.app.run("sketch_split", { sketch: this.name, entity: pk.id, at: uv }).catch(() => {});
        else await this.app.run(t === "trim" ? "sketch_trim" : "sketch_extend", { sketch: this.name, entity: pk.id, pick: uv }).catch(() => {});
      }
      return true;
    }
    const loc = this.locate(e.x, e.y); if (!loc) return true;
    this.clicks.push(loc);
    const before = new Set(Object.keys(this.pts())), placed = this.clicks.slice(), gen = this.gen;
    let ran = false;
    const c = this.clicks, S = this.name, run = (n, a) => { ran = true; return this.app.run(n, { sketch: S, ...a }); };
    try {
      if (t === "line" || t === "centerline") {
        if (c.length === 2) {
          const inf = this.infer(c[0].uv, c[1].uv);
          const end = c[1].snapped ? c[1].ref : inf.uv;
          const r = await run(t === "line" ? "sketch_line" : "sketch_centerline", { start: c[0].ref, end });
          const res = r?.result?.result; // {entity, points} from the sketch command
          if (res && inf.rel && !c[1].snapped) await run("sketch_relation", { relation: inf.rel, refs: [res.entity] });
          if (this.gen !== gen) { /* the tool changed while the line was being added: no chain */ }
          else if (t === "line" && res && !(c[1].snapped && c[1].ref === this.chainStart)) {
            this.clicks = [{ uv: inf.uv, ref: res.points[1], snapped: true }]; // continue the chain
            this.chainStart ??= res.points[0];
          } else this.reset();
        } else if (c.length === 1 && this.chainStart === null && t === "line") this.chainStart = typeof c[0].ref === "string" ? c[0].ref : null;
      } else if (t === "rectangle" || t === "center_rectangle") {
        if (c.length === 2) { await run("sketch_rectangle", { corner1: c[0].uv, corner2: c[1].uv, centered: t === "center_rectangle" }); this.reset(); }
      } else if (t === "circle") {
        if (c.length === 2) { await run("sketch_circle", { center: c[0].ref, radius: dist(c[0].uv, c[1].uv) }); this.reset(); }
      } else if (t === "arc") {
        if (c.length === 3) {
          const ccw = this.arcSweep >= 0;
          await run("sketch_arc", ccw ? { center: c[0].ref, start: c[1].ref, end: this.arcEnd(c) } : { center: c[0].ref, start: this.arcEnd(c), end: c[1].ref });
          this.reset();
        }
      } else if (t === "arc3") {
        if (c.length === 3) { await run("sketch_three_point_arc", { start: c[0].ref, end: c[1].ref, mid: c[2].uv }); this.reset(); }
      } else if (t === "polygon") {
        if (c.length === 2) {
          const ang = Math.atan2(c[1].uv[1] - c[0].uv[1], c[1].uv[0] - c[0].uv[0]) * 180 / Math.PI;
          await run("sketch_polygon", { center: c[0].uv, radius: dist(c[0].uv, c[1].uv), sides: this.app.polygonSides || 6, angle: ang }); this.reset();
        }
      } else if (t === "slot") {
        if (c.length === 3) {
          const w = 2 * distToLine(c[2].uv, c[0].uv, c[1].uv);
          await run("sketch_slot", { center1: c[0].uv, center2: c[1].uv, width: Math.max(w, 0.1) }); this.reset();
        }
      } else if (t === "point") {
        await run("sketch_point", { position: c[0].uv }); this.reset();
      } else if (t === "ellipse") {
        if (c.length === 3) {
          const b = Math.max(distToLine(c[2].uv, c[0].uv, c[1].uv), 0.1);
          await run("sketch_ellipse", { center: c[0].ref, major_end: c[1].uv, minor_radius: b }); this.reset();
        }
      } else if (t === "arc_tangent") {
        if (c.length === 1 && typeof c[0].ref !== "string") { this.clicks = []; this.app.toast("Start a tangent arc on the end point of a line or arc."); }
        else if (c.length === 2) { await run("sketch_tangent_arc", { start: c[0].ref, end: c[1].ref }); this.reset(); }
      } else if (t === "circle3") {
        if (c.length === 3) { await run("sketch_circle_3pt", { p1: c[0].ref, p2: c[1].ref, p3: c[2].ref }); this.reset(); }
      } else if (t === "conic") {
        if (c.length === 3) { // then the rho (unitless: passed as an "angle" so no unit conversion)
          const args = { start: c[0].ref, end: c[1].ref, apex: c[2].uv };
          this.reset();
          this.openModify(e.x, e.y, "Conic rho (0.5: parabola)", this.app.conicRho || 0.5, true, async (v) => {
            this.app.conicRho = Math.min(0.99, Math.max(0.01, v));
            await run("sketch_conic", { ...args, rho: this.app.conicRho }).catch(() => {});
          });
        }
      } else if (t === "spline") {
        // collect points; double-click / Enter finishes, clicking the first point closes it
        if (c.length > 3) {
          const s0 = this.vp.toScreen(this.w(...c[0].uv));
          if (Math.hypot(s0[0] - e.x, s0[1] - e.y) < SNAP_PX) { c.pop(); await this.finishSpline(true); }
        }
      } else if (t === "text") {
        const at = c[0].uv; this.reset();
        this.app.modal("Sketch Text", `<div class="field"><label>Text</label><input id="sktx" value="Text"></div>
          <div class="field"><label>Height (mm)</label><input id="skth" type="number" value="10"></div>
          <div class="field"><label>Angle (°)</label><input id="skta" type="number" value="0"></div>
          <div class="field"><label>Font</label><input id="sktf" value="Arial"></div>`, async () => {
          const q = (id) => document.getElementById(id).value;
          await run("sketch_text", { position: at, text: q("sktx"), height: parseFloat(q("skth")), angle: parseFloat(q("skta")) || 0, font: q("sktf") || "Arial" });
        }, "sk_text");
      }
      if (ran) await this.attach(placed, before);
    } catch (err) { this.reset(); }
    this.preview();
    return true;
  }

  arcEnd(c) { // end point projected onto the arc radius
    const r = dist(c[0].uv, c[1].uv), a = Math.atan2(c[2].uv[1] - c[0].uv[1], c[2].uv[0] - c[0].uv[0]);
    return c[2].snapped ? c[2].ref : [c[0].uv[0] + r * Math.cos(a), c[0].uv[1] + r * Math.sin(a)];
  }

  move(e) {
    this.mouse = e;
    const loc = this.locate(e.x, e.y);
    if (loc) this.app.status.coords(loc.uv);
    if (this.dragging) { this.dragMove(e); return true; }
    if (this.tool === "smart_dim" && this.dimPick.length) { this.dimPreview = this.dimSpec(loc?.uv); this.vp.dirty = true; }
    if (this.tool === "select" || this.tool === "smart_dim") return false;
    this.preview(loc);
    return true;
  }

  preview(loc = this.mouse ? this.locate(this.mouse.x, this.mouse.y) : null) {
    const items = [], c = this.clicks, W = (uv) => this.w(uv[0], uv[1]);
    if (loc?.snapped) items.push({ pts: W(loc.uv), point: true, color: 0xf08a00, size: 10 });
    for (const a of loc?.align || []) items.push({ pts: [...W(a.from), ...W(a.to)], color: 0x4f86d9, dashed: true });
    this.cue = loc?.cue && this.mouse ? { x: this.mouse.x, y: this.mouse.y, type: loc.cue } : null;
    if (!loc || !c.length) { this.vp.setTemp(items); this.app.collabSketchDraft?.(null); return; }
    const a = c[0].uv; let b = loc.uv;
    const line = (p, q, o = {}) => items.push({ pts: [...W(p), ...W(q)], ...o });
    const t = this.tool;
    if (t === "line" || t === "centerline") {
      const inf = loc.snapped ? { uv: b, rel: null } : this.infer(a, b); b = inf.uv;
      line(a, b, { dashed: t === "centerline" });
      if (inf.rel) line([a[0] - (inf.rel === "horizontal" ? 1e3 : 0), a[1] - (inf.rel === "vertical" ? 1e3 : 0)], [a[0] + (inf.rel === "horizontal" ? 1e3 : 0), a[1] + (inf.rel === "vertical" ? 1e3 : 0)], { color: 0xd6a400, dashed: true });
      this.app.prompt(`L = ${UNITS.fmt(dist(a, b), 2)}`);
    } else if (t === "rectangle" || t === "center_rectangle") {
      const [p, q] = t === "rectangle" ? [a, b] : [[2 * a[0] - b[0], 2 * a[1] - b[1]], b];
      line(p, [q[0], p[1]]); line([q[0], p[1]], q); line(q, [p[0], q[1]]); line([p[0], q[1]], p);
      this.app.prompt(`${UNITS.fmt(Math.abs(q[0] - p[0]), 2)} × ${UNITS.fmt(Math.abs(q[1] - p[1]), 2)}`);
    } else if (t === "circle" || t === "polygon") {
      const r = dist(a, b), n = t === "circle" ? 64 : this.app.polygonSides || 6, a0 = Math.atan2(b[1] - a[1], b[0] - a[0]);
      const ring = []; for (let k = 0; k <= n; k++) { const th = a0 + (2 * Math.PI * k) / n; ring.push(...W([a[0] + r * Math.cos(th), a[1] + r * Math.sin(th)])); }
      items.push({ pts: ring }); this.app.prompt(`R = ${UNITS.fmt(r, 2)}`);
    } else if (t === "arc") {
      if (c.length === 1) line(a, b, { dashed: true });
      else {
        const r = dist(a, c[1].uv), a0 = Math.atan2(c[1].uv[1] - a[1], c[1].uv[0] - a[0]);
        const a1 = Math.atan2(b[1] - a[1], b[0] - a[0]);
        let d = a1 - a0; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI;
        // accumulate sweep so arcs beyond 180 degrees follow the mouse
        const prev = this.arcSweep || 0; let sweep = d; if (Math.abs(prev) > Math.PI / 2 && Math.sign(prev) !== Math.sign(d)) sweep = d + Math.sign(prev) * 2 * Math.PI;
        this.arcSweep = sweep;
        const ring = []; for (let k = 0; k <= 48; k++) { const th = a0 + (sweep * k) / 48; ring.push(...W([a[0] + r * Math.cos(th), a[1] + r * Math.sin(th)])); }
        items.push({ pts: ring }); this.app.prompt(`R = ${UNITS.fmt(r, 2)}  A = ${(Math.abs(sweep) * 180 / Math.PI).toFixed(1)}°`);
      }
    } else if (t === "arc3") {
      if (c.length === 1) line(a, b);
      else { const cc = circum(a, c[1].uv, b); if (cc) { const ring = arcPts(cc, a, c[1].uv, b); items.push({ pts: ring.flatMap(W) }); } }
    } else if (t === "ellipse") {
      if (c.length === 1) { line(a, b, { dashed: true }); this.app.prompt(`a = ${dist(a, b).toFixed(2)} mm`); }
      else {
        const m = c[1].uv, A = dist(a, m), B = Math.max(distToLine(b, a, m), 0.01), th = Math.atan2(m[1] - a[1], m[0] - a[0]);
        const ring = []; for (let k = 0; k <= 64; k++) { const u = (2 * Math.PI * k) / 64, x = A * Math.cos(u), y = B * Math.sin(u);
          ring.push(...W([a[0] + x * Math.cos(th) - y * Math.sin(th), a[1] + x * Math.sin(th) + y * Math.cos(th)])); }
        items.push({ pts: ring }); this.app.prompt(`a = ${A.toFixed(2)}  b = ${B.toFixed(2)} mm`);
      }
    } else if (t === "circle3") {
      if (c.length === 1) line(a, b, { dashed: true });
      else { const cc = circum(a, c[1].uv, b); if (cc) { const r = dist(cc, a), ring = []; for (let k = 0; k <= 64; k++) { const u = (2 * Math.PI * k) / 64; ring.push(...W([cc[0] + r * Math.cos(u), cc[1] + r * Math.sin(u)])); } items.push({ pts: ring }); } }
    } else if (t === "arc_tangent") {
      line(a, b, { dashed: true });
    } else if (t === "conic") {
      if (c.length === 1) line(a, b, { dashed: true });
      else { // rational quadratic: ends a, c[1], apex at the cursor, weight rho / (1 - rho)
        const e2 = c[1].uv, rho = this.app.conicRho || 0.5, w = rho / (1 - rho), ring = [];
        for (let k = 0; k <= 48; k++) { const u = k / 48, b0 = (1 - u) ** 2, b1 = 2 * u * (1 - u) * w, b2 = u * u, d = b0 + b1 + b2;
          ring.push(...W([(b0 * a[0] + b1 * b[0] + b2 * e2[0]) / d, (b0 * a[1] + b1 * b[1] + b2 * e2[1]) / d])); }
        items.push({ pts: ring }); line(a, b, { dashed: true }); line(e2, b, { dashed: true }); this.app.prompt(`rho = ${rho}`);
      }
    } else if (t === "spline") {
      const pts = [...c.map((k) => k.uv), b]; items.push({ pts: catmull(pts).flatMap(W) });
      for (const k of c) items.push({ pts: W(k.uv), point: true, color: 0x1d4fd6, size: 6 });
      this.app.prompt("Spline: click points; double-click or Enter to finish, click the first point to close.");
    } else if (t === "slot") {
      if (c.length === 1) line(a, b, { dashed: true });
      else { const w = distToLine(b, a, c[1].uv); items.push({ pts: slotPts(a, c[1].uv, w).flatMap(W) }); }
    }
    this.vp.setTemp(items);
    this.app.collabSketchDraft?.(items); // (a cloud document: the others see what is being drawn)
  }

  async finishSpline(closed = false) {
    const pts = this.clicks.map((k) => (k.snapped ? k.ref : k.uv));
    this.reset();
    if (pts.length >= 2) await this.app.run("sketch_spline", { sketch: this.name, points: pts, closed }).catch(() => {});
    this.preview();
  }

  dblclick() {
    if (this.tool === "line") { this.reset(); return true; }
    if (this.tool === "spline") { this.clicks.pop(); this.finishSpline(false); return true; } // dblclick added a duplicate point
    return false;
  }

  key(e) {
    if (this.tool === "spline" && (e.key === "Enter" || e.key === "Escape") && this.clicks.length >= 2) { this.finishSpline(false); return true; }
    if (e.key === "Escape") { if (this.clicks.length || this.dimPick.length) this.reset(); else this.setToolSelect(); return true; }
    return false;
  }

  setToolSelect() { this.app.setSketchTool("select"); }

  down(e) {
    if (this.tool !== "select" || e.button !== 0) return false;
    const s = this.snap(e.x, e.y);
    if (s && s.id !== "ORIGIN") { this.dragging = { id: s.id, uv: s.uv, moved: false }; return true; }
    const pk = this.vp.pick(e.x, e.y, { sketchOnly: true }), uv = this.uvAt(e.x, e.y);
    if (!s && pk && pk.sketch === this.name && uv && this.detail?.sketch?.entities?.[pk.id]) {
      this.dragging = { entity: pk.id, frm: uv, uv, moved: false }; return true;
    }
    return false;
  }
  dragMove(e) {
    const uv = this.uvAt(e.x, e.y); if (!uv) return;
    const d = this.dragging; d.uv = uv; d.moved = true;
    if (d.id) { this.vp.setTemp([{ pts: this.w(uv[0], uv[1]), point: true, color: 0x2f7be0, size: 10 }]); return; }
    const E = this.detail.sketch.entities, P = this.pts(), ent = E[d.entity], W = (q) => this.w(q[0], q[1]);
    let ring = null;
    if (ent.type === "circle" || ent.type === "arc") { // the curve resizes to pass through the cursor
      const c = P[ent.c], R = dist(c, uv); let a0 = 0, sw = 2 * Math.PI;
      if (ent.type === "arc") { a0 = Math.atan2(P[ent.s][1] - c[1], P[ent.s][0] - c[0]); sw = (((Math.atan2(P[ent.e][1] - c[1], P[ent.e][0] - c[0]) - a0) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI) || 2 * Math.PI; }
      ring = []; for (let k = 0; k <= 64; k++) { const t = a0 + sw * k / 64; ring.push(...W([c[0] + R * Math.cos(t), c[1] + R * Math.sin(t)])); }
    } else { // a ghost of the entity, moved
      const obj = this.vp.sketchEnts.find((o) => o.userData.sketch === this.name && o.userData.id === d.entity);
      if (obj) {
        const du = uv[0] - d.frm[0], dv = uv[1] - d.frm[1], off = [0, 1, 2].map((i) => this.X[i] * du + this.Y[i] * dv), q = obj.userData.pts;
        ring = []; for (let i = 0; i < q.length; i += 3) ring.push(q[i] + off[0], q[i + 1] + off[1], q[i + 2] + off[2]);
      }
    }
    this.vp.setTemp(ring ? [{ pts: ring, color: 0x2f7be0 }] : []);
  }
  async up() {
    const d = this.dragging; this.dragging = null;
    if (!d) return false;
    this.vp.setTemp([]);
    if (!d.moved) return false;
    if (d.id) await this.app.run("sketch_move_point", { sketch: this.name, point: d.id, position: d.uv.map(r4) }).catch(() => {});
    else await this.app.run("sketch_drag_entity", { sketch: this.name, entity: d.entity, frm: d.frm.map(r4), to: d.uv.map(r4) }).catch(() => {});
    return true;
  }

  // As in SOLIDWORKS: pick one or two items and the dimension follows the cursor; where it is placed chooses a
  // horizontal, vertical or aligned distance (and which side of two lines an angle is), the click places it there
  // and the Modify box opens on it. A click on an existing dimension edits it.
  dimItemAt(sx, sy) {
    const s = this.snap(sx, sy);
    if (s) return { kind: "point", id: s.id };
    const pick = this.vp.pick(sx, sy, { sketchOnly: true });
    return pick && pick.sketch === this.name ? { kind: pick.type, id: pick.id } : null;
  }

  async dimClick(e) {
    const uv = this.uvAt(e.x, e.y); if (!uv) return;
    const p = this.dimPick;
    if (p.length === 2) return this.placeDim(uv, e); // after two picks the next click places the dimension
    const item = this.dimItemAt(e.x, e.y);
    if (item && !p.some((i) => i.id === item.id)) {
      p.push(item); this.dimShowPicks();
      this.dimPreview = this.dimSpec(uv); this.vp.dirty = true;
      return;
    }
    if (p.length && this.dimSpec(uv)) return this.placeDim(uv, e); // empty space (or the picked item again)
  }

  dimShowPicks() {
    const p = this.dimPick, P = this.pts();
    this.vp.setSelection(p.filter((i) => i.kind !== "point").map((i) => ({ kind: "sketch", id: i.id, sketch: this.name })));
    this.vp.setTemp(p.filter((i) => i.kind === "point").map((i) => ({ pts: this.w(...P[i.id]), point: true, color: 0x2f7be0, size: 10 })));
  }

  // the dimension the picks make with the cursor at uv: {type, refs, supplement?, pos, value} or null
  dimSpec(uv) {
    const p = this.dimPick, E = this.detail?.sketch?.entities || {}, P = this.pts();
    if (!p.length || !uv) return null;
    const ctr = (i) => i.kind === "point" ? i.id : (i.kind === "circle" || i.kind === "arc") ? E[i.id]?.c : null;
    let spec = null;
    if (p.length === 1) {
      const [i] = p, e = E[i.id];
      if (i.kind === "circle") spec = { type: "diameter", refs: [i.id] };
      else if (i.kind === "arc") spec = { type: "radius", refs: [i.id] };
      else if (i.kind === "line") {
        const A = P[e.p1], B = P[e.p2], tilted = Math.abs(A[0] - B[0]) > 1e-6 && Math.abs(A[1] - B[1]) > 1e-6;
        const m = tilted ? region(A, B, uv) : "aligned"; // a sloped line: its horizontal or vertical extent too
        spec = m === "aligned" ? { type: "length", refs: [i.id] } : { type: m === "h" ? "hdistance" : "vdistance", refs: [e.p1, e.p2] };
      }
    } else {
      const [a, b] = p;
      if (a.kind === "line" && b.kind === "line") {
        spec = this.parallel(a.id, b.id) ? { type: "distance", refs: [a.id, b.id] }
          : { type: "angle", refs: [a.id, b.id], supplement: this.angleSide(a.id, b.id, uv) };
      } else if (a.kind === "line" || b.kind === "line") {
        const [l, o] = a.kind === "line" ? [a, b] : [b, a], c = ctr(o);
        if (c) spec = { type: "distance", refs: [l.id, c] };
      } else {
        const ca = ctr(a), cb = ctr(b);
        if (ca && cb && ca !== cb) {
          const m = region(P[ca], P[cb], uv);
          spec = { type: m === "h" ? "hdistance" : m === "v" ? "vdistance" : "distance", refs: [ca, cb] };
        }
      }
    }
    if (!spec) return null;
    spec.pos = uv; spec.preview = true;
    try { spec.value = this.measure(spec); } catch { return null; }
    return spec;
  }

  // true when uv lies in the sector of the supplementary angle (180 - the angle between the line directions)
  angleSide(a, b, uv) {
    const E = this.detail.sketch.entities, P = this.pts(), la = E[a], lb = E[b];
    const A1 = P[la.p1], B1 = P[lb.p1], d1 = sub(P[la.p2], A1), d2 = sub(P[lb.p2], B1), den = crs(d1, d2);
    if (Math.abs(den) < 1e-12) return false;
    const t = crs(sub(B1, A1), d2) / den, c = sub(uv, [A1[0] + t * d1[0], A1[1] + t * d1[1]]);
    return (crs(c, d2) / den) * (crs(d1, c) / den) < 0; // c = al d1 + be d2 with al, be of opposite signs
  }

  async placeDim(uv, e) {
    const spec = this.dimSpec(uv);
    this.dimPick = []; this.dimPreview = null; this.vp.setSelection([]); this.vp.setTemp([]); this.vp.dirty = true;
    if (!spec) return;
    const r = await this.app.run("sketch_dimension", { sketch: this.name, kind: spec.type, refs: spec.refs,
      position: spec.pos.map(r4), supplement: !!spec.supplement }).catch(() => null);
    const res = r?.result?.result; if (!res) return;
    if (res.driven) { this.app.toast("This dimension would over-define the sketch, so it was added as a driven (reference) dimension."); return; }
    this.openModify(e.x, e.y, res.dimension, res.value, spec.type === "angle", (v, sc) => this.app.run("set_dimension", { dimension: res.dimension, value: v, configurations: sc }), res.dimension);
  }

  parallel(a, b) {
    const E = this.detail.sketch.entities, P = this.pts();
    const d = (id) => { const e = E[id]; return [P[e.p2][0] - P[e.p1][0], P[e.p2][1] - P[e.p1][1]]; };
    const u = d(a), v = d(b); return Math.abs(u[0] * v[1] - u[1] * v[0]) / (Math.hypot(...u) * Math.hypot(...v)) < 1e-3;
  }

  measure({ type, refs, supplement }) {
    const E = this.detail.sketch.entities, P = this.pts();
    const pt = (r) => P[r] ?? (E[r]?.c ? P[E[r].c] : E[r]?.p ? P[E[r].p] : null);
    if (type === "length") { const e = E[refs[0]]; return dist(P[e.p1], P[e.p2]); }
    if (type === "diameter") return 2 * (E[refs[0]].r ?? dist(P[E[refs[0]].c], P[E[refs[0]].s]));
    if (type === "radius") return E[refs[0]].r ?? dist(P[E[refs[0]].c], P[E[refs[0]].s]);
    if (type === "angle") {
      const d = (id) => { const e = E[id]; return sub(P[e.p2], P[e.p1]); }, u = d(refs[0]), v = d(refs[1]);
      const a = Math.abs(Math.atan2(crs(u, v), u[0] * v[0] + u[1] * v[1])) * 180 / Math.PI;
      return supplement ? 180 - a : a;
    }
    if (type === "hdistance") return Math.abs(pt(refs[1])[0] - pt(refs[0])[0]);
    if (type === "vdistance") return Math.abs(pt(refs[1])[1] - pt(refs[0])[1]);
    const isLine = (r) => E[r]?.type === "line";
    if (isLine(refs[0]) && isLine(refs[1])) return distToLine(P[E[refs[1]].p1], P[E[refs[0]].p1], P[E[refs[0]].p2]);
    if (isLine(refs[0]) || isLine(refs[1])) { const [l, q] = isLine(refs[0]) ? refs : [refs[1], refs[0]]; return distToLine(pt(q), P[E[l].p1], P[E[l].p2]); }
    return dist(pt(refs[0]), pt(refs[1]));
  }

  // drag a dimension's label: it stays where it is dropped
  dragDimLabel(ev, d) {
    if (ev.button !== 0) return;
    ev.stopPropagation();
    const start = [ev.clientX, ev.clientY]; let moved = false;
    const mv = (m) => {
      if (!moved && Math.hypot(m.clientX - start[0], m.clientY - start[1]) < 4) return;
      moved = true;
      const rc = this.vp.canvas.getBoundingClientRect(), uv = this.uvAt(m.clientX - rc.left, m.clientY - rc.top);
      if (uv) { this.dimDrag = { id: d.id, uv }; this.vp.dirty = true; }
    };
    const up = async () => {
      document.removeEventListener("pointermove", mv); document.removeEventListener("pointerup", up);
      const dd = this.dimDrag;
      if (moved && dd) {
        this.labelDragged = true; setTimeout(() => { this.labelDragged = false; });
        await this.app.run("sketch_move_dimension", { sketch: this.name, dimension: `${d.id}@${this.name}`, position: dd.uv.map(r4) }).catch(() => {});
      }
      this.dimDrag = null; this.vp.dirty = true;
    };
    document.addEventListener("pointermove", mv); document.addEventListener("pointerup", up);
  }

  // lengths shown/entered in document units, returned in mm. As in SOLIDWORKS the box takes arithmetic, a unit
  // ("1in", "25.4mm"), or "=" and an equation ("=\"width\"/2"), which links the dimension (target) to it.
  openModify(x, y, title, value, isAngle, onOK, target = null) {
    if (typeof isAngle === "function") { onOK = isAngle; isAngle = false; }
    if (!isAngle && UNITS.f !== 1) { const cb = onOK; onOK = (v) => cb(UNITS.toMM(v)); value = UNITS.len(+value); title = `${title} (${UNITS.name})`; }
    this.closeModify();
    const box = el("div", ""); box.id = "modify";
    box.style.left = `${Math.min(x + 12, this.vp.w - 190)}px`; box.style.top = `${Math.min(y + 12, this.vp.h - 80)}px`;
    const inp = el("input"); inp.value = (+value).toFixed(3).replace(/\.?0+$/, "");
    const ok = el("button", "", icon("ok")), cancel = el("button", "", icon("cancel")), rebuild = el("button", "", icon("rebuild"));
    ok.title = "OK"; cancel.title = "Cancel"; rebuild.title = "Rebuild with this value";
    const row = el("div", "mrow"); row.append(ok, cancel, rebuild);
    box.append(el("div", "mname", escapeHtml(title)), inp, row);
    // with several configurations, which ones the new value is for (as the SOLIDWORKS Modify box asks)
    const cfgs = this.app.model?.configurations || [];
    let scope = null;
    if (target && cfgs.length > 1) {
      const sel = el("select", "mscope"); sel.innerHTML = `<option value="this">This configuration</option><option value="all">All configurations</option><option value="specify">Specify configurations...</option>`;
      const pick = el("div", "mcfgs hidden"); pick.innerHTML = cfgs.map((c) => `<label class="pm-check"><input type="checkbox" value="${escapeHtml(c)}" ${c === this.app.model.active_config ? "checked" : ""}> ${escapeHtml(c)}</label>`).join("");
      sel.onchange = () => pick.classList.toggle("hidden", sel.value !== "specify");
      scope = () => sel.value === "specify" ? [...pick.querySelectorAll("input:checked")].map((x) => x.value) : sel.value;
      box.append(sel, pick);
    }
    this.app.graphics.append(box); inp.focus(); inp.select();
    const apply = async (close) => {
      if (target && inp.value.trim().startsWith("=")) { // an equation
        await this.app.run("set_equation", { target, expression: inp.value.trim() }).catch(() => {}); if (close) this.closeModify(); return;
      }
      const v = evalExpr(inp.value, isAngle); if (v === null) { inp.style.outlineColor = "red"; return; }
      await onOK(v, scope ? scope() : undefined); if (close) this.closeModify();
    };
    ok.onclick = () => apply(true); cancel.onclick = () => this.closeModify(); rebuild.onclick = () => apply(false);
    inp.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") apply(true); if (e.key === "Escape") this.closeModify(); };
    this.modify = box;
  }
  closeModify() { this.modify?.remove(); this.modify = null; }

  renderOverlay() {
    const v = !this.name && this.showDims;
    if (!v) return this._renderOverlay();
    const keep = [this.name, this.detail, this.O, this.X, this.Y, this.Z];
    this.name = v.name; this.detail = v.detail; this.O = v.overlay.origin; this.X = v.overlay.x; this.Z = v.overlay.z; this.Y = cross(this.Z, this.X);
    this.displayOnly = true;
    try { this._renderOverlay(); } finally { [this.name, this.detail, this.O, this.X, this.Y, this.Z] = keep; this.displayOnly = false; }
  }

  _renderOverlay() {
    const host = this.app.overlayDims; if (!host) return;
    host.innerHTML = "";
    if (!this.name || !this.detail?.sketch || !this.O) return;
    const sk = this.detail.sketch, E = sk.entities, P = this.pts();
    if (this.cue && this.tool !== "select" && !this.displayOnly) { // the inference the click will make, beside the cursor (SOLIDWORKS' yellow glyphs)
      const g = el("div", "rel-glyph cue", CUE[this.cue.type] || "•"); g.title = this.cue.type;
      g.style.left = `${this.cue.x + 16}px`; g.style.top = `${this.cue.y + 16}px`; host.append(g);
    }
    if (!this.displayOnly) { // sketch origin: red arrows along the sketch x/y axes
      const [ox, oy] = this.scr(0, 0), [xx, xy] = this.scr(1, 0), [yx, yy] = this.scr(0, 1);
      const ux = [xx - ox, xy - oy], uy = [yx - ox, yy - oy], nx = Math.hypot(...ux) || 1, ny = Math.hypot(...uy) || 1;
      const L = 26, ax = [ox + ux[0] / nx * L, oy + ux[1] / nx * L], ay = [ox + uy[0] / ny * L, oy + uy[1] / ny * L];
      const svg = `<svg style="position:absolute;inset:0;width:100%;height:100%;overflow:visible" xmlns="http://www.w3.org/2000/svg">
        <defs><marker id="oa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#d0312d"/></marker></defs>
        <line x1="${ox}" y1="${oy}" x2="${ax[0]}" y2="${ax[1]}" stroke="#d0312d" stroke-width="1.6" marker-end="url(#oa)"/>
        <line x1="${ox}" y1="${oy}" x2="${ay[0]}" y2="${ay[1]}" stroke="#d0312d" stroke-width="1.6" marker-end="url(#oa)"/>
        <circle cx="${ox}" cy="${oy}" r="2.5" fill="#d0312d"/></svg>`;
      host.insertAdjacentHTML("beforeend", svg);
    }
    const center = (id) => P[id] ?? (E[id]?.c ? P[E[id].c] : E[id]?.p ? P[E[id].p] : null);
    // dimensions drawn as SOLIDWORKS does: extension lines, a dimension line with arrows set off from the
    // geometry (away from the sketch's middle), a leader for radii and diameters, an arc for angles
    const S = (q) => this.scr(q[0], q[1]);
    const allPts = Object.values(P).map(S);
    const cen = allPts.length ? [allPts.reduce((a, q) => a + q[0], 0) / allPts.length, allPts.reduce((a, q) => a + q[1], 0) / allPts.length] : [0, 0];
    const lines = [], arrows = [], OFF = 26, col = (d) => (d.driven ? "#777" : "#1d4fd6");
    const arrow = (tip, dir, c) => { // filled arrowhead at tip pointing along dir (screen)
      const L = Math.hypot(...dir) || 1, ux = dir[0] / L, uy = dir[1] / L, a = 8, w = 3;
      arrows.push(`<path d="M${tip[0]} ${tip[1]} L${tip[0] - ux * a - uy * w} ${tip[1] - uy * a + ux * w} L${tip[0] - ux * a + uy * w} ${tip[1] - uy * a - ux * w}Z" fill="${c}"/>`);
    };
    const seg = (a, b, c, w = 1) => lines.push(`<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="${c}" stroke-width="${w}"/>`);
    const ext = (q, q2, c) => { const dx = q2[0] - q[0], dy = q2[1] - q[1], L = Math.hypot(dx, dy); // extension line, gap at the geometry
      if (L < 1) return; seg([q[0] + dx / L * 3, q[1] + dy / L * 3], [q2[0] + dx / L * 5, q2[1] + dy / L * 5], c, 0.8); };
    const foot = (q, l) => { const a = P[l.p1], b = P[l.p2], dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy || 1;
      const t = ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / L2; return [a[0] + t * dx, a[1] + t * dy]; };
    const linear = (A, B, c, mode) => { // A, B: measured points (screen); mode: aligned / h / v. Returns the label position.
      let A2, B2, n;
      if (mode === "h" || mode === "v") {
        const k = mode === "h" ? 1 : 0; // the screen coordinate the dimension line sits at
        const lo = Math.min(A[k], B[k]), hi = Math.max(A[k], B[k]);
        const side = cen[k] > hi ? -1 : cen[k] < lo ? 1 : (cen[k] - lo < hi - cen[k] ? 1 : -1);
        const at = side < 0 ? lo - OFF : hi + OFF;
        A2 = k ? [A[0], at] : [at, A[1]]; B2 = k ? [B[0], at] : [at, B[1]];
        n = k ? [0, side] : [side, 0];
      } else {
        const dx = B[0] - A[0], dy = B[1] - A[1], L = Math.hypot(dx, dy) || 1;
        n = [-dy / L, dx / L];
        const m = [(A[0] + B[0]) / 2, (A[1] + B[1]) / 2];
        if ((m[0] - cen[0]) * n[0] + (m[1] - cen[1]) * n[1] < 0) n = [-n[0], -n[1]];
        A2 = [A[0] + n[0] * OFF, A[1] + n[1] * OFF]; B2 = [B[0] + n[0] * OFF, B[1] + n[1] * OFF];
      }
      ext(A, A2, c); ext(B, B2, c); seg(A2, B2, c);
      arrow(A2, [A2[0] - B2[0], A2[1] - B2[1]], c); arrow(B2, [B2[0] - A2[0], B2[1] - A2[1]], c);
      return [(A2[0] + B2[0]) / 2, (A2[1] + B2[1]) / 2];
    };
    const placed = (A, B, Q, c, mode) => { // A, B measured, Q the label (sketch coordinates): the dimension line runs through Q
      let A2, B2;
      if (mode === "h") { A2 = [A[0], Q[1]]; B2 = [B[0], Q[1]]; }
      else if (mode === "v") { A2 = [Q[0], A[1]]; B2 = [Q[0], B[1]]; }
      else { const dx = B[0] - A[0], dy = B[1] - A[1], L = Math.hypot(dx, dy) || 1, n = [-dy / L, dx / L], o = (Q[0] - A[0]) * n[0] + (Q[1] - A[1]) * n[1];
        A2 = [A[0] + n[0] * o, A[1] + n[1] * o]; B2 = [B[0] + n[0] * o, B[1] + n[1] * o]; }
      const sA2 = S(A2), sB2 = S(B2), sQ = S(Q);
      ext(S(A), sA2, c); ext(S(B), sB2, c); seg(sA2, sB2, c);
      arrow(sA2, [sA2[0] - sB2[0], sA2[1] - sB2[1]], c); arrow(sB2, [sB2[0] - sA2[0], sB2[1] - sA2[1]], c);
      const dx = sB2[0] - sA2[0], dy = sB2[1] - sA2[1], L2 = dx * dx + dy * dy || 1, t = ((sQ[0] - sA2[0]) * dx + (sQ[1] - sA2[1]) * dy) / L2;
      if (t < 0 || t > 1) seg(t < 0 ? sA2 : sB2, [sA2[0] + t * dx, sA2[1] + t * dy], c); // label outside: extend the line to it
      return sQ;
    };
    const dims = this.dimPreview && !this.displayOnly ? [...sk.dimensions, this.dimPreview] : sk.dimensions, skName = this.name;
    for (const d of dims) {
      const r = d.refs, c = d.preview ? "#d0312d" : col(d);
      const pos = this.dimDrag && this.dimDrag.id === d.id ? this.dimDrag.uv : d.pos;
      let at = null;
      try {
        if (d.type === "length" || (d.type === "distance" && r.length === 1)) {
          const e = E[r[0]]; at = pos ? placed(P[e.p1], P[e.p2], pos, c, "aligned") : linear(S(P[e.p1]), S(P[e.p2]), c, "aligned");
        } else if (d.type === "radius" || d.type === "diameter") {
          const e = E[r[0]], cc = P[e.c], rad = e.r ?? dist(cc, P[e.s]);
          const C = S(cc), Q = S([cc[0] + rad, cc[1]]), R = Math.hypot(Q[0] - C[0], Q[1] - C[1]);  // (screen radius)
          let u = [Math.SQRT1_2, -Math.SQRT1_2];
          if (pos) { const q = S(pos), L = Math.hypot(q[0] - C[0], q[1] - C[1]); if (L > 1) u = [(q[0] - C[0]) / L, (q[1] - C[1]) / L]; }
          const edge = [C[0] + u[0] * R, C[1] + u[1] * R], tip = pos ? S(pos) : [C[0] + u[0] * (R + 30), C[1] + u[1] * (R + 30)];
          const from = d.type === "diameter" ? [C[0] - u[0] * R, C[1] - u[1] * R] : C;
          seg(from, tip, c); arrow(edge, u, c); if (d.type === "diameter") arrow(from, [-u[0], -u[1]], c);
          at = pos ? tip : [tip[0] + 14, tip[1] - 6];
        } else if (d.type === "angle") {
          const a = E[r[0]], b = E[r[1]];
          const A1 = S(P[a.p1]), A2 = S(P[a.p2]), B1 = S(P[b.p1]), B2 = S(P[b.p2]);
          const d1 = [A2[0] - A1[0], A2[1] - A1[1]], d2 = [B2[0] - B1[0], B2[1] - B1[1]], den = d1[0] * d2[1] - d1[1] * d2[0];
          if (Math.abs(den) > 1e-9) {
            const t = ((B1[0] - A1[0]) * d2[1] - (B1[1] - A1[1]) * d2[0]) / den, X = [A1[0] + t * d1[0], A1[1] + t * d1[1]];
            const away = (p1, p2) => { const q = Math.hypot(p1[0] - X[0], p1[1] - X[1]) > Math.hypot(p2[0] - X[0], p2[1] - X[1]) ? p1 : p2; const L = Math.hypot(q[0] - X[0], q[1] - X[1]) || 1; return [(q[0] - X[0]) / L, (q[1] - X[1]) / L]; };
            let u1 = away(A1, A2), u2 = away(B1, B2), R = 40;
            if (pos) { // the arc spans the sector the label was placed in, through the label
              const q = S(pos), cq = [q[0] - X[0], q[1] - X[1]], n1 = Math.hypot(...d1), n2 = Math.hypot(...d2);
              const al = crs(cq, d2) / den, be = crs(d1, cq) / den;
              u1 = [Math.sign(al || 1) * d1[0] / n1, Math.sign(al || 1) * d1[1] / n1]; u2 = [Math.sign(be || 1) * d2[0] / n2, Math.sign(be || 1) * d2[1] / n2];
              R = Math.max(Math.hypot(...cq), 12);
            }
            const s1 = [X[0] + u1[0] * R, X[1] + u1[1] * R], s2 = [X[0] + u2[0] * R, X[1] + u2[1] * R];
            const sweep = u1[0] * u2[1] - u1[1] * u2[0] > 0 ? 1 : 0;
            lines.push(`<path d="M${s1[0]} ${s1[1]} A${R} ${R} 0 0 ${sweep} ${s2[0]} ${s2[1]}" fill="none" stroke="${c}" stroke-width="1"/>`);
            const m = [u1[0] + u2[0], u1[1] + u2[1]], mL = Math.hypot(...m) || 1;
            at = pos ? S(pos) : [X[0] + m[0] / mL * (R + 12), X[1] + m[1] / mL * (R + 12)];
          } else at = S(mid(mid(P[a.p1], P[a.p2]), mid(P[b.p1], P[b.p2])));
        } else { // distance between two items, or horizontal / vertical distance
          const kind = (id) => E[id]?.type;
          let a, b;
          if (kind(r[0]) === "line" && kind(r[1]) === "line") { a = mid(P[E[r[0]].p1], P[E[r[0]].p2]); b = foot(a, E[r[1]]); }
          else if (kind(r[0]) === "line") { b = center(r[1]); a = b && foot(b, E[r[0]]); }
          else if (kind(r[1]) === "line") { a = center(r[0]); b = a && foot(a, E[r[1]]); }
          else { a = center(r[0]); b = center(r[1]); }
          const mode = d.type === "hdistance" ? "h" : d.type === "vdistance" ? "v" : "aligned";
          if (a && b) at = pos ? placed(a, b, pos, c, mode) : linear(S(a), S(b), c, mode);
        }
      } catch (err) { console.error("dimension", d.id, err); at = null; }
      if (!at) continue;
      const [x, y] = at;
      const label = el("div", "dim-label" + (d.driven ? " driven" : "") + (d.preview ? " preview" : ""));
      const lv = UNITS.len(+d.value);
      const val = d.type === "angle" ? `${(+d.value).toFixed(2)}°` : d.type === "diameter" ? `Ø${fmt(lv)}` : d.type === "radius" ? `R${fmt(lv)}` : fmt(lv);
      label.textContent = d.driven ? `(${val})` : val;
      label.style.left = `${x}px`; label.style.top = `${y}px`;
      if (d.preview) { host.append(label); continue; }
      label.title = `${d.id}@${skName}`;
      if (this.app.selDims?.has(d.id)) label.classList.add("sel");
      const edit = () => { if (!d.driven) this.openModify(x, y, `${d.id}@${skName}`, d.value, d.type === "angle", (v, sc) => this.app.run("set_dimension", { dimension: `${d.id}@${skName}`, value: v, configurations: sc }), `${d.id}@${skName}`); };
      if (this.displayOnly) { label.onclick = (e) => { e.stopPropagation(); edit(); }; host.append(label); continue; }
      label.onpointerdown = (e) => this.dragDimLabel(e, d);
      label.onclick = (e) => { e.stopPropagation(); if (this.labelDragged) return; if (this.tool === "smart_dim") edit(); else this.app.selectDim(d.id, e.ctrlKey); };
      label.ondblclick = (e) => { e.stopPropagation(); edit(); };
      host.append(label);
    }
    if (lines.length) host.insertAdjacentHTML("afterbegin", `<svg style="position:absolute;inset:0;width:100%;height:100%;overflow:visible;pointer-events:none" xmlns="http://www.w3.org/2000/svg">${lines.join("")}${arrows.join("")}</svg>`);
    if (!this.app.showRelations || this.displayOnly) return;
    const seen = new Map();
    for (const rel of sk.relations) {
      const g = GLYPH[rel.type]; if (!g) continue;
      for (const ref of rel.refs.slice(0, 2)) {
        const e = E[ref]; let uv;
        if (e?.type === "line") uv = mid(P[e.p1], P[e.p2]); else if (e && (e.c)) uv = P[e.c]; else if (P[ref]) uv = P[ref];
        if (!uv) continue;
        const key = uv.map((v) => v.toFixed(3)).join(); const n = seen.get(key) || 0; seen.set(key, n + 1);
        const [x, y] = this.scr(uv[0], uv[1]);
        const gl = el("div", "rel-glyph", g); gl.style.left = `${x + 12 + n * 16}px`; gl.style.top = `${y + 12}px`; gl.title = rel.type;
        host.append(gl);
      }
    }
  }
}

const CUE = { coincident: "•", midpoint: "▾", quadrant: "◔" };

const TOOL_HINT = {
  line: "Line: click to start, click to end. Continues as a chain; double-click or Esc to finish.",
  centerline: "Centerline: click start and end points.",
  rectangle: "Corner Rectangle: click the first corner, then the opposite corner.",
  center_rectangle: "Center Rectangle: click the centre, then a corner.",
  circle: "Circle: click the centre, then a point on the circle.",
  arc: "Centerpoint Arc: click the centre, the start point, then the end point.",
  arc3: "3 Point Arc: click start, end, then a point on the arc.",
  polygon: "Polygon: click the centre, then a vertex.",
  slot: "Straight Slot: click two centre points, then the width.",
  ellipse: "Ellipse: click the centre, the end of the major axis, then a point setting the minor radius.",
  spline: "Spline: click the through points; double-click or Enter to finish, click the first point to close.",
  arc_tangent: "Tangent Arc: click the end point of a line or arc, then the arc's end.",
  circle3: "Perimeter Circle: click three points on the circle.",
  split: "Split Entities: click a line or arc where it should be split.",
  conic: "Conic: click the start, the end, then the apex the curve is tangent towards; then enter rho (0.5: parabola).",
  text: "Sketch Text: click the start of the baseline, then enter the text.",
  point: "Point: click to place.",
  smart_dim: "Smart Dimension: select one or two items, then click to place the dimension.",
  trim: "Trim Entities (power trim): click the segment of an entity to remove it up to the nearest intersections.",
  sk_fillet: "Sketch Fillet: click a corner point (or two lines that meet). Esc to finish.",
  sk_chamfer: "Sketch Chamfer: click a corner point (or two lines that meet). Esc to finish.",
  extend: "Extend Entities: click near the end of a line to extend it to the next entity.",
  select: "",
};

function sub(a, b) { return [a[0] - b[0], a[1] - b[1]]; }
function onArc(c, s, e, q) { // q (on the circle) lies on the counter-clockwise arc from s to e
  const a = (p) => (Math.atan2(p[1] - c[1], p[0] - c[0]) + 2 * Math.PI) % (2 * Math.PI);
  const a0 = a(s), sw = (a(e) - a0 + 2 * Math.PI) % (2 * Math.PI), aq = (a(q) - a0 + 2 * Math.PI) % (2 * Math.PI);
  return aq <= sw + 1e-9;
}
function crs(a, b) { return a[0] * b[1] - a[1] * b[0]; }
function r4(x) { return Math.round(x * 1e4) / 1e4; }
// where a distance between A and B is placed: above / below them (x between theirs) is horizontal, beside them
// (y between theirs) vertical, anywhere else aligned, as SOLIDWORKS chooses by the cursor
function region(A, B, Q) {
  const inX = Q[0] >= Math.min(A[0], B[0]) && Q[0] <= Math.max(A[0], B[0]), inY = Q[1] >= Math.min(A[1], B[1]) && Q[1] <= Math.max(A[1], B[1]);
  return inX && !inY ? "h" : inY && !inX ? "v" : "aligned";
}
function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
function dist(a, b) { return Math.hypot(a[0] - b[0], a[1] - b[1]); }
function mid(a, b) { return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]; }
function offsetMid(a, b, off) { const m = mid(a, b), dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1; return [m[0] - (dy / L) * off, m[1] + (dx / L) * off]; }
function distToLine(p, a, b) { const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1; return Math.abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / L; }
function fmt(v) { return (+v).toFixed(2).replace(/\.00$/, ""); }
function circum(a, b, c) {
  const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1])); if (Math.abs(d) < 1e-9) return null;
  const s = (p) => p[0] * p[0] + p[1] * p[1];
  return [(s(a) * (b[1] - c[1]) + s(b) * (c[1] - a[1]) + s(c) * (a[1] - b[1])) / d, (s(a) * (c[0] - b[0]) + s(b) * (a[0] - c[0]) + s(c) * (b[0] - a[0])) / d];
}
function arcPts(c, a, b, m) {
  const r = dist(c, a), ang = (p) => Math.atan2(p[1] - c[1], p[0] - c[0]);
  let a0 = ang(a), a1 = ang(b), am = ang(m); const norm = (x) => ((x % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  let sweep = norm(a1 - a0); if (norm(am - a0) > sweep) sweep -= 2 * Math.PI;
  const out = []; for (let k = 0; k <= 48; k++) { const t = a0 + (sweep * k) / 48; out.push([c[0] + r * Math.cos(t), c[1] + r * Math.sin(t)]); }
  return out;
}
function catmull(p) { // smooth preview curve through the clicked points
  if (p.length < 3) return p;
  const out = [];
  for (let i = 0; i < p.length - 1; i++) {
    const p0 = p[Math.max(i - 1, 0)], p1 = p[i], p2 = p[i + 1], p3 = p[Math.min(i + 2, p.length - 1)];
    for (let k = 0; k < 12; k++) {
      const t = k / 12, t2 = t * t, t3 = t2 * t;
      out.push([0, 1].map((j) => 0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2 + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3)));
    }
  }
  out.push(p[p.length - 1]); return out;
}

function slotPts(a, b, w) {
  const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1, nx = -dy / L * w, ny = dx / L * w, base = Math.atan2(dy, dx);
  const out = [];
  for (let k = 0; k <= 24; k++) { const t = base - Math.PI / 2 + (Math.PI * k) / 24; out.push([b[0] + w * Math.cos(t), b[1] + w * Math.sin(t)]); }
  for (let k = 0; k <= 24; k++) { const t = base + Math.PI / 2 + (Math.PI * k) / 24; out.push([a[0] + w * Math.cos(t), a[1] + w * Math.sin(t)]); }
  out.push([b[0] - nx, b[1] - ny]); return out;
}
const UNIT_MM = { mm: 1, cm: 10, m: 1000, in: 25.4, '"': 25.4, ft: 304.8 };
function evalExpr(s, isAngle = false) { // arithmetic with an optional unit, like the SOLIDWORKS Modify box ("25.4*2", "1in", "30deg")
  const m = /^(.*?)\s*(mm|cm|m|in|ft|"|°|deg)?\s*$/.exec(String(s).trim()); if (!m) return null;
  const expr = m[1], unit = m[2];
  if (!expr || !/^[\d+\-*/().\s]+$/.test(expr)) return null;
  let v; try { v = Function(`"use strict";return (${expr})`)(); } catch { return null; }
  if (!Number.isFinite(v)) return null;
  if (unit && UNIT_MM[unit] && !isAngle) v = v * UNIT_MM[unit] / UNITS.f; // into document units
  return v;
}
