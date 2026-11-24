"""More commands (registered with the same @command registry as session.py, which re-exports them): Flex /
Deform / Freeform, fillet profiles and FilletXpert, mold tools, Insert / Mirror Part, cosmetic threads, the
remaining surface tools, library features and the Design Library, Hole Series."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Literal

from openswx.core.document import DocumentError
from openswx.session import APP, Vec2, Vec3, _edge, _ext_params, _face, _feat, command, unlocked

__all__ = ["flex", "deform", "freeform", "fillet_profile", "fillet_xpert", "parting_line", "parting_surface",
           "shut_off_surfaces", "tooling_split", "insert_part", "mirror_part", "cosmetic_thread", "ruled_surface",
           "radiate_surface", "extend_surface", "untrim_surface", "flatten_surface", "filled_surface_edges",
           "save_library_feature", "list_design_library", "insert_library_feature", "delete_library_item",
           "add_part_to_design_library", "hole_series"]


def openswx_home() -> Path:
    return Path(os.environ.get("OPENSWX_HOME") or Path.home() / ".openswx")


# ====================================================================== Flex / Deform / Freeform
@command(category="features")
def flex(type: Literal["bend", "twist", "taper", "stretch"], axis: str | Vec3 = "X", angle: float = 90.0,
         factor: float = 0.5, distance: float = 10.0, start: float | None = None, end: float | None = None,
         bend_direction: str | Vec3 | None = None) -> dict:
    """Flex the body along an axis ('X'/'Y'/'Z' or a vector) between trim positions start/end measured along
    it (default the whole body): bend by angle (deg, toward bend_direction), twist by angle (deg), taper to
    factor at the far end, or stretch by distance (mm)."""
    return _feat("flex", {"type": type, "axis": axis, "angle": angle, "factor": factor, "distance": distance,
                          "start": start, "end": end, "bend_direction": bend_direction})


@command(category="features")
def deform(points: list[Vec3], moves: list[Vec3], radius: float = 20.0) -> dict:
    """Deform (point): push the body at each point by its move [dx, dy, dz], falling off smoothly to nothing
    at `radius` mm from the point."""
    if len(points) != len(moves):
        raise DocumentError("give one move per point")
    return _feat("deform", {"points": [{"point": p, "move": m} for p, m in zip(points, moves)], "radius": radius})


@command(category="features")
def freeform(face: str, distance: float, radius: float | None = None) -> dict:
    """Freeform: pull a face ('FaceN') out along its normal by `distance` (negative pushes in), smoothly."""
    return _feat("freeform", {"face": _face(APP.doc, face), "distance": distance, "radius": radius})


# ====================================================================== fillets
@command(category="features")
def fillet_profile(edges: list[str], radius: float, profile: Literal["conic", "curvature_continuous"] = "conic",
                   rho: float = 0.5) -> dict:
    """Fillet with a conic (rho: 0.5 parabola, lower flatter, higher sharper) or curvature-continuous (G2)
    profile, on straight edges between planar faces; `radius` is the distance from the edge along each face."""
    doc = APP.doc
    return _feat("profile_fillet", {"edges": [_edge(doc, e) for e in edges], "radius": radius, "profile": profile,
                                    "rho": rho})


@command(category="features")
def fillet_xpert(edges: list[str], radius: float) -> dict:
    """FilletXpert: fillet edges that will not fillet all at once, by finding an order that works: all
    together first, then one at a time (longest first); edges that cannot take the radius are reported."""
    from openswx.core.geom import resolve_edge
    from openswx.session import fillet as _fillet
    doc = APP.doc
    refs = [_edge(doc, e) for e in edges]
    try:
        r = _fillet(radius, edges=edges)
        return {"features": [r["feature"]], "failed": [], "model": doc.summary()}
    except Exception:
        pass
    made, failed = [], []
    for ref in sorted(refs, key=lambda x: -x.get("length", 0.0)):  # ids change as features are added: re-resolve
        try:
            e = resolve_edge(doc.body, ref)
            idx = next(i for i, x in enumerate(doc.body.edges()) if x.is_same(e))
            made.append(_fillet(radius, edges=[f"Edge{idx}"])["feature"])
        except Exception:
            failed.append(ref["point"])
    if not made:
        raise DocumentError("none of the edges takes that radius")
    return {"features": made, "failed": failed, "model": doc.summary()}


# ====================================================================== mold tools
@command(category="mold")
def parting_line(direction: str | Vec3 = "Y", level: float | None = None) -> dict:
    """Mold Tools > Parting Line: the edges dividing the part between core and cavity for the pull direction."""
    return _feat("parting_line", {"direction": direction, "level": level})


@command(category="mold")
def parting_surface(direction: str | Vec3 = "Y", margin: float = 10.0, level: float | None = None) -> dict:
    """Mold Tools > Parting Surface: a flat surface on the parting plane out to `margin` beyond the part."""
    return _feat("parting_surface", {"direction": direction, "margin": margin, "level": level})


@command(category="mold")
def shut_off_surfaces(direction: str | Vec3 = "Y", level: float | None = None) -> dict:
    """Mold Tools > Shut-off Surfaces: patches over the openings through the part at the parting level."""
    return _feat("shut_off_surfaces", {"direction": direction, "level": level})


@command(category="mold")
def tooling_split(direction: str | Vec3 = "Y", margin: float = 10.0, level: float | None = None,
                  keep_part: bool = True) -> dict:
    """Mold Tools > Tooling Split: a block `margin` mm around the part split at the parting level into core
    and cavity bodies (the part kept as a third body unless keep_part is false). Scale the part first for
    shrinkage (Features > Scale)."""
    return _feat("tooling_split", {"direction": direction, "margin": margin, "level": level, "keep_part": keep_part})


# ====================================================================== derived parts, cosmetic threads
@command(category="features")
def insert_part(path: str, position: Vec3 | None = None, rotation_deg: Vec3 | None = None,
                op: Literal["add", "cut"] = "add", merge: bool = True) -> dict:
    """Insert Part: bring another part file's body (.oswprt, STEP, Parasolid, .sldprt) into this part, linked
    to the file (it updates on rebuild), placed by position and rotation (deg about X, Y, Z)."""
    return _feat("insert_part", {"path": str(path), "position": position, "rotation": rotation_deg, "op": op, "merge": merge})


@command(category="features")
def mirror_part(path: str, plane: str = "Right", position: Vec3 | None = None) -> dict:
    """Mirror Part: a mirrored (opposite-hand) copy of another part file's body, about plane (Front / Top /
    Right or a plane name), linked to the file."""
    return _feat("insert_part", {"path": str(path), "mirror": plane, "position": position, "merge": False})


@command(category="features")
def cosmetic_thread(face: str, label: str | None = None, length: float | None = None) -> dict:
    """Cosmetic Thread on a cylindrical face ('FaceN', a hole or a shaft): label like 'M8x1.25' (default from
    the diameter), length (default the face's length). Shown on the model and in drawings; no geometry."""
    return _feat("cosmetic_thread", {"face": _face(APP.doc, face), "label": label, "length": length})


# ====================================================================== surfaces
@command(category="surfaces")
def ruled_surface(edges: list[str], distance: float = 10.0,
                  type: Literal["normal", "tangent", "direction"] = "normal", direction: str | Vec3 = "Y") -> dict:
    """Surfaces > Ruled Surface from model edges: along the adjacent face normal, tangent to it (outward) or
    along a direction, `distance` mm."""
    doc = APP.doc
    return _feat("ruled_surface", {"edges": [_edge(doc, e) for e in edges], "distance": distance, "type": type,
                                   "direction": direction})


@command(category="surfaces")
def radiate_surface(edges: list[str], plane: str = "Top", distance: float = 10.0) -> dict:
    """Surfaces > Radiate Surface: outward from model edges, parallel to a reference plane, `distance` mm."""
    doc = APP.doc
    return _feat("radiate_surface", {"edges": [_edge(doc, e) for e in edges], "plane": plane, "distance": distance})


@command(category="surfaces")
def extend_surface(distance: float, surface: str | None = None, faces: list[str] | None = None) -> dict:
    """Surfaces > Extend Surface: grow a surface body's faces (or model faces) past their edges by `distance`."""
    doc = APP.doc
    return _feat("extend_surface", {"distance": distance, "surface": surface, "faces": [_face(doc, f) for f in faces or []]})


@command(category="surfaces")
def untrim_surface(surface: str | None = None, faces: list[str] | None = None,
                   mode: Literal["internal", "natural"] = "internal") -> dict:
    """Surfaces > Untrim Surface: fill the holes of faces (internal) or return them to their surfaces' natural
    bounds (natural)."""
    doc = APP.doc
    return _feat("untrim_surface", {"surface": surface, "faces": [_face(doc, f) for f in faces or []], "mode": mode})


@command(category="surfaces")
def flatten_surface(surface: str | None = None, faces: list[str] | None = None, plane: str = "Top") -> dict:
    """Surfaces > Flatten: unroll planar, cylindrical and conical faces flat (true size) onto a plane."""
    doc = APP.doc
    return _feat("flatten_surface", {"surface": surface, "faces": [_face(doc, f) for f in faces or []], "plane": plane})


@command(category="surfaces")
def filled_surface_edges(edges: list[str], continuity: Literal["contact", "tangent"] = "tangent",
                         points: list[Vec3] | None = None) -> dict:
    """Surfaces > Filled Surface over a closed loop of model edges (a hole or gap), tangent (G1) to the faces
    next to them or just touching (contact)."""
    doc = APP.doc
    return _feat("filled_surface", {"edges": [_edge(doc, e) for e in edges], "continuity": continuity, "points": points})


# ====================================================================== library features / Design Library
def _library_dir() -> Path:
    d = openswx_home() / "library"
    (d / "parts").mkdir(parents=True, exist_ok=True)
    return d


def _has_model_refs(v) -> bool:
    """Does a parameter tree hold references to faces / edges of the model (those cannot travel)?"""
    if isinstance(v, dict):
        if v.get("kind") in ("face", "edge"):
            return True
        return any(_has_model_refs(x) for x in v.values())
    if isinstance(v, list):
        return any(_has_model_refs(x) for x in v)
    return False


@command(category="library")
def save_library_feature(name: str, features: list[str], description: str = "") -> dict:
    """Save features (and the sketches they use) as a library feature in the Design Library, to insert into
    other parts. Sketch-based features travel; features on model faces / edges (fillets...) cannot."""
    from openswx.core.document import consumed_sketches
    doc = APP.doc
    picked: list[str] = []
    for n in features:
        f = doc.find(n)
        for sk in consumed_sketches(f):
            if sk not in picked:
                picked.append(sk)
        if f["name"] not in picked:
            picked.append(f["name"])
    feats = [copy.deepcopy(f) for f in doc.features if f["name"] in picked]
    for f in feats:
        p = {k: v for k, v in f["params"].items() if k != "plane"}
        if _has_model_refs(p):
            raise DocumentError(f"{f['name']} uses faces or edges of this part; library features take sketch-based features")
    path = _library_dir() / f"{name}.oswlib"
    path.write_text(json.dumps({"format": "openswx-library-feature", "name": name, "description": description,
                                "units": doc.units, "features": feats}, indent=1), encoding="utf-8")
    return {"saved": str(path), "features": [f["name"] for f in feats]}


@command(mutates=False, category="library")
def list_design_library() -> dict:
    """The Design Library: library features (.oswlib) and parts (in its parts folder) with their paths."""
    d = _library_dir()
    feats = []
    for p in sorted(d.glob("*.oswlib")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        feats.append({"name": data.get("name", p.stem), "description": data.get("description", ""), "path": str(p),
                      "features": [f["name"] for f in data.get("features", [])]})
    parts = [{"name": p.stem, "path": str(p)} for p in sorted((d / "parts").glob("*"))
             if p.suffix.lower() in (".oswprt", ".step", ".stp", ".x_t", ".x_b", ".sldprt")]
    return {"folder": str(d), "features": feats, "parts": parts}


@command(category="library")
def add_part_to_design_library(path: str | None = None) -> dict:
    """Add a part file (default: the active part, saved under its name) to the Design Library's parts."""
    import shutil
    d = _library_dir() / "parts"
    if path:
        dst = d / Path(path).name
        shutil.copyfile(path, dst)
    else:
        doc = APP.doc
        dst = d / f"{doc.name}.oswprt"
        dst.write_text(json.dumps(doc.to_dict(), indent=1), encoding="utf-8")
    return {"added": str(dst)}


@command(category="library")
def delete_library_item(path: str) -> dict:
    """Remove a library feature or part from the Design Library."""
    p = Path(path).resolve()
    if _library_dir().resolve() not in p.parents:
        raise DocumentError("that file is not in the Design Library")
    p.unlink()
    return {"deleted": str(p)}


@command(category="library")
def insert_library_feature(name: str, plane: str | dict = "Top", offset: Vec2 | None = None) -> dict:
    """Insert a library feature into the active part: its sketches go on `plane` (a plane name or 'FaceN'),
    moved by offset [x, y] in sketch coordinates; features are renamed to fit this part."""
    from openswx.session import _plane_spec
    path = Path(name) if name.endswith(".oswlib") else _library_dir() / f"{name}.oswlib"
    if not path.exists():
        raise DocumentError(f"no library feature {name!r}")
    data = json.loads(path.read_text(encoding="utf-8"))
    doc = APP.doc
    target = _plane_spec(doc, plane)
    dx, dy = (float(v) for v in (offset or [0, 0]))
    rename = {f["name"]: doc._new_name(f["type"]) for f in data["features"]}

    def remap(v):
        if isinstance(v, str):
            head, sep, tail = v.partition("/")
            return rename[head] + sep + tail if head in rename else v
        if isinstance(v, dict):
            return {k: remap(x) for k, x in v.items()}
        if isinstance(v, list):
            return [remap(x) for x in v]
        return v
    added = []
    for f in data["features"]:
        p = remap(copy.deepcopy(f["params"]))
        if f["type"] == "sketch":
            p["plane"] = target
            for pt in p.get("sketch", {}).get("points", {}).values():
                pt[0] += dx
                pt[1] += dy
        doc.add_feature(f["type"], p, name=rename[f["name"]])
        added.append(rename[f["name"]])
    return {"inserted": added, "model": doc.summary()}


# ====================================================================== Hole Series (assembly)
@command(category="assembly")
def hole_series(position: Vec3, direction: Vec3, diameters: list[float],
                components: list[str] | None = None) -> dict:
    """Hole Series: one hole through several components of the assembly, made as a feature in each part (so
    it is in the part files too). position / direction in assembly coordinates; diameters per component in
    order along the direction (first = start hole, last = end hole; one value for all); components: those to
    drill (default: every component the line passes through)."""
    import numpy as np
    from build123d import Edge as _E, Vector as _V
    from openswx.core.sketch import Sketch
    asm = APP.asm
    p0, d = np.array(position, float), np.array(direction, float)
    d = d / np.linalg.norm(d)
    hits = []
    for c, shape in asm.world_shapes():
        if components and c.name not in components:
            continue
        bb = shape.bounding_box()
        far = bb.diagonal * 4 + float(np.linalg.norm(p0 - np.array(list(bb.center()))))
        line = _E.make_line(_V(*(p0 - d * far)), _V(*(p0 + d * far)))
        if shape.distance_to(line) > 1e-6:
            continue
        hits.append(((np.array(list(bb.center())) - p0) @ d, c))
    if not hits:
        raise DocumentError("the hole line passes through no component")
    hits.sort(key=lambda h: h[0])
    made = []
    for k, (_, c) in enumerate(hits):
        dia = diameters[-1] if k == len(hits) - 1 else diameters[min(k, len(diameters) - 1)]
        R, t = c.matrix()
        lp, ld = R.T @ (p0 - t), R.T @ d  # the line in the part's own coordinates
        doc = c.doc(APP.documents)
        sk = Sketch()
        sk.add_circle([0.0, 0.0], dia / 2)
        sk.solve()
        sk_feat = doc.add_feature("sketch", {"plane": {"origin": list(map(float, lp)), "normal": list(map(float, ld))},
                                             "sketch": sk.to_dict()})
        p = _ext_params(sk_feat["name"], 10.0, "ThroughAllBoth", False, 0.0, False, None, None, True, 0.0, None, 0.0, False)
        cut = doc.add_feature("cut_extrude", p)
        if c.link not in APP.documents:  # an embedded part: keep the change in the assembly
            c.part = doc.to_dict()
        made.append({"component": c.name, "diameter": dia, "feature": cut["name"]})
    asm.revision += 1
    asm.dirty = True
    return {"holes": made, "assembly": asm.summary()}



# ====================================================================== assemblies: smart / magnetic mates, mate references
__all__ += ["smart_mate", "connection_point", "mate_reference", "magnetic_mate", "magnetic_snap", "apply_mate_references",
            "save_alternate_position", "show_alternate_position", "delete_alternate_position", "set_envelope",
            "set_speedpak", "large_assembly_mode", "defeature", "pack_and_go", "route_pipe", "route_cable", "weld_bead",
            "list_pipe_standards", "route_pipe_standard", "insert_fitting"]


@command(category="assembly")
def smart_mate(entity1: str, entity2: str) -> dict:
    """SmartMates: mate two component faces with the mate their shapes call for - planar + planar:
    coincident (face to face), cylinder + cylinder: concentric, planar + cylinder: tangent."""
    from openswx.session import _measure_item, add_mate
    k1, k2 = _measure_item(entity1)[1].get("type"), _measure_item(entity2)[1].get("type")
    kinds = {k1, k2}
    if kinds == {"plane"}:
        t = "coincident"
    elif kinds == {"cylinder"}:
        t = "concentric"
    elif kinds == {"plane", "cylinder"}:
        t = "tangent"
    else:
        raise DocumentError(f"no SmartMate for a {k1} face and a {k2} face")
    out = add_mate(t, entity1, entity2)
    return {"mate": out["mate"], "type": t, "assembly": out["assembly"]}


@command(category="features")
def connection_point(point: Vec3, direction: str | Vec3 = "Y", name: str | None = None) -> dict:
    """Connection point for magnetic mates (Asset Publisher): a point of this part and the direction a mating
    part comes in from."""
    feat = APP.doc.add_feature("connection_point", {"point": point, "direction": direction}, name=name)
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(category="features")
def mate_reference(face: str, type: Literal["coincident", "concentric", "tangent", "parallel"] = "coincident",
                   reference: str | None = None, aligned: bool = False) -> dict:
    """Mate Reference: name a face of this part and the mate it takes; in an assembly, parts whose mate
    references have the same name are mated automatically (apply_mate_references)."""
    feat = APP.doc.add_feature("mate_reference", {"face": _face(APP.doc, face), "type": type, "reference": reference,
                                                  "aligned": aligned})
    return {"feature": feat["name"], "model": APP.doc.summary()}


def _component_infos(c, key: str) -> list[tuple[str, dict]]:
    doc = c.doc(APP.documents)
    return [(n, i[key]) for n, i in doc.ctx.info.items() if isinstance(i, dict) and key in i]


@command(category="assembly")
def magnetic_mate(point1: str, point2: str) -> dict:
    """Magnetic mate between two connection points 'Component/Connection Point1': they meet, facing each other."""
    asm = APP.asm

    def ref(sel):
        cname, _, pname = sel.partition("/")
        c = asm.component(cname)
        cp = dict(_component_infos(c, "connection_point")).get(pname)
        if cp is None:
            raise DocumentError(f"{cname} has no connection point {pname!r}")
        return {"component": cname, "point": {"point": cp["point"], "normal": cp["normal"]}}
    m = asm.add_mate("connection", ref(point1), ref(point2))
    return {"mate": m["name"], "assembly": asm.summary()}


@command(category="assembly")
def magnetic_snap(component: str, tolerance: float = 25.0) -> dict:
    """After dragging a component: if one of its connection points is within `tolerance` mm of another
    component's, snap them together with a magnetic mate."""
    import numpy as np
    asm = APP.asm
    c = asm.component(component)
    mine = _component_infos(c, "connection_point")
    best = None
    for other in asm.components:
        if other.name == c.name:
            continue
        for on, ocp in _component_infos(other, "connection_point"):
            po = other.to_world(np.array(ocp["point"], float))
            for n, cp in mine:
                d = float(np.linalg.norm(c.to_world(np.array(cp["point"], float)) - po))
                if d <= tolerance and (best is None or d < best[0]):
                    best = (d, f"{c.name}/{n}", f"{other.name}/{on}")
    if best is None:
        return {"snapped": False, "assembly": asm.summary()}
    out = magnetic_mate(best[2], best[1])
    return {"snapped": True, "mate": out["mate"], "distance": best[0], "assembly": out["assembly"]}


@command(category="assembly")
def apply_mate_references(component: str | None = None) -> dict:
    """Mate components through their mate references: references of the same name in two parts give that
    mate between their faces (only for `component` when given). Pairs already mated this way are skipped."""
    asm = APP.asm
    have = {(m["a"].get("component"), m["b"].get("component"), m.get("ref")) for m in asm.mates}
    made = []
    comps = asm.components
    for i, a in enumerate(comps):
        for b in comps[i + 1:]:
            if component and component not in (a.name, b.name):
                continue
            ra = {r["name"]: r for _, r in _component_infos(a, "mate_reference")}
            rb = {r["name"]: r for _, r in _component_infos(b, "mate_reference")}
            for name in sorted(ra.keys() & rb.keys()):
                if (a.name, b.name, name) in have:
                    continue
                m = asm.add_mate(ra[name]["type"], {"component": a.name, "face": ra[name]["face"]},
                                 {"component": b.name, "face": rb[name]["face"]}, None, ra[name].get("aligned", False))
                m["ref"] = name
                made.append(m["name"])
    return {"mates": made, "assembly": asm.summary()}


# ====================================================================== alternate positions, envelopes, display modes
@command(category="assembly")
def save_alternate_position(name: str) -> dict:
    """Alternate Position: remember where every component is now under a name (for drawings' alternate
    position views and to show again later)."""
    asm = APP.asm
    asm._checkpoint()
    asm.alternate_positions = getattr(asm, "alternate_positions", {}) or {}
    asm.alternate_positions[name] = {c.name: c.x.tolist() for c in asm.components}
    asm.dirty = True
    return {"alternate_positions": list(asm.alternate_positions)}


@command(category="assembly")
def show_alternate_position(name: str) -> dict:
    """Put the components back where an alternate position has them (the mates are then re-solved)."""
    import numpy as np
    asm = APP.asm
    pos = (getattr(asm, "alternate_positions", {}) or {}).get(name)
    if pos is None:
        raise DocumentError(f"no alternate position {name!r}")
    asm._checkpoint()
    for c in asm.components:
        if c.name in pos:
            c.x = np.array(pos[c.name], float)
    asm.solve()
    asm.revision += 1
    return asm.summary()


@command(category="assembly")
def delete_alternate_position(name: str) -> dict:
    """Delete a saved alternate position."""
    asm = APP.asm
    if name not in (getattr(asm, "alternate_positions", {}) or {}):
        raise DocumentError(f"no alternate position {name!r}")
    asm._checkpoint()
    del asm.alternate_positions[name]
    return {"alternate_positions": list(asm.alternate_positions)}


@command(category="assembly")
def set_envelope(component: str, envelope: bool = True) -> dict:
    """Envelope: a component kept for reference (shown see-through) but left out of mass properties, the
    BOM and interference detection."""
    asm = APP.asm
    c = asm.component(component)
    asm._checkpoint()
    c.envelope = envelope
    asm.revision += 1
    return asm.summary()


@command(category="assembly")
def set_speedpak(component: str, speedpak: bool = True) -> dict:
    """SpeedPak: show a component simplified (its small holes and fillets removed) to keep big assemblies fast;
    mates and mass properties still use the full part."""
    asm = APP.asm
    asm.component(component).speedpak = speedpak
    asm.revision += 1
    return asm.summary()


@command(category="assembly")
def large_assembly_mode(on: bool = True) -> dict:
    """Large Assembly Mode: a coarser display without edges, for speed (geometry is unchanged)."""
    asm = APP.asm
    asm.large_assembly_mode = on
    asm.revision += 1
    return asm.summary()


# ====================================================================== Defeature
def defeature_shape(body, max_hole: float | None = None, max_fillet: float | None = None):
    """Remove holes up to max_hole diameter and fillets / rounds up to max_fillet radius (defaults: 10% and 3%
    of the part's size) with OCC's defeaturing; if that fails the body is returned unchanged."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
    from build123d import Shape as _S, Vector as _V
    from openswx.core.geom import geom_name, interior_point
    size = body.bounding_box().diagonal
    max_hole = size * 0.10 if max_hole is None else max_hole
    max_fillet = size * 0.03 if max_fillet is None else max_fillet
    remove = []
    for f in body.faces():
        k = geom_name(f)
        ad = BRepAdaptor_Surface(f.wrapped)
        if k == "cylinder":
            cyl = ad.Cylinder()
            r = cyl.Radius()
            full = ad.LastUParameter() - ad.FirstUParameter() > 6.28
            q = interior_point(f)
            o, dd = cyl.Axis().Location(), cyl.Axis().Direction()
            axis_d = _V(dd.X(), dd.Y(), dd.Z())
            to_axis = _V(o.X(), o.Y(), o.Z()) - q
            to_axis = to_axis - axis_d * to_axis.dot(axis_d)
            hole = f.normal_at(q).dot(to_axis) > 0  # the face looks at its own axis: a hole
            if (full and hole and 2 * r <= max_hole) or (not full and r <= max_fillet):
                remove.append(f)
        elif k == "torus" and ad.Torus().MinorRadius() <= max_fillet:
            remove.append(f)
    if not remove:
        return body
    df = BRepAlgoAPI_Defeaturing()
    df.SetShape(body.wrapped)
    for f in remove:
        df.AddFaceToRemove(f.wrapped)
    df.SetRunParallel(False)
    df.Build()
    if not df.IsDone():
        return body
    return _S.cast(df.Shape())


@command(category="features")
def defeature(max_hole_diameter: float | None = None, max_fillet_radius: float | None = None) -> dict:
    """Defeature: a simplified copy of the active part (or of the whole assembly) as a new part document:
    holes up to max_hole_diameter and fillets up to max_fillet_radius removed (defaults scale with the size),
    for sharing a model without its details."""
    from openswx.core.document import PartDocument
    from openswx.core.features import encode_shape
    src = APP.active_doc
    body = src.body
    if body is None:
        raise DocumentError("nothing to defeature")
    simple = defeature_shape(body, max_hole_diameter, max_fillet_radius)
    name = f"{src.name}-Defeature"
    doc = PartDocument(name)
    doc.add_feature("import", {"brep": encode_shape(simple)})
    APP.documents[name] = doc
    APP.active = name
    return {"document": name, "faces_before": len(body.faces()), "faces_after": len(simple.faces()),
            "model": doc.summary()}


# ====================================================================== Pack and Go
@command(mutates=False, category="document")
def pack_and_go(folder: str, prefix: str = "", suffix: str = "", zip_file: str | None = None) -> dict:
    """Pack and Go: save the active document and every open document it uses (an assembly's parts) into a
    folder, renamed with a prefix / suffix; zip_file also packs the folder into a .zip."""
    import shutil
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    doc = APP.active_doc
    written = []

    def save(d):
        ext = ".oswasm" if getattr(d, "kind", "part") == "assembly" else ".oswprt"
        p = out / f"{prefix}{d.name}{suffix}{ext}"
        data = d.to_dict()
        data["name"] = f"{prefix}{d.name}{suffix}"
        p.write_text(json.dumps(data, indent=1), encoding="utf-8")
        written.append(str(p))
    save(doc)
    if getattr(doc, "kind", "part") == "assembly":
        for name in sorted({c.link for c in doc.components if c.link in APP.documents}):
            save(APP.documents[name])
    if zip_file:
        base = str(Path(zip_file).with_suffix(""))
        shutil.make_archive(base, "zip", out)
        return {"files": written, "zip": base + ".zip"}
    return {"files": written}


# ====================================================================== routing, weld beads
def _virtual_component(shape, base: str, description: str, properties: dict | None = None) -> dict:
    """Add a shape to the active assembly as a virtual (embedded) part component, fixed where it is."""
    from openswx.core.document import PartDocument
    from openswx.core.features import encode_shape
    asm = APP.asm
    n = 1 + sum(1 for c in asm.components if c.name.rsplit("-", 1)[0].startswith(base))
    doc = PartDocument(f"{base}{n}")
    doc.properties["Description"] = description
    for k, v in (properties or {}).items():
        doc.properties[k] = str(v)
    doc.add_feature("import", {"brep": encode_shape(shape)})
    comp = asm.insert(doc, None, fixed=True)
    return {"component": comp.name, "volume": shape.volume, "assembly": asm.summary()}


def _filleted_path(points, bend_radius: float):
    """A 3D polyline with each corner rounded by bend_radius (a tube's bends)."""
    import math as _m
    from build123d import Edge as _E, Vector as _V, Wire as _W
    P = [_V(*p) for p in points]
    if len(P) < 2:
        raise DocumentError("a route needs at least two points")
    edges, start = [], P[0]
    for i in range(1, len(P) - 1):
        a, b, c = P[i - 1], P[i], P[i + 1]
        u1, u2 = (a - b).normalized(), (c - b).normalized()
        theta = _m.acos(max(-1.0, min(1.0, u1.dot(u2))))
        if bend_radius <= 0 or theta > _m.pi - 1e-6:
            edges.append(_E.make_line(start, b))
            start = b
            continue
        t = bend_radius / _m.tan(theta / 2)
        if t > min((a - b).length, (c - b).length) - 1e-6:
            raise DocumentError("the bend radius is too large for the route's segments")
        t1, t2 = b + u1 * t, b + u2 * t
        centre = b + (u1 + u2).normalized() * (bend_radius / _m.sin(theta / 2))
        mid = centre + (b - centre).normalized() * bend_radius
        edges.append(_E.make_line(start, t1))
        edges.append(_E.make_three_point_arc(t1, mid, t2))
        start = t2
    edges.append(_E.make_line(start, P[-1]))
    return _W(edges)


def _sweep_circle(path, outer_d: float, inner_d: float = 0.0):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
    from build123d import Face as _F, Plane as _P, Shape as _S, Wire as _W
    pl = _P(path.position_at(0), z_dir=path.tangent_at(0))
    prof = _F(_W.make_circle(outer_d / 2, pl), [_W.make_circle(inner_d / 2, pl)] if inner_d > 0 else [])
    mk = BRepOffsetAPI_MakePipe(path.wrapped, prof.wrapped)
    mk.Build()
    if not mk.IsDone():
        raise DocumentError("the route could not be swept")
    return _S.cast(mk.Shape())


@command(category="assembly")
def route_pipe(points: list[Vec3], outer_diameter: float, wall_thickness: float, bend_radius: float = 0.0) -> dict:
    """Routing > Pipe / Tube: a pipe through points (assembly coordinates) with bends of bend_radius at the
    corners (0: sharp mitred corners), added as a virtual part ("Pipe<n>")."""
    if wall_thickness <= 0 or wall_thickness * 2 >= outer_diameter:
        raise DocumentError("the wall must be thinner than half the outer diameter")
    path = _filleted_path(points, bend_radius)
    shape = _sweep_circle(path, outer_diameter, outer_diameter - 2 * wall_thickness)
    out = _virtual_component(shape, "Pipe", f"Pipe OD {outer_diameter:g} x {wall_thickness:g}, length {path.length:.1f}")
    out["length"] = path.length
    return out


@command(mutates=False, category="assembly")
def list_pipe_standards() -> dict:
    """The piping library: standard pipe sizes (NPS) with OD and schedule 40 / 80 walls, and the B16.9
    elbows, tees, reducers, caps and B16.5 class 150 weld-neck flanges available per size."""
    from openswx.manufacturing import piping
    return piping.catalog()


def _pipe_props(q: dict) -> dict:
    props = {"Size": f"NPS {q['size']}", "Schedule": q["schedule"], "OD": f"{q['od']:g}", "Wall": f"{q['wall']:g}",
             "Fitting": q["kind"]}
    if q.get("length") is not None:
        props["Length"] = f"{q['length']:.1f}"
    if q.get("angle") is not None:
        props["Angle"] = f"{q['angle']:.1f}"
    return props


@command(category="assembly")
def route_pipe_standard(points: list[Vec3], size: str, schedule: Literal["40", "80"] = "40",
                        elbows: Literal["long", "short"] = "long", bend_radius: float | None = None,
                        flanges: Literal["both", "start", "end", "none"] = "both") -> dict:
    """Routing > Pipe from the piping library: a route of standard pipe (NPS size, schedule 40 / 80) through
    points (assembly coordinates, mm). Every corner gets a butt-weld elbow of the corner's angle (B16.9 long or
    short radius, or bend_radius in mm), the straight runs are cut to length between the fittings, and class 150
    weld-neck flanges go on the chosen ends. Each pipe, elbow and flange is a virtual component with Size,
    Schedule, Length / Angle properties for the BOM; the result lists them with the cut list."""
    from openswx.manufacturing import piping
    ends = {"both": ("start", "end"), "start": ("start",), "end": ("end",), "none": ()}[flanges]
    try:
        parts = piping.layout(points, str(size), str(schedule), bend_radius if bend_radius else elbows, ends)
    except ValueError as exc:
        raise DocumentError(str(exc)) from None
    made = []
    for q in parts:
        base = {"pipe": "Pipe", "elbow": "Elbow", "flange": "Flange"}[q["kind"]]
        r = _virtual_component(q["shape"], base, q["description"], _pipe_props(q))
        made.append({"component": r["component"], "kind": q["kind"], "description": q["description"],
                     **({"length": round(q["length"], 2)} if "length" in q else {}),
                     **({"angle": round(q["angle"], 2)} if "angle" in q else {})})
    cut = [m["length"] for m in made if m["kind"] == "pipe"]
    return {"parts": made, "cut_list": cut, "pipe_length": round(sum(cut), 2),
            "elbows": sum(m["kind"] == "elbow" for m in made), "flanges": sum(m["kind"] == "flange" for m in made),
            "assembly": APP.asm.summary()}


@command(category="assembly")
def insert_fitting(kind: Literal["elbow", "tee", "reducer", "cap", "flange"], size: str, position: Vec3,
                   direction: Vec3 = (0, 0, 1), turn_direction: Vec3 | None = None, schedule: Literal["40", "80"] = "40",
                   angle: float = 90.0, elbow_radius: Literal["long", "short"] = "long", small_size: str | None = None) -> dict:
    """Insert one fitting from the piping library into the assembly as a virtual component. position is the
    fitting's connection end (for a tee: its centre) and direction the way it runs from there (the pipe's
    axis); turn_direction is where an elbow turns to / a tee's branch points (square to direction). angle for
    elbows; small_size for a reducer's small end."""
    from openswx.manufacturing import piping
    size, schedule = str(size), str(schedule)
    try:
        if kind == "elbow":
            shape, R, t = piping.elbow_shape(size, schedule, angle, elbow_radius)
            desc = f"Elbow {angle:g} deg {'LR' if elbow_radius == 'long' else 'SR'} NPS {size} sch {schedule}"
        elif kind == "tee":
            shape, _ = piping.tee_shape(size, schedule)
            desc = f"Tee equal NPS {size} sch {schedule}"
        elif kind == "reducer":
            if not small_size:
                raise ValueError("a reducer needs small_size")
            shape, _ = piping.reducer_shape(size, str(small_size), schedule)
            desc = f"Reducer concentric NPS {size} x {small_size} sch {schedule}"
        elif kind == "cap":
            shape, _ = piping.cap_shape(size, schedule)
            desc = f"Cap NPS {size} sch {schedule}"
        else:
            shape, _ = piping.flange_shape(size, schedule)
            desc = f"Flange WN class 150 NPS {size}"
        spec = piping.pipe(size, schedule)
    except ValueError as exc:
        raise DocumentError(str(exc)) from None
    placed = piping.frame(position, direction, turn_direction) * shape
    props = _pipe_props({"size": size, "schedule": schedule, "od": spec["od"], "wall": spec["wall"], "kind": kind,
                         **({"angle": angle} if kind == "elbow" else {})})
    return _virtual_component(placed, kind.capitalize(), desc, props)


@command(category="assembly")
def route_cable(points: list[Vec3], diameter: float) -> dict:
    """Routing > Electrical: a cable / harness of `diameter` along a smooth curve through points (assembly
    coordinates, e.g. from connector to connector), added as a virtual part ("Cable<n>")."""
    from build123d import Edge as _E, Vector as _V, Wire as _W
    if len(points) < 2:
        raise DocumentError("a cable needs at least two points")
    pts = [_V(*p) for p in points]
    path = _W([_E.make_spline(pts)]) if len(pts) > 2 else _W([_E.make_line(pts[0], pts[1])])
    shape = _sweep_circle(path, diameter)
    out = _virtual_component(shape, "Cable", f"Cable D {diameter:g}, length {path.length:.1f}")
    out["length"] = path.length
    return out


@command(category="assembly")
def weld_bead(face1: str, face2: str, size: float) -> dict:
    """Weld Bead: a fillet weld of leg `size` along where two faces meet (a tee or corner joint): in an assembly
    ('Comp/FaceN', added as a virtual part "Weld Bead<n>"), or in the active part (a feature)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
    from build123d import Compound as _C, Face as _F, Shape as _S, Wire as _W
    from openswx.core.geom import face_normal
    from openswx.session import _measure_item
    f1, f2 = _measure_item(face1)[0], _measure_item(face2)[0]
    edges = [e for e in f1.edges() if any(e.is_same(x) for x in f2.edges())]  # faces of one body: their shared edges
    if not edges:  # separate bodies: where the faces cross / touch
        sec = BRepAlgoAPI_Section(f1.wrapped, f2.wrapped)
        sec.Build()
        edges = _C(sec.Shape()).edges()
    if not edges:
        raise DocumentError("the faces do not meet")
    beads = []
    for e in edges:
        p, t = e.position_at(0), e.tangent_at(0)
        n1, n2 = face_normal(f1, p), face_normal(f2, p)
        n1, n2 = n1 - t * n1.dot(t), n2 - t * n2.dot(t)  # across the joint
        tri = _F(_W.make_polygon([p, p + n2.normalized() * size, p + n1.normalized() * size], close=True))
        mk = BRepOffsetAPI_MakePipe(_W([e]).wrapped, tri.wrapped)
        mk.Build()
        beads.append(_S.cast(mk.Shape()))
    bead = beads[0] if len(beads) == 1 else _C(beads)
    length = sum(e.length for e in edges)
    if getattr(APP.active_doc, "kind", "part") == "assembly":
        out = _virtual_component(bead, "Weld Bead", f"Fillet weld {size:g} mm, length {length:.1f}")
        out["length"] = length
        return out
    from openswx.core.features import encode_shape
    return _feat("import", {"brep": encode_shape(bead)})



# ====================================================================== drawings: symbols, stacked balloons, layers, formats
__all__ += ["drawing_add_surface_finish", "drawing_add_weld_symbol", "drawing_add_stacked_balloon", "drawing_add_layer",
            "drawing_set_layer", "drawing_set_sheet_format", "drawing_save_sheet_format", "list_sheet_formats",
            "drawing_view_alternate_position"]


@command(category="drawing")
def drawing_add_surface_finish(x: float, y: float, ra: str | float | None = None,
                               process: Literal["any", "required", "prohibited"] = "required", lay: str | None = None,
                               leader: Vec2 | None = None, layer: str | None = None) -> dict:
    """Surface finish symbol (ISO 1302) with its tip at (x, y) sheet mm: process any / required (machining) /
    prohibited; ra the roughness (e.g. 1.6 or 'Rz 6.3'); lay symbol (=, X, M, C, R...); optional leader."""
    from openswx.session import _drawing_edit, _next_ann

    def f(st):
        it = {"id": _next_ann(st, "Finish"), "x": x, "y": y, "ra": ra, "process": process, "lay": lay, "leader": leader,
              "layer": layer}
        st.setdefault("finishes", []).append(it)
        return it
    return {"finish": _drawing_edit(f, annotations=True)}


@command(category="drawing")
def drawing_add_weld_symbol(x: float, y: float, leader: Vec2,
                            type: Literal["fillet", "v_groove", "square", "bevel", "plug", "spot", "u_groove", "j_groove"] = "fillet",
                            side: Literal["arrow", "other", "both"] = "arrow", size: str | float | None = None,
                            length: str | float | None = None, all_around: bool = False, field: bool = False,
                            process: str | None = None, layer: str | None = None) -> dict:
    """Weld symbol: reference line from (x, y) sheet mm with an arrow to the joint at `leader`; the weld's
    symbol on the arrow side, the other side or both; size and length; all-around and field-weld marks;
    process (e.g. 'MIG') in the tail."""
    from openswx.session import _drawing_edit, _next_ann

    def f(st):
        it = {"id": _next_ann(st, "Weld"), "x": x, "y": y, "leader": leader, "type": type, "side": side, "size": size,
              "length": length, "all_around": all_around, "field": field, "process": process, "layer": layer}
        st.setdefault("welds", []).append(it)
        return it
    return {"weld": _drawing_edit(f, annotations=True)}


@command(category="drawing")
def drawing_add_stacked_balloon(view: str, point: Vec2, items: list[str], x: float | None = None, y: float | None = None,
                                direction: Literal["down", "right"] = "down") -> dict:
    """Stacked balloon: one leader to the component at `point` and a stack of balloons - the component's item
    number first, then `items` (component names, which show their BOM numbers, or plain text) touching it."""
    from openswx.session import APP as _APP, _new_balloon, _views_edit

    def f(st, DS):
        v = DS.find_view(st, view)
        sh = DS.sheet_of(st, view)
        q = DS.sheet_to_view(st, _APP.active_doc, v, point)
        comp = DS.component_at(_APP.active_doc, st, v, q)
        b = _new_balloon(st, sh, view, q, x if x is not None else point[0] + 15, y if y is not None else point[1] - 15,
                         comp.name if comp else None, None if comp else (items[0] if items else "?"))
        b["stack"], b["direction"] = list(items if comp else items[1:]), direction
        return b
    return {"balloon": _views_edit(f)}


def _all_annotations(st):
    holders = [st] + list(st.get("sheets", []))
    for h in holders:
        for k in ("notes", "datums", "gdt", "finishes", "welds", "dims", "balloons"):
            yield from h.get(k, [])


@command(category="drawing")
def drawing_add_layer(name: str, color: str = "#000000", visible: bool = True) -> dict:
    """Drawing layer (or change one): a colour ('#rrggbb') and whether it shows; annotations and dimensions
    put on it (drawing_set_layer) take its colour and visibility."""
    from openswx.session import _drawing_edit

    def f(st):
        st.setdefault("layers", {})[name] = {"color": color, "visible": visible}
        return st["layers"]
    return {"layers": _drawing_edit(f)}


@command(category="drawing")
def drawing_set_layer(items: list[str], layer: str | None) -> dict:
    """Put annotations / dimensions (their ids: Note1, Dim3, Weld1...) on a layer (None: no layer)."""
    from openswx.session import _drawing_edit

    def f(st):
        if layer is not None and layer not in st.get("layers", {}):
            raise DocumentError(f"no layer {layer!r}: add it with drawing_add_layer")
        found = []
        for it in _all_annotations(st):
            if it.get("id") in items:
                it["layer"] = layer
                found.append(it["id"])
        missing = set(items) - set(found)
        if missing:
            raise DocumentError(f"no annotation {sorted(missing)[0]!r}")
        return found
    return {"moved": _drawing_edit(f)}


@command(mutates=False, category="drawing")
def list_sheet_formats() -> dict:
    """Sheet formats (title-block templates): the presets and the ones saved by the user."""
    from openswx.drawing.drawing_more import list_formats
    return list_formats()


@command(category="drawing")
def drawing_set_sheet_format(preset: str | None = None, format: dict | None = None) -> dict:
    """Use a sheet format for the drawing's title block: a preset / saved name ('ISO 7200', 'Minimal', ...),
    a format {name, width, height, cells: [{x, y, w, h, label, text, size, bold}]} (text can hold {title},
    {dwg_no}, {material}, {scale}, {sheet}, {date}, {drawn}, {rev}, {company}, {$PRP:"Name"}), or neither to go
    back to the standard block."""
    from openswx.drawing.drawing_more import check_format, list_formats
    from openswx.session import _drawing_edit
    if preset is not None:
        allf = list_formats()
        fmt = allf["saved"].get(preset) or allf["presets"].get(preset)
        if fmt is None:
            raise DocumentError(f"no sheet format {preset!r}")
    else:
        fmt = format
    try:
        fmt = check_format(fmt) if fmt is not None else None
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc

    def f(st):
        if fmt is None:
            st.pop("format", None)
        else:
            st["format"] = fmt
        return fmt
    return {"format": _drawing_edit(f)}


@command(mutates=False, category="drawing")
def drawing_save_sheet_format(name: str) -> dict:
    """Save the drawing's current sheet format under a name, for other drawings."""
    from openswx.drawing.drawing_more import formats_dir
    from openswx.session import _drawing_state
    fmt = _drawing_state().get("format")
    if not fmt:
        raise DocumentError("the drawing uses the standard title block: set a sheet format first")
    p = formats_dir() / f"{name}.json"
    p.write_text(json.dumps({**fmt, "name": name}, indent=1), encoding="utf-8")
    return {"saved": str(p)}


@command(category="drawing")
def drawing_view_alternate_position(view: str, position: str | None) -> dict:
    """Alternate Position View: show an assembly view's alternate position (save_alternate_position) over it in
    phantom lines; None removes it."""
    from openswx.session import APP as _APP, _views_edit
    if position is not None and position not in (getattr(_APP.active_doc, "alternate_positions", {}) or {}):
        raise DocumentError(f"no alternate position {position!r}")

    def f(st, DS):
        v = DS.find_view(st, view)
        if position is None:
            v.pop("alt_position", None)
        else:
            v["alt_position"] = position
        return v
    return {"view": _views_edit(f)}



# ====================================================================== analysis: nonlinear, dynamics, drop, fatigue
__all__ += ["nonlinear_run", "harmonic_run", "step_response_run", "drop_test_run", "fatigue_run", "fatigue_results",
            "flow_simulation", "flow_results", "cfd_external", "cfd_internal", "cfd_results",
            "costing", "sustainability_report", "dimxpert_auto", "dimxpert_clear",
            "set_render_scene", "add_decal", "delete_decal"]


def _part_study():
    from openswx.session import _resolve_study, _sim_doc
    doc = _sim_doc()
    if getattr(doc, "kind", "part") == "assembly":
        raise DocumentError("this study runs on a part: open the part")
    if doc.body is None:
        raise DocumentError("the part has no body")
    if not (doc.material or {}).get("elastic_modulus"):
        raise DocumentError("assign a library material (with elastic modulus) first")
    return doc, _resolve_study(doc)


def _public(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}


@command(mutates=False, category="simulation")
def nonlinear_run(steps: int = 10, hardening: float = 0.05, element_size: float | None = None,
                  mesh: Literal["auto", "tetrahedral", "voxel"] = "auto") -> dict:
    """Nonlinear static study (elastic-plastic): bilinear material - elastic to the yield strength, then a
    tangent modulus of `hardening` x E - with the study's fixtures and loads applied in `steps` increments.
    Returns the load-displacement curve, max stress, plastic elements and plastic strain; plot with
    simulation_results. mesh: as for simulation_run."""
    from openswx.analysis import fea_tet
    from openswx.commands.simulation import _mesh_kind
    from openswx.analysis.fea_more import solve_nonlinear
    doc, (fixtures, loads, _con) = _part_study()
    try:
        with unlocked():
            solver = fea_tet.solve_nonlinear if _mesh_kind(mesh) == "tetrahedral" else solve_nonlinear
            r = solver(doc.body, doc.material, fixtures, loads, steps, hardening, element_size or doc.study.get("element_size"))
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.sim_result = (doc.revision, r)
    return {"study": "nonlinear", **_public(r)}


@command(mutates=False, category="simulation")
def harmonic_run(f_min: float, f_max: float, points: int = 200, damping: float = 0.02, modes: int = 10,
                 element_size: float | None = None, mesh: Literal["auto", "tetrahedral", "voxel"] = "auto") -> dict:
    """Dynamic (harmonic) study: the study's loads as F sin(2 pi f t), swept from f_min to f_max Hz; steady
    amplitude of the largest displacement against frequency (modal superposition, modal damping ratio).
    mesh: as for simulation_run."""
    from openswx.commands.simulation import _mesh_kind
    from openswx.analysis.fea_more import solve_harmonic
    if not 0 <= f_min < f_max:
        raise DocumentError("give 0 <= f_min < f_max")
    doc, (fixtures, loads, _con) = _part_study()
    try:
        return {"study": "harmonic", **solve_harmonic(doc.body, doc.material, fixtures, loads, f_min, f_max, points, damping,
                                                      modes, element_size or doc.study.get("element_size"), mesh=_mesh_kind(mesh))}
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc


@command(mutates=False, category="simulation")
def step_response_run(duration: float, points: int = 400, damping: float = 0.02, modes: int = 10,
                      element_size: float | None = None, mesh: Literal["auto", "tetrahedral", "voxel"] = "auto") -> dict:
    """Dynamic (transient) study: the study's loads applied suddenly and held; displacement against time over
    `duration` seconds, the peak and the dynamic amplification over the static value. mesh: as for
    simulation_run."""
    from openswx.commands.simulation import _mesh_kind
    from openswx.analysis.fea_more import solve_step_response
    if duration <= 0:
        raise DocumentError("duration must be positive")
    doc, (fixtures, loads, _con) = _part_study()
    try:
        return {"study": "transient", **solve_step_response(doc.body, doc.material, fixtures, loads, duration, points, damping,
                                                            modes, element_size or doc.study.get("element_size"), mesh=_mesh_kind(mesh))}
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc


@command(mutates=False, category="simulation")
def drop_test_run(height: float, impact_faces: list[str], direction: Vec3 = (0, 0, -1),
                  element_size: float | None = None, mesh: Literal["auto", "tetrahedral", "voxel"] = "auto") -> dict:
    """Drop test: the part falls `height` mm along `direction` onto a rigid floor, landing on impact_faces
    ('FaceN'). Energy method: peak deceleration (g), stress and deflection at impact, factor of safety; plot
    with simulation_results. mesh: as for simulation_run."""
    from openswx.analysis import fea_tet
    from openswx.commands.simulation import _mesh_kind
    from openswx.analysis.fea_more import solve_drop
    from openswx.core.geom import resolve_face
    doc, _study = _part_study()
    if not impact_faces:
        raise DocumentError("pick the faces the part lands on")
    faces = [resolve_face(doc.body, _face(doc, f)) for f in impact_faces]
    try:
        with unlocked():
            solver = fea_tet.solve_drop if _mesh_kind(mesh) == "tetrahedral" else solve_drop
            r = solver(doc.body, doc.material, faces, height, direction, element_size or doc.study.get("element_size"))
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.sim_result = (doc.revision, r)
    return {"study": "drop_test", **_public(r)}


@command(mutates=False, category="simulation")
def fatigue_run(cycles: float = 1e6, loading: Literal["reversed", "zero_based"] = "reversed", scale: float = 1.0) -> dict:
    """Fatigue study on the last static result (simulation_run): the stress cycles `cycles` times, fully
    reversed or zero-to-peak, scaled by `scale`. S-N curve from the material's tensile strength; returns the
    shortest life, Miner damage, fatigue factor of safety; fatigue_results plots life per vertex."""
    from openswx.analysis.fea_more import fatigue_life
    from openswx.session import _sim_doc
    cached = getattr(APP, "sim_result", None)
    doc = _sim_doc()
    if not cached or cached[0] != doc.revision or "_grid" not in cached[1]:
        raise DocumentError("run the static study first (the model changed since the last run)")
    try:
        r = fatigue_life(cached[1]["_grid"]["vm"], doc.material or {}, cycles, loading, scale)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.fatigue_result = (doc.revision, cached[1], r)
    return {"study": "fatigue", "cycles": cycles, "loading": loading, **_public(r)}


@command(mutates=False, category="simulation", mcp=False)
def fatigue_results() -> dict:
    """Per display-mesh vertex log10(life cycles) of the last fatigue_run (UI plot; 12 = infinite life)."""
    import numpy as np
    from openswx.analysis.fea import map_to_mesh, refine_mesh
    from openswx.session import _sim_doc
    cached = getattr(APP, "fatigue_result", None)
    if not cached or cached[0] != _sim_doc().revision:
        raise DocumentError("run the fatigue study first")
    static, r = cached[1], cached[2]
    g = dict(static["_grid"], vm=np.log10(np.maximum(r["_life"], 1.0)))
    mesh = refine_mesh(_sim_doc().mesh(), 2 * static["element_size"])
    out = map_to_mesh({"_grid": g}, mesh)
    return {"faces": mesh["faces"], "log_life": out["stress"], **_public(r)}


# ====================================================================== flow, costing, sustainability
@command(mutates=False, category="simulation")
def flow_simulation(speed: float = 10.0, direction: Vec3 = (1, 0, 0), fluid: Literal["air", "water"] = "air",
                    cells_across: int = 20) -> dict:
    """Flow Simulation (external, potential flow): the part in a free stream of `speed` m/s along `direction`.
    Surface pressure coefficient and velocity, net pressure (side / lift) force and a skin-friction drag
    estimate. Ideal (inviscid) flow on a voxel grid - a first-look estimate, not turbulent CFD: no separation,
    wake or pressure drag. flow_results plots Cp."""
    from openswx.analysis.analysis_more import flow_study
    from openswx.session import _sim_doc
    doc = _sim_doc()
    if doc.body is None:
        raise DocumentError("the document has no body")
    if speed <= 0:
        raise DocumentError("speed must be positive")
    try:
        r = flow_study(doc.body, direction, speed, fluid, cells_across)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.flow_result = (doc.revision, r)
    return {"study": "flow", **_public(r)}


@command(mutates=False, category="simulation", mcp=False)
def flow_results() -> dict:
    """Per display-mesh vertex pressure coefficient Cp of the last flow_simulation (UI plot)."""
    from openswx.analysis.analysis_more import flow_surface_values
    from openswx.session import _sim_doc
    cached = getattr(APP, "flow_result", None)
    if not cached or cached[0] != _sim_doc().revision:
        raise DocumentError("run the flow simulation first")
    mesh = _sim_doc().mesh()
    return {"faces": mesh["faces"], "cp": flow_surface_values(cached[1], mesh), **_public(cached[1])}


def _cfd_body():
    from openswx.session import _sim_doc
    doc = _sim_doc()
    if doc.body is None:
        raise DocumentError("the document has no body")
    return doc


@command(mutates=False, category="simulation")
def cfd_external(speed: float = 10.0, direction: Vec3 = (1, 0, 0), fluid: Literal["air", "water"] = "air",
                 cells_across: int = 16, max_seconds: float = 300.0) -> dict:
    """Flow Simulation (Navier-Stokes, external): the part in a free stream of `speed` m/s along `direction`,
    solved with the lattice Boltzmann method on a voxel grid (cells_across: cells over the part's size square
    to the flow). Drag and lift (forces in N and coefficients on the frontal area), surface pressure
    coefficient, wake slice; laminar flows are within ~10 % of reference drag, high Reynolds numbers are a
    coarse large-eddy estimate (see `warning`). cfd_results plots the pressure and the speed slice."""
    from openswx.analysis.cfd import external_flow
    doc = _cfd_body()
    if speed <= 0:
        raise DocumentError("speed must be positive")
    try:
        with unlocked():
            r = external_flow(doc.body, direction, speed, fluid, cells_across, budget=max_seconds)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.cfd_result = (doc.revision, r)
    return {"study": "cfd_external", **_public(r)}


@command(mutates=False, category="simulation")
def cfd_internal(axis: Literal["X", "Y", "Z"] = "X", speed: float = 1.0, fluid: Literal["air", "water"] = "water",
                 cells_across: int = 16, max_seconds: float = 300.0) -> dict:
    """Flow Simulation (Navier-Stokes, internal, like FloXpress): fluid through the part's internal passage
    along `axis` - in at the low end, out at the high end - at a mean inlet velocity of `speed` m/s. Pressure
    drop between the end faces, flow rate, Reynolds number and a speed slice (cfd_results); laminar pipe flow
    matches Hagen-Poiseuille within ~3 %."""
    from openswx.analysis.cfd import internal_flow
    doc = _cfd_body()
    if speed <= 0:
        raise DocumentError("speed must be positive")
    try:
        with unlocked():
            r = internal_flow(doc.body, axis, speed, fluid, cells_across, budget=max_seconds)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.cfd_result = (doc.revision, r)
    return {"study": "cfd_internal", **_public(r)}


@command(mutates=False, category="simulation", mcp=False)
def cfd_results() -> dict:
    """The last cfd_external / cfd_internal study for the UI: per display-mesh vertex pressure coefficient
    (external flow) and the mid-plane speed slice."""
    from openswx.analysis.cfd import surface_values
    doc = _cfd_body()
    cached = getattr(APP, "cfd_result", None)
    if not cached or cached[0] != doc.revision:
        raise DocumentError("run the flow simulation first")
    r = cached[1]
    out = {**_public(r), "slice": r["_slice"]}
    if "_surface" in r:
        mesh = doc.mesh()
        out.update(faces=mesh["faces"], cp=surface_values(r, mesh))
    return out


@command(mutates=False, category="evaluate")
def costing(method: Literal["machining", "sheet_metal", "additive", "casting"] = "machining", quantity: int = 1,
            rate_per_hour: float = 80.0, setup_hours: float = 1.0, price_per_kg: float | None = None) -> dict:
    """Costing: should-cost estimate of the active part (USD, generic rates - set the shop rate and material
    price per kg for your own): machining from stock, sheet metal (blank, cutting, bends), additive or
    casting, with a per-item breakdown and the setup / tooling shared over the quantity."""
    from openswx.analysis.analysis_more import cost_estimate
    try:
        return cost_estimate(APP.doc, method, quantity, rate_per_hour, setup_hours, price_per_kg)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc


@command(mutates=False, category="evaluate")
def sustainability_report(region: Literal["Asia", "Europe", "North America", "Oceania", "South America"] = "Oceania",
                          process: Literal["machining", "casting", "sheet_metal", "additive"] = "machining",
                          transport_km: float = 1000.0, recycled_fraction: float = 0.0) -> dict:
    """Sustainability: carbon footprint (kg CO2e), total energy (MJ) and water (L) of the active part by
    life-cycle phase - material, manufacturing (on the region's grid), transport, end of life - from generic
    published figures for its material class."""
    from openswx.analysis.analysis_more import sustainability
    try:
        return sustainability(APP.doc, region, process, transport_km, recycled_fraction)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc


# ====================================================================== DimXpert / MBD
def _doc_edit(doc, fn):
    doc._checkpoint()
    out = fn(doc)
    doc.revision += 1
    doc.dirty = True
    return out


@command(category="mbd")
def dimxpert_auto(general_tolerance: float = 0.1, position_tolerance: float = 0.1) -> dict:
    """DimXpert Auto Dimension Scheme: datums A/B/C on the main planar faces, the overall sizes with a
    general tolerance, and hole patterns (count x diameter H7) with a position tolerance to the datums, as 3D
    annotations (PMI) shown on the model and saved with it (model-based definition)."""
    from openswx.analysis.analysis_more import dimxpert_scheme
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body")
    try:
        pmi = dimxpert_scheme(doc.body, general_tolerance, position_tolerance)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc

    def f(d):
        d.pmi = pmi
        return pmi
    return {"pmi": _doc_edit(doc, f)}


@command(category="mbd")
def dimxpert_clear() -> dict:
    """Delete the part's DimXpert annotations (PMI)."""
    def f(d):
        d.pmi = []
    _doc_edit(APP.doc, f)
    return {"pmi": []}


# ====================================================================== rendering (scene and decals)
@command(category="render")
def set_render_scene(scene: Literal["studio", "warehouse", "outdoor", "plain_white", "dark"] = "studio",
                     floor: bool = True, shadows: bool = True, exposure: float = 1.0,
                     background: str | None = None) -> dict:
    """Render scene for photo rendering (PhotoView-style): lighting environment, floor with shadows, exposure
    and an optional background colour '#rrggbb'. Saved with the document; the Render Tools pane renders it."""
    def f(d):
        d.render = {**(getattr(d, "render", {}) or {}), "scene": scene, "floor": floor, "shadows": shadows,
                    "exposure": exposure, "background": background}
        return {k: v for k, v in d.render.items() if k != "decals"}
    return {"render": _doc_edit(APP.active_doc, f)}


@command(category="render")
def add_decal(face: str, image: str, width: float = 20.0, rotation: float = 0.0, name: str | None = None) -> dict:
    """Decal: an image (PNG/JPEG file path or data: URL) on a face of the active part, centred on the face,
    `width` mm wide and rotated by `rotation` degrees. Shown in the viewport and in renders."""
    import base64
    import mimetypes
    from openswx.core.geom import face_normal, interior_point, resolve_face
    doc = APP.doc
    if not image.startswith("data:"):
        p = Path(image)
        if not p.is_file():
            raise DocumentError(f"no image file {image!r}")
        mime = mimetypes.guess_type(p.name)[0] or ""
        if mime not in ("image/png", "image/jpeg"):
            raise DocumentError("decals need a PNG or JPEG image")
        image = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
    elif not image.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
        raise DocumentError("decals need a PNG or JPEG image")
    if width <= 0:
        raise DocumentError("width must be positive")
    ref = _face(doc, face)
    fc = resolve_face(doc.body, ref)
    c = interior_point(fc)

    def f(d):
        r = getattr(d, "render", {}) or {}
        decals = r.setdefault("decals", [])
        nm = name or f"Decal{len(decals) + 1}"
        if any(x["name"] == nm for x in decals):
            raise DocumentError(f"decal {nm!r} exists")
        it = {"name": nm, "face": ref, "image": image, "width": width, "rotation": rotation, "center": list(c),
              "normal": list(face_normal(fc, c))}
        decals.append(it)
        d.render = r
        return {k: v for k, v in it.items() if k != "image"}
    return {"decal": _doc_edit(doc, f)}


@command(category="render")
def delete_decal(name: str) -> dict:
    """Delete a decal by name."""
    doc = APP.doc
    decals = (getattr(doc, "render", {}) or {}).get("decals", [])
    if not any(x["name"] == name for x in decals):
        raise DocumentError(f"no decal {name!r}")

    def f(d):
        d.render["decals"] = [x for x in d.render["decals"] if x["name"] != name]
    _doc_edit(doc, f)
    return {"deleted": name}



# ====================================================================== PDM vault
__all__ += ["pdm_set_vault", "pdm_list", "pdm_check_in", "pdm_check_out", "pdm_undo_check_out", "pdm_get_version",
            "pdm_change_state", "pdm_history", "pdm_search", "pdm_where_used", "export_edrawing", "macro_start",
            "macro_stop", "macro_status", "run_macro"]


def _vault():
    from openswx.manufacturing.ecosystem import Vault
    cfg = openswx_home() / "pdm.json"
    root = None
    if cfg.is_file():
        try:
            root = json.loads(cfg.read_text(encoding="utf-8")).get("vault")
        except ValueError:
            root = None
    return Vault(Path(root) if root else openswx_home() / "vault")


def _pdm(fn):
    from openswx.manufacturing.ecosystem import VaultError
    try:
        return fn(_vault())
    except VaultError as exc:
        raise DocumentError(str(exc)) from exc


@command(mutates=False, category="pdm")
def pdm_set_vault(path: str) -> dict:
    """PDM: use the vault folder at `path` (shared network folders work; created if missing)."""
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    openswx_home().mkdir(parents=True, exist_ok=True)
    (openswx_home() / "pdm.json").write_text(json.dumps({"vault": str(root)}), encoding="utf-8")
    return {"vault": str(root)}


@command(mutates=False, category="pdm")
def pdm_list() -> dict:
    """PDM: the vault's files with their latest version, revision, workflow state and who has them checked out."""
    return _pdm(lambda v: {"vault": str(v.root), "files": v.files()})


@command(mutates=False, category="pdm")
def pdm_check_in(path: str | None = None, comment: str = "", keep_checked_out: bool = False) -> dict:
    """PDM Check In: store the file (default the active document, which must be saved) as the next version in
    the vault with a comment; new files are added. The file must be checked out to you (except when new)."""
    if path is None:
        doc = APP.active_doc
        if not getattr(doc, "path", None):
            raise DocumentError("save the document first")
        if doc.dirty:
            raise DocumentError("the document has unsaved changes: save it first")
        path = doc.path
    return _pdm(lambda v: v.check_in(Path(path), comment, keep_checked_out))


@command(mutates=False, category="pdm")
def pdm_check_out(name: str, dest_dir: str | None = None, open: bool = True) -> dict:
    """PDM Check Out: lock a vault file for you and copy its latest version to dest_dir (default the local
    workspace); opens it unless open=False. Others can view but not check in until you check it back in."""
    dest = Path(dest_dir) if dest_dir else openswx_home() / "workspace"
    out = _pdm(lambda v: v.check_out(name, dest))
    if open and Path(out["path"]).suffix in (".oswprt", ".oswasm", ".oswdrw"):
        from openswx.session import open_part
        open_part(out["path"])
    return out


@command(mutates=False, category="pdm")
def pdm_undo_check_out(name: str) -> dict:
    """PDM Undo Check Out: release your lock without storing a new version."""
    return _pdm(lambda v: v.undo_check_out(name))


@command(mutates=False, category="pdm")
def pdm_get_version(name: str, version: int | None = None, dest_dir: str | None = None) -> dict:
    """PDM Get Version: copy a version (default the latest) of a vault file to dest_dir (read-only use; no lock)."""
    dest = Path(dest_dir) if dest_dir else openswx_home() / "workspace"
    return {"path": str(_pdm(lambda v: v.get(name, version, dest)))}


@command(mutates=False, category="pdm")
def pdm_change_state(name: str, state: Literal["Work in Progress", "Pending Approval", "Released"],
                     comment: str = "") -> dict:
    """PDM workflow: Work in Progress -> Pending Approval -> Released (or back to Work in Progress). Releasing
    gives the next revision letter (A, B, ...) and blocks changes until it goes back to Work in Progress."""
    return _pdm(lambda v: v.change_state(name, state, comment))


@command(mutates=False, category="pdm")
def pdm_history(name: str) -> dict:
    """PDM History: versions (comment, user, date, size), revisions and the check-in/out and state log."""
    return _pdm(lambda v: v.history(name))


@command(mutates=False, category="pdm")
def pdm_search(text: str) -> dict:
    """PDM Search: vault files whose name, check-in comments or custom properties contain the text."""
    return _pdm(lambda v: {"files": v.search(text)})


@command(mutates=False, category="pdm")
def pdm_where_used(name: str) -> dict:
    """PDM Where Used: vault assemblies that use the file."""
    return _pdm(lambda v: {"name": name, "used_in": v.where_used(name)})


# ====================================================================== eDrawings-style export
def viewer_payload(doc) -> dict:
    """The model for the single-file HTML viewer: faces / edges (per component in an assembly), properties, PMI."""
    from openswx.core.features import mass_properties
    if doc.body is None:
        raise DocumentError("the document has no body")
    mesh = doc.mesh()
    asm = getattr(doc, "kind", "part") == "assembly"
    colors = {}
    if asm:
        for c in doc.components:
            colors[c.name] = ((getattr(c.doc(doc.live_docs), "material", {}) or {}).get("color")) if not c.is_subassembly else None
    faces = []
    for f in mesh["faces"]:
        comp = f.get("component")
        faces.append({"v": f["v"], "t": f["t"], "color": f.get("color") or colors.get(comp), "component": comp,
                      "envelope": f.get("envelope", False)})
    edges = [{"p": e["p"], "component": e["id"].split("/")[0] if asm else None} for e in mesh["edges"]]
    mat = getattr(doc, "material", {}) or {}
    props = {"Name": doc.name, "Type": "Assembly" if asm else "Part"}
    if not asm:
        mp = mass_properties(doc.body, mat.get("density") or 1000.0)
        props.update({"Material": mat.get("name") or "-", "Mass": f"{mp['mass'] / 1000:.4g} kg",
                      "Volume": f"{mp['volume']:.6g} mm³"})
    props.update({k: str(v) for k, v in (getattr(doc, "properties", {}) or {}).items()})
    return {"name": doc.name, "units": getattr(doc, "units", "mm"), "color": mat.get("color"), "faces": faces,
            "edges": edges, "properties": props, "components": [c.name for c in doc.components] if asm else [],
            "pmi": [] if asm else list(getattr(doc, "pmi", []) or [])}


def write_viewer(path: str, title: str, payload: dict) -> Path:
    from openswx.manufacturing.ecosystem import viewer_html
    three = (Path(__file__).parent.parent / "web" / "vendor" / "three.module.min.js").read_text(encoding="utf-8")
    p = Path(path)
    if p.suffix.lower() not in (".html", ".htm"):
        p = p.with_suffix(".html")
    p.write_text(viewer_html(title, payload, three), encoding="utf-8")
    return p


@command(mutates=False, category="file")
def export_edrawing(path: str) -> dict:
    """Publish an eDrawings-style viewer: one self-contained .html file (no install needed; opens in any
    browser) with the model, its properties and PMI, rotate / pan / zoom, standard views, section, measure,
    component visibility and markup (pen and notes, saved as PNG / JSON)."""
    doc = APP.active_doc
    payload = viewer_payload(doc)
    p = write_viewer(path, doc.name, payload)
    return {"path": str(p.resolve()), "faces": len(payload["faces"]), "size_bytes": p.stat().st_size}


# ====================================================================== macros
@command(mutates=False, category="tools")
def macro_start() -> dict:
    """Tools > Macro > Record: record the model-changing commands run from now on (UI or API)."""
    APP.macro = []
    return {"recording": True}


@command(mutates=False, category="tools")
def macro_status() -> dict:
    """Whether a macro is recording, and how many commands it holds."""
    return {"recording": APP.macro is not None, "commands": len(APP.macro or [])}


@command(mutates=False, category="tools")
def macro_stop(path: str | None = None) -> dict:
    """Tools > Macro > Stop: end recording and save the macro as a Python script (.py) replaying the commands
    through the openswx API (default name in the macros folder)."""
    import datetime
    from openswx.manufacturing.ecosystem import macro_source
    if APP.macro is None:
        raise DocumentError("no macro is recording")
    entries, APP.macro = APP.macro, None
    p = Path(path) if path else openswx_home() / "macros" / f"macro-{datetime.datetime.now():%Y%m%d-%H%M%S}.py"
    if p.suffix != ".py":
        p = p.with_suffix(".py")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(macro_source(entries), encoding="utf-8")
    return {"path": str(p.resolve()), "commands": len(entries)}


@command(mutates=False, category="tools", mcp=False)
def run_macro(path: str) -> dict:
    """Tools > Macro > Run: execute a macro (.py, Python using the openswx API as `S`). Macros run with your
    permissions: run only ones you trust."""
    import runpy
    from openswx import session
    p = Path(path)
    if not p.is_file() or p.suffix != ".py":
        raise DocumentError(f"no macro file {path!r}")
    before = len(APP.log)
    try:
        runpy.run_path(str(p), init_globals={"S": session}, run_name="__main__")
    except session.CommandError as exc:
        raise DocumentError(f"the macro stopped: {exc}") from exc
    return {"path": str(p.resolve()), "commands": len(APP.log) - before}
