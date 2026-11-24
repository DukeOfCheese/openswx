"""Fillet and chamfer feature builders."""
from __future__ import annotations

import math

from build123d import Plane, Solid, Vector

from openswx.core.geom import resolve_edge, resolve_face, vec

from ._base import Ctx, FeatureError, _compound, _solids, apply_op


def _edges_from(ctx: Ctx, p: dict):
    if ctx.body is None:
        raise FeatureError("the part has no body")
    edges = [resolve_edge(ctx.body, r) for r in p.get("edges", [])]
    for fr in p.get("faces", []):
        edges += list(resolve_face(ctx.body, fr).edges())
    if not edges:
        raise FeatureError("select at least one edge or face")
    uniq = []
    for e in edges:
        if not any(e.is_same(u) for u in uniq):
            uniq.append(e)
    return uniq


def _per_solid(ctx: Ctx, edges, fn) -> None:
    out = []
    for s in _solids(ctx.body):
        mine = [e for e in edges if any(e.is_same(se) for se in s.edges())]
        out += list(fn(s, mine).solids()) if mine else [s]
    ctx.body = _compound(out)


def build_fillet(ctx: Ctx, p: dict, name: str) -> None:
    r = float(p.get("radius", 1.0))
    if r <= 0:
        raise FeatureError("radius must be positive")
    edges = _edges_from(ctx, p)
    try:
        _per_solid(ctx, edges, lambda s, es: s.fillet(r, es))
    except Exception as exc:
        raise FeatureError(f"fillet failed (radius too large for the geometry?): {exc}") from exc


def build_variable_fillet(ctx: Ctx, p: dict, name: str) -> None:
    """Variable Size Fillet: radius varies linearly from `r_start` to `r_end` along each selected edge."""
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from build123d import Shape as _Shape
    r1, r2 = float(p.get("r_start", 2.0)), float(p.get("r_end", 5.0))
    if r1 <= 0 or r2 <= 0:
        raise FeatureError("radii must be positive")
    edges = _edges_from(ctx, p)
    out = []
    for s in _solids(ctx.body):
        mine = [e for e in edges if any(e.is_same(se) for se in s.edges())]
        if not mine:
            out.append(s)
            continue
        mk = BRepFilletAPI_MakeFillet(s.wrapped)
        for e in mine:
            mk.Add(r1, r2, e.wrapped)
        mk.Build()
        if not mk.IsDone():
            raise FeatureError("variable fillet failed (radii too large for the geometry?)")
        out += list(_Shape.cast(mk.Shape()).solids())
    ctx.body = _compound(out)


def build_chamfer(ctx: Ctx, p: dict, name: str) -> None:
    d = float(p.get("distance", 1.0))
    ctype = p.get("type", "angle_distance")
    if ctype == "distance_distance":
        d2 = float(p.get("distance2", d))
    elif ctype == "equal_distance":
        d2 = d
    else:
        d2 = d * math.tan(math.radians(float(p.get("angle", 45.0))))
    edges = _edges_from(ctx, p)
    try:
        _per_solid(ctx, edges, lambda s, es: s.chamfer(d, d2, es))
    except Exception as exc:
        raise FeatureError(f"chamfer failed: {exc}") from exc


def build_vertex_chamfer(ctx: Ctx, p: dict, name: str) -> None:
    """Chamfer > Vertex: cut a corner vertex off by the plane through the points d1, d2, d3 along its three
    edges (equal distance if only d1). Convex corners with exactly three edges."""
    from build123d import Face as _F, Shell, Wire as _W
    if ctx.body is None:
        raise FeatureError("the part has no body")
    target = vec(p["vertex"])
    v = min((Vector(x.X, x.Y, x.Z) for x in ctx.body.vertices()), key=lambda q: (q - target).length)
    if (v - target).length > 1e-3:
        raise FeatureError("no vertex at that position")
    edges = [e for e in ctx.body.edges() if min((e.position_at(0) - v).length, (e.position_at(1) - v).length) < 1e-6]
    if len(edges) != 3:
        raise FeatureError(f"vertex chamfer needs a corner with three edges (found {len(edges)})")
    ds = [float(p.get("d1", 2.0))] * 3 if p.get("d2") is None else [float(p.get(k)) for k in ("d1", "d2", "d3")]
    pts = []
    for e, d in zip(edges, ds):
        if d <= 0 or d >= e.length:
            raise FeatureError("chamfer distances must be positive and shorter than the edges")
        t = d / e.length
        pts.append(e.position_at(t) if (e.position_at(0) - v).length < 1e-6 else e.position_at(1 - t))
    tri = lambda a, b, c: _F(_W.make_polygon([a, b, c], close=True))  # noqa: E731
    tet = Solid(Shell([tri(v, pts[0], pts[1]), tri(v, pts[1], pts[2]), tri(v, pts[2], pts[0]), tri(pts[0], pts[1], pts[2])]))
    if tet.volume < 0:
        tet = Solid(tet.wrapped.Reversed())
    apply_op(ctx, "cut", tet)
    ctx.tools[name] = [("cut", tet)]


def build_face_fillet(ctx: Ctx, p: dict, name: str) -> None:
    """Face Fillet: blend two face sets that meet (the fillet runs along their common edges)."""
    if ctx.body is None:
        raise FeatureError("the part has no body")
    f1 = [resolve_face(ctx.body, r) for r in p.get("faces1", [])]
    f2 = [resolve_face(ctx.body, r) for r in p.get("faces2", [])]
    common = [e for a in f1 for b in f2 for e in a.edges() if any(e.is_same(x) for x in b.edges())]
    if not common:
        raise FeatureError("the two face sets must meet along at least one edge")
    r = float(p.get("radius", 2.0))
    try:
        _per_solid(ctx, common, lambda s, es: s.fillet(r, es))
    except Exception as exc:
        raise FeatureError(f"face fillet failed (radius too large?): {exc}") from exc


def build_full_round_fillet(ctx: Ctx, p: dict, name: str) -> None:
    """Full Round Fillet: replace a planar centre face between two parallel side faces with a half-round
    tangent to both (radius = half the distance between the side faces)."""
    from build123d import Keep
    if ctx.body is None:
        raise FeatureError("the part has no body")
    c = resolve_face(ctx.body, p["center"])
    s1, s2 = resolve_face(ctx.body, p["side1"]), resolve_face(ctx.body, p["side2"])
    for f in (c, s1, s2):
        if f.geom_type.name.lower() != "plane":
            raise FeatureError("full round fillet needs planar faces")
    n = c.normal_at().normalized()
    m = s1.normal_at().normalized()
    if abs(abs(m.dot(s2.normal_at().normalized())) - 1) > 1e-6 or abs(m.dot(n)) > 1e-6:
        raise FeatureError("side faces must be parallel to each other and perpendicular to the centre face")
    w = abs((s1.center() - s2.center()).dot(m))
    r = w / 2
    u = n.cross(m).normalized()
    cc = c.center()
    mid = s1.center() + (s2.center() - s1.center()) * 0.5
    axis_pt = cc - n * r + m * (mid - cc).dot(m)
    us = [vv.dot(u) for vv in (Vector(x.X, x.Y, x.Z) for x in c.vertices())]
    L = max(us) - min(us)
    start = axis_pt + u * (min(us) - axis_pt.dot(u))
    # remove the square-edged cap (width w, height r) and add the half cylinder
    ydir = n.cross(u)  # the box plane's y axis
    cap = Solid.make_box(L, w, r, Plane(origin=start - ydir * r, x_dir=u, z_dir=n))
    cyl = Solid.make_cylinder(r, L, Plane(origin=start, x_dir=m, z_dir=u))
    half = cyl.split(Plane(origin=axis_pt, x_dir=u, z_dir=n), keep=Keep.TOP)
    body = ctx.body.cut(cap).fuse(half).clean()
    ctx.body = body
    ctx.tools[name] = [("add", half)]
