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
           "review_model", "export_handoff_pack", "tolerance_stack", "design_study", "print_orientation", "sheet_metal_gauge"]


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


# ====================================================================== tolerance stack-up, design study
@command(mutates=False, category="evaluate")
def tolerance_stack(contributors: list[dict], general_class: Literal["f", "m", "c", "v"] | None = None) -> dict:
    """Tolerance stack-up along one direction: `contributors` is the chain of model dimensions that make up a
    gap or overall size, each {"dimension": "D1@Sketch1", "sign": 1} (sign -1 for dimensions that subtract).
    Uses each dimension's own tolerance (set_dimension_tolerance); one without takes the ISO 2768 general
    tolerance of `general_class` (default: the setting). Returns the nominal result, the worst-case limits and
    the statistical (root-sum-square) limits, with each contributor's share - the largest is the one worth
    tightening."""
    from . import settings
    from . import tolerance as T
    from .inspection import general_tolerance
    doc = APP.doc
    cls = general_class or settings.get("general_tolerance")
    if not contributors:
        raise DocumentError("give at least one contributor")
    tols = getattr(doc, "tolerances", {})
    rows, nominal, wc_lo, wc_hi, mid, var = [], 0.0, 0.0, 0.0, 0.0, 0.0
    for c in contributors:
        name, sign = c.get("dimension"), (-1.0 if float(c.get("sign", 1)) < 0 else 1.0)
        if name not in doc.dim_values:
            raise DocumentError(f"no dimension {name!r} (names: get_equations)")
        value = float(doc.dim_values[name])
        source = "dimension"
        try:
            dev = T.deviations(value, tols.get(name))
        except T.ToleranceError as exc:
            raise DocumentError(f"{name}: {exc}") from exc
        if dev is None:
            g = general_tolerance("linear", abs(value), cls)
            if g is None:
                raise DocumentError(f"{name} ({value:g} mm) has no tolerance and is below the range of ISO 2768; set one")
            dev, source = (-g, g), f"ISO 2768-{cls}"
        lo, hi = (dev[0], dev[1]) if sign > 0 else (-dev[1], -dev[0])
        nominal += sign * value
        wc_lo += lo
        wc_hi += hi
        mid += (lo + hi) / 2
        half = (hi - lo) / 2
        var += half * half
        rows.append({"dimension": name, "sign": int(sign), "value": value, "lower": round(dev[0], 6), "upper": round(dev[1], 6),
                     "from": source, "band": round(hi - lo, 6)})
    rss = math.sqrt(var)
    total_band = sum(r["band"] for r in rows) or 1.0
    for r in rows:
        r["share_pct"] = round(100 * r["band"] / total_band, 1)
    r6 = lambda v: round(v, 6)  # noqa: E731
    return {"nominal": r6(nominal), "worst_case": {"min": r6(nominal + wc_lo), "max": r6(nominal + wc_hi)},
            "statistical": {"min": r6(nominal + mid - rss), "max": r6(nominal + mid + rss),
                            "note": "root sum square of the half bands about the mean: each tolerance taken as +/- 3 sigma"},
            "contributors": rows}


@command(category="evaluate")
def design_study(variables: dict[str, list[float]], measures: list[str] | None = None, objective: dict | None = None,
                 mode: Literal["grid", "paired"] = "grid", keep_best: bool = False, max_runs: int = 200) -> dict:
    """Parametric design study: rebuild the part for each combination of values and tabulate the results.
    variables: {"width": [20, 30, 40], "D1@Sketch2": [5, 8]} - global variables or sketch dimensions with the
    values to try (grid: every combination; paired: first with first, second with second, ...). measures:
    quantities per run (default mass and volume; any requirement quantity: surface_area, size_x/y/z,
    longest_side, min_thickness, errors). objective: {"quantity": "mass", "goal": "min"} picks the best run
    among those that rebuild and meet every requirement of the document. The part is put back as it was
    unless keep_best=True, which leaves it at the best run (one Undo step either way)."""
    import itertools

    from . import session as S
    doc = APP.doc
    if not variables or any(not v for v in variables.values()):
        raise DocumentError("give each variable at least one value")
    measures = list(measures or ["mass", "volume"])
    for q in measures + ([objective["quantity"]] if objective else []):
        if q not in QUANTITIES or q in ("dimension", "components", "interferences"):
            raise DocumentError(f"cannot tabulate {q!r}; choose from mass, volume, surface_area, size_x, size_y, size_z, "
                                "longest_side, min_thickness, bodies, errors")
    if objective and objective.get("goal") not in ("min", "max"):
        raise DocumentError('objective must be {"quantity": ..., "goal": "min" or "max"}')
    names = list(variables)
    for n in names:
        if n not in doc.variables and n not in doc.dim_values:
            raise DocumentError(f"{n!r} is neither a global variable nor a dimension of this part")
    if mode == "paired":
        if len({len(v) for v in variables.values()}) != 1:
            raise DocumentError("paired mode needs the same number of values for every variable")
        combos = list(zip(*(variables[n] for n in names)))
    else:
        combos = list(itertools.product(*(variables[n] for n in names)))
    if len(combos) > max_runs:
        raise DocumentError(f"{len(combos)} runs is over max_runs={max_runs}; try fewer values or raise it")

    def apply(combo):
        for n, v in zip(names, combo):
            if n in doc.variables:
                S.set_global_variable.__wrapped__(n, float(v))
            else:
                S.set_dimension.__wrapped__(n, float(v))

    doc._checkpoint()
    n_undo = len(doc._undo)
    start = json.dumps(doc.to_dict())
    runs = []
    best = None
    try:
        for combo in combos:
            row = {"values": dict(zip(names, map(float, combo)))}
            try:
                apply(combo)
                cache: dict = {}
                row.update({q: round(_quantity(doc, q, None, cache), 6) for q in measures})
                if objective and objective["quantity"] not in row:
                    row[objective["quantity"]] = round(_quantity(doc, objective["quantity"], None, cache), 6)
                failed = [r["name"] for r in check_requirements.__wrapped__()["requirements"] if not r["passes"]]
                row["rebuilds"] = _quantity(doc, "errors", None, {}) == 0
                row["requirements_failed"] = failed
                row["feasible"] = row["rebuilds"] and not failed
            except (S.CommandError, DocumentError, ValueError, KeyError) as exc:
                row.update(rebuilds=False, feasible=False, error=str(exc))
            runs.append(row)
        if objective:
            ok = [r for r in runs if r.get("feasible") and objective["quantity"] in r]
            if ok:
                best = (min if objective["goal"] == "min" else max)(ok, key=lambda r: r[objective["quantity"]])
    finally:
        doc.load_dict(json.loads(start))
        del doc._undo[n_undo:]
    kept = False
    if keep_best and best is not None:
        apply([best["values"][n] for n in names])
        del doc._undo[n_undo:]
        kept = True
    doc.dirty = True
    return {"variables": names, "runs": runs, "count": len(runs), "feasible": sum(1 for r in runs if r.get("feasible")),
            "best": best, "kept_best": kept}


# ====================================================================== 3D printing, sheet-metal gauges
@command(mutates=False, category="evaluate")
def print_orientation(overhang_angle: float = 45.0, bed: list[float] | None = None) -> dict:
    """3D-print orientation advisor: for each of the six axis-aligned ways up, the build height, the area that
    sits on the bed, the overhanging area (faces pointing down more steeply than `overhang_angle` degrees from
    vertical, not counting what rests on the bed) and an estimate of the support volume (each overhanging
    patch times its height above the bed: an upper bound, since supports that land on the part are shorter).
    The orientations are returned best first: least support, then lowest. bed: [x, y, z] printer volume in
    mm - orientations that do not fit are marked."""
    import numpy as np

    from .geom import tessellate
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body")
    if not 0 < overhang_angle < 90:
        raise DocumentError("overhang_angle must be between 0 and 90 degrees")
    mesh = tessellate(doc.body, tol=0.02)
    tris = []
    for f in mesh["faces"]:
        v = np.asarray(f["v"], float).reshape(-1, 3)
        t = np.asarray(f["t"], int).reshape(-1, 3)
        if len(t):
            tris.append(v[t])
    if not tris:
        raise DocumentError("the part has no faces to analyse")
    T = np.concatenate(tris)  # (n, 3 corners, 3 coordinates)
    cross = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    area = np.linalg.norm(cross, axis=1) / 2
    keep = area > 1e-12
    T, cross, area = T[keep], cross[keep], area[keep]
    normal = cross / (2 * area[:, None])
    centre = T.mean(axis=1)
    # the mesh's facets may wind either way: point each normal away from the solid's inside using the volume sign
    if float(np.einsum("ij,ij->i", centre, cross).sum()) < 0:
        normal = -normal
    limit = -math.cos(math.radians(overhang_angle))  # n . up below this: steeper than the angle allows
    out = []
    for label, up in (("+X", (1, 0, 0)), ("-X", (-1, 0, 0)), ("+Y", (0, 1, 0)), ("-Y", (0, -1, 0)), ("+Z", (0, 0, 1)), ("-Z", (0, 0, -1))):
        d = np.asarray(up, float)
        h = T @ d  # height of every corner
        base, top = float(h.min()), float(h.max())
        low = h.max(axis=1) - base  # how far the facet's highest corner is above the bed
        nd = normal @ d
        on_bed = (low < 1e-4 * max(top - base, 1.0) + 1e-6) & (nd < -0.999)
        over = (nd < limit) & ~on_bed
        proj = area * np.abs(nd)
        support = float((proj[over] * (h[over].mean(axis=1) - base)).sum())
        size = [float(np.ptp(T[:, :, i])) for i in range(3)]
        axis = int(np.argmax(np.abs(d)))
        footprint = sorted(size[i] for i in range(3) if i != axis)
        row = {"up": label, "height": round(top - base, 4), "bed_contact_area": round(float(area[on_bed].sum()), 4),
               "overhang_area": round(float(area[over].sum()), 4), "support_volume": round(support, 4),
               "footprint": [round(footprint[0], 4), round(footprint[1], 4)]}
        if bed:
            b = sorted(float(x) for x in bed[:2])
            row["fits"] = footprint[0] <= b[0] + 1e-9 and footprint[1] <= b[1] + 1e-9 and (top - base) <= float(bed[2]) + 1e-9
        out.append(row)
    out.sort(key=lambda r: (not r.get("fits", True), r["support_volume"], -r["bed_contact_area"], r["height"]))
    return {"overhang_angle": overhang_angle, "orientations": out, "best": out[0]["up"],
            "note": "support volume is an upper bound (supports are taken down to the bed)"}


# sheet thickness by gauge number, mm: Manufacturers' Standard Gauge (steel), galvanised, stainless (US
# Standard) and aluminium / brass (Brown & Sharpe)
SHEET_GAUGES = {
    "steel": {7: 4.554, 8: 4.176, 9: 3.797, 10: 3.416, 11: 3.038, 12: 2.657, 13: 2.278, 14: 1.897, 16: 1.519, 18: 1.214,
              20: 0.912, 22: 0.759, 24: 0.607, 26: 0.455, 28: 0.378},
    "galvanized": {10: 3.510, 11: 3.132, 12: 2.753, 14: 1.994, 16: 1.613, 18: 1.311, 20: 1.006, 22: 0.853, 24: 0.701,
                   26: 0.551, 28: 0.475},
    "stainless": {7: 4.763, 8: 4.366, 10: 3.571, 11: 3.175, 12: 2.778, 14: 1.984, 16: 1.588, 18: 1.270, 20: 0.953,
                  22: 0.794, 24: 0.635, 26: 0.476, 28: 0.397},
    "aluminum": {8: 3.264, 10: 2.588, 12: 2.052, 14: 1.628, 16: 1.291, 18: 1.024, 20: 0.812, 22: 0.644, 24: 0.511,
                 26: 0.405, 28: 0.321},
}


@command(mutates=False, category="sheet_metal")
def sheet_metal_gauge(material: Literal["steel", "galvanized", "stainless", "aluminum"] = "steel",
                      gauge: int | None = None) -> dict:
    """Sheet-metal gauge table: the thickness in mm of a gauge number in steel (Manufacturers' Standard
    Gauge), galvanised steel, stainless steel (US Standard) or aluminium (Brown & Sharpe) - gauge numbers mean
    different thicknesses in each. Without `gauge`: the whole table for the material. Use the thickness with
    base_flange(thickness=...); a common default bend radius is the thickness itself."""
    table = SHEET_GAUGES[material]
    if gauge is None:
        return {"material": material, "gauges": {str(g): t for g, t in table.items()}}
    if gauge not in table:
        raise DocumentError(f"no {gauge} gauge in the {material} table; it has {sorted(table)}")
    t = table[gauge]
    return {"material": material, "gauge": gauge, "thickness": t, "thickness_in": round(t / 25.4, 4), "suggested_bend_radius": t}
