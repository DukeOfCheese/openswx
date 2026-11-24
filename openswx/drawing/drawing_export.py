"""Export drawing sheets (the SVG subset written by drawing.py) to PDF and DXF.

PDF is written directly (vector paths, Helvetica text, circular clip paths for detail views) so no
PDF library is needed. DXF uses ezdxf with layers VISIBLE / HIDDEN / THIN / TEXT / BORDER.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

NS = "{http://www.w3.org/2000/svg}"
PT = 72 / 25.4  # points per mm
# GD&T and drafting glyphs that the PDF base-14 fonts cannot show
ASCII = {"⌀": "DIA ", "⏤": "STRT", "⏥": "FLAT", "○": "CIRC", "⌭": "CYL", "⌒": "PROF", "⌓": "PRFS", "⟂": "PERP",
         "∠": "ANG", "∥": "PAR", "⌖": "POS", "◎": "CONC", "⌯": "SYM", "↗": "RUN", "⌰": "TRUN", "°": " deg", "±": "+/-",
         "×": "x", "—": "-", "–": "-", "Ⓜ": "(M)", "Ⓛ": "(L)", "↧": "DEPTH "}


def _num(v, default=0.0) -> float:
    try:
        return float(str(v).replace("mm", ""))
    except (TypeError, ValueError):
        return default


def _color(c):
    if not c or c == "none" or c.startswith("url("):
        return None
    c = c.strip()
    if c.startswith("#") and len(c) == 4:
        c = "#" + "".join(ch * 2 for ch in c[1:])
    if c.startswith("#") and len(c) == 7:
        return tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return (0, 0, 0)


def _points(s: str):
    vals = [float(v) for v in re.split(r"[\s,]+", s.strip()) if v]
    return list(zip(vals[0::2], vals[1::2]))


def _path_polys(d: str):
    """M/L/H/V/Z paths (all that drawing.py emits) -> list of (points, closed)."""
    out, cur, x, y = [], [], 0.0, 0.0
    for cmd, args in re.findall(r"([MLHVZmlhvz])([^MLHVZmlhvz]*)", d):
        nums = [float(v) for v in re.split(r"[\s,]+", args.strip()) if v]
        if cmd in "Mm":
            if cur:
                out.append((cur, False))
            pts = list(zip(nums[0::2], nums[1::2]))
            if cmd == "m":
                pts = [(x + a, y + b) for a, b in pts]
            cur = pts[:]
        elif cmd in "Ll":
            for a, b in zip(nums[0::2], nums[1::2]):
                x, y = (x + a, y + b) if cmd == "l" else (a, b)
                cur.append((x, y))
        elif cmd in "Hh":
            cur += [((x + v) if cmd == "h" else v, y) for v in nums]
        elif cmd in "Vv":
            cur += [(x, (y + v) if cmd == "v" else v) for v in nums]
        elif cmd in "Zz":
            if cur:
                out.append((cur, True))
            cur = []
        if cur:
            x, y = cur[-1]
    if cur:
        out.append((cur, False))
    return out


def _walk(svg: str):
    """viewBox and an iterator of (element, tag, clip) for drawable elements; clip = enclosing circle clip."""
    root = ET.fromstring(svg)
    clips = {}
    for cp in root.iter(NS + "clipPath"):
        c = cp.find(NS + "circle")
        if c is not None:
            clips[cp.get("id")] = (_num(c.get("cx")), _num(c.get("cy")), _num(c.get("r")))

    def rec(el, clip):
        for ch in el:
            tag = ch.tag.replace(NS, "")
            if tag in ("defs", "clipPath", "pattern"):
                continue
            if tag == "g":
                m = re.search(r"url\(#([^)]+)\)", ch.get("clip-path", ""))
                yield from rec(ch, clips.get(m.group(1)) if m else clip)
            else:
                yield ch, tag, clip
    vb = [float(v) for v in root.get("viewBox", "0 0 420 297").split()]
    return vb, rec(root, None)


def _text_of(el) -> str:
    return "".join(el.itertext())


def _rotation(el) -> float:
    m = re.search(r"rotate\(([-\d.]+)", el.get("transform", ""))
    return float(m.group(1)) if m else 0.0


def svg_to_pdf(svg: str) -> bytes:
    return svgs_to_pdf([svg])


def svgs_to_pdf(svgs: list[str]) -> bytes:
    """A PDF with one page per SVG sheet (drawings with several sheets)."""
    pages = [_page(s) for s in svgs]
    n = len(pages)
    # objects: 1 catalog, 2 pages, 3 / 4 fonts, then per page a page object and its content stream
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            ("<< /Type /Pages /Kids [" + " ".join(f"{5 + 2 * i} 0 R" for i in range(n)) + f"] /Count {n} >>").encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    for i, (W, H, content) in enumerate(pages):
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W * PT:.2f} {H * PT:.2f}] /Contents {6 + 2 * i} 0 R "
                     f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>").encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _page(svg: str):
    """(width mm, height mm, PDF content stream) of one SVG sheet."""
    (_vx, _vy, W, H), items = _walk(svg)
    ops: list[str] = []

    def T(x, y):  # SVG mm (y down) -> PDF points (y up)
        return x * PT, (H - y) * PT

    def style(el, fill_default="none"):
        stroke = _color(el.get("stroke"))
        fill = _color(el.get("fill", fill_default))
        if el.get("fill", "").startswith("url("):
            fill = (0.82, 0.82, 0.82)  # section hatch pattern -> light grey fill
        sw = _num(el.get("stroke-width"), 0.25) * PT
        dash = el.get("stroke-dasharray")
        s = [f"{sw:.3f} w", ("[" + " ".join(f"{_num(v) * PT:.2f}" for v in re.split(r"[\s,]+", dash.strip())) + "] 0 d")
             if dash else "[] 0 d"]
        if stroke:
            s.append("%.3f %.3f %.3f RG" % stroke)
        if fill:
            s.append("%.3f %.3f %.3f rg" % fill)
        paint = ("B" if stroke else "f") if fill else ("S" if stroke else "n")
        return s, paint

    def poly(pts, closed):
        if not pts:
            return []
        return ["%.3f %.3f m" % T(*pts[0])] + ["%.3f %.3f l" % T(*p) for p in pts[1:]] + (["h"] if closed else [])

    def circle(cx, cy, r):
        x, y = T(cx, cy)
        r *= PT
        k = 0.5523 * r
        return [f"{x + r:.3f} {y:.3f} m",
                f"{x + r:.3f} {y + k:.3f} {x + k:.3f} {y + r:.3f} {x:.3f} {y + r:.3f} c",
                f"{x - k:.3f} {y + r:.3f} {x - r:.3f} {y + k:.3f} {x - r:.3f} {y:.3f} c",
                f"{x - r:.3f} {y - k:.3f} {x - k:.3f} {y - r:.3f} {x:.3f} {y - r:.3f} c",
                f"{x + k:.3f} {y - r:.3f} {x + r:.3f} {y - k:.3f} {x + r:.3f} {y:.3f} c", "h"]

    for el, tag, clip in items:
        g = ["q"] + (circle(*clip) + ["W n"] if clip else [])
        if tag == "rect":
            if el.get("stroke") in (None, "none") and el.get("fill", "none") in ("none", "#fff", "#ffffff", "white"):
                continue  # sheet background / invisible pick boxes
            x, y, w, h = (_num(el.get(k)) for k in ("x", "y", "width", "height"))
            s, paint = style(el, "#000")
            g += s + poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True) + [paint]
        elif tag == "line":
            s, _paint = style(el)
            g += s + poly([(_num(el.get("x1")), _num(el.get("y1"))), (_num(el.get("x2")), _num(el.get("y2")))], False) + ["S"]
        elif tag in ("polyline", "polygon"):
            s, paint = style(el)
            g += s + poly(_points(el.get("points", "")), tag == "polygon") + [paint]
        elif tag == "circle":
            s, paint = style(el, "#000")
            g += s + circle(_num(el.get("cx")), _num(el.get("cy")), _num(el.get("r"))) + [paint]
        elif tag == "path":
            s, paint = style(el, "#000")
            body = []
            for pts, closed in _path_polys(el.get("d", "")):
                body += poly(pts, closed)
            g += s + body + [("f*" if paint == "f" and el.get("fill-rule") == "evenodd" else paint)]
        elif tag == "text":
            txt = _text_of(el)
            if not txt.strip():
                continue
            for k, v in ASCII.items():
                txt = txt.replace(k, v)
            txt = txt.encode("latin-1", "replace").decode("latin-1")
            size = _num(el.get("font-size"), 3.0)
            x, y = _num(el.get("x")), _num(el.get("y"))
            width = 0.52 * size * len(txt)  # Helvetica average advance
            anchor = el.get("text-anchor", "start")
            dx = -width / 2 if anchor == "middle" else -width if anchor == "end" else 0.0
            rot = -math.radians(_rotation(el))
            c, s_ = math.cos(rot), math.sin(rot)
            px, py = T(x, y)
            px, py = px + dx * PT * c, py + dx * PT * s_
            fill = _color(el.get("fill", "#000")) or (0, 0, 0)
            font = "/F2" if el.get("font-weight") in ("700", "bold") else "/F1"
            esc = txt.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            g += ["%.3f %.3f %.3f rg" % fill, "BT", f"{font} {size * PT:.2f} Tf",
                  f"{c:.5f} {s_:.5f} {-s_:.5f} {c:.5f} {px:.3f} {py:.3f} Tm", f"({esc}) Tj", "ET"]
        else:
            continue
        ops += g + ["Q"]

    return W, H, "\n".join(ops).encode("latin-1")


def svg_to_dxf(svg: str, path: str) -> str:
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    (_vx, _vy, W, H), items = _walk(svg)
    doc = ezdxf.new("R2010", setup=True)
    doc.units = 4  # millimetres
    msp = doc.modelspace()
    for name, lt, color in (("VISIBLE", "CONTINUOUS", 7), ("HIDDEN", "DASHED", 8), ("THIN", "CONTINUOUS", 1),
                            ("TEXT", "CONTINUOUS", 3), ("BORDER", "CONTINUOUS", 7)):
        if name not in doc.layers:
            doc.layers.add(name, color=color, linetype=lt if lt in doc.linetypes else "CONTINUOUS")

    def F(x, y):  # SVG y down -> DXF y up
        return x, H - y

    def layer(el):
        if el.get("stroke-dasharray"):
            return "HIDDEN"
        return "VISIBLE" if _num(el.get("stroke-width"), 0.25) >= 0.3 else "THIN"

    for el, tag, _clip in items:
        if tag == "rect":
            if el.get("stroke") in (None, "none"):
                continue  # background / invisible pick boxes
            x, y, w, h = (_num(el.get(k)) for k in ("x", "y", "width", "height"))
            msp.add_lwpolyline([F(x, y), F(x + w, y), F(x + w, y + h), F(x, y + h)], close=True, dxfattribs={"layer": "BORDER"})
        elif tag == "line":
            msp.add_line(F(_num(el.get("x1")), _num(el.get("y1"))), F(_num(el.get("x2")), _num(el.get("y2"))),
                         dxfattribs={"layer": layer(el)})
        elif tag in ("polyline", "polygon"):
            pts = [F(*p) for p in _points(el.get("points", ""))]
            if len(pts) > 1:
                msp.add_lwpolyline(pts, close=tag == "polygon", dxfattribs={"layer": layer(el)})
        elif tag == "circle":
            msp.add_circle(F(_num(el.get("cx")), _num(el.get("cy"))), _num(el.get("r")), dxfattribs={"layer": layer(el)})
        elif tag == "path":
            for pts, closed in _path_polys(el.get("d", "")):
                if len(pts) > 1:
                    msp.add_lwpolyline([F(*p) for p in pts], close=closed, dxfattribs={"layer": "THIN"})
        elif tag == "text":
            txt = _text_of(el)
            if not txt.strip():
                continue
            align = {"middle": "BOTTOM_CENTER", "end": "BOTTOM_RIGHT"}.get(el.get("text-anchor", ""), "BOTTOM_LEFT")
            t = msp.add_text(txt, height=_num(el.get("font-size"), 3.0), rotation=-_rotation(el), dxfattribs={"layer": "TEXT"})
            t.set_placement(F(_num(el.get("x")), _num(el.get("y"))), align=TextEntityAlignment[align])
    doc.saveas(path)
    return path
