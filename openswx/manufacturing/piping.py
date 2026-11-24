"""Piping library for Routing: standard pipe sizes, butt-weld fittings and weld-neck flanges, and the layout of
a pipe route through points (straight runs cut to length, an elbow at every corner, optional end flanges).

Dimensions are nominal values in mm after ASME B36.10 (pipe OD / wall, schedules 40 and 80), ASME B16.9
(long / short radius elbows, equal tees, concentric reducers, caps) and ASME B16.5 class 150 (weld-neck
flanges). They are for layout and bills of materials; check the standard before manufacture."""
from __future__ import annotations

import math

# NPS: (OD, wall sch 40, wall sch 80)
PIPE = {
    "1/4": (13.7, 2.24, 3.02), "3/8": (17.1, 2.31, 3.20), "1/2": (21.3, 2.77, 3.73), "3/4": (26.7, 2.87, 3.91),
    "1": (33.4, 3.38, 4.55), "1-1/4": (42.2, 3.56, 4.85), "1-1/2": (48.3, 3.68, 5.08), "2": (60.3, 3.91, 5.54),
    "2-1/2": (73.0, 5.16, 7.01), "3": (88.9, 5.49, 7.62), "4": (114.3, 6.02, 8.56), "5": (141.3, 6.55, 9.53),
    "6": (168.3, 7.11, 10.97), "8": (219.1, 8.18, 12.70), "10": (273.0, 9.27, 15.09), "12": (323.8, 10.31, 17.48),
}
SCHEDULES = ("40", "80")
# B16.9 centre-to-end: 90 deg long radius (= bend radius), 90 deg short radius, equal tee (run and branch),
# concentric reducer end-to-end, cap height
ELBOW_LR = {"1/2": 38, "3/4": 38, "1": 38, "1-1/4": 48, "1-1/2": 57, "2": 76, "2-1/2": 95, "3": 114, "4": 152,
            "5": 190, "6": 229, "8": 305, "10": 381, "12": 457}
ELBOW_SR = {"1": 25, "1-1/4": 32, "1-1/2": 38, "2": 51, "2-1/2": 64, "3": 76, "4": 102, "5": 127, "6": 152,
            "8": 203, "10": 254, "12": 305}
TEE = {"1/2": 25, "3/4": 29, "1": 38, "1-1/4": 48, "1-1/2": 57, "2": 64, "2-1/2": 76, "3": 86, "4": 105, "5": 124,
       "6": 143, "8": 178, "10": 216, "12": 254}
REDUCER = {"3/4": 38, "1": 51, "1-1/4": 51, "1-1/2": 64, "2": 76, "2-1/2": 89, "3": 89, "4": 102, "5": 127, "6": 140,
           "8": 152, "10": 178, "12": 203}
CAP = {"1/2": 25, "3/4": 25, "1": 38, "1-1/4": 38, "1-1/2": 38, "2": 38, "2-1/2": 38, "3": 51, "4": 64, "5": 76,
       "6": 89, "8": 102, "10": 127, "12": 152}
# B16.5 class 150 weld neck: flange OD, thickness, bolt circle, bolts, bolt hole, hub OD at the flange, length
FLANGE_150 = {
    "1/2": (90, 11.2, 60.3, 4, 15.7, 30, 46), "3/4": (100, 12.7, 69.9, 4, 15.7, 38, 51),
    "1": (110, 14.3, 79.4, 4, 15.7, 49, 54), "1-1/4": (115, 15.9, 88.9, 4, 15.7, 59, 56),
    "1-1/2": (125, 17.5, 98.4, 4, 15.7, 65, 60), "2": (150, 19.1, 120.7, 4, 19.1, 78, 62),
    "2-1/2": (180, 22.3, 139.7, 4, 19.1, 90, 68), "3": (190, 23.9, 152.4, 4, 19.1, 108, 68),
    "4": (230, 23.9, 190.5, 8, 19.1, 135, 75), "5": (255, 23.9, 215.9, 8, 22.4, 164, 87),
    "6": (280, 25.4, 241.3, 8, 22.4, 192, 87), "8": (345, 28.6, 298.5, 8, 22.4, 246, 100),
    "10": (405, 30.2, 362.0, 12, 25.4, 305, 100), "12": (485, 31.8, 431.8, 12, 25.4, 365, 113),
}
FITTINGS = ("elbow", "tee", "reducer", "cap", "flange")


def pipe(size: str, schedule: str = "40") -> dict:
    """OD, wall and ID (mm) of a standard pipe size (NPS, e.g. '2' or '1-1/2')."""
    size = str(size)
    if size not in PIPE:
        raise ValueError(f"unknown pipe size {size!r}: one of {', '.join(PIPE)}")
    if str(schedule) not in SCHEDULES:
        raise ValueError(f"schedule must be one of {', '.join(SCHEDULES)}")
    od, w40, w80 = PIPE[size]
    wall = w40 if str(schedule) == "40" else w80
    return {"size": size, "schedule": str(schedule), "od": od, "wall": wall, "id": od - 2 * wall}


def catalog() -> dict:
    """Everything the library holds, per size: pipe OD / walls and the fittings available."""
    out = []
    for s, (od, w40, w80) in PIPE.items():
        fl = FLANGE_150.get(s)
        out.append({"size": s, "od": od, "wall_sch40": w40, "wall_sch80": w80, "elbow_lr": ELBOW_LR.get(s),
                    "elbow_sr": ELBOW_SR.get(s), "tee": TEE.get(s), "reducer": REDUCER.get(s), "cap": CAP.get(s),
                    "flange_150": dict(zip(("od", "thickness", "bolt_circle", "bolts", "bolt_hole", "hub", "length"), fl))
                    if fl else None})
    return {"sizes": out, "schedules": list(SCHEDULES), "fittings": list(FITTINGS),
            "standards": "ASME B36.10 pipe, B16.9 butt-weld fittings, B16.5 class 150 weld-neck flanges (nominal)"}


def _need(table: dict, size: str, what: str):
    if size not in table:
        raise ValueError(f"no standard {what} for size {size}")
    return table[size]


def bend_radius(size: str, radius: str = "long") -> float:
    return float(_need(ELBOW_LR if radius == "long" else ELBOW_SR, size, f"{radius} radius elbow"))


def _zcyl(r: float, length: float, z0: float = 0.0):
    """Solid cylinder along +Z from z0."""
    from build123d import Align, Cylinder, Pos
    return Pos(0, 0, z0) * Cylinder(r, length, align=(Align.CENTER, Align.CENTER, Align.MIN))


def _revolve(points: list[tuple[float, float]]):
    """Revolve a closed (r, z) polygon (in the XZ plane) a full turn about Z."""
    from build123d import Axis, Polyline, make_face, revolve
    return revolve(make_face(Polyline(*[(r, 0, z) for r, z in points], close=True)), Axis.Z, 360)


def pipe_shape(size: str, schedule: str, length: float):
    """A straight pipe of `length` along +Z from the origin."""
    p = pipe(size, schedule)
    return _zcyl(p["od"] / 2, length) - _zcyl(p["id"] / 2, length + 2, -1)


def elbow_shape(size: str, schedule: str, angle: float = 90.0, radius: str | float = "long"):
    """An elbow: enters along +Z with its end face on z = 0 centred on the origin, and turns by `angle`
    towards +X about a bend centre at (R, 0, 0). Returns (shape, bend radius, centre-to-end)."""
    from build123d import Axis, Circle, revolve
    p = pipe(size, schedule)
    R = float(radius) if not isinstance(radius, str) else bend_radius(size, radius)
    if R <= p["od"] / 2:
        raise ValueError("the bend radius must exceed the pipe radius")
    shape = revolve(Circle(p["od"] / 2) - Circle(p["id"] / 2), Axis((R, 0, 0), (0, 1, 0)), angle)
    return shape, R, R * math.tan(math.radians(angle) / 2)


def tee_shape(size: str, schedule: str):
    """An equal tee: the run along Z centred on the origin (ends at z = +-C), the branch along +X to x = C.
    Returns (shape, C)."""
    from build123d import Rot
    p = pipe(size, schedule)
    C = float(_need(TEE, size, "tee"))
    ro, ri = p["od"] / 2, p["id"] / 2
    outer = _zcyl(ro, 2 * C, -C) + Rot(0, 90, 0) * _zcyl(ro, C)
    bore = _zcyl(ri, 2 * C + 2, -C - 1) + Rot(0, 90, 0) * _zcyl(ri, C + 1)
    return outer - bore, C


def reducer_shape(size: str, small: str, schedule: str):
    """A concentric reducer from `size` (at z = 0) to `small` (at z = H) along +Z. Returns (shape, H)."""
    big, sm = pipe(size, schedule), pipe(small, schedule)
    if sm["od"] >= big["od"]:
        raise ValueError("the small end must be smaller than the large end")
    H = float(_need(REDUCER, size, "reducer"))
    s = 0.2 * H  # straight lengths at both ends, a cone between
    outer = [(big["od"] / 2, 0), (big["od"] / 2, s), (sm["od"] / 2, H - s), (sm["od"] / 2, H)]
    inner = [(sm["id"] / 2, H), (sm["id"] / 2, H - s), (big["id"] / 2, s), (big["id"] / 2, 0)]
    return _revolve(outer + inner), H


def cap_shape(size: str, schedule: str):
    """A cap: open end on z = 0, a 2:1 elliptical head closing it at z = E. Returns (shape, E)."""
    p = pipe(size, schedule)
    E = float(_need(CAP, size, "cap"))
    ro, ri, t = p["od"] / 2, p["id"] / 2, p["wall"]
    ho = min(ro / 2, E * 0.6)  # outer head depth (2:1 ellipse)
    hi = ho - t
    sl = E - ho  # straight length before the head
    n = 16
    angs = [math.pi / 2 * k / n for k in range(n + 1)]
    outer = [(ro, 0.0)] + [(ro * math.cos(a), sl + ho * math.sin(a)) for a in angs[:-1]] + [(0.0, E)]
    inner = [(0.0, sl + hi)] + [(ri * math.cos(a), sl + hi * math.sin(a)) for a in reversed(angs[:-1])] + [(ri, 0.0)]
    return _revolve(outer + inner), E


def flange_shape(size: str, schedule: str):
    """A class 150 weld-neck flange: its face on z = 0, the hub running along +Z to z = Y where the pipe is
    welded on. Returns (shape, Y)."""
    from build123d import PolarLocations
    p = pipe(size, schedule)
    O, tf, bc, nb, hole, X, Y = _need(FLANGE_150, size, "class 150 flange")
    body = _revolve([(p["id"] / 2, 0), (O / 2, 0), (O / 2, tf), (X / 2, tf), (p["od"] / 2, Y), (p["id"] / 2, Y)])
    for loc in PolarLocations(bc / 2, nb):
        body = body - loc * _zcyl(hole / 2, tf + 2, -1)
    return body, float(Y)


def frame(origin, z_dir, x_dir=None):
    """A Location whose +Z is z_dir at origin (x_dir: where local +X points, made square to z_dir)."""
    from build123d import Location, Plane, Vector
    z = Vector(*z_dir).normalized()
    if x_dir is None:
        ref = Vector(0, 0, 1) if abs(z.Z) < 0.9 else Vector(1, 0, 0)
        x = ref.cross(z).normalized()
    else:
        x = Vector(*x_dir)
        x = (x - z * x.dot(z)).normalized()
    return Location(Plane(Vector(*origin), x_dir=x, z_dir=z))


def layout(points, size: str, schedule: str = "40", radius: str | float = "long", flanges=("start", "end")) -> list[dict]:
    """The parts of a pipe route through points, in route order: [{kind, shape (placed), description, ...}].
    Every corner gets an elbow of the corner's angle (bend radius from B16.9 for 'long' / 'short', or a number
    in mm); the straight runs between are cut to length; weld-neck flanges sit at the requested ends
    ('start' / 'end') with their face on the end point."""
    from build123d import Vector
    P = [Vector(*p) for p in points]
    P = [q for i, q in enumerate(P) if i == 0 or (q - P[i - 1]).length > 1e-9]
    if len(P) < 2:
        raise ValueError("a route needs at least two distinct points")
    spec = pipe(size, schedule)
    R = float(radius) if not isinstance(radius, str) else bend_radius(size, radius)
    kind = ("LR" if radius == "long" else "SR") if isinstance(radius, str) else f"R{R:g}"
    items = []  # (sort key, part)
    cut = [[0.0, 0.0] for _ in range(len(P) - 1)]  # per segment: length taken by fittings at its start, its end
    for i in range(1, len(P) - 1):
        a, b, c = P[i - 1], P[i], P[i + 1]
        d1, d2 = (b - a).normalized(), (c - b).normalized()
        ang = math.degrees(math.acos(max(-1.0, min(1.0, d1.dot(d2)))))
        if ang < 1e-6:
            continue
        if ang > 179.0:
            raise ValueError(f"the route turns back on itself at point {i + 1}")
        el, _, t = elbow_shape(size, schedule, ang, R)
        cut[i - 1][1] += t
        cut[i][0] += t
        side = (d2 - d1 * d2.dot(d1)).normalized()  # the way the route turns, square to the incoming run
        items.append((i, {"kind": "elbow", "angle": ang, "bend_radius": R, "shape": frame(b - d1 * t, d1, side) * el,
                          "description": f"Elbow {ang:.0f} deg {kind} NPS {size} sch {schedule}"}))
    ends = set(flanges or ())
    if ends - {"start", "end"}:
        raise ValueError("flanges are 'start' and / or 'end'")
    if ends:
        fl, Y = flange_shape(size, schedule)
        if "start" in ends:
            cut[0][0] += Y
            items.append((-1, {"kind": "flange", "shape": frame(P[0], P[1] - P[0]) * fl,
                               "description": f"Flange WN class 150 NPS {size}"}))
        if "end" in ends:
            cut[-1][1] += Y
            items.append((len(P), {"kind": "flange", "shape": frame(P[-1], P[-2] - P[-1]) * fl,
                                   "description": f"Flange WN class 150 NPS {size}"}))
    for i in range(len(P) - 1):
        a, b = P[i], P[i + 1]
        d = (b - a).normalized()
        L = (b - a).length - cut[i][0] - cut[i][1]
        if L < -1e-6:
            raise ValueError(f"segment {i + 1} ({(b - a).length:.1f} mm) is shorter than its fittings need "
                             f"({cut[i][0] + cut[i][1]:.1f} mm)")
        if L > 1e-6:
            items.append((i + 0.5, {"kind": "pipe", "length": L, "shape": frame(a + d * cut[i][0], d) * pipe_shape(size, schedule, L),
                                    "description": f"Pipe NPS {size} sch {schedule} x {L:.1f}"}))
    out = [q for _, q in sorted(items, key=lambda kv: kv[0])]
    for q in out:
        q.update(size=size, schedule=str(schedule), od=spec["od"], wall=spec["wall"])
    return out
