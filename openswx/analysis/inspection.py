"""Inspection (like SOLIDWORKS Inspection): a numbered characteristic list from a part's drawing - every
dimension, geometric tolerance and surface finish - and its 3D PMI (DimXpert), with the tolerance each one
carries (ISO 2768 general tolerances for untoleranced dimensions unless overridden), balloons on the drawing,
measured results with pass / fail, and a first-article inspection report (AS9102-style forms 1-3, HTML) or CSV.

Stored in the part's drawing state as `inspection`: {"general": "m", "order": [source ids], "chars": {source id:
{"upper", "lower", "measured": [...], "note"}}, "balloons": bool, "header": {...}}. The order keeps a
characteristic's number when others are added or deleted (deleted ones drop out, new ones go at the end).
"""

from __future__ import annotations

import csv
import io
import math
import re
from html import escape

# ISO 2768-1 general tolerances: linear (upper bounds of the ranges, mm) and +/- per class
_LIN_RANGES = (3, 6, 30, 120, 400, 1000, 2000, 4000)
_LIN = {"f": (0.05, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, None), "m": (0.1, 0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2.0),
        "c": (0.2, 0.3, 0.5, 0.8, 1.2, 2.0, 3.0, 4.0), "v": (None, 0.5, 1.0, 1.5, 2.5, 4.0, 6.0, 8.0)}
_RAD_RANGES = (3, 6, math.inf)  # external radii and chamfer heights
_RAD = {"f": (0.2, 0.5, 1.0), "m": (0.2, 0.5, 1.0), "c": (0.4, 1.0, 2.0), "v": (0.4, 1.0, 2.0)}
_ANG_RANGES = (10, 50, 120, 400, math.inf)  # by the shorter leg's length; +/- degrees
_ANG = {"f": (1.0, 0.5, 1 / 3, 1 / 6, 1 / 12), "m": (1.0, 0.5, 1 / 3, 1 / 6, 1 / 12),
        "c": (1.5, 1.0, 0.5, 0.25, 1 / 6), "v": (3.0, 2.0, 1.0, 0.5, 1 / 3)}
_GDT_NAMES = {"straightness": "Straightness", "flatness": "Flatness", "circularity": "Circularity",
              "cylindricity": "Cylindricity", "profile_line": "Profile of a line", "profile_surface": "Profile of a surface",
              "perpendicularity": "Perpendicularity", "angularity": "Angularity", "parallelism": "Parallelism",
              "position": "Position", "concentricity": "Concentricity", "symmetry": "Symmetry",
              "circular_runout": "Circular runout", "total_runout": "Total runout"}


def general_tolerance(kind: str, value: float, cls: str = "m", leg: float | None = None) -> float | None:
    """ISO 2768-1 +/- tolerance for an untoleranced dimension (degrees for angles; leg: the shorter side, mm)."""
    cls = cls.lower()
    if cls not in _LIN:
        raise ValueError("general tolerance class must be f, m, c or v")
    if kind == "angle":
        L = leg if leg is not None else 10.0
        return next(t for r, t in zip(_ANG_RANGES, _ANG[cls]) if L <= r)
    if kind == "radius":
        return next(t for r, t in zip(_RAD_RANGES, _RAD[cls]) if value <= r)
    if value < 0.5:
        return None  # ISO 2768: below 0.5 mm the tolerance goes on the dimension itself
    for r, t in zip(_LIN_RANGES, _LIN[cls]):
        if value <= r:
            return t
    return _LIN[cls][-1]


def zone(x: float, y: float, W: float, H: float) -> str:
    """Drawing zone (ISO 5457: ~50 mm fields, numbers left to right, letters top to bottom), e.g. 'B3'."""
    nc, nr = max(1, round(W / 50)), max(1, round(H / 50))
    c = min(nc, max(1, int(x / (W / nc)) + 1))
    r = min(nr, max(1, int(y / (H / nr)) + 1))
    return f"{'ABCDEFGHJKLMNPQRSTUV'[r - 1]}{c}"


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _tol_text(nominal, upper, lower, unit="") -> str:
    if upper is None and lower is None:
        return f"{_fmt(nominal)}{unit} (no tolerance)"
    if upper is not None and lower is not None and abs(upper + lower) < 1e-12:
        return f"{_fmt(nominal)}{unit} ±{_fmt(upper)}"
    return f"{_fmt(nominal)}{unit} +{_fmt(upper or 0)}/{_fmt(lower or 0)}"


def state(st: dict) -> dict:
    ins = st.setdefault("inspection", {})
    ins.setdefault("general", "m")
    ins.setdefault("order", [])
    ins.setdefault("chars", {})
    ins.setdefault("balloons", True)
    ins.setdefault("header", {})
    return ins


def _iso286(text: str, fallback: str) -> tuple[float, float]:
    """(lower, upper) deviations in mm from a '(+0.015/0)'-style ISO 286 note, else from the fallback text."""
    for s in (text, fallback):
        m = re.search(r"\+\s*(\d*\.?\d+)\s*/\s*([+-]?\s*\d*\.?\d+)", s or "")
        if m:
            return float(m.group(2).replace(" ", "")), float(m.group(1))
    return 0.0, 0.0


def collect(doc, st: dict, include_pmi: bool = True) -> list[dict]:
    """Every inspectable item, before numbering: drawing dimensions, GD&T frames, surface finishes, PMI."""
    from openswx.drawing import drawing_sheets as DS
    from openswx.drawing.drawing import SHEETS
    cls = state(st)["general"]
    items = []
    for sh in st.get("sheets") or []:
        W, H = SHEETS.get(sh.get("size", "A3"), SHEETS["A3"])
        views = {v["id"]: v for v in sh.get("views", [])}
        for d in sh.get("dims", []):
            v = views.get(d["view"], {})
            value, kind, text = DS.dim_value(d), d["kind"], d.get("text") or ""
            parsed = DS.parse_dim_text(text) if text else None
            if parsed:  # text that overrides the value: inspect what the drawing says
                value = parsed["value"]
            m = re.match(r"\s*(\d+)\s*[Xx×]", text)
            count = int(m.group(1)) if m else 1
            if kind == "angle":
                legs = [math.dist(*d[k]) for k in ("l1", "l2")]
                tol, ctype, unit, des = general_tolerance("angle", value, cls, min(legs)), "angle", "°", "Angle"
            elif kind == "radius":
                tol, ctype, unit, des = general_tolerance("radius", value, cls), "radius", "", "Radius"
            elif kind in ("diameter", "callout"):
                tol, ctype, unit, des = general_tolerance("linear", value, cls), "diameter", "", "Diameter"
            else:
                tol, ctype, unit, des = general_tolerance("linear", value, cls), "linear", "", "Dimension"
            lower, general = (-tol if tol is not None else None), tol is not None
            if d.get("tol"):  # the dimension's own tolerance comes before the general one
                from openswx.analysis import tolerance
                try:
                    lower, tol = tolerance.deviations(value, d["tol"])
                    general = False
                except tolerance.ToleranceError:
                    pass
            items.append({"source": d["id"], "sheet": sh["name"], "view": d["view"], "zone": zone(v.get("x", 0), v.get("y", 0), W, H),
                          "type": ctype, "designator": des, "nominal": round(value, 4), "upper": tol,
                          "lower": lower, "unit": unit, "count": count,
                          "general": general, "text": text or None})
        for g in sh.get("gdt", []):
            name = _GDT_NAMES.get(g["characteristic"], g["characteristic"])
            ref = " | ".join(g.get("datums") or [])
            req = (f"{name} {'⌀' if g.get('diameter') else ''}{_fmt(g['tolerance'])}"
                   f"{(' ' + g['modifier']) if g.get('modifier') else ''}{(' | ' + ref) if ref else ''}")
            items.append({"source": g["id"], "sheet": sh["name"], "view": None, "zone": zone(g["x"], g["y"], W, H),
                          "type": "gdt", "designator": name, "nominal": 0.0, "upper": float(g["tolerance"]), "lower": 0.0,
                          "unit": "", "count": 1, "general": False, "text": req})
        for f in sh.get("finishes", []):
            ra = f.get("ra")
            m = re.search(r"(\d+(?:\.\d*)?)", str(ra)) if ra is not None else None
            if not m:
                continue
            label = "Rz" if "rz" in str(ra).lower() else "Ra"
            items.append({"source": f["id"], "sheet": sh["name"], "view": None, "zone": zone(f["x"], f["y"], W, H),
                          "type": "finish", "designator": f"Surface finish {label}", "nominal": 0.0,
                          "upper": float(m.group(1)), "lower": 0.0, "unit": " µm", "count": 1, "general": False,
                          "text": f"{label} {m.group(1)} µm max"})
    if include_pmi:
        from openswx.analysis.analysis_more import _hole_tol
        for p in getattr(doc, "pmi", []) or []:
            if p["type"] == "size":
                m = re.search(r"±\s*(\d+(?:\.\d*)?)", p.get("text", ""))
                tol = float(m.group(1)) if m else general_tolerance("linear", p["value"], cls)
                items.append({"source": p["id"], "sheet": "3D (PMI)", "view": None, "zone": "", "type": "linear",
                              "designator": f"Size {p['axis']}", "nominal": p["value"], "upper": tol, "lower": -tol,
                              "unit": "", "count": 1, "general": not m, "text": p.get("text")})
            elif p["type"] == "hole":
                lo, hi = _iso286(p.get("text", ""), _hole_tol(p["diameter"]))
                items.append({"source": p["id"], "sheet": "3D (PMI)", "view": None, "zone": "", "type": "diameter",
                              "designator": "Hole diameter", "nominal": p["diameter"], "upper": hi, "lower": lo, "unit": "",
                              "count": p.get("count", 1), "general": False, "text": p.get("text")})
                m = re.search(r"⌀\s*(\d+(?:\.\d*)?)", p.get("position", ""))
                if m:
                    items.append({"source": p["id"] + ".pos", "sheet": "3D (PMI)", "view": None, "zone": "", "type": "gdt",
                                  "designator": "Position", "nominal": 0.0, "upper": float(m.group(1)), "lower": 0.0,
                                  "unit": "", "count": p.get("count", 1), "general": False, "text": p["position"]})
    return items


def characteristics(doc, st: dict, include_pmi: bool = True) -> list[dict]:
    """The numbered characteristic list with overrides and results applied (numbers stay put)."""
    ins = state(st)
    items = collect(doc, st, include_pmi)
    by = {it["source"]: it for it in items}
    order = [s for s in ins["order"] if s in by] + [it["source"] for it in items if it["source"] not in ins["order"]]
    ins["order"] = order
    out = []
    for n, src in enumerate(order, 1):
        it = dict(by[src], number=n)
        ov = ins["chars"].get(src, {})
        if "upper" in ov or "lower" in ov:
            it["upper"], it["lower"], it["general"] = ov.get("upper", it["upper"]), ov.get("lower", it["lower"]), False
        if ov.get("note"):
            it["note"] = ov["note"]
        if it["type"] in ("gdt", "finish"):
            it["requirement"] = it["text"]
        else:
            pre = f"{it['count']}X " if it["count"] > 1 else ""
            sym = {"diameter": "⌀", "radius": "R"}.get(it["type"], "")
            it["requirement"] = (pre + sym + _tol_text(it["nominal"], it["upper"], it["lower"], it["unit"])
                                 + (f" (ISO 2768-{ins['general']})" if it["general"] else ""))
        lo = it["nominal"] + it["lower"] if it["lower"] is not None else None
        hi = it["nominal"] + it["upper"] if it["upper"] is not None else None
        it["min"], it["max"] = (round(lo, 6) if lo is not None else None), (round(hi, 6) if hi is not None else None)
        meas = ov.get("measured") or []
        it["measured"] = meas
        if meas:
            bad = [m for m in meas if (lo is not None and m < lo - 1e-12) or (hi is not None and m > hi + 1e-12)]
            it["result"] = "fail" if bad else "pass"
        else:
            it["result"] = None
        out.append(it)
    return out


def set_override(st: dict, source: str, **kw) -> dict:
    ch = state(st)["chars"].setdefault(source, {})
    ch.update({k: v for k, v in kw.items() if v is not None})
    return ch


def summary(chars: list[dict]) -> dict:
    done = [c for c in chars if c["result"]]
    failed = sum(c["result"] == "fail" for c in done)
    status = "not measured" if not done else "fail" if failed else "pass" if len(done) == len(chars) else "incomplete"
    return {"characteristics": len(chars), "measured": len(done), "passed": len(done) - failed, "failed": failed,
            "status": status}


def to_csv(chars: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Char No.", "Sheet", "Zone", "View", "Characteristic", "Requirement", "Nominal", "Min", "Max",
                "Measured", "Result", "Source"])
    for c in chars:
        w.writerow([c["number"], c["sheet"], c["zone"], c["view"] or "", c["designator"], c["requirement"], _fmt(c["nominal"]),
                    "" if c["min"] is None else _fmt(c["min"]), "" if c["max"] is None else _fmt(c["max"]),
                    " ".join(_fmt(m) for m in c["measured"]), c["result"] or "", c["source"]])
    return buf.getvalue()


def to_html(doc, st: dict, chars: list[dict]) -> str:
    """A first-article inspection report: form 1 (part), form 2 (materials), form 3 (characteristics)."""
    ins = state(st)
    hdr = ins["header"]
    props = getattr(doc, "properties", {}) or {}
    part_no = hdr.get("part_number") or props.get("PartNo") or props.get("DWG NO.") or doc.name
    rows1 = [("Part number", part_no), ("Part name", hdr.get("part_name") or props.get("Description") or props.get("TITLE") or doc.name),
             ("Revision", hdr.get("revision") or props.get("Revision") or ""), ("Serial number", hdr.get("serial") or ""),
             ("Organisation", hdr.get("organization") or ""), ("FAI", "Partial" if hdr.get("partial") else "Full"),
             ("Inspector", hdr.get("inspector") or ""), ("Date", hdr.get("date") or "")]
    s = summary(chars)
    mat = (getattr(doc, "material", {}) or {}).get("name") or "-"

    def tr(cells, th=False):
        tag = "th" if th else "td"
        return "<tr>" + "".join(f"<{tag}>{escape(str(c))}</{tag}>" for c in cells) + "</tr>"

    form3 = [tr(["5. Char No.", "6. Reference location", "7. Characteristic designator", "8. Requirement",
                 "9. Results", "10. Designed tooling", "11. Nonconformance", "Result"], True)]
    for c in chars:
        loc = " / ".join(x for x in (c["sheet"], c["zone"], c["view"] or "") if x)
        row = tr([c["number"], loc, c["designator"], c["requirement"], ", ".join(_fmt(m) for m in c["measured"]),
                  "", "", (c["result"] or "-").upper()])
        form3.append(row.replace("<td>FAIL</td>", '<td class="fail">FAIL</td>').replace("<td>PASS</td>", '<td class="pass">PASS</td>'))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>First Article Inspection - {escape(str(part_no))}</title>
<style>body{{font:12px Segoe UI,Arial,sans-serif;margin:24px;color:#111}}h1{{font-size:18px}}h2{{font-size:14px;margin-top:22px}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #888;padding:3px 6px;text-align:left;vertical-align:top}}
th{{background:#eef}}.pass{{color:#070;font-weight:600}}.fail{{color:#b00;font-weight:600}}</style></head><body>
<h1>First Article Inspection Report</h1>
<h2>Form 1 - Part number accountability</h2><table>{''.join(tr(r) for r in rows1)}</table>
<h2>Form 2 - Product accountability: raw material, special processes and functional testing</h2>
<table>{tr(["Material / process", "Specification", "Code", "Certificate"], True)}{tr([mat, "", "", ""])}</table>
<h2>Form 3 - Characteristic accountability, verification and compatibility evaluation</h2><table>{''.join(form3)}</table>
<p>{s['characteristics']} characteristics, {s['measured']} measured: {s['passed']} pass, {s['failed']} fail - <b>{escape(s['status'].upper())}</b>.
Untoleranced dimensions take ISO 2768-{escape(ins['general'])} general tolerances.</p></body></html>
"""


def balloon_svg(st: dict, sheet: dict, svg: str) -> list[str]:
    """Inspection balloons (numbered circles) next to the sheet's dimension texts and annotation symbols."""
    ins = st.get("inspection") or {}
    if not ins.get("balloons") or not ins.get("order"):
        return []
    num = {s: i for i, s in enumerate(ins["order"], 1)}
    here = {d["id"] for k in ("dims", "gdt", "finishes") for d in sheet.get(k, [])}
    spots = {}
    for m in re.finditer(r'<text data-dim="([^"]+)" x="([-\d.]+)" y="([-\d.]+)"', svg):
        spots.setdefault(m.group(1), (float(m.group(2)), float(m.group(3))))
    for k in ("gdt", "finishes"):
        for a in sheet.get(k, []):
            spots.setdefault(a["id"], (a["x"], a["y"]))
    out = []
    for src, (x, y) in spots.items():
        if src not in num or src not in here:
            continue
        bx, by = x + 7.0, y - 6.0
        out.append(f'<g class="insp-balloon" data-char="{num[src]}"><circle cx="{bx:.2f}" cy="{by:.2f}" r="2.8" fill="#fff" '
                   f'stroke="#c2185b" stroke-width="0.3"/><text x="{bx:.2f}" y="{by + 1.1:.2f}" font-size="3" '
                   f'text-anchor="middle" fill="#c2185b">{num[src]}</text></g>')
    return out
