"""CAM: 3-axis milling and drilling toolpaths for a part, with G-code (like SOLIDWORKS CAM / CAMWorks Standard).

The part is seen from the tool side as a height map (the highest material over each point, from the
tessellated body). Z-level roughing clears every level down to the part's bottom with a zig-zag and a
contour pass that keep `leave` mm of stock on walls and floors; finishing then takes the walls (a contour
pass at each level) and the flat floors (found from the B-rep's upward planar faces) to size. The tool
centre is kept `tool radius + leave + one cell` from the nearest material cell, so the raster never lets the
tool into the part: the cell size (`tolerance`) is the precision. Holes along the tool axis (B-rep cylinders,
grouped by axis and radius, reachable from above) are drilled with canned cycles and left out of the
milling. A stock-removal simulation on the same raster reports gouges (none expected) and the material left.
"""
from __future__ import annotations

import math

import numpy as np

# cutting speed for carbide tools (m/min) and chip load per tooth as a fraction of the tool diameter
MATERIALS = {
    "aluminum": (300.0, 0.010), "brass": (200.0, 0.008), "plastic": (250.0, 0.012), "mild_steel": (120.0, 0.006),
    "stainless": (80.0, 0.004), "titanium": (50.0, 0.004), "cast_iron": (100.0, 0.006)}
MAX_RPM = 18000
RAPID = 10000.0  # mm/min, for the time estimate
TOOL_CHANGE_S = 10.0
POSTS = ("fanuc", "linuxcnc", "grbl")


def feeds(material: str, diameter: float, flutes: int = 3, drill: bool = False) -> dict:
    """Spindle speed and feeds from the material's cutting speed and chip load (a starting point)."""
    vc, k = MATERIALS[material]
    if drill:
        vc *= 0.6
    rpm = min(MAX_RPM, vc * 1000 / (math.pi * diameter))
    if drill:
        f = rpm * 2 * k * diameter  # feed per revolution ~ two chip loads
        return {"rpm": round(rpm), "feed": round(f, 1), "plunge": round(f, 1)}
    f = rpm * k * diameter * flutes
    return {"rpm": round(rpm), "feed": round(f, 1), "plunge": round(f * 0.3, 1)}


def setup_rotation(setup: str) -> np.ndarray:
    """Rotation taking part coordinates to machine coordinates (the tool comes down from machine +Z)."""
    s = setup.upper().strip()
    R = {"+Z": np.eye(3), "-Z": np.diag([1.0, -1.0, -1.0]),
         "+Y": np.array([[1.0, 0, 0], [0, 0, -1], [0, 1, 0]]), "-Y": np.array([[1.0, 0, 0], [0, 0, 1], [0, -1, 0]]),
         "+X": np.array([[0.0, 0, -1], [0, 1, 0], [1, 0, 0]]), "-X": np.array([[0.0, 0, 1], [0, 1, 0], [-1, 0, 0]])}
    if s not in R:
        raise ValueError("setup must be +Z, -Z, +X, -X, +Y or -Y (the side the tool comes from)")
    return R[s]


def heightmap(V: np.ndarray, T: np.ndarray, x0: float, y0: float, cell: float, n: tuple) -> np.ndarray:
    """Highest triangle over each cell centre (machine coordinates); -inf where there is none."""
    nx, ny = n
    H = np.full((nx, ny), -np.inf)
    P = V[T]  # (m, 3, 3)
    a, b, c = P[:, 0], P[:, 1], P[:, 2]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    keep = np.abs(area) > 1e-12  # vertical triangles have no top area
    lo = np.floor((P[keep, :, :2].min(axis=1) - [x0, y0]) / cell - 0.5).astype(int) + 1
    hi = np.floor((P[keep, :, :2].max(axis=1) - [x0, y0]) / cell - 0.5).astype(int)
    for pa, pb, pc, ar, (i0, j0), (i1, j1) in zip(a[keep], b[keep], c[keep], area[keep], lo, hi):
        i0, j0, i1, j1 = max(i0, 0), max(j0, 0), min(i1, nx - 1), min(j1, ny - 1)
        if i1 < i0 or j1 < j0:
            continue
        X = x0 + (np.arange(i0, i1 + 1) + 0.5) * cell
        Y = y0 + (np.arange(j0, j1 + 1) + 0.5) * cell
        X, Y = np.meshgrid(X, Y, indexing="ij")
        w0 = ((pb[0] - X) * (pc[1] - Y) - (pc[0] - X) * (pb[1] - Y)) / ar
        w1 = ((pc[0] - X) * (pa[1] - Y) - (pa[0] - X) * (pc[1] - Y)) / ar
        w2 = 1 - w0 - w1
        m = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        if not m.any():
            continue
        z = w0 * pa[2] + w1 * pb[2] + w2 * pc[2]
        sub = H[i0:i1 + 1, j0:j1 + 1]
        np.maximum(sub, np.where(m, z, -np.inf), out=sub)
    return H


# marching squares: corners b0 (i, j), b1 (i+1, j), b2 (i+1, j+1), b3 (i, j+1); edges B, R, T, L
_SEGS = {1: [("L", "B")], 2: [("B", "R")], 3: [("L", "R")], 4: [("R", "T")], 6: [("B", "T")], 7: [("L", "T")],
         8: [("T", "L")], 9: [("B", "T")], 11: [("R", "T")], 12: [("L", "R")], 13: [("B", "R")], 14: [("L", "B")]}
_SADDLE = {5: ([("B", "R"), ("T", "L")], [("L", "B"), ("R", "T")]),  # (centre inside, centre outside)
           10: ([("L", "B"), ("R", "T")], [("B", "R"), ("T", "L")])}


def contours(G: np.ndarray, x0: float, y0: float, cell: float) -> list[np.ndarray]:
    """Closed iso-lines G = 0 (G sampled at cell centres, negative along the border) as (k, 2) arrays."""
    nx, ny = G.shape
    ins = G >= 0
    b0, b1, b2, b3 = ins[:-1, :-1], ins[1:, :-1], ins[1:, 1:], ins[:-1, 1:]
    case = b0 * 1 + b1 * 2 + b2 * 4 + b3 * 8
    centre = (G[:-1, :-1] + G[1:, :-1] + G[1:, 1:] + G[:-1, 1:]) >= 0

    def edge(name, I, J):
        if name == "B":
            ia, ja, ib, jb, eid = I, J, I + 1, J, (J * nx + I) * 2
        elif name == "T":
            ia, ja, ib, jb, eid = I, J + 1, I + 1, J + 1, ((J + 1) * nx + I) * 2
        elif name == "L":
            ia, ja, ib, jb, eid = I, J, I, J + 1, (J * nx + I) * 2 + 1
        else:
            ia, ja, ib, jb, eid = I + 1, J, I + 1, J + 1, (J * nx + I + 1) * 2 + 1
        ga, gb = G[ia, ja], G[ib, jb]
        t = ga / (ga - gb)
        x = x0 + (ia + t * (ib - ia) + 0.5) * cell
        y = y0 + (ja + t * (jb - ja) + 0.5) * cell
        return eid, np.stack([x, y], 1)

    ids, pts = [], []
    jobs = [(case == c, s) for c, s in _SEGS.items()]
    for c, (yes, no) in _SADDLE.items():
        jobs += [((case == c) & centre, yes), ((case == c) & ~centre, no)]
    for mask, segs in jobs:
        I, J = np.nonzero(mask)
        if not len(I):
            continue
        for a, b in segs:
            ea, pa = edge(a, I, J)
            eb, pb = edge(b, I, J)
            ids.append(np.stack([ea, eb], 1))
            pts.append(np.stack([pa, pb], 1))
    if not ids:
        return []
    ids, pts = np.concatenate(ids), np.concatenate(pts)
    point, nbr = {}, {}
    for k, (ea, eb) in enumerate(ids.tolist()):
        point[ea], point[eb] = pts[k, 0], pts[k, 1]
        nbr.setdefault(ea, []).append(eb)
        nbr.setdefault(eb, []).append(ea)
    seen, out = set(), []
    for start in nbr:
        if start in seen:
            continue
        loop, prev, cur = [start], None, start
        seen.add(start)
        while True:
            nxt = [e for e in nbr[cur] if e != prev]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            if cur == start or cur in seen:
                break
            seen.add(cur)
            loop.append(cur)
        if len(loop) > 2:
            out.append(np.array([point[e] for e in loop]))
    return out


def simplify(P: np.ndarray, tol: float, closed: bool = True) -> np.ndarray:
    """Ramer-Douglas-Peucker; a closed loop comes back closed (last point = first)."""
    if closed:
        P = np.vstack([P, P[:1]])
    keep = np.zeros(len(P), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(P) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = P[i], P[j]
        d = b - a
        L = math.hypot(*d)
        seg = P[i + 1:j] - a
        dist = np.abs(seg[:, 0] * d[1] - seg[:, 1] * d[0]) / L if L > 1e-12 else np.hypot(seg[:, 0], seg[:, 1])
        k = int(np.argmax(dist))
        if dist[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return P[keep]


def find_holes(body, R: np.ndarray, max_diameter: float) -> list[dict]:
    """Cylindrical holes along the machine Z axis: [{x, y, diameter, top, bottom}] (machine coordinates,
    before the origin shift). Faces of one hole (split cylinders) are grouped by axis and radius."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Cylinder
    groups = {}
    for f in body.faces():
        ad = BRepAdaptor_Surface(f.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            continue
        cyl = ad.Cylinder()
        ax = cyl.Axis().Direction()
        d = R @ np.array([ax.X(), ax.Y(), ax.Z()])
        if abs(d[2]) < 0.9999:
            continue
        r = cyl.Radius()
        if 2 * r > max_diameter + 1e-9:
            continue
        loc = cyl.Axis().Location()
        o = R @ np.array([loc.X(), loc.Y(), loc.Z()])
        c = f.center()
        nn = R @ np.array(tuple(f.normal_at(c)))
        pc = R @ np.array([c.X, c.Y, c.Z])
        if np.dot(nn[:2], o[:2] - pc[:2]) <= 0:  # normal away from the axis: a boss, not a hole
            continue
        u0, u1, _v0, _v1 = BRepTools.UVBounds_s(f.wrapped)
        bb = f.bounding_box()
        corners = np.array([[x, y, z] for x in (bb.min.X, bb.max.X) for y in (bb.min.Y, bb.max.Y)
                            for z in (bb.min.Z, bb.max.Z)]) @ R.T
        key = (round(o[0], 3), round(o[1], 3), round(r, 4))
        g = groups.setdefault(key, {"x": o[0], "y": o[1], "diameter": 2 * r, "top": -np.inf, "bottom": np.inf, "span": 0.0})
        g["top"] = max(g["top"], corners[:, 2].max())
        g["bottom"] = min(g["bottom"], corners[:, 2].min())
        g["span"] += u1 - u0
    return [g for g in groups.values() if g["span"] >= 2 * math.pi - 1e-3]


def _runs(row: np.ndarray, xs: np.ndarray) -> list[tuple[float, float]]:
    """Intervals of a row where the field is >= 0, ends interpolated to the zero crossing."""
    ins = row >= 0
    if not ins.any():
        return []
    d = np.diff(ins.astype(np.int8))
    starts = list(np.nonzero(d == 1)[0] + 1)
    ends = list(np.nonzero(d == -1)[0])
    if ins[0]:
        starts.insert(0, 0)
    if ins[-1]:
        ends.append(len(row) - 1)
    out = []
    for s, e in zip(starts, ends):
        a = xs[s] if s == 0 else xs[s - 1] + (xs[s] - xs[s - 1]) * row[s - 1] / (row[s - 1] - row[s])
        b = xs[e] if e == len(row) - 1 else xs[e] + (xs[e + 1] - xs[e]) * row[e] / (row[e] - row[e + 1])
        if b - a > 1e-6:
            out.append((a, b))
    return out


class _Grid:
    def __init__(self, x0, y0, cell, n):
        self.x0, self.y0, self.cell, self.n = x0, y0, cell, n
        self.xs = x0 + (np.arange(n[0]) + 0.5) * cell
        self.ys = y0 + (np.arange(n[1]) + 0.5) * cell
        self.X, self.Y = np.meshgrid(self.xs, self.ys, indexing="ij")

    def idx(self, x, y):
        i = np.clip(np.floor((np.asarray(x) - self.x0) / self.cell).astype(int), 0, self.n[0] - 1)
        j = np.clip(np.floor((np.asarray(y) - self.y0) / self.cell).astype(int), 0, self.n[1] - 1)
        return i, j


def _clear_field(H, grid, z, clearance, box):
    """>= 0 where the tool centre may go at level z: away from material above z, inside the box."""
    from scipy.ndimage import distance_transform_edt
    forbidden = H > z + 1e-6
    D = distance_transform_edt(~forbidden) * grid.cell if forbidden.any() else np.full(H.shape, 1e9)
    out = np.minimum.reduce([grid.X - box[0], box[2] - grid.X, grid.Y - box[1], box[3] - grid.Y])
    return np.minimum(D - clearance, out)


def _passes_zigzag(F, grid, z, stepover, restrict=None):
    """Zig-zag rows (along X) over F >= 0 at level z; restrict: optional mask the rows must touch."""
    ys = np.arange(grid.ys[0] + stepover / 2, grid.ys[-1], stepover)
    passes = []
    for k, y in enumerate(ys):
        j = int(np.clip(round((y - grid.y0) / grid.cell - 0.5), 0, grid.n[1] - 1))
        for a, b in _runs(F[:, j], grid.xs):
            if restrict is not None:
                i0, _ = grid.idx(a, y)
                i1, _ = grid.idx(b, y)
                if not restrict[i0:i1 + 1, j].any():
                    continue
            y_ = float(grid.ys[j])
            p = [(a, y_, z), (b, y_, z)]
            passes.append(p if k % 2 == 0 else p[::-1])
    return passes


def _passes_contour(F, grid, z, tol, restrict=None):
    """Contour passes on F = 0 at level z, oriented with the cleared side on the left (the material wall
    on the right: climb milling with an M3 spindle)."""
    out = []
    for P in contours(F, grid.x0, grid.y0, grid.cell):
        if restrict is not None:
            i, j = grid.idx(P[:, 0], P[:, 1])
            if not restrict[i, j].any():
                continue
        P = simplify(P, tol)
        if len(P) < 3:
            continue
        d = P[1] - P[0]
        m = (P[0] + P[1]) / 2
        L = math.hypot(*d) or 1.0
        right = m + np.array([d[1], -d[0]]) / L * grid.cell
        i, j = grid.idx(right[0], right[1])
        if F[i, j] >= 0:
            P = P[::-1]
        out.append([(float(x), float(y), z) for x, y in P])
    return out


def _link(passes, ok, safe_z):
    """Order passes greedily (nearest start next) and join them: a feed move when the straight line stays in
    the cleared area at the same level, otherwise retract / rapid / plunge. Moves: (kind, (x, y, z))."""
    moves, todo, cur = [], list(passes), None
    while todo:
        k = 0 if cur is None else int(np.argmin([math.dist(cur[:2], p[0][:2]) for p in todo]))
        p = todo.pop(k)
        if cur is not None and abs(cur[2] - p[0][2]) < 1e-9 and ok(cur, p[0]):
            moves.append(("feed", p[0]))
        else:
            if cur is not None:
                moves.append(("rapid", (cur[0], cur[1], safe_z)))
            moves.append(("rapid", (p[0][0], p[0][1], safe_z)))
            moves.append(("plunge", p[0]))
        moves += [("feed", q) for q in p[1:]]
        cur = p[-1]
    if cur is not None:
        moves.append(("rapid", (cur[0], cur[1], safe_z)))
    return moves


def _segment_ok(F, grid):
    def ok(a, b):
        n = max(2, int(math.dist(a[:2], b[:2]) / (grid.cell * 0.5)) + 1)
        t = np.linspace(0, 1, n)
        i, j = grid.idx(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        return bool((F[i, j] >= -1e-9).all())
    return ok


def _levels(top, bottom, step, floors):
    lv = list(np.arange(top - step, bottom - 1e-9, -step))
    if not lv or lv[-1] > bottom + 1e-6:
        lv.append(bottom)
    lv += [f for f in floors if bottom - 1e-9 <= f < top]
    lv = sorted(set(round(float(v), 6) for v in lv), reverse=True)
    out = []
    for v in lv:  # merge levels closer than 0.05 mm (keep the lower: floors come out exact)
        if out and out[-1] - v < 0.05:
            out[-1] = v
        else:
            out.append(v)
    return out


def _dilate(mask, dist, cell):
    from scipy.ndimage import distance_transform_edt
    if not mask.any():
        return mask
    return distance_transform_edt(~mask) * cell <= dist


def generate(body, setup: str = "+Z", tool_diameter: float = 6.0, flutes: int = 3, stepdown: float | None = None,
             stepover: float | None = None, leave: float = 0.3, stock_margin: float = 1.0, stock_top: float = 1.0,
             material: str = "aluminum", drill: bool = True, max_drill_diameter: float = 20.0,
             tolerance: float | None = None, safe_height: float = 5.0) -> dict:
    """Roughing, finishing and drilling for `body` seen from `setup`. Returns the operations (machine
    coordinates, origin at the stock's top front-left corner), tools, holes and the stock-removal check."""
    if material not in MATERIALS:
        raise ValueError(f"material must be one of {', '.join(MATERIALS)}")
    if tool_diameter <= 0 or leave < 0:
        raise ValueError("the tool diameter must be positive and the stock to leave not negative")
    R = setup_rotation(setup)
    r = tool_diameter / 2
    stepdown = stepdown or tool_diameter * 0.5
    stepover = stepover or tool_diameter * 0.45
    if stepover > tool_diameter * 0.95:
        raise ValueError("the stepover must be under the tool diameter")
    cell = float(tolerance or min(0.25, max(0.05, tool_diameter / 30)))
    verts, tris = body.tessellate(cell / 4, 0.2)
    V = np.array([(v.X, v.Y, v.Z) for v in verts]) @ R.T
    T = np.array(tris, dtype=int)
    plo, phi = V.min(axis=0), V.max(axis=0)
    stock = np.array([plo[0] - stock_margin, plo[1] - stock_margin, phi[0] + stock_margin, phi[1] + stock_margin])
    shift = np.array([-stock[0], -stock[1], -(phi[2] + stock_top)])  # origin: stock top, front-left corner
    V = V + shift
    stock = stock + [shift[0], shift[1], shift[0], shift[1]]
    top_z, bottom_z = 0.0, float(plo[2] + shift[2])
    pad = r + 3 * cell
    x0, y0 = stock[0] - pad, stock[1] - pad
    n = (int(math.ceil((stock[2] - stock[0] + 2 * pad) / cell)), int(math.ceil((stock[3] - stock[1] + 2 * pad) / cell)))
    if n[0] * n[1] > 6e6:
        raise ValueError(f"the raster would have {n[0] * n[1]:,} cells: raise the tolerance")
    grid = _Grid(x0, y0, cell, n)
    H = heightmap(V, T, x0, y0, cell, n)
    part_H = H.copy()
    box = (stock[0] - r * 0.5, stock[1] - r * 0.5, stock[2] + r * 0.5, stock[3] + r * 0.5)
    safe_z = top_z + safe_height
    warnings = []

    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    floors = []
    for f in body.faces():  # flat floors: upward planar faces
        if BRepAdaptor_Surface(f.wrapped).GetType() != GeomAbs_Plane:
            continue
        c = f.center()
        if (R @ np.array(tuple(f.normal_at(c))))[2] > 0.9999:
            floors.append(float((R @ np.array([c.X, c.Y, c.Z]))[2] + shift[2]))

    holes = []
    if drill:
        for h in find_holes(body, R, max_drill_diameter):
            h = {**h, "x": h["x"] + shift[0], "y": h["y"] + shift[1], "top": h["top"] + shift[2], "bottom": h["bottom"] + shift[2]}
            i, j = grid.idx(h["x"], h["y"])
            if H[i, j] > h["top"] + 1e-3:
                continue  # something above the hole: not reachable from this side
            inside = (grid.X - h["x"]) ** 2 + (grid.Y - h["y"]) ** 2 <= (h["diameter"] / 2 + cell) ** 2
            H[inside] = np.maximum(H[inside], h["top"])  # the mill leaves the hole to the drill
            holes.append({"x": round(float(h["x"]), 4), "y": round(float(h["y"]), 4), "diameter": round(float(h["diameter"]), 4),
                          "top": round(float(h["top"]), 4), "bottom": round(float(h["bottom"]), 4),
                          "through": bool(h["bottom"] <= bottom_z + 1e-3), "depth": round(float(h["top"] - h["bottom"]), 4)})

    mill = {"number": 1, "type": "flat end mill", "diameter": tool_diameter, "flutes": flutes, **feeds(material, tool_diameter, flutes)}
    tools, ops = [mill], []
    levels = _levels(top_z, bottom_z, stepdown, floors)
    rough = []
    for z in _levels(top_z, bottom_z, stepdown, [f + leave for f in floors]):  # roughing stops `leave` above floors
        F = _clear_field(H, grid, z - leave, r + leave + cell, box)
        if (F >= 0).any():
            rough += _link(_passes_zigzag(F, grid, z, stepover) + _passes_contour(F, grid, z, cell * 0.3), _segment_ok(F, grid), safe_z)
    ops.append({"name": f"Rough (Z-level, leave {leave:g} mm)", "kind": "rough", "tool": 1, "moves": rough})
    if leave > 0:
        fin = []
        for z in levels:
            F = _clear_field(H, grid, z, r + cell, box)
            passes = []
            if any(abs(z - f) < 1e-6 for f in floors):
                passes = _passes_zigzag(F, grid, z, stepover, restrict=_dilate(np.abs(H - z) < 1e-3, r + stepover, cell))
            fin += _link(passes + _passes_contour(F, grid, z, cell * 0.3), _segment_ok(F, grid), safe_z)
        ops.append({"name": "Finish walls and floors", "kind": "finish", "tool": 1, "moves": fin})
    for d in sorted({h["diameter"] for h in holes}):
        t = {"number": len(tools) + 1, "type": "drill", "diameter": d, "flutes": 2, **feeds(material, d, 2, drill=True)}
        tools.append(t)
        hs = [h for h in holes if h["diameter"] == d]
        cycles = []
        for h in hs:
            depth = h["bottom"] - (1.0 + 0.3 * d if h["through"] else 0.0)  # through: the drill point breaks out
            cycles.append({"x": h["x"], "y": h["y"], "r": round(h["top"] + 1.0, 4), "z": round(depth, 4),
                           "peck": round(d, 3) if (h["top"] - depth) > 3 * d else None})
        ops.append({"name": f"Drill Ø{d:g} ({len(hs)} hole{'s' if len(hs) > 1 else ''})", "kind": "drill",
                    "tool": t["number"], "cycles": cycles})

    # stock-removal check on the raster
    in_stock = (grid.X >= stock[0]) & (grid.X <= stock[2]) & (grid.Y >= stock[1]) & (grid.Y <= stock[3])
    S = np.where(in_stock, top_z, -np.inf)
    for op in ops:
        if op["kind"] == "drill":
            d = tools[op["tool"] - 1]["diameter"]
            for c in op["cycles"]:
                inside = (grid.X - c["x"]) ** 2 + (grid.Y - c["y"]) ** 2 <= (d / 2 - cell) ** 2  # the faceted wall
                S[inside] = np.minimum(S[inside], c["z"])
        else:
            _sweep(S, grid, op["moves"], r)
    part = np.isfinite(part_H) & in_stock
    Hp, Sp = np.where(part, part_H, 0.0), np.where(in_stock, S, 0.0)
    gouge = float(np.max(np.where(part, Hp - Sp, -np.inf))) if part.any() else 0.0
    left = np.where(part, Sp - Hp, 0.0)
    left_outside = np.where(in_stock & ~part, np.maximum(Sp - bottom_z, 0.0), 0.0)
    verify = {"gouge_mm": round(max(gouge, 0.0), 4), "max_left_mm": round(float(left.max()), 4),
              "material_left_mm3": round(float((left.sum() + left_outside.sum()) * cell * cell), 2),
              "area_over_0.1mm_pct": round(100.0 * float((left > 0.1).sum()) / max(1, int(part.sum())), 2)}
    if verify["gouge_mm"] > 2 * cell:
        warnings.append("the stock-removal check found the tool inside the part")
    total = TOOL_CHANGE_S / 60 * len(tools)
    for op in ops:
        length, tmin = _op_time(op, tools[op["tool"] - 1])
        op["length_mm"], op["time_min"] = round(length, 1), round(tmin, 3)
        total += tmin
    return {"setup": setup.upper(), "material": material, "cell": cell, "tool_diameter": tool_diameter, "leave": leave,
            "stock": {"size": [round(float(stock[2] - stock[0]), 3), round(float(stock[3] - stock[1]), 3),
                               round(float(phi[2] - plo[2] + stock_top), 3)], "margin": stock_margin, "top": stock_top},
            "tools": tools, "holes": holes, "levels": [round(z, 4) for z in levels], "operations": ops,
            "verification": verify, "cycle_time_min": round(float(total), 2), "warnings": warnings,
            "_R": R, "_shift": shift, "safe_z": safe_z, "_raster": (grid, part_H, S)}


def _sweep(S, grid, moves, r):
    """Lower the stock height map to every feed / plunge position of a flat end mill of radius r."""
    from scipy.ndimage import distance_transform_edt
    pts, prev = [], None
    for kind, p in moves:
        if kind != "rapid" and prev is not None:
            a, b = np.array(prev if kind == "feed" else p), np.array(p)
            k = max(2, int(math.dist(a[:2], b[:2]) / (grid.cell * 0.5)) + 1)
            pts.append(a + (b - a) * np.linspace(0, 1, k)[:, None])
        prev = p
    if not pts:
        return
    P = np.vstack(pts)
    for z in np.unique(np.round(P[:, 2], 6)):
        Q = P[np.abs(P[:, 2] - z) < 1e-6]
        i, j = grid.idx(Q[:, 0], Q[:, 1])
        mark = np.zeros(S.shape, bool)
        mark[i, j] = True
        reach = distance_transform_edt(~mark) * grid.cell <= r
        np.minimum(S, np.where(reach, z, np.inf), out=S)


def _op_time(op, tool):
    if op["kind"] == "drill":
        length = 0.0
        for c in op["cycles"]:
            depth = c["r"] - c["z"]
            length += depth * (1 + (depth / c["peck"] if c["peck"] else 0))  # each peck goes back to R
        rap = sum(math.dist((a["x"], a["y"]), (b["x"], b["y"])) for a, b in zip(op["cycles"], op["cycles"][1:]))
        return length, length / tool["feed"] + rap / RAPID
    feed_len, tmin, prev = 0.0, 0.0, None
    for kind, p in op["moves"]:
        if prev is not None:
            d = math.dist(prev, p)
            if kind == "rapid":
                tmin += d / RAPID
            else:
                feed_len += d
                tmin += d / (tool["plunge"] if kind == "plunge" else tool["feed"])
        prev = p
    return feed_len, tmin


def _f(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def gcode(result: dict, post: str = "fanuc", program: int = 1000, name: str = "PART") -> str:
    """G-code for the operations: fanuc / linuxcnc (canned drilling cycles, G43 length offsets) or grbl (no
    tool changer: M0 pauses; drilling written out as moves)."""
    if post not in POSTS:
        raise ValueError(f"post must be one of {', '.join(POSTS)}")
    grbl = post == "grbl"
    tools, safe = result["tools"], result["safe_z"]
    L = [] if grbl else ["%", f"O{program} ({name.upper()[:40]})"]
    L.append(f"(openswx CAM - {name} - setup {result['setup']} - material {result['material']})")
    L.append(f"(stock {' x '.join(_f(v) for v in result['stock']['size'])} mm - origin: stock top, front-left corner)")
    for t in tools:
        L.append(f"(T{t['number']} {t['type']} D{_f(t['diameter'])} - S{t['rpm']} F{_f(t['feed'])})")
    L.append("G21 G90 G94 G17" + ("" if grbl else " G40 G49 G80"))
    if post == "linuxcnc":
        L.append("G64 P0.01")
    current = None
    for op in result["operations"]:
        t = tools[op["tool"] - 1]
        L.append(f"({op['name']})")
        first = current != t["number"]
        if first:
            if current is not None:
                L += ["M5", "M9"]
            L.append(f"M0 (load T{t['number']} {t['type']} D{_f(t['diameter'])})" if grbl else f"T{t['number']} M6")
            L += [f"S{t['rpm']} M3", "M8"]
            current = t["number"]
        if op["kind"] == "drill":
            cyc = op["cycles"]
            L.append(f"G0 X{_f(cyc[0]['x'])} Y{_f(cyc[0]['y'])}")
            L.append(f"G43 Z{_f(safe)} H{t['number']}" if not grbl and first else f"G0 Z{_f(safe)}")
            if grbl:
                for c in cyc:
                    L += [f"G0 X{_f(c['x'])} Y{_f(c['y'])}", f"G0 Z{_f(c['r'])}"]
                    z = c["r"]
                    while z > c["z"] + 1e-9:
                        z = max(c["z"], z - (c["peck"] or 1e9))
                        L.append(f"G1 Z{_f(z)} F{_f(t['feed'])}")
                        if z > c["z"] + 1e-9:
                            L.append(f"G0 Z{_f(c['r'])}")
                    L.append(f"G0 Z{_f(safe)}")
            else:
                for k, c in enumerate(cyc):
                    xy = f"X{_f(c['x'])} Y{_f(c['y'])}"
                    if k == 0:
                        q = f" Q{_f(c['peck'])}" if c["peck"] else ""
                        L.append(f"G98 {'G83' if c['peck'] else 'G81'} {xy} Z{_f(c['z'])} R{_f(c['r'])}{q} F{_f(t['feed'])}")
                    else:
                        L.append(xy)
                L.append("G80")
            continue
        last, started, feed = {}, False, None
        for kind, p in op["moves"]:
            x, y, z = (_f(v) for v in p)
            if kind == "rapid" and not started:
                L.append(f"G0 X{x} Y{y}")
                L.append(f"G43 Z{z} H{t['number']}" if not grbl and first else f"G0 Z{z}")
                started = True
            else:
                words = [f"{a}{v}" for a, v in (("X", x), ("Y", y), ("Z", z)) if last.get(a) != v]
                if not words:
                    continue
                if kind == "rapid":
                    L.append("G0 " + " ".join(words))
                else:
                    f = t["plunge"] if kind == "plunge" else t["feed"]
                    L.append(f"G1 {' '.join(words)}" + (f" F{_f(f)}" if f != feed else ""))
                    feed = f
            last = {"X": x, "Y": y, "Z": z}
    L += ["M5", "M9", f"G0 Z{_f(safe)}"]
    L += ["M30"] if grbl else ["G28 G91 Z0", "G90", "M30", "%"]
    return "\n".join(L) + "\n"


def toolpath_display(result: dict) -> list[dict]:
    """Operations as polylines in part coordinates for the 3-D view: [{op, kind, feed: [[x,y,z,...]], rapid}]."""
    Rt, shift = result["_R"].T, result["_shift"]

    def back(p):
        return [round(float(v), 4) for v in Rt @ (np.asarray(p, float) - shift)]

    out = []
    for op in result["operations"]:
        feed, rapid = [], []
        if op["kind"] == "drill":
            for c in op["cycles"]:
                feed.append(back((c["x"], c["y"], c["r"])) + back((c["x"], c["y"], c["z"])))
        else:
            cur, prev = [], None
            for kind, p in op["moves"]:
                if kind == "rapid":
                    if len(cur) > 3:
                        feed.append(cur)
                    cur = []
                    if prev is not None:
                        rapid.append(back(prev) + back(p))
                else:
                    if not cur and prev is not None:
                        cur = back(prev)
                    cur = cur + back(p)
                prev = p
            if len(cur) > 3:
                feed.append(cur)
        out.append({"op": op["name"], "kind": op["kind"], "feed": feed, "rapid": rapid})
    return out


def tool_profile(kind: str, radius: float, corner_radius: float, d: np.ndarray) -> np.ndarray:
    """Height of the tool's cutting surface above its tip at radial distance d from the axis (d <= radius):
    flat 0; ball r - sqrt(r2 - d2); bull nose flat in the middle with a corner radius."""
    if kind == "flat":
        return np.zeros_like(d)
    if kind == "ball":
        return radius - np.sqrt(np.maximum(radius * radius - d * d, 0.0))
    c = corner_radius
    e = np.maximum(d - (radius - c), 0.0)
    return c - np.sqrt(np.maximum(c * c - e * e, 0.0))


def drop_cutter(H: np.ndarray, cell: float, kind: str, radius: float, corner_radius: float = 0.0) -> np.ndarray:
    """Tool-tip height that just touches the height map H at every cell: the highest of (surface under the
    tool - the tool's own profile there), over the tool's footprint. The tool can go no lower without cutting
    into the part, so a path on this surface cannot gouge (to within the raster)."""
    k = int(math.floor(radius / cell))
    out = np.full(H.shape, -np.inf)
    nx, ny = H.shape
    for di in range(-k, k + 1):
        for dj in range(-k, k + 1):
            d = cell * math.hypot(di, dj)
            if d > radius:
                continue
            p = float(tool_profile(kind, radius, corner_radius, np.array(d)))
            src = H[max(0, di):nx + min(0, di), max(0, dj):ny + min(0, dj)]
            dst = out[max(0, -di):nx + min(0, -di), max(0, -dj):ny + min(0, -dj)]
            np.maximum(dst, src - p, out=dst)
    return out


def _thin(points: list[tuple], tol: float) -> list[tuple]:
    """A polyline with the points dropped that lie within tol of the chord that replaces them (greedy: each
    chord is stretched as far as every point it skips allows)."""
    if len(points) < 3:
        return points
    P = np.asarray(points, float)
    out, a, i = [points[0]], 0, 2
    while i < len(P):
        seg = P[i] - P[a]
        L = float(np.linalg.norm(seg))
        mid = P[a + 1:i] - P[a]
        dev = (np.linalg.norm(np.cross(mid, seg), axis=1) / L).max() if L > 1e-12 else np.linalg.norm(mid, axis=1).max()
        if dev > tol:
            a = i - 1
            out.append(points[a])
        i += 1
    out.append(points[-1])
    return out


def finish_3d(result: dict, tool_diameter: float = 6.0, tool: str = "ball", corner_radius: float | None = None,
              stepover: float | None = None, direction: str = "x", leave: float = 0.0, flutes: int = 2) -> dict:
    """Add a 3D parallel finishing operation to a generate() result: straight passes `stepover` apart, the
    tool riding on the drop-cutter surface of the part. Returns the operation (also appended to the result)."""
    if tool not in ("ball", "bull", "flat"):
        raise ValueError("tool must be ball, bull or flat")
    if direction not in ("x", "y"):
        raise ValueError("direction must be x or y")
    r = tool_diameter / 2
    if r <= 0:
        raise ValueError("the tool diameter must be positive")
    c = 0.0
    if tool == "bull":
        c = float(corner_radius if corner_radius is not None else r / 4)
        if not 0 < c < r:
            raise ValueError("a bull-nose corner radius must be between 0 and the tool radius")
    grid, part_H, _S = result["_raster"]
    cell = grid.cell
    if r < 2 * cell:
        raise ValueError(f"the tool is too small for this raster ({cell:g} mm cells): regenerate with a finer tolerance")
    stepover = float(stepover or (0.15 * tool_diameter if tool == "ball" else 0.4 * tool_diameter))
    if not 0 < stepover < tool_diameter:
        raise ValueError("the stepover must be between 0 and the tool diameter")
    finite = np.isfinite(part_H)
    if not finite.any():
        raise ValueError("there is no part under the tool")
    floor = float(part_H[finite].min())
    H = np.where(finite, part_H, floor)  # beside the part the tool may go down to its lowest level
    Z = drop_cutter(H, cell, tool, r, c) + leave
    if direction == "y":
        Z, xs, ys = Z.T, grid.ys, grid.xs
    else:
        xs, ys = grid.xs, grid.ys
    # passes over the part's own extent (plus the tool radius, so edges are finished from outside)
    rows = np.nonzero(finite.any(axis=0) if direction == "x" else finite.any(axis=1))[0]
    cols = np.nonzero(finite.any(axis=1) if direction == "x" else finite.any(axis=0))[0]
    pad = int(math.ceil(r / cell))
    j0, j1 = max(0, rows.min() - pad), min(len(ys) - 1, rows.max() + pad)
    i0, i1 = max(0, cols.min() - pad), min(len(xs) - 1, cols.max() + pad)
    step = max(1, int(round(stepover / cell)))
    safe = result["safe_z"]
    moves, flip, tol = [], False, cell * 0.05
    for j in range(j0, j1 + 1, step):
        idx = range(i1, i0 - 1, -1) if flip else range(i0, i1 + 1)
        line = [(float(xs[i]), float(ys[j]), float(Z[i, j])) for i in idx]
        if direction == "y":
            line = [(p[1], p[0], p[2]) for p in line]
        line = _thin(line, tol)
        moves.append(("rapid", (line[0][0], line[0][1], safe)))
        moves.append(("plunge", line[0]))
        moves += [("feed", p) for p in line[1:]]
        moves.append(("rapid", (line[-1][0], line[-1][1], safe)))
        flip = not flip
    number = len(result["tools"]) + 1
    t = {"number": number, "type": {"ball": "ball end mill", "bull": "bull nose end mill", "flat": "flat end mill"}[tool],
         "diameter": tool_diameter, "flutes": flutes, **feeds(result["material"], tool_diameter, flutes)}
    if tool == "bull":
        t["corner_radius"] = c
    result["tools"].append(t)
    actual = step * cell
    scallop = (r - math.sqrt(max(r * r - (actual / 2) ** 2, 0.0))) if tool == "ball" else \
              (c - math.sqrt(max(c * c - min(actual / 2, c) ** 2, 0.0))) if tool == "bull" else 0.0
    op = {"name": f"Finish 3D parallel ({t['type']} D{tool_diameter:g}, stepover {actual:.3g} mm)", "kind": "finish3d",
          "tool": number, "moves": moves, "stepover": round(actual, 4), "scallop_mm": round(scallop, 5), "leave": leave}
    length, tmin = _op_time(op, t)
    op["length_mm"], op["time_min"] = round(length, 1), round(tmin, 3)
    result["operations"].append(op)
    result["cycle_time_min"] = round(float(result["cycle_time_min"] + tmin + TOOL_CHANGE_S / 60), 2)
    return op
