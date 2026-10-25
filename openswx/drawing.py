"""2D drawings: standard projected views (hidden-line removal), overall dimensions, border and title block.

Third-angle projection (ANSI) places Top above and Right to the right of Front; first-angle (ISO)
places Top below and Left to the right. Output is an SVG sheet in millimetres.
"""
from __future__ import annotations

import datetime as _dt
import math
from xml.sax.saxutils import escape

from build123d import Vector

SHEETS = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594), "A0": (1189, 841),
          "A4 portrait": (210, 297), "ANSI A": (279.4, 215.9), "ANSI B": (431.8, 279.4), "ANSI C": (558.8, 431.8),
          "ANSI D": (863.6, 558.8), "ANSI E": (1117.6, 863.6)}  # width, height mm
SCALES = [10, 5, 4, 2, 1, 1 / 2, 1 / 4, 1 / 5, 1 / 10, 1 / 20, 1 / 50, 1 / 100]
VIEW_DIRS = {  # view direction (towards the viewer) and up vector
    "front": ((0, 0, 1), (0, 1, 0)), "back": ((0, 0, -1), (0, 1, 0)), "top": ((0, 1, 0), (0, 0, -1)),
    "bottom": ((0, -1, 0), (0, 0, 1)), "right": ((1, 0, 0), (0, 1, 0)), "left": ((-1, 0, 0), (0, 1, 0)),
    "isometric": ((1, 1, 1), (0, 1, 0)),
}


def project(shape, view: str):
    d, up = VIEW_DIRS[view]
    bb = shape.bounding_box()
    c = bb.center()
    origin = c + Vector(*d).normalized() * (bb.diagonal * 10 + 100)
    vis, hid = shape.project_to_viewport(origin, up, c)
    return _polys(vis), _polys(hid)


def _polys(edges):
    out = []
    for e in edges:
        n = 2 if e.geom_type.name.lower() == "line" else 40
        out.append([(e.position_at(i / (n - 1)).X, e.position_at(i / (n - 1)).Y) for i in range(n)])
    return out


def _bbox(polys):
    xs = [x for p in polys for x, _ in p] or [0]
    ys = [y for p in polys for _, y in p] or [0]
    return min(xs), min(ys), max(xs), max(ys)


def _scale_label(s: float) -> str:
    """Scale as a ratio: 2:1, 1:5, and non-standard ones exactly (0.8 -> 4:5)."""
    from fractions import Fraction
    f = Fraction(s).limit_denominator(100)
    return f"{f.numerator}:{f.denominator}"


def make_drawing(doc, sheet: str = "A3", projection: str = "third", scale: float | None = None,
                 state: dict | None = None) -> dict:
    """state (the document's drawing settings): offsets {view: [dx, dy]} (sheet mm), notes, datums, gdt,
    model_items (bool). Top/side views move with the front view and slide along their projection axis."""
    state = state if state is not None else getattr(doc, "drawing", None) or {}
    units = getattr(doc, "units", "mm") or "mm"
    _UNIT.update(f=UNIT_FACTORS.get(units, 1.0), name=units if units in UNIT_FACTORS else "mm")
    if state.get("sheets"):  # a drawing document with sheets and placed views (drawing_sheets)
        from .drawing_sheets import active_sheet, render_sheet
        sh = active_sheet(state)
        return {"svg": render_sheet(doc, state, sh), "scale": sh.get("scale", 1.0),
                "scale_label": _scale_label(float(sh.get("scale") or 1.0)), "sheet": sh.get("size", "A3"),
                "sheet_name": sh["name"], "sheets": [x["name"] for x in state["sheets"]],
                "views": [v["id"] for v in sh["views"]], "projection": sh.get("projection", "third")}
    sheet = state.get("sheet", sheet)
    projection = state.get("projection", projection)
    scale = state.get("scale", scale)
    body = doc.body
    if body is None:
        raise ValueError("the part has no body to draw")
    W, H = SHEETS.get(sheet, SHEETS["A3"])
    side = "right" if projection == "third" else "left"
    names = ["front", "top", side, "isometric"]
    views = {n: project(body, n) for n in names}
    boxes = {n: _bbox(v[0] + v[1]) for n, v in views.items()}
    size = {n: (b[2] - b[0], b[3] - b[1]) for n, b in boxes.items()}
    margin, title_h = 12, 44
    avail_w, avail_h = (W - 2 * margin) * 0.62, (H - 2 * margin - title_h) * 0.85
    need_w = size["front"][0] + size[side][0] + 30
    need_h = size["front"][1] + size["top"][1] + 30
    if scale is None:
        scale = next((s for s in SCALES if need_w * s <= avail_w and need_h * s <= avail_h), SCALES[-1])
    gap = 22
    # sheet positions (centres) in mm, SVG y grows downward
    fx = margin + 20 + size["front"][0] * scale / 2
    if projection == "third":
        fy = H - margin - title_h - 18 - size["front"][1] * scale / 2
        ty = fy - (size["front"][1] + size["top"][1]) * scale / 2 - gap
    else:
        fy = margin + 18 + size["front"][1] * scale / 2
        ty = fy + (size["front"][1] + size["top"][1]) * scale / 2 + gap
    sx = fx + (size["front"][0] + size[side][0]) * scale / 2 + gap
    iso_s = scale * 0.8
    ix, iy = W - margin - 15 - size["isometric"][0] * iso_s / 2, margin + 15 + size["isometric"][1] * iso_s / 2
    place = {"front": (fx, fy, scale), "top": (fx, ty, scale), side: (sx, fy, scale), "isometric": (ix, iy, iso_s)}
    # views dragged in the drawing editor (projected views stay aligned to the front view like SOLIDWORKS)
    off = {k: tuple(v) for k, v in (state or {}).get("offsets", {}).items()}
    f_off = off.get("front", (0.0, 0.0))
    for name in list(place):
        cx, cy, s = place[name]
        dx, dy = off.get(name, (0.0, 0.0))
        if name == "top":
            dx, dy = f_off[0], dy + f_off[1]
        elif name == side:
            dx, dy = dx + f_off[0], f_off[1]
        elif name == "front":
            dx, dy = f_off
        place[name] = (cx + dx, cy + dy, s)

    def tf(name):
        cx, cy, s = place[name]
        b = boxes[name]
        mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return lambda x, y: (cx + (x - mx) * s, cy - (y - my) * s)

    hidden = set(state.get("hidden_views", []))  # views deleted in the drawing editor
    shown = [n for n in names if n not in hidden]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}" '
           f'font-family="Segoe UI, Arial, sans-serif">',
           f'<rect x="0" y="0" width="{W}" height="{H}" fill="#fff"/>',
           f'<rect x="{margin}" y="{margin}" width="{W - 2 * margin}" height="{H - 2 * margin}" fill="none" stroke="#000" stroke-width="0.5"/>']
    for name in shown:
        t = tf(name)
        vis, hid = views[name]
        cx, cy, s = place[name]
        hw, hh = size[name][0] * s / 2, size[name][1] * s / 2
        out.append(f'<g data-view="{name}"><rect class="view-box" x="{cx - hw - 2:.2f}" y="{cy - hh - 2:.2f}" '
                   f'width="{2 * hw + 4:.2f}" height="{2 * hh + 4:.2f}" fill="none" stroke="none"/>')
        if name == "isometric":
            hid = []  # pictorial views are shown with hidden lines removed
        for poly in hid:
            pts = " ".join(f"{a:.3f},{b:.3f}" for a, b in (t(x, y) for x, y in poly))
            out.append(f'<polyline points="{pts}" fill="none" stroke="#555" stroke-width="0.18" stroke-dasharray="1.6 0.9"/>')
        for poly in vis:
            pts = " ".join(f"{a:.3f},{b:.3f}" for a, b in (t(x, y) for x, y in poly))
            out.append(f'<polyline points="{pts}" fill="none" stroke="#000" stroke-width="0.35" stroke-linejoin="round"/>')
        cx, cy, s = place[name]
        label = {"front": "FRONT", "top": "TOP", "right": "RIGHT", "left": "LEFT", "isometric": "ISOMETRIC"}[name]
        below = 16 if name == "front" else 7  # leave room for the width dimension under the front view
        out.append(f'<text x="{cx:.2f}" y="{cy + size[name][1] * s / 2 + below:.2f}" font-size="3" text-anchor="middle">{label}</text></g>')
    if "front" in shown:
        out += _detail_view(views["front"][0], tf("front"), boxes["front"], scale, W, margin)
    if getattr(doc, "kind", "part") == "assembly":
        out += _bom(doc, margin)
        if "isometric" in shown:
            out += _balloons(doc, tf("isometric"), body)
    if "top" in shown:
        out += _hole_callouts(body, tf("top"), boxes["top"])
        out += _section_view(body, tf("top"), boxes["top"], place, size, scale, W, H, margin, title_h, side)
    # overall dimensions on the front and top views (model units, independent of scale)
    if "front" in shown:
        out += _dim_h(tf("front"), boxes["front"], size["front"][0], below=True)
        out += _dim_v(tf("front"), boxes["front"], size["front"][1])
    if "top" in shown:
        out += _dim_v(tf("top"), boxes["top"], size["top"][1])
    if state.get("model_items") and getattr(doc, "kind", "part") == "part":
        out += _model_items(doc, body, {n: tf(n) for n in shown}, shown)
    out += _annotations(state)
    # title block
    tb_w, x0, y0 = 170, W - margin - 170, H - margin - title_h
    mat = getattr(doc, "material", {}).get("name") or "<not specified>"
    props = {k.lower(): v for k, v in getattr(doc, "properties", {}).items()}
    title = props.get("description") or doc.name
    rows = [("TITLE", escape(title)), ("DWG NO.", escape(props.get("partno") or props.get("part number") or doc.name)),
            ("MATERIAL", escape(str(mat))),
            ("SCALE", _scale_label(scale) + f"   SHEET {sheet}"),
            ("PROJECTION", "THIRD ANGLE" if projection == "third" else "FIRST ANGLE"),
            ("DRAWN", escape(props.get("author") or props.get("drawnby") or "")),
            ("DATE", _dt.date.today().isoformat()), ("UNITS", _UNIT["name"])]
    out.append(f'<rect x="{x0}" y="{y0}" width="{tb_w}" height="{title_h}" fill="none" stroke="#000" stroke-width="0.5"/>')
    rh = title_h / len(rows)
    for i, (k, v) in enumerate(rows):
        y = y0 + rh * i
        if i:
            out.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + tb_w}" y2="{y}" stroke="#000" stroke-width="0.25"/>')
        out.append(f'<text x="{x0 + 2}" y="{y + rh * 0.72:.2f}" font-size="2.6" fill="#555">{k}</text>')
        out.append(f'<text x="{x0 + 34}" y="{y + rh * 0.72:.2f}" font-size="{3.6 if i == 0 else 3}" '
                   f'font-weight="{700 if i == 0 else 400}">{v}</text>')
    out.append(f'<line x1="{x0 + 32}" y1="{y0}" x2="{x0 + 32}" y2="{y0 + title_h}" stroke="#000" stroke-width="0.25"/>')
    out.append("</svg>")
    return {"svg": "\n".join(out), "scale": scale, "scale_label": _scale_label(scale), "sheet": sheet,
            "views": shown, "projection": projection}


GDT_SYMBOLS = {"straightness": "⏤", "flatness": "⏥", "circularity": "○", "cylindricity": "⌭", "profile_line": "⌒",
               "profile_surface": "⌓", "perpendicularity": "⟂", "angularity": "∠", "parallelism": "∥", "position": "⌖",
               "concentricity": "◎", "symmetry": "⌯", "circular_runout": "↗", "total_runout": "⌰"}


def _leader(x, y, lx, ly) -> list[str]:
    ang = math.atan2(y - ly, x - lx)
    return [f'<line x1="{x:.3f}" y1="{y:.3f}" x2="{lx:.3f}" y2="{ly:.3f}" stroke="#000" stroke-width="0.18"/>',
            _arrow(lx, ly, ang)]


def _annotations(state: dict) -> list[str]:
    """Notes, datum feature symbols and GD&T feature control frames placed in sheet millimetres."""
    out = []
    for n in state.get("notes", []):
        size = n.get("size", 3.5)
        for i, line in enumerate(str(n["text"]).split("\n")):
            out.append(f'<text data-ann="{n["id"]}" x="{n["x"]:.2f}" y="{n["y"] + i * size * 1.3:.2f}" font-size="{size}">{escape(line)}</text>')
        if n.get("leader"):
            out += _leader(n["x"], n["y"] - size * 0.35, *n["leader"])
    for d in state.get("datums", []):
        x, y = d["x"], d["y"]
        out += [f'<rect data-ann="{d["id"]}" x="{x - 3:.2f}" y="{y - 3:.2f}" width="6" height="6" fill="#fff" stroke="#000" stroke-width="0.3"/>',
                f'<text x="{x:.2f}" y="{y + 1.3:.2f}" font-size="3.6" text-anchor="middle" font-weight="700">{escape(d["label"])}</text>']
        if d.get("leader"):
            lx, ly = d["leader"]
            out.append(f'<line x1="{x:.3f}" y1="{y + 3:.3f}" x2="{lx:.3f}" y2="{ly:.3f}" stroke="#000" stroke-width="0.25"/>')
            a = math.atan2(ly - y, lx - x) + math.pi / 2
            out.append(f'<path d="M{lx - 1.6 * math.cos(a):.3f},{ly - 1.6 * math.sin(a):.3f} L{lx + 1.6 * math.cos(a):.3f},'
                       f'{ly + 1.6 * math.sin(a):.3f} L{lx + 2.2 * math.cos(a - math.pi / 2):.3f},{ly + 2.2 * math.sin(a - math.pi / 2):.3f} Z" fill="#000"/>')
    for g in state.get("gdt", []):
        cells = [GDT_SYMBOLS.get(g["characteristic"], "?"), ("⌀" if g.get("diameter") else "") + _fmt(g["tolerance"], raw=True)
                 + {"MMC": " Ⓜ", "LMC": " Ⓛ"}.get(g.get("modifier") or "", "")] + list(g.get("datums", []))
        x, y, h = g["x"], g["y"], 6.0
        from .drawing_export import ASCII  # size cells for the PDF fallback text too (e.g. POS, DIA)
        plain = lambda c: "".join(ASCII.get(ch, ch) for ch in c)  # noqa: E731
        widths = [max(7.0, 2.4 * len(plain(cells[0])) + 2.5)] + [max(8.0, 2.2 * len(plain(c)) + 2.5) for c in cells[1:]]
        cx = x
        for c, w in zip(cells, widths):
            out.append(f'<rect data-ann="{g["id"]}" x="{cx:.2f}" y="{y - h / 2:.2f}" width="{w:.2f}" height="{h}" fill="#fff" stroke="#000" stroke-width="0.3"/>')
            out.append(f'<text x="{cx + w / 2:.2f}" y="{y + 1.3:.2f}" font-size="3.6" text-anchor="middle">{escape(c)}</text>')
            cx += w
        if g.get("leader"):
            out += _leader(x, y, *g["leader"])
    return out


def _view_mapper(body, name: str, box):
    """3D point -> 2D view coordinates (same frame as project()): x = up x dir, y = up, aligned via the bbox."""
    d, up = (Vector(*v).normalized() for v in VIEW_DIRS[name])
    xv = up.cross(d)
    bb = body.bounding_box()
    corners = [Vector(x, y, z) for x in (bb.min.X, bb.max.X) for y in (bb.min.Y, bb.max.Y) for z in (bb.min.Z, bb.max.Z)]
    mx, my = min(c.dot(xv) for c in corners), min(c.dot(up) for c in corners)
    return lambda p: (p.dot(xv) - mx + box[0], p.dot(up) - my + box[1])


def _dim_aligned(a, b, text: str, offset: float = 7.0) -> list[str]:
    (x1, y1), (x2, y2) = a, b
    L = math.hypot(x2 - x1, y2 - y1)
    if L < 1e-6:
        return []
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    nx, ny = -uy, ux
    if ny > 0 or (abs(ny) < 1e-9 and nx > 0):  # put the dimension above / left of the feature
        nx, ny = -nx, -ny
    p1, p2 = (x1 + nx * offset, y1 + ny * offset), (x2 + nx * offset, y2 + ny * offset)
    ang = math.degrees(math.atan2(uy, ux))
    if ang > 90 or ang <= -90:
        ang += 180
    mx, my = (p1[0] + p2[0]) / 2 + nx * 1.2, (p1[1] + p2[1]) / 2 + ny * 1.2
    return [f'<line x1="{x1 + nx:.3f}" y1="{y1 + ny:.3f}" x2="{p1[0] + nx * 1.5:.3f}" y2="{p1[1] + ny * 1.5:.3f}" stroke="#000" stroke-width="0.18"/>',
            f'<line x1="{x2 + nx:.3f}" y1="{y2 + ny:.3f}" x2="{p2[0] + nx * 1.5:.3f}" y2="{p2[1] + ny * 1.5:.3f}" stroke="#000" stroke-width="0.18"/>',
            f'<line x1="{p1[0]:.3f}" y1="{p1[1]:.3f}" x2="{p2[0]:.3f}" y2="{p2[1]:.3f}" stroke="#000" stroke-width="0.18"/>',
            _arrow(*p1, math.atan2(-uy, -ux)), _arrow(*p2, math.atan2(uy, ux)),
            f'<text x="{mx:.3f}" y="{my:.3f}" font-size="3.2" text-anchor="middle" '
            f'transform="rotate({ang:.2f} {mx:.3f} {my:.3f})">{escape(text)}</text>']


def _model_items(doc, body, tfs: dict, names: list[str]) -> list[str]:
    """Model Items: driving sketch dimensions shown in the standard view that looks straight at the sketch."""
    import numpy as np
    from .sketch import _Getter, _kind
    out = []
    boxes = {}
    for f in doc.features:
        if f["type"] != "sketch" or f["name"] not in doc.ctx.sketches or f["params"].get("hidden"):
            continue
        sk, plane = doc.ctx.sketches[f["name"]]
        n = plane.z_dir
        view = next((v for v in names if v != "isometric" and abs(abs(n.dot(Vector(*VIEW_DIRS[v][0]).normalized())) - 1) < 1e-6), None)
        if view is None:
            continue
        if view not in boxes:
            vis, hid = project(body, view)
            boxes[view] = _bbox(vis + hid)
        to2 = _view_mapper(body, view, boxes[view])
        t = tfs[view]
        x, idx = sk._vector()
        g = _Getter(sk, x, idx)
        S = lambda q: t(*to2(plane.from_local_coords(Vector(float(q[0]), float(q[1]), 0))))  # noqa: E731
        for d in sk.dimensions:
            if d.get("driven"):
                continue
            refs, typ = d["refs"], d["type"]
            try:
                if typ in ("radius", "diameter"):
                    c = g.center(refs[0]); r = g.r(refs[0])
                    p_on = c + np.array([r * math.cos(math.radians(45)), r * math.sin(math.radians(45))])
                    (ax, ay), (bx, by) = S(c), S(p_on)
                    tx, ty = bx + (bx - ax) * 0.6 + 2, by + (by - ay) * 0.6
                    out += _leader(tx, ty, bx, by)
                    label = ("R" if typ == "radius" else "⌀") + _fmt(d["value"])
                    out.append(f'<text x="{tx + 0.8:.3f}" y="{ty - 0.6:.3f}" font-size="3.2">{label}</text>')
                    continue
                if typ == "angle":
                    continue
                kinds = [_kind(sk, r) for r in refs]
                if typ == "length" or (typ == "distance" and len(refs) == 1):
                    a, b = g.line(refs[0])
                elif typ in ("hdistance", "vdistance"):
                    a, b = (g.center(r) if _kind(sk, r) != "line" else g.line(r)[0] for r in refs[:2])
                    b = np.array([b[0], a[1]]) if typ == "hdistance" else np.array([a[0], b[1]])
                elif "line" in kinds:
                    line, other = (refs[0], refs[1]) if kinds[0] == "line" else (refs[1], refs[0])
                    p = g.line(other)[0] if _kind(sk, other) == "line" else g.center(other)
                    a0, b0 = g.line(line)
                    u = (b0 - a0) / np.linalg.norm(b0 - a0)
                    a, b = a0 + u * float(np.dot(p - a0, u)), p
                else:
                    a, b = g.center(refs[0]), g.center(refs[1])
                out += _dim_aligned(S(a), S(b), _fmt(d["value"]))
            except Exception:  # dimension references something we cannot place: skip it
                continue
    return out


def _detail_view(front_vis, t_front, box, scale, W, margin) -> list[str]:
    """DETAIL B (2:1): magnify the lower-right corner region of the front view."""
    r_model = 0.22 * min(box[2] - box[0], box[3] - box[1]) or 5
    cx_m, cy_m = box[2] - r_model * 0.6, box[1] + r_model * 0.6
    k = 2.0  # detail scale relative to the sheet scale
    s = scale * k
    dx, dy = W / 2, margin + 18 + r_model * s
    (ox, oy) = t_front(cx_m, cy_m)
    out = [f'<circle cx="{ox:.3f}" cy="{oy:.3f}" r="{r_model * scale:.3f}" fill="none" stroke="#000" stroke-width="0.25" stroke-dasharray="4 1.5"/>',
           f'<text x="{ox + r_model * scale + 1:.3f}" y="{oy - r_model * scale:.3f}" font-size="3.4" font-weight="700">B</text>',
           f'<clipPath id="detailB"><circle cx="{dx:.3f}" cy="{dy:.3f}" r="{r_model * s:.3f}"/></clipPath>',
           '<g clip-path="url(#detailB)">']
    for poly in front_vis:
        pts = " ".join(f"{dx + (x - cx_m) * s:.3f},{dy - (y - cy_m) * s:.3f}" for x, y in poly)
        out.append(f'<polyline points="{pts}" fill="none" stroke="#000" stroke-width="0.35"/>')
    out += ["</g>", f'<circle cx="{dx:.3f}" cy="{dy:.3f}" r="{r_model * s:.3f}" fill="none" stroke="#000" stroke-width="0.25"/>',
            f'<text x="{dx:.3f}" y="{dy + r_model * s + 6:.3f}" font-size="3" text-anchor="middle">DETAIL B  SCALE {_scale_label(s)}</text>']
    return out


def _bom(doc, margin) -> list[str]:
    """Bill of materials for assemblies: item, part number, quantity (grouped by source part)."""
    from collections import Counter
    counts = Counter((c.link or c.name.rsplit("-", 1)[0]) for c in doc.components)
    x0, y0, rh, cw = margin + 4, margin + 4, 5.5, (17, 60, 14)
    out = [f'<rect x="{x0}" y="{y0}" width="{sum(cw)}" height="{rh * (len(counts) + 1)}" fill="none" stroke="#000" stroke-width="0.35"/>']
    for i, (label, x) in enumerate(zip(("ITEM NO.", "PART NUMBER", "QTY."), (x0, x0 + cw[0], x0 + cw[0] + cw[1]))):
        out.append(f'<text x="{x + 1.5}" y="{y0 + rh * 0.72}" font-size="2.6" font-weight="700">{label}</text>')
        if i:
            out.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y0 + rh * (len(counts) + 1)}" stroke="#000" stroke-width="0.25"/>')
    for r, (part, q) in enumerate(counts.items(), 1):
        y = y0 + rh * r
        out.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + sum(cw)}" y2="{y}" stroke="#000" stroke-width="0.25"/>')
        for text, x in ((str(r), x0), (escape(part), x0 + cw[0]), (str(q), x0 + cw[0] + cw[1])):
            out.append(f'<text x="{x + 1.5}" y="{y + rh * 0.72}" font-size="2.8">{text}</text>')
    return out


def _balloons(doc, t_iso, body) -> list[str]:
    """Auto Balloons on the isometric view: one numbered balloon per BOM item, pointing at a component."""
    import numpy as np
    d, up = VIEW_DIRS["isometric"]
    dv = np.array(d, float) / np.linalg.norm(d)
    r_ = np.cross(up, dv); r_ /= np.linalg.norm(r_)
    u_ = np.cross(dv, r_)
    c0 = body.bounding_box().center()
    items, out, seen = {}, [], set()
    for c in doc.components:
        key = c.link or c.name.rsplit("-", 1)[0]
        items.setdefault(key, len(items) + 1)
        if key in seen:
            continue
        seen.add(key)
        shape = c.doc(doc.live_docs).body
        if shape is None:
            continue
        p = shape.moved(c.location()).bounding_box().center()
        v = np.array([p.X - c0.X, p.Y - c0.Y, p.Z - c0.Z])
        x, y = t_iso(float(v @ r_), float(v @ u_))
        bx, by = x + 18, y - 14
        out += [f'<line x1="{x:.3f}" y1="{y:.3f}" x2="{bx:.3f}" y2="{by:.3f}" stroke="#000" stroke-width="0.2"/>',
                f'<circle cx="{x:.3f}" cy="{y:.3f}" r="0.6" fill="#000"/>',
                f'<circle cx="{bx:.3f}" cy="{by:.3f}" r="3.6" fill="#fff" stroke="#000" stroke-width="0.3"/>',
                f'<text x="{bx:.3f}" y="{by + 1.2:.3f}" font-size="3.4" text-anchor="middle">{items[key]}</text>']
    return out


def _hole_callouts(body, t_top, box) -> list[str]:
    """Ø callouts for holes seen in the top view (circular edges whose axis is vertical), one per hole."""
    out, seen = [], set()
    for e in body.edges():
        if e.geom_type.name.lower() != "circle" or abs(abs(e.normal().Y) - 1) > 1e-6:
            continue
        c = e.arc_center
        key = (round(c.X, 3), round(c.Z, 3))
        if key in seen:
            continue
        # only internal circles (holes/bosses inside the outline) get a callout, the largest at that centre
        same = [x for x in body.edges() if x.geom_type.name.lower() == "circle" and abs(abs(x.normal().Y) - 1) < 1e-6
                and (round(x.arc_center.X, 3), round(x.arc_center.Z, 3)) == key]
        r = min(x.radius for x in same)
        seen.add(key)
        cx, cy = t_top(c.X, -c.Z)
        ex, ey = t_top(c.X + r * 0.7071, -c.Z + r * 0.7071)
        out.append(f'<line x1="{ex:.3f}" y1="{ey:.3f}" x2="{ex + 7:.3f}" y2="{ey - 7:.3f}" stroke="#000" stroke-width="0.18"/>')
        out.append(f'<line x1="{ex + 7:.3f}" y1="{ey - 7:.3f}" x2="{ex + 14:.3f}" y2="{ey - 7:.3f}" stroke="#000" stroke-width="0.18"/>')
        out.append(_arrow(ex, ey, math.atan2(ey - (ey - 7), ex - (ex + 7))))
        out.append(f'<text x="{ex + 8:.3f}" y="{ey - 8:.3f}" font-size="3">Ø{_fmt(2 * r)}</text>')
    return out


def _section_view(body, t_top, top_box, place, size, scale, W, H, margin, title_h, side) -> list[str]:
    """Section A-A: cut at the mid Z plane, viewed from the front (+Z), cut faces hatched."""
    from build123d import Keep, Plane
    bb = body.bounding_box()
    zc = (bb.min.Z + bb.max.Z) / 2
    try:
        cut = body.split(Plane(origin=(0, 0, zc), z_dir=(0, 0, 1)), Keep.BOTTOM)
    except Exception:
        return []
    shapes = cut if isinstance(cut, (list, tuple)) else [cut]
    from build123d import Compound
    solids = [s for sh in shapes if sh is not None for s in sh.solids()]
    if not solids:
        return []
    half = Compound(solids)
    vis, _hid = project(half, "front")
    b = _bbox(vis)
    w, h = b[2] - b[0], b[3] - b[1]
    sx_side, sy = place[side][0], place[side][1]
    cx = sx_side + (size[side][0] + w) * scale / 2 + 22
    cy = sy
    if cx + w * scale / 2 > W - margin - 4:  # no room to the right: put it above the side view
        cx, cy = sx_side, sy - (size[side][1] + h) * scale / 2 - 30
    mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2

    def T(x, y):
        return cx + (x - mx) * scale, cy - (y - my) * scale
    out = ['<defs><pattern id="hatch" width="2" height="2" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           '<line x1="0" y1="0" x2="0" y2="2" stroke="#000" stroke-width="0.18"/></pattern></defs>']
    hc = half.bounding_box().center()  # projected views are relative to the shape's centre
    for f in half.faces():  # cut faces lie on the section plane
        if f.geom_type.name.lower() == "plane" and abs(abs(f.normal_at().Z) - 1) < 1e-6 and abs(f.center().Z - zc) < 1e-6:
            for w_ in [f.outer_wire()] + list(f.inner_wires()):
                pts = [T(p[0] - hc.X, p[1] - hc.Y) for p in _wire_pts(w_)]
                d = "M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts) + " Z"
                out.append(f'<path d="{d}" fill="url(#hatch)" stroke="none" fill-rule="evenodd"/>')
    for poly in vis:
        pts = " ".join(f"{a:.3f},{b_:.3f}" for a, b_ in (T(x, y) for x, y in poly))
        out.append(f'<polyline points="{pts}" fill="none" stroke="#000" stroke-width="0.35"/>')
    out.append(f'<text x="{cx:.2f}" y="{cy + h * scale / 2 + 7:.2f}" font-size="3" text-anchor="middle">SECTION A-A</text>')
    # cutting plane line on the top view (top view y = -Z)
    (x1, y1), (x2, _) = t_top(top_box[0] - 4, -zc), t_top(top_box[2] + 4, -zc)
    out.append(f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y1:.3f}" stroke="#000" stroke-width="0.35" '
               f'stroke-dasharray="6 1.5 1 1.5"/>')
    for x in (x1, x2):  # view arrows point towards the back (up the sheet in the top view)
        out.append(f'<line x1="{x:.3f}" y1="{y1:.3f}" x2="{x:.3f}" y2="{y1 - 5:.3f}" stroke="#000" stroke-width="0.35"/>')
        out.append(_arrow(x, y1 - 5, -math.pi / 2))
        out.append(f'<text x="{x:.3f}" y="{y1 - 7:.3f}" font-size="3.4" text-anchor="middle" font-weight="700">A</text>')
    return out


def _wire_pts(wire):
    pts = []
    for e in wire.edges():
        n = 2 if e.geom_type.name.lower() == "line" else 32
        for i in range(n):
            p = e.position_at(i / (n - 1))
            pts.append((p.X, p.Y))
    return pts


def _arrow(x, y, ang):
    import math
    a1, a2 = ang + 2.7, ang - 2.7
    return (f'<path d="M{x:.3f},{y:.3f} L{x + 2.2 * math.cos(a1):.3f},{y + 2.2 * math.sin(a1):.3f} '
            f'L{x + 2.2 * math.cos(a2):.3f},{y + 2.2 * math.sin(a2):.3f} Z" fill="#000"/>')


_UNIT = {"f": 1.0, "name": "mm"}  # document units for dimension text (set per make_drawing call)
UNIT_FACTORS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}


def _fmt(v: float, raw: bool = False) -> str:
    if not raw:
        v = v / _UNIT["f"]
        if _UNIT["name"] in ("in", "ft"):
            return f"{v:.3f}".rstrip("0").rstrip(".")
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _dim_h(t, box, value, below=True):
    import math
    (x1, y1), (x2, _) = t(box[0], box[1]), t(box[2], box[1])
    y = y1 + 8
    return [f'<line x1="{x1:.3f}" y1="{y1 + 1:.3f}" x2="{x1:.3f}" y2="{y + 1.5:.3f}" stroke="#000" stroke-width="0.18"/>',
            f'<line x1="{x2:.3f}" y1="{y1 + 1:.3f}" x2="{x2:.3f}" y2="{y + 1.5:.3f}" stroke="#000" stroke-width="0.18"/>',
            f'<line x1="{x1:.3f}" y1="{y:.3f}" x2="{x2:.3f}" y2="{y:.3f}" stroke="#000" stroke-width="0.18"/>',
            _arrow(x1, y, math.pi), _arrow(x2, y, 0),
            f'<text x="{(x1 + x2) / 2:.3f}" y="{y - 1.2:.3f}" font-size="3.2" text-anchor="middle">{_fmt(value)}</text>']


def _dim_v(t, box, value):
    import math
    (x1, y1), (_, y2) = t(box[0], box[1]), t(box[0], box[3])
    x = x1 - 8
    ym = (y1 + y2) / 2
    return [f'<line x1="{x1 - 1:.3f}" y1="{y1:.3f}" x2="{x - 1.5:.3f}" y2="{y1:.3f}" stroke="#000" stroke-width="0.18"/>',
            f'<line x1="{x1 - 1:.3f}" y1="{y2:.3f}" x2="{x - 1.5:.3f}" y2="{y2:.3f}" stroke="#000" stroke-width="0.18"/>',
            f'<line x1="{x:.3f}" y1="{y1:.3f}" x2="{x:.3f}" y2="{y2:.3f}" stroke="#000" stroke-width="0.18"/>',
            _arrow(x, y1, math.pi / 2), _arrow(x, y2, -math.pi / 2),
            f'<text x="{x - 1.2:.3f}" y="{ym:.3f}" font-size="3.2" text-anchor="middle" '
            f'transform="rotate(-90 {x - 1.2:.3f} {ym:.3f})">{_fmt(value)}</text>']
