"""Assembly tools: hole series, mates, alternate positions, envelopes and Pack and Go."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from openswx.core.document import DocumentError
from openswx.commands.support import _ext_params, _face
from openswx.session import APP, Vec3, command


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


@command(category="assembly")
def smart_mate(entity1: str, entity2: str) -> dict:
    """SmartMates: mate two component faces with the mate their shapes call for - planar + planar:
    coincident (face to face), cylinder + cylinder: concentric, planar + cylinder: tangent."""
    from openswx.commands.support import _measure_item
    from openswx.session import add_mate
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


__all__ = ["hole_series", "smart_mate", "connection_point", "mate_reference", "magnetic_mate",
           "magnetic_snap", "apply_mate_references", "save_alternate_position",
           "show_alternate_position", "delete_alternate_position", "set_envelope", "set_speedpak",
           "large_assembly_mode", "pack_and_go"]
