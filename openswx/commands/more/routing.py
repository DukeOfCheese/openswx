"""Routing: pipes, standard pipe runs, fittings, cables and weld beads."""
from __future__ import annotations

from typing import Literal

from openswx.core.document import DocumentError
from openswx.commands.support import _feat
from openswx.session import APP, Vec3, command


def _virtual_component(shape, base: str, description: str, properties: dict | None = None) -> dict:
    """Add a shape to the active assembly as a virtual (embedded) part component, fixed where it is."""
    from openswx.core.document import PartDocument
    from openswx.core.features import encode_shape
    asm = APP.asm
    n = 1 + sum(1 for c in asm.components if c.name.rsplit("-", 1)[0].startswith(base))
    doc = PartDocument(f"{base}{n}")
    doc.properties["Description"] = description
    for k, v in (properties or {}).items():
        doc.properties[k] = str(v)
    doc.add_feature("import", {"brep": encode_shape(shape)})
    comp = asm.insert(doc, None, fixed=True)
    return {"component": comp.name, "volume": shape.volume, "assembly": asm.summary()}


def _filleted_path(points, bend_radius: float):
    """A 3D polyline with each corner rounded by bend_radius (a tube's bends)."""
    import math as _m
    from build123d import Edge as _E, Vector as _V, Wire as _W
    P = [_V(*p) for p in points]
    if len(P) < 2:
        raise DocumentError("a route needs at least two points")
    edges, start = [], P[0]
    for i in range(1, len(P) - 1):
        a, b, c = P[i - 1], P[i], P[i + 1]
        u1, u2 = (a - b).normalized(), (c - b).normalized()
        theta = _m.acos(max(-1.0, min(1.0, u1.dot(u2))))
        if bend_radius <= 0 or theta > _m.pi - 1e-6:
            edges.append(_E.make_line(start, b))
            start = b
            continue
        t = bend_radius / _m.tan(theta / 2)
        if t > min((a - b).length, (c - b).length) - 1e-6:
            raise DocumentError("the bend radius is too large for the route's segments")
        t1, t2 = b + u1 * t, b + u2 * t
        centre = b + (u1 + u2).normalized() * (bend_radius / _m.sin(theta / 2))
        mid = centre + (b - centre).normalized() * bend_radius
        edges.append(_E.make_line(start, t1))
        edges.append(_E.make_three_point_arc(t1, mid, t2))
        start = t2
    edges.append(_E.make_line(start, P[-1]))
    return _W(edges)


def _sweep_circle(path, outer_d: float, inner_d: float = 0.0):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
    from build123d import Face as _F, Plane as _P, Shape as _S, Wire as _W
    pl = _P(path.position_at(0), z_dir=path.tangent_at(0))
    prof = _F(_W.make_circle(outer_d / 2, pl), [_W.make_circle(inner_d / 2, pl)] if inner_d > 0 else [])
    mk = BRepOffsetAPI_MakePipe(path.wrapped, prof.wrapped)
    mk.Build()
    if not mk.IsDone():
        raise DocumentError("the route could not be swept")
    return _S.cast(mk.Shape())


@command(category="assembly")
def route_pipe(points: list[Vec3], outer_diameter: float, wall_thickness: float, bend_radius: float = 0.0) -> dict:
    """Routing > Pipe / Tube: a pipe through points (assembly coordinates) with bends of bend_radius at the
    corners (0: sharp mitred corners), added as a virtual part ("Pipe<n>")."""
    if wall_thickness <= 0 or wall_thickness * 2 >= outer_diameter:
        raise DocumentError("the wall must be thinner than half the outer diameter")
    path = _filleted_path(points, bend_radius)
    shape = _sweep_circle(path, outer_diameter, outer_diameter - 2 * wall_thickness)
    out = _virtual_component(shape, "Pipe", f"Pipe OD {outer_diameter:g} x {wall_thickness:g}, length {path.length:.1f}")
    out["length"] = path.length
    return out


@command(mutates=False, category="assembly")
def list_pipe_standards() -> dict:
    """The piping library: standard pipe sizes (NPS) with OD and schedule 40 / 80 walls, and the B16.9
    elbows, tees, reducers, caps and B16.5 class 150 weld-neck flanges available per size."""
    from openswx.manufacturing import piping
    return piping.catalog()


def _pipe_props(q: dict) -> dict:
    props = {"Size": f"NPS {q['size']}", "Schedule": q["schedule"], "OD": f"{q['od']:g}", "Wall": f"{q['wall']:g}",
             "Fitting": q["kind"]}
    if q.get("length") is not None:
        props["Length"] = f"{q['length']:.1f}"
    if q.get("angle") is not None:
        props["Angle"] = f"{q['angle']:.1f}"
    return props


@command(category="assembly")
def route_pipe_standard(points: list[Vec3], size: str, schedule: Literal["40", "80"] = "40",
                        elbows: Literal["long", "short"] = "long", bend_radius: float | None = None,
                        flanges: Literal["both", "start", "end", "none"] = "both") -> dict:
    """Routing > Pipe from the piping library: a route of standard pipe (NPS size, schedule 40 / 80) through
    points (assembly coordinates, mm). Every corner gets a butt-weld elbow of the corner's angle (B16.9 long or
    short radius, or bend_radius in mm), the straight runs are cut to length between the fittings, and class 150
    weld-neck flanges go on the chosen ends. Each pipe, elbow and flange is a virtual component with Size,
    Schedule, Length / Angle properties for the BOM; the result lists them with the cut list."""
    from openswx.manufacturing import piping
    ends = {"both": ("start", "end"), "start": ("start",), "end": ("end",), "none": ()}[flanges]
    try:
        parts = piping.layout(points, str(size), str(schedule), bend_radius if bend_radius else elbows, ends)
    except ValueError as exc:
        raise DocumentError(str(exc)) from None
    made = []
    for q in parts:
        base = {"pipe": "Pipe", "elbow": "Elbow", "flange": "Flange"}[q["kind"]]
        r = _virtual_component(q["shape"], base, q["description"], _pipe_props(q))
        made.append({"component": r["component"], "kind": q["kind"], "description": q["description"],
                     **({"length": round(q["length"], 2)} if "length" in q else {}),
                     **({"angle": round(q["angle"], 2)} if "angle" in q else {})})
    cut = [m["length"] for m in made if m["kind"] == "pipe"]
    return {"parts": made, "cut_list": cut, "pipe_length": round(sum(cut), 2),
            "elbows": sum(m["kind"] == "elbow" for m in made), "flanges": sum(m["kind"] == "flange" for m in made),
            "assembly": APP.asm.summary()}


@command(category="assembly")
def insert_fitting(kind: Literal["elbow", "tee", "reducer", "cap", "flange"], size: str, position: Vec3,
                   direction: Vec3 = (0, 0, 1), turn_direction: Vec3 | None = None, schedule: Literal["40", "80"] = "40",
                   angle: float = 90.0, elbow_radius: Literal["long", "short"] = "long", small_size: str | None = None) -> dict:
    """Insert one fitting from the piping library into the assembly as a virtual component. position is the
    fitting's connection end (for a tee: its centre) and direction the way it runs from there (the pipe's
    axis); turn_direction is where an elbow turns to / a tee's branch points (square to direction). angle for
    elbows; small_size for a reducer's small end."""
    from openswx.manufacturing import piping
    size, schedule = str(size), str(schedule)
    try:
        if kind == "elbow":
            shape, R, t = piping.elbow_shape(size, schedule, angle, elbow_radius)
            desc = f"Elbow {angle:g} deg {'LR' if elbow_radius == 'long' else 'SR'} NPS {size} sch {schedule}"
        elif kind == "tee":
            shape, _ = piping.tee_shape(size, schedule)
            desc = f"Tee equal NPS {size} sch {schedule}"
        elif kind == "reducer":
            if not small_size:
                raise ValueError("a reducer needs small_size")
            shape, _ = piping.reducer_shape(size, str(small_size), schedule)
            desc = f"Reducer concentric NPS {size} x {small_size} sch {schedule}"
        elif kind == "cap":
            shape, _ = piping.cap_shape(size, schedule)
            desc = f"Cap NPS {size} sch {schedule}"
        else:
            shape, _ = piping.flange_shape(size, schedule)
            desc = f"Flange WN class 150 NPS {size}"
        spec = piping.pipe(size, schedule)
    except ValueError as exc:
        raise DocumentError(str(exc)) from None
    placed = piping.frame(position, direction, turn_direction) * shape
    props = _pipe_props({"size": size, "schedule": schedule, "od": spec["od"], "wall": spec["wall"], "kind": kind,
                         **({"angle": angle} if kind == "elbow" else {})})
    return _virtual_component(placed, kind.capitalize(), desc, props)


@command(category="assembly")
def route_cable(points: list[Vec3], diameter: float) -> dict:
    """Routing > Electrical: a cable / harness of `diameter` along a smooth curve through points (assembly
    coordinates, e.g. from connector to connector), added as a virtual part ("Cable<n>")."""
    from build123d import Edge as _E, Vector as _V, Wire as _W
    if len(points) < 2:
        raise DocumentError("a cable needs at least two points")
    pts = [_V(*p) for p in points]
    path = _W([_E.make_spline(pts)]) if len(pts) > 2 else _W([_E.make_line(pts[0], pts[1])])
    shape = _sweep_circle(path, diameter)
    out = _virtual_component(shape, "Cable", f"Cable D {diameter:g}, length {path.length:.1f}")
    out["length"] = path.length
    return out


@command(category="assembly")
def weld_bead(face1: str, face2: str, size: float) -> dict:
    """Weld Bead: a fillet weld of leg `size` along where two faces meet (a tee or corner joint): in an assembly
    ('Comp/FaceN', added as a virtual part "Weld Bead<n>"), or in the active part (a feature)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
    from build123d import Compound as _C, Face as _F, Shape as _S, Wire as _W
    from openswx.core.geom import face_normal
    from openswx.commands.support import _measure_item
    f1, f2 = _measure_item(face1)[0], _measure_item(face2)[0]
    edges = [e for e in f1.edges() if any(e.is_same(x) for x in f2.edges())]  # faces of one body: their shared edges
    if not edges:  # separate bodies: where the faces cross / touch
        sec = BRepAlgoAPI_Section(f1.wrapped, f2.wrapped)
        sec.Build()
        edges = _C(sec.Shape()).edges()
    if not edges:
        raise DocumentError("the faces do not meet")
    beads = []
    for e in edges:
        p, t = e.position_at(0), e.tangent_at(0)
        n1, n2 = face_normal(f1, p), face_normal(f2, p)
        n1, n2 = n1 - t * n1.dot(t), n2 - t * n2.dot(t)  # across the joint
        tri = _F(_W.make_polygon([p, p + n2.normalized() * size, p + n1.normalized() * size], close=True))
        mk = BRepOffsetAPI_MakePipe(_W([e]).wrapped, tri.wrapped)
        mk.Build()
        beads.append(_S.cast(mk.Shape()))
    bead = beads[0] if len(beads) == 1 else _C(beads)
    length = sum(e.length for e in edges)
    if getattr(APP.active_doc, "kind", "part") == "assembly":
        out = _virtual_component(bead, "Weld Bead", f"Fillet weld {size:g} mm, length {length:.1f}")
        out["length"] = length
        return out
    from openswx.core.features import encode_shape
    return _feat("import", {"brep": encode_shape(bead)})


__all__ = ["route_pipe", "route_cable", "weld_bead", "list_pipe_standards", "route_pipe_standard",
           "insert_fitting"]
