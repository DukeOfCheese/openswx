"""DimXpert and render appearance: scene, decals."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from openswx.core.document import DocumentError
from openswx.commands.support import _face
from openswx.session import APP, command

from .common import _doc_edit


@command(category="mbd")
def dimxpert_auto(general_tolerance: float = 0.1, position_tolerance: float = 0.1) -> dict:
    """DimXpert Auto Dimension Scheme: datums A/B/C on the main planar faces, the overall sizes with a
    general tolerance, and hole patterns (count x diameter H7) with a position tolerance to the datums, as 3D
    annotations (PMI) shown on the model and saved with it (model-based definition)."""
    from openswx.analysis.analysis_more import dimxpert_scheme
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body")
    try:
        pmi = dimxpert_scheme(doc.body, general_tolerance, position_tolerance)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc

    def f(d):
        d.pmi = pmi
        return pmi
    return {"pmi": _doc_edit(doc, f)}


@command(category="mbd")
def dimxpert_clear() -> dict:
    """Delete the part's DimXpert annotations (PMI)."""
    def f(d):
        d.pmi = []
    _doc_edit(APP.doc, f)
    return {"pmi": []}


@command(category="render")
def set_render_scene(scene: Literal["studio", "warehouse", "outdoor", "plain_white", "dark"] = "studio",
                     floor: bool = True, shadows: bool = True, exposure: float = 1.0,
                     background: str | None = None) -> dict:
    """Render scene for photo rendering (PhotoView-style): lighting environment, floor with shadows, exposure
    and an optional background colour '#rrggbb'. Saved with the document; the Render Tools pane renders it."""
    def f(d):
        d.render = {**(getattr(d, "render", {}) or {}), "scene": scene, "floor": floor, "shadows": shadows,
                    "exposure": exposure, "background": background}
        return {k: v for k, v in d.render.items() if k != "decals"}
    return {"render": _doc_edit(APP.active_doc, f)}


@command(category="render")
def add_decal(face: str, image: str, width: float = 20.0, rotation: float = 0.0, name: str | None = None) -> dict:
    """Decal: an image (PNG/JPEG file path or data: URL) on a face of the active part, centred on the face,
    `width` mm wide and rotated by `rotation` degrees. Shown in the viewport and in renders."""
    import base64
    import mimetypes
    from openswx.core.geom import face_normal, interior_point, resolve_face
    doc = APP.doc
    if not image.startswith("data:"):
        p = Path(image)
        if not p.is_file():
            raise DocumentError(f"no image file {image!r}")
        mime = mimetypes.guess_type(p.name)[0] or ""
        if mime not in ("image/png", "image/jpeg"):
            raise DocumentError("decals need a PNG or JPEG image")
        image = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
    elif not image.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
        raise DocumentError("decals need a PNG or JPEG image")
    if width <= 0:
        raise DocumentError("width must be positive")
    ref = _face(doc, face)
    fc = resolve_face(doc.body, ref)
    c = interior_point(fc)

    def f(d):
        r = getattr(d, "render", {}) or {}
        decals = r.setdefault("decals", [])
        nm = name or f"Decal{len(decals) + 1}"
        if any(x["name"] == nm for x in decals):
            raise DocumentError(f"decal {nm!r} exists")
        it = {"name": nm, "face": ref, "image": image, "width": width, "rotation": rotation, "center": list(c),
              "normal": list(face_normal(fc, c))}
        decals.append(it)
        d.render = r
        return {k: v for k, v in it.items() if k != "image"}
    return {"decal": _doc_edit(doc, f)}


@command(category="render")
def delete_decal(name: str) -> dict:
    """Delete a decal by name."""
    doc = APP.doc
    decals = (getattr(doc, "render", {}) or {}).get("decals", [])
    if not any(x["name"] == name for x in decals):
        raise DocumentError(f"no decal {name!r}")

    def f(d):
        d.render["decals"] = [x for x in d.render["decals"] if x["name"] != name]
    _doc_edit(doc, f)
    return {"deleted": name}


__all__ = ["dimxpert_auto", "dimxpert_clear", "set_render_scene", "add_decal", "delete_decal"
]
