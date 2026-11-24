"""Shared rebuild context, reference resolution, booleans and mass properties for the feature builders."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import io
import math

from build123d import Axis, Compound, Plane, Shape, Solid, Vector, import_brep
from build123d import export_brep
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from openswx.core.geom import STD_AXES, STD_PLANES, plane_for_normal, resolve_edge, resolve_face, vec


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
    unfold: dict = field(default_factory=dict)  # sheet metal: bend feature -> the Unfold that lays it flat


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


def _rot(v: Vector, axis: Axis, ang: float) -> Vector:
    k = axis.direction.normalized()
    a = math.radians(ang)
    return v * math.cos(a) + k.cross(v) * math.sin(a) + k * k.dot(v) * (1 - math.cos(a))


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


_MP_CACHE: dict = {}


def mass_properties(shape: Shape | None, density_kg_m3: float = 1000.0) -> dict:
    if shape is None:
        return {"volume": 0.0, "surface_area": 0.0, "mass": 0.0, "center_of_mass": None, "bodies": 0}
    # shapes are immutable and every rebuild makes new ones, so results are cached per shape. (hash(shape) is
    # OCCT's hash of the underlying shape; the TShape() wrapper's own __hash__ changed from call to call, so the
    # cache never hit and every model summary recomputed volume and area)
    key = (hash(shape), str(shape.wrapped.Location().Transformation().TranslationPart().Coord()), density_kg_m3)
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
