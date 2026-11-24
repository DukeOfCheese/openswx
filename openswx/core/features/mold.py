"""Mold and body tools: split, intersect, delete / move / replace face and indent."""
from __future__ import annotations

from build123d import Shape, Solid, Vector

from openswx.core.geom import resolve_face, vec

from ._base import Ctx, FeatureError, _compound, resolve_plane
from .surfaces import _add_surface


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
