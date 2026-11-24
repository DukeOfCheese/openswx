"""Surface body builders: extruded, revolved, swept, lofted, boundary, filled, offset, knit, trim, thicken."""
from __future__ import annotations

from build123d import Location, Shape, Solid

from openswx.core.geom import resolve_face, vec

from ._base import Ctx, FeatureError, _path_wire, _sketch, apply_op, resolve_axis, resolve_plane


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
    from build123d import Face as _F, Wire
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
