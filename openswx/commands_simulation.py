"""Simulation and evaluation commands (same registry as session.py, which re-exports them).

Moved out of session.py; helpers come from it by name, and other commands through `_S` at call time."""
from __future__ import annotations

from . import session as _S
from .session import (  # noqa: F401
    APP, DocumentError, Vec3, _direction, _eval_body, _face, _plane_spec, _resolve_study, _sim_doc, _sim_face,
    _study_edit, _thermal, command, unlocked,
)

__all__ = ["draft_analysis", "thickness_analysis", "undercut_analysis", "clearance_verification",
           "curvature_analysis", "section_view", "section_properties", "check_geometry", "body_interference",
           "simulation_add_fixture", "simulation_add_force", "simulation_add_pressure", "simulation_add_spring",
           "simulation_add_contact", "simulation_add_bolt", "simulation_clear", "simulation_run",
           "simulation_results", "frequency_run", "frequency_mode_shape", "buckling_run", "buckling_mode_shape",
           "thermal_add_temperature", "thermal_add_heat_power", "thermal_add_convection", "thermal_clear",
           "thermal_run", "thermal_results"]


@command(mutates=False, category="evaluate")
def draft_analysis(direction: str | Vec3 = "Y", angle: float = 1.0) -> dict:
    """Evaluate > Draft Analysis: classify each face against the pull direction ('X'/'Y'/'Z', [x, y, z] or a
    plane name = its normal): positive / negative draft (>= angle deg), requires draft, or straddle."""
    from build123d import Vector
    from .evaluate import draft_analysis as da
    if isinstance(direction, str):
        d = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}.get(direction.upper())
        if d is None:
            from .features import resolve_plane
            d = tuple(resolve_plane(APP.doc.ctx, direction).z_dir)
    else:
        d = tuple(direction)
    return da(_eval_body(), Vector(*d), angle)


@command(mutates=False, category="evaluate")
def thickness_analysis(target: float = 1.0) -> dict:
    """Evaluate > Thickness Analysis: the wall thickness through the material from every face; faces
    thinner than `target` mm are listed (min_thickness per face, the part's minimum)."""
    from .evaluate import thickness_analysis as ta
    return ta(_eval_body(), target)


@command(mutates=False, category="evaluate")
def undercut_analysis(direction: str | Vec3 = "Y") -> dict:
    """Evaluate > Undercut Detection: for a mold pulled along +/- direction ('X'/'Y'/'Z', [x, y, z] or a
    plane name), each face is direction1 / direction2 / side (free) or undercut (blocked by the part)."""
    from build123d import Vector
    from .evaluate import undercut_analysis as ua
    return ua(_eval_body(), Vector(*_direction(direction)))


@command(mutates=False, category="evaluate")
def clearance_verification(min_clearance: float = 1.0, components: list[str] | None = None) -> dict:
    """Evaluate > Clearance Verification (assemblies): the gap between every pair of components (or those
    listed); pairs closer than min_clearance mm - touching or interfering included - are reported."""
    from .evaluate import clearance_verification as cv
    shapes = [(c.name, s) for c, s in APP.asm.world_shapes() if not components or c.name in components]
    return cv(shapes, min_clearance)


@command(mutates=False, category="evaluate")
def curvature_analysis() -> dict:
    """Evaluate > Curvature: maximum principal curvature (1/mm) per tessellation vertex of each face."""
    from .evaluate import curvature_analysis as ca
    return ca(_eval_body())


@command(mutates=False, category="evaluate", mcp=False)
def section_view(plane: str = "Front", offset: float = 0.0, flip: bool = False) -> dict:
    """Evaluate > Section View: cut plane ('Front'/'Top'/'Right', a reference plane or 'FaceN') offset along
    its normal; returns the plane and the cross-section cap mesh and area."""
    from .evaluate import section_view as sv
    from .features import resolve_plane
    from build123d import Plane
    doc = APP.active_doc
    ctx = getattr(doc, "ctx", None)
    if ctx is None:
        from .features import Ctx
        ctx = Ctx()
    pl = resolve_plane(ctx, _plane_spec(doc, plane) if getattr(doc, "kind", "part") == "part" else plane)
    pl = Plane(origin=pl.origin + pl.z_dir * offset, x_dir=pl.x_dir, z_dir=pl.z_dir)
    return sv(_eval_body(), pl, flip)


@command(mutates=False, category="evaluate")
def section_properties(plane: str = "Front", offset: float = 0.0) -> dict:
    """Area of the body's cross-section on a plane (Section Properties), e.g. for a cut through a part."""
    r = _S.section_view(plane, offset)
    return {"area": r["area"], "origin": r["origin"], "normal": r["normal"]}


@command(mutates=False, category="evaluate")
def check_geometry() -> dict:
    """Tools > Check: B-rep validity, open shells, very short edges, minimum edge length and radius."""
    from .evaluate import check_geometry as cg
    return cg(_eval_body())


@command(mutates=False, category="evaluate")
def body_interference() -> dict:
    """Interference between the solid bodies of a multibody part (pairs and overlap volume)."""
    from .evaluate import body_interference as bi
    return bi(_eval_body())


@command(category="simulation")
def simulation_add_fixture(face: str) -> dict:
    """Simulation > Fixture (Fixed Geometry): clamp a face ('FaceN') of the active part."""
    ref = _sim_face(face)
    return _study_edit(lambda st: st["fixtures"].append(ref))


@command(category="simulation")
def simulation_add_force(face: str, force: Vec3 | None = None, magnitude: float | None = None) -> dict:
    """Simulation > Force on a face: a total force vector [Fx, Fy, Fz] in N, or `magnitude` N normal to the
    face pushing into it. Spread uniformly over the face."""
    if force is None and magnitude is None:
        raise DocumentError("give a force vector or a magnitude")
    ref = _sim_face(face)
    return _study_edit(lambda st: st["loads"].append({"type": "force", "face": ref, "force": force, "magnitude": magnitude}))


@command(category="simulation")
def simulation_add_pressure(face: str, pressure: float) -> dict:
    """Simulation > Pressure on a face in MPa (N/mm^2), acting into the face."""
    ref = _sim_face(face)
    return _study_edit(lambda st: st["loads"].append({"type": "pressure", "face": ref, "pressure": pressure}))


@command(category="simulation")
def simulation_add_spring(face1: str, face2: str, normal_stiffness: float, shear_stiffness: float | None = None) -> dict:
    """Simulation > Connectors > Spring between two faces of separate bodies/components: total stiffness in
    N/mm along the line joining the faces (normal) and across it (shear, default = normal)."""
    r1, r2 = _sim_face(face1), _sim_face(face2)
    return _study_edit(lambda st: st.setdefault("connectors", []).append(
        {"type": "spring", "a": r1, "b": r2, "kn": normal_stiffness, "kt": shear_stiffness if shear_stiffness is not None else normal_stiffness}))


@command(category="simulation")
def simulation_add_contact(face1: str, face2: str) -> dict:
    """Simulation > Contact > No Penetration (frictionless) between facing faces of separate bodies or
    components: they may separate but not pass through each other (iterative, compression only)."""
    r1, r2 = _sim_face(face1), _sim_face(face2)
    return _study_edit(lambda st: st.setdefault("connectors", []).append({"type": "contact", "a": r1, "b": r2}))


@command(category="simulation")
def simulation_add_bolt(hole1: str, hole2: str, diameter: float | None = None, preload: float = 0.0,
                        bolt_material: str = "AISI 1020") -> dict:
    """Simulation > Connectors > Bolt between two coaxial hole faces (cylindrical 'FaceN') of separate
    bodies/components: axial stiffness E A / L and shear G A / L of a bolt of `diameter` (default the hole
    diameter) over the grip length L between the holes, optional preload (N, clamping)."""
    from .materials import lookup
    m = lookup(bolt_material)
    if not m:
        raise DocumentError(f"unknown bolt material {bolt_material!r}")
    r1, r2 = _sim_face(hole1), _sim_face(hole2)
    return _study_edit(lambda st: st.setdefault("connectors", []).append(
        {"type": "bolt", "a": r1, "b": r2, "diameter": diameter, "preload": preload, "E": m["elastic_modulus"],
         "nu": m["poisson"]}))


@command(category="simulation")
def simulation_clear(fixtures: bool = True, loads: bool = True) -> dict:
    """Remove the study's fixtures and/or loads."""
    def f(st):
        if fixtures:
            st["fixtures"] = []
        if loads:
            st["loads"] = []
            st["connectors"] = []
    return _study_edit(f)


@command(mutates=False, category="simulation")
def simulation_run(element_size: float | None = None) -> dict:
    """Run the linear static study: voxel hex mesh (incompatible-mode elements), returns max displacement
    (mm), max von Mises stress (MPa), factor of safety (material yield / max stress), reactions (N). Smaller
    element_size = more accurate and slower (default: ~12k elements)."""
    from .fea import solve_static
    doc = _sim_doc()
    if doc.body is None:
        raise DocumentError("the document has no body")
    fixtures, loads, connectors = _resolve_study(doc)
    material, regions = getattr(doc, "material", None), None
    if getattr(doc, "kind", "part") == "assembly":  # bonded components, each with its own part material
        mats = []
        for c, shape in doc.world_shapes():
            m = getattr(c.doc(doc.live_docs), "material", {}) or {}
            if not m.get("elastic_modulus"):
                raise DocumentError(f"assign a library material to {c.name}'s part first")
            mats.append((c, shape, m))
        material = dict(mats[0][2])
        material["yield_strength"] = min(m.get("yield_strength") or m.get("tensile_strength") or 1e18 for _, _, m in mats)
        regions = [(sol, m["elastic_modulus"] / material["elastic_modulus"]) for _, shape, m in mats for sol in shape.solids()]
    try:
        with unlocked():
            r = solve_static(doc.body, material, fixtures, loads, element_size or doc.study.get("element_size"),
                             regions=regions, connectors=connectors)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.sim_result = (doc.revision, r)
    out = {k: v for k, v in r.items() if not k.startswith("_")}
    if regions:
        out["bonded_components"] = len(regions)
    return out


@command(mutates=False, category="simulation", mcp=False)
def simulation_results() -> dict:
    """Per-vertex von Mises stress and displacement on the display mesh for the last simulation_run (UI plot)."""
    from .fea import map_to_mesh
    cached = getattr(APP, "sim_result", None)
    if not cached or cached[0] != _sim_doc().revision:
        raise DocumentError("run the study first (the model changed since the last run)")
    r = cached[1]
    from .fea import refine_mesh
    mesh = refine_mesh(_sim_doc().mesh(), 2 * r["element_size"])
    for f in mesh["faces"]:
        f["v"] = [round(c, 4) for c in f["v"]]
    out = map_to_mesh(r, mesh)
    out["faces"] = mesh["faces"]  # the refined display mesh the per-vertex values refer to
    out.update({k: v for k, v in r.items() if not k.startswith("_")})
    return out


@command(mutates=False, category="simulation")
def frequency_run(modes: int = 5, element_size: float | None = None) -> dict:
    """Frequency study: the first `modes` natural frequencies (Hz) of the part / bonded assembly with the
    study's fixtures (material density and modulus from the library)."""
    from .fea import solve_modal
    doc = _sim_doc()
    if doc.body is None:
        raise DocumentError("the document has no body")
    fixtures, _loads, _con = _resolve_study(doc)
    material, regions = getattr(doc, "material", None), None
    if getattr(doc, "kind", "part") == "assembly":
        mats = [(shape, c.doc(doc.live_docs).material) for c, shape in doc.world_shapes()]
        material = dict(mats[0][1])
        regions = [(sol, m["elastic_modulus"] / material["elastic_modulus"], m["density"] / material["density"])
                   for shape, m in mats for sol in shape.solids()]
    try:
        with unlocked():
            r = solve_modal(doc.body, material, fixtures, modes, element_size or doc.study.get("element_size"), regions=regions)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.modal_result = (doc.revision, r)
    return {k: v for k, v in r.items() if not k.startswith("_")}


@command(mutates=False, category="simulation", mcp=False)
def frequency_mode_shape(mode: int = 1) -> dict:
    """Normalised mode shape (per display-mesh vertex displacement) of the last frequency_run (UI plot)."""
    from .fea import map_to_mesh, refine_mesh
    cached = getattr(APP, "modal_result", None)
    if not cached or cached[0] != _sim_doc().revision:
        raise DocumentError("run the frequency study first")
    r = cached[1]
    if not 1 <= mode <= len(r["frequencies_hz"]):
        raise DocumentError(f"mode must be 1..{len(r['frequencies_hz'])}")
    g = dict(r["_grid"], U=r["_grid"]["shapes"][mode - 1])
    mesh = refine_mesh(_sim_doc().mesh(), 2 * r["element_size"])
    out = map_to_mesh({"_grid": g}, mesh)
    return {"faces": mesh["faces"], "displacement": out["displacement"], "frequency_hz": r["frequencies_hz"][mode - 1],
            "mode": mode}


@command(mutates=False, category="simulation")
def buckling_run(modes: int = 3, element_size: float | None = None) -> dict:
    """Buckling study (linear): load factors for the study's fixtures and loads; critical load = factor x the
    applied loads (a factor below 1 means the part buckles under them)."""
    from .fea import solve_buckling
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body")
    fixtures, loads, _con = _resolve_study(doc)
    try:
        with unlocked():
            r = solve_buckling(doc.body, doc.material, fixtures, loads, modes, element_size or doc.study.get("element_size"))
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.buckling_result = (doc.revision, r)
    return {k: v for k, v in r.items() if not k.startswith("_")}


@command(mutates=False, category="simulation", mcp=False)
def buckling_mode_shape(mode: int = 1) -> dict:
    """Normalised buckling mode shape per display-mesh vertex of the last buckling_run (UI plot)."""
    from .fea import map_to_mesh, refine_mesh
    cached = getattr(APP, "buckling_result", None)
    if not cached or cached[0] != APP.doc.revision:
        raise DocumentError("run the buckling study first")
    r = cached[1]
    if not 1 <= mode <= len(r["load_factors"]):
        raise DocumentError(f"mode must be 1..{len(r['load_factors'])}")
    g = dict(r["_grid"], U=r["_grid"]["shapes"][mode - 1])
    mesh = refine_mesh(APP.doc.mesh(), 2 * r["element_size"])
    out = map_to_mesh({"_grid": g}, mesh)
    return {"faces": mesh["faces"], "displacement": out["displacement"], "load_factor": r["load_factors"][mode - 1],
            "mode": mode}


@command(category="simulation")
def thermal_add_temperature(face: str, temperature: float) -> dict:
    """Thermal study: hold a face ('FaceN') at a fixed temperature (degC)."""
    ref = _face(APP.doc, face)
    return _study_edit(lambda st: _thermal(APP.doc)["temperatures"].append({"face": ref, "value": temperature}))


@command(category="simulation")
def thermal_add_heat_power(face: str, watts: float) -> dict:
    """Thermal study: heat power (W) entering the part through a face (negative = removed)."""
    ref = _face(APP.doc, face)
    return _study_edit(lambda st: _thermal(APP.doc)["heat"].append({"face": ref, "value": watts}))


@command(category="simulation")
def thermal_add_convection(face: str, coefficient: float, ambient: float = 20.0) -> dict:
    """Thermal study: convection on a face, film coefficient in W/(m^2 K) to an ambient temperature (degC)."""
    ref = _face(APP.doc, face)
    return _study_edit(lambda st: _thermal(APP.doc)["convection"].append({"face": ref, "h": coefficient, "ambient": ambient}))


@command(category="simulation")
def thermal_clear() -> dict:
    """Remove all thermal loads and boundary conditions."""
    return _study_edit(lambda st: st.__setitem__("thermal", {"temperatures": [], "heat": [], "convection": []}))


@command(mutates=False, category="simulation")
def thermal_run(element_size: float | None = None, transient: bool = False, duration: float = 60.0,
                steps: int = 50, initial_temperature: float = 20.0) -> dict:
    """Run the thermal study (finite-cell voxel mesh, material thermal conductivity). Steady state by default:
    returns min/max temperature (degC), the heat (W) into the part through each
    temperature boundary (heat_in_per_temperature_boundary, in the order added; negative = out) and their net sum.
    transient=True: the part starts at initial_temperature (degC) and is integrated over `duration` seconds in
    `steps` implicit steps using the material density and specific heat; returns the min/max/mean
    temperature history and the final field."""
    from .fea import solve_thermal, solve_thermal_transient
    from .geom import resolve_face
    doc = APP.doc
    if doc.body is None:
        raise DocumentError("the part has no body")
    th = _thermal(doc)
    rf = lambda r: resolve_face(doc.body, r)  # noqa: E731
    loads = ([(rf(x["face"]), x["value"]) for x in th["temperatures"]],
             [(rf(x["face"]), x["value"]) for x in th["heat"]],
             [(rf(x["face"]), x["h"], x["ambient"]) for x in th["convection"]])
    try:
        if transient:
            from .materials import lookup
            mat = doc.material
            cp = mat.get("specific_heat") or (lookup(mat.get("name", "")) or {}).get("specific_heat")
            with unlocked():
                r = solve_thermal_transient(doc.body, mat.get("thermal_conductivity"), mat.get("density"), cp, *loads,
                                            initial=initial_temperature, duration=duration, steps=steps,
                                            element_size=element_size)
        else:
            with unlocked():
                r = solve_thermal(doc.body, doc.material.get("thermal_conductivity"), *loads, element_size)
    except ValueError as exc:
        raise DocumentError(str(exc)) from exc
    APP.thermal_result = (doc.revision, r)
    return {k: v for k, v in r.items() if not k.startswith("_")}


@command(mutates=False, category="simulation", mcp=False)
def thermal_results() -> dict:
    """Temperature per display-mesh vertex for the last thermal_run (UI plot)."""
    from .fea import map_temperature, refine_mesh
    cached = getattr(APP, "thermal_result", None)
    if not cached or cached[0] != APP.doc.revision:
        raise DocumentError("run the thermal study first (the model changed since the last run)")
    r = cached[1]
    mesh = refine_mesh(APP.doc.mesh(), 2 * r["element_size"])
    out = map_temperature(r, mesh)
    out["faces"] = mesh["faces"]
    out.update({k: v for k, v in r.items() if not k.startswith("_")})
    return out
