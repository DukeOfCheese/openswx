"""Part shape edits: flex, deform, freeform, fillets, inserted parts, cosmetic threads, defeature."""
from __future__ import annotations

from typing import Literal

from openswx.core.document import DocumentError
from openswx.core.geom import defeature_shape
from openswx.commands.support import _edge, _face, _feat
from openswx.session import APP, Vec3, command


@command(category="features")
def flex(type: Literal["bend", "twist", "taper", "stretch"], axis: str | Vec3 = "X", angle: float = 90.0,
         factor: float = 0.5, distance: float = 10.0, start: float | None = None, end: float | None = None,
         bend_direction: str | Vec3 | None = None) -> dict:
    """Flex the body along an axis ('X'/'Y'/'Z' or a vector) between trim positions start/end measured along
    it (default the whole body): bend by angle (deg, toward bend_direction), twist by angle (deg), taper to
    factor at the far end, or stretch by distance (mm)."""
    return _feat("flex", {"type": type, "axis": axis, "angle": angle, "factor": factor, "distance": distance,
                          "start": start, "end": end, "bend_direction": bend_direction})


@command(category="features")
def deform(points: list[Vec3], moves: list[Vec3], radius: float = 20.0) -> dict:
    """Deform (point): push the body at each point by its move [dx, dy, dz], falling off smoothly to nothing
    at `radius` mm from the point."""
    if len(points) != len(moves):
        raise DocumentError("give one move per point")
    return _feat("deform", {"points": [{"point": p, "move": m} for p, m in zip(points, moves)], "radius": radius})


@command(category="features")
def freeform(face: str, distance: float, radius: float | None = None) -> dict:
    """Freeform: pull a face ('FaceN') out along its normal by `distance` (negative pushes in), smoothly."""
    return _feat("freeform", {"face": _face(APP.doc, face), "distance": distance, "radius": radius})


@command(category="features")
def fillet_profile(edges: list[str], radius: float, profile: Literal["conic", "curvature_continuous"] = "conic",
                   rho: float = 0.5) -> dict:
    """Fillet with a conic (rho: 0.5 parabola, lower flatter, higher sharper) or curvature-continuous (G2)
    profile, on straight edges between planar faces; `radius` is the distance from the edge along each face."""
    doc = APP.doc
    return _feat("profile_fillet", {"edges": [_edge(doc, e) for e in edges], "radius": radius, "profile": profile,
                                    "rho": rho})


@command(category="features")
def fillet_xpert(edges: list[str], radius: float) -> dict:
    """FilletXpert: fillet edges that will not fillet all at once, by finding an order that works: all
    together first, then one at a time (longest first); edges that cannot take the radius are reported."""
    from openswx.core.geom import resolve_edge
    from openswx.session import fillet as _fillet
    doc = APP.doc
    refs = [_edge(doc, e) for e in edges]
    try:
        r = _fillet(radius, edges=edges)
        return {"features": [r["feature"]], "failed": [], "model": doc.summary()}
    except Exception:
        pass
    made, failed = [], []
    for ref in sorted(refs, key=lambda x: -x.get("length", 0.0)):  # ids change as features are added: re-resolve
        try:
            e = resolve_edge(doc.body, ref)
            idx = next(i for i, x in enumerate(doc.body.edges()) if x.is_same(e))
            made.append(_fillet(radius, edges=[f"Edge{idx}"])["feature"])
        except Exception:
            failed.append(ref["point"])
    if not made:
        raise DocumentError("none of the edges takes that radius")
    return {"features": made, "failed": failed, "model": doc.summary()}


@command(category="features")
def insert_part(path: str, position: Vec3 | None = None, rotation_deg: Vec3 | None = None,
                op: Literal["add", "cut"] = "add", merge: bool = True) -> dict:
    """Insert Part: bring another part file's body (.oswprt, STEP, Parasolid, .sldprt) into this part, linked
    to the file (it updates on rebuild), placed by position and rotation (deg about X, Y, Z)."""
    return _feat("insert_part", {"path": str(path), "position": position, "rotation": rotation_deg, "op": op, "merge": merge})


@command(category="features")
def mirror_part(path: str, plane: str = "Right", position: Vec3 | None = None) -> dict:
    """Mirror Part: a mirrored (opposite-hand) copy of another part file's body, about plane (Front / Top /
    Right or a plane name), linked to the file."""
    return _feat("insert_part", {"path": str(path), "mirror": plane, "position": position, "merge": False})


@command(category="features")
def cosmetic_thread(face: str, label: str | None = None, length: float | None = None) -> dict:
    """Cosmetic Thread on a cylindrical face ('FaceN', a hole or a shaft): label like 'M8x1.25' (default from
    the diameter), length (default the face's length). Shown on the model and in drawings; no geometry."""
    return _feat("cosmetic_thread", {"face": _face(APP.doc, face), "label": label, "length": length})


@command(category="features")
def defeature(max_hole_diameter: float | None = None, max_fillet_radius: float | None = None) -> dict:
    """Defeature: a simplified copy of the active part (or of the whole assembly) as a new part document:
    holes up to max_hole_diameter and fillets up to max_fillet_radius removed (defaults scale with the size),
    for sharing a model without its details."""
    from openswx.core.document import PartDocument
    from openswx.core.features import encode_shape
    src = APP.active_doc
    body = src.body
    if body is None:
        raise DocumentError("nothing to defeature")
    simple = defeature_shape(body, max_hole_diameter, max_fillet_radius)
    name = f"{src.name}-Defeature"
    doc = PartDocument(name)
    doc.add_feature("import", {"brep": encode_shape(simple)})
    APP.documents[name] = doc
    APP.active = name
    return {"document": name, "faces_before": len(body.faces()), "faces_after": len(simple.faces()),
            "model": doc.summary()}


__all__ = ["flex", "deform", "freeform", "fillet_profile", "fillet_xpert", "insert_part",
           "mirror_part", "cosmetic_thread", "defeature"]
