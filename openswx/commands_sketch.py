"""Sketch commands (same registry as session.py, which re-exports them).

Moved out of session.py; helpers come from it by name, and other commands through `_S` at call time."""
from __future__ import annotations

from typing import Literal

from . import session as _S
from .session import (  # noqa: F401
    APP, DocumentError, Path, SketchError, Vec2, Vec3, _add_projected, _dim_id, _edge, _face, _plane_spec, _sk,
    command, math,
)

__all__ = ["create_sketch", "edit_sketch_plane", "sketch_line", "sketch_centerline", "sketch_rectangle",
           "sketch_circle", "sketch_arc", "sketch_three_point_arc", "sketch_polygon", "sketch_slot", "sketch_point",
           "sketch_relation", "sketch_dimension", "sketch_move_dimension", "set_dimension", "sketch_delete",
           "sketch_ellipse", "sketch_conic", "sketch_spline", "sketch_equation_curve", "sketch_text",
           "sketch_linear_pattern", "sketch_circular_pattern", "sketch_convert_entities",
           "sketch_intersection_curve", "import_dxf", "export_sketch_dxf", "sketch_mirror", "sketch_offset",
           "sketch_fillet", "sketch_circle_3pt", "sketch_tangent_arc", "sketch_transform", "sketch_split",
           "sketch_chamfer", "sketch_trim", "sketch_extend", "sketch_set_construction", "sketch_drag_entity",
           "sketch_move_point", "set_sketch_visibility", "create_3d_sketch", "add_3d_sketch_segment"]


# ====================================================================== sketching
@command(category="sketch")
def create_sketch(plane: str | dict = "Front", name: str | None = None) -> dict:
    """Insert a new sketch on 'Front', 'Top', 'Right', a reference plane name (e.g. 'Plane1') or a
    planar face id ('Face5'). Returns the sketch name; add geometry with the sketch_* tools."""
    feat = APP.doc.add_feature("sketch", {"plane": _plane_spec(APP.doc, plane), "sketch": {}}, name=name)
    return {"sketch": feat["name"], "model": APP.doc.summary()}


@command(category="sketch")
def edit_sketch_plane(sketch: str, plane: str | dict) -> dict:
    """Edit Sketch Plane: move a sketch to another plane ('Front', 'Plane1') or planar face ('Face5'); its
    geometry keeps its sketch coordinates and the features built on it rebuild."""
    doc = APP.doc
    if doc.find(sketch)["type"] != "sketch":
        raise DocumentError(f"{sketch} is not a sketch")
    doc.edit_feature(sketch, {"plane": _plane_spec(doc, plane)})
    return doc.summary()


@command(category="sketch")
def sketch_line(sketch: str, start: Vec2 | str, end: Vec2 | str, construction: bool = False) -> dict:
    """Add a line. Endpoints are [x, y] in sketch coordinates (mm) or existing point ids (e.g. 'P3',
    'ORIGIN') to connect to them. Returns the entity id and its point ids."""
    def f(sk):
        eid = sk.add_line(start, end, construction)
        return {"entity": eid, "points": [sk.entities[eid]["p1"], sk.entities[eid]["p2"]]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_centerline(sketch: str, start: Vec2 | str, end: Vec2 | str) -> dict:
    """Add a construction centerline (used as the default revolve axis or for symmetry)."""
    def f(sk):
        eid = sk.add_centerline(start, end)
        return {"entity": eid, "points": [sk.entities[eid]["p1"], sk.entities[eid]["p2"]]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_rectangle(sketch: str, corner1: Vec2, corner2: Vec2, centered: bool = False) -> dict:
    """Add a corner rectangle between two corners, or a center rectangle when centered=True
    (corner1 = centre, corner2 = a corner). Horizontal/vertical relations are added automatically."""
    def f(sk):
        lines = sk.add_center_rectangle(corner1, corner2) if centered else sk.add_rectangle(corner1, corner2)
        return {"entities": lines, "points": [sk.entities[line]["p1"] for line in lines[:4]]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_circle(sketch: str, center: Vec2 | str, radius: float, construction: bool = False) -> dict:
    """Add a circle by centre ([x, y] or point id) and radius."""
    def f(sk):
        eid = sk.add_circle(center, radius, construction)
        return {"entity": eid, "center": sk.entities[eid]["c"]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_arc(sketch: str, center: Vec2 | str, start: Vec2 | str, end: Vec2 | str) -> dict:
    """Add a centre-point arc, counter-clockwise from start to end."""
    def f(sk):
        eid = sk.add_arc(center, start, end)
        e = sk.entities[eid]
        return {"entity": eid, "points": {"center": e["c"], "start": e["s"], "end": e["e"]}}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_three_point_arc(sketch: str, start: Vec2 | str, end: Vec2 | str, mid: Vec2) -> dict:
    """Add an arc through start, a point on the arc (mid) and end."""
    def f(sk):
        eid = sk.add_three_point_arc(start, end, mid)
        e = sk.entities[eid]
        return {"entity": eid, "points": {"center": e["c"], "start": e["s"], "end": e["e"]}}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_polygon(sketch: str, center: Vec2, radius: float, sides: int = 6, angle: float = 90.0) -> dict:
    """Add a regular polygon inscribed in a construction circle (polygon tool)."""
    return _sk(sketch, lambda sk: {"entities": sk.add_polygon(center, radius, sides, angle)})


@command(category="sketch")
def sketch_slot(sketch: str, center1: Vec2, center2: Vec2, width: float) -> dict:
    """Add a straight slot between two arc centres with the given width."""
    return _sk(sketch, lambda sk: {"entities": sk.add_slot(center1, center2, width)})


@command(category="sketch")
def sketch_point(sketch: str, position: Vec2) -> dict:
    """Add a sketch point (e.g. hole centres)."""
    def f(sk):
        eid = sk.add_point(position)
        return {"entity": eid, "point": sk.entities[eid]["p"]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_relation(sketch: str, relation: Literal["coincident", "horizontal", "vertical", "parallel", "perpendicular", "collinear", "equal", "tangent", "concentric", "midpoint", "fix", "symmetric"], refs: list[str]) -> dict:
    """Add a geometric relation between sketch items (entity ids like 'Line3', point ids like 'P2',
    or 'ORIGIN'). symmetric takes [point, point, line]."""
    return _sk(sketch, lambda sk: {"relation": sk.add_relation(relation, refs)})


@command(category="sketch")
def sketch_dimension(sketch: str, kind: Literal["distance", "hdistance", "vdistance", "length", "radius", "diameter", "angle"], refs: list[str], value: float | None = None,
                     position: Vec2 | None = None, supplement: bool = False) -> dict:
    """Add a driving dimension (Smart Dimension). Units mm/degrees. Omit value to keep the current
    size. If it would over-define the sketch it is added as a driven (reference) dimension.
    position: where the label is placed (sketch [x, y]); supplement: for an angle, dimension the other
    side of the lines (180 minus the angle between their directions)."""
    def f(sk):
        d = sk.add_dimension(kind, refs, value, position, supplement)
        return {"dimension": f"{d['id']}@{sketch}", "value": d["value"], "driven": d["driven"]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_move_dimension(sketch: str, dimension: str, position: Vec2) -> dict:
    """Move a sketch dimension's label to position (sketch [x, y]); the geometry does not change."""
    _, did = _dim_id(dimension)
    return _sk(sketch, lambda sk: sk.move_dimension(did, position))


@command(category="sketch")
def set_dimension(dimension: str, value: float, sketch: str | None = None,
                  configurations: str | list[str] | None = None) -> dict:
    """Change a dimension value, e.g. set_dimension('D1@Sketch1', 42). Rebuilds the model. configurations
    (as in the Modify box): None or 'all' sets it in all configurations (the base value; overrides
    of it are removed), 'this' only in the active configuration, or a list of configuration names."""
    sk_name, did = _dim_id(dimension)
    sk_name = sk_name or sketch
    if not sk_name:
        raise DocumentError("give the dimension as 'D1@Sketch1' or pass sketch=")
    doc = APP.doc
    target = f"{did}@{sk_name}"
    if configurations in (None, "all"):
        out = _sk(sk_name, lambda sk: sk.set_dimension(did, value))
        key = doc._config_key(target)
        if any(key in c.get("values", {}) for c in doc.configurations.values()):
            for c in doc.configurations.values():
                c.get("values", {}).pop(key, None)
            doc.rebuild()
        return out
    names = [doc.active_config] if configurations == "this" else list(configurations)
    for n in names:
        doc.set_configuration_value(n, target, value)
    return {"configurations": names, "model": doc.summary()}


@command(category="sketch")
def sketch_delete(sketch: str, item: str) -> dict:
    """Delete a sketch entity, relation (R#) or dimension (D#)."""
    return _sk(sketch, lambda sk: sk.delete(item))


@command(category="sketch")
def sketch_ellipse(sketch: str, center: Vec2 | str, major_end: Vec2 | str, minor_radius: float,
                   construction: bool = False) -> dict:
    """Add an ellipse: centre, one end of the major axis ([x, y] or point ids) and the minor radius."""
    return _sk(sketch, lambda sk: {"entity": sk.add_ellipse(center, major_end, minor_radius, construction)})


@command(category="sketch")
def sketch_conic(sketch: str, start: Vec2 | str, end: Vec2 | str, apex: Vec2 | str, rho: float = 0.5,
                 construction: bool = False) -> dict:
    """Add a conic from start to end, tangent at both ends to the lines towards `apex` ([x, y] or point ids);
    rho 0..1 (0.5 parabola, less an ellipse arc, more a hyperbola)."""
    return _sk(sketch, lambda sk: {"entity": sk.add_conic(start, end, apex, rho, construction)})


@command(category="sketch")
def sketch_spline(sketch: str, points: list[Vec2 | str], closed: bool = False, construction: bool = False) -> dict:
    """Add an interpolating spline through `points` ([x, y] or point ids); closed=True makes it periodic."""
    def f(sk):
        eid = sk.add_spline(points, closed, construction)
        return {"entity": eid, "points": sk.entities[eid]["pts"]}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_equation_curve(sketch: str, x: str, y: str, t_start: float = 0.0, t_end: float = 1.0, points: int = 25,
                          closed: bool = False, construction: bool = False) -> dict:
    """Equation Driven Curve (parametric): a spline through `points` samples of x(t), y(t) for t from t_start
    to t_end. The expressions use `t`, the part's global variables, + - * / ^ and the equation functions
    (sin, cos, tan take degrees, like every openswx equation; sqr, abs, min, max; pi). Examples: an involute
    x='10*(cos(t)+t*pi/180*sin(t))', y='10*(sin(t)-t*pi/180*cos(t))'; a parabola x='t', y='t^2/20' (the
    explicit form y = f(x) is x='t'). closed=True joins the end to the start."""
    from .equations import EquationError, Evaluator
    if points < 3 or points > 500:
        raise DocumentError("points must be between 3 and 500")
    if t_end == t_start:
        raise DocumentError("t_start and t_end must differ")
    ev = Evaluator(APP.doc.variables)
    pts = []
    n = points if not closed else points + 1
    for i in range(points):
        ev.globals["t"] = t_start + (t_end - t_start) * i / (n - 1)
        try:
            px, py = float(ev.eval(x)), float(ev.eval(y))
        except (EquationError, ZeroDivisionError, ValueError, OverflowError) as exc:
            raise DocumentError(f"cannot evaluate the curve at t = {ev.globals['t']:g}: {exc}") from exc
        if not (math.isfinite(px) and math.isfinite(py)):
            raise DocumentError(f"the curve is not finite at t = {ev.globals['t']:g}")
        if pts and math.dist(pts[-1], (px, py)) < 1e-9:
            raise DocumentError(f"the curve does not move at t = {ev.globals['t']:g} (two samples coincide)")
        pts.append([px, py])

    def f(sk):
        eid = sk.add_spline(pts, closed, construction)
        return {"entity": eid, "points": sk.entities[eid]["pts"], "samples": len(pts)}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_text(sketch: str, position: Vec2, text: str, height: float = 10.0, font: str = "Arial",
                angle: float = 0.0) -> dict:
    """Sketch Text: letter outlines at `position` (baseline start), cap height `height` mm, rotated `angle`
    degrees. The closed letter contours extrude/cut like any profile (counters become holes)."""
    return _sk(sketch, lambda sk: {"entity": sk.add_text(position, text, height, font, angle)})


@command(category="sketch")
def sketch_linear_pattern(sketch: str, entities: list[str], spacing: float, count: int, angle: float = 0.0,
                          spacing2: float = 0.0, count2: int = 1, angle2: float = 90.0) -> dict:
    """Linear Sketch Pattern: `count` instances `spacing` apart along `angle` degrees (sketch X = 0), with
    an optional second direction (`spacing2`, `count2`, `angle2`)."""
    return _sk(sketch, lambda sk: {"entities": sk.linear_pattern_entities(entities, spacing, count, angle,
                                                                          spacing2, count2, angle2)})


@command(category="sketch")
def sketch_circular_pattern(sketch: str, entities: list[str], center: Vec2 | str, count: int,
                            angle: float = 360.0) -> dict:
    """Circular Sketch Pattern: `count` instances about `center` ([x, y] or point id) over `angle` degrees."""
    return _sk(sketch, lambda sk: {"entities": sk.circular_pattern_entities(entities, center, count, angle)})


@command(category="sketch")
def sketch_convert_entities(sketch: str, faces: list[str] | None = None, edges: list[str] | None = None) -> dict:
    """Convert Entities: project model edges (`edges` 'EdgeN') and/or the boundary edges of `faces`
    ('FaceN') onto the sketch plane as fixed sketch geometry."""
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body to convert edges from")
    if sketch not in doc.ctx.sketches:
        raise DocumentError(f"unknown sketch {sketch!r}")
    plane = doc.ctx.sketches[sketch][1]
    from .geom import resolve_edge, resolve_face
    picked = []
    for f in faces or []:
        picked += resolve_face(doc.body, _face(doc, f)).edges()
    for e in edges or []:
        picked.append(resolve_edge(doc.body, _edge(doc, e)))
    if not picked:
        raise DocumentError("select faces or edges to convert")
    return _sk(sketch, lambda sk: {"entities": _add_projected(sk, plane, picked, doc.body)})


@command(category="sketch")
def sketch_intersection_curve(sketch: str) -> dict:
    """Intersection Curve: add the section of the part body with the sketch plane as fixed sketch geometry."""
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body to intersect")
    if sketch not in doc.ctx.sketches:
        raise DocumentError(f"unknown sketch {sketch!r}")
    plane = doc.ctx.sketches[sketch][1]
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from build123d import Compound as _C
    o, n = plane.origin, plane.z_dir
    sec = BRepAlgoAPI_Section(doc.body.wrapped, gp_Pln(gp_Pnt(o.X, o.Y, o.Z), gp_Dir(n.X, n.Y, n.Z)))
    sec.Build()
    edges = _C(sec.Shape()).edges()
    if not edges:
        raise DocumentError("the sketch plane does not cut the body")
    return _sk(sketch, lambda sk: {"entities": _add_projected(sk, plane, list(edges))})


@command(category="sketch")
def import_dxf(path: str, sketch: str | None = None, plane: str = "Front", scale: float = 1.0,
               offset: Vec2 | None = None) -> dict:
    """File > Open DXF into a sketch: LINE / CIRCLE / ARC / (LW)POLYLINE with bulges / SPLINE / ELLIPSE / POINT
    from model space, into `sketch` (or a new sketch on `plane`), scaled and offset (mm)."""
    from .dxf_io import dwg_to_dxf, import_into_sketch
    if not Path(path).exists():
        raise FileNotFoundError(path)
    if Path(path).suffix.lower() == ".dwg":  # closed format: convert through the ODA File Converter
        import tempfile
        path = dwg_to_dxf(path, tempfile.mkdtemp())
    name = sketch or _S.create_sketch(plane)["sketch"]
    return _sk(name, lambda sk: {"sketch": name, "entities": import_into_sketch(sk, path, scale, tuple(offset or (0, 0)))})


@command(mutates=False, category="sketch")
def export_sketch_dxf(sketch: str, path: str, include_construction: bool = False) -> dict:
    """Save a sketch as DXF (local sketch XY, mm); a .dwg path converts through the ODA File Converter."""
    from .dxf_io import dxf_to_dwg, export_sketch
    doc = APP.doc
    if sketch not in doc.ctx.sketches:
        raise DocumentError(f"unknown sketch {sketch!r}")
    want_dwg = Path(path).suffix.lower() == ".dwg"
    p = Path(path).with_suffix(".dxf")
    export_sketch(doc.ctx.sketches[sketch][0], str(p), include_construction)
    if want_dwg:
        dwg = dxf_to_dwg(str(p), str(p.with_suffix(".dwg")))
        p.unlink(missing_ok=True)
        return {"path": str(Path(dwg).resolve())}
    return {"path": str(p.resolve())}


@command(category="sketch")
def sketch_mirror(sketch: str, entities: list[str], about: str, copy: bool = True) -> dict:
    """Mirror Entities: copy entities across a sketch line/centerline (`about`), adding symmetric relations.
    copy=False removes the originals (the Copy option off)."""
    def f(sk):
        new = sk.mirror_entities(entities, about)
        if not copy:
            sk.remove_entities(entities)
        return {"entities": new}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_offset(sketch: str, entities: list[str], distance: float, reverse: bool = False,
                  bidirectional: bool = False) -> dict:
    """Offset Entities: circles/arcs (concentric) or a connected chain of lines; positive distance offsets a
    closed loop outward, negative inward (reverse flips the side). Adds parallel/concentric relations and
    driving distances. bidirectional offsets to both sides."""
    d = -distance if reverse else distance
    def f(sk):
        out = sk.offset_entities(entities, d)
        if bidirectional:
            out += sk.offset_entities(entities, -d)
        return {"entities": out}
    return _sk(sketch, f)


@command(category="sketch")
def sketch_fillet(sketch: str, point: str, radius: float) -> dict:
    """Sketch Fillet: round the corner at `point` (shared by two lines) with a tangent arc of `radius`."""
    return _sk(sketch, lambda sk: {"entity": sk.fillet_corner(point, radius)})


@command(category="sketch")
def sketch_circle_3pt(sketch: str, p1: Vec2 | str, p2: Vec2 | str, p3: Vec2 | str, construction: bool = False) -> dict:
    """Perimeter Circle: the circle through three points ([x, y] or point ids)."""
    return _sk(sketch, lambda sk: {"entity": sk.add_circle_3pt(p1, p2, p3, construction)})


@command(category="sketch")
def sketch_tangent_arc(sketch: str, start: str, end: Vec2 | str, construction: bool = False) -> dict:
    """Tangent Arc: from `start` (a point id at the end of a line or arc) to `end`, leaving tangent to that
    line / arc (a tangent relation is added)."""
    return _sk(sketch, lambda sk: {"entity": sk.add_tangent_arc(start, end, construction)})


@command(category="sketch")
def sketch_transform(sketch: str, entities: list[str], move: Vec2 | None = None, rotate: float = 0.0,
                     center: Vec2 | str | None = None, scale: float = 1.0, copy: bool = False) -> dict:
    """Move / Copy / Rotate / Scale Entities: scale by `scale` and turn by `rotate` degrees about `center`
    ([x, y] or a point id; default the origin), then shift by `move` [dx, dy]. copy=True keeps the originals
    and returns the new entities."""
    return _sk(sketch, lambda sk: {"entities": sk.transform_entities(entities, move or (0.0, 0.0), rotate,
                                                                      center if center is not None else (0.0, 0.0), scale, copy)})


@command(category="sketch")
def sketch_split(sketch: str, entity: str, at: Vec2) -> dict:
    """Split Entities: cut a line or arc in two at the point `at`."""
    return _sk(sketch, lambda sk: {"entities": sk.split_entity(entity, at)})


@command(category="sketch")
def sketch_chamfer(sketch: str, point: str, distance: float, distance2: float | None = None,
                   angle: float | None = None) -> dict:
    """Sketch Chamfer: cut the corner at `point` (shared by two lines) with a line `distance` back along each
    line (distance2: a different distance on the second; or angle: degrees from the first line)."""
    return _sk(sketch, lambda sk: {"entity": sk.chamfer_corner(point, distance, distance2, angle)})


@command(category="sketch")
def sketch_trim(sketch: str, entity: str, pick: Vec2) -> dict:
    """Trim Entities (power trim): remove the portion of `entity` around the point `pick` [x, y] up to the
    nearest intersections with other sketch entities (deletes it if nothing crosses it)."""
    return _sk(sketch, lambda sk: {"entities": sk.trim(entity, pick)})


@command(category="sketch")
def sketch_extend(sketch: str, entity: str, pick: Vec2) -> dict:
    """Extend Entities: lengthen the end of line `entity` nearest `pick` to the next entity it meets."""
    return _sk(sketch, lambda sk: {"entity": sk.extend(entity, pick)})


@command(category="sketch")
def sketch_set_construction(sketch: str, entities: list[str], construction: bool = True) -> dict:
    """Toggle 'For construction' on sketch entities (construction geometry is ignored by features)."""
    def f(sk):
        for eid in entities:
            if eid not in sk.entities:
                raise SketchError(f"unknown sketch entity {eid}")
            sk.entities[eid]["construction"] = construction
    return _sk(sketch, f)


@command(category="sketch", mcp=False)
def sketch_drag_entity(sketch: str, entity: str, frm: Vec2, to: Vec2) -> dict:
    """Drag a sketch entity (used by the UI): a circle or arc grabbed on its curve resizes, anything else
    moves by to - frm; the sketch is re-solved."""
    return _sk(sketch, lambda sk: sk.drag_entity(entity, frm, to))


@command(category="sketch", mcp=False)
def sketch_move_point(sketch: str, point: str, position: Vec2) -> dict:
    """Drag a sketch point to a new position and re-solve (used by the UI)."""
    def f(sk):
        if point not in sk.points:
            raise SketchError(f"unknown point {point}")
        sk.points[point] = [float(position[0]), float(position[1])]
        return None
    return _sk(sketch, f)


@command(category="sketch")
def set_sketch_visibility(sketch: str, hidden: bool) -> dict:
    """Show or hide a sketch in the graphics area."""
    APP.doc.edit_feature(sketch, {"hidden": hidden})
    return APP.doc.summary()


@command(category="sketch")
def create_3d_sketch(points: list[Vec3], kind: Literal["line", "spline"] = "line", closed: bool = False,
                     name: str | None = None) -> dict:
    """3D Sketch: a chain through [x, y, z] points as connected lines (kind 'line') or one spline. Use its
    name as a sweep path. Add more chains with add_3d_sketch_segment."""
    feat = APP.doc.add_feature("sketch3d", {"segments": [{"points": points, "kind": kind, "closed": closed}]}, name=name)
    return {"sketch": feat["name"], "info": APP.doc.ctx.info.get(feat["name"]), "model": APP.doc.summary()}


@command(category="sketch")
def add_3d_sketch_segment(sketch: str, points: list[Vec3], kind: Literal["line", "spline"] = "line",
                          closed: bool = False) -> dict:
    """Append a line chain or spline through [x, y, z] points to an existing 3D sketch."""
    doc = APP.doc
    f = doc.find(sketch)
    if f["type"] != "sketch3d":
        raise DocumentError(f"{sketch} is not a 3D sketch")
    doc.edit_feature(sketch, {"segments": f["params"]["segments"] + [{"points": points, "kind": kind, "closed": closed}]})
    return {"sketch": sketch, "info": doc.ctx.info.get(sketch), "model": doc.summary()}
