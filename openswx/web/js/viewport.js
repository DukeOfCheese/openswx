// 3D graphics area: orthographic camera, SOLIDWORKS-style navigation, picking, highlighting, previews.

import * as THREE from "three";

// Decode /api/mesh.bin: magic "OSWXMESH", version, header length, compact JSON header in which {"$buf": i}
// marks the little-endian typed arrays that follow, back to back. Builds the same object setMesh() expects.
export function decodeMeshBinary(data) {
  const magic = String.fromCharCode(...new Uint8Array(data, 0, 8));
  if (magic !== "OSWXMESH") throw new Error("not an openswx mesh document");
  const view = new DataView(data);
  const version = view.getUint32(8, true);
  if (version !== 1) throw new Error(`unsupported mesh document version ${version}`);
  const start = 16 + view.getUint32(12, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(data, 16, start - 16)));
  const arrays = [];
  let offset = start;
  for (const [kind, count] of header.buf || []) {
    arrays.push(kind === 0 ? new Float32Array(data, offset, count) : new Uint32Array(data, offset, count));
    offset += 4 * count;
  }
  const revive = (value) => {
    if (Array.isArray(value)) return value.map(revive);
    if (value && typeof value === "object") {
      if (typeof value.$buf === "number" && Object.keys(value).length === 1) return arrays[value.$buf];
      for (const key of Object.keys(value)) value[key] = revive(value[key]);
    }
    return value;
  };
  return revive(header.mesh);
}

// A geometry built from /api/mesh.bin already has typed-array indices; setIndex() would store a bare typed array
// where the renderer expects a BufferAttribute, so those become one here (plain JSON arrays go through as before).
const applyIndex = (geo, t) => {
  if (ArrayBuffer.isView(t)) geo.setIndex(new THREE.Uint32BufferAttribute(t, 1)); // (typed arrays are no BufferAttribute)
  else geo.setIndex(t);
};

const COL = {
  body: 0xc4cad3, hover: 0xf08a00, sel: 0x2f7be0, edge: 0x101010, preview: 0xffcf33,
  sketchUnder: 0x1d4fd6, sketchFull: 0x111111, sketchOver: 0xd0312d, sketchDangling: 0x8a6516, construction: 0x6b7cab, plane: 0x6f9ee0,
};
// a face's highlight: the highlight / selection colour lightened half way to white (it is shaded on top)
const tint = (c) => [16, 8, 0].reduce((t, sh) => t | (Math.round((((c >> sh) & 255) + 255) / 2) << sh), 0);

// mouse presets (User Settings > mouse_preset; settings.py describes them): button 1 is the middle, 2 the right
// one; each returns rotate / pan / zoom or null (the press goes to the tools). Alt + left drag always rotates.
export const MOUSE = {
  solidworks: (b, e) => b === 1 ? (e.ctrlKey ? "pan" : e.shiftKey ? "zoom" : "rotate") : b === 2 && e.ctrlKey ? "pan" : null,
  onshape: (b, e) => b === 1 ? "pan" : b === 2 ? (e.ctrlKey ? "pan" : "rotate") : null,
  fusion: (b, e) => b === 1 ? (e.shiftKey ? "rotate" : "pan") : null,
  blender: (b, e) => b === 1 ? (e.shiftKey ? "pan" : e.ctrlKey ? "zoom" : "rotate") : null,
};
const VIEWS = {
  front: [[0, 0, 1], [0, 1, 0]], back: [[0, 0, -1], [0, 1, 0]], top: [[0, 1, 0], [0, 0, -1]],
  bottom: [[0, -1, 0], [0, 0, 1]], right: [[1, 0, 0], [0, 1, 0]], left: [[-1, 0, 0], [0, 1, 0]],
  isometric: [[1, 1, 1], [0, 1, 0]], trimetric: [[0.8, 0.55, 1.0], [0, 1, 0]], dimetric: [[1, 0.55, 1], [0, 1, 0]],
};

export class Viewport {
  constructor(canvas, overlay, triad) {
    this.canvas = canvas; this.overlay = overlay; this.triad = triad;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(window.devicePixelRatio || 1);
    this.renderer.setClearColor(0x000000, 0);
    this.scene = new THREE.Scene();
    this.camera = this.ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, -1e5, 1e5);
    this.persp = new THREE.PerspectiveCamera(30, 1, 0.1, 1e7); // View > Perspective
    this.scene.add(this.camera); this.scene.add(this.persp);
    this.scene.add(new THREE.AmbientLight(0xffffff, 1.05));
    const key = new THREE.DirectionalLight(0xffffff, 1.7); key.position.set(0.6, 1.0, 2.0); this.camera.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.45); fill.position.set(-1.5, -0.6, 0.8); this.camera.add(fill);
    this.lights = [key, fill];
    this.pivot = null; // middle-click rotation centre
    this.target = new THREE.Vector3();
    this.viewHeight = 200;
    this.groups = {};
    for (const g of ["body", "edges", "sketch", "planes", "refs", "preview", "temp", "origin", "overlay"]) {
      this.groups[g] = new THREE.Group(); this.scene.add(this.groups[g]);
    }
    this.faces = new Map(); this.edges = new Map(); this.sketchEnts = []; this.planeObjs = new Map(); this.refObjs = new Map();
    this.style = "shaded_edges";
    this.hover = null; this.selection = [];
    this.shownPlanes = new Set(); this.planesOff = false; this.activeSketch = null; this.hiddenSketches = new Set();
    this.hiddenComponents = new Set(); this.viewHistory = [];
    this.mouse = { preset: "solidworks", zoomReverse: false };
    this.hoverFaces = new Set(); this.selFaces = new Set(); // a feature's faces, hovered / selected in the FeatureManager
    this.listeners = {}; this.frameHooks = [];
    this.bbox = new THREE.Box3(new THREE.Vector3(-50, -50, -50), new THREE.Vector3(50, 50, 50));
    this._setView("trimetric", false);
    this._buildCube();
    this._bindInput();
    // your own camera moves (mouse, keys, view and zoom tools) take the camera: followModel leaves it alone until
    // the next Zoom to Fit
    this.userHeld = false;
    for (const m of ["rotateBy", "panBy", "zoomAt", "setView", "normalTo", "zoomToRect", "zoomToItems", "applyView", "previousView"]) {
      const fn = this[m];
      this[m] = (...a) => { this.userHeld = true; this.anim2 = null; return fn.apply(this, a); };
    }
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
    this._loop();
  }

  on(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  emit(ev, ...a) { (this.listeners[ev] || []).forEach((f) => f(...a)); }

  setMesh(mesh) {
    this.mesh = mesh;
    for (const g of ["body", "edges", "sketch", "planes"]) this._clear(this.groups[g]);
    this.faces.clear(); this.edges.clear(); this.sketchEnts = []; this.planeObjs.clear();
    const box = new THREE.Box3();
    for (const f of mesh.faces) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(f.v, 3));
      applyIndex(geo, f.t); geo.computeVertexNormals(); geo.computeBoundingBox(); box.union(geo.boundingBox);
      const hex = f.color || mesh.material_color; // appearance: face/feature/part colour, else the material colour
      const base = hex ? parseInt(hex.slice(1), 16) : COL.body;
      const mat = new THREE.MeshPhongMaterial({ color: base, specular: 0x2a2a2a, shininess: 38, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
      const m = new THREE.Mesh(geo, mat); m.userData = { kind: "face", id: f.id, type: f.type, base, own: !!hex };
      this.groups.body.add(m); this.faces.set(f.id, m);
    }
    for (const f of mesh.surfaces || []) { // surface bodies: light, double-sided, not pickable
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(f.v, 3)); applyIndex(geo, f.t); geo.computeVertexNormals();
      geo.computeBoundingBox(); box.union(geo.boundingBox);
      this.groups.body.add(new THREE.Mesh(geo, new THREE.MeshPhongMaterial({ color: 0xd9c98a, side: THREE.DoubleSide, transparent: true, opacity: 0.85,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 })));
    }
    for (const f of mesh.context || []) { // Edit Part in context: the rest of the assembly, ghosted and not pickable
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(f.v, 3)); applyIndex(geo, f.t); geo.computeVertexNormals();
      this.groups.body.add(new THREE.Mesh(geo, new THREE.MeshPhongMaterial({ color: 0x8fa4bf, transparent: true, opacity: 0.28, depthWrite: false,
        side: THREE.DoubleSide })));
    }
    for (const e of mesh.edges) {
      const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(e.p, 3));
      const line = new THREE.Line(geo, new THREE.LineBasicMaterial({ color: COL.edge }));
      line.userData = { kind: "edge", id: e.id, pts: e.p };
      this.groups.edges.add(line); this.edges.set(e.id, line);
    }
    for (const s of mesh.sketches || []) {
      const visible = s.name === this.activeSketch || (!s.absorbed && !s.hidden && !this.hiddenSketches.has(s.name));
      if (!visible) continue;
      for (const e of s.entities) {
        const col = e.state === "dangling" ? COL.sketchDangling : e.construction ? COL.construction : e.state === "fully_defined" ? COL.sketchFull : e.state === "over_defined" ? COL.sketchOver : COL.sketchUnder;
        let obj;
        if (e.type === "point") {
          const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(e.p, 3));
          obj = new THREE.Points(geo, new THREE.PointsMaterial({ color: col, size: 6, sizeAttenuation: false, depthTest: false }));
        } else {
          const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(e.p, 3));
          const mat = e.construction ? new THREE.LineDashedMaterial({ color: col, dashSize: 3, gapSize: 2, depthTest: false })
            : new THREE.LineBasicMaterial({ color: col, depthTest: s.name !== this.activeSketch });
          obj = new THREE.Line(geo, mat); if (e.construction) obj.computeLineDistances();
        }
        obj.renderOrder = 5;
        obj.userData = { kind: "sketch", sketch: s.name, id: e.id, type: e.type, pts: e.p, baseColor: col };
        this.groups.sketch.add(obj); this.sketchEnts.push(obj);
        for (let i = 0; i < e.p.length; i += 3) box.expandByPoint(new THREE.Vector3(e.p[i], e.p[i + 1], e.p[i + 2]));
      }
    }
    if (!box.isEmpty()) this.bbox = box;
    this.planes = mesh.planes || [];
    this._buildPlanes();
    this._buildRefs(mesh.refs || []);
    this.analysisOn = false;
    this.applyStyle(); this._applyHighlights(); this.dirty = true;
    if (this.zebraOn) this.setZebra(true);
    if (this.section) this.setSection(this.section);
  }

  _buildPlanes() {
    this._clear(this.groups.planes); this.planeObjs.clear();
    const size = Math.max(this.bbox.getSize(new THREE.Vector3()).length() * 0.6, 60);
    for (const p of this.planes) {
      const o = new THREE.Vector3(...p.origin), x = new THREE.Vector3(...p.x), z = new THREE.Vector3(...p.z);
      const y = new THREE.Vector3().crossVectors(z, x);
      let c = o.clone();
      if (p.standard) { // centre standard planes on the model like SOLIDWORKS sizes them around it
        const bc = this.bbox.getCenter(new THREE.Vector3());
        c = o.clone().add(x.clone().multiplyScalar(bc.clone().sub(o).dot(x))).add(y.clone().multiplyScalar(bc.clone().sub(o).dot(y)));
      }
      const h = size / 2;
      const corners = [[-h, -h], [h, -h], [h, h], [-h, h]].map(([a, b]) => c.clone().add(x.clone().multiplyScalar(a)).add(y.clone().multiplyScalar(b)));
      const geo = new THREE.BufferGeometry().setFromPoints(corners); geo.setIndex([0, 1, 2, 0, 2, 3]);
      const quad = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: COL.plane, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false }));
      const border = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(corners), new THREE.LineBasicMaterial({ color: 0x3d68ad }));
      const grp = new THREE.Group(); grp.add(quad); grp.add(border);
      quad.userData = { kind: "plane", id: p.name };
      grp.userData = { name: p.name, corner: corners[3], hidden: !!p.hidden };
      grp.visible = this.planeVisible(p.name, grp);
      this.groups.planes.add(grp); this.planeObjs.set(p.name, grp);
    }
  }

  _buildRefs(refs) { // reference axes (dash-dot), points (stars) and coordinate systems (RGB triads)
    this._clear(this.groups.refs); this.refObjs.clear();
    const size = Math.max(this.bbox.getSize(new THREE.Vector3()).length() * 0.6, 60);
    for (const r of refs) {
      const grp = new THREE.Group(); grp.visible = !r.hidden;
      if (r.kind === "axis") {
        const o = new THREE.Vector3(...r.origin), d = new THREE.Vector3(...r.dir).normalize();
        const bc = this.bbox.isEmpty() ? o : this.bbox.getCenter(new THREE.Vector3());
        const mid = o.clone().add(d.clone().multiplyScalar(bc.clone().sub(o).dot(d)));
        const a = mid.clone().add(d.clone().multiplyScalar(-size / 2)), b = mid.clone().add(d.clone().multiplyScalar(size / 2));
        const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]),
          new THREE.LineDashedMaterial({ color: 0x3d68ad, dashSize: size / 40, gapSize: size / 80, depthTest: false }));
        line.computeLineDistances(); grp.add(line);
        grp.userData = { kind: "axis", id: r.name, pts: [...a.toArray(), ...b.toArray()] };
      } else if (r.kind === "point") {
        const p = new THREE.Vector3(...r.p), s = size / 60;
        const pts = [[s, 0, 0], [0, s, 0], [0, 0, s]].flatMap((v) => { const w = new THREE.Vector3(...v); return [p.clone().sub(w), p.clone().add(w)]; });
        grp.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x3d68ad, depthTest: false })));
        grp.userData = { kind: "point", id: r.name, p: p };
      } else if (r.kind === "curve") { // 3D sketch / helix
        for (const pl of r.polylines) grp.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(
          Array.from({ length: pl.length / 3 }, (_, i) => new THREE.Vector3(pl[3 * i], pl[3 * i + 1], pl[3 * i + 2]))), new THREE.LineBasicMaterial({ color: COL.sketchUnder })));
        grp.userData = { kind: "curve", id: r.name, polys: r.polylines };
      } else if (r.kind === "csys") {
        const o = new THREE.Vector3(...r.origin), L = size / 8;
        for (const [k, c] of [["x", 0xd0312d], ["y", 0x2f9e44], ["z", 0x1d4fd6]]) {
          const e = o.clone().add(new THREE.Vector3(...r[k]).multiplyScalar(L));
          grp.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([o, e]), new THREE.LineBasicMaterial({ color: c, depthTest: false })));
        }
        grp.userData = { kind: "csys", id: r.name, p: o };
      }
      grp.renderOrder = 5; this.groups.refs.add(grp); this.refObjs.set(r.name, grp);
    }
  }

  // section view: clip everything beyond the plane (kept side: (p - origin) . normal <= 0) and draw the cap
  setSection(sec) {
    this.section = sec || null;
    this.renderer.localClippingEnabled = !!sec;
    const planes = sec ? [new THREE.Plane(new THREE.Vector3(...sec.normal).negate(), new THREE.Vector3(...sec.normal).dot(new THREE.Vector3(...sec.origin)) + 1e-4)] : [];
    for (const g of ["body", "edges"]) this.groups[g].traverse((o) => { if (o.material) { o.material.clippingPlanes = planes; o.material.needsUpdate = true; } });
    if (!this.groups.section) { this.groups.section = new THREE.Group(); this.scene.add(this.groups.section); }
    this._clear(this.groups.section);
    for (const f of sec?.cap || []) {
      const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(f.v, 3)); applyIndex(geo, f.t); geo.computeVertexNormals();
      this.groups.section.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x9fb6dd, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1 })));
    }
    for (const e of sec?.cap_edges || []) {
      const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(e.p, 3));
      this.groups.section.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0x1d4fd6 })));
    }
    this.dirty = true;
  }

  // analysis colours: {faceId: 0xrrggbb} (draft) or {faceId: [[r,g,b] per vertex]} (curvature); null restores
  setAnalysis(colors) {
    for (const [id, m] of this.faces) {
      const c = colors?.[id];
      if (Array.isArray(c)) {
        m.geometry.setAttribute("color", new THREE.Float32BufferAttribute(c.flat(), 3));
        m.material.vertexColors = true; m.material.color.setHex(0xffffff);
      } else {
        m.material.vertexColors = false; m.geometry.deleteAttribute?.("color");
        m.material.color.setHex(c ?? m.userData.base ?? COL.body);
      }
      m.userData.analysis = c ?? null;
      m.material.needsUpdate = true;
    }
    this.analysisOn = !!colors; this.dirty = true;
  }

  // temporarily show a different (e.g. refined result) triangulation per face; null restores the model mesh
  setFaceGeometry(faces) {
    const by = Object.fromEntries((faces || []).map((f) => [f.id, f]));
    for (const [id, m] of this.faces) {
      const f = by[id];
      if (f) {
        m.userData.origGeo ??= m.geometry;
        const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(f.v, 3)); applyIndex(geo, f.t); geo.computeVertexNormals();
        if (m.geometry !== m.userData.origGeo) m.geometry.dispose();
        m.geometry = geo;
      } else if (m.userData.origGeo) { if (m.geometry !== m.userData.origGeo) m.geometry.dispose(); m.geometry = m.userData.origGeo; delete m.userData.origGeo; }
      delete m.userData.rest;
    }
    this.dirty = true;
  }

  // deformed shape: displace each face's vertices by scale * displacement ({faceId: [[dx,dy,dz] per vertex]})
  setDeformation(disp, scale = 1) {
    for (const [id, m] of this.faces) {
      const pos = m.geometry.getAttribute("position");
      m.userData.rest ??= Float32Array.from(pos.array);
      const d = disp?.[id];
      for (let i = 0; i < pos.count; i++) {
        const o = d?.[i] || [0, 0, 0];
        pos.setXYZ(i, m.userData.rest[3 * i] + scale * o[0], m.userData.rest[3 * i + 1] + scale * o[1], m.userData.rest[3 * i + 2] + scale * o[2]);
      }
      pos.needsUpdate = true; m.geometry.computeVertexNormals();
    }
    this.groups.edges.visible = !disp; // edges are not deformed: hide them while showing the deformed shape
    this.dirty = true;
  }

  // colour legend for result plots: {title, min, max, unit} or null
  setLegend(lg) {
    let el = this.canvas.parentElement.querySelector(".vp-legend");
    if (!lg) { el?.remove(); return; }
    if (!el) { el = document.createElement("div"); el.className = "vp-legend"; this.canvas.parentElement.append(el); }
    const stops = 8, rows = [];
    for (let k = stops; k >= 0; k--) rows.push(`<div><span style="background:${lg.color(k / stops)}"></span>${(lg.min + (lg.max - lg.min) * k / stops).toExponential(3)}</div>`);
    el.innerHTML = `<b>${lg.title}</b><small>${lg.unit}</small>${rows.join("")}`;
  }

  // zebra stripes: a striped matcap shows surface continuity like SOLIDWORKS' Zebra Stripes
  setZebra(on) {
    if (on && !this.zebraTex) {
      const cv = document.createElement("canvas"); cv.width = cv.height = 256; const g = cv.getContext("2d");
      for (let y = 0; y < 256; y += 16) { g.fillStyle = (y / 16) % 2 ? "#111" : "#f4f4f4"; g.fillRect(0, y, 256, 16); }
      this.zebraTex = new THREE.CanvasTexture(cv);
    }
    for (const m of this.faces.values()) {
      if (on) { m.userData.mat ??= m.material; m.material = new THREE.MeshMatcapMaterial({ matcap: this.zebraTex, side: THREE.DoubleSide, clippingPlanes: m.userData.mat.clippingPlanes }); }
      else if (m.userData.mat) { m.material.dispose(); m.material = m.userData.mat; delete m.userData.mat; }
    }
    this.zebraOn = !!on; this.dirty = true;
  }

  // motion playback: move each component's faces/edges by pose * current^-1 (poses are [rotvec, translation])
  setComponentPoses(poses, current) {
    const M = (x) => { const rv = new THREE.Vector3(x[0], x[1], x[2]), a = rv.length();
      const q = a > 1e-12 ? new THREE.Quaternion().setFromAxisAngle(rv.clone().divideScalar(a), a) : new THREE.Quaternion();
      return new THREE.Matrix4().compose(new THREE.Vector3(x[3], x[4], x[5]), q, new THREE.Vector3(1, 1, 1)); };
    const delta = {};
    for (const [n, x] of Object.entries(poses || {})) if (current[n]) delta[n] = M(x).multiply(M(current[n]).invert());
    for (const obj of [...this.faces.values(), ...this.edges.values()]) {
      const comp = obj.userData.id.split("/")[0], d = delta[comp];
      obj.matrixAutoUpdate = !d; if (d) obj.matrix.copy(d); else obj.matrix.identity();
      obj.matrixWorldNeedsUpdate = true;
    }
    this.dirty = true;
  }

  // planes show translucent unless hidden (the item's own Hide, or View > Planes off); shownPlanes: shown anyway for now.
  // While a sketch is open SOLIDWORKS draws no planes (not even the sketch's own) unless one is shown on purpose.
  planeVisible(n, g) { return this.shownPlanes.has(n) || (!this.activeSketch && !this.planesOff && !g.userData.hidden); }
  showPlanes(names) { this.shownPlanes = new Set(names); for (const [n, g] of this.planeObjs) g.visible = this.planeVisible(n, g); this.dirty = true; }
  togglePlanes() { this.planesOff = !this.planesOff; this.showPlanes([...this.shownPlanes]); }

  setPreview(mesh) {
    this._clear(this.groups.preview);
    if (mesh && this.activeSketch) { // a sketch tool's preview (offset, mirror, pattern...): new or moved entities
      const cur = new Map(this.sketchEnts.filter((o) => o.userData.sketch === this.activeSketch).map((o) => [o.userData.id, o.userData.pts.join()]));
      const sk = (mesh.sketches || []).find((x) => x.name === this.activeSketch);
      for (const e of sk?.entities || []) {
        if (cur.get(e.id) === e.p.join()) continue;
        const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(e.p, 3));
        const o = e.type === "point" ? new THREE.Points(geo, new THREE.PointsMaterial({ color: 0xd18f00, size: 7, sizeAttenuation: false, depthTest: false }))
          : new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0xd18f00, depthTest: false }));
        o.renderOrder = 9; this.groups.preview.add(o);
      }
      this.dirty = true;
      return;
    }
    const fresh = (mesh?.refs || []).filter((r) => !this.refObjs.has(r.name));
    if (fresh.length) { // reference geometry preview: draw just the new axis/point/csys in the preview colour
      const keep = this.refObjs; this.refObjs = new Map(); const grp = this.groups.refs; this.groups.refs = this.groups.preview;
      this._buildRefs(fresh);
      for (const g of this.refObjs.values()) g.traverse((c) => { if (c.material && fresh[0].kind !== "csys") c.material.color.setHex(0xd18f00); });
      this.groups.refs = grp; this.refObjs = keep; this.dirty = true;
      return;
    }
    if (mesh && mesh.faces) {
      for (const f of mesh.faces) {
        const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(f.v, 3));
        applyIndex(geo, f.t); geo.computeVertexNormals();
        // translucent overlay drawn over the model, which stays visible and pickable
        const m = new THREE.Mesh(geo, new THREE.MeshPhongMaterial({ color: COL.preview, transparent: true, opacity: 0.42,
          depthWrite: false, depthTest: false, side: THREE.DoubleSide }));
        m.renderOrder = 8; this.groups.preview.add(m);
      }
      for (const e of mesh.edges) {
        const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(e.p, 3));
        const l = new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0x9c7a00, transparent: true, opacity: 0.7, depthTest: false }));
        l.renderOrder = 9; this.groups.preview.add(l);
      }
    }
    this.dirty = true;
  }

  // temporary sketch geometry (rubber-band while drawing): list of {pts:[x,y,z...], color, dashed}
  setTemp(items) {
    this._clear(this.groups.temp);
    for (const it of items || []) {
      const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(it.pts, 3));
      let obj;
      if (it.point) obj = new THREE.Points(geo, new THREE.PointsMaterial({ color: it.color ?? COL.hover, size: it.size ?? 8, sizeAttenuation: false, depthTest: false }));
      else {
        const mat = it.dashed ? new THREE.LineDashedMaterial({ color: it.color ?? COL.sketchUnder, dashSize: 2, gapSize: 2, depthTest: false })
          : new THREE.LineBasicMaterial({ color: it.color ?? COL.sketchUnder, depthTest: false });
        obj = new THREE.Line(geo, mat); if (it.dashed) obj.computeLineDistances();
      }
      obj.renderOrder = 10; this.groups.temp.add(obj);
    }
    this.dirty = true;
  }

  // result overlays that stay until cleared (CAM toolpaths, plastics markers): {pts, color, dashed, point, size, onTop}
  setOverlay(items) {
    this._clear(this.groups.overlay);
    for (const it of items || []) {
      const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(it.pts, 3));
      let obj;
      if (it.point) obj = new THREE.Points(geo, new THREE.PointsMaterial({ color: it.color ?? COL.hover, size: it.size ?? 8, sizeAttenuation: false, depthTest: !it.onTop }));
      else {
        const mat = it.dashed ? new THREE.LineDashedMaterial({ color: it.color ?? 0x888888, dashSize: 1.5, gapSize: 1.5, depthTest: !it.onTop })
          : new THREE.LineBasicMaterial({ color: it.color ?? 0x2f7be0, depthTest: !it.onTop });
        obj = new THREE.Line(geo, mat); if (it.dashed) obj.computeLineDistances();
      }
      obj.renderOrder = 8; this.groups.overlay.add(obj);
    }
    this.dirty = true;
  }

  applyStyle(style) {
    if (style) this.style = style;
    const s = this.style;
    for (const [id, m] of this.faces) {
      m.visible = s !== "wireframe" && this.componentShown(id);
      m.material.color.setHex(s === "hlr" ? 0xffffff : m.userData.base ?? COL.body);
      m.material.emissive?.setHex(s === "hlr" ? 0xffffff : 0x000000);
    }
    this.groups.edges.visible = s !== "shaded";
    for (const [id, l] of this.edges) { l.material.depthTest = s !== "wireframe"; l.visible = this.componentShown(id); }
    this._applyHighlights(); this.dirty = true;
  }

  // assembly Hide/Show Components: ids are "Component/Face3"
  componentShown(id) { return !this.hiddenComponents.size || !this.hiddenComponents.has(String(id).split("/")[0]); }
  setHiddenComponents(names) { this.hiddenComponents = new Set(names); this.applyStyle(); }

  setFeatureFaces(hover, sel) { // FeatureManager cross-highlighting
    if (hover !== undefined) this.hoverFaces = new Set(hover || []);
    if (sel !== undefined) this.selFaces = new Set(sel || []);
    this._applyHighlights();
  }

  setHover(h) { if (JSON.stringify(h) !== JSON.stringify(this.hover)) { this.hover = h; this._applyHighlights(); } }
  setSelection(list) { this.selection = list.slice(); this._applyHighlights(); }

  _applyHighlights() {
    const isSel = (k, id, sk) => this.selection.some((s) => s.kind === k && s.id === id && (!sk || s.sketch === sk));
    const isHov = (k, id, sk) => this.hover && this.hover.kind === k && this.hover.id === id && (!sk || this.hover.sketch === sk);
    for (const [id, m] of this.faces) {
      const a = m.userData.analysis; // analysis colouring wins over the appearance colour
      const base = this.style === "hlr" ? 0xffffff : Array.isArray(a) ? 0xffffff : a ?? m.userData.base ?? COL.body;
      if (!m.material.color) continue; // matcap (zebra) material
      m.material.color.setHex(isSel("face", id) || this.selFaces.has(id) ? tint(COL.sel) : isHov("face", id) || this.hoverFaces.has(id) ? tint(COL.hover) : base);
    }
    for (const [id, l] of this.edges) l.material.color.setHex(isSel("edge", id) ? COL.sel : isHov("edge", id) ? COL.hover : COL.edge);
    for (const o of this.sketchEnts) {
      const { id, sketch, baseColor } = o.userData;
      o.material.color.setHex(isSel("sketch", id, sketch) ? COL.sel : isHov("sketch", id, sketch) ? COL.hover : baseColor);
    }
    for (const [n, g] of this.planeObjs) {
      const hov = isHov("plane", n), sel = isSel("plane", n);
      g.visible = this.planeVisible(n, g) || (!this.activeSketch && (hov || sel));
      g.children[0].material.color.setHex(sel ? COL.sel : hov ? COL.hover : COL.plane);
      g.children[1].material.color.setHex(sel ? COL.sel : hov ? COL.hover : 0x3d68ad);
    }
    for (const [n, g] of this.refObjs) {
      const k = g.userData.kind, hl = isSel(k, n) ? COL.sel : isHov(k, n) ? COL.hover : null;
      g.children.forEach((c, i) => c.material.color.setHex(hl ?? (k === "csys" ? [0xd0312d, 0x2f9e44, 0x1d4fd6][i] : k === "curve" ? COL.sketchUnder : 0x3d68ad)));
    }
    this.dirty = true;
  }

  resize() {
    const el = this.canvas.parentElement; const w = el.clientWidth, h = el.clientHeight;
    if (!w || !h) return;
    this.w = w; this.h = h;
    this.renderer.setSize(w, h, false);
    this._updateProjection(); this.dirty = true;
  }

  _updateProjection() {
    const a = (this.w || 1) / (this.h || 1), hh = this.viewHeight / 2;
    Object.assign(this.ortho, { left: -hh * a, right: hh * a, top: hh, bottom: -hh });
    this.ortho.updateProjectionMatrix();
    if (this.camera.isPerspectiveCamera) {
      const d = this._perspDist(), size = this.bbox.isEmpty() ? 100 : this.bbox.getSize(new THREE.Vector3()).length();
      Object.assign(this.persp, { aspect: a, near: Math.max(d / 1000, 1e-3), far: d * 4 + size * 8 });
      this.persp.updateProjectionMatrix();
      this._placeCamera();
    }
  }

  get worldPerPixel() { return this.viewHeight / (this.h || 1); }

  _setView(name, animate = true, dir = null, up = null) {
    const [d, u] = dir ? [dir, up] : VIEWS[name] || VIEWS.isometric;
    const D = new THREE.Vector3(...d).normalize(), U = new THREE.Vector3(...u).normalize();
    const eye = this.target.clone().add(D.clone().multiplyScalar(1000));
    const m = new THREE.Matrix4().lookAt(eye, this.target, U);
    const q = new THREE.Quaternion().setFromRotationMatrix(m);
    if (!animate) { this.camera.quaternion.copy(q); this._placeCamera(); this.dirty = true; return; }
    const q0 = this.camera.quaternion.clone(), t0 = performance.now();
    this.anim = (t) => {
      const k = Math.min(1, (t - t0) / 320), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      this.camera.quaternion.slerpQuaternions(q0, q, e); this._placeCamera();
      if (k >= 1) {
        this.anim = null;
        if (this.pendingFit) { this.pendingFit = false; this.zoomFit(); }
      }
    };
  }

  pushView() { this.viewHistory.push(this.viewState()); if (this.viewHistory.length > 20) this.viewHistory.shift(); this.savedView = this.viewHistory.at(-1); }
  previousView() { const v = this.viewHistory.pop(); if (v) this.restoreView(v); this.savedView = this.viewHistory.at(-1); }
  setView(name) { this.pushView(); this._setView(name); }
  normalTo(normal, xdir) { // look straight at a plane: sketch x axis to the right
    const n = new THREE.Vector3(...normal).normalize(), x = new THREE.Vector3(...xdir).normalize();
    const up = new THREE.Vector3().crossVectors(n, x);
    this.pushView(); this._setView(null, true, n.toArray(), up.toArray());
  }

  viewState() { return { q: this.camera.quaternion.clone(), t: this.target.clone(), h: this.viewHeight }; }
  applyView(v) { // a saved (named) view: {q: [x, y, z, w], t: [x, y, z], h, perspective}
    this.pushView();
    if (v.perspective !== undefined) this.setPerspective(v.perspective);
    this.camera.quaternion.set(...v.q); this.target.set(...v.t); this.viewHeight = v.h;
    this._updateProjection(); this._placeCamera();
  }
  savedViewData() { const q = this.camera.quaternion; return { quaternion: [q.x, q.y, q.z, q.w], target: this.target.toArray(), height: this.viewHeight, perspective: !!this.perspective }; }
  restoreView(s) { if (!s) return; this.camera.quaternion.copy(s.q); this.target.copy(s.t); this.viewHeight = s.h; this._placeCamera(); this._updateProjection(); this.dirty = true; }

  _placeCamera() {
    const back = new THREE.Vector3(0, 0, 1).applyQuaternion(this.camera.quaternion);
    this.camera.position.copy(this.target).add(back.multiplyScalar(this.camera.isPerspectiveCamera ? this._perspDist() : 5000));
    this.camera.updateMatrixWorld(); this.dirty = true;
  }
  // perspective: the camera stands where the view height fits the field of view at the target
  _perspDist() { return (this.viewHeight / 2) / Math.tan(THREE.MathUtils.degToRad(this.persp.fov / 2)); }

  setPerspective(on) {
    const to = on ? this.persp : this.ortho;
    if (to === this.camera) return;
    to.quaternion.copy(this.camera.quaternion);
    for (const l of this.lights) to.add(l);
    this.camera = to; this.perspective = !!on;
    this._updateProjection(); this._placeCamera();
  }

  // middle-click a face or vertex: the view then rotates about that point (a click on nothing clears it)
  setPivot(p) {
    this.pivot = p ? new THREE.Vector3(...p) : null;
    if (!this.pivotMark) {
      const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0], 3));
      this.pivotMark = new THREE.Points(geo, new THREE.PointsMaterial({ color: 0xd0312d, size: 9, sizeAttenuation: false, depthTest: false }));
      this.pivotMark.renderOrder = 12; this.scene.add(this.pivotMark);
    }
    this.pivotMark.visible = !!this.pivot;
    if (this.pivot) this.pivotMark.position.copy(this.pivot);
    this.dirty = true;
  }

  zoomFit(animate = true, only = null) {
    if (!only) this.userHeld = false; // (the whole model in view again: following an agent may resume)
    if (this.anim && animate) { this.pendingFit = true; return; } // fit for the final orientation
    const box = only && !only.isEmpty() ? only : this.bbox.isEmpty() ? new THREE.Box3(new THREE.Vector3(-50, -50, -50), new THREE.Vector3(50, 50, 50)) : this.bbox;
    const { c, height } = this._fit(box);
    if (!animate) { this.anim2 = null; this.target.copy(c); this.viewHeight = height; this._placeCamera(); this._updateProjection(); return; }
    this._glide(c, height, 260);
  }

  // where the camera looks and how much it shows (view height) to frame `box` from the current direction
  _fit(box) {
    const c = box.getCenter(new THREE.Vector3());
    const { w, h } = this._extent(box, c);
    const a = (this.w || 1) / (this.h || 1);
    return { c, height: Math.max(2 * h, (2 * w) / a, 1) * 1.25 };
  }

  // half-width and half-height of `box` on screen about the point `c`, in model units
  _extent(box, c) {
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion), up = new THREE.Vector3(0, 1, 0).applyQuaternion(this.camera.quaternion);
    let w = 0, h = 0;
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      const p = new THREE.Vector3(x, y, z).sub(c); w = Math.max(w, Math.abs(p.dot(right))); h = Math.max(h, Math.abs(p.dot(up)));
    }
    return { w, h };
  }

  _glide(c, height, ms) { // move the target and view height there, easing in and out
    const t0 = performance.now(), c0 = this.target.clone(), h0 = this.viewHeight;
    this.anim2 = (t) => {
      const k = Math.min(1, (t - t0) / ms), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      this.target.lerpVectors(c0, c, e); this.viewHeight = h0 + (height - h0) * e;
      this._placeCamera(); this._updateProjection();
      if (k >= 1) this.anim2 = null;
    };
  }

  // An agent changed the model: if it no longer sits well in the view (part of it outside, or it fills under 40%
  // of it), glide to frame it. Nothing moves while the camera is yours (see userHeld) or a view change is running.
  followModel() {
    if (this.userHeld || this.anim || this.anim2 || this.bbox.isEmpty()) return false;
    const a = (this.w || 1) / (this.h || 1), hh = this.viewHeight / 2;
    const { w, h } = this._extent(this.bbox, this.target);
    const fit = this._fit(this.bbox);
    if (w <= hh * a * 0.95 && h <= hh * 0.95 && fit.height >= this.viewHeight * 0.4) return false;
    this._glide(fit.c, fit.height, 600);
    return true;
  }

  // Zoom to Selection: fit the selected faces / edges / sketch entities
  zoomToItems(items) {
    const box = new THREE.Box3();
    for (const it of items) {
      const o = it.kind === "face" ? this.faces.get(it.id) : it.kind === "edge" ? this.edges.get(it.id)
        : it.kind === "sketch" ? this.sketchEnts.find((x) => x.userData.id === it.id && x.userData.sketch === it.sketch) : null;
      if (o) { o.geometry.computeBoundingBox(); box.union(o.geometry.boundingBox); }
    }
    if (box.isEmpty()) return this.zoomFit();
    const pad = box.getSize(new THREE.Vector3()).length() * 0.1 || 1;
    this.pushView(); this.zoomFit(true, box.expandByScalar(pad));
  }

  // Zoom to Area: the screen rectangle fills the view
  zoomToRect(x0, y0, x1, y1) {
    const w = Math.abs(x1 - x0), h = Math.abs(y1 - y0); if (w < 4 || h < 4) return;
    this.pushView();
    const c = this.screenToWorldOnTargetPlane((x0 + x1) / 2, (y0 + y1) / 2);
    const k = Math.max(h / this.h, w / this.w);
    this.target.copy(c); this.viewHeight = Math.max(this.viewHeight * k, 1e-3);
    this._placeCamera(); this._updateProjection();
  }

  rotateBy(dx, dy) {
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(this.camera.quaternion);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion);
    const q = new THREE.Quaternion().setFromAxisAngle(up, -dx).multiply(new THREE.Quaternion().setFromAxisAngle(right, -dy));
    this.camera.quaternion.premultiply(q).normalize();
    if (this.pivot) this.target.sub(this.pivot).applyQuaternion(q).add(this.pivot); // turn about the picked point
    this._placeCamera();
  }

  panBy(dxPix, dyPix) {
    const s = this.worldPerPixel;
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion), up = new THREE.Vector3(0, 1, 0).applyQuaternion(this.camera.quaternion);
    this.target.add(right.multiplyScalar(-dxPix * s)).add(up.multiplyScalar(dyPix * s));
    this._placeCamera();
  }

  zoomAt(factor, sx, sy) {
    const before = this.screenToWorldOnTargetPlane(sx, sy);
    this.viewHeight = Math.min(Math.max(this.viewHeight * factor, 1e-3), 1e6);
    this._updateProjection();
    const after = this.screenToWorldOnTargetPlane(sx, sy);
    this.target.add(before.sub(after)); this._placeCamera();
  }

  ndc(sx, sy) { return new THREE.Vector2((sx / this.w) * 2 - 1, -(sy / this.h) * 2 + 1); }
  ray(sx, sy) { const r = new THREE.Raycaster(); r.setFromCamera(this.ndc(sx, sy), this.camera); return r; }
  screenToWorldOnTargetPlane(sx, sy) {
    const n = new THREE.Vector3(0, 0, 1).applyQuaternion(this.camera.quaternion);
    const pl = new THREE.Plane().setFromNormalAndCoplanarPoint(n, this.target);
    return this.ray(sx, sy).ray.intersectPlane(pl, new THREE.Vector3()) || this.target.clone();
  }
  screenToPlane(sx, sy, origin, x, z) { // -> sketch-local [u, v]
    const O = new THREE.Vector3(...origin), X = new THREE.Vector3(...x), Z = new THREE.Vector3(...z), Y = new THREE.Vector3().crossVectors(Z, X);
    const p = this.ray(sx, sy).ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(Z, O), new THREE.Vector3());
    if (!p) return null;
    p.sub(O); return [p.dot(X), p.dot(Y)];
  }
  toScreen(v) {
    const p = (v.isVector3 ? v.clone() : new THREE.Vector3(...v)).project(this.camera);
    return [(p.x + 1) / 2 * this.w, (1 - p.y) / 2 * this.h, p.z];
  }

  pick(sx, sy, opts = {}) {
    const { sketchOnly = false, kinds = null } = opts;
    const want = (k) => !kinds || kinds.includes(k);
    // 1) sketch points and entities in screen space
    let best = null, bestD = 1e9;
    if (want("sketch")) {
      for (const o of this.sketchEnts) {
        const pts = o.userData.pts;
        if (o.userData.type === "point") {
          const [x, y] = this.toScreen(pts.slice(0, 3)); const d = Math.hypot(x - sx, y - sy);
          if (d < 8 && d - 2 < bestD) { best = { kind: "sketch", id: o.userData.id, sketch: o.userData.sketch, type: "point" }; bestD = d - 2; }
          continue;
        }
        const d = this._polyDist(pts, sx, sy);
        if (d < 6 && d < bestD) { best = { kind: "sketch", id: o.userData.id, sketch: o.userData.sketch, type: o.userData.type }; bestD = d; }
      }
      if (best || sketchOnly) return best;
    }
    // 2) reference axes / points / coordinate systems
    for (const [id, g] of this.refObjs) {
      if (!g.visible || !want(g.userData.kind)) continue;
      const d = g.userData.kind === "axis" ? this._polyDist(g.userData.pts, sx, sy)
        : g.userData.kind === "curve" ? Math.min(...g.userData.polys.map((pl) => this._polyDist(pl, sx, sy)))
        : Math.hypot(...((s) => [s[0] - sx, s[1] - sy])(this.toScreen(g.userData.p)));
      if (d < 6 && d < bestD) { best = { kind: g.userData.kind, id }; bestD = d; }
    }
    if (best) return best;
    // vertices (edge end points), only when a command asks for them
    if (kinds && kinds.includes("vertex")) {
      for (const l of this.edges.values()) {
        const p = l.userData.pts;
        for (const k of [0, p.length - 3]) {
          const w = [p[k], p[k + 1], p[k + 2]], s = this.toScreen(w), d = Math.hypot(s[0] - sx, s[1] - sy);
          if (d < 8 && d < bestD) { best = { kind: "vertex", id: w.map((c) => c.toFixed(4)).join(","), point: w }; bestD = d; }
        }
      }
      if (best) return best;
    }
    // 3) edges
    if (want("edge") && this.groups.edges.visible) {
      for (const [id, l] of this.edges) {
        const d = this._polyDist(l.userData.pts, sx, sy, true);
        if (d < 5 && d < bestD) { best = { kind: "edge", id }; bestD = d; }
      }
      if (best) return best;
    }
    // 3) faces
    const ray = this.ray(sx, sy);
    if (want("face")) {
      const hits = ray.intersectObjects([...this.faces.values()].filter((m) => m.visible), false);
      if (hits.length) return { kind: "face", id: hits[0].object.userData.id, point: hits[0].point.toArray(), normal: hits[0].face?.normal?.toArray() };
    }
    // 4) visible planes
    if (want("plane")) {
      const quads = [...this.planeObjs.values()].filter((g) => g.visible).map((g) => g.children[0]);
      const hits = ray.intersectObjects(quads, false);
      if (hits.length) return { kind: "plane", id: hits[0].object.userData.id };
    }
    return null;
  }

  pickAll(sx, sy) {
    const out = [], seen = new Set();
    for (const [id, l] of this.edges) if (l.visible && this.groups.edges.visible && this._polyDist(l.userData.pts, sx, sy) < 6) out.push({ kind: "edge", id });
    for (const h of this.ray(sx, sy).intersectObjects([...this.faces.values()].filter((m) => m.visible), false)) {
      const id = h.object.userData.id; if (seen.has(id)) continue; seen.add(id);
      out.push({ kind: "face", id, point: h.point.toArray(), normal: h.face?.normal?.toArray() });
    }
    return out;
  }

  _polyDist(pts, sx, sy, occlusion = false) {
    let best = 1e9, bestP = null;
    let prev = null, prevW = null;
    for (let i = 0; i < pts.length; i += 3) {
      const w = new THREE.Vector3(pts[i], pts[i + 1], pts[i + 2]);
      const s = this.toScreen(w);
      if (prev) {
        const [d, t] = segDistT(sx, sy, prev[0], prev[1], s[0], s[1]);
        if (d < best) { best = d; bestP = prevW.clone().lerp(w, t); }
      }
      prev = s; prevW = w;
    }
    if (occlusion && best < 6 && bestP) { // ignore edges hidden behind faces (depth at the picked point)
      const hits = this.ray(sx, sy).intersectObjects([...this.faces.values()].filter((m) => m.visible), false);
      if (hits.length) {
        const camDir = new THREE.Vector3(0, 0, -1).applyQuaternion(this.camera.quaternion);
        const depthEdge = bestP.clone().sub(this.camera.position).dot(camDir);
        const depthHit = hits[0].point.clone().sub(this.camera.position).dot(camDir);
        if (depthEdge > depthHit + this.viewHeight * 0.01) return 1e9;
      }
    }
    return best;
  }

  // User Settings: body, edge, highlight and selection colours ("#rrggbb"; missing ones are left as they are)
  setColors({ body, edge, hover, sel } = {}) {
    const hex = (c) => (typeof c === "string" && /^#[0-9a-f]{6}$/i.test(c) ? parseInt(c.slice(1), 16) : null);
    for (const [k, v] of Object.entries({ body, edge, hover, sel })) if (hex(v) !== null) COL[k] = hex(v);
    for (const m of this.faces.values()) if (!m.userData.own) m.userData.base = COL.body; // (faces with no appearance of their own)
    if (this.faces.size) { this.applyStyle(this.style); this._applyHighlights(); }
  }

  _bindInput() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    c.addEventListener("pointerdown", (e) => {
      try { c.setPointerCapture(e.pointerId); } catch { /* (a pointer that has already gone) */ }
      const mode = e.button === 0 ? (e.altKey ? "rotate" : null) : (MOUSE[this.mouse.preset] || MOUSE.solidworks)(e.button, e);
      drag = { x: e.clientX, y: e.clientY, mode, button: e.button, moved: false, sx: e.offsetX, sy: e.offsetY };
      if (e.button === 1) e.preventDefault();
      if (!mode) this.emit("down", { x: e.offsetX, y: e.offsetY, button: e.button, event: e });
    });
    c.addEventListener("pointermove", (e) => {
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
        drag.x = e.clientX; drag.y = e.clientY;
        if (drag.mode === "rotate") { this.rotateBy(dx * 0.008, dy * 0.008); return; }
        if (drag.mode === "pan") { this.panBy(dx, dy); return; }
        if (drag.mode === "zoom") { this.zoomAt(Math.exp(dy * 0.01), drag.sx, drag.sy); return; }
      }
      this.emit("move", { x: e.offsetX, y: e.offsetY, buttons: e.buttons, drag, event: e });
    });
    c.addEventListener("pointerup", (e) => {
      const d = drag; drag = null;
      if (d && d.button === 2 && d.mode && !d.moved) { this.emit("rightclick", { x: e.offsetX, y: e.offsetY, event: e }); return; } // (right drag moves the view; a right click is still one)
      if (d && d.button === 1 && d.mode && !d.moved) { // middle click: set (or clear) the rotation centre
        const pk = this.pick(e.offsetX, e.offsetY, { kinds: ["vertex", "face"] });
        this.setPivot(pk?.point || null);
        return;
      }
      if (d && !d.mode) this.emit("up", { x: e.offsetX, y: e.offsetY, button: e.button, moved: d.moved, event: e });
      if (d && !d.mode && !d.moved && e.button === 0) this.emit("click", { x: e.offsetX, y: e.offsetY, event: e });
      if (d && !d.mode && !d.moved && e.button === 2) this.emit("rightclick", { x: e.offsetX, y: e.offsetY, event: e });
    });
    c.addEventListener("dblclick", (e) => this.emit("dblclick", { x: e.offsetX, y: e.offsetY, event: e }));
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      // SOLIDWORKS default: rolling the wheel forward (away from you) zooms out.
      // (User Settings > mouse_zoom_reverse turns it round)
      const out = (e.deltaY < 0) !== this.mouse.zoomReverse;
      this.zoomAt(out ? 1.12 : 1 / 1.12, e.offsetX, e.offsetY);
    }, { passive: false });
    c.addEventListener("pointerleave", () => this.emit("leave"));
  }

  _loop() {
    const tick = (t) => {
      requestAnimationFrame(tick); // first: an exception in a frame hook must not stop the view updating
      if (this.anim) this.anim(t);
      if (this.anim2) this.anim2(t);
      if (this.dirty || this.anim || this.anim2) {
        this.dirty = false;
        this.renderer.render(this.scene, this.camera);
        this._drawTriad(); this._updateCube();
        for (const f of this.frameHooks) f();
      }
    };
    requestAnimationFrame(tick);
  }

  _buildCube() {
    const host = this.canvas.parentElement, cube = document.createElement("div"); cube.className = "vcube";
    const body = document.createElement("div"); body.className = "vc-body"; cube.append(body);
    const faces = [["front", "Front", "translateZ(30px)"], ["back", "Back", "rotateY(180deg) translateZ(30px)"],
      ["right", "Right", "rotateY(90deg) translateZ(30px)"], ["left", "Left", "rotateY(-90deg) translateZ(30px)"],
      ["top", "Top", "rotateX(90deg) translateZ(30px)"], ["bottom", "Bottom", "rotateX(-90deg) translateZ(30px)"]];
    for (const [v, label, tf] of faces) {
      const f = document.createElement("div"); f.className = "vc-face"; f.textContent = label; f.style.transform = tf; f.title = `${label} view`;
      f.onclick = (e) => { e.stopPropagation(); this.setView(v); }; body.append(f);
    }
    const iso = document.createElement("button"); iso.className = "vc-iso"; iso.textContent = "Iso"; iso.title = "Isometric view";
    iso.onclick = (e) => { e.stopPropagation(); this.setView("isometric"); }; cube.append(iso);
    host.append(cube); this.cubeBody = body;
  }
  _updateCube() { // the cube shows the world axes as the camera sees them (CSS y points down)
    if (!this.cubeBody) return;
    const F = new THREE.Matrix4().makeScale(1, -1, 1), R = new THREE.Matrix4().extractRotation(this.camera.matrixWorldInverse);
    const m = new THREE.Matrix4().multiplyMatrices(F, R).multiply(F);
    this.cubeBody.style.transform = `matrix3d(${m.elements.map((x) => x.toFixed(6)).join(",")})`;
  }

  _drawTriad() {
    const g = this.triad.getContext("2d"); const W = this.triad.width;
    g.clearRect(0, 0, W, W);
    const q = this.camera.quaternion.clone().invert();
    const axes = [["X", [1, 0, 0], "#d22"], ["Y", [0, 1, 0], "#1a9a2a"], ["Z", [0, 0, 1], "#2050d0"]]
      .map(([n, v, col]) => [n, new THREE.Vector3(...v).applyQuaternion(q), col]).sort((a, b) => a[1].z - b[1].z);
    const c = W / 2, L = W * 0.32;
    for (const [n, v, col] of axes) {
      g.strokeStyle = col; g.fillStyle = col; g.lineWidth = 2.2;
      g.beginPath(); g.moveTo(c, c); g.lineTo(c + v.x * L, c - v.y * L); g.stroke();
      const ang = Math.atan2(-v.y, v.x), ex = c + v.x * L, ey = c - v.y * L;
      g.beginPath(); g.moveTo(ex, ey); g.lineTo(ex - 7 * Math.cos(ang - 0.4), ey - 7 * Math.sin(ang - 0.4)); g.lineTo(ex - 7 * Math.cos(ang + 0.4), ey - 7 * Math.sin(ang + 0.4)); g.fill();
      g.font = "bold 11px Segoe UI"; g.fillText(n, c + v.x * (L + 10) - 4, c - v.y * (L + 10) + 4);
    }
  }

  _clear(group) { while (group.children.length) { const o = group.children.pop(); o.traverse?.((x) => { x.geometry?.dispose(); x.material?.dispose?.(); }); } }
}

function segDistT(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy;
  let t = L ? ((px - ax) * dx + (py - ay) * dy) / L : 0; t = Math.max(0, Math.min(1, t));
  return [Math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t];
}

export const VIEW_NAMES = VIEWS;
