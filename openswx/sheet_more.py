"""Sheet metal: Rip, Vent, Unfold and Fold.

Rip opens a gap along the corner edges of a hollow part (the first step of converting a shelled box to sheet
metal). Vent cuts an opening through the sheet from a sketched outline, leaving straight ribs across it.
"""
from __future__ import annotations

import math

from build123d import Face, Plane, Vector, Wire, extrude

from .features import Ctx, FeatureError, _sketch, apply_op
from .geom import resolve_edge

NAME_PREFIX = {"rip": "Rip", "vent": "Vent", "unfold": "Unfold", "fold": "Fold"}


def _wall_depth(body, start: Vector, d: Vector, reach: float) -> float:
    """Length of material met going from `start` along `d` (up to `reach`)."""
    from build123d import Edge
    try:
        hit = body.intersect(Edge.make_line(start, start + d * reach))
    except Exception:  # noqa: BLE001 - no intersection
        return 0.0
    if hit is None:
        return 0.0
    edges = hit.edges() if hasattr(hit, "edges") else []
    return sum(e.length for e in edges)


def build_rip(ctx: Ctx, p: dict, name: str) -> None:
    """Rip: along each straight corner edge (where two planar walls meet at the outside), cut a slot `gap` mm
    wide through the wall thickness, centred on the plane that halves the corner. The walls come apart there,
    so the part can be unfolded."""
    if ctx.body is None:
        raise FeatureError("rip needs a body")
    gap = float(p.get("gap", 0.1))
    if gap <= 0:
        raise FeatureError("the gap must be positive")
    edges = p.get("edges") or []
    if not edges:
        raise FeatureError("pick at least one edge to rip")
    tools = []
    for ref in edges:
        edge = resolve_edge(ctx.body, ref)
        if edge.geom_type.name.lower() != "line":
            raise FeatureError("rips need straight edges")
        adj = [f for f in ctx.body.faces() if any(edge.is_same(x) for x in f.edges())]
        if len(adj) != 2 or any(f.geom_type.name.lower() != "plane" for f in adj):
            raise FeatureError("pick an edge where two flat walls meet")
        a0, a1 = edge.position_at(0), edge.position_at(1)
        u, length = (a1 - a0).normalized(), (a1 - a0).length
        mid = a0 + u * (length / 2)
        n1, n2 = (f.normal_at(f.center()).normalized() for f in adj)
        b = n1 + n2
        if b.length < 1e-6:
            raise FeatureError("the walls at this edge are flat (180 degrees): nothing to rip")
        b = b.normalized()  # out of the corner
        across = (n1 - n2).normalized()
        reach = ctx.body.bounding_box().diagonal
        depth = _wall_depth(ctx.body, mid + b * 1e-6, -b, reach)  # material between the outer and inner corner
        if depth <= 1e-9:
            raise FeatureError("the edge is not an outside corner of the body")
        inward = 1.5 * depth + gap
        # the slot's cross-section at the edge's start: gap wide across the corner, from just outside it to
        # past the inner corner; swept along the edge
        q = [a0 + across * s * (gap / 2) + b * h for s, h in ((-1, gap), (1, gap), (1, -inward), (-1, -inward))]
        section = Face(Wire.make_polygon(q, close=True))
        tools.append(extrude(section, amount=length, dir=u))
    for tool in tools:
        apply_op(ctx, "cut", tool)
    ctx.tools[name] = [("cut", t) for t in tools]


def build_vent(ctx: Ctx, p: dict, name: str) -> None:
    """Vent: the closed outline(s) in `sketch` (drawn on the sheet face) cut through the sheet, leaving
    `ribs` straight ribs `rib_width` mm wide across the opening at `rib_angle` degrees (sketch x axis = 0),
    `rib_spacing` mm apart centre to centre (default: spread evenly over the opening)."""
    if ctx.body is None:
        raise FeatureError("vents need a body")
    sk, plane = _sketch(ctx, p["sketch"])
    faces = sk.profiles(plane)
    if not faces:
        raise FeatureError("the vent sketch needs a closed outline")
    t = float((ctx.sheet or {}).get("thickness") or p.get("depth") or 0)
    if t <= 0:
        raise FeatureError("vents go through sheet metal (insert a Base Flange first) or need a depth")
    n_ribs, w = int(p.get("ribs", 0)), float(p.get("rib_width", 2.0))
    ang = math.radians(float(p.get("rib_angle", 0.0)))
    if n_ribs < 0 or (n_ribs and w <= 0):
        raise FeatureError("rib count must be >= 0 and the rib width positive")
    region = faces[0]
    for f in faces[1:]:
        region = region.fuse(f)
    if n_ribs:
        along = plane.x_dir * math.cos(ang) + plane.y_dir * math.sin(ang)
        across = plane.z_dir.cross(along).normalized()
        bb = region.bounding_box()
        c = region.center()
        corners = [Vector(x, y, z) for x in (bb.min.X, bb.max.X) for y in (bb.min.Y, bb.max.Y) for z in (bb.min.Z, bb.max.Z)]
        span = max(abs((q - c).dot(across)) for q in corners) * 2
        reach = max(abs((q - c).dot(along)) for q in corners) * 2 + 2 * w
        spacing = float(p.get("rib_spacing") or span / (n_ribs + 1))
        for k in range(n_ribs):
            off = (k - (n_ribs - 1) / 2) * spacing
            o = c + across * off
            strip = Face.make_rect(reach, w, Plane(origin=o, x_dir=along, z_dir=plane.z_dir))
            region = region.cut(strip)
    tools = [extrude(f, amount=t * 1.01 + 0.01, dir=plane.z_dir, both=True) for f in region.faces()]
    if not tools:
        raise FeatureError("the ribs cover the whole opening")
    for tool in tools:
        apply_op(ctx, "cut", tool)
    ctx.tools[name] = [("cut", x) for x in tools]


def _base(key: str) -> str:
    return key.split("#")[0]  # (a jog keeps its two bends as "Jog1" and "Jog1#2")


def build_unfold(ctx: Ctx, p: dict, name: str) -> None:
    """Unfold: the listed bends (features before this one) are laid flat; features after it work on the flat
    sheet until a Fold bends them back. The geometry is done by the bends themselves (the rebuild builds them
    flat); this feature checks the list."""
    if ctx.sheet is None:
        raise FeatureError("insert a Base Flange first")
    bends = p.get("bends") or []
    if not bends:
        raise FeatureError("pick the bends to unfold")
    if ctx.flat:
        return
    pend = {_base(k) for k in ctx.sheet.get("unfolded", {})}
    for b in bends:
        if b not in pend:
            raise FeatureError(f"{b} is not a bend (edge flange, hem, sketched bend or jog) before this Unfold")
    ctx.info[name] = {"bends": list(bends)}


def build_fold(ctx: Ctx, p: dict, name: str) -> None:
    """Fold: bend back the listed unfolded bends (default: all of them), in the order they were made. Holes
    and notches cut across a bend zone while it was flat are wrapped onto the bend."""
    from .features import _bend
    if ctx.sheet is None:
        raise FeatureError("insert a Base Flange first")
    if ctx.flat:
        return  # the flat pattern stays flat
    pend = dict(ctx.sheet.get("unfolded", {}))
    want = p.get("bends") or sorted({_base(k) for k in pend}, key=lambda b: [_base(k) for k in pend].index(b))
    keys = [k for k in pend if _base(k) in want]
    missing = [b for b in want if b not in {_base(k) for k in keys}]
    if missing or not keys:
        raise FeatureError(f"nothing unfolded to fold: {', '.join(missing) or 'no bends'} (add an Unfold before this Fold)")
    arcs = []
    for k in keys:
        e = pend.pop(k)
        a, u, n, m = (Vector(*e[x]) for x in ("a", "u", "n", "m"))
        BA, arc, pt, vv = _bend(ctx, a, u, n, m, e["phi"], e["r"], e["up"])
        arcs.append(arc)
        for j, o in list(pend.items()):  # bends on the side that moved move with it
            if (Vector(*o["a"]) - a).dot(m) > BA / 2:
                pend[j] = {**o, "a": list(pt(Vector(*o["a"]))), "u": list(vv(Vector(*o["u"]))),
                           "n": list(vv(Vector(*o["n"]))), "m": list(vv(Vector(*o["m"])))}
    ctx.sheet["unfolded"] = pend
    ctx.tools[name] = [("add", x) for x in arcs]
    ctx.info[name] = {"bends": sorted({_base(k) for k in keys}, key=lambda b: [_base(k) for k in keys].index(b))}


BUILDERS = {"rip": build_rip, "vent": build_vent, "unfold": build_unfold, "fold": build_fold}
