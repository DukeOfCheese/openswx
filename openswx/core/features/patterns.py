"""Pattern and mirror feature builders."""
from __future__ import annotations

import math

from build123d import Compound, Location, Plane, Vector

from openswx.core.geom import resolve_face

from ._base import Ctx, FeatureError, _compound, _path_wire, _sketch, apply_op, resolve_axis, resolve_direction, resolve_plane


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
