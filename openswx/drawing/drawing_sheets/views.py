"""Drawing views: directions, projection geometry, placement and picking."""
from __future__ import annotations

import math

import numpy as np
from build123d import Compound, Keep, Plane, Vector

from openswx.drawing import drawing as D
from openswx.drawing.drawing_sheets.helpers import (
    DrawingError,
    _bands,
    _clip_hatch,
    _clip_polys,
    _corners,
    _fit_circle,
    _frame_of,
    _kept,
    _unit,
    _warp,
    _wire_points,
    find_view,
    sheet_of,
    to2d,
    to3d,
    view_scale,
)


ORIENTATIONS = {**D.VIEW_DIRS, "dimetric": ((1, 0.5, 1), (0, 1, 0)), "trimetric": ((1, 0.75, 0.55), (0, 1, 0))}
LABELS = {"front": "FRONT", "back": "BACK", "top": "TOP", "bottom": "BOTTOM", "left": "LEFT", "right": "RIGHT",
          "isometric": "ISOMETRIC", "dimetric": "DIMETRIC", "trimetric": "TRIMETRIC"}
KINDS = ("model", "projected", "section", "detail", "auxiliary")


def frame(st: dict, view: dict):
    """The view's (d, up, x), following its parents."""
    kind = view["kind"]
    if kind == "model":
        d, up = ORIENTATIONS[view.get("orientation", "front")]
        return _frame_of(d, up)
    parent = find_view(st, view["parent"])
    pd, pu, px = frame(st, parent)
    if kind == "projected":
        side = view["side"]
        sheet = sheet_of(st, view["id"])
        if sheet.get("projection", "third") == "first":  # first angle: the view placed right shows the left side
            side = {"right": "left", "left": "right", "top": "bottom", "bottom": "top"}[side]
        return {"right": _frame_of(px, pu), "left": _frame_of(-px, pu), "top": _frame_of(pu, -pd),
                "bottom": _frame_of(-pu, pd)}[side]
    if kind == "detail":
        return pd, pu, px
    if kind == "section":
        a, b = (np.asarray(view[k], float) for k in ("a", "b"))
        t3 = _unit(px * (b - a)[0] + pu * (b - a)[1])  # the cut line in the model
        n = _unit(np.cross(t3, pd))  # the cutting plane holds the line and the parent's view direction
        if view.get("flip"):
            n = -n
        return _frame_of(n, pd)
    if kind == "auxiliary":
        e = np.asarray(view["direction"], float)
        t3 = _unit(px * e[0] + pu * e[1])  # an inclined edge seen in the parent: look square to it
        n = _unit(np.cross(t3, pd))
        if view.get("flip"):
            n = -n
        return _frame_of(n, pd)
    raise DrawingError(f"unknown view kind {kind!r}")


_FAR = 1e5


def _project(shape, fr):
    d, up, _x = fr
    vis, hid = shape.project_to_viewport(tuple(d * _FAR), tuple(up), (0.0, 0.0, 0.0))
    return D._polys(vis), D._polys(hid)


def _section_shape(body, st: dict, view: dict):
    """The body cut by the section plane, keeping the material behind it (away from the viewer), and the
    plane's origin."""
    parent = find_view(st, view["parent"])
    pfr = frame(st, parent)
    fr = frame(st, view)
    p0 = to3d(view["a"], pfr)
    pl = Plane(origin=tuple(p0), z_dir=tuple(fr[0]))
    kept = body.split(pl, keep=Keep.BOTTOM)
    shapes = kept if isinstance(kept, (list, tuple)) else [kept]
    solids = [s for sh in shapes if sh is not None for s in sh.solids()]
    if not solids:
        raise DrawingError("the section line does not cut the part")
    return Compound(solids), p0


def view_geometry(doc, st: dict, view: dict, cache: dict | None = None) -> dict:
    """{"vis", "hid", "box", "hatch": [[pts]], "frame"} of a view in its 2D model coordinates."""
    key = view["id"]
    if cache is not None and key in cache:
        return cache[key]
    body = doc.body
    if body is None:
        raise DrawingError("the part has no body to draw")
    fr = frame(st, view)
    hatch = []
    if view["kind"] == "section":
        shape, p0 = _section_shape(body, st, view)
        vis, hid = _project(shape, fr)
        d = fr[0]
        for f in shape.faces():  # the cut faces lie in the section plane
            if f.geom_type.name.lower() != "plane":
                continue
            n = f.normal_at()
            if abs(abs(n.dot(Vector(*d))) - 1) > 1e-6 or abs(float((np.array(tuple(f.center())) - p0) @ d)) > 1e-6:
                continue
            for w in [f.outer_wire()] + list(f.inner_wires()):
                hatch.append([to2d(tuple(q), fr) for q in _wire_points(w)])
    elif view["kind"] == "detail":
        g = view_geometry(doc, st, find_view(st, view["parent"]), cache)
        vis, hid = g["vis"], g["hid"]
    elif view.get("broken_out"):
        shape, hatch = _broken_out(body, fr, view["broken_out"])
        vis, hid = _project(shape, fr)
    else:
        vis, hid = _project(body, fr)
    if view["kind"] == "detail":
        cx, cy = view["center"]
        r = float(view["radius"])
        box = (cx - r, cy - r, cx + r, cy + r)
        out = {"vis": vis, "hid": hid, "box": box, "hatch": hatch, "frame": fr, "verts": g.get("verts"), "warp": None}
    else:
        crop, bands = view.get("crop"), _bands(view)
        verts = _corners(vis + hid)
        if crop or bands:
            vis, hid = _clip_polys(vis, crop, bands), _clip_polys(hid, crop, bands)
            hatch = _clip_hatch(hatch, crop, bands)
            verts = [q for q in verts if _kept(q, crop, bands)]
        warp = _warp(bands, BREAK_GAP / view_scale(st, view)) if bands else None
        box = D._bbox([[warp[0](*q) for q in p] for p in vis + hid] if warp else vis + hid)
        out = {"vis": vis, "hid": hid, "box": box, "hatch": hatch, "frame": fr, "verts": verts, "warp": warp}
    if cache is not None:
        cache[key] = out
    return out


BREAK_GAP = 6.0  # sheet mm between the two break lines of a broken view


def _broken_out(body, fr, cuts: list[dict]):
    """The body with each broken-out section removed - a closed profile (view coordinates: "profile" points or
    "center" + "radius") cut from the viewer down to "depth" below the body's nearest point - and the cut
    floors to hatch."""
    from build123d import Face, Solid, Wire
    d = np.asarray(fr[0], float)
    bb = body.bounding_box()
    corners = [np.array([x, y, z]) for x in (bb.min.X, bb.max.X) for y in (bb.min.Y, bb.max.Y) for z in (bb.min.Z, bb.max.Z)]
    front = max(float(c @ d) for c in corners)
    reach = float(bb.diagonal) + 1.0
    shape, levels = body, []
    for c in cuts:
        level = front - float(c["depth"])
        if c.get("radius"):
            w = Wire.make_circle(float(c["radius"]), Plane(origin=tuple(to3d(c["center"], fr, level)), z_dir=tuple(d)))
        else:
            w = Wire.make_polygon([tuple(to3d(q, fr, level)) for q in c["profile"]], close=True)
        shape = shape.cut(Solid.extrude(Face(w), Vector(*(d * reach))))
        levels.append(level)
    hatch = []
    for f in shape.faces():
        if f.geom_type.name.lower() != "plane":
            continue
        if abs(abs(f.normal_at().dot(Vector(*d))) - 1) > 1e-6:
            continue
        lv = float(np.array(tuple(f.center())) @ d)
        if any(abs(lv - L) < 1e-6 for L in levels):
            for w in [f.outer_wire()] + list(f.inner_wires()):
                hatch.append([to2d(tuple(q), fr) for q in _wire_points(w)])
    return shape, hatch


def placement(st: dict, view: dict, g: dict):
    """2D model coordinates -> sheet mm for the view (its centre at (x, y), y down)."""
    s = view_scale(st, view)
    b = g["box"]
    mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    cx, cy = view["x"], view["y"]
    if g.get("warp"):  # a broken view: the bands between break lines are closed up
        w = g["warp"][0]
        return lambda x, y: (lambda u, v: (cx + (u - mx) * s, cy - (v - my) * s))(*w(x, y))
    return lambda x, y: (cx + (x - mx) * s, cy - (y - my) * s)


def half_size(st: dict, view: dict, g: dict) -> tuple[float, float]:
    s = view_scale(st, view)
    b = g["box"]
    return (b[2] - b[0]) * s / 2, (b[3] - b[1]) * s / 2


def align(st: dict, view: dict, cache: dict, doc) -> None:
    """Projected views stay aligned with their parent (SOLIDWORKS keeps them on the projection line)."""
    if view["kind"] != "projected" or view.get("aligned") is False:
        return
    parent = find_view(st, view["parent"])
    align(st, parent, cache, doc)
    if view["side"] in ("left", "right"):
        view["y"] = parent["y"]
    else:
        view["x"] = parent["x"]


def snap_vertex(g: dict, xy, tol: float):
    """The view-geometry vertex (polyline end or corner) nearest to xy within tol, else None."""
    best, bd = None, tol
    verts = g.get("verts")  # the model's corners (not where a crop or break cuts an edge)
    if verts is None:
        verts = _corners(g["vis"] + g["hid"])
    for q in verts:
        dd = math.hypot(q[0] - xy[0], q[1] - xy[1])
        if dd < bd:
            best, bd = q, dd
    return best


def pick_circle(g: dict, xy, tol: float):
    """(centre, radius) of the curved edge nearest to xy (a circle or arc seen face-on), else None."""
    best, bd = None, tol
    for poly in g["vis"] + g["hid"]:
        if len(poly) < 5:
            continue
        dd = min(math.hypot(q[0] - xy[0], q[1] - xy[1]) for q in poly)
        if dd >= bd:
            continue
        c = _fit_circle(poly)  # not an ellipse or spline seen in this view
        if c is not None:
            best, bd = c, dd
    return best


def view_circles(g: dict) -> list:
    """Full circles seen face-on in a view: [(centre, radius)], each once."""
    out = []
    for poly in g["vis"] + g["hid"]:
        if len(poly) < 5 or math.dist(poly[0], poly[-1]) > 1e-6:
            continue
        c = _fit_circle(poly)
        if c and not any(math.dist(c[0], o[0]) < 1e-6 and abs(c[1] - o[1]) < 1e-6 for o in out):
            out.append(c)
    return out


def pick_line(g: dict, xy, tol: float):
    """The straight edge (a 2-point polyline) nearest to xy within tol, as (a, b), else None."""
    best, bd = None, tol
    q = np.asarray(xy, float)
    for poly in g["vis"] + g["hid"]:
        if len(poly) != 2:
            continue
        a, b = np.asarray(poly[0], float), np.asarray(poly[1], float)
        ab = b - a
        L = float(ab @ ab)
        if L < 1e-12:
            continue
        s = max(0.0, min(1.0, float((q - a) @ ab) / L))
        dd = float(np.linalg.norm(a + ab * s - q))
        if dd < bd:
            best, bd = (tuple(poly[0]), tuple(poly[1])), dd
    return best


def lines_angle(l1, l2, at):
    """(apex, ray 1, ray 2, degrees) for the angle between two lines in the quadrant holding `at`; None when
    the lines are parallel."""
    p1, p2 = np.asarray(l1[0], float), np.asarray(l2[0], float)
    d1, d2 = np.asarray(l1[1], float) - p1, np.asarray(l2[1], float) - p2
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9 * np.linalg.norm(d1) * np.linalg.norm(d2):
        return None
    w = p2 - p1
    s = (w[0] * d2[1] - w[1] * d2[0]) / den
    apex = p1 + d1 * s
    u1, u2 = d1 / np.linalg.norm(d1), d2 / np.linalg.norm(d2)
    w = np.asarray(at, float) - apex
    det = u1[0] * u2[1] - u1[1] * u2[0]  # w = a u1 + b u2: `at` lies in the sector between sign(a) u1 and sign(b) u2
    a = (w[0] * u2[1] - w[1] * u2[0]) / det
    b = (u1[0] * w[1] - u1[1] * w[0]) / det
    if a < 0:
        u1 = -u1
    if b < 0:
        u2 = -u2
    return apex, u1, u2, math.degrees(math.acos(max(-1.0, min(1.0, float(u1 @ u2)))))


def line_gap(l1, l2) -> float:
    """Distance between two parallel lines."""
    p1, p2 = np.asarray(l1[0], float), np.asarray(l2[0], float)
    u = np.asarray(l1[1], float) - p1
    u = u / np.linalg.norm(u)
    w = p2 - p1
    return abs(float(w[0] * u[1] - w[1] * u[0]))


def component_at(doc, st: dict, view: dict, xy):
    """The assembly component seen at view point xy (the one nearest the viewer), else None."""
    from build123d import Axis
    if getattr(doc, "kind", "part") != "assembly":
        return None
    fr = frame(st, find_view(st, view["parent"]) if view["kind"] == "detail" else view)
    d = np.asarray(fr[0], float)
    p = to3d(xy, fr)
    ray = Axis(tuple(p + d * 1e5), tuple(-d))
    best, depth = None, -math.inf
    for c, shape in doc.world_shapes():
        for q, _n in shape.find_intersection_points(ray):
            z = float(np.asarray(tuple(q)) @ d)
            if z > depth:
                best, depth = c, z
    return best
