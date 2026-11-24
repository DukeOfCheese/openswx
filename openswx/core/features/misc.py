"""Reference geometry, 3D sketches, curves and part import."""
from __future__ import annotations

import base64

from build123d import Axis, Compound, Plane, Shape, import_brep

from openswx.core.geom import plane_for_normal, resolve_edge, resolve_face, vec

from ._base import Ctx, FeatureError, _compound, _rot, _sketch, apply_op, body_key, decode_shape, resolve_axis, resolve_direction, resolve_plane, resolve_point, same_body


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
