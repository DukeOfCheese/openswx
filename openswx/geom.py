"""Geometry helpers: SOLIDWORKS-convention planes, persistent face/edge references, tessellation."""
from __future__ import annotations

import math

from build123d import Axis, Edge, Face, Plane, Shape, Vector
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.gp import gp_Pnt

X, Y, Z = Vector(1, 0, 0), Vector(0, 1, 0), Vector(0, 0, 1)


def vec(v) -> Vector:
    return v if isinstance(v, Vector) else Vector(*v)


def tup(v) -> list[float]:
    v = vec(v)
    return [round(v.X, 9), round(v.Y, 9), round(v.Z, 9)]


def plane_for_normal(origin, normal) -> Plane:
    """Sketch plane whose in-plane axes follow SOLIDWORKS conventions.

    Front (+Z): x=+X, y=+Y.  Top (+Y): x=+X, y=-Z.  Right (+X): x=-Z, y=+Y.
    """
    n = vec(normal).normalized()
    if abs(n.dot(Y)) > 0.999:
        yd = -Z if n.dot(Y) > 0 else Z
    else:
        yd = (Y - n * Y.dot(n)).normalized()
    xd = yd.cross(n).normalized()
    return Plane(origin=vec(origin), x_dir=xd, z_dir=n)


STD_PLANES = {
    "Front": plane_for_normal((0, 0, 0), Z),
    "Top": plane_for_normal((0, 0, 0), Y),
    "Right": plane_for_normal((0, 0, 0), X),
}

STD_AXES = {"X": Axis((0, 0, 0), (1, 0, 0)), "Y": Axis((0, 0, 0), (0, 1, 0)), "Z": Axis((0, 0, 0), (0, 0, 1))}


def closest_point(shape: Shape, point) -> tuple[Vector, float]:
    p = vec(point)
    v = BRepBuilderAPI_MakeVertex(gp_Pnt(p.X, p.Y, p.Z)).Vertex()
    d = BRepExtrema_DistShapeShape(v, shape.wrapped)
    if not d.IsDone() or d.NbSolution() == 0:
        return p, float("inf")
    q = d.PointOnShape2(1)
    return Vector(q.X(), q.Y(), q.Z()), d.Value()


def face_normal(face: Face, at=None) -> Vector:
    try:
        if at is not None:
            return face.normal_at(vec(at)).normalized()
        return face.normal_at().normalized()
    except Exception:
        return face.normal_at(face.center()).normalized()


def face_ref(face: Face, body: Shape | None = None) -> dict:
    """Persistent reference: the face's name (openswx/naming.py), a point on the face, the normal there, and the
    surface type."""
    from .naming import label_of_face
    p, _ = closest_point(face, face.center())
    ref = {"kind": "face", "point": tup(p), "normal": tup(face_normal(face, p)), "type": geom_name(face),
           "area": round(face.area, 6)}
    label = label_of_face(face, body)
    if label:
        ref["label"] = label
    return ref


def _adjacent_signature(shape: Shape, edge: Edge) -> list[str]:
    """Describe the faces that meet at an edge (surface type + planar normal); survives most edits."""
    sig = []
    for f in shape.faces():
        if any(edge.is_same(e) for e in f.edges()):
            t = geom_name(f)
            if t == "plane":
                n = face_normal(f)
                t += "(%.3f,%.3f,%.3f)" % (n.X + 0.0, n.Y + 0.0, n.Z + 0.0)
            sig.append(t)
    return sorted(sig)


def edge_ref(edge: Edge, shape: Shape | None = None) -> dict:
    ref = {"kind": "edge", "point": tup(edge.position_at(0.5)), "tangent": tup(edge.tangent_at(0.5)),
           "type": geom_name(edge), "length": round(edge.length, 6)}
    if ref["type"] == "circle":
        ref["radius"] = round(edge.radius, 6)
    if shape is not None:
        ref["adjacent"] = _adjacent_signature(shape, edge)
        from .naming import edge_faces
        faces = edge_faces(shape, edge)
        if faces:
            ref["faces"] = faces
    return ref


def resolve_face(shape: Shape, ref: dict, tol: float = 1e-3) -> Face:
    """Exact match first (point lies on the face with the same normal); if the model changed, fall back
    to the nearest face of the same surface type and orientation (handles moved faces after edits). A named
    reference finds its face by name first (the name survives edits that move the face)."""
    p, n = vec(ref["point"]), vec(ref.get("normal", (0, 0, 0)))
    if ref.get("label"):
        from .naming import find_face
        hit = find_face(shape, ref["label"], p)
        if hit is not None:
            return hit
    best, best_score, fallback, fb_d = None, float("inf"), None, float("inf")
    for f in shape.faces():
        q, d = closest_point(f, p)
        aligned = n.length == 0 or face_normal(f, q).dot(n) > 0.999
        if d <= max(tol, 1e-2):
            score = d + (0.0 if aligned else 10.0)
            if score < best_score:
                best, best_score = f, score
        same_type = ref.get("type") in (None, geom_name(f))
        if aligned and same_type and d < fb_d:
            fallback, fb_d = f, d
    if best is not None and best_score < 10.0:
        return best
    if fallback is not None:
        return fallback
    raise ValueError(f"face reference {ref['point']} no longer resolves to a face")


def resolve_edge(shape: Shape, ref: dict, tol: float = 1e-2) -> Edge:
    p = vec(ref["point"])
    if ref.get("faces"):  # named: the edge between those two faces
        from .naming import find_edge
        hit = find_edge(shape, ref["faces"], p)
        if hit is not None:
            return hit
    t = vec(ref.get("tangent", (0, 0, 0)))
    adj = ref.get("adjacent")
    best, best_d, fallback, fb_score = None, float("inf"), None, float("inf")
    for e in shape.edges():
        q, d = closest_point(e, p)
        if d < best_d:
            best, best_d = e, d
        if ref.get("type") not in (None, geom_name(e)):
            continue
        if t.length > 0 and geom_name(e) == "line" and abs(e.tangent_at(0.5).dot(t)) < 0.999:
            continue
        score = d
        if "radius" in ref and geom_name(e) == "circle":
            score += abs(e.radius - ref["radius"]) * 10
        if score < fb_score and (adj is None or _adjacent_signature(shape, e) == adj):
            fallback, fb_score = e, score
    if best is not None and best_d <= tol and (adj is None or _adjacent_signature(shape, best) == adj):
        return best
    if fallback is not None:
        return fallback
    if best is not None and best_d <= tol:
        return best
    raise ValueError(f"edge reference {ref['point']} no longer resolves to an edge")


def geom_name(s: Shape) -> str:
    g = s.geom_type
    return getattr(g, "name", str(g)).lower()


def face_info(i: int, f: Face) -> dict:
    p, _ = closest_point(f, f.center())
    info = {"id": f"Face{i}", "type": geom_name(f), "point": tup(p), "area": round(f.area, 6)}
    info["normal"] = tup(face_normal(f, p))
    return info


def edge_info(i: int, e: Edge) -> dict:
    info = {"id": f"Edge{i}", "type": geom_name(e), "point": tup(e.position_at(0.5)), "length": round(e.length, 6)}
    if info["type"] == "circle":
        info["radius"] = round(e.radius, 6)
        info["center"] = tup(e.arc_center)
    return info


def tessellate(shape: Shape | None, tol: float = 0.05, ang: float = 0.2) -> dict:
    out = {"faces": [], "edges": []}
    if shape is None:
        return out
    bb = shape.bounding_box()
    tol = max(tol, bb.diagonal * 1e-3) if bb.diagonal > 0 else tol
    for i, f in enumerate(shape.faces()):
        verts, tris = f.tessellate(tol, ang)
        out["faces"].append({
            "id": f"Face{i}",
            "type": geom_name(f),
            "v": [round(c, 5) for p in verts for c in (p.X, p.Y, p.Z)],
            "t": [k for tri in tris for k in tri],
        })
    for i, e in enumerate(shape.edges()):
        n = 2 if geom_name(e) == "line" else 48
        pts = [e.position_at(t / (n - 1)) for t in range(n)]
        out["edges"].append({"id": f"Edge{i}", "p": [round(c, 5) for p in pts for c in (p.X, p.Y, p.Z)]})
    return out


def angle_deg(a, b) -> float:
    a, b = vec(a).normalized(), vec(b).normalized()
    return math.degrees(math.acos(max(-1.0, min(1.0, a.dot(b)))))


def interior_point(face: Face) -> Vector:
    """A point strictly inside a face (the centroid can fall in a hole): first uv grid sample the face
    classifier accepts, else the centroid."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN
    for k in (2, 4, 8, 16):
        for i in range(1, k):
            for j in range(1, k):
                p = face.position_at(i / k, j / k)
                if BRepClass_FaceClassifier(face.wrapped, gp_Pnt(p.X, p.Y, p.Z), 1e-7).State() == TopAbs_IN:
                    return p
    return face.center()
