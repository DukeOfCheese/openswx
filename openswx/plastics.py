"""Plastics: injection-moulding fill analysis (like SOLIDWORKS Plastics' fill results).

The cavity is voxelised and filled from the gate(s) at a constant volume flow rate. In every cell the melt
moves by Hele-Shaw / Darcy flow with a permeability t^2 / 12 eta, t the local wall thickness: a thin wall
carries flow exactly like a slit of that gap, so one cell or many across the wall give the same pressure.
The thickness is measured per cell from the exact B-rep chords along the three axes through its centre
(1/t^2 = sum 1/chord^2, exact for a flat wall at any angle). The melt is a power-law fluid; eta is evaluated at each cell's wall shear rate
(lagged one step), which reproduces power-law slit flow exactly. Each step solves the pressure on the
filled cells with p = 0 on the flow front, moves the front by the fluxes into it and records the time each
cell fills. Isothermal at the melt temperature: freeze-off is checked afterwards (fill time against the
freeze time of the thin sections), not simulated.

Results: fill time and pattern, injection pressure (curve and at end of fill), clamp force, cooling time,
weld lines (cells filled from opposite sides at once), air traps (local last-to-fill points), sink-mark risk
(sections much thicker than the nominal wall) and short shots (the required pressure over the machine
limit). Material values are generic grade averages.
"""
from __future__ import annotations

import math

import numpy as np

# rho (kg/m3, melt), k (W/m K), cp (J/kg K), n (power law), eta (Pa s at 1000 1/s and the reference melt
# temperature), melt / mold / ejection / no-flow temperatures (C), b (1/K: eta ~ exp(-b (T - T_ref)))
MATERIALS = {
    "ABS": dict(rho=950, k=0.17, cp=2100, n=0.30, eta=200, melt=240, mold=60, eject=88, noflow=120, b=0.020),
    "PC": dict(rho=1050, k=0.20, cp=1800, n=0.65, eta=350, melt=300, mold=90, eject=127, noflow=160, b=0.015),
    "PP": dict(rho=750, k=0.15, cp=2700, n=0.33, eta=100, melt=230, mold=40, eject=93, noflow=120, b=0.015),
    "PA66": dict(rho=970, k=0.25, cp=2500, n=0.60, eta=80, melt=290, mold=80, eject=150, noflow=220, b=0.020),
    "POM": dict(rho=1150, k=0.25, cp=2200, n=0.40, eta=150, melt=205, mold=90, eject=145, noflow=160, b=0.015),
    "HDPE": dict(rho=750, k=0.30, cp=2800, n=0.40, eta=200, melt=220, mold=40, eject=100, noflow=120, b=0.012),
    "PS": dict(rho=950, k=0.15, cp=2000, n=0.30, eta=150, melt=220, mold=40, eject=80, noflow=110, b=0.020),
    "PC/ABS": dict(rho=1000, k=0.18, cp=1900, n=0.40, eta=250, melt=260, mold=70, eject=110, noflow=130, b=0.018),
}
GAMMA_REF = 1000.0


def material(name: str, melt: float | None = None, mold: float | None = None, newtonian: bool = False) -> dict:
    key = {k.upper(): k for k in MATERIALS}.get(name.upper())
    if key is None:
        raise ValueError(f"plastic must be one of {', '.join(MATERIALS)}")
    m = dict(MATERIALS[key], name=key)
    T = m["melt"] if melt is None else float(melt)
    eta_T = m["eta"] * math.exp(-m["b"] * (T - m["melt"]))
    n = 1.0 if newtonian else m["n"]
    m.update(melt=T, mold=m["mold"] if mold is None else float(mold), n=n, eta_T=eta_T,
             consistency=eta_T * GAMMA_REF ** (1 - n))  # power law m (Pa s^n)
    return m


def eta_effective(mat: dict, mean_speed: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Viscosity that makes 12 eta u / t^2 the power-law slit pressure gradient at mean speed u (m/s), gap t (m)."""
    n = mat["n"]
    if n == 1.0:
        return np.full_like(t, mat["eta_T"])
    gw = np.clip((2 * n + 1) / n * mean_speed / (t / 2), 1.0, 1e6)  # wall shear rate (1/s)
    return mat["consistency"] * gw ** (n - 1) * (2 * n + 1) / (3 * n)


def chord_thickness(body, inside: np.ndarray, origin, h: float) -> np.ndarray:
    """Per cell wall thickness from the exact chords of the B-rep along the three axes through the cell centre
    (one ray per line of cells): 1/t^2 = sum 1/chord^2, exact for a flat wall at any angle."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(body.wrapped, 1e-7)
    n = inside.shape
    inv = np.zeros(n)
    jitter = (1.3e-7, 1.7e-7, 1.1e-7)  # off exact edges / vertices
    for ax in range(3):
        o1, o2 = [a for a in range(3) if a != ax]
        cs = origin[ax] + (np.arange(n[ax]) + 0.5) * h  # cell centres along the ray
        for i in range(n[o1]):
            for j in range(n[o2]):
                sl = [i, j]
                line = [slice(None)] * 3
                line[o1], line[o2] = i, j
                mask = inside[tuple(line)]
                if not mask.any():
                    continue
                p = [0.0, 0.0, 0.0]
                p[o1] = origin[o1] + (sl[0] + 0.5) * h + jitter[o1]
                p[o2] = origin[o2] + (sl[1] + 0.5) * h + jitter[o2]
                p[ax] = origin[ax] - 1.0
                d = [0.0, 0.0, 0.0]
                d[ax] = 1.0
                inter.Perform(gp_Lin(gp_Pnt(*p), gp_Dir(*d)), -1e9, 1e9)
                chord = np.full(n[ax], np.inf)
                if inter.IsDone() and inter.NbPnt() >= 2:
                    w = np.sort([inter.WParameter(k) + origin[ax] - 1.0 for k in range(1, inter.NbPnt() + 1)])
                    a, b = w[0:-1:2], w[1::2]
                    for k in np.nonzero(mask)[0]:
                        c = cs[k]
                        m = (a <= c) & (c <= b)
                        if not m.any():  # a kept cell whose centre is just outside: the nearest interval
                            m = np.zeros(len(a), bool)
                            m[int(np.argmin(np.minimum(abs(a - c), abs(b - c))))] = True
                        chord[k] = float((b - a)[m][0])
                v = inv[tuple(line)]
                v += np.where(mask, 1.0 / chord ** 2, 0.0)
                inv[tuple(line)] = v
    return np.where(inside, 1.0 / np.sqrt(np.maximum(inv, 1e-30)), 0.0)


def freeze_time(t_mm: float, mat: dict, to: str = "noflow") -> float:
    """Seconds for the centre of a wall of thickness t to cool from melt to `to` (slab, first Fourier term)."""
    alpha = mat["k"] / (mat["rho"] * mat["cp"])
    t = t_mm * 1e-3
    ratio = (8 / math.pi ** 2) * (mat["melt"] - mat["mold"]) / max(mat[to] - mat["mold"], 1e-6)
    return max(0.0, t * t / (math.pi ** 2 * alpha) * math.log(max(ratio, 1.0)))


def fill(body, gates: list, mat: dict, fill_time: float | None = None, max_pressure_MPa: float = 150.0,
         element_size: float | None = None, max_cells: int = 60000, clamp_axis: str = "Z", steps: int = 120) -> dict:
    """Fill the cavity `body` from gates ([x, y, z] points or faces). Returns the summary, the injection
    pressure history and (private) per-cell fields for plotting."""
    from scipy.ndimage import label
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import cg, spsolve

    from .fea import voxelize
    V_mm3, A_mm2 = body.volume, body.area
    nominal = 2 * V_mm3 / A_mm2  # thin-walled parts: V / A ~ t / 2
    h = float(element_size) if element_size else max(nominal / 2, (V_mm3 / max_cells) ** (1 / 3))
    frac, _flags, origin, n = voxelize(body, h, sub=2)
    inside = frac >= 0.5
    if inside.sum() < 8:
        raise ValueError("the part is too small for this element size")
    lab, _k = label(inside)
    t_cell = chord_thickness(body, inside, origin, h)
    idx = -np.ones(n, int)
    cells = np.argwhere(inside)
    idx[inside] = np.arange(len(cells))
    N = len(cells)
    centres = origin + (cells + 0.5) * h
    vol = frac[inside] * h ** 3  # mm3
    thick = t_cell[inside]
    pa, pb, pax = [], [], []  # face pairs a -> b along +axis
    for ax in range(3):
        sl_a, sl_b = [slice(None)] * 3, [slice(None)] * 3
        sl_a[ax], sl_b[ax] = slice(0, -1), slice(1, None)
        a, b = idx[tuple(sl_a)], idx[tuple(sl_b)]
        m = (a >= 0) & (b >= 0)
        pa.append(a[m])
        pb.append(b[m])
        pax.append(np.full(int(m.sum()), ax))
    pa, pb, pax = np.concatenate(pa), np.concatenate(pb), np.concatenate(pax)
    gate_cells = set()
    for g in gates:
        if hasattr(g, "wrapped"):  # a face: the cells next to it
            near = np.array([g.distance_to(tuple(c)) for c in centres]) <= h * 0.75
            gate_cells |= set(np.nonzero(near)[0].tolist())
        else:
            d = np.linalg.norm(centres - np.asarray(g, float), axis=1)
            near = np.nonzero(d <= h * 0.9)[0]
            gate_cells |= set(near.tolist() if len(near) else [int(np.argmin(d))])
    if not gate_cells:
        raise ValueError("no gate: give a point on the part or a face")
    gate = np.array(sorted(gate_cells))
    reach = np.isin(lab[inside], np.unique(lab[inside][gate]))
    if fill_time is None:
        fill_time = float(np.clip(0.4 + V_mm3 / 25000.0, 0.4, 6.0))  # ~1 s for 15 cm3: a generic default
    V_total = float(vol[reach].sum())
    Q = V_total / fill_time  # mm3/s
    f = np.zeros(N)
    f[gate] = 1.0
    filled = np.zeros(N, bool)
    filled[gate] = True
    t_fill = np.full(N, np.inf)
    t_fill[gate] = 0.0
    donors = np.zeros((N, 6))  # flux received from -x, +x, -y, +y, -z, +z while filling
    speed = np.full(N, GAMMA_REF * (thick * 1e-3 / 2) * mat["n"] / (2 * mat["n"] + 1))  # m/s at gamma_ref
    p = np.zeros(N)
    t, history, short = 0.0, [], False
    dt = V_total / steps / Q
    area_m2 = h * h * 1e-6
    enclosed = np.zeros(n, bool)
    n_reach = int(reach.sum())
    for _it in range(steps * 8):
        eta = eta_effective(mat, speed, thick * 1e-3)
        K = (thick * 1e-3) ** 2 / (12 * eta) * (vol / h ** 3)  # m2 / (Pa s), times the cell's fluid fraction
        G = 2 * K[pa] * K[pb] / np.maximum(K[pa] + K[pb], 1e-30) * h * 1e-3  # m3 / (Pa s): K A / L with A / L = h
        fa, fb = filled[pa], filled[pb]
        both, edge_a, edge_b = fa & fb, fa & ~fb, fb & ~fa  # edge: filled -> front face (p = 0 on the front)
        if not (edge_a.any() or edge_b.any()):
            break
        F = np.nonzero(filled)[0]
        loc = -np.ones(N, int)
        loc[F] = np.arange(len(F))
        rows = np.concatenate([pa[both], pb[both], pa[both], pb[both], pa[edge_a], pb[edge_b]])
        cols = np.concatenate([pa[both], pb[both], pb[both], pa[both], pa[edge_a], pb[edge_b]])
        vals = np.concatenate([G[both], G[both], -G[both], -G[both], G[edge_a], G[edge_b]])
        A = coo_matrix((vals, (loc[rows], loc[cols])), shape=(len(F), len(F))).tocsr()
        rhs = np.zeros(len(F))
        rhs[loc[gate]] = Q * 1e-9 * vol[gate] / vol[gate].sum()  # m3/s
        pf = spsolve(A, rhs) if len(F) < 40000 else cg(A, rhs, x0=p[F], rtol=1e-8, maxiter=3000)[0]
        p[:] = 0.0
        p[F] = pf
        history.append((t, float(p[gate].max()) / 1e6))
        if history[-1][1] > max_pressure_MPa:
            short = True
            break
        qa = np.where(edge_a, G * p[pa], 0.0)  # into b (+axis side of a filled cell)
        qb = np.where(edge_b, G * p[pb], 0.0)  # into a (-axis side of a filled cell)
        qin = np.bincount(pb, weights=qa, minlength=N) + np.bincount(pa, weights=qb, minlength=N)
        qff = np.where(both, np.abs(G * (p[pa] - p[pb])), 0.0)
        through = (np.bincount(pa, weights=qff + qa, minlength=N) + np.bincount(pb, weights=qff + qb, minlength=N)) / 2
        speed = np.where(filled, np.maximum(through / (area_m2 * vol / h ** 3), 1e-6), speed)
        for side, (sel, tgt, q) in enumerate(((edge_a, pb, qa), (edge_b, pa, qb))):
            for ax in range(3):  # edge_a feeds its front cell from the -axis side, edge_b from the +axis side
                m = sel & (pax == ax)
                if m.any():
                    np.add.at(donors[:, 2 * ax + side], tgt[m], q[m])
        front = np.nonzero(qin > 0)[0]
        add = qin[front] * 1e9 * dt / vol[front]
        took = np.minimum(add, 1.0 - f[front])  # a cell takes no more than it holds: time follows the
        gained = float((took * vol[front]).sum())  # volume actually in the cavity, so the fill time is V / Q
        newly = took >= 1.0 - f[front] - 1e-12
        tf = front[newly]
        t_fill[tf] = t + (1.0 - f[tf]) / add[newly] * gained / Q
        f[front] += took
        filled[tf] = True
        t += gained / Q
        fg = np.zeros(n, bool)
        fg[inside] = filled | ~reach
        e_lab, ne = label(inside & ~fg)
        if ne > 1:  # unfilled space split up: its small pieces are closed in by the melt
            sizes = np.bincount(e_lab.ravel())
            sizes[0] = 0
            small = np.nonzero((sizes >= 4) & (sizes <= 0.1 * n_reach))[0]
            if len(small):
                enclosed |= np.isin(e_lab, small)
        if filled[reach].all():
            break
    done = filled & reach
    t_end = float(np.max(t_fill[done])) if done.any() else 0.0
    # weld lines: neighbours on either side of a cell flowing toward each other (fronts meeting at more than
    # ~45 degrees; flatter meetings are meld lines and are not flagged)
    U = np.zeros(tuple(n) + (3,))
    dirs = np.stack([donors[:, 2 * a] - donors[:, 2 * a + 1] for a in range(3)], 1)
    U[inside] = dirs / np.maximum(np.linalg.norm(dirs, axis=1, keepdims=True), 1e-30)
    weld_g = np.zeros(n, bool)
    for a in range(3):
        lo, hi = [slice(None)] * 3, [slice(None)] * 3
        mid = [slice(None)] * 3
        lo[a], mid[a], hi[a] = slice(0, -2), slice(1, -1), slice(2, None)
        both_in = inside[tuple(lo)] & inside[tuple(hi)] & inside[tuple(mid)]
        conv = U[tuple(lo)][..., a] - U[tuple(hi)][..., a]
        weld_g[tuple(mid)] |= both_in & (conv > 0.75)
    weld = weld_g[inside]
    weld[gate] = False
    # air traps: small pieces of unfilled space the melt closed in (at least 4 cells), where each fills last
    T = np.full(n, -np.inf)
    T[inside] = np.where(np.isfinite(t_fill), t_fill, -np.inf)
    last = np.unravel_index(int(np.argmax(T)), n)
    plab, npk = label(enclosed, structure=np.ones((3, 3, 3)))
    traps = []
    for k in range(1, npk + 1):
        cs = np.argwhere(plab == k)
        c = cs[int(np.argmax(T[tuple(cs.T)]))]
        traps.append([round(float(v), 3) for v in origin + (c + 0.5) * h])
    # the last place to fill traps air too unless it lies on the part's outline seen along the clamp axis (where
    # the parting line vents it)
    from scipy.ndimage import distance_transform_edt
    ax = "XYZ".index(clamp_axis.upper())
    outline = inside.any(axis=ax)
    col = tuple(v for k, v in enumerate(last) if k != ax)
    if np.isfinite(T[last]) and distance_transform_edt(np.pad(outline, 1))[tuple(c + 1 for c in col)] > 2.5:
        traps.append([round(float(v), 3) for v in origin + (np.array(last) + 0.5) * h])
    nominal_t = float(np.median(thick))
    sink = thick > 1.5 * nominal_t
    # clamp force: mean end-of-fill pressure per column x its area
    P = np.zeros(n)
    P[inside] = p
    cnt = inside.sum(axis=ax)
    clamp_N = float(np.where(cnt > 0, P.sum(axis=ax) / np.maximum(cnt, 1), 0.0).sum() * area_m2)
    t95, t05 = float(np.percentile(thick, 95)), float(np.percentile(thick, 5))
    warnings = []
    if short:
        warnings.append(f"short shot: the pressure needed passed {max_pressure_MPa:g} MPa with "
                        f"{100 * vol[done].sum() / V_total:.1f} % filled - add or move gates, thicken the walls or fill more slowly")
    fz = freeze_time(t05, mat, "noflow")
    if t_end > fz:
        warnings.append(f"the fill ({t_end:.2f} s) takes longer than the thin sections ({t05:.2f} mm) take to freeze "
                        f"({fz:.2f} s): hesitation / short-shot risk - fill faster or thicken them")
    if not reach.all():
        warnings.append(f"{int((~reach).sum())} cells are not connected to a gate")
    return {
        "material": mat["name"], "melt_temperature_C": mat["melt"], "mold_temperature_C": mat["mold"],
        "viscosity_Pa_s": round(mat["eta_T"], 2), "power_law_n": mat["n"],
        "cells": N, "element_size": round(h, 4), "gate_cells": len(gate), "fill_time_s": round(t_end, 4),
        "flow_rate_cm3_s": round(Q / 1000, 4), "filled_pct": round(100 * float(vol[done].sum()) / V_total, 2),
        "short_shot": short, "injection_pressure_MPa": round(history[-1][1] if history else 0.0, 4),
        "max_injection_pressure_MPa": round(max((v for _t, v in history), default=0.0), 4),
        "clamp_force_tonne": round(clamp_N / 9806.65, 4), "clamp_axis": clamp_axis.upper(),
        "nominal_wall_mm": round(nominal_t, 3), "max_wall_mm": round(float(thick.max()), 3),
        "cooling_time_s": round(freeze_time(t95, mat, "eject"), 2), "weld_line_cells": int(weld.sum()),
        "weld_lines": [[round(float(v), 3) for v in c] for c in centres[weld][:: max(1, int(weld.sum()) // 200)]],
        "air_traps": traps, "sink_mark_cells": int(sink.sum()),
        "last_to_fill": [round(float(v), 3) for v in origin + (np.array(last) + 0.5) * h],
        "pressure_history": [[round(a, 5), round(b, 4)] for a, b in history[:: max(1, len(history) // 100)]],
        "warnings": warnings,
        "_cells": {"centres": centres, "fill_time": np.where(np.isfinite(t_fill), t_fill, np.nan), "pressure": p / 1e6,
                   "thickness": thick, "weld": weld.astype(float), "sink": sink.astype(float)}}


def surface_values(result: dict, mesh: dict, field: str) -> dict:
    """Each display vertex's value of a cell field (nearest cell centre); None where it never filled."""
    from scipy.spatial import cKDTree
    c = result["_cells"]
    tree = cKDTree(c["centres"])
    vals = np.asarray(c[field], float)
    out = {}
    for f in mesh["faces"]:
        _, k = tree.query(np.asarray(f["v"], float).reshape(-1, 3))
        out[f["id"]] = [None if not np.isfinite(a) else round(float(a), 5) for a in vals[k]]
    return out
