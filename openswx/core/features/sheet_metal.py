"""Sheet metal builders: flanges, bends, hems, reliefs and forming tools."""
from __future__ import annotations

import math

from build123d import Axis, Compound, Location, Plane, Solid, Vector, extrude

from openswx.core.geom import plane_for_normal, resolve_edge, resolve_face, vec

from ._base import Ctx, FeatureError, _rot, _sketch, apply_op
from .solids import build_extrude


def build_base_flange(ctx: Ctx, p: dict, name: str) -> None:
    """Sheet metal Base Flange: the sketch profile extruded by the sheet thickness; sets the part's sheet
    metal parameters (thickness, bend radius, K-factor) used by later flanges and the flat pattern."""
    t = float(p.get("thickness", 2.0))
    if t <= 0:
        raise FeatureError("thickness must be positive")
    build_extrude(ctx, {"sketch": p["sketch"], "depth": t, "reverse": p.get("reverse", False)}, name)
    _, base_plane = _sketch(ctx, p["sketch"])
    nrm = base_plane.z_dir * (-1 if p.get("reverse") else 1)
    ctx.sheet = {"thickness": t, "radius": float(p.get("bend_radius", t)), "k": float(p.get("k_factor", 0.5)),
                 "bends": [], "normal": list(nrm), "bottom": base_plane.origin.dot(nrm)}


def build_edge_flange(ctx: Ctx, p: dict, name: str) -> None:
    """Sheet metal Edge Flange on a straight edge of the sheet's face: bend of radius r (inside) through
    `angle`, then a straight flange of `length`. Flattened: an equivalent strip of length BA + length."""
    if ctx.sheet is None or ctx.body is None:
        raise FeatureError("insert a Base Flange first")
    t, r, k = ctx.sheet["thickness"], float(p.get("bend_radius", ctx.sheet["radius"])), ctx.sheet["k"]
    L, phi = float(p.get("length", 20.0)), math.radians(float(p.get("angle", 90.0)))
    if not 0 < phi <= math.pi or L < 0:
        raise FeatureError("angle must be in (0, 180] degrees and length >= 0")
    edge = resolve_edge(ctx.body, p["edge"])
    if edge.geom_type.name.lower() != "line":
        raise FeatureError("edge flanges need a straight edge")
    a0, a1 = edge.position_at(0), edge.position_at(1)
    e_dir, e_len = (a1 - a0).normalized(), (a1 - a0).length
    adj = [f for f in ctx.body.faces() if any(edge.is_same(x) for x in f.edges())]
    if len(adj) != 2 or any(f.geom_type.name.lower() != "plane" for f in adj):
        raise FeatureError("pick an edge between the sheet face and its thickness face")
    widths = [f.area / e_len for f in adj]
    side, face = (adj[0], adj[1]) if abs(widths[0] - t) < abs(widths[1] - t) else (adj[1], adj[0])
    n, s = face.normal_at().normalized(), side.normal_at().normalized()
    if p.get("flip"):
        n = -n
        a0 = a0 + n * (-t)  # bend from the opposite face: the corner on that face becomes the inner corner
    BA = phi * (r + k * t)
    if ctx.flat or name in ctx.unfold:
        # flat strip in the sheet plane, pointing away from the side face (box y axis = n x x_dir must be +s)
        x_dir, origin = (e_dir, a0) if n.cross(e_dir).dot(s) > 0 else (-e_dir, a1)
        tool = Solid.make_box(e_len, BA + L, t, Plane(origin=origin - n * t, x_dir=x_dir, z_dir=n))
    else:
        C = a0 + n * r
        rad = lambda ang, rr: C + (n * (-math.cos(ang)) + s * math.sin(ang)) * rr  # noqa: E731
        w = n * math.sin(phi) + s * math.cos(phi)
        A, B, D, G = rad(0, r), rad(0, r + t), rad(phi, r + t), rad(phi, r)
        from build123d import Edge as _E, Face as _F, Wire as _W
        edges = [_E.make_line(A, B), _E.make_three_point_arc(B, rad(phi / 2, r + t), D)]
        if L > 0:
            edges += [_E.make_line(D, D + w * L), _E.make_line(D + w * L, G + w * L), _E.make_line(G + w * L, G)]
        else:
            edges += [_E.make_line(D, G)]
        edges += [_E.make_three_point_arc(G, rad(phi / 2, r), A)]
        prof = _F(_W(edges))
        tool = extrude(prof, amount=e_len, dir=e_dir)
    apply_op(ctx, "add", tool)
    bend = {"feature": name, "angle": math.degrees(phi), "radius": r, "allowance": BA, "length": L}
    if ctx.flat or name in ctx.unfold:
        y = n.cross(x_dir).normalized()
        c0 = origin + y * (BA / 2)
        bend |= {"flat_line": [list(c0), list(c0 + x_dir * e_len)], "toward": list(n)}
        if not ctx.flat:  # unfolded: what a Fold needs to bend it back (the line on the face, mid-zone)
            _keep_unfolded(ctx, name, a0 + s * (BA / 2), e_dir, n, s, phi, r, 1.0)
    ctx.sheet["bends"].append(bend)
    ctx.tools[name] = [("add", tool)]


def build_hem(ctx: Ctx, p: dict, name: str) -> None:
    """Sheet metal Hem on a straight edge: the sheet folded back 180 deg. closed = tight (gap ~ 0), open =
    `gap` between the faces; `length` of the folded-back leg."""
    if ctx.sheet is None:
        raise FeatureError("insert a Base Flange first")
    gap = 0.0 if p.get("type", "closed") == "closed" else float(p.get("gap", ctx.sheet["thickness"]))
    r = max(gap / 2, 0.01 * ctx.sheet["thickness"])
    build_edge_flange(ctx, {"edge": p["edge"], "angle": 180.0, "length": float(p.get("length", 10.0)),
                            "bend_radius": r, "flip": p.get("flip", False)}, name)
    ctx.sheet["bends"][-1]["hem"] = p.get("type", "closed")


def _bend(ctx: Ctx, a: Vector, u: Vector, n: Vector, m: Vector, phi: float, r: float, up: float):
    """Bend the sheet about the line through `a` (on the sheet face whose outward normal is `n`) with
    direction `u`; `m` points across the line towards the side that moves; up = +1 bends towards +n.
    Volume-conserving for K = 0.5 (bend allowance BA = phi (r + K t)). Returns (BA, point map, vector map)
    taking the moving side's flat geometry to its bent position."""
    t, k = ctx.sheet["thickness"], ctx.sheet["k"]
    BA = phi * (r + k * t)
    body = ctx.body
    bb = body.bounding_box()
    big = bb.diagonal * 4 + 100
    ac = a + u * (bb.center() - a).dot(u)  # centre the cutting boxes on the body along the line, not on `a`
    half = lambda shift, keep_pos: Solid.make_box(big, big, big, Plane(  # noqa: E731
        origin=ac + m * shift - u * big / 2 - n * big / 2 - (m * big if not keep_pos else Vector(0, 0, 0)), x_dir=m, z_dir=n))
    as_shape = lambda x: Compound(list(x)) if isinstance(x, (list, tuple)) else x  # noqa: E731  (ShapeList)
    A = as_shape(body.intersect(half(-BA / 2, False)))  # fixed side, shortened by half the allowance
    B = as_shape(body.intersect(half(BA / 2, True)))  # moving side
    if A is None or B is None or not A.solids() or not B.solids():
        raise FeatureError("the bend line must cross the sheet")
    n0 = a.dot(n) - (0.0 if up > 0 else t)  # inside face of the bend (the line lies on the outward face)
    p0 = a + m * (-BA / 2)
    p0 = p0 + n * (n0 - p0.dot(n))
    C = p0 + n * (up * r)  # bend axis
    ax = Axis(C, u * (-1 if up > 0 else 1))  # rotates the inner-face direction (-up n) onto m
    deg = math.degrees(phi)
    from build123d import Edge as _E, Face as _F, Wire as _W
    pr = lambda ang, rr: C + (n * (-up * math.cos(ang)) + m * math.sin(ang)) * rr  # noqa: E731
    prof = _F(_W([_E.make_line(pr(0, r), pr(0, r + t)), _E.make_three_point_arc(pr(0, r + t), pr(phi / 2, r + t), pr(phi, r + t)),
                  _E.make_line(pr(phi, r + t), pr(phi, r)), _E.make_three_point_arc(pr(phi, r), pr(phi / 2, r), pr(0, r))]))
    us = [Vector(v.X, v.Y, v.Z).dot(u) for v in body.vertices()]
    prof = prof.moved(Location(u * (min(us) - prof.center().dot(u))))
    arc = extrude(prof, amount=max(us) - min(us), dir=u)
    for tool in _bend_zone_cuts(ctx, body, a, u, n, m, phi, r, up, BA, C, min(us), max(us)):
        arc = arc.cut(tool)  # holes and notches cut while the sheet was flat, wrapped onto the bend
    B = B.moved(Location(m * (-BA))).rotate(ax, deg)  # shift back to the bend start, rotate about the axis
    ctx.body = A.fuse(arc).fuse(B).clean()
    pt = lambda q: C + _rot(q - m * BA - C, ax, deg)  # noqa: E731
    vv = lambda v: _rot(v, ax, deg)  # noqa: E731
    return BA, arc, pt, vv


def _bend_zone_cuts(ctx: Ctx, body, a, u, n, m, phi, r, up, BA, C, u0, u1) -> list:
    """Material missing from the flat bend zone (holes or notches cut while the sheet was flat), as cutting
    tools wrapped onto the bend: a point of the zone `s` along m from its start, `v` along u and `d` into
    the sheet from the inside face lands at angle s / BA * phi, radius r + d about the bend axis. The cut
    walls run radially, so for K = 0.5 the bent part keeps the flat part's volume."""
    t = ctx.sheet["thickness"]
    top = a.dot(n)  # the sheet spans [top - t, top] along n
    start = a - m * (BA / 2)
    corner = start + n * (top - t - start.dot(n)) + u * (u0 - start.dot(u))
    zone = Solid.make_box(BA, u1 - u0, t, Plane(origin=corner, x_dir=m, z_dir=n)) if n.cross(m).dot(u) > 0 else \
        Solid.make_box(BA, u1 - u0, t, Plane(origin=corner + u * (u1 - u0), x_dir=m, z_dir=n))
    try:
        missing = zone.cut(body)
    except Exception:  # noqa: BLE001 - a failed boolean: bend as before
        return []
    if missing is None or not missing.solids() or missing.volume < 1e-9 * max(zone.volume, 1e-9):
        return []
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.BRepLib import BRepLib
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.Geom2d import Geom2d_Line, Geom2d_TrimmedCurve
    from OCP.Geom2dAPI import Geom2dAPI_Interpolate
    from OCP.collections import HArray1_gp_Pnt2d
    from OCP.gp import gp_Ax3, gp_Dir, gp_Dir2d, gp_Pnt, gp_Pnt2d
    from build123d import Face as _F
    inner_level = top if up > 0 else top - t  # the inside face of the bend
    eps = 0.05 * t
    r0 = max(r - eps, 0.5 * r)  # the tool starts a little inside the bend's inner face and ends past its outer one
    # the bend's cylinder: angle 0 along -up n (the zone's start), increasing towards m; v along `ax`
    xd = n * (-up)
    ax = xd.cross(m).normalized()
    surf = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(C.X, C.Y, C.Z), gp_Dir(ax.X, ax.Y, ax.Z), gp_Dir(xd.X, xd.Y, xd.Z)), r0)
    uv = lambda q: gp_Pnt2d((q - start).dot(m) / BA * phi, (q - C).dot(ax))  # noqa: E731

    tools = []
    for f in missing.faces():
        if f.geom_type.name.lower() != "plane" or abs(abs(f.normal_at(f.center()).dot(n)) - 1) > 1e-9 \
                or abs(f.center().dot(n) - inner_level) > 1e-6:
            continue  # one footprint per cut: its face on the inside face of the bend
        occ_wires = []
        for w in [f.outer_wire()] + list(f.inner_wires()):
            mk = BRepBuilderAPI_MakeWire()
            for e in w.edges():
                if e.geom_type.name.lower() == "line":  # a straight line in the flat stays straight in (angle, v)
                    a2, b2 = uv(e.position_at(0)), uv(e.position_at(1))
                    ln = math.hypot(b2.X() - a2.X(), b2.Y() - a2.Y())
                    if ln < 1e-12:
                        continue
                    seg = Geom2d_TrimmedCurve(Geom2d_Line(a2, gp_Dir2d(b2.X() - a2.X(), b2.Y() - a2.Y())), 0.0, ln)
                    mk.Add(BRepBuilderAPI_MakeEdge(seg, surf).Edge())
                else:
                    pts = [uv(e.position_at(i / 96)) for i in range(97)]
                    closed = (e.position_at(0) - e.position_at(1)).length < 1e-9
                    arr = HArray1_gp_Pnt2d(1, len(pts) - (1 if closed else 0))
                    for i, q in enumerate(pts[: arr.Length()], 1):
                        arr.SetValue(i, q)
                    it = Geom2dAPI_Interpolate(arr, closed, 1e-9)
                    it.Perform()
                    mk.Add(BRepBuilderAPI_MakeEdge(it.Curve(), surf).Edge())
            if not mk.IsDone():
                raise FeatureError("a cut across the bend could not be wrapped onto it")
            occ_wires.append(mk.Wire())
        mf = BRepBuilderAPI_MakeFace(surf, occ_wires[0], True)
        for iw in occ_wires[1:]:
            mf.Add(iw)
        face = mf.Face()
        BRepLib.BuildCurves3d_s(face)
        face = _F(face)
        c = face.center()
        outward = face.normal_at(c).dot(c - C - ax * (c - C).dot(ax)) > 0
        depth = (r + t + eps) - r0
        try:
            tools.append(Solid.thicken(face, depth if outward else -depth))
        except Exception as exc:  # noqa: BLE001
            raise FeatureError(f"a cut across the bend could not be wrapped onto it: {exc}") from exc
    return tools


def _bend_line(ctx: Ctx, p: dict):
    sk, plane = _sketch(ctx, p["sketch"])
    lines = [e for e in sk.entities.values() if e["type"] == "line" and not e.get("construction")]
    if len(lines) != 1:
        raise FeatureError("the bend sketch needs exactly one line (the bend line)")
    a = plane.from_local_coords(Vector(*sk.coords(lines[0]["p1"]), 0))
    b = plane.from_local_coords(Vector(*sk.coords(lines[0]["p2"]), 0))
    u = (b - a).normalized()
    n = plane.z_dir.normalized()  # sketch on the sheet face: n points out of that face
    m = u.cross(n).normalized()  # in-sheet direction across the bend line
    fixed = vec(p["fixed"]) if p.get("fixed") is not None else ctx.body.center() - m * 1e3
    if (fixed - a).dot(m) > 0:
        m = -m  # m points from the fixed side towards the moving side
    return a, u, n, m


def _keep_unfolded(ctx: Ctx, key: str, a, u, n, m, phi: float, r: float, up: float) -> None:
    """A bend an Unfold has laid flat: its line (through `a` on the face with normal `n`, along `u`, the
    moving side towards `m`), angle and radius, kept until a Fold bends it back (sheet_more.build_fold)."""
    pend = dict(ctx.sheet.get("unfolded", {}))
    pend[key] = {"a": list(a), "u": list(u), "n": list(n), "m": list(m), "phi": phi, "r": r, "up": up}
    ctx.sheet["unfolded"] = pend


def _flat_bend_line(ctx: Ctx, a: Vector, u: Vector) -> list:
    """The bend line through `a` along `u`, across the whole flat sheet (for the flat pattern's bend notes)."""
    us = [Vector(v.X, v.Y, v.Z).dot(u) for v in ctx.body.vertices()]
    a0 = a - u * a.dot(u)
    return [list(a0 + u * min(us)), list(a0 + u * max(us))]


def build_sketched_bend(ctx: Ctx, p: dict, name: str) -> None:
    """Sheet metal Sketched Bend: bend the sheet along a sketch line (bend centreline) on its face by
    `angle` with inside radius r. The side containing `fixed` ([x, y, z], default the side holding the base
    sketch origin) stays put; flattened, the part is unchanged (bend allowance BA = phi (r + K t))."""
    if ctx.sheet is None or ctx.body is None:
        raise FeatureError("insert a Base Flange first")
    r = float(p.get("bend_radius", ctx.sheet["radius"]))
    phi = math.radians(float(p.get("angle", 90.0)))
    if not 0 < phi < math.pi:
        raise FeatureError("bend angle must be between 0 and 180 degrees")
    a, u, n, m = _bend_line(ctx, p)
    if ctx.flat or name in ctx.unfold:  # the flat pattern keeps the sheet flat: only the bend line is noted
        if not ctx.flat:
            _keep_unfolded(ctx, name, a, u, n, m, phi, r, -1.0 if p.get("flip") else 1.0)
        BA = phi * (r + ctx.sheet["k"] * ctx.sheet["thickness"])
        ctx.sheet["bends"].append({"feature": name, "angle": math.degrees(phi), "radius": r, "allowance": BA, "sketched": True,
                                   "flat_line": _flat_bend_line(ctx, a, u), "toward": list(n * (-1 if p.get("flip") else 1))})
        return
    BA, arc, _, _ = _bend(ctx, a, u, n, m, phi, r, -1.0 if p.get("flip") else 1.0)
    ctx.sheet["bends"].append({"feature": name, "angle": math.degrees(phi), "radius": r, "allowance": BA, "sketched": True})
    ctx.tools[name] = [("add", arc)]


def build_corner_relief(ctx: Ctx, p: dict, name: str) -> None:
    """Sheet metal Corner Relief: a rectangular (size x size) or circular (diameter size) relief cut through
    the sheet, centred on a corner point, along the base sheet normal."""
    if ctx.sheet is None or ctx.body is None:
        raise FeatureError("insert a Base Flange first")
    c, size = vec(p["point"]), float(p.get("size", 2 * ctx.sheet["thickness"]))
    if size <= 0:
        raise FeatureError("relief size must be positive")
    n = vec(ctx.sheet["normal"]).normalized()
    depth = size + 2 * ctx.sheet["thickness"]
    pl = plane_for_normal(c - n * depth / 2, n)
    if p.get("type", "rectangular") == "circular":
        tool = Solid.make_cylinder(size / 2, depth, pl)
    else:
        tool = Solid.make_box(size, size, depth, Plane(origin=c - n * depth / 2 - pl.x_dir * size / 2 - pl.y_dir * size / 2,
                                                       x_dir=pl.x_dir, z_dir=n))
    apply_op(ctx, "cut", tool)
    ctx.tools[name] = [("cut", tool)]


def build_dimple(ctx: Ctx, p: dict, name: str) -> None:
    """Sheet metal Forming Tool (dimple): press a spherical cup of `diameter` and `height` into the sheet at a
    point on its face; the wall keeps the sheet thickness (reverse = press the other way)."""
    if ctx.sheet is None or ctx.body is None:
        raise FeatureError("insert a Base Flange first")
    t = ctx.sheet["thickness"]
    face = resolve_face(ctx.body, p["face"])
    n = face.normal_at().normalized()
    if p.get("reverse"):
        n = -n
    a, h = float(p.get("diameter", 20.0)) / 2, float(p.get("height", 5.0))
    if h <= 0 or h > a:
        raise FeatureError("dimple height must be in (0, diameter / 2]")
    q = vec(p["point"])
    top = q - n * (q - face.center()).dot(n)  # on the selected face
    if p.get("reverse"):
        top = top + n * t  # pressing from the other face: start on the opposite surface
    R = (a * a + h * h) / (2 * h)
    cen = top + n * (h - R)  # sphere through the rim circle (radius a) at the face, apex h above it
    bottom = top - n * t
    keep = Solid.make_box(4 * R, 4 * R, 4 * R, Plane(origin=bottom - plane_for_normal(bottom, n).x_dir * 2 * R
                                                      - plane_for_normal(bottom, n).y_dir * 2 * R, x_dir=plane_for_normal(bottom, n).x_dir, z_dir=n))
    outer = Solid.make_sphere(R).moved(Location(cen)).intersect(keep)
    inner = Solid.make_sphere(R - t).moved(Location(cen - n * 0)).intersect(keep)
    as_shape = lambda x: Compound(list(x)) if isinstance(x, (list, tuple)) else x  # noqa: E731
    outer, inner = as_shape(outer), as_shape(inner)
    body = ctx.body.fuse(outer).cut(inner).clean()
    ctx.body = body
    ctx.tools[name] = [("add", outer)]
    ctx.info[name] = {"sphere_radius": R}


def _developed_profile(sk, lines, t, side, r, k):
    """Flat width of a miter-flange profile (open chain of lines, sheet thickness t on `side`): the straight
    parts left after the bends plus each bend's allowance BA = angle (r + k t). Returns (width, [(deg, BA)])."""
    segs = [(Vector(*sk.coords(ln["p1"]), 0), Vector(*sk.coords(ln["p2"]), 0)) for ln in lines]
    same = lambda a, b: (a - b).length < 1e-9  # noqa: E731
    ends = [q for s_ in segs for q in s_]
    starts = [q for q in ends if sum(same(q, o) for o in ends) == 1]
    total = sum((b - a).length for a, b in segs)
    if len(starts) != 2 or r <= 0:
        return total, []  # not a single chain (or sharp): the sketched length
    pts, rest = [starts[0]], list(segs)
    while rest:
        nxt = next((s_ for s_ in rest if same(s_[0], pts[-1]) or same(s_[1], pts[-1])), None)
        if nxt is None:
            return total, []
        rest.remove(nxt)
        pts.append(nxt[1] if same(nxt[0], pts[-1]) else nxt[0])
    d = [(pts[i + 1] - pts[i]).normalized() for i in range(len(pts) - 1)]
    W, bends = total, []
    for i in range(1, len(pts) - 1):
        ang = math.acos(max(-1.0, min(1.0, d[i - 1].dot(d[i]))))  # turn angle
        turn = d[i - 1].cross(d[i]).Z * side
        rp = r + t if turn > 0 else r  # radius of the sketched (profile-line) face at this corner
        BA = ang * (r + k * t)
        W += BA - 2 * rp * math.tan(ang / 2)
        bends.append((math.degrees(ang), BA))
    return W, bends


def _bent_profile(sk, plane, lines, t, side, r):
    """Thickened miter-flange profile with the sheet bend radius at every corner: the lines must form one open
    chain. Each corner gets concentric arcs, inner radius r on the inside of the turn and r + t outside.
    Returns a Face in model space, or None (not a single chain, or r <= 0) for the sharp fallback."""
    from build123d import Edge as _E, Face as _F, Wire as _W
    if r <= 0 or len(lines) < 2:
        return None
    segs = [(tuple(sk.coords(ln["p1"])), tuple(sk.coords(ln["p2"]))) for ln in lines]
    same = lambda a, b: abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9  # noqa: E731
    ends = [q for s in segs for q in s]
    starts = [q for q in ends if sum(same(q, o) for o in ends) == 1]
    if len(starts) != 2:
        return None
    pts, rest = [starts[0]], list(segs)
    while rest:
        nxt = next((s for s in rest if same(s[0], pts[-1]) or same(s[1], pts[-1])), None)
        if nxt is None:
            return None
        rest.remove(nxt)
        pts.append(nxt[1] if same(nxt[0], pts[-1]) else nxt[0])
    P = [Vector(x, y, 0) for x, y in pts]
    d = [(P[i + 1] - P[i]).normalized() for i in range(len(P) - 1)]
    nl = [Vector(-v.Y, v.X, 0) * side for v in d]  # towards the material
    Q = [P[0] + nl[0] * t]
    for i in range(1, len(P) - 1):  # mitred offset vertex
        m = nl[i - 1] + nl[i]
        Q.append(P[i] + m * (t / (1 + nl[i - 1].dot(nl[i]))))
    Q.append(P[-1] + nl[-1] * t)
    rp, rq = [], []
    for i in range(1, len(P) - 1):
        turn = d[i - 1].cross(d[i]).Z * side  # > 0: the material lies on the inside of the turn
        rp.append(r + t if turn > 0 else r)
        rq.append(r if turn > 0 else r + t)

    def filleted(poly, radii):
        w = _W.make_polygon([plane.from_local_coords(v) for v in poly], close=False)
        for k, rad in enumerate(radii, 1):
            corner = plane.from_local_coords(poly[k])
            vx = min(w.vertices(), key=lambda v: (Vector(v) - corner).length)
            w = w.fillet_2d(rad, [vx])
        return w

    try:
        wp, wq = filleted(P, rp), filleted(Q, rq)
        g = lambda v: plane.from_local_coords(v)  # noqa: E731
        edges = list(wp.edges()) + [_E.make_line(g(P[-1]), g(Q[-1]))] + list(wq.edges()) + [_E.make_line(g(Q[0]), g(P[0]))]
        face = _F(_W(edges))
    except Exception:  # degenerate (radius larger than a segment): keep the sharp profile
        return None
    return face if face.area > 0 else None


def build_miter_flange(ctx: Ctx, p: dict, name: str) -> None:
    """Sheet metal Miter Flange along a chain of straight sheet edges (`edges`, or a single `edge`): an open
    profile sketch of lines, drawn on a plane perpendicular to the first edge at one of its ends, is thickened
    by the sheet thickness (flip = to the other side), carried around the chain (rotated about the sheet
    normal at each corner) and mitred on the bisector planes where consecutive edges meet."""
    from build123d import Edge as _E, Face as _F, Keep, Wire as _W
    if ctx.sheet is None or ctx.body is None:
        raise FeatureError("insert a Base Flange first")
    t = ctx.sheet["thickness"]
    refs = p.get("edges") or [p["edge"]]
    edges = [resolve_edge(ctx.body, r) for r in refs]
    if any(e.geom_type.name.lower() != "line" for e in edges):
        raise FeatureError("miter flanges need straight edges")
    sk, plane = _sketch(ctx, p["sketch"])
    lines = [e for e in sk.entities.values() if e["type"] == "line" and not e.get("construction")]
    if not lines:
        raise FeatureError("the profile sketch needs straight lines")
    nrm = vec(ctx.sheet["normal"]).normalized()
    # order the edges into a chain starting at the end nearest the profile plane
    P0 = plane.origin
    first = edges[0]
    a0, b0 = first.position_at(0), first.position_at(1)
    if abs((a0 - P0).dot(plane.z_dir)) > abs((b0 - P0).dot(plane.z_dir)):
        a0, b0 = b0, a0
    if abs(abs(plane.z_dir.dot((b0 - a0).normalized())) - 1) > 1e-6:
        raise FeatureError("draw the profile on a plane perpendicular to the first edge")
    chain = [(a0, b0)]
    rest = edges[1:]
    while rest:
        tail = chain[-1][1]
        nxt = next((e for e in rest if min((e.position_at(0) - tail).length, (e.position_at(1) - tail).length) < 1e-6), None)
        if nxt is None:
            raise FeatureError("the edges must form one connected chain")
        rest.remove(nxt)
        a, b = (nxt.position_at(0), nxt.position_at(1)) if (nxt.position_at(0) - tail).length < 1e-6 else (nxt.position_at(1), nxt.position_at(0))
        chain.append((a, b))
    side = -1.0 if p.get("flip") else 1.0
    r_bend = float(p.get("bend_radius", ctx.sheet["radius"]))
    if ctx.flat:  # flat pattern: one strip per chain edge, as wide as the developed profile
        W, bends = _developed_profile(sk, lines, t, side, r_bend, ctx.sheet["k"])
        strips = []
        for a, b in chain:
            d = (b - a).normalized()
            o = nrm.cross(d).normalized()
            mid = a + (b - a) * 0.5 - nrm * (t / 2)
            if ctx.body.is_inside(mid + o * (t / 4)):
                o = -o  # point away from the sheet
            base = a - nrm * t  # the strip spans the sheet thickness below the top face
            if (a - P0).dot(nrm) < -t / 2:  # the edge lies on the bottom face
                base = a
            strips.append(Solid.make_box((b - a).length, W, t, Plane(origin=base, x_dir=d, z_dir=nrm)
                                         if nrm.cross(d).dot(o) > 0 else Plane(origin=base + d * (b - a).length, x_dir=-d, z_dir=nrm)))
        tool = strips[0]
        for sg in strips[1:]:
            tool = tool.fuse(sg)
        apply_op(ctx, "add", tool)
        ctx.tools[name] = [("add", tool)]
        ctx.sheet["bends"] += [{"feature": name, "angle": a_, "radius": r_bend, "allowance": ba, "miter": True}
                               for a_, ba in bends] * len(chain)
        return
    prof_faces = []
    bent = _bent_profile(sk, plane, lines, t, side, r_bend)
    if bent is not None:
        prof_faces, lines = [bent], []
    for ln in lines:
        A = Vector(*sk.coords(ln["p1"]), 0)
        B = Vector(*sk.coords(ln["p2"]), 0)
        dv = (B - A).normalized()
        off = Vector(-dv.Y, dv.X, 0) * (t * side)
        pts = [plane.from_local_coords(v) for v in (A, B, B + off, A + off)]
        prof_faces.append(_F(_W([_E.make_line(pts[k], pts[(k + 1) % 4]) for k in range(4)])))
    d0 = (b0 - a0).normalized()
    ext = 4 * max(t, max(f.bounding_box().diagonal for f in prof_faces))
    tools = []
    for k, (a, b) in enumerate(chain):
        d = (b - a).normalized()
        ang = math.degrees(math.atan2(d0.cross(d).dot(nrm), d0.dot(d)))  # rotation about the sheet normal
        e0 = ext if k > 0 else 0.0
        e1 = ext if k < len(chain) - 1 else 0.0
        segs = []
        for f in prof_faces:
            g = f.moved(Location(a - a0)).rotate(Axis(a, nrm), ang) if k else f
            g = g.moved(Location(-d * e0))
            segs.append(extrude(g, amount=(b - a).length + e0 + e1, dir=d))
        tool = segs[0]
        for sg in segs[1:]:
            tool = tool.fuse(sg)
        mid = a + (b - a) * 0.5
        for P, dn in ((a, chain[k - 1] if k else None), (b, chain[k + 1] if k < len(chain) - 1 else None)):
            if dn is None:
                continue
            other = (dn[1] - dn[0]).normalized()
            di, dj = (d, other) if P is b else (other, d)  # arriving / leaving directions at the corner
            mplane = (di + dj).normalized()
            keep = Keep.BOTTOM if (mid - P).dot(mplane) < 0 else Keep.TOP
            tool = tool.split(Plane(origin=P, z_dir=mplane), keep=keep)
        tools.append(tool)
    tool = tools[0]
    for tl in tools[1:]:
        tool = tool.fuse(tl)
    apply_op(ctx, "add", tool)
    ctx.tools[name] = [("add", tool)]


def build_louver(ctx: Ctx, p: dict, name: str) -> None:
    """Forming tool Louver: a slot `length` x `width` cut through the sheet at `point` on a face, with an
    open-front hood (arc band of the sheet thickness) rising `height` from one long edge of the slot."""
    from build123d import Edge as _E, Face as _F, Wire as _W
    if ctx.sheet is None or ctx.body is None:
        raise FeatureError("insert a Base Flange first")
    t = ctx.sheet["thickness"]
    face = resolve_face(ctx.body, p["face"])
    n = face.normal_at().normalized()
    L, W, H = float(p.get("length", 30.0)), float(p.get("width", 6.0)), float(p.get("height", 4.0))
    q = vec(p["point"])
    top = q - n * (q - face.center()).dot(n)
    pl = plane_for_normal(top, n)
    ux = (pl.x_dir * math.cos(math.radians(p.get("angle", 0.0))) + pl.y_dir * math.sin(math.radians(p.get("angle", 0.0)))).normalized()
    wy = n.cross(ux).normalized()
    corner = top - ux * (L / 2) - wy * (W / 2)
    slot = Solid.make_box(L, W, t * 3, Plane(origin=corner - n * (t * 2), x_dir=ux, z_dir=n))
    # hood cross-section (in the wy-n plane): arc from the attached slot edge up to height H over the width
    P = lambda yy, zz: corner + wy * yy + n * zz  # noqa: E731
    outer = [P(0, 0), P(W / 2, H * 0.75 + t), P(W, H + t)]
    inner = [P(W, H), P(W / 2, H * 0.75), P(0, -t if False else 0)]
    prof = _F(_W([_E.make_three_point_arc(outer[0], outer[1], outer[2]), _E.make_line(outer[2], inner[0]),
                  _E.make_three_point_arc(inner[0], inner[1], P(t * 0.5, 0)), _E.make_line(P(t * 0.5, 0), outer[0])]))
    hood = extrude(prof, amount=L, dir=ux)
    ctx.body = ctx.body.cut(slot).fuse(hood).clean()
    ctx.tools[name] = [("cut", slot), ("add", hood)]


def build_lance(ctx: Ctx, p: dict, name: str) -> None:
    """Forming tool Bridge Lance: a strip `length` x `width` at `point` on a face is cut free along its long
    sides and raised `height` as a bridge with ramps of `ramp` length at both ends (still attached)."""
    from build123d import Edge as _E, Face as _F, Wire as _W
    if ctx.sheet is None or ctx.body is None:
        raise FeatureError("insert a Base Flange first")
    t = ctx.sheet["thickness"]
    face = resolve_face(ctx.body, p["face"])
    n = face.normal_at().normalized()
    L, W, H = float(p.get("length", 30.0)), float(p.get("width", 6.0)), float(p.get("height", 3.0))
    r = float(p.get("ramp", H))
    if 2 * r >= L:
        raise FeatureError("the ramps are longer than the lance")
    q = vec(p["point"])
    top = q - n * (q - face.center()).dot(n)
    pl = plane_for_normal(top, n)
    ux = (pl.x_dir * math.cos(math.radians(p.get("angle", 0.0))) + pl.y_dir * math.sin(math.radians(p.get("angle", 0.0)))).normalized()
    wy = n.cross(ux).normalized()
    start = top - ux * (L / 2) - wy * (W / 2)
    strip = Solid.make_box(L, W, t * 3, Plane(origin=start - n * (t * 2), x_dir=ux, z_dir=n))
    P = lambda xx, zz: start + ux * xx + n * zz  # noqa: E731  (profile in the ux-n plane)
    pts = [P(0, 0), P(r, H), P(L - r, H), P(L, 0), P(L, -t), P(L - r, H - t), P(r, H - t), P(0, -t)]
    prof = _F(_W([_E.make_line(pts[k], pts[(k + 1) % len(pts)]) for k in range(len(pts))]))
    bridge = extrude(prof, amount=W, dir=wy)
    ctx.body = ctx.body.cut(strip).fuse(bridge).clean()
    ctx.tools[name] = [("cut", strip), ("add", bridge)]


def _forming_frame(ctx: Ctx, p: dict):
    """Sheet thickness, outward normal of the picked face, the point projected onto it, and in-plane axes
    rotated by p['angle'] (deg)."""
    if ctx.sheet is None or ctx.body is None:
        raise FeatureError("insert a Base Flange first")
    face = resolve_face(ctx.body, p["face"])
    n = face.normal_at().normalized()
    q = vec(p["point"])
    top = q - n * (q - face.center()).dot(n)
    pl = plane_for_normal(top, n)
    ang = math.radians(float(p.get("angle", 0.0)))
    ux = (pl.x_dir * math.cos(ang) + pl.y_dir * math.sin(ang)).normalized()
    return ctx.sheet["thickness"], n, top, ux, n.cross(ux).normalized()


def build_extruded_flange(ctx: Ctx, p: dict, name: str) -> None:
    """Forming tool Extruded Flange (flanged hole): a hole of inner `diameter` pierced through the sheet at
    `point` and a collar of sheet thickness drawn `height` out of the face (reverse = into the other side)."""
    t, n, top, _, _ = _forming_frame(ctx, p)
    r, H = float(p.get("diameter", 10.0)) / 2, float(p.get("height", 5.0))
    if r <= 0 or H <= 0:
        raise FeatureError("diameter and height must be positive")
    if p.get("reverse"):
        n, top = -n, top - n * t  # draw the collar out of the opposite face
    hole = Solid.make_cylinder(r, H + 3 * t, plane_for_normal(top - n * (2 * t), n))
    collar = Solid.make_cylinder(r + t, H, plane_for_normal(top, n)).cut(Solid.make_cylinder(r, H, plane_for_normal(top, n)))
    ctx.body = ctx.body.cut(hole).fuse(collar).clean()
    ctx.tools[name] = [("cut", hole), ("add", collar)]


def build_form_rib(ctx: Ctx, p: dict, name: str) -> None:
    """Forming tool Rib (embossed stiffening bead): a straight bead `length` long (end to end) and `width`
    wide, raised `height` out of the face with a circular-arc cross-section and domed ends; the bead wall
    keeps the sheet thickness (reverse = pressed the other way)."""
    t, n, top, ux, wy = _forming_frame(ctx, p)
    L, W, H = float(p.get("length", 40.0)), float(p.get("width", 8.0)), float(p.get("height", 2.5))
    a = W / 2
    if H <= 0 or H > a:
        raise FeatureError("rib height must be in (0, width / 2]")
    if L <= W:
        raise FeatureError("rib length must exceed its width")
    if p.get("reverse"):
        n, wy, top = -n, -wy, top - n * t
    R = (a * a + H * H) / (2 * H)  # arc through the bead edges (half-width a) with apex H above the face
    axis0 = top + n * (H - R)  # centre line of the arc cylinder
    half = L / 2 - a  # straight part between the two domes
    bottom = top - n * t

    def capsule(rad):
        cyl = Solid.make_cylinder(rad, 2 * half, Plane(origin=axis0 - ux * half, x_dir=wy, z_dir=ux))
        s = cyl.fuse(Solid.make_sphere(rad).moved(Location(axis0 - ux * half)))
        return s.fuse(Solid.make_sphere(rad).moved(Location(axis0 + ux * half)))

    big = 4 * (L + R)
    pl = plane_for_normal(bottom, n)
    keep = Solid.make_box(big, big, big, Plane(origin=bottom - pl.x_dir * big / 2 - pl.y_dir * big / 2, x_dir=pl.x_dir, z_dir=n))
    as_shape = lambda x: Compound(list(x)) if isinstance(x, (list, tuple)) or hasattr(x, "first") else x  # noqa: E731
    outer, inner = as_shape(capsule(R).intersect(keep)), as_shape(capsule(R - t).intersect(keep))
    ctx.body = ctx.body.fuse(outer).cut(inner).clean()
    ctx.tools[name] = [("add", outer)]
    ctx.info[name] = {"arc_radius": R}


def build_auto_corner_relief(ctx: Ctx, p: dict, name: str) -> None:
    """Sheet metal Corner Relief (collect all corners): wherever two flange features meet at a corner
    (touching or overlapping), cut a square relief of `size` (default 2 x thickness), centred on the corner,
    through the full flange height."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    if ctx.sheet is None or ctx.body is None:
        raise FeatureError("insert a Base Flange first")
    t = ctx.sheet["thickness"]
    size = float(p.get("size", 2 * t))
    nrm = vec(ctx.sheet["normal"]).normalized()
    bend_feats = {b.get("feature") for b in ctx.sheet.get("bends", [])}
    flange_tools = [(k, tl) for k, ts in ctx.tools.items() for op, tl in ts if op == "add" and k in bend_feats]
    heights = [v.X * nrm.X + v.Y * nrm.Y + v.Z * nrm.Z for v in ctx.body.vertices()]
    lo_h, hi_h = min(heights) - 1, max(heights) + 1
    cuts, seen = [], []
    for a in range(len(flange_tools)):
        for b in range(a + 1, len(flange_tools)):
            if flange_tools[a][0] == flange_tools[b][0]:
                continue
            d = BRepExtrema_DistShapeShape(flange_tools[a][1].wrapped, flange_tools[b][1].wrapped)
            if not d.IsDone() or d.Value() > 1e-6:
                continue
            pa = d.PointOnShape1(1)
            c = Vector(pa.X(), pa.Y(), pa.Z())
            c = c - nrm * c.dot(nrm)  # project onto the sheet base plane
            if any((c - q).length < 1e-6 for q in seen):
                continue
            seen.append(c)
            pl = plane_for_normal(c, nrm)
            cuts.append(Solid.make_box(size, size, hi_h - lo_h, Plane(origin=c + nrm * lo_h - pl.x_dir * size / 2 - pl.y_dir * size / 2,
                                                                       x_dir=pl.x_dir, z_dir=nrm)))
    if not cuts:
        raise FeatureError("no corners where two flanges meet")
    tool = cuts[0]
    for c in cuts[1:]:
        tool = tool.fuse(c)
    apply_op(ctx, "cut", tool)
    ctx.tools[name] = [("cut", tool)]
    ctx.info[name] = {"corners": len(cuts)}


def build_jog(ctx: Ctx, p: dict, name: str) -> None:
    """Sheet metal Jog: two opposite bends of `angle` along a sketch line, `distance` apart (flat length
    between the two bend centrelines), offsetting the rest of the sheet. Flat pattern unchanged."""
    if ctx.sheet is None or ctx.body is None:
        raise FeatureError("insert a Base Flange first")
    r = float(p.get("bend_radius", ctx.sheet["radius"]))
    phi = math.radians(float(p.get("angle", 90.0)))
    D = float(p.get("distance", 10.0))
    t, k = ctx.sheet["thickness"], ctx.sheet["k"]
    BA = phi * (r + k * t)
    if not 0 < phi < math.pi:
        raise FeatureError("jog angle must be between 0 and 180 degrees")
    if D <= BA:
        raise FeatureError(f"jog distance must exceed the bend allowance ({BA:.3f} mm)")
    a, u, n, m = _bend_line(ctx, p)
    up = -1.0 if p.get("flip") else 1.0
    if ctx.flat or name in ctx.unfold:  # two bend lines, D apart, bending opposite ways
        if not ctx.flat:
            _keep_unfolded(ctx, name, a, u, n, m, phi, r, up)
            _keep_unfolded(ctx, name + "#2", a + m * D, u, n, m, phi, r, -up)
        common = {"feature": name, "angle": math.degrees(phi), "radius": r, "allowance": BA, "jog": True}
        ctx.sheet["bends"] += [common | {"flat_line": _flat_bend_line(ctx, a, u), "toward": list(n * up)},
                               common | {"flat_line": _flat_bend_line(ctx, a + m * D, u), "toward": list(n * -up)}]
        return
    _, arc1, pt, vv = _bend(ctx, a, u, n, m, phi, r, up)
    a2, n2, m2 = pt(a + m * D), vv(n), vv(m)
    _, arc2, pt2, _ = _bend(ctx, a2, u, n2, m2, phi, r, -up)
    rise = (pt2(pt(a + m * (D + 2 * BA))) - a).dot(n)  # a top-face point beyond both bends
    ctx.sheet["bends"] += [{"feature": name, "angle": math.degrees(phi), "radius": r, "allowance": BA, "jog": True}] * 2
    ctx.tools[name] = [("add", arc1), ("add", arc2)]
    ctx.info[name] = {"allowance": BA, "flat_distance": D, "offset": round(rise, 9)}
