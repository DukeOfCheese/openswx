"""Design review commands (same registry as session.py, which re-exports them): the document as a script,
comparing two documents, requirements checked like tests, an automatic model review and the hand-off pack."""
from __future__ import annotations

import json
import math
import operator
from pathlib import Path
from typing import Literal

from .document import DocumentError
from .session import APP, command

__all__ = ["export_script", "compare_documents", "add_requirement", "delete_requirement", "check_requirements",
           "review_model", "export_handoff_pack"]


def _kind(doc) -> str:
    return getattr(doc, "kind", "part")


# ====================================================================== the document as a script
@command(mutates=False, category="file")
def export_script(path: str | None = None) -> dict:
    """The active document as a Python script: the commands that built it, in order, through the openswx API
    (`S.create_sketch(...)`, `S.extrude(...)`). Run it (Tools > Macro > Run, or `python script.py`) to build the
    document again; edit it or keep it under version control like any text. Available for documents made in
    openswx from scratch - one opened from an older file or imported has no recorded history. `path`: also
    write it there (.py)."""
    doc = APP.active_doc
    if getattr(doc, "journal", None) is None:
        raise DocumentError(f"{doc.name} has no recorded history: it was imported, or saved by a version of openswx "
                            "that did not record one. Documents made from New onwards can be exported as scripts.")
    asm = _kind(doc) == "assembly"
    lines = [f'"""{doc.name}: an openswx {"assembly" if asm else "part"} as a script (export_script).',
             'Run with Tools > Macro > Run, or `python <this file>` where openswx is installed. Lengths in mm."""',
             "from openswx import session as S", "",
             f"S.{'new_assembly' if asm else 'new_part'}({doc.name!r})"]
    for e in doc.journal:
        args = ", ".join(f"{k}={v!r}" for k, v in e["args"].items())
        lines.append(f"S.{e['command']}({args})")
    text = "\n".join(lines) + "\n"
    out = {"script": text, "commands": len(doc.journal)}
    if path:
        p = Path(path)
        if p.suffix.lower() != ".py":
            p = p.with_suffix(".py")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        out["path"] = str(p.resolve())
    return out


# ====================================================================== compare
def _load(ref: str):
    """An open document by name, or a native file read without opening it in the session."""
    if ref in APP.documents:
        return APP.documents[ref]
    p = Path(ref)
    if not p.is_file():
        raise DocumentError(f"{ref!r} is neither an open document nor a file")
    data = json.loads(p.read_text(encoding="utf-8"))
    if p.suffix.lower() == ".oswasm":
        from .assembly import AssemblyDocument
        doc = AssemblyDocument(p.stem)
        doc.live_docs = {}
    elif p.suffix.lower() == ".oswprt":
        from .document import PartDocument
        doc = PartDocument(p.stem)
    else:
        raise DocumentError("compare_documents reads .oswprt and .oswasm files (open other formats first)")
    doc.load_dict(data)
    return doc


def _diff(a, b, prefix=""):
    """Leaf-level differences of two JSON values as [(path, a, b)]."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{prefix}.{k}" if prefix else str(k))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _diff(x, y, f"{prefix}[{i}]")
        return out
    if isinstance(a, float) or isinstance(b, float):
        try:
            if math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9):
                return []
        except (TypeError, ValueError):
            pass
    return [] if a == b else [(prefix, a, b)]


def _short(v, n=80):
    s = json.dumps(v, default=str)
    return v if len(s) <= n else s[:n] + "..."


def _measure(doc) -> dict:
    from .features import mass_properties
    if _kind(doc) == "assembly":
        s = doc.summary()
        return {"volume": s.get("volume_mm3"), "components": len(doc.components), "mates": len(doc.mates)}
    if doc.body is None:
        return {"volume": 0.0, "mass": 0.0, "surface_area": 0.0, "faces": 0}
    m = mass_properties(doc.body, doc.material.get("density", 1000.0))
    return {"volume": m["volume"], "mass": m["mass"], "surface_area": m["surface_area"], "faces": m.get("faces"),
            "bounding_box": m.get("bounding_box")}


@command(mutates=False, category="evaluate")
def compare_documents(a: str, b: str | None = None) -> dict:
    """What changed between two versions of a design. `a`, `b`: open documents by name or .oswprt / .oswasm
    files (b defaults to the active document, so compare_documents('old.oswprt') compares a saved file with
    what is open). Reports features (or components and mates) added, removed and changed - with the
    parameters that differ - plus the change in volume, mass, surface area and bounding box. For parts it
    also gives the volume of material added and removed."""
    da = _load(a)
    db = _load(b) if b else APP.active_doc
    if _kind(da) != _kind(db):
        raise DocumentError("compare a part with a part, or an assembly with an assembly")
    out = {"a": da.name, "b": db.name, "kind": _kind(da)}
    if _kind(da) == "assembly":
        ca, cb = {c.name: c for c in da.components}, {c.name: c for c in db.components}
        out["components"] = {"added": sorted(set(cb) - set(ca)), "removed": sorted(set(ca) - set(cb)),
                             "moved": sorted(n for n in set(ca) & set(cb)
                                             if not all(math.isclose(x, y, abs_tol=1e-9) for x, y in zip(ca[n].x, cb[n].x)))}
        ma, mb = {m["name"]: m for m in da.mates}, {m["name"]: m for m in db.mates}
        out["mates"] = {"added": sorted(set(mb) - set(ma)), "removed": sorted(set(ma) - set(mb)),
                        "changed": sorted(n for n in set(ma) & set(mb) if _diff(ma[n], mb[n]))}
    else:
        fa, fb = {f["name"]: f for f in da.features}, {f["name"]: f for f in db.features}
        changed = {}
        for n in fa.keys() & fb.keys():
            d = _diff({"type": fa[n]["type"], "params": fa[n]["params"], "suppressed": fa[n].get("suppressed", False)},
                      {"type": fb[n]["type"], "params": fb[n]["params"], "suppressed": fb[n].get("suppressed", False)})
            if d:
                changed[n] = [{"parameter": p, "a": _short(x), "b": _short(y)} for p, x, y in d[:40]]
        order_a = [n for n in fa if n in fb]
        order_b = [n for n in fb if n in fa]
        out["features"] = {"added": [n for n in fb if n not in fa], "removed": [n for n in fa if n not in fb],
                           "changed": changed, "reordered": order_a != order_b}
        dv = _diff(getattr(da, "variables", {}), getattr(db, "variables", {}))
        out["global_variables"] = [{"name": p, "a": x, "b": y} for p, x, y in dv]
        if da.material.get("name") != db.material.get("name"):
            out["material"] = {"a": da.material.get("name"), "b": db.material.get("name")}
        if da.body is not None and db.body is not None:
            try:
                added, removed = db.body - da.body, da.body - db.body
                out["material_added_mm3"] = round(float(getattr(added, "volume", 0.0) or 0.0), 6)
                out["material_removed_mm3"] = round(float(getattr(removed, "volume", 0.0) or 0.0), 6)
            except Exception:  # noqa: BLE001 - a boolean the kernel cannot do: the totals below still tell
                pass
    ma, mb = _measure(da), _measure(db)
    out["measures"] = {k: {"a": ma.get(k), "b": mb.get(k),
                           **({"change": mb[k] - ma[k]} if isinstance(ma.get(k), (int, float)) and isinstance(mb.get(k), (int, float)) else {})}
                       for k in sorted(set(ma) | set(mb))}
    f = out.get("features") or {}
    c = out.get("components") or {}
    out["identical"] = not (f.get("added") or f.get("removed") or f.get("changed") or f.get("reordered")
                            or c.get("added") or c.get("removed") or c.get("moved")
                            or (out.get("mates") or {}).get("added") or (out.get("mates") or {}).get("removed")
                            or (out.get("mates") or {}).get("changed") or out.get("global_variables") or out.get("material"))
    return out


# ====================================================================== requirements
QUANTITIES = ("mass", "volume", "surface_area", "size_x", "size_y", "size_z", "longest_side", "min_thickness",
              "dimension", "bodies", "errors", "components", "interferences")
_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": None, "!=": None}


def _quantity(doc, quantity: str, target: str | None, cache: dict):
    asm = _kind(doc) == "assembly"
    if quantity == "errors":
        s = doc.summary()
        return float(len(s.get("errors") or []) + sum(1 for f in s.get("features", []) if f.get("status") == "error"))
    if quantity == "components":
        if not asm:
            raise DocumentError("'components' is an assembly quantity")
        return float(len(doc.components))
    if quantity == "interferences":
        if not asm:
            raise DocumentError("'interferences' is an assembly quantity")
        from . import session as S
        r = S.check_interference.__wrapped__()
        return float(len(r.get("interferences", r.get("pairs", []))))
    if quantity == "dimension":
        if asm or target not in doc.dim_values:
            raise DocumentError(f"no dimension {target!r} (names: get_equations)")
        return float(doc.dim_values[target])
    if asm:
        if quantity == "volume":
            return float(doc.summary().get("volume_mm3") or 0.0)
        raise DocumentError(f"{quantity!r} is a part quantity (assemblies: volume, components, interferences, errors)")
    if doc.body is None:
        raise DocumentError("the part has no body")
    if "mp" not in cache:
        from .features import mass_properties
        cache["mp"] = mass_properties(doc.body, doc.material.get("density", 1000.0))
    mp = cache["mp"]
    if quantity in ("mass", "volume", "surface_area", "bodies"):
        return float(mp[quantity])
    bb = mp["bounding_box"]
    size = [bb["max"][i] - bb["min"][i] for i in range(3)]
    if quantity.startswith("size_"):
        return float(size["xyz".index(quantity[-1])])
    if quantity == "longest_side":
        return float(max(size))
    if quantity == "min_thickness":
        from . import session as S
        return float(S.thickness_analysis.__wrapped__()["min_thickness"])
    raise DocumentError(f"unknown quantity {quantity!r}; expected one of {QUANTITIES}")


@command(category="edit")
def add_requirement(name: str, quantity: Literal["mass", "volume", "surface_area", "size_x", "size_y", "size_z",
                                                 "longest_side", "min_thickness", "dimension", "bodies", "errors",
                                                 "components", "interferences"],
                    op: Literal["<", "<=", ">", ">=", "==", "!="], value: float, target: str | None = None,
                    tolerance: float = 1e-6) -> dict:
    """A requirement the design must keep meeting, saved with the document and checked by check_requirements
    (and `openswx check FILE` on the command line, for CI). Examples: ('Light enough', 'mass', '<', 250)
    - mass in grams; ('Fits the bay', 'longest_side', '<=', 200); ('Wall', 'min_thickness', '>=', 1.5);
    ('Shaft size', 'dimension', '==', 12, target='D1@Sketch1'); ('Rebuilds', 'errors', '==', 0); assemblies:
    ('No clashes', 'interferences', '==', 0). Lengths mm, volume mm3, area mm2. An existing requirement of
    the same name is replaced. tolerance: how close counts for == and !=."""
    doc = APP.active_doc
    if not name.strip():
        raise DocumentError("give the requirement a name")
    if quantity == "dimension" and not target:
        raise DocumentError("a 'dimension' requirement needs target='D1@Sketch1'")
    req = {"name": name.strip(), "quantity": quantity, "op": op, "value": float(value), "tolerance": float(tolerance),
           **({"target": target} if target else {})}
    current = _quantity(doc, quantity, target, {})  # (refuses what cannot be measured on this document)
    doc._checkpoint()
    doc.requirements = [r for r in getattr(doc, "requirements", []) if r["name"] != req["name"]] + [req]
    doc.dirty = True
    doc.revision += 1
    return {"requirement": req, "current": round(current, 6), "passes": _passes(current, req)}


def _passes(current: float, req: dict) -> bool:
    if req["op"] == "==":
        return abs(current - req["value"]) <= req.get("tolerance", 1e-6)
    if req["op"] == "!=":
        return abs(current - req["value"]) > req.get("tolerance", 1e-6)
    return bool(_OPS[req["op"]](current, req["value"]))


@command(category="edit")
def delete_requirement(name: str) -> dict:
    """Remove a requirement by name."""
    doc = APP.active_doc
    reqs = getattr(doc, "requirements", [])
    if not any(r["name"] == name for r in reqs):
        raise DocumentError(f"no requirement {name!r}")
    doc._checkpoint()
    doc.requirements = [r for r in reqs if r["name"] != name]
    doc.dirty = True
    doc.revision += 1
    return {"requirements": [r["name"] for r in doc.requirements]}


@command(mutates=False, category="evaluate")
def check_requirements() -> dict:
    """Check every requirement of the active document against the current model: each with its measured
    value and pass / fail, and `ok` for the lot. A requirement that cannot be measured counts as failed, with
    the reason."""
    doc = APP.active_doc
    cache: dict = {}
    results = []
    for r in getattr(doc, "requirements", []):
        try:
            cur = _quantity(doc, r["quantity"], r.get("target"), cache)
            results.append({**r, "current": round(cur, 6), "passes": _passes(cur, r)})
        except (DocumentError, KeyError, ValueError) as exc:
            results.append({**r, "current": None, "passes": False, "error": str(exc)})
    return {"document": doc.name, "requirements": results, "failed": [r["name"] for r in results if not r["passes"]],
            "ok": all(r["passes"] for r in results)}


# ====================================================================== review
@command(mutates=False, category="evaluate")
def review_model(min_wall: float = 1.0, draft_direction: str | None = None, draft_angle: float = 1.0) -> dict:
    """An automatic design review of the active part: every finding names what it is about, how serious it is
    (error / warning / info) and the command that addresses it. Checks: features that fail to rebuild,
    sketches that are under- or over-defined, references that fell back to geometry, invalid geometry and
    sliver edges, walls thinner than `min_wall` mm, faces without draft (when draft_direction is given, e.g.
    'Y', for moulded parts), a missing material, and the document's requirements. `ok` is true when there
    are no errors."""
    from . import session as S
    doc = APP.doc
    findings = []

    def add(severity, subject, message, fix=None):
        findings.append({"severity": severity, "subject": subject, "message": message, **({"fix": fix} if fix else {})})

    summary = doc.summary()
    for f in summary["features"]:
        if f.get("suppressed"):
            continue
        if f.get("status") == "error":
            add("error", f["name"], f"does not rebuild: {f.get('error')}", f"edit_feature('{f['name']}', ...) or delete_feature")
        elif f.get("status") not in (None, "ok"):
            add("warning", f["name"], f"rebuilt with a warning: {f.get('error') or f.get('status')}")
        if f["type"] == "sketch" and not f.get("rolled_back"):
            if f.get("sketch_state") == "under_defined":
                add("warning", f["name"], "the sketch is under-defined: its geometry can move when something else changes",
                    f"sketch_dimension / sketch_relation on '{f['name']}' (get_feature shows the free entities)")
            elif f.get("sketch_state") in ("over_defined", "unsolved"):
                add("error", f["name"], "the sketch is over-defined or cannot be solved",
                    f"sketch_delete a conflicting relation or dimension of '{f['name']}'")
            if not f.get("absorbed_by") and doc.body is not None:
                add("info", f["name"], "the sketch is not used by any feature")
    for e in summary.get("errors") or []:
        if not any(x["message"].endswith(str(e)) for x in findings):
            add("error", "rebuild", str(e))
    if doc.body is None:
        add("info", doc.name, "the part has no solid body yet")
    else:
        chk = S.check_geometry.__wrapped__()
        if not chk["valid"]:
            add("error", "geometry", "the body is not valid: " + "; ".join(map(str, chk["issues"][:5])), "check_geometry")
        elif chk["issues"]:
            add("warning", "geometry", "; ".join(map(str, chk["issues"][:5])), "check_geometry")
        if chk.get("solids", 1) > 1:
            add("info", "bodies", f"the part has {chk['solids']} separate bodies", "combine_bodies if they should be one")
        try:
            th = S.thickness_analysis.__wrapped__(target=min_wall)
            if th["min_thickness"] < min_wall - 1e-9:
                add("warning", "wall thickness", f"thinnest wall {th['min_thickness']:.3g} mm, under {min_wall:g} mm on "
                    f"{len(th['thin_faces'])} face(s): {', '.join(map(str, th['thin_faces'][:6]))}", "thickness_analysis")
        except Exception as exc:  # noqa: BLE001 - the analysis failing is itself worth saying
            add("info", "wall thickness", f"could not be measured: {exc}")
        if draft_direction:
            try:
                dr = S.draft_analysis.__wrapped__(direction=draft_direction, angle=draft_angle)
                need = [k for k, v in (dr.get("faces") or {}).items() if v in ("requires", "requires_draft", "straddle")]
                if need:
                    add("warning", "draft", f"{len(need)} face(s) have less than {draft_angle:g} deg of draft along "
                        f"{draft_direction}: {', '.join(need[:6])}", "draft")
            except Exception as exc:  # noqa: BLE001
                add("info", "draft", f"could not be analysed: {exc}")
    if not doc.material.get("name"):
        add("info", "material", "no material is set: the mass uses 1000 kg/m3", "set_material")
    req = check_requirements.__wrapped__()
    for r in req["requirements"]:
        if not r["passes"]:
            now = "cannot be measured: " + r["error"] if r.get("error") else f"is {r['current']:g}"
            add("error", f"requirement '{r['name']}'", f"{r['quantity']} {now}, required {r['op']} {r['value']:g}")
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("error", "warning", "info")}
    return {"document": doc.name, "ok": counts["error"] == 0, "counts": counts, "findings": findings,
            "requirements_checked": len(req["requirements"])}


# ====================================================================== hand-off pack
@command(mutates=False, category="file")
def export_handoff_pack(folder: str, zip_file: bool = True, formats: list[str] | None = None) -> dict:
    """Everything a workshop or supplier needs, in one folder (and a .zip): the model as STEP (formats: e.g.
    ['step', 'x_t', 'stl']), the drawing as PDF when the document has one, a picture, mass properties, the
    custom properties, for assemblies the BOM (CSV), for sheet metal the flat pattern (DXF), the requirements
    check - and manifest.json listing what is there. Files are named after the document."""
    import zipfile

    from . import session as S
    doc = APP.active_doc
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    name, files, skipped = doc.name, [], []

    def attempt(label, fn):
        try:
            p = fn()
            if p:
                files.append({"file": Path(p).name, "what": label})
        except Exception as exc:  # noqa: BLE001 - one export failing must not lose the rest
            skipped.append({"what": label, "reason": str(exc)})

    for fmt in formats or ["step"]:
        ext = "igs" if fmt == "iges" else fmt
        attempt(f"model ({fmt})", lambda fmt=fmt, ext=ext: S.export_file.__wrapped__(str(out / f"{name}.{ext}"), fmt) and out / f"{name}.{ext}")
    if getattr(doc, "drawing", None) and doc.drawing.get("sheets"):
        attempt("drawing (pdf)", lambda: S.export_drawing.__wrapped__(str(out / f"{name}.pdf"), "pdf") and out / f"{name}.pdf")
    else:
        skipped.append({"what": "drawing (pdf)", "reason": "the document has no drawing (drawing_new)"})

    def picture():
        import base64
        res = S.get_view_image.__wrapped__(view="isometric", width=1200, height=900)
        (out / f"{name}.png").write_bytes(base64.b64decode(res["png_base64"]))
        return out / f"{name}.png"
    attempt("picture", picture)
    if _kind(doc) == "assembly":
        attempt("BOM (csv)", lambda: S.export_bom.__wrapped__(str(out / f"{name}-BOM.csv")) and out / f"{name}-BOM.csv")
    else:
        if doc.summary().get("sheet_metal"):
            attempt("flat pattern (dxf)", lambda: S.export_flat_pattern_dxf.__wrapped__(str(out / f"{name}-flat.dxf")) and out / f"{name}-flat.dxf")

        def props():
            mp = S.get_mass_properties.__wrapped__()
            (out / f"{name}-properties.json").write_text(json.dumps(
                {"material": doc.material.get("name"), "mass_properties": mp, "custom_properties": doc.properties,
                 "tolerances": getattr(doc, "tolerances", {})}, indent=1, default=str), encoding="utf-8")
            return out / f"{name}-properties.json"
        attempt("properties", props)
    req = check_requirements.__wrapped__()
    if req["requirements"]:
        (out / f"{name}-requirements.json").write_text(json.dumps(req, indent=1), encoding="utf-8")
        files.append({"file": f"{name}-requirements.json", "what": "requirements check"})
    from . import __version__
    manifest = {"document": name, "kind": _kind(doc), "openswx": __version__, "files": files, "not_included": skipped,
                "requirements_ok": req["ok"] if req["requirements"] else None}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    result = {"folder": str(out.resolve()), **manifest}
    if zip_file:
        zp = out.parent / f"{out.name}.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(out.iterdir()):
                if f.is_file():
                    z.write(f, f.name)
        result["zip"] = str(zp.resolve())
    return result
