"""Drawing tables: BOM, hole and revision tables, and the holes they list."""
from __future__ import annotations

import math
from xml.sax.saxutils import escape

import numpy as np
from build123d import Vector

from openswx.drawing import drawing as D
from openswx.drawing.drawing_sheets.helpers import (
    DrawingError,
    _bands,
    _kept,
    find_view,
    to2d,
    to3d,
    view_scale,
)
from openswx.drawing.drawing_sheets.views import frame, placement, view_geometry


def item_numbers(doc, sheet: dict) -> dict:
    """Component name -> BOM item number (the sheet's BOM order when it has one)."""
    if getattr(doc, "kind", "part") != "assembly":
        return {}
    tb = next((t for t in sheet.get("tables", []) if t["type"] == "bom"), {})
    item = {r["part"]: r["item"] for r in bom_rows(doc, tb)}
    return {c.name: item.get(c.link or c.name.rsplit("-", 1)[0]) for c in doc.components}


def _sheet_tables(doc, st, sheet, W, H, margin, cache) -> list[str]:
    out = []
    for tb in sheet.get("tables", []):
        if tb["type"] == "bom" and getattr(doc, "kind", "part") == "assembly":
            out += _bom_table(doc, tb, W, H, margin)
        elif tb["type"] == "revision":
            out += _revision_table(sheet.get("revisions", []), W, margin)
        elif tb["type"] == "hole":
            try:
                out += _hole_table(doc, st, tb, cache)
            except DrawingError as exc:
                out.append(f'<text x="{tb["x"]:.2f}" y="{tb["y"]:.2f}" font-size="3" fill="#c00">{escape(tb["id"])}: {escape(str(exc))}</text>')
    return out


def _table(x0, y0, heads, widths, rows, rh=5.5, attr="") -> list[str]:
    """A ruled table: bold heads, then rows of cell texts."""
    n = len(rows) + 1
    out = [f'<g{attr}><rect x="{x0:.2f}" y="{y0:.2f}" width="{sum(widths)}" height="{rh * n}" fill="#fff" stroke="#000" stroke-width="0.35"/>']
    xs = [x0 + sum(widths[:i]) for i in range(len(widths))]
    for i, (h, x) in enumerate(zip(heads, xs)):
        out.append(f'<text x="{x + 1.5:.2f}" y="{y0 + rh * 0.72:.2f}" font-size="2.6" font-weight="700">{escape(h)}</text>')
        if i:
            out.append(f'<line x1="{x:.2f}" y1="{y0:.2f}" x2="{x:.2f}" y2="{y0 + rh * n:.2f}" stroke="#000" stroke-width="0.25"/>')
    for r, row in enumerate(rows, 1):
        y = y0 + rh * r
        out.append(f'<line x1="{x0:.2f}" y1="{y:.2f}" x2="{x0 + sum(widths):.2f}" y2="{y:.2f}" stroke="#000" stroke-width="0.25"/>')
        for text, x in zip(row, xs):
            out.append(f'<text x="{x + 1.5:.2f}" y="{y + rh * 0.72:.2f}" font-size="2.8">{escape(str(text))}</text>')
    out.append("</g>")
    return out


def bom_rows(doc, tb: dict) -> list[dict]:
    """The BOM's rows: {"item", "part", "part_number", "description", "qty"} - parts grouped by source, in the
    table's own order (tb["order"]) then as they come, with the user's part number / description overrides."""
    from collections import Counter
    counts = Counter((c.link or c.name.rsplit("-", 1)[0]) for c in doc.components if not getattr(c, "envelope", False))
    order = [p for p in tb.get("order", []) if p in counts] + [p for p in counts if p not in tb.get("order", [])]
    over = tb.get("rows", {})
    out = []
    for i, part in enumerate(order, 1):
        o = over.get(part, {})
        name = part.replace("\\", "/").rsplit("/", 1)[-1]
        out.append({"item": i, "part": part, "part_number": o.get("part_number") or name.rsplit(".", 1)[0],
                    "description": o.get("description", ""), "qty": counts[part]})
    return out


def _bom_table(doc, tb, W, H, margin, title_h=44) -> list[str]:
    """The BOM: where it was moved to, else on the title block's top edge at the right (SOLIDWORKS' anchor)."""
    rows = bom_rows(doc, tb)
    widths, rh = (17, 52, 56, 14), 5.5
    x0 = tb.get("x", W - margin - sum(widths))
    y0 = tb.get("y", H - margin - title_h - rh * (len(rows) + 1))
    return _table(x0, y0, ("ITEM NO.", "PART NUMBER", "DESCRIPTION", "QTY."), widths,
                  [(r["item"], r["part_number"], r["description"], r["qty"]) for r in rows], attr=' data-table="BOM"')


def view_holes(doc, st: dict, view: dict, cache: dict | None = None) -> list[dict]:
    """The holes seen end-on in a view, tagged as a SOLIDWORKS hole table does (a letter per size, smallest
    first, numbered left to right): {"tag", "c" (view coordinates), "d" (diameter), "depth" (None: through)}."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.gp import gp_Pnt, gp_Vec
    body = doc.body
    if body is None:
        raise DrawingError("the part has no body")
    src = find_view(st, view["parent"]) if view["kind"] == "detail" else view
    fr = frame(st, src)
    d = np.asarray(fr[0], float)
    found: dict = {}
    for f in body.faces():
        ad = BRepAdaptor_Surface(f.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            continue
        cyl = ad.Cylinder()
        ax = cyl.Axis()
        a = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        if abs(abs(float(a @ d)) - 1) > 1e-6:
            continue
        loc = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
        prop = BRepGProp_Face(f.wrapped)
        u0, u1, v0, v1 = prop.Bounds()
        pnt, nrm = gp_Pnt(), gp_Vec()
        prop.Normal((u0 + u1) / 2, (v0 + v1) / 2, pnt, nrm)
        p = np.array([pnt.X(), pnt.Y(), pnt.Z()])
        foot = loc + a * float((p - loc) @ a)
        if float((foot - p) @ np.array([nrm.X(), nrm.Y(), nrm.Z()])) <= 0:
            continue  # material inside the cylinder: a boss, not a hole
        c2 = to2d(loc, fr)
        key = (round(c2[0], 4), round(c2[1], 4), round(cyl.Radius(), 4))
        ext = [float(np.asarray(tuple(v)) @ d) for v in f.vertices()] or [0.0]
        lo, hi = found.get(key, (math.inf, -math.inf))
        found[key] = (min(lo, *ext), max(hi, *ext))
    bb = body.bounding_box()
    corners = [np.array([x, y, z]) for x in (bb.min.X, bb.max.X) for y in (bb.min.Y, bb.max.Y) for z in (bb.min.Z, bb.max.Z)]
    span = (min(float(c @ d) for c in corners), max(float(c @ d) for c in corners))
    crop, bands = src.get("crop"), _bands(src)
    holes = []
    for (cx, cy, r), (lo, hi) in found.items():
        if not _kept((cx, cy), crop, bands):
            continue
        axis_pts = [to3d((cx, cy), fr, span[0] + (span[1] - span[0]) * (k + 0.5) / 40) for k in range(40)]
        through = not any(body.is_inside(Vector(*q)) for q in axis_pts)
        holes.append({"c": (cx, cy), "d": 2 * r, "depth": None if through else hi - lo})
    sizes = sorted({(round(h["d"], 4), h["depth"] is None, round(h["depth"] or 0, 4)) for h in holes},
                   key=lambda s: (s[0], not s[1], s[2]))
    for h in holes:
        h["_s"] = sizes.index((round(h["d"], 4), h["depth"] is None, round(h["depth"] or 0, 4)))
    holes.sort(key=lambda h: (h["_s"], round(h["c"][0], 3), -h["c"][1]))
    num: dict = {}
    for h in holes:
        num[h["_s"]] = num.get(h["_s"], 0) + 1
        h["tag"] = f"{_letters(h['_s'])}{num[h['_s']]}"
        del h["_s"]
    return holes


def _letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _hole_table(doc, st, tb, cache) -> list[str]:
    v = find_view(st, tb["view"])
    g = view_geometry(doc, st, v, cache)
    t = placement(st, v, g)
    holes = view_holes(doc, st, v, cache)
    ox, oy = tb["origin"]
    out = []
    sx, sy = t(ox, oy)  # the datum origin: X and Y arrows
    xd, yd = t(ox + 1, oy), t(ox, oy + 1)
    ux = ((xd[0] - sx), (xd[1] - sy))
    uy = ((yd[0] - sx), (yd[1] - sy))
    nx, ny = math.hypot(*ux) or 1, math.hypot(*uy) or 1
    for (u, n, lab) in ((ux, nx, "X"), (uy, ny, "Y")):
        ex, ey = sx + u[0] / n * 8, sy + u[1] / n * 8
        out.append(f'<line x1="{sx:.3f}" y1="{sy:.3f}" x2="{ex:.3f}" y2="{ey:.3f}" stroke="#000" stroke-width="0.25"/>')
        out.append(D._arrow(ex, ey, math.atan2(u[1], u[0])))
        out.append(f'<text x="{ex + u[0] / n * 2 - 1:.3f}" y="{ey + u[1] / n * 2 + 1:.3f}" font-size="2.6">{lab}</text>')
    s = view_scale(st, v)
    rows = []
    for h in holes:
        hx, hy = t(*h["c"])
        rr = h["d"] / 2 * s
        out.append(f'<text x="{hx + rr * 0.8 + 0.6:.3f}" y="{hy - rr * 0.8 - 0.6:.3f}" font-size="2.6">{escape(h["tag"])}</text>')
        size = f"⌀{D._fmt(h['d'])} " + ("THRU" if h["depth"] is None else f"↧{D._fmt(h['depth'])}")
        rows.append((h["tag"], D._fmt(h["c"][0] - ox), D._fmt(h["c"][1] - oy), size))
    out += _table(tb["x"], tb["y"], ("TAG", "X LOC", "Y LOC", "SIZE"), (14, 22, 22, 34), rows, attr=f' data-table="{escape(tb["id"])}"')
    return out


def _revision_table(rows, W, margin) -> list[str]:
    cw = (12, 70, 24, 22)
    x0, y0, rh = W - margin - sum(cw), margin, 5.5
    heads = ("REV", "DESCRIPTION", "DATE", "APPROVED")
    n = len(rows) + 1
    out = [f'<rect x="{x0}" y="{y0}" width="{sum(cw)}" height="{rh * n}" fill="#fff" stroke="#000" stroke-width="0.35"/>']
    xs = [x0 + sum(cw[:i]) for i in range(len(cw))]
    for i, (h, x) in enumerate(zip(heads, xs)):
        out.append(f'<text x="{x + 1.5}" y="{y0 + rh * 0.72}" font-size="2.6" font-weight="700">{h}</text>')
        if i:
            out.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y0 + rh * n}" stroke="#000" stroke-width="0.25"/>')
    for r, row in enumerate(rows, 1):
        y = y0 + rh * r
        out.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + sum(cw)}" y2="{y}" stroke="#000" stroke-width="0.25"/>')
        for k, x in zip(("rev", "description", "date", "approved"), xs):
            out.append(f'<text x="{x + 1.5}" y="{y + rh * 0.72}" font-size="2.8">{escape(str(row.get(k, "")))}</text>')
    return out
