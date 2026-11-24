// PropertyManager: declarative feature forms with selection boxes, group boxes, OK/Cancel and preview.
import { icon } from "./icons.js";

const UPTO = [["UpToNext", "Up To Next"], ["UpToVertex", "Up To Vertex"], ["UpToSurface", "Up To Surface"], ["OffsetFromSurface", "Offset From Surface"]];
const END_BOSS = [["Blind", "Blind"], ["ThroughAll", "Through All"], ["MidPlane", "Mid Plane"], ...UPTO];
const END_CUT = [["Blind", "Blind"], ["ThroughAll", "Through All"], ["ThroughAllBoth", "Through All - Both"], ["MidPlane", "Mid Plane"], ...UPTO];
const needsFace = (v) => ["UpToSurface", "OffsetFromSurface"].includes(v.end_condition);
const AXES = [["", "(select edge/axis)"], ["X", "X axis"], ["Y", "Y axis"], ["Z", "Z axis"]];

// Document units for display/input. The model and the API always work in millimetres and grams.
const UNIT_TABLE = { mm: [1, "g", 1], cm: [10, "g", 1], m: [1000, "kg", 1000], in: [25.4, "lb", 453.59237], ft: [304.8, "lb", 453.59237] };
export const UNITS = {
  name: "mm", f: 1, mass: "g", mf: 1,
  set(n) { const t = UNIT_TABLE[n] || UNIT_TABLE.mm; this.name = UNIT_TABLE[n] ? n : "mm"; [this.f, this.mass, this.mf] = t; },
  len(mm) { return mm / this.f; },
  toMM(v) { return v * this.f; },
  fmt(mm, d = 3) { return `${(mm / this.f).toFixed(d).replace(/\.?0+$/, "")} ${this.name}`; },
};
const isLengthField = (f) => ["depth", "fillet"].includes(f.icon) && !/°|\(deg/.test(f.label) && !f.notLength;

const extrudeSpec = (cut) => ({
  title: cut ? "Cut-Extrude" : "Boss-Extrude", icon: cut ? "cut_extrude" : "extrude", command: cut ? "cut_extrude" : "extrude",
  groups: [
    { title: "From", fields: [{ key: "from", type: "select", options: [["sketch", "Sketch Plane"], ["offset", "Offset"]], value: "sketch" },
      { key: "start_offset", type: "number", label: "Offset", icon: "depth", value: 0, showIf: (v) => v.from === "offset" }] },
    { title: "Direction 1", fields: [
      { key: "end_condition", type: "select", options: cut ? END_CUT : END_BOSS, value: "Blind", withReverse: "reverse" },
      { key: "depth", type: "number", label: "D1", icon: "depth", value: 10, step: 1, showIf: (v) => ["Blind", "MidPlane"].includes(v.end_condition) },
      { key: "up_to_face", type: "selection", accepts: ["face"], single: true, label: "Face/Plane", showIf: needsFace },
      { key: "up_to_vertex", type: "selection", accepts: ["vertex"], single: true, label: "Vertex (click a corner)", showIf: (v) => v.end_condition === "UpToVertex" },
      { key: "up_to_offset", type: "number", label: "Offset", icon: "depth", value: 5, step: 1, showIf: (v) => v.end_condition === "OffsetFromSurface" },
      { key: "use_draft", type: "check", label: "Draft On/Off", value: false },
      { key: "draft", type: "number", label: "Angle (°)", icon: "draft", value: 5, step: 1, showIf: (v) => v.use_draft },
      { key: "draft_outward", type: "check", label: "Draft outward", value: false, showIf: (v) => v.use_draft }] },
    { title: "Direction 2", check: "use_dir2", value: false, fields: [
      { key: "direction2_end_condition", type: "select", options: [["Blind", "Blind"], ["ThroughAll", "Through All"]], value: "Blind" },
      { key: "direction2_depth", type: "number", label: "D2", icon: "depth", value: 10, step: 1, showIf: (v) => v.direction2_end_condition === "Blind" }] },
    ...(cut ? [{ title: "Options", fields: [{ key: "flip_side_to_cut", type: "check", label: "Flip side to cut", value: false }] }]
      : [{ title: "Feature Scope", fields: [{ key: "merge", type: "check", label: "Merge result", value: true }] }]),
    { title: "Selected Contours", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Sketch" }] },
  ],
  args: (v) => ({
    sketch: v.sketch?.[0]?.value, depth: v.depth, end_condition: v.end_condition, reverse: !!v.reverse,
    draft: v.use_draft ? v.draft : 0, draft_outward: !!v.draft_outward, start_offset: v.from === "offset" ? v.start_offset : 0,
    direction2_depth: v.use_dir2 ? v.direction2_depth : null, direction2_end_condition: v.use_dir2 ? v.direction2_end_condition : null,
    ...(cut ? { flip_side_to_cut: !!v.flip_side_to_cut } : { merge: v.merge }),
    ...(needsFace(v) ? { up_to: v.up_to_face?.[0]?.value ?? null, up_to_offset: v.up_to_offset ?? 0 } : {}),
    ...(v.end_condition === "UpToVertex" ? { up_to: v.up_to_vertex?.[0]?.value ?? null } : {}),
  }),
  fromParams: (p) => ({
    sketch: [{ label: p.sketch, value: p.sketch }], depth: p.depth, end_condition: p.end_condition, reverse: p.reverse,
    use_draft: !!p.draft, draft: p.draft || 5, draft_outward: p.draft_outward, from: p.start_offset ? "offset" : "sketch",
    start_offset: p.start_offset || 0, use_dir2: !!p.direction2, direction2_depth: p.direction2?.depth ?? 10,
    direction2_end_condition: p.direction2?.end_condition ?? "Blind", merge: p.merge ?? true, flip_side_to_cut: p.flip_side_to_cut,
  }),
  message: "Set the options. Select a sketch if none is selected.",
});

const revolveSpec = (cut) => ({
  title: cut ? "Cut-Revolve" : "Revolve", icon: cut ? "cut_revolve" : "revolve", command: cut ? "cut_revolve" : "revolve",
  groups: [
    { title: "Axis of Revolution", fields: [{ key: "axis", type: "selection", accepts: ["sketchline", "edge"], single: true, label: "Axis",
      hint: "Select a centerline or line (default: first centerline)" }] },
    { title: "Direction 1", fields: [
      { key: "type", type: "select", options: [["OneDirection", "One-Direction"], ["MidPlane", "Mid-Plane"], ["TwoDirection", "Two-Direction"]], value: "OneDirection", withReverse: "reverse" },
      { key: "angle", type: "number", label: "Angle (°)", icon: "depth", value: 360, step: 5 },
      { key: "angle2", type: "number", label: "Dir 2 (°)", icon: "depth", value: 90, step: 5, showIf: (v) => v.type === "TwoDirection" }] },
    ...(cut ? [] : [{ title: "Feature Scope", fields: [{ key: "merge", type: "check", label: "Merge result", value: true }] }]),
    { title: "Selected Contours", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Sketch" }] },
  ],
  args: (v) => ({ sketch: v.sketch?.[0]?.value, axis: v.axis?.[0]?.value ?? null, angle: v.angle, type: v.type,
    angle2: v.type === "TwoDirection" ? v.angle2 : null, reverse: !!v.reverse, ...(cut ? {} : { merge: v.merge }) }),
  fromParams: (p) => ({ sketch: [{ label: p.sketch, value: p.sketch }], axis: p.axis ? [{ label: typeof p.axis === "string" ? p.axis : "Edge", value: p.axis }] : [],
    angle: p.angle, type: p.type, angle2: p.angle2 ?? 90, reverse: p.reverse, merge: p.merge ?? true }),
  message: "Select the sketch and the axis of revolution.",
});

const profileSpec = (kind, cut) => ({
  title: `${cut ? "Cut-" : ""}${kind === "sweep" ? "Sweep" : "Loft"}`, icon: `${cut ? "cut_" : ""}${kind}`, command: `${cut ? "cut_" : ""}${kind}`,
  groups: kind === "sweep"
    ? [{ title: "Profile and Path", fields: [
        { key: "profile", type: "selection", accepts: ["sketch"], single: true, label: "Profile (closed sketch)" },
        { key: "path", type: "selection", accepts: ["sketch"], single: true, label: "Path (sketch)" }] },
      { title: "Options", fields: [{ key: "frenet", type: "check", label: "Follow path curvature (Frenet)", value: false },
        ...(cut ? [] : [{ key: "twist", type: "number", label: "Twist along path (°)", icon: "circular_pattern", value: 0, step: 15 },
          { key: "merge", type: "check", label: "Merge result", value: true }])] },
      ...(cut ? [] : [{ title: "Guide Curves", fields: [{ key: "guide", type: "selection", accepts: ["sketch"], single: true, label: "Guide curve (optional)" }] }])]
    : [{ title: "Profiles", fields: [{ key: "profiles", type: "selection", accepts: ["sketch"], label: "Profiles (in order)" }] },
      ...(cut ? [] : [{ title: "Guide Curves", fields: [{ key: "guides", type: "selection", accepts: ["sketch"], label: "Guide curves (2 or more, optional)" }] },
        { title: "Centerline Parameters", fields: [{ key: "centerline", type: "selection", accepts: ["sketch"], single: true, label: "Centerline (optional)" }] }]),
      { title: "Options", fields: [{ key: "ruled", type: "check", label: "Ruled (straight) faces", value: false },
        ...(cut ? [] : [{ key: "merge", type: "check", label: "Merge result", value: true }])] }],
  args: (v) => kind === "sweep"
    ? { profile: v.profile?.[0]?.value, path: v.path?.[0]?.value, frenet: !!v.frenet, ...(cut ? {} : { merge: v.merge, twist: v.twist || 0, guide: v.guide?.[0]?.value ?? null }) }
    : { profiles: (v.profiles || []).map((i) => i.value), ruled: !!v.ruled, ...(cut ? {} : { merge: v.merge, centerline: v.centerline?.[0]?.value ?? null,
      guides: (v.guides || []).map((i) => i.value) }) },
  fromParams: (p) => kind === "sweep"
    ? { profile: [{ label: p.sketch, value: p.sketch }], path: [{ label: p.path, value: p.path }], frenet: p.frenet, merge: p.merge ?? true,
      twist: p.twist || 0, guide: p.guide ? [{ label: p.guide, value: p.guide }] : [] }
    : { profiles: p.profiles.map((s) => ({ label: s, value: s })), ruled: p.ruled, merge: p.merge ?? true,
      centerline: p.centerline ? [{ label: p.centerline, value: p.centerline }] : [],
      guides: (p.guides || []).map((g) => ({ label: g, value: g })) },
  message: kind === "sweep" ? "Select the profile sketch and the path sketch (in the tree or graphics)." : "Select two or more profile sketches in order.",
});

const ISO_SIZES = [...["M2", "M2.5", "M3", "M4", "M5", "M6", "M8", "M10", "M12", "M16", "M20", "M24"].map((s) => [s, `ISO ${s}`]),
  ...["#4-40", "#6-32", "#8-32", "#10-24", "1/4-20", "5/16-18", "3/8-16", "1/2-13"].map((s) => [s, `ANSI ${s}`])];
const B1_SPECS = {
  rib: {
    title: "Rib", icon: "rib", command: "rib",
    groups: [{ title: "Parameters", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Rib sketch (open line)" },
      { key: "thickness", type: "number", label: "Thickness", icon: "depth", value: 3, step: 0.5 }, { key: "flip", type: "check", label: "Flip material side", value: false }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, thickness: v.thickness, flip: !!v.flip }),
    fromParams: (p) => ({ ...p, sketch: [{ label: p.sketch, value: p.sketch }] }),
    message: "Select an open sketch line drawn across the region to brace.",
  },
  hole_wizard: {
    title: "Hole Specification", icon: "hole", command: "hole_wizard",
    groups: [{ title: "Hole Type", fields: [{ key: "hole_type", type: "radio", value: "clearance", options: [["clearance", "Hole (clearance)"], ["tapped", "Straight Tap"], ["counterbore", "Counterbore"], ["countersink", "Countersink"]] }] },
      { title: "Hole Specifications (ISO)", fields: [{ key: "size", type: "select", value: "M6", options: ISO_SIZES }] },
      { title: "End Condition", fields: [{ key: "end_condition", type: "select", value: "ThroughAll", options: [["ThroughAll", "Through All"], ["Blind", "Blind"]] },
        { key: "depth", type: "number", label: "Depth", icon: "depth", value: 10, step: 1, showIf: (v) => v.end_condition === "Blind" }] },
      { title: "Positions", fields: [{ key: "face", type: "selection", accepts: ["face"], single: true, label: "Face" }, { key: "points", type: "points", label: "Hole centres" }] }],
    args: (v) => ({ face: v.face?.[0]?.value, points: v.points || [], size: v.size, hole_type: v.hole_type, end_condition: v.end_condition, depth: v.depth }),
    fromParams: (p) => ({ ...p, face: [{ kind: "face", label: "Face<1>", value: p.face }] }),
    message: "Pick the hole type and ISO size, select a planar face and click to place hole centres.",
  },
  thread: {
    title: "Thread", icon: "thread", command: "thread",
    groups: [{ title: "Thread Location", fields: [{ key: "face", type: "selection", accepts: ["face"], single: true, label: "Cylindrical face" },
      { key: "pitch", type: "number", label: "Pitch", icon: "depth", value: 1.25, step: 0.25 }, { key: "length", type: "number", label: "Length", icon: "depth", value: 10, step: 1 },
      { key: "internal", type: "check", label: "Internal (hole)", value: false }] }],
    args: (v) => ({ face: v.face?.[0]?.value, pitch: v.pitch, length: v.length, internal: !!v.internal }),
    fromParams: (p) => ({ ...p, face: [{ kind: "face", label: "Face<1>", value: p.face }] }),
    message: "Select a cylindrical face (shaft or hole) and set the pitch and length (ISO 60° profile).",
  },
};

B1_SPECS.dome = {
  title: "Dome", icon: "revolve", command: "dome",
  groups: [{ title: "Parameters", fields: [{ key: "face", type: "selection", accepts: ["face"], single: true, label: "Circular planar face" },
    { key: "height", type: "number", label: "Height", icon: "depth", value: 5, step: 0.5 }, { key: "reverse", type: "check", label: "Reverse (dent)", value: false }] }],
  args: (v) => ({ face: v.face?.[0]?.value, height: v.height, reverse: !!v.reverse }),
  fromParams: (p) => ({ ...p, face: [{ kind: "face", label: "Face<1>", value: p.face }] }),
  message: "Select a circular planar face and set the dome height.",
};

const SHEET_SPECS = {
  ...B1_SPECS,
  structural_member: {
    title: "Structural Member", icon: "weldment", command: "structural_member",
    groups: [{ title: "Selections", fields: [
      { key: "profile", type: "select", value: "square_tube", options: [["square_tube", "Square tube"], ["rect_tube", "Rectangular tube"], ["pipe", "Pipe"], ["angle", "Angle"], ["solid_bar", "Solid bar"]] },
      { key: "width", type: "number", label: "Width / OD", icon: "depth", value: 20, step: 1 },
      { key: "height", type: "number", label: "Height", icon: "depth", value: 20, step: 1, showIf: (v) => ["rect_tube", "angle", "solid_bar"].includes(v.profile) },
      { key: "thickness", type: "number", label: "Wall", icon: "shell", value: 2, step: 0.5, showIf: (v) => v.profile !== "solid_bar" },
      { key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Layout sketch (path segments)" }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, profile: v.profile, width: v.width, height: ["rect_tube", "angle", "solid_bar"].includes(v.profile) ? v.height : v.width, thickness: v.thickness }),
    fromParams: (p) => ({ ...p, sketch: [{ label: p.sketch, value: p.sketch }] }),
    message: "Choose a profile and select the layout sketch; a member is placed on every straight segment.",
  },
  base_flange: {
    title: "Base Flange", icon: "base_flange", command: "base_flange",
    groups: [{ title: "Sheet Metal Parameters", fields: [
      { key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Sketch (closed)" },
      { key: "thickness", type: "number", label: "Thickness", icon: "depth", value: 2, step: 0.5 },
      { key: "bend_radius", type: "number", label: "Bend radius", icon: "fillet", value: 2, step: 0.5 },
      { key: "k_factor", type: "number", label: "K-Factor", icon: "draft", value: 0.5, step: 0.05 },
      { key: "reverse", type: "check", label: "Reverse direction", value: false }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, thickness: v.thickness, bend_radius: v.bend_radius, k_factor: v.k_factor, reverse: !!v.reverse }),
    fromParams: (p) => ({ ...p, sketch: [{ label: p.sketch, value: p.sketch }] }),
    message: "Select a closed sketch and set the sheet thickness, bend radius and K-factor.",
  },
  edge_flange: {
    title: "Edge-Flange", icon: "edge_flange", command: "edge_flange",
    groups: [{ title: "Flange Parameters", fields: [
      { key: "edge", type: "selection", accepts: ["edge"], single: true, label: "Edge (on the sheet face)" },
      { key: "length", type: "number", label: "Length", icon: "depth", value: 20, step: 1 },
      { key: "angle", type: "number", label: "Angle (°)", icon: "draft", value: 90, step: 5 },
      { key: "flip", type: "check", label: "Bend from the other face", value: false }] }],
    args: (v) => ({ edge: v.edge?.[0]?.value, length: v.length, angle: v.angle, flip: !!v.flip }),
    fromParams: (p) => ({ ...p, edge: [{ kind: "edge", label: "Edge<1>", value: p.edge }] }),
    message: "Select a straight edge of the sheet's top or bottom face.",
  },
  vertex_chamfer: {
    title: "Chamfer (Vertex)", icon: "vertex_chamfer", command: "vertex_chamfer",
    groups: [{ title: "Items to Chamfer", fields: [{ key: "vertex", type: "selection", accepts: ["vertex"], single: true, label: "Corner vertex" }] },
      { title: "Chamfer Parameters", fields: [{ key: "equal", type: "check", label: "Equal distance", value: true },
        { key: "d1", type: "number", label: "Distance 1", icon: "depth", value: 5, step: 0.5 },
        { key: "d2", type: "number", label: "Distance 2", icon: "depth", value: 5, step: 0.5, showIf: (v) => !v.equal },
        { key: "d3", type: "number", label: "Distance 3", icon: "depth", value: 5, step: 0.5, showIf: (v) => !v.equal }] }],
    args: (v) => ({ vertex: v.vertex?.[0]?.value, d1: v.d1, ...(v.equal ? {} : { d2: v.d2, d3: v.d3 }) }),
    fromParams: (p) => ({ vertex: [{ kind: "vertex", label: "Vertex", value: p.vertex }], d1: p.d1, d2: p.d2 ?? p.d1, d3: p.d3 ?? p.d1, equal: p.d2 == null }),
    message: "Select a corner vertex (where three edges meet).",
  },
  face_fillet: {
    title: "Fillet (Face)", icon: "face_fillet", command: "face_fillet",
    groups: [{ title: "Items to Fillet", fields: [{ key: "faces1", type: "selection", accepts: ["face"], label: "Face set 1" },
      { key: "faces2", type: "selection", accepts: ["face"], label: "Face set 2" },
      { key: "radius", type: "number", label: "Radius", icon: "fillet", value: 4, step: 0.5 }] }],
    args: (v) => ({ faces1: (v.faces1 || []).map((i) => i.value), faces2: (v.faces2 || []).map((i) => i.value), radius: v.radius }),
    fromParams: (p) => ({ radius: p.radius, faces1: [], faces2: [] }),
    message: "Select face set 1, then face set 2 (they must meet along an edge).",
  },
  full_round_fillet: {
    title: "Fillet (Full Round)", icon: "full_round_fillet", command: "full_round_fillet",
    groups: [{ title: "Items to Fillet", fields: [{ key: "side1", type: "selection", accepts: ["face"], single: true, label: "Side face set 1" },
      { key: "center", type: "selection", accepts: ["face"], single: true, label: "Center face set" },
      { key: "side2", type: "selection", accepts: ["face"], single: true, label: "Side face set 2" }] }],
    args: (v) => ({ side1: v.side1?.[0]?.value, center: v.center?.[0]?.value, side2: v.side2?.[0]?.value }),
    fromParams: () => ({}),
    message: "Select a side face, the centre face to round over, then the opposite side face.",
  },
  wrap: {
    title: "Wrap", icon: "wrap", command: "wrap",
    groups: [{ title: "Wrap Type", fields: [{ key: "type", type: "radio", value: "emboss", options: [["emboss", "Emboss"], ["deboss", "Deboss"], ["scribe", "Scribe"]] }] },
      { title: "Wrap Parameters", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Source sketch" },
        { key: "face", type: "selection", accepts: ["face"], single: true, label: "Cylindrical face" },
        { key: "depth", type: "number", label: "Thickness", icon: "depth", value: 1, step: 0.25, showIf: (v) => v.type !== "scribe" }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, face: v.face?.[0]?.value, type: v.type, depth: v.depth }),
    fromParams: (p) => ({ ...p, sketch: [{ label: p.sketch, value: p.sketch }], face: [{ kind: "face", label: "Face<1>", value: p.face }] }),
    message: "Select a sketch (on a plane tangent to / in front of the cylinder) and the cylindrical face.",
  },
  hem: {
    title: "Hem", icon: "hem", command: "hem",
    groups: [{ title: "Edges", fields: [{ key: "edge", type: "selection", accepts: ["edge"], single: true, label: "Edge (on the sheet face)" }] },
      { title: "Type and Size", fields: [{ key: "type", type: "radio", value: "closed", options: [["closed", "Closed"], ["open", "Open"]] },
        { key: "length", type: "number", label: "Length", icon: "depth", value: 10, step: 1 },
        { key: "gap", type: "number", label: "Gap distance", icon: "depth", value: 2, step: 0.5, showIf: (v) => v.type === "open" },
        { key: "flip", type: "check", label: "Reverse direction", value: false }] }],
    args: (v) => ({ edge: v.edge?.[0]?.value, type: v.type, length: v.length, gap: v.type === "open" ? v.gap : null, flip: !!v.flip }),
    fromParams: (p) => ({ ...p, edge: [{ kind: "edge", label: "Edge<1>", value: p.edge }] }),
    message: "Select a straight edge of the sheet face to hem.",
  },
  rip: {
    title: "Rip", icon: "rip", command: "rip",
    groups: [{ title: "Rip Parameters", fields: [{ key: "edges", type: "selection", accepts: ["edge"], label: "Corner edges to rip" },
      { key: "gap", type: "number", label: "Rip gap", icon: "depth", value: 0.1, step: 0.05 }] }],
    args: (v) => ({ edges: (v.edges || []).map((i) => i.value), gap: v.gap }),
    fromParams: (p) => ({ ...p, edges: [] }),
    message: "Select the straight corner edges where the walls should come apart.",
  },
  unfold: {
    title: "Unfold", icon: "unfold", command: "unfold",
    groups: [{ title: "Selections", fields: [{ key: "bends", type: "selection", accepts: ["feature"], label: "Bends to unfold (tree: edge flanges, hems, sketched bends, jogs)" }] }],
    args: (v) => ({ bends: (v.bends || []).map((i) => i.value) }),
    fromParams: (p) => ({ bends: (p.bends || []).map((b) => ({ label: b, value: b })) }),
    message: "Select the bend features in the FeatureManager tree first (Ctrl+click for several), then Unfold. Cut across them, then Fold.",
  },
  fold: {
    title: "Fold", icon: "fold", command: "fold",
    groups: [{ title: "Selections", fields: [{ key: "bends", type: "selection", accepts: ["feature"], label: "Bends to fold (none: all unfolded bends)" }] }],
    args: (v) => ({ bends: (v.bends || []).map((i) => i.value) }),
    fromParams: (p) => ({ bends: (p.bends || []).map((b) => ({ label: b, value: b })) }),
    message: "OK folds every unfolded bend, or select the ones to fold in the tree. Cuts across them are wrapped onto the bends.",
  },
  vent: {
    title: "Vent", icon: "vent", command: "vent",
    groups: [{ title: "Boundary", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Sketch with the vent outline" }] },
      { title: "Ribs", fields: [{ key: "ribs", type: "number", label: "Number of ribs", icon: "linear_pattern", value: 3, step: 1 },
        { key: "rib_width", type: "number", label: "Rib width", icon: "depth", value: 2, step: 0.5 },
        { key: "rib_angle", type: "number", label: "Rib angle (°)", icon: "draft", value: 0, step: 15 }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, ribs: Math.max(0, Math.round(v.ribs)), rib_width: v.rib_width, rib_angle: v.rib_angle }),
    fromParams: (p) => ({ ...p, sketch: [{ label: p.sketch, value: p.sketch }] }),
    message: "Sketch the vent outline on the sheet face, select the sketch, then set the ribs.",
  },
  jog: {
    title: "Jog", icon: "jog", command: "jog",
    groups: [{ title: "Selections", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Sketch with one jog line" }] },
      { title: "Jog Offset", fields: [{ key: "distance", type: "number", label: "Flat distance between bends", icon: "depth", value: 15, step: 1 },
        { key: "angle", type: "number", label: "Jog angle (°)", icon: "draft", value: 90, step: 5 },
        { key: "bend_radius", type: "number", label: "Bend radius", icon: "depth", value: 2, step: 0.5 },
        { key: "flip", type: "check", label: "Reverse direction", value: false }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, distance: v.distance, angle: v.angle, bend_radius: v.bend_radius, flip: !!v.flip }),
    fromParams: (p) => ({ ...p, sketch: [{ label: p.sketch, value: p.sketch }] }),
    message: "Sketch one line across the sheet face (the first bend), then set the offset.",
  },
  corner_relief: {
    title: "Corner Relief", icon: "corner_relief", command: "corner_relief",
    groups: [{ title: "Corners", fields: [{ key: "corner", type: "selection", accepts: ["vertex"], single: true, label: "Corner vertex" }] },
      { title: "Relief Options", fields: [{ key: "type", type: "radio", value: "rectangular", options: [["rectangular", "Rectangular"], ["circular", "Circular"]] },
        { key: "size", type: "number", label: "Relief size", icon: "depth", value: 4, step: 0.5 }] }],
    args: (v) => ({ point: v.corner?.[0]?.value, size: v.size, type: v.type }),
    fromParams: (p) => ({ ...p, corner: [{ kind: "vertex", label: "Corner", value: p.point }] }),
    message: "Select the corner vertex to relieve.",
  },
  dimple: {
    title: "Forming Tool - Dimple", icon: "dimple", command: "forming_tool_dimple",
    groups: [{ title: "Placement", fields: [{ key: "face", type: "selection", accepts: ["face"], single: true, label: "Click the sheet face where it goes" }] },
      { title: "Dimple", fields: [{ key: "diameter", type: "number", label: "Diameter", icon: "depth", value: 20, step: 1 },
        { key: "height", type: "number", label: "Height", icon: "depth", value: 5, step: 0.5 },
        { key: "reverse", type: "check", label: "Reverse direction", value: false }] }],
    args: (v) => ({ face: v.face?.[0]?.value, point: v.face?.[0]?.point, diameter: v.diameter, height: v.height, reverse: !!v.reverse }),
    fromParams: (p) => ({ ...p, face: [{ kind: "face", label: "Face<1>", value: p.face, point: p.point }] }),
    message: "Click the point on a sheet face where the dimple is pressed.",
  },
  louver: {
    title: "Forming Tool - Louver", icon: "louver", command: "forming_tool_louver",
    groups: [{ title: "Placement", fields: [{ key: "face", type: "selection", accepts: ["face"], single: true, label: "Click the sheet face where it goes" }] },
      { title: "Louver", fields: [{ key: "length", type: "number", label: "Length", icon: "depth", value: 30, step: 1 },
        { key: "width", type: "number", label: "Width", icon: "depth", value: 6, step: 0.5 },
        { key: "height", type: "number", label: "Height", icon: "depth", value: 4, step: 0.5 },
        { key: "angle", type: "number", label: "Rotation (°)", icon: "draft", value: 0, step: 15 }] }],
    args: (v) => ({ face: v.face?.[0]?.value, point: v.face?.[0]?.point, length: v.length, width: v.width, height: v.height, angle: v.angle }),
    fromParams: (p) => ({ ...p, face: [{ kind: "face", label: "Face<1>", value: p.face, point: p.point }] }),
    message: "Click the point on a sheet face where the louver goes.",
  },
  lance: {
    title: "Forming Tool - Bridge Lance", icon: "lance", command: "forming_tool_lance",
    groups: [{ title: "Placement", fields: [{ key: "face", type: "selection", accepts: ["face"], single: true, label: "Click the sheet face where it goes" }] },
      { title: "Lance", fields: [{ key: "length", type: "number", label: "Length", icon: "depth", value: 30, step: 1 },
        { key: "width", type: "number", label: "Width", icon: "depth", value: 6, step: 0.5 },
        { key: "height", type: "number", label: "Height", icon: "depth", value: 3, step: 0.5 },
        { key: "ramp", type: "number", label: "Ramp length", icon: "depth", value: 3, step: 0.5 },
        { key: "angle", type: "number", label: "Rotation (°)", icon: "draft", value: 0, step: 15 }] }],
    args: (v) => ({ face: v.face?.[0]?.value, point: v.face?.[0]?.point, length: v.length, width: v.width, height: v.height, ramp: v.ramp, angle: v.angle }),
    fromParams: (p) => ({ ...p, face: [{ kind: "face", label: "Face<1>", value: p.face, point: p.point }] }),
    message: "Click the point on a sheet face where the bridge lance goes.",
  },
  extruded_flange: {
    title: "Forming Tool - Extruded Flange", icon: "extruded_flange", command: "forming_tool_extruded_flange",
    groups: [{ title: "Placement", fields: [{ key: "face", type: "selection", accepts: ["face"], single: true, label: "Click the sheet face where it goes" }] },
      { title: "Extruded Flange", fields: [{ key: "diameter", type: "number", label: "Inner diameter", icon: "depth", value: 10, step: 0.5 },
        { key: "height", type: "number", label: "Flange height", icon: "depth", value: 5, step: 0.5 },
        { key: "reverse", type: "check", label: "Reverse direction", value: false }] }],
    args: (v) => ({ face: v.face?.[0]?.value, point: v.face?.[0]?.point, diameter: v.diameter, height: v.height, reverse: !!v.reverse }),
    fromParams: (p) => ({ ...p, face: [{ kind: "face", label: "Face<1>", value: p.face, point: p.point }] }),
    message: "Click the point on a sheet face where the flanged hole goes.",
  },
  form_rib: {
    title: "Forming Tool - Rib", icon: "form_rib", command: "forming_tool_rib",
    groups: [{ title: "Placement", fields: [{ key: "face", type: "selection", accepts: ["face"], single: true, label: "Click the sheet face where it goes" }] },
      { title: "Rib", fields: [{ key: "length", type: "number", label: "Length", icon: "depth", value: 40, step: 1 },
        { key: "width", type: "number", label: "Width", icon: "depth", value: 8, step: 0.5 },
        { key: "height", type: "number", label: "Height", icon: "depth", value: 2.5, step: 0.5 },
        { key: "angle", type: "number", label: "Rotation (°)", icon: "draft", value: 0, step: 15 },
        { key: "reverse", type: "check", label: "Reverse direction", value: false }] }],
    args: (v) => ({ face: v.face?.[0]?.value, point: v.face?.[0]?.point, length: v.length, width: v.width, height: v.height, angle: v.angle, reverse: !!v.reverse }),
    fromParams: (p) => ({ ...p, face: [{ kind: "face", label: "Face<1>", value: p.face, point: p.point }] }),
    message: "Click the point on a sheet face where the rib is centred.",
  },
  miter_flange: {
    title: "Miter Flange", icon: "miter_flange", command: "miter_flange",
    groups: [{ title: "Miter Parameters", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Profile sketch (open lines)" },
      { key: "edges", type: "selection", accepts: ["edge"], label: "Edges (connected chain, first at the profile)" },
      { key: "flip", type: "check", label: "Flip material side", value: false }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, edges: (v.edges || []).map((i) => i.value), flip: !!v.flip }),
    fromParams: (p) => ({ sketch: [{ label: p.sketch, value: p.sketch }], flip: p.flip,
      edges: (p.edges || [p.edge]).map((e, i) => ({ kind: "edge", label: `Edge<${i + 1}>`, value: e })) }),
    message: "Sketch the flange profile on a plane at one end of an edge (perpendicular to it), then pick the edge.",
  },
  sketched_bend: {
    title: "Sketched Bend", icon: "sketched_bend", command: "sketched_bend",
    groups: [{ title: "Bend Parameters", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Sketch with one bend line" },
      { key: "angle", type: "number", label: "Bend angle (°)", icon: "draft", value: 90, step: 5 },
      { key: "bend_radius", type: "number", label: "Bend radius", icon: "depth", value: 2, step: 0.5 },
      { key: "flip", type: "check", label: "Reverse direction", value: false }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, angle: v.angle, bend_radius: v.bend_radius, flip: !!v.flip }),
    fromParams: (p) => ({ ...p, sketch: [{ label: p.sketch, value: p.sketch }] }),
    message: "Sketch a single line across the sheet face (the bend centreline), then select that sketch.",
  },
  revolved_surface: {
    title: "Surface-Revolve", icon: "revolved_surface", command: "revolved_surface",
    groups: [{ title: "Revolve Parameters", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Profile sketch" },
      { key: "axis", type: "selection", accepts: ["sketchline", "edge", "axis"], single: true, label: "Axis of revolution", withAxes: "axisname" },
      { key: "angle", type: "number", label: "Angle (°)", icon: "draft", value: 360, step: 15 }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, axis: v.axis?.[0]?.value ?? v.axisname ?? "Y", angle: v.angle }),
    fromParams: (p) => ({ ...p, sketch: [{ label: p.sketch, value: p.sketch }], ...(typeof p.axis === "string" && p.axis.length === 1 ? { axisname: p.axis, axis: [] } : { axis: [{ label: String(p.axis), value: p.axis }] }) }),
    message: "Select the profile sketch and an axis (centerline, edge or X/Y/Z).",
  },
  swept_surface: {
    title: "Surface-Sweep", icon: "swept_surface", command: "swept_surface",
    groups: [{ title: "Profile and Path", fields: [{ key: "profile", type: "selection", accepts: ["sketch"], single: true, label: "Profile (open or closed curve)" },
      { key: "path", type: "selection", accepts: ["sketch"], single: true, label: "Path (sketch, 3D sketch, helix)" }] }],
    args: (v) => ({ profile: v.profile?.[0]?.value, path: v.path?.[0]?.value }),
    fromParams: (p) => ({ profile: [{ label: p.profile, value: p.profile }], path: [{ label: p.path, value: p.path }] }),
    message: "Select the profile curve, then the path.",
  },
  boundary_surface: {
    title: "Boundary-Surface", icon: "boundary_surface", command: "boundary_surface",
    groups: [{ title: "Direction 1", fields: [{ key: "d1", type: "selection", accepts: ["sketch"], label: "Curves (sketches / 3D sketches)" }] },
      { title: "Direction 2", fields: [{ key: "d2", type: "selection", accepts: ["sketch"], label: "Curves (optional)" }] }],
    args: (v) => ({ direction1: (v.d1 || []).map((i) => i.value), direction2: (v.d2 || []).map((i) => i.value) }),
    fromParams: (p) => ({ d1: p.direction1.map((s) => ({ label: s, value: s })), d2: (p.direction2 || []).map((s) => ({ label: s, value: s })) }),
    message: "Select the direction 1 curves, then (optionally) the direction 2 curves; together they must form a closed loop.",
  },
  lofted_surface: {
    title: "Surface-Loft", icon: "lofted_surface", command: "lofted_surface",
    groups: [{ title: "Profiles", fields: [{ key: "profiles", type: "selection", accepts: ["sketch"], label: "Profiles (in order)" },
      { key: "ruled", type: "check", label: "Ruled (straight between profiles)", value: false }] }],
    args: (v) => ({ profiles: (v.profiles || []).map((i) => i.value), ruled: !!v.ruled }),
    fromParams: (p) => ({ profiles: p.profiles.map((s) => ({ label: s, value: s })), ruled: !!p.ruled }),
    message: "Select two or more profile curves in order.",
  },
  planar_surface: {
    title: "Planar Surface", icon: "planar_surface", command: "planar_surface",
    groups: [{ title: "Bounding Entities", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Closed sketch" }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value }),
    fromParams: (p) => ({ sketch: [{ label: p.sketch, value: p.sketch }] }),
    message: "Select a sketch with closed contours.",
  },
  filled_surface: {
    title: "Filled Surface", icon: "filled_surface", command: "filled_surface",
    groups: [{ title: "Patch Boundary", fields: [{ key: "boundary", type: "selection", accepts: ["sketch"], single: true, label: "Closed boundary (sketch / 3D sketch)" }] }],
    args: (v) => ({ boundary: v.boundary?.[0]?.value }),
    fromParams: (p) => ({ boundary: [{ label: p.boundary, value: p.boundary }] }),
    message: "Select a closed boundary curve; it may be non-planar (3D sketch).",
  },
  offset_surface: {
    title: "Offset Surface", icon: "offset_surface", command: "offset_surface",
    groups: [{ title: "Offset Parameters", fields: [{ key: "surface", type: "selection", accepts: ["feature"], single: true, label: "Surface body (tree)" },
      { key: "faces", type: "selection", accepts: ["face"], label: "…or model faces" },
      { key: "distance", type: "number", label: "Distance", icon: "depth", value: 5, step: 1 }] }],
    args: (v) => ({ surface: v.surface?.[0]?.value ?? null, faces: (v.faces || []).map((i) => i.value), distance: v.distance }),
    fromParams: (p) => ({ distance: p.distance, ...(p.surface ? { surface: [{ label: p.surface, value: p.surface }] } : {}) }),
    message: "Select a surface body in the tree or model faces, and the offset distance (0 = copy).",
  },
  knit_surface: {
    title: "Knit Surface", icon: "knit_surface", command: "knit_surface",
    groups: [{ title: "Selections", fields: [{ key: "surfaces", type: "selection", accepts: ["feature"], label: "Surfaces to knit (tree)" },
      { key: "form_solid", type: "check", label: "Create solid", value: false }] }],
    args: (v) => ({ surfaces: (v.surfaces || []).map((i) => i.value), form_solid: !!v.form_solid }),
    fromParams: (p) => ({ surfaces: p.surfaces.map((s) => ({ label: s, value: s })), form_solid: !!p.form_solid }),
    message: "Select the surface bodies in the FeatureManager tree; tick Create solid for a closed knit.",
  },
  trim_surface: {
    title: "Trim Surface", icon: "trim_surface", command: "trim_surface",
    groups: [{ title: "Selections", fields: [{ key: "surface", type: "selection", accepts: ["feature"], single: true, label: "Surface to trim (tree)" },
      { key: "tool", type: "selection", accepts: ["plane", "face"], single: true, label: "Trim tool (plane / planar face)" },
      { key: "flip", type: "check", label: "Keep the other side", value: false }] }],
    args: (v) => ({ surface: v.surface?.[0]?.value, tool: v.tool?.[0]?.value ?? "Right", flip: !!v.flip }),
    fromParams: (p) => ({ surface: [{ label: p.surface, value: p.surface }], tool: [{ label: typeof p.tool === "string" ? p.tool : "Face", value: p.tool }] }),
    message: "Select the surface body and a plane; the side the plane normal points to is kept.",
  },
  end_cap: {
    title: "End Cap", icon: "end_cap", command: "end_cap",
    groups: [{ title: "Parameters", fields: [{ key: "member", type: "selection", accepts: ["feature"], single: true, label: "Structural member (tree)" },
      { key: "thickness", type: "number", label: "Thickness", icon: "depth", value: 3, step: 0.5 }] }],
    args: (v) => ({ member: v.member?.[0]?.value, thickness: v.thickness }),
    fromParams: (p) => ({ member: [{ label: p.member, value: p.member }], thickness: p.thickness }),
    message: "Select a structural member (tube or pipe) in the tree.",
  },
  gusset: {
    title: "Gusset", icon: "gusset", command: "gusset",
    groups: [{ title: "Supporting Faces", fields: [{ key: "faces", type: "selection", accepts: ["face"], label: "Two perpendicular faces" }] },
      { title: "Profile", fields: [{ key: "d1", type: "number", label: "Leg d1", icon: "depth", value: 20, step: 1 },
        { key: "d2", type: "number", label: "Leg d2", icon: "depth", value: 20, step: 1 },
        { key: "thickness", type: "number", label: "Thickness", icon: "depth", value: 3, step: 0.5 },
        { key: "position", type: "number", label: "Position along edge (0-1)", icon: "depth", value: 0.5, step: 0.1 }] }],
    args: (v) => ({ face1: v.faces?.[0]?.value, face2: v.faces?.[1]?.value, d1: v.d1, d2: v.d2, thickness: v.thickness, position: v.position }),
    fromParams: (p) => ({ ...p, faces: [{ kind: "face", label: "Face<1>", value: p.face1 }, { kind: "face", label: "Face<2>", value: p.face2 }] }),
    message: "Select the two faces of the inside corner.",
  },
  extruded_surface: {
    title: "Surface-Extrude", icon: "surface", command: "extruded_surface",
    groups: [{ title: "Direction 1", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Sketch (open or closed)" },
      { key: "depth", type: "number", label: "Depth", icon: "depth", value: 20, step: 1 }, { key: "reverse", type: "check", label: "Reverse", value: false }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, depth: v.depth, reverse: !!v.reverse }),
    fromParams: (p) => ({ ...p, sketch: [{ label: p.sketch, value: p.sketch }] }),
    message: "Select a sketch; its curves become a surface body.",
  },
  thicken: {
    title: "Thicken", icon: "thicken", command: "thicken",
    groups: [{ title: "Thicken Parameters", fields: [{ key: "surface", type: "selection", accepts: ["feature"], single: true, label: "Surface feature (tree)" },
      { key: "thickness", type: "number", label: "Thickness", icon: "depth", value: 2, step: 0.5 }, { key: "flip", type: "check", label: "Thicken other side", value: false }] }],
    args: (v) => ({ surface: v.surface?.[0]?.value, thickness: v.thickness, flip: !!v.flip }),
    fromParams: (p) => ({ ...p, surface: [{ label: p.surface, value: p.surface }] }),
    message: "Select the surface feature in the FeatureManager tree.",
  },
};

// a mate entity can be a picked face or its component's standard plane: "Part1-1/Face3" -> "Part1-1/Front Plane"
const asPlane = (face, plane) => (plane && face ? `${typeof face === "string" ? face.split("/")[0] : face.component}/${plane}` : face);

const faceItems = (p) => (p || []).map((r, i) => ({ kind: "face", label: `Face<${i + 1}>`, value: r }));
const EDIT_SPECS = { // body / face editing and curves
  split_body: {
    title: "Split", icon: "cut_extrude", command: "split_body",
    groups: [{ title: "Trim Tools", fields: [{ key: "tool", type: "selection", accepts: ["plane", "face", "feature"], single: true, label: "Plane, planar face or surface feature" }] },
      { title: "Resulting Bodies", fields: [{ key: "keep", type: "radio", value: "all", options: [["all", "Keep all pieces (as bodies)"], ["above", "Keep the side the normal points to"], ["below", "Keep the other side"]] }] }],
    args: (v) => ({ tool: v.tool?.[0]?.value, keep: v.keep }),
    fromParams: (p) => ({ tool: [{ label: typeof p.tool === "string" ? p.tool : "Face<1>", value: p.tool }], keep: p.keep }),
    message: "Select the plane, planar face or surface body to split with.",
  },
  indent: {
    title: "Indent", icon: "dimple", command: "indent",
    groups: [{ title: "Selections", fields: [{ key: "target", type: "number", label: "Target body (index)", icon: "depth", value: 0, step: 1 },
      { key: "tool", type: "number", label: "Tool body (index)", icon: "depth", value: 1, step: 1 }] },
      { title: "Parameters", fields: [{ key: "thickness", type: "number", label: "Thickness", icon: "depth", value: 1, step: 0.25 },
        { key: "clearance", type: "number", label: "Clearance", icon: "depth", value: 0, step: 0.25 },
        { key: "cut", type: "check", label: "Cut (remove the tool's shape only)", value: false }] }],
    args: (v) => ({ target: Math.round(v.target), tool: Math.round(v.tool), thickness: v.thickness, clearance: v.clearance, cut: !!v.cut }),
    fromParams: (p) => ({ ...p }),
    message: "Give the target body and the tool body (multibody part: bodies counted from 0).",
  },
  intersect: {
    title: "Intersect", icon: "knit_surface", command: "intersect",
    groups: [{ title: "Selections", fields: [{ key: "tools", type: "selection", accepts: ["feature", "plane", "face"], label: "Surfaces and planes (with the solid bodies)" }] },
      { title: "Regions to Exclude", fields: [{ key: "exclude", type: "table", label: "Region numbers (from the feature's result)", columns: ["#"] }] },
      { title: "Options", fields: [{ key: "merge", type: "check", label: "Merge result", value: true }] }],
    args: (v) => ({ tools: (v.tools || []).map((i) => i.value), exclude: (v.exclude || []).map((r) => r[0]).filter((x) => Number.isFinite(x)), merge: !!v.merge }),
    fromParams: (p) => ({ tools: (p.tools || []).map((t) => ({ label: typeof t === "string" ? t : "Face<1>", value: t })), exclude: (p.exclude || []).map((i) => [i]), merge: p.merge }),
    message: "Select surface bodies (tree) and planes; every region they enclose with the solids becomes a body.",
  },
  cut_with_surface: {
    title: "Cut With Surface", icon: "cut_extrude", command: "cut_with_surface",
    groups: [{ title: "Surface Cut Parameters", fields: [{ key: "surface", type: "selection", accepts: ["feature", "plane", "face"], single: true, label: "Surface feature or plane" },
      { key: "flip", type: "check", label: "Flip side to cut", value: false }] }],
    args: (v) => ({ surface: v.surface?.[0]?.value, flip: !!v.flip }),
    fromParams: (p) => ({ surface: [{ label: typeof p.tool === "string" ? p.tool : "Face<1>", value: p.tool }], flip: p.flip }),
    message: "Select the surface body (in the tree) or plane; the material its normal points to is removed.",
  },
  delete_face: {
    title: "Delete Face", icon: "trash", command: "delete_face",
    groups: [{ title: "Selections", fields: [{ key: "faces", type: "selection", accepts: ["face"], label: "Faces to delete" }] },
      { title: "Options", fields: [{ key: "mode", type: "radio", value: "patch", options: [["patch", "Delete and Patch"], ["delete", "Delete (leave a surface body)"]] }] }],
    args: (v) => ({ faces: (v.faces || []).map((i) => i.value), mode: v.mode }),
    fromParams: (p) => ({ faces: faceItems(p.faces), mode: p.mode }),
    message: "Select the faces to remove (e.g. a hole's or fillet's faces).",
  },
  move_face: {
    title: "Move Face", icon: "offset", command: "move_face",
    groups: [{ title: "Move Face", fields: [{ key: "faces", type: "selection", accepts: ["face"], label: "Faces" },
      { key: "distance", type: "number", label: "Offset distance", icon: "depth", value: 5, step: 1 }] }],
    args: (v) => ({ faces: (v.faces || []).map((i) => i.value), distance: v.distance }),
    fromParams: (p) => ({ faces: faceItems(p.faces), distance: p.distance }),
    message: "Select faces; positive distances move them out of the material.",
  },
  replace_face: {
    title: "Replace Face", icon: "surface", command: "replace_face",
    groups: [{ title: "Replace Face Parameters", fields: [{ key: "faces", type: "selection", accepts: ["face"], label: "Target faces" },
      { key: "surface", type: "selection", accepts: ["feature"], single: true, label: "Replacement surface (tree)" }] }],
    args: (v) => ({ faces: (v.faces || []).map((i) => i.value), surface: v.surface?.[0]?.value }),
    fromParams: (p) => ({ faces: faceItems(p.faces), surface: [{ label: p.surface, value: p.surface }] }),
    message: "Select the faces to replace, then the surface body that replaces them.",
  },
  projected_curve: {
    title: "Projected Curve", icon: "intersection_curve", command: "projected_curve",
    groups: [{ title: "Selections", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Sketch to project" },
      { key: "faces", type: "selection", accepts: ["face"], label: "Projection faces (optional)" },
      { key: "reverse", type: "check", label: "Reverse projection", value: false }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, faces: (v.faces || []).map((i) => i.value), reverse: !!v.reverse }),
    fromParams: (p) => ({ sketch: [{ label: p.sketch, value: p.sketch }], faces: faceItems(p.faces), reverse: p.reverse }),
    message: "Select a sketch; it is projected along its plane's normal onto the faces it meets.",
  },
  split_line: {
    title: "Split Line", icon: "intersection_curve", command: "split_line",
    groups: [{ title: "Selections", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Sketch to project" },
      { key: "faces", type: "selection", accepts: ["face"], label: "Faces to split (optional)" },
      { key: "reverse", type: "check", label: "Reverse projection", value: false }] }],
    args: (v) => ({ sketch: v.sketch?.[0]?.value, faces: (v.faces || []).map((i) => i.value), reverse: !!v.reverse }),
    fromParams: (p) => ({ sketch: [{ label: p.sketch, value: p.sketch }], faces: faceItems(p.faces), reverse: p.reverse }),
    message: "Select a sketch; it is projected onto the faces it meets and splits them.",
  },
  composite_curve: {
    title: "Composite Curve", icon: "sketch3d", command: "composite_curve",
    groups: [{ title: "Entities to Join", fields: [{ key: "items", type: "selection", accepts: ["sketch", "edge"], label: "Sketches, curves and edges" }] }],
    args: (v) => ({ items: (v.items || []).map((i) => i.value) }),
    fromParams: (p) => ({ items: (p.items || []).map((r, i) => (typeof r === "string" ? { label: r, value: r } : { kind: "edge", label: `Edge<${i + 1}>`, value: r })) }),
    message: "Select connected sketches, curves and model edges.",
  },
  xyz_curve: {
    title: "Curve Through XYZ Points", icon: "spline", command: "curve_through_points",
    groups: [{ title: "Points", fields: [{ key: "points", type: "table", label: "Points", columns: ["X", "Y", "Z"] }, { key: "closed", type: "check", label: "Closed curve", value: false }] }],
    args: (v) => ({ points: v.points || [], closed: !!v.closed }),
    fromParams: (p) => ({ points: p.points, closed: p.closed }),
    message: "Enter one point (X, Y, Z) per row.",
  },
};

export const SPECS = {
  ...EDIT_SPECS,
  ...SHEET_SPECS,
  sweep: profileSpec("sweep", false),
  cut_sweep: profileSpec("sweep", true),
  loft: profileSpec("loft", false),
  cut_loft: profileSpec("loft", true),
  draft: {
    title: "Draft", icon: "draft_feat", command: "draft",
    groups: [{ title: "Draft Angle", fields: [{ key: "angle", type: "number", label: "Angle (°)", icon: "draft", value: 3, step: 0.5 },
      { key: "reverse", type: "check", label: "Reverse direction", value: false }] },
    { title: "Neutral Plane", fields: [{ key: "neutral", type: "selection", accepts: ["plane", "face"], single: true, label: "Neutral plane / face" }] },
    { title: "Faces to Draft", fields: [{ key: "faces", type: "selection", accepts: ["face"], label: "Faces" }] }],
    args: (v) => ({ neutral: v.neutral?.[0]?.value ?? "Top", faces: (v.faces || []).map((i) => i.value), angle: v.angle, reverse: !!v.reverse }),
    fromParams: (p) => ({ angle: p.angle, reverse: p.reverse, neutral: [{ label: typeof p.neutral === "string" ? p.neutral : "Face<1>", value: p.neutral }],
      faces: (p.faces || []).map((r, i) => ({ kind: "face", label: `Face<${i + 1}>`, value: r })) }),
    message: "Select the neutral plane, then the faces to draft.",
  },
  mate: {
    title: "Mate", icon: "mate", command: "add_mate",
    groups: [
      { title: "Mate Selections", fields: [{ key: "ents", type: "selection", accepts: ["face", "edge"], label: "Faces (path mate: edge, then face)" },
        { key: "plane1", type: "select", label: "Entity 1 as plane", value: "", options: [["", "Selected face"], ["Front Plane", "Front Plane of its component"], ["Top Plane", "Top Plane of its component"], ["Right Plane", "Right Plane of its component"]] },
        { key: "plane2", type: "select", label: "Entity 2 as plane", value: "", options: [["", "Selected face"], ["Front Plane", "Front Plane of its component"], ["Top Plane", "Top Plane of its component"], ["Right Plane", "Right Plane of its component"]] }] },
      { title: "Standard Mates", fields: [{ key: "type", type: "radio", value: "coincident", options: [["coincident", "Coincident"], ["parallel", "Parallel"],
        ["perpendicular", "Perpendicular"], ["tangent", "Tangent"], ["concentric", "Concentric"], ["lock", "Lock"], ["distance", "Distance"], ["angle", "Angle"],
        ["width", "Width (A face, A face, B face, B face)"], ["gear", "Gear (two cylindrical faces)"],
        ["symmetric", "Symmetric (face, face, symmetry plane face)"], ["slot", "Slot (slot face, pin, other slot face)"],
        ["limit_distance", "Limit distance (two faces)"], ["limit_angle", "Limit angle (two faces)"],
        ["cam", "Cam (cam face, roller cylinder)"], ["path", "Path (path edge, follower cylinder)"],
        ["hinge", "Hinge (cylinder, cylinder, face, face)"], ["screw", "Screw (two cylinders)"],
        ["rack_pinion", "Rack Pinion (rack edge, pinion cylinder)"], ["universal_joint", "Universal Joint (two shaft cylinders)"],
        ["linear_coupler", "Linear Coupler (face/edge, face/edge)"]] },
        { key: "pitch", type: "number", label: "Pitch (mm per revolution)", icon: "depth", value: 2, step: 0.25, showIf: (v) => v.type === "screw" },
        { key: "pdia", type: "number", label: "Pinion pitch diameter", icon: "depth", value: 20, step: 1, showIf: (v) => v.type === "rack_pinion" },
        { key: "lmin", type: "number", label: "Minimum", icon: "depth", value: 0, step: 1, showIf: (v) => v.type.startsWith("limit") },
        { key: "lmax", type: "number", label: "Maximum", icon: "depth", value: 20, step: 1, showIf: (v) => v.type.startsWith("limit") },
        { key: "value", type: "number", label: "Value", icon: "depth", value: 10, step: 1, showIf: (v) => ["distance", "angle"].includes(v.type) },
        { key: "ratio", type: "number", label: "Ratio B:A", icon: "circular_pattern", value: 2, step: 0.5, showIf: (v) => ["gear", "linear_coupler"].includes(v.type) },
        { key: "aligned", type: "check", label: "Aligned (flip mate alignment)", value: false, showIf: (v) => ["coincident", "distance"].includes(v.type) }] },
    ],
    args: (v) => v.type === "width"
      ? { type: "width", entity1: v.ents?.[0]?.value, entity3: v.ents?.[1]?.value, entity2: v.ents?.[2]?.value, entity4: v.ents?.[3]?.value }
      : v.type === "hinge"
      ? { type: "hinge", entity1: v.ents?.[0]?.value, entity2: v.ents?.[1]?.value, entity3: v.ents?.[2]?.value, entity4: v.ents?.[3]?.value }
      : ["symmetric", "slot"].includes(v.type)
      ? { type: v.type, entity1: v.ents?.[0]?.value, entity2: v.ents?.[1]?.value, entity3: v.ents?.[2]?.value }
      : v.type.startsWith("limit")
      ? { type: v.type, entity1: v.ents?.[0]?.value, entity2: v.ents?.[1]?.value, value: v.lmin, value2: v.lmax }
      : { type: v.type, entity1: asPlane(v.ents?.[0]?.value, v.plane1), entity2: asPlane(v.ents?.[1]?.value, v.plane2),
          value: ["distance", "angle"].includes(v.type) ? v.value : ["gear", "linear_coupler"].includes(v.type) ? v.ratio : v.type === "screw" ? v.pitch
            : v.type === "rack_pinion" ? v.pdia : null, aligned: !!v.aligned },
    fromParams: (p) => p,
    message: "Select two faces on different components (planar, or cylindrical for Concentric).",
  },
  extrude: extrudeSpec(false),
  cut_extrude: extrudeSpec(true),
  revolve: revolveSpec(false),
  cut_revolve: revolveSpec(true),
  fillet: {
    title: "Fillet", icon: "fillet", command: "fillet",
    commandFor: (v) => (v.ftype === "variable" ? "variable_fillet" : "fillet"),
    groups: [
      { title: "Fillet Type", fields: [{ key: "ftype", type: "radio", options: [["constant", "Constant Size Fillet"], ["variable", "Variable Size Fillet"]], value: "constant" }] },
      { title: "Items To Fillet", fields: [{ key: "items", type: "selection", accepts: ["edge", "face"], label: "Edges, Faces" },
        { key: "radius", type: "number", label: "Radius", icon: "fillet", value: 5, step: 0.5, showIf: (v) => v.ftype !== "variable" },
        { key: "r_start", type: "number", label: "Start radius", icon: "fillet", value: 2, step: 0.5, showIf: (v) => v.ftype === "variable" },
        { key: "r_end", type: "number", label: "End radius", icon: "fillet", value: 6, step: 0.5, showIf: (v) => v.ftype === "variable" }] },
    ],
    args: (v) => (v.ftype === "variable"
      ? { edges: (v.items || []).filter((i) => i.kind === "edge").map((i) => i.value), r_start: v.r_start, r_end: v.r_end }
      : { radius: v.radius, edges: (v.items || []).filter((i) => i.kind === "edge").map((i) => i.value),
        faces: (v.items || []).filter((i) => i.kind === "face").map((i) => i.value) }),
    fromParams: (p) => ({ ftype: p.r_start != null ? "variable" : "constant", radius: p.radius ?? 5, r_start: p.r_start ?? 2, r_end: p.r_end ?? 6,
      items: [...(p.edges || []).map((r, i) => ({ kind: "edge", label: `Edge<${i + 1}>`, value: r })),
        ...(p.faces || []).map((r, i) => ({ kind: "face", label: `Face<${i + 1}>`, value: r }))] }),
    message: "Select edges or faces to fillet and set the radius.",
  },
  chamfer: {
    title: "Chamfer", icon: "chamfer", command: "chamfer",
    groups: [
      { title: "Chamfer Type", fields: [{ key: "type", type: "radio", options: [["angle_distance", "Angle Distance"], ["distance_distance", "Distance Distance"], ["equal_distance", "Equal Distance"]], value: "angle_distance" }] },
      { title: "Items To Chamfer", fields: [{ key: "items", type: "selection", accepts: ["edge", "face"], label: "Edges, Faces" }] },
      { title: "Chamfer Parameters", fields: [
        { key: "distance", type: "number", label: "Distance", icon: "chamfer", value: 2, step: 0.5 },
        { key: "angle", type: "number", label: "Angle (°)", icon: "draft", value: 45, step: 1, showIf: (v) => v.type === "angle_distance" },
        { key: "distance2", type: "number", label: "Distance 2", icon: "chamfer", value: 2, step: 0.5, showIf: (v) => v.type === "distance_distance" }] },
    ],
    args: (v) => ({ distance: v.distance, type: v.type, angle: v.angle, distance2: v.distance2,
      edges: (v.items || []).filter((i) => i.kind === "edge").map((i) => i.value), faces: (v.items || []).filter((i) => i.kind === "face").map((i) => i.value) }),
    fromParams: (p) => ({ distance: p.distance, type: p.type, angle: p.angle, distance2: p.distance2,
      items: [...(p.edges || []).map((r, i) => ({ kind: "edge", label: `Edge<${i + 1}>`, value: r })), ...(p.faces || []).map((r, i) => ({ kind: "face", label: `Face<${i + 1}>`, value: r }))] }),
    message: "Select edges or faces to chamfer.",
  },
  shell: {
    title: "Shell", icon: "shell", command: "shell",
    groups: [{ title: "Parameters", fields: [
      { key: "thickness", type: "number", label: "Thickness", icon: "shell", value: 2, step: 0.5 },
      { key: "faces", type: "selection", accepts: ["face"], label: "Faces to Remove" },
      { key: "outward", type: "check", label: "Shell outward", value: false }] }],
    args: (v) => ({ thickness: v.thickness, faces_to_remove: (v.faces || []).map((i) => i.value), outward: !!v.outward }),
    fromParams: (p) => ({ thickness: p.thickness, outward: p.outward, faces: (p.faces || []).map((r, i) => ({ kind: "face", label: `Face<${i + 1}>`, value: r })) }),
    message: "Select faces to remove and set the wall thickness.",
  },
  hole: {
    title: "Hole", icon: "hole", command: "hole",
    groups: [
      { title: "Hole Type", fields: [{ key: "type", type: "radio", options: [["simple", "Hole"], ["counterbore", "Counterbore"], ["countersink", "Countersink"]], value: "simple" }] },
      { title: "Hole Specifications", fields: [
        { key: "diameter", type: "number", label: "Diameter", icon: "hole", value: 6, step: 0.5 },
        { key: "cbore_diameter", type: "number", label: "C'bore Ø", icon: "hole", value: 11, step: 0.5, showIf: (v) => v.type === "counterbore" },
        { key: "cbore_depth", type: "number", label: "C'bore depth", icon: "depth", value: 6, step: 0.5, showIf: (v) => v.type === "counterbore" },
        { key: "csk_diameter", type: "number", label: "C'sink Ø", icon: "hole", value: 12, step: 0.5, showIf: (v) => v.type === "countersink" },
        { key: "csk_angle", type: "number", label: "C'sink angle", icon: "draft", value: 90, step: 1, showIf: (v) => v.type === "countersink" }] },
      { title: "End Condition", fields: [
        { key: "end_condition", type: "select", options: [["Blind", "Blind"], ["ThroughAll", "Through All"]], value: "Blind" },
        { key: "depth", type: "number", label: "Depth", icon: "depth", value: 10, step: 1, showIf: (v) => v.end_condition === "Blind" },
        { key: "drill_point", type: "check", label: "Drill point (118°)", value: false, showIf: (v) => v.end_condition === "Blind" }] },
      { title: "Positions", fields: [{ key: "face", type: "selection", accepts: ["face"], single: true, label: "Face", hint: "Select a planar face, then click on it to place holes" },
        { key: "points", type: "points", label: "Hole centres" }] },
    ],
    args: (v) => ({ face: v.face?.[0]?.value, points: v.points || [], diameter: v.diameter, depth: v.depth, end_condition: v.end_condition,
      type: v.type, cbore_diameter: v.cbore_diameter, cbore_depth: v.cbore_depth, csk_diameter: v.csk_diameter, csk_angle: v.csk_angle, drill_point: !!v.drill_point }),
    fromParams: (p) => ({ ...p, face: [{ kind: "face", label: "Face<1>", value: p.face }], points: p.points }),
    message: "Select a planar face, then click on the face to position holes.",
  },
  linear_pattern: {
    title: "Linear Pattern", icon: "linear_pattern", command: "linear_pattern",
    groups: [
      { title: "Direction 1", fields: [{ key: "dir1", type: "selection", accepts: ["edge", "axis"], single: true, label: "Direction", withAxes: "axis1", withReverse: "reverse" },
        { key: "spacing", type: "number", label: "Spacing", icon: "depth", value: 20, step: 1 },
        { key: "count", type: "number", label: "Instances", icon: "linear_pattern", value: 3, step: 1, min: 1 }] },
      { title: "Direction 2", check: "use_dir2", value: false, fields: [
        { key: "dir2", type: "selection", accepts: ["edge"], single: true, label: "Direction", withAxes: "axis2", withReverse: "reverse2" },
        { key: "spacing2", type: "number", label: "Spacing", icon: "depth", value: 20, step: 1 },
        { key: "count2", type: "number", label: "Instances", icon: "linear_pattern", value: 2, step: 1, min: 1 }] },
      { title: "Features and Faces", fields: [{ key: "features", type: "selection", accepts: ["feature"], label: "Features to Pattern" }] },
    ],
    args: (v) => ({ features: (v.features || []).map((i) => i.value), direction: v.dir1?.[0]?.value ?? v.axis1 ?? "X", spacing: v.spacing, count: v.count, reverse: !!v.reverse,
      ...(v.use_dir2 ? { direction2: v.dir2?.[0]?.value ?? v.axis2 ?? "Y", spacing2: v.spacing2, count2: v.count2, reverse2: !!v.reverse2 } : {}) }),
    fromParams: (p) => ({ features: p.features.map((f) => ({ kind: "feature", label: f, value: f })), spacing: p.spacing, count: p.count, reverse: p.reverse,
      ...(typeof p.direction === "string" ? { axis1: p.direction } : { dir1: [{ kind: "edge", label: "Edge<1>", value: p.direction }] }),
      use_dir2: !!p.direction2, spacing2: p.direction2?.spacing ?? 20, count2: p.direction2?.count ?? 2 }),
    message: "Select a direction (edge or axis) and the features to pattern.",
  },
  circular_pattern: {
    title: "Circular Pattern", icon: "circular_pattern", command: "circular_pattern",
    groups: [
      { title: "Direction 1", fields: [{ key: "axis", type: "selection", accepts: ["edge", "face", "axis"], single: true, label: "Axis", withAxes: "axisname", withReverse: "reverse" },
        { key: "angle", type: "number", label: "Angle (°)", icon: "draft", value: 360, step: 5 },
        { key: "count", type: "number", label: "Instances", icon: "circular_pattern", value: 4, step: 1, min: 1 },
        { key: "equal_spacing", type: "check", label: "Equal spacing", value: true }] },
      { title: "Features and Faces", fields: [{ key: "features", type: "selection", accepts: ["feature"], label: "Features to Pattern" }] },
    ],
    args: (v) => ({ features: (v.features || []).map((i) => i.value), axis: v.axis?.[0]?.value ?? v.axisname ?? "Y", count: v.count, angle: v.angle,
      equal_spacing: !!v.equal_spacing, reverse: !!v.reverse }),
    fromParams: (p) => ({ features: p.features.map((f) => ({ kind: "feature", label: f, value: f })), count: p.count, angle: p.angle, equal_spacing: p.equal_spacing, reverse: p.reverse,
      ...(typeof p.axis === "string" ? { axisname: p.axis } : { axis: [{ kind: "edge", label: "Axis<1>", value: p.axis }] }) }),
    message: "Select an axis (circular/linear edge, cylindrical face or X/Y/Z) and the features.",
  },
  sketch_pattern: {
    title: "Sketch Driven Pattern", icon: "sketch_pattern", command: "sketch_pattern",
    groups: [
      { title: "Selections", fields: [{ key: "sketch", type: "selection", accepts: ["sketch"], single: true, label: "Reference Sketch (points)" }] },
      { title: "Features and Faces", fields: [{ key: "features", type: "selection", accepts: ["feature"], label: "Features to Pattern" }] },
    ],
    args: (v) => ({ features: (v.features || []).map((i) => i.value), sketch: v.sketch?.[0]?.value }),
    fromParams: (p) => ({ features: p.features.map((f) => ({ kind: "feature", label: f, value: f })), sketch: [{ label: p.sketch, value: p.sketch }] }),
    message: "Select a sketch containing points (the first point is the seed position) and the features.",
  },
  curve_pattern: {
    title: "Curve Driven Pattern", icon: "curve_pattern", command: "curve_pattern",
    groups: [
      { title: "Direction 1", fields: [{ key: "path", type: "selection", accepts: ["sketch"], single: true, label: "Pattern Direction (curve sketch)" },
        { key: "count", type: "number", label: "Instances", icon: "linear_pattern", value: 4, step: 1, min: 2 }] },
      { title: "Features and Faces", fields: [{ key: "features", type: "selection", accepts: ["feature"], label: "Features to Pattern" }] },
    ],
    args: (v) => ({ features: (v.features || []).map((i) => i.value), path: v.path?.[0]?.value, count: v.count }),
    fromParams: (p) => ({ features: p.features.map((f) => ({ kind: "feature", label: f, value: f })), path: [{ label: p.path, value: p.path }], count: p.count }),
    message: "Select a curve sketch (instances are spaced equally along it from its start) and the features.",
  },
  table_pattern: {
    title: "Table Driven Pattern", icon: "table_pattern", command: "table_pattern",
    groups: [
      { title: "Features and Faces", fields: [{ key: "features", type: "selection", accepts: ["feature"], label: "Features to Pattern" }] },
      { title: "Point Table", fields: [{ key: "offsets", type: "table", label: "Offsets from seed", columns: ["dX", "dY", "dZ"] }] },
    ],
    args: (v) => ({ features: (v.features || []).map((i) => i.value), offsets: v.offsets || [] }),
    fromParams: (p) => ({ features: p.features.map((f) => ({ kind: "feature", label: f, value: f })), offsets: p.offsets }),
    message: "Select the features, then enter one instance offset (dX, dY, dZ) per line.",
  },
  fill_pattern: {
    title: "Fill Pattern", icon: "fill_pattern", command: "fill_pattern",
    groups: [
      { title: "Fill Boundary", fields: [{ key: "face", type: "selection", accepts: ["face"], single: true, label: "Planar face" }] },
      { title: "Pattern Layout", fields: [{ key: "layout", type: "radio", value: "square", options: [["square", "Square"], ["staggered", "Perforation (staggered)"]] },
        { key: "spacing", type: "number", label: "Instance spacing", icon: "linear_pattern", value: 10, step: 1 },
        { key: "angle", type: "number", label: "Angle (°)", icon: "draft", value: 0, step: 5 },
        { key: "margin", type: "number", label: "Margin", icon: "depth", value: 2, step: 0.5 }] },
      { title: "Features to Pattern", fields: [{ key: "features", type: "selection", accepts: ["feature"], label: "Seed features" }] },
    ],
    args: (v) => ({ features: (v.features || []).map((i) => i.value), face: v.face?.[0]?.value, spacing: v.spacing, margin: v.margin, layout: v.layout, angle: v.angle }),
    fromParams: (p) => ({ ...p, features: p.features.map((f) => ({ kind: "feature", label: f, value: f })), face: [{ kind: "face", label: "Face<1>", value: p.face }] }),
    message: "Select the planar face to fill and the seed features (e.g. a hole on that face).",
  },
  mirror: {
    title: "Mirror", icon: "mirror", command: "mirror",
    groups: [
      { title: "Mirror Face/Plane", fields: [{ key: "plane", type: "selection", accepts: ["plane", "face"], single: true, label: "Plane" }] },
      { title: "Features to Mirror", fields: [{ key: "features", type: "selection", accepts: ["feature"], label: "Features" }] },
      { title: "Bodies to Mirror", check: "bodies", value: false, fields: [] },
    ],
    args: (v) => ({ plane: v.plane?.[0]?.value, features: (v.features || []).map((i) => i.value), bodies: !!v.bodies }),
    fromParams: (p) => ({ plane: [{ label: typeof p.plane === "string" ? p.plane : "Face<1>", value: p.plane }], features: (p.features || []).map((f) => ({ kind: "feature", label: f, value: f })), bodies: p.bodies }),
    message: "Select a mirror plane and the features (or bodies) to mirror.",
  },
  plane: {
    title: "Plane", icon: "plane", command: "reference_plane",
    groups: [
      { title: "First Reference", fields: [{ key: "reference", type: "selection", accepts: ["plane", "face"], single: true, label: "Reference" },
        { key: "offset", type: "number", label: "Offset", icon: "depth", value: 20, step: 1 },
        { key: "flip", type: "check", label: "Flip offset", value: false },
        { key: "angle", type: "number", label: "Angle (°)", icon: "draft", value: 0, step: 5 }] },
    ],
    args: (v) => ({ reference: v.reference?.[0]?.value ?? "Front", offset: v.offset, flip: !!v.flip, angle: v.angle }),
    fromParams: (p) => ({ reference: [{ label: typeof p.reference === "string" ? p.reference : "Face<1>", value: p.reference }], offset: p.offset, flip: p.flip, angle: p.angle || 0 }),
    message: "Select a plane or planar face and set the offset distance.",
  },
  ref_axis: {
    title: "Axis", icon: "ref_axis", command: "reference_axis",
    groups: [{ title: "Selections", fields: [{ key: "mode", type: "radio", value: "reference", options: [["reference", "One Line/Edge/Axis or Cylindrical/Conical Face"], ["planes", "Two Planes"], ["points", "Two Points/Vertices"]] },
      { key: "reference", type: "selection", accepts: ["edge", "face", "sketchline"], single: true, label: "Edge, cylindrical face or sketch line", withAxes: "axisname", showIf: (v) => v.mode === "reference" },
      { key: "planes", type: "selection", accepts: ["plane", "face"], label: "Two planes / planar faces", showIf: (v) => v.mode === "planes" },
      { key: "points", type: "selection", accepts: ["point", "edge", "face"], label: "Two points (point, arc edge centre, face centre)", showIf: (v) => v.mode === "points" }] }],
    args: (v) => v.mode === "planes" ? { planes: (v.planes || []).map((i) => i.value) }
      : v.mode === "points" ? { points: (v.points || []).map((i) => i.value) } : { reference: v.reference?.[0]?.value ?? v.axisname ?? null },
    fromParams: (p) => p.planes ? { mode: "planes", planes: p.planes.map((x) => ({ label: typeof x === "string" ? x : "Face", value: x })) }
      : p.points ? { mode: "points", points: p.points.map((x) => ({ label: typeof x === "string" ? x : Array.isArray(x) ? `(${x.join(", ")})` : "Ref", value: x })) }
      : typeof p.reference === "string" && p.reference.length === 1 ? { mode: "reference", axisname: p.reference } : { mode: "reference", reference: [{ label: typeof p.reference === "string" ? p.reference : "Edge", value: p.reference }] },
    message: "Select an edge, cylindrical face or sketch line; or two planes; or two points.",
  },
  helix: {
    title: "Helix/Spiral", icon: "helix", command: "helix",
    groups: [
      { title: "Axis", fields: [{ key: "axis", type: "selection", accepts: ["edge", "axis"], single: true, label: "Axis (edge, axis or X/Y/Z)", withAxes: "axisname", withReverse: "reverse" }] },
      { title: "Parameters", fields: [{ key: "pitch", type: "number", label: "Pitch", icon: "depth", value: 5, step: 0.5 },
        { key: "revolutions", type: "number", label: "Revolutions", icon: "circular_pattern", value: 4, step: 0.5 },
        { key: "radius", type: "number", label: "Radius", icon: "depth", value: 10, step: 1 },
        { key: "start_angle", type: "number", label: "Start angle (°)", icon: "draft", value: 0, step: 15 },
        { key: "counterclockwise", type: "check", label: "Counterclockwise (left-hand)", value: false }] },
      { title: "Taper Helix", fields: [{ key: "taper", type: "number", label: "Taper angle (°)", icon: "draft", value: 0, step: 1 }] },
    ],
    args: (v) => ({ axis: v.axis?.[0]?.value ?? v.axisname ?? "Z", pitch: v.pitch, revolutions: v.revolutions, radius: v.radius,
      start_angle: v.start_angle, counterclockwise: !!v.counterclockwise, reverse: !!v.reverse, taper: v.taper }),
    fromParams: (p) => ({ ...p, ...(typeof p.axis === "string" && p.axis.length === 1 ? { axisname: p.axis, axis: [] } : { axis: [{ kind: "edge", label: typeof p.axis === "string" ? p.axis : "Axis", value: p.axis }] }) }),
    message: "Pick the helix axis (default Z through the origin), then set pitch, revolutions and radius.",
  },
  ref_point: {
    title: "Point", icon: "ref_point", command: "reference_point",
    groups: [{ title: "Selections", fields: [{ key: "mode", type: "radio", value: "reference", options: [["reference", "Arc centre / face centre / edge midpoint"], ["coords", "Coordinates"]] },
      { key: "reference", type: "selection", accepts: ["edge", "face", "point"], single: true, label: "Edge, face or point", showIf: (v) => v.mode === "reference" },
      { key: "x", type: "number", label: "X", icon: "depth", value: 0, step: 1, showIf: (v) => v.mode === "coords" },
      { key: "y", type: "number", label: "Y", icon: "depth", value: 0, step: 1, showIf: (v) => v.mode === "coords" },
      { key: "z", type: "number", label: "Z", icon: "depth", value: 0, step: 1, showIf: (v) => v.mode === "coords" }] }],
    args: (v) => v.mode === "coords" ? { coords: [v.x, v.y, v.z] } : { reference: v.reference?.[0]?.value ?? null },
    fromParams: (p) => p.coords ? { mode: "coords", x: p.coords[0], y: p.coords[1], z: p.coords[2] } : { mode: "reference", reference: [{ label: typeof p.reference === "string" ? p.reference : "Ref", value: p.reference }] },
    message: "Select an arc/edge, face or point, or enter coordinates.",
  },
  csys: {
    title: "Coordinate System", icon: "csys", command: "coordinate_system",
    groups: [{ title: "Selections", fields: [{ key: "origin", type: "selection", accepts: ["point", "edge", "face"], single: true, label: "Origin (default: part origin)" },
      { key: "x_axis", type: "selection", accepts: ["edge", "axis"], single: true, label: "X axis", withAxes: "xname" },
      { key: "y_axis", type: "selection", accepts: ["edge", "axis"], single: true, label: "Y axis", withAxes: "yname" }] }],
    args: (v) => ({ origin: v.origin?.[0]?.value ?? null, x_axis: v.x_axis?.[0]?.value ?? v.xname ?? null, y_axis: v.y_axis?.[0]?.value ?? v.yname ?? null }),
    fromParams: (p) => ({ ...(p.origin ? { origin: [{ label: typeof p.origin === "string" ? p.origin : "Ref", value: p.origin }] } : {}),
      ...(typeof p.x_axis === "string" ? { xname: p.x_axis } : p.x_axis ? { x_axis: [{ label: "X", value: p.x_axis }] } : {}),
      ...(typeof p.y_axis === "string" ? { yname: p.y_axis } : p.y_axis ? { y_axis: [{ label: "Y", value: p.y_axis }] } : {}) }),
    message: "Select the origin and the X and Y axis directions (Z follows the right-hand rule).",
  },
};

export class PropertyManager {
  constructor(root) { this.root = root; this.active = null; }

  open(spec, opts = {}) {
    this.spec = spec; this.opts = opts;
    this.values = {};
    for (const g of spec.groups) {
      if (g.check) this.values[g.check] = g.value;
      for (const f of g.fields) {
        if (f.value !== undefined) this.values[f.key] = f.value;
        if (f.type === "selection") this.values[f.key] = [];
        if (f.type === "points") this.values[f.key] = [];
      }
    }
    Object.assign(this.values, opts.initial || {});
    this.focusKey = null;
    this.active = true;
    this.collapsed = new Set();
    this.render();
    const firstEmpty = this._selFields().find((f) => !(this.values[f.key] || []).length);
    this.focus((firstEmpty || this._selFields()[0])?.key ?? null);
  }

  close() { this.active = null; this.root.innerHTML = ""; }
  _selFields() { return this.spec.groups.flatMap((g) => g.fields.filter((f) => f.type === "selection")); }
  focus(key) { this.focusKey = key; this.root.querySelectorAll(".pm-sel").forEach((el) => el.classList.toggle("focus", el.dataset.key === key)); }
  focusedAccepts() { const f = this._selFields().find((x) => x.key === this.focusKey); return f ? f.accepts : []; }

  // returns true if the pick was consumed
  accept(pick) {
    if (!this.active) return false;
    const f = this._selFields().find((x) => x.key === this.focusKey) || this._selFields().find((x) => x.accepts.includes(pick.kind));
    if (!f || !f.accepts.includes(pick.kind)) return false;
    const list = this.values[f.key];
    const ix = list.findIndex((i) => JSON.stringify(i.value) === JSON.stringify(pick.value));
    if (ix >= 0) list.splice(ix, 1);
    else if (f.single) list.splice(0, list.length, pick); else list.push(pick);
    if (f.single && list.length) { // auto-advance focus like SOLIDWORKS
      const next = this._selFields().find((x) => x.key !== f.key && !(this.values[x.key] || []).length);
      if (next) this.focusKey = next.key;
    }
    this.render(); this.changed();
    return true;
  }

  addPoint(uv) { if (this.values.points) { this.values.points.push(uv.map((x) => Math.round(x * 1000) / 1000)); this.render(); this.changed(); } }
  setMessage(msg, err = false) { this.msg = msg; this.err = err; const el = this.root.querySelector(".pm-msg"); if (el) { el.textContent = msg; el.classList.toggle("err", err); } }
  changed() { this.opts.onChange?.(this.args()); }
  args() { return this.spec.args(this.values); }

  render() {
    const s = this.spec, v = this.values, root = this.root;
    root.innerHTML = "";
    const head = el("div", "pm-head", `${icon(s.icon)}<span>${this.opts.title || s.title}</span>`);
    const actions = el("div", "pm-actions");
    const ok = el("button", "pm-btn", icon("ok")); ok.title = "OK"; ok.onclick = () => this.opts.onOK?.(this.args());
    const cancel = el("button", "pm-btn", icon("cancel")); cancel.title = "Cancel"; cancel.onclick = () => this.opts.onCancel?.();
    actions.append(ok, cancel);
    if (this.opts.extraButtons) for (const b of this.opts.extraButtons) actions.append(b);
    root.append(head, actions, el("div", `pm-msg${this.err ? " err" : ""}`, escapeHtml(this.msg || s.message || "")));
    for (const g of s.groups) {
      const box = el("div", "pm-group" + (this.collapsed.has(g.title) || (g.check && !v[g.check]) ? " collapsed" : ""));
      const gh = el("div", "pm-gh");
      if (g.check) {
        const cb = el("input"); cb.type = "checkbox"; cb.checked = !!v[g.check];
        cb.onclick = (e) => { e.stopPropagation(); v[g.check] = cb.checked; this.render(); this.changed(); };
        gh.append(cb);
      }
      gh.append(el("span", "", g.title), el("span", "chev", "&#9662;"));
      gh.onclick = () => { if (g.check) return; this.collapsed.has(g.title) ? this.collapsed.delete(g.title) : this.collapsed.add(g.title); this.render(); };
      const gb = el("div", "pm-gb");
      for (const f of g.fields) { if (!f.showIf || f.showIf(v)) gb.append(this._field(f)); }
      box.append(gh, gb); root.append(box);
    }
    this.focus(this.focusKey);
  }

  _field(f) {
    const v = this.values;
    const row = el("div", "pm-row");
    const set = (val) => { v[f.key] = val; this.render(); this.changed(); };
    if (f.type === "number") {
      if (f.icon) row.append(el("span", "", icon(f.icon)));
      const L = isLengthField(f) && UNITS.f !== 1;
      row.append(el("label", "", L ? `${f.label} (${UNITS.name})` : f.label));
      const inp = el("input"); inp.type = "number"; inp.step = L ? (f.step ?? 1) / UNITS.f : f.step ?? 1; if (f.min !== undefined) inp.min = f.min;
      inp.value = L ? +UNITS.len(+v[f.key]).toFixed(6) : v[f.key];
      inp.onchange = () => { let x = parseFloat(inp.value); if (!isNaN(x)) { if (L) x = UNITS.toMM(x); v[f.key] = f.min === 1 ? Math.max(1, Math.round(x)) : x; this.changed(); } };
      inp.onkeydown = (e) => { if (e.key === "Enter") { inp.onchange(); this.opts.onOK?.(this.args()); } };
      row.append(inp);
    } else if (f.type === "select") {
      if (f.withReverse) row.append(this._reverse(f.withReverse));
      const sel = el("select");
      for (const [val, lab] of f.options) { const o = el("option", "", lab); o.value = val; sel.append(o); }
      sel.value = v[f.key]; sel.onchange = () => set(sel.value);
      row.append(sel);
    } else if (f.type === "check") {
      const lab = el("label", "pm-check"); const cb = el("input"); cb.type = "checkbox"; cb.checked = !!v[f.key];
      cb.onchange = () => set(cb.checked); lab.append(cb, document.createTextNode(f.label)); row.append(lab);
    } else if (f.type === "radio") {
      const wrap = el("div", "pm-radio");
      for (const [val, lab] of f.options) {
        const l = el("label", "pm-check"); const r = el("input"); r.type = "radio"; r.name = f.key; r.checked = v[f.key] === val;
        r.onchange = () => set(val); l.append(r, document.createTextNode(lab)); wrap.append(l);
      }
      row.append(wrap);
    } else if (f.type === "selection") {
      const col = el("div", ""); col.style.cssText = "display:flex;flex-direction:column;gap:4px;flex:1;min-width:0";
      const top = el("div", "pm-row");
      if (f.withReverse) top.append(this._reverse(f.withReverse));
      if (f.withAxes) {
        const sel = el("select"); for (const [val, lab] of AXES) { const o = el("option", "", lab); o.value = val; sel.append(o); }
        sel.value = v[f.withAxes] || ""; sel.onchange = () => { v[f.withAxes] = sel.value || null; if (sel.value) v[f.key] = []; this.render(); this.changed(); };
        top.append(sel);
      }
      if (top.children.length) col.append(top);
      const box = el("div", "pm-sel"); box.dataset.key = f.key; box.title = f.hint || f.label;
      box.onclick = () => this.focus(f.key);
      const items = v[f.key] || [];
      if (!items.length) box.append(el("div", "pm-sel-item", `<span style="color:#888">${escapeHtml(f.label)}</span>`));
      items.forEach((it, i) => {
        const r = el("div", "pm-sel-item", `<span>${escapeHtml(it.label)}</span>`); const x = el("span", "x", "&#10005;");
        x.onclick = (e) => { e.stopPropagation(); items.splice(i, 1); this.render(); this.changed(); };
        r.append(x); box.append(r);
      });
      col.append(box); row.append(col);
    } else if (f.type === "text") {
      const col = el("div", ""); col.style.cssText = "display:flex;flex-direction:column;gap:2px;flex:1;min-width:0";
      const line = el("div", ""); line.style.cssText = "display:flex;gap:4px";
      const inp = el("input"); inp.type = "text"; inp.value = v[f.key] ?? ""; inp.style.cssText = "flex:1;min-width:0";
      inp.onchange = () => { v[f.key] = inp.value; this.changed(); };
      line.append(inp);
      if (f.browse && this.opts.browse) { // a file path: Browse... opens the file dialog (desktop) or asks for the path
        const b = el("button", "pm-icobtn", "…"); b.title = "Browse";
        b.onclick = async () => { const p = await this.opts.browse(f.browse); if (p) { inp.value = p; v[f.key] = p; this.changed(); } };
        line.append(b);
      }
      col.append(el("label", "", escapeHtml(f.label)), line); row.append(col);
    } else if (f.type === "file") { // read into a data: URL
      const col = el("div", ""); col.style.cssText = "display:flex;flex-direction:column;gap:2px;flex:1;min-width:0";
      const inp = el("input"); inp.type = "file"; if (f.accept) inp.accept = f.accept;
      inp.onchange = () => { const file = inp.files[0]; if (!file) return; const rd = new FileReader(); rd.onload = () => { v[f.key] = rd.result; v[`${f.key}_name`] = file.name; this.render(); this.changed(); }; rd.readAsDataURL(file); };
      col.append(el("label", "", escapeHtml(f.label) + (v[`${f.key}_name`] ? `: ${escapeHtml(v[`${f.key}_name`])}` : "")), inp); row.append(col);
    } else if (f.type === "table") {
      const col = el("div", ""); col.style.cssText = "display:flex;flex-direction:column;gap:2px;flex:1;min-width:0";
      const ta = el("textarea"); ta.rows = 6; ta.spellcheck = false; ta.style.cssText = "width:100%;font:12px monospace;resize:vertical";
      ta.placeholder = f.columns.join(", "); ta.value = (v[f.key] || []).map((r) => r.join(", ")).join("\n");
      ta.onchange = () => { v[f.key] = ta.value.split("\n").map((l) => l.split(/[,\s]+/).filter(Boolean).map(Number)).filter((r) => r.length === f.columns.length && r.every((x) => !isNaN(x))); this.changed(); };
      col.append(el("label", "", `${escapeHtml(f.label)} (${f.columns.join(", ")} per line)`), ta); row.append(col);
    } else if (f.type === "points") {
      const box = el("div", "pm-sel"); box.style.minHeight = "30px";
      const pts = v[f.key] || [];
      if (!pts.length) box.append(el("div", "pm-sel-item", `<span style="color:#888">${f.label}: click on the face</span>`));
      pts.forEach((p, i) => {
        const r = el("div", "pm-sel-item", `<span>(${p[0].toFixed(2)}, ${p[1].toFixed(2)})</span>`); const x = el("span", "x", "&#10005;");
        x.onclick = () => { pts.splice(i, 1); this.render(); this.changed(); }; r.append(x); box.append(r);
      });
      row.append(box);
    }
    return row;
  }

  _reverse(key) {
    const b = el("button", "pm-icobtn" + (this.values[key] ? " on" : ""), icon("reverse")); b.title = "Reverse Direction";
    b.onclick = () => { this.values[key] = !this.values[key]; this.render(); this.changed(); };
    return b;
  }
}

export function el(tag, cls = "", html = "") { const e = document.createElement(tag); if (cls) e.className = cls; if (html) e.innerHTML = html; return e; }
export function escapeHtml(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }
