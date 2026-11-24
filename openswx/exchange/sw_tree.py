from __future__ import annotations

import re
import struct

_NEWCLASS = re.compile(rb"\xff\xff\x01\x00..([A-Za-z_][A-Za-z0-9_]+)$", re.S)
_OBJ = re.compile(rb"\x04\x80\xff\xfe\xff(.)", re.S)

# SOLIDWORKS default feature names -> class (used for objects written without a class tag)
_NAME_CLASS = [
    (r"Sketch\d+", "moProfileFeature_c"), (r"3DSketch\d+", "moProfileFeature_c"),
    (r"Boss-Extrude\d+", "moExtrusion_c"), (r"Cut-Extrude\d+", "moExtrusion_c"),
    (r"Extrude\d+", "moICE_c"),  # (older default name, boss or cut)
    (r"(Base-|Boss-)?Revolve\d*", "moRevolution_c"), (r"Cut-Revolve\d+", "moRevolution_c"),
    (r"Base-Extrude\d*", "moExtrusion_c"),
    (r"(Base-|Boss-)?Sweep\d*", "moSweep_c"), (r"Cut-Sweep\d+", "moSweepCut_c"),
    (r"Fillet\d+", "moFillet_c"), (r"Chamfer\d+", "moChamfer_c"), (r"Shell\d+", "moShell_c"),
    (r"Plane\d+", "moRefPlane_c"), (r"Axis\d+", "moRefAxis_c"), (r"D\d+", "moLengthParameter_c"),
    (r"(Front|Top|Right)( Plane)?", "moRefPlane_c"),
]
_FOLDERS = {"Favorites", "History", "Selection Sets", "Sensors", "Design Binder", "Annotations", "Notes", "Markups",
            "Solid Bodies", "Surface Bodies", "Comments", "Equations", "Tables", "Configuration Table", "Origin"}
PARAM_CLASSES = ("moLengthParameter_c", "moAngleParameter_c", "moIntegerParameter_c")


def _stream(path: str, config: int | None = None) -> bytes:
    from openswx.exchange.sldprt import _active_config, read_streams
    streams = read_streams(path)
    if config is None:
        config = _active_config(streams)
    names = sorted((int(m.group(1)), n) for n in streams if (m := re.fullmatch(r"Contents/Config-(\d+)-ResolvedFeatures", n)))
    if not names:
        raise ValueError("the document has no feature tree stream")
    pick = [n for i, n in names if i == config] or [names[0][1]]
    return streams[pick[0]]


def _object_word(buf: bytes) -> bytes:
    """The class word that precedes every tree object's name (`04 80` in current files, `05 80` in some older
    ones): the word right after the first class tag that is followed by a name."""
    m = re.search(rb"\xff\xff\x01\x00..[A-Za-z_][A-Za-z0-9_]+?_c([\x01-\x7f]\x80)\xff\xfe\xff", buf[:4096], re.S)
    return m.group(1) if m else b"\x04\x80"


def scan(buf: bytes) -> list[dict]:
    """Tree objects in stream order: name, class (from its tag, or None with the class-reference word)."""
    out = []
    word = _object_word(buf)
    pattern = _OBJ if word == b"\x04\x80" else re.compile(re.escape(word) + rb"\xff\xfe\xff(.)", re.S)
    for m in pattern.finditer(buf):
        n = m.group(1)[0]
        end = m.end() + 2 * n
        try:
            name = buf[m.end():end].decode("utf-16-le")
        except UnicodeDecodeError:
            continue
        if not name or any(ord(c) < 32 for c in name):
            continue
        tag = _NEWCLASS.search(buf[max(0, m.start() - 80):m.start()])
        ref = struct.unpack_from("<H", buf, m.start() - 2)[0] if m.start() >= 2 else 0
        out.append({"name": name, "offset": m.start(), "name_end": end,
                    "cls": tag.group(1).decode() if tag else None,
                    "ref": ref if (tag is None and ref & 0x8000) else None})
    # objects written without a tag: same class as others referenced by the same word, else by default name
    by_ref: dict[int, str] = {}
    for o in out:
        if o["cls"] is None:
            guess = next((c for pat, c in _NAME_CLASS if re.fullmatch(pat, o["name"])), None)
            if guess and o["ref"] is not None:
                by_ref.setdefault(o["ref"], guess)
    for o in out:
        if o["cls"] is None:
            o["cls"] = by_ref.get(o["ref"]) or next((c for pat, c in _NAME_CLASS if re.fullmatch(pat, o["name"])), None)
    # objects with their own names and no tag: by what they hold. A sketch holds point records; an object right
    # after a sketch that holds an end spec is an extrude (boss or cut)
    for i, o in enumerate(out):
        if o["cls"] is not None or o["name"] in _FOLDERS:
            continue
        end = out[i + 1]["offset"] if i + 1 < len(out) else len(buf)
        if len(_POINT.findall(buf, o["name_end"], end)) >= 2:
            o["cls"] = by_ref.setdefault(o["ref"], "moProfileFeature_c") if o["ref"] is not None else "moProfileFeature_c"
        elif i and out[i - 1]["cls"] == "moProfileFeature_c" and _end_spec(buf, o["name_end"], end)[0] is not None:
            o["cls"] = "moICE_c"
    return out


_POINT = re.compile(rb"\x00\x00\xf0\x3f\x00{8}[\x10-\x1f]\x00")


_ENDSPEC = re.compile(rb"\x00\x00\x01\x00[\x00\x01]{12}([\x00-\x20])\x00\x00\x00", re.S)


def _end_spec(buf: bytes, start: int, end: int) -> tuple[int | None, bool | None]:
    """(swEndConditions_e, reversed) of the first end spec in buf[start:end]. An end spec (`moEndSpec_c`, or
    its class word once the class is known) is `00 00 01 00`, 12 bytes of 0/1 flags (the 7th: the direction
    is reversed), then the condition as a u32."""
    tag = buf.find(b"moEndSpec_c", start, end)
    if tag >= 0:
        m = _ENDSPEC.match(buf, tag + 11)
    else:
        m = next((m for m in _ENDSPEC.finditer(buf, start, end)
                  if m.start() >= 2 and buf[m.start() - 1] & 0x80), None)  # preceded by a class word
    if m is None:
        return None, None
    return m.group(1)[0], buf[m.start() + 10] == 1


def _end_condition(buf: bytes, start: int, end: int) -> int | None:
    return _end_spec(buf, start, end)[0]


def read_tree(path: str, config: int | None = None) -> list[dict]:
    """Feature tree of a part: [{name, cls, offset, id, end_condition, params: [{name, value, kind}]}] in tree
    order. Folders are left out; each dimension parameter is attached to the feature it follows; id is the
    feature id the Parasolid faces carry (FACE_ID_2001), a u32 8 bytes after the name."""
    buf = _stream(path, config)
    feats: list[dict] = []
    objs = scan(buf)
    for i, o in enumerate(objs):
        if o["cls"] in PARAM_CLASSES:
            if feats and o["name_end"] + 30 <= len(buf):
                v = struct.unpack_from("<d", buf, o["name_end"] + 22)[0]
                kind = "angle" if o["cls"] == "moAngleParameter_c" else "length"
                if o["cls"] == "moIntegerParameter_c" or (v != 0 and abs(v) < 1e-30):  # a u32 count, not a double
                    v, kind = struct.unpack_from("<I", buf, o["name_end"] + 22)[0], "integer"
                feats[-1]["params"].append({"name": o["name"], "value": v, "kind": kind})
            continue
        if o["name"] in _FOLDERS or o["name"].endswith("___EndTag___") or o["name"].startswith("Material <"):
            continue
        if o["cls"] and o["cls"].endswith("Folder_c"):
            continue
        end = next((p["offset"] for p in objs[i + 1:] if p["cls"] not in PARAM_CLASSES), len(buf))
        fid = struct.unpack_from("<I", buf, o["name_end"] + 8)[0] if o["name_end"] + 12 <= len(buf) else None
        feats.append({"name": o["name"], "cls": o["cls"], "offset": o["offset"], "id": fid,
                      **dict(zip(("end_condition", "reverse"), _end_spec(buf, o["name_end"], end)
                                 if o["cls"] in ("moExtrusion_c", "moICE_c", "moBoss_c", "moCut_c") else (None, None))),
                      "params": []})
    return feats
