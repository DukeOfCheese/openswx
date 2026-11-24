from __future__ import annotations

import math

from build123d import Plane, Shape, Vector

from openswx.core.geom import face_normal, tessellate


def draft_analysis(body: Shape, direction: Vector, angle: float = 1.0) -> dict:
    """Classify every face against the pull direction: positive draft (>= angle), negative (<= -angle),
    requires draft (between), or straddle (curved face crossing the parting threshold)."""
    d = direction.normalized()
    out, counts = {}, {"positive": 0, "negative": 0, "requires_draft": 0, "straddle": 0}
    for i, f in enumerate(body.faces()):
        drafts = []
        for u in (0.1, 0.5, 0.9):
            for v in (0.1, 0.5, 0.9):
                try:
                    p = f.position_at(u, v)
                    n = face_normal(f, p)
                except Exception:  # degenerate parameter point
                    continue
                drafts.append(90.0 - math.degrees(math.acos(max(-1.0, min(1.0, n.dot(d))))))
        if not drafts:
            continue
        lo, hi = min(drafts), max(drafts)
        if lo >= angle - 1e-9:
            cls = "positive"
        elif hi <= -angle + 1e-9:
            cls = "negative"
        elif lo > -angle and hi < angle:
            cls = "requires_draft"
        else:
            cls = "straddle"
        counts[cls] += 1
        out[f"Face{i}"] = {"class": cls, "min_draft": round(lo, 6), "max_draft": round(hi, 6)}
    return {"direction": list(d), "angle": angle, "faces": out, "counts": counts}


def curvature_analysis(body: Shape) -> dict:
    """Maximum principal curvature (1/mm) at every tessellation vertex of every face."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.gp import gp_Pnt
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    mesh = tessellate(body)
    out, kmax = {}, 0.0
    for i, f in enumerate(body.faces()):
        v = mesh["faces"][i]["v"]
        sas = ShapeAnalysis_Surface(BRep_Tool.Surface_s(f.wrapped))
        props = BRepLProp_SLProps(BRepAdaptor_Surface(f.wrapped), 2, 1e-6)
        ks = []
        for j in range(0, len(v), 3):
            uv = sas.ValueOfUV(gp_Pnt(v[j], v[j + 1], v[j + 2]), 1e-4)
            props.SetParameters(uv.X(), uv.Y())
            k = max(abs(props.MaxCurvature()), abs(props.MinCurvature())) if props.IsCurvatureDefined() else 0.0
            ks.append(round(k, 6))
        kmax = max(kmax, max(ks, default=0.0))
        out[f"Face{i}"] = ks
    return {"faces": out, "max": kmax}


def section_view(body: Shape, plane: Plane, flip: bool = False) -> dict:
    """Cut the body by a plane: returns the plane and the tessellated cap (cross-section) faces."""
    from build123d import Compound, Keep
    n = -plane.z_dir if flip else plane.z_dir
    kept = body.split(Plane(origin=plane.origin, z_dir=n, x_dir=plane.x_dir), keep=Keep.BOTTOM)
    cap_faces = []
    if kept is not None:
        for f in kept.faces():
            if f.geom_type.name.lower() != "plane":
                continue
            c = f.center()
            if abs((c - plane.origin).dot(n)) < 1e-6 and abs(abs(f.normal_at().dot(n)) - 1) < 1e-6:
                cap_faces.append(f)
    cap = tessellate(Compound(cap_faces)) if cap_faces else {"faces": [], "edges": []}
    return {"origin": list(plane.origin), "normal": list(n), "cap": cap["faces"], "cap_edges": cap["edges"],
            "area": round(sum(f.area for f in cap_faces), 6)}


def check_geometry(body: Shape) -> dict:
    """Tools > Check: B-rep validity plus small-feature statistics."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    valid = BRepCheck_Analyzer(body.wrapped).IsValid()
    edges, faces = body.edges(), body.faces()
    lengths = sorted(e.length for e in edges)
    radii = [e.radius for f in faces if f.geom_type.name.lower() in ("cylinder", "sphere", "torus", "cone")
             for e in f.edges() if e.geom_type.name.lower() == "circle"]
    issues = []
    if not valid:
        issues.append("invalid B-rep topology/geometry (BRepCheck)")
    short = [x for x in lengths if x < 1e-3]
    if short:
        issues.append(f"{len(short)} edge(s) shorter than 0.001 mm")
    from OCP.BRep import BRep_Tool
    open_shells = sum(1 for s in body.shells() if not BRep_Tool.IsClosed_s(s.wrapped))
    if open_shells:
        issues.append(f"{open_shells} open shell(s)")
    return {"valid": not issues, "issues": issues, "solids": len(body.solids()), "faces": len(faces),
            "edges": len(edges), "min_edge_length": round(lengths[0], 6) if lengths else None,
            "min_radius": round(min(radii), 6) if radii else None}


def body_interference(body: Shape) -> dict:
    """Interference between the solid bodies of a multibody part (pairs and overlap volume)."""
    solids = list(body.solids())
    pairs = []
    for i in range(len(solids)):
        for j in range(i + 1, len(solids)):
            inter = solids[i].intersect(solids[j])
            parts = list(inter) if isinstance(inter, (list, tuple)) else ([inter] if inter is not None else [])
            vol = sum(s.volume for p in parts for s in p.solids())
            if vol > 1e-9:
                pairs.append({"bodies": [i, j], "volume": round(vol, 6)})
    return {"bodies": len(solids), "interferences": pairs}


_SAMPLES = (0.15, 0.5, 0.85)


def _face_samples(f):
    """Points on a face with its outward normal there (a 3 x 3 grid in the face's parameter range)."""
    out = []
    for u in _SAMPLES:
        for v in _SAMPLES:
            try:
                p = f.position_at(u, v)
                if not f.is_inside(p):  # trimmed-away part of the surface
                    continue
                out.append((p, face_normal(f, p)))
            except Exception:
                continue
    if not out:
        c = f.center()
        out.append((c, face_normal(f, c)))
    return out


def _ray_hits(body: Shape, origin: Vector, d: Vector, eps: float = 1e-4) -> list[float]:
    """Distances along d from origin to where the line meets the body (ahead of the origin only)."""
    from build123d import Axis
    hits = []
    for q, _n in body.find_intersection_points(Axis(origin, d)):
        t = (q - origin).dot(d)
        if t > eps:
            hits.append(t)
    return sorted(hits)


def thickness_analysis(body: Shape, target: float = 1.0) -> dict:
    """Thickness Analysis: from points on every face, the wall thickness straight through the material
    (inward along the normal to the next face); faces thinner than `target` are flagged."""
    out, thin, overall = {}, [], math.inf
    for i, f in enumerate(body.faces()):
        best = math.inf
        for p, n in _face_samples(f):
            n = n.normalized()
            hits = _ray_hits(body, p - n * 1e-5, -n)  # started just inside the face
            if hits:
                best = min(best, hits[0] + 1e-5)
        if best == math.inf:
            continue
        out[f"Face{i}"] = {"min_thickness": round(best, 6), "thin": best < target - 1e-9}
        overall = min(overall, best)
        if best < target - 1e-9:
            thin.append(f"Face{i}")
    return {"target": target, "min_thickness": None if overall == math.inf else round(overall, 6),
            "faces": out, "thin_faces": thin}


def undercut_analysis(body: Shape, direction: Vector) -> dict:
    """Undercut Detection for a two-part mold pulled along +/- direction: a face facing +direction must see
    out along +direction (likewise for -direction); faces the body blocks are undercuts."""
    d = direction.normalized()
    out, counts = {}, {"direction1": 0, "direction2": 0, "undercut": 0, "side": 0}
    for i, f in enumerate(body.faces()):
        cls = None
        for p, n in _face_samples(f):
            nd = n.dot(d)
            if nd > 1e-6:
                ok, side = not _ray_hits(body, p + n * 1e-5, d), "direction1"
            elif nd < -1e-6:
                ok, side = not _ray_hits(body, p + n * 1e-5, -d), "direction2"
            else:  # parallel to the pull: free if it sees out either way
                ok, side = (not _ray_hits(body, p + n * 1e-5, d)) or (not _ray_hits(body, p + n * 1e-5, -d)), "side"
            if not ok:
                cls = "undercut"
                break
            cls = side if cls in (None, side) else ("side" if "side" in (cls, side) else cls)
        counts[cls] += 1
        out[f"Face{i}"] = {"class": cls}
    return {"direction": list(d), "faces": out, "counts": counts}


def clearance_verification(shapes: list, min_clearance: float) -> dict:
    """Clearance Verification: the smallest gap between every pair of components; pairs closer than
    `min_clearance` (touching or interfering included) are reported."""
    pairs, closest = [], None
    for i in range(len(shapes)):
        for j in range(i + 1, len(shapes)):
            (na, a), (nb, b) = shapes[i], shapes[j]
            gap = a.distance_to(b)
            if closest is None or gap < closest["distance"]:
                closest = {"components": [na, nb], "distance": round(gap, 6)}
            if gap < min_clearance - 1e-9:
                pairs.append({"components": [na, nb], "distance": round(gap, 6)})
    return {"min_clearance": min_clearance, "violations": pairs, "closest": closest}
