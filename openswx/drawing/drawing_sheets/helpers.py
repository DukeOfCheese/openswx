"""Primitives shared by the drawing sheet modules: frames, lookups, clipping and SVG helpers."""
from __future__ import annotations

import math

import numpy as np


class DrawingError(ValueError):
    pass


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    if n < 1e-12:
        raise DrawingError("zero direction")
    return v / n


def _frame_of(d, up):
    """(direction towards the viewer, up, x) as unit vectors; up is made square to d."""
    d = _unit(d)
    up = np.asarray(up, float)
    up = up - d * float(up @ d)
    if np.linalg.norm(up) < 1e-9:  # looking straight along the given up: take another
        up = np.array([0.0, 0.0, -1.0]) if abs(d[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
        up = up - d * float(up @ d)
    up = _unit(up)
    return d, up, np.cross(up, d)


def to2d(p, fr) -> tuple[float, float]:
    _d, up, x = fr
    p = np.asarray(p, float)
    return float(p @ x), float(p @ up)


def to3d(xy, fr, depth: float = 0.0) -> np.ndarray:
    d, up, x = fr
    return x * xy[0] + up * xy[1] + d * depth


def sheet_of(st: dict, view_id: str) -> dict:
    for sh in st.get("sheets", []):
        if any(v["id"] == view_id for v in sh["views"]):
            return sh
    raise DrawingError(f"no view {view_id!r}")


def find_view(st: dict, view_id: str) -> dict:
    for sh in st.get("sheets", []):
        for v in sh["views"]:
            if v["id"] == view_id:
                return v
    raise DrawingError(f"no view {view_id!r}")


def active_sheet(st: dict) -> dict:
    sheets = st.get("sheets") or []
    if not sheets:
        raise DrawingError("the drawing has no sheets (start one with drawing_new)")
    return sheets[min(max(int(st.get("active", 0)), 0), len(sheets) - 1)]


def next_id(st: dict, prefix: str) -> str:
    used = {v["id"] for sh in st.get("sheets", []) for v in sh["views"]}
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


def view_scale(st: dict, view: dict) -> float:
    if view.get("scale"):
        return float(view["scale"])
    if view["kind"] == "detail":
        return view_scale(st, find_view(st, view["parent"])) * float(view.get("magnify", 2.0))
    if view["kind"] in ("projected", "section", "auxiliary"):
        return view_scale(st, find_view(st, view["parent"]))
    return float(sheet_of(st, view["id"]).get("scale") or 1.0)


def _corners(polys) -> list:
    """Snap points: the ends of each edge's polyline (a closed loop - a full circle - has no corner)."""
    out = []
    for poly in polys:
        if len(poly) > 2 and math.dist(poly[0], poly[-1]) < 1e-6:
            continue
        out += [poly[0], poly[-1]] if len(poly) > 2 else list(poly)
    return out


def _bands(view: dict) -> list[tuple[int, float, float]]:
    """The view's break bands (axis 0: x, 1: y; low, high) in view coordinates, in increasing order."""
    out = [(0 if b["orientation"] == "vertical" else 1, min(b["a"], b["b"]), max(b["a"], b["b"])) for b in view.get("breaks", [])]
    return sorted(out)


def _kept(q, crop, bands) -> bool:
    if crop and not (crop[0] - 1e-9 <= q[0] <= crop[2] + 1e-9 and crop[1] - 1e-9 <= q[1] <= crop[3] + 1e-9):
        return False
    return not any(lo + 1e-9 < q[ax] < hi - 1e-9 for ax, lo, hi in bands)


def _seg_keep(p, q, crop, bands) -> list[tuple[float, float]]:
    """The parameter intervals of segment p-q inside the crop rectangle and outside every break band."""
    t0, t1 = 0.0, 1.0
    if crop:
        for i, lo, hi in ((0, crop[0], crop[2]), (1, crop[1], crop[3])):
            a, b = p[i], q[i]
            if abs(b - a) < 1e-12:
                if a < lo - 1e-9 or a > hi + 1e-9:
                    return []
                continue
            ta, tb = sorted(((lo - a) / (b - a), (hi - a) / (b - a)))
            t0, t1 = max(t0, ta), min(t1, tb)
        if t1 - t0 <= 1e-12:
            return []
    ivs = [(t0, t1)]
    for i, lo, hi in bands:
        a, b = p[i], q[i]
        if abs(b - a) < 1e-12:
            if lo + 1e-9 < a < hi - 1e-9:
                return []
            continue
        ta, tb = sorted(((lo - a) / (b - a), (hi - a) / (b - a)))
        new = []
        for s0, e0 in ivs:
            if tb <= s0 or ta >= e0:
                new.append((s0, e0))
                continue
            if ta > s0:
                new.append((s0, ta))
            if tb < e0:
                new.append((tb, e0))
        ivs = new
    return [(s0, e0) for s0, e0 in ivs if e0 - s0 > 1e-12]


def _clip_polys(polys, crop, bands):
    """Polylines cut to the crop rectangle and out of the break bands (a piece per kept run)."""
    out = []
    for poly in polys:
        cur = None
        for p, q in zip(poly, poly[1:]):
            for s0, e0 in _seg_keep(p, q, crop, bands):
                a = (p[0] + (q[0] - p[0]) * s0, p[1] + (q[1] - p[1]) * s0)
                b = (p[0] + (q[0] - p[0]) * e0, p[1] + (q[1] - p[1]) * e0)
                if cur is not None and s0 < 1e-12:
                    cur.append(b)
                else:
                    if cur is not None:
                        out.append(cur)
                    cur = [a, b]
                if e0 < 1 - 1e-12:
                    out.append(cur)
                    cur = None
        if cur is not None:
            out.append(cur)
    return [[(float(x), float(y)) for x, y in c] for c in out]


def _half(poly, i: int, v: float, keep_low: bool):
    """Sutherland-Hodgman: the part of a polygon with coordinate i <= v (keep_low) or >= v."""
    def inside(q):
        return q[i] <= v + 1e-12 if keep_low else q[i] >= v - 1e-12

    def cross(p, q):
        t = (v - p[i]) / (q[i] - p[i])
        return p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
    out = []
    for k in range(len(poly)):
        p, q = poly[k - 1], poly[k]
        if inside(q):
            if not inside(p):
                out.append(cross(p, q))
            out.append(q)
        elif inside(p):
            out.append(cross(p, q))
    return out


def _clip_hatch(polys, crop, bands):
    out = []
    for poly in polys:
        parts = [poly]
        if crop:
            for i, v, low in ((0, crop[0], False), (0, crop[2], True), (1, crop[1], False), (1, crop[3], True)):
                parts = [_half(q, i, v, low) for q in parts]
        for i, lo, hi in bands:
            parts = [x for q in parts for x in (_half(q, i, lo, True), _half(q, i, hi, False))]
        out += [q for q in parts if len(q) >= 3]
    return out


def _warp(bands, gap: float):
    """(forward, inverse) maps closing up each break band to the gap between its break lines."""
    def fwd(x, y):
        xy = [x, y]
        for i, lo, hi in bands:
            if (x, y)[i] >= hi - 1e-9:
                xy[i] -= hi - lo - gap
        return xy[0], xy[1]

    def inv(x, y):
        xy = [x, y]
        for i, lo, hi in bands:  # in increasing order: each step undoes one band
            if xy[i] >= lo + gap / 2:
                xy[i] += hi - lo - gap
        return xy[0], xy[1]
    return fwd, inv


def _wire_points(w):
    pts = []
    for e in w.edges():
        n = 2 if e.geom_type.name.lower() == "line" else 32
        pts += [tuple(e.position_at(i / (n - 1))) for i in range(n)]
    return pts


def _polyline(pts, style: str) -> str:
    return f'<polyline points="{" ".join(f"{a:.3f},{b:.3f}" for a, b in pts)}" fill="none" {style}/>'


VIS = 'stroke="#000" stroke-width="0.35" stroke-linejoin="round"'
HID = 'stroke="#555" stroke-width="0.18" stroke-dasharray="1.6 0.9"'
THIN = 'stroke="#000" stroke-width="0.25"'


def _fit_circle(poly):
    """(centre, radius) when the polyline is a circular arc / circle in the view, else None."""
    if len(poly) < 5:
        return None
    n = len(poly) - 1
    (ax, ay), (bx, by), (cx, cy) = poly[0], poly[n // 3], poly[2 * n // 3]
    den = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(den) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / den
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / den
    r = math.hypot(ax - ux, ay - uy)
    if max(abs(math.hypot(q[0] - ux, q[1] - uy) - r) for q in poly) > max(1e-3, r * 1e-3):
        return None
    return (ux, uy), r
