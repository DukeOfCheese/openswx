from __future__ import annotations

import math

from openswx.core.document import DocumentError
from openswx.commands.support import _sk
from openswx.session import APP, Vec2, command

__all__ = ["sketch_make_block", "sketch_insert_block", "list_sketch_blocks", "delete_sketch_block", "sketch_from_image"]

# entity type -> the keys that hold point ids
_POINT_KEYS = {"line": ("p1", "p2"), "circle": ("c",), "arc": ("c", "s", "e"), "point": ("p",), "ellipse": ("c", "m")}


def _blocks(doc) -> dict:
    return doc.__dict__.setdefault("blocks", {})


@command(category="sketch")
def sketch_make_block(sketch: str, entities: list[str], name: str, insertion_point: Vec2 | str | None = None) -> dict:
    """Make Block: save sketch entities (lines, arcs, circles, ellipses, splines, points) as a named block of
    the part. insertion_point ([x, y] or a point id; default the sketch origin) is the point that lands on
    `position` when the block is inserted. Points shared between the entities stay shared in every copy. The
    sketch itself is left as it is; a block of the same name is replaced."""
    doc = APP.doc
    if not name.strip():
        raise DocumentError("give the block a name")
    if sketch not in doc.ctx.sketches:
        raise DocumentError(f"unknown sketch {sketch!r}")
    sk = doc.ctx.sketches[sketch][0]
    if not entities:
        raise DocumentError("select the entities to put in the block")
    if isinstance(insertion_point, str):
        ox, oy = sk.coords(sk.point_id(insertion_point))
    else:
        ox, oy = insertion_point or (0.0, 0.0)
    points, out = {}, []

    def rel(pid):
        if pid not in points:
            x, y = sk.coords(pid)
            points[pid] = [float(x) - ox, float(y) - oy]
        return pid

    for eid in entities:
        e = sk.entities.get(eid)
        if e is None:
            raise DocumentError(f"no entity {eid!r} in {sketch}")
        t = e["type"]
        if t == "spline":
            rec = {"type": t, "pts": [rel(p) for p in e["pts"]], "closed": bool(e.get("closed"))}
        elif t in _POINT_KEYS:
            rec = {"type": t, **{k: rel(e[k]) for k in _POINT_KEYS[t]}}
            for k in ("r", "b"):
                if k in e:
                    rec[k] = float(e[k])
        else:
            raise DocumentError(f"{eid} ({t}) cannot go in a block: lines, arcs, circles, ellipses, splines and points can")
        rec["construction"] = bool(e.get("construction"))
        out.append(rec)
    doc._checkpoint()
    _blocks(doc)[name.strip()] = {"points": points, "entities": out}
    doc.dirty = True
    doc.revision += 1
    return {"block": name.strip(), "entities": len(out), "points": len(points)}


@command(category="sketch")
def sketch_insert_block(sketch: str, name: str, position: Vec2 = (0.0, 0.0), angle: float = 0.0, scale: float = 1.0) -> dict:
    """Insert Block: add a copy of a block's geometry to a sketch with its insertion point at `position`,
    turned by `angle` degrees and scaled by `scale`. The copy is ordinary sketch geometry (as after Explode
    Block): it can be dimensioned, related and edited like the rest of the sketch."""
    doc = APP.doc
    block = _blocks(doc).get(name)
    if block is None:
        raise DocumentError(f"no block {name!r}; this part has {sorted(_blocks(doc))}")
    if scale <= 0:
        raise DocumentError("scale must be positive")
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))

    def place(p):
        x, y = p[0] * scale, p[1] * scale
        return [position[0] + x * ca - y * sa, position[1] + x * sa + y * ca]

    def f(sk):
        ids = {pid: sk.point_id(place(p)) for pid, p in block["points"].items()}
        made = []
        for e in block["entities"]:
            t, con = e["type"], e.get("construction", False)
            if t == "line":
                made.append(sk.add_line(ids[e["p1"]], ids[e["p2"]], con))
            elif t == "circle":
                made.append(sk.add_circle(ids[e["c"]], e["r"] * scale, con))
            elif t == "arc":
                made.append(sk.add_arc(ids[e["c"]], ids[e["s"]], ids[e["e"]], con))
            elif t == "ellipse":
                made.append(sk.add_ellipse(ids[e["c"]], ids[e["m"]], e["b"] * scale, con))
            elif t == "spline":
                made.append(sk.add_spline([ids[p] for p in e["pts"]], e.get("closed", False), con))
            elif t == "point":
                made.append(sk.add_point(ids[e["p"]], con))
        return {"entities": made, "points": list(ids.values())}
    return _sk(sketch, f)


@command(mutates=False, category="sketch")
def list_sketch_blocks() -> dict:
    """The part's sketch blocks, with how many entities each holds."""
    return {"blocks": [{"name": n, "entities": len(b["entities"]), "types": sorted({e["type"] for e in b["entities"]})}
                       for n, b in sorted(_blocks(APP.doc).items())]}


@command(category="sketch")
def delete_sketch_block(name: str) -> dict:
    """Delete a sketch block (copies already inserted stay: they are ordinary geometry)."""
    doc = APP.doc
    if name not in _blocks(doc):
        raise DocumentError(f"no block {name!r}")
    doc._checkpoint()
    del doc.blocks[name]
    doc.dirty = True
    doc.revision += 1
    return list_sketch_blocks.__wrapped__()


@command(category="sketch")
def sketch_from_image(sketch: str, path: str, width: float, threshold: float = 0.5, tolerance: float | None = None,
                      invert: bool = False, position: Vec2 = (0.0, 0.0), min_area: float | None = None,
                      max_segments: int = 3000) -> dict:
    """Trace a picture into sketch geometry: the dark shapes of an image (a scan of a drawing, a logo, a
    photographed gasket on a light background) become closed outlines of straight lines, holes included.
    width: how wide the whole picture is in mm (that sets the scale). threshold: 0-1 brightness below which a
    pixel counts as shape (invert=True traces the light shapes instead). tolerance: how far, in mm, an
    outline may be straightened (default 0.75 pixel) - larger gives fewer, longer lines. position: where the
    picture's lower-left corner goes. min_area: outlines enclosing less (mm2) are dropped as specks (default
    4 pixels). The outlines are ordinary lines: closed profiles that extrude, and can be dimensioned and edited.
    Formats: PNG, JPEG, BMP, GIF, TIFF."""
    import numpy as np

    from openswx.manufacturing.cam import contours, simplify
    try:
        from PIL import Image
    except ImportError as exc:
        raise DocumentError("tracing a picture needs the Pillow package (pip install pillow)") from exc
    if width <= 0:
        raise DocumentError("width (mm) must be positive")
    if not 0 < threshold < 1:
        raise DocumentError("threshold must be between 0 and 1")
    try:
        with Image.open(path) as im:
            im.load()
            if im.mode in ("RGBA", "LA", "P"):  # transparent areas count as background
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im)
            gray = np.asarray(im.convert("L"), float) / 255.0
    except (OSError, ValueError) as exc:
        raise DocumentError(f"cannot read the picture: {exc}") from exc
    h_px, w_px = gray.shape
    if w_px < 2 or h_px < 2:
        raise DocumentError("the picture is too small to trace")
    if w_px * h_px > 16_000_000:
        raise DocumentError("the picture is over 16 megapixels: scale it down first")
    cell = width / w_px
    field = (gray - threshold) if invert else (threshold - gray)  # >= 0 inside the shapes
    G = np.full((w_px + 2, h_px + 2), -1.0)  # x to the right, y up; a border of background all round
    G[1:-1, 1:-1] = field[::-1, :].T
    tol = float(tolerance) if tolerance is not None else 0.75 * cell
    floor = float(min_area) if min_area is not None else 4 * cell * cell
    loops = []
    for c in contours(G, position[0] - cell, position[1] - cell, cell):
        if len(c) < 3:
            continue
        area = 0.5 * abs(float(np.dot(c[:, 0], np.roll(c[:, 1], -1)) - np.dot(c[:, 1], np.roll(c[:, 0], -1))))
        if area < floor:
            continue
        s = simplify(c, tol, closed=True)[:-1]
        if len(s) >= 3:
            loops.append((area, s))
    if not loops:
        raise DocumentError("nothing to trace: no dark shape in the picture (try invert=True or another threshold)")
    total = sum(len(s) for _, s in loops)
    if total > max_segments:
        raise DocumentError(f"the outlines need {total} lines (limit {max_segments}): raise the tolerance or min_area, "
                            "or scale the picture down")
    loops.sort(key=lambda e: -e[0])

    def f(sk):
        made = []
        for _area, pts in loops:
            ids = [sk.point_id([float(x), float(y)]) for x, y in pts]
            made.append([sk.add_line(a, b) for a, b in zip(ids, ids[1:] + ids[:1])])
        return {"loops": len(made), "lines": sum(len(m) for m in made), "entities": [e for m in made for e in m]}
    out = _sk(sketch, f)
    out["result"].update(scale_mm_per_pixel=round(cell, 6), size=[round(w_px * cell, 4), round(h_px * cell, 4)],
                         areas=[round(a, 4) for a, _ in loops[:20]])
    return out
