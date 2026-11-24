"""2D parametric sketch: entities, relations, dimensions, constraint solver and profile extraction.

Coordinates are in the sketch plane (mm). The sketch origin "ORIGIN" is a fixed point at (0, 0).
Status mirrors SOLIDWORKS colouring: under_defined (blue), fully_defined (black), over_defined (red).
"""
from __future__ import annotations

import copy
import math
import os

import numpy as np
from build123d import Axis, Edge, Face, Plane, Vector, Wire
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.TopAbs import TopAbs_IN
from scipy.optimize import least_squares

ORIGIN = "ORIGIN"
RELATIONS = {
    "coincident", "horizontal", "vertical", "parallel", "perpendicular", "collinear", "equal",
    "tangent", "concentric", "midpoint", "fix", "symmetric",
}
DIMENSIONS = {"distance", "hdistance", "vdistance", "length", "radius", "diameter", "angle"}


class SketchError(ValueError):
    pass


def _pt(v) -> list[float]:
    return [float(v[0]), float(v[1])]


_PROFILES: dict = {}  # (sketch contents, plane) -> regions, oldest first


class Sketch:
    def __init__(self, data: dict | None = None):
        d = copy.deepcopy(data) if data else {}
        self.points: dict[str, list[float]] = d.get("points", {})
        self.entities: dict[str, dict] = d.get("entities", {})
        self.relations: list[dict] = d.get("relations", [])
        self.dimensions: list[dict] = d.get("dimensions", [])
        self.counter: int = d.get("counter", 0)
        self.status: dict = d.get("status", {})

    # ------------------------------------------------------------------ serialization
    def to_dict(self) -> dict:
        return copy.deepcopy({
            "points": self.points, "entities": self.entities, "relations": self.relations,
            "dimensions": self.dimensions, "counter": self.counter, "status": self.status,
        })

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter}"

    # ------------------------------------------------------------------ geometry creation
    def point_id(self, p) -> str:
        """Accept an existing point id or coordinates; return a point id."""
        if isinstance(p, str):
            if p != ORIGIN and p not in self.points:
                raise SketchError(f"unknown sketch point {p!r}")
            return p
        pid = self._id("P")
        self.points[pid] = _pt(p)
        return pid

    def add_point(self, p, construction: bool = False) -> str:
        eid = self._id("Point")
        self.entities[eid] = {"type": "point", "p": self.point_id(p), "construction": construction}
        return eid

    def add_line(self, a, b, construction: bool = False) -> str:
        eid = self._id("Line")
        self.entities[eid] = {"type": "line", "p1": self.point_id(a), "p2": self.point_id(b), "construction": construction}
        return eid

    def add_centerline(self, a, b) -> str:
        return self.add_line(a, b, construction=True)

    def add_circle(self, center, radius: float, construction: bool = False) -> str:
        if radius <= 0:
            raise SketchError("circle radius must be positive")
        eid = self._id("Circle")
        self.entities[eid] = {"type": "circle", "c": self.point_id(center), "r": float(radius), "construction": construction}
        return eid

    def add_arc(self, center, start, end, construction: bool = False) -> str:
        """Centre-point arc, counter-clockwise from start to end."""
        eid = self._id("Arc")
        self.entities[eid] = {"type": "arc", "c": self.point_id(center), "s": self.point_id(start),
                              "e": self.point_id(end), "construction": construction}
        return eid

    def add_three_point_arc(self, start, end, mid) -> str:
        s = np.array(self.coords(self.point_id(start)) if isinstance(start, str) else start, float)
        e = np.array(self.coords(self.point_id(end)) if isinstance(end, str) else end, float)
        m = np.array(mid, float)
        c = _circumcenter(s, m, e)
        # orient CCW so that the mid point lies on the arc
        a_s, a_e, a_m = (math.atan2(*(p - c)[::-1]) for p in (s, e, m))
        ccw = (a_m - a_s) % (2 * math.pi) < (a_e - a_s) % (2 * math.pi)
        return self.add_arc(_pt(c), start, end) if ccw else self.add_arc(_pt(c), end, start)

    def add_ellipse(self, center, major_end, minor_radius: float, construction: bool = False) -> str:
        """Ellipse from its centre, one end of the major axis and the minor radius."""
        if minor_radius <= 0:
            raise SketchError("ellipse minor radius must be positive")
        eid = self._id("Ellipse")
        c, m = self.point_id(center), self.point_id(major_end)
        if np.linalg.norm(np.array(self.coords(m)) - np.array(self.coords(c))) < 1e-9:
            raise SketchError("ellipse major axis has zero length")
        self.entities[eid] = {"type": "ellipse", "c": c, "m": m, "b": float(minor_radius), "construction": construction}
        return eid

    def add_spline(self, points: list, closed: bool = False, construction: bool = False) -> str:
        """Interpolating spline through the given points (ids or coordinates)."""
        if len(points) < 2:
            raise SketchError("a spline needs at least two points")
        eid = self._id("Spline")
        self.entities[eid] = {"type": "spline", "pts": [self.point_id(q) for q in points], "closed": bool(closed),
                              "construction": construction}
        return eid

    def add_conic(self, start, end, apex, rho: float = 0.5, construction: bool = False) -> str:
        """Conic from `start` to `end`, tangent at each end to the line towards `apex`; rho (0..1) sets its
        fullness: below 0.5 an ellipse arc, 0.5 a parabola, above 0.5 a hyperbola."""
        if not 0.0 < float(rho) < 1.0:
            raise SketchError("conic rho must be between 0 and 1")
        s, e, m = self.point_id(start), self.point_id(end), self.point_id(apex)
        a, b, c = (np.array(self.coords(q)) for q in (s, e, m))
        if abs(_cross(b - a, c - a)) < 1e-9:
            raise SketchError("the conic's apex must not lie on the line through its ends")
        eid = self._id("Conic")
        self.entities[eid] = {"type": "conic", "s": s, "e": e, "m": m, "rho": float(rho), "construction": construction}
        return eid

    def add_text(self, position, text: str, height: float = 10.0, font: str = "Arial", angle: float = 0.0) -> str:
        """Sketch Text: letter outlines (closed contours) with the baseline start at `position`."""
        if not text.strip():
            raise SketchError("sketch text is empty")
        if height <= 0:
            raise SketchError("text height must be positive")
        eid = self._id("Text")
        self.entities[eid] = {"type": "text", "p": self.point_id(position), "text": text, "height": float(height),
                              "font": font, "angle": float(angle), "construction": False}
        return eid

    def add_rectangle(self, c1, c2, construction: bool = False) -> list[str]:
        """Corner rectangle with automatic horizontal/vertical relations (as SOLIDWORKS adds)."""
        (x1, y1), (x2, y2) = _pt(c1), _pt(c2)
        p = [self.point_id([x1, y1]), self.point_id([x2, y1]), self.point_id([x2, y2]), self.point_id([x1, y2])]
        lines = [self.add_line(p[i], p[(i + 1) % 4], construction) for i in range(4)]
        self.add_relation("horizontal", [lines[0]])
        self.add_relation("horizontal", [lines[2]])
        self.add_relation("vertical", [lines[1]])
        self.add_relation("vertical", [lines[3]])
        return lines

    def add_center_rectangle(self, center, corner, construction: bool = False) -> list[str]:
        (cx, cy), (x, y) = _pt(center), _pt(corner)
        lines = self.add_rectangle([2 * cx - x, 2 * cy - y], [x, y], construction)
        diag = self.add_line(self.entities[lines[0]]["p1"], self.entities[lines[1]]["p2"], construction=True)
        cpid = center if isinstance(center, str) else self.point_id([cx, cy])
        self.add_relation("midpoint", [cpid, diag])
        return lines + [diag]

    def add_polygon(self, center, radius: float, sides: int, angle_deg: float = 90.0) -> list[str]:
        if sides < 3:
            raise SketchError("polygon needs at least 3 sides")
        cx, cy = _pt(center) if not isinstance(center, str) else self.coords(center)
        cid = self.point_id(center)
        pts = []
        for k in range(sides):
            a = math.radians(angle_deg) + 2 * math.pi * k / sides
            pts.append(self.point_id([cx + radius * math.cos(a), cy + radius * math.sin(a)]))
        lines = [self.add_line(pts[k], pts[(k + 1) % sides]) for k in range(sides)]
        circ = self.add_circle(cid, radius, construction=True)
        for k in range(sides):
            self.add_relation("coincident", [pts[k], circ])
        for k in range(1, sides):
            self.add_relation("equal", [lines[0], lines[k]])
        return lines + [circ]

    def add_slot(self, c1, c2, width: float) -> list[str]:
        """Straight slot between two arc centres."""
        a, b = np.array(_pt(c1)), np.array(_pt(c2))
        d = b - a
        if np.linalg.norm(d) < 1e-9 or width <= 0:
            raise SketchError("slot needs two distinct centres and a positive width")
        n = np.array([-d[1], d[0]]) / np.linalg.norm(d) * width / 2
        ca, cb = self.point_id(_pt(a)), self.point_id(_pt(b))
        p1, p2, p3, p4 = (self.point_id(_pt(q)) for q in (a - n, b - n, b + n, a + n))
        l1 = self.add_line(p1, p2)
        a1 = self.add_arc(cb, p2, p3)
        l2 = self.add_line(p3, p4)
        a2 = self.add_arc(ca, p4, p1)
        for ln in (l1, l2):
            for arc in (a1, a2):
                self.add_relation("tangent", [ln, arc])
        return [l1, a1, l2, a2]

    # ------------------------------------------------------------------ sketch tools
    def _copy_entities(self, entity_ids: list[str], xf, rot: float = 0.0) -> list[str]:
        """Copy entities through the 2D point map `xf` (a rigid motion rotating by `rot` radians)."""
        mapping: dict[str, str] = {}

        def mp(pid: str) -> str:
            if pid not in mapping:
                mapping[pid] = self.point_id(_pt(xf(np.array(self.coords(pid), float))))
            return mapping[pid]

        created = []
        for eid in entity_ids:
            e = self.entities.get(eid)
            if e is None:
                raise SketchError(f"unknown entity {eid}")
            n = copy.deepcopy(e)
            for k in ("p", "p1", "p2", "c", "s", "e", "m"):
                if k in n:
                    n[k] = mp(n[k])
            if "pts" in n:
                n["pts"] = [mp(q) for q in n["pts"]]
            if n["type"] == "text":
                n["angle"] = n.get("angle", 0.0) + math.degrees(rot)
            new = self._id(eid.rstrip("0123456789") or "Entity")
            self.entities[new] = n
            created.append(new)
        return created

    def linear_pattern_entities(self, entity_ids: list[str], spacing: float, count: int, angle: float = 0.0,
                                spacing2: float = 0.0, count2: int = 1, angle2: float = 90.0) -> list[str]:
        """Linear Sketch Pattern: `count` copies along direction `angle` (deg from sketch X), optionally a
        second direction."""
        if count < 1 or count2 < 1:
            raise SketchError("instance counts must be at least 1")
        d1 = np.array([math.cos(math.radians(angle)), math.sin(math.radians(angle))]) * spacing
        d2 = np.array([math.cos(math.radians(angle2)), math.sin(math.radians(angle2))]) * spacing2
        created = []
        for i in range(count):
            for j in range(count2):
                if i == 0 and j == 0:
                    continue
                off = d1 * i + d2 * j
                created += self._copy_entities(entity_ids, lambda q, off=off: q + off)
        return created

    def circular_pattern_entities(self, entity_ids: list[str], center, count: int, angle: float = 360.0) -> list[str]:
        """Circular Sketch Pattern: `count` instances about `center` spread over `angle` degrees (equal spacing;
        a full 360 does not duplicate the seed)."""
        if count < 2:
            raise SketchError("count must be at least 2")
        c = np.array(self.coords(center) if isinstance(center, str) else _pt(center), float)
        step = math.radians(angle) / (count if abs(angle - 360.0) < 1e-9 else count - 1)
        created = []
        for k in range(1, count):
            a = step * k
            R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
            created += self._copy_entities(entity_ids, lambda q, R=R: c + R @ (q - c), rot=a)
        return created

    def add_circle_3pt(self, p1, p2, p3, construction: bool = False) -> str:
        """Perimeter Circle: the circle through three points."""
        a, b, c = (np.array(self.coords(q) if isinstance(q, str) else _pt(q), float) for q in (p1, p2, p3))
        cc = _circumcenter(a, b, c)
        if cc is None:
            raise SketchError("the three points are in a line")
        return self.add_circle(_pt(cc), float(np.linalg.norm(a - cc)), construction)

    def add_tangent_arc(self, start: str, end, construction: bool = False) -> str:
        """Tangent Arc: from `start` (the end point of a line or arc) to `end`, leaving tangent to that entity;
        a tangent relation is added."""
        if start not in self.points:
            raise SketchError("a tangent arc starts at the end point of a line or arc")
        owner = next(((eid, e) for eid, e in self.entities.items()
                      if e["type"] in ("line", "arc") and start in (e.get("p1"), e.get("p2"), e.get("s"), e.get("e"))), None)
        if owner is None:
            raise SketchError("a tangent arc starts at the end point of a line or arc")
        eid, e = owner
        P = np.array(self.coords(start), float)
        if e["type"] == "line":  # leaving the line in its direction of travel
            other = e["p2"] if e["p1"] == start else e["p1"]
            t = P - np.array(self.coords(other), float)
        else:  # leaving the arc along its tangent, away from the arc
            c = np.array(self.coords(e["c"]), float)
            rad = P - c
            t = np.array([-rad[1], rad[0]]) * (1.0 if start == e["e"] else -1.0)  # arcs run counterclockwise s -> e
        t = t / np.linalg.norm(t)
        E = np.array(self.coords(end) if isinstance(end, str) else _pt(end), float)
        n = np.array([-t[1], t[0]])  # left of the direction of travel
        den = 2 * float(n @ (P - E))
        if abs(den) < 1e-12:
            raise SketchError("the end point lies straight ahead: that is a line, not an arc")
        s = -float((P - E) @ (P - E)) / den  # centre P + n s is as far from E as from P
        C = P + n * s
        end_id = end if isinstance(end, str) else self.point_id(_pt(E))
        arc = self.add_arc(_pt(C), start, end_id, construction) if s > 0 else self.add_arc(_pt(C), end_id, start, construction)
        self.add_relation("tangent", [eid, arc])
        return arc

    def _entity_point_ids(self, entity_ids: list[str]) -> list[str]:
        out = []
        for eid in entity_ids:
            if eid not in self.entities:
                raise SketchError(f"unknown entity {eid}")
            for q in _entity_points(self.entities[eid]):
                if q != ORIGIN and q not in out:
                    out.append(q)
        return out

    def drag_entity(self, entity_id: str, frm, to) -> None:
        """Drag an entity with the mouse, as SOLIDWORKS does in an under-defined sketch: a circle or arc grabbed
        on its curve changes size (it passes through `to`), anything else moves by to - frm. The solve that
        follows keeps the relations and dimensions (fully defined geometry does not move)."""
        if entity_id not in self.entities:
            raise SketchError(f"unknown entity {entity_id}")
        e = self.entities[entity_id]
        frm, to = np.array(_pt(frm), float), np.array(_pt(to), float)
        if e["type"] in ("circle", "arc"):
            c = np.array(self.coords(e["c"]), float)
            r = float(np.linalg.norm(to - c))
            if r < 1e-9:
                return
            if e["type"] == "circle":
                e["r"] = r
            else:
                for k in ("s", "e"):
                    if e[k] != ORIGIN:
                        v = np.array(self.coords(e[k]), float) - c
                        self.points[e[k]] = _pt(c + v / (np.linalg.norm(v) or 1.0) * r)
            return
        self.transform_entities([entity_id], move=tuple(to - frm))

    def transform_entities(self, entity_ids: list[str], move=(0.0, 0.0), rotate: float = 0.0, center=(0.0, 0.0),
                           scale: float = 1.0, copy: bool = False) -> list[str]:
        """Move / Copy / Rotate / Scale Entities: about `center`, scale by `scale`, turn by `rotate` degrees,
        then shift by `move`. copy=True leaves the originals; otherwise they move (with the points they share)."""
        if scale <= 0:
            raise SketchError("scale factor must be positive")
        c = np.array(self.coords(center) if isinstance(center, str) else _pt(center), float)
        a = math.radians(rotate)
        R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        off = np.array(_pt(move), float)

        def xf(q):
            return c + R @ ((np.asarray(q, float) - c) * scale) + off
        targets = self._copy_entities(entity_ids, xf, rot=a) if copy else list(entity_ids)
        if not copy:
            for pid in self._entity_point_ids(entity_ids):
                self.points[pid] = _pt(xf(self.coords(pid)))
            for eid in entity_ids:
                e = self.entities[eid]
                if e["type"] == "text":
                    e["angle"] = e.get("angle", 0.0) + rotate
        if scale != 1.0:
            for eid in targets:
                e = self.entities[eid]
                if e["type"] == "circle":
                    e["r"] *= scale
                elif e["type"] == "ellipse":
                    e["b"] *= scale
                elif e["type"] == "text":
                    e["height"] *= scale
        if scale != 1.0 and not copy:  # size dimensions on the scaled entities follow
            for d in self.dimensions:
                if d["type"] in ("distance", "hdistance", "vdistance", "length", "radius", "diameter") and set(d["refs"]) <= set(entity_ids) | set(self._entity_point_ids(entity_ids)):
                    d["value"] *= scale
        return targets

    def split_entity(self, eid: str, at) -> list[str]:
        """Split Entities: cut a line or arc in two at the point `at` (projected onto it); the halves share it."""
        e = self.entities.get(eid)
        if e is None:
            raise SketchError(f"unknown entity {eid}")
        q = np.array(_pt(at), float)
        cons = e.get("construction", False)
        if e["type"] == "line":
            a, b = np.array(self.coords(e["p1"])), np.array(self.coords(e["p2"]))
            s = float((q - a) @ (b - a) / ((b - a) @ (b - a)))
            if not 1e-6 < s < 1 - 1e-6:
                raise SketchError("the split point must lie between the line's ends")
            mid = self.point_id(_pt(a + (b - a) * s))
            p2 = e["p2"]
            e["p2"] = mid
            new = self.add_line(mid, p2, cons)
            self.add_relation("collinear", [eid, new])
            return [eid, new]
        if e["type"] == "arc":
            c = np.array(self.coords(e["c"]))
            r = float(np.linalg.norm(np.array(self.coords(e["s"])) - c))
            ang = math.atan2(q[1] - c[1], q[0] - c[0])
            s0 = math.atan2(*(np.array(self.coords(e["s"])) - c)[::-1])
            span = (math.atan2(*(np.array(self.coords(e["e"])) - c)[::-1]) - s0) % (2 * math.pi)
            if not 1e-6 < (ang - s0) % (2 * math.pi) < span - 1e-6:
                raise SketchError("the split point must lie on the arc, between its ends")
            mid = self.point_id(_pt(c + r * np.array([math.cos(ang), math.sin(ang)])))
            end = e["e"]
            e["e"] = mid
            new = self.add_arc(e["c"], mid, end, cons)
            return [eid, new]
        raise SketchError("split lines or arcs (trim a circle to make arcs)")

    def mirror_entities(self, entity_ids: list[str], about: str) -> list[str]:
        """Mirror entities about a line; mirrored points get symmetric relations (Mirror Entities)."""
        if self.entities.get(about, {}).get("type") != "line":
            raise SketchError("mirror about a sketch line or centerline")
        a = np.array(self.coords(self.entities[about]["p1"])); b = np.array(self.coords(self.entities[about]["p2"]))
        d = (b - a) / np.linalg.norm(b - a)
        mapping: dict[str, str] = {}

        def mirror_point(pid: str) -> str:
            if pid in mapping:
                return mapping[pid]
            p = np.array(self.coords(pid))
            rel = p - a
            dist = abs(_cross(d, rel))
            if dist < 1e-9:  # point on the mirror line maps to itself
                mapping[pid] = pid
                return pid
            q = a + 2 * np.dot(rel, d) * d - rel
            new = self.point_id(_pt(q))
            mapping[pid] = new
            self.add_relation("symmetric", [pid, new, about])
            return new

        created = []
        for eid in entity_ids:
            e = self.entities.get(eid)
            if e is None:
                raise SketchError(f"unknown entity {eid}")
            c = e.get("construction", False)
            if e["type"] == "line":
                created.append(self.add_line(mirror_point(e["p1"]), mirror_point(e["p2"]), c))
            elif e["type"] == "circle":
                new = self.add_circle(mirror_point(e["c"]), e["r"], c)
                self.add_relation("equal", [eid, new]); created.append(new)
            elif e["type"] == "arc":  # mirroring reverses orientation: swap start/end
                new = self.add_arc(mirror_point(e["c"]), mirror_point(e["e"]), mirror_point(e["s"]), c)
                self.add_relation("equal", [eid, new]); created.append(new)
            elif e["type"] == "point":
                created.append(self.add_point(mirror_point(e["p"]), c))
            elif e["type"] == "conic":
                created.append(self.add_conic(mirror_point(e["s"]), mirror_point(e["e"]), mirror_point(e["m"]), e["rho"], c))
        return created

    def offset_entities(self, entity_ids: list[str], distance: float) -> list[str]:
        """Offset Entities: circles/arcs become concentric; a closed loop of lines is offset outward
        (positive distance) or inward (negative), with parallel relations and driving distances."""
        created = []
        lines = [e for e in entity_ids if self.entities.get(e, {}).get("type") == "line"]
        for eid in entity_ids:
            e = self.entities.get(eid)
            if e is None:
                raise SketchError(f"unknown entity {eid}")
            if e["type"] == "circle":
                r = e["r"] + distance
                if r <= 0:
                    raise SketchError("offset would give a non-positive radius")
                new = self.add_circle(e["c"], r)
                self.add_relation("concentric", [eid, new])
                created.append(new)
            elif e["type"] == "arc":
                c = np.array(self.coords(e["c"]))
                s, en = np.array(self.coords(e["s"])), np.array(self.coords(e["e"]))
                r = np.linalg.norm(s - c)
                k = (r + distance) / r
                if r + distance <= 0:
                    raise SketchError("offset would give a non-positive radius")
                new = self.add_arc(e["c"], _pt(c + (s - c) * k), _pt(c + (en - c) * k))
                self.add_relation("concentric", [eid, new])
                created.append(new)
        if lines:
            created += self._offset_line_loop(lines, distance)
        return created

    def _offset_line_loop(self, lines: list[str], distance: float) -> list[str]:
        # order the lines into a chain via shared points
        segs = {l: (self.entities[l]["p1"], self.entities[l]["p2"]) for l in lines}
        order, pts = [lines[0]], [segs[lines[0]][0], segs[lines[0]][1]]
        remaining = set(lines[1:])
        while remaining:
            last = pts[-1]
            nxt = next((l for l in remaining if last in segs[l]), None)
            if nxt is None:
                raise SketchError("offset of lines needs one connected chain")
            remaining.discard(nxt)
            a, b = segs[nxt]
            pts.append(b if a == last else a)
            order.append(nxt)
        closed = pts[0] == pts[-1]
        P = [np.array(self.coords(p)) for p in (pts[:-1] if closed else pts)]
        n = len(P)
        area = sum(_cross(P[i], P[(i + 1) % n]) for i in range(n)) / 2 if closed else 1.0
        sgn = 1.0 if area >= 0 else -1.0  # CCW loop: outward normal is to the right of travel

        def off_line(i):
            p, q = P[i], P[(i + 1) % n]
            dv = (q - p) / np.linalg.norm(q - p)
            nrm = np.array([dv[1], -dv[0]]) * sgn
            return p + nrm * distance, dv
        m = n if closed else n - 1
        offs = [off_line(i) for i in range(m)]
        corners = []
        for i in range(m if closed else m + 1):
            if not closed and i == 0:
                corners.append(offs[0][0]); continue
            if not closed and i == m:
                corners.append(offs[m - 1][0] + offs[m - 1][1] * np.linalg.norm(P[m] - P[m - 1])); continue
            (p1, d1), (p2, d2) = offs[i - 1], offs[i % m]
            den = _cross(d1, d2)
            if abs(den) < 1e-12:
                corners.append(p2)
            else:
                t = _cross(p2 - p1, d2) / den
                corners.append(p1 + d1 * t)
        ids = [self.point_id(_pt(c)) for c in corners]
        created = []
        for i in range(m):
            a, b = ids[i], ids[(i + 1) % len(ids)] if closed else ids[i + 1]
            new = self.add_line(a, b)
            created.append(new)
            self.relations.append({"id": self._id("R"), "type": "parallel", "refs": [order[i], new]})
            self.dimensions.append({"id": self._next_dim_id(), "type": "distance", "refs": [order[i], new],
                                    "driven": False, "value": abs(distance)})
        return created

    # ------------------------------------------------------------------ trim / extend
    def _intersections(self, eid: str) -> list[float]:
        """Parameters along entity `eid` where other entities cross it.
        Lines: t in [0,1]; circles/arcs: angle (rad, CCW from +x about the centre)."""
        e = self.entities[eid]
        out = []
        for oid, o in self.entities.items():
            if oid == eid or o["type"] not in ("line", "circle", "arc"):
                continue
            for pt in _intersect(self, e, o):
                if e["type"] == "line":
                    a, b = np.array(self.coords(e["p1"])), np.array(self.coords(e["p2"]))
                    t = float(np.dot(pt - a, b - a) / np.dot(b - a, b - a))
                    if -1e-9 <= t <= 1 + 1e-9:
                        out.append(t)
                else:
                    c = np.array(self.coords(e["c"]))
                    ang = math.atan2(pt[1] - c[1], pt[0] - c[0])
                    if e["type"] == "circle" or _on_arc(self, e, ang):
                        out.append(ang)
        return sorted(set(round(v, 12) for v in out))

    def trim(self, eid: str, pick) -> list[str]:
        """Power trim: remove the part of entity `eid` around `pick` [x, y] bounded by the nearest intersections.
        No intersections: the entity is deleted. Returns ids of the remaining pieces."""
        if eid not in self.entities:
            raise SketchError(f"unknown entity {eid}")
        e = self.entities[eid]
        pick = np.array(pick, float)
        cuts = self._intersections(eid)
        if e["type"] == "line":
            a, b = np.array(self.coords(e["p1"])), np.array(self.coords(e["p2"]))
            tp = float(np.dot(pick - a, b - a) / np.dot(b - a, b - a))
            inner = [t for t in cuts if 1e-9 < t < 1 - 1e-9]
            lo = max([t for t in inner if t < tp], default=None)
            hi = min([t for t in inner if t > tp], default=None)
            if lo is None and hi is None:
                self.delete(eid)
                return []
            pieces = []
            if lo is not None:
                p2_old = e["p2"]
                e["p2"] = self.point_id(_pt(a + (b - a) * lo))
                pieces.append(eid)
                if hi is not None:
                    r = self.point_id(_pt(a + (b - a) * hi))
                    pieces.append(self.add_line(r, p2_old, e.get("construction", False)))
            else:
                e["p1"] = self.point_id(_pt(a + (b - a) * hi))
                pieces.append(eid)
            self._cleanup_points()
            return pieces
        c = np.array(self.coords(e["c"]))
        ap = math.atan2(pick[1] - c[1], pick[0] - c[0])
        r = e["r"] if e["type"] == "circle" else float(np.linalg.norm(np.array(self.coords(e["s"])) - c))
        if e["type"] == "circle":
            if not cuts:
                self.delete(eid)
                return []
            if len(cuts) < 2:
                raise SketchError("a circle needs two intersections to trim")
            rel = sorted(((x - ap) % (2 * math.pi), x) for x in cuts)
            a_start, a_end = rel[0][1], rel[-1][1]  # keep the arc from the cut after the pick round to the one before
            pts = [self.point_id(_pt(c + r * np.array([math.cos(x), math.sin(x)]))) for x in (a_start, a_end)]
            cid = e["c"]
            del self.entities[eid]
            self.relations = [x for x in self.relations if eid not in x["refs"]]
            self.dimensions = [x for x in self.dimensions if eid not in x["refs"]]
            return [self.add_arc(cid, pts[0], pts[1])]
        s_ang = math.atan2(*(np.array(self.coords(e["s"])) - c)[::-1])
        rel_pick = (ap - s_ang) % (2 * math.pi)
        span = (math.atan2(*(np.array(self.coords(e["e"])) - c)[::-1]) - s_ang) % (2 * math.pi)
        inner = [x for x in ((a - s_ang) % (2 * math.pi) for a in cuts) if 1e-9 < x < span - 1e-9]
        lo = max([x for x in inner if x < rel_pick], default=None)
        hi = min([x for x in inner if x > rel_pick], default=None)
        if lo is None and hi is None:
            self.delete(eid)
            return []

        def to_pt(rel_a):
            return self.point_id(_pt(c + r * np.array([math.cos(s_ang + rel_a), math.sin(s_ang + rel_a)])))
        pieces, end_old = [], e["e"]
        if lo is not None:
            e["e"] = to_pt(lo)
            pieces.append(eid)
            if hi is not None:
                pieces.append(self.add_arc(e["c"], to_pt(hi), end_old))
        else:
            e["s"] = to_pt(hi)
            pieces.append(eid)
        self._cleanup_points()
        return pieces

    def extend(self, eid: str, pick) -> str:
        """Extend the end of line `eid` nearest to `pick` to the next entity it would meet."""
        e = self.entities.get(eid)
        if not e or e["type"] != "line":
            raise SketchError("extend works on lines")
        a, b = np.array(self.coords(e["p1"])), np.array(self.coords(e["p2"]))
        end = "p2" if np.linalg.norm(np.array(pick) - b) < np.linalg.norm(np.array(pick) - a) else "p1"
        d = b - a
        best = None
        for oid, o in self.entities.items():
            if oid == eid or o["type"] == "point":
                continue
            for pt in _intersect(self, e, o, infinite=True):
                t = float(np.dot(pt - a, d) / np.dot(d, d))
                if end == "p2" and t > 1 + 1e-9 and (best is None or t < best):
                    best = t
                if end == "p1" and t < -1e-9 and (best is None or t > best):
                    best = t
        if best is None:
            raise SketchError("nothing to extend to")
        e[end] = self.point_id(_pt(a + d * best))
        self._cleanup_points()
        return eid

    def _cleanup_points(self) -> None:
        used = {pid for e in self.entities.values() for pid in _entity_points(e)}
        used |= {r for c in self.relations + self.dimensions for r in c["refs"]}
        for pid in [p for p in self.points if p not in used]:
            del self.points[pid]

    def _next_dim_id(self) -> str:
        k = len(self.dimensions) + 1
        while any(d["id"] == f"D{k}" for d in self.dimensions):
            k += 1
        return f"D{k}"

    def fillet_corner(self, point: str, radius: float) -> str:
        """Sketch Fillet: round the corner where two lines meet at `point` with a tangent arc."""
        if radius <= 0:
            raise SketchError("fillet radius must be positive")
        lines = [eid for eid, e in self.entities.items() if e["type"] == "line" and point in (e["p1"], e["p2"])]
        if len(lines) != 2:
            raise SketchError("sketch fillet needs a point shared by exactly two lines")
        P = np.array(self.coords(point))
        far = [self.entities[l]["p2"] if self.entities[l]["p1"] == point else self.entities[l]["p1"] for l in lines]
        u = [(np.array(self.coords(f)) - P) / np.linalg.norm(np.array(self.coords(f)) - P) for f in far]
        theta = math.acos(max(-1.0, min(1.0, float(np.dot(u[0], u[1])))))
        if theta < 1e-6 or abs(theta - math.pi) < 1e-6:
            raise SketchError("lines are collinear; nothing to fillet")
        t = radius / math.tan(theta / 2)
        for f, uu in zip(far, u):
            if t >= np.linalg.norm(np.array(self.coords(f)) - P) - 1e-9:
                raise SketchError("fillet radius too large for the adjoining lines")
        T = [P + uu * t for uu in u]
        bis = (u[0] + u[1]) / np.linalg.norm(u[0] + u[1])
        C = P + bis * (radius / math.sin(theta / 2))
        tids = [self.point_id(_pt(x)) for x in T]
        for l, tid in zip(lines, tids):
            e = self.entities[l]
            e["p1" if e["p1"] == point else "p2"] = tid
        ccw = _cross(T[0] - C, T[1] - C) > 0
        arc = self.add_arc(_pt(C), tids[0], tids[1]) if ccw else self.add_arc(_pt(C), tids[1], tids[0])
        for l in lines:
            self.add_relation("tangent", [l, arc])
        # drop the old corner point and anything that referenced it
        self.relations = [r for r in self.relations if point not in r["refs"]]
        self.dimensions = [d for d in self.dimensions if point not in d["refs"]]
        if not any(point in _entity_points(e) for e in self.entities.values()):
            self.points.pop(point, None)
        return arc

    def chamfer_corner(self, point: str, distance: float, distance2: float | None = None, angle: float | None = None) -> str:
        """Sketch Chamfer: cut the corner where two lines meet at `point` with a line, `distance` back along
        the first line and `distance2` (default: equal) along the second - or at `angle` degrees to the first."""
        if distance <= 0:
            raise SketchError("chamfer distance must be positive")
        lines = [eid for eid, e in self.entities.items() if e["type"] == "line" and point in (e["p1"], e["p2"])]
        if len(lines) != 2:
            raise SketchError("sketch chamfer needs a point shared by exactly two lines")
        P = np.array(self.coords(point))
        far = [self.entities[l]["p2"] if self.entities[l]["p1"] == point else self.entities[l]["p1"] for l in lines]
        u = [(np.array(self.coords(f)) - P) / np.linalg.norm(np.array(self.coords(f)) - P) for f in far]
        theta = math.acos(max(-1.0, min(1.0, float(np.dot(u[0], u[1])))))
        if theta < 1e-6 or abs(theta - math.pi) < 1e-6:
            raise SketchError("lines are collinear; nothing to chamfer")
        d1 = float(distance)
        if angle is not None:  # the chamfer line at `angle` to the first line: sine rule in the corner triangle
            a = math.radians(float(angle))
            if not 0 < a < math.pi - theta:
                raise SketchError("chamfer angle out of range for this corner")
            d2 = d1 * math.sin(a) / math.sin(math.pi - theta - a)
        else:
            d2 = float(distance2) if distance2 else d1
        for f, d in zip(far, (d1, d2)):
            if d >= np.linalg.norm(np.array(self.coords(f)) - P) - 1e-9:
                raise SketchError("chamfer distance too large for the adjoining lines")
        tids = [self.point_id(_pt(P + uu * d)) for uu, d in zip(u, (d1, d2))]
        for l, tid in zip(lines, tids):
            e = self.entities[l]
            e["p1" if e["p1"] == point else "p2"] = tid
        ch = self.add_line(tids[0], tids[1])
        self.relations = [r for r in self.relations if point not in r["refs"]]
        self.dimensions = [d for d in self.dimensions if point not in d["refs"]]
        if not any(point in _entity_points(e) for e in self.entities.values()):
            self.points.pop(point, None)
        return ch

    def remove_entities(self, entity_ids: list[str]) -> None:
        """Delete entities together with the relations and dimensions on points no entity uses any more
        (Mirror / Move without Copy: nothing dangles where the originals were)."""
        for eid in entity_ids:
            self.delete(eid)
        used = {pid for e in self.entities.values() for pid in _entity_points(e)} | {ORIGIN}
        orphans = {pid for pid in self.points if pid not in used}
        self.relations = [r for r in self.relations if not orphans & set(r["refs"])]
        self.dimensions = [d for d in self.dimensions if not orphans & set(d["refs"])]
        for pid in orphans:
            del self.points[pid]

    def delete(self, item_id: str) -> None:
        if item_id in self.entities:
            del self.entities[item_id]
            self.relations = [r for r in self.relations if item_id not in r["refs"]]
            self.dimensions = [d for d in self.dimensions if item_id not in d["refs"]]
            used = {pid for e in self.entities.values() for pid in _entity_points(e)}
            used |= {r for c in self.relations + self.dimensions for r in c["refs"]}
            for pid in [p for p in self.points if p not in used]:
                del self.points[pid]
            return
        n = len(self.relations) + len(self.dimensions)
        self.relations = [r for r in self.relations if r["id"] != item_id]
        self.dimensions = [d for d in self.dimensions if d["id"] != item_id]
        if len(self.relations) + len(self.dimensions) == n:
            raise SketchError(f"no sketch entity, relation or dimension named {item_id!r}")

    # ------------------------------------------------------------------ relations / dimensions
    def add_relation(self, kind: str, refs: list[str]) -> str:
        if kind not in RELATIONS:
            raise SketchError(f"unknown relation {kind!r}; expected one of {sorted(RELATIONS)}")
        for r in refs:
            if r != ORIGIN and r not in self.points and r not in self.entities:
                raise SketchError(f"unknown reference {r!r}")
        rel = {"id": self._id("R"), "type": kind, "refs": list(refs)}
        if kind == "fix":
            rel["values"] = {r: self._snapshot(r) for r in refs}
        self.relations.append(rel)
        return rel["id"]

    def add_dimension(self, kind: str, refs: list[str], value: float | None = None,
                      position: list[float] | None = None, supplement: bool = False) -> dict:
        """position: where the label sits (sketch coordinates); supplement: an angle measured on the other side
        of the lines (180 - the angle between their directions), as SOLIDWORKS picks by where it is placed."""
        if kind not in DIMENSIONS:
            raise SketchError(f"unknown dimension {kind!r}; expected one of {sorted(DIMENSIONS)}")
        dim = {"id": f"D{sum(1 for _ in self.dimensions) + 1}", "type": kind, "refs": list(refs), "driven": False}
        if position is not None:
            dim["pos"] = [float(position[0]), float(position[1])]
        if supplement and kind == "angle":
            dim["supplement"] = True
        while any(d["id"] == dim["id"] for d in self.dimensions):
            dim["id"] = f"D{int(dim['id'][1:]) + 1}"
        dim["value"] = float(value) if value is not None else self.measure(dim)
        dof_before = self.solve()["dof"]
        self.dimensions.append(dim)
        st = self.solve()
        if st["failing"] or st["dof"] >= dof_before:
            # SOLIDWORKS behaviour: a conflicting or redundant dimension becomes a driven (reference) dimension.
            dim["driven"] = True
            self.solve()
            dim["value"] = self.measure(dim)
        return dim

    def move_dimension(self, dim_id: str, position: list[float]) -> None:
        """Move a dimension's label (display only: the geometry does not change)."""
        for d in self.dimensions:
            if d["id"] == dim_id:
                d["pos"] = [float(position[0]), float(position[1])]
                return
        raise SketchError(f"no dimension {dim_id!r}")

    def set_dimension(self, dim_id: str, value: float) -> None:
        for d in self.dimensions:
            if d["id"] == dim_id:
                if d["driven"]:
                    raise SketchError(f"{dim_id} is a driven (reference) dimension")
                d["value"] = float(value)
                return
        raise SketchError(f"no dimension {dim_id!r}")

    def _snapshot(self, ref: str):
        if ref == ORIGIN:
            return [0.0, 0.0]
        if ref in self.points:
            return list(self.points[ref])
        e = self.entities[ref]
        return {k: (list(self.points[v]) if isinstance(v, str) and v in self.points else v) for k, v in e.items()}

    # ------------------------------------------------------------------ evaluation helpers
    def coords(self, pid: str) -> list[float]:
        return [0.0, 0.0] if pid == ORIGIN else self.points[pid]

    def measure(self, dim: dict) -> float:
        x, idx = self._vector()
        return float(_dim_measure(self, dim, _Getter(self, x, idx)))

    # ------------------------------------------------------------------ solver
    def _vector(self):
        idx: dict[tuple, int] = {}
        vals: list[float] = []
        for pid, (px, py) in self.points.items():
            idx[(pid, 0)] = len(vals); vals.append(px)
            idx[(pid, 1)] = len(vals); vals.append(py)
        for eid, e in self.entities.items():
            if e["type"] == "circle":
                idx[(eid, "r")] = len(vals); vals.append(e["r"])
            elif e["type"] == "ellipse":
                idx[(eid, "b")] = len(vals); vals.append(e["b"])
        return np.array(vals, float), idx

    def _residuals(self, x, idx, include_driven=False):
        g = _Getter(self, x, idx)
        out: list[float] = []
        owners: list[str] = []
        for eid, e in self.entities.items():  # implicit: arc end points share the radius
            if e["type"] == "arc":
                out.append(g.dist(e["s"], e["c"]) - g.dist(e["e"], e["c"])); owners.append(eid)
        for r in self.relations:
            res = _relation_residuals(self, r, g)
            out += res; owners += [r["id"]] * len(res)
        for d in self.dimensions:
            if d["driven"] and not include_driven:
                continue
            out.append(_dim_measure(self, d, g, signed_target=True) - _dim_target(self, d, g)); owners.append(d["id"])
        return np.array(out, float), owners

    def _sparsity(self, idx, owners, n):
        """Which unknowns each residual row can depend on: the points (and radii) of what its constraint
        refers to. Lets the solver estimate the Jacobian with a few grouped evaluations instead of one per
        unknown (large imported sketches)."""
        from scipy.sparse import lil_matrix

        def cols_of(ref) -> set:
            out = set()
            if ref in self.entities:
                e = self.entities[ref]
                for q in _entity_points(e):
                    out |= {idx[(q, 0)], idx[(q, 1)]} if (q, 0) in idx else set()
                for comp in ("r", "b"):
                    if (ref, comp) in idx:
                        out.add(idx[(ref, comp)])
            elif (ref, 0) in idx:
                out |= {idx[(ref, 0)], idx[(ref, 1)]}
            return out
        refs = {r["id"]: r["refs"] for r in self.relations}
        refs.update({d["id"]: d["refs"] for d in self.dimensions})
        pat = lil_matrix((len(owners), n), dtype=int)
        cache: dict = {}
        for row, own in enumerate(owners):
            if own not in cache:
                cols = set()
                for ref in ([own] if own in self.entities else refs.get(own, [])):
                    cols |= cols_of(ref)
                    e = self.entities.get(ref)
                    if e is not None and e["type"] == "line":  # constraints on a line use both its ends
                        cols |= cols_of(e["p1"]) | cols_of(e["p2"])
                cache[own] = sorted(cols)
            for c in cache[own]:
                pat[row, c] = 1
        return pat

    def solve(self) -> dict:
        x0, idx = self._vector()
        res0, owners = self._residuals(x0, idx)
        n = len(x0)
        if len(res0) and n:
            fun = _TimedResiduals(lambda v: self._residuals(v, idx)[0], x0, SOLVE_SECONDS + 0.02 * n)
            opts = dict(method="trf", xtol=1e-14, ftol=1e-14, gtol=1e-14, max_nfev=400 * (n + 1))
            try:
                if not _LEGACY and float(np.abs(res0).max()) <= 1e-11:
                    pass  # every relation and dimension already holds (new geometry was added, nothing moved): no search
                elif n > _sparse_from():  # a Jacobian from grouped columns of the sparsity pattern (same values, fewer evaluations)
                    pat = self._sparsity(idx, owners, n)
                    least_squares(fun, x0, jac=lambda v: _numeric_jac(fun, v, pat), **opts)
                    if float(np.abs(fun(fun.best_x)).max(initial=0.0)) > 1e-8:
                        least_squares(fun, fun.best_x, **opts)  # not converged: densely, from the best so far
                else:
                    least_squares(fun, x0, **opts)
            except _OutOfTime:
                pass  # a sketch that will not converge (conflicting constraints): keep the closest point found
            x = fun.best_x
            res, _ = self._residuals(x, idx)
            jac = _numeric_jac(fun.f, x, self._sparsity(idx, owners, n) if n > _sparse_from() else None)
        else:
            x, res, jac = x0, np.zeros(0), np.zeros((0, n))
        # write back
        for (key, comp), i in idx.items():
            if comp in ("r", "b"):
                self.entities[key][comp] = float(x[i])
            else:
                self.points[key][comp] = float(x[i])
        for d in self.dimensions:
            if d["driven"]:
                d["value"] = self.measure(d)
        # degrees of freedom
        if jac.size:
            _, s, vt = np.linalg.svd(jac)  # (once: the singular values give the rank, vt the free directions)
            rank = int((s > 1e-7 * max(1.0, s[0])).sum())
            null = vt[rank:]
        else:
            rank, null = 0, np.eye(n)
        free = {i for i in range(n) if null.size and np.abs(null[:, i]).max() > 1e-6}
        failing = sorted({owners[i] for i, r in enumerate(res) if abs(r) > 1e-6})
        redundant = len(res) - rank  # informational: tangencies are legitimately rank-degenerate
        state = "over_defined" if failing else ("fully_defined" if not free else "under_defined")
        ent_state = {}
        for eid, e in self.entities.items():
            vars_ = [idx[(p, c)] for p in _entity_points(e) if p != ORIGIN for c in (0, 1)]
            if e["type"] == "circle":
                vars_.append(idx[(eid, "r")])
            elif e["type"] == "ellipse":
                vars_.append(idx[(eid, "b")])
            ent_state[eid] = "under_defined" if any(v in free for v in vars_) else "fully_defined"
        for eid in ent_state:
            if eid in failing or any(eid in c["refs"] for c in self.relations + self.dimensions if c["id"] in failing):
                ent_state[eid] = "over_defined"
        self.status = {"state": state, "dof": n - rank, "failing": failing, "redundant": max(redundant, 0),
                       "entities": ent_state}
        return self.status

    # ------------------------------------------------------------------ 3D conversion
    def to_edges(self, plane: Plane, construction: bool = False) -> list[tuple[str, Edge]]:
        out = []
        for eid, e in self.entities.items():
            if e.get("construction", False) != construction or e["type"] == "point":
                continue
            out += [(eid, edge) for edge in _entity_edges(self, e, plane)]
        return out

    def profiles(self, plane: Plane) -> list[Face]:
        """Closed regions of the sketch: the plane is split by every sketch curve and regions are kept by the
        even-odd rule, so shared edges, nested holes and overlapping contours all behave as in SOLIDWORKS.
        (Cached by sketch contents and plane: rebuilds and parameter trials ask for the same regions again.)"""
        import json
        key = (json.dumps([self.points, self.entities], sort_keys=True, default=str),
               tuple(round(float(v), 9) for vec in (plane.origin, plane.x_dir, plane.z_dir) for v in vec))
        if key in _PROFILES:  # (copies: the faces handed out get changed by what uses them - a later
            return [copy.deepcopy(f) for f in _PROFILES[key]]  # build from shared ones failed)
        edges = [e for _, e in self.to_edges(plane)]
        if not edges:
            raise SketchError("sketch has no geometry")
        try:
            regions = self._split_regions(plane, edges)
        except Exception:
            regions = None
        out = regions if regions else self._profiles_by_wires(plane, edges)
        if len(_PROFILES) >= 64:
            _PROFILES.pop(next(iter(_PROFILES)))
        _PROFILES[key] = [copy.deepcopy(f) for f in out]
        return out

    def _split_regions(self, plane: Plane, edges) -> list[Face]:
        from build123d import Compound, Shape
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
        from OCP.collections import List_TopoDS_Shape
        import numpy as np
        uniq, seen = [], []
        for e in edges:  # coincident duplicates (e.g. two rectangles sharing a side) would break the parity count
            m, ln = e.position_at(0.5), e.length
            key = (ln, m.X, m.Y, m.Z)
            if not any(abs(ln - k[0]) < 1e-7 and (k[1] - m.X) ** 2 + (k[2] - m.Y) ** 2 + (k[3] - m.Z) ** 2 < 1e-14 for k in seen):
                uniq.append(e)
                seen.append(key)
        bb = Compound(uniq).bounding_box()
        size = bb.diagonal * 2 + 10
        c = bb.center()
        o = plane.origin + plane.x_dir * (c - plane.origin).dot(plane.x_dir) + plane.y_dir * (c - plane.origin).dot(plane.y_dir)
        big = Face.make_rect(size, size, Plane(origin=o, x_dir=plane.x_dir, z_dir=plane.z_dir))
        args, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
        args.Append(big.wrapped)
        for e in uniq:
            tools.Append(e.wrapped)
        sp = BRepAlgoAPI_Splitter()
        sp.SetArguments(args)
        sp.SetTools(tools)
        sp.Build()
        if not sp.IsDone():
            return []
        # sketch curves as 2D polyline segments (sketch coordinates) for the even-odd test, all in numpy
        o3 = np.array([plane.origin.X, plane.origin.Y, plane.origin.Z])
        ax = np.array([[plane.x_dir.X, plane.x_dir.Y, plane.x_dir.Z], [plane.y_dir.X, plane.y_dir.Y, plane.y_dir.Z]])
        seg_a, seg_b = [], []
        for e in uniq:
            n = 2 if e.geom_type.name.lower() == "line" else 96
            q = np.array([tuple(e.position_at(i / (n - 1))) for i in range(n)])
            uv = (q - o3) @ ax.T
            seg_a.append(uv[:-1])
            seg_b.append(uv[1:])
        A, B = np.concatenate(seg_a), np.concatenate(seg_b)
        E = B - A
        ang = 0.3712  # generic ray direction avoids passing exactly through vertices
        dx, dy = math.cos(ang), math.sin(ang)
        den = dx * E[:, 1] - dy * E[:, 0]
        use = np.abs(den) >= 1e-15
        A, E, den = A[use], E[use], den[use]
        faces, probes = [], []
        for f in Shape.cast(sp.Shape()).faces():
            verts, tris = f.tessellate(1e-3 * size, 0.2)
            if not tris:
                continue
            best = max(tris, key=lambda t: ((verts[t[1]] - verts[t[0]]).cross(verts[t[2]] - verts[t[0]])).length)
            c = (verts[best[0]] + verts[best[1]] + verts[best[2]]) * (1 / 3)
            faces.append(f)
            probes.append((np.array([c.X, c.Y, c.Z]) - o3) @ ax.T)
        if not faces:
            return []
        P = np.array(probes)  # (regions, 2): a point inside each region
        hits = np.zeros(len(P), int)
        step = max(1, 2_000_000 // max(len(A), 1))  # (regions x segments in chunks: bounded memory for big sketches)
        for i in range(0, len(P), step):
            p = P[i:i + step]
            rx = A[None, :, 0] - p[:, None, 0]
            ry = A[None, :, 1] - p[:, None, 1]
            t = (rx * E[None, :, 1] - ry * E[None, :, 0]) / den[None, :]
            u = (rx * dy - ry * dx) / den[None, :]
            hits[i:i + step] = ((t > 0) & (u >= 0) & (u < 1)).sum(axis=1)
        return [f for f, h in zip(faces, hits) if h % 2 == 1]

    def _profiles_by_wires(self, plane: Plane, edges) -> list[Face]:
        wires = [w for w in Wire.combine(edges, tol=1e-6) if w.is_closed]
        if not wires:
            raise SketchError("sketch has no closed contour")
        faces = [Face(w) for w in wires]
        depth, parent = [0] * len(wires), [None] * len(wires)
        for i, w in enumerate(wires):
            probe = w.edges()[0].position_at(0.5)
            for j, f in enumerate(faces):
                if i != j and faces[j].area > faces[i].area and _face_contains(f, probe):
                    depth[i] += 1
                    if parent[i] is None or faces[j].area < faces[parent[i]].area:
                        parent[i] = j
        result = []
        for i, w in enumerate(wires):
            if depth[i] % 2 == 0:
                holes = [wires[k] for k in range(len(wires)) if parent[k] == i and depth[k] == depth[i] + 1]
                result.append(Face(w, holes) if holes else Face(w))
        return result

    def axes(self, plane: Plane) -> dict[str, Axis]:
        """Every sketch line as a potential revolve/pattern axis, keyed by entity id."""
        out = {}
        for eid, e in self.entities.items():
            if e["type"] == "line":
                a = plane.from_local_coords(Vector(*self.coords(e["p1"]), 0))
                b = plane.from_local_coords(Vector(*self.coords(e["p2"]), 0))
                if (b - a).length > 1e-9:
                    out[eid] = Axis(a, (b - a).normalized())
        return out

    def summary(self) -> dict:
        return {
            "status": self.status,
            "entities": {k: dict(v) for k, v in self.entities.items()},
            "points": self.points,
            "relations": self.relations,
            "dimensions": self.dimensions,
        }


# ---------------------------------------------------------------------- internals
class _Getter:
    def __init__(self, sk: Sketch, x, idx):
        self.sk, self.x, self.idx = sk, x, idx

    def p(self, pid: str) -> np.ndarray:
        if pid == ORIGIN:
            return np.zeros(2)
        return np.array([self.x[self.idx[(pid, 0)]], self.x[self.idx[(pid, 1)]]])

    def r(self, eid: str) -> float:
        e = self.sk.entities[eid]
        if e["type"] == "circle":
            return self.x[self.idx[(eid, "r")]]
        if e["type"] == "arc":
            return float(np.linalg.norm(self.p(e["s"]) - self.p(e["c"])))
        raise SketchError(f"{eid} has no radius")

    def dist(self, a: str, b: str) -> float:
        return float(np.linalg.norm(self.p(a) - self.p(b)))

    def line(self, eid: str):
        e = self.sk.entities[eid]
        if e["type"] != "line":
            raise SketchError(f"{eid} is not a line")
        return self.p(e["p1"]), self.p(e["p2"])

    def center(self, ref: str) -> np.ndarray:
        if ref == ORIGIN or ref in self.sk.points:
            return self.p(ref)
        e = self.sk.entities[ref]
        if e["type"] in ("circle", "arc"):
            return self.p(e["c"])
        if e["type"] == "point":
            return self.p(e["p"])
        raise SketchError(f"{ref} has no centre")


def _kind(sk: Sketch, ref: str) -> str:
    if ref == ORIGIN or ref in sk.points:
        return "point"
    t = sk.entities[ref]["type"]
    return "point" if t == "point" else t


def _as_point(sk: Sketch, ref: str) -> str:
    if ref == ORIGIN or ref in sk.points:
        return ref
    e = sk.entities[ref]
    if e["type"] == "point":
        return e["p"]
    raise SketchError(f"{ref} is not a point")


def _entity_points(e: dict) -> list[str]:
    return [e[k] for k in ("p", "p1", "p2", "c", "s", "e", "m") if k in e] + list(e.get("pts", []))


def _cross(a, b) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def _point_line_signed(g: _Getter, p, line) -> float:
    a, b = g.line(line)
    return _cross(_unit(b - a), g.p(p) - a)


def _relation_residuals(sk: Sketch, r: dict, g: _Getter) -> list[float]:
    t, refs = r["type"], r["refs"]
    kinds = [_kind(sk, x) for x in refs]
    if t == "horizontal":
        if len(refs) == 1:
            a, b = g.line(refs[0]); return [a[1] - b[1]]
        return [g.p(_as_point(sk, refs[0]))[1] - g.p(_as_point(sk, refs[1]))[1]]
    if t == "vertical":
        if len(refs) == 1:
            a, b = g.line(refs[0]); return [a[0] - b[0]]
        return [g.p(_as_point(sk, refs[0]))[0] - g.p(_as_point(sk, refs[1]))[0]]
    if t == "coincident":
        if kinds == ["point", "point"]:
            return list(g.p(_as_point(sk, refs[0])) - g.p(_as_point(sk, refs[1])))
        pt, other = (refs[0], refs[1]) if kinds[0] == "point" else (refs[1], refs[0])
        pt = _as_point(sk, pt)
        if _kind(sk, other) == "line":
            return [_point_line_signed(g, pt, other)]
        return [float(np.linalg.norm(g.p(pt) - g.center(other))) - g.r(other)]
    if t in ("parallel", "perpendicular", "collinear"):
        a1, b1 = g.line(refs[0]); a2, b2 = g.line(refs[1])
        d1, d2 = _unit(b1 - a1), _unit(b2 - a2)
        if t == "parallel":
            return [_cross(d1, d2)]
        if t == "perpendicular":
            return [float(np.dot(d1, d2))]
        return [_cross(d1, a2 - a1), _cross(d1, b2 - a1)]
    if t == "equal":
        if kinds[0] == "line":
            a1, b1 = g.line(refs[0]); a2, b2 = g.line(refs[1])
            return [float(np.linalg.norm(b1 - a1) - np.linalg.norm(b2 - a2))]
        return [g.r(refs[0]) - g.r(refs[1])]
    if t == "tangent":
        if "line" in kinds:
            line, curve = (refs[0], refs[1]) if kinds[0] == "line" else (refs[1], refs[0])
            a, b = g.line(line)
            return [abs(_cross(_unit(b - a), g.center(curve) - a)) - g.r(curve)]
        dc = float(np.linalg.norm(g.center(refs[0]) - g.center(refs[1])))
        r1, r2 = g.r(refs[0]), g.r(refs[1])
        return [dc - (r1 + r2)] if abs(dc - (r1 + r2)) <= abs(dc - abs(r1 - r2)) else [dc - abs(r1 - r2)]
    if t == "concentric":
        return list(g.center(refs[0]) - g.center(refs[1]))
    if t == "midpoint":
        pt, line = (refs[0], refs[1]) if kinds[0] == "point" else (refs[1], refs[0])
        a, b = g.line(line)
        return list(g.p(_as_point(sk, pt)) - (a + b) / 2)
    if t == "symmetric":
        p1, p2, line = _as_point(sk, refs[0]), _as_point(sk, refs[1]), refs[2]
        a, b = g.line(line)
        d = _unit(b - a)
        m = (g.p(p1) + g.p(p2)) / 2
        return [_cross(d, m - a), float(np.dot(d, g.p(p2) - g.p(p1)))]
    if t == "fix":
        out = []
        for ref in refs:
            v = r["values"][ref]
            if isinstance(v, list):
                out += list(g.p(_as_point(sk, ref)) - np.array(v))
            else:
                for key in ("p", "p1", "p2", "c", "s", "e"):
                    if key in v:
                        out += list(g.p(sk.entities[ref][key]) - np.array(v[key]))
                if "r" in v and isinstance(v["r"], (int, float)):
                    out.append(g.r(ref) - v["r"])
        return out
    raise SketchError(f"unsupported relation {t}")


def _dim_measure(sk: Sketch, d: dict, g: _Getter, signed_target: bool = False) -> float:
    t, refs = d["type"], d["refs"]
    kinds = [_kind(sk, x) for x in refs]
    if t == "length":
        a, b = g.line(refs[0]); return float(np.linalg.norm(b - a))
    if t == "radius":
        return g.r(refs[0])
    if t == "diameter":
        return 2 * g.r(refs[0])
    if t == "angle":
        a1, b1 = g.line(refs[0]); a2, b2 = g.line(refs[1])
        d1, d2 = _unit(b1 - a1), _unit(b2 - a2)
        ang = math.degrees(math.atan2(_cross(d1, d2), float(np.dot(d1, d2))))
        if d.get("supplement"):
            ang = math.copysign(180.0 - abs(ang), ang)
        return abs(ang) if not signed_target else ang
    if t in ("hdistance", "vdistance"):
        a, b = (g.center(r) if _kind(sk, r) != "line" else g.line(r)[0] for r in refs[:2])
        v = (b - a)[0 if t == "hdistance" else 1]
        return abs(v) if not signed_target else float(v)
    if t == "distance":
        if len(refs) == 1:
            a, b = g.line(refs[0]); return float(np.linalg.norm(b - a))
        if "line" in kinds:
            if kinds.count("line") == 2:  # parallel lines: distance from first point of line 2
                p = sk.entities[refs[1]]["p1"]
                v = _point_line_signed(g, p, refs[0])
            else:
                line, pt = (refs[0], refs[1]) if kinds[0] == "line" else (refs[1], refs[0])
                c = g.center(pt)
                a, b = g.line(line)
                v = _cross(_unit(b - a), c - a)
            return abs(v) if not signed_target else float(v)
        return float(np.linalg.norm(g.center(refs[0]) - g.center(refs[1])))
    raise SketchError(f"unsupported dimension {t}")


def _dim_target(sk: Sketch, d: dict, g: _Getter) -> float:
    """Target value with the sign of the current configuration for signed measures."""
    v = d["value"]
    if d["type"] == "angle":
        cur = _dim_measure(sk, d, g, signed_target=True)
        sgn = 1.0 if cur >= 0 else -1.0
        # allow the supplementary configuration that is currently closest
        cands = [sgn * v, sgn * (180 - v) if abs(cur) > 90 and v < 90 else sgn * v]
        return min(cands, key=lambda c: abs(c - cur))
    if d["type"] in ("hdistance", "vdistance") or (d["type"] == "distance" and "line" in [_kind(sk, r) for r in d["refs"]]):
        cur = _dim_measure(sk, d, g, signed_target=True)
        return v if cur >= 0 else -v
    return v


SOLVE_SECONDS = 4.0  # time box for one sketch solve (plus 20 ms per unknown)


class _OutOfTime(Exception):
    pass


# OPENSWX_SKETCH_SOLVER=legacy: the solver as it was before the 2026-11 speed-up (always search, grouped Jacobian
# only over 40 unknowns) - for comparing survey runs (tools/survey.py) with and without the change
_LEGACY = os.environ.get("OPENSWX_SKETCH_SOLVER", "").strip().lower() == "legacy"


def _sparse_from() -> int:
    return 40 if _LEGACY else SPARSE_FROM


SPARSE_FROM = 12  # unknowns above which the Jacobian is built by column groups (was 40: every sketch of a few
# dimensioned lines paid one residual evaluation per unknown, per iteration)


class _TimedResiduals:
    """The residual function, remembering the best point evaluated and stopping the solver at a deadline."""

    def __init__(self, f, x0, seconds: float):
        import time
        self.f, self.deadline, self._time = f, time.monotonic() + seconds, time.monotonic
        self.best_x, self.best = np.asarray(x0, float).copy(), math.inf

    def __call__(self, x):
        r = self.f(x)
        cost = float(r @ r)
        if cost < self.best:
            self.best, self.best_x = cost, np.asarray(x, float).copy()
        if self._time() > self.deadline:
            raise _OutOfTime
        return r


_GROUPS: dict = {}


def _numeric_jac(f, x, sparsity=None, h=1e-7):
    """Forward-difference Jacobian; with a sparsity pattern, columns no residual shares are perturbed
    together (one evaluation per group instead of per unknown)."""
    f0 = f(x)
    J = np.zeros((len(f0), len(x)))
    if sparsity is not None:
        key = id(sparsity)
        plan = _GROUPS.get(key)
        if plan is None or plan[0] is not sparsity:
            from scipy.optimize._numdiff import group_columns
            pat = sparsity.tocsc()
            groups = group_columns(pat)
            steps = []
            for g in range(int(groups.max()) + 1 if len(groups) else 0):
                cols = np.nonzero(groups == g)[0]
                sub = pat[:, cols].tocoo()
                steps.append((cols, sub.row, cols[sub.col]))
            if len(_GROUPS) > 64:
                _GROUPS.clear()
            plan = _GROUPS[key] = (sparsity, steps)
        for cols, rows, rcols in plan[1]:
            xp = x.copy()
            xp[cols] += h
            df = (f(xp) - f0) / h
            J[rows, rcols] = df[rows]
        return J
    for i in range(len(x)):
        xp = x.copy(); xp[i] += h
        J[:, i] = (f(xp) - f0) / h
    return J


def _circumcenter(a, b, c):
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        raise SketchError("three-point arc points are collinear")
    ux = ((a @ a) * (b[1] - c[1]) + (b @ b) * (c[1] - a[1]) + (c @ c) * (a[1] - b[1])) / d
    uy = ((a @ a) * (c[0] - b[0]) + (b @ b) * (a[0] - c[0]) + (c @ c) * (b[0] - a[0])) / d
    return np.array([ux, uy])


def _entity_edge(sk: Sketch, e: dict, plane: Plane) -> Edge:
    P = lambda pid: plane.from_local_coords(Vector(*sk.coords(pid), 0))  # noqa: E731
    if e["type"] == "line":
        return Edge.make_line(P(e["p1"]), P(e["p2"]))
    if e["type"] == "circle":
        c = P(e["c"])
        return Edge.make_circle(e["r"], Plane(origin=c, x_dir=plane.x_dir, z_dir=plane.z_dir))
    if e["type"] == "arc":
        c = np.array(sk.coords(e["c"])); s = np.array(sk.coords(e["s"])); en = np.array(sk.coords(e["e"]))
        r = np.linalg.norm(s - c)
        a0 = math.atan2(*(s - c)[::-1]); a1 = math.atan2(*(en - c)[::-1])
        if a1 <= a0:
            a1 += 2 * math.pi
        am = (a0 + a1) / 2
        mid = c + r * np.array([math.cos(am), math.sin(am)])
        endp = c + r * np.array([math.cos(a1), math.sin(a1)])
        to3 = lambda q: plane.from_local_coords(Vector(float(q[0]), float(q[1]), 0))  # noqa: E731
        return Edge.make_three_point_arc(P(e["s"]), to3(mid), to3(endp))
    if e["type"] == "ellipse":
        c, m = np.array(sk.coords(e["c"])), np.array(sk.coords(e["m"]))
        a, b = float(np.linalg.norm(m - c)), float(e["b"])
        u = (m - c) / a
        if b > a:  # keep the OCC major >= minor convention by swapping the axes
            a, b, u = b, a, np.array([-u[1], u[0]])
        xd = plane.x_dir * float(u[0]) + plane.y_dir * float(u[1])
        return Edge.make_ellipse(a, b, Plane(origin=P(e["c"]), x_dir=xd, z_dir=plane.z_dir))
    if e["type"] == "conic":  # rational quadratic Bezier: ends, apex weighted rho / (1 - rho)
        rho = float(e["rho"])
        return _bspline_edge({"poles": [sk.coords(e["s"]), sk.coords(e["m"]), sk.coords(e["e"])], "knots": [0, 0, 0, 1, 1, 1],
                              "degree": 2, "weights": [1.0, rho / (1.0 - rho), 1.0]}, plane)
    if e["type"] == "spline":
        if e.get("bspline"):  # exact B-spline (e.g. read from a .sldprt): poles in sketch coordinates
            return _bspline_edge(e["bspline"], plane)
        pts = [P(q) for q in e["pts"]]
        if e.get("closed") and len(pts) > 2:
            return Edge.make_spline(pts, periodic=True)
        return Edge.make_spline(pts)
    raise SketchError(f"cannot convert {e['type']}")


def _bspline_edge(b: dict, plane: Plane) -> Edge:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.Geom import Geom_BSplineCurve
    from OCP.collections import Array1_double as TColStd_Array1OfReal, Array1_gp_Pnt as TColgp_Array1OfPnt
    from OCP.collections import Array1_int as TColStd_Array1OfInteger
    poles = TColgp_Array1OfPnt(1, len(b["poles"]))
    for i, q in enumerate(b["poles"], 1):
        poles.SetValue(i, plane.from_local_coords(Vector(float(q[0]), float(q[1]), 0)).to_pnt())
    uniq, mults = [], []
    for k in b["knots"]:
        if uniq and abs(k - uniq[-1]) < 1e-12:
            mults[-1] += 1
        else:
            uniq.append(float(k))
            mults.append(1)
    knots, mult = TColStd_Array1OfReal(1, len(uniq)), TColStd_Array1OfInteger(1, len(uniq))
    for i, (k, m) in enumerate(zip(uniq, mults), 1):
        knots.SetValue(i, k)
        mult.SetValue(i, m)
    if b.get("weights"):  # rational (conics, curves taken from a B-rep)
        w = TColStd_Array1OfReal(1, len(b["weights"]))
        for i, v in enumerate(b["weights"], 1):
            w.SetValue(i, float(v))
        curve = Geom_BSplineCurve(poles, w, knots, mult, int(b["degree"]), bool(b.get("periodic", False)))
    else:
        curve = Geom_BSplineCurve(poles, knots, mult, int(b["degree"]), bool(b.get("periodic", False)))
    return Edge(BRepBuilderAPI_MakeEdge(curve).Edge())


def _text_shape(sk: Sketch, e: dict, plane: Plane):
    from build123d import Align, Location, Text
    x, y = sk.coords(e["p"])
    try:
        txt = Text(e["text"], font_size=e["height"], font=e.get("font") or "Arial", align=(Align.MIN, Align.MIN))
    except Exception:  # unknown font -> build123d default
        txt = Text(e["text"], font_size=e["height"], align=(Align.MIN, Align.MIN))
    txt = txt.moved(Location((x, y, 0), (0, 0, 1), e.get("angle", 0.0)))
    return txt.moved(plane.location)


def _entity_edges(sk: Sketch, e: dict, plane: Plane) -> list[Edge]:
    """All 3D edges of a sketch entity (text expands to its letter outlines)."""
    if e["type"] == "text":
        return list(_text_shape(sk, e, plane).edges())
    return [_entity_edge(sk, e, plane)]


def _intersect(sk: Sketch, e: dict, o: dict, infinite: bool = False) -> list[np.ndarray]:
    """2D intersection points of entity e with entity o (o lines are finite; arcs respected)."""
    P = lambda pid: np.array(sk.coords(pid), float)  # noqa: E731
    out = []
    if e["type"] == "line":
        a, b = P(e["p1"]), P(e["p2"])
        if o["type"] == "line":
            c, d = P(o["p1"]), P(o["p2"])
            r, s = b - a, d - c
            den = _cross(r, s)
            if abs(den) < 1e-12:
                return []
            t = _cross(c - a, s) / den
            u = _cross(c - a, r) / den
            if -1e-9 <= u <= 1 + 1e-9 and (infinite or -1e-9 <= t <= 1 + 1e-9):
                out.append(a + r * t)
            return out
        c = P(o["c"])
        rad = o["r"] if o["type"] == "circle" else float(np.linalg.norm(P(o["s"]) - c))
        d = b - a
        A, B, C = d @ d, 2 * d @ (a - c), (a - c) @ (a - c) - rad * rad
        disc = B * B - 4 * A * C
        if disc < -1e-12 or A < 1e-15:
            return []
        for sgn in (-1, 1):
            t = (-B + sgn * math.sqrt(max(disc, 0))) / (2 * A)
            q = a + d * t
            if (infinite or -1e-9 <= t <= 1 + 1e-9) and (o["type"] == "circle" or _on_arc(sk, o, math.atan2(q[1] - c[1], q[0] - c[0]))):
                out.append(q)
        return out
    # e is a circle/arc
    if o["type"] == "line":
        return _intersect(sk, o, e)
    c1, c2 = P(e["c"]), P(o["c"])
    r1 = e["r"] if e["type"] == "circle" else float(np.linalg.norm(P(e["s"]) - c1))
    r2 = o["r"] if o["type"] == "circle" else float(np.linalg.norm(P(o["s"]) - c2))
    dvec = c2 - c1
    dd = float(np.linalg.norm(dvec))
    if dd < 1e-12 or dd > r1 + r2 + 1e-9 or dd < abs(r1 - r2) - 1e-9:
        return []
    aa = (r1 * r1 - r2 * r2 + dd * dd) / (2 * dd)
    h = math.sqrt(max(r1 * r1 - aa * aa, 0))
    m = c1 + dvec * (aa / dd)
    perp = np.array([-dvec[1], dvec[0]]) / dd
    for q in ([m] if h < 1e-12 else [m + perp * h, m - perp * h]):
        if o["type"] == "circle" or _on_arc(sk, o, math.atan2(q[1] - c2[1], q[0] - c2[0])):
            out.append(q)
    return out


def _on_arc(sk: Sketch, arc: dict, ang: float) -> bool:
    c = np.array(sk.coords(arc["c"]))
    s = np.array(sk.coords(arc["s"])) - c
    e = np.array(sk.coords(arc["e"])) - c
    a0 = math.atan2(s[1], s[0])
    span = (math.atan2(e[1], e[0]) - a0) % (2 * math.pi)
    return (ang - a0) % (2 * math.pi) <= span + 1e-9


def _face_contains(face: Face, point: Vector) -> bool:
    from OCP.gp import gp_Pnt
    c = BRepClass_FaceClassifier(face.wrapped, gp_Pnt(point.X, point.Y, point.Z), 1e-7)
    return c.State() == TopAbs_IN
