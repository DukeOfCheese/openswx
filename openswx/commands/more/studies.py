"""Additional studies: nonlinear, dynamic, drop, fatigue and flow simulations."""
from __future__ import annotations

from typing import Literal

from openswx.core.document import DocumentError
from openswx.commands.support import _face
from openswx.session import APP, Vec3, command, unlocked


def _part_study():
    from openswx.commands.support import _resolve_study, _sim_doc
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
    from openswx.commands.support import _sim_doc
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
    from openswx.commands.support import _sim_doc
    cached = getattr(APP, "fatigue_result", None)
    if not cached or cached[0] != _sim_doc().revision:
        raise DocumentError("run the fatigue study first")
    static, r = cached[1], cached[2]
    g = dict(static["_grid"], vm=np.log10(np.maximum(r["_life"], 1.0)))
    mesh = refine_mesh(_sim_doc().mesh(), 2 * static["element_size"])
    out = map_to_mesh({"_grid": g}, mesh)
    return {"faces": mesh["faces"], "log_life": out["stress"], **_public(r)}


@command(mutates=False, category="simulation")
def flow_simulation(speed: float = 10.0, direction: Vec3 = (1, 0, 0), fluid: Literal["air", "water"] = "air",
                    cells_across: int = 20) -> dict:
    """Flow Simulation (external, potential flow): the part in a free stream of `speed` m/s along `direction`.
    Surface pressure coefficient and velocity, net pressure (side / lift) force and a skin-friction drag
    estimate. Ideal (inviscid) flow on a voxel grid - a first-look estimate, not turbulent CFD: no separation,
    wake or pressure drag. flow_results plots Cp."""
    from openswx.analysis.analysis_more import flow_study
    from openswx.commands.support import _sim_doc
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
    from openswx.commands.support import _sim_doc
    cached = getattr(APP, "flow_result", None)
    if not cached or cached[0] != _sim_doc().revision:
        raise DocumentError("run the flow simulation first")
    mesh = _sim_doc().mesh()
    return {"faces": mesh["faces"], "cp": flow_surface_values(cached[1], mesh), **_public(cached[1])}


def _cfd_body():
    from openswx.commands.support import _sim_doc
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


__all__ = ["nonlinear_run", "harmonic_run", "step_response_run", "drop_test_run", "fatigue_run",
           "fatigue_results", "flow_simulation", "flow_results", "cfd_external", "cfd_internal",
           "cfd_results"]
