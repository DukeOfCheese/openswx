"""Sketch blocks (same command registry as session.py, which re-exports them): a named group of sketch
geometry kept with the part and inserted again anywhere, at any angle and scale."""
from __future__ import annotations

import math

from .document import DocumentError
from .session import APP, Vec2, _sk, command

__all__ = ["sketch_make_block", "sketch_insert_block", "list_sketch_blocks", "delete_sketch_block"]

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
