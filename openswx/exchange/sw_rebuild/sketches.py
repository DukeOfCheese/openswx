"""Sketch decoding, relations, dimensions and section recovery for the SOLIDWORKS history rebuild."""
from __future__ import annotations

import copy
import itertools
import math

from openswx.core.document import DocumentError, PartDocument

from ._base import (_MOVED_LATER, _Oracle, _REVOLVE_AXES, _TAGGED, _TRUST, RebuildError, _check_time, _exact,
                    _faces_on_body, _log, _plane, _tagged_ok, _volume)

def _sketch_dict(sk: dict) -> tuple[dict, list[str]]:
    """An openswx sketch (params["sketch"]) from a decoded SOLIDWORKS sketch; also its centerline ids."""
    points, ents, n = {}, {}, 0
    pid = {}

    def p(i):
        nonlocal n
        if i not in pid:
            n += 1
            pid[i] = f"P{n}"
            points[pid[i]] = [float(v) for v in sk["points"][i]]
        return pid[i]

    degree: dict = {}
    for a, b in sk["lines"]:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    ends = [(s, e) for _, s, e in sk["arcs"] if s != e] + [(sp["pts"][0], sp["pts"][-1]) for sp in sk.get("splines", [])]
    for s, e in ends:
        degree[s] = degree.get(s, 0) + 1
        degree[e] = degree.get(e, 0) + 1
    axes = []
    line_ids, arc_ids = {}, {}  # decoded line (a, b) / arc centre + ends -> entity id
    cons_used = set()  # a flagged pair marks one line: a centerline can coincide with a profile edge
    for a, b in sk["lines"]:
        n += 1
        eid = f"Line{n}"
        line_ids[frozenset((a, b))] = eid
        # a lone segment is taken for a centerline (revolve axis); construction lines are flagged in their records
        # (a flagged construction line that closes a chain, e.g. a centerline along a revolve profile, is
        # treated as profile geometry: SOLIDWORKS closes such profiles with it)
        closes = degree[a] == 2 or degree[b] == 2
        flagged = frozenset((a, b)) in sk.get("construction", ()) and frozenset((a, b)) not in cons_used
        if flagged:
            cons_used.add(frozenset((a, b)))
        open_end = (degree[a] == 1 and degree[b] == 1) or (flagged and not closes)
        if flagged and closes:
            axes.append(eid)  # still a revolve axis candidate
        ents[eid] = {"type": "line", "p1": p(a), "p2": p(b), "construction": open_end}
        if open_end:
            axes.append(eid)
    for c, s, e in sk["arcs"]:
        n += 1
        if s == e:
            r = math.dist(sk["points"][c], sk["points"][s])
            ents[f"Circle{n}"] = {"type": "circle", "c": p(c), "r": r, "construction": False}
            arc_ids[(c, frozenset((s, e)))] = f"Circle{n}"
        else:
            ents[f"Arc{n}"] = {"type": "arc", "c": p(c), "s": p(s), "e": p(e), "construction": False}
            arc_ids[(c, frozenset((s, e)))] = f"Arc{n}"
    for c, far, minor in sk.get("ellipses", []):
        n += 1
        ents[f"Ellipse{n}"] = {"type": "ellipse", "c": p(c), "m": p(far), "b": float(minor), "construction": False}
    for sp in sk.get("splines", []):
        n += 1
        ents[f"Spline{n}"] = {"type": "spline", "pts": [p(i) for i in sp["pts"]], "closed": False, "construction": False,
                              **({"bspline": sp["bspline"]} if "bspline" in sp else {})}
    # the sketch's own relations (decoded), then relations read off the geometry where none was decoded: lines
    # that are exactly horizontal / vertical (what SOLIDWORKS adds to rectangles and most sketched lines), so
    # edited dimensions move the geometry as expected
    rels = []
    for r in _decoded_relations(sk, pid, line_ids, arc_ids):
        n += 1
        rels.append({"id": f"R{n}", **r})
    for eid, e in ents.items():
        if e["type"] == "line" and not e["construction"]:
            (x1, y1), (x2, y2) = points[e["p1"]], points[e["p2"]]
            if abs(y1 - y2) <= 1e-9 * max(1.0, abs(x1 - x2)) and abs(x1 - x2) > 1e-9:
                n += 1
                rels.append({"id": f"R{n}", "type": "horizontal", "refs": [eid]})
            elif abs(x1 - x2) <= 1e-9 * max(1.0, abs(y1 - y2)) and abs(y1 - y2) > 1e-9:
                n += 1
                rels.append({"id": f"R{n}", "type": "vertical", "refs": [eid]})
    # a line ending on an arc along the arc's tangent there: tangent (fillets, slots)
    for lid, ln in ents.items():
        if ln["type"] != "line" or ln["construction"]:
            continue
        for aid, ar in ents.items():
            if ar["type"] != "arc" or ar["construction"]:
                continue
            for end in (ln["p1"], ln["p2"]):
                if end not in (ar["s"], ar["e"]):
                    continue
                other = ln["p2"] if end == ln["p1"] else ln["p1"]
                (ex, ey), (ox, oy), (cx, cy) = points[end], points[other], points[ar["c"]]
                dx, dy, rx, ry = ox - ex, oy - ey, ex - cx, ey - cy
                if abs(dx * rx + dy * ry) <= 1e-7 * max(1.0, math.hypot(dx, dy) * math.hypot(rx, ry)):
                    n += 1
                    rels.append({"id": f"R{n}", "type": "tangent", "refs": [lid, aid]})
                    break
    # relations that cannot repeat the ones above: perpendicular slanted lines meeting at a point, arcs meeting
    # along a common tangent, arcs / circles about the same centre written as separate points
    hv = {r["refs"][0] for r in rels if r["type"] in ("horizontal", "vertical")}
    lines = [(k, e) for k, e in ents.items() if e["type"] == "line" and not e["construction"] and k not in hv]
    for i, (la, a_) in enumerate(lines):
        for lb, b_ in lines[i + 1:]:
            common = {a_["p1"], a_["p2"]} & {b_["p1"], b_["p2"]}
            if len(common) != 1:
                continue
            (ax1, ay1), (ax2, ay2) = points[a_["p1"]], points[a_["p2"]]
            (bx1, by1), (bx2, by2) = points[b_["p1"]], points[b_["p2"]]
            ua, ub = (ax2 - ax1, ay2 - ay1), (bx2 - bx1, by2 - by1)
            if abs(ua[0] * ub[0] + ua[1] * ub[1]) <= 1e-9 * math.hypot(*ua) * math.hypot(*ub):
                n += 1
                rels.append({"id": f"R{n}", "type": "perpendicular", "refs": [la, lb]})
    round_ = [(k, e) for k, e in ents.items() if e["type"] in ("arc", "circle") and not e["construction"]]
    for i, (ka, a_) in enumerate(round_):
        for kb, b_ in round_[i + 1:]:
            ca, cb = points[a_["c"]], points[b_["c"]]
            if a_["c"] != b_["c"] and math.dist(ca, cb) <= 1e-9 * max(1.0, abs(ca[0]) + abs(ca[1])):
                n += 1
                rels.append({"id": f"R{n}", "type": "concentric", "refs": [ka, kb]})
                continue
            if a_["type"] != "arc" or b_["type"] != "arc":
                continue
            common = {a_["s"], a_["e"]} & {b_["s"], b_["e"]}
            if len(common) != 1 or a_["c"] == b_["c"]:
                continue
            q = points[next(iter(common))]
            ra, rb = (q[0] - ca[0], q[1] - ca[1]), (q[0] - cb[0], q[1] - cb[1])
            if abs(ra[0] * rb[1] - ra[1] * rb[0]) <= 1e-9 * math.hypot(*ra) * math.hypot(*rb):  # centres in line
                n += 1
                rels.append({"id": f"R{n}", "type": "tangent", "refs": [ka, kb]})
    seen, kept = set(), []  # (an inferred relation the sketch already has: once)
    for r in rels:
        key = (r["type"], frozenset(r["refs"]))
        if key not in seen:
            seen.add(key)
            kept.append(r)
    data = {"points": points, "entities": ents, "relations": kept, "dimensions": [], "counter": n, "status": {}}
    data["dimensions"] = _sketch_dimensions(sk, data, pid, line_ids, arc_ids)
    return data, axes


# swConstraintType_e codes of decoded relations -> openswx relation types (others are left to the geometry)
_REL_TYPES = {4: "horizontal", 5: "vertical", 6: "tangent", 7: "parallel", 8: "perpendicular", 10: "concentric",
              12: "midpoint", 14: "equal"}


def _decoded_relations(sk: dict, pid: dict, line_ids: dict, arc_ids: dict) -> list:
    """The sketch's decoded relations whose references resolve to rebuilt entities: [{"type", "refs"}]."""
    handles, order = sk.get("handles", {}), sk.get("point_order", [])
    out = []
    for r in sk.get("relations", []):
        kind = _REL_TYPES.get(r["type"])
        if kind is None:
            continue
        refs = []
        for cls, idx in r["refs"]:
            h = handles.get((cls, idx))
            if h and h[0] == "line" and frozenset(h[1]) in line_ids:
                refs.append(line_ids[frozenset(h[1])])
            elif h and h[0] == "arc" and (h[1][0], frozenset(h[1][1:])) in arc_ids:
                refs.append(arc_ids[(h[1][0], frozenset(h[1][1:]))])
            elif cls == "sgPointHandle" and idx < len(order) and order[idx] in pid:
                refs.append(pid[order[idx]])
            else:
                break
        # (only with as many references as the relation takes: a midpoint whose point is the sketch origin,
        # for one, lists only its line)
        need = 1 if kind in ("horizontal", "vertical") else 2
        # and only on the kinds of entity it applies to (a handle index can name another kind of segment)
        kinds = sorted("line" if x.startswith("Line") else "point" if x.startswith("P") else "round" for x in refs)
        allowed = {"horizontal": [["line"]], "vertical": [["line"]], "parallel": [["line", "line"]],
                   "perpendicular": [["line", "line"]], "tangent": [["line", "round"], ["round", "round"]],
                   "concentric": [["round", "round"]], "midpoint": [["line", "point"]],
                   "equal": [["line", "line"], ["round", "round"]]}[kind]
        if len(refs) == len(r["refs"]) == need and len(set(refs)) == len(refs) and kinds in allowed:
            out.append({"type": kind, "refs": refs})
    return out


def _sketch_dimensions(sk: dict, data: dict, pid: dict, line_ids: dict, arc_ids: dict) -> list:
    """openswx dimensions for the decoded SOLIDWORKS sketch dimensions whose references resolve and whose value
    the geometry already has (so they drive the sketch without moving it)."""
    from openswx.core.sketch import Sketch
    refmap, handles, order = sk.get("refmap", {}), sk.get("handles", {}), sk.get("point_order", [])
    probe = Sketch(data)
    out = []
    for dim in sk.get("dimensions", []):
        refs = []
        for word, idx in dim["refs"]:
            cls = refmap.get(word)
            h = handles.get((cls, idx)) if cls else None
            if h and h[0] == "line" and frozenset(h[1]) in line_ids:
                refs.append(("line", line_ids[frozenset(h[1])]))
            elif h and h[0] == "arc" and (h[1][0], frozenset(h[1][1:])) in arc_ids:
                refs.append(("arc", arc_ids[(h[1][0], frozenset(h[1][1:]))]))
            elif cls is None and idx < len(order) and order[idx] in pid:  # (a point of the sketch geometry)
                refs.append(("point", pid[order[idx]]))
            else:
                break
        if len(refs) != len(dim["refs"]):
            continue
        kinds = sorted(k for k, _ in refs)
        if dim["angle"]:
            types = ["angle"] if kinds == ["line", "line"] else []
            values = [math.degrees(v) for v in dim["values"]]
        else:
            types = ({1: {"line": ["length"], "arc": ["diameter", "radius"], "point": []}}.get(len(refs), {})
                     .get(kinds[0], []) if len(refs) == 1 else ["distance", "hdistance", "vdistance"])
            values = [v * 1000.0 for v in dim["values"]]
        for t in types:
            d = {"id": f"D{len(out) + 1}", "type": t, "refs": [r for _, r in refs], "driven": False}
            try:
                got = probe.measure(d)
            except Exception:
                continue
            match = next((v for v in values if abs(got - v) <= 1e-6 * max(1.0, abs(v))), None)
            if match is not None and not any(sorted(o["refs"]) == sorted(d["refs"]) for o in out):
                out.append({**d, "value": match})
                break
    return out

def _orient_arcs(sk: dict, oracle: "_Oracle") -> None:
    """Pick each arc's sweep (start -> end counter-clockwise, or the complement) whose midpoint lies on the
    stored body's boundary; arcs where neither or both do keep the stream order (settled later)."""
    import numpy as np
    from build123d import Vector
    R, t = sk["R"], sk["t"]

    def mid(c, s, e):
        a0 = math.atan2(s[1] - c[1], s[0] - c[0])
        a1 = math.atan2(e[1] - c[1], e[0] - c[0])
        a = a0 + ((a1 - a0) % (2 * math.pi)) / 2
        r = math.dist(c, s)
        p = R @ np.array([c[0] + r * math.cos(a), c[1] + r * math.sin(a), 0.0]) + t
        return Vector(*map(float, p))

    out = []
    for c, s, e in sk["arcs"]:
        if s != e:
            P = sk["points"]
            here, there = oracle.on_boundary(mid(P[c], P[s], P[e])), oracle.on_boundary(mid(P[c], P[e], P[s]))
            if here and there:  # (both on a face lying in the sketch plane: the side the feature swept stays on
                # the boundary a little way off the plane)
                n = Vector(*map(float, R @ np.array([0.0, 0.0, 1.0])))

                def swept(q):
                    return any(oracle.on_boundary(q + n * d) for d in (0.05, -0.05))
                here, there = swept(mid(P[c], P[s], P[e])), swept(mid(P[c], P[e], P[s]))
            if there and not here:
                s, e = e, s
        out.append((c, s, e))
    sk["arcs"] = out

def _reach(oracle: "_Oracle", sk: dict) -> list[float]:
    """Blind depths that take a sketch's extrusion as far as the stored body goes along the sketch normal,
    either way (for end conditions whose target face is not decoded)."""
    if _MOVED_LATER[0]:
        return []
    import numpy as np
    n, o = sk["R"][:, 2], sk["t"]
    ds = [float(np.dot(np.array([v.X, v.Y, v.Z]) - o, n)) for v in oracle.shape.vertices()]
    return sorted({round(abs(d), 9) for d in (max(ds), min(ds)) if abs(d) > 1e-6})

def _loose_axes(sk: dict) -> list[dict]:
    """Axes through pairs of the sketch's points that no decoded entity uses (a centerline whose segment
    references were not decoded), in model coordinates."""
    import numpy as np
    used = {i for a, b in sk["lines"] for i in (a, b)} | {i for arc in sk["arcs"] for i in arc}
    used |= {i for sp in sk.get("splines", []) for i in sp["pts"]}
    loose = [i for i in sk["points"] if i not in used][:8]
    out = []
    for a, b in itertools.combinations(loose, 2):
        pa, pb = (sk["R"] @ np.array([*sk["points"][i], 0.0]) + sk["t"] for i in (a, b))
        if np.linalg.norm(pb - pa) > 1e-6:
            out.append({"origin": [float(v) for v in pa], "direction": [float(v) for v in (pb - pa) / np.linalg.norm(pb - pa)]})
    return out

def _revolve_angles(angles: list[float], lengths: list[float]) -> list[float]:
    """Candidate revolve angles (degrees). An untagged angle dimension can be taken for a length by its name,
    so without an angle a 'length' up to 2 pi (read in mm, i.e. radians * 1000) is offered as radians too."""
    if angles:
        out = [angles[0]]
    else:
        out = [math.degrees(v / 1000.0) for v in lengths[:1] if 0 < v / 1000.0 <= 2 * math.pi + 1e-9] + [360.0]
    return [360.0 if abs(g - 360.0) < 1e-6 else g for g in out]  # 2 pi comes back as 360.0000000000008

def _edges_sketch(edges, R, t, closing: list | None = None) -> dict | None:
    """Section edges as an openswx sketch (lines, circles, arcs and exact (rational) B-splines) in the frame
    (R, t); `closing`: extra segments [(p, q)] in sketch coordinates. None when a curve cannot be converted."""
    import numpy as np
    from build123d import Shape, Vector
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert
    from OCP.Geom import Geom_BSplineCurve
    pts, ents, ids = {}, {}, {}

    def loc(v):
        q = R.T @ (np.array([v.X, v.Y, v.Z]) - t)
        return [float(q[0]), float(q[1])]

    def pid(xy):
        key = (round(xy[0], 5), round(xy[1], 5))
        if key not in ids:
            ids[key] = f"P{len(ids) + 1}"
            pts[ids[key]] = list(xy)
        return ids[key]
    for k, e in enumerate(edges, 1):
        kind = e.geom_type.name
        if kind == "LINE":
            ents[f"Line{k}"] = {"type": "line", "p1": pid(loc(e.position_at(0))), "p2": pid(loc(e.position_at(1))),
                                "construction": False}
        elif kind == "CIRCLE":
            c = loc(e.arc_center)
            if e.is_closed:
                ents[f"Circle{k}"] = {"type": "circle", "c": pid(c), "r": float(e.radius), "construction": False}
            else:
                a, m, b = loc(e.position_at(0)), loc(e.position_at(0.5)), loc(e.position_at(1))
                ccw = (a[0] - c[0]) * (m[1] - c[1]) - (a[1] - c[1]) * (m[0] - c[0]) > 0
                s_, e_ = (a, b) if ccw else (b, a)
                ents[f"Arc{k}"] = {"type": "arc", "c": pid(c), "s": pid(s_), "e": pid(e_), "construction": False}
        else:  # any other curve, exactly, as a (rational) B-spline
            nurbs = Shape.cast(BRepBuilderAPI_NurbsConvert(e.wrapped, True).Shape()).edges()[0]
            u0, u1 = BRep_Tool.Range_s(nurbs.wrapped)
            curve = BRep_Tool.Curve_s(nurbs.wrapped, u0, u1)
            bs = curve.Copy() if isinstance(curve, Geom_BSplineCurve) else None  # (OCP returns the concrete type)
            if not isinstance(bs, Geom_BSplineCurve):
                return None
            if not bs.IsPeriodic():
                bs.Segment(u0, u1)
            poles = [loc(Vector(bs.Pole(i).X(), bs.Pole(i).Y(), bs.Pole(i).Z())) for i in range(1, bs.NbPoles() + 1)]
            knots = [bs.Knot(i) for i in range(1, bs.NbKnots() + 1) for _ in range(bs.Multiplicity(i))]
            weights = [bs.Weight(i) for i in range(1, bs.NbPoles() + 1)] if bs.IsRational() else None
            ends = [pid(loc(nurbs.position_at(0))), pid(loc(nurbs.position_at(1)))]
            ents[f"Spline{k}"] = {"type": "spline", "pts": ends, "closed": bool(e.is_closed), "construction": False,
                                  "bspline": {"degree": bs.Degree(), "knots": knots, "poles": poles,
                                              **({"weights": weights} if weights else {}),
                                              **({"periodic": True} if bs.IsPeriodic() else {})}}
    for k, (a, b) in enumerate(closing or [], 1):
        ents[f"Axis{k}"] = {"type": "line", "p1": pid(a), "p2": pid(b), "construction": False}
    return {"points": pts, "entities": ents, "relations": [], "dimensions": [], "counter": len(ents) + len(pts),
            "status": {}}

def _section_sketches(oracle: "_Oracle", sk: dict) -> list[dict]:
    """Sketches recovered from the stored body when a sketch's segments are not decoded: its section just
    above and just below the sketch plane, as lines, circles, arcs and (rational) B-splines in sketch
    coordinates. For an extrusion this is its profile (plus any later cuts through it). For a revolve
    (_REVOLVE_AXES set: standard axes through the origin) also the half-sections: the body cut by the half of
    the sketch plane on either side of an axis lying in it, closed along the axis - a revolved profile."""
    from build123d import Shape
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    R, t = sk["R"], sk["t"]
    n = R[:, 2]
    out = []
    for d in (1e-3, -1e-3):
        o = t + n * d
        try:
            op = BRepAlgoAPI_Section(oracle.shape.wrapped, gp_Pln(gp_Pnt(*map(float, o)), gp_Dir(*map(float, n))), False)
            op.Approximation(True)  # (else freeform sections come as 1000-point polylines: slow, inexact booleans)
            op.Build()
            edges = Shape.cast(op.Shape()).edges() if op.IsDone() else []
        except Exception:
            edges = []
        if edges:
            got = _edges_sketch(edges, R, t)
            if got:
                out.append(got)
    if _REVOLVE_AXES[0] and abs(float(n @ t)) < 1e-6:  # (the plane holds the origin)
        out += _half_sections(oracle, R, t, _REVOLVE_AXES[0])
    return out

def _half_sections(oracle: "_Oracle", R, t, axes: list) -> list[dict]:
    """Per axis lying in the sketch plane and per side of it: the body's section by a half-plane bounded by the
    axis, closed along the axis. The half-plane is turned about the axis to where the section is largest
    (a revolved body is the same in every one, unless a later cut - a cross recess, a slot - runs through
    it) and the section turned back into the sketch plane."""
    import numpy as np
    from build123d import Axis, Face, Shape, Wire
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    n = R[:, 2]
    bb = oracle.shape.bounding_box()
    L = 2.0 * float(bb.diagonal) + 1.0
    out = []

    def half(a, b, side):
        corners = [-a * L, a * L, a * L + side * b * L, -a * L + side * b * L]
        try:
            face = Face(Wire.make_polygon([tuple(map(float, c)) for c in corners], close=True))
            op = BRepAlgoAPI_Section(oracle.shape.wrapped, face.wrapped, False)
            op.Approximation(True)
            op.Build()
            edges = Shape.cast(op.Shape()).edges() if op.IsDone() else []
        except Exception:
            return [], []
        on_axis = lambda e: all(np.linalg.norm(np.cross(np.array(tuple(e.position_at(u))), a)) < 1e-6  # noqa: E731
                                for u in (0.0, 0.5, 1.0))
        edges = [e for e in edges if not on_axis(e)]  # (where the body meets the axis: the closing line below)
        on = sorted({round(float(np.array(tuple(e.position_at(u))) @ a), 9) for e in edges for u in (0.0, 1.0)
                     if np.linalg.norm(np.cross(np.array(tuple(e.position_at(u))), a)) < 1e-6})
        spans = list(zip(on[0::2], on[1::2])) if len(on) >= 2 else []
        return edges, spans

    def area(edges, spans, a):
        from build123d import Edge
        try:
            ws = Wire.combine(list(edges) + [Edge.make_line(tuple(a * lo), tuple(a * hi)) for lo, hi in spans])
            return sum(Face(w).area for w in ws if w.is_closed)
        except Exception:
            return 0.0
    for a in axes:
        a = np.asarray(a, float)
        if abs(float(a @ n)) > 1e-9:
            continue  # not in the sketch plane
        b0 = np.cross(n, a)
        for side in (1.0, -1.0):
            best = None
            for deg in (0.0, 15.0, 30.0, 45.0, 60.0, 75.0):
                th = math.radians(deg) * side
                b = b0 * math.cos(th) + np.cross(a, b0) * math.sin(th)  # b0 turned about a
                edges, spans = half(a, b, side)
                if not edges:
                    continue
                ar = area(edges, spans, a)
                if best is None or ar > best[0] + 1e-9:
                    best = (ar, deg, edges, spans)
            if best is None:
                continue
            ar, deg, edges, spans = best
            if deg:  # back into the sketch plane
                edges = [e.rotate(Axis((0, 0, 0), tuple(map(float, a))), -deg * side) for e in edges]
            closing = [([float(v) for v in (R.T @ (a * lo - t))[:2]], [float(v) for v in (R.T @ (a * hi - t))[:2]])
                       for lo, hi in spans]
            got = _edges_sketch(edges, R, t, closing)
            if got:
                out.append(got)
    return out

def _consume(doc: PartDocument, oracle: "_Oracle", sketch: str, sk: dict, feature: str, build) -> None:
    """Build `feature` (`build` adds it) from `sketch`. The sketch's plane is its decoded frame unless that was
    not found (then its candidate planes are tried too); when the decoded segments do not build, the profile
    is taken from a section of the stored body instead. The combination that agrees best is kept."""
    frames = [(sk["R"], sk["t"])] + list(sk.get("frames", []))
    original = copy.deepcopy(doc.find(sketch)["params"]["sketch"])
    best, choice, done, fit = -math.inf, None, False, 0.0  # fit: the best candidate's gain / |volume change|
    best_tagged = False  # the best has the faces SOLIDWORKS tagged with the feature (ranks before the score)
    s0, v0 = oracle.score_doc(doc), _volume(doc)
    base = s0 if math.isfinite(s0) else 0.0
    room = max(oracle.volume - (base + v0) / 2, 1e-9)  # stored material the part does not have yet
    cover, covering = 0.0, None  # (retry) the decoded candidate adding most of that material

    def attempt(plane, data, alt=False):  # alt: another reading of the sketch, kept only when it matches exactly
        nonlocal best, choice, done, fit, cover, covering, best_tagged
        _check_time()
        try:
            doc.edit_feature(sketch, {"plane": plane, "sketch": data})
            build()
        except (DocumentError, RebuildError):
            return
        s, v = oracle.score_doc(doc), _volume(doc)
        done = _exact(oracle, doc.body, s0, v0, s, v)
        tagged = done or _tagged_ok(oracle, doc.body, s0, v0, s, v)
        doc.delete_feature(feature, with_children=False)
        _log(f"{feature}: candidate {'alt' if alt else 'decoded' if data is original else 'section'} scores {s:.6g} (volume {v:.6g}, from {v0:.6g}){' exact' if done else ''}")
        if (tagged, s) > (best_tagged, best + 1e-6) and (not alt or done or choice is None):
            best, choice, best_tagged = s, (plane, data), tagged
            fit = (s - base) / abs(v - v0) if abs(v - v0) > 1e-9 else 0.0
        added = ((s + v) - (base + v0)) / 2 / room
        if v > v0 and added > cover and data is not None and not decoded_done:
            cover, covering = added, (plane, data)
    decoded_done = False
    for R, t in frames:
        attempt(_plane(R, t), original)
        if done:
            break
    # other readings of the sketch: on the plane that fits best so far (each reading costs a full set of trials)
    planes = [fr for fr in frames if choice is not None and _plane(*fr) == choice[0]] or frames[:1]
    chords = _chords(sk)
    if not done and chords:  # lines across the profile not flagged as construction (a centre rectangle's diagonals)
        bare = _sketch_dict({**sk, "construction": set(sk.get("construction", ())) | chords})[0]
        for R, t in planes:
            attempt(_plane(R, t), bare, alt=True)
            if done:
                break
    for arcs in _closing_arcs(sk):  # an open chain whose closing arc was not decoded (a sector)
        if done:
            break
        closed = _sketch_dict({**sk, "arcs": arcs})[0]
        for R, t in planes:
            attempt(_plane(R, t), closed, alt=True)
            if done:
                break
    if not done and _TRUST[0] and sk.get("gap_lines"):  # the segments the point references miss, from their records
        filled = _sketch_dict({**sk, "lines": list(sk["lines"]) + list(sk["gap_lines"])})[0]
        for R, t in frames:
            attempt(_plane(R, t), filled)
            if done:
                break
    decoded_done = True
    if not done and _TRUST[0] and cover >= 0.9 and fit < 0.9:  # (retry) a decoded profile holding nearly all the
        # stored material, which later features carve down (one that already fits well is checked against sections)
        choice, done = covering, True  # left: later features (a shell, cuts) take the rest away
    # segments not decoded (or not closed), or no plane fits well: the profile from the stored body. (A decoded
    # profile that fits well is kept even when later features change the body: the section would absorb them.)
    if not done and fit < (0.999 if _TRUST[0] else 0.9) and not (_TRUST[0] and choice is not None and best > base
                                                                  and fit < 0.9):
        # (on the retry, a decoded profile that fits well but not exactly, e.g. a slot read as its construction
        # geometry, gives way to a section that matches exactly; on the first pass the section could absorb later
        # features, such as a fillet's rounds)
        loose = fit >= 0.9
        if choice is not None:  # the best plane so far first
            frames.sort(key=lambda fr: _plane(*fr) != choice[0])
        for R, t in frames[:1] if loose else frames:
            for data in _section_sketches(oracle, {**sk, "R": R, "t": t}):
                attempt(_plane(R, t), data, alt=loose)
                if done:
                    break
            if done:
                break
    if choice is None:
        raise RebuildError(f"{feature}: no variant builds")
    n0 = len(doc.body.solids()) if doc.body is not None else 0
    doc.edit_feature(sketch, {"plane": choice[0], "sketch": choice[1]})
    build()
    n1 = len(doc.body.solids()) if doc.body is not None else 0
    if n0 and n1 > n0 and abs(_volume(doc) - v0) <= 1e-6 * max(1.0, v0):  # it only splits off an empty sliver:
        for nm in (feature, f"{feature} (cut)"):  # not what SOLIDWORKS made (the import path finds it absent)
            if any(x["name"] == nm for x in doc.features):
                doc.delete_feature(nm, with_children=False)
        raise RebuildError(f"{feature}: it changes nothing but splits the body")

def _around_origin(data: dict) -> dict:
    """The loops of a section sketch whose extent holds the origin (a member's section, not the members
    beside it that the same plane cuts)."""
    pts, ents = data["points"], data["entities"]
    parent = {k: k for k in ents}

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    by_point: dict = {}
    for k, e in ents.items():
        for key in ("p1", "p2", "c", "s", "e"):
            if key in e and (e["type"] != "arc" or key != "c"):
                by_point.setdefault(e[key], []).append(k)
    for ks in by_point.values():
        for k in ks[1:]:
            parent[root(k)] = root(ks[0])
    groups: dict = {}
    for k in ents:
        groups.setdefault(root(k), []).append(k)

    def extent(ks):
        xs, ys = [], []
        for k in ks:
            e = ents[k]
            if e["type"] == "circle":
                c, r = pts[e["c"]], e["r"]
                xs += [c[0] - r, c[0] + r]
                ys += [c[1] - r, c[1] + r]
            for key in ("p1", "p2", "s", "e"):
                if key in e:
                    xs.append(pts[e[key]][0])
                    ys.append(pts[e[key]][1])
            for q in e.get("pts", []):
                xs.append(pts[q][0])
                ys.append(pts[q][1])
            for q in e.get("bspline", {}).get("poles", []):  # (a closed spline's one point says nothing)
                xs.append(q[0])
                ys.append(q[1])
        return (min(xs), min(ys), max(xs), max(ys)) if xs else None
    keep = []
    for ks in groups.values():
        b = extent(ks)
        if b and b[0] - 1e-6 <= 0 <= b[2] + 1e-6 and b[1] - 1e-6 <= 0 <= b[3] + 1e-6:
            keep += ks
    return {**data, "entities": {k: ents[k] for k in ents if k in keep}}

def _closing_arcs(sk: dict) -> list:
    """For a sketch whose segments form one open chain: arc lists that close it with an arc between its two
    loose ends, about each point equidistant from them (a sector whose arc the stream did not give)."""
    degree: dict = {}
    for a, b in [*sk.get("lines", []), *[(s, e) for _, s, e in sk.get("arcs", []) if s != e]]:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    ends = [q for q, n in degree.items() if n == 1]
    if len(ends) != 2:
        return []
    P = sk["points"]
    p, q = ends
    r = math.dist(P[p], P[q])
    if r < 1e-9:
        return []
    out = []
    for c, xy in P.items():
        if c in (p, q):
            continue
        rp, rq = math.dist(xy, P[p]), math.dist(xy, P[q])
        if rp > 1e-6 and abs(rp - rq) <= 1e-6 * max(rp, 1.0):
            out.append(list(sk.get("arcs", [])) + [(c, p, q)])
    return out[:3]

def _chords(sk: dict) -> set:
    """Lines both of whose ends meet two or more other segments: chords across a profile, which SOLIDWORKS
    sketches as construction lines (a centre rectangle's diagonals) when the flag is not found."""
    degree: dict = {}
    lines = sk.get("lines", [])
    for a, b in [*lines, *[(s, e) for _, s, e in sk.get("arcs", []) if s != e]]:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    return {frozenset((a, b)) for a, b in lines if a != b and degree[a] >= 3 and degree[b] >= 3}

def _settle_arcs(doc: PartDocument, oracle: _Oracle, sketch: str) -> None:
    """The stream gives an arc's two end points but not reliably which one it starts from (counter-clockwise):
    flip each arc of `sketch` and keep the flip when the part (with the feature that consumes the sketch)
    then agrees better with the stored body."""
    feat = doc.find(sketch)
    arcs = [eid for eid, e in feat["params"]["sketch"]["entities"].items() if e["type"] == "arc"]
    if not arcs:
        return
    if _TAGGED[0] and doc.body is not None and _faces_on_body(doc.body, _TAGGED[0], share=0.98):
        return  # the feature already reproduces the faces SOLIDWORKS tagged with it
    best = oracle.score_doc(doc)
    for eid in arcs:
        data = copy.deepcopy(feat["params"]["sketch"])  # the rebuild writes solved points back into the params
        flipped = copy.deepcopy(data)
        e = flipped["entities"][eid]
        e["s"], e["e"] = e["e"], e["s"]
        try:
            doc.edit_feature(sketch, {"sketch": flipped})
        except DocumentError:
            continue
        s = oracle.score_doc(doc)
        if s > best + 1e-6:
            best = s
        else:
            try:
                doc.edit_feature(sketch, {"sketch": data})
            except DocumentError as exc:
                raise RebuildError(f"{sketch}: {exc}") from exc
