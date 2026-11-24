"""Feature-family parameter helpers (scale, draft, fillet, hole, helix) for the SOLIDWORKS history rebuild."""
from __future__ import annotations

import math

from openswx.core.document import PartDocument

from ._base import TOL, RebuildError, _Oracle, _inside_mesh, _log, _mesh_volume

def _scale_bytes(buf: bytes, f: dict) -> float | None:
    """A uniform scale's factor as stored: three equal doubles (x, y, z factors) in the moScale_c object."""
    import struct
    for k in range(f["offset"], min(len(buf) - 24, f["offset"] + 1024)):
        x, y, z = struct.unpack_from("<3d", buf, k)
        if x == y == z and 1e-3 < x < 1e3 and x != 1.0:
            return x
    return None


def _scale_factor(buf: bytes, f: dict, doc: PartDocument, oracle: "_Oracle") -> float:
    """A uniform scale's factor: three equal doubles (x, y, z factors) in the moScale_c object, else the cube
    root of the volume ratio to the stored body."""
    import struct
    end = min(len(buf) - 24, f["offset"] + 1024)
    for k in range(f["offset"], end):
        x, y, z = struct.unpack_from("<3d", buf, k)
        if x == y == z and 1e-3 < x < 1e3 and x != 1.0:
            return x
    return (oracle.volume / max(_mesh_volume(doc.body), 1e-12)) ** (1 / 3)

def _draft_variants(doc: PartDocument, oracle: "_Oracle", angles: list) -> list:
    """Draft (neutral plane): the faces to tilt are the body's faces that are off the stored boundary (drafted
    faces keep the tag of the feature that made them); the neutral plane is not decoded, so the body's planar
    faces square to those faces are tried (larger first), each way round."""
    from build123d import GeomType
    from openswx.core.geom import face_ref
    if doc.body is None or not angles:
        raise RebuildError("nothing to draft")
    pick = [fc for fc in doc.body.faces() if not oracle.on_boundary(fc.center())]
    if not pick:
        raise RebuildError("the drafted faces are not found")
    normals = [p.normal_at() for p in pick if p.geom_type == GeomType.PLANE]
    neutral = [fc for fc in doc.body.faces() if fc.geom_type == GeomType.PLANE and not any(fc.is_same(p) for p in pick)
               and all(abs(fc.normal_at().dot(n)) < 1e-3 for n in normals)]
    neutral.sort(key=lambda fc: -fc.area)
    faces = [face_ref(p) for p in pick]
    out = []
    for fc in neutral[:6]:
        c, n = fc.center(), fc.normal_at()
        x = (fc.edges()[0].position_at(1) - fc.edges()[0].position_at(0)).normalized()
        plane = {"origin": [c.X, c.Y, c.Z], "normal": [n.X, n.Y, n.Z], "x_dir": [x.X, x.Y, x.Z]}
        out += [{"neutral": plane, "faces": faces, "angle": angles[0], "reverse": r} for r in (False, True)]
    if not out:
        raise RebuildError("no neutral plane for the draft")
    return out

def _fillet_radii(faces: list) -> list[float]:
    """Radii of the round faces a fillet made (cylinders; tori: the tube radius), most common first."""
    from collections import Counter
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus
    radii: Counter = Counter()
    for fc in faces:
        srf = BRepAdaptor_Surface(fc.wrapped)
        if srf.GetType() == GeomAbs_Cylinder:
            radii[round(srf.Cylinder().Radius(), 6)] += 1
        elif srf.GetType() == GeomAbs_Torus:
            radii[round(srf.Torus().MinorRadius(), 6)] += 1
    return [r for r, _ in radii.most_common(3)]

def _helix(doc: PartDocument, oracle: "_Oracle", name: str, sketch, sketches: dict, lengths: list) -> None:
    """Helix on a sketch circle: its height and pitch are the larger and the smaller length parameter; it
    starts at the circle's point. Its handedness and direction are not decoded: the variant whose curve runs
    inside the stored body (a swept spring holds its path) is kept."""
    import numpy as np
    from build123d import Axis, Edge, Vector
    sk = sketches.get(sketch) if sketch else None
    circ = [(c, s_) for c, s_, e_ in (sk or {}).get("arcs", []) if s_ == e_]
    if not circ or len(lengths) < 2:
        raise RebuildError(f"{name}: no circle or size for the helix")
    c, s_ = circ[0]
    R, t, P = sk["R"], sk["t"], sk["points"]
    o = R @ np.array([*P[c], 0.0]) + t
    q = R @ np.array([*P[s_], 0.0]) + t
    r = float(np.linalg.norm(q - o))
    h, pitch = max(lengths[:2]), min(lengths[:2])
    tris = oracle._tris()
    best, choice = -1.0, None
    for rev in (False, True):
        d = R[:, 2] * (-1 if rev else 1)
        for left in (False, True):
            w = Edge.make_helix(pitch, h, r, center=Vector(*o), normal=Vector(*d), lefthand=left)
            p0 = w.position_at(0)
            a0, a1 = np.array([p0.X, p0.Y, p0.Z]) - o, q - o
            ang = math.degrees(math.atan2(float(np.dot(np.cross(a0, a1), d)), float(np.dot(a0, a1))))
            w = w.rotate(Axis(Vector(*o), Vector(*d)), ang)
            pts = np.array([[v.X, v.Y, v.Z] for v in (w.position_at(i / 40) for i in range(41))])
            inside = float(_inside_mesh(tris, pts).mean()) if tris is not None else 0.0
            if inside > best:
                best, choice = inside, {"origin": [float(v) for v in o], "axis": {"origin": [float(v) for v in o],
                                        "direction": [float(v) for v in R[:, 2]]}, "radius": r, "pitch": pitch,
                                        "height": h, "reverse": rev, "counterclockwise": left, "start_angle": ang}
    _log(f"{name}: helix inside the stored body {best:.2f}")
    doc.add_feature("helix", choice, name=name)

def _hole_variants(tagged: list) -> list[list[dict]]:
    """openswx hole parameters read off the faces a Hole Wizard feature made, a list of variants per hole:
    coaxial cylinders (and a cone for a countersink, or a drill point) per hole; each hole is placed by a plane
    at one end of its faces, both ends offered (a drilled hole: the end away from its tip), blind or through."""
    import numpy as np
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder
    groups = []  # [axis point, axis direction, [(kind, radius, half angle, t0, t1)]]
    for fc in tagged:
        srf = BRepAdaptor_Surface(fc.wrapped)
        if srf.GetType() == GeomAbs_Cylinder:
            g_ = srf.Cylinder()
            kind, r, ang = "cyl", g_.Radius(), 0.0
        elif srf.GetType() == GeomAbs_Cone:
            g_ = srf.Cone()
            kind, r, ang = "cone", g_.RefRadius(), abs(g_.SemiAngle())
        else:
            continue
        ax = g_.Axis()
        o = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
        d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        g = next((g for g in groups if abs(abs(np.dot(g[1], d)) - 1) < 1e-6
                  and np.linalg.norm(np.cross(o - g[0], g[1])) < 1e-4), None)
        if g is None:
            g = [o, d, []]
            groups.append(g)
        ts = [float(np.dot(np.array([v.X, v.Y, v.Z]) - g[0], g[1])) for v in fc.vertices()]
        if kind == "cone":  # the radius at each end of the cone face
            rs = [math.dist([v.X, v.Y, v.Z], g[0] + g[1] * np.dot(np.array([v.X, v.Y, v.Z]) - g[0], g[1]))
                  for v in fc.vertices()]
            r = max(rs) if rs else r
        g[2].append((kind, r, ang, min(ts, default=0.0), max(ts, default=0.0)))
    out = []
    for o, d, parts in groups:
        cyls = sorted({round(r, 6) for k, r, *_ in parts if k == "cyl"})
        if not cyls:
            continue
        out.append([])
        lo, hi = min(p[3] for p in parts), max(p[4] for p in parts)
        dia = 2 * cyls[0]
        # a cone as wide as the hole is its drill point (a blind hole's tip); a wider one is a countersink
        tips = [p for p in parts if p[0] == "cone" and abs(p[1] - cyls[0]) < 1e-4]
        parts = [p for p in parts if p not in tips]
        clo, chi = min(p[3] for p in parts), max(p[4] for p in parts)
        for sign, top in ((1.0, hi), (-1.0, lo)):  # the hole enters from either end
            if tips and (tips[0][3] + tips[0][4]) / 2 * sign > (clo + chi) / 2 * sign:
                continue  # (not from the tip's end)
            if tips:
                top = chi if sign > 0 else clo
            n = d * sign
            origin = o + d * top
            x = np.cross(n, [0, 0, 1]) if abs(n[2]) < 0.9 else np.cross(n, [1, 0, 0])
            plane = {"origin": [float(v) for v in origin], "normal": [float(v) for v in n],
                     "x_dir": [float(v) for v in x / np.linalg.norm(x)]}
            base = {"face": plane, "points": [[0.0, 0.0]], "diameter": dia, "depth": (chi - clo) if tips else hi - lo,
                    "type": "simple"}
            if tips:
                base["drill_point"] = True
            cone = next((p for p in parts if p[0] == "cone"), None)
            if cone is not None:
                base.update(type="countersink", csk_diameter=2 * cone[1], csk_angle=math.degrees(2 * cone[2]))
            elif len(cyls) > 1:
                big = max((p for p in parts if p[0] == "cyl"), key=lambda p: p[1])
                base.update(type="counterbore", cbore_diameter=2 * big[1], cbore_depth=big[4] - big[3])
            out[-1].append(base)
            out[-1].append({**base, "end_condition": "ThroughAll"})
    return out

def _off_boundary_edges(body, oracle: _Oracle, reach: float = math.inf) -> list:
    """Edges a fillet / chamfer of size `reach` rounded: off the stored body's boundary, but close to it (an edge
    that a later feature removed is usually farther away)."""
    from build123d import Vertex
    out = []
    for e in body.edges():
        ds = [oracle.skin.distance_to(Vertex(*e.position_at(t))) for t in (0.25, 0.5, 0.75)]
        if all(TOL <= d <= reach for d in ds):
            out.append(e)
    return out
