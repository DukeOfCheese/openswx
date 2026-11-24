"""Drawing documents: sheets holding views the user places, as in a SOLIDWORKS drawing.

State (kept in the model document's `drawing` dict, saved with it):
    {"sheets": [sheet, ...], "active": index}
    sheet = {"name", "size" ("A4".."A1"), "scale", "projection" ("third" | "first"), "views": [view, ...],
             "notes", "datums", "gdt" (annotations in sheet mm), "revisions": [row, ...], "tables": [table, ...]}
    view  = {"id", "kind", "x", "y" (sheet mm, centre of the view), "scale" (None: the sheet's),
             "hidden" ("visible" | "removed"), "dims" (show the model's sketch dimensions), "label", ...kind fields}
Kinds: "model" (orientation: front / back / top / bottom / left / right / isometric / dimetric / trimetric),
"projected" (parent, side: left / right / top / bottom; aligned to the parent as in SOLIDWORKS), "section"
(parent, cut line a-b in the parent's 2D model coordinates, flip), "detail" (parent, centre and radius in the
parent's 2D model coordinates, magnification), "auxiliary" (parent, direction in the parent's 2D coordinates:
the view looks square to an inclined edge).

A view's 2D model coordinates are (p . x, p . up) for its direction d (towards the viewer), up and
x = up x d: the kernel's hidden-line projection is taken with the camera looking at the world origin, which
gives exactly these coordinates, so cutting lines and detail circles map back to the model.
"""
from __future__ import annotations

import math
import re
from xml.sax.saxutils import escape

import numpy as np
from build123d import Compound, Keep, Plane, Vector

from openswx.drawing import drawing as D

ORIENTATIONS = {**D.VIEW_DIRS, "dimetric": ((1, 0.5, 1), (0, 1, 0)), "trimetric": ((1, 0.75, 0.55), (0, 1, 0))}
LABELS = {"front": "FRONT", "back": "BACK", "top": "TOP", "bottom": "BOTTOM", "left": "LEFT", "right": "RIGHT",
          "isometric": "ISOMETRIC", "dimetric": "DIMETRIC", "trimetric": "TRIMETRIC"}
KINDS = ("model", "projected", "section", "detail", "auxiliary")


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


def _polyline(pts, style: str) -> str:
    return f'<polyline points="{" ".join(f"{a:.3f},{b:.3f}" for a, b in pts)}" fill="none" {style}/>'


VIS = 'stroke="#000" stroke-width="0.35" stroke-linejoin="round"'
HID = 'stroke="#555" stroke-width="0.18" stroke-dasharray="1.6 0.9"'
THIN = 'stroke="#000" stroke-width="0.25"'


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


def render_sheet(doc, st: dict, sheet: dict, cache: dict | None = None) -> str:
    cache = {} if cache is None else cache
    W, H = D.SHEETS.get(sheet.get("size", "A3"), D.SHEETS["A3"])
    margin, title_h = 12, 44
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}" '
           f'font-family="Segoe UI, Arial, sans-serif" data-sheet="{escape(sheet["name"])}">',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="#fff"/>',
           f'<rect x="{margin}" y="{margin}" width="{W - 2 * margin}" height="{H - 2 * margin}" fill="none" stroke="#000" stroke-width="0.5"/>',
           '<defs><pattern id="hatch" width="2" height="2" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           '<line x1="0" y1="0" x2="0" y2="2" stroke="#000" stroke-width="0.18"/></pattern></defs>']
    for v in sheet["views"]:
        align(st, v, cache, doc)
    for v in sheet["views"]:
        try:
            out += _render_view(doc, st, v, cache)
        except DrawingError as exc:
            out.append(f'<text x="{v["x"]:.2f}" y="{v["y"]:.2f}" font-size="3" fill="#c00" text-anchor="middle">'
                       f'{escape(v["id"])}: {escape(str(exc))}</text>')
    out += _sheet_tables(doc, st, sheet, W, H, margin, cache)
    from openswx.drawing import drawing_more as DM
    out += DM.annotations(st, sheet)
    if (st.get("inspection") or {}).get("balloons"):
        from openswx.analysis.inspection import balloon_svg
        out += balloon_svg(st, sheet, "\n".join(out))
    if st.get("format"):  # a sheet format (title-block template) chosen for the drawing
        vals = DM.title_fields(doc, sheet, st.get("title"), D._scale_label(float(sheet.get("scale") or 1.0)),
                               sheet.get("projection", "third"))
        out += DM.title_block_svg(st["format"], vals, W, H, margin)
    else:
        out += _title_block(doc, sheet, W, H, margin, title_h, st.get("title"))
    out.append("</svg>")
    return "\n".join(out)


def _render_view(doc, st, v, cache) -> list[str]:
    g = view_geometry(doc, st, v, cache)
    t = placement(st, v, g)
    hw, hh = half_size(st, v, g)
    out = [f'<g data-view="{escape(v["id"])}"><rect class="view-box" x="{v["x"] - hw - 2:.2f}" y="{v["y"] - hh - 2:.2f}" '
           f'width="{2 * hw + 4:.2f}" height="{2 * hh + 4:.2f}" fill="none" stroke="none" pointer-events="all"/>']
    clip_end = []
    if v["kind"] == "detail":
        cid = f'clip-{v["id"]}'.replace(" ", "_")
        r = float(v["radius"]) * view_scale(st, v)
        out.append(f'<clipPath id="{cid}"><circle cx="{v["x"]:.3f}" cy="{v["y"]:.3f}" r="{r:.3f}"/></clipPath>')
        out.append(f'<g clip-path="url(#{cid})">')
        clip_end = ["</g>", f'<circle cx="{v["x"]:.3f}" cy="{v["y"]:.3f}" r="{r:.3f}" fill="none" {THIN}/>']
    for pts in g["hatch"]:
        d = "M" + " L".join(f"{a:.3f},{b:.3f}" for a, b in (t(*q) for q in pts)) + " Z"
        out.append(f'<path d="{d}" fill="url(#hatch)" stroke="none" fill-rule="evenodd"/>')
    pictorial = v["kind"] == "model" and v.get("orientation") in ("isometric", "dimetric", "trimetric")
    if v.get("hidden", "removed" if pictorial else "visible") == "visible":
        out += [_polyline([t(*q) for q in poly], HID) for poly in g["hid"]]
    out += [_polyline([t(*q) for q in poly], VIS) for poly in g["vis"]]
    if v.get("center_marks"):
        out += _center_marks(st, v, g, t)
    if v.get("centerlines") and v["kind"] in ("model", "projected", "auxiliary"):
        out += _centerlines(doc, st, v, g, t)
    from openswx.drawing import drawing_more as DM
    if v["kind"] in ("model", "projected", "auxiliary", "section"):
        out += DM.thread_marks(doc, st, v, t, frame, to2d, view_scale(st, v))
    out += DM.alternate_phantom(doc, st, v, t, frame, _project)
    out += clip_end
    out += _break_lines(v, g, t, hw, hh)
    out += _render_balloons(doc, sheet_of(st, v["id"]), v, t)
    if v.get("dims") and getattr(doc, "kind", "part") == "part" and v["kind"] in ("model", "projected", "auxiliary"):
        out += _model_dims(doc, g["frame"], t)
    dims = DM.layered_dims(st, sheet_of(st, v["id"]), v, lambda sh: _render_dims(st, sh, v, g, t))
    out += dims
    out += _child_marks(doc, st, v, t, cache)
    label = v.get("label") or _default_label(st, v)
    s = view_scale(st, v)
    sheet_scale = float(sheet_of(st, v["id"]).get("scale") or 1.0)
    if abs(s - sheet_scale) > 1e-9 and v["kind"] != "detail":
        label += f"  SCALE {D._scale_label(s)}"
    if label:
        # below the view, and below any dimensions placed under it
        low = max([v["y"] + hh] + [float(y) for e in dims for y in re.findall(r' y[12]?="([-\d.]+)"', e)])
        out.append(f'<text x="{v["x"]:.2f}" y="{low + 7:.2f}" font-size="3" text-anchor="middle">{escape(label)}</text>')
    out.append("</g>")
    return out


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


def item_numbers(doc, sheet: dict) -> dict:
    """Component name -> BOM item number (the sheet's BOM order when it has one)."""
    if getattr(doc, "kind", "part") != "assembly":
        return {}
    tb = next((t for t in sheet.get("tables", []) if t["type"] == "bom"), {})
    item = {r["part"]: r["item"] for r in bom_rows(doc, tb)}
    return {c.name: item.get(c.link or c.name.rsplit("-", 1)[0]) for c in doc.components}


def dim_text(d: dict, base: str, value: float | None = None) -> str:
    """What a dimension shows: its override text, else the value (`base`) with the dimension's tolerance."""
    if d.get("text"):
        return d["text"]
    if not d.get("tol"):
        return base
    from openswx.analysis import tolerance
    try:
        return tolerance.text(base, dim_value(d) if value is None else value, d["tol"])
    except tolerance.ToleranceError:
        return base


def dim_value(d: dict) -> float:
    a, b = d.get("a"), d.get("b")
    k = d["kind"]
    if k == "ordinate":  # the distance from the set's zero, along its axis
        i = 0 if d.get("axis", "x") == "x" else 1
        return abs(float(b[i]) - float(a[i]))
    if k == "angle":
        res = lines_angle(d["l1"], d["l2"], d["at"])
        return res[3] if res else 0.0
    if k == "line_distance":
        return line_gap(d["l1"], d["l2"])
    if k == "radius":
        return float(d["r"])
    if k in ("diameter", "callout"):
        return 2 * float(d["r"])
    if k == "horizontal":
        return abs(b[0] - a[0])
    if k == "vertical":
        return abs(b[1] - a[1])
    return math.hypot(b[0] - a[0], b[1] - a[1])


def parse_dim_text(text: str):
    """A dimension's display text - 'Ø9', '2X ⌀16', 'R5', '95' (SOLIDWORKS' <MOD-DIAM> and other tags allowed) -
    as {"kind": "diameter" / "radius" / "linear", "value", "decimals", "prefix", "text": cleaned}; None when it
    holds no number."""
    s = text.replace("<MOD-DIAM>", "⌀").replace("Ø", "⌀")
    s = re.sub(r"<[^>]*>", "", s).strip()
    m = re.search(r"(⌀|R)?\s*(\d+(?:\.\d*)?|\.\d+)", s)
    if not m:
        return None
    num = m.group(2)
    return {"kind": {"⌀": "diameter", "R": "radius"}.get(m.group(1) or "", "linear"), "value": float(num),
            "decimals": len(num.split(".")[1]) if "." in num else 0, "prefix": s[:m.start()].strip(), "text": s}


def match_dimension(g: dict, kind: str, value: float, tol: float) -> dict | None:
    """The view geometry a dimension of this value measures (view coordinates): a circle (diameter), an arc
    (radius), else two vertices a horizontal or vertical distance apart - a straight edge of that length
    first, the pair along the view's outline (top for horizontal, left for vertical) before inner ones."""
    if kind in ("diameter", "radius"):
        circles = [c for c in (_fit_circle(p) for p in g["vis"] + g["hid"]) if c]
        want = value / 2 if kind == "diameter" else value
        best = min(circles, key=lambda c: abs(c[1] - want), default=None)
        if best is None or abs(best[1] - want) > tol / (2 if kind == "diameter" else 1):
            return None
        return {"kind": kind, "c": list(best[0]), "r": best[1], "angle": 45.0}
    verts = []
    for q in g.get("verts") or _corners(g["vis"] + g["hid"]):
        if not any(abs(q[0] - o[0]) < 1e-6 and abs(q[1] - o[1]) < 1e-6 for o in verts):
            verts.append(q)
    edges = {(tuple(p[0]), tuple(p[-1])) for p in g["vis"] + g["hid"] if len(p) == 2}
    x0, y0, x1, y1 = g["box"]
    span = max(x1 - x0, y1 - y0, 1e-9)
    best, score = None, None
    for i, a in enumerate(verts):
        for b in verts[i + 1:]:
            for axis, kd in ((0, "horizontal"), (1, "vertical")):
                if abs(abs(b[axis] - a[axis]) - value) > tol:
                    continue
                along = abs(b[1 - axis] - a[1 - axis])  # off the measured direction
                edge = along < 1e-6 and ((tuple(a), tuple(b)) in edges or (tuple(b), tuple(a)) in edges)
                rim = (y1 - max(a[1], b[1])) if axis == 0 else (min(a[0], b[0]) - x0)
                sc = (not edge, along > 1e-6, abs(abs(b[axis] - a[axis]) - value), rim / span, along / span)
                if score is None or sc < score:
                    lo, hi = (a, b) if a[axis] <= b[axis] else (b, a)
                    best, score = {"kind": kd, "a": list(lo), "b": list(hi)}, sc
    return best


def _render_dims(st, sheet, v, g, t) -> list[str]:
    out = []
    span = max(g["box"][2] - g["box"][0], g["box"][3] - g["box"][1], 1e-6)
    for d in sheet.get("dims", []):
        if d["view"] != v["id"]:
            continue
        if d["kind"] in ("radius", "diameter", "callout"):  # follow the model: the nearest circle to where it was
            # (of concentric circles, the one of its size - not the innermost)
            near = [c for c in (_fit_circle(p) for p in g["vis"] + g["hid"]) if c]
            c = min(near, key=lambda c: math.dist(c[0], d["c"]) + abs(c[1] - d["r"]), default=None)
            if c and math.dist(c[0], d["c"]) + abs(c[1] - d["r"]) < 0.05 * span + d["r"]:
                d["c"], d["r"] = list(c[0]), c[1]
            (cx, cy), r = t(*d["c"]), d["r"] * view_scale(st, v)
            ang = math.radians(d.get("angle", 45.0))
            ex, ey = cx + r * math.cos(ang), cy - r * math.sin(ang)
            tx, ty = cx + (r + d.get("offset", 8.0)) * math.cos(ang), cy - (r + d.get("offset", 8.0)) * math.sin(ang)
            out += D._leader(tx, ty, ex, ey)
            if d["kind"] == "diameter":  # the leader runs across the circle
                out.append(f'<line x1="{ex:.3f}" y1="{ey:.3f}" x2="{2 * cx - ex:.3f}" y2="{2 * cy - ey:.3f}" stroke="#000" stroke-width="0.18"/>')
            if d["kind"] == "callout":  # Hole Callout: the hole's size and depth, a line per item
                lines = (d.get("text") or d.get("callout") or "⌀" + D._fmt(dim_value(d))).split("\n")
            else:
                lines = [dim_text(d, ("⌀" if d["kind"] == "diameter" else "R") + D._fmt(dim_value(d)))]
            anchor = "start" if math.cos(ang) >= 0 else "end"
            for i, line in enumerate(lines):
                out.append(f'<text data-dim="{escape(d["id"])}" x="{tx + (1 if math.cos(ang) >= 0 else -1):.3f}" y="{ty - 0.8 + 4.0 * i:.3f}" '
                           f'font-size="3.2" text-anchor="{anchor}">{escape(line)}</text>')
            continue
        if d["kind"] == "ordinate":
            out += _render_ordinate(d, t)
            continue
        if d["kind"] in ("angle", "line_distance"):
            for k in ("l1", "l2"):  # follow the model: the nearest straight edge to where the line was
                mid = ((d[k][0][0] + d[k][1][0]) / 2, (d[k][0][1] + d[k][1][1]) / 2)
                q = pick_line(g, mid, 0.02 * span)
                if q:
                    d[k] = [list(q[0]), list(q[1])]
            out += _render_angle(st, v, d, t) if d["kind"] == "angle" else _render_line_gap(d, t)
            continue
        for k in ("a", "b"):  # follow the model: the nearest vertex to where the end was
            q = snap_vertex(g, d[k], 0.02 * span)
            if q:
                d[k] = list(q)
        a, b = d["a"], d["b"]
        if d["kind"] == "horizontal":
            b2 = (b[0], a[1])
            pa, pb = t(*a), t(*b2)
            ext = (t(*b), pb)
        elif d["kind"] == "vertical":
            b2 = (a[0], b[1])
            pa, pb = t(*a), t(*b2)
            ext = (t(*b), pb)
        else:
            pa, pb, ext = t(*a), t(*b), None
        text = dim_text(d, D._fmt(dim_value(d)))
        seg = D._dim_aligned(pa, pb, text, offset=float(d.get("offset", 8.0)))
        if seg:
            seg[-1] = seg[-1].replace("<text ", f'<text data-dim="{escape(d["id"])}" ', 1)
        if ext and ext[0] != ext[1]:  # the measured point off the dimension's base line
            out.append(f'<line x1="{ext[0][0]:.3f}" y1="{ext[0][1]:.3f}" x2="{ext[1][0]:.3f}" y2="{ext[1][1]:.3f}" '
                       f'stroke="#000" stroke-width="0.13" stroke-dasharray="0.8 0.8"/>')
        out += seg
    return out


def _render_angle(st, v, d, t) -> list[str]:
    """Angular dimension: an arc about the lines' crossing through the placement point, arrows at both ends,
    extension lines where the arc lies beyond an edge."""
    res = lines_angle(d["l1"], d["l2"], d["at"])
    if res is None:
        return []
    apex, u1, u2, deg = res
    P = np.array(t(*apex))
    R = max(3.0, float(np.linalg.norm(np.array(t(*d["at"])) - P)))
    s = view_scale(st, v)
    dirs = [np.array([u[0], -u[1]]) for u in (u1, u2)]  # sheet y runs down
    th1, th2 = (math.atan2(q[1], q[0]) for q in dirs)
    delta = (th2 - th1 + math.pi) % (2 * math.pi) - math.pi
    e1, e2 = P + R * dirs[0], P + R * dirs[1]
    out = [f'<path d="M{e1[0]:.3f},{e1[1]:.3f} A{R:.3f},{R:.3f} 0 0 {1 if delta > 0 else 0} {e2[0]:.3f},{e2[1]:.3f}" '
           f'fill="none" stroke="#000" stroke-width="0.18"/>']
    sgn = 1.0 if delta > 0 else -1.0
    out.append(D._arrow(*e1, math.atan2(-math.cos(th1) * sgn, math.sin(th1) * sgn)))  # pointing back along the arc
    out.append(D._arrow(*e2, math.atan2(math.cos(th2) * sgn, -math.sin(th2) * sgn)))
    for line, u, q in ((d["l1"], u1, dirs[0]), (d["l2"], u2, dirs[1])):  # extension lines beyond the edges
        reach = max(float((np.asarray(p, float) - apex) @ u) for p in line) * s
        if R > reach + 0.5:
            a0, a1 = P + q * (reach + 1.0), P + q * (R + 1.5)
            out.append(f'<line x1="{a0[0]:.3f}" y1="{a0[1]:.3f}" x2="{a1[0]:.3f}" y2="{a1[1]:.3f}" stroke="#000" stroke-width="0.13"/>')
    tm = th1 + delta / 2
    tx, ty = P[0] + (R + 2.5) * math.cos(tm), P[1] + (R + 2.5) * math.sin(tm)
    text = dim_text(d, f"{D._fmt(deg)}°", deg)
    out.append(f'<text data-dim="{escape(d["id"])}" x="{tx:.3f}" y="{ty + 1.1:.3f}" font-size="3.2" text-anchor="middle">{escape(text)}</text>')
    return out


def _render_ordinate(d, t) -> list[str]:
    """Ordinate dimension: an extension line from the point out to the set's common line, with the distance
    from the zero point written at its end (no dimension line or arrows)."""
    (ax, ay), (bx, by) = t(*d["a"]), t(*d["b"])
    off = float(d.get("offset", 10.0))
    text = dim_text(d, D._fmt(dim_value(d)))
    if d.get("axis", "x") == "x":  # values along the view's x: extension lines run up / down the sheet
        ey = ay + off
        gap = 1.0 if ey > by else -1.0
        return [f'<line x1="{bx:.3f}" y1="{by + gap:.3f}" x2="{bx:.3f}" y2="{ey:.3f}" stroke="#000" stroke-width="0.13"/>',
                f'<text data-dim="{escape(d["id"])}" x="{bx:.3f}" y="{ey + (4.0 if off > 0 else -1.2):.3f}" font-size="3.2" '
                f'text-anchor="middle">{escape(text)}</text>']
    ex = ax - off
    gap = -1.0 if ex < bx else 1.0
    return [f'<line x1="{bx + gap:.3f}" y1="{by:.3f}" x2="{ex:.3f}" y2="{by:.3f}" stroke="#000" stroke-width="0.13"/>',
            f'<text data-dim="{escape(d["id"])}" x="{ex + (-1.2 if off > 0 else 1.2):.3f}" y="{by + 1.1:.3f}" font-size="3.2" '
            f'text-anchor="{"end" if off > 0 else "start"}">{escape(text)}</text>']


def _render_line_gap(d, t) -> list[str]:
    """Distance between parallel edges: dimensioned square to them, from the first edge's middle."""
    p1 = np.asarray(d["l1"][0], float)
    m1 = (p1 + np.asarray(d["l1"][1], float)) / 2
    p2 = np.asarray(d["l2"][0], float)
    u = np.asarray(d["l2"][1], float) - p2
    u = u / np.linalg.norm(u)
    m2 = p2 + u * float((m1 - p2) @ u)
    seg = D._dim_aligned(t(*m1), t(*m2), dim_text(d, D._fmt(dim_value(d))), offset=float(d.get("offset", 0.0)))
    if seg:
        seg[-1] = seg[-1].replace("<text ", f'<text data-dim="{escape(d["id"])}" ', 1)
    return seg


def _center_marks(st, v, g, t) -> list[str]:
    """Center marks on every circle seen face-on (chain lines a little past the circle)."""
    out, s = [], view_scale(st, v)
    for c, r in view_circles(g):
        (cx, cy), rs = t(*c), r * s
        if rs < 0.6:
            continue
        L = rs + 2.0
        for x1, y1, x2, y2 in ((cx - L, cy, cx + L, cy), (cx, cy - L, cx, cy + L)):
            out.append(f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" stroke="#000" stroke-width="0.13" '
                       f'stroke-dasharray="4 1 0.8 1"/>')
    return out


def _centerlines(doc, st, v, g, t) -> list[str]:
    """Centerlines along the axes of cylindrical faces seen side-on, a little past the faces."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    body = doc.body
    if body is None:
        return []
    fr = frame(st, v)
    d = np.asarray(fr[0], float)
    crop, bands = v.get("crop"), _bands(v)
    segs = []
    for f in body.faces():
        ad = BRepAdaptor_Surface(f.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            continue
        ax = ad.Cylinder().Axis()
        a = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        if abs(float(a @ d)) > 1e-6:
            continue
        loc = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
        ts = [float((np.asarray(tuple(q)) - loc) @ a) for q in f.vertices()] or [0.0]
        p, q = to2d(loc + a * min(ts), fr), to2d(loc + a * max(ts), fr)
        if math.dist(p, q) < 1e-6 or not (_kept(p, crop, bands) or _kept(q, crop, bands)):
            continue
        key = tuple(round(c, 4) for c in (*p, *q))
        if key not in segs:
            segs.append(key)
    out = []
    for x1, y1, x2, y2 in segs:
        (sx, sy), (ex, ey) = t(x1, y1), t(x2, y2)
        L = math.hypot(ex - sx, ey - sy)
        ux, uy = (ex - sx) / L, (ey - sy) / L
        out.append(f'<line x1="{sx - ux * 2:.3f}" y1="{sy - uy * 2:.3f}" x2="{ex + ux * 2:.3f}" y2="{ey + uy * 2:.3f}" '
                   f'stroke="#000" stroke-width="0.13" stroke-dasharray="6 1.2 1 1.2"/>')
    return out


def _render_balloons(doc, sheet, v, t) -> list[str]:
    """Balloons: the BOM item number in a circle, a leader to a dot on the component."""
    items = item_numbers(doc, sheet)
    out = []
    for b in sheet.get("balloons", []):
        if b["view"] != v["id"]:
            continue
        text = b.get("text") or str(items.get(b.get("component")) or "?")
        bx, by = float(b["x"]), float(b["y"])
        px, py = t(*b["p"])
        L = math.hypot(px - bx, py - by)
        r = 4.0
        out.append(f'<g data-ann="{escape(b["id"])}">')
        if L > r:
            sx, sy = bx + (px - bx) / L * r, by + (py - by) / L * r
            out.append(f'<line x1="{sx:.3f}" y1="{sy:.3f}" x2="{px:.3f}" y2="{py:.3f}" stroke="#000" stroke-width="0.18"/>')
            out.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="0.6" fill="#000"/>')
        out.append(f'<circle cx="{bx:.3f}" cy="{by:.3f}" r="{r}" fill="#fff" stroke="#000" stroke-width="0.25"/>')
        out.append(f'<text x="{bx:.3f}" y="{by + 1.3:.3f}" font-size="3.6" text-anchor="middle">{escape(text)}</text>')
        if b.get("stack"):  # a stacked balloon: the other items touch the first
            from openswx.drawing.drawing_more import stacked_svg
            out += stacked_svg(b, {c: str(n) for c, n in items.items()})
            out.append("</g>")
        else:
            out[-1] += "</g>"
    return out


def _default_label(st, v) -> str:
    if v["kind"] == "model":
        return LABELS.get(v.get("orientation", "front"), "")
    if v["kind"] == "section":
        return f'SECTION {v.get("letter", "A")}-{v.get("letter", "A")}'
    if v["kind"] == "detail":
        return f'DETAIL {v.get("letter", "B")}  SCALE {D._scale_label(view_scale(st, v))}'
    if v["kind"] == "auxiliary":
        return f'VIEW {v.get("letter", "C")}'
    return ""


def _child_marks(doc, st, parent, t, cache) -> list[str]:
    """Section lines, detail circles and auxiliary arrows a parent view shows for its children."""
    out = []
    for sh in st["sheets"]:
        for c in sh["views"]:
            if c.get("parent") != parent["id"]:
                continue
            if c["kind"] == "section":
                (x1, y1), (x2, y2) = t(*c["a"]), t(*c["b"])
                L = math.hypot(x2 - x1, y2 - y1) or 1.0
                ux, uy = (x2 - x1) / L, (y2 - y1) / L
                out.append(f'<line x1="{x1 - ux * 4:.3f}" y1="{y1 - uy * 4:.3f}" x2="{x2 + ux * 4:.3f}" y2="{y2 + uy * 4:.3f}" '
                           f'stroke="#000" stroke-width="0.35" stroke-dasharray="6 1.5 1 1.5"/>')
                # arrows point the way the section looks (away from the viewer of the section)
                fr = frame(st, c)
                lx, ly = to2d(-fr[0], frame(st, parent))
                ang = math.atan2(-ly, lx)
                for x, y in ((x1 - ux * 4, y1 - uy * 4), (x2 + ux * 4, y2 + uy * 4)):
                    ex, ey = x + 5 * math.cos(ang), y + 5 * math.sin(ang)
                    out.append(f'<line x1="{x:.3f}" y1="{y:.3f}" x2="{ex:.3f}" y2="{ey:.3f}" stroke="#000" stroke-width="0.35"/>')
                    out.append(D._arrow(ex, ey, ang))
                    out.append(f'<text x="{x - 2.5 * math.cos(ang):.3f}" y="{y - 2.5 * math.sin(ang) + 1.2:.3f}" font-size="3.4" '
                               f'text-anchor="middle" font-weight="700">{escape(c.get("letter", "A"))}</text>')
            elif c["kind"] == "detail":
                cx, cy = t(*c["center"])
                r = float(c["radius"]) * view_scale(st, parent)
                out.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" fill="none" stroke="#000" stroke-width="0.25" stroke-dasharray="4 1.5"/>')
                out.append(f'<text x="{cx + r + 1:.3f}" y="{cy - r:.3f}" font-size="3.4" font-weight="700">{escape(c.get("letter", "B"))}</text>')
            elif c["kind"] == "auxiliary":
                fr = frame(st, c)
                lx, ly = to2d(-fr[0], frame(st, parent))
                ang = math.atan2(-ly, lx)
                g = view_geometry(doc, st, parent, cache)
                bx, by = t((g["box"][0] + g["box"][2]) / 2, (g["box"][1] + g["box"][3]) / 2)
                sx, sy = bx - 14 * math.cos(ang), by - 14 * math.sin(ang)
                out.append(f'<line x1="{sx:.3f}" y1="{sy:.3f}" x2="{sx + 6 * math.cos(ang):.3f}" y2="{sy + 6 * math.sin(ang):.3f}" stroke="#000" stroke-width="0.35"/>')
                out.append(D._arrow(sx + 6 * math.cos(ang), sy + 6 * math.sin(ang), ang))
                out.append(f'<text x="{sx - 2:.3f}" y="{sy:.3f}" font-size="3.4" font-weight="700">{escape(c.get("letter", "C"))}</text>')
    return out


def _break_lines(v, g, t, hw, hh) -> list[str]:
    """The pair of zig-zag break lines at each break band of a broken view."""
    out = []
    for i, lo, hi in _bands(v):
        for c in (lo, hi):
            if i == 0:
                x = t(c, g["box"][1])[0]
                y0, y1, m = v["y"] - hh - 2, v["y"] + hh + 2, v["y"]
                pts = [(x, y0), (x, m - 2), (x + 1.5, m - 1), (x - 1.5, m + 1), (x, m + 2), (x, y1)]
            else:
                y = t(g["box"][0], c)[1]
                x0, x1, m = v["x"] - hw - 2, v["x"] + hw + 2, v["x"]
                pts = [(x0, y), (m - 2, y), (m - 1, y - 1.5), (m + 1, y + 1.5), (m + 2, y), (x1, y)]
            out.append(_polyline(pts, THIN))
    return out


def _model_dims(doc, fr, t) -> list[str]:
    """The model's driving sketch dimensions, in views that look straight at the sketch."""
    from openswx.core.sketch import _Getter, _kind
    out = []
    d = Vector(*fr[0])
    for f in doc.features:
        if f["type"] != "sketch" or f["name"] not in doc.ctx.sketches or f["params"].get("hidden"):
            continue
        sk, plane = doc.ctx.sketches[f["name"]]
        if abs(abs(plane.z_dir.dot(d)) - 1) > 1e-6:
            continue
        x, idx = sk._vector()
        g = _Getter(sk, x, idx)

        def S(q, plane=plane):
            p = plane.from_local_coords(Vector(float(q[0]), float(q[1]), 0))
            return t(*to2d(tuple(p), fr))
        for dim in sk.dimensions:
            if dim.get("driven"):
                continue
            refs, typ = dim["refs"], dim["type"]
            try:
                if typ in ("radius", "diameter"):
                    c = g.center(refs[0])
                    r = g.r(refs[0])
                    p_on = c + np.array([r * math.cos(math.radians(45)), r * math.sin(math.radians(45))])
                    (ax, ay), (bx, by) = S(c), S(p_on)
                    tx, ty = bx + (bx - ax) * 0.6 + 2, by + (by - ay) * 0.6
                    out += D._leader(tx, ty, bx, by)
                    lab = ("R" if typ == "radius" else "⌀") + D._fmt(dim["value"])
                    out.append(f'<text x="{tx + 0.8:.3f}" y="{ty - 0.6:.3f}" font-size="3.2">{lab}</text>')
                    continue
                if typ == "angle":
                    continue
                kinds = [_kind(sk, r) for r in refs]
                if typ == "length" or (typ == "distance" and len(refs) == 1):
                    a, b = g.line(refs[0])
                elif typ in ("hdistance", "vdistance"):
                    a, b = (g.center(r) if _kind(sk, r) != "line" else g.line(r)[0] for r in refs[:2])
                    b = np.array([b[0], a[1]]) if typ == "hdistance" else np.array([a[0], b[1]])
                elif "line" in kinds:
                    line, other = (refs[0], refs[1]) if kinds[0] == "line" else (refs[1], refs[0])
                    p = g.line(other)[0] if _kind(sk, other) == "line" else g.center(other)
                    a0, b0 = g.line(line)
                    u = (b0 - a0) / np.linalg.norm(b0 - a0)
                    a, b = a0 + u * float(np.dot(p - a0, u)), p
                else:
                    a, b = g.center(refs[0]), g.center(refs[1])
                out += D._dim_aligned(S(a), S(b), D._fmt(dim["value"]))
            except Exception:  # a dimension we cannot place in this view
                continue
    return out


def _sheet_tables(doc, st, sheet, W, H, margin, cache) -> list[str]:
    out = []
    for tb in sheet.get("tables", []):
        if tb["type"] == "bom" and getattr(doc, "kind", "part") == "assembly":
            out += _bom_table(doc, tb, W, H, margin)
        elif tb["type"] == "revision":
            out += _revision_table(sheet.get("revisions", []), W, margin)
        elif tb["type"] == "hole":
            try:
                out += _hole_table(doc, st, tb, cache)
            except DrawingError as exc:
                out.append(f'<text x="{tb["x"]:.2f}" y="{tb["y"]:.2f}" font-size="3" fill="#c00">{escape(tb["id"])}: {escape(str(exc))}</text>')
    return out


def _table(x0, y0, heads, widths, rows, rh=5.5, attr="") -> list[str]:
    """A ruled table: bold heads, then rows of cell texts."""
    n = len(rows) + 1
    out = [f'<g{attr}><rect x="{x0:.2f}" y="{y0:.2f}" width="{sum(widths)}" height="{rh * n}" fill="#fff" stroke="#000" stroke-width="0.35"/>']
    xs = [x0 + sum(widths[:i]) for i in range(len(widths))]
    for i, (h, x) in enumerate(zip(heads, xs)):
        out.append(f'<text x="{x + 1.5:.2f}" y="{y0 + rh * 0.72:.2f}" font-size="2.6" font-weight="700">{escape(h)}</text>')
        if i:
            out.append(f'<line x1="{x:.2f}" y1="{y0:.2f}" x2="{x:.2f}" y2="{y0 + rh * n:.2f}" stroke="#000" stroke-width="0.25"/>')
    for r, row in enumerate(rows, 1):
        y = y0 + rh * r
        out.append(f'<line x1="{x0:.2f}" y1="{y:.2f}" x2="{x0 + sum(widths):.2f}" y2="{y:.2f}" stroke="#000" stroke-width="0.25"/>')
        for text, x in zip(row, xs):
            out.append(f'<text x="{x + 1.5:.2f}" y="{y + rh * 0.72:.2f}" font-size="2.8">{escape(str(text))}</text>')
    out.append("</g>")
    return out


def bom_rows(doc, tb: dict) -> list[dict]:
    """The BOM's rows: {"item", "part", "part_number", "description", "qty"} - parts grouped by source, in the
    table's own order (tb["order"]) then as they come, with the user's part number / description overrides."""
    from collections import Counter
    counts = Counter((c.link or c.name.rsplit("-", 1)[0]) for c in doc.components if not getattr(c, "envelope", False))
    order = [p for p in tb.get("order", []) if p in counts] + [p for p in counts if p not in tb.get("order", [])]
    over = tb.get("rows", {})
    out = []
    for i, part in enumerate(order, 1):
        o = over.get(part, {})
        name = part.replace("\\", "/").rsplit("/", 1)[-1]
        out.append({"item": i, "part": part, "part_number": o.get("part_number") or name.rsplit(".", 1)[0],
                    "description": o.get("description", ""), "qty": counts[part]})
    return out


def _bom_table(doc, tb, W, H, margin, title_h=44) -> list[str]:
    """The BOM: where it was moved to, else on the title block's top edge at the right (SOLIDWORKS' anchor)."""
    rows = bom_rows(doc, tb)
    widths, rh = (17, 52, 56, 14), 5.5
    x0 = tb.get("x", W - margin - sum(widths))
    y0 = tb.get("y", H - margin - title_h - rh * (len(rows) + 1))
    return _table(x0, y0, ("ITEM NO.", "PART NUMBER", "DESCRIPTION", "QTY."), widths,
                  [(r["item"], r["part_number"], r["description"], r["qty"]) for r in rows], attr=' data-table="BOM"')


def view_holes(doc, st: dict, view: dict, cache: dict | None = None) -> list[dict]:
    """The holes seen end-on in a view, tagged as a SOLIDWORKS hole table does (a letter per size, smallest
    first, numbered left to right): {"tag", "c" (view coordinates), "d" (diameter), "depth" (None: through)}."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.gp import gp_Pnt, gp_Vec
    body = doc.body
    if body is None:
        raise DrawingError("the part has no body")
    src = find_view(st, view["parent"]) if view["kind"] == "detail" else view
    fr = frame(st, src)
    d = np.asarray(fr[0], float)
    found: dict = {}
    for f in body.faces():
        ad = BRepAdaptor_Surface(f.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            continue
        cyl = ad.Cylinder()
        ax = cyl.Axis()
        a = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        if abs(abs(float(a @ d)) - 1) > 1e-6:
            continue
        loc = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
        prop = BRepGProp_Face(f.wrapped)
        u0, u1, v0, v1 = prop.Bounds()
        pnt, nrm = gp_Pnt(), gp_Vec()
        prop.Normal((u0 + u1) / 2, (v0 + v1) / 2, pnt, nrm)
        p = np.array([pnt.X(), pnt.Y(), pnt.Z()])
        foot = loc + a * float((p - loc) @ a)
        if float((foot - p) @ np.array([nrm.X(), nrm.Y(), nrm.Z()])) <= 0:
            continue  # material inside the cylinder: a boss, not a hole
        c2 = to2d(loc, fr)
        key = (round(c2[0], 4), round(c2[1], 4), round(cyl.Radius(), 4))
        ext = [float(np.asarray(tuple(v)) @ d) for v in f.vertices()] or [0.0]
        lo, hi = found.get(key, (math.inf, -math.inf))
        found[key] = (min(lo, *ext), max(hi, *ext))
    bb = body.bounding_box()
    corners = [np.array([x, y, z]) for x in (bb.min.X, bb.max.X) for y in (bb.min.Y, bb.max.Y) for z in (bb.min.Z, bb.max.Z)]
    span = (min(float(c @ d) for c in corners), max(float(c @ d) for c in corners))
    crop, bands = src.get("crop"), _bands(src)
    holes = []
    for (cx, cy, r), (lo, hi) in found.items():
        if not _kept((cx, cy), crop, bands):
            continue
        axis_pts = [to3d((cx, cy), fr, span[0] + (span[1] - span[0]) * (k + 0.5) / 40) for k in range(40)]
        through = not any(body.is_inside(Vector(*q)) for q in axis_pts)
        holes.append({"c": (cx, cy), "d": 2 * r, "depth": None if through else hi - lo})
    sizes = sorted({(round(h["d"], 4), h["depth"] is None, round(h["depth"] or 0, 4)) for h in holes},
                   key=lambda s: (s[0], not s[1], s[2]))
    for h in holes:
        h["_s"] = sizes.index((round(h["d"], 4), h["depth"] is None, round(h["depth"] or 0, 4)))
    holes.sort(key=lambda h: (h["_s"], round(h["c"][0], 3), -h["c"][1]))
    num: dict = {}
    for h in holes:
        num[h["_s"]] = num.get(h["_s"], 0) + 1
        h["tag"] = f"{_letters(h['_s'])}{num[h['_s']]}"
        del h["_s"]
    return holes


def _letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _hole_table(doc, st, tb, cache) -> list[str]:
    v = find_view(st, tb["view"])
    g = view_geometry(doc, st, v, cache)
    t = placement(st, v, g)
    holes = view_holes(doc, st, v, cache)
    ox, oy = tb["origin"]
    out = []
    sx, sy = t(ox, oy)  # the datum origin: X and Y arrows
    xd, yd = t(ox + 1, oy), t(ox, oy + 1)
    ux = ((xd[0] - sx), (xd[1] - sy))
    uy = ((yd[0] - sx), (yd[1] - sy))
    nx, ny = math.hypot(*ux) or 1, math.hypot(*uy) or 1
    for (u, n, lab) in ((ux, nx, "X"), (uy, ny, "Y")):
        ex, ey = sx + u[0] / n * 8, sy + u[1] / n * 8
        out.append(f'<line x1="{sx:.3f}" y1="{sy:.3f}" x2="{ex:.3f}" y2="{ey:.3f}" stroke="#000" stroke-width="0.25"/>')
        out.append(D._arrow(ex, ey, math.atan2(u[1], u[0])))
        out.append(f'<text x="{ex + u[0] / n * 2 - 1:.3f}" y="{ey + u[1] / n * 2 + 1:.3f}" font-size="2.6">{lab}</text>')
    s = view_scale(st, v)
    rows = []
    for h in holes:
        hx, hy = t(*h["c"])
        rr = h["d"] / 2 * s
        out.append(f'<text x="{hx + rr * 0.8 + 0.6:.3f}" y="{hy - rr * 0.8 - 0.6:.3f}" font-size="2.6">{escape(h["tag"])}</text>')
        size = f"⌀{D._fmt(h['d'])} " + ("THRU" if h["depth"] is None else f"↧{D._fmt(h['depth'])}")
        rows.append((h["tag"], D._fmt(h["c"][0] - ox), D._fmt(h["c"][1] - oy), size))
    out += _table(tb["x"], tb["y"], ("TAG", "X LOC", "Y LOC", "SIZE"), (14, 22, 22, 34), rows, attr=f' data-table="{escape(tb["id"])}"')
    return out


def _revision_table(rows, W, margin) -> list[str]:
    cw = (12, 70, 24, 22)
    x0, y0, rh = W - margin - sum(cw), margin, 5.5
    heads = ("REV", "DESCRIPTION", "DATE", "APPROVED")
    n = len(rows) + 1
    out = [f'<rect x="{x0}" y="{y0}" width="{sum(cw)}" height="{rh * n}" fill="#fff" stroke="#000" stroke-width="0.35"/>']
    xs = [x0 + sum(cw[:i]) for i in range(len(cw))]
    for i, (h, x) in enumerate(zip(heads, xs)):
        out.append(f'<text x="{x + 1.5}" y="{y0 + rh * 0.72}" font-size="2.6" font-weight="700">{h}</text>')
        if i:
            out.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y0 + rh * n}" stroke="#000" stroke-width="0.25"/>')
    for r, row in enumerate(rows, 1):
        y = y0 + rh * r
        out.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + sum(cw)}" y2="{y}" stroke="#000" stroke-width="0.25"/>')
        for k, x in zip(("rev", "description", "date", "approved"), xs):
            out.append(f'<text x="{x + 1.5}" y="{y + rh * 0.72}" font-size="2.8">{escape(str(row.get(k, "")))}</text>')
    return out


TITLE_FIELDS = ("company", "title", "dwg_no", "material", "drawn", "drawn_date", "checked", "approved")


def _title_block(doc, sheet, W, H, margin, title_h, fields: dict | None = None) -> list[str]:
    """The sheet format's title block. fields (the drawing's "title" dict, set with drawing_title_block)
    override what comes from the model: company, title, dwg_no, material, drawn, drawn_date, checked,
    approved."""
    import datetime as _dt
    fields = fields or {}
    tb_w, x0, y0 = 170, W - margin - 170, H - margin - title_h
    mat = fields.get("material") or getattr(doc, "material", {}).get("name") or "<not specified>"
    props = {k.lower(): v for k, v in getattr(doc, "properties", {}).items()}
    title = fields.get("title") or props.get("description") or doc.name
    rev = (sheet.get("revisions") or [{}])[-1].get("rev", "")
    drawn = fields.get("drawn") or props.get("author") or props.get("drawnby") or ""
    date = fields.get("drawn_date") or _dt.date.today().isoformat()
    signed = "   ".join(f"{k} {fields[f]}" for k, f in (("CHK", "checked"), ("APPR", "approved")) if fields.get(f))
    rows = [("TITLE", escape(title)), ("DWG NO.", escape(fields.get("dwg_no") or props.get("partno") or props.get("part number") or doc.name)),
            ("MATERIAL", escape(str(mat))),
            ("SCALE", D._scale_label(float(sheet.get("scale") or 1.0)) + f"   SHEET {escape(sheet['name'])}  {sheet.get('size', 'A3')}"),
            ("PROJECTION", "THIRD ANGLE" if sheet.get("projection", "third") == "third" else "FIRST ANGLE"),
            ("DRAWN", escape(f"{drawn}   {date}".strip())), ("CHECKED", escape(signed)), ("REV", escape(str(rev)))]
    out = [f'<rect x="{x0}" y="{y0}" width="{tb_w}" height="{title_h}" fill="#fff" stroke="#000" stroke-width="0.5"/>']
    if fields.get("company"):
        out.append(f'<text x="{x0 + tb_w - 2}" y="{y0 + title_h / len(rows) * 0.72:.2f}" font-size="3" font-weight="700" '
                   f'text-anchor="end">{escape(fields["company"])}</text>')
    rh = title_h / len(rows)
    for i, (k, v) in enumerate(rows):
        y = y0 + rh * i
        if i:
            out.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + tb_w}" y2="{y}" stroke="#000" stroke-width="0.25"/>')
        out.append(f'<text x="{x0 + 2}" y="{y + rh * 0.72:.2f}" font-size="2.6" fill="#555">{k}</text>')
        out.append(f'<text x="{x0 + 34}" y="{y + rh * 0.72:.2f}" font-size="{3.6 if i == 0 else 3}" '
                   f'font-weight="{700 if i == 0 else 400}">{v}</text>')
    out.append(f'<line x1="{x0 + 32}" y1="{y0}" x2="{x0 + 32}" y2="{y0 + title_h}" stroke="#000" stroke-width="0.25"/>')
    return out


def new_drawing(doc, size: str = "A3", projection: str = "third", scale: float | None = None,
                standard_views: bool = True) -> dict:
    """A drawing with one sheet; with standard_views: Front, Top, the side view and an isometric, laid out
    and scaled to fit the sheet as Make Drawing from Part does."""
    st = {"sheets": [], "active": 0}
    sheet = {"name": "Sheet1", "size": size, "scale": scale or 1.0, "projection": projection, "views": [],
             "notes": [], "datums": [], "gdt": [], "revisions": [], "tables": []}
    st["sheets"].append(sheet)
    if getattr(doc, "kind", "part") == "assembly":
        sheet["tables"].append({"type": "bom"})
    if standard_views:
        _standard_layout(doc, st, sheet, scale)
    return st


def _standard_layout(doc, st, sheet, scale):
    W, H = D.SHEETS.get(sheet["size"], D.SHEETS["A3"])
    margin, title_h = 12, 44
    front = {"id": next_id(st, "Drawing View"), "kind": "model", "orientation": "front", "x": 0.0, "y": 0.0}
    sheet["views"].append(front)
    top = {"id": next_id(st, "Drawing View"), "kind": "projected", "parent": front["id"], "side": "top", "x": 0.0, "y": 0.0}
    sheet["views"].append(top)
    side = {"id": next_id(st, "Drawing View"), "kind": "projected", "parent": front["id"], "side": "right", "x": 0.0, "y": 0.0}
    sheet["views"].append(side)
    iso = {"id": next_id(st, "Drawing View"), "kind": "model", "orientation": "isometric", "x": 0.0, "y": 0.0}
    sheet["views"].append(iso)
    cache: dict = {}
    sz = {v["id"]: (lambda b: (b[2] - b[0], b[3] - b[1]))(view_geometry(doc, st, v, cache)["box"]) for v in sheet["views"]}
    fw, fh = sz[front["id"]]
    tw, th = sz[top["id"]]
    sw, sh = sz[side["id"]]
    if not scale:
        avail_w, avail_h = (W - 2 * margin) * 0.62, (H - 2 * margin - title_h) * 0.85
        scale = next((s for s in D.SCALES if (fw + sw + 30) * s <= avail_w and (fh + th + 30) * s <= avail_h), D.SCALES[-1])
    sheet["scale"] = scale
    gap = 22
    fx = margin + 20 + fw * scale / 2
    third = sheet["projection"] == "third"
    fy = (H - margin - title_h - 18 - fh * scale / 2) if third else (margin + 18 + fh * scale / 2)
    front.update(x=fx, y=fy)
    top.update(x=fx, y=fy - (fh + th) * scale / 2 - gap if third else fy + (fh + th) * scale / 2 + gap)
    side.update(x=fx + (fw + sw) * scale / 2 + gap, y=fy)
    iw, ih = sz[iso["id"]]
    iso.update(scale=scale * 0.8, x=W - margin - 15 - iw * scale * 0.4, y=margin + 15 + ih * scale * 0.4)


def default_position(doc, st, parent: dict, side: str, child: dict, gap: float = 22.0) -> tuple[float, float]:
    """Where a new projected / section / auxiliary / detail view goes: beside its parent on the given side,
    moved further out along that side (projected views keep their alignment) until it is clear of the other
    views, the border and the title block; a detail view may go anywhere free."""
    cache: dict = {}
    sheet = sheet_of(st, parent["id"])
    W, H = D.SHEETS.get(sheet.get("size", "A3"), D.SHEETS["A3"])
    margin, title_h, tb_w = 12, 44, 170
    pg = view_geometry(doc, st, parent, cache)
    phw, phh = half_size(st, parent, pg)
    cg = view_geometry(doc, st, child, cache)
    chw, chh = half_size(st, child, cg)
    boxes = []
    for v in sheet["views"]:
        if v["id"] == child["id"]:
            continue
        try:
            hw, hh = half_size(st, v, view_geometry(doc, st, v, cache))
        except DrawingError:
            continue
        boxes.append((v["x"] - hw - 4, v["y"] - hh - 4, v["x"] + hw + 4, v["y"] + hh + 12))  # (+ the label)
    boxes.append((W - margin - tb_w, H - margin - title_h, W - margin, H - margin))  # title block

    def free(x, y):
        b = (x - chw, y - chh, x + chw, y + chh + 8)
        if b[0] < margin + 2 or b[1] < margin + 2 or b[2] > W - margin - 2 or b[3] > H - margin - 2:
            return False
        return not any(b[0] < o[2] and b[2] > o[0] and b[1] < o[3] and b[3] > o[1] for o in boxes)

    step = {"right": (1, 0), "left": (-1, 0), "top": (0, -1), "bottom": (0, 1)}
    ux, uy = step[side]
    x0 = parent["x"] + ux * (phw + chw + gap)
    y0 = parent["y"] + uy * (phh + chh + gap)
    for k in range(0, 60):  # out along the requested side first
        x, y = x0 + ux * 5 * k, y0 + uy * 5 * k
        if free(x, y):
            return x, y
    if child["kind"] != "projected":  # anywhere free on the sheet, nearest the parent first
        cands = [(x, y) for x in np.arange(margin + chw + 4, W - margin - chw - 4, 5.0)
                 for y in np.arange(margin + chh + 4, H - margin - chh - 12, 5.0)]
        cands.sort(key=lambda q: math.hypot(q[0] - parent["x"], q[1] - parent["y"]))
        for x, y in cands:
            if free(x, y):
                return float(x), float(y)
    return x0, y0


def sheet_to_view(st: dict, doc, view: dict, xy) -> tuple[float, float]:
    """A sheet point (mm) -> the view's 2D model coordinates (for picking cut lines and detail circles)."""
    g = view_geometry(doc, st, view, {})
    s = view_scale(st, view)
    b = g["box"]
    mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    x, y = mx + (xy[0] - view["x"]) / s, my - (xy[1] - view["y"]) / s
    return g["warp"][1](x, y) if g.get("warp") else (x, y)


def next_letter(st: dict) -> str:
    used = {v.get("letter") for sh in st.get("sheets", []) for v in sh["views"]}
    for c in "ABCDEFGHJKLMNPRSTUVWXYZ":  # (I, O and Q are not used for view letters)
        if c not in used:
            return c
    return "Z"
