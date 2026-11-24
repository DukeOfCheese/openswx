// More commands: PropertyManagers and dialogs for Flex/Deform, fillet profiles, mold tools, Insert Part, the extra
// surface tools, assembly tools (SmartMates, routing, SpeedPak...), analysis studies, costing, sustainability,
// DimXpert, rendering, PDM, the Design Library and macros. Mixed into the App class by installMore().
import * as THREE from "three";
import { el, escapeHtml } from "./propman.js";

const $ = (s) => document.querySelector(s);
const vec = (s) => String(s ?? "").split(/[,\s]+/).filter(Boolean).map(Number);
const vec3 = (s, d = null) => { const v = vec(s); return v.length === 3 && v.every((x) => !isNaN(x)) ? v : d; };
const pts3 = (s) => String(s ?? "").split(/[;\n]+/).map((l) => vec(l)).filter((p) => p.length === 3 && p.every((x) => !isNaN(x)));
const sel = (key, accepts, label, single = false) => ({ key, type: "selection", accepts, label, single });
const num = (key, label, value, step = 1, ic = "depth", extra = {}) => ({ key, type: "number", label, value, step, icon: ic, ...extra });
const txt = (key, label, value = "", extra = {}) => ({ key, type: "text", label, value, ...extra });
const ids = (v) => (v || []).map((i) => i.value);
const one = (v) => v?.[0]?.value ?? null;
const AXIS = { X: [1, 0, 0], Y: [0, 1, 0], Z: [0, 0, 1] };
const DIR = [["Y", "Y (Top)"], ["X", "X (Right)"], ["Z", "Z (Front)"]];
const pull = (v) => AXIS[v.direction || "Y"].map((c) => (v.reverse ? -c : c));
const PLANES = [["Top", "Top Plane"], ["Front", "Front Plane"], ["Right", "Right Plane"]];
const faceItem = (id) => ({ kind: "face", label: id, value: id });
const edgeItem = (id) => ({ kind: "edge", label: id, value: id });
const refItems = (kind, refs) => (refs || []).map((r, i) => ({ kind, label: `${kind[0].toUpperCase()}${kind.slice(1)}<${i + 1}>`, value: r }));

const moldSpec = (cmd, title, ic, extra = [], extraArgs = () => ({})) => ({
  title, icon: ic, command: cmd,
  groups: [{ title: "Mold Parameters", fields: [{ key: "direction", type: "select", options: DIR, value: "Y" },
    { key: "reverse", type: "check", label: "Reverse direction of pull", value: false },
    { key: "use_level", type: "check", label: "Parting level (non-planar parting line)", value: false },
    num("level", "Level along pull", 0, 1, "depth", { showIf: (v) => v.use_level }), ...extra] }],
  args: (v) => ({ direction: pull(v), level: v.use_level ? v.level : null, ...extraArgs(v) }),
  fromParams: (p) => { const d = Array.isArray(p.direction) ? p.direction : AXIS[p.direction] || AXIS.Y; const ax = ["X", "Y", "Z"][d.findIndex((c) => Math.abs(c) > 0.5)] || "Y";
    return { ...p, direction: ax, reverse: d[(["X", "Y", "Z"]).indexOf(ax)] < 0, use_level: p.level != null, level: p.level ?? 0 }; },
  message: "Set the direction of pull: the part's faces split into core and cavity at the parting line.",
});

export const MORE_SPECS = {
  flex: {
    title: "Flex", icon: "revolve", command: "flex",
    groups: [{ title: "Flex Input", fields: [{ key: "type", type: "radio", value: "bend", options: [["bend", "Bending"], ["twist", "Twisting"], ["taper", "Tapering"], ["stretch", "Stretching"]] },
      num("angle", "Angle (°)", 90, 5, "draft", { showIf: (v) => ["bend", "twist"].includes(v.type) }),
      num("factor", "Taper factor (end / start)", 0.5, 0.1, "draft", { showIf: (v) => v.type === "taper" }),
      num("distance", "Stretch distance", 10, 1, "depth", { showIf: (v) => v.type === "stretch" })] },
      { title: "Triad", fields: [{ key: "axis", type: "select", value: "X", options: [["X", "Along X"], ["Y", "Along Y"], ["Z", "Along Z"]] },
        { key: "bend_direction", type: "select", value: "", options: [["", "Bend toward: auto"], ["X", "Bend toward X"], ["Y", "Bend toward Y"], ["Z", "Bend toward Z"]], showIf: (v) => v.type === "bend" }] },
      { title: "Trim Planes", check: "use_trim", value: false, fields: [num("start", "Trim plane 1 (along axis)", -50, 1), num("end", "Trim plane 2 (along axis)", 50, 1)] }],
    args: (v) => ({ type: v.type, axis: v.axis, angle: v.angle, factor: v.factor, distance: v.distance, start: v.use_trim ? v.start : null,
      end: v.use_trim ? v.end : null, bend_direction: v.bend_direction || null }),
    fromParams: (p) => ({ ...p, use_trim: p.start != null || p.end != null, start: p.start ?? -50, end: p.end ?? 50, bend_direction: p.bend_direction || "" }),
    message: "Bend, twist, taper or stretch the body along an axis, optionally between two trim planes.",
  },
  deform: {
    title: "Deform", icon: "dimple", command: "deform",
    groups: [{ title: "Deform Points", fields: [txt("points", "Points x, y, z (one per line, or ; between)", "0, 10, 0"), txt("moves", "Moves dx, dy, dz (one per point)", "0, 5, 0"),
      num("radius", "Deform region radius", 20, 1)] }],
    args: (v) => ({ points: pts3(v.points), moves: pts3(v.moves), radius: v.radius }),
    fromParams: (p) => ({ points: p.points.map((q) => q.point.join(", ")).join("; "), moves: p.points.map((q) => q.move.join(", ")).join("; "), radius: p.radius }),
    message: "Push the body at points by a move each; the change fades out smoothly over the radius.",
  },
  freeform: {
    title: "Freeform", icon: "dimple", command: "freeform",
    groups: [{ title: "Face Settings", fields: [sel("face", ["face"], "Face to modify", true), num("distance", "Push distance (along the normal)", 3, 0.5),
      num("radius", "Region radius (0: whole face)", 0, 1)] }],
    args: (v) => ({ face: one(v.face), distance: v.distance, radius: v.radius > 0 ? v.radius : null }),
    fromParams: (p) => ({ face: refItems("face", [p.face]), distance: p.distance, radius: p.radius || 0 }),
    message: "Select a face and push its middle out (negative: in); its edges stay put.",
    prefill: (s) => ({ face: s.filter((x) => x.kind === "face").slice(0, 1).map((x) => faceItem(x.id)) }),
  },
  fillet_profile: {
    title: "Fillet (Conic / Curvature Continuous)", icon: "fillet", command: "fillet_profile",
    groups: [{ title: "Items To Fillet", fields: [sel("edges", ["edge"], "Edges (between flat faces)"), num("radius", "Radius", 5, 0.5, "fillet")] },
      { title: "Fillet Parameters", fields: [{ key: "profile", type: "select", value: "conic", options: [["conic", "Conic Rho"], ["curvature_continuous", "Curvature Continuous"]] },
        num("rho", "Conic rho (0.5: parabola)", 0.5, 0.05, "draft", { showIf: (v) => v.profile === "conic", notLength: true })] }],
    args: (v) => ({ edges: ids(v.edges), radius: v.radius, profile: v.profile, rho: v.rho }),
    fromParams: (p) => ({ ...p, edges: refItems("edge", p.edges) }),
    message: "Select straight edges between flat faces; pick the profile.",
    prefill: (s) => ({ edges: s.filter((x) => x.kind === "edge").map((x) => edgeItem(x.id)) }),
  },
  fillet_xpert: {
    title: "FilletXpert", icon: "fillet", command: "fillet_xpert", noPreview: true,
    groups: [{ title: "Add", fields: [sel("edges", ["edge"], "Edges"), num("radius", "Radius", 2, 0.5, "fillet")] }],
    args: (v) => ({ edges: ids(v.edges), radius: v.radius }),
    message: "Select the edges: FilletXpert finds an order that works and adds the fillets.",
    prefill: (s) => ({ edges: s.filter((x) => x.kind === "edge").map((x) => edgeItem(x.id)) }),
  },
  parting_line: moldSpec("parting_line", "Parting Line", "intersection_curve"),
  shut_off_surfaces: moldSpec("shut_off_surfaces", "Shut-Off Surfaces", "planar_surface"),
  parting_surface: moldSpec("parting_surface", "Parting Surface", "surface", [num("margin", "Distance (beyond the part)", 10, 1)], (v) => ({ margin: v.margin })),
  tooling_split: moldSpec("tooling_split", "Tooling Split", "cut_extrude", [num("margin", "Block margin", 10, 1),
    { key: "keep_part", type: "check", label: "Keep the part body", value: true }], (v) => ({ margin: v.margin, keep_part: !!v.keep_part })),
  insert_part: {
    title: "Insert Part", icon: "import", command: "insert_part", noPreview: true, // (previews never read files)
    groups: [{ title: "Part", fields: [txt("path", "File (.oswprt, .step, .x_t, .sldprt)", "", { browse: ["openswx / STEP / Parasolid / SOLIDWORKS part (*.oswprt;*.step;*.stp;*.x_t;*.x_b;*.sldprt)"] })] },
      { title: "Locate Part", fields: [txt("position", "Position x, y, z", "0, 0, 0"), txt("rotation", "Rotation x, y, z (°)", "0, 0, 0")] },
      { title: "Options", fields: [{ key: "op", type: "select", value: "add", options: [["add", "Add (merge)"], ["cut", "Cut from the body"]] },
        { key: "merge", type: "check", label: "Merge result", value: true }] }],
    args: (v) => ({ path: v.path, position: vec3(v.position, [0, 0, 0]), rotation_deg: vec3(v.rotation, [0, 0, 0]), op: v.op, merge: !!v.merge }),
    fromParams: (p) => ({ path: p.path, position: (p.position || [0, 0, 0]).join(", "), rotation: (p.rotation || [0, 0, 0]).join(", "), op: p.op || "add", merge: p.merge ?? true }),
    message: "Pick a part file to bring in as a body (it updates when the file changes).",
  },
  mirror_part: {
    title: "Mirror Part", icon: "mirror", command: "mirror_part", noPreview: true, // (previews never read files)
    groups: [{ title: "Part", fields: [txt("path", "File to mirror", "", { browse: ["openswx / STEP / Parasolid / SOLIDWORKS part (*.oswprt;*.step;*.stp;*.x_t;*.x_b;*.sldprt)"] }),
      { key: "plane", type: "select", value: "Right", options: [["Right", "Right Plane (YZ)"], ["Front", "Front Plane (XY)"], ["Top", "Top Plane (XZ)"]] },
      txt("position", "Position x, y, z", "0, 0, 0")] }],
    args: (v) => ({ path: v.path, plane: v.plane, position: vec3(v.position, [0, 0, 0]) }),
    message: "A mirrored (opposite-hand) copy of a part file, linked to it.",
  },
  cosmetic_thread: {
    title: "Cosmetic Thread", icon: "thread", command: "cosmetic_thread",
    groups: [{ title: "Thread Settings", fields: [sel("face", ["face"], "Cylindrical face (hole or shaft)", true), txt("label", "Callout (blank: from the size)", ""),
      num("length", "Length (0: the face's length)", 0, 1)] }],
    args: (v) => ({ face: one(v.face), label: v.label || null, length: v.length > 0 ? v.length : null }),
    fromParams: (p) => ({ face: refItems("face", [p.face]), label: p.label || "", length: p.length || 0 }),
    message: "Select a cylindrical face: the thread is shown (dashed) and called out in drawings, not modelled.",
    prefill: (s) => ({ face: s.filter((x) => x.kind === "face").slice(0, 1).map((x) => faceItem(x.id)) }),
  },
  ruled_surface: {
    title: "Ruled Surface", icon: "surface", command: "ruled_surface",
    groups: [{ title: "Type", fields: [{ key: "type", type: "radio", value: "normal", options: [["normal", "Normal to Surface"], ["tangent", "Tangent to Surface"], ["direction", "Sweep (direction)"]] },
      { key: "direction", type: "select", value: "Y", options: DIR, showIf: (v) => v.type === "direction" }] },
      { title: "Distance / Direction", fields: [num("distance", "Distance", 10, 1)] }, { title: "Edge Selection", fields: [sel("edges", ["edge"], "Edges")] }],
    args: (v) => ({ edges: ids(v.edges), distance: v.distance, type: v.type, direction: v.direction }),
    fromParams: (p) => ({ ...p, edges: refItems("edge", p.edges) }),
    message: "Select model edges: a band of surface grows from them.",
    prefill: (s) => ({ edges: s.filter((x) => x.kind === "edge").map((x) => edgeItem(x.id)) }),
  },
  radiate_surface: {
    title: "Radiate Surface", icon: "surface", command: "radiate_surface",
    groups: [{ title: "Radiate Parameters", fields: [{ key: "plane", type: "select", value: "Top", options: PLANES }, sel("edges", ["edge"], "Edges to radiate"), num("distance", "Distance", 10, 1)] }],
    args: (v) => ({ edges: ids(v.edges), plane: v.plane, distance: v.distance }),
    fromParams: (p) => ({ ...p, edges: refItems("edge", p.edges) }),
    message: "Select edges; the surface radiates outward parallel to the plane.",
    prefill: (s) => ({ edges: s.filter((x) => x.kind === "edge").map((x) => edgeItem(x.id)) }),
  },
  extend_surface: {
    title: "Extend Surface", icon: "offset_surface", command: "extend_surface",
    groups: [{ title: "Surface", fields: [sel("surface", ["feature"], "Surface body (tree)", true), sel("faces", ["face"], "…or model faces")] },
      { title: "End Condition", fields: [num("distance", "Distance", 10, 1)] }],
    args: (v) => ({ distance: v.distance, surface: one(v.surface), faces: ids(v.faces) }),
    message: "Select a surface body (or faces) and the distance to extend it past its edges.",
  },
  untrim_surface: {
    title: "Untrim Surface", icon: "trim_surface", command: "untrim_surface",
    groups: [{ title: "Selections", fields: [sel("surface", ["feature"], "Surface body (tree)", true), sel("faces", ["face"], "…or model faces"),
      { key: "mode", type: "radio", value: "internal", options: [["internal", "Fill internal holes"], ["natural", "Extend to natural boundary"]] }] }],
    args: (v) => ({ surface: one(v.surface), faces: ids(v.faces), mode: v.mode }),
    message: "Select a trimmed surface: fill its holes or restore its natural boundary.",
  },
  flatten_surface: {
    title: "Surface Flatten", icon: "flatten", command: "flatten_surface",
    groups: [{ title: "Selections", fields: [sel("surface", ["feature"], "Surface body (tree)", true), sel("faces", ["face"], "…or developable faces"),
      { key: "plane", type: "select", value: "Top", options: PLANES }] }],
    args: (v) => ({ surface: one(v.surface), faces: ids(v.faces), plane: v.plane }),
    message: "Select developable faces (flat, cylindrical, conical): they unroll onto the plane keeping their area.",
    prefill: (s) => ({ faces: s.filter((x) => x.kind === "face").map((x) => faceItem(x.id)) }),
  },
  filled_surface_edges: {
    title: "Filled Surface (edges)", icon: "filled_surface", command: "filled_surface_edges",
    groups: [{ title: "Patch Boundary", fields: [sel("edges", ["edge"], "Boundary edges (closed loop)"),
      { key: "continuity", type: "select", value: "tangent", options: [["tangent", "Tangent"], ["contact", "Contact"]] }] },
      { title: "Constraint Curves", fields: [txt("points", "Pass through points x, y, z (optional, ; between)", "")] }],
    args: (v) => ({ edges: ids(v.edges), continuity: v.continuity, points: pts3(v.points).length ? pts3(v.points) : null }),
    message: "Select the edges around an opening: the patch fills it, tangent to the faces around it.",
    prefill: (s) => ({ edges: s.filter((x) => x.kind === "edge").map((x) => edgeItem(x.id)) }),
  },
  connection_point: {
    title: "Connection Point", icon: "ref_point", command: "connection_point",
    groups: [{ title: "Selections", fields: [txt("point", "Point x, y, z", "0, 0, 0"), { key: "direction", type: "select", value: "Y", options: DIR },
      { key: "reverse", type: "check", label: "Reverse direction", value: false }, txt("name", "Name (blank: automatic)", "")] }],
    args: (v) => ({ point: vec3(v.point, [0, 0, 0]), direction: pull(v), name: v.name || null }),
    message: "A connection point for Magnetic Mates: where the part connects and the direction it faces.",
  },
  mate_reference: {
    title: "Mate Reference", icon: "mate", command: "mate_reference",
    groups: [{ title: "Primary Reference Entity", fields: [sel("face", ["face"], "Face", true),
      { key: "type", type: "select", value: "coincident", options: [["coincident", "Coincident"], ["concentric", "Concentric"], ["tangent", "Tangent"], ["parallel", "Parallel"]] },
      { key: "aligned", type: "check", label: "Aligned", value: false }, txt("reference", "Name (blank: automatic)", "")] }],
    args: (v) => ({ face: one(v.face), type: v.type, aligned: !!v.aligned, reference: v.reference || null }),
    message: "Pick the face this part mates by: inserting it into an assembly mates it automatically.",
    prefill: (s) => ({ face: s.filter((x) => x.kind === "face").slice(0, 1).map((x) => faceItem(x.id)) }),
  },
  add_decal: {
    title: "Decal", icon: "appearance", command: "add_decal", noPreview: true,
    groups: [{ title: "Image", fields: [{ key: "image", type: "file", label: "Image (PNG / JPEG)", accept: "image/png,image/jpeg" }] },
      { title: "Mapping", fields: [sel("face", ["face"], "Face", true), num("width", "Width", 20, 1), num("rotation", "Rotation (°)", 0, 15, "draft")] }],
    args: (v) => ({ face: one(v.face), image: v.image || "", width: v.width, rotation: v.rotation }),
    message: "Choose an image and the face to put it on.",
    prefill: (s) => ({ face: s.filter((x) => x.kind === "face").slice(0, 1).map((x) => faceItem(x.id)) }),
  },
  weld_bead: {
    title: "Weld Bead", icon: "weldment", command: "weld_bead", noPreview: true,
    groups: [{ title: "Weld Path", fields: [sel("faces", ["face"], "Two faces to join (set 1, set 2)"), num("size", "Bead size (leg)", 4, 0.5)] }],
    args: (v) => ({ face1: v.faces?.[0]?.value, face2: v.faces?.[1]?.value, size: v.size }),
    message: "Select the two faces meeting at the joint (of the same or two components).",
    prefill: (s) => ({ faces: s.filter((x) => x.kind === "face").slice(0, 2).map((x) => faceItem(x.id)) }),
  },
  smart_mate: {
    title: "SmartMates", icon: "mate", command: "smart_mate", noPreview: true,
    groups: [{ title: "Entities", fields: [sel("ents", ["face", "edge"], "Two faces / edges (of two components)")] }],
    args: (v) => ({ entity1: v.ents?.[0]?.value, entity2: v.ents?.[1]?.value }),
    message: "Pick an entity on each component: the mate type follows from the geometry (flat-flat coincident, round-round concentric...).",
    prefill: (s) => ({ ents: s.filter((x) => x.kind === "face" || x.kind === "edge").slice(0, 2).map((x) => ({ kind: x.kind, label: x.id, value: x.id })) }),
  },
};
export const MORE_FEATURE_TO_SPEC = { flex: "flex", deform: "deform", freeform: "freeform", profile_fillet: "fillet_profile",
  parting_line: "parting_line", parting_surface: "parting_surface", shut_off_surfaces: "shut_off_surfaces", tooling_split: "tooling_split",
  insert_part: "insert_part", cosmetic_thread: "cosmetic_thread", ruled_surface: "ruled_surface", radiate_surface: "radiate_surface" };
export const MORE_TYPE_ICON = { flex: "revolve", deform: "dimple", freeform: "dimple", profile_fillet: "fillet", parting_line: "intersection_curve",
  parting_surface: "surface", shut_off_surfaces: "planar_surface", tooling_split: "cut_extrude", insert_part: "import", cosmetic_thread: "thread",
  ruled_surface: "surface", radiate_surface: "surface", extend_surface: "offset_surface", untrim_surface: "trim_surface", flatten_surface: "flatten",
  connection_point: "ref_point", mate_reference: "mate" };

export function menuTree(items) {
  const root = [];
  const find = (list, label) => list.find((n) => n.children && n.label === label);
  for (const it of items) {
    if (it === "-") { root.push("-"); continue; }
    const path = it[0].split(" > ");
    let list = root;
    for (const part of path.slice(0, -1)) {
      let node = find(list, part);
      if (!node) { node = { label: part, children: [] }; list.push(node); }
      list = node.children;
    }
    list.push({ label: path[path.length - 1], key: it[1], fn: it[2] });
  }
  const tidy = (list) => { // no leading, trailing or doubled separators
    const out = [];
    for (const n of list) { if (n === "-" && (!out.length || out[out.length - 1] === "-")) continue; out.push(n.children ? { ...n, children: tidy(n.children) } : n); }
    while (out[out.length - 1] === "-") out.pop();
    return out;
  };
  return tidy(root);
}

export function renderMenu(drop, nodes, onPick) {
  drop.innerHTML = "";
  for (const n of nodes) {
    if (n === "-") { drop.append(el("div", "menu-sep")); continue; }
    if (n.children) {
      const row = el("div", "menu-item has-sub", `<span>${escapeHtml(n.label)}</span><span class="key">▸</span>`);
      const sub = el("div", "menu-drop menu-sub"); renderMenu(sub, n.children, onPick); row.append(sub);
      row.onmouseenter = () => { // open beside the row; flip to the left (or shift up) near the window's edges
        sub.classList.remove("left"); sub.style.top = "";
        requestAnimationFrame(() => {
          const r = sub.getBoundingClientRect();
          if (r.right > innerWidth - 4) sub.classList.add("left");
          if (r.bottom > innerHeight - 4) sub.style.top = `${Math.max(-row.getBoundingClientRect().top + 4, innerHeight - 4 - r.bottom - 4)}px`;
        });
      };
      row.onclick = (e) => e.stopPropagation();
      drop.append(row); continue;
    }
    const row = el("div", "menu-item", `<span>${escapeHtml(n.label)}</span><span class="key">${n.key || ""}</span>`);
    row.onclick = (e) => { e.stopPropagation(); onPick(n.fn); }; drop.append(row);
  }
}

export function lineChart(series, { xlabel = "", ylabel = "", w = 380, h = 190 } = {}) {
  const all = series.flatMap((s) => s.points); if (!all.length) return "";
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(0, ...ys), y1 = Math.max(...ys);
  const L = 52, B = 28, T = 10, R = 10;
  const X = (x) => L + (w - L - R) * (x - x0) / ((x1 - x0) || 1), Y = (y) => h - B - (h - B - T) * (y - y0) / ((y1 - y0) || 1);
  const fmt = (v) => Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-2 && v !== 0) ? v.toExponential(1) : +v.toPrecision(3);
  const ticks = [0, 0.5, 1].map((f) => `<text x="${L - 4}" y="${Y(y0 + f * (y1 - y0)) + 3}" text-anchor="end">${fmt(y0 + f * (y1 - y0))}</text>` +
    `<text x="${X(x0 + f * (x1 - x0))}" y="${h - B + 13}" text-anchor="middle">${fmt(x0 + f * (x1 - x0))}</text>`).join("");
  const lines = series.map((s) => `<polyline fill="none" stroke="${s.color || "#2563eb"}" stroke-width="1.6" points="${s.points.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ")}"/>`).join("");
  return `<svg class="time-chart" viewBox="0 0 ${w} ${h}" width="100%" style="font:10px sans-serif;margin-top:8px">
    <rect x="${L}" y="${T}" width="${w - L - R}" height="${h - B - T}" fill="none" stroke="#999"/>${ticks}${lines}
    <text x="${(w + L) / 2}" y="${h - 2}" text-anchor="middle">${escapeHtml(xlabel)}</text>
    <text x="12" y="${(h - B) / 2}" text-anchor="middle" transform="rotate(-90 12 ${(h - B) / 2})">${escapeHtml(ylabel)}</text></svg>`;
}

const kvNum = (v) => v !== 0 && Math.abs(v) < 1e-3 ? v.toExponential(3) : (+v.toPrecision(5)).toLocaleString();  // (small forces would show as 0)
const kv = (rows) => `<div class="kv">${rows.filter((r) => r && r[1] !== undefined && r[1] !== null).map(([k, v]) => `<div>${escapeHtml(k)}</div><div>${typeof v === "number" ? kvNum(v) : escapeHtml(String(v))}</div>`).join("")}</div>`;
const rgbRamp = (t) => { t = Math.max(0, Math.min(1, t)); return t < 0.25 ? [0, 4 * t, 1] : t < 0.5 ? [0, 1, 2 - 4 * t] : t < 0.75 ? [4 * t - 2, 1, 0] : [1, 4 - 4 * t, 0]; };
const css = (c) => `rgb(${c.map((x) => Math.round(x * 255)).join(",")})`;

const SCENES = {
  studio: { bg: 0xeef0f3, walls: 0xc9cdd3, lights: 0xffffff, floor: 0xdcdfe4, sky: null },
  warehouse: { bg: 0x9a8f80, walls: 0xa08a6c, lights: 0xffe2b8, floor: 0x8d8478, sky: null },
  outdoor: { bg: 0xa9cbea, walls: 0xa3b08e, lights: 0xfff6e0, floor: 0xb9b3a6, sky: [0x6aa7e8, 0xdfeaf5] },
  plain_white: { bg: 0xffffff, walls: 0xffffff, lights: 0xffffff, floor: 0xffffff, sky: null },
  dark: { bg: 0x16181d, walls: 0x202329, lights: 0xdde6ff, floor: 0x22252b, sky: null },
};

function envScene(name) {
  const S = SCENES[name] || SCENES.studio, scene = new THREE.Scene();
  const room = new THREE.Mesh(new THREE.BoxGeometry(20, 12, 20), new THREE.MeshBasicMaterial({ color: S.walls, side: THREE.BackSide }));
  room.position.y = 4; scene.add(room);
  if (S.sky) { // gradient sky dome
    const g = new THREE.SphereGeometry(9, 32, 16), c = [], p = g.getAttribute("position"), a = new THREE.Color(S.sky[0]), b = new THREE.Color(S.sky[1]);
    for (let i = 0; i < p.count; i++) { const t = Math.max(0, p.getY(i) / 9); const k = b.clone().lerp(a, t); c.push(k.r, k.g, k.b); }
    g.setAttribute("color", new THREE.Float32BufferAttribute(c, 3));
    scene.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.BackSide })));
  }
  for (const [x, y, z, sx, sz, k] of [[0, 9.5, 0, 8, 8, 6], [-9.5, 5, 3, 0, 6, 4], [9.5, 5, -3, 0, 6, 3]]) { // softboxes
    const m = new THREE.Mesh(new THREE.BoxGeometry(sx || 0.1, sx ? 0.1 : 3, sz), new THREE.MeshBasicMaterial({ color: new THREE.Color(S.lights).multiplyScalar(k) }));
    m.position.set(x, y, z); scene.add(m);
  }
  return scene;
}

function pbrFor(hex, mat) {
  const cat = String(mat?.category || "").toLowerCase(), metal = /steel|iron|alumin|copper|titanium/.test(cat);
  return new THREE.MeshStandardMaterial({ color: hex, metalness: metal ? 0.85 : 0.0, roughness: metal ? 0.32 : /rubber/.test(cat) ? 0.9 : 0.45,
    side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
}

const MIXIN = {
  /** fields: [key, label, type ("number"|"text"|"vec"|"list"|"points"|"select"|"check"), default, options] */
  cmdForm(title, cmd, fields, { ic = "properties", fixed = {}, note = "", xf = (x) => x } = {}) {
    const id = (k) => `cf_${k}`;
    const html = (note ? `<p class="muted">${escapeHtml(note)}</p>` : "") + fields.map(([k, label, type, def, opts]) => {
      if (type === "check") return `<label class="pm-check"><input id="${id(k)}" type="checkbox" ${def ? "checked" : ""}> ${escapeHtml(label)}</label>`;
      if (type === "select") return `<div class="field"><label>${escapeHtml(label)}</label><select id="${id(k)}">${opts.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return `<option value="${escapeHtml(v)}" ${v === def ? "selected" : ""}>${escapeHtml(l)}</option>`; }).join("")}</select></div>`;
      const t = type === "number" ? `type="number" step="any"` : "";
      return `<div class="field"><label>${escapeHtml(label)}</label><input id="${id(k)}" ${t} value="${escapeHtml(def ?? "")}" style="width:100%"></div>`;
    }).join("");
    const read = () => {
      const out = { ...fixed };
      for (const [k, , type] of fields) {
        const e = document.getElementById(id(k)); if (!e) continue;
        if (type === "check") out[k] = e.checked;
        else if (type === "number") out[k] = e.value === "" ? null : parseFloat(e.value);
        else if (type === "vec") out[k] = vec3(e.value);
        else if (type === "list") out[k] = vec(e.value);
        else if (type === "points") out[k] = pts3(e.value);
        else out[k] = e.value === "" ? null : e.value;
      }
      return out;
    };
    let done;
    const p = new Promise((resolve) => { done = resolve; });
    // resolve after the modal has closed, so a follow-up (results) modal is not closed with it
    this.modal(title, html, async () => { const res = await this.run(cmd, xf(read())); setTimeout(() => done(res.result)); }, ic);
    const cancel = document.querySelector("#modal-box .mb-foot .btn:not(.primary)");
    if (cancel) cancel.addEventListener("click", () => done(null));
    return p;
  },

  showResult(title, r, extra = "", ic = "properties", onOK = null) {
    const rows = Object.entries(r || {}).filter(([, v]) => typeof v !== "object" || v === null).map(([k, v]) => [k.replace(/_/g, " "), typeof v === "boolean" ? (v ? "yes" : "no") : v]);
    this.modal(title, kv(rows) + extra, onOK, ic);
    if (onOK) { const ok = document.querySelector("#modal-box .mb-foot .primary"); if (ok) ok.textContent = "Show Plot"; }
  },

  selectedIds(kind) { return (this.selection || []).filter((s) => s.kind === kind).map((s) => s.id); },

  async nonlinearDialog() {
    const r = await this.cmdForm("Nonlinear Static Study", "nonlinear_run", [["steps", "Load steps", "number", 10], ["hardening", "Hardening (tangent modulus / E)", "number", 0.05],
      ["element_size", "Element size (mm, blank: auto)", "number", ""]], { ic: "sim_run", note: "Elastic-plastic material (bilinear) with the study's fixtures and loads." });
    if (!r) return;
    const chart = lineChart([{ points: [[0, 0], ...r.curve.map((c) => [c.max_displacement, c.load_factor])] }], { xlabel: "max displacement (mm)", ylabel: "load factor" });
    this.showResult("Nonlinear Study - Results", r, chart, "sim_run", () => this.simPlot("stress"));
  },

  async harmonicDialog() {
    const r = await this.cmdForm("Dynamic Study - Harmonic", "harmonic_run", [["f_min", "From (Hz)", "number", 0], ["f_max", "To (Hz)", "number", 2000],
      ["points", "Points", "number", 200], ["damping", "Modal damping ratio", "number", 0.02], ["modes", "Modes", "number", 10]], { ic: "sim_frequency", note: "The study's loads applied as F·sin(2πft)." });
    if (r) this.showResult("Harmonic Response", { peak_frequency_hz: r.peak_frequency, peak_displacement_mm: r.peak_displacement, damping: r.damping },
      lineChart([{ points: r.response.map((p) => [p.frequency, p.max_displacement]) }], { xlabel: "frequency (Hz)", ylabel: "amplitude (mm)" }), "sim_frequency");
  },

  async transientDialog() {
    const r = await this.cmdForm("Dynamic Study - Transient", "step_response_run", [["duration", "Duration (s)", "number", 0.05], ["points", "Points", "number", 400],
      ["damping", "Modal damping ratio", "number", 0.02], ["modes", "Modes", "number", 10]], { ic: "sim_frequency", note: "The study's loads applied suddenly at t = 0 and held." });
    if (r) this.showResult("Transient Response", { static_displacement_mm: r.static_displacement, peak_displacement_mm: r.peak_displacement, dynamic_amplification: r.dynamic_amplification },
      lineChart([{ points: r.history.map((p) => [p.time, p.displacement]) }], { xlabel: "time (s)", ylabel: "displacement (mm)" }), "sim_frequency");
  },

  async dropTestDialog() {
    const faces = this.selectedIds("face");
    if (!faces.length) return this.toast("Select the face(s) the part lands on, then run Drop Test.", "err");
    const r = await this.cmdForm("Drop Test", "drop_test_run", [["height", "Drop height (mm)", "number", 1000],
      ["direction", "Gravity direction x, y, z", "vec", "0, -1, 0"], ["element_size", "Element size (mm, blank: auto)", "number", ""]],
    { ic: "sim_run", fixed: { impact_faces: faces }, note: `Lands on ${faces.join(", ")} (rigid floor; energy method).` });
    if (r) this.showResult("Drop Test - Results", r, "", "sim_run", () => this.simPlot("stress"));
  },

  async fatigueDialog() {
    const r = await this.cmdForm("Fatigue", "fatigue_run", [["cycles", "Number of cycles", "number", 1e6], ["loading", "Loading", "select", "reversed", [["reversed", "Fully reversed (R = -1)"], ["zero_based", "Zero based (R = 0)"]]],
      ["scale", "Load scale factor", "number", 1]], { ic: "sim_run", note: "Uses the last static study's stress (run it first)." });
    if (r) this.showResult("Fatigue - Results", r, "", "sim_run", () => this.fatiguePlot());
  },

  async fatiguePlot() {
    const r = await this.evalCall("fatigue_results"); if (!r) return;
    this.vp.setDeformation(null); this.vp.setFaceGeometry(r.faces);
    const all = Object.values(r.log_life).flat(), lo = Math.min(...all), hi = Math.max(...all);
    const col = (x) => rgbRamp(1 - (x - lo) / ((hi - lo) || 1)); // short life red
    this.vp.setAnalysis(Object.fromEntries(Object.entries(r.log_life).map(([id, v]) => [id, v.map(col)])));
    this.vp.setLegend({ title: "Life (log10 cycles)", unit: "12 = infinite", min: lo, max: hi, color: (t) => css(rgbRamp(1 - t)) });
  },

  flowDialog() {
    const kinds = [["external", "External flow - Navier-Stokes (drag, lift, wake)"], ["internal", "Internal flow - Navier-Stokes (pressure drop through a passage)"],
      ["potential", "External flow - ideal (potential) flow, quick first look"]];
    this.modal("Flow Simulation", `<p class="muted">Study type</p>${kinds.map(([v, l], i) => `<label class="pm-check"><input type="radio" name="fsk" value="${v}" ${i ? "" : "checked"}> ${escapeHtml(l)}</label>`).join("")}`,
      () => { const k = document.querySelector("input[name=fsk]:checked")?.value; setTimeout(() => this.flowStudy(k)); }, "sim_run");
  },

  async flowStudy(kind) {
    if (kind === "potential") {
      const r = await this.cmdForm("Flow Simulation - Potential Flow", "flow_simulation", [["speed", "Free-stream speed (m/s)", "number", 10], ["direction", "Flow direction x, y, z", "vec", "1, 0, 0"],
        ["fluid", "Fluid", "select", "air", [["air", "Air"], ["water", "Water"]]], ["cells_across", "Cells across the part", "number", 20]],
      { ic: "sim_run", note: "Ideal flow: pressure coefficient and velocity on the surface, skin-friction drag estimate. No separation, wake or pressure drag." });
      if (r) this.showResult("Flow Simulation - Results", r, "", "sim_run", () => this.flowPlot());
      return;
    }
    const ext = kind === "external";
    const fields = ext
      ? [["speed", "Free-stream speed (m/s)", "number", 10], ["direction", "Flow direction x, y, z", "vec", "1, 0, 0"], ["fluid", "Fluid", "select", "air", [["air", "Air"], ["water", "Water"]]]]
      : [["axis", "Flow along (in at the low end)", "select", "X", ["X", "Y", "Z"]], ["speed", "Mean inlet velocity (m/s)", "number", 1], ["fluid", "Fluid", "select", "water", [["water", "Water"], ["air", "Air"]]]];
    fields.push(["cells_across", ext ? "Cells across the part (accuracy vs time)" : "Cells across the inlet", "number", 16], ["max_seconds", "Time limit (s)", "number", 300]);
    const r = await this.cmdForm(ext ? "Flow Simulation - External" : "Flow Simulation - Internal", ext ? "cfd_external" : "cfd_internal", fields,
      { ic: "sim_run", note: "Lattice Boltzmann Navier-Stokes solver on a voxel grid: separation and wakes included. Runs up to the time limit." });
    if (!r) return;
    const shown = ext ? { reynolds: r.reynolds, drag_N: r.drag_N, drag_coefficient: r.drag_coefficient, lift_side_force_N: r.lift_side_force_N, frontal_area_mm2: r.frontal_area_mm2, min_cp: r.min_cp, max_cp: r.max_cp, max_velocity: r.max_velocity }
      : { reynolds: r.reynolds, pressure_drop_Pa: r.pressure_drop_Pa, flow_rate_l_min: r.flow_rate_l_min, inlet_area_mm2: r.inlet_area_mm2, max_velocity: r.max_velocity };
    Object.assign(shown, { method: r.method, cells: r.cells, steps: r.steps, converged: r.converged });
    const warn = r.warning ? `<p class="muted">⚠ ${escapeHtml(r.warning)}</p>` : "";
    this.showResult("Flow Simulation - Results", shown, warn + (r.history?.length ? lineChart([{ points: r.history.map((v, i) => [i * 25, v]) }], { xlabel: "step", ylabel: ext ? "drag coefficient" : "pressure drop (Pa)" }) : ""),
      "sim_run", () => this.cfdPlot());
  },

  /** Surface pressure (external flow) on the model and the mid-plane speed slice in a window. */
  async cfdPlot() {
    const r = await this.evalCall("cfd_results"); if (!r) return;
    if (r.cp) {
      this.vp.setDeformation(null); this.vp.setFaceGeometry(null);
      const all = Object.values(r.cp).flat(), lo = Math.min(...all), hi = Math.max(...all);
      this.vp.setAnalysis(Object.fromEntries(Object.entries(r.cp).map(([id, v]) => [id, v.map((x) => rgbRamp((x - lo) / ((hi - lo) || 1)))])));
      this.vp.setLegend({ title: "Pressure coefficient Cp", unit: `U = ${r.speed} m/s, q = ${r.dynamic_pressure_Pa.toFixed(1)} Pa`, min: lo, max: hi, color: (t) => css(rgbRamp(t)) });
    }
    const s = r.slice, rows = s.speed, H = rows.length, Wd = rows[0].length;
    const vals = rows.flat().filter((v) => v !== null && !isNaN(v)), mx = Math.max(...vals) || 1;
    const cv = document.createElement("canvas"); cv.width = Wd; cv.height = H;
    const g = cv.getContext("2d"), img = g.createImageData(Wd, H);
    rows.forEach((row, j) => row.forEach((v, i) => {
      const k = ((H - 1 - j) * Wd + i) * 4, c = v === null || isNaN(v) ? [90, 90, 90] : rgbRamp(v / mx).map((x) => Math.round(x * 255));
      img.data[k] = c[0]; img.data[k + 1] = c[1]; img.data[k + 2] = c[2]; img.data[k + 3] = 255;
    }));
    g.putImageData(img, 0, 0);
    // (opened after the results box, which closes once this returns)
    setTimeout(() => this.modal("Flow - Speed on the Mid Plane", `<img src="${cv.toDataURL()}" style="width:100%;image-rendering:pixelated;border:1px solid var(--border)">
      <p class="muted">Speed 0 (blue) to ${mx.toPrecision(3)} m/s (red); grey: the part. Flow from left to right.</p>`, null, "sim_run"));
  },

  async flowPlot() {
    const r = await this.evalCall("flow_results"); if (!r) return;
    this.vp.setDeformation(null); this.vp.setFaceGeometry(null);
    const all = Object.values(r.cp).flat(), lo = Math.min(...all), hi = Math.max(...all);
    this.vp.setAnalysis(Object.fromEntries(Object.entries(r.cp).map(([id, v]) => [id, v.map((x) => rgbRamp((x - lo) / ((hi - lo) || 1)))])));
    this.vp.setLegend({ title: "Pressure coefficient Cp", unit: `U = ${r.speed} m/s, q = ${r.dynamic_pressure_Pa.toFixed(1)} Pa`, min: lo, max: hi, color: (t) => css(rgbRamp(t)) });
  },

  async costingDialog() {
    const r = await this.cmdForm("Costing", "costing", [["method", "Method", "select", this.model?.sheet_metal ? "sheet_metal" : "machining",
      [["machining", "Machining"], ["sheet_metal", "Sheet metal"], ["additive", "3D printing"], ["casting", "Casting"]]], ["quantity", "Quantity", "number", 1],
    ["rate_per_hour", "Shop rate (USD / h)", "number", 80], ["setup_hours", "Setup (h)", "number", 1], ["price_per_kg", "Material price (USD / kg, blank: generic)", "number", ""]], { ic: "mass" });
    if (!r) return;
    const tbl = `<table class="dw-bom" style="margin-top:8px"><tr><th>Item</th><th>USD</th></tr>${r.breakdown.map((b) => `<tr><td>${escapeHtml(b.item)}</td><td style="text-align:right">${b.cost.toFixed(2)}</td></tr>`).join("")}
      <tr><td><b>Cost each</b></td><td style="text-align:right"><b>${r.cost_each.toFixed(2)}</b></td></tr><tr><td>Batch of ${r.quantity}</td><td style="text-align:right">${r.cost_batch.toFixed(2)}</td></tr></table>
      <p class="muted">Generic rates: an estimate to compare designs, not a quote.</p>`;
    this.showResult("Costing", { method: r.method, material: r.material || "-", mass_kg: r.mass_kg }, tbl, "mass");
  },

  async sustainabilityDialog() {
    const r = await this.cmdForm("Sustainability", "sustainability_report", [["region", "Manufacturing region", "select", "Oceania", ["Asia", "Europe", "North America", "Oceania", "South America"]],
      ["process", "Process", "select", "machining", [["machining", "Machining"], ["casting", "Casting"], ["sheet_metal", "Sheet metal"], ["additive", "3D printing"]]],
      ["transport_km", "Transport distance (km)", "number", 1000], ["recycled_fraction", "Recycled content (0-1)", "number", 0]], { ic: "material" });
    if (!r) return;
    const P = Object.entries(r.phases), keys = [["co2", "Carbon (kg CO₂e)"], ["energy", "Energy (MJ)"], ["water", "Water (L)"]];
    const colors = { material: "#2563eb", manufacturing: "#16a34a", transportation: "#f59e0b", end_of_life: "#9333ea" };
    const bars = keys.map(([k, lab]) => {
      const tot = P.reduce((a, [, v]) => a + Math.max(0, v[k]), 0) || 1;
      let x = 0;
      const segs = P.map(([n, v]) => { const w = Math.max(0, v[k]) / tot * 100; const s = `<rect x="${x}%" width="${w}%" height="14" fill="${colors[n]}"><title>${n}: ${v[k].toFixed(3)}</title></rect>`; x += w; return s; }).join("");
      return `<div style="margin:6px 0"><div>${lab}: <b>${r.total[k].toFixed(3)}</b></div><svg width="100%" height="14">${segs}</svg></div>`;
    }).join("");
    const legend = Object.entries(colors).map(([n, c]) => `<span style="color:${c}">■</span> ${n.replace(/_/g, " ")}`).join(" &nbsp; ");
    this.showResult("Sustainability", { material: r.material || "-", mass_kg: r.mass_kg, region: r.region }, bars + `<p class="muted">${legend}<br>Generic published figures for the material class; end of life credits recycling.</p>`, "material");
  },

  async dimxpertDialog() {
    const r = await this.cmdForm("DimXpert - Auto Dimension Scheme", "dimxpert_auto", [["general_tolerance", "General tolerance (±mm)", "number", 0.1],
      ["position_tolerance", "Position tolerance (⌀mm)", "number", 0.1]], { ic: "gdt", note: "Datums, sizes and hole patterns with tolerances, as 3D annotations." });
    if (r) { this.showPMI = true; this.updatePMI(); }
  },

  togglePMI() { this.showPMI = this.showPMI === false; this.updatePMI(); },

  updatePMI() {
    const host = this.vp.canvas.parentElement;
    host.querySelectorAll(".pmi-label").forEach((e) => e.remove());
    this.vp.frameHooks = this.vp.frameHooks.filter((f) => !f.pmi);
    const pmi = this.showPMI === false || this.model?.kind === "assembly" ? [] : (this.model?.pmi || []);
    if (!pmi.length) return;
    const labels = pmi.map((p) => {
      const d = el("div", `pmi-label ${p.type}`, escapeHtml(p.type === "datum" ? p.label : p.text) + (p.position ? `<small>${escapeHtml(p.position)}</small>` : ""));
      host.append(d); return [d, new THREE.Vector3(...p.anchor)];
    });
    const hook = () => {
      for (const [d, v] of labels) { const [x, y, z] = this.vp.toScreen(v); d.style.left = `${x}px`; d.style.top = `${y}px`; d.hidden = z > 1; }
    };
    hook.pmi = true; this.vp.frameHooks.push(hook); this.vp.dirty = true;
  },

  updateDecals() {
    const vp = this.vp;
    if (!vp.groups.decals) { vp.groups.decals = new THREE.Group(); vp.scene.add(vp.groups.decals); }
    vp._clear(vp.groups.decals);
    const decals = this.model?.kind === "assembly" ? [] : (this.model?.render?.decals || []);
    for (const d of decals) {
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(d.width, d.width), new THREE.MeshBasicMaterial({ transparent: true, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }));
      new THREE.TextureLoader().load(d.image, (t) => {
        t.colorSpace = THREE.SRGBColorSpace; mesh.material.map = t; mesh.material.needsUpdate = true;
        mesh.scale.set(1, t.image.height / t.image.width, 1); vp.dirty = true;
      });
      const n = new THREE.Vector3(...d.normal).normalize();
      mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), n);
      mesh.rotateZ(THREE.MathUtils.degToRad(d.rotation || 0));
      mesh.position.copy(new THREE.Vector3(...d.center).addScaledVector(n, 0.02));
      mesh.userData = { kind: "decal", name: d.name };
      vp.groups.decals.add(mesh);
    }
    vp.dirty = true;
  },

  afterRefreshMore() { this.updatePMI(); this.updateDecals(); },

  renderSettings() {
    return { scene: $("#rsc").value, floor: $("#rfl").checked, shadows: $("#rsh").checked, exposure: parseFloat($("#rex").value), background: $("#rbgu").checked ? $("#rbg").value : null };
  },

  renderDialog() {
    const r = this.model?.render || {};
    this.modal("Render Tools", `<div class="field"><label>Scene</label><select id="rsc">${Object.keys(SCENES).map((k) => `<option value="${k}" ${k === (r.scene || "studio") ? "selected" : ""}>${k.replace("_", " ")}</option>`).join("")}</select></div>
      <label class="pm-check"><input id="rfl" type="checkbox" ${r.floor !== false ? "checked" : ""}> Floor</label>
      <label class="pm-check"><input id="rsh" type="checkbox" ${r.shadows !== false ? "checked" : ""}> Shadows</label>
      <div class="field"><label>Exposure</label><input id="rex" type="range" min="0.3" max="2.5" step="0.05" value="${r.exposure || 1}" style="width:100%"></div>
      <div class="field"><label>Background</label><div style="display:flex;gap:8px;align-items:center"><input id="rbg" type="color" value="${r.background || "#e9ebee"}"><label class="pm-check"><input id="rbgu" type="checkbox" ${r.background ? "checked" : ""}> use this colour</label></div></div>
      <div class="field"><label>Resolution</label><select id="rres"><option value="1">Screen</option><option value="2" selected>2× screen</option><option value="3">3× screen</option></select></div>
      <div class="field"><label>Quality</label><select id="rq"><option value="0">Fast (real-time shading)</option><option value="64">Path traced, 64 samples</option><option value="256" selected>Path traced, 256 samples</option><option value="1024">Path traced, 1024 samples</option></select></div>
      <div id="rprev" style="margin-top:8px"></div>`, async () => {
      const s = this.renderSettings(), samples = parseInt($("#rq").value) || 0, scale = parseInt($("#rres").value);
      const url = samples ? await this.renderPathTracedWithProgress(s, scale, samples) : this.renderPhoto(s, scale);
      if (!url) return; // cancelled
      await this.run("set_render_scene", s);
      const name = `${this.model.name}-render.png`;
      if (this.desk?.save_data_url) { const path = await this.askPath("save", name, ["PNG image (*.png)"], "Save Rendering"); if (path) await this.desk.save_data_url(path, url); }
      else { const a = document.createElement("a"); a.href = url; a.download = name; a.click(); }
    }, "appearance");
    const foot = document.querySelector("#modal-box .mb-foot");
    const ok = foot.querySelector(".primary"); if (ok) ok.textContent = "Render";
    const prev = el("button", "btn", "Preview"); prev.onclick = () => { $("#rprev").innerHTML = `<img src="${this.renderPhoto(this.renderSettings(), 1)}" style="width:100%;border:1px solid var(--border)">`; };
    foot.prepend(prev);
  },

  /** Render the current view with PBR materials, an environment, floor and shadows; returns a PNG data URL. */
  renderPhoto(s, scale = 2) {
    const vp = this.vp, S = SCENES[s.scene] || SCENES.studio;
    const W = Math.round(vp.w * scale), H = Math.round(vp.h * scale);
    const r = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    r.setSize(W, H, false); r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = s.exposure || 1;
    r.outputColorSpace = THREE.SRGBColorSpace; r.shadowMap.enabled = !!s.shadows; r.shadowMap.type = THREE.PCFSoftShadowMap;
    const pm = new THREE.PMREMGenerator(r), env = pm.fromScene(envScene(s.scene), 0.03).texture;
    const scene = new THREE.Scene(); scene.environment = env;
    scene.background = new THREE.Color(s.background || S.bg);
    const box = vp.bbox.clone(), c = box.getCenter(new THREE.Vector3()), rad = box.getSize(new THREE.Vector3()).length() / 2 || 50;
    const mat = this.model?.material || {}, made = [];
    for (const [, m] of vp.faces) {
      if (!vp.componentShown(m.userData.id)) continue;
      const pbr = pbrFor(m.userData.base ?? 0xb4bac2, mat); made.push(pbr);
      const mm = new THREE.Mesh(m.geometry, pbr); mm.castShadow = mm.receiveShadow = true; scene.add(mm);
    }
    if (vp.groups.decals) scene.add(vp.groups.decals.clone());
    if (s.floor) {
      const fm = new THREE.MeshStandardMaterial({ color: S.floor, roughness: 0.85, metalness: 0 }); made.push(fm);
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(rad * 30, rad * 30), fm);
      floor.rotation.x = -Math.PI / 2; floor.position.set(c.x, box.min.y - 0.01, c.z); floor.receiveShadow = true; scene.add(floor);
    }
    const sun = new THREE.DirectionalLight(0xffffff, 2.2);
    sun.position.set(c.x + rad * 1.5, c.y + rad * 4, c.z + rad * 2); sun.target.position.copy(c);
    sun.castShadow = !!s.shadows; sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0005; sun.shadow.normalBias = rad * 0.002;
    const sc = sun.shadow.camera; sc.left = sc.bottom = -rad * 2; sc.right = sc.top = rad * 2; sc.near = rad * 0.5; sc.far = rad * 10;
    scene.add(sun, sun.target, new THREE.HemisphereLight(0xffffff, 0x444444, 0.3));
    const cam = vp.camera.clone();
    if (cam.isPerspectiveCamera) cam.aspect = W / H;
    cam.updateProjectionMatrix();
    r.render(scene, cam);
    const url = r.domElement.toDataURL("image/png");
    for (const m of made) m.dispose();
    pm.dispose(); env.dispose(); r.dispose();
    return url;
  },

  /** Path-traced rendering with a progress toast and Cancel; resolves to a PNG data URL, or null if cancelled. */
  async renderPathTracedWithProgress(s, scale, samples) {
    const t = el("div", "toast"); t.style.minWidth = "260px";
    t.innerHTML = `<div><b>Path tracing</b> <span class="pt-msg">loading...</span></div>
      <div style="height:6px;background:#0002;border-radius:3px;margin:6px 0"><div class="pt-bar" style="height:6px;width:0;background:#2b7de9;border-radius:3px"></div></div>
      <button class="btn pt-cancel">Cancel</button>`;
    document.querySelector("#toasts").append(t);
    let cancelled = false;
    t.querySelector(".pt-cancel").onclick = () => { cancelled = true; };
    try {
      return await this.renderPathTraced(s, scale, samples, (done, phase) => {
        t.querySelector(".pt-bar").style.width = `${Math.round(100 * done / samples)}%`;
        t.querySelector(".pt-msg").textContent = phase === "compiling" ? "preparing the renderer (the first time can take a minute)..."
          : `${Math.floor(done)} / ${samples} samples`;
      }, () => cancelled);
    } catch (e) {
      this.toast(`Path tracing failed: ${escapeHtml(e.message || String(e))} (Fast quality still works)`, "err");
      return null;
    } finally { t.remove(); }
  },

  /** The same scene as renderPhoto, path traced (three-gpu-pathtracer, loaded on first use): soft shadows,
   *  reflections and indirect light computed rather than approximated. onProgress(samples, phase) - phase
   *  "compiling" while the GPU builds the tracer's shader (only the first render of a session: the renderer
   *  is kept and reused), then "tracing"; isCancelled() stops it. Resolves to a PNG data URL, or null if
   *  cancelled. */
  async renderPathTraced(s, scale = 1, samples = 256, onProgress = () => {}, isCancelled = () => false) {
    const { WebGLPathTracer, GradientEquirectTexture } = await import("./vendor/three-gpu-pathtracer.module.js");
    const vp = this.vp, S = SCENES[s.scene] || SCENES.studio;
    const W = Math.round(vp.w * scale), H = Math.round(vp.h * scale);
    if (!this._pathTracer) { // one WebGL context and tracer for the session: its shader takes long to compile
      const r = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: true });
      if (!r.capabilities.isWebGL2) { r.dispose(); throw new Error("this browser has no WebGL2"); }
      r.toneMapping = THREE.ACESFilmicToneMapping; r.outputColorSpace = THREE.SRGBColorSpace;
      const pt = new WebGLPathTracer(r);
      pt.tiles.set(2, 2); pt.bounces = 6; pt.renderDelay = 0; pt.minSamples = 0; pt.fadeDuration = 0; pt.rasterizeScene = false;
      this._pathTracer = { r, pt };
    }
    const { r, pt } = this._pathTracer;
    r.setSize(W, H, false); r.toneMappingExposure = s.exposure || 1;
    const scene = new THREE.Scene(), made = [];
    const env = new GradientEquirectTexture(256); // a sky over the scene's walls: the light the model sees
    env.topColor.set(S.sky ? S.sky[0] : S.lights); env.bottomColor.set(S.walls); env.exponent = 1.5; env.update();
    scene.environment = env; scene.environmentIntensity = 0.9;
    scene.background = new THREE.Color(s.background || S.bg);
    const box = vp.bbox.clone(), c = box.getCenter(new THREE.Vector3()), rad = box.getSize(new THREE.Vector3()).length() / 2 || 50;
    const mat = this.model?.material || {};
    for (const [, m] of vp.faces) {
      if (!vp.componentShown(m.userData.id)) continue;
      const pbr = pbrFor(m.userData.base ?? 0xb4bac2, mat); pbr.polygonOffset = false; made.push(pbr);
      scene.add(new THREE.Mesh(m.geometry, pbr));
    }
    if (s.floor) {
      const fm = new THREE.MeshStandardMaterial({ color: S.floor, roughness: 0.85, metalness: 0 }); made.push(fm);
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(rad * 400, rad * 400), fm);
      floor.rotation.x = -Math.PI / 2; floor.position.set(c.x, box.min.y - 0.01, c.z); scene.add(floor);
    }
    const sun = new THREE.DirectionalLight(0xffffff, s.shadows ? 2.2 : 0); // (path tracing always casts shadows: no sun = none)
    sun.position.set(c.x + rad * 1.5, c.y + rad * 4, c.z + rad * 2); sun.target.position.copy(c);
    scene.add(sun, sun.target);
    const cam = vp.camera.clone();
    if (cam.isPerspectiveCamera) cam.aspect = W / H;
    else { // the viewport's orthographic camera spans +/-1e5 mm: in single precision the tracer's hits then lose ~0.01 mm,
      // enough for bounced rays to hit their own surface (striping). Same view, from just outside the model:
      // (slid along its own axis only, so the picture is exactly the viewport's)
      const back = new THREE.Vector3(0, 0, 1).applyQuaternion(cam.quaternion);
      cam.position.addScaledVector(back, rad * 3 - cam.position.clone().sub(c).dot(back)); cam.near = rad * 0.5; cam.far = rad * 400;
    }
    cam.updateProjectionMatrix(); cam.updateMatrixWorld();
    try {
      pt.setScene(scene, cam);
      pt.reset();
      const t0 = performance.now(), pause = () => new Promise((res) => setTimeout(res, 0));
      let traced = 0;
      while (pt.samples < samples) {
        if (isCancelled()) return null;
        pt.renderSample();
        if (pt.isCompiling) { // the first render: the GPU is building the shader (it can take a minute)
          onProgress(0, "compiling");
          if (performance.now() - t0 > 300000) throw new Error("the GPU did not finish compiling the path tracer in 5 minutes");
          await new Promise((res) => setTimeout(res, 100));
          continue;
        }
        onProgress(Math.min(pt.samples, samples), "tracing");
        if (++traced > samples * 64) throw new Error("the path tracer made no progress");
        if (traced % 4 === 0) await pause(); // let the page breathe (and the toast update) between tiles
      }
      return r.domElement.toDataURL("image/png");
    } finally {
      for (const m of made) m.dispose(); env.dispose(); // (the renderer and tracer stay for the next render)
    }
  },

  altPositionsDialog() {
    const names = Object.keys(this.model?.alternate_positions || {});
    this.modal("Alternate Position", `<div class="field"><label>Save the current positions as</label><input id="apn" value="Position${names.length + 1}"></div>
      ${names.length ? `<p>Saved: ${names.map((n) => `<button class="btn" data-ap="${escapeHtml(n)}">${escapeHtml(n)}</button><button class="btn" data-apd="${escapeHtml(n)}" title="Delete">✕</button>`).join(" ")}</p>` : ""}`,
    () => this.run("save_alternate_position", { name: $("#apn").value }), "explode");
    document.querySelectorAll("[data-ap]").forEach((b) => b.onclick = async () => { await this.run("show_alternate_position", { name: b.dataset.ap }).catch(() => {}); $("#modal").classList.add("hidden"); });
    document.querySelectorAll("[data-apd]").forEach((b) => b.onclick = async () => { await this.run("delete_alternate_position", { name: b.dataset.apd }).catch(() => {}); $("#modal").classList.add("hidden"); });
  },

  async compToggle(cmd, key, label) {
    const comps = this.selectedComponentsAny?.() || [];
    if (!comps.length) return this.toast(`Select a component for ${label}.`, "err");
    const cur = (this.model.components || []).find((c) => c.name === comps[0]);
    const on = !(cur && cur[key]);
    for (const c of comps) await this.run(cmd, { component: c, [key]: on }).catch(() => {});
    this.toast(`${label} ${on ? "on" : "off"}: ${comps.join(", ")}`);
  },

  async packAndGoDialog() {
    const r = await this.cmdForm("Pack and Go", "pack_and_go", [["folder", "Save to folder", "text", ""], ["prefix", "Add prefix", "text", ""], ["suffix", "Add suffix", "text", ""],
      ["zip_file", "Save to Zip file (optional)", "text", ""]], { ic: "save", note: "Copies the assembly and every file it uses, renaming references." });
    if (r) this.toast(`Pack and Go: ${(r.files || []).length} files copied.`);
  },

  async defeatureDialog() {
    await this.cmdForm("Defeature", "defeature", [["max_hole_diameter", "Remove holes up to ⌀ (mm, blank: auto)", "number", ""],
      ["max_fillet_radius", "Remove fillets up to R (mm, blank: auto)", "number", ""]], { ic: "trash", note: "A simplified copy (new document) without small holes and fillets, for sharing." });
  },

  async pipeSizes() {
    if (!this._pipeCat) this._pipeCat = (await this.run("list_pipe_standards", {})).result;
    return this._pipeCat.sizes.map((s) => [s.size, `NPS ${s.size}  (OD ${s.od})`]);
  },

  /** Routing > Pipe from the piping library: standard sizes, elbows at the corners, end flanges. */
  async pipeRouteDialog() {
    const sizes = await this.pipeSizes();
    const r = await this.cmdForm("Routing - Pipe", "route_pipe_standard",
      [["points", "Route points x, y, z (one per line or ; between)", "points", "0, 0, 0; 500, 0, 0; 500, 400, 0"],
        ["size", "Nominal pipe size", "select", "2", sizes], ["schedule", "Schedule", "select", "40", ["40", "80"]],
        ["elbows", "Elbows", "select", "long", [["long", "Long radius (1.5 D)"], ["short", "Short radius (1.0 D)"]]],
        ["bend_radius", "Custom bend radius (mm, blank: standard elbow)", "number", ""],
        ["flanges", "Weld-neck flanges", "select", "both", [["both", "Both ends"], ["start", "Start"], ["end", "End"], ["none", "None"]]]],
      { ic: "sweep", note: "ASME B36.10 pipe, B16.9 elbows, B16.5 class 150 flanges (nominal sizes). Each part becomes a virtual component listed in the BOM." });
    if (r) this.showResult("Route - Cut List", { pipes: r.cut_list.length, total_pipe_length_mm: r.pipe_length, elbows: r.elbows, flanges: r.flanges },
      `<table class="kv">${r.parts.map((p) => `<tr><td>${escapeHtml(p.component)}</td><td>${escapeHtml(p.description)}</td></tr>`).join("")}</table>`, "sweep");
  },

  async fittingDialog() {
    const sizes = await this.pipeSizes();
    await this.cmdForm("Insert Fitting", "insert_fitting",
      [["kind", "Fitting", "select", "elbow", [["elbow", "Elbow (B16.9)"], ["tee", "Tee, equal (B16.9)"], ["reducer", "Reducer, concentric (B16.9)"], ["cap", "Cap (B16.9)"], ["flange", "Weld-neck flange, class 150 (B16.5)"]]],
        ["size", "Nominal pipe size", "select", "2", sizes], ["small_size", "Reducer small end", "select", "1", sizes],
        ["schedule", "Schedule", "select", "40", ["40", "80"]], ["angle", "Elbow angle (deg)", "number", 90],
        ["elbow_radius", "Elbow radius", "select", "long", [["long", "Long"], ["short", "Short"]]],
        ["position", "Connection point x, y, z (tee: centre)", "vec", "0, 0, 0"], ["direction", "Pipe axis direction", "vec", "0, 0, 1"],
        ["turn_direction", "Turn / branch direction", "vec", "1, 0, 0"]], { ic: "sweep" });
  },

  routeDialog(kind) {
    const pipe = kind === "pipe";
    return this.cmdForm(pipe ? "Routing - Pipe" : "Routing - Cable", pipe ? "route_pipe" : "route_cable",
      [["points", "Route points x, y, z (one per line or ; between)", "points", "0, 0, 0; 100, 0, 0; 100, 100, 0"],
        ...(pipe ? [["outer_diameter", "Outer diameter (mm)", "number", 21.3], ["wall_thickness", "Wall thickness (mm)", "number", 2.8], ["bend_radius", "Bend radius (mm, 0: elbows)", "number", 40]]
          : [["diameter", "Cable diameter (mm)", "number", 6]])], { ic: "sweep", note: "The route becomes a virtual component of the assembly." });
  },

  async holeSeriesDialog() {
    await this.cmdForm("Hole Series", "hole_series", [["position", "Hole axis point x, y, z", "vec", "0, 0, 0"], ["direction", "Direction x, y, z", "vec", "0, -1, 0"],
      ["diameters", "Diameter in each component, in order (mm)", "list", "6.6, 6.6"]], { ic: "hole", note: "One hole through every component it meets, each a feature of that part." });
  },

  async magneticSnap() {
    const comps = this.selectedComponentsAny?.() || [];
    if (!comps.length) return this.toast("Select the component to snap.", "err");
    const r = await this.run("magnetic_snap", { component: comps[0] }).catch(() => null);
    if (r) this.toast(r.result?.mate ? `Snapped: ${escapeHtml(r.result.mate)}` : "No connection point within reach.");
  },

  async designLibraryDialog() {
    const lib = await this.evalCall("list_design_library"); if (!lib) return;
    const asm = this.model?.kind === "assembly";
    const feats = (lib.features || []).map((f) => `<tr><td>${escapeHtml(f.name)}</td><td class="muted">${escapeHtml(f.description || "feature")}</td>
      <td>${asm ? "" : `<button class="btn" data-lf="${escapeHtml(f.name)}">Insert</button>`} <button class="btn" data-ld="${escapeHtml(f.path)}" title="Delete">✕</button></td></tr>`).join("");
    const parts = (lib.parts || []).map((p) => `<tr><td>${escapeHtml(p.name)}</td><td class="muted">part</td><td>${asm ? `<button class="btn" data-lp="${escapeHtml(p.path)}">Insert</button>` : ""}
      <button class="btn" data-ld="${escapeHtml(p.path)}" title="Delete">✕</button></td></tr>`).join("");
    const picked = (this.selection || []).filter((s) => s.kind === "feature").map((s) => s.id);
    this.modal("Design Library", `<p class="muted">${escapeHtml(lib.folder)}</p><table class="dw-bom">${feats}${parts}${!feats && !parts ? "<tr><td>The library is empty.</td></tr>" : ""}</table>
      ${asm ? "" : `<h4>Add Library Feature</h4><div class="field"><label>Name</label><input id="dln" value=""></div><div class="field"><label>Description</label><input id="dld" value=""></div>
      <p class="muted">Features: ${picked.length ? escapeHtml(picked.join(", ")) : "select features in the FeatureManager tree first"}</p>`}
      <button class="btn" id="dlpart">Add the active ${asm ? "assembly" : "part"} to the library</button>`,
    !asm && picked.length ? () => this.run("save_library_feature", { name: $("#dln").value || picked[0], features: picked, description: $("#dld").value }) : null, "folder");
    document.querySelectorAll("[data-lf]").forEach((b) => b.onclick = async () => {
      const plane = this.selection.find((s) => s.kind === "face" || s.kind === "plane")?.id || "Top";
      await this.run("insert_library_feature", { name: b.dataset.lf, plane }).catch(() => {}); $("#modal").classList.add("hidden");
    });
    document.querySelectorAll("[data-lp]").forEach((b) => b.onclick = async () => { await this.run("insert_component", { path: b.dataset.lp }).catch(() => {}); $("#modal").classList.add("hidden"); });
    document.querySelectorAll("[data-ld]").forEach((b) => b.onclick = async () => { await this.run("delete_library_item", { path: b.dataset.ld }).catch(() => {}); this.designLibraryDialog(); });
    const ap = $("#dlpart"); if (ap) ap.onclick = async () => { await this.run("add_part_to_design_library", {}).catch(() => {}); this.designLibraryDialog(); };
  },

  async pdmDialog(filter = "") {
    const all = await this.evalCall("pdm_list"); if (!all) return;
    const files = filter ? ((await this.evalCall("pdm_search", { text: filter }))?.files || []) : all.files;
    const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "");
    const rows = files.map((f) => `<tr><td>${escapeHtml(f.name)}</td><td>${f.version ?? ""}</td><td>${escapeHtml(f.revision || "-")}</td><td>${escapeHtml(f.state)}</td>
      <td>${f.checked_out ? `🔒 ${escapeHtml(f.checked_out.user)}` : ""}</td><td class="muted">${when(f.modified)}</td>
      <td style="white-space:nowrap">${f.checked_out ? `<button class="btn" data-pu="${escapeHtml(f.name)}">Undo Check Out</button>` : `<button class="btn" data-po="${escapeHtml(f.name)}">Check Out</button>`}
      <button class="btn" data-pg="${escapeHtml(f.name)}">Get</button> <button class="btn" data-ph="${escapeHtml(f.name)}">History</button>
      <select data-ps="${escapeHtml(f.name)}"><option value="">State…</option>${["Work in Progress", "Pending Approval", "Released"].filter((s) => s !== f.state).map((s) => `<option>${s}</option>`).join("")}</select></td></tr>`).join("");
    this.modal("PDM Vault", `<div class="field"><label>Vault folder</label><div style="display:flex;gap:6px"><input id="pdv" value="${escapeHtml(all.vault)}" style="flex:1"><button class="btn" id="pdvs">Set</button></div></div>
      <div style="display:flex;gap:6px;margin:6px 0"><input id="pdq" placeholder="Search name, comments, properties" value="${escapeHtml(filter)}" style="flex:1"><button class="btn" id="pdqs">Search</button></div>
      <div style="overflow-x:auto"><table class="dw-bom"><tr><th>File</th><th>Ver</th><th>Rev</th><th>State</th><th>Checked out</th><th>Modified</th><th></th></tr>${rows || "<tr><td colspan=7>No files.</td></tr>"}</table></div>
      <h4>Check In the active document</h4><div style="display:flex;gap:6px;flex-wrap:wrap"><input id="pdc" placeholder="Comment" style="flex:1"><label class="pm-check"><input id="pdk" type="checkbox"> Keep checked out</label><button class="btn primary" id="pdci">Check In</button></div>`, null, "folder");
    const again = () => this.pdmDialog(filter);
    $("#pdvs").onclick = async () => { await this.run("pdm_set_vault", { path: $("#pdv").value }).catch(() => {}); this.pdmDialog(); };
    $("#pdqs").onclick = () => this.pdmDialog($("#pdq").value.trim());
    $("#pdci").onclick = async () => { const r = await this.run("pdm_check_in", { comment: $("#pdc").value, keep_checked_out: $("#pdk").checked }).catch(() => null); if (r) { this.toast(`Checked in ${escapeHtml(r.result.name)} version ${r.result.version}`); again(); } };
    document.querySelectorAll("[data-po]").forEach((b) => b.onclick = async () => { await this.run("pdm_check_out", { name: b.dataset.po }).catch(() => null); again(); });
    document.querySelectorAll("[data-pu]").forEach((b) => b.onclick = async () => { await this.run("pdm_undo_check_out", { name: b.dataset.pu }).catch(() => null); again(); });
    document.querySelectorAll("[data-pg]").forEach((b) => b.onclick = async () => { const r = await this.run("pdm_get_version", { name: b.dataset.pg }).catch(() => null); if (r) this.toast(`Copied to ${escapeHtml(r.result.path)}`); });
    document.querySelectorAll("[data-ps]").forEach((s) => s.onchange = async () => { if (s.value) await this.run("pdm_change_state", { name: s.dataset.ps, state: s.value }).catch(() => null); again(); });
    document.querySelectorAll("[data-ph]").forEach((b) => b.onclick = async () => {
      const h = await this.evalCall("pdm_history", { name: b.dataset.ph }); if (!h) return;
      const used = await this.evalCall("pdm_where_used", { name: b.dataset.ph });
      this.modal(`History - ${h.name}`, `<p>State: <b>${escapeHtml(h.state)}</b>, revision ${escapeHtml(h.revision || "-")}${used?.used_in?.length ? `; used in ${escapeHtml(used.used_in.join(", "))}` : ""}</p>
        <table class="dw-bom"><tr><th>Ver</th><th>Rev</th><th>Comment</th><th>User</th><th>Date</th><th></th></tr>${h.versions.slice().reverse().map((x) => `<tr><td>${x.version}</td><td>${escapeHtml(x.revision || "")}</td>
        <td>${escapeHtml(x.comment || "")}</td><td>${escapeHtml(x.user)}</td><td>${when(x.time)}</td><td><button class="btn" data-pv="${x.version}">Get</button></td></tr>`).join("")}</table>
        <h4>Log</h4><div class="muted" style="max-height:140px;overflow:auto">${h.history.slice().reverse().map((e) => `${when(e.time)} · ${escapeHtml(e.user)} · ${escapeHtml(e.event)}${e.comment ? ` - ${escapeHtml(e.comment)}` : ""}`).join("<br>")}</div>`, null, "folder");
      document.querySelectorAll("[data-pv]").forEach((g) => g.onclick = async () => { const r = await this.run("pdm_get_version", { name: h.name, version: parseInt(g.dataset.pv) }).catch(() => null); if (r) this.toast(`Copied to ${escapeHtml(r.result.path)}`); });
    });
  },

  async publishEDrawing() {
    const path = await this.askPath("save", `${this.model.name}.html`, ["eDrawings-style viewer (*.html)"], "Publish eDrawings File");
    if (!path) return;
    const r = await this.run("export_edrawing", { path }).catch(() => null);
    if (r) this.toast(`Published ${escapeHtml(r.result.path)}: open it in any browser to view and mark up.`);
  },

  async macroRecord() {
    const st = await this.evalCall("macro_status");
    if (st?.recording) return this.macroStop();
    await this.run("macro_start"); this.toast("Recording a macro: Tools > Macro > Stop saves it.");
  },

  async macroStop() {
    const st = await this.evalCall("macro_status");
    if (!st?.recording) return this.toast("No macro is recording.", "err");
    const path = await this.askPath("save", "macro.py", ["Python macro (*.py)"], "Save Macro");
    const r = await this.run("macro_stop", { path: path || null }).catch(() => null);
    if (r) this.toast(`Saved ${r.result.commands} commands to ${escapeHtml(r.result.path)}`);
  },

  // Tools > Dimension Tolerances: every model dimension with its tolerance (type, values or ISO 286 fit)
  async toleranceDialog() {
    if ((this.model.kind || "part") !== "part") return this.toast("Dimension tolerances belong to a part.", "err");
    const r = await this.evalCall("get_dimension_tolerances", { all_dimensions: true });
    if (!r) return;
    if (!r.dimensions.length) return this.toast("This part has no dimensions yet.");
    const types = ["none", "symmetric", "bilateral", "limit", "fit"];
    const rows = r.dimensions.map((d, i) => {
      const t = d.tolerance || { type: "none" };
      return `<tr data-i="${i}"><td>${escapeHtml(d.dimension)}</td><td>${d.value == null ? "" : +d.value.toFixed(4)}</td>
        <td><select class="tt">${types.map((x) => `<option${x === t.type ? " selected" : ""}>${x}</option>`).join("")}</select></td>
        <td><input class="tp" type="number" step="0.01" min="0" value="${t.plus ?? ""}" style="width:70px"></td>
        <td><input class="tm" type="number" step="0.01" min="0" value="${t.minus ?? ""}" style="width:70px"></td>
        <td><input class="tf" value="${escapeHtml(t.fit || "")}" placeholder="H7" style="width:60px"></td>
        <td>${d.min == null ? "" : `${d.min} … ${d.max}`}</td></tr>`;
    }).join("");
    this.modal("Dimension Tolerances", `<p>Symmetric: ± plus. Bilateral and limit: +plus / −minus. Fit: an ISO 286 class (H7, g6, ...).</p>
      <div style="max-height:50vh;overflow:auto"><table class="dw-bom"><tr><th>Dimension</th><th>Value</th><th>Type</th><th>Plus</th><th>Minus</th><th>Fit</th><th>Limits</th></tr>${rows}</table></div>`,
    async () => {
      for (const tr of document.querySelectorAll("#modal-box tr[data-i]")) {
        const d = r.dimensions[+tr.dataset.i], was = d.tolerance || { type: "none" };
        const now = { type: tr.querySelector(".tt").value, plus: parseFloat(tr.querySelector(".tp").value) || 0,
          minus: parseFloat(tr.querySelector(".tm").value) || 0, fit: tr.querySelector(".tf").value.trim() || null };
        const same = was.type === now.type && (was.plus || 0) === now.plus && (was.minus ?? (was.type === "symmetric" ? 0 : 0)) === now.minus && (was.fit || null) === now.fit;
        if (same || (was.type === "none" && now.type === "none")) continue;
        await this.run("set_dimension_tolerance", { dimension: d.dimension, ...now });
      }
    }, "smart_dim");
  },

  // Tools > Design Review: findings with what to do about each
  async reviewDialog() {
    const r = await this.evalCall("review_model", {});
    if (!r) return;
    const colour = { error: "#c62828", warning: "#b26a00", info: "#555" };
    const rows = r.findings.map((f) => `<tr><td style="color:${colour[f.severity]}"><b>${f.severity}</b></td><td>${escapeHtml(f.subject)}</td><td>${escapeHtml(f.message)}${f.fix ? `<br><span style="color:#666">→ ${escapeHtml(f.fix)}</span>` : ""}</td></tr>`).join("");
    this.modal(`Design Review: ${r.counts.error} errors, ${r.counts.warning} warnings`, r.findings.length
      ? `<div style="max-height:55vh;overflow:auto"><table class="dw-bom"><tr><th></th><th>About</th><th>Finding</th></tr>${rows}</table></div>`
      : "<p>Nothing to report: the part rebuilds, its sketches are defined and its requirements pass.</p>", null, "check");
  },

  // Tools > Requirements: limits the design must keep meeting (checked here, in the review and by `openswx check`)
  async requirementsDialog() {
    const r = await this.evalCall("check_requirements", {});
    if (!r) return;
    const rows = r.requirements.map((q) => `<tr><td>${q.passes ? "✔" : "✘"}</td><td>${escapeHtml(q.name)}</td><td>${q.quantity}${q.target ? " " + escapeHtml(q.target) : ""} ${q.op} ${q.value}</td><td>${q.current ?? escapeHtml(q.error || "")}</td><td><button class="btn rq-del" data-n="${escapeHtml(q.name)}">Delete</button></td></tr>`).join("");
    const quantities = ["mass", "volume", "surface_area", "size_x", "size_y", "size_z", "longest_side", "min_thickness", "dimension", "bodies", "errors", "components", "interferences"];
    this.modal("Requirements", `${r.requirements.length ? `<table class="dw-bom"><tr><th></th><th>Name</th><th>Requirement</th><th>Now</th><th></th></tr>${rows}</table>` : "<p>No requirements yet.</p>"}
      <p style="margin-top:10px"><b>Add</b> (mass in g, lengths in mm; OK adds it)</p>
      <div class="field"><label>Name</label><input id="rqn"></div>
      <div class="field"><label>Quantity</label><select id="rqq">${quantities.map((x) => `<option>${x}</option>`).join("")}</select></div>
      <div class="field"><label>Dimension (for "dimension")</label><input id="rqt" placeholder="D1@Sketch1"></div>
      <div class="field"><label>Must be</label><select id="rqo">${["<", "<=", ">", ">=", "==", "!="].map((x) => `<option>${escapeHtml(x)}</option>`).join("")}</select><input id="rqv" type="number" step="any"></div>`,
    async () => {
      const name = $("#rqn").value.trim();
      if (!name) return;
      await this.run("add_requirement", { name, quantity: $("#rqq").value, op: $("#rqo").value, value: parseFloat($("#rqv").value) || 0, target: $("#rqt").value.trim() || null });
      setTimeout(() => this.requirementsDialog(), 0);
    }, "check");
    for (const b of document.querySelectorAll(".rq-del")) b.onclick = async () => { await this.run("delete_requirement", { name: b.dataset.n }).catch(() => null); this.requirementsDialog(); };
  },

  async exportScript() {
    const path = await this.askPath("save", `${this.model.name}.py`, ["Python script (*.py)"], "Export Script");
    if (!path) return;
    const r = await this.api("/api/command", { name: "export_script", args: { path } });
    if (r.ok) this.toast(`Saved ${r.result.commands} commands to ${escapeHtml(r.result.path)}`); else this.toast(r.error, "err");
  },

  async handoffPack() {
    const path = await this.askPath("save", `${this.model.name}-pack`, ["Folder"], "Hand-off Pack (a folder and a .zip beside it)");
    if (!path) return;
    const r = await this.api("/api/command", { name: "export_handoff_pack", args: { folder: path.replace(/\.zip$/i, "") } });
    if (r.ok) this.toast(`${r.result.files.length} files in ${escapeHtml(r.result.folder)}${r.result.not_included.length ? ` (${r.result.not_included.length} not included: see manifest.json)` : ""}`); else this.toast(r.error, "err");
  },

  // Tools > Sketch Entities > Equation Driven Curve (in an open sketch)
  async equationCurve() {
    if (!this.sk.active) return this.toast("Open a sketch first: the curve is drawn in it.", "err");
    const r = await this.cmdForm("Equation Driven Curve", "sketch_equation_curve", [["x", "x(t)", "text", "t"], ["y", "y(t)", "text", "t^2/20"],
      ["t_start", "t from", "number", -10], ["t_end", "t to", "number", 10], ["points", "Points", "number", 25], ["closed", "Closed", "check", false]],
    { fixed: { sketch: this.sk.name }, note: "Uses t, global variables and the equation functions (sin / cos in degrees)." });
    if (r) await this.loadSketch();
  },

  // Tools > User Settings: saved for this user in the openswx home folder (System Options is per session / document)
  async userSettingsDialog() {
    const r = await this.api("/api/command", { name: "get_settings", args: {} });
    if (!r.ok) return this.toast(r.error, "err");
    const { settings, about, file } = r.result;
    const shown = Object.entries(about).filter(([, a]) => !(a.default && typeof a.default === "object")); // (shortcuts: Customize Keyboard)
    const rows = shown.map(([k, a]) => {
      const v = settings[k];
      let input;
      if (typeof a.default === "boolean") input = `<input id="op_${k}" type="checkbox" ${v ? "checked" : ""}>`;
      else if (a.kind === "color") input = `<input id="op_${k}" type="color" value="${escapeHtml(v)}">`;
      else if (a.choices) input = `<select id="op_${k}">${a.choices.map((c) => `<option value="${escapeHtml(c)}"${c === v ? " selected" : ""}>${escapeHtml(c)}${a.choice_docs?.[c] ? ` (${escapeHtml(a.choice_docs[c])})` : ""}</option>`).join("")}</select>`;
      else if (typeof a.default === "number") input = `<input id="op_${k}" type="number" min="${a.min ?? ""}" max="${a.max ?? ""}" value="${v}" style="width:90px">`;
      else input = `<input id="op_${k}" value="${escapeHtml(String(v))}" style="width:180px">`;
      return `<div class="field"><label>${escapeHtml(a.doc)}</label>${input}</div>`;
    }).join("");
    this.modal("User Settings", `${rows}<p class="muted">Saved in ${escapeHtml(file)}. Autosave and MCP toolsets apply from the next start. Keyboard shortcuts: Tools &gt; Customize Keyboard.</p>`, async () => {
      const values = {};
      for (const [k, a] of shown) {
        const e = document.getElementById(`op_${k}`);
        values[k] = typeof a.default === "boolean" ? e.checked : typeof a.default === "number" ? parseFloat(e.value) : e.value;
      }
      const s = await this.api("/api/command", { name: "set_settings", args: { values } });
      if (!s.ok) { this.toast(s.error, "err"); throw new Error(s.error); }
      this.applyUserSettings(s.result.settings);
      this.toast("Settings saved.");
    }, "properties");
  },

  // Tools > Assembly > Display States: named sets of hidden components
  async displayStatesDialog() {
    if (this.model.kind !== "assembly") return this.toast("Display states belong to an assembly.", "err");
    const m = this.model, hidden = [...this.hiddenComps];
    const rows = (m.display_states || []).map((n) => `<tr><td>${n === m.display_state ? "●" : ""}</td><td>${escapeHtml(n)}</td><td><button class="btn ds-act" data-n="${escapeHtml(n)}">Show</button> <button class="btn ds-del" data-n="${escapeHtml(n)}">Delete</button></td></tr>`).join("");
    this.modal("Display States", `${rows ? `<table class="dw-bom"><tr><th></th><th>State</th><th></th></tr>${rows}</table>` : "<p>No display states yet.</p>"}
      <p><button class="btn ds-all">Show every component</button></p>
      <div class="field"><label>Save what is hidden now (${hidden.length} component${hidden.length === 1 ? "" : "s"}) as</label><input id="dsn"></div>`, async () => {
      const name = $("#dsn").value.trim();
      if (name) await this.run("save_display_state", { name, hidden });
    }, "eye");
    const again = () => setTimeout(() => this.displayStatesDialog(), 0);
    for (const b of document.querySelectorAll(".ds-act")) b.onclick = async () => { await this.run("activate_display_state", { name: b.dataset.n }).catch(() => null); again(); };
    for (const b of document.querySelectorAll(".ds-del")) b.onclick = async () => { await this.run("delete_display_state", { name: b.dataset.n }).catch(() => null); again(); };
    document.querySelector(".ds-all").onclick = async () => { await this.run("activate_display_state", { name: null }).catch(() => null); again(); };
  },

  // Tools > Assembly > Configurations: which components each configuration leaves out
  async asmConfigDialog() {
    if (this.model.kind !== "assembly") return this.toast("This dialog is for assemblies (parts: the ConfigurationManager tab).", "err");
    const m = this.model, all = [...m.components.map((c) => c.name), ...(m.suppressed_components || [])].sort();
    const sup = new Set(m.suppressed_components || []);
    const cfgs = m.configurations.map((n) => `<option${n === m.active_config ? " selected" : ""}>${escapeHtml(n)}</option>`).join("");
    const comps = all.map((n) => `<label class="pm-check"><input type="checkbox" class="ac-c" value="${escapeHtml(n)}" ${sup.has(n) ? "" : "checked"}> ${escapeHtml(n)}</label>`).join("");
    this.modal("Assembly Configurations", `<div class="field"><label>Configuration (changing it shows that configuration)</label><select id="acs">${cfgs}</select>
        <button class="btn" id="acd" ${m.active_config === "Default" ? "disabled" : ""}>Delete</button></div>
      <div class="field"><label>New configuration (a copy of this one)</label><input id="acn"></div>
      <p>Components in <b>${escapeHtml(m.active_config)}</b> (untick to suppress; mate values edited now belong to it):</p>
      <div style="max-height:40vh;overflow:auto">${comps}</div>`, async () => {
      const name = $("#acn").value.trim(), ticks = [...document.querySelectorAll(".ac-c")].map((c) => [c.value, c.checked]);
      if (name) await this.run("add_configuration", { name }); // first: the changes below then belong to the new one only
      for (const [comp, shown] of ticks) {
        if (shown === sup.has(comp)) await this.run("suppress_component", { name: comp, suppressed: !shown });
      }
    }, "properties");
    $("#acs").onchange = async () => { await this.run("activate_configuration", { name: $("#acs").value }).catch(() => null); setTimeout(() => this.asmConfigDialog(), 0); };
    $("#acd").onclick = async () => { await this.run("delete_configuration", { name: m.active_config }).catch(() => null); setTimeout(() => this.asmConfigDialog(), 0); };
  },

  // Help > Tutorials
  tutorialDialog(which = null) {
    const m = this.model || {};
    if (which === null) {
      const list = TUTORIALS.map((t, i) => `<p><button class="btn tut-open" data-i="${i}">${escapeHtml(t.name)}</button></p>`).join("");
      this.modal("Tutorials", `<p>Each tutorial is a short list of steps. Steps are ticked from the model as you do them: close this, work, and reopen it from Help.</p>${list}`, null, "help");
      for (const b of document.querySelectorAll(".tut-open")) b.onclick = () => this.tutorialDialog(+b.dataset.i);
      return;
    }
    const t = TUTORIALS[which];
    this.lastTutorial = which;
    const wrong = t.kind === "assembly" ? false : (m.kind || "part") !== "part";
    const rows = t.steps.map(([text, done], i) => { let ok = false; try { ok = !wrong && !!done(m); } catch { ok = false; } return `<tr><td>${ok ? "✔" : i + 1}</td><td style="${ok ? "color:#777" : ""}">${escapeHtml(text)}</td></tr>`; }).join("");
    this.modal(t.name, `${wrong ? "<p>Open or start a part for this tutorial (File > New > Part).</p>" : ""}<table class="dw-bom">${rows}</table>
      <p><button class="btn tut-back">All tutorials</button></p>`, null, "help");
    document.querySelector(".tut-back").onclick = () => this.tutorialDialog();
  },

  async macroRun() {
    const path = await this.askPath("open", "", ["Python macro (*.py)"], "Run Macro");
    if (!path) return;
    const r = await this.run("run_macro", { path }).catch(() => null);
    if (r) { this.toast(`Macro ran ${r.result.commands} commands.`); this.vp.zoomFit(); }
  },
};

// Help > Tutorials: each step says what to do and is ticked off from the model itself, so the list can be
// reopened at any time to see what is left.
const has = (m, type) => (m.features || []).some((f) => f.type === type && f.status !== "error");
const TUTORIALS = [
  { name: "Your first part: a plate with a hole", kind: "part", steps: [
    ["Features tab > Extruded Boss/Base, then pick the Top Plane in the tree.", (m) => has(m, "sketch")],
    ["Press R and drag a rectangle; Smart Dimension its sides if you like.", (m) => (m.features || []).some((f) => f.type === "sketch" && f.sketch_state)],
    ["Exit Sketch (top-right corner), set the Depth and press OK.", (m) => has(m, "extrude") && (m.volume_mm3 || 0) > 0],
    ["Click the top face, choose Sketch on the small toolbar, draw a circle (C).", (m) => (m.features || []).filter((f) => f.type === "sketch").length >= 2],
    ["Features > Extruded Cut, Through All, OK.", (m) => (m.features || []).some((f) => f.name.startsWith("Cut-Extrude"))],
    ["Pick an edge, Features > Fillet, set a radius, OK.", (m) => has(m, "fillet")],
    ["Tools > Material (or the Material item in the tree): pick one; Evaluate > Mass Properties shows the mass.", (m) => !!(m.material && m.material.name)],
    ["File > Save (Ctrl+S).", (m) => !!m.path && !m.dirty]] },
  { name: "Design intent: dimensions, equations and a design review", kind: "part", steps: [
    ["Start a sketch with a rectangle and add Smart Dimensions until it turns black (fully defined).", (m) => (m.features || []).some((f) => f.sketch_state === "fully_defined")],
    ["Extrude it.", (m) => has(m, "extrude")],
    ["Tools > Equations: add a global variable, then make a dimension equal to it (\"width\").", (m) => Object.keys(m.global_variables || {}).length > 0],
    ["Tools > Dimension Tolerances: give one dimension a tolerance or a fit (H7).", (m) => Object.keys(m.tolerances || {}).length > 0],
    ["Tools > Requirements: add one, for example mass < some value.", (m) => (m.requirements || []).length > 0],
    ["Tools > Design Review: read the findings; nothing here is ticked for you - the review is the result.", () => false]] },
  { name: "An assembly of two parts", kind: "assembly", steps: [
    ["File > New > Assembly (with at least one part open or saved).", (m) => m.kind === "assembly"],
    ["Insert Components: add a part twice; the first is fixed (f), the second floats (-).", (m) => (m.components || []).length >= 2],
    ["Mate: pick a face on each, Coincident, OK. Add mates until the second part stops moving.", (m) => (m.mates || []).length >= 1],
    ["Evaluate > Interference Detection.", () => false],
    ["Tools > Assembly > Configurations: add one and untick a component to suppress it there.", (m) => (m.configurations || []).length >= 2],
    ["File > Make Drawing from Assembly, then add a BOM and balloons.", (m) => !!(m.drawing && (m.drawing.sheets || []).length)]] },
];

export { kv, rgbRamp, css, TUTORIALS };

export function installMore(App) {
  for (const [k, fn] of Object.entries(MIXIN)) App.prototype[k] = fn;
}
