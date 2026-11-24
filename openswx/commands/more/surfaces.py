"""Surface tools: ruled, radiate, extend, untrim, flatten and filled surfaces."""
from __future__ import annotations

from typing import Literal

from openswx.commands.support import _edge, _face, _feat
from openswx.session import APP, Vec3, command


@command(category="surfaces")
def ruled_surface(edges: list[str], distance: float = 10.0,
                  type: Literal["normal", "tangent", "direction"] = "normal", direction: str | Vec3 = "Y") -> dict:
    """Surfaces > Ruled Surface from model edges: along the adjacent face normal, tangent to it (outward) or
    along a direction, `distance` mm."""
    doc = APP.doc
    return _feat("ruled_surface", {"edges": [_edge(doc, e) for e in edges], "distance": distance, "type": type,
                                   "direction": direction})


@command(category="surfaces")
def radiate_surface(edges: list[str], plane: str = "Top", distance: float = 10.0) -> dict:
    """Surfaces > Radiate Surface: outward from model edges, parallel to a reference plane, `distance` mm."""
    doc = APP.doc
    return _feat("radiate_surface", {"edges": [_edge(doc, e) for e in edges], "plane": plane, "distance": distance})


@command(category="surfaces")
def extend_surface(distance: float, surface: str | None = None, faces: list[str] | None = None) -> dict:
    """Surfaces > Extend Surface: grow a surface body's faces (or model faces) past their edges by `distance`."""
    doc = APP.doc
    return _feat("extend_surface", {"distance": distance, "surface": surface, "faces": [_face(doc, f) for f in faces or []]})


@command(category="surfaces")
def untrim_surface(surface: str | None = None, faces: list[str] | None = None,
                   mode: Literal["internal", "natural"] = "internal") -> dict:
    """Surfaces > Untrim Surface: fill the holes of faces (internal) or return them to their surfaces' natural
    bounds (natural)."""
    doc = APP.doc
    return _feat("untrim_surface", {"surface": surface, "faces": [_face(doc, f) for f in faces or []], "mode": mode})


@command(category="surfaces")
def flatten_surface(surface: str | None = None, faces: list[str] | None = None, plane: str = "Top") -> dict:
    """Surfaces > Flatten: unroll planar, cylindrical and conical faces flat (true size) onto a plane."""
    doc = APP.doc
    return _feat("flatten_surface", {"surface": surface, "faces": [_face(doc, f) for f in faces or []], "plane": plane})


@command(category="surfaces")
def filled_surface_edges(edges: list[str], continuity: Literal["contact", "tangent"] = "tangent",
                         points: list[Vec3] | None = None) -> dict:
    """Surfaces > Filled Surface over a closed loop of model edges (a hole or gap), tangent (G1) to the faces
    next to them or just touching (contact)."""
    doc = APP.doc
    return _feat("filled_surface", {"edges": [_edge(doc, e) for e in edges], "continuity": continuity, "points": points})


__all__ = ["ruled_surface", "radiate_surface", "extend_surface", "untrim_surface",
           "flatten_surface", "filled_surface_edges"]
