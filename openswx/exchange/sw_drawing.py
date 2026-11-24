from __future__ import annotations

import math
import re
import struct
from pathlib import Path

from openswx.exchange.sldprt import read_streams

ORIENT = {"*Front": "front", "*Back": "back", "*Top": "top", "*Bottom": "bottom", "*Left": "left", "*Right": "right",
          "*Isometric": "isometric", "*Dimetric": "dimetric", "*Trimetric": "trimetric"}
_U16 = re.compile(rb"(?:[ -~]\x00){4,}")


def _strings(data: bytes):
    for m in _U16.finditer(data):
        yield m.start(), m.group().decode("utf-16-le")


def _orthonormal(m) -> bool:
    if not all(math.isfinite(v) and abs(v) <= 1.000001 for v in m):
        return False
    rows = (m[0:3], m[3:6], m[6:9])
    return all(abs(sum(a * b for a, b in zip(rows[i], rows[j])) - (i == j)) < 1e-4
               for i in range(3) for j in range(i, 3))


def _nice_scale(s: float) -> bool:
    """A drawing scale as people set them: 1:n or n:1 (1:2.5 etc. as n/10)."""
    if not (math.isfinite(s) and 1e-3 < s < 1e3):
        return False
    return any(abs(v * k - round(v * k)) < 1e-13 for v in (s, 1 / s) for k in (1, 2, 4, 10))


def _placement(d: bytes, start: int, end: int):
    """(x, y, scale) of a view - sheet metres from the lower-left corner. A view record holds its 3x3 view
    orientation, then its sheet position (x, y, z) and scale, as doubles; a model view's first record holds a
    flag block (all-ones bytes) in place of the orientation."""
    ortho, flagged = None, None
    for k in range(max(start, 96), min(end, len(d) - 8)):
        s = struct.unpack_from("<d", d, k)[0]
        if not _nice_scale(s):
            continue
        x, y, z = struct.unpack_from("<3d", d, k - 24)
        if not (0.015 < x < 1.5 and 0.015 < y < 1.5 and abs(z) < 1):
            continue
        if _orthonormal(struct.unpack_from("<9d", d, k - 96)):
            ortho = (x, y, s)
            break
        if flagged is None and b"\xff" * 8 in d[k - 96:k - 24]:
            flagged = (x, y, s)
    return ortho or flagged


def _sheet_size(d: bytes) -> str | None:
    """The sheet's standard size: stored as the doubles (height, width) in metres."""
    import numpy as np

    from openswx.drawing.drawing import SHEETS
    best = None
    for align in range(8):
        a = np.frombuffer(d[align:align + (len(d) - align) // 8 * 8], "<f8")
        if len(a) < 2:
            continue
        with np.errstate(invalid="ignore"):
            for name, (w, h) in SHEETS.items():
                hit = np.nonzero((np.abs(a[:-1] - h / 1000) < 1e-7) & (np.abs(a[1:] - w / 1000) < 1e-7))[0]
                if len(hit) and (best is None or align + 8 * hit[0] < best[0]):
                    best = (align + 8 * int(hit[0]), name)
    return best[1] if best else None


# a note's lines end at its font (a name, or a size in points); the texts that are not notes of their own:
# sheet-format properties, zone letters, view labels, bend tags, component names (balloons, BOM rows), paths,
# hatch patterns, detail view labels, table headers
_FONT = re.compile(r"\d+(?:\.\d+)?PTS|SW[A-Z0-9]+|Century Gothic|Arial|Times New Roman|Tahoma|Verdana|Calibri"
                   r"|Courier New|Segoe UI|ISOCPEUR|Simplex")
_NOT_NOTE = re.compile(r"<ZONE|<VLNAME|<bend-|DetailItem\d|Plane\d|Sketch\d|Default$|ITEM NO|ZONE$|X LOC|Hole Table"
                       r"|[A-Za-z]:\\|.*-\d+$|<FONT size=[\d.]+><ZONE|ANSI\d+ \(|ISO\d+ \(|[A-Z]+ \(\d+ ?: ?\d+\)$|.*\$PRP"
                       r"|(?:MATERIAL|FINISH|TITLE|DATE|NAME|SIGNATURE|SIZE|SCALE|WEIGHT|REV(?:ISION)?|DWG\.? +NO\.?"
                       r"|DRAWN|CHECKED|APPROVED|Q\.A\.|COMMENTS|NEXT ASSY|USED ON|APPLICATION):?$")
_SYMBOLS = {"<MOD-DEG>": "°", "<MOD-DIAM>": "Ø", "<MOD-PM>": "±", "<MOD-RAD>": "R"}


def _note_text(lines: list[str]) -> tuple[str, float | None]:
    """A note's text, its SOLIDWORKS symbol tags as characters and other formatting dropped, and the text height
    (mm) when a <FONT size=..> gives it."""
    size = None
    m = re.search(r"<FONT[^>]*size=([\d.]+)(PTS)?", lines[0])
    if m:
        size = float(m.group(1)) * (25.4 / 72 if m.group(2) else 1.0)
    out = []
    for ln in lines:
        for tag, ch in _SYMBOLS.items():
            ln = ln.replace(tag, ch)
        out.append(re.sub(r"<[^>]*>", "", ln).strip())
    return "\n".join(ln for ln in out if ln), size


def _notes(d: bytes, toks: list, after: int, sheet_at: list, sheets: list[str], size: str | None) -> list[dict]:
    """The sheets' notes (not the sheet format's): `DetailItemN` records after the first view record holding the
    note's lines, its font and then its position (x, y, 0 doubles in metres from the sheet's lower left).
    Notes in the title block's corner belong to the sheet format (openswx draws its own title block)."""
    from openswx.drawing.drawing import SHEETS
    W = SHEETS.get(size, (1e9, 1e9))[0] if size else 1e9
    out, seen = [], set()
    for i, (o, s) in enumerate(toks):
        if o < after or s in seen or not re.fullmatch(r"DetailItem\d+", s):
            continue
        seen.add(s)
        lines = []
        for _, t in toks[i + 1:i + 12]:
            if _FONT.fullmatch(t) or re.fullmatch(r"DetailItem\d+", t) or t in lines:
                break
            lines.append(t)
        if not lines or _NOT_NOTE.match(lines[0]) or len(re.sub(r"<[^>]*>|\W", "", lines[0])) < 2:
            continue
        pos = None
        for k in range(o + 20, min(o + 1500, len(d) - 24)):
            x, y, z = struct.unpack_from("<3d", d, k)
            if 0.001 < x < 1.3 and 0.001 < y < 1.3 and abs(z) < 1e-9:
                pos = (round(x * 1000, 3), round(y * 1000, 3))
                break
        # (SOLIDWORKS' ANSI formats' title blocks: 250 mm wide; the border strip holds the zone marks)
        if pos is None or (pos[0] > W - 260 and pos[1] < 72) or min(pos) < 8:
            continue
        text, height = _note_text(lines)
        sheet = next((n for p, n in reversed(sheet_at) if p < o), sheets[0])
        out.append({"text": text, "position": pos, "sheet": sheet, **({"size": round(height, 2)} if height else {})})
    return out


# feature control frames: the characteristic tag (<IGTOL-STRAIGHT>, ...) then the frame's texts up to <Gtol>
_GTOL = {"STRAIGHT": "straightness", "FLAT": "flatness", "CIRC": "circularity", "CYL": "cylindricity",
         "LPROF": "profile_line", "SPROF": "profile_surface", "PROFILE": "profile_surface", "PERP": "perpendicularity",
         "ANGULAR": "angularity", "ANG": "angularity", "PARA": "parallelism", "POS": "position", "TP": "position",
         "CONC": "concentricity", "SYM": "symmetry", "CRUNOUT": "circular_runout", "RUNOUT": "circular_runout",
         "TRUNOUT": "total_runout"}


def _triples(d: bytes, a: int, b: int) -> list[tuple[int, float, float]]:
    """(offset, x, y) of the (x, y, 0) doubles in d[a:b] that lie on a sheet (metres, from the lower left)."""
    out = []
    for k in range(max(a, 0), min(b, len(d) - 24)):
        x, y, z = struct.unpack_from("<3d", d, k)
        if 0.001 < x < 1.3 and 0.001 < y < 1.3 and abs(z) < 1e-9:
            out.append((k, x, y))
    return out


def _gdt(d: bytes, toks: list, sheet_at: list, sheets: list[str]) -> list[dict]:
    """Feature control frames: the characteristic, the tolerance (a diameter zone after <MOD-DIAM>) and the frame's
    position (datum references are not decoded: no sample frame has one) - the first sheet point written after its texts that is not also written before
    them (those belong to the drawing view the frame sits in)."""
    out = []
    for i, (o, s) in enumerate(toks):
        m = re.fullmatch(r"<I?GTOL-([A-Z]+)>", s)
        if not m:
            continue
        kind = _GTOL.get(m.group(1))
        tol, diameter = None, False
        for _, t in toks[i + 1:i + 12]:
            if t == "<Gtol>" or re.fullmatch(r"DetailItem\d+|<I?GTOL-[A-Z]+>", t):
                break
            diameter = diameter or "<MOD-DIAM>" in t
            v = re.sub(r"<[^>]*>", "", t).strip()
            if tol is None and re.fullmatch(r"\d*\.?\d+", v):
                tol = float(v)
        if kind is None or tol is None:
            continue
        before = {(round(x, 9), round(y, 9)) for _, x, y in _triples(d, o - 3000, o)}
        pos = next(((x, y) for _, x, y in _triples(d, o + 20, o + 3000) if (round(x, 9), round(y, 9)) not in before), None)
        if pos is None:
            continue
        sheet = next((n for p, n in reversed(sheet_at) if p < o), sheets[0])
        out.append({"characteristic": kind, "tolerance": tol, "diameter": diameter, "datums": [],
                    "position": (round(pos[0] * 1000, 3), round(pos[1] * 1000, 3)), "sheet": sheet})
    return out


def read_drawing(path: str) -> dict:
    """{"models": [paths as written], "sheets": [names], "sheet_size": "A3" | ... | None,
    "views": [{"name", "orientation" | None, "sheet", "position": (x, y) sheet mm from the lower left, "scale"}],
    "notes": [{"text", "position", "sheet", "size"?}],
    "gdt": [{"characteristic", "tolerance", "diameter", "datums", "position", "sheet"}]} (position and scale when the
    view record gives them)."""
    st = read_streams(path)
    d = st.get("Contents/Definition", b"")
    toks = list(_strings(d))
    models = []
    for _, s in toks:
        s2 = s[:-1] if s.endswith("N") and s[:-1].lower().endswith((".sldprt", ".sldasm")) else s
        if s2.lower().endswith((".sldprt", ".sldasm")) and s2 not in models:
            models.append(s2)
    # the view records proper: the last run of "Drawing ViewN" names (the first run is a table of contents)
    names = [(o, s) for o, s in toks if re.fullmatch(r"Drawing View\d+", s)]
    seen, records = set(), []
    for o, s in reversed(names):
        if s in seen:
            break
        seen.add(s)
        records.append((o, s))
    records.reverse()
    sheets = [s for _, s in _strings(st.get("SheetPreviews/SheetNames", b"\x00"))] or ["Sheet1"]
    sheets = [re.sub(r"^[^A-Za-z0-9]+", "", s) for s in sheets]
    sheet_at = [(o, s) for o, s in toks if s in sheets]
    views = []
    for i, (o, s) in enumerate(records):
        end = records[i + 1][0] if i + 1 < len(records) else len(d)
        orient = next((ORIENT[t] for p, t in toks if o < p < end and t in ORIENT), None)
        sheet = next((n for p, n in reversed(sheet_at) if p < o), sheets[0])  # the sheet record before it
        view = {"name": s, "orientation": orient, "sheet": sheet}
        place = _placement(d, o, end)
        if place:
            view["position"] = (round(place[0] * 1000, 3), round(place[1] * 1000, 3))  # sheet mm, y up
            view["scale"] = place[2]
        dims = []  # a dimension's display text, boxed: <Border type=14>value</Border> (each held twice)
        for p, t in toks:
            if o < p < end and t.startswith("<Border type=14>") and t not in dims:
                dims.append(t)
        if dims:
            view["dimensions"] = [re.sub(r"</?Border[^>]*>", "", t) for t in dims]
        views.append(view)
    # the model the views show: the first one named inside the view records (a drawing lists others too)
    shown = next((s.rstrip("N") for o, s in toks if records and o > records[0][0]
                  and s.rstrip("N").lower().endswith((".sldprt", ".sldasm"))), None)
    same = [m for m in models if Path(m.replace("\\", "/")).stem.lower() == Path(path).stem.lower()]
    for first in [shown] + same[:1]:  # a model named like the drawing (SOLIDWORKS' default) comes before all
        if first and first in models:
            models.remove(first)
            models.insert(0, first)
    size = _sheet_size(d)
    notes = _notes(d, toks, records[0][0], sheet_at, sheets, size) if records else []
    gdt = _gdt(d, toks, sheet_at, sheets) if records else []
    return {"models": models, "sheets": sheets, "views": views, "sheet_size": size, "notes": notes, "gdt": gdt}


def find_model(path: str, models: list[str]) -> Path | None:
    """The referenced model on this computer (the one the views show first): the stored path, else a file of
    that name beside the drawing (or in a folder below it)."""
    here = Path(path).parent
    for m in models:
        p = Path(m)
        if p.exists():
            return p
        cand = here / p.name
        if cand.exists():
            return cand
        hits = [q for q in here.rglob(p.name)] if len(list(here.iterdir())) < 2000 else []
        if hits:
            return hits[0]
    return None
