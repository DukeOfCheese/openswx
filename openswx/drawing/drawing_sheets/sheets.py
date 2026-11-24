"""Drawing sheets: layout, rendering to SVG, title block and placing new views."""
from __future__ import annotations

import math
import re
from xml.sax.saxutils import escape

import numpy as np

from openswx.drawing import drawing as D
from openswx.drawing.drawing_sheets.annotations import _render_balloons
from openswx.drawing.drawing_sheets.dimensions import _model_dims, _render_dims
from openswx.drawing.drawing_sheets.helpers import (
    DrawingError,
    HID,
    THIN,
    VIS,
    _bands,
    _kept,
    _polyline,
    next_id,
    sheet_of,
    to2d,
    view_scale,
)
from openswx.drawing.drawing_sheets.tables import _sheet_tables
from openswx.drawing.drawing_sheets.views import (
    LABELS,
    _project,
    align,
    frame,
    half_size,
    placement,
    view_circles,
    view_geometry,
)


def render_sheet(doc, st: dict, sheet: dict, cache: dict | None = None) -> str:
    cache = {} if cache is None else cache
    W, H = D.SHEETS.get(sheet.get("size", "A3"), D.SHEETS["A3"])
    margin, title_h = 12, 44
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}" '
           f'font-family="Segoe UI, Arial, sans-serif" data-sheet="{escape(sheet["name"])}">',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="#fff"/>',
           f'<rect x="{margin}" y="{margin}" width="{W - 2 * margin}" height="{H - 2 * margin}" fill="none" stroke="#000" stroke-width="0.5"/>',
           '<defs><pattern id="hatch" width="2" height="2" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           '<line x1="0" y1="0" x2="0" y2="2" stroke="#000" stroke-width="0.18"/></pattern></defs>']
    for v in sheet["views"]:
        align(st, v, cache, doc)
    for v in sheet["views"]:
        try:
            out += _render_view(doc, st, v, cache)
        except DrawingError as exc:
            out.append(f'<text x="{v["x"]:.2f}" y="{v["y"]:.2f}" font-size="3" fill="#c00" text-anchor="middle">'
                       f'{escape(v["id"])}: {escape(str(exc))}</text>')
    out += _sheet_tables(doc, st, sheet, W, H, margin, cache)
    from openswx.drawing import drawing_more as DM
    out += DM.annotations(st, sheet)
    if (st.get("inspection") or {}).get("balloons"):
        from openswx.analysis.inspection import balloon_svg
        out += balloon_svg(st, sheet, "\n".join(out))
    if st.get("format"):  # a sheet format (title-block template) chosen for the drawing
        vals = DM.title_fields(doc, sheet, st.get("title"), D._scale_label(float(sheet.get("scale") or 1.0)),
                               sheet.get("projection", "third"))
        out += DM.title_block_svg(st["format"], vals, W, H, margin)
    else:
        out += _title_block(doc, sheet, W, H, margin, title_h, st.get("title"))
    out.append("</svg>")
    return "\n".join(out)


def _render_view(doc, st, v, cache) -> list[str]:
    g = view_geometry(doc, st, v, cache)
    t = placement(st, v, g)
    hw, hh = half_size(st, v, g)
    out = [f'<g data-view="{escape(v["id"])}"><rect class="view-box" x="{v["x"] - hw - 2:.2f}" y="{v["y"] - hh - 2:.2f}" '
           f'width="{2 * hw + 4:.2f}" height="{2 * hh + 4:.2f}" fill="none" stroke="none" pointer-events="all"/>']
    clip_end = []
    if v["kind"] == "detail":
        cid = f'clip-{v["id"]}'.replace(" ", "_")
        r = float(v["radius"]) * view_scale(st, v)
        out.append(f'<clipPath id="{cid}"><circle cx="{v["x"]:.3f}" cy="{v["y"]:.3f}" r="{r:.3f}"/></clipPath>')
        out.append(f'<g clip-path="url(#{cid})">')
        clip_end = ["</g>", f'<circle cx="{v["x"]:.3f}" cy="{v["y"]:.3f}" r="{r:.3f}" fill="none" {THIN}/>']
    for pts in g["hatch"]:
        d = "M" + " L".join(f"{a:.3f},{b:.3f}" for a, b in (t(*q) for q in pts)) + " Z"
        out.append(f'<path d="{d}" fill="url(#hatch)" stroke="none" fill-rule="evenodd"/>')
    pictorial = v["kind"] == "model" and v.get("orientation") in ("isometric", "dimetric", "trimetric")
    if v.get("hidden", "removed" if pictorial else "visible") == "visible":
        out += [_polyline([t(*q) for q in poly], HID) for poly in g["hid"]]
    out += [_polyline([t(*q) for q in poly], VIS) for poly in g["vis"]]
    if v.get("center_marks"):
        out += _center_marks(st, v, g, t)
    if v.get("centerlines") and v["kind"] in ("model", "projected", "auxiliary"):
        out += _centerlines(doc, st, v, g, t)
    from openswx.drawing import drawing_more as DM
    if v["kind"] in ("model", "projected", "auxiliary", "section"):
        out += DM.thread_marks(doc, st, v, t, frame, to2d, view_scale(st, v))
    out += DM.alternate_phantom(doc, st, v, t, frame, _project)
    out += clip_end
    out += _break_lines(v, g, t, hw, hh)
    out += _render_balloons(doc, sheet_of(st, v["id"]), v, t)
    if v.get("dims") and getattr(doc, "kind", "part") == "part" and v["kind"] in ("model", "projected", "auxiliary"):
        out += _model_dims(doc, g["frame"], t)
    dims = DM.layered_dims(st, sheet_of(st, v["id"]), v, lambda sh: _render_dims(st, sh, v, g, t))
    out += dims
    out += _child_marks(doc, st, v, t, cache)
    label = v.get("label") or _default_label(st, v)
    s = view_scale(st, v)
    sheet_scale = float(sheet_of(st, v["id"]).get("scale") or 1.0)
    if abs(s - sheet_scale) > 1e-9 and v["kind"] != "detail":
        label += f"  SCALE {D._scale_label(s)}"
    if label:
        # below the view, and below any dimensions placed under it
        low = max([v["y"] + hh] + [float(y) for e in dims for y in re.findall(r' y[12]?="([-\d.]+)"', e)])
        out.append(f'<text x="{v["x"]:.2f}" y="{low + 7:.2f}" font-size="3" text-anchor="middle">{escape(label)}</text>')
    out.append("</g>")
    return out


def _center_marks(st, v, g, t) -> list[str]:
    """Center marks on every circle seen face-on (chain lines a little past the circle)."""
    out, s = [], view_scale(st, v)
    for c, r in view_circles(g):
        (cx, cy), rs = t(*c), r * s
        if rs < 0.6:
            continue
        L = rs + 2.0
        for x1, y1, x2, y2 in ((cx - L, cy, cx + L, cy), (cx, cy - L, cx, cy + L)):
            out.append(f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" stroke="#000" stroke-width="0.13" '
                       f'stroke-dasharray="4 1 0.8 1"/>')
    return out


def _centerlines(doc, st, v, g, t) -> list[str]:
    """Centerlines along the axes of cylindrical faces seen side-on, a little past the faces."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    body = doc.body
    if body is None:
        return []
    fr = frame(st, v)
    d = np.asarray(fr[0], float)
    crop, bands = v.get("crop"), _bands(v)
    segs = []
    for f in body.faces():
        ad = BRepAdaptor_Surface(f.wrapped)
        if ad.GetType() != GeomAbs_Cylinder:
            continue
        ax = ad.Cylinder().Axis()
        a = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        if abs(float(a @ d)) > 1e-6:
            continue
        loc = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
        ts = [float((np.asarray(tuple(q)) - loc) @ a) for q in f.vertices()] or [0.0]
        p, q = to2d(loc + a * min(ts), fr), to2d(loc + a * max(ts), fr)
        if math.dist(p, q) < 1e-6 or not (_kept(p, crop, bands) or _kept(q, crop, bands)):
            continue
        key = tuple(round(c, 4) for c in (*p, *q))
        if key not in segs:
            segs.append(key)
    out = []
    for x1, y1, x2, y2 in segs:
        (sx, sy), (ex, ey) = t(x1, y1), t(x2, y2)
        L = math.hypot(ex - sx, ey - sy)
        ux, uy = (ex - sx) / L, (ey - sy) / L
        out.append(f'<line x1="{sx - ux * 2:.3f}" y1="{sy - uy * 2:.3f}" x2="{ex + ux * 2:.3f}" y2="{ey + uy * 2:.3f}" '
                   f'stroke="#000" stroke-width="0.13" stroke-dasharray="6 1.2 1 1.2"/>')
    return out


def _default_label(st, v) -> str:
    if v["kind"] == "model":
        return LABELS.get(v.get("orientation", "front"), "")
    if v["kind"] == "section":
        return f'SECTION {v.get("letter", "A")}-{v.get("letter", "A")}'
    if v["kind"] == "detail":
        return f'DETAIL {v.get("letter", "B")}  SCALE {D._scale_label(view_scale(st, v))}'
    if v["kind"] == "auxiliary":
        return f'VIEW {v.get("letter", "C")}'
    return ""


def _child_marks(doc, st, parent, t, cache) -> list[str]:
    """Section lines, detail circles and auxiliary arrows a parent view shows for its children."""
    out = []
    for sh in st["sheets"]:
        for c in sh["views"]:
            if c.get("parent") != parent["id"]:
                continue
            if c["kind"] == "section":
                (x1, y1), (x2, y2) = t(*c["a"]), t(*c["b"])
                L = math.hypot(x2 - x1, y2 - y1) or 1.0
                ux, uy = (x2 - x1) / L, (y2 - y1) / L
                out.append(f'<line x1="{x1 - ux * 4:.3f}" y1="{y1 - uy * 4:.3f}" x2="{x2 + ux * 4:.3f}" y2="{y2 + uy * 4:.3f}" '
                           f'stroke="#000" stroke-width="0.35" stroke-dasharray="6 1.5 1 1.5"/>')
                # arrows point the way the section looks (away from the viewer of the section)
                fr = frame(st, c)
                lx, ly = to2d(-fr[0], frame(st, parent))
                ang = math.atan2(-ly, lx)
                for x, y in ((x1 - ux * 4, y1 - uy * 4), (x2 + ux * 4, y2 + uy * 4)):
                    ex, ey = x + 5 * math.cos(ang), y + 5 * math.sin(ang)
                    out.append(f'<line x1="{x:.3f}" y1="{y:.3f}" x2="{ex:.3f}" y2="{ey:.3f}" stroke="#000" stroke-width="0.35"/>')
                    out.append(D._arrow(ex, ey, ang))
                    out.append(f'<text x="{x - 2.5 * math.cos(ang):.3f}" y="{y - 2.5 * math.sin(ang) + 1.2:.3f}" font-size="3.4" '
                               f'text-anchor="middle" font-weight="700">{escape(c.get("letter", "A"))}</text>')
            elif c["kind"] == "detail":
                cx, cy = t(*c["center"])
                r = float(c["radius"]) * view_scale(st, parent)
                out.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" fill="none" stroke="#000" stroke-width="0.25" stroke-dasharray="4 1.5"/>')
                out.append(f'<text x="{cx + r + 1:.3f}" y="{cy - r:.3f}" font-size="3.4" font-weight="700">{escape(c.get("letter", "B"))}</text>')
            elif c["kind"] == "auxiliary":
                fr = frame(st, c)
                lx, ly = to2d(-fr[0], frame(st, parent))
                ang = math.atan2(-ly, lx)
                g = view_geometry(doc, st, parent, cache)
                bx, by = t((g["box"][0] + g["box"][2]) / 2, (g["box"][1] + g["box"][3]) / 2)
                sx, sy = bx - 14 * math.cos(ang), by - 14 * math.sin(ang)
                out.append(f'<line x1="{sx:.3f}" y1="{sy:.3f}" x2="{sx + 6 * math.cos(ang):.3f}" y2="{sy + 6 * math.sin(ang):.3f}" stroke="#000" stroke-width="0.35"/>')
                out.append(D._arrow(sx + 6 * math.cos(ang), sy + 6 * math.sin(ang), ang))
                out.append(f'<text x="{sx - 2:.3f}" y="{sy:.3f}" font-size="3.4" font-weight="700">{escape(c.get("letter", "C"))}</text>')
    return out


def _break_lines(v, g, t, hw, hh) -> list[str]:
    """The pair of zig-zag break lines at each break band of a broken view."""
    out = []
    for i, lo, hi in _bands(v):
        for c in (lo, hi):
            if i == 0:
                x = t(c, g["box"][1])[0]
                y0, y1, m = v["y"] - hh - 2, v["y"] + hh + 2, v["y"]
                pts = [(x, y0), (x, m - 2), (x + 1.5, m - 1), (x - 1.5, m + 1), (x, m + 2), (x, y1)]
            else:
                y = t(g["box"][0], c)[1]
                x0, x1, m = v["x"] - hw - 2, v["x"] + hw + 2, v["x"]
                pts = [(x0, y), (m - 2, y), (m - 1, y - 1.5), (m + 1, y + 1.5), (m + 2, y), (x1, y)]
            out.append(_polyline(pts, THIN))
    return out


TITLE_FIELDS = ("company", "title", "dwg_no", "material", "drawn", "drawn_date", "checked", "approved")


def _title_block(doc, sheet, W, H, margin, title_h, fields: dict | None = None) -> list[str]:
    """The sheet format's title block. fields (the drawing's "title" dict, set with drawing_title_block)
    override what comes from the model: company, title, dwg_no, material, drawn, drawn_date, checked,
    approved."""
    import datetime as _dt
    fields = fields or {}
    tb_w, x0, y0 = 170, W - margin - 170, H - margin - title_h
    mat = fields.get("material") or getattr(doc, "material", {}).get("name") or "<not specified>"
    props = {k.lower(): v for k, v in getattr(doc, "properties", {}).items()}
    title = fields.get("title") or props.get("description") or doc.name
    rev = (sheet.get("revisions") or [{}])[-1].get("rev", "")
    drawn = fields.get("drawn") or props.get("author") or props.get("drawnby") or ""
    date = fields.get("drawn_date") or _dt.date.today().isoformat()
    signed = "   ".join(f"{k} {fields[f]}" for k, f in (("CHK", "checked"), ("APPR", "approved")) if fields.get(f))
    rows = [("TITLE", escape(title)), ("DWG NO.", escape(fields.get("dwg_no") or props.get("partno") or props.get("part number") or doc.name)),
            ("MATERIAL", escape(str(mat))),
            ("SCALE", D._scale_label(float(sheet.get("scale") or 1.0)) + f"   SHEET {escape(sheet['name'])}  {sheet.get('size', 'A3')}"),
            ("PROJECTION", "THIRD ANGLE" if sheet.get("projection", "third") == "third" else "FIRST ANGLE"),
            ("DRAWN", escape(f"{drawn}   {date}".strip())), ("CHECKED", escape(signed)), ("REV", escape(str(rev)))]
    out = [f'<rect x="{x0}" y="{y0}" width="{tb_w}" height="{title_h}" fill="#fff" stroke="#000" stroke-width="0.5"/>']
    if fields.get("company"):
        out.append(f'<text x="{x0 + tb_w - 2}" y="{y0 + title_h / len(rows) * 0.72:.2f}" font-size="3" font-weight="700" '
                   f'text-anchor="end">{escape(fields["company"])}</text>')
    rh = title_h / len(rows)
    for i, (k, v) in enumerate(rows):
        y = y0 + rh * i
        if i:
            out.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + tb_w}" y2="{y}" stroke="#000" stroke-width="0.25"/>')
        out.append(f'<text x="{x0 + 2}" y="{y + rh * 0.72:.2f}" font-size="2.6" fill="#555">{k}</text>')
        out.append(f'<text x="{x0 + 34}" y="{y + rh * 0.72:.2f}" font-size="{3.6 if i == 0 else 3}" '
                   f'font-weight="{700 if i == 0 else 400}">{v}</text>')
    out.append(f'<line x1="{x0 + 32}" y1="{y0}" x2="{x0 + 32}" y2="{y0 + title_h}" stroke="#000" stroke-width="0.25"/>')
    return out


def new_drawing(doc, size: str = "A3", projection: str = "third", scale: float | None = None,
                standard_views: bool = True) -> dict:
    """A drawing with one sheet; with standard_views: Front, Top, the side view and an isometric, laid out
    and scaled to fit the sheet as Make Drawing from Part does."""
    st = {"sheets": [], "active": 0}
    sheet = {"name": "Sheet1", "size": size, "scale": scale or 1.0, "projection": projection, "views": [],
             "notes": [], "datums": [], "gdt": [], "revisions": [], "tables": []}
    st["sheets"].append(sheet)
    if getattr(doc, "kind", "part") == "assembly":
        sheet["tables"].append({"type": "bom"})
    if standard_views:
        _standard_layout(doc, st, sheet, scale)
    return st


def _standard_layout(doc, st, sheet, scale):
    W, H = D.SHEETS.get(sheet["size"], D.SHEETS["A3"])
    margin, title_h = 12, 44
    front = {"id": next_id(st, "Drawing View"), "kind": "model", "orientation": "front", "x": 0.0, "y": 0.0}
    sheet["views"].append(front)
    top = {"id": next_id(st, "Drawing View"), "kind": "projected", "parent": front["id"], "side": "top", "x": 0.0, "y": 0.0}
    sheet["views"].append(top)
    side = {"id": next_id(st, "Drawing View"), "kind": "projected", "parent": front["id"], "side": "right", "x": 0.0, "y": 0.0}
    sheet["views"].append(side)
    iso = {"id": next_id(st, "Drawing View"), "kind": "model", "orientation": "isometric", "x": 0.0, "y": 0.0}
    sheet["views"].append(iso)
    cache: dict = {}
    sz = {v["id"]: (lambda b: (b[2] - b[0], b[3] - b[1]))(view_geometry(doc, st, v, cache)["box"]) for v in sheet["views"]}
    fw, fh = sz[front["id"]]
    tw, th = sz[top["id"]]
    sw, sh = sz[side["id"]]
    if not scale:
        avail_w, avail_h = (W - 2 * margin) * 0.62, (H - 2 * margin - title_h) * 0.85
        scale = next((s for s in D.SCALES if (fw + sw + 30) * s <= avail_w and (fh + th + 30) * s <= avail_h), D.SCALES[-1])
    sheet["scale"] = scale
    gap = 22
    fx = margin + 20 + fw * scale / 2
    third = sheet["projection"] == "third"
    fy = (H - margin - title_h - 18 - fh * scale / 2) if third else (margin + 18 + fh * scale / 2)
    front.update(x=fx, y=fy)
    top.update(x=fx, y=fy - (fh + th) * scale / 2 - gap if third else fy + (fh + th) * scale / 2 + gap)
    side.update(x=fx + (fw + sw) * scale / 2 + gap, y=fy)
    iw, ih = sz[iso["id"]]
    iso.update(scale=scale * 0.8, x=W - margin - 15 - iw * scale * 0.4, y=margin + 15 + ih * scale * 0.4)


def default_position(doc, st, parent: dict, side: str, child: dict, gap: float = 22.0) -> tuple[float, float]:
    """Where a new projected / section / auxiliary / detail view goes: beside its parent on the given side,
    moved further out along that side (projected views keep their alignment) until it is clear of the other
    views, the border and the title block; a detail view may go anywhere free."""
    cache: dict = {}
    sheet = sheet_of(st, parent["id"])
    W, H = D.SHEETS.get(sheet.get("size", "A3"), D.SHEETS["A3"])
    margin, title_h, tb_w = 12, 44, 170
    pg = view_geometry(doc, st, parent, cache)
    phw, phh = half_size(st, parent, pg)
    cg = view_geometry(doc, st, child, cache)
    chw, chh = half_size(st, child, cg)
    boxes = []
    for v in sheet["views"]:
        if v["id"] == child["id"]:
            continue
        try:
            hw, hh = half_size(st, v, view_geometry(doc, st, v, cache))
        except DrawingError:
            continue
        boxes.append((v["x"] - hw - 4, v["y"] - hh - 4, v["x"] + hw + 4, v["y"] + hh + 12))  # (+ the label)
    boxes.append((W - margin - tb_w, H - margin - title_h, W - margin, H - margin))  # title block

    def free(x, y):
        b = (x - chw, y - chh, x + chw, y + chh + 8)
        if b[0] < margin + 2 or b[1] < margin + 2 or b[2] > W - margin - 2 or b[3] > H - margin - 2:
            return False
        return not any(b[0] < o[2] and b[2] > o[0] and b[1] < o[3] and b[3] > o[1] for o in boxes)

    step = {"right": (1, 0), "left": (-1, 0), "top": (0, -1), "bottom": (0, 1)}
    ux, uy = step[side]
    x0 = parent["x"] + ux * (phw + chw + gap)
    y0 = parent["y"] + uy * (phh + chh + gap)
    for k in range(0, 60):  # out along the requested side first
        x, y = x0 + ux * 5 * k, y0 + uy * 5 * k
        if free(x, y):
            return x, y
    if child["kind"] != "projected":  # anywhere free on the sheet, nearest the parent first
        cands = [(x, y) for x in np.arange(margin + chw + 4, W - margin - chw - 4, 5.0)
                 for y in np.arange(margin + chh + 4, H - margin - chh - 12, 5.0)]
        cands.sort(key=lambda q: math.hypot(q[0] - parent["x"], q[1] - parent["y"]))
        for x, y in cands:
            if free(x, y):
                return float(x), float(y)
    return x0, y0


def sheet_to_view(st: dict, doc, view: dict, xy) -> tuple[float, float]:
    """A sheet point (mm) -> the view's 2D model coordinates (for picking cut lines and detail circles)."""
    g = view_geometry(doc, st, view, {})
    s = view_scale(st, view)
    b = g["box"]
    mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    x, y = mx + (xy[0] - view["x"]) / s, my - (xy[1] - view["y"]) / s
    return g["warp"][1](x, y) if g.get("warp") else (x, y)


def next_letter(st: dict) -> str:
    used = {v.get("letter") for sh in st.get("sheets", []) for v in sh["views"]}
    for c in "ABCDEFGHJKLMNPRSTUVWXYZ":  # (I, O and Q are not used for view letters)
        if c not in used:
            return c
    return "Z"
