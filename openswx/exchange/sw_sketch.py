"""SOLIDWORKS sketches from the `ResolvedFeatures` stream (clean-room format analysis; see docs/re/README.md).

A sketch object (`moProfileFeature_c`, named Sketch<n>) holds its entities as point records followed by the
segment objects that use them:
- point record: u32 id, a `FF FF <version> <byte> FF x8 00 00 80 BF ...` header, then `00 00 F0 3F`,
  8 zero bytes and a 2-byte tag, then x, y as little-endian doubles (metres, sketch coordinates), a u16 kind
  and a u16 count and, per segment using the point, the segment's class (its definition
  `FF FF 01 00 <len> <name>` the first time, then a class word) + u16 index + `FF FF FF FF 00 00 00 00`;
- segment classes: `sgLineHandle` (two end points), `sgArcHandle` (end points; a full circle repeats its one
  point), `sgSplineHandle`; arc centres and spline through points have their own kinds (see parse_sketch);
- after the entities, the sketch frame: u32 time stamp, `00 00`, u32 2, a flag byte and, when it is 1, a
  row-major 3x3 rotation (model = R * sketch), translation (metres) and scale.
"""
from __future__ import annotations

import math
import re
import struct

OPTIONS = {"fill_from_records": True, "skip_null_words": True}  # decoding switches (for diagnosis)
_PT = re.compile(rb"\x00\x00\xf0\x3f\x00\x00\x00\x00\x00\x00\x00\x00(..)", re.S)
_CLS = re.compile(rb"\xff\xff\x01\x00(..)", re.S)
_FRAME = re.compile(rb"(....)\x00\x00\x02\x00\x00\x00([\x00\x01])", re.S)


def _frame(buf: bytes, start: int, end: int):
    """Sketch plane: (R, t in mm, offset of the frame record). The record is a u32 time stamp, `00 00`, u32 2
    and a flag byte; flag 1 is followed by the rotation, translation (metres) and scale 1.0."""
    import numpy as np
    R, t, fpos = np.eye(3), np.zeros(3), None
    for m in _FRAME.finditer(buf, start, end):  # the first record that passes the checks
        stamp = struct.unpack("<I", m.group(1))[0]
        if not 631152000 <= stamp <= 2524608000 or m.start() < 4:  # 1990 .. 2050
            continue
        if not 1 <= struct.unpack_from("<I", buf, m.start() - 4)[0] <= 16:  # preceded by a small count (3, 4)
            continue
        if m.group(2)[0] == 0:
            R, t, fpos = np.eye(3), np.zeros(3), m.start()
            break
        if m.end() + 104 <= len(buf):
            v = struct.unpack_from("<13d", buf, m.end())
            M = np.array(v[:9]).reshape(3, 3)
            if np.allclose(M @ M.T, np.eye(3), atol=1e-9) and abs(v[12] - 1.0) < 1e-12:
                R, t, fpos = M, np.array(v[9:12]) * 1000.0, m.start()
                break
    if fpos is None:  # sketch on a face: the plane's origin, normal, flag byte and rotation follow the class name
        face = plane_frame(buf, start, end)
        if face is not None:
            R, t = face
    return R, t, (fpos if fpos is not None else end)


def plane_frame(buf: bytes, start: int, end: int):
    """(R, t in mm) of the first plane data record (`moFaceRefPlnData_c` of a reference plane, `FaceRefPlnData_c`
    of a sketch on a face) in buf[start:end]: origin (3 doubles, metres), normal (3 doubles), a flag byte and,
    when it is 1, the row-major rotation; None when there is none."""
    import numpy as np
    f = buf.find(b"FaceRefPlnData_c", start, end)
    if f < 0 or f + 16 + 49 + 72 > len(buf):
        return None
    p = f + 16
    t = np.array(struct.unpack_from("<3d", buf, p)) * 1000.0
    R = np.eye(3)
    if buf[p + 48] == 1:
        M = np.array(struct.unpack_from("<9d", buf, p + 49)).reshape(3, 3)
        if np.allclose(M @ M.T, np.eye(3), atol=1e-9):
            R = M
    if not np.all(np.isfinite(t)):
        return None
    return R, t


def _other_frames(buf: bytes, start: int, end: int, R, t) -> list:
    """Further candidate planes for a sketch whose frame record was not found (some older sketches on faces):
    every flag byte 1 followed by an orthonormal rotation, a translation and scale 1.0 in the sketch."""
    import numpy as np
    out = []
    for k in range(start, end - 105):
        if buf[k] != 1:
            continue
        v = struct.unpack_from("<13d", buf, k + 1)
        M = np.array(v[:9]).reshape(3, 3)
        if abs(v[12] - 1.0) < 1e-12 and np.allclose(M @ M.T, np.eye(3), atol=1e-9) and np.linalg.det(M) > 0:
            q = np.array(v[9:12]) * 1000.0
            if not any(np.allclose(M, F) for F, _ in out + [(R, t)]):
                out.append((M, q if np.any(q) else t))
    return out


def _bsplines(buf: bytes, start: int, end: int) -> list[dict]:
    """Spline curves of a sketch (in `modifSplineList_c`): a u32 knot count and the knots, then a u32 pole
    count and the poles as x, y, z doubles (metres); degree = knots - poles - 1."""
    out = []
    s = buf.find(b"modifSplineList_c", start, end)
    k = s
    while 0 <= k < end - 8:
        nk = struct.unpack_from("<I", buf, k)[0]
        if 5 <= nk <= 4096 and k + 4 + nk * 8 + 4 <= end:
            knots = struct.unpack_from(f"<{nk}d", buf, k + 4)
            q = k + 4 + nk * 8
            npole = struct.unpack_from("<I", buf, q)[0]
            deg = nk - npole - 1
            if (knots[0] == 0.0 and all(b >= a for a, b in zip(knots, knots[1:])) and knots[-1] > 0
                    and 1 <= deg <= 9 and knots.count(knots[0]) == deg + 1 and q + 4 + npole * 24 <= end):
                v = struct.unpack_from(f"<{npole * 3}d", buf, q + 4)
                out.append({"degree": deg, "knots": list(knots),
                            "poles": [[v[3 * i] * 1000.0, v[3 * i + 1] * 1000.0] for i in range(npole)]})
                k = q + 4 + npole * 24
                continue
        k += 1
    return out


# (the 8 bytes before `00 00 80 BF` are all FF, or FF x4 then 00 x4 in older files)
_REC = re.compile(rb"(....)\xff\xff..[\x00-\x0f]\xff{4}(?:\xff{4}|\x00{4})\x00\x00\x80\xbf", re.S)
_PT3 = re.compile(rb"\x00\x00\xf0\x3f\x00{8}\x0e\x00")  # a 3D sketch point
_HDR = re.compile(rb"\xff{4}(?:\xff{4}|\x00{4})\x00\x00\x80\xbf")


def _segment_records(buf: bytes, start: int, end: int, points: dict, tags=range(0x10, 0x20)) -> list:
    """Segments written as records of their own (some files keep the point-to-segment references only there):
    the same header as a point, but where a point has its tag and coordinates, two u16 end points: indices into
    the sketch's point records in file order (every non-segment record with a real id, whether or not it
    carries coordinates). The u16 20 bytes after the header holds the kind in its low 3 bits (4, 5: line)
    and bit 0x08 for construction geometry. Returns [(point id, point id, kind, construction)]."""
    order, segs = [], []
    for m in _REC.finditer(buf, start, end):
        if m.group(1) == b"\xff\xff\xff\xff":
            continue  # (helper records, not sketch entities)
        rid = struct.unpack("<I", m.group(1))[0]
        if rid >= 0xFFFE:
            continue  # the sketch's own record
        tail = buf.find(b"\x00\x00\xf0\x3f" + bytes(8), m.end(), m.end() + 80)
        q = tail + 12
        if tail < 0 or (buf[q + 1] == 0 and buf[q] in tags):
            order.append(rid)  # a point record
            continue
        a, b = struct.unpack_from("<HH", buf, q)
        flags = struct.unpack_from("<H", buf, m.end() + 20)[0]
        segs.append((a, b, flags & 0x07, bool(flags & 0x08)))
    out = []
    for a, b, kind, cons in segs:
        if a < len(order) and b < len(order) and a != b and order[a] in points and order[b] in points:
            out.append((order[a], order[b], kind, cons))
    return out


def _point_order(buf: bytes, start: int, end: int) -> list:
    """Ids of the sketch's point records in file order (what segment records and point handles index)."""
    order = []
    for m in _REC.finditer(buf, start, end):
        rid = struct.unpack("<I", m.group(1))[0]
        if rid >= 0xFFFE:
            continue
        tail = buf.find(b"\x00\x00\xf0\x3f" + bytes(8), m.end(), m.end() + 80)
        q = tail + 12
        if tail < 0 or (buf[q + 1] == 0 and 0x10 <= buf[q] <= 0x1f):
            order.append(rid)
    return order


_REL = re.compile(rb"\xff\xff\xff\xff\x00{17}[^\x00]\x00\x00\x00\x02\x00\x00\x00\x00\x00\xfe\xff\x00\x00", re.S)
_DIMREF = re.compile(rb"\x01\x00\x00\x00(.)\x00[\x00\x01]\x00\x00\x00\x00\x00", re.S)


def dimensions(buf: bytes, params: list) -> list:
    """Sketch dimensions from the parameter objects that follow a sketch (sw_tree.scan entries): each dimension
    is written twice (consecutive objects with the same references), with the value as a double 22 bytes after
    the name; then `01 00 00 00`, a u16 reference count, a u16 flag, 4 zero bytes and the references (class
    word, u16 index, FF FF FF FF 00 00 00 00). Returns [{"refs": [(class word, index)], "values": [...],
    "angle": bool}]."""
    out = []
    for o in params:
        ne = o["name_end"]
        if ne + 30 > len(buf):
            continue
        v = struct.unpack_from("<d", buf, ne + 22)[0]
        m = _DIMREF.search(buf, ne + 30, ne + 120)
        if not m:
            continue
        n, q = m.group(1)[0], m.end()
        refs = []
        for _ in range(n):
            if q + 12 > len(buf) or buf[q + 4:q + 8] != b"\xff\xff\xff\xff":
                break
            refs.append(struct.unpack_from("<HH", buf, q))
            q += 12
        if len(refs) != n or not refs:
            continue
        angle = o["cls"] == "moAngleParameter_c"
        if out and out[-1]["refs"] == refs and len(out[-1]["values"]) == 1:
            out[-1]["values"].append(v)
        else:
            out.append({"refs": refs, "values": [v], "angle": angle})
    return out


def relations(buf: bytes, start: int, end: int, state: dict) -> list:
    """Sketch relations in buf[start:end]: `FF FF FF FF`, 17 zero bytes, the type as a u32 (swConstraintType_e:
    4 horizontal, 5 vertical, 6 tangent, 7 parallel, 8 perpendicular, 10 concentric, 14 equal, 17 fixed, ...),
    a u32 (2), `00 00 FE FF 00 00`, then the references as in a point's list (class word or class defined
    inline, u16 index, `FF FF FF FF 00 00 00 00`) up to the first word that is neither. Returns
    [{"type": code, "refs": [(class, index)]}]."""
    defined, refmap = state.setdefault("defined", []), state.setdefault("refmap", {})
    out = []
    for m in _REL.finditer(buf, start, end):
        code = struct.unpack_from("<I", buf, m.start() + 21)[0]
        q, refs = m.end(), []
        while q + 12 <= end:
            if _CLS.match(buf, q):  # the class is defined here
                n = struct.unpack_from("<H", buf, q + 4)[0]
                cls = buf[q + 6:q + 6 + n].decode("latin-1")
                if cls not in defined:
                    defined.append(cls)
                q += 6 + n
            else:
                word = struct.unpack_from("<H", buf, q)[0]
                if not word & 0x8000:
                    break
                cls = refmap.get(word)
                if cls is None:  # (class words are handed out in definition order)
                    cls = next((c for c in defined if c not in refmap.values()), None)
                    if cls is None:
                        break
                    refmap[word] = cls
                q += 2
            if buf[q + 2:q + 6] != b"\xff\xff\xff\xff":
                break
            refs.append((cls, struct.unpack_from("<H", buf, q)[0]))
            q += 10
        if refs:
            out.append({"type": code, "refs": refs})
    return out


def parse_sketch(buf: bytes, start: int, end: int, state: dict | None = None) -> dict:
    """Entities of the sketch object spanning buf[start:end]: points (mm), lines, arcs (centre, start, end;
    counter-clockwise, start == end for a circle), splines (point ids in order) and the frame. `state`
    carries the segment class words between the sketches of one stream (classes are numbered archive-wide)."""
    R, t, fpos = _frame(buf, start, end)
    alternatives = [] if fpos < end else _other_frames(buf, start, end, R, t)
    points, uses = {}, {}  # point id -> (x, y);  (class, index) -> [point ids]
    state = state if state is not None else {}
    defined, refmap = state.setdefault("defined", []), state.setdefault("refmap", {})  # classes; class word -> class
    centres, through, axis = [], [], []  # centre points; spline through points; ellipse axis points
    pos = start
    for m in _PT.finditer(buf, start, fpos):
        x, y = struct.unpack_from("<dd", buf, m.end())
        tag = m.group(1)
        if tag[1] != 0 or not 0x10 <= tag[0] <= 0x1f:
            continue  # not an entity point (origin / helper records); 1E, or 1A etc. with other flags set
        # header `FF FF <version u16> <byte> FF x8 00 00 80 BF` (version 0x1F in current files, 7 in older ones,
        # whose 8 bytes are FF x4 then 00 x4; some records have 00 x4 in place of `FF FF <version>`)
        last = None
        for last in _HDR.finditer(buf, pos, m.start()):
            pass
        hdr = (last.start() if last else -1) - 5
        if hdr < 4 or (buf[hdr:hdr + 2] != b"\xff\xff" and buf[hdr:hdr + 4] != bytes(4)):
            continue
        pid = struct.unpack_from("<I", buf, hdr - 4)[0]
        if pid in points or pid == 0xFFFFFFFF or not (math.isfinite(x) and math.isfinite(y) and abs(x) < 1e3 and abs(y) < 1e3):
            continue  # another record type that happens to share the layout
        points[pid] = (x * 1000.0, y * 1000.0)
        q = m.end() + 16
        kind, count = struct.unpack_from("<HH", buf, q)
        if kind == 0 and buf[q + 4:q + 8] == bytes(4) and buf[q + 10:q + 14] == b"\xfe\xff\xff\xff":
            centres += [pid] * count  # an arc / circle centre, shared by `count` arcs
            pos = m.end()
            continue
        if kind == 4:
            through.append(pid)
            pos = m.end()
            continue
        if kind == 3:  # an end of an ellipse axis
            axis.append(pid)
            pos = m.end()
            continue
        q += 4
        for _ in range(count):
            if _CLS.match(buf, q):  # the class is defined here
                n = struct.unpack_from("<H", buf, q + 4)[0]
                cls = buf[q + 6:q + 6 + n].decode("latin-1")
                if cls not in defined:
                    defined.append(cls)
                q += 6 + n
            else:
                word = struct.unpack_from("<H", buf, q)[0]
                if OPTIONS["skip_null_words"] and word == 0 and struct.unpack_from("<H", buf, q + 2)[0] & 0x8000:
                    q += 2  # a null word before the reference
                    word = struct.unpack_from("<H", buf, q)[0]
                if not word & 0x8000:
                    break
                cls = refmap.get(word)
                if cls is None and kind == 2:  # a point on a circle (its class may be defined outside sketches)
                    cls = refmap[word] = "sgArcHandle"
                if cls is None:  # class words are handed out in definition order
                    cls = next((c for c in defined if c not in refmap.values()), defined[-1] if defined else None)
                    refmap[word] = cls
                q += 2
            idx = struct.unpack_from("<H", buf, q)[0]
            q += 2 + 8
            uses.setdefault((cls, idx), []).append(pid)
        pos = m.end()
    lines, arcs, splines, ellipses = [], [], [], []
    handles = {}  # (segment class, index) -> ("line", (a, b)) / ("arc", (c, s, e))
    free = list(centres)
    segs = sorted(uses.items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))
    # arcs first (their centre is the one equidistant from both ends), then circles take the centres left, in order
    for (cls, idx), pids in sorted(segs, key=lambda kv: len(set(kv[1])) == 1):
        if cls == "sgLineHandle" and len(pids) >= 2:
            lines.append((pids[0], pids[1]))
            handles[(cls, idx)] = ("line", (pids[0], pids[1]))
        elif cls == "sgArcHandle" and pids and free:
            s, e = pids[0], pids[-1]
            ps, pe = points[s], points[e]
            # equidistant from both ends; ties (a slot's two centres) go to the nearer centre
            c = free[0] if s == e else min(free, key=lambda c: (round(abs(math.dist(points[c], ps) - math.dist(points[c], pe)), 6),
                                                              math.dist(points[c], ps)))
            free.remove(c)
            arcs.append((c, s, e))
            handles[(cls, idx)] = ("arc", (c, s, e))
        elif cls == "sgSplineHandle" and len(pids) >= 2:
            splines.append([pids[0], *through, pids[-1]])
        elif cls == "sgEllipseHandle" and pids and len(set(pids)) == 1 and len(axis) >= 4 and free:
            ax = [points[a] for a in axis[:4]]  # a full ellipse: centre, axis ends
            mid = (sum(a[0] for a in ax) / 4, sum(a[1] for a in ax) / 4)
            c = min(free, key=lambda c: math.dist(points[c], mid))
            free.remove(c)
            far = max(axis[:4], key=lambda a: math.dist(points[a], points[c]))
            minor = min(math.dist(points[a], points[c]) for a in axis[:4])
            ellipses.append((c, far, minor))
            axis = axis[4:]
    # segments the point references miss (some files write no or only partial references): their own records
    have = {frozenset(ln) for ln in lines} | {frozenset((s_, e_)) for _, s_, e_ in arcs}
    construction = set()
    referenced = bool(lines or arcs or splines or ellipses)
    degree: dict = {}
    for ln in [*lines, *[(s_, e_) for _, s_, e_ in arcs], *[(q_, q_) for q_ in through], *splines]:
        for q_ in ln:
            degree[q_] = degree.get(q_, 0) + 1
    # only for points no decoded segment reaches (records also list construction lines whose flag is not
    # always where it is expected, and splines)
    gaps = []  # record lines that would join a decoded segment's loose end (a candidate the rebuild may try)
    for a, b, kind, cons in _segment_records(buf, start, fpos, points):
        if cons:  # the records say which lines are construction geometry
            construction.add(frozenset((a, b)))
        if (referenced and kind in (4, 5) and not cons and frozenset((a, b)) not in have
                and (degree.get(a) == 1 or degree.get(b) == 1)):
            gaps.append((a, b))
        # (only for sketches whose point references give no segments at all: filling single gaps also turns
        # arcs into lines, since a record does not say which it is)
        if (OPTIONS["fill_from_records"] and not referenced and kind in (4, 5) and frozenset((a, b)) not in have
                and (not degree.get(a) or not degree.get(b))):
            lines.append((a, b))
            have.add(frozenset((a, b)))
            degree[a], degree[b] = degree.get(a, 0) + 1, degree.get(b, 0) + 1
    curves = _bsplines(buf, start, fpos)
    splines = [{"pts": sp, **({"bspline": curves[i]} if i < len(curves) else {})} for i, sp in enumerate(splines)]
    return {"points": points, "lines": lines, "arcs": arcs, "splines": splines, "ellipses": ellipses, "R": R, "t": t,
            "frames": alternatives, "handles": handles, "refmap": dict(refmap),
            "point_order": _point_order(buf, start, fpos), "relations": relations(buf, start, end, state),
            "construction": construction, "gap_lines": gaps}


def parse_sketch3d(buf: bytes, start: int, end: int) -> dict:
    """A 3D sketch (`mo3DProfileFeature_c`): point records as in a 2D sketch but with the tag `0E 00` and x, y, z
    (metres, model coordinates); the reference lists of its points are incomplete, the segment records
    (indices into the point records in file order) give every segment. Returns points (mm) and lines."""
    points, pos = {}, start
    for m in _PT3.finditer(buf, start, end):
        last = None
        for last in _HDR.finditer(buf, pos, m.start()):
            pass
        hdr = (last.start() if last else -1) - 5
        pos = m.end()
        if hdr < 4 or (buf[hdr:hdr + 2] != b"\xff\xff" and buf[hdr:hdr + 4] != bytes(4)):
            continue
        pid = struct.unpack_from("<I", buf, hdr - 4)[0]
        xyz = struct.unpack_from("<ddd", buf, m.end())
        if pid in points or pid >= 0xFFFE or not all(math.isfinite(v) and abs(v) < 1e3 for v in xyz):
            continue
        points[pid] = tuple(v * 1000.0 for v in xyz)
    lines = [(a, b) for a, b, kind, cons in _segment_records(buf, start, end, points, tags=(0x0E,))
             if kind in (4, 5) and not cons]
    return {"points": points, "lines": lines}


def read_sketches3d(path: str, config: int | None = None) -> dict:
    """{3D sketch name: parse_sketch3d(...)} for the part's 3D sketches."""
    from openswx.exchange.sw_tree import _stream, scan
    buf = _stream(path, config)
    objs = scan(buf)
    out = {}
    for i, o in enumerate(objs):
        if o["cls"] == "mo3DProfileFeature_c":
            end = objs[i + 1]["offset"] if i + 1 < len(objs) else len(buf)
            out[o["name"]] = parse_sketch3d(buf, o["name_end"], end)
    return out


# standard plane frames (model = R * sketch): Top (x -> +X, y -> -Z) and Right (x -> -Z, y -> +Y)
TOP = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]]
RIGHT = [[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]]


def read_sketches(path: str, config: int | None = None) -> dict:
    """{sketch name: parse_sketch(...)} for every sketch in the part's feature tree."""
    from openswx.exchange.sw_tree import _stream, scan
    buf = _stream(path, config)
    objs = scan(buf)
    import numpy as np
    out, state = {}, {}
    planes, recent = [], None  # user reference planes so far; one defined since the previous sketch
    for i, o in enumerate(objs):
        end = next((p["offset"] for p in objs[i + 1:] if p["cls"] not in ("moLengthParameter_c", "moAngleParameter_c")),
                   len(buf))
        if o["cls"] == "moRefPlane_c":
            fr = plane_frame(buf, o["name_end"], end)
            if fr is not None and not (np.allclose(fr[0], np.eye(3)) and not np.any(fr[1])):
                planes.append(fr)
                recent = fr
        elif o["cls"] == "moProfileFeature_c" and o["name"] != "Origin":
            sk = parse_sketch(buf, o["name_end"], end, state)
            sk["dimensions"] = dimensions(buf, [p for p in objs[i + 1:] if p["offset"] < end
                                                and p["cls"] in ("moLengthParameter_c", "moAngleParameter_c")])
            # a frame record with flag 0 means "the sketch plane itself": the Front plane or a user plane
            if np.allclose(sk["R"], np.eye(3)) and not np.any(sk["t"]) and not sk["frames"]:
                std = [(np.array(TOP), np.zeros(3)), (np.array(RIGHT), np.zeros(3))]
                if recent is not None:
                    sk["R"], sk["t"] = recent
                    sk["frames"] = [(np.eye(3), np.zeros(3))] + [f for f in planes if f is not recent] + std
                else:
                    sk["frames"] = list(planes) + std
            out[o["name"]] = sk
            recent = None
    return out
