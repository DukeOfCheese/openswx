"""Document, query, history, configuration and equation commands (same registry as session.py, which re-exports them).

Moved out of session.py; helpers come from it by name, and other commands through `_S` at call time."""
from __future__ import annotations

from typing import Literal

from . import session as _S
from .session import (  # noqa: F401
    APP, COMMANDS, DocumentError, END_CONDITIONS, PartDocument, Path, Vec3, _TL, _context_meshes, _edge, _face,
    _import_file, _is_edge_sel, _is_face_sel, _measure_item, _open_sldasm_native, _recreate_drawing, _sw_properties,
    command, json, mass_properties, math, re,
)

__all__ = ["new_part", "open_part", "save_part", "recent_files", "clear_recent_files", "save_drawing_document", "list_documents", "activate_document",
           "close_document", "import_file", "export_file", "get_model", "get_feature", "get_topology",
           "get_mass_properties", "list_end_conditions", "get_mesh", "get_activity", "measure", "save_named_view",
           "delete_named_view", "face_features", "measure_items", "undo", "redo", "rebuild", "edit_feature",
           "redefine_feature", "delete_feature", "get_design_table", "apply_design_table", "delete_design_table",
           "reorder_feature", "add_folder", "remove_folder", "rename_feature", "suppress_feature",
           "set_global_variable", "delete_global_variable", "set_equation", "clear_equation", "get_equations",
           "add_configuration", "activate_configuration", "delete_configuration", "set_configuration_value",
           "export_bom", "rollback", "set_material", "save_custom_material", "delete_custom_material",
           "list_materials", "set_appearance", "set_custom_property", "set_units", "parent_child", "get_view_image"]


# ====================================================================== document commands
@command(category="document")
def new_part(name: str | None = None) -> dict:
    """Create a new, empty part document (like File > New > Part) and make it active."""
    doc = APP.new_document(name)
    from . import settings
    doc.units = settings.get("units")  # (Tools > Options: units of new documents)
    return doc.summary()


@command(category="document")
def open_part(path: str, history: bool = True) -> dict:
    """Open a document. .oswprt/.oswasm open natively; .step/.stp/.brep and Parasolid .x_t/.x_b are imported
    as a base feature. .sldprt: with history (default) the feature tree is rebuilt as editable openswx
    features when every feature is understood and the result matches the file's embedded Parasolid body;
    otherwise that body is imported as one feature. .sldasm: components (each part read natively) at their
    saved positions, with the common mates. Everything is read natively: openswx never uses SOLIDWORKS,
    installed or not."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    APP.drop_throwaway()  # (an untouched Part1 made on demand gives way to the opened document)
    res = _open(p, history)
    if not set(res) <= {"solidworks_file", "geometry"}:  # (a document was opened, not only the container read)
        from . import recent
        recent.add(p)
    return res


def _open(p: Path, history: bool) -> dict:
    ext = p.suffix.lower()
    if ext == ".oswprt":
        doc = PartDocument.open(str(p))
        APP.documents[doc.name] = doc
        APP.active = doc.name
        return doc.summary()
    if ext in (".step", ".stp", ".brep", ".x_t", ".x_b", ".xmt_txt", ".xmt_bin", ".igs", ".iges", ".stl", ".3mf", ".fcstd"):
        doc = APP.new_document(p.stem)
        _import_file(doc, str(p))
        return doc.summary()
    if ext == ".oswdrw":  # a drawing document: open (or reuse) its model, then show the drawing
        data = json.loads(p.read_text(encoding="utf-8"))
        from . import migrate
        try:
            data = migrate.migrate(data, "openswx-drawing")
        except migrate.FormatError as exc:
            raise DocumentError(f"{p.name}: {exc}") from exc
        ref = Path(data["model"])
        model = (ref if ref.is_absolute() else p.parent / ref).resolve()
        doc = next((d for d in APP.documents.values() if getattr(d, "path", None) and Path(d.path).resolve() == model), None)
        if doc is None:
            if not model.exists():
                raise DocumentError(f"the drawing's model {model} is missing")
            _S.open_part(str(model))
            doc = APP.active_doc
        else:
            APP.active = next(n for n, d in APP.documents.items() if d is doc)
        doc.drawing = data.get("drawing") or {}
        doc.drawing_path = str(p.resolve())
        return {**doc.summary(), "open_drawing": True}
    if ext == ".oswasm":
        import json as _json
        from .assembly import AssemblyDocument
        asm = AssemblyDocument(p.stem)
        asm.live_docs = APP.documents
        asm.load_dict(_json.loads(p.read_text(encoding="utf-8")))
        asm.path, asm.dirty = str(p), False
        APP.documents[asm.name] = asm
        APP.active = asm.name
        return asm.summary()
    if ext == ".slddrw":  # a SOLIDWORKS drawing: open its model natively and recreate the sheets and views
        from .sw_drawing import find_model, read_drawing
        drw = read_drawing(str(p))
        model = find_model(str(p), drw["models"])
        if model is None:
            raise DocumentError(f"{p.name}: its model ({', '.join(Path(m.replace(chr(92), '/')).name for m in drw['models']) or 'none named'}) "
                                "is not beside the drawing; put the part / assembly file next to it")
        res = _S.open_part(str(model), history)
        APP.active_doc.show_drawing = True  # the UI opens the drawing editor on the next refresh
        return {**_recreate_drawing(drw), "model_file": str(model), "open_drawing": True, "model": APP.active_doc.summary(),
                "note": "sheets and views recreated from the SOLIDWORKS drawing (sheet size, view orientations, positions and scales), "
                        "dimensions placed by value on what measures them; notes and other annotations are not imported yet", "model_open": {k: v for k, v in res.items() if k != "model"}}
    if ext in (".sldprt", ".sldasm", ".slddrw"):
        from .sldprt import read_info
        info = read_info(str(p))
        native_error = None
        if ext == ".sldasm":
            try:
                asm, notes = _open_sldasm_native(p)
            except Exception as exc:  # report what the container holds
                native_error = str(exc)
            else:
                return {"model": asm.summary(), "geometry": "read natively (component tree + embedded Parasolid data)",
                        "notes": notes, "solidworks_file": {k: v for k, v in info.items() if k != "streams"}}
        history_error = None
        if ext == ".sldprt" and history:
            from . import cache
            from .sw_rebuild import RebuildError, rebuild_isolated
            doc = None
            ck = cache.key(str(p), "sldprt-history")
            try:  # in a child process: a kernel crash while trying candidates must not take openswx down
                hit = cache.get_json("history", ck)  # (rebuilt before: the result, or why it was not rebuilt)
                if hit is not None and not hit.get("ok"):
                    raise RebuildError(hit["error"])
                if hit is not None:
                    data, report = hit["data"], hit["report"]
                else:
                    try:
                        data, report = rebuild_isolated(str(p))
                    except RebuildError as exc:  # (not when it ran out of time or crashed: that can go otherwise)
                        if not re.search(r"took longer|crashed", str(exc)):
                            cache.put_json("history", ck, {"ok": False, "error": str(exc)})
                        raise
                    cache.put_json("history", ck, {"ok": True, "data": data, "report": report})
                doc = APP.new_document(p.stem)
                data["name"] = doc.name
                doc.load_dict(data)
                if any(v.get("status") == "error" for v in doc.state.values()):
                    raise DocumentError("the rebuilt history does not regenerate")
                if "regenerated_volume" in report and abs((doc.body.volume if doc.body is not None else 0.0)
                                                          - report["regenerated_volume"]) > 1e-6 * max(1.0, report["regenerated_volume"]):
                    raise DocumentError("the rebuilt history regenerates to a different body")
            except Exception as exc:  # history not understood: open the stored body instead
                if doc is not None:
                    APP.documents.pop(doc.name, None)
                    APP.active = next(iter(APP.documents), None)
                history_error = str(exc)
            else:
                doc._undo.clear()
                doc.path, doc.dirty = None, False
                _sw_properties(doc, info)
                return {"model": doc.summary(), "geometry": "feature history rebuilt from the file (editable)",
                        "feature_history": {"features": report["features"],
                                            "volume_mm3": round(report["volume"], 6),
                                            "stored_volume_mm3": round(report["stored_volume"], 6),
                                            "not_rebuilt": report["skipped"],
                                            "imported_geometry": report.get("imported", [])},
                        "solidworks_file": {k: v for k, v in info.items() if k != "streams"}}
        if ext == ".sldprt":
            doc = APP.new_document(p.stem)
            try:
                feat = _import_file(doc, str(p))
            except Exception as exc:  # unsupported geometry: report it
                APP.documents.pop(doc.name, None)
                APP.active = next(iter(APP.documents), None)
                native_error = str(exc)
            else:
                _sw_properties(doc, info)
                return {"model": doc.summary(), "geometry": "read natively from the embedded Parasolid data",
                        "notes": feat["params"].get("notes", []) + ([f"feature history not rebuilt: {history_error}"]
                                                                    if history_error else []),
                        "solidworks_file": {k: v for k, v in info.items() if k != "streams"}}
        return {"solidworks_file": info, **({"geometry": f"not imported: {native_error}"} if native_error else {})}
    raise DocumentError(f"unsupported file type {ext}")


@command(category="document")
def save_part(path: str | None = None) -> dict:
    """Save the active document (part .oswprt or assembly .oswasm). path: the file to write (Save As); the
    extension is added when missing. Without path the document is written to the file it was opened from or last
    saved to; a document never saved to a file needs a path, and gets an error that says so. Later saves without
    path go to the file last written. A cloud part (live collaboration) is kept by its server and needs no
    saving: with a path, this writes a local copy of it, and it stays the cloud part. Returns {"saved": path}."""
    d = APP.active_doc
    c = getattr(d, "_collab", None)
    if c is not None and not getattr(c, "closed", True):
        if not path:
            raise DocumentError("cloud parts save automatically (every change goes to the server); give a path to "
                                "save a local copy")
        saved = d.save_copy(path)
        from . import recent
        recent.add(saved)
        return {"saved": saved, "copy": True}
    if not path and not getattr(d, "path", None):
        kind = getattr(d, "kind", "part")
        raise DocumentError(f"{d.name} has never been saved to a file, so there is nowhere to save it: give the "
                            f"path argument (the file to write, e.g. {d.name}{'.oswasm' if kind == 'assembly' else '.oswprt'})")
    saved = d.save(path)
    if APP.active != d.name:
        APP.documents[d.name] = APP.documents.pop(APP.active)
        APP.active = d.name
    from . import recent
    recent.add(saved)
    return {"saved": saved}


@command(mutates=False, category="document")
def recent_files() -> dict:
    """Recently opened or saved documents, newest first (at most 15): path, name, folder, kind, when, and
    whether the file still exists. Open one with open_part."""
    from . import recent
    return {"recent": recent.entries()}


@command(mutates=False, category="document", mcp=False)
def clear_recent_files(path: str | None = None) -> dict:
    """Forget one recent file (path) or the whole list (no path). The files themselves are not touched."""
    from . import recent
    if path is None:
        recent.clear()
    else:
        recent.remove(path)
    return {"recent": recent.entries()}


@command(category="document")
def save_drawing_document(path: str | None = None) -> dict:
    """Save the drawing as its own document (.oswdrw, like a SOLIDWORKS .slddrw) that refers to its part /
    assembly file by a relative path (save the model first); opening it opens the model with this drawing."""
    import os
    d = APP.active_doc
    if not getattr(d, "drawing", None):
        raise DocumentError("this document has no drawing yet (start one with drawing_new)")
    if not getattr(d, "path", None):
        raise DocumentError("save the part / assembly first: the drawing refers to its file")
    model = Path(d.path).resolve()
    dp = Path(path or getattr(d, "drawing_path", None) or model.with_suffix(".oswdrw")).resolve()
    try:
        ref = os.path.relpath(model, dp.parent)
    except ValueError:  # another drive: keep the full path
        ref = str(model)
    dp.write_text(json.dumps({"format": "openswx-drawing", "version": 1, "model": ref, "drawing": d.drawing}, indent=1),
                  encoding="utf-8")
    d.drawing_path = str(dp)
    return {"saved": str(dp), "model": str(model)}


@command(mutates=False, category="document")
def list_documents() -> dict:
    """List open documents and which one is active."""
    return {"active": APP.active, "documents": [{"name": n, "path": d.path, "dirty": d.dirty} for n, d in APP.documents.items()]}


@command(category="document")
def activate_document(name: str) -> dict:
    """Switch the active document."""
    if name not in APP.documents:
        raise DocumentError(f"no open document {name!r}")
    APP.active = name
    return APP.active_doc.summary()


@command(category="document")
def close_document(name: str | None = None) -> dict:
    """Close a document (default: the active one). Unsaved changes are discarded."""
    name = name or APP.active
    APP.documents.pop(name, None)
    APP.active = next(iter(APP.documents), None)
    return _S.list_documents.__wrapped__()


@command(category="document")
def import_file(path: str) -> dict:
    """Import a STEP (.step/.stp), IGES, BREP, Parasolid (.x_t/.x_b), SOLIDWORKS part (.sldprt, read natively)
    or a closed triangle mesh (.stl, .3mf: becomes a faceted solid) into the active part as an Imported feature."""
    feat = _import_file(APP.doc, path)
    return {"feature": feat["name"], "model": APP.doc.summary()}


@command(mutates=False, category="document")
def export_file(path: str, format: Literal["step", "stl", "brep", "x_t", "iges", "3mf", "gltf", "glb"] | None = None) -> dict:
    """Export the active part's (or assembly's) bodies to STEP, IGES (solids), STL, 3MF (3D printing), glTF /
    GLB (web / visualisation), BREP or Parasolid text .x_t (opens in SOLIDWORKS and other Parasolid-based
    CAD); the format is inferred from the extension."""
    from build123d import export_brep, export_gltf, export_step, export_stl
    body = APP.active_doc.body
    if body is None:
        raise DocumentError("nothing to export: the part has no body")
    fmt = (format or Path(path).suffix.lstrip(".")).lower().replace("stp", "step").replace("igs", "iges")
    if fmt == "step":
        export_step(body, path)
    elif fmt == "iges":
        from OCP.IGESControl import IGESControl_Writer
        w = IGESControl_Writer("MM", 1)  # 1: BRep solids (faces, shells and solids kept)
        w.AddShape(body.wrapped)
        w.ComputeModel()
        if not w.Write(str(path)):
            raise DocumentError("IGES export failed")
    elif fmt == "3mf":
        from build123d import Mesher
        m = Mesher()
        m.add_shape(body, linear_deflection=0.01, angular_deflection=0.1)
        m.write(str(path))
    elif fmt in ("gltf", "glb"):
        export_gltf(body, str(path), binary=fmt == "glb", linear_deflection=0.01, angular_deflection=0.1)
    elif fmt == "stl":
        export_stl(body, path, tolerance=0.01, angular_tolerance=0.1)
    elif fmt == "brep":
        export_brep(body, path)
    elif fmt in ("x_t", "xt", "xmt_txt"):
        from .parasolid_write import XTWriteError, write_xt
        try:
            write_xt(body, path)
        except XTWriteError as exc:
            raise DocumentError(f"Parasolid export: {exc}") from exc
        fmt = "x_t"
    else:
        raise DocumentError(f"unsupported export format {fmt!r}")
    return {"exported": str(Path(path).resolve()), "format": fmt}


# ====================================================================== queries
@command(mutates=False, category="query")
def get_model() -> dict:
    """Summary of the active document. Parts: FeatureManager tree (with status/errors), rollback, bodies,
    volume, configurations. Assemblies (kind='assembly'): components with positions/DOF and mates."""
    out = APP.active_doc.summary()
    if APP.is_throwaway(APP.active_doc):  # (the untouched part made on demand: the window may show Home over it)
        out["throwaway"] = True
    if getattr(APP.active_doc, "drawing_path", None):
        out["drawing_document"] = APP.active_doc.drawing_path
    if getattr(APP.active_doc, "show_drawing", False):  # once, after a SOLIDWORKS drawing was opened
        out["show_drawing"] = True
        APP.active_doc.show_drawing = False
    ctx = APP.context
    if ctx and ctx["part"] == APP.active and ctx["assembly"] in APP.documents:
        out["in_context"] = {"assembly": ctx["assembly"], "component": ctx["component"]}
    return out


@command(mutates=False, category="query")
def get_feature(name: str) -> dict:
    """Full definition of one feature (parameters, rebuild state; sketches include geometry,
    relations, dimensions and solve status)."""
    return APP.doc.feature_detail(name)


@command(mutates=False, category="query")
def get_topology() -> dict:
    """List current faces and edges with ids (Face0.., Edge0..), type, a point on each, normals,
    areas/lengths and radii. Use the ids for fillet, chamfer, shell, hole, sketch-on-face, etc.
    Ids are only valid until the next model change; `name`, where present, stays valid across edits
    (a face is named after the feature and sketch entity that made it, an edge 'faceA|faceB' after the two
    faces it lies between) and is accepted wherever an id is. In assemblies ids are 'Component/Face#'."""
    return APP.active_doc.topology()


@command(mutates=False, category="query")
def get_mass_properties() -> dict:
    """Mass properties of the active part or assembly (mm, g): volume, surface area, mass, centre of mass,
    inertia tensor at the centre of mass, bounding box and topology counts."""
    d = APP.active_doc
    return mass_properties(d.body, getattr(d, "material", {"density": 1000.0})["density"])


@command(mutates=False, category="query")
def list_end_conditions() -> dict:
    """End conditions accepted by extrude/cut/hole, with their SOLIDWORKS swEndConditions_e values."""
    from .features import SUPPORTED_END_CONDITIONS
    return {k: {"sw_value": v, "supported": k in SUPPORTED_END_CONDITIONS} for k, v in END_CONDITIONS.items()}


@command(mutates=False, category="query", mcp=False)
def get_mesh() -> dict:
    """The active document's display mesh for the viewport: triangles per face (with face ids), edge polylines,
    sketch, plane and reference-geometry overlays."""
    m = APP.active_doc.mesh()
    ctx = APP.context
    if ctx and ctx["part"] == APP.active and ctx["assembly"] in APP.documents:
        m["context"] = _context_meshes(APP.documents[ctx["assembly"]], ctx["component"])
    return m


@command(mutates=False, category="query")
def get_activity(limit: int = 30) -> dict:
    """Recent command log (from the UI and from agents)."""
    return {"version": APP.version, "log": APP.log[-limit:]}


@command(mutates=False, category="query")
def measure(a: str, b: str) -> dict:
    """Minimum distance between two selections (Face#/Edge# ids)."""
    body = APP.doc.body
    if body is None:
        raise DocumentError("the part has no body")

    def pick(s):
        from .geom import resolve_edge, resolve_face
        if _is_edge_sel(s):
            return resolve_edge(body, _edge(APP.doc, s))
        if _is_face_sel(s):
            return resolve_face(body, _face(APP.doc, s))
        raise DocumentError(f"cannot measure {s!r}")
    sa, sb = pick(a), pick(b)
    return {"distance": sa.distance_to(sb), "a": a, "b": b}


@command(category="view")
def save_named_view(name: str, quaternion: list[float], target: Vec3, height: float, perspective: bool = False) -> dict:
    """Orientation dialog New View: save the current view (camera orientation quaternion [x, y, z, w], the
    point it looks at and the height of the view in mm) under a name, kept with the document."""
    doc = APP.active_doc
    if not name.strip():
        raise DocumentError("give the view a name")
    if name.strip().lower() in ("front", "back", "left", "right", "top", "bottom", "isometric", "trimetric", "dimetric"):
        raise DocumentError(f"{name!r} is a standard view")
    doc._checkpoint()
    views = getattr(doc, "named_views", None)
    if views is None:
        views = doc.named_views = {}
    views[name.strip()] = {"q": [float(v) for v in quaternion], "t": [float(v) for v in target], "h": float(height),
                           "perspective": bool(perspective)}
    doc.dirty = True
    return {"named_views": views}


@command(category="view")
def delete_named_view(name: str) -> dict:
    """Delete a saved view."""
    doc = APP.active_doc
    views = getattr(doc, "named_views", {}) or {}
    if name not in views:
        raise DocumentError(f"no saved view {name!r}")
    doc._checkpoint()
    del views[name]
    doc.dirty = True
    return {"named_views": views}


@command(mutates=False, category="query")
def face_features() -> dict:
    """Which feature made each face of the part: {"Face3": "Boss-Extrude1", ...} (assemblies: empty)."""
    doc = APP.active_doc
    return {"faces": doc.face_features() if hasattr(doc, "face_features") else {}}


@command(mutates=False, category="query")
def measure_items(items: list[str | Vec3]) -> dict:
    """Measure: one item gives its size (face area, radius/diameter, edge length,
    vertex XYZ); two give the minimum distance, the centre distance of circles/cylinders, dX/dY/dZ and the
    angle between planar faces or straight edges. Items: Face#/Edge# ids (Component/Face# in an assembly) or
    [x, y, z] vertices."""
    import numpy as _np
    got = [_measure_item(i) for i in items]
    out = {"items": [g[1] for g in got]}
    if len(got) == 2:
        (sa, ia), (sb, ib) = got
        out["distance"] = sa.distance_to(sb)
        ref = lambda i: i.get("center") or i.get("point")
        pa, pb = ref(ia), ref(ib)
        if pa and pb:
            d = _np.array(pb) - _np.array(pa)
            out["center_distance" if ("center" in ia or "center" in ib) else "point_distance"] = float(_np.linalg.norm(d))
            out["dx"], out["dy"], out["dz"] = (float(v) for v in d)
        da = ia.get("normal") or ia.get("direction") or ia.get("axis")
        db = ib.get("normal") or ib.get("direction") or ib.get("axis")
        if da and db:
            c = abs(float(_np.dot(da, db)) / (_np.linalg.norm(da) * _np.linalg.norm(db)))
            out["angle"] = math.degrees(math.acos(min(1.0, c)))
            if c > 1 - 1e-9 and "normal" in ia and "normal" in ib:  # parallel planes: their separation
                out["normal_distance"] = abs(float(_np.dot(_np.array(list(sb.center())) - _np.array(list(sa.center())), da)))
    return out


# ====================================================================== tree editing
@command(category="edit")
def undo() -> dict:
    """Undo the last change to the active document. In a cloud document (live collaboration) this takes back
    your own last change only, and is refused when someone has changed the same thing since."""
    alt = _S.redirected("undo", APP.active_doc) if _S.COMMAND_HOOKS else None
    if alt is not None:
        return alt
    APP.active_doc.undo(); return APP.active_doc.summary()


@command(category="edit")
def redo() -> dict:
    """Redo the last undone change."""
    alt = _S.redirected("redo", APP.active_doc) if _S.COMMAND_HOOKS else None
    if alt is not None:
        return alt
    APP.active_doc.redo(); return APP.active_doc.summary()


@command(category="edit")
def rebuild() -> dict:
    """Force a full rebuild (Ctrl+Q)."""
    return {"rebuild": APP.doc.rebuild(), "model": APP.doc.summary()}


@command(category="edit")
def edit_feature(name: str, params: dict) -> dict:
    """Change parameters of an existing feature (merged into its current parameters), e.g.
    edit_feature('Boss-Extrude1', {'depth': 25}). The model rebuilds; invalid edits are rejected."""
    APP.doc.edit_feature(name, params)
    return APP.doc.summary()


@command(category="edit", mcp=False)
def redefine_feature(name: str, command_name: str, args: dict) -> dict:
    """Re-define a feature using the creation command's arguments (used by the UI's Edit Feature).
    The command runs on a scratch copy to translate selections into parameters."""
    meta = COMMANDS.get(command_name)
    linked = (meta is not None and meta["mutates"] and meta["category"] in _S.FEATURE_CATEGORIES
              and command_name in _S.FILE_ARGS)  # (Insert Part, Mirror Part: a feature that names a file)
    if not (_S.feature_command(command_name) or (linked and not getattr(_TL, "preview", False))):
        raise DocumentError(f"{command_name!r} cannot redefine a feature (only commands that make one can"
                            f"{'; and a preview never reads files' if linked else ''})")
    if linked and getattr(_TL, "origin", "agent") == "agent":
        _S._check_workspace(command_name, args)  # (as if called itself: an agent stays in its workspace)
    doc = APP.doc
    target = doc.find(name)
    scratch = PartDocument(doc.name)
    scratch.load_dict(doc.to_dict())
    # selections resolve against the model as it was before this feature (e.g. Delete Face's faces are gone after it)
    idx = scratch.index(name)
    if scratch.rollback is None or scratch.rollback > idx:
        scratch.rollback = idx
        scratch.rebuild()
    key = APP.active
    APP.documents[key] = scratch
    was_preview = getattr(_TL, "preview", False)  # (itself previewed, by the Edit Feature panel: it stays one)
    _TL.preview = True
    try:
        COMMANDS[command_name]["fn"](**args)
        new_params = scratch.features[-1 if scratch.rollback is None else scratch.rollback - 1]["params"]
    finally:
        _TL.preview = was_preview
        APP.documents[key] = doc
    if target["type"] == "sketch":
        raise DocumentError("use the sketch tools to edit a sketch")
    doc.edit_feature(name, new_params, replace=True)
    return doc.summary()


@command(category="edit")
def delete_feature(name: str, delete_absorbed_sketch: bool = True) -> dict:
    """Delete a feature by name (e.g. 'Boss-Extrude1'). delete_absorbed_sketch=True (default) also deletes the
    sketch it absorbed (the sketch that defines it); False keeps the sketch. Returns what was removed."""
    removed = APP.doc.delete_feature(name, delete_absorbed_sketch)
    return {"removed": removed, "model": APP.doc.summary()}


@command(mutates=False, category="configurations")
def get_design_table() -> dict:
    """The part's design table: columns (dimension names such as 'D1@Sketch1' / 'Boss-Extrude1.depth', or
    '$STATE@Feature') and a row per configuration."""
    return APP.doc.get_design_table()


@command(category="configurations")
def apply_design_table(columns: list[str], rows: list[dict]) -> dict:
    """Design Table: rows [{'name': configuration, 'values': [cell per column]}] create or update
    configurations; '$STATE@Feature' cells are 'S' (suppressed) or 'U'; empty cells keep the base value."""
    made = APP.doc.apply_design_table(columns, rows)
    return {"created": made, "model": APP.doc.summary()}


@command(category="configurations")
def delete_design_table() -> dict:
    """Delete the design table; the configurations it made stay."""
    APP.doc.delete_design_table()
    return APP.doc.summary()


@command(category="edit")
def reorder_feature(name: str, after: str | None = None) -> dict:
    """Move a feature (with the sketches it consumes) in the FeatureManager to just after `after` (None: the
    top). Refused when the feature would come before something it references, or vice versa."""
    APP.doc.reorder(name, after)
    return APP.doc.summary()


@command(category="edit")
def add_folder(name: str, features: list[str]) -> dict:
    """FeatureManager Add to New Folder: group the given features under a folder (display only)."""
    APP.doc.add_folder(name, features)
    return APP.doc.summary()


@command(category="edit")
def remove_folder(name: str) -> dict:
    """Dissolve a FeatureManager folder; its features stay in the tree."""
    APP.doc.remove_folder(name)
    return APP.doc.summary()


@command(category="edit")
def rename_feature(name: str, new_name: str) -> dict:
    """Rename a feature; references from other features are updated."""
    APP.doc.rename(name, new_name)
    return APP.doc.summary()


@command(category="edit")
def suppress_feature(name: str, suppressed: bool = True, configuration: str | None = None) -> dict:
    """Suppress (or unsuppress) a feature in a configuration (default: the active one)."""
    APP.doc.set_suppressed(name, suppressed, configuration)
    return APP.doc.summary()


# ====================================================================== equations / configurations
@command(category="equations")
def set_global_variable(name: str, expression: str | float) -> dict:
    """Create or change a global variable (Equations > Global Variables), e.g. ('width', 120) or
    ('half', '"width"/2'). Use it in equations as "width"."""
    APP.doc.set_variable(name, expression)
    return APP.doc.equations()


@command(category="equations")
def delete_global_variable(name: str) -> dict:
    """Delete a global variable."""
    APP.doc.delete_variable(name)
    return APP.doc.equations()


@command(category="equations")
def set_equation(target: str, expression: str) -> dict:
    """Drive a dimension with an equation (SOLIDWORKS syntax): target 'D1@Sketch1' (sketch dimension) or
    'D1@Boss-Extrude1' (feature dimension: extrude D1=depth, revolve D1=angle, fillet D1=radius, shell
    D1=thickness, hole D1=diameter/D2=depth, pattern D1=spacing/D2=count, plane D1=offset). Expression may
    use global variables and other dimensions in quotes, + - * / ^, sin/cos/tan (degrees), sqrt, min, max."""
    APP.doc.set_equation(target, expression)
    return APP.doc.equations()


@command(category="equations")
def clear_equation(target: str) -> dict:
    """Remove the equation driving a dimension (it keeps its current value)."""
    APP.doc.clear_equation(target)
    return APP.doc.equations()


@command(mutates=False, category="equations")
def get_equations() -> dict:
    """List global variables, equations and the current value of every named dimension (D#@Feature)."""
    return APP.doc.equations()


@command(category="configurations")
def add_configuration(name: str, copy_from: str | None = None, derived_from: str | None = None) -> dict:
    """Add a configuration (copying suppression states/overrides from `copy_from`, default the active one)
    and make it active. Parts: per-configuration feature suppression and dimension values. Assemblies:
    per-configuration component suppression (suppress_component) and mate values (edit_mate while the
    configuration is active). derived_from (parts): instead of a copy, a derived configuration - it follows
    that parent's suppression states and values wherever it has none of its own, and keeps following when
    the parent changes."""
    if derived_from is not None:
        if getattr(APP.active_doc, "kind", "part") != "part":
            raise DocumentError("derived configurations are for parts")
        APP.active_doc.add_configuration(name, derived_from=derived_from)
        return APP.active_doc.summary()
    APP.active_doc.add_configuration(name, copy_from)  # (parts and assemblies both have configurations)
    return APP.active_doc.summary()


@command(category="configurations")
def activate_configuration(name: str) -> dict:
    """Show a configuration (ConfigurationManager double-click)."""
    APP.active_doc.activate_configuration(name)
    return APP.active_doc.summary()


@command(category="configurations")
def delete_configuration(name: str) -> dict:
    """Delete a configuration (not Default)."""
    APP.active_doc.delete_configuration(name)
    return APP.active_doc.summary()


@command(category="configurations")
def set_configuration_value(configuration: str, target: str, value: float | str) -> dict:
    """Override a dimension in one configuration, e.g. ('Long', 'D1@Boss-Extrude1', 80)."""
    APP.doc.set_configuration_value(configuration, target, value)
    return APP.doc.summary()


@command(mutates=False, category="document")
def export_bom(path: str) -> dict:
    """Export the active assembly's bill of materials as CSV (item, part number, description, quantity)."""
    import csv
    rows = _S.assembly_bom()["bom"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ITEM NO.", "PART NUMBER", "DESCRIPTION", "QTY."])
        for r in rows:
            w.writerow([r["item"], r["part_number"], r["description"], r["qty"]])
    return {"path": path, "rows": len(rows)}


@command(category="edit")
def rollback(to_feature: str | None = None) -> dict:
    """Move the rollback bar to just below `to_feature`; omit to roll forward to the end."""
    APP.doc.rollback_to(to_feature)
    return APP.doc.summary()


@command(category="edit")
def set_material(name: str, density_kg_m3: float | None = None, properties: dict | None = None) -> dict:
    """Assign a material. Library names (see list_materials, e.g. 'AISI 1020', '6061-T6 (SS)', 'ABS') bring
    their density, elastic modulus, Poisson ratio, strengths and colour; any other name needs a density.
    properties overrides any of: elastic_modulus, poisson, yield_strength, tensile_strength (MPa),
    thermal_conductivity, specific_heat, color."""
    APP.doc.set_material(name, density_kg_m3, properties)
    return APP.doc.summary()


@command(category="document")
def save_custom_material(name: str, properties: dict) -> dict:
    """Save a material to the user's Custom Materials library (kept across documents): density (kg/m^3)
    is required; elastic_modulus, poisson, yield_strength, tensile_strength, thermal_conductivity,
    specific_heat, color optional."""
    from .materials import save_custom_material as _save
    try:
        return {"material": _save(name, properties)}
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc


@command(category="document")
def delete_custom_material(name: str) -> dict:
    """Remove a material from the Custom Materials library."""
    from .materials import delete_custom_material as _del
    try:
        _del(name)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    return {"deleted": name}


@command(mutates=False, category="query")
def list_materials(category: str | None = None) -> dict:
    """The material library: density (kg/m^3), elastic modulus/yield/tensile strength (MPa), Poisson ratio,
    thermal conductivity (W/m K) and default colour, grouped by category."""
    from .materials import library
    lib = library()
    return {n: m for n, m in lib.items() if category is None or m["category"].lower() == category.lower()}


@command(category="edit")
def set_appearance(color: str | None, target: Literal["part", "feature", "face"] = "part",
                   feature: str | None = None, face: str | None = None) -> dict:
    """Appearance: colour ('#rrggbb', or null to clear) the whole part, a feature's faces (`feature`) or one
    face (`face` 'FaceN'). Face colours beat feature colours, which beat the part colour."""
    doc = APP.doc
    ref = feature if target == "feature" else _face(doc, face) if target == "face" and face else None
    if target != "part" and ref is None:
        raise DocumentError(f"give the {target} to colour")
    doc.set_appearance(color, target, ref)
    return doc.summary()


@command(category="edit")
def set_custom_property(name: str, value: str | None) -> dict:
    """File > Properties: set a custom property (e.g. 'Description', 'PartNo', 'Author'); value null deletes
    it. Drawings use Description/PartNo/Author in the title block and BOM."""
    doc = APP.doc
    doc._checkpoint()
    if value is None:
        doc.properties.pop(name, None)
    else:
        doc.properties[name] = str(value)
    doc.revision += 1
    return {"properties": doc.properties}


@command(category="edit")
def set_units(units: Literal["mm", "cm", "m", "in", "ft"]) -> dict:
    """Document units (display in the UI and drawings). The API always takes and returns millimetres."""
    doc = APP.doc
    doc._checkpoint()
    doc.units = units
    doc.revision += 1
    return doc.summary()


@command(mutates=False, category="query")
def parent_child(name: str) -> dict:
    """Parent/Child relationships of a feature: the features it references (parents) and the features that
    reference it (children)."""
    doc = APP.doc
    feat = doc.find(name)
    names = {f["name"] for f in doc.features}

    def refs(v) -> set[str]:
        if isinstance(v, str):
            base = v.split("/")[0]
            return {base} if base in names else set()
        if isinstance(v, dict):
            return set().union(*(refs(x) for x in v.values())) if v else set()
        if isinstance(v, (list, tuple)):
            return set().union(*(refs(x) for x in v)) if v else set()
        return set()
    parents = sorted(refs(feat["params"]) - {feat["name"]}, key=doc.index)
    children = [f["name"] for f in doc.features if f is not feat and feat["name"] in refs(f["params"])]
    return {"feature": feat["name"], "parents": parents, "children": children}


# ====================================================================== view capture
@command(mutates=False, category="query")
def get_view_image(view: Literal["isometric", "front", "back", "top", "bottom", "left", "right", "trimetric"] = "isometric",
                   width: int = 800, height: int = 600, show_edges: bool = True) -> dict:
    """Render the active part to a PNG (base64) from a standard view, for visual checks."""
    from .render import render_png
    png = render_png(APP.doc, view=view, width=width, height=height, edges=show_edges)
    import base64
    return {"png_base64": base64.b64encode(png).decode(), "view": view, "width": width, "height": height}
