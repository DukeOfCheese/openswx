"""Reader for the SOLIDWORKS 2015+ document container (.sldprt/.sldasm/.slddrw).

Implements the format described in docs/re/README.md: ZIP-like local records (method 8, raw deflate)
with a non-standard signature and nibble-swapped stream names; geometry is Parasolid binary transmit
data inside zlib streams. Every stream is verified against its CRC-32 and size.
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

RECORD = b"\x14\x00\x06\x00\x08\x00"  # version 20, flags 6, method 8


def _swap(b: bytes) -> bytes:
    return bytes(((x & 0x0F) << 4) | (x >> 4) for x in b)


def read_streams(path: str) -> dict[str, bytes]:
    data = Path(path).read_bytes()
    out: dict[str, bytes] = {}
    i = data.find(RECORD)
    while i != -1:
        if i + 30 <= len(data):
            _dos, crc, csize, usize, nlen = struct.unpack_from("<5I", data, i + 6)
            start = i + 26
            if 0 < nlen < 1024 and start + nlen + csize <= len(data):
                name = _swap(data[start:start + nlen])
                if all(32 <= c < 127 for c in name):
                    payload = data[start + nlen:start + nlen + csize]
                    try:
                        raw = zlib.decompressobj(-15).decompress(payload)
                    except zlib.error:
                        raw = None
                    if raw is not None and len(raw) == usize and zlib.crc32(raw) == crc:
                        out[name.decode()] = raw
                        i = data.find(RECORD, start + nlen + csize)
                        continue
        i = data.find(RECORD, i + 1)
    if not out:
        raise ValueError("not a SOLIDWORKS 2015+ container (no valid records found)")
    return out


def _chunks(raw: bytes):
    """Consecutive zlib chunks of a geometry stream as (offset, inflated bytes). Each chunk is preceded by an
    8-byte header (uncompressed size, compressed size); a large transmit file is split over several chunks
    (the first 1 MiB, the rest in 4 KiB pieces)."""
    k = 0
    while True:
        m = re.compile(rb"\x78[\x01\x5e\x9c\xda]").search(raw, k)
        if m is None:
            return
        d = zlib.decompressobj()
        try:
            out = d.decompress(raw[m.start():])
        except zlib.error:
            k = m.start() + 1
            continue
        if not d.eof:
            k = m.start() + 1
            continue
        yield m.start(), out
        k = len(raw) - len(d.unused_data)


def parasolid_blocks(streams: dict[str, bytes]) -> list[dict]:
    """Find zlib-compressed Parasolid transmit data inside the geometry streams: a blob starts at a chunk that
    inflates to a 'PS' transmit header and continues through the following chunks up to the next header."""
    blocks = []
    for name, raw in streams.items():
        if "Partition" not in name and "Bodies" not in name and "Body" not in name:
            continue
        cur = None
        for off, out in _chunks(raw):
            if out.startswith(b"PS") and b"TRANSMIT FILE" in out[:128]:
                cur = {"stream": name, "offset": off, "parts": [out]}
                blocks.append(cur)
            elif cur is not None:
                cur["parts"].append(out)
        for blk in blocks:
            if "parts" not in blk:
                continue
            xt = b"".join(blk.pop("parts"))
            kind = re.search(rb"TRANSMIT FILE(?: \((\w+)\))?", xt[:128])
            schema = re.search(rb"SCH_[0-9_]+", xt[:256])
            version = re.search(rb"modeller version (\d+)", xt[:128])
            blk.update({"size": len(xt), "kind": (kind.group(1) or b"bodies").decode() if kind else "unknown",
                        "schema": schema.group(0).decode() if schema else None,
                        "modeller_version": version.group(1).decode() if version else None, "data": xt})
    return blocks


def _xml_props(raw: bytes) -> dict:
    try:
        root = ET.fromstring(raw.lstrip(b"\x86").decode("utf-8", "replace"))
    except ET.ParseError:
        return {}
    out = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if el.text and el.text.strip() and len(el) == 0:
            name = el.attrib.get("name") or tag
            out[name] = el.text.strip()
        if tag == "property" and "name" in el.attrib:
            vals = [c.text for c in el.iter() if c is not el and c.text]
            if vals:
                out[el.attrib["name"]] = vals[0]
    return out


def read_info(path: str) -> dict:
    streams = read_streams(path)
    info = {"file": str(Path(path).resolve()), "streams": {k: len(v) for k, v in sorted(streams.items())}}
    feat_xml = streams.get("swXmlContents/Features")
    if feat_xml:
        try:
            root = ET.fromstring(feat_xml.lstrip(b"\x86").decode("utf-8", "replace"))
            ns = {"sw": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
            pre = "sw:" if ns else ""
            f = root.find(f".//{pre}swFile", ns)
            info["document_type"] = f.attrib.get("swDocType") if f is not None else None
            info["sw_version"] = root.attrib.get("swVersion")
            info["configurations"] = [c.attrib.get("swName") for c in root.iterfind(f".//{pre}swConfiguration", ns)]
        except ET.ParseError:
            pass
    for key in ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml"):
        if key in streams:
            info.setdefault("properties", {}).update(_xml_props(streams[key]))
    info["has_preview_png"] = "PreviewPNG" in streams
    info["parasolid"] = [{k: v for k, v in b.items() if k != "data"} for b in parasolid_blocks(streams)]
    info["geometry_note"] = ("B-rep geometry is stored as Parasolid binary transmit data (see 'parasolid'), "
                             "read natively by read_sldprt(); `sldinfo --extract` writes it as .x_b files. "
                             "Native feature history is not decoded.")
    return info


def configurations(path: str) -> list[dict]:
    """The configurations of a SOLIDWORKS part that have geometry stored: [{"id", "name", "active"}], in file
    order. (A configuration that was never activated since the last rebuild has no partition of its own and is
    left out: SOLIDWORKS would rebuild it on demand, which needs the feature history.)"""
    streams = read_streams(path)
    have = {int(m.group(1)) for n in streams if (m := re.fullmatch(r"Contents/Config-(\d+)-Partition", n))}
    raw = streams.get("swXmlContents/Features")
    out = []
    if raw:
        text = raw.lstrip(b"\x86").decode("utf-8", "replace")
        for m in re.finditer(r"<swConfiguration [^>]*>", text):
            tag = m.group(0)
            idm, nm = re.search(r'swID="(\d+)"', tag), re.search(r'swName="([^"]*)"', tag)
            if idm and int(idm.group(1)) in have:
                out.append({"id": int(idm.group(1)), "name": nm.group(1) if nm else f"Config {idm.group(1)}",
                            "active": 'swMostRecentConfiguration="YES"' in tag})
    return out


def _active_config(streams: dict) -> int | None:
    """Id of the active configuration (swMostRecentConfiguration in swXmlContents/Features): the one
    SOLIDWORKS opens. Its geometry is Contents/Config-<id>-Partition."""
    raw = streams.get("swXmlContents/Features")
    if not raw:
        return None
    text = raw.lstrip(b"\x86").decode("utf-8", "replace")
    for m in re.finditer(r"<swConfiguration [^>]*>", text):
        tag = m.group(0)
        if 'swMostRecentConfiguration="YES"' in tag:
            idm = re.search(r'swID="(\d+)"', tag)
            return int(idm.group(1)) if idm else None
    return None


def read_sldprt(path: str, config: int | None = None):
    """B-rep geometry of a .sldprt without SOLIDWORKS: (build123d shape in mm, notes). Results are kept in the
    disk cache (openswx/system/cache.py), so a part read before opens at once."""
    from openswx.system import cache
    k = cache.key(path, "sldprt-body", config)
    hit = cache.get_shape("body", k)
    if hit is not None:
        return hit[0], hit[1].get("notes", [])
    shape, notes = _read_sldprt(path, config)
    cache.put_shape("body", k, shape, {"notes": notes})
    return shape, notes


def _read_sldprt(path: str, config: int | None = None):
    """B-rep geometry of a .sldprt without SOLIDWORKS: (build123d shape in mm, notes).

    Reads the Parasolid partition of `Contents/Config-<n>-Partition` (the lowest n unless `config` is
    given) with the clean-room XT reader in parasolid.py. Solid bodies are returned when there are any,
    otherwise sheet bodies; several bodies come back as a Compound."""
    from build123d import Compound, Shape

    from openswx.exchange.parasolid import XTError
    streams = read_streams(path)
    parts = sorted((int(m.group(1)), n) for n in streams if (m := re.fullmatch(r"Contents/Config-(\d+)-Partition", n)))
    if not parts:
        raise ValueError("the document holds no Parasolid partition (is it a part file?)")
    if config is None:
        config = _active_config(streams)  # the configuration SOLIDWORKS opens (last saved active)
    if config is not None:
        parts = [p for p in parts if p[0] == config] or parts
    n_cfg, name = parts[0]
    notes = []
    # the partition normally holds the bodies; some parts (e.g. plastics studies) keep an empty partition and
    # store them as plain body transmit files in Config-<n>-FeatureBodies/LocalBodies
    local = f"Config-{n_cfg}-FeatureBodies/LocalBodies"
    sources = [(name, "partition")] + ([(local, "bodies")] if local in streams else [])
    for stream, kind in sources:
        found = []
        for blk in parasolid_blocks({stream: streams[stream]}):
            if blk["kind"] != kind:
                continue  # rollback deltas
            try:
                model, b, built = _build(path, stream, blk)
            except XTError as exc:
                notes.append(f"{stream}: {exc}")
                continue
            bodies = [(model.nodes[i][1]["body_type"], s) for i, s in zip(b.body_ids(), built)]
            chosen = [Shape.cast(s) for t, s in bodies if t == 1] or [Shape.cast(s) for t, s in bodies if t in (3, 6)]
            found += [s for s in chosen if s.faces()]
            notes += b.warnings
            if found and kind == "partition":
                break
        if found:
            return (found[0] if len(found) == 1 else Compound(found)), notes
    raise ValueError(f"no bodies found in {name}" + (f" ({'; '.join(notes)})" if notes else ""))


_BUILD_CACHE: dict = {}


def _build(path: str, stream: str, blk: dict):
    """Parsed transmit model, its builder and its rebuilt bodies, cached per file version and blob (opening
    an assembly reads each part's geometry and then its face ids from the same rebuild)."""
    from pathlib import Path as _P
    from openswx.exchange.parasolid import _Builder, read_xt_bytes
    p = _P(path)
    key = (str(p.resolve()), p.stat().st_mtime_ns, stream, blk["offset"])
    hit = _BUILD_CACHE.get(key)
    if hit is None:
        model = read_xt_bytes(blk["data"])
        b = _Builder(model)
        hit = (model, b, b.bodies())
        if len(_BUILD_CACHE) > 32:
            _BUILD_CACHE.clear()
        _BUILD_CACHE[key] = hit
    return hit


def part_face_ids(path: str, config: int | None = None) -> list[dict]:
    """SOLIDWORKS face ids of a part (FACE_ID_2001 attributes in its Parasolid partition), what assembly
    mate references resolve against. The geometry of an entry (centre, normal, surface type in part
    coordinates, mm) is worked out on demand by face_geometry(), since mates name only a few faces."""
    from openswx.exchange.parasolid import face_attributes
    streams = read_streams(path)
    parts = sorted((int(m.group(1)), n) for n in streams if (m := re.fullmatch(r"Contents/Config-(\d+)-Partition", n)))
    if config is None:
        config = _active_config(streams)
    if config is not None:
        parts = [q for q in parts if q[0] == config] or parts
    for blk in parasolid_blocks({parts[0][1]: streams[parts[0][1]]}) if parts else []:
        if blk["kind"] != "partition":
            continue
        model, b, built = _build(path, parts[0][1], blk)
        ctx = {"model": model, "builder": b, "built": built}
        return [{"ids": ids, "_ctx": ctx, "_idx": idx} for idx, ids in face_attributes(model).items()]
    return []


def face_geometry(entry: dict) -> dict | None:
    """Centre, normal and surface type (part coordinates, mm) of a part_face_ids entry: the XT face is paired
    with the repaired face lying on its surface (a raw face has no pcurves yet); candidates are ranked by how
    well their vertices match first, so only a few face centres (surface integrals) are computed."""
    if "center" in entry:
        return entry
    from build123d import Face, Shape
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    ctx, idx = entry["_ctx"], entry["_idx"]
    model, b = ctx["model"], ctx["builder"]
    if "fixed" not in ctx:
        ctx["fixed"] = [f for s in ctx["built"] for f in Shape.cast(s).faces()]
        ctx["fverts"] = [f.vertices() for f in ctx["fixed"]]
        ctx["centres"] = {}
    try:
        geo = b.surface(model.nodes[idx][1]["surface"])
        verts = list(Face(b.face(idx)).vertices())
    except Exception:
        return None
    score = lambda fv: sum(min((abs(v.X - w.X) + abs(v.Y - w.Y) + abs(v.Z - w.Z) for w in fv), default=1e9)  # noqa: E731
                           for v in verts)
    ranked = sorted(range(len(ctx["fixed"])), key=lambda k: score(ctx["fverts"][k]))
    for k in ranked[:6] if verts else ranked:
        f = ctx["fixed"][k]
        if k not in ctx["centres"]:
            ctx["centres"][k] = f.center()
        c = ctx["centres"][k]
        pr = GeomAPI_ProjectPointOnSurf(b._pnt([c.X / 1000, c.Y / 1000, c.Z / 1000]), geo) if geo is not None else None
        if pr is None or not pr.NbPoints() or pr.LowerDistance() > 1e-5:
            continue
        n = f.normal_at(c) if f.geom_type.name == "PLANE" else f.normal_at()
        entry.update({"center": (c.X, c.Y, c.Z), "normal": (n.X, n.Y, n.Z), "type": f.geom_type.name})
        return entry
    return None


def match_face_ref(faces: list[dict], numbers: list[int]) -> dict | None:
    """The face a mate reference 'kind,F,a,b,...' names: its id holds a record for feature F followed (after
    the time stamp and 0) by a, b, ... When several faces carry that record (a shell copies the faces it
    offsets), the one with the shortest id, the original face, wins."""
    if not numbers:
        return None
    F, rest = numbers[0], numbers[1:]
    hits = []
    for f in faces:
        ids = f["ids"]
        for i in range(1, len(ids) - 2):
            if ids[i] == F and ids[i + 2] == 0 and ids[i + 3:i + 3 + len(rest)] == rest:
                hits.append(f)
                break
    return min(hits, key=lambda f: len(f["ids"])) if hits else None


_MATE_TYPES = {"moMateCoincident": "coincident", "moMateConcentric": "concentric", "moMateParallel": "parallel",
               "moMatePerpendicular": "perpendicular", "moMateTangent": "tangent", "moMateDistanceDim_c": "distance",
               "moMatePlanarAngleDim_c": "angle", "moMateLock_c": "lock", "moMateWidth": "width"}


# SOLIDWORKS' default mate names -> mate class (longest prefixes first)
_NAME_CLASS = [("LimitDistance", "moMateLimitDistanceDim_c"), ("Perpendicular", "moMatePerpendicular"),
               ("Coincident", "moMateCoincident"), ("Concentric", "moMateConcentric"), ("Parallel", "moMateParallel"),
               ("Distance", "moMateDistanceDim_c"), ("Tangent", "moMateTangent"), ("Angle", "moMatePlanarAngleDim_c"),
               ("Width", "moMateWidth"), ("Lock", "moMateLock_c")]


def assembly_mates(path: str, config: int = 0) -> list[dict]:
    """Mates of a .sldasm from Contents/Config-<n>-MatesList: type, name and, per mated entity, the face
    reference (kind and numbers) and component name. Values are not decoded: the saved positions satisfy
    the mates, so they are measured from the geometry instead."""
    raw = read_streams(path).get(f"Contents/Config-{config}-MatesList", b"")
    pat = re.compile(rb"\xff\xff\x01\x00..([A-Za-z_][A-Za-z0-9_]+)|((?:[\x20-\x7e]\x00){3,})", re.S)
    mates, cur, pending, skip, entity = [], None, None, 0, "face"
    for m in pat.finditer(raw):
        if m.group(1):
            cls = m.group(1).decode()
            if cls.startswith("moMate") and not cls.endswith(("Data_c", "HelpData_c")):
                cur = {"class": cls, "type": _MATE_TYPES.get(cls), "name": None, "refs": [], "_at": m.start()}
                mates.append(cur)
                pending = None
            elif "Ref" in cls and not cls.endswith("Data_c"):  # the kind of the entity the next reference names
                entity = "line" if "Line" in cls else "point" if ("Point" in cls or "SketchEnt" in cls) else "face"
            continue
        if cur is None:
            continue
        text = m.group(2).decode("utf-16-le")
        if ":" + chr(92) in text or text.lower().endswith((".sldprt", ".sldasm")):
            skip = 2  # a referenced document, followed by its name and configuration
            continue
        if skip:
            skip -= 1
            continue
        if text.startswith("mo") and "," in text:  # face reference; its component follows
            kind, *nums = [x for x in text.split(",") if x.strip()]
            try:
                pending = {"kind": kind, "numbers": [int(x) for x in nums], "entity": entity}
            except ValueError:
                pending = None
        elif "@" in text:
            parts = text.split("@")
            if pending is not None:
                cur["refs"].append({**pending, "component": parts[0]})
                pending, entity = None, "face"
            elif len(parts) == 3:  # a component's reference plane / origin: 'Front@block-1@asm'
                cur["refs"].append({"kind": "refgeom", "numbers": [], "name": parts[0], "component": parts[1]})
            # 'comp-1@asm' alone introduces the component of a reference geometry entity that follows
        elif cur["name"] is None:
            cur["name"] = text
        else:  # a class name is written only once: a later mate starts with just its name, so its class is
            # taken from SOLIDWORKS' default mate name when it has one, else from the previous mate
            cls = next((c for prefix, c in _NAME_CLASS if text.startswith(prefix) and text[len(prefix):].isdigit()),
                       cur["class"])
            cur = {"class": cls, "type": _MATE_TYPES.get(cls), "name": text, "refs": [], "_at": m.start()}
            mates.append(cur)
            pending = None
    # the numbers in each mate's record (its value is among them: metres / radians; which one is not decoded)
    import numpy as np
    for i, mt in enumerate(mates):
        a, b = mt.pop("_at"), (mates[i + 1]["_at"] if i + 1 < len(mates) else len(raw))
        vals = set()
        for k in range(8):
            chunk = raw[a + k:b]
            arr = np.frombuffer(chunk[:len(chunk) // 8 * 8], "<f8")
            with np.errstate(invalid="ignore", over="ignore"):
                ok = np.isfinite(arr) & (np.abs(arr) > 1e-7) & (np.abs(arr) < 10.0)
            vals.update(float(v) for v in arr[ok])
        mt["numbers"] = sorted(vals)
    # two 2-entity mates whose boundary was missed come out as one mate with four references: split them
    # (the first pair keeps the tagged class, the second takes the class its default name implies)
    out = []
    for m in mates:
        if len(m["refs"]) == 4 and m["type"] not in ("width",):
            name_cls = next((c for prefix, c in _NAME_CLASS if (m["name"] or "").startswith(prefix)), m["class"])
            out.append({**m, "name": None, "refs": m["refs"][:2]})
            out.append({**m, "class": name_cls, "type": _MATE_TYPES.get(name_cls), "refs": m["refs"][2:]})
        else:
            out.append(m)
    mates = out
    return mates


def _transform(text: str, row_major: bool = True):
    """Component transform from swTransform: a row-major 4x4 matrix, 16 numbers in metres. Rows 1-3 are the
    component's X, Y and Z axes in parent coordinates, each followed by 0; row 4 is the translation and 1.
    Returns (R mapping component -> parent coordinates, t in mm). The row layout reproduces the stored assembly
    bounding boxes exactly on the SOLIDWORKS sample assemblies; reading the axes as columns is off by up to
    87 mm."""
    import numpy as np
    v = [float(x) for x in text.split()]
    if len(v) < 15:
        return np.eye(3), np.zeros(3)
    m = np.array(v[:12]).reshape(3, 4)[:, :3]  # rows = component axes
    R = m.T if row_major else m
    t = np.array(v[12:15]) * 1000.0
    return R, t


def assembly_tree(path: str, _depth: int = 0) -> dict:
    """Structure of a .sldasm from its published XML summary (swXmlContents/COMPINSTANCETREE): component
    names, the part/assembly file and configuration each one uses, and its transform, flattened through
    subassemblies to world coordinates (mm). Paths are resolved next to the assembly when the stored
    absolute path does not exist."""
    import numpy as np
    streams = read_streams(path)
    raw = streams.get("swXmlContents/COMPINSTANCETREE")
    if not raw:
        raise ValueError("the document has no component tree (is it an assembly?)")
    root = ET.fromstring(raw.lstrip(b"\x86").decode("utf-8", "replace"))
    tag = lambda el: el.tag.split("}")[-1]  # noqa: E731
    files = {el.get("id"): el for el in root.iter() if tag(el) == "swFile"}
    models = {el.get("id"): el for el in root.iter() if tag(el) == "swModel"}
    here = Path(path).resolve()
    top = next((m for m in models.values()
                if files.get(m.get("swFileRef")) is not None and files[m.get("swFileRef")].get("swDocType") == "ASSEMBLY"
                and Path(files[m.get("swFileRef")].get("swPath", "")).name.lower() == here.name.lower()), None)
    if top is None:
        top = next((m for m in models.values() if any(tag(c) == "swReference" for c in m)), None)
    if top is None:
        raise ValueError("no top-level assembly model in the component tree")

    def resolve(stored: str) -> str | None:
        p = Path(stored)
        if p.exists():
            return str(p)
        name = Path(stored.replace("\\", "/")).name
        for cand in [here.parent / name, *here.parent.rglob(name)]:
            if cand.exists():
                return str(cand)
        return None

    out = []
    depth = _depth

    def walk(model, R0, t0, prefix):
        for ref in model:
            if tag(ref) != "swReference":
                continue
            sub = models.get(ref.get("swModelRef"))
            fil = files.get(sub.get("swFileRef")) if sub is not None else None
            R, t = _transform(ref.get("swTransform", ""))
            Rw, tw = R0 @ R, R0 @ t + t0
            name = prefix + ref.get("swComponentName", ref.get("swName", "?")) + "-" + ref.get("swReferenceNumber", "1")
            entry = {"name": name, "suppressed": ref.get("swSuppressed") == "YES", "hidden": ref.get("swHidden") == "YES",
                     "configuration": ref.get("swConfigurationName"), "configuration_id": int(ref.get("swConfigurationId", "0") or 0),
                     "stored_path": fil.get("swPath") if fil is not None else None,
                     "path": resolve(fil.get("swPath")) if fil is not None else None,
                     "type": fil.get("swDocType") if fil is not None else None,
                     "rotation": Rw.tolist(), "translation": tw.tolist()}
            if sub is not None and any(tag(c) == "swReference" for c in sub):
                walk(sub, Rw, tw, name + "/")  # a subassembly: its own references follow, in world coordinates
            elif entry["type"] == "ASSEMBLY" and entry["path"] and depth < 8:
                for c in assembly_tree(entry["path"], _depth=depth + 1)["components"]:  # subassembly in its own file
                    Rc, tc = np.array(c["rotation"]), np.array(c["translation"])
                    out.append({**c, "name": name + "/" + c["name"], "rotation": (Rw @ Rc).tolist(),
                                "translation": (Rw @ tc + tw).tolist(),
                                "suppressed": c["suppressed"] or entry["suppressed"]})
            else:
                out.append(entry)

    walk(top, np.eye(3), np.zeros(3), "")
    return {"assembly": top.get("swName"), "components": out}


def extract(path: str, outdir: str) -> list[str]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    streams = read_streams(path)
    written = []
    for name, raw in streams.items():
        dst = out / "streams" / name.replace("/", "_")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(raw)
        written.append(str(dst))
    if "PreviewPNG" in streams:
        (out / "preview.png").write_bytes(streams["PreviewPNG"]); written.append(str(out / "preview.png"))
    for k, b in enumerate(parasolid_blocks(streams)):
        dst = out / f"parasolid_{k}_{b['kind']}.x_b"
        dst.write_bytes(b["data"]); written.append(str(dst))
    return written
