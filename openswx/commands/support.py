"""Helpers the command modules share: selection specs, foreign-file readers, drawing and simulation edits."""
from __future__ import annotations

import math
import re
from pathlib import Path

from openswx.core.document import DocumentError, PartDocument
from openswx.core.features import encode_shape
from openswx.core.geom import edge_ref, face_ref


def _face(doc: PartDocument, sel) -> dict:
    """Accept 'Face3' (from get_topology) or a reference dict."""
    if isinstance(sel, dict):
        return sel
    if doc.body is None:
        raise DocumentError("the part has no body yet")
    faces = doc.body.faces()
    if not re.fullmatch(r"(Face)?\d+", str(sel)):  # a persistent name from get_topology ('Boss-Extrude1:cap+')
        from openswx.core.naming import find_face
        hit = find_face(doc.body, str(sel))
        if hit is None:
            raise DocumentError(f"unknown face {sel!r}; call get_topology for current face ids and names")
        return face_ref(hit, doc.body)
    try:
        return face_ref(faces[int(str(sel).replace("Face", ""))], doc.body)
    except (ValueError, IndexError):
        raise DocumentError(f"unknown face {sel!r}; call get_topology for current face ids") from None


def _edge(doc: PartDocument, sel) -> dict:
    if isinstance(sel, dict):
        return sel
    if doc.body is None:
        raise DocumentError("the part has no body yet")
    edges = doc.body.edges()
    if "|" in str(sel):  # a persistent name from get_topology: the two faces the edge lies between
        from openswx.core.naming import find_edge
        hit = find_edge(doc.body, str(sel).split("|"))
        if hit is None:
            raise DocumentError(f"unknown edge {sel!r}; call get_topology for current edge ids and names")
        return edge_ref(hit, doc.body)
    try:
        return edge_ref(edges[int(str(sel).replace("Edge", ""))], doc.body)
    except (ValueError, IndexError):
        raise DocumentError(f"unknown edge {sel!r}; call get_topology for current edge ids") from None


def _is_edge_sel(s) -> bool:
    """'Edge7', or an edge's persistent name ('faceA|faceB')."""
    return isinstance(s, str) and (s.startswith("Edge") or "|" in s)


def _is_face_sel(s) -> bool:
    """'Face3', or a face's persistent name ('Boss-Extrude1:cap+': feature, colon, what made the face)."""
    return isinstance(s, str) and not _is_edge_sel(s) and (s.startswith("Face") or ":" in s)


def _plane_spec(doc: PartDocument, plane):
    if _is_face_sel(plane):
        return _face(doc, plane)
    return plane


def _axis_spec(doc: PartDocument, axis):
    if _is_edge_sel(axis):
        return _edge(doc, axis)
    if _is_face_sel(axis):
        return _face(doc, axis)
    return axis


def _dim_id(dim: str) -> tuple[str | None, str]:
    if "@" in dim:
        d, sk = dim.split("@", 1)
        return sk, d
    return None, dim


def _sw_properties(doc, info: dict) -> None:
    """Carry a SOLIDWORKS file's summary and custom properties (docProps) over as custom properties, without
    replacing any the document already has. Who last saved the file is left out."""
    skip = {"lastmodifiedby", "last saved by", "revision", "totaltime"}
    for k, v in (info.get("properties") or {}).items():
        if isinstance(v, (str, int, float)) and str(v).strip() and k.lower() not in skip and k not in doc.properties:
            doc.properties[str(k)] = str(v)
    doc.dirty = False


def _open_sldasm_native(p: Path):
    """A SOLIDWORKS assembly as an openswx assembly without SOLIDWORKS: the component tree from the file's
    XML summary, each part's B-rep from its embedded Parasolid data (the component's configuration), placed
    at its saved position. Mates are not decoded, so every component comes in fixed."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    from openswx.core.assembly import AssemblyDocument
    from openswx.exchange.sldprt import assembly_tree
    tree = assembly_tree(str(p))
    asm = AssemblyDocument(p.stem)
    asm.live_docs = APP.documents
    parts, notes, placed = {}, [], {}
    for c in tree["components"]:
        if c["suppressed"]:
            continue
        if c["type"] != "PART" or not c["path"]:
            notes.append(f"{c['name']}: {'file not found: ' + str(c['stored_path']) if not c['path'] else 'not a part'}")
            continue
        key = (c["path"], c["configuration_id"])
        if key not in parts:
            part = PartDocument(Path(c["path"]).stem)
            try:
                _import_file(part, c["path"], c["configuration_id"])
            except Exception as exc:
                notes.append(f"{c['name']}: {exc}")
                parts[key] = None
                continue
            parts[key] = part
        if parts[key] is None:
            continue
        R = np.array(c["rotation"])
        comp = asm.insert(parts[key], None, [float(v) for v in c["translation"]], [0, 0, 0], True)
        comp.x[:3] = Rotation.from_matrix(R).as_rotvec()  # direct: Euler angles lose information at gimbal lock
        comp.name = c["name"]  # keep the SOLIDWORKS component name (mates refer to it)
        placed[c["name"]] = (key, R, np.array(c["translation"]))
    if not asm.components:
        raise DocumentError("no component could be read" + (f" ({'; '.join(notes[:3])})" if notes else ""))
    notes += _import_sldasm_mates(asm, p, placed)
    APP.documents[asm.name] = asm
    APP.active = asm.name
    return asm, notes


def _import_sldasm_mates(asm, p: Path, placed: dict) -> list[str]:
    """Rebuild the assembly's mates. A mated entity is named in the file by a face (its SOLIDWORKS face id:
    FACE_ID_2001 attributes in the part's Parasolid data, matched to the component's face by position), a
    component reference plane / origin, or nothing (the assembly's own planes / origin). Mates on edges and
    vertices name a face too, so the face's straight edges and vertices are candidates as well; the saved
    positions satisfy every mate, so the first candidate pair (in the order the file's entity hint suggests)
    on which the mate holds there is taken. Distance / angle values are measured there; for edges and vertices
    the value must be one of the numbers in the mate's record (several edges of a face can hold a distance).
    Mated components are then freed (all but the first) unless solving would move anything."""
    import itertools
    import math
    import numpy as np
    from openswx.core.assembly import ASSEMBLY, _mate_residual, plane_ref
    from openswx.core.geom import edge_ref, face_ref
    from openswx.exchange.sldprt import assembly_mates, face_geometry, match_face_ref, part_face_ids
    try:
        mates = assembly_mates(str(p))
    except Exception as exc:
        return [f"mates not read: {exc}"]
    notes, ids_cache, done = [], {}, 0
    frame = asm.component(ASSEMBLY)
    SUPPORTED = ("coincident", "concentric", "parallel", "perpendicular", "distance", "angle", "tangent")

    def local_face(ref):
        key, R, t = placed[ref["component"]]
        if key not in ids_cache:
            ids_cache[key] = part_face_ids(*key)
        hit = match_face_ref(ids_cache[key], ref["numbers"])
        hit = face_geometry(hit) if hit is not None else None
        if hit is None:
            return None
        c0 = np.array(hit["center"])
        best, bd = None, 1e-3
        for f in asm._local_body(asm.component(ref["component"])).faces():
            fc = f.center()
            d = float(np.linalg.norm(np.array([fc.X, fc.Y, fc.Z]) - c0))
            if d < bd:
                best, bd = f, d
        return best

    def origin(comp):
        return ({"component": comp.name, "vertex": [0.0, 0.0, 0.0]}, (comp, "vertex", np.zeros(3), np.zeros(3), 0.0))

    def std_plane(comp, nm):
        pr = plane_ref(nm)
        if pr is None:
            return None
        return ({"component": comp.name, "plane": pr}, (comp, "plane", np.array(pr["point"], float), np.array(pr["normal"], float), 0.0))

    def candidates(ref):
        """(mate entity reference, entity as the solver sees it) for what a SOLIDWORKS reference may name."""
        name = ref["component"]
        if name not in placed:
            return []
        comp = asm.component(name)
        if ref["kind"] == "refgeom":
            nm = ref.get("name", "")
            if nm.lower().startswith("origin"):
                return [origin(comp)]
            got = std_plane(comp, nm)
            return [got] if got else []
        face = local_face(ref)
        if face is None:
            return []
        body = asm._local_body(comp)
        out = {"face": [], "line": [], "point": []}
        fref = {"component": name, "face": face_ref(face, body)}
        try:
            out["face"].append((fref, asm._resolve_entity(comp, fref)))
        except Exception:  # (a face that is neither planar nor cylindrical)
            pass
        seen = []
        for e in face.edges():
            if e.geom_type.name.lower() == "line":
                q, d = e.position_at(0.5), e.tangent_at(0.5)
                out["line"].append(({"component": name, "line": edge_ref(e, body)},
                                    (comp, "line", np.array([q.X, q.Y, q.Z]), np.array([d.X, d.Y, d.Z]), 0.0)))
            for v in e.vertices():
                pt = np.array([v.X, v.Y, v.Z])
                if not any(np.allclose(pt, s, atol=1e-9) for s in seen):
                    seen.append(pt)
                    out["point"].append(({"component": name, "vertex": [float(x) for x in pt]}, (comp, "vertex", pt, np.zeros(3), 0.0)))
        hint = ref.get("entity", "face")
        return [c for k in [hint] + [k for k in ("face", "line", "point") if k != hint] for c in out[k]]

    assembly_side = [std_plane(frame, n) for n in ("Front", "Top", "Right")] + [origin(frame)]

    def listed(v, numbers, scale):  # a measured value among the record's numbers
        return any(abs(abs(v) * scale - abs(n)) <= 1e-9 * max(1.0, abs(n)) for n in numbers)

    def fit(typ, ca, cb, numbers):
        """(type, value, aligned, entity a, entity b, value confirmed) when the mate holds, else None."""
        (_ra, ea), (_rb, eb) = ca, cb
        Pa, Na = ea[0].to_world(ea[2]), ea[0].dir_to_world(ea[3])
        Pb, Nb = eb[0].to_world(eb[2]), eb[0].dir_to_world(eb[3])
        faces_only = {ea[1], eb[1]} <= {"plane", "axis"}
        value, aligned, sure = None, bool(np.dot(Na, Nb) > 0), True
        if faces_only:
            if typ == "coincident" and ea[1] == eb[1] == "axis":
                typ = "concentric"  # coincident cylindrical faces: same axis (and radius)
            if typ in ("coincident", "distance", "parallel") and ea[1] == eb[1] == "plane" and abs(float(np.dot(Na, Nb))) < 1 - 1e-6:
                return None  # (non-parallel faces: the mate is on an edge or vertex of them)
            if typ == "distance":
                if ea[1] != "plane" or eb[1] != "plane":
                    return None
                # openswx measures the signed gap along entity1's normal; an aligned pair is swapped to keep it positive
                value = float(np.dot(Pb - Pa, Na))
                if value < 0 and aligned:
                    (ca, ea, Pa, Na), (cb, eb, Pb, Nb) = (cb, eb, Pb, Nb), (ca, ea, Pa, Na)
                    value = float(np.dot(Pb - Pa, Na))
                sure = not numbers or listed(value, numbers, 1e-3)
            elif typ == "angle":  # openswx compares cos(angle) with n1 . n2 (signed)
                if ea[1] != "plane" or eb[1] != "plane":
                    return None
                value = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(Na, Nb))))))
                sure = (not numbers or listed(math.radians(value), numbers, 1.0)
                        or listed(math.pi - math.radians(value), numbers, 1.0))
        else:
            if typ not in ("coincident", "distance", "parallel", "perpendicular"):
                return None
            if typ == "distance":
                try:
                    r = _mate_residual({"type": "distance", "value": 0.0}, ea, eb, ea[0].x, eb[0].x)
                except Exception:
                    return None
                value = float(r[-1])
                if abs(value) < 1e-9 or not listed(value, numbers, 1e-3):
                    return None  # (which edge / vertex is meant shows only through the value)
        mate = {"type": typ, "value": value, "aligned": aligned}
        try:
            if max(abs(v) for v in _mate_residual(mate, ea, eb, ea[0].x, eb[0].x)) > 1e-6:
                return None
        except Exception:
            return None
        return typ, value, aligned, ca, cb, sure

    mated, anchored = set(), set()
    expanded = []
    for m in mates:  # a mate on more than two faces (coincident / parallel / concentric): each with the first
        if m["type"] in ("coincident", "parallel", "concentric") and len(m["refs"]) > 2:
            expanded += [{**m, "name": f"{m['name']}.{k}" if k > 1 else m["name"], "refs": [m["refs"][0], r]}
                         for k, r in enumerate(m["refs"][1:], 1)]
        else:
            expanded.append(m)
    for m in expanded:
        typ = m["type"]
        if typ not in SUPPORTED or not 1 <= len(m["refs"]) <= 2:
            notes.append(f"mate {m['name']} ({m['class']}) not imported")
            continue
        sides = [candidates(r) for r in m["refs"]]
        if len(sides) == 1:  # the other entity is the assembly's own plane / origin (origin to origin first)
            sides.append(assembly_side[::-1] if sides[0][0][1][1] == "vertex" else assembly_side)
        if not sides[0] or not sides[1]:
            notes.append(f"mate {m['name']}: a mated face could not be identified")
            continue
        best = None
        for ca, cb in itertools.islice(itertools.product(*sides), 20000):
            got = fit(typ, ca, cb, m.get("numbers", []))
            if got and got[5]:
                best = got
                break
            best = best or got
        if best is None:
            if len(m["refs"]) == 1:
                anchored.add(m["refs"][0]["component"])
                notes.append(f"mate {m['name']}: anchors {m['refs'][0]['component']} to assembly reference geometry (kept fixed)")
            else:
                notes.append(f"mate {m['name']}: no reading of its entities holds at the saved position (not imported)")
            continue
        mtype, value, aligned, (a, ea), (b, eb), _ = best
        try:
            mate = asm.add_mate(mtype, a, b, value, aligned)
        except Exception as exc:
            notes.append(f"mate {m['name']}: {exc}")
            continue
        # the saved pose satisfies every SOLIDWORKS mate: one that does not hold there was misread
        ea, eb = asm._local_entity(a), asm._local_entity(b)
        try:
            bad = max(abs(v) for v in _mate_residual(mate, ea, eb, ea[0].x, eb[0].x)) > 1e-6
        except Exception:
            bad = True
        if bad:
            asm.mates.remove(mate)
            notes.append(f"mate {m['name']}: does not hold at the saved position (not imported)")
            continue
        if m["name"] and all(x["name"] != m["name"] for x in asm.mates):
            mate["name"] = m["name"]  # keep the SOLIDWORKS mate name
        mated.update((a["component"], b["component"]))
        done += 1
    mated.discard(ASSEMBLY)
    if done:
        before = {c.name: c.x.copy() for c in asm.components}
        ground = asm.components[0].name
        for c in asm.components:
            if c.name in mated and c.name != ground and c.name not in anchored:
                c.fixed = False
        try:
            asm.solve()

            def pose_change(c):  # compare poses, not rotation vectors (equivalent vectors differ near 180 deg)
                R0, t0 = c.matrix(before[c.name])
                R1, t1 = c.matrix()
                return max(float(np.abs(R1 - R0).max()) * 100.0, float(np.abs(t1 - t0).max()))
            moved = max(pose_change(c) for c in asm.components)
        except Exception:
            moved = float("inf")
        if moved > 1e-4:  # (mm) the imported mates do not hold the saved positions: keep everything fixed
            for c in asm.components:
                c.x[:] = before[c.name]
                c.fixed = True
            notes.append("mates imported, components kept fixed (solving them would move parts)")
    notes.insert(0, f"{done} of {len(expanded)} mates imported")
    return notes


def _recreate_drawing(drw: dict) -> dict:
    """An openswx drawing of the active model with the SOLIDWORKS drawing's sheets and views: model views in
    their orientations, views without one projected from the model view before them (top, then right, as a
    standard three-view), at a standard scale that fits the model on an A3 sheet. Views whose sheet position
    and scale the file gives are placed there (on the smallest sheet that holds them), projected views on the
    side of their parent they sit on."""
    from openswx.commands.drawing import (drawing_add_gdt, drawing_add_note, drawing_add_sheet, drawing_add_view,
                                          drawing_new, drawing_projected_view, drawing_sheet_properties)
    from openswx.drawing.drawing import SHEETS
    doc = APP.active_doc
    body = doc.body
    size = max(tuple(body.bounding_box().max - body.bounding_box().min)) if body is not None else 100.0
    fit = next((s for s in (10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01) if size * s <= 75), 0.01)  # room for 3 views
    by_sheet: dict = {}
    for v in drw["views"]:
        by_sheet.setdefault(v["sheet"], []).append(v)
    sheets = [s for s in drw["sheets"] if s in by_sheet] or drw["sheets"][:1]
    made, scale, notes, gdt = [], fit, 0, 0
    for i, name in enumerate(sheets):
        views = by_sheet.get(name, [])
        placed = [v["position"] for v in views if v.get("position")]
        fmt = drw.get("sheet_size") or next((f for f in ("A4", "A3", "A2", "A1", "A0") if all(
            x < SHEETS[f][0] - 10 and y < SHEETS[f][1] - 10 for x, y in placed)), "A0")
        height = SHEETS[fmt][1]
        scales = [v["scale"] for v in views if v.get("scale")]
        scale = max(set(scales), key=scales.count) if scales else fit
        if i == 0:
            drawing_new(sheet=fmt, scale=scale, standard_views=False)
            drawing_sheet_properties(name=name)
        else:
            drawing_add_sheet(size=fmt, name=name, scale=scale)
        parent, parent_at, sides, model_views, at_origin = None, None, [], 0, []
        for v in views:
            orient = v["orientation"] or (None if parent else "front")
            pos = (v["position"][0], height - v["position"][1]) if v.get("position") else None  # y down
            if not orient and pos and parent_at and min(abs(pos[0] - parent_at[0]), abs(pos[1] - parent_at[1])) > 1:
                orient = "isometric"  # not in line with the view before it: not a projection of it
            if orient:  # a model view: the first left of centre, others (often the isometric) top right
                x, y = pos or ((140.0, 170.0) if model_views == 0 else (330.0, 90.0 + 70.0 * (model_views - 1)))
                own = v.get("scale") if v.get("scale") and v["scale"] != scale else None
                parent = drawing_add_view(orient, x, y, scale=own)["view"]["id"]
                if pos:
                    at_origin.append((parent, pos))
                parent_at = pos
                model_views += 1
                sides = ["top", "right", "bottom", "left"]
                made.append({"sw": v["name"], "view": parent, "orientation": orient})
            elif sides:
                if pos and parent_at:  # the side it sits on, from the stored positions
                    dx, dy = pos[0] - parent_at[0], pos[1] - parent_at[1]
                    side = ("right" if dx > 0 else "left") if abs(dx) > abs(dy) else ("bottom" if dy > 0 else "top")
                    side = side if side in sides else sides[0]
                else:
                    side = sides[0]
                sides.remove(side)
                kw = {"x": pos[0], "y": pos[1]} if pos else {}
                pv = drawing_projected_view(parent, side, **kw)["view"]["id"]
                if pos:
                    at_origin.append((pv, pos))
                made.append({"sw": v["name"], "view": pv, "projected_from": parent})
            if v.get("dimensions") and made and made[-1]["sw"] == v["name"]:
                made[-1]["dimensions"] = _import_dimensions(made[-1]["view"], v["dimensions"])
        _origins_at(at_origin)
        for n in drw.get("notes", []):  # the sheet's notes, where SOLIDWORKS has them
            if n["sheet"] == name:
                drawing_add_note(n["text"], n["position"][0], height - n["position"][1], size=n.get("size", 3.5))
                notes += 1
        for g in drw.get("gdt", []):  # feature control frames
            if g["sheet"] == name:
                drawing_add_gdt(g["characteristic"], g["tolerance"], g["position"][0], height - g["position"][1],
                                datums=g["datums"], diameter=g["diameter"])
                gdt += 1
    return {"sheets": sheets, "views": made, "scale": scale, "notes": notes, "gdt": gdt}


def _origins_at(placed: list) -> None:
    """Place a sheet's views [(view, pos)] so the model origin is at pos (sheet mm, y down) - where SOLIDWORKS
    records a view - when that keeps every one of them on the sheet (an assembly's origin here need not be
    SOLIDWORKS'); else they stay centred there."""
    def f(st, DS):
        moves = []
        for view, pos in placed:
            v = DS.find_view(st, view)
            b, s = DS.view_geometry(APP.active_doc, st, v)["box"], DS.view_scale(st, v)
            x, y = pos[0] + (b[0] + b[2]) / 2 * s, pos[1] - (b[1] + b[3]) / 2 * s
            W, H = DS.D.SHEETS.get(DS.sheet_of(st, view).get("size", "A3"), DS.D.SHEETS["A3"])
            hw, hh = (b[2] - b[0]) * s / 2, (b[3] - b[1]) * s / 2
            if not (hw <= x <= W - hw and hh <= y <= H - hh):
                return
            moves.append((v, x, y))
        for v, x, y in moves:
            v["x"], v["y"] = x, y
    if placed:
        _views_edit(f)


def _import_dimensions(view: str, texts: list[str]) -> dict:
    """The SOLIDWORKS view's dimensions, by value, on what measures them in the recreated view."""
    from openswx.commands.drawing import drawing_dimension_value
    done, missed = 0, []
    for text in texts:
        try:
            drawing_dimension_value(view, text)
            done += 1
        except (DocumentError, CommandError):
            missed.append(text)
    return {"placed": done, "not_found": missed}


def _read_iges(path: str):
    """IGES: the solids in the file, or - for surface-only IGES (the usual case) - its faces sewn into a solid."""
    from build123d import Compound, Shape, Solid
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.IGESControl import IGESControl_Reader
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    r = IGESControl_Reader()
    if r.ReadFile(path) != IFSelect_RetDone:
        raise DocumentError(f"cannot read IGES file {path}")
    r.TransferRoots()
    shape = Shape.cast(r.OneShape())
    if shape.solids():
        return shape, []
    sew = BRepBuilderAPI_Sewing(1e-3)
    for f in shape.faces():
        sew.Add(f.wrapped)
    sew.Perform()
    solids = []
    ex = TopExp_Explorer(sew.SewedShape(), TopAbs_SHELL)
    while ex.More():
        s = Solid(BRepBuilderAPI_MakeSolid(TopoDS.Shell(ex.Current())).Solid())
        if s.is_valid and s.volume < 0:  # inside-out shell
            s = Solid(s.wrapped.Reversed())
        if s.is_valid and abs(s.volume) > 1e-9:
            solids.append(s)
        ex.Next()
    if not solids:
        raise DocumentError("the IGES surfaces do not close into a solid (import the faces as a surface instead)")
    return Compound(solids), [f"sewed {len(shape.faces())} IGES faces into {len(solids)} solid(s)"]


MESH_FACE_LIMIT = 200_000


def _read_mesh(path: str):
    """A triangle mesh (.stl, .3mf) as solid bodies: every closed, consistently oriented mesh becomes a solid
    whose faces are its triangles, with coplanar neighbours merged into one planar face where that works."""
    from build123d import Compound, Mesher
    try:
        shapes = Mesher().read(path)
    except Exception as exc:  # noqa: BLE001 - lib3mf reports unreadable files in its own ways
        raise DocumentError(f"cannot read the mesh: {exc}") from exc
    solids = [s for s in shapes if s.solids()]
    if not solids:
        raise DocumentError("the mesh is not a closed volume (open edges or inconsistent facets): no solid to import")
    notes = []
    if len(solids) < len(shapes):
        notes.append(f"{len(shapes) - len(solids)} open mesh(es) were left out (not closed volumes)")
    n = sum(len(s.faces()) for s in solids)
    if n > MESH_FACE_LIMIT:
        raise DocumentError(f"the mesh has {n} facets; over {MESH_FACE_LIMIT} is too many to edit as a solid "
                            "(decimate it first)")
    out = []
    for s in solids:
        try:
            c = s.clean()
            out.append(c if c.is_valid and abs(c.volume - s.volume) <= 1e-6 * max(1.0, abs(s.volume)) else s)
        except Exception:  # noqa: BLE001 - merging facets is a nicety: keep the triangles
            out.append(s)
    merged = sum(len(s.faces()) for s in out)
    notes.append(f"mesh of {n} facets imported as {len(out)} solid(s)" + (f", {merged} faces after merging coplanar facets" if merged < n else "")
                 + "; curved surfaces stay faceted")
    return (out[0] if len(out) == 1 else Compound(out)), notes


def _read_foreign(path: str, config: int | None = None):
    """(shape, notes) of a SOLIDWORKS part, Parasolid or IGES file. Read in a child process with a time limit
    (isolated.py), so a file the kernel hangs or crashes on costs that file and not the application; a body
    already in the disk cache is used directly."""
    import multiprocessing

    from openswx.system import isolated
    if not isolated.enabled() or multiprocessing.current_process().name != "MainProcess":
        return isolated.read_geometry_here(path, config)
    if path.lower().endswith(".sldprt"):
        from openswx.system import cache
        hit = cache.get_shape("body", cache.key(path, "sldprt-body", config))
        if hit is not None:
            return hit[0], hit[1].get("notes", [])
    try:
        return isolated.read_geometry(path, config)
    except isolated.IsolationError as exc:
        raise DocumentError(str(exc)) from exc


def _import_file(doc: PartDocument, path: str, config: int | None = None) -> dict:
    from build123d import import_brep, import_step
    p = Path(path)
    ext, notes = p.suffix.lower(), []
    if ext in (".x_t", ".x_b", ".xmt_txt", ".xmt_bin", ".sldprt", ".igs", ".iges"):
        shape, notes = _read_foreign(str(p), config)
    elif ext in (".stl", ".3mf"):
        shape, notes = _read_mesh(str(p))
    elif ext == ".fcstd":
        from openswx.exchange.fcstd import FcstdError, read_fcstd
        try:
            shape, notes = read_fcstd(str(p))
        except FcstdError as exc:
            raise DocumentError(str(exc)) from exc
    else:
        shape = import_step(str(p)) if ext in (".step", ".stp") else import_brep(str(p))
    if not shape.solids():
        raise DocumentError("file contains no solid bodies")
    params = {"brep": encode_shape(shape), "source": p.name}
    if notes:
        params["notes"] = notes
    return doc.add_feature("import", params)


def _eval_body():
    body = APP.active_doc.body
    if body is None:
        raise DocumentError("nothing to evaluate: the document has no body")
    return body


def _direction(direction) -> tuple:
    if isinstance(direction, str):
        d = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}.get(direction.upper())
        if d is None:
            from openswx.core.features import resolve_plane
            d = tuple(resolve_plane(APP.doc.ctx, direction).z_dir)
        return d
    return tuple(direction)


def _sim_doc():
    return APP.active_doc


def _sim_face(sel):
    """Part: 'FaceN' -> face ref; assembly: 'Comp/FaceN' -> {component, face}."""
    d = _sim_doc()
    if getattr(d, "kind", "part") == "assembly":
        return sel if isinstance(sel, dict) else d.face_ref_for(sel)
    return _face(d, sel)


def _study_edit(fn):
    doc = _sim_doc()
    doc._checkpoint()
    st = doc.study
    out = fn(st)
    doc.revision += 1
    doc.dirty = True
    APP.sim_result = None
    return {"study": out if out is not None else st}


def _resolve_study(doc):
    from openswx.core.geom import face_normal, resolve_face
    body = doc.body
    if getattr(doc, "kind", "part") == "assembly":
        def rf(r):
            c = doc.component(r["component"])
            return resolve_face(doc._local_body(c), r["face"]).moved(c.location())
    else:
        rf = lambda r: resolve_face(body, r)  # noqa: E731
    fixtures = [rf(r) for r in doc.study["fixtures"]]
    loads = []
    for ld in doc.study["loads"]:
        face = rf(ld["face"])
        n = face_normal(face, face.center())
        if ld["type"] == "pressure":
            vecf = [-c * ld["pressure"] * face.area for c in n]
        elif ld.get("force") is not None:
            vecf = list(ld["force"])
        else:
            vecf = [-c * ld["magnitude"] for c in n]
        loads.append((face, vecf))
    connectors = []
    for c in doc.study.get("connectors", []):
        fa, fb = rf(c["a"]), rf(c["b"])
        if c["type"] == "contact":
            axis = face_normal(fa, fa.center())  # from face a towards face b
            if (fb.center() - fa.center()).dot(axis) < 0:
                axis = -axis
            connectors.append({"a": fa, "b": fb, "contact": True, "axis": list(axis)})
        elif c["type"] == "spring":
            axis = fb.center() - fa.center()
            connectors.append({"a": fa, "b": fb, "kn": c["kn"], "kt": c["kt"],
                               "axis": list(axis.normalized()) if axis.length > 1e-9 else None})
        else:  # bolt between coaxial holes
            circ = [e for e in fa.edges() if e.geom_type.name.lower() == "circle"]
            if not circ:
                raise DocumentError("bolt connectors need cylindrical hole faces")
            axis = circ[0].normal().normalized()
            L = abs((fb.center() - fa.center()).dot(axis)) or 1.0
            d = c.get("diameter") or 2 * circ[0].radius
            A = math.pi * d * d / 4
            G = c["E"] / (2 * (1 + c["nu"]))
            connectors.append({"a": fa, "b": fb, "kn": c["E"] * A / L, "kt": G * A / L, "axis": list(axis),
                               "preload": c.get("preload", 0.0)})
    return fixtures, loads, connectors


def _thermal(doc) -> dict:
    return doc.study.setdefault("thermal", {"temperatures": [], "heat": [], "convection": []})


def _context_meshes(asm, component: str) -> list[dict]:
    """The assembly's other components, placed in the edited component's frame (shown ghosted)."""
    from openswx.core.geom import tessellate
    comp = asm.component(component)
    inv = comp.location().inverse()
    out = []
    for c, shape in asm.world_shapes():
        if c.name == component:
            continue
        for f in tessellate(shape.moved(inv))["faces"]:
            out.append({"v": f["v"], "t": f["t"], "component": c.name})
    return out


def _measure_item(item):
    """A selection to measure: [x, y, z] (a vertex), Face#/Edge# of the part, or Component/Face# in an
    assembly. Returns (shape or None, info dict)."""
    from build123d import Vertex as _Vx
    from openswx.core.geom import geom_name
    if isinstance(item, (list, tuple)):
        p = [float(v) for v in item]
        return _Vx(*p), {"kind": "vertex", "id": "Vertex", "point": p}
    doc = APP.active_doc
    if getattr(doc, "kind", "part") == "assembly" or "/" in item:
        comp, _, sub = item.partition("/")
        shapes = dict((c.name, sh) for c, sh in APP.asm.world_shapes())
        if comp not in shapes:
            raise DocumentError(f"no component {comp!r}")
        body, label = shapes[comp], item
    else:
        body, sub, label = doc.body, item, item
        if body is None:
            raise DocumentError("the part has no body")
    try:
        k = int(sub[4:])
        shape = body.faces()[k] if sub.startswith("Face") else body.edges()[k] if sub.startswith("Edge") else None
    except (ValueError, IndexError):
        shape = None
    if shape is None:
        raise DocumentError(f"cannot measure {item!r}")
    info = {"kind": "face" if sub.startswith("Face") else "edge", "id": label, "type": geom_name(shape)}
    if info["kind"] == "face":
        info["area"] = shape.area
        if info["type"] == "plane":
            info["normal"] = list(shape.normal_at())
        if info["type"] == "cylinder":
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            cyl = BRepAdaptor_Surface(shape.wrapped).Cylinder()
            ax = cyl.Axis()
            info["radius"] = cyl.Radius(); info["diameter"] = 2 * cyl.Radius()
            info["axis"] = [ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()]
            info["center"] = [ax.Location().X(), ax.Location().Y(), ax.Location().Z()]
    else:
        info["length"] = shape.length
        if info["type"] == "circle":
            info["radius"] = shape.radius; info["diameter"] = 2 * shape.radius
            info["center"] = list(shape.arc_center)
        if info["type"] == "line":
            d = shape.position_at(1) - shape.position_at(0)
            info["direction"] = list(d.normalized())
        info["start"], info["end"] = list(shape.position_at(0)), list(shape.position_at(1))
    return shape, info


def _drawing_state(d=None) -> dict:
    d = d or APP.active_doc
    if not hasattr(d, "drawing") or d.drawing is None:
        d.drawing = {}
    return d.drawing


def _drawing_edit(fn, annotations: bool = False):
    """Run fn on the drawing state (annotations: on the active sheet's annotation lists when the drawing has
    sheets, else on the single automatic sheet)."""
    d = APP.active_doc
    d._checkpoint()
    st = _drawing_state(d)
    if annotations and st.get("sheets"):
        from openswx.drawing.drawing_sheets import active_sheet
        st = active_sheet(st)
    try:
        out = fn(st)
    except ValueError as exc:  # (drawing errors: no such view, a cut that misses the part, ...)
        raise DocumentError(str(exc)) from exc
    d.revision += 1
    d.dirty = True
    return out


def _next_ann(st: dict, prefix: str) -> str:
    sheets = APP.active_doc.drawing.get("sheets") or [st]
    used = {a["id"] for sh in sheets for k in ("notes", "datums", "gdt", "finishes", "welds") for a in sh.get(k, [])}
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


def _views_edit(fn):
    """fn(st, module) on a drawing that has sheets; returns its result."""
    from openswx.drawing import drawing_sheets as DS

    def g(st):
        if not st.get("sheets"):
            raise DocumentError("this drawing has no sheets yet: start one with drawing_new")
        return fn(st, DS)
    return _drawing_edit(g)


def _beside(DS, st, parent: dict, view: dict) -> str:
    """The side of the parent a derived view goes on: the way its arrows point, as SOLIDWORKS places it."""
    fr, pfr = DS.frame(st, view), DS.frame(st, parent)
    lx, ly = DS.to2d(-fr[0], pfr)
    return ("right" if lx > 0 else "left") if abs(lx) >= abs(ly) else ("top" if ly > 0 else "bottom")


_PICK_MM = 3.0  # how near (sheet mm) a pick must be to a vertex or circle


def _line_dimension(st, DS, v, sh, g, did, k, q1, p2, tol, offset, text, at) -> dict:
    """Angle between two straight edges, or the distance between two parallel ones."""
    import numpy as np
    if p2 is None:
        raise DocumentError("pick two straight edges")
    l1 = DS.pick_line(g, q1, tol)
    l2 = DS.pick_line(g, DS.sheet_to_view(st, APP.active_doc, v, p2), tol)
    if l1 is None or l2 is None:
        raise DocumentError("pick points on two straight edges of the view")
    if l1 == l2:
        raise DocumentError("pick two different edges")
    d = {"id": did, "view": v["id"], "kind": k, "l1": [list(l1[0]), list(l1[1])], "l2": [list(l2[0]), list(l2[1])], "offset": offset}
    crossing = DS.lines_angle(l1, l2, q1)
    if k == "angle":
        if crossing is None:
            raise DocumentError("the edges are parallel: dimension the distance between them (line_distance)")
        if at is not None:
            d["at"] = list(DS.sheet_to_view(st, APP.active_doc, v, at))
        else:  # the angle between the edges as picked: each ray from the crossing towards its pick
            apex = crossing[0]
            q2 = DS.sheet_to_view(st, APP.active_doc, v, p2)
            rays = []
            for line, q in ((l1, q1), (l2, q2)):
                u = np.asarray(line[1], float) - np.asarray(line[0], float)
                u = u / max(1e-12, float(np.linalg.norm(u)))
                rays.append(u if float((np.asarray(q, float) - apex) @ u) >= 0 else -u)
            mid = rays[0] + rays[1]
            mid = mid / max(1e-9, float(np.linalg.norm(mid)))
            d["at"] = [float(c) for c in apex + mid * (offset + 10) / DS.view_scale(st, v)]
    elif crossing is not None:
        raise DocumentError("the edges are not parallel: dimension the angle between them")
    if text:
        d["text"] = text
    sh.setdefault("dims", []).append(d)
    return {**d, "value": DS.dim_value(d)}


def _hole_callout(st, DS, v, sh, g, did, q1, tol, offset, text, angle) -> dict:
    """Hole Callout: the hole's diameter and THRU or its depth, on a leader."""
    c = DS.pick_circle(g, q1, tol)
    if c is None:
        raise DocumentError("no hole seen end-on near that point")
    holes = DS.view_holes(APP.active_doc, st, v)
    hole = min(holes, key=lambda h: math.dist(h["c"], c[0])) if holes else None
    if hole is not None and math.dist(hole["c"], c[0]) < 1e-3 and abs(hole["d"] - 2 * c[1]) < 1e-3:
        callout = "⌀" + DS.D._fmt(hole["d"]) + " " + ("THRU" if hole["depth"] is None else "↧" + DS.D._fmt(hole["depth"]))
    else:
        callout = "⌀" + DS.D._fmt(2 * c[1])
    ang = angle if angle is not None else math.degrees(math.atan2(q1[1] - c[0][1], q1[0] - c[0][0]))
    d = {"id": did, "view": v["id"], "kind": "callout", "c": list(c[0]), "r": c[1], "angle": ang, "offset": offset,
         "callout": callout}
    if text:
        d["text"] = text
    sh.setdefault("dims", []).append(d)
    return {**d, "value": DS.dim_value(d)}


def _new_balloon(st, sh, view, q, x, y, component, text=None) -> dict:
    used = {b["id"] for s in st["sheets"] for b in s.get("balloons", [])}
    bid = next(f"Balloon{i}" for i in range(1, 9999) if f"Balloon{i}" not in used)
    b = {"id": bid, "view": view, "p": [float(q[0]), float(q[1])], "x": float(x), "y": float(y), "component": component}
    if text:
        b["text"] = text
    sh.setdefault("balloons", []).append(b)
    return b


def _asm_ref(sel):
    return sel if isinstance(sel, dict) else APP.asm.face_ref_for(sel)


_BOLT_LENGTHS = [6, 8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100, 110, 120, 130, 140, 150]


def _sk(sketch: str, fn):
    return APP.doc.edit_sketch(sketch, fn)


def _add_projected(sk, plane, edges, body=None) -> list[str]:
    """Add 3D edges to a sketch, projected onto its plane, as fixed lines/circles/arcs/splines. With the body,
    each keeps a reference to its model edge ("ext"): if a change removes that edge the entity is dangling."""
    import math as _m
    pts: dict[tuple, str] = {}

    def loc(v):
        q = plane.to_local_coords(v)
        return [q.X, q.Y]

    def pid(v):
        x, y = loc(v)
        key = (round(x, 6), round(y, 6))
        if key not in pts:
            pts[key] = sk.point_id([x, y])
            sk.add_relation("fix", [pts[key]])
        return pts[key]

    created = []
    for e in edges:
        kind = e.geom_type.name.lower()
        normal_ok = kind == "circle" and abs(abs(e.normal().dot(plane.z_dir)) - 1) < 1e-6
        if kind == "line":
            a, b = pid(e.position_at(0)), pid(e.position_at(1))
            if a != b:
                created.append(sk.add_line(a, b))
        elif normal_ok and e.is_closed:
            c = sk.point_id(loc(e.arc_center))
            sk.add_relation("fix", [c])
            eid = sk.add_circle(c, e.radius)
            sk.add_relation("fix", [eid])
            created.append(eid)
        elif normal_ok:
            c = sk.point_id(loc(e.arc_center))
            sk.add_relation("fix", [c])
            s0, s1 = pid(e.position_at(0)), pid(e.position_at(1))
            # arcs are CCW about the sketch normal: swap ends if the edge runs clockwise
            mid = loc(e.position_at(0.5))
            cc, a0 = sk.coords(c), sk.coords(s0)
            a1 = sk.coords(s1)
            ang = lambda q: _m.atan2(q[1] - cc[1], q[0] - cc[0])  # noqa: E731
            ccw = (ang(mid) - ang(a0)) % (2 * _m.pi) < (ang(a1) - ang(a0)) % (2 * _m.pi)
            created.append(sk.add_arc(c, s0, s1) if ccw else sk.add_arc(c, s1, s0))
        else:  # ellipses, B-splines, out-of-plane circles: spline through samples
            n = 16
            samp = [pid(e.position_at(0))] + [sk.point_id(loc(e.position_at(k / n))) for k in range(1, n)] \
                + [pid(e.position_at(1))]
            closed = samp[0] == samp[-1]
            eid = sk.add_spline(samp[:-1] if closed else samp, closed=closed)
            for q in sk.entities[eid]["pts"]:
                sk.add_relation("fix", [q])
            created.append(eid)
        if body is not None and created and "ext" not in sk.entities[created[-1]]:
            sk.entities[created[-1]]["ext"] = edge_ref(e)
    return created


def _up_to(doc, up_to):
    """Up-to target: a face id ('Face3') / face reference, or a vertex position [x, y, z]."""
    if up_to is None:
        return None
    if isinstance(up_to, (list, tuple)):
        return [float(v) for v in up_to]
    return _face(doc, up_to)


def _ext_params(sketch, depth, end_condition, reverse, draft, draft_outward, direction2_depth,
                direction2_end_condition, merge, start_offset, up_to=None, up_to_offset=0.0, reverse_offset=False):
    p = {"sketch": sketch, "depth": depth, "end_condition": end_condition, "reverse": reverse,
         "draft": draft, "draft_outward": draft_outward, "merge": merge, "start_offset": start_offset}
    if up_to is not None:
        p.update(up_to=_up_to(APP.doc, up_to), up_to_offset=up_to_offset, reverse_offset=reverse_offset)
    if direction2_depth is not None or direction2_end_condition is not None:
        p["direction2"] = {"depth": direction2_depth or depth, "end_condition": direction2_end_condition or "Blind"}
    return p


def _feat(ftype: str, params: dict) -> dict:
    feat = APP.doc.add_feature(ftype, params)
    return {"feature": feat["name"], "info": APP.doc.ctx.info.get(feat["name"]), "model": APP.doc.summary()}


def _point_spec(doc: PartDocument, pt):
    if _is_edge_sel(pt):
        return _edge(doc, pt)
    if _is_face_sel(pt):
        return _face(doc, pt)
    return pt


# Imported after the definitions above so either module can be imported first (session imports this one).
from openswx.session import APP, CommandError  # noqa: E402
