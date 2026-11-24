"""Balloons on drawing views: the BOM item number in a circle with a leader to the component."""
from __future__ import annotations

import math
from xml.sax.saxutils import escape

from openswx.drawing.drawing_sheets.tables import item_numbers


def _render_balloons(doc, sheet, v, t) -> list[str]:
    """Balloons: the BOM item number in a circle, a leader to a dot on the component."""
    items = item_numbers(doc, sheet)
    out = []
    for b in sheet.get("balloons", []):
        if b["view"] != v["id"]:
            continue
        text = b.get("text") or str(items.get(b.get("component")) or "?")
        bx, by = float(b["x"]), float(b["y"])
        px, py = t(*b["p"])
        L = math.hypot(px - bx, py - by)
        r = 4.0
        out.append(f'<g data-ann="{escape(b["id"])}">')
        if L > r:
            sx, sy = bx + (px - bx) / L * r, by + (py - by) / L * r
            out.append(f'<line x1="{sx:.3f}" y1="{sy:.3f}" x2="{px:.3f}" y2="{py:.3f}" stroke="#000" stroke-width="0.18"/>')
            out.append(f'<circle cx="{px:.3f}" cy="{py:.3f}" r="0.6" fill="#000"/>')
        out.append(f'<circle cx="{bx:.3f}" cy="{by:.3f}" r="{r}" fill="#fff" stroke="#000" stroke-width="0.25"/>')
        out.append(f'<text x="{bx:.3f}" y="{by + 1.3:.3f}" font-size="3.6" text-anchor="middle">{escape(text)}</text>')
        if b.get("stack"):  # a stacked balloon: the other items touch the first
            from openswx.drawing.drawing_more import stacked_svg
            out += stacked_svg(b, {c: str(n) for c, n in items.items()})
            out.append("</g>")
        else:
            out[-1] += "</g>"
    return out
