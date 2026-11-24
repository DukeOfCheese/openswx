"""Dimensions on drawing views: values, display text, matching and their SVG leaders."""
from __future__ import annotations

import math
import re
from xml.sax.saxutils import escape

import numpy as np
from build123d import Vector

from openswx.drawing import drawing as D
from openswx.drawing.drawing_sheets.helpers import _corners, _fit_circle, to2d, view_scale
from openswx.drawing.drawing_sheets.views import line_gap, lines_angle, pick_line, snap_vertex


def dim_text(d: dict, base: str, value: float | None = None) -> str:
    """What a dimension shows: its override text, else the value (`base`) with the dimension's tolerance."""
    if d.get("text"):
        return d["text"]
    if not d.get("tol"):
        return base
    from openswx.analysis import tolerance
    try:
        return tolerance.text(base, dim_value(d) if value is None else value, d["tol"])
    except tolerance.ToleranceError:
        return base


def dim_value(d: dict) -> float:
    a, b = d.get("a"), d.get("b")
    k = d["kind"]
    if k == "ordinate":  # the distance from the set's zero, along its axis
        i = 0 if d.get("axis", "x") == "x" else 1
        return abs(float(b[i]) - float(a[i]))
    if k == "angle":
        res = lines_angle(d["l1"], d["l2"], d["at"])
        return res[3] if res else 0.0
    if k == "line_distance":
        return line_gap(d["l1"], d["l2"])
    if k == "radius":
        return float(d["r"])
    if k in ("diameter", "callout"):
        return 2 * float(d["r"])
    if k == "horizontal":
        return abs(b[0] - a[0])
    if k == "vertical":
        return abs(b[1] - a[1])
    return math.hypot(b[0] - a[0], b[1] - a[1])


def parse_dim_text(text: str):
    """A dimension's display text - 'Ø9', '2X ⌀16', 'R5', '95' (SOLIDWORKS' <MOD-DIAM> and other tags allowed) -
    as {"kind": "diameter" / "radius" / "linear", "value", "decimals", "prefix", "text": cleaned}; None when it
    holds no number."""
    s = text.replace("<MOD-DIAM>", "⌀").replace("Ø", "⌀")
    s = re.sub(r"<[^>]*>", "", s).strip()
    m = re.search(r"(⌀|R)?\s*(\d+(?:\.\d*)?|\.\d+)", s)
    if not m:
        return None
    num = m.group(2)
    return {"kind": {"⌀": "diameter", "R": "radius"}.get(m.group(1) or "", "linear"), "value": float(num),
            "decimals": len(num.split(".")[1]) if "." in num else 0, "prefix": s[:m.start()].strip(), "text": s}


def match_dimension(g: dict, kind: str, value: float, tol: float) -> dict | None:
    """The view geometry a dimension of this value measures (view coordinates): a circle (diameter), an arc
    (radius), else two vertices a horizontal or vertical distance apart - a straight edge of that length
    first, the pair along the view's outline (top for horizontal, left for vertical) before inner ones."""
    if kind in ("diameter", "radius"):
        circles = [c for c in (_fit_circle(p) for p in g["vis"] + g["hid"]) if c]
        want = value / 2 if kind == "diameter" else value
        best = min(circles, key=lambda c: abs(c[1] - want), default=None)
        if best is None or abs(best[1] - want) > tol / (2 if kind == "diameter" else 1):
            return None
        return {"kind": kind, "c": list(best[0]), "r": best[1], "angle": 45.0}
    verts = []
    for q in g.get("verts") or _corners(g["vis"] + g["hid"]):
        if not any(abs(q[0] - o[0]) < 1e-6 and abs(q[1] - o[1]) < 1e-6 for o in verts):
            verts.append(q)
    edges = {(tuple(p[0]), tuple(p[-1])) for p in g["vis"] + g["hid"] if len(p) == 2}
    x0, y0, x1, y1 = g["box"]
    span = max(x1 - x0, y1 - y0, 1e-9)
    best, score = None, None
    for i, a in enumerate(verts):
        for b in verts[i + 1:]:
            for axis, kd in ((0, "horizontal"), (1, "vertical")):
                if abs(abs(b[axis] - a[axis]) - value) > tol:
                    continue
                along = abs(b[1 - axis] - a[1 - axis])  # off the measured direction
                edge = along < 1e-6 and ((tuple(a), tuple(b)) in edges or (tuple(b), tuple(a)) in edges)
                rim = (y1 - max(a[1], b[1])) if axis == 0 else (min(a[0], b[0]) - x0)
                sc = (not edge, along > 1e-6, abs(abs(b[axis] - a[axis]) - value), rim / span, along / span)
                if score is None or sc < score:
                    lo, hi = (a, b) if a[axis] <= b[axis] else (b, a)
                    best, score = {"kind": kd, "a": list(lo), "b": list(hi)}, sc
    return best


def _render_dims(st, sheet, v, g, t) -> list[str]:
    out = []
    span = max(g["box"][2] - g["box"][0], g["box"][3] - g["box"][1], 1e-6)
    for d in sheet.get("dims", []):
        if d["view"] != v["id"]:
            continue
        if d["kind"] in ("radius", "diameter", "callout"):  # follow the model: the nearest circle to where it was
            # (of concentric circles, the one of its size - not the innermost)
            near = [c for c in (_fit_circle(p) for p in g["vis"] + g["hid"]) if c]
            c = min(near, key=lambda c: math.dist(c[0], d["c"]) + abs(c[1] - d["r"]), default=None)
            if c and math.dist(c[0], d["c"]) + abs(c[1] - d["r"]) < 0.05 * span + d["r"]:
                d["c"], d["r"] = list(c[0]), c[1]
            (cx, cy), r = t(*d["c"]), d["r"] * view_scale(st, v)
            ang = math.radians(d.get("angle", 45.0))
            ex, ey = cx + r * math.cos(ang), cy - r * math.sin(ang)
            tx, ty = cx + (r + d.get("offset", 8.0)) * math.cos(ang), cy - (r + d.get("offset", 8.0)) * math.sin(ang)
            out += D._leader(tx, ty, ex, ey)
            if d["kind"] == "diameter":  # the leader runs across the circle
                out.append(f'<line x1="{ex:.3f}" y1="{ey:.3f}" x2="{2 * cx - ex:.3f}" y2="{2 * cy - ey:.3f}" stroke="#000" stroke-width="0.18"/>')
            if d["kind"] == "callout":  # Hole Callout: the hole's size and depth, a line per item
                lines = (d.get("text") or d.get("callout") or "⌀" + D._fmt(dim_value(d))).split("\n")
            else:
                lines = [dim_text(d, ("⌀" if d["kind"] == "diameter" else "R") + D._fmt(dim_value(d)))]
            anchor = "start" if math.cos(ang) >= 0 else "end"
            for i, line in enumerate(lines):
                out.append(f'<text data-dim="{escape(d["id"])}" x="{tx + (1 if math.cos(ang) >= 0 else -1):.3f}" y="{ty - 0.8 + 4.0 * i:.3f}" '
                           f'font-size="3.2" text-anchor="{anchor}">{escape(line)}</text>')
            continue
        if d["kind"] == "ordinate":
            out += _render_ordinate(d, t)
            continue
        if d["kind"] in ("angle", "line_distance"):
            for k in ("l1", "l2"):  # follow the model: the nearest straight edge to where the line was
                mid = ((d[k][0][0] + d[k][1][0]) / 2, (d[k][0][1] + d[k][1][1]) / 2)
                q = pick_line(g, mid, 0.02 * span)
                if q:
                    d[k] = [list(q[0]), list(q[1])]
            out += _render_angle(st, v, d, t) if d["kind"] == "angle" else _render_line_gap(d, t)
            continue
        for k in ("a", "b"):  # follow the model: the nearest vertex to where the end was
            q = snap_vertex(g, d[k], 0.02 * span)
            if q:
                d[k] = list(q)
        a, b = d["a"], d["b"]
        if d["kind"] == "horizontal":
            b2 = (b[0], a[1])
            pa, pb = t(*a), t(*b2)
            ext = (t(*b), pb)
        elif d["kind"] == "vertical":
            b2 = (a[0], b[1])
            pa, pb = t(*a), t(*b2)
            ext = (t(*b), pb)
        else:
            pa, pb, ext = t(*a), t(*b), None
        text = dim_text(d, D._fmt(dim_value(d)))
        seg = D._dim_aligned(pa, pb, text, offset=float(d.get("offset", 8.0)))
        if seg:
            seg[-1] = seg[-1].replace("<text ", f'<text data-dim="{escape(d["id"])}" ', 1)
        if ext and ext[0] != ext[1]:  # the measured point off the dimension's base line
            out.append(f'<line x1="{ext[0][0]:.3f}" y1="{ext[0][1]:.3f}" x2="{ext[1][0]:.3f}" y2="{ext[1][1]:.3f}" '
                       f'stroke="#000" stroke-width="0.13" stroke-dasharray="0.8 0.8"/>')
        out += seg
    return out


def _render_angle(st, v, d, t) -> list[str]:
    """Angular dimension: an arc about the lines' crossing through the placement point, arrows at both ends,
    extension lines where the arc lies beyond an edge."""
    res = lines_angle(d["l1"], d["l2"], d["at"])
    if res is None:
        return []
    apex, u1, u2, deg = res
    P = np.array(t(*apex))
    R = max(3.0, float(np.linalg.norm(np.array(t(*d["at"])) - P)))
    s = view_scale(st, v)
    dirs = [np.array([u[0], -u[1]]) for u in (u1, u2)]  # sheet y runs down
    th1, th2 = (math.atan2(q[1], q[0]) for q in dirs)
    delta = (th2 - th1 + math.pi) % (2 * math.pi) - math.pi
    e1, e2 = P + R * dirs[0], P + R * dirs[1]
    out = [f'<path d="M{e1[0]:.3f},{e1[1]:.3f} A{R:.3f},{R:.3f} 0 0 {1 if delta > 0 else 0} {e2[0]:.3f},{e2[1]:.3f}" '
           f'fill="none" stroke="#000" stroke-width="0.18"/>']
    sgn = 1.0 if delta > 0 else -1.0
    out.append(D._arrow(*e1, math.atan2(-math.cos(th1) * sgn, math.sin(th1) * sgn)))  # pointing back along the arc
    out.append(D._arrow(*e2, math.atan2(math.cos(th2) * sgn, -math.sin(th2) * sgn)))
    for line, u, q in ((d["l1"], u1, dirs[0]), (d["l2"], u2, dirs[1])):  # extension lines beyond the edges
        reach = max(float((np.asarray(p, float) - apex) @ u) for p in line) * s
        if R > reach + 0.5:
            a0, a1 = P + q * (reach + 1.0), P + q * (R + 1.5)
            out.append(f'<line x1="{a0[0]:.3f}" y1="{a0[1]:.3f}" x2="{a1[0]:.3f}" y2="{a1[1]:.3f}" stroke="#000" stroke-width="0.13"/>')
    tm = th1 + delta / 2
    tx, ty = P[0] + (R + 2.5) * math.cos(tm), P[1] + (R + 2.5) * math.sin(tm)
    text = dim_text(d, f"{D._fmt(deg)}°", deg)
    out.append(f'<text data-dim="{escape(d["id"])}" x="{tx:.3f}" y="{ty + 1.1:.3f}" font-size="3.2" text-anchor="middle">{escape(text)}</text>')
    return out


def _render_ordinate(d, t) -> list[str]:
    """Ordinate dimension: an extension line from the point out to the set's common line, with the distance
    from the zero point written at its end (no dimension line or arrows)."""
    (ax, ay), (bx, by) = t(*d["a"]), t(*d["b"])
    off = float(d.get("offset", 10.0))
    text = dim_text(d, D._fmt(dim_value(d)))
    if d.get("axis", "x") == "x":  # values along the view's x: extension lines run up / down the sheet
        ey = ay + off
        gap = 1.0 if ey > by else -1.0
        return [f'<line x1="{bx:.3f}" y1="{by + gap:.3f}" x2="{bx:.3f}" y2="{ey:.3f}" stroke="#000" stroke-width="0.13"/>',
                f'<text data-dim="{escape(d["id"])}" x="{bx:.3f}" y="{ey + (4.0 if off > 0 else -1.2):.3f}" font-size="3.2" '
                f'text-anchor="middle">{escape(text)}</text>']
    ex = ax - off
    gap = -1.0 if ex < bx else 1.0
    return [f'<line x1="{bx + gap:.3f}" y1="{by:.3f}" x2="{ex:.3f}" y2="{by:.3f}" stroke="#000" stroke-width="0.13"/>',
            f'<text data-dim="{escape(d["id"])}" x="{ex + (-1.2 if off > 0 else 1.2):.3f}" y="{by + 1.1:.3f}" font-size="3.2" '
            f'text-anchor="{"end" if off > 0 else "start"}">{escape(text)}</text>']


def _render_line_gap(d, t) -> list[str]:
    """Distance between parallel edges: dimensioned square to them, from the first edge's middle."""
    p1 = np.asarray(d["l1"][0], float)
    m1 = (p1 + np.asarray(d["l1"][1], float)) / 2
    p2 = np.asarray(d["l2"][0], float)
    u = np.asarray(d["l2"][1], float) - p2
    u = u / np.linalg.norm(u)
    m2 = p2 + u * float((m1 - p2) @ u)
    seg = D._dim_aligned(t(*m1), t(*m2), dim_text(d, D._fmt(dim_value(d))), offset=float(d.get("offset", 0.0)))
    if seg:
        seg[-1] = seg[-1].replace("<text ", f'<text data-dim="{escape(d["id"])}" ', 1)
    return seg


def _model_dims(doc, fr, t) -> list[str]:
    """The model's driving sketch dimensions, in views that look straight at the sketch."""
    from openswx.core.sketch import _Getter, _kind
    out = []
    d = Vector(*fr[0])
    for f in doc.features:
        if f["type"] != "sketch" or f["name"] not in doc.ctx.sketches or f["params"].get("hidden"):
            continue
        sk, plane = doc.ctx.sketches[f["name"]]
        if abs(abs(plane.z_dir.dot(d)) - 1) > 1e-6:
            continue
        x, idx = sk._vector()
        g = _Getter(sk, x, idx)

        def S(q, plane=plane):
            p = plane.from_local_coords(Vector(float(q[0]), float(q[1]), 0))
            return t(*to2d(tuple(p), fr))
        for dim in sk.dimensions:
            if dim.get("driven"):
                continue
            refs, typ = dim["refs"], dim["type"]
            try:
                if typ in ("radius", "diameter"):
                    c = g.center(refs[0])
                    r = g.r(refs[0])
                    p_on = c + np.array([r * math.cos(math.radians(45)), r * math.sin(math.radians(45))])
                    (ax, ay), (bx, by) = S(c), S(p_on)
                    tx, ty = bx + (bx - ax) * 0.6 + 2, by + (by - ay) * 0.6
                    out += D._leader(tx, ty, bx, by)
                    lab = ("R" if typ == "radius" else "⌀") + D._fmt(dim["value"])
                    out.append(f'<text x="{tx + 0.8:.3f}" y="{ty - 0.6:.3f}" font-size="3.2">{lab}</text>')
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
                out += D._dim_aligned(S(a), S(b), D._fmt(dim["value"]))
            except Exception:  # a dimension we cannot place in this view
                continue
    return out
