"""Feature builders. Each builder consumes a rebuild context and updates the body.

Features that add or remove material record their tool bodies in ctx.tools so that pattern and
mirror features can re-apply them (SOLIDWORKS "pattern features" semantics).
"""
from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass, field

from build123d import Axis, Compound, Kind, Location, Plane, Shape, Solid, Vector, extrude, import_brep, revolve
from build123d import export_brep, offset as b3d_offset
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from .geom import STD_AXES, STD_PLANES, plane_for_normal, resolve_edge, resolve_face, vec
from .sketch import Sketch

END_CONDITIONS = {  # swEndConditions_e values for interop
    "Blind": 0, "ThroughAll": 1, "ThroughNext": 2, "UpToVertex": 3, "UpToSurface": 4,
    "OffsetFromSurface": 5, "MidPlane": 6, "UpToBody": 7, "ThroughAllBoth": 9,
    "UpToSelection": 10, "UpToNext": 11,
}
SUPPORTED_END_CONDITIONS = {"Blind", "ThroughAll", "MidPlane", "ThroughAllBoth", "UpToVertex", "UpToSurface",
                            "OffsetFromSurface", "UpToNext"}

NAME_PREFIX = {
    "sketch": "Sketch", "extrude": "Boss-Extrude", "cut_extrude": "Cut-Extrude", "revolve": "Revolve",
    "cut_revolve": "Cut-Revolve", "fillet": "Fillet", "chamfer": "Chamfer", "shell": "Shell", "hole": "Hole",
    "linear_pattern": "LPattern", "circular_pattern": "CirPattern", "mirror": "Mirror", "plane": "Plane",
    "import": "Imported", "sweep": "Sweep", "cut_sweep": "Cut-Sweep", "loft": "Loft", "cut_loft": "Cut-Loft",
    "draft": "Draft", "base_flange": "Base-Flange", "edge_flange": "Edge-Flange",
    "extruded_surface": "Surface-Extrude", "thicken": "Thicken", "structural_member": "Structural Member",
    "rib": "Rib", "thread": "Thread", "body_op": "Body", "dome": "Dome", "variable_fillet": "VarFillet",
    "sketch_pattern": "Sketch-Pattern", "curve_pattern": "Curve-Pattern",
    "table_pattern": "Table-Pattern",
    "ref_axis": "Axis", "ref_point": "Point", "csys": "Coordinate System",
    "sketch3d": "3DSketch", "helix": "Helix/Spiral",
    "hem": "Hem", "sketched_bend": "Sketched Bend", "jog": "Jog",
    "corner_relief": "Corner Relief", "dimple": "Dimple", "miter_flange": "Miter Flange",
    "louver": "Louver", "lance": "Lance", "extruded_flange": "Extruded Flange", "form_rib": "Rib", "auto_corner_relief": "Corner Relief", "vertex_chamfer": "Chamfer", "face_fillet": "Fillet",
    "full_round_fillet": "Fillet", "wrap": "Wrap", "revolved_surface": "Surface-Revolve", "swept_surface": "Surface-Sweep",
    "lofted_surface": "Surface-Loft", "boundary_surface": "Boundary-Surface", "planar_surface": "Surface-Plane", "filled_surface": "Surface-Fill",
    "offset_surface": "Surface-Offset", "knit_surface": "Surface-Knit", "trim_surface": "Surface-Trim",
    "end_cap": "End cap", "gusset": "Gusset", "fill_pattern": "Fill-Pattern",
    "split": "Split", "cut_with_surface": "SurfaceCut", "delete_face": "DeleteFace", "move_face": "Move Face", "replace_face": "Replace Face", "split_line": "Split Line", "intersect": "Intersect", "indent": "Indent",
    "projected_curve": "Curve", "composite_curve": "CompCurve", "xyz_curve": "Curve",
}


class FeatureError(ValueError):
    pass


@dataclass
class Ctx:
    flat: bool = False  # sheet metal: rebuild as the flat pattern
    surfaces: dict = field(default_factory=dict)  # surface bodies by feature name
    sheet: dict | None = None  # sheet metal parameters set by the base flange
    body: Shape | None = None
    planes: dict = field(default_factory=lambda: dict(STD_PLANES))
    sketches: dict = field(default_factory=dict)  # name -> (Sketch, Plane)
    tools: dict = field(default_factory=dict)  # feature name -> list[(op, Shape)]
    info: dict = field(default_factory=dict)  # feature name -> extra status
    axes: dict = field(default_factory=dict)  # reference axis name -> Axis
    points: dict = field(default_factory=dict)  # reference point name -> Vector
    csys: dict = field(default_factory=dict)  # coordinate system name -> Plane (x_dir, z_dir, origin)
    curves: dict = field(default_factory=dict)  # 3D sketch / helix name -> list[Edge]


# ---------------------------------------------------------------------- reference resolution
def end_condition_name(v) -> str:
    if isinstance(v, int):
        for k, n in END_CONDITIONS.items():
            if n == v:
                v = k
                break
    v = str(v).replace(" ", "").replace("-", "")
    for k in END_CONDITIONS:
        if k.lower() == v.lower() or ("swEndCond" + k).lower() == v.lower():
            if k not in SUPPORTED_END_CONDITIONS:
                raise FeatureError(f"end condition {k} is not supported yet; use one of {sorted(SUPPORTED_END_CONDITIONS)}")
            return k
    raise FeatureError(f"unknown end condition {v!r}")


def resolve_plane(ctx: Ctx, spec) -> Plane:
    if isinstance(spec, str):
        key = spec.replace(" Plane", "").strip()
        if key in ctx.planes:
            return ctx.planes[key]
        if spec in ctx.planes:
            return ctx.planes[spec]
        raise FeatureError(f"unknown plane {spec!r}")
    if isinstance(spec, dict) and spec.get("kind") == "face":
        if ctx.body is None:
            raise FeatureError("no body to reference a face on")
        face = resolve_face(ctx.body, spec)
        if face.geom_type.name.lower() != "plane":
            raise FeatureError("selected face is not planar")
        n = face.normal_at().normalized()
        p = face.center()
        return plane_for_normal(n * p.dot(n), n)
    if isinstance(spec, dict) and "origin" in spec and "normal" in spec:  # explicit frame (e.g. read from a .sldprt)
        x = spec.get("x_dir")
        return Plane(vec(spec["origin"]), x_dir=vec(x) if x else None, z_dir=vec(spec["normal"]))
    raise FeatureError(f"cannot interpret plane reference {spec!r}")


def resolve_axis(ctx: Ctx, spec) -> Axis:
    if isinstance(spec, str):
        if spec in ctx.axes:
            return ctx.axes[spec]
        if spec.upper() in STD_AXES:
            return STD_AXES[spec.upper()]
        if "/" in spec:  # Sketch1/Line3
            sk_name, ent = spec.split("/", 1)
            if sk_name not in ctx.sketches:
                raise FeatureError(f"unknown sketch {sk_name}")
            sk, pl = ctx.sketches[sk_name]
            axes = sk.axes(pl)
            if ent not in axes:
                raise FeatureError(f"{spec} is not a sketch line")
            return axes[ent]
    if isinstance(spec, dict):
        if "origin" in spec and "direction" in spec:
            return Axis(vec(spec["origin"]), vec(spec["direction"]).normalized())
        if spec.get("kind") == "edge":
            if ctx.body is None:
                raise FeatureError("no body to reference an edge on")
            e = resolve_edge(ctx.body, spec)
            kind = e.geom_type.name.lower()
            if kind == "line":
                return Axis(e.position_at(0), (e.position_at(1) - e.position_at(0)).normalized())
            if kind == "circle":
                return Axis(e.arc_center, e.normal())
            raise FeatureError("edge must be linear or circular to define an axis")
        if spec.get("kind") == "face":  # cylindrical face -> its axis
            face = resolve_face(ctx.body, spec)
            for e in face.edges():
                if e.geom_type.name.lower() == "circle":
                    return Axis(e.arc_center, e.normal())
            raise FeatureError("face does not define an axis")
    raise FeatureError(f"cannot interpret axis reference {spec!r}")


def resolve_point(ctx: Ctx, spec) -> Vector:
    """A point: [x, y, z], a reference point name, the origin, or a vertex / edge (arc centre or
    midpoint) / face (centre) reference."""
    if isinstance(spec, (list, tuple)):
        return vec(spec)
    if isinstance(spec, str):
        if spec in ctx.points:
            return ctx.points[spec]
        if spec.lower() == "origin":
            return Vector(0, 0, 0)
        if spec in ctx.csys:
            return ctx.csys[spec].origin
        raise FeatureError(f"unknown point {spec!r}")
    if isinstance(spec, dict):
        if ctx.body is None and spec.get("kind") in ("vertex", "edge", "face"):
            raise FeatureError("no body to reference geometry on")
        if spec.get("kind") == "vertex":
            target = vec(spec["point"])
            return min((Vector(*v) for v in ctx.body.vertices()), key=lambda q: (q - target).length)
        if spec.get("kind") == "edge":
            e = resolve_edge(ctx.body, spec)
            return e.arc_center if e.geom_type.name.lower() == "circle" else e.position_at(0.5)
        if spec.get("kind") == "face":
            return resolve_face(ctx.body, spec).center()
    raise FeatureError(f"cannot interpret point reference {spec!r}")


def resolve_direction(ctx: Ctx, spec) -> Vector:
    if isinstance(spec, (list, tuple)):
        return vec(spec).normalized()
    if isinstance(spec, dict) and "vector" in spec:
        return vec(spec["vector"]).normalized()
    if isinstance(spec, dict) and spec.get("kind") == "face":
        return resolve_plane(ctx, spec).z_dir
    return resolve_axis(ctx, spec).direction.normalized()


def _sketch(ctx: Ctx, name: str):
    if name not in ctx.sketches:
        raise FeatureError(f"unknown sketch {name!r}")
    return ctx.sketches[name]


def _solids(shape: Shape | None) -> list[Solid]:
    return [] if shape is None else list(shape.solids())


def _compound(solids) -> Shape | None:
    solids = list(solids)
    if not solids:
        return None
    return Compound(solids)


def _valid(shape: Shape) -> Shape:
    """A boolean result OpenCascade marks invalid (e.g. a fuse along faces that only nearly coincide), repaired:
    later booleans on an invalid body come out inside out or empty."""
    if shape.is_valid:
        return shape
    from OCP.ShapeFix import ShapeFix_Shape
    fix = ShapeFix_Shape(shape.wrapped)
    fix.Perform()
    fixed = Shape.cast(fix.Shape())
    return fixed if fixed.is_valid and fixed.solids() else shape


def _material(solids) -> list:
    """Solids without the empty ones (zero volume: degenerate leftovers of a boolean, e.g. the sheet-like shells
    a cut along shared faces can leave), unless nothing else is left."""
    solids = list(solids)
    vols = [abs(s.volume) for s in solids]
    big = max(vols, default=0.0)
    keep = [s for s, v in zip(solids, vols) if v > 1e-9 * max(big, 1.0)]
    return keep or solids


def apply_op(ctx: Ctx, op: str, tool: Shape, merge: bool = True) -> None:
    _apply_op(ctx, op, tool, merge)
    if ctx.body is not None and len(ctx.body.solids()) > 1:
        ctx.body = _compound(_material(ctx.body.solids()))


def _apply_op(ctx: Ctx, op: str, tool: Shape, merge: bool = True) -> None:
    if op == "add":
        if ctx.body is None:
            ctx.body = _compound(tool.solids())
        elif merge:
            fused = _valid(ctx.body.fuse(tool).clean()).solids()
            # (a fuse that fails returns no solids: keep both as bodies rather than lose the part)
            ctx.body = _compound(fused if fused else _solids(ctx.body) + list(tool.solids()))
        else:
            ctx.body = _compound(_solids(ctx.body) + list(tool.solids()))
    else:
        if ctx.body is None:
            raise FeatureError("nothing to cut: the part has no body yet")
        ctx.body = _compound(_valid(ctx.body.cut(tool).clean()).solids())


def _through_length(ctx: Ctx, plane: Plane) -> float:
    size = 1000.0
    if ctx.body is not None:
        bb = ctx.body.bounding_box()
        size = bb.diagonal + (bb.center() - plane.origin).length
    return 2 * size + 10


# ---------------------------------------------------------------------- builders
def _ext_alive(body, ref: dict) -> bool:
    """Does the model edge a converted sketch entity came from still exist (same kind, same place)?"""
    from .geom import closest_point, geom_name
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
    tool = tools[0]
    for t in tools[1:]:
        tool = tool.fuse(t)
    op = "cut" if cut else "add"
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


def _path_wire(ctx: Ctx, sketch_name: str):
    from build123d import Wire
    if sketch_name in ctx.curves:  # 3D sketch or helix
        edges = list(ctx.curves[sketch_name])
    else:
        sk, plane = _sketch(ctx, sketch_name)
        edges = [e for _, e in sk.to_edges(plane)]
    if not edges:
        raise FeatureError(f"path sketch {sketch_name} has no geometry")
    wires = Wire.combine(edges, tol=1e-6)
    if len(wires) != 1:
        raise FeatureError("the sweep path must be one connected chain of sketch entities")
    return wires[0]


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
    if ctx.flat:
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
    if ctx.flat:
        y = n.cross(x_dir).normalized()
        c0 = origin + y * (BA / 2)
        bend |= {"flat_line": [list(c0), list(c0 + x_dir * e_len)], "toward": list(n)}
    ctx.sheet["bends"].append(bend)
    ctx.tools[name] = [("add", tool)]


def build_extruded_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Extruded Surface: every sketch curve (open or closed) swept along the sketch normal."""
    from build123d import Face as _F, Shell
    sk, plane = _sketch(ctx, p["sketch"])
    d = plane.z_dir * (-1 if p.get("reverse") else 1) * float(p.get("depth", 10.0))
    faces = [_F.extrude(e, d) for _, e in sk.to_edges(plane)]
    if not faces:
        raise FeatureError("the sketch has no curves")
    ctx.surfaces[name] = Shell(faces)


def build_thicken(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Thicken: turn a surface body into a solid of the given thickness (flip = other side)."""
    surf = ctx.surfaces.get(p["surface"])
    if surf is None:
        raise FeatureError(f"no surface body named {p['surface']!r}")
    t = float(p.get("thickness", 1.0)) * (-1 if p.get("flip") else 1)
    try:
        solid = Solid.thicken(surf, t)
    except Exception as exc:
        raise FeatureError(f"thicken failed: {exc}") from exc
    apply_op(ctx, "add", solid, p.get("merge", True))
    ctx.tools[name] = [("add", solid)]
    if p.get("consume", True):
        ctx.surfaces.pop(p["surface"], None)


# ---------------------------------------------------------------------- B8 sheet metal extras
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
    B = B.moved(Location(m * (-BA))).rotate(ax, deg)  # shift back to the bend start, rotate about the axis
    ctx.body = A.fuse(arc).fuse(B).clean()
    pt = lambda q: C + _rot(q - m * BA - C, ax, deg)  # noqa: E731
    vv = lambda v: _rot(v, ax, deg)  # noqa: E731
    return BA, arc, pt, vv


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
    if ctx.flat:  # the flat pattern keeps the sheet flat: only the bend line is noted
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
    if ctx.flat:  # two bend lines, D apart, bending opposite ways
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


# ---------------------------------------------------------------------- B8 surfaces
def _sketch_wires(ctx: Ctx, name: str):
    from build123d import Wire
    if name in ctx.curves:
        edges = list(ctx.curves[name])
    else:
        sk, plane = _sketch(ctx, name)
        edges = [e for _, e in sk.to_edges(plane)]
    if not edges:
        raise FeatureError(f"{name} has no curves")
    return Wire.combine(edges, tol=1e-6)


def _add_surface(ctx: Ctx, name: str, shape) -> None:
    ctx.surfaces[name] = shape
    ctx.info[name] = {"surface_area": round(shape.area, 6), "faces": len(shape.faces())}


def build_revolved_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Revolved Surface: sketch curves revolved about an axis (sketch line, edge, X/Y/Z)."""
    from build123d import Shell
    ax = resolve_axis(ctx, p["axis"])
    ang = float(p.get("angle", 360.0))
    axes = set()
    if isinstance(p["axis"], str) and "/" in p["axis"]:
        axes.add(p["axis"].split("/", 1)[1])
    sk, plane = _sketch(ctx, p["sketch"])
    faces = []
    from build123d import Face as _F
    for eid, e in sk.to_edges(plane):
        if eid in axes:
            continue
        faces.append(_F.revolve(e, ang, ax))
    if not faces:
        raise FeatureError("nothing to revolve")
    _add_surface(ctx, name, Shell(faces) if len(faces) > 1 else faces[0])


def build_swept_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Swept Surface: an open or closed profile curve swept along a path."""
    from build123d import Face as _F, Shell
    prof = _sketch_wires(ctx, p["profile"])
    path = _path_wire(ctx, p["path"])
    faces = []
    for w in prof:
        s = _F.sweep(w, path)
        faces += list(s.faces())
    _add_surface(ctx, name, Shell(faces) if len(faces) > 1 else faces[0])


def build_lofted_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Lofted Surface: a skin through two or more profile curves (sketches / 3D sketches)."""
    from build123d import Shell
    wires = []
    for s in p["profiles"]:
        ws = _sketch_wires(ctx, s)
        if len(ws) != 1:
            raise FeatureError(f"loft profile {s} must be one connected curve")
        wires.append(ws[0])
    if len(wires) < 2:
        raise FeatureError("a loft needs at least two profiles")
    _add_surface(ctx, name, Shell.make_loft(wires, ruled=bool(p.get("ruled"))))


def build_boundary_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Boundary Surface: with curves in both directions, a patch whose edges are exactly the
    direction-1 and direction-2 curves (they must form a closed loop); with direction-1 curves only, a smooth
    skin through them in order."""
    from build123d import Face as _F, Shell, Wire
    d1, d2 = p.get("direction1", []), p.get("direction2", [])
    if len(d1) < 2 and not (d1 and d2):
        raise FeatureError("a boundary surface needs at least two direction-1 curves (or curves in both directions)")
    if not d2:
        build_lofted_surface(ctx, {"profiles": d1, "ruled": False}, name)
        return
    edges = []
    for nm in list(d1) + list(d2):
        for w in _sketch_wires(ctx, nm):
            edges += list(w.edges())
    loops = Wire.combine(edges, tol=1e-4)
    if len(loops) != 1 or not loops[0].is_closed:
        raise FeatureError("the direction-1 and direction-2 curves must meet end to end in one closed loop")
    try:
        face = _F.make_surface(loops[0])
    except Exception as exc:
        raise FeatureError(f"boundary surface failed: {exc}") from exc
    _add_surface(ctx, name, face)


def build_planar_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Planar Surface: the closed regions of a sketch as surface faces."""
    from build123d import Shell
    sk, plane = _sketch(ctx, p["sketch"])
    faces = sk.profiles(plane)
    if not faces:
        raise FeatureError("the sketch has no closed region")
    _add_surface(ctx, name, Shell(faces) if len(faces) > 1 else faces[0])


def build_filled_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Filled Surface: an N-sided patch through a closed boundary (sketch or 3D sketch, may be
    non-planar), optionally pulled through constraint points."""
    from build123d import Face as _F
    ws = _sketch_wires(ctx, p["boundary"])
    if len(ws) != 1 or not ws[0].is_closed:
        raise FeatureError("the boundary must be one closed chain of curves")
    pts = [vec(q) for q in p.get("points") or []] or None
    try:
        face = _F.make_surface(ws[0], surface_points=pts)
    except Exception as exc:
        raise FeatureError(f"fill failed: {exc}") from exc
    _add_surface(ctx, name, face)


def build_offset_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Offset Surface: a copy of a surface body (or of model faces) offset along its normals."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    from build123d import Shell
    if p.get("surface"):
        src = ctx.surfaces.get(p["surface"])
        if src is None:
            raise FeatureError(f"no surface body named {p['surface']!r}")
    else:
        if ctx.body is None:
            raise FeatureError("no body to offset faces from")
        fs = [resolve_face(ctx.body, f) for f in p.get("faces", [])]
        if not fs:
            raise FeatureError("give a surface or faces to offset")
        src = Shell(fs) if len(fs) > 1 else fs[0]
    d = float(p.get("distance", 5.0))
    if abs(d) < 1e-9:
        _add_surface(ctx, name, src.moved(Location()))
        return
    mk = BRepOffsetAPI_MakeOffsetShape()
    mk.PerformBySimple(src.wrapped, d)
    if not mk.IsDone():
        raise FeatureError("offset failed (offset larger than a curvature radius?)")
    _add_surface(ctx, name, Shape.cast(mk.Shape()))


def build_knit_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Knit Surface: sew surface bodies along shared edges; form_solid makes a closed knit a solid."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    names = p.get("surfaces", [])
    if len(names) < 1:
        raise FeatureError("select surface bodies to knit")
    sew = BRepBuilderAPI_Sewing(float(p.get("tolerance", 1e-4)))
    for s in names:
        if s not in ctx.surfaces:
            raise FeatureError(f"no surface body named {s!r}")
        sew.Add(ctx.surfaces[s].wrapped)
    sew.Perform()
    sewn = Shape.cast(sew.SewedShape())
    for s in names:
        ctx.surfaces.pop(s, None)
    if p.get("form_solid"):
        shells = sewn.shells()
        if not shells:
            raise FeatureError("the knit did not produce a shell")
        mk = BRepBuilderAPI_MakeSolid(shells[0].wrapped)
        solid = Solid(mk.Solid())
        if solid.volume < 0:
            solid = Solid(solid.wrapped.Reversed())
        if abs(solid.volume) < 1e-9:
            raise FeatureError("the knit surfaces do not enclose a volume (gaps?)")
        apply_op(ctx, "add", solid, p.get("merge", True))
        ctx.tools[name] = [("add", solid)]
        ctx.info[name] = {"volume": solid.volume}
        return
    _add_surface(ctx, name, sewn)


def build_trim_surface(ctx: Ctx, p: dict, name: str) -> None:
    """Surfaces > Trim Surface: cut a surface body with a plane (or planar face) and keep the side
    containing `keep` [x, y, z] (default: the side the plane normal points to)."""
    from build123d import Keep
    src = ctx.surfaces.get(p["surface"])
    if src is None:
        raise FeatureError(f"no surface body named {p['surface']!r}")
    pl = resolve_plane(ctx, p["tool"])
    keep_top = True
    if p.get("keep") is not None:
        keep_top = (vec(p["keep"]) - pl.origin).dot(pl.z_dir) > 0
    if p.get("flip"):
        keep_top = not keep_top
    out = src.split(pl, keep=Keep.TOP if keep_top else Keep.BOTTOM)
    if out is None or not out.faces():
        raise FeatureError("nothing left after the trim")
    ctx.surfaces.pop(p["surface"], None)
    _add_surface(ctx, name, out)


# ---------------------------------------------------------------------- B8 weldments
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


# ISO metric hole data (mm): clearance (normal), tap drill, socket-head counterbore (dia, depth), 90° countersink dia
ISO_HOLES = {
    "M2": (2.4, 1.6, (4.4, 2.3), 4.4), "M2.5": (2.9, 2.05, (5.5, 2.8), 5.5), "M3": (3.4, 2.5, (6.5, 3.3), 6.5),
    "M4": (4.5, 3.3, (8.0, 4.4), 8.4), "M5": (5.5, 4.2, (10.0, 5.4), 10.4), "M6": (6.6, 5.0, (11.0, 6.4), 12.4),
    "M8": (9.0, 6.8, (15.0, 8.6), 16.4), "M10": (11.0, 8.5, (18.0, 10.6), 20.4), "M12": (13.5, 10.2, (20.0, 12.6), 24.4),
    "M16": (17.5, 14.0, (26.0, 16.6), 32.4), "M20": (22.0, 17.5, (33.0, 20.6), 40.4), "M24": (26.0, 21.0, (40.0, 24.8), 48.4),
}


# ANSI inch (UNC) data in inches: clearance (normal), tap drill, SHCS counterbore (dia, depth), 82° countersink dia
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


def _seed_tools(ctx: Ctx, names: list[str]):
    out = []
    for n in names:
        if n not in ctx.tools:
            raise FeatureError(f"{n} cannot be patterned (only extrudes, revolves, holes and patterns of them)")
        out += ctx.tools[n]
    if not out:
        raise FeatureError("select features to pattern")
    return out


def _apply_copies(ctx: Ctx, name: str, seeds, transforms) -> None:
    """Pattern copies: each seed tool moved by each transform. Runs of copies with the same operation go to
    the kernel as one boolean with many tools (one boolean per copy grows with the body: minutes for a ring
    of ribs); copies are applied in order otherwise."""
    applied = [(op, t(tool)) for t in transforms for op, tool in seeds]
    if ctx.info.get("_tools_only"):  # (a trial that only needs the copies, not the patterned body)
        ctx.tools[name] = list(seeds) + applied
        return
    i = 0
    while i < len(applied):
        j = i
        while j < len(applied) and applied[j][0] == applied[i][0]:
            j += 1
        op, tools = applied[i][0], [tl for _, tl in applied[i:j]]
        if ctx.body is None or len(tools) == 1:
            for tl in tools:
                apply_op(ctx, op, tl)
        else:
            try:  # (as one compound tool: copies that do not overlap each other need no pairwise intersections)
                one = Compound([sol for tl in tools for sol in tl.solids()])
                res = ctx.body.fuse(one) if op == "add" else ctx.body.cut(one)
                sols = res.clean().solids()
                if not sols:
                    raise ValueError("empty result")
                ctx.body = _compound(sols)
            except Exception:  # (the kernel failed with many tools at once: one at a time)
                for tl in tools:
                    apply_op(ctx, op, tl)
        i = j
    ctx.tools[name] = list(seeds) + applied


def build_linear_pattern(ctx: Ctx, p: dict, name: str) -> None:
    seeds = _seed_tools(ctx, p["features"])
    d1 = resolve_direction(ctx, p["direction"]) * (-1 if p.get("reverse") else 1)
    n1, s1 = int(p.get("count", 2)), float(p.get("spacing", 10.0))
    dir2 = p.get("direction2")
    if dir2:
        d2 = resolve_direction(ctx, dir2["direction"]) * (-1 if dir2.get("reverse") else 1)
        n2, s2 = int(dir2.get("count", 2)), float(dir2.get("spacing", 10.0))
    else:
        d2, n2, s2 = Vector(0, 0, 0), 1, 0.0
    skip = {tuple(x) for x in p.get("skip", [])}
    transforms = []
    for i in range(n1):
        for j in range(n2):
            if (i, j) == (0, 0) or (i, j) in skip:
                continue
            off = d1 * (s1 * i) + d2 * (s2 * j)
            transforms.append(lambda t, off=off: t.moved(Location(off)))
    _apply_copies(ctx, name, seeds, transforms)


def build_circular_pattern(ctx: Ctx, p: dict, name: str) -> None:
    seeds = _seed_tools(ctx, p["features"])
    axis = resolve_axis(ctx, p["axis"])
    n = int(p.get("count", 4))
    total = float(p.get("angle", 360.0))
    if p.get("equal_spacing", True):
        step = total / n if abs(total - 360.0) < 1e-9 else total / max(n - 1, 1)
    else:
        step = total
    if p.get("reverse"):
        step = -step
    transforms = [lambda t, a=step * k: t.rotate(axis, a) for k in range(1, n)]
    _apply_copies(ctx, name, seeds, transforms)


def build_sketch_pattern(ctx: Ctx, p: dict, name: str) -> None:
    """Sketch Driven Pattern: copy seed features to every sketch point, measured from the reference
    point (default: the seed's position = first sketch point or `reference` [x, y])."""
    seeds = _seed_tools(ctx, p["features"])
    sk, plane = _sketch(ctx, p["sketch"])
    pts = [plane.from_local_coords(Vector(*sk.coords(e["p"]), 0)) for e in sk.entities.values() if e["type"] == "point"]
    if not pts:
        raise FeatureError("the pattern sketch has no sketch points")
    ref = plane.from_local_coords(Vector(*p["reference"], 0)) if p.get("reference") else pts[0]
    transforms = [lambda t, off=(q - ref): t.moved(Location(off)) for q in pts if (q - ref).length > 1e-9]
    _apply_copies(ctx, name, seeds, transforms)


def build_curve_pattern(ctx: Ctx, p: dict, name: str) -> None:
    """Curve Driven Pattern: `count` copies spaced equally along a sketch curve (path sketch), translated."""
    seeds = _seed_tools(ctx, p["features"])
    path = _path_wire(ctx, p["path"])
    n = int(p.get("count", 4))
    if n < 2:
        raise FeatureError("count must be at least 2")
    p0 = path.position_at(0)
    transforms = [lambda t, off=(path.position_at(k / (n - 1)) - p0): t.moved(Location(off)) for k in range(1, n)]
    _apply_copies(ctx, name, seeds, transforms)


def build_table_pattern(ctx: Ctx, p: dict, name: str) -> None:
    """Table Driven Pattern: one instance per row of `offsets` ([dx, dy, dz] from the seed)."""
    seeds = _seed_tools(ctx, p["features"])
    rows = [Vector(*r) for r in p["offsets"]]
    if p.get("csys"):
        if p["csys"] not in ctx.csys:
            raise FeatureError(f"unknown coordinate system {p['csys']!r}")
        f = ctx.csys[p["csys"]]
        rows = [f.x_dir * r.X + f.y_dir * r.Y + f.z_dir * r.Z for r in rows]
    if not rows:
        raise FeatureError("the pattern table is empty")
    _apply_copies(ctx, name, seeds, [lambda t, off=off: t.moved(Location(off)) for off in rows if off.length > 1e-9])


def build_fill_pattern(ctx: Ctx, p: dict, name: str) -> None:
    """Fill Pattern: fill a planar face with a square or staggered grid of the seed features, keeping
    instances whose centre lies on the face at least `margin` from its boundary."""
    if ctx.body is None:
        raise FeatureError("the part has no body")
    seeds = _seed_tools(ctx, p["features"])
    face = resolve_face(ctx.body, p["face"])
    if face.geom_type.name.lower() != "plane":
        raise FeatureError("fill pattern needs a planar face")
    s, margin = float(p.get("spacing", 10.0)), float(p.get("margin", 0.0))
    if s <= 0:
        raise FeatureError("spacing must be positive")
    pl = Plane(face)
    ang = math.radians(float(p.get("angle", 0.0)))
    u = pl.x_dir * math.cos(ang) + pl.y_dir * math.sin(ang)
    v = pl.z_dir.cross(u)
    c = Compound([t for _, t in seeds]).bounding_box().center()
    c = c - pl.z_dir * (c - pl.origin).dot(pl.z_dir)
    stagger = p.get("layout", "square") == "staggered"
    row = s * math.sqrt(3) / 2 if stagger else s
    reach = face.bounding_box().diagonal + (c - face.center()).length
    ni, nj = int(reach / s) + 2, int(reach / row) + 2
    edges = face.edges()
    transforms = []
    for j in range(-nj, nj + 1):
        shift = s / 2 if stagger and j % 2 else 0.0
        for i in range(-ni, ni + 1):
            off = u * (i * s + shift) + v * (j * row)
            if off.length < 1e-9:
                continue
            q = c + off
            if not face.is_inside(q) or min(e.distance_to(q) for e in edges) < margin - 1e-9:
                continue
            transforms.append(lambda t, off=off: t.moved(Location(off)))
    if not transforms:
        raise FeatureError("no instances fit on the face")
    _apply_copies(ctx, name, seeds, transforms)


def build_mirror(ctx: Ctx, p: dict, name: str) -> None:
    plane = resolve_plane(ctx, p["plane"])
    if p.get("bodies"):
        if ctx.body is None:
            raise FeatureError("the part has no body")
        apply_op(ctx, "add", ctx.body.mirror(plane), p.get("merge", True))
        return
    seeds = _seed_tools(ctx, p["features"])
    _apply_copies(ctx, name, seeds, [lambda t: t.mirror(plane)])


def build_plane(ctx: Ctx, p: dict, name: str) -> None:
    base = resolve_plane(ctx, p.get("reference", "Front"))
    n = -base.z_dir if p.get("flip") else base.z_dir
    origin = base.origin + n * float(p.get("offset", 0.0))
    ang = float(p.get("angle", 0.0))
    pl = plane_for_normal(origin, n)
    if ang:
        axis = resolve_axis(ctx, p["angle_axis"]) if p.get("angle_axis") else Axis(base.origin, base.x_dir)
        pl = plane_for_normal(origin, _rot(n, axis, ang))
    ctx.planes[name] = pl
    ctx.info[name] = {"plane_origin": list(pl.origin), "plane_x": list(pl.x_dir), "plane_z": list(pl.z_dir)}


def build_ref_axis(ctx: Ctx, p: dict, name: str) -> None:
    """Reference Axis from: `reference` (edge, cylindrical face, sketch line, X/Y/Z), the intersection
    of two `planes`, or two `points`."""
    if p.get("planes"):
        a, b = (resolve_plane(ctx, s) for s in p["planes"])
        d = a.z_dir.cross(b.z_dir)
        if d.length < 1e-9:
            raise FeatureError("the two planes are parallel")
        d = d.normalized()
        # point on both planes closest to the first plane's origin
        n1, n2 = a.z_dir, b.z_dir
        h1, h2 = n1.dot(a.origin), n2.dot(b.origin)
        k = n1.dot(n2)
        det = 1 - k * k
        o = n1 * ((h1 - k * h2) / det) + n2 * ((h2 - k * h1) / det)
        ax = Axis(o, d)
    elif p.get("points"):
        q0, q1 = (resolve_point(ctx, s) for s in p["points"])
        if (q1 - q0).length < 1e-9:
            raise FeatureError("the two points coincide")
        ax = Axis(q0, (q1 - q0).normalized())
    else:
        ax = resolve_axis(ctx, p["reference"])
    ctx.axes[name] = ax
    ctx.info[name] = {"axis_origin": list(ax.position), "axis_direction": list(ax.direction)}


def build_ref_point(ctx: Ctx, p: dict, name: str) -> None:
    """Reference Point at `coords` [x, y, z] or a `reference` (vertex, arc centre, edge midpoint, face
    centre, other point), optionally shifted by `offset` [dx, dy, dz]."""
    q = resolve_point(ctx, p["coords"] if p.get("coords") is not None else p["reference"])
    if p.get("offset"):
        q = q + vec(p["offset"])
    ctx.points[name] = q
    ctx.info[name] = {"point": list(q)}


def build_sketch3d(ctx: Ctx, p: dict, name: str) -> None:
    """3D Sketch: segments of [x, y, z] points, each a polyline (`kind` line) or an interpolating spline."""
    from build123d import Edge
    edges = []
    for seg in p.get("segments", []):
        pts = [vec(q) for q in seg["points"]]
        if len(pts) < 2:
            raise FeatureError("a 3D sketch segment needs at least two points")
        if seg.get("kind", "line") == "spline":
            edges.append(Edge.make_spline(pts, periodic=bool(seg.get("closed")) and len(pts) > 2))
        else:
            ring = pts + ([pts[0]] if seg.get("closed") else [])
            edges += [Edge.make_line(a, b) for a, b in zip(ring, ring[1:]) if (b - a).length > 1e-9]
    ctx.curves[name] = edges
    ctx.info[name] = {"length": sum(e.length for e in edges), "edges": len(edges)}


def build_helix(ctx: Ctx, p: dict, name: str) -> None:
    """Helix/Spiral curve: `pitch`, `revolutions` (or `height`), `radius`, about `axis` through `origin`,
    starting at `start_angle` degrees; `taper` degrees makes a conical helix."""
    from build123d import Edge
    ax = resolve_axis(ctx, p.get("axis", "Z"))
    origin = vec(p["origin"]) if p.get("origin") is not None else ax.position
    pitch, r = float(p["pitch"]), float(p["radius"])
    if pitch <= 0 or r <= 0:
        raise FeatureError("pitch and radius must be positive")
    h = float(p["height"]) if p.get("height") else pitch * float(p.get("revolutions", 1))
    d = ax.direction.normalized() * (-1 if p.get("reverse") else 1)
    w = Edge.make_helix(pitch, h, r, center=origin, normal=d, angle=float(p.get("taper", 0.0)),
                        lefthand=bool(p.get("counterclockwise")))
    if p.get("start_angle"):
        w = w.rotate(Axis(origin, d), float(p["start_angle"]))
    edges = list(w.edges())
    ctx.curves[name] = edges
    ctx.info[name] = {"length": sum(e.length for e in edges), "start": list(edges[0].position_at(0)),
                      "start_tangent": list(edges[0].tangent_at(0))}


def build_csys(ctx: Ctx, p: dict, name: str) -> None:
    """Coordinate System: `origin` point, X axis direction and Y axis direction (Z = X x Y). Usable as a
    plane (its XY plane) and as a frame for table patterns."""
    o = resolve_point(ctx, p.get("origin") or [0, 0, 0])
    x = resolve_direction(ctx, p.get("x_axis") or [1, 0, 0])
    y = resolve_direction(ctx, p.get("y_axis") or [0, 1, 0])
    y = y - x * y.dot(x)
    if y.length < 1e-9:
        raise FeatureError("the X and Y axes are parallel")
    z = x.cross(y.normalized())
    pl = Plane(origin=o, x_dir=x, z_dir=z)
    ctx.csys[name] = pl
    ctx.planes[name] = pl
    ctx.info[name] = {"csys_origin": list(pl.origin), "csys_x": list(pl.x_dir), "csys_y": list(pl.y_dir), "csys_z": list(pl.z_dir)}


def _rot(v: Vector, axis: Axis, ang: float) -> Vector:
    k = axis.direction.normalized()
    a = math.radians(ang)
    return v * math.cos(a) + k.cross(v) * math.sin(a) + k * k.dot(v) * (1 - math.cos(a))


def build_import(ctx: Ctx, p: dict, name: str) -> None:
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".brep")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(base64.b64decode(p["brep"]))
        shape = import_brep(tmp)
    finally:
        os.unlink(tmp)
    if p.get("op") == "replace":  # the body becomes the imported one (the rest of a part read from a file)
        ctx.body = _compound(shape.solids()) if shape.solids() else shape
        ctx.tools[name] = []
        return
    op = "cut" if p.get("op") == "cut" else "add"  # cut: remove the imported material (a feature read from a file)
    tools = [(op, shape)] + ([("cut", decode_shape(p["cut_brep"]))] if p.get("cut_brep") else [])
    ctx.tools[name] = tools
    if p.get("result") and ctx.body is not None and same_body(p.get("base"), body_key(ctx.body)):
        # the body is the one the result was worked out from (a part read from a file, not edited since)
        res = decode_shape(p["result"])
        ctx.body = _compound(res.solids()) if res.solids() else res
        return
    for o, t in tools:
        apply_op(ctx, o, t, p.get("merge", True))
    if p.get("result") and (ctx.body is None or body_key(ctx.body)[0] <= 0 or not ctx.body.is_valid):
        # (the features before it changed and the kernel could not merge the stored geometry with them)
        raise FeatureError("the imported geometry no longer fits the features before it")


def decode_shape(b64: str) -> Shape:
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".brep")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(base64.b64decode(b64))
        return import_brep(tmp)
    finally:
        os.unlink(tmp)


def same_body(a, b) -> bool:
    """Two body_key values describe the same body: the same face / edge counts, volume within 1e-6, bounding
    box within 1e-4 mm."""
    if not a or not b or len(a) != len(b) or len(a) != 9:
        return False
    return (a[1:3] == b[1:3] and abs(a[0] - b[0]) <= 1e-6 * max(1.0, abs(a[0]))
            and all(abs(x - y) <= 1e-4 for x, y in zip(a[3:], b[3:])))


def body_key(body) -> list[float]:
    """A body's volume, face / edge counts and bounding box: tells whether a body is still the same one (not
    the area: OCC's default surface integration on B-spline faces changes when a shape is written and read)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(body.wrapped, g)
    box = Bnd_Box()
    BRepBndLib.Add_s(body.wrapped, box, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return [g.Mass(), len(body.faces()), len(body.edges()), lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z()]


def encode_shape(shape: Shape) -> str:
    buf = io.BytesIO()
    export_brep(shape, buf)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------- body / face editing and curves
def _cutting_tool(ctx: Ctx, spec):
    """A plane (name or planar face) or a surface body (its feature name) to split with."""
    if isinstance(spec, str) and spec in ctx.surfaces:
        return ctx.surfaces[spec]
    return resolve_plane(ctx, spec)


def build_split(ctx: Ctx, p: dict, name: str) -> None:
    """Split: cut the solid bodies with a plane or a surface. keep: 'all' (every piece becomes a body),
    'above' / 'below' (the side the plane's normal / the surface's normal points to, or the other)."""
    from build123d import Keep
    if ctx.body is None:
        raise FeatureError("the part has no body to split")
    tool = _cutting_tool(ctx, p["tool"])
    keep = {"all": Keep.BOTH, "above": Keep.TOP, "below": Keep.BOTTOM}[p.get("keep", "all")]
    out = []
    for s in ctx.body.solids():
        r = s.split(tool, keep=keep)
        for part in (r if isinstance(r, (list, tuple)) else [r]):
            if part is not None:
                out += list(part.solids())
    if not out:
        raise FeatureError("nothing of the body is left on that side")
    ctx.body = _compound(out)
    ctx.info[name] = {"bodies": len(out)}


def build_intersect(ctx: Ctx, p: dict, name: str) -> None:
    """Intersect: the regions the solid bodies and the `tools` (surface bodies, planes) enclose between them
    - including volumes closed only by surfaces - become solids; `exclude` drops regions by index (in the
    order reported: by centroid) and `merge` fuses what is left into one body."""
    from build123d import Face
    from OCP.BOPAlgo import BOPAlgo_MakerVolume
    from OCP.collections import List_TopoDS_Shape
    shapes = list(ctx.body.solids()) if ctx.body is not None else []
    extent = max([ctx.body.bounding_box().diagonal] if ctx.body is not None else [0.0] +
                 [s.bounding_box().diagonal for s in ctx.surfaces.values()]) or 100.0
    for t in p["tools"]:
        if isinstance(t, str) and t in ctx.surfaces:
            shapes.append(ctx.surfaces[t])
        else:
            pl = resolve_plane(ctx, t)
            shapes.append(Face.make_rect(4 * extent, 4 * extent, pl))
    if len(shapes) < 2:
        raise FeatureError("intersect needs a body and a tool, or two or more tools")
    args = List_TopoDS_Shape()
    for s in shapes:
        args.Append(s.wrapped)
    mv = BOPAlgo_MakerVolume()
    mv.SetArguments(args)
    mv.SetIntersect(True)
    mv.SetRunParallel(False)
    mv.Perform()
    regions = list(Shape.cast(mv.Shape()).solids()) if not mv.HasErrors() else []
    if not regions:
        raise FeatureError("the bodies and surfaces do not enclose any region")
    regions.sort(key=lambda s: tuple(round(c, 6) for c in s.center()))
    ctx.info[name] = {"regions": [{"index": i, "volume": s.volume, "center": list(s.center())} for i, s in enumerate(regions)]}
    drop = {int(i) for i in p.get("exclude", [])}
    keep = [s for i, s in enumerate(regions) if i not in drop]
    if not keep:
        raise FeatureError("every region is excluded")
    if p.get("merge", True) and len(keep) > 1:
        fused = keep[0]
        for s in keep[1:]:
            fused = fused.fuse(s)
        keep = list(fused.clean().solids())
    ctx.body = _compound(keep)


def build_delete_face(ctx: Ctx, p: dict, name: str) -> None:
    """Delete Face. mode 'patch' (default): remove the faces and heal the solid by extending the faces round
    them (a hole, fillet or boss disappears); 'delete': the solid becomes a surface body without them."""
    from build123d import Shell
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
    if ctx.body is None:
        raise FeatureError("the part has no body")
    faces = [resolve_face(ctx.body, f) for f in p["faces"]]
    if p.get("mode", "patch") == "patch":
        d = BRepAlgoAPI_Defeaturing()
        d.SetShape(ctx.body.wrapped)
        for f in faces:
            d.AddFaceToRemove(f.wrapped)
        d.SetRunParallel(False)
        d.Build()
        res = Shape.cast(d.Shape()) if d.IsDone() else None
        if res is None or not res.solids():
            raise FeatureError("these faces cannot be removed and patched (use mode 'delete' for a surface)")
        ctx.body = _compound(res.solids())
        return
    hit, rest = [], []
    for s in ctx.body.solids():
        (hit if any(any(f.is_same(g) for g in s.faces()) for f in faces) else rest).append(s)
    keep = [g for s in hit for g in s.faces() if not any(g.is_same(f) for f in faces)]
    ctx.body = _compound(rest)
    _add_surface(ctx, name, Shell(keep))


def build_move_face(ctx: Ctx, p: dict, name: str) -> None:
    """Move Face (offset): move `faces` by `distance` along their normals (away from the material when
    positive); the neighbouring faces extend or trim to meet them."""
    if ctx.body is None:
        raise FeatureError("the part has no body")
    faces = [resolve_face(ctx.body, f) for f in p["faces"]]
    out = []
    for s in ctx.body.solids():
        mine = [f for f in faces if any(f.is_same(g) for g in s.faces())]
        out += list(_offset_faces(s, mine, float(p["distance"])).solids()) if mine else [s]
    ctx.body = _compound(out)


def _offset_faces(solid, faces, dist: float) -> Shape:
    """The solid with `faces` moved `dist` along their normals and the neighbouring faces extended to them."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Skin
    from OCP.GeomAbs import GeomAbs_Intersection
    from OCP.TopoDS import TopoDS
    m = BRepOffset_MakeOffset()
    m.Initialize(solid.wrapped, 0.0, 1e-6, BRepOffset_Skin, False, False, GeomAbs_Intersection, False)
    for f in faces:
        m.SetOffsetOnFace(f.wrapped, dist)
    m.MakeOffsetShape()
    if not m.IsDone():
        raise FeatureError("the faces cannot be moved that far (neighbouring faces cannot meet them)")
    sh = m.Shape()
    sol = Solid(BRepBuilderAPI_MakeSolid(TopoDS.Shell(sh)).Solid()) if sh.ShapeType().name == "TopAbs_SHELL" else Shape.cast(sh)
    if not sol.is_valid or sol.volume <= 0:
        raise FeatureError("moving the faces gives an invalid solid")
    return sol


def _solid_offset(solid, d: float) -> Shape:
    """The solid grown by d all round (edges rounded); the simple algorithm when the joined one fails (spheres)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.BRepOffset import BRepOffset_Skin
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    from OCP.GeomAbs import GeomAbs_Arc
    from OCP.TopoDS import TopoDS
    if abs(d) < 1e-12:
        return solid
    for simple in (False, True):
        try:
            m = BRepOffsetAPI_MakeOffsetShape()
            if simple:
                m.PerformBySimple(solid.wrapped, d)
            else:
                m.PerformByJoin(solid.wrapped, d, 1e-6, BRepOffset_Skin, False, False, GeomAbs_Arc)
            sh = m.Shape()
            out = Solid(BRepBuilderAPI_MakeSolid(TopoDS.Shell(sh)).Solid()) if sh.ShapeType().name == "TopAbs_SHELL" else Shape.cast(sh)
            if out.solids() and out.is_valid and sum(s.volume for s in out.solids()) > solid.volume:
                return _compound(out.solids())
        except Exception:
            continue
    raise FeatureError("the tool body cannot be offset (indent clearance / thickness)")


def build_indent(ctx: Ctx, p: dict, name: str) -> None:
    """Indent: the target body takes the shape of the tool body - the tool plus `clearance` is removed from it
    and, unless `cut`, a wall `thickness` thick wraps the part of the tool sticking out of the target (the piece
    nearest `region_point` when it sticks out on more than one side). The tool body is kept."""
    if ctx.body is None:
        raise FeatureError("the part has no body")
    bodies = list(ctx.body.solids())
    ti, oi = int(p["target"]), int(p["tool"])
    if not (0 <= ti < len(bodies) and 0 <= oi < len(bodies)) or ti == oi:
        raise FeatureError(f"pick two different bodies (the part has {len(bodies)})")
    target, tool = bodies[ti], bodies[oi]
    c, t = float(p.get("clearance", 0.0)), float(p.get("thickness", 1.0))
    if c < 0 or (t <= 0 and not p.get("cut")):
        raise FeatureError("clearance must be 0 or more and thickness positive")
    def solids(shape):  # a boolean's result (one shape or a list of them) as its solids
        parts = shape if isinstance(shape, (list, tuple)) else [shape]
        return [s for part in parts if part is not None for s in part.solids()]
    hole = _solid_offset(tool, c)
    result = solids(target.cut(hole))
    if not p.get("cut"):
        out_pieces = solids(tool.cut(target))
        if not out_pieces:
            raise FeatureError("the tool body does not stick out of the target body: nothing to wrap")
        if p.get("region_point") is not None and len(out_pieces) > 1:
            q = vec(p["region_point"])
            out_pieces = [min(out_pieces, key=lambda s: s.distance_to(q))]
        wall = _compound(solids(_solid_offset(tool, c + t).cut(hole)))
        reach = _solid_offset(_compound(out_pieces), c + t)
        added = solids(wall.intersect(reach))
        result = solids(_compound(result).fuse(_compound(added)).clean()) if added else result
    new = [s for i, s in enumerate(bodies) if i != ti] + result
    ctx.body = _compound(new)


def build_replace_face(ctx: Ctx, p: dict, name: str) -> None:
    """Replace Face: the solid takes the shape of a surface body in place of `faces` - the faces are pushed
    out past the surface, their neighbours extended, and the body is trimmed back to the surface."""
    from build123d import Keep
    if ctx.body is None:
        raise FeatureError("the part has no body")
    if p["surface"] not in ctx.surfaces:
        raise FeatureError(f"no surface body {p['surface']!r}")
    surf = ctx.surfaces[p["surface"]]
    faces = [resolve_face(ctx.body, f) for f in p["faces"]]
    n = faces[0].normal_at(faces[0].center())
    out = []
    for s in ctx.body.solids():
        mine = [f for f in faces if any(f.is_same(g) for g in s.faces())]
        if not mine:
            out.append(s)
            continue
        far = min((Vector(*v) for v in s.vertices()), key=lambda v: v.dot(n))  # the body's side away from the faces
        reach = surf.bounding_box().diagonal + s.bounding_box().diagonal
        grown = _offset_faces(s, mine, reach)
        r = grown.split(surf, keep=Keep.BOTH)
        pieces = [x for part in (r if isinstance(r, (list, tuple)) else [r]) if part is not None for x in part.solids()]
        if len(pieces) < 2:
            raise FeatureError("the surface does not cut right across the body; extend it past the body's edges")
        keep = [x for x in pieces if x.distance_to(far) < 1e-6]
        if not keep:
            raise FeatureError("could not tell which side of the surface the body is on")
        out += keep
    ctx.body = _compound(out)


def _sketch_edges(ctx: Ctx, sketch_name: str) -> list:
    sk, plane = _sketch(ctx, sketch_name)
    return [e for eid, e in sk.to_edges(plane) if not sk.entities.get(eid, {}).get("construction")]


def build_projected_curve(ctx: Ctx, p: dict, name: str) -> None:
    """Projected Curve (sketch onto faces): the sketch's curves projected along its plane's normal (reverse:
    the other way) onto `faces` (default: the nearest faces of the body met)."""
    edges = _project_sketch(ctx, p)
    ctx.curves[name] = edges
    ctx.info[name] = {"length": sum(e.length for e in edges)}


def build_split_line(ctx: Ctx, p: dict, name: str) -> None:
    """Split Line (projection): imprint a sketch projected onto faces, splitting them into separate faces
    (e.g. to draft or colour part of a face). The solid itself is unchanged."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.collections import List_TopoDS_Shape
    edges = _project_sketch(ctx, p)
    before = len(ctx.body.faces())
    args, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
    for s in ctx.body.solids():
        args.Append(s.wrapped)
    for e in edges:
        tools.Append(e.wrapped)
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(args)
    sp.SetTools(tools)
    sp.Build()
    if not sp.IsDone():
        raise FeatureError("the split line could not be imprinted on the faces")
    res = Shape.cast(sp.Shape())
    if not res.solids():
        raise FeatureError("the split line could not be imprinted on the faces")
    ctx.body = _compound(res.solids())
    ctx.info[name] = {"new_faces": len(ctx.body.faces()) - before}


def _project_sketch(ctx: Ctx, p: dict) -> list:
    """Edges of p["sketch"] projected along its plane's normal (p["reverse"]: the other way) onto p["faces"]
    (default: the nearest faces of the body met)."""
    from build123d import Wire
    from OCP.BRepProj import BRepProj_Projection
    from OCP.gp import gp_Dir
    if ctx.body is None:
        raise FeatureError("the part has no body to project onto")
    sk, plane = _sketch(ctx, p["sketch"])
    target = Compound([resolve_face(ctx.body, f) for f in p["faces"]]) if p.get("faces") else ctx.body
    d = plane.z_dir * (-1.0 if p.get("reverse") else 1.0)
    edges = []
    for w in Wire.combine(_sketch_edges(ctx, p["sketch"]), tol=1e-6):
        proj = BRepProj_Projection(w.wrapped, target.wrapped, gp_Dir(*-d))
        found = []
        while proj.More():
            pw = Wire(proj.Current())
            depth = (pw.center() - plane.origin).dot(-d)
            if depth >= -1e-6:  # on the side the sketch looks towards
                found.append((depth, pw))
            proj.Next()
        if found and not p.get("faces"):  # the nearest layer only
            near = min(x[0] for x in found)
            found = [x for x in found if x[0] <= near + 1e-6]
        for _, pw in found:
            edges += list(pw.edges())
    if not edges:
        raise FeatureError("the sketch does not project onto those faces")
    return edges


def build_composite_curve(ctx: Ctx, p: dict, name: str) -> None:
    """Composite Curve: sketches, curves and model edges joined end to end into one curve."""
    from build123d import Wire
    edges = []
    for ref in p["items"]:
        if isinstance(ref, str) and ref in ctx.curves:
            edges += list(ctx.curves[ref])
        elif isinstance(ref, str):
            edges += _sketch_edges(ctx, ref)
        else:
            if ctx.body is None:
                raise FeatureError("no body to take edges from")
            edges.append(resolve_edge(ctx.body, ref))
    wires = Wire.combine(edges, tol=1e-5)
    if len(wires) != 1:
        raise FeatureError(f"the curves do not join end to end ({len(wires)} separate pieces)")
    ctx.curves[name] = list(wires[0].edges())
    ctx.info[name] = {"length": wires[0].length, "closed": wires[0].is_closed}


def build_xyz_curve(ctx: Ctx, p: dict, name: str) -> None:
    """Curve Through XYZ Points: a smooth spline through the points (closed: a periodic loop)."""
    from build123d import Edge
    pts = [vec(q) for q in p["points"]]
    if len(pts) < 2:
        raise FeatureError("a curve needs at least two points")
    e = Edge.make_line(pts[0], pts[1]) if len(pts) == 2 else Edge.make_spline(pts, periodic=bool(p.get("closed")))
    ctx.curves[name] = [e]
    ctx.info[name] = {"length": e.length}


BUILDERS = {
    "split": build_split,
    "cut_with_surface": lambda c, p, n: build_split(c, {**p, "keep": "above" if p.get("flip") else "below"}, n),
    "delete_face": build_delete_face,
    "intersect": build_intersect,
    "move_face": build_move_face,
    "replace_face": build_replace_face,
    "indent": build_indent,
    "projected_curve": build_projected_curve,
    "split_line": build_split_line,
    "composite_curve": build_composite_curve,
    "xyz_curve": build_xyz_curve,
    "sketch": build_sketch,
    "extrude": build_extrude,
    "cut_extrude": lambda c, p, n: build_extrude(c, p, n, cut=True),
    "revolve": build_revolve,
    "cut_revolve": lambda c, p, n: build_revolve(c, p, n, cut=True),
    "fillet": build_fillet,
    "chamfer": build_chamfer,
    "shell": build_shell,
    "hole": build_hole,
    "linear_pattern": build_linear_pattern,
    "circular_pattern": build_circular_pattern,
    "mirror": build_mirror,
    "plane": build_plane,
    "import": build_import,
    "sweep": build_sweep,
    "cut_sweep": lambda c, p, n: build_sweep(c, p, n, cut=True),
    "loft": build_loft,
    "cut_loft": lambda c, p, n: build_loft(c, p, n, cut=True),
    "draft": build_draft,
    "base_flange": build_base_flange,
    "edge_flange": build_edge_flange,
    "extruded_surface": build_extruded_surface,
    "thicken": build_thicken,
    "structural_member": build_structural_member,
    "rib": build_rib,
    "dome": build_dome,
    "variable_fillet": build_variable_fillet,
    "sketch_pattern": build_sketch_pattern,
    "curve_pattern": build_curve_pattern,
    "table_pattern": build_table_pattern,
    "ref_axis": build_ref_axis,
    "ref_point": build_ref_point,
    "csys": build_csys,
    "sketch3d": build_sketch3d,
    "hem": build_hem,
    "vertex_chamfer": build_vertex_chamfer,
    "face_fillet": build_face_fillet,
    "full_round_fillet": build_full_round_fillet,
    "wrap": build_wrap,
    "sketched_bend": build_sketched_bend,
    "jog": build_jog,
    "corner_relief": build_corner_relief,
    "dimple": build_dimple,
    "miter_flange": build_miter_flange,
    "louver": build_louver,
    "lance": build_lance,
    "extruded_flange": build_extruded_flange,
    "form_rib": build_form_rib,
    "auto_corner_relief": build_auto_corner_relief,
    "revolved_surface": build_revolved_surface,
    "swept_surface": build_swept_surface,
    "lofted_surface": build_lofted_surface,
    "planar_surface": build_planar_surface,
    "boundary_surface": build_boundary_surface,
    "filled_surface": build_filled_surface,
    "offset_surface": build_offset_surface,
    "knit_surface": build_knit_surface,
    "trim_surface": build_trim_surface,
    "end_cap": build_end_cap,
    "gusset": build_gusset,
    "helix": build_helix,
    "fill_pattern": build_fill_pattern,
    "thread": build_thread,
    "body_op": build_body_op,
}


_MP_CACHE: dict = {}


def mass_properties(shape: Shape | None, density_kg_m3: float = 1000.0) -> dict:
    if shape is None:
        return {"volume": 0.0, "surface_area": 0.0, "mass": 0.0, "center_of_mass": None, "bodies": 0}
    # shapes are immutable and every rebuild makes new ones, so results are cached per shape
    key = (shape.wrapped.TShape().__hash__(), str(shape.wrapped.Location().Transformation().TranslationPart().Coord()), density_kg_m3)
    hit = _MP_CACHE.get(key)
    if hit is not None and hit[0].IsSame(shape.wrapped):
        return dict(hit[1])
    out = _mass_properties(shape, density_kg_m3)
    if len(_MP_CACHE) > 64:
        _MP_CACHE.clear()
    _MP_CACHE[key] = (shape.wrapped, out)
    return dict(out)


def _mass_properties(shape: Shape, density_kg_m3: float) -> dict:
    # OCC's default fixed-order Gauss rule is exact on analytic and swept faces but can be off by tenths of a
    # percent on B-spline faces; those get adaptive integration instead
    from build123d import GeomType
    freeform = any(f.geom_type in (GeomType.BSPLINE, GeomType.BEZIER, GeomType.OFFSET, GeomType.OTHER)
                   for f in shape.faces())
    props, aprops = GProp_GProps(), GProp_GProps()
    # volume (and centre of mass / inertia) come from the solids only: an open sheet has no volume
    solids = shape.solids()
    vol_shape = (solids[0] if len(solids) == 1 else Compound(list(solids))).wrapped if solids else None
    if freeform:
        if vol_shape is not None:
            BRepGProp.VolumeProperties_s(vol_shape, props, 1e-6)
        BRepGProp.SurfaceProperties_s(shape.wrapped, aprops, 1e-6)
    else:
        if vol_shape is not None:
            BRepGProp.VolumeProperties_s(vol_shape, props)
        BRepGProp.SurfaceProperties_s(shape.wrapped, aprops)
    vol = props.Mass()
    com = props.CentreOfMass()
    m = props.MatrixOfInertia()
    rho = density_kg_m3 * 1e-9  # kg/mm^3
    bb = shape.bounding_box()
    return {
        "volume": vol,  # mm^3
        "surface_area": aprops.Mass(),  # mm^2
        "mass": vol * rho * 1000.0,  # g
        "density": density_kg_m3,
        "center_of_mass": [com.X(), com.Y(), com.Z()],
        "inertia_at_com": [[m.Value(i, j) * rho * 1000.0 for j in (1, 2, 3)] for i in (1, 2, 3)],  # g*mm^2
        "bounding_box": {"min": [bb.min.X, bb.min.Y, bb.min.Z], "max": [bb.max.X, bb.max.Y, bb.max.Z]},
        "bodies": len(shape.solids()),
        "faces": len(shape.faces()),
        "edges": len(shape.edges()),
        "vertices": len(shape.vertices()),
    }


# advanced features (flex, mold tools, insert part, cosmetic threads, more surfaces) register themselves here
from . import advanced as _advanced  # noqa: E402

BUILDERS.update(_advanced.BUILDERS)
NAME_PREFIX.update(_advanced.NAME_PREFIX)
_filled_from_sketch = BUILDERS["filled_surface"]
BUILDERS["filled_surface"] = lambda c, p, n: (_advanced.build_filled_surface_edges if p.get("edges") else _filled_from_sketch)(c, p, n)

# sheet metal Rip and Vent
from . import sheet_more as _sheet_more  # noqa: E402

BUILDERS.update(_sheet_more.BUILDERS)
NAME_PREFIX.update(_sheet_more.NAME_PREFIX)
