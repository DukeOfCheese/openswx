from __future__ import annotations

import math
from pathlib import Path

from openswx.core.sketch import Sketch, SketchError


def import_into_sketch(sk: Sketch, path: str, scale: float = 1.0, offset=(0.0, 0.0)) -> list[str]:
    """Add the DXF modelspace entities (LINE, CIRCLE, ARC, LWPOLYLINE/POLYLINE with bulges, SPLINE, ELLIPSE,
    POINT) to the sketch, merging coincident end points. Returns the new entity ids."""
    import ezdxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    ox, oy = offset
    pts: dict[tuple, str] = {}

    def P(x, y):
        q = [x * scale + ox, y * scale + oy]
        key = (round(q[0], 6), round(q[1], 6))
        if key not in pts:
            pts[key] = sk.point_id(q)
        return pts[key]

    def arc(cx, cy, r, a0, a1):  # CCW from a0 to a1 (degrees)
        c = sk.point_id([cx * scale + ox, cy * scale + oy])
        s = P(cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0)))
        e = P(cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)))
        return sk.add_arc(c, s, e)

    def bulge_seg(p0, p1, b):
        if abs(b) < 1e-12:
            return sk.add_line(P(*p0), P(*p1))
        from ezdxf.math import bulge_to_arc
        c, a0, a1, r = bulge_to_arc(p0, p1, b)
        return arc(c.x, c.y, r, math.degrees(a0), math.degrees(a1))

    made = []
    for e in msp:
        t = e.dxftype()
        try:
            if t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                if math.dist((a.x, a.y), (b.x, b.y)) > 1e-9:
                    made.append(sk.add_line(P(a.x, a.y), P(b.x, b.y)))
            elif t == "CIRCLE":
                c = e.dxf.center
                made.append(sk.add_circle([c.x * scale + ox, c.y * scale + oy], e.dxf.radius * scale))
            elif t == "ARC":
                c = e.dxf.center
                made.append(arc(c.x, c.y, e.dxf.radius, e.dxf.start_angle, e.dxf.end_angle))
            elif t == "LWPOLYLINE":
                v = list(e.get_points("xyb"))
                segs = list(zip(v, v[1:] + ([v[0]] if e.closed else [])))
                for (x0, y0, b), (x1, y1, _b) in segs:
                    made.append(bulge_seg((x0, y0), (x1, y1), b))
            elif t == "POLYLINE":
                v = [(p.dxf.location.x, p.dxf.location.y, p.dxf.bulge) for p in e.vertices]
                segs = list(zip(v, v[1:] + ([v[0]] if e.is_closed else [])))
                for (x0, y0, b), (x1, y1, _b) in segs:
                    made.append(bulge_seg((x0, y0), (x1, y1), b))
            elif t == "SPLINE":
                fit = list(e.fit_points) or list(e.flattening(0.01))
                ids = [P(p[0], p[1]) for p in fit]
                if len(ids) >= 2:
                    made.append(sk.add_spline(ids, closed=bool(e.closed)))
            elif t == "ELLIPSE":
                c, m = e.dxf.center, e.dxf.major_axis
                if abs(e.dxf.start_param) < 1e-9 and abs(e.dxf.end_param - 2 * math.pi) < 1e-9:
                    made.append(sk.add_ellipse([c.x * scale + ox, c.y * scale + oy],
                                               [(c.x + m.x) * scale + ox, (c.y + m.y) * scale + oy],
                                               math.hypot(m.x, m.y) * e.dxf.ratio * scale))
                else:  # partial ellipse: spline through samples
                    made.append(sk.add_spline([P(p.x, p.y) for p in e.flattening(0.01)]))
            elif t == "POINT":
                p = e.dxf.location
                made.append(sk.add_point([p.x * scale + ox, p.y * scale + oy]))
        except SketchError:
            continue
    if not made:
        raise SketchError("the DXF has no supported 2D entities in model space")
    return made


def export_sketch(sk: Sketch, path: str, include_construction: bool = False) -> str:
    """Write the sketch geometry (local XY) as DXF: LINE, CIRCLE, ARC, ELLIPSE, SPLINE, POINT; text as outlines."""
    import ezdxf
    import numpy as np
    doc = ezdxf.new("R2010", setup=True)
    doc.units = 4
    msp = doc.modelspace()
    if "CONSTRUCTION" not in doc.layers:
        doc.layers.add("CONSTRUCTION", color=8, linetype="DASHED" if "DASHED" in doc.linetypes else "CONTINUOUS")
    C = sk.coords
    for e in sk.entities.values():
        if e.get("construction") and not include_construction:
            continue
        att = {"layer": "CONSTRUCTION" if e.get("construction") else "0"}
        t = e["type"]
        if t == "line":
            msp.add_line(C(e["p1"]), C(e["p2"]), dxfattribs=att)
        elif t == "circle":
            msp.add_circle(C(e["c"]), e["r"], dxfattribs=att)
        elif t == "arc":
            c, s, en = (np.array(C(e[k])) for k in ("c", "s", "e"))
            r = float(np.linalg.norm(s - c))
            msp.add_arc(c.tolist(), r, math.degrees(math.atan2(*(s - c)[::-1])), math.degrees(math.atan2(*(en - c)[::-1])),
                        dxfattribs=att)
        elif t == "ellipse":
            c, m = np.array(C(e["c"])), np.array(C(e["m"]))
            a = float(np.linalg.norm(m - c))
            if e["b"] <= a:
                ratio, major = e["b"] / a, m - c
            else:  # the "minor" radius is the larger one: the major axis is perpendicular
                ratio, major = a / e["b"], np.array([-(m - c)[1], (m - c)[0]]) * e["b"] / a
            msp.add_ellipse(c.tolist(), [float(major[0]), float(major[1]), 0.0], ratio, dxfattribs=att)
        elif t == "spline":
            fit = [C(q) for q in e["pts"]] + ([C(e["pts"][0])] if e.get("closed") else [])
            msp.add_spline(fit_points=[(x, y, 0) for x, y in fit], dxfattribs=att)
        elif t == "point":
            msp.add_point(C(e["p"]), dxfattribs=att)
        elif t == "text":
            from build123d import Plane
            from openswx.core.sketch import _entity_edges
            for edge in _entity_edges(sk, e, Plane.XY):
                n = 2 if edge.geom_type.name.lower() == "line" else 17
                msp.add_lwpolyline([(p.X, p.Y) for p in (edge.position_at(k / (n - 1)) for k in range(n))], dxfattribs=att)
    doc.saveas(path)
    return path


def export_face_outline(face, path: str, bends: list | None = None) -> list:
    """Flat-pattern style DXF of a planar face: outer and inner loops in the face's own 2D frame. With
    `bends` (sheet metal bends carrying a flat "flat_line" and the "toward" direction they bend to), also the
    bend lines (layer BEND, dashed), a note on each ("UP 90° R2", layer NOTES) and the bounding box (layer
    BBOX, with its size). Returns the notes written."""
    import ezdxf
    from build123d import Plane
    pl = Plane(face)
    doc = ezdxf.new("R2010", setup=True)
    doc.units = 4
    msp = doc.modelspace()

    def loc(v):
        return pl.to_local_coords(v)

    for w in [face.outer_wire()] + list(face.inner_wires()):
        for edge in w.edges():
            kind = edge.geom_type.name.lower()
            if kind == "line":
                a, b = loc(edge.position_at(0)), loc(edge.position_at(1))
                msp.add_line((a.X, a.Y), (b.X, b.Y))
            elif kind == "circle" and abs(abs(edge.normal().dot(pl.z_dir)) - 1) < 1e-6:
                c = loc(edge.arc_center)
                if edge.is_closed:
                    msp.add_circle((c.X, c.Y), edge.radius)
                else:
                    s, e, m = (loc(edge.position_at(t)) for t in (0, 1, 0.5))
                    ang = lambda q: math.degrees(math.atan2(q.Y - c.Y, q.X - c.X))  # noqa: E731
                    a0, a1, am = ang(s), ang(e), ang(m)
                    ccw = (am - a0) % 360 < (a1 - a0) % 360
                    msp.add_arc((c.X, c.Y), edge.radius, a0 if ccw else a1, a1 if ccw else a0)
            else:
                msp.add_lwpolyline([(p.X, p.Y) for p in (loc(edge.position_at(k / 32)) for k in range(33))])
    notes = []
    if bends is not None:
        from build123d import Vector
        doc.layers.add("BEND", color=1, linetype="DASHED")
        doc.layers.add("NOTES", color=3)
        doc.layers.add("BBOX", color=8)
        h = max(2.5, 0.02 * face.bounding_box().diagonal)
        for b in bends:
            a, c = loc(Vector(*b["flat_line"][0])), loc(Vector(*b["flat_line"][1]))
            msp.add_line((a.X, a.Y), (c.X, c.Y), dxfattribs={"layer": "BEND"})
            up = Vector(*b["toward"]).dot(pl.z_dir) > 0  # towards the viewer of the DXF
            text = f"{'UP' if up else 'DOWN'} {b['angle']:g}° R{b['radius']:g}"
            ang = math.degrees(math.atan2(c.Y - a.Y, c.X - a.X))
            if ang > 90 or ang <= -90:
                ang -= 180 if ang > 0 else -180  # keep the text readable (never upside down)
            m = ((a.X + c.X) / 2, (a.Y + c.Y) / 2)
            msp.add_text(text, height=h, rotation=ang, dxfattribs={"layer": "NOTES"}).set_placement(
                m, align=ezdxf.enums.TextEntityAlignment.BOTTOM_CENTER)
            notes.append({"text": text, "start": [a.X, a.Y], "end": [c.X, c.Y]})
        pts = [loc(face.outer_wire().position_at(k / 256)) for k in range(257)] + \
            [loc(v.center()) for v in face.vertices()]
        x0, x1 = min(q.X for q in pts), max(q.X for q in pts)
        y0, y1 = min(q.Y for q in pts), max(q.Y for q in pts)
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": "BBOX"})
        msp.add_text(f"{x1 - x0:.2f} x {y1 - y0:.2f}", height=h, dxfattribs={"layer": "NOTES"}).set_placement(
            (x0, y0 - 1.5 * h), align=ezdxf.enums.TextEntityAlignment.TOP_LEFT)
        notes.append({"text": "bounding box", "size": [x1 - x0, y1 - y0]})
    doc.saveas(path)
    return notes
    return path


def _odafc():
    from ezdxf.addons import odafc
    if not odafc.is_installed():
        raise SketchError("DWG needs the free ODA File Converter (opendesign.com/guestfiles/oda_file_converter); "
                          "install it, or use DXF")
    return odafc


def dwg_to_dxf(path: str, out_dir: str) -> str:
    """Convert a DWG to a temporary DXF through the ODA File Converter (DWG is a closed format)."""
    odafc = _odafc()
    out = str(Path(out_dir) / (Path(path).stem + ".dxf"))
    odafc.convert(path, out, version="R2018")
    return out


def dxf_to_dwg(dxf_path: str, dwg_path: str) -> str:
    odafc = _odafc()
    odafc.convert(dxf_path, dwg_path, version="R2018", replace=True)
    return dwg_path
