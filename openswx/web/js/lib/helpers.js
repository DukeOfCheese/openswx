import { UNITS } from "../propman.js";

export function cap(s) { return s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, " ") : s; }
export function segHitsRect(a, b, x0, y0, x1, y1) { // does the screen segment a-b cross the rectangle's edges?
  const cr = (p, q, r, t) => { const d = (q[0] - p[0]) * (t[1] - r[1]) - (q[1] - p[1]) * (t[0] - r[0]); if (!d) return false;
    const u = ((r[0] - p[0]) * (t[1] - r[1]) - (r[1] - p[1]) * (t[0] - r[0])) / d, v = ((r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])) / d;
    return u >= 0 && u <= 1 && v >= 0 && v <= 1; };
  const c = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  return c.some((p, i) => cr(a, b, p, c[(i + 1) % 4]));
}

export function applicableRelations(kinds) {
  const c = (t) => kinds.filter((k) => k === t).length, n = kinds.length;
  const curves = c("circle") + c("arc");
  const out = [];
  if (n === 1 && c("line")) out.push("horizontal", "vertical");
  if (n === 2 && c("line") === 2) out.push("parallel", "perpendicular", "equal", "collinear");
  if (n === 2 && c("point") === 2) out.push("coincident", "horizontal", "vertical");
  if (n === 2 && c("point") === 1 && c("line") === 1) out.push("coincident", "midpoint");
  if (n === 2 && c("point") === 1 && curves === 1) out.push("coincident", "concentric");
  if (n === 2 && c("line") === 1 && curves === 1) out.push("tangent");
  if (n === 2 && curves === 2) out.push("equal", "concentric", "tangent");
  if (n === 3 && c("point") === 2 && c("line") === 1) out.push("symmetric");
  if (n >= 1) out.push("fix");
  return out;
}

export function planeBasis(n) { // same convention as the server (SOLIDWORKS-like sketch axes)
  const N = norm(n);
  let y;
  if (Math.abs(N[1]) > 0.999) y = N[1] > 0 ? [0, 0, -1] : [0, 0, 1];
  else { const d = N[1]; y = norm([-N[0] * d, 1 - N[1] * d, -N[2] * d]); }
  const x = norm([y[1] * N[2] - y[2] * N[1], y[2] * N[0] - y[0] * N[2], y[0] * N[1] - y[1] * N[0]]);
  return { x, y, n: N };
}
export function facePlaneUV(n, p, refNormal) {
  const { x, y, n: N } = planeBasis(refNormal || n);
  const d = p[0] * N[0] + p[1] * N[1] + p[2] * N[2];
  const O = [N[0] * d, N[1] * d, N[2] * d], v = [p[0] - O[0], p[1] - O[1], p[2] - O[2]];
  return [v[0] * x[0] + v[1] * x[1] + v[2] * x[2], v[0] * y[0] + v[1] * y[1] + v[2] * y[2]];
}
export function norm(v) { const l = Math.hypot(...v) || 1; return v.map((x) => x / l); }

export function massHtml(mp, full = false) {
  const f = (x, d = 3) => (+x).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  if (!mp.bodies) return "<p>No solid bodies.</p>";
  const u = UNITS.name, L = (x) => x / UNITS.f, M = (g) => g / UNITS.mf; // display in document units
  const c = mp.center_of_mass, I = mp.inertia_at_com;
  return `<div class="kv">
    <div>Density</div><div>${f(M(mp.density / 1e6) * UNITS.f ** 3, 5)} ${UNITS.mass}/${u}³</div>
    <div>Mass</div><div><b>${f(M(mp.mass), 4)} ${UNITS.mass}</b></div>
    <div>Volume</div><div>${f(mp.volume / UNITS.f ** 3, 4)} ${u}³</div>
    <div>Surface area</div><div>${f(mp.surface_area / UNITS.f ** 2, 4)} ${u}²</div>
    <div>Center of mass X</div><div>${f(L(c[0]))} ${u}</div><div>Center of mass Y</div><div>${f(L(c[1]))} ${u}</div><div>Center of mass Z</div><div>${f(L(c[2]))} ${u}</div>
    <div>Bodies / faces / edges</div><div>${mp.bodies} / ${mp.faces} / ${mp.edges}</div></div>` +
    (full ? `<p style="margin-top:10px"><b>Moments of inertia</b> (${UNITS.mass}·${u}², at the centre of mass, output coordinate system)</p><div class="kv">
      ${["X", "Y", "Z"].map((a, i) => `<div>L${a}x / L${a}y / L${a}z</div><div>${[0, 1, 2].map((j) => f(M(I[i][j]) / UNITS.f ** 2, 4)).join(" / ")}</div>`).join("")}</div>
      <p style="margin-top:8px"><b>Bounding box</b> (${u}): ${mp.bounding_box.min.map((x) => f(L(x), 3)).join(", ")} → ${mp.bounding_box.max.map((x) => f(L(x), 3)).join(", ")}</p>` : "");
}