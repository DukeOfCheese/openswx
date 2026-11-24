"""Drawing annotations and sheet tools beyond the basics: surface finish and weld symbols, stacked balloons,
cosmetic threads in views, alternate-position (phantom) views, layers, and sheet formats (title-block
templates). drawing.py / drawing_sheets.py call in here when they render a sheet."""
from __future__ import annotations

import json
import math
import os
import re
from html import escape
from pathlib import Path

import numpy as np

PHANTOM = 'stroke="#000" stroke-width="0.18" stroke-dasharray="6 1.2 1.2 1.2 1.2 1.2" fill="none"'
THIN = 'stroke="#000" stroke-width="0.25"'


def _layered(root: dict, item: dict, chunks: list[str]) -> list[str]:
    """An annotation's SVG on its layer: hidden layers drop it, coloured layers recolour it."""
    lay = (root.get("layers") or {}).get(item.get("layer") or "")
    if not lay:
        return chunks
    if not lay.get("visible", True):
        return []
    col = lay.get("color")
    if not col:
        return chunks
    return [c.replace('"#000"', f'"{col}"').replace('"#555"', f'"{col}"') for c in chunks]


def annotations(root: dict, holder: dict) -> list[str]:
    """Every annotation of a sheet (or of the automatic drawing): notes, datums, GD&T frames, surface finish
    and weld symbols, each on its layer."""
    from openswx.drawing import drawing as D
    out = []
    for kind in ("notes", "datums", "gdt"):
        for it in holder.get(kind, []):
            out += _layered(root, it, D._annotations({kind: [it]}))
    for it in holder.get("finishes", []):
        out += _layered(root, it, surface_finish_svg(it))
    for it in holder.get("welds", []):
        out += _layered(root, it, weld_svg(it))
    return out


def layered_dims(root: dict, sheet: dict, v: dict, render) -> list[str]:
    """A view's dimensions, each on its layer (render({"dims": [d]}) draws one)."""
    out = []
    for d in sheet.get("dims", []):
        if d["view"] == v["id"]:
            out += _layered(root, d, render({"dims": [d]}))
    return out


def surface_finish_svg(f: dict) -> list[str]:
    """The surface texture symbol with its tip at (x, y): a short and a long leg at 60 degrees; `process`
    any (open), required (material removal: a bar) or prohibited (a circle); Ra above the extension line;
    the lay symbol beside it; an optional leader from the tip."""
    x, y, h = float(f["x"]), float(f["y"]), 3.5
    k = 1 / math.tan(math.radians(60))
    e1, e2 = (x - h * k, y - h), (x + 2 * h * k, y - 2 * h)
    out = [f'<g data-ann="{escape(f["id"])}"><polyline points="{e1[0]:.3f},{e1[1]:.3f} {x:.3f},{y:.3f} {e2[0]:.3f},{e2[1]:.3f}" fill="none" {THIN}/>']
    proc = f.get("process", "any")
    if proc == "required":
        out.append(f'<line x1="{e1[0]:.3f}" y1="{e1[1]:.3f}" x2="{x + h * k:.3f}" y2="{e1[1]:.3f}" {THIN}/>')
    elif proc == "prohibited":
        out.append(f'<circle cx="{x:.3f}" cy="{y - h * 0.62:.3f}" r="{h * 0.36:.3f}" fill="none" {THIN}/>')
    text = f.get("ra")
    if text not in (None, ""):
        label = str(text) if str(text).lower().startswith(("ra", "rz")) else f"Ra {text}"
        out.append(f'<line x1="{e2[0]:.3f}" y1="{e2[1]:.3f}" x2="{e2[0] + 3 + 1.9 * len(label):.3f}" y2="{e2[1]:.3f}" {THIN}/>')
        out.append(f'<text x="{e2[0] + 1:.3f}" y="{e2[1] - 0.8:.3f}" font-size="2.6">{escape(label)}</text>')
    if f.get("lay"):
        out.append(f'<text x="{x + h * 1.1:.3f}" y="{y - 0.3:.3f}" font-size="2.6">{escape(str(f["lay"]))}</text>')
    if f.get("leader"):
        from openswx.drawing.drawing import _leader
        out += _leader(x, y, *f["leader"])
    out.append("</g>")
    return out


WELD_TYPES = ("fillet", "v_groove", "square", "bevel", "plug", "spot", "u_groove", "j_groove")


def _weld_glyph(kind: str, sx: float, y: float, sgn: float) -> list[str]:
    """The weld's basic symbol on the reference line at sx; sgn +1 draws it below (arrow side), -1 above."""
    s = 3.0
    if kind == "fillet":
        return [f'<polygon points="{sx:.3f},{y:.3f} {sx:.3f},{y + sgn * s:.3f} {sx + s:.3f},{y:.3f}" fill="none" {THIN}/>']
    if kind == "v_groove":
        return [f'<polyline points="{sx - 1.5:.3f},{y + sgn * s:.3f} {sx:.3f},{y:.3f} {sx + 1.5:.3f},{y + sgn * s:.3f}" fill="none" {THIN}/>']
    if kind == "square":
        return [f'<line x1="{sx - 0.8:.3f}" y1="{y:.3f}" x2="{sx - 0.8:.3f}" y2="{y + sgn * s:.3f}" {THIN}/>',
                f'<line x1="{sx + 0.8:.3f}" y1="{y:.3f}" x2="{sx + 0.8:.3f}" y2="{y + sgn * s:.3f}" {THIN}/>']
    if kind == "bevel":
        return [f'<line x1="{sx:.3f}" y1="{y:.3f}" x2="{sx:.3f}" y2="{y + sgn * s:.3f}" {THIN}/>',
                f'<line x1="{sx:.3f}" y1="{y:.3f}" x2="{sx + 1.8:.3f}" y2="{y + sgn * s:.3f}" {THIN}/>']
    if kind == "plug":
        top = y if sgn > 0 else y - 2
        return [f'<rect x="{sx - 1.5:.3f}" y="{top:.3f}" width="3" height="2" fill="none" {THIN}/>']
    if kind == "spot":
        return [f'<circle cx="{sx:.3f}" cy="{y:.3f}" r="1.5" fill="none" {THIN}/>']
    if kind == "u_groove":
        sweep = 1 if sgn > 0 else 0
        return [f'<path d="M{sx - 1.5:.3f},{y + sgn * s:.3f} L{sx - 1:.3f},{y + sgn:.3f} A1,1 0 0 {sweep} {sx + 1:.3f},{y + sgn:.3f} '
                f'L{sx + 1.5:.3f},{y + sgn * s:.3f}" fill="none" {THIN}/>']
    if kind == "j_groove":
        sweep = 0 if sgn > 0 else 1
        return [f'<path d="M{sx:.3f},{y:.3f} L{sx:.3f},{y + sgn * s:.3f} M{sx:.3f},{y + sgn:.3f} A1.5,1.5 0 0 {sweep} {sx + 1.5:.3f},{y + sgn * s:.3f}" '
                f'fill="none" {THIN}/>']
    raise ValueError(f"unknown weld type {kind!r}")


def weld_svg(w: dict) -> list[str]:
    """A weld symbol: reference line from (x, y), arrow to the joint (leader), the weld's symbol below the line
    (arrow side), above it (other side) or both; size left of the symbol, length right; all-around circle and
    field-weld flag at the junction; process in the tail."""
    x, y = float(w["x"]), float(w["y"])
    L = 24.0
    out = [f'<g data-ann="{escape(w["id"])}"><line x1="{x:.3f}" y1="{y:.3f}" x2="{x + L:.3f}" y2="{y:.3f}" {THIN}/>']
    if w.get("leader"):
        from openswx.drawing.drawing import _arrow
        lx, ly = (float(v) for v in w["leader"])
        out.append(f'<line x1="{x:.3f}" y1="{y:.3f}" x2="{lx:.3f}" y2="{ly:.3f}" {THIN}/>')
        out.append(_arrow(lx, ly, math.atan2(ly - y, lx - x)))
    sx = x + 9
    side = w.get("side", "arrow")
    for sgn in ((1.0,) if side == "arrow" else (-1.0,) if side == "other" else (1.0, -1.0)):
        out += _weld_glyph(w.get("type", "fillet"), sx, y, sgn)
        ty = y + 3.2 if sgn > 0 else y - 1.0
        if w.get("size") not in (None, ""):
            out.append(f'<text x="{sx - 1.2:.3f}" y="{ty:.3f}" font-size="2.6" text-anchor="end">{escape(str(w["size"]))}</text>')
        if w.get("length") not in (None, ""):
            out.append(f'<text x="{sx + 4.2:.3f}" y="{ty:.3f}" font-size="2.6">{escape(str(w["length"]))}</text>')
    if w.get("all_around"):
        out.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="1.4" fill="none" {THIN}/>')
    if w.get("field"):
        out.append(f'<line x1="{x:.3f}" y1="{y:.3f}" x2="{x:.3f}" y2="{y - 5:.3f}" {THIN}/>')
        out.append(f'<polygon points="{x:.3f},{y - 5:.3f} {x + 3:.3f},{y - 4.2:.3f} {x:.3f},{y - 3.4:.3f}" fill="#000" stroke="none"/>')
    if w.get("process"):
        out.append(f'<polyline points="{x + L + 2.5:.3f},{y - 2:.3f} {x + L:.3f},{y:.3f} {x + L + 2.5:.3f},{y + 2:.3f}" fill="none" {THIN}/>')
        out.append(f'<text x="{x + L + 3.2:.3f}" y="{y + 0.9:.3f}" font-size="2.6">{escape(str(w["process"]))}</text>')
    out.append("</g>")
    return out


def stacked_svg(b: dict, items: dict) -> list[str]:
    """The extra balloons of a stack, touching the first one downwards or to the right."""
    out, r = [], 4.0
    bx, by = float(b["x"]), float(b["y"])
    for k, it in enumerate(b.get("stack", []), 1):
        cx, cy = (bx, by + 2 * r * k) if b.get("direction", "down") == "down" else (bx + 2 * r * k, by)
        text = str(items.get(it) or it)
        out.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r}" fill="#fff" stroke="#000" stroke-width="0.25"/>')
        out.append(f'<text x="{cx:.3f}" y="{cy + 1.3:.3f}" font-size="3.6" text-anchor="middle">{escape(text)}</text>')
    return out


def thread_marks(doc, st: dict, v: dict, t, frame, to2d, scale: float) -> list[str]:
    """Cosmetic threads drawn as in ISO 6410: seen end-on, a thin three-quarter circle at the thread diameter;
    seen side-on, thin lines along the thread (hidden lines for internal threads)."""
    infos = [i["cosmetic_thread"] for i in getattr(getattr(doc, "ctx", None), "info", {}).values()
             if isinstance(i, dict) and "cosmetic_thread" in i]
    if not infos:
        return []
    fr = frame(st, v)
    d = np.asarray(fr[0], float)
    out = []
    for th in infos:
        o, a = np.asarray(th["axis_origin"], float), np.asarray(th["axis"], float)
        r = th["thread_diameter"] / 2
        if abs(abs(float(a @ d)) - 1) < 1e-6:  # end-on
            cx, cy = t(*to2d(tuple(o), fr))
            rs = r * scale
            out.append(f'<path d="M{cx + rs:.3f},{cy:.3f} A{rs:.3f},{rs:.3f} 0 1 1 {cx:.3f},{cy + rs:.3f}" fill="none" '
                       f'stroke="#000" stroke-width="0.18"/>')
        elif abs(float(a @ d)) < 1e-6:  # side-on
            perp = np.cross(a, d)
            perp = perp / np.linalg.norm(perp)
            style = ('stroke="#555" stroke-width="0.18" stroke-dasharray="1.6 0.9"' if th.get("internal")
                     else 'stroke="#000" stroke-width="0.18"')
            for sgn in (1, -1):
                p = t(*to2d(tuple(o + perp * r * sgn), fr))
                q = t(*to2d(tuple(o + perp * r * sgn + a * th["length"]), fr))
                out.append(f'<line x1="{p[0]:.3f}" y1="{p[1]:.3f}" x2="{q[0]:.3f}" y2="{q[1]:.3f}" {style}/>')
    return out


def alternate_phantom(doc, st: dict, v: dict, t, frame, project) -> list[str]:
    """An assembly view's alternate position: the components that move, drawn where that position puts them
    in phantom lines (long dash, two short dashes)."""
    name = v.get("alt_position")
    pos = (getattr(doc, "alternate_positions", {}) or {}).get(name) if name else None
    if not pos or getattr(doc, "kind", "part") != "assembly":
        return []
    from build123d import Compound
    shapes = []
    for c in doc.components:
        if c.name not in pos or np.allclose(pos[c.name], c.x):
            continue
        body = doc._local_body(c)
        if body is None:
            continue
        keep = c.x.copy()
        c.x = np.asarray(pos[c.name], float)
        try:
            shapes.append(body.moved(c.location()))
        finally:
            c.x = keep
    if not shapes:
        return []
    vis, _hid = project(Compound(shapes), frame(st, v))
    return [f'<polyline points="{" ".join(f"{a:.3f},{b:.3f}" for a, b in (t(*q) for q in poly))}" {PHANTOM}/>' for poly in vis]


def _cell(x, y, w, h, label, text, size=3.0, bold=False):
    return {"x": x, "y": y, "w": w, "h": h, "label": label, "text": text, "size": size, "bold": bold}


PRESETS = {
    "ISO 7200": {"name": "ISO 7200", "width": 180, "height": 36, "cells": [
        _cell(0, 0, 60, 9, "Responsible dept.", "{company}"), _cell(60, 0, 60, 9, "Technical reference", "{checked}"),
        _cell(120, 0, 60, 9, "Approved by", "{approved}"), _cell(0, 9, 60, 9, "Created by", "{drawn}"),
        _cell(60, 9, 60, 9, "Document type", "PART DRAWING"), _cell(120, 9, 60, 9, "Document status", "{status}"),
        _cell(0, 18, 120, 18, "Title", "{title}", 4.5, True), _cell(120, 18, 30, 9, "Identification number", "{dwg_no}"),
        _cell(150, 18, 15, 9, "Rev.", "{rev}"), _cell(165, 18, 15, 9, "Lang.", "en"),
        _cell(120, 27, 30, 9, "Date of issue", "{date}"), _cell(150, 27, 30, 9, "Sheet", "{sheet}")]},
    "Minimal": {"name": "Minimal", "width": 120, "height": 18, "cells": [
        _cell(0, 0, 80, 9, "TITLE", "{title}", 4, True), _cell(80, 0, 40, 9, "DWG NO.", "{dwg_no}"),
        _cell(0, 9, 40, 9, "SCALE", "{scale}"), _cell(40, 9, 40, 9, "MATERIAL", "{material}"), _cell(80, 9, 40, 9, "REV", "{rev}")]},
}


def formats_dir() -> Path:
    d = Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx") / "sheet_formats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_formats() -> dict:
    saved = {}
    for p in sorted(formats_dir().glob("*.json")):
        try:
            saved[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {"presets": PRESETS, "saved": saved}


def check_format(fmt: dict) -> dict:
    if not isinstance(fmt, dict) or not isinstance(fmt.get("cells"), list) or not fmt["cells"]:
        raise ValueError("a sheet format is {name, width, height, cells: [{x, y, w, h, label, text, size?, bold?}]}")
    for c in fmt["cells"]:
        for k in ("x", "y", "w", "h"):
            if not isinstance(c.get(k), (int, float)):
                raise ValueError(f"every cell needs numbers x, y, w, h (cell {c.get('label')!r})")
    w = max(c["x"] + c["w"] for c in fmt["cells"])
    h = max(c["y"] + c["h"] for c in fmt["cells"])
    return {"name": fmt.get("name", "Custom"), "width": float(fmt.get("width") or w), "height": float(fmt.get("height") or h),
            "cells": fmt["cells"]}


def title_fields(doc, sheet: dict, fields: dict | None, scale_label: str, projection: str) -> dict:
    """What a sheet format's {placeholders} stand for."""
    import datetime as _dt
    fields = fields or {}
    props = {k.lower(): v for k, v in getattr(doc, "properties", {}).items()}
    out = {"title": fields.get("title") or props.get("description") or doc.name,
           "dwg_no": fields.get("dwg_no") or props.get("partno") or props.get("part number") or doc.name,
           "material": fields.get("material") or getattr(doc, "material", {}).get("name") or "<not specified>",
           "scale": scale_label, "sheet": str(sheet.get("name", "1")), "size": sheet.get("size", "A3"),
           "projection": "THIRD ANGLE" if projection == "third" else "FIRST ANGLE",
           "drawn": fields.get("drawn") or props.get("author") or "", "date": fields.get("drawn_date") or _dt.date.today().isoformat(),
           "checked": fields.get("checked") or "", "approved": fields.get("approved") or "",
           "company": fields.get("company") or "", "status": fields.get("status") or "",
           "rev": (sheet.get("revisions") or [{}])[-1].get("rev", "")}
    for k, v in props.items():
        out.setdefault(f"prp:{k}", v)
    return out


def _fill(text: str, values: dict) -> str:
    def sub(m):
        key = m.group(1)
        if key.lower().startswith("$prp:"):
            return str(values.get("prp:" + key[5:].strip().strip('"').lower(), ""))
        return str(values.get(key.lower(), ""))
    return re.sub(r"\{([^}]+)\}", sub, str(text))


def title_block_svg(fmt: dict, values: dict, W: float, H: float, margin: float) -> list[str]:
    """A custom sheet format's title block in the bottom-right corner: bordered cells, each a small grey label
    and its text ({title}, {dwg_no}, {material}, {scale}, {sheet}, {date}, {drawn}, {rev}... or {$PRP:"Name"})."""
    fw, fh = fmt["width"], fmt["height"]
    x0, y0 = W - margin - fw, H - margin - fh
    out = [f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{fw:.2f}" height="{fh:.2f}" fill="#fff" stroke="#000" stroke-width="0.5"/>']
    for c in fmt["cells"]:
        x, y, w, h = x0 + c["x"], y0 + c["y"], c["w"], c["h"]
        out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="none" stroke="#000" stroke-width="0.25"/>')
        if c.get("label"):
            out.append(f'<text x="{x + 1:.2f}" y="{y + 2.4:.2f}" font-size="1.9" fill="#555">{escape(str(c["label"]))}</text>')
        size = float(c.get("size", 3.0))
        bold = ' font-weight="700"' if c.get("bold") else ""
        out.append(f'<text x="{x + 1.5:.2f}" y="{y + h - max(1.2, (h - size) / 2 - 0.6):.2f}" font-size="{size}"{bold}>'
                   f'{escape(_fill(c.get("text", ""), values))}</text>')
    return out
