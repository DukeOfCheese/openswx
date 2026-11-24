"""More simulation studies on fea.py's voxel finite-cell mesh: nonlinear static (elastic-plastic material,
bilinear hardening, incremental secant iterations), linear dynamics by modal superposition (harmonic response
sweep and step-load time history), a drop test (energy method: the impact's kinetic energy taken up as strain
energy under an equivalent inertial load) and fatigue (S-N curve from the material's strengths, Goodman mean
stress correction, Miner's rule). Units: mm, N, MPa, tonne, s."""

from __future__ import annotations

import math

import numpy as np
from scipy.sparse import coo_matrix, diags

from openswx.analysis.fea import SLIVER_FILL, _B_from_grads, _D, _build_structural, _grads, mass_fill

G_MM = 9810.0  # standard gravity, mm/s^2


def _material(material: dict):
    E, nu = material.get("elastic_modulus"), material.get("poisson")
    if not E or nu is None:
        raise ValueError("assign a library material (with elastic modulus and Poisson ratio) first")
    return float(E), float(nu)


def _dofs(m, faces):
    fixed = set()
    for f in faces:
        fixed.update(m["nodes_near"](f))
    if not fixed:
        raise ValueError("no mesh nodes found on the fixed faces; reduce element_size")
    fdofs = np.array(sorted(3 * c + d for c in fixed for d in range(3)))
    return fdofs, np.setdiff1d(np.arange(3 * m["nn"]), fdofs)


def _loads(m, loads) -> np.ndarray:
    if m.get("kind") == "tet":  # (a tetrahedral model: fea_tet.modal_basis)
        return m["loads"](loads)
    F = np.zeros(3 * m["nn"])
    adj = np.bincount(m["conn"].ravel(), minlength=m["nn"]).astype(float)
    for face, force in loads:
        nodes = m["nodes_near"](face)
        if not nodes:
            raise ValueError("no mesh nodes found on a loaded face; reduce element_size")
        w = adj[nodes] / adj[nodes].sum()
        for c, wc in zip(nodes, w):
            F[3 * c: 3 * c + 3] += np.asarray(force, float) * wc
    return F


def _lumped_mass(m, rho: float) -> np.ndarray:
    me = rho * 1e-12 * mass_fill(m["fr"]) * m["rho_scale"] * m["h"] ** 3 / 8  # tonne per node per element
    return np.bincount(m["conn"].ravel(), weights=np.repeat(me, 8), minlength=m["nn"])


def _factor(A):
    from scipy.sparse.linalg import splu
    A = A.tocsc()
    A = (A + A.T) * 0.5
    return splu(A.tocsc(), permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0, options={"SymmetricMode": True})


def _von_mises(m, U, E, nu) -> np.ndarray:
    """Element von Mises stress for modulus E everywhere (times the mesh's own region factors)."""
    B0 = _B_from_grads(_grads(0, 0, 0, m["hh"])[0])
    sig = (_D(E, nu) @ B0 @ U[m["edofs"]].T).T * m["scale"][:, None]
    sx, sy, sz, txy, tyz, tzx = sig.T
    return np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2) + 3 * (txy ** 2 + tyz ** 2 + tzx ** 2))


def _grid(m, vm, U) -> dict:
    return {"origin": m["origin"], "h": m["h"], "n": m["n"], "mask": m["mask"],
            "elem_ijk": np.stack([m["I"], m["J"], m["K"]], 1), "vm": vm, "U": U.reshape(-1, 3), "used": m["used"]}


def solve_nonlinear(body, material: dict, fixtures: list, loads: list, steps: int = 10, hardening: float = 0.05,
                    element_size: float | None = None, target_elements: int = 12000) -> dict:
    """Elastic-plastic static study: bilinear material (yield strength, then a tangent modulus `hardening` x E),
    the load applied in `steps` increments, each solved by secant iterations (deformation theory, small
    displacements). Returns the load-displacement curve, the stress, plastic elements and plastic strain."""
    E, nu = _material(material)
    sy = float(material.get("yield_strength") or 0)
    if sy <= 0:
        raise ValueError("the material needs a yield strength for a nonlinear study")
    if not fixtures or not loads:
        raise ValueError("add fixtures and loads first")
    Et = max(1e-6, float(hardening)) * E
    m = _build_structural(body, E, nu, element_size, target_elements, None, keep=True)
    fdofs, free = _dofs(m, fixtures)
    F = _loads(m, loads)
    ne = len(m["I"])
    s = np.ones(ne)  # per-element secant modulus factor
    shape = (3 * m["nn"], 3 * m["nn"])
    curve, U, vm_unit, iters = [], np.zeros(3 * m["nn"]), np.zeros(ne), 0
    for k in range(1, int(steps) + 1):
        Fk = F * k / steps
        for _ in range(60):
            iters += 1
            Kg = coo_matrix(((m["Kall"] * (m["scale"] * s)[:, None, None]).reshape(-1), (m["rows"], m["cols"])), shape=shape).tocsr()
            U = np.zeros(3 * m["nn"])
            U[free] = _factor(Kg[free][:, free]).solve(Fk[free])
            vm_unit = _von_mises(m, U, E, nu)  # E x (equivalent strain)
            eps = vm_unit / E
            sig = np.where(vm_unit <= sy, vm_unit, sy + Et * (eps - sy / E))
            s_new = np.where(vm_unit > 1e-12, sig / np.maximum(vm_unit, 1e-30), 1.0)
            done = np.abs(s_new - s).max() < 1e-4
            s = 0.5 * s + 0.5 * s_new
            if done:
                break
        curve.append({"load_factor": k / steps, "max_displacement": float(np.linalg.norm(U.reshape(-1, 3), axis=1).max())})
    stress = vm_unit * s
    plastic = np.maximum(0.0, vm_unit / E - stress / E)
    disp = np.linalg.norm(U.reshape(-1, 3), axis=1)
    return {"element_size": m["h"], "elements": ne, "max_displacement": float(disp.max()), "max_von_mises": float(stress.max()),
            "yield_strength": sy, "plastic_elements": int((vm_unit > sy).sum()), "max_plastic_strain": float(plastic.max()),
            "iterations": iters, "curve": curve, "_grid": _grid(m, stress, U)}


def _modal_basis(body, material, fixtures, modes, element_size, target_elements, mesh: str = "voxel"):
    if mesh == "tetrahedral":
        from openswx.analysis.fea_tet import modal_basis
        return modal_basis(body, material, fixtures, modes, element_size, target_elements)
    from scipy.sparse.linalg import eigsh
    E, nu = _material(material)
    rho = float(material.get("density") or 0)
    if rho <= 0:
        raise ValueError("the material needs a density for a dynamic study")
    m = _build_structural(body, E, nu, element_size, target_elements, None)
    fdofs, free = _dofs(m, fixtures)
    Mdiag = np.repeat(_lumped_mass(m, rho), 3)
    A = m["Kg"][free][:, free].tocsc()
    A = (A + A.T) * 0.5
    k = max(1, min(int(modes), len(free) - 2))
    vals, vecs = eigsh(A, k=k, M=diags(Mdiag[free]).tocsc(), sigma=0.0, which="LM")  # M-orthonormal vectors
    order = np.argsort(vals)
    return m, free, np.clip(vals[order], 0, None), vecs[:, order]


def solve_harmonic(body, material: dict, fixtures: list, loads: list, f_min: float, f_max: float, points: int = 200,
                   damping: float = 0.02, modes: int = 10, element_size: float | None = None,
                   target_elements: int = 8000, mesh: str = "voxel") -> dict:
    """Harmonic response: the loads applied as F sin(2 pi f t), steady-state amplitude of the largest node
    displacement over a frequency sweep, by superposing the first `modes` modes with a modal damping ratio."""
    m, free, w2, phi = _modal_basis(body, material, fixtures, modes, element_size, target_elements, mesh)
    P = phi.T @ _loads(m, loads)[free]
    w = np.sqrt(w2)
    out, best = [], (0.0, 0.0)
    for f in np.linspace(float(f_min), float(f_max), int(points)):
        om = 2 * math.pi * f
        u = np.zeros(3 * m["nn"], complex)
        u[free] = phi @ (P / (w2 - om ** 2 + 2j * damping * w * om))
        amp = float(np.sqrt((np.abs(u.reshape(-1, 3)) ** 2).sum(axis=1)).max())
        out.append({"frequency": float(f), "max_displacement": amp})
        if amp > best[1]:
            best = (float(f), amp)
    return {"frequencies_hz": [float(x) / (2 * math.pi) for x in w], "damping": damping, "response": out,
            "peak_frequency": best[0], "peak_displacement": best[1], "element_size": m["h"]}


def solve_step_response(body, material: dict, fixtures: list, loads: list, duration: float, points: int = 400,
                        damping: float = 0.02, modes: int = 10, element_size: float | None = None,
                        target_elements: int = 8000, mesh: str = "voxel") -> dict:
    """Transient response to the loads applied suddenly at t = 0 and held (modal superposition, each mode
    solved exactly): the displacement of the node that moves most, against time; the peak and its ratio to
    the static value (dynamic amplification: 2 when undamped)."""
    m, free, w2, phi = _modal_basis(body, material, fixtures, modes, element_size, target_elements, mesh)
    P = phi.T @ _loads(m, loads)[free]
    w = np.sqrt(np.maximum(w2, 1e-12))
    q_static = P / w ** 2
    u_static = np.zeros(3 * m["nn"])
    u_static[free] = phi @ q_static
    mags = np.linalg.norm(u_static.reshape(-1, 3), axis=1)
    node = int(np.argmax(mags))
    dirv = u_static[3 * node: 3 * node + 3] / (mags[node] or 1.0)
    phin = np.zeros((3, phi.shape[1]))
    for d in range(3):
        r = int(np.searchsorted(free, 3 * node + d))
        if r < len(free) and free[r] == 3 * node + d:
            phin[d] = phi[r]
    z = float(damping)
    wd = w * math.sqrt(max(1e-12, 1 - z * z))
    hist = []
    for t in np.linspace(0.0, float(duration), int(points)):
        qt = q_static * (1 - np.exp(-z * w * t) * (np.cos(wd * t) + z / math.sqrt(1 - z * z) * np.sin(wd * t)))
        hist.append({"time": float(t), "displacement": float(dirv @ (phin @ qt))})
    peak = max(h["displacement"] for h in hist)
    return {"frequencies_hz": [float(x) / (2 * math.pi) for x in w], "history": hist, "static_displacement": float(mags[node]),
            "peak_displacement": peak, "dynamic_amplification": peak / (mags[node] or 1.0), "element_size": m["h"]}


def solve_drop(body, material: dict, impact_faces: list, height: float, direction, element_size: float | None = None,
               target_elements: int = 12000) -> dict:
    """Drop test (energy method): the part falls `height` mm along `direction` onto a rigid floor and lands on
    impact_faces. Its kinetic energy m g h is taken up as strain energy under an inertial load of G times its
    weight with the landing faces held; G (the peak deceleration in g) and the stress and deflection at that
    instant follow. A quasi-static estimate: no explicit dynamics, rebound or local contact stress."""
    E, nu = _material(material)
    rho = float(material.get("density") or 0)
    if rho <= 0:
        raise ValueError("the material needs a density for a drop test")
    if height <= 0:
        raise ValueError("the drop height must be positive")
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    m = _build_structural(body, E, nu, element_size, target_elements, None)
    fdofs, free = _dofs(m, impact_faces)
    mass = _lumped_mass(m, rho)
    F = (mass[:, None] * G_MM * d[None, :]).reshape(-1)  # the part's weight, as nodal inertial forces
    U = np.zeros(3 * m["nn"])
    U[free] = _factor(m["Kg"][free][:, free]).solve(F[free])
    Ug = 0.5 * float(U @ F)  # strain energy under 1 g (N mm)
    total = float(mass.sum())
    Gs = math.sqrt(total * G_MM * height / Ug) if Ug > 0 else float("inf")
    # slivers (massless, under-integrated) only follow their neighbours: report and plot the real cells
    good = m["fr"] >= SLIVER_FILL
    on_good = np.zeros(m["nn"], bool)
    on_good[np.unique(m["conn"][good].ravel())] = True
    Uv = U.reshape(-1, 3).copy()
    if not on_good.all():  # a sliver-only node takes the mean of the real nodes it shares a cell with
        acc, cnt = np.zeros((m["nn"], 3)), np.zeros(m["nn"])
        for e in np.nonzero(~good)[0]:
            nodes = m["conn"][e]
            ok = nodes[on_good[nodes]]
            if len(ok):
                acc[nodes] += Uv[ok].mean(axis=0)
                cnt[nodes] += 1
        bad = ~on_good & (cnt > 0)
        Uv[bad] = acc[bad] / cnt[bad, None]
        Uv[~on_good & (cnt == 0)] = 0.0
    U = Uv.reshape(-1)
    vm = _von_mises(m, U, E, nu) * Gs
    if good.any():
        vm = np.minimum(vm, vm[good].max())
    disp = np.linalg.norm(Uv, axis=1) * Gs
    ys = material.get("yield_strength") or material.get("tensile_strength") or 0.0
    return {"element_size": m["h"], "mass_kg": total * 1000, "impact_velocity_m_s": math.sqrt(2 * G_MM * height) / 1000,
            "peak_acceleration_g": Gs, "max_von_mises": float(vm.max()), "max_displacement": float(disp.max()),
            "factor_of_safety": float(ys / vm.max()) if ys and vm.max() > 0 else None, "_grid": _grid(m, vm, U * Gs)}


INFINITE_LIFE = 1e12


def fatigue_life(vm, material: dict, cycles: float, loading: str = "reversed", scale: float = 1.0) -> dict:
    """Fatigue from a static result's element von Mises stress: S-N curve S = a N^b through 0.9 Su at 10^3
    cycles and the endurance limit Se (ferrous: 0.5 Su, at most 700 MPa, at 10^6; others: 0.35 Su at 5x10^8);
    loading reversed (R = -1) or zero_based (R = 0: Goodman correction for the mean stress); Miner damage."""
    su = float(material.get("tensile_strength") or 0)
    if su <= 0:
        raise ValueError("the material needs a tensile strength for a fatigue study")
    cat = str(material.get("category") or "").lower()
    ferrous = any(k in cat for k in ("steel", "iron"))
    se, n_end = (min(0.5 * su, 700.0), 1e6) if ferrous else (0.35 * su, 5e8)
    s3 = 0.9 * su
    b = -math.log10(s3 / se) / math.log10(n_end / 1e3)
    a = s3 / (1e3 ** b)
    s = np.asarray(vm, float) * float(scale)
    if loading == "reversed":
        sar = s
    else:  # zero to peak: amplitude and mean are both half the peak
        sa, sm = s / 2, s / 2
        sar = np.where(sm < su, sa / np.maximum(1 - sm / su, 1e-9), np.inf)
    life = np.where(sar <= se, INFINITE_LIFE, (np.maximum(sar, 1e-9) / a) ** (1 / b))
    life = np.where(sar >= su, 1.0, life)
    worst = float(life.min())
    return {"endurance_limit": se, "s_n_exponent": b, "min_life_cycles": worst if worst < INFINITE_LIFE else None,
            "infinite_life": bool(worst >= INFINITE_LIFE), "damage": float(cycles / worst) if worst < INFINITE_LIFE else 0.0,
            "fatigue_safety_factor": float(se / sar.max()) if np.isfinite(sar.max()) and sar.max() > 0 else None,
            "elements_failing": int((life < cycles).sum()), "_life": life}
