"""Feature builders that add or remove solid material: sketch, extrude, revolve, sweep, loft, shell, hole and the rest."""
from __future__ import annotations

import math

from build123d import Axis, Compound, Kind, Location, Plane, Shape, Solid, Vector, extrude, revolve
from build123d import offset as b3d_offset

from openswx.core.geom import STD_AXES, resolve_face, vec
from openswx.core.sketch import Sketch

from ._base import Ctx, FeatureError, _compound, _path_wire, _sketch, _solids, _through_length, apply_op, end_condition_name, resolve_axis, resolve_plane


def _ext_alive(body, ref: dict) -> bool:
    """Does the model edge a converted sketch entity came from still exist (same kind, same place)?"""
    from openswx.core.geom import closest_point, geom_name
    for e in body.edges():
        if geom_name(e) != ref.get("type"):
            continue
        if "radius" in ref and abs(e.radius - ref["radius"]) > 1e-4:
            continue
        if closest_point(e, ref["point"])[1] < 1e-3:
            return True
    return False


def build_sketch(ctx: Ctx, p: dict, name: str) -> None:
    plane = resolve_plane(ctx, p.get("plane", "Front"))
    sk = Sketch(p.get("sketch"))
    st = sk.solve()
    # dangling: converted geometry whose model edge has gone (SOLIDWORKS draws it brown)
    ext = [eid for eid, e in sk.entities.items() if e.get("ext")]
    dangling = [eid for eid in ext if ctx.body is None or not _ext_alive(ctx.body, sk.entities[eid]["ext"])]
    if dangling:
        st = sk.status = {**st, "entities": {**st.get("entities", {}), **{d: "dangling" for d in dangling}}, "dangling": dangling}
    p["sketch"] = sk.to_dict()
    ctx.sketches[name] = (sk, plane)
    ctx.info[name] = {"sketch_state": st["state"], "dof": st["dof"], "plane_origin": list(plane.origin),
                      "plane_x": list(plane.x_dir), "plane_z": list(plane.z_dir)}


def _extrude_dir(ctx, p, which, plane, cut):
    n = plane.z_dir
    d = -n if cut else n
    if p.get("reverse") or p.get("flip"):
        d = -d
    return d if which == 1 else -d


def _thin_faces(sk, plane, thin: dict) -> list:
    """Thin feature: each chain of sketch curves (open or closed) made into a wall of `thickness`, on one side
    (side 'one'), the other ('other') or centred on the curves ('mid'). With `fillet` (auto-fillet corners)
    the chain's corners are rounded with that radius first and the wall follows them."""
    from build123d import Face as _F, Kind, Side, Wire
    t = float(thin.get("thickness", 1.0))
    side = thin.get("side", "one")
    if t <= 0:
        raise FeatureError("thin feature thickness must be positive")
    edges = [e for eid, e in sk.to_edges(plane) if not sk.entities.get(eid, {}).get("construction")]
    if not edges:
        raise FeatureError("the sketch has no curves")
    faces = []
    r = float(thin.get("fillet") or 0.0)
    for w in Wire.combine(edges, tol=1e-6):
        if r > 0:
            ends = {(round(v.X, 6), round(v.Y, 6), round(v.Z, 6)) for v in (w @ 0, w @ 1)} if not w.is_closed else set()
            corners = [v for v in w.vertices() if (round(v.X, 6), round(v.Y, 6), round(v.Z, 6)) not in ends]
            if corners:
                try:
                    w = w.fillet_2d(r, corners)
                except Exception as exc:
                    raise FeatureError(f"the corners cannot be rounded with radius {r:g}: {exc}") from exc
        if w.is_closed:
            a, b = (t / 2, -t / 2) if side == "mid" else (t, 0.0) if side == "one" else (0.0, -t)
            outer = w.offset_2d(a, kind=Kind.INTERSECTION) if a else w  # sharp corners, as SOLIDWORKS makes them
            inner = w.offset_2d(b, kind=Kind.INTERSECTION) if b else w
            fo, fi = _F(outer), _F(inner)
            if fo.area < fi.area:
                fo, fi, outer, inner = fi, fo, inner, outer
            faces.append(_F(outer, [inner]))
        else:
            if side == "mid":  # two half walls (Side.BOTH would round the ends)
                faces += [_F(w.offset_2d(t / 2, kind=Kind.INTERSECTION, side=sd, closed=True)) for sd in (Side.LEFT, Side.RIGHT)]
            else:
                sd = Side.LEFT if side == "one" else Side.RIGHT
                faces.append(_F(w.offset_2d(t, kind=Kind.INTERSECTION, side=sd, closed=True)))
    return faces


def build_extrude(ctx: Ctx, p: dict, name: str, cut: bool = False) -> None:
    sk, plane = _sketch(ctx, p["sketch"])
    faces = _thin_faces(sk, plane, p["thin"]) if p.get("thin") else sk.profiles(plane)
    if p.get("start_offset"):
        off = plane.z_dir * float(p["start_offset"])
        faces = [f.moved(Location(off)) for f in faces]
    cond = end_condition_name(p.get("end_condition", "Blind"))
    depth = float(p.get("depth", 10.0))
    draft = float(p.get("draft", 0.0))
    taper = -draft if p.get("draft_outward") else draft
    d1 = _extrude_dir(ctx, p, 1, plane, cut)
    tools = []
    for f in faces:
        if cond == "Blind":
            if depth <= 0:
                raise FeatureError("depth must be positive")
            tools.append(extrude(f, amount=depth, dir=d1, taper=taper))
        elif cond == "MidPlane":
            tools.append(extrude(f, amount=depth / 2, dir=d1, both=True, taper=taper))
        elif cond == "ThroughAll":
            tools.append(extrude(f, amount=_through_length(ctx, plane), dir=d1))
        elif cond == "ThroughAllBoth":
            tools.append(extrude(f, amount=_through_length(ctx, plane), dir=d1, both=True))
        elif cond == "UpToVertex":
            v = vec(p["up_to"])
            d = (v - plane.origin).dot(d1)
            if d <= 1e-9:
                raise FeatureError("the vertex is not in front of the sketch in the extrude direction")
            tools.append(extrude(f, amount=d, dir=d1, taper=taper))
        elif cond in ("UpToSurface", "OffsetFromSurface"):
            tools.append(_extrude_up_to_surface(ctx, f, d1, plane, p, offset=float(p.get("up_to_offset", 0.0))
                                                if cond == "OffsetFromSurface" else 0.0))
        elif cond == "UpToNext":
            if ctx.body is None:
                raise FeatureError("Up To Next needs existing geometry in the extrude direction")
            tools.append(_extrude_up_to_next(ctx, f, d1, plane))
        d2 = p.get("direction2")
        if d2 and cond not in ("MidPlane", "ThroughAllBoth"):
            c2 = end_condition_name(d2.get("end_condition", "Blind"))
            amt = _through_length(ctx, plane) if c2 == "ThroughAll" else float(d2.get("depth", depth))
            t2 = -float(d2.get("draft", 0)) if d2.get("draft_outward") else float(d2.get("draft", 0))
            tools.append(extrude(f, amount=amt, dir=-d1, taper=t2))
    op = "cut" if cut else "add"
    if cut and len(tools) > 1 and not p.get("flip_side_to_cut") and ctx.body is not None:
        # many profiles cut at once with one boolean against a compound of the tools (60 holes: 0.04 s instead
        # of 0.33 s fusing them one by one first); any trouble falls back to the fused tool below
        before = ctx.body
        try:
            tool = Compound(tools)
            apply_op(ctx, "cut", tool, p.get("merge", True))
            if ctx.body is not None and ctx.body.solids():
                ctx.tools[name] = [(op, tool)]
                return
        except Exception:  # noqa: BLE001
            pass
        ctx.body = before
    tool = tools[0]
    for t in tools[1:]:
        tool = tool.fuse(t)
    if cut and p.get("flip_side_to_cut"):
        if ctx.body is None:
            raise FeatureError("nothing to cut")
        keep = ctx.body.intersect(tool)
        ctx.body = _compound(keep.solids())
    else:
        apply_op(ctx, op, tool, p.get("merge", True))
    ctx.tools[name] = [(op, tool)]


def _halfspace_keep(face_plane: Plane, keep_point: Vector, size: float) -> Shape:
    """A large solid box on the keep_point side of a plane (used to trim extrusions at a surface)."""
    n = face_plane.z_dir
    side = 1.0 if (keep_point - face_plane.origin).dot(n) >= 0 else -1.0
    box_plane = Plane(origin=face_plane.origin, x_dir=face_plane.x_dir, z_dir=n * side)
    return Solid.make_box(size, size, size, box_plane.offset(0)).moved(
        Location(box_plane.x_dir * (-size / 2) + box_plane.y_dir * (-size / 2)))


def _extrude_up_to_surface(ctx: Ctx, f, d1: Vector, plane: Plane, p: dict, offset: float = 0.0):
    if ctx.body is None:
        raise FeatureError("Up To Surface needs a face to extrude to")
    face = resolve_face(ctx.body, p["up_to"])
    if face.geom_type.name.lower() != "plane":
        return _extrude_to_curved_face(ctx, f, d1, plane, face, offset)
    n = face.normal_at().normalized()
    q = face.center()
    # offset moves the end face back towards the sketch (SOLIDWORKS "Offset From Surface"); reverse_offset flips it
    sgn = -1.0 if p.get("reverse_offset") else 1.0
    q = q - d1 * (offset * sgn)
    L = _through_length(ctx, plane)
    tool = extrude(f, amount=L, dir=d1)
    keep = _halfspace_keep(Plane(origin=q, x_dir=_any_perp(n), z_dir=n), plane.origin, 4 * L)
    out = tool.intersect(keep)
    solids = [s for item in (out if isinstance(out, (list, tuple)) else [out]) for s in item.solids()]
    if not solids:
        raise FeatureError("the surface is not in front of the sketch in the extrude direction")
    return _compound(solids)


def _extrude_to_curved_face(ctx: Ctx, f, d1: Vector, plane: Plane, face, offset: float = 0.0):
    """Up To Surface on a curved face: split a long extrusion with the face and keep the piece that
    starts at the sketch (the face must span the whole profile, as in SOLIDWORKS)."""
    from build123d import Keep
    if offset:
        raise FeatureError("Offset From Surface supports planar faces only")
    L = _through_length(ctx, plane)
    tool = extrude(f, amount=L, dir=d1)
    parts = tool.split(face, Keep.ALL)
    pieces = [s for p in (parts if isinstance(parts, (list, tuple)) else [parts]) if p is not None for s in p.solids()]
    probe = f.center() + d1 * 1e-3
    for piece in pieces:
        if piece.is_inside(probe):
            if len(pieces) < 2:
                raise FeatureError("the selected face does not cross the whole extrusion")
            return piece
    raise FeatureError("the surface is not in front of the sketch in the extrude direction")


def _extrude_up_to_next(ctx: Ctx, f, d1: Vector, plane: Plane):
    """Extrude until the first body boundary met: keep the extruded segment connected to the sketch that
    ends at the next face (computed with a long tool split by the existing body)."""
    L = _through_length(ctx, plane)
    long_tool = extrude(f, amount=L, dir=d1)
    outside = long_tool.cut(ctx.body)
    inside = long_tool.intersect(ctx.body)
    # the piece that touches the sketch face: material directly in front of the profile
    probe = f.center() + d1 * 1e-3
    for piece in (outside.solids() if outside else []):
        if piece.is_inside(probe):
            return piece
    for piece in (inside.solids() if inside else []):  # sketch sits inside material: up to the next exit face
        if piece.is_inside(probe):
            return piece
    raise FeatureError("nothing found in the extrude direction for Up To Next")


def _any_perp(n: Vector) -> Vector:
    a = Vector(1, 0, 0) if abs(n.X) < 0.9 else Vector(0, 1, 0)
    return n.cross(a).normalized()


def _curve_of(wire):
    """A wire (edges in order, orientation respected) as one B-spline curve."""
    from OCP.BRep import BRep_Tool
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomConvert import GeomConvert, GeomConvert_CompCurveToBSplineCurve
    from OCP.TopAbs import TopAbs_REVERSED
    joined = None
    for e in wire.order_edges():
        b = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(BRep_Tool.Curve_s(e.wrapped, 0, 1), *BRep_Tool.Range_s(e.wrapped)))
        if e.wrapped.Orientation() == TopAbs_REVERSED:
            b.Reverse()
        if joined is None:
            joined = GeomConvert_CompCurveToBSplineCurve(b)
        elif not joined.Add(b, 1e-5):
            raise FeatureError("a loft profile or guide curve is not connected end to end")
    return joined.BSplineCurve()


def _guided_loft(sections, guides) -> Shape:
    """Loft through profile faces following guide curves: each profile is cut where the guides cross it, and a
    curve-network (Gordon) patch spans each stretch between neighbouring guides; the patches and the end
    profiles are sewn into a solid."""
    from build123d import Face, Vertex, Wire
    from ocp_gordon import interpolate_curve_network
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    from OCP.TopoDS import TopoDS
    if len(guides) < 2:
        raise FeatureError("a loft needs two or more guide curves (with one, sweep the profile along it with a guide)")
    wires = [f.outer_wire() for f in sections]
    tol = 1e-3 * max(w.bounding_box().diagonal for w in wires)
    hits = []  # for each guide, its parameter on every profile
    for g in guides:
        row = []
        for w in wires:
            if w.distance_to(g) > tol:
                raise FeatureError("every guide curve must cross every loft profile")
            q = min((g.position_at(t / 200) for t in range(201)), key=lambda q: w.distance_to(Vertex(q)))
            row.append(w.param_at_point(q))
        hits.append(row)
    order = sorted(range(len(guides)), key=lambda k: hits[k][0])  # round the first profile
    guides, hits = [guides[k] for k in order], [hits[k] for k in order]

    def span(w, a, b):  # the profile from parameter a to b going forwards (across its start if need be)
        if b > a + 1e-9:
            return w.trim(a, b)
        parts = ([w.trim(a, 1.0)] if a < 1 - 1e-9 else []) + ([w.trim(0.0, b)] if b > 1e-9 else [])
        return Wire([e for p in parts for e in p.edges()])
    patches = []
    for k in range(len(guides)):
        nxt = (k + 1) % len(guides)
        ucurves = [_curve_of(span(w, hits[k][i], hits[nxt][i])) for i, w in enumerate(wires)]
        srf = interpolate_curve_network(ucurves, [_curve_of(guides[k]), _curve_of(guides[nxt])], tol)
        patches.append(Face(BRepBuilderAPI_MakeFace(srf, 1e-6).Face()))
    for g in guides:  # the side must run along every guide
        if min(f.distance_to(Vertex(g.position_at(0.5))) for f in patches) > 10 * tol:
            raise FeatureError("the loft cannot follow the guides here: add guides at the profiles' corners")
    sew = BRepBuilderAPI_Sewing(tol)
    for f in patches + [sections[0], sections[-1]]:
        sew.Add(f.wrapped)
    sew.Perform()
    shell = sew.SewedShape()
    if shell.ShapeType().name != "TopAbs_SHELL":
        raise FeatureError("the guided loft did not close into a solid")
    solid = Solid(BRepBuilderAPI_MakeSolid(TopoDS.Shell(shell)).Solid())
    if solid.volume < 0:
        solid = Solid(solid.wrapped.Reversed())
    return solid


def _pipe(path, wires) -> Shape:
    """A solid swept along `path` through one or more section wires (in order along the path): OCC's pipe
    shell (Loft with a centerline)."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    ps = BRepOffsetAPI_MakePipeShell(path.wrapped)
    for w in wires:
        ps.Add(w.wrapped, False, False)
    ps.Build()
    if not ps.IsDone():
        raise FeatureError("the sweep could not be built along this path")
    ps.MakeSolid()
    out = Shape.cast(ps.Shape())
    if not out.solids():
        raise FeatureError("the sweep did not close into a solid")
    return out


def _profile_sweep(face, path, p) -> Shape:
    """A sweep with twist and / or a guide curve: a smooth loft through copies of the profile carried along
    the path (parallel-transported frame), turned in proportion to the distance (twist, degrees) and scaled
    so the profile keeps touching the guide (taken at the same fraction of its length)."""
    from build123d import loft
    twist = float(p.get("twist") or 0.0)
    guide = p.get("_guide")
    t0 = path.tangent_at(0).normalized()
    p0 = path.position_at(0)
    n0 = Vector(1, 0, 0) if abs(t0.X) < 0.9 else Vector(0, 1, 0)
    n0 = (n0 - t0 * n0.dot(t0)).normalized()
    start = Location(Plane(origin=p0, x_dir=n0, z_dir=t0)).inverse()

    def reach(u, pos, t):  # the guide's distance from the path, square to the path
        g = guide.position_at(u) - pos
        return (g - t * g.dot(t)).length
    r0 = reach(0, p0, t0) if guide is not None else 1.0
    if guide is not None and r0 < 1e-6:
        raise FeatureError("the guide curve must start on the profile, away from the path")
    count = max(8 if guide is None else 16, int(abs(twist) / 12) + 2)  # sections
    sub = 8  # finer steps for transporting the frame
    nrm, sections = n0, []
    for i in range(count * sub + 1):
        u = i / (count * sub)
        t = path.tangent_at(u).normalized()
        nrm = (nrm - t * nrm.dot(t)).normalized()
        if i % sub:
            continue
        pos = path.position_at(u)
        f = face.moved(Location(Plane(origin=pos, x_dir=nrm, z_dir=t)) * start)
        if twist:
            f = f.rotate(Axis(pos, t), twist * u)
        if guide is not None:
            k = reach(u, pos, t) / r0
            if k < 1e-6:
                raise FeatureError("the guide curve meets the path: the profile would shrink to nothing")
            f = f.moved(Location(-pos)).scale(k).moved(Location(pos))
        sections.append(f)
    return loft(sections)


def build_sweep(ctx: Ctx, p: dict, name: str, cut: bool = False) -> None:
    from build123d import Transition, sweep
    sk, plane = _sketch(ctx, p["sketch"])
    faces = sk.profiles(plane)
    path = _path_wire(ctx, p["path"])
    if p.get("twist") or p.get("guide"):  # twist along the path, or a guide curve the profile follows
        if p.get("guide"):
            p = {**p, "_guide": _path_wire(ctx, p["guide"])}
        tools = [_profile_sweep(f, path, p) for f in faces]
    else:
        tools = [sweep(f, path=path, transition=Transition.RIGHT, is_frenet=bool(p.get("frenet", False))) for f in faces]
    tool = tools[0]
    for t in tools[1:]:
        tool = tool.fuse(t)
    op = "cut" if cut else "add"
    apply_op(ctx, op, tool, p.get("merge", True))
    ctx.tools[name] = [(op, tool)]


def build_loft(ctx: Ctx, p: dict, name: str, cut: bool = False) -> None:
    from build123d import loft
    sections = []
    for sname in p["profiles"]:
        sk, plane = _sketch(ctx, sname)
        faces = sk.profiles(plane)
        if len(faces) != 1:
            raise FeatureError(f"loft profile {sname} must contain exactly one closed contour")
        sections.append(faces[0])
    if len(sections) < 2:
        raise FeatureError("a loft needs at least two profiles")
    if p.get("guides"):  # Guide curves: the loft's side follows them
        tool = _guided_loft(sections, [_path_wire(ctx, g) for g in p["guides"]])
    elif p.get("centerline"):  # Loft with a centerline: the sections are blended along it
        tool = _pipe(_path_wire(ctx, p["centerline"]), [f.outer_wire() for f in sections])
    else:
        tool = loft(sections, ruled=bool(p.get("ruled", False)))
    op = "cut" if cut else "add"
    apply_op(ctx, op, tool, p.get("merge", True))
    ctx.tools[name] = [(op, tool)]


def build_draft(ctx: Ctx, p: dict, name: str) -> None:
    """Neutral-plane draft: tilt the selected faces by `angle` about their intersection with the neutral plane."""
    from build123d import draft
    if ctx.body is None:
        raise FeatureError("the part has no body")
    neutral = resolve_plane(ctx, p["neutral"])
    faces = [resolve_face(ctx.body, r) for r in p.get("faces", [])]
    if not faces:
        raise FeatureError("select faces to draft")
    angle = float(p.get("angle", 1.0)) * (-1 if p.get("reverse") else 1)
    out = []
    for s in _solids(ctx.body):
        mine = [f for f in faces if any(f.is_same(sf) for sf in s.faces())]
        if not mine:
            out.append(s)
            continue
        try:
            res = s.draft(mine, neutral, angle) if hasattr(s, "draft") else draft(mine, neutral, angle)
        except Exception as exc:
            raise FeatureError(f"draft failed: {exc}") from exc
        out += list(res.solids())
    ctx.body = _compound(out)


def build_shell(ctx: Ctx, p: dict, name: str) -> None:
    if ctx.body is None:
        raise FeatureError("the part has no body")
    t = float(p.get("thickness", 1.0))
    faces = [resolve_face(ctx.body, r) for r in p.get("faces", [])]
    out = []
    for s in _solids(ctx.body):
        mine = [f for f in faces if any(f.is_same(sf) for sf in s.faces())]
        amount = t if p.get("outward") else -t
        # sharp (intersection) offsets keep square inner corners, as SOLIDWORKS shell does;
        # fall back to rounded offsets where the kernel cannot intersect the offset faces
        res, last = None, None
        for kind in (Kind.INTERSECTION, Kind.ARC):
            try:
                res = b3d_offset(s, amount=amount, openings=mine or None, kind=kind)
                break
            except Exception as exc:  # OCCT offset failures
                last = exc
        if res is None and not p.get("outward"):
            try:
                res = _shell_by_subtraction(s, t, mine)
            except Exception as exc:
                last = exc
        if res is None:
            raise FeatureError(f"shell failed (thickness too large for the geometry?): {last}")
        out += list(res.solids())
    ctx.body = _compound(out)


def _shell_by_subtraction(solid: Solid, t: float, openings: list) -> Shape:
    """Fallback shell: hollow the solid with a closed inward offset, then open the removed faces by
    cutting the wall region behind each (planar) opening face."""
    inner = None
    for kind in (Kind.INTERSECTION, Kind.ARC):
        try:
            inner = b3d_offset(solid, amount=-t, kind=kind)
            break
        except Exception:
            continue
    if inner is None or not inner.solids():
        raise FeatureError("could not offset the body inward")
    hollow = solid.cut(inner)
    for f in openings:
        if f.geom_type.name.lower() != "plane":
            raise FeatureError("fallback shell supports planar faces to remove only")
        hollow = hollow.cut(extrude(f, amount=t * 1.001, dir=-f.normal_at()))
    return hollow.clean()


def build_hole(ctx: Ctx, p: dict, name: str) -> None:
    if ctx.body is None:
        raise FeatureError("the part has no body")
    plane = resolve_plane(ctx, p["face"])
    dia = float(p.get("diameter", 5.0))
    cond = end_condition_name(p.get("end_condition", "Blind"))
    depth = _through_length(ctx, plane) if cond in ("ThroughAll", "ThroughAllBoth") else float(p.get("depth", 10.0))
    htype = p.get("type", "simple")
    tools = []
    for u, v in p.get("points", [[0, 0]]):
        o = plane.from_local_coords(Vector(float(u), float(v), 0))
        down = Plane(origin=o, x_dir=plane.x_dir, z_dir=-plane.z_dir)
        tool = Solid.make_cylinder(dia / 2, depth, down)
        if htype == "counterbore":
            tool = tool.fuse(Solid.make_cylinder(float(p["cbore_diameter"]) / 2, float(p["cbore_depth"]), down))
        elif htype == "countersink":
            cd = float(p["csk_diameter"])
            ang = float(p.get("csk_angle", 90.0))
            h = (cd - dia) / 2 / math.tan(math.radians(ang / 2))
            tool = tool.fuse(Solid.make_cone(cd / 2, dia / 2, h, down))
        if cond == "Blind" and p.get("drill_point", False):
            tip = dia / 2 / math.tan(math.radians(59))
            tip_plane = Plane(origin=o - plane.z_dir * depth, x_dir=plane.x_dir, z_dir=-plane.z_dir)
            tool = tool.fuse(Solid.make_cone(dia / 2, 0, tip, tip_plane))
        tools.append(tool)
    tool = tools[0]
    for t in tools[1:]:
        tool = tool.fuse(t)
    apply_op(ctx, "cut", tool)
    ctx.tools[name] = [("cut", tool)]


ISO_HOLES = {
    "M2": (2.4, 1.6, (4.4, 2.3), 4.4), "M2.5": (2.9, 2.05, (5.5, 2.8), 5.5), "M3": (3.4, 2.5, (6.5, 3.3), 6.5),
    "M4": (4.5, 3.3, (8.0, 4.4), 8.4), "M5": (5.5, 4.2, (10.0, 5.4), 10.4), "M6": (6.6, 5.0, (11.0, 6.4), 12.4),
    "M8": (9.0, 6.8, (15.0, 8.6), 16.4), "M10": (11.0, 8.5, (18.0, 10.6), 20.4), "M12": (13.5, 10.2, (20.0, 12.6), 24.4),
    "M16": (17.5, 14.0, (26.0, 16.6), 32.4), "M20": (22.0, 17.5, (33.0, 20.6), 40.4), "M24": (26.0, 21.0, (40.0, 24.8), 48.4),
}


_IN = 25.4


ANSI_HOLES = {k: (c * _IN, t * _IN, (cb * _IN, cd * _IN), cs * _IN) for k, (c, t, (cb, cd), cs) in {
    "#4-40": (0.1285, 0.089, (0.219, 0.112), 0.225), "#6-32": (0.1495, 0.1065, (0.250, 0.138), 0.279),
    "#8-32": (0.177, 0.136, (0.312, 0.164), 0.332), "#10-24": (0.201, 0.1495, (0.375, 0.190), 0.385),
    "1/4-20": (0.266, 0.201, (0.438, 0.250), 0.507), "5/16-18": (0.332, 0.257, (0.531, 0.312), 0.635),
    "3/8-16": (0.397, 0.3125, (0.625, 0.375), 0.762), "1/2-13": (0.531, 0.4219, (0.812, 0.500), 1.016)}.items()}


def hole_wizard_params(size: str, hole_type: str) -> dict:
    """Translate a standard hole (ISO metric or ANSI inch size + type) into Hole feature parameters."""
    table = ISO_HOLES if size in ISO_HOLES else ANSI_HOLES if size in ANSI_HOLES else None
    if table is None:
        raise FeatureError(f"unknown size {size!r}; ISO: {', '.join(ISO_HOLES)}; ANSI: {', '.join(ANSI_HOLES)}")
    clr, tap, (cb_d, cb_h), csk = table[size]
    if hole_type == "countersink" and table is ANSI_HOLES:
        return {"type": "countersink", "diameter": clr, "csk_diameter": csk, "csk_angle": 82.0}
    if hole_type == "clearance":
        return {"type": "simple", "diameter": clr}
    if hole_type == "tapped":
        return {"type": "simple", "diameter": tap, "thread": size}
    if hole_type == "counterbore":
        return {"type": "counterbore", "diameter": clr, "cbore_diameter": cb_d, "cbore_depth": cb_h}
    if hole_type == "countersink":
        return {"type": "countersink", "diameter": clr, "csk_diameter": csk, "csk_angle": 90.0}
    raise FeatureError("hole_type must be clearance, tapped, counterbore or countersink")


def build_dome(ctx: Ctx, p: dict, name: str) -> None:
    """Dome: a spherical cap of height `height` on a circular planar face (outward; reverse = dent)."""
    if ctx.body is None:
        raise FeatureError("the part has no body")
    face = resolve_face(ctx.body, p["face"])
    circ = [e for e in face.edges() if e.geom_type.name.lower() == "circle"]
    if face.geom_type.name.lower() != "plane" or len(circ) != 1:
        raise FeatureError("dome needs a circular planar face")
    a, c, n = circ[0].radius, circ[0].arc_center, face.normal_at().normalized()
    h = float(p.get("height", a / 2))
    if h <= 0 or h > a:
        raise FeatureError("dome height must be in (0, face radius]")
    R = (a * a + h * h) / (2 * h)
    sign = -1.0 if p.get("reverse") else 1.0
    sphere = Solid.make_sphere(R).moved(Location(c + n * (sign * (h - R))))
    slab = extrude(face, amount=h * 1.0001, dir=n * sign)
    cap = sphere.intersect(slab)
    solids = [s for it in (cap if isinstance(cap, (list, tuple)) else [cap]) for s in it.solids()]
    tool = _compound(solids)
    apply_op(ctx, "cut" if p.get("reverse") else "add", tool)
    ctx.tools[name] = [("cut" if p.get("reverse") else "add", tool)]


def build_rib(ctx: Ctx, p: dict, name: str) -> None:
    """Rib: each sketch line is thickened (symmetric about the sketch plane) and extended within the sketch
    plane until it meets the existing material, like SOLIDWORKS' parallel-to-sketch rib."""
    if ctx.body is None:
        raise FeatureError("a rib needs existing material to attach to")
    sk, plane = _sketch(ctx, p["sketch"])
    t = float(p.get("thickness", 3.0))
    L = _through_length(ctx, plane)
    tools = []
    from build123d import Face as _F, Wire as _W, Edge as _E
    for eid, e in sk.entities.items():
        if e["type"] != "line" or e.get("construction"):
            continue
        a = plane.from_local_coords(Vector(*sk.coords(e["p1"]), 0))
        b = plane.from_local_coords(Vector(*sk.coords(e["p2"]), 0))
        d = (b - a).normalized()
        perp = plane.z_dir.cross(d).normalized()  # in-plane direction perpendicular to the line
        best = None
        for side in ((-1, 1) if p.get("flip") else (1, -1)):
            off = perp * (side * L)
            face = _F(_W([_E.make_line(a, b), _E.make_line(b, b + off), _E.make_line(b + off, a + off), _E.make_line(a + off, a)]))
            slab = extrude(face, amount=t / 2, dir=plane.z_dir, both=True)
            rest = slab.cut(ctx.body)
            probe = (a + b) * 0.5 + perp * (side * 1e-3)
            for piece in rest.solids():
                if piece.is_inside(probe):
                    far = (a + b) * 0.5 + off * 0.999
                    if not piece.is_inside(far):  # bounded by material: a valid rib
                        best = piece
                    break
            if best is not None:
                break
        if best is None:
            raise FeatureError(f"rib line {eid} does not close against the part (try flip)")
        tools.append(best)
    if not tools:
        raise FeatureError("the rib sketch needs at least one line")
    tool = tools[0]
    for tl in tools[1:]:
        tool = tool.fuse(tl)
    apply_op(ctx, "add", tool)
    ctx.tools[name] = [("add", tool)]


def build_thread(ctx: Ctx, p: dict, name: str) -> None:
    """Modelled ISO 60° thread on a cylindrical face: a triangular profile swept along a helix and cut
    (external) or added (internal fill of a tapped hole is modelled as a cut into the wall)."""
    from build123d import Edge as _E, Face as _F, Helix, Wire as _W, sweep
    if ctx.body is None:
        raise FeatureError("the part has no body")
    face = resolve_face(ctx.body, p["face"])
    circ = [e for e in face.edges() if e.geom_type.name.lower() == "circle"]
    if face.geom_type.name.lower() != "cylinder" or not circ:
        raise FeatureError("select a cylindrical face for the thread")
    c0, axis_d, r = circ[0].arc_center, circ[0].normal().normalized(), circ[0].radius
    other = [e for e in circ if (e.arc_center - c0).length > 1e-6]
    if other and (other[0].arc_center - c0).dot(axis_d) < 0:
        axis_d = -axis_d
    pitch, length = float(p.get("pitch", 1.0)), float(p.get("length", 10.0))
    H = 0.8660254 * pitch
    depth = 0.6134 * pitch  # ISO external thread depth
    internal = bool(p.get("internal", False))
    plane = Plane(origin=c0, z_dir=axis_d)
    helix = Helix(pitch=pitch, height=length, radius=r, center=plane.origin, direction=axis_d)
    start = helix.position_at(0)
    radial = (start - c0 - axis_d * (start - c0).dot(axis_d)).normalized()
    sgn = 1.0 if internal else -1.0  # external threads cut inward from the surface
    tip = start + radial * (sgn * depth)
    half = pitch * 0.5 * 0.9
    base_a, base_b = start - axis_d * half + radial * (-sgn * 0.05), start + axis_d * half + radial * (-sgn * 0.05)
    prof = _F(_W([_E.make_line(base_a, tip), _E.make_line(tip, base_b), _E.make_line(base_b, base_a)]))
    tool = sweep(prof, path=helix, is_frenet=True)
    apply_op(ctx, "cut", tool)
    ctx.tools[name] = [("cut", tool)]
    ctx.info[name] = {"thread": f"M{2 * r:g}x{pitch:g}", "H": H}


def build_body_op(ctx: Ctx, p: dict, name: str) -> None:
    """Body operations: combine (add/subtract/common), delete_body, move_copy (translate/rotate, copy), scale."""
    if ctx.body is None:
        raise FeatureError("the part has no body")
    bodies = list(ctx.body.solids())
    op = p["op"]

    def pick(i):
        if not 0 <= i < len(bodies):
            raise FeatureError(f"no body {i}; the part has {len(bodies)} bodies")
        return bodies[i]
    if op == "delete_body":
        keep = [b for i, b in enumerate(bodies) if i not in set(p["bodies"])]
        ctx.body = _compound(keep)
    elif op == "combine":
        main = pick(int(p["main"]))
        tools = [pick(int(i)) for i in p["tools"]]
        res = main
        for tl in tools:
            res = res.fuse(tl) if p["mode"] == "add" else res.cut(tl) if p["mode"] == "subtract" else res.intersect(tl)
        rest = [b for i, b in enumerate(bodies) if i != int(p["main"]) and i not in {int(x) for x in p["tools"]}]
        out = res if not isinstance(res, (list, tuple)) else _compound([s for r_ in res for s in r_.solids()])
        ctx.body = _compound(list(out.solids()) + rest)
    elif op == "move_copy":
        idx = [int(i) for i in p["bodies"]]
        moved = []
        for i in idx:
            b = pick(i)
            if p.get("angle"):
                b = b.rotate(Axis(vec(p.get("axis_origin", (0, 0, 0))), vec(p.get("axis", (0, 0, 1)))), float(p["angle"]))
            b = b.moved(Location(vec(p.get("translate", (0, 0, 0)))))
            moved.append(b)
        keep = bodies if p.get("copy") else [b for i, b in enumerate(bodies) if i not in set(idx)]
        ctx.body = _compound(keep + moved)
    elif op == "scale":
        k = float(p["factor"])
        if k <= 0:
            raise FeatureError("scale factor must be positive")
        c = vec(p.get("about", (0, 0, 0)))  # fixed point: the origin (default) or e.g. the centroid
        ctx.body = _compound(s.moved(Location(-c)).scale(k).moved(Location(c)) for s in bodies)
    else:
        raise FeatureError(f"unknown body operation {op!r}")


def build_revolve(ctx: Ctx, p: dict, name: str, cut: bool = False) -> None:
    sk, plane = _sketch(ctx, p["sketch"])
    axis_spec = p.get("axis")
    if axis_spec is None:
        cl = [eid for eid, e in sk.entities.items() if e["type"] == "line" and e.get("construction")]
        if not cl:
            raise FeatureError("select an axis: the sketch has no centerline")
        axis_spec = f"{p['sketch']}/{cl[0]}"
    elif isinstance(axis_spec, str) and "/" not in axis_spec and axis_spec.upper() not in STD_AXES:
        axis_spec = f"{p['sketch']}/{axis_spec}"
    axis = resolve_axis(ctx, axis_spec)
    faces = sk.profiles(plane)
    angle = float(p.get("angle", 360.0))
    rtype = p.get("type", "OneDirection")
    sign = -1.0 if p.get("reverse") else 1.0
    tools = []
    for f in faces:
        if rtype == "MidPlane":
            f0 = f.rotate(axis, -sign * angle / 2)
            tools.append(revolve(f0, axis, sign * angle))
        else:
            tools.append(revolve(f, axis, sign * angle))
            if rtype == "TwoDirection":
                tools.append(revolve(f, axis, -sign * float(p.get("angle2", angle))))
    tool = tools[0]
    for t in tools[1:]:
        tool = tool.fuse(t)
    op = "cut" if cut else "add"
    apply_op(ctx, op, tool, p.get("merge", True))
    ctx.tools[name] = [(op, tool)]


def build_wrap(ctx: Ctx, p: dict, name: str) -> None:
    """Wrap: wrap a sketch's closed profiles around a cylindrical face (sketch x -> arc length around the
    axis, sketch y -> along the axis) and emboss (raise) or deboss (cut) them by `depth`, or scribe (split only
    is approximated by a 0.01 mm deboss)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.BRepLib import BRepLib
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.Geom2d import Geom2d_Line, Geom2d_TrimmedCurve
    from OCP.Geom2dAPI import Geom2dAPI_Interpolate
    from OCP.gp import gp_Dir2d
    from OCP.collections import HArray1_gp_Pnt2d as TColgp_HArray1OfPnt2d
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Pnt2d
    from build123d import Face as _F
    if ctx.body is None:
        raise FeatureError("the part has no body")
    cyl = resolve_face(ctx.body, p["face"])
    if cyl.geom_type.name.lower() != "cylinder":
        raise FeatureError("wrap needs a cylindrical face")
    circ = next((e for e in cyl.edges() if e.geom_type.name.lower() == "circle"), None)
    if circ is None:
        raise FeatureError("cannot find the cylinder axis")
    R, C, ax = circ.radius, circ.arc_center, circ.normal().normalized()
    sk, plane = _sketch(ctx, p["sketch"])
    # cylinder frame: angle 0 towards the sketch origin's projection, v along the axis
    o = plane.origin
    radial = (o - C) - ax * (o - C).dot(ax)
    if radial.length < 1e-9:
        radial = plane.x_dir - ax * plane.x_dir.dot(ax)
    xd = radial.normalized()
    v0 = (o - C).dot(ax)
    surf = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(C.X, C.Y, C.Z), gp_Dir(ax.X, ax.Y, ax.Z), gp_Dir(xd.X, xd.Y, xd.Z)), R)
    sgn = 1.0 if plane.x_dir.dot(ax.cross(xd)) >= 0 else -1.0  # sketch +x runs the positive angle direction?
    yup = 1.0 if plane.y_dir.dot(ax) >= 0 else -1.0
    uv = lambda x, y: gp_Pnt2d(sgn * x / R, v0 + yup * y)  # noqa: E731
    faces = []
    for prof in sk.profiles(plane):
        wires = [prof.outer_wire()] + list(prof.inner_wires())
        occ_wires = []
        for w in wires:
            mk = BRepBuilderAPI_MakeWire()
            for e in w.edges():
                loc = [plane.to_local_coords(e.position_at(t)) for t in ((0, 1) if e.geom_type.name.lower() == "line" else
                                                                       [k / 24 for k in range(25)])]
                if len(loc) == 2:
                    a2, b2 = uv(loc[0].X, loc[0].Y), uv(loc[1].X, loc[1].Y)
                    ln = math.hypot(b2.X() - a2.X(), b2.Y() - a2.Y())
                    seg = Geom2d_TrimmedCurve(Geom2d_Line(a2, gp_Dir2d(b2.X() - a2.X(), b2.Y() - a2.Y())), 0.0, ln)
                    edge = BRepBuilderAPI_MakeEdge(seg, surf).Edge()
                else:
                    arr = TColgp_HArray1OfPnt2d(1, len(loc))
                    for k, q in enumerate(loc, 1):
                        arr.SetValue(k, uv(q.X, q.Y))
                    it = Geom2dAPI_Interpolate(arr, False, 1e-9)
                    it.Perform()
                    edge = BRepBuilderAPI_MakeEdge(it.Curve(), surf).Edge()
                mk.Add(edge)
            if not mk.IsDone():
                raise FeatureError("could not wrap the sketch profile")
            occ_wires.append(mk.Wire())
        mf = BRepBuilderAPI_MakeFace(surf, occ_wires[0], True)
        for iw in occ_wires[1:]:
            mf.Add(iw)
        f = mf.Face()
        BRepLib.BuildCurves3d_s(f)
        faces.append(_F(f))
    if not faces:
        raise FeatureError("the wrap sketch has no closed profiles")
    kind = p.get("type", "emboss")
    depth = float(p.get("depth", 1.0)) if kind != "scribe" else 0.01
    tools = []
    for f in faces:
        outward = f.normal_at().dot(f.center() - C - ax * (f.center() - C).dot(ax)) > 0
        t = depth if (kind == "emboss") == outward else -depth
        tools.append(Solid.thicken(f, t))
    tool = tools[0]
    for t in tools[1:]:
        tool = tool.fuse(t)
    apply_op(ctx, "add" if kind == "emboss" else "cut", tool)
    ctx.tools[name] = [("add" if kind == "emboss" else "cut", tool)]
