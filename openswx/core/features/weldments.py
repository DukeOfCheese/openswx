"""Weldment builders: structural members, end caps and gussets."""
from __future__ import annotations

from build123d import Compound, Location, Plane, Vector, extrude

from openswx.core.geom import resolve_face

from ._base import Ctx, FeatureError, _compound, _sketch, _solids, apply_op


def build_end_cap(ctx: Ctx, p: dict, name: str) -> None:
    """Weldments > End Cap: close the open ends of a structural member's tubes/pipes with plates of
    `thickness`, inset into the member end."""
    if ctx.body is None:
        raise FeatureError("no body")
    member = p["member"]
    info = ctx.info.get(member)
    if not info or "cut_list" not in info:
        raise FeatureError(f"{member} is not a structural member")
    t = float(p.get("thickness", 3.0))
    tools = [tl for _, tl in ctx.tools.get(member, [])]
    caps = []
    from build123d import Face as _F
    for tl in tools:
        for f in tl.faces():
            if f.geom_type.name.lower() != "plane" or not f.inner_wires():
                continue  # only hollow-section end faces (annulus / square ring)
            for iw in f.inner_wires():
                nrm = f.normal_at().normalized()
                cap = extrude(_F(iw), amount=t, dir=-nrm)
                caps.append(cap)
    if not caps:
        raise FeatureError("the member has no open (hollow) ends to cap")
    tool = caps[0]
    for c in caps[1:]:
        tool = tool.fuse(c)
    apply_op(ctx, "add", tool)
    ctx.tools[name] = [("add", tool)]
    ctx.info[name] = {"caps": len(caps)}


def build_gusset(ctx: Ctx, p: dict, name: str) -> None:
    """Weldments > Gusset: a triangular plate (legs d1, d2, `thickness`) in the corner between two
    perpendicular planar faces, centred on their common edge (or at `position` 0..1 along it)."""
    if ctx.body is None:
        raise FeatureError("no body")
    f1, f2 = resolve_face(ctx.body, p["face1"]), resolve_face(ctx.body, p["face2"])
    if f1.geom_type.name.lower() != "plane" or f2.geom_type.name.lower() != "plane":
        raise FeatureError("gusset faces must be planar")
    n1, n2 = f1.normal_at().normalized(), f2.normal_at().normalized()
    if abs(n1.dot(n2)) > 1e-6:
        raise FeatureError("gusset faces must be perpendicular")
    common = [e for e in f1.edges() if any(e.is_same(x) for x in f2.edges())]
    if common:
        e = common[0]
    else:  # faces of different members: use the line where their planes meet, clipped to face 1
        raise FeatureError("the two faces must share an edge (an inside corner)")
    pos = float(p.get("position", 0.5))
    c = e.position_at(pos)
    d1, d2, t = float(p.get("d1", 20.0)), float(p.get("d2", 20.0)), float(p.get("thickness", 3.0))
    ax = (e.position_at(1) - e.position_at(0)).normalized()
    from build123d import Edge as _E, Face as _F, Wire as _W
    P = [c, c + n2 * d1, c + n1 * d2]  # along face1 (away from face2 = +n2) and along face2 (+n1)
    tri = _F(_W([_E.make_line(P[0], P[1]), _E.make_line(P[1], P[2]), _E.make_line(P[2], P[0])]))
    tri = tri.moved(Location(ax * (-t / 2)))
    plate = extrude(tri, amount=t, dir=ax)
    apply_op(ctx, "add", plate)
    ctx.tools[name] = [("add", plate)]


def _member_profile(kind: str, w: float, h: float, t: float, plane: Plane):
    """Cross-section face (centred on the plane origin) for weldment structural members."""
    from build123d import Face as _F, Wire as _W
    if kind == "pipe":
        outer = _W.make_circle(w / 2, plane)
        return _F(outer, [_W.make_circle(w / 2 - t, plane)]) if t > 0 else _F(outer)
    rect = lambda a, b: _W.make_rect(a, b, plane)  # noqa: E731
    if kind in ("square_tube", "rect_tube"):
        return _F(rect(w, h), [rect(w - 2 * t, h - 2 * t)])
    if kind == "solid_bar":
        return _F(rect(w, h))
    if kind == "angle":  # L-section, heel at the origin corner
        pts = [(0, 0), (w, 0), (w, t), (t, t), (t, h), (0, h)]
        from build123d import Edge as _E
        P = [plane.from_local_coords((x - w / 2, y - h / 2)) for x, y in pts]
        return _F(_W([_E.make_line(P[i], P[(i + 1) % len(P)]) for i in range(len(P))]))
    raise FeatureError(f"unknown profile {kind!r}; use square_tube, rect_tube, pipe, angle or solid_bar")


def build_structural_member(ctx: Ctx, p: dict, name: str) -> None:
    """Weldments > Structural Member: sweep a standard profile along every straight segment of a sketch.
    corner = "miter" (default): members meeting at a corner are extended and trimmed on the bisector plane
    (SOLIDWORKS end miter); "none": members stop at the sketch points. profile = "sketch" takes the profile
    from `profile_sketch` (its own x, y coordinates: x along the path sketch's normal, the segment's start at
    the origin); `segments` limits the members to those sketch lines; `trim` cuts the members back where they
    run into the bodies already there (members added after others, as at a frame's corners)."""
    from build123d import Keep
    if p["sketch"] in ctx.curves:  # a 3D sketch: its lines (Edge1, Edge2, ...); profiles oriented about Z / X
        sk, plane = None, Plane.XY
    else:
        sk, plane = _sketch(ctx, p["sketch"])
    kind, w = p.get("profile", "square_tube"), float(p.get("width", 20.0))
    h, t = float(p.get("height", w)), float(p.get("thickness", 2.0))
    miter = p.get("corner", "miter") == "miter"
    own = None
    if kind == "sketch":
        psk, _ = _sketch(ctx, p["profile_sketch"])
        own = psk.profiles(Plane.XY)
        if not own:
            raise FeatureError("the profile sketch has no closed profile")
        bb = Compound(own).bounding_box()
        w = h = max(bb.size.X, bb.size.Y, 1e-3) + 2 * max(abs(bb.min.X), abs(bb.max.X), abs(bb.min.Y), abs(bb.max.Y))
    only = set(p.get("segments") or [])
    segs = []
    if sk is None:
        for i, e in enumerate(ctx.curves[p["sketch"]]):
            eid = f"Edge{i + 1}"
            if e.geom_type.name == "LINE" and (not only or eid in only):
                segs.append((eid, e.position_at(0), e.position_at(1)))
    for eid, e in (sk.entities.items() if sk is not None else []):
        if e["type"] != "line" or e.get("construction") or (only and eid not in only):
            continue
        a = plane.from_local_coords(Vector(*sk.coords(e["p1"]), 0))
        b = plane.from_local_coords(Vector(*sk.coords(e["p2"]), 0))
        if (b - a).length > 1e-9:
            segs.append((eid, a, b))
    if not segs:
        raise FeatureError("the sketch has no straight segments for structural members")
    key = lambda v: (round(v.X, 6), round(v.Y, 6), round(v.Z, 6))  # noqa: E731
    at: dict = {}
    for i, (_, a, b) in enumerate(segs):
        at.setdefault(key(a), []).append(i)
        at.setdefault(key(b), []).append(i)
    ext = 2.0 * max(w, h)
    tools, cut_list = [], []
    for i, (eid, a, b) in enumerate(segs):
        d = (b - a).normalized()
        L = (b - a).length
        cut_list.append({"segment": eid, "length": round(L, 6)})
        cuts = []  # (corner point, plane normal pointing away from this member)
        e0 = e1 = 0.0
        for end, P, inward in ((0, a, d), (1, b, -d)):
            others = [j for j in at[key(P)] if j != i]
            if not miter or len(others) != 1:
                continue
            _, oa, ob = segs[others[0]]
            dj = ((ob - oa) if key(oa) == key(P) else (oa - ob)).normalized()  # from P into the other member
            di = -inward  # along this member towards P
            if abs(di.dot(dj)) > 1 - 1e-9:
                continue  # collinear: no miter
            nrm = (di + dj).normalized()  # miter plane bisects the corner (contains inner and outer corner lines)
            cuts.append((P, nrm))
            if end == 0:
                e0 = ext
            else:
                e1 = ext
        sec = Plane(origin=a - d * e0, x_dir=plane.z_dir if abs(d.dot(plane.z_dir)) < 0.99 else plane.x_dir, z_dir=d)
        if own is not None:
            tool = None
            for f in own:
                piece = extrude(f.moved(Location(sec)), amount=L + e0 + e1, dir=d)
                tool = piece if tool is None else tool.fuse(piece)
        else:
            tool = extrude(_member_profile(kind, w, h, t, sec), amount=L + e0 + e1, dir=d)
        for P, nrm in cuts:
            mid = a + d * (L / 2)
            keep = Keep.BOTTOM if (mid - P).dot(nrm) < 0 else Keep.TOP
            tool = tool.split(Plane(origin=P, z_dir=nrm), keep=keep)
        tools.append(tool)
    if p.get("trim") and ctx.body is not None:  # trimmed back where they run into the bodies already there
        tools = [tl.cut(ctx.body) for tl in tools]
        tools = [tl for tl in tools if tl.solids()]
        if not tools:
            raise FeatureError("the members lie entirely inside the existing bodies")
    if p.get("merge", True):
        tool = tools[0]
        for tl in tools[1:]:
            tool = tool.fuse(tl)
        apply_op(ctx, "add", tool, True)
    else:  # one body per member, as SOLIDWORKS keeps them
        tool = _compound([sol for tl in tools for sol in tl.solids()])
        ctx.body = _compound((_solids(ctx.body) if ctx.body is not None else []) + list(tool.solids()))
    ctx.tools[name] = [("add", tool)]
    desc = {"square_tube": f"SQ TUBE {w:g} x {w:g} x {t:g}", "rect_tube": f"RECT TUBE {w:g} x {h:g} x {t:g}",
            "pipe": f"PIPE {w:g} OD x {t:g}", "angle": f"ANGLE {w:g} x {h:g} x {t:g}", "solid_bar": f"BAR {w:g} x {h:g}",
            "sketch": f"PROFILE {p.get('profile_sketch')}"}[kind]
    ctx.info[name] = {"members": len(tools), "profile": kind, "description": desc, "cut_list": cut_list,
                      "corner": "miter" if miter else "none"}
