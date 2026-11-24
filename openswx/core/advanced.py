"""Advanced part features: Flex / Deform / Freeform, conic and curvature-continuous fillet profiles, mold tools
(parting line, parting surface, shut-off surfaces, tooling split), Insert Part / Mirror Part, cosmetic threads and
the remaining surface tools (ruled, radiate, extend, untrim, flatten). The builders follow features.py: each takes
(ctx, params, feature name) and changes the rebuild context; BUILDERS / NAME_PREFIX there pick them up."""

from __future__ import annotations

import math
import os

from build123d import Compound, Edge, Face, Location, Plane, Shape, Solid, Vector, Wire

from openswx.core.features import Ctx, FeatureError, _add_surface, _compound, _material, _valid, apply_op, resolve_plane
from openswx.core.geom import face_normal, geom_name, resolve_edge, resolve_face, vec

AXES = {"X": Vector(1, 0, 0), "Y": Vector(0, 1, 0), "Z": Vector(0, 0, 1)}


def _dir(spec) -> Vector:
    if isinstance(spec, str):
        key = spec.strip().upper().lstrip("+")
        neg = key.startswith("-")
        v = AXES.get(key.lstrip("-"))
        if v is None:
            raise FeatureError(f"unknown direction {spec!r}: X, Y, Z or a vector")
        return -v if neg else v
    v = vec(spec)
    if v.length < 1e-12:
        raise FeatureError("the direction is a zero vector")
    return v.normalized()


def map_shape(shape: Shape, fn, n: int = 20) -> Shape:
    """Deform a shape by moving the control points of its NURBS geometry through fn(x, y, z) -> (x, y, z).
    The geometry is converted to NURBS, refined (degree >= 3, at least n poles per direction) so a nonlinear
    map is followed closely, then edited in place: the parameter curves of the edges stay attached to their
    faces (a copied surface would detach them). Affine maps are exact; others converge with n."""
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert
    from OCP.BRepLib import BRepLib
    from OCP.gp import gp_Pnt
    from OCP.ShapeFix import ShapeFix_Shape
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    whole = Shape.cast(BRepBuilderAPI_NurbsConvert(shape.wrapped, True).Shape())

    def moved(p, tr):
        w = p.Transformed(tr)
        return gp_Pnt(*fn(w.X(), w.Y(), w.Z())).Transformed(tr.Inverted())

    for f in whole.faces():
        loc = TopLoc_Location()
        s = BRep_Tool.Surface_s(TopoDS.Face(f.wrapped), loc)
        if not hasattr(s, "SetPole"):
            raise FeatureError("a face could not be converted to NURBS")
        if s.UDegree() < 3 or s.VDegree() < 3:
            s.IncreaseDegree(max(3, s.UDegree()), max(3, s.VDegree()))
        for along_u in (True, False):
            k = s.NbUKnots() if along_u else s.NbVKnots()
            lo, hi = (s.UKnot(1), s.UKnot(k)) if along_u else (s.VKnot(1), s.VKnot(k))
            if (s.NbUPoles() if along_u else s.NbVPoles()) < n:
                for i in range(1, n):
                    (s.InsertUKnot if along_u else s.InsertVKnot)(lo + (hi - lo) * i / n, 1, 1e-9, True)
        tr = loc.Transformation()
        for i in range(1, s.NbUPoles() + 1):
            for j in range(1, s.NbVPoles() + 1):
                s.SetPole(i, j, moved(s.Pole(i, j), tr))
    for e in whole.edges():
        loc = TopLoc_Location()
        c = BRep_Tool.Curve_s(TopoDS.Edge(e.wrapped), loc, 0.0, 0.0)
        if c is None or not hasattr(c, "SetPole"):
            continue
        if c.Degree() < 3:
            c.IncreaseDegree(3)
        lo, hi = c.FirstParameter(), c.LastParameter()
        if c.NbPoles() < n:
            for i in range(1, n):
                c.InsertKnot(lo + (hi - lo) * i / n, 1, 1e-9, True)
        tr = loc.Transformation()
        for i in range(1, c.NbPoles() + 1):
            c.SetPole(i, moved(c.Pole(i), tr))
    b = BRep_Builder()
    for v in whole.vertices():
        vt = TopoDS.Vertex(v.wrapped)
        p = BRep_Tool.Pnt_s(vt)
        b.UpdateVertex(vt, gp_Pnt(*fn(p.X(), p.Y(), p.Z())), BRep_Tool.Tolerance_s(vt))
    BRepLib.SameParameter_s(whole.wrapped, 1e-4, True)
    fix = ShapeFix_Shape(whole.wrapped)
    fix.Perform()
    return Shape.cast(fix.Shape())


def _set_body(ctx: Ctx, shape: Shape, what: str) -> None:
    solids = [s for s in shape.solids() if s.is_valid]
    if not solids:
        raise FeatureError(f"{what} gave no valid solid (try smaller values)")
    ctx.body = _compound(_material(solids))


def build_flex(ctx: Ctx, p: dict, name: str) -> None:
    """Flex: bend, twist, taper or stretch the body along an axis (X / Y / Z or a vector) between two trim
    positions measured along it (default: the whole body). Bend: angle (deg) toward bend_direction; twist:
    angle (deg); taper: factor at the far end (1 = none); stretch: distance (mm)."""
    if ctx.body is None:
        raise FeatureError("no body to flex")
    kind = p.get("type", "bend")
    a = _dir(p.get("axis", "X"))
    bdir = p.get("bend_direction")
    b = _dir(bdir) if bdir is not None else (Vector(0, 1, 0) if abs(a.Y) < 0.9 else Vector(0, 0, 1))
    b = (b - a * b.dot(a)).normalized()
    c = a.cross(b)
    bb = ctx.body.bounding_box()
    corners = [Vector(x, y, z) for x in (bb.min.X, bb.max.X) for y in (bb.min.Y, bb.max.Y) for z in (bb.min.Z, bb.max.Z)]
    ts = [q.dot(a) for q in corners]
    t0 = float(p["start"]) if p.get("start") is not None else min(ts)
    t1 = float(p["end"]) if p.get("end") is not None else max(ts)
    if t1 - t0 < 1e-6:
        raise FeatureError("the trim positions must enclose part of the body")
    L, mid = t1 - t0, (bb.min + bb.max) * 0.5
    hb, hc = mid.dot(b), mid.dot(c)

    def split(x, y, z):
        q = Vector(x, y, z)
        return q.dot(a), q.dot(b) - hb, q.dot(c) - hc

    def join(t, h, w):
        q = a * t + b * (h + hb) + c * (w + hc)
        return (q.X, q.Y, q.Z)

    if kind == "twist":
        ang = math.radians(float(p.get("angle", 90.0)))

        def fn(x, y, z):
            t, h, w = split(x, y, z)
            th = ang * min(max((t - t0) / L, 0.0), 1.0)
            return join(t, h * math.cos(th) - w * math.sin(th), h * math.sin(th) + w * math.cos(th))
    elif kind == "taper":
        k = float(p.get("factor", 0.5))
        if k <= 0:
            raise FeatureError("the taper factor must be positive")

        def fn(x, y, z):
            t, h, w = split(x, y, z)
            s = 1 + (k - 1) * min(max((t - t0) / L, 0.0), 1.0)
            return join(t, h * s, w * s)
    elif kind == "stretch":
        dist = float(p.get("distance", 10.0))

        def fn(x, y, z):
            t, h, w = split(x, y, z)
            return join(t + dist * min(max((t - t0) / L, 0.0), 1.0), h, w)
    elif kind == "bend":
        phi = math.radians(float(p.get("angle", 90.0)))
        if abs(phi) < 1e-9:
            raise FeatureError("give a bend angle")
        R = L / phi  # the neutral axis (through the body centre) keeps its length

        def fn(x, y, z):
            t, h, w = split(x, y, z)
            if t <= t0:
                return join(t, h, w)
            s = min(t - t0, L)
            ang = s / R
            nt, nh = t0 + (R - h) * math.sin(ang), R - (R - h) * math.cos(ang)
            extra = t - t1
            if extra > 0:  # beyond the bend: straight on along the bent direction
                nt, nh = nt + extra * math.cos(phi), nh + extra * math.sin(phi)
            return join(nt, nh, w)
    else:
        raise FeatureError(f"unknown flex type {kind!r}: bend, twist, taper or stretch")
    _set_body(ctx, map_shape(ctx.body, fn, int(p.get("resolution", 20))), "flex")
    ctx.info[name] = {"type": kind, "start": t0, "end": t1}


def _falloff(points: list[dict], radius: float):
    pts = [(vec(q["point"]), vec(q["move"])) for q in points]
    r2 = radius * radius

    def fn(x, y, z):
        q = Vector(x, y, z)
        out = q
        for c, m in pts:
            d2 = (q - c).length ** 2
            if d2 < r2:
                w = (1 - d2 / r2) ** 2  # smooth: zero slope at the edge of the region
                out = out + m * w
        return (out.X, out.Y, out.Z)
    return fn


def build_deform(ctx: Ctx, p: dict, name: str) -> None:
    """Deform (point): push the body at points ([{"point": [x, y, z], "move": [dx, dy, dz]}]) with a smooth
    fall-off to zero at `radius` from each point."""
    if ctx.body is None:
        raise FeatureError("no body to deform")
    pts = p.get("points") or []
    if not pts:
        raise FeatureError("give at least one point and its move")
    r = float(p.get("radius", 20.0))
    if r <= 0:
        raise FeatureError("the deform radius must be positive")
    _set_body(ctx, map_shape(ctx.body, _falloff(pts, r), int(p.get("resolution", 20))), "deform")


def build_freeform(ctx: Ctx, p: dict, name: str) -> None:
    """Freeform: pull a face's interior along its normal by `distance` (positive outward); the push falls off
    smoothly to the face's surroundings (radius: half the face's size unless given)."""
    if ctx.body is None:
        raise FeatureError("no body to edit")
    face = resolve_face(ctx.body, p["face"])
    from openswx.core.geom import interior_point
    c = interior_point(face)
    n = face_normal(face, c)
    bb = face.bounding_box()
    r = float(p.get("radius") or max(bb.size.X, bb.size.Y, bb.size.Z) * 0.5)
    pts = [{"point": list(c), "move": list(n * float(p.get("distance", 2.0)))}]
    _set_body(ctx, map_shape(ctx.body, _falloff(pts, r), int(p.get("resolution", 20))), "freeform")


def _faces_of(body: Shape, edge: Edge) -> list[Face]:
    return [f for f in body.faces() if any(edge.is_same(e) for e in f.edges())]


def build_profile_fillet(ctx: Ctx, p: dict, name: str) -> None:
    """Fillet with a conic (rho) or curvature-continuous profile on straight edges between planar faces.
    The profile meets each face `radius` from the edge; a conic with rho 0.5 is a parabola (circular is about
    0.414), curvature continuous is a quintic that leaves both faces with zero curvature (G2)."""
    if ctx.body is None:
        raise FeatureError("no body to fillet")
    r = float(p.get("radius", 5.0))
    profile = p.get("profile", "conic")
    rho = float(p.get("rho", 0.5))
    if not 0.05 <= rho <= 0.95:
        raise FeatureError("rho must be between 0.05 and 0.95")
    tools_cut, tools_add = [], []
    for ref in p.get("edges", []):
        e = resolve_edge(ctx.body, ref)
        if geom_name(e) != "line":
            raise FeatureError("conic / curvature-continuous fillets need straight edges")
        fs = _faces_of(ctx.body, e)
        if len(fs) != 2 or any(geom_name(f) != "plane" for f in fs):
            raise FeatureError("conic / curvature-continuous fillets need an edge between two planar faces")
        A, B = e.position_at(0), e.position_at(1)
        d = (B - A).normalized()
        legs = []
        for f in fs:
            t = d.cross(f.normal_at()).normalized()
            mid = A + (B - A) * 0.5
            if f.distance_to(mid + t * min(r, 1e-2)) > 1e-6:  # the leg must run into the face
                t = -t
            legs.append(t)
        P1, P2, C = A + legs[0] * r, A + legs[1] * r, A
        if profile == "conic":  # a rational quadratic: weight rho / (1 - rho) on the corner
            curve = Edge.make_bezier(P1, C, P2, weights=[1.0, rho / (1 - rho), 1.0])
        elif profile in ("curvature_continuous", "g2"):  # three collinear poles at each end: zero end curvature
            curve = Edge.make_bezier(P1, P1 + (C - P1) * 0.4, P1 + (C - P1) * 0.75, P2 + (C - P2) * 0.75,
                                     P2 + (C - P2) * 0.4, P2)
        else:
            raise FeatureError(f"unknown fillet profile {profile!r}: conic or curvature_continuous")
        wire = Wire([curve, Edge.make_line(P2, C), Edge.make_line(C, P1)])
        prism = Solid.extrude(Face(wire), B - A)
        probe = C + (legs[0] + legs[1]) * (r * 0.05)
        (tools_cut if ctx.body.is_inside(probe) else tools_add).append(prism)
    if not tools_cut and not tools_add:
        raise FeatureError("select straight edges to fillet")
    for t in tools_cut:
        apply_op(ctx, "cut", t)
    for t in tools_add:
        apply_op(ctx, "add", t)
    ctx.tools[name] = [("cut", t) for t in tools_cut] + [("add", t) for t in tools_add]


def _parting_edges(body: Shape, d: Vector) -> list[Edge]:
    """Edges where the part turns from faces that see the pull direction (and walls along it) to faces
    that face away: the parting line for a two-plate mold."""
    faces = body.faces()
    side = [1 if face_normal(f, f.center()).dot(d) > -1e-6 else -1 for f in faces]
    out = []
    for e in body.edges():
        adj = [i for i, f in enumerate(faces) if any(e.is_same(x) for x in f.edges())]
        if len(adj) == 2 and side[adj[0]] != side[adj[1]]:
            out.append(e)
    return out


def _parting(ctx: Ctx, p: dict) -> tuple[Vector, float, list[Edge]]:
    if ctx.body is None:
        raise FeatureError("no part body for mold tools")
    d = _dir(p.get("direction", "Y"))
    edges = _parting_edges(ctx.body, d)
    if p.get("level") is not None:
        return d, float(p["level"]), edges
    if not edges:
        raise FeatureError("no parting line found for that pull direction")
    hs = [e.position_at(t).dot(d) for e in edges for t in (0.0, 0.5, 1.0)]
    if max(hs) - min(hs) > 1e-4:
        raise FeatureError("the parting line is not planar for this pull direction: give the parting `level`")
    return d, sum(hs) / len(hs), edges


def build_parting_line(ctx: Ctx, p: dict, name: str) -> None:
    """Mold Tools > Parting Line: the edges where the part divides between the two mold halves for the pull
    direction (shown as a curve)."""
    d, level, edges = _parting(ctx, p)
    if not edges:
        raise FeatureError("no parting line found for that pull direction")
    ctx.curves[name] = edges
    ctx.info[name] = {"direction": list(d), "level": level, "edges": len(edges), "length": sum(e.length for e in edges)}


def _section_faces(body: Shape, d: Vector, level: float) -> list[Face]:
    """The part's cross-section at the parting level as faces (largest first)."""
    from build123d import Keep
    try:
        half = body.split(Plane(d * level, z_dir=d), Keep.TOP)
    except Exception:
        return []
    half = Compound(half) if isinstance(half, (list, tuple)) else half
    faces = []
    for f in half.faces():
        if geom_name(f) == "plane" and abs(abs(f.normal_at().dot(d)) - 1) < 1e-9 and abs(f.center().dot(d) - level) < 1e-6:
            faces.append(f)
    return sorted(faces, key=lambda f: -f.area)


def build_parting_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Mold Tools > Parting Surface: a flat surface on the parting plane from the part's outline out to
    `margin` beyond it."""
    d, level, _ = _parting(ctx, p)
    margin = float(p.get("margin", 10.0))
    bb = ctx.body.bounding_box()
    size = max(bb.size.X, bb.size.Y, bb.size.Z) + 2 * margin
    c = (bb.min + bb.max) * 0.5
    c = c - d * (c.dot(d) - level)
    outer = Face.make_rect(size, size, Plane(c, z_dir=d))
    sec = _section_faces(ctx.body, d, level)
    surf = outer
    for f in sec:
        surf = surf.cut(Face(f.outer_wire()))
    _add_surface(ctx, name, surf)


def build_shut_off_surfaces(ctx: Ctx, p: dict, name: str) -> None:
    """Mold Tools > Shut-off Surfaces: patches closing the openings that run through the part along the pull,
    so core and cavity seal against each other (the holes in the part's section at the parting level)."""
    d, level, _ = _parting(ctx, p)
    holes = [Face(w) for f in _section_faces(ctx.body, d, level) for w in f.inner_wires()]
    if not holes:
        raise FeatureError("the part has no openings to shut off at the parting level")
    _add_surface(ctx, name, holes[0] if len(holes) == 1 else Compound(holes))


def build_tooling_split(ctx: Ctx, p: dict, name: str) -> None:
    """Mold Tools > Tooling Split: a mold block `margin` around the part, split at the parting level into the
    cavity (the side the pull points to) and the core, each minus the part. Bodies: part (unless keep_part is
    false), core, cavity. Openings through the part are shut off at the parting plane."""
    from build123d import Box, Keep
    d, level, _ = _parting(ctx, p)
    margin = float(p.get("margin", 10.0))
    part = ctx.body
    bb = part.bounding_box()
    lo, hi = bb.min - Vector(margin, margin, margin), bb.max + Vector(margin, margin, margin)
    size = hi - lo
    block = Box(size.X, size.Y, size.Z).moved(Location((lo + hi) * 0.5))
    pl = Plane(d * level, z_dir=d)
    one = lambda x: Compound(list(x)) if isinstance(x, (list, tuple)) else x  # noqa: E731
    cavity = _valid(one(block.split(pl, Keep.TOP)).cut(part))
    core = _valid(one(block.split(pl, Keep.BOTTOM)).cut(part))
    if not cavity.solids() or not core.solids():
        raise FeatureError("the parting plane does not cut the mold block")
    bodies = (list(part.solids()) if p.get("keep_part", True) else []) + core.solids() + cavity.solids()
    ctx.body = _compound(bodies)
    ctx.info[name] = {"level": level, "direction": list(d), "core_volume": core.volume, "cavity_volume": cavity.volume}


_PART_CACHE: dict = {}


def _load_part_body(path: str):
    if not os.path.exists(path):
        raise FeatureError(f"part file not found: {path}")
    key = (os.path.abspath(path), os.path.getmtime(path))
    if key in _PART_CACHE:
        return _PART_CACHE[key]
    ext = os.path.splitext(path)[1].lower()
    if ext == ".oswprt":
        from openswx.core.document import PartDocument
        body = PartDocument.open(path).body
    elif ext in (".step", ".stp"):
        from build123d import import_step
        body = import_step(path)
    elif ext in (".x_t", ".x_b"):
        from openswx.exchange.parasolid import read_xt_shape
        body = read_xt_shape(path)
    elif ext == ".sldprt":
        from openswx.exchange.sldprt import read_sldprt
        body = read_sldprt(path)
    else:
        raise FeatureError(f"cannot insert a {ext} file as a part")
    if body is None or not body.solids():
        raise FeatureError(f"{os.path.basename(path)} has no solid body")
    _PART_CACHE[key] = body
    return body


def build_insert_part(ctx: Ctx, p: dict, name: str) -> None:
    """Insert Part (a derived part): another part file's body, placed by position / rotation (degrees about
    X, Y, Z) and optionally mirrored about a plane (Mirror Part); it follows changes to that file on rebuild."""
    shape = _load_part_body(p["path"])
    if p.get("mirror"):
        shape = shape.mirror(resolve_plane(ctx, p["mirror"]))
    rx, ry, rz = (float(v) for v in (p.get("rotation") or [0, 0, 0]))
    shape = shape.moved(Location(tuple(float(v) for v in (p.get("position") or [0, 0, 0])), (rx, ry, rz)))
    apply_op(ctx, "cut" if p.get("op") == "cut" else "add", Compound(shape.solids()), p.get("merge", True))
    ctx.info[name] = {"source": p["path"], "volume": shape.volume}


def build_cosmetic_thread(ctx: Ctx, p: dict, name: str) -> None:
    """Cosmetic Thread on a cylindrical face (hole or shaft): records the thread (label, diameters, length) for
    drawings and shows the thread diameter as circles at its ends; the geometry is not changed."""
    if ctx.body is None:
        raise FeatureError("no body for a cosmetic thread")
    face = resolve_face(ctx.body, p["face"])
    if geom_name(face) != "cylinder":
        raise FeatureError("a cosmetic thread goes on a cylindrical face")
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
    ax = cyl.Axis()
    o = Vector(ax.Location().X(), ax.Location().Y(), ax.Location().Z())
    a = Vector(ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z())
    r = cyl.Radius()
    from openswx.core.geom import interior_point
    q = interior_point(face)
    radial = (q - (o + a * (q - o).dot(a))).normalized()
    internal = not ctx.body.is_inside(q - radial * min(r * 0.5, 0.5))  # (a hole: just inside the face is air)
    ts = [(v - o).dot(a) for e in face.edges() for v in (e.position_at(0), e.position_at(1))]
    t0, t1 = min(ts), max(ts)
    length = float(p.get("length") or (t1 - t0))
    label = p.get("label") or (f"M{round(2 * r / 0.85):g}" if internal else f"M{2 * r:g}")
    thread_r = r / 0.85 if internal else r * 0.85  # internal: the major diameter; external: the minor
    ctx.curves[name] = [Edge.make_circle(thread_r, Plane(o + a * t, z_dir=a)) for t in (t0, t0 + length)]
    ctx.info[name] = {"cosmetic_thread": {"label": label, "internal": internal, "diameter": 2 * r,
                                          "thread_diameter": 2 * thread_r, "length": length,
                                          "axis_origin": list(o + a * t0), "axis": list(a)}}


def _ruled_from(edges, dir_fn, distance):
    from OCP.BRepFill import BRepFill
    faces = []
    for e in edges:
        n = 2 if geom_name(e) == "line" else 24
        pts = [e.position_at(i / (n - 1)) for i in range(n)]
        moved = [q + dir_fn(e, i / (n - 1)) * distance for i, q in enumerate(pts)]
        e2 = Edge.make_line(moved[0], moved[-1]) if n == 2 else Edge.make_spline(moved)
        faces.append(Face(BRepFill.Face_s(e.wrapped, e2.wrapped)))
    return faces[0] if len(faces) == 1 else Compound(faces)


def _model_edges(ctx: Ctx, refs):
    if ctx.body is None:
        raise FeatureError("no body to take edges from")
    es = [resolve_edge(ctx.body, r) for r in refs or []]
    if not es:
        raise FeatureError("select edges")
    return es


def build_ruled_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Ruled Surface from model edges: `type` direction (along a vector), normal (along the
    adjacent face's normal) or tangent (continuing the adjacent face outward), `distance` mm."""
    es = _model_edges(ctx, p.get("edges"))
    kind = p.get("type", "normal")
    body = ctx.body
    if kind == "direction":
        dv = _dir(p.get("direction", "Y"))
        fn = lambda e, t: dv  # noqa: E731
    else:
        def fn(e, t):
            f = _faces_of(body, e)[0]
            q = e.position_at(t)
            n = face_normal(f, q)
            if kind == "normal":
                return n
            out = e.tangent_at(t).cross(n).normalized()
            return out if f.distance_to(q - out * 1e-3) < 1e-6 else -out  # away from the face
    _add_surface(ctx, name, _ruled_from(es, fn, float(p.get("distance", 10.0))))


def build_radiate_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Radiate Surface: from model edges outward in a plane parallel to the reference `plane`,
    perpendicular to the edges, `distance` mm."""
    es = _model_edges(ctx, p.get("edges"))
    n = resolve_plane(ctx, p.get("plane", "Top")).z_dir
    body = ctx.body

    def fn(e, t):
        q = e.position_at(t)
        out = e.tangent_at(t).cross(n).normalized()
        return out if not body.is_inside(q + out * 1e-2) else -out
    _add_surface(ctx, name, _ruled_from(es, fn, float(p.get("distance", 10.0))))


def _surface_source(ctx: Ctx, p: dict):
    if p.get("surface"):
        s = ctx.surfaces.get(p["surface"])
        if s is None:
            raise FeatureError(f"no surface body named {p['surface']!r}")
        return s.faces()
    if ctx.body is None:
        raise FeatureError("no faces to work on")
    fs = [resolve_face(ctx.body, f) for f in p.get("faces", [])]
    if not fs:
        raise FeatureError("give a surface body or faces")
    return fs


def _uv_bounds(face):
    from OCP.BRepTools import BRepTools
    return BRepTools.UVBounds_s(face.wrapped)


def _make_face(surf, u0, u1, v0, v1):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    mk = BRepBuilderAPI_MakeFace(surf, u0, u1, v0, v1, 1e-6)
    if not mk.IsDone():
        raise FeatureError("the face could not be rebuilt on its surface")
    return Face(mk.Face())


def build_extend_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Extend Surface: grow faces by `distance` mm past their edges on the same underlying surface
    (B-spline surfaces are extended smoothly). The result is a new surface body."""
    from OCP.BRep import BRep_Tool
    from OCP.GeomConvert import GeomConvert
    from OCP.GeomLib import GeomLib
    dist = float(p.get("distance", 10.0))
    out = []
    for f in _surface_source(ctx, p):
        surf = BRep_Tool.Surface_s(f.wrapped)
        u0, u1, v0, v1 = _uv_bounds(f)
        kind = geom_name(f)
        if kind in ("plane", "cylinder", "cone"):
            um, vm = (u0 + u1) / 2, (v0 + v1) / 2
            lu = surf.Value(u0, vm).Distance(surf.Value(u1, vm)) or 1.0
            lv = surf.Value(um, v0).Distance(surf.Value(um, v1)) or 1.0
            du, dv = (u1 - u0) * dist / lu, (v1 - v0) * dist / lv
            if kind != "plane":  # u is an angle there: never wrap past a full turn
                du = 0.0 if u1 - u0 > 2 * math.pi - 1e-6 else min(du, (2 * math.pi - (u1 - u0)) / 2)
            out.append(_make_face(surf, u0 - du, u1 + du, v0 - dv, v1 + dv))
        else:
            from OCP.Geom import Geom_RectangularTrimmedSurface
            bs = GeomConvert.SurfaceToBSplineSurface_s(Geom_RectangularTrimmedSurface(surf, u0, u1, v0, v1))
            for in_u in (True, False):
                for after in (True, False):
                    GeomLib.ExtendSurfByLength_s(bs, dist, 1, in_u, after)
            ub0, ub1, vb0, vb1 = bs.Bounds()
            out.append(_make_face(bs, ub0, ub1, vb0, vb1))
    _add_surface(ctx, name, out[0] if len(out) == 1 else Compound(out))


def build_untrim_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Untrim Surface: `mode` internal fills the holes of each face; natural returns the face to the
    natural bounds of its surface (its parameter range; infinite surfaces keep the face's own range)."""
    from OCP.BRep import BRep_Tool
    mode = p.get("mode", "internal")
    out = []
    for f in _surface_source(ctx, p):
        if mode == "internal":
            out.append(Face(f.outer_wire()))
            continue
        surf = BRep_Tool.Surface_s(f.wrapped)
        u0, u1, v0, v1 = surf.Bounds()
        fu0, fu1, fv0, fv1 = _uv_bounds(f)
        big = 1e50
        out.append(_make_face(surf, u0 if abs(u0) < big else fu0, u1 if abs(u1) < big else fu1,
                              v0 if abs(v0) < big else fv0, v1 if abs(v1) < big else fv1))
    _add_surface(ctx, name, out[0] if len(out) == 1 else Compound(out))


def build_flatten_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Flatten: unroll developable faces (planar, cylindrical, conical) into the plane `plane`
    (default Top) at true size: the flat face keeps the face's area."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    pl = resolve_plane(ctx, p.get("plane", "Top"))
    out = []
    for f in _surface_source(ctx, p):
        kind = geom_name(f)
        ad = BRepAdaptor_Surface(f.wrapped)
        if kind == "plane":
            flat = lambda u, v: (u, v)  # noqa: E731
        elif kind == "cylinder":
            R = ad.Cylinder().Radius()
            flat = lambda u, v, R=R: (u * R, v)  # noqa: E731
        elif kind == "cone":
            cone = ad.Cone()
            sa = cone.SemiAngle()
            Lr = cone.RefRadius() / math.sin(sa) if abs(math.sin(sa)) > 1e-12 else 1e9  # slant length to the apex

            def flat(u, v, Lr=Lr, sa=sa):
                rad, ang = Lr + v, u * math.sin(sa)
                return (rad * math.cos(ang), rad * math.sin(ang))
        else:
            raise FeatureError(f"{kind} faces are not developable: flatten works on planar, cylindrical and conical faces")
        wires = []
        for w in [f.outer_wire(), *f.inner_wires()]:
            pts = [pl.from_local_coords((*flat(u, v), 0)) for u, v in _wire_uv(f, w)]
            wires.append(Wire.make_polygon(_dedupe(pts), close=True))
        out.append(Face(wires[0], wires[1:]))
    _add_surface(ctx, name, out[0] if len(out) == 1 else Compound(out))


def _wire_uv(face, wire, k: int = 32):
    """The wire's points in the face's (u, v) parameters, in order, from the edges' own parameter curves (so
    seams of closed surfaces come out right, which projecting points cannot do)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve2d
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.TopAbs import TopAbs_REVERSED
    out = []
    ex = BRepTools_WireExplorer(wire.wrapped, face.wrapped)
    while ex.More():
        e = ex.Current()
        c2 = BRepAdaptor_Curve2d(e, face.wrapped)
        a, b = c2.FirstParameter(), c2.LastParameter()
        n = 2 if c2.GetType().name == "GeomAbs_Line" else k
        ts = [a + (b - a) * i / (n - 1) for i in range(n)]
        if e.Orientation() == TopAbs_REVERSED:
            ts.reverse()
        for t in ts:
            q = c2.Value(t)
            out.append((q.X(), q.Y()))
        ex.Next()
    return out


def _dedupe(pts):
    out = []
    for q in pts:
        if not out or (q - out[-1]).length > 1e-6:
            out.append(q)
    if len(out) > 1 and (out[0] - out[-1]).length < 1e-6:
        out.pop()
    return out


def build_filled_surface_edges(ctx: Ctx, p: dict, name: str) -> None:
    """Filled Surface from model edges (a hole or gap in the body): `continuity` contact or tangent (G1 to
    the faces next to each edge), optionally pulled through constraint points."""
    from OCP.BRepFill import BRepFill_Filling
    from OCP.GeomAbs import GeomAbs_C0, GeomAbs_G1
    from OCP.gp import gp_Pnt
    es = _model_edges(ctx, p.get("edges"))
    tangent = p.get("continuity", "tangent") == "tangent"
    fill = BRepFill_Filling()
    for e in es:
        fs = _faces_of(ctx.body, e)
        if tangent and fs:
            fill.Add(e.wrapped, fs[0].wrapped, GeomAbs_G1, True)
        else:
            fill.Add(e.wrapped, GeomAbs_C0, True)
    for q in p.get("points") or []:
        fill.Add(gp_Pnt(*[float(v) for v in q]))
    fill.Build()
    if not fill.IsDone():
        raise FeatureError("the fill failed: the edges must form one closed loop")
    _add_surface(ctx, name, Face(fill.Face()))


def build_connection_point(ctx: Ctx, p: dict, name: str) -> None:
    """Connection point (for magnetic mates): a point and the direction a mating part comes in from."""
    pt, d = vec(p["point"]), _dir(p.get("direction", "Y"))
    ctx.points[name] = pt
    ctx.info[name] = {"connection_point": {"point": list(pt), "normal": list(d)}}


def build_mate_reference(ctx: Ctx, p: dict, name: str) -> None:
    """Mate Reference: a named face of this part and the mate it takes; inserted into an assembly next to a
    part with a mate reference of the same name, the two are mated automatically."""
    if ctx.body is None:
        raise FeatureError("no body for a mate reference")
    face = resolve_face(ctx.body, p["face"])
    ctx.info[name] = {"mate_reference": {"name": p.get("reference") or name, "type": p.get("type", "coincident"),
                                         "face": p["face"], "aligned": bool(p.get("aligned")), "kind": geom_name(face)}}


BUILDERS = {
    "connection_point": build_connection_point, "mate_reference": build_mate_reference,
    "flex": build_flex, "deform": build_deform, "freeform": build_freeform, "profile_fillet": build_profile_fillet,
    "parting_line": build_parting_line, "parting_surface": build_parting_surface,
    "shut_off_surfaces": build_shut_off_surfaces, "tooling_split": build_tooling_split,
    "insert_part": build_insert_part, "cosmetic_thread": build_cosmetic_thread,
    "ruled_surface": build_ruled_surface, "radiate_surface": build_radiate_surface,
    "extend_surface": build_extend_surface, "untrim_surface": build_untrim_surface,
    "flatten_surface": build_flatten_surface,
}
NAME_PREFIX = {
    "connection_point": "Connection Point", "mate_reference": "MateReference",
    "flex": "Flex", "deform": "Deform", "freeform": "Freeform", "profile_fillet": "Fillet",
    "parting_line": "Parting Line", "parting_surface": "Parting Surface", "shut_off_surfaces": "Shut-Off Surface",
    "tooling_split": "Tooling Split", "insert_part": "Insert Part", "cosmetic_thread": "Cosmetic Thread",
    "ruled_surface": "Ruled Surface", "radiate_surface": "Radiate Surface", "extend_surface": "Surface-Extend",
    "untrim_surface": "Surface-Untrim", "flatten_surface": "Surface-Flatten",
}
