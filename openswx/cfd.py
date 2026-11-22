"""Navier-Stokes flow on the part's voxel grid with the lattice Boltzmann method (D3Q19, two-relaxation-time
collision, Smagorinsky large-eddy model when the grid cannot resolve the Reynolds number).

External flow: the part in a free stream (an inlet face upstream, free-stream sides, a zero-gradient outlet);
drag and lift from the momentum exchanged at the walls, the surface pressure coefficient and a mid-plane slice
of the speed. Internal flow: fluid through the part's internal passage along one axis (a uniform inlet
velocity, a fixed-pressure outlet); the pressure drop and the flow rate.

Walls are half-way bounce-back on the voxelised part (stair-stepped). Measured against references: laminar pipe
flow within 2-3 % of Hagen-Poiseuille (plus the entrance loss) at 10-16 cells across; sphere drag 5-10 % high at
Re 20-100 (12-20 cells). Above a few hundred the flow is a large-eddy estimate on a coarse grid (regularised
collision, Smagorinsky model, no wall functions): the boundary layers are not resolved and bluff-body drag can
be twice the real value, so treat it as indicative there."""
from __future__ import annotations

import math
import time

import numpy as np

FLUIDS = {"air": {"density": 1.204, "viscosity": 1.81e-5}, "water": {"density": 998.0, "viscosity": 1.0e-3}}

C = np.array([(0, 0, 0), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1),
              (1, 1, 0), (-1, -1, 0), (1, -1, 0), (-1, 1, 0), (1, 0, 1), (-1, 0, -1), (1, 0, -1), (-1, 0, 1),
              (0, 1, 1), (0, -1, -1), (0, 1, -1), (0, -1, 1)], int)
W = np.array([1 / 3] + [1 / 18] * 6 + [1 / 36] * 12)
OPP = np.array([0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15, 18, 17])
CS_SMAG = 0.17
MAGIC = 3 / 16  # TRT: puts the half-way bounce-back wall exactly half-way for Poiseuille flow
MARGINS = (1.0, 3.0, 1.0)  # external flow: fluid upstream, downstream and to the sides, in part sizes
SIDES = "open"  # external flow side boundaries: 'free' (the free stream) or 'open' (zero gradient)


def _feq(rho, u):
    """Equilibrium populations (19, ...) for density rho (...) and velocity u (3, ...): the incompressible model
    of He and Luo (the momentum terms do not scale with rho), so a pressure drop along a long passage does not
    change the velocities as it would in the plain, weakly compressible model."""
    usq = 1.5 * (u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
    out = np.empty((19,) + np.shape(rho), np.float32)
    for i in range(19):
        cu = 3.0 * (C[i, 0] * u[0] + C[i, 1] * u[1] + C[i, 2] * u[2])
        out[i] = W[i] * (rho + cu + 0.5 * cu * cu - usq)
    return out


F32 = np.float32
_W0, _W1, _W2 = F32(1 / 3), F32(1 / 18), F32(1 / 36)
_PX, _NX = [1, 7, 9, 11, 13], [2, 8, 10, 12, 14]  # directions with c_x = +1 / -1 (likewise y, z)
_PY, _NY = [3, 7, 10, 15, 17], [4, 8, 9, 16, 18]
_PZ, _NZ = [5, 11, 14, 15, 18], [6, 12, 13, 16, 17]
# opposite pairs (i, i + 1): their weight and c_i . u as (component, sign) terms
_PAIRS = [(1, _W1, ((0, 1),)), (3, _W1, ((1, 1),)), (5, _W1, ((2, 1),)), (7, _W2, ((0, 1), (1, 1))),
          (9, _W2, ((0, 1), (1, -1))), (11, _W2, ((0, 1), (2, 1))), (13, _W2, ((0, 1), (2, -1))),
          (15, _W2, ((1, 1), (2, 1))), (17, _W2, ((1, 1), (2, -1)))]


def _sum(f, idx):
    out = f[idx[0]].copy()
    for k in idx[1:]:
        out += f[k]
    return out


def _moments(f):
    """Density and velocity (incompressible model: rho0 = 1) of populations f (19, ...)."""
    rho = f.sum(axis=0)
    u = np.empty((3,) + rho.shape, F32)
    u[0] = _sum(f, _PX) - _sum(f, _NX)
    u[1] = _sum(f, _PY) - _sum(f, _NY)
    u[2] = _sum(f, _PZ) - _sum(f, _NZ)
    return rho, u


def _cu(u, terms):
    a, s = terms[0]
    out = u[a].copy() if s > 0 else -u[a]
    for a, s in terms[1:]:
        if s > 0:
            out += u[a]
        else:
            out -= u[a]
    return out


def _slices(s):
    return (slice(1, None), slice(None, -1)) if s > 0 else (slice(None, -1), slice(1, None)) if s < 0 else (slice(None),) * 2


class Lattice:
    """A D3Q19 lattice over a box of cells; `solid` marks wall cells (bounce-back). Streaming does not wrap
    round: the boundary planes are set by the flow's boundary conditions after each step."""

    def __init__(self, solid: np.ndarray, nu: float, smagorinsky: bool):
        self.solid = solid
        self.shape = solid.shape
        self.tau0 = 3.0 * nu + 0.5
        self.smag = smagorinsky
        self.f = _feq(np.ones(self.shape, F32), np.zeros((3,) + self.shape, F32))
        self._g = np.empty_like(self.f)  # the second buffer streaming writes into
        self._moves = []
        for i in range(19):
            d = [_slices(int(c)) for c in C[i]]
            self._moves.append((tuple(x[0] for x in d), tuple(x[1] for x in d)))
        fluid = ~solid
        self._fluid = fluid.astype(F32)  # (wall cells are zeroed after each collision: nothing grows in them)
        self.links = []  # per direction i: the fluid cells whose neighbour x + c_i is a wall
        for i in range(1, 19):
            m = fluid & np.roll(solid, shift=tuple(-C[i]), axis=(0, 1, 2))
            if m.any():
                self.links.append((i, np.nonzero(m)))
        self.force = np.zeros(3)

    def collide(self, rho, u):
        """Two-relaxation-time collision, in place: the even part of each opposite pair relaxes with the
        viscosity's rate, the odd part with the rate the magic parameter gives (exact wall position)."""
        f = self.f
        usq = F32(1.5) * (u[0] * u[0] + u[1] * u[1] + u[2] * u[2])
        base = rho - usq
        if self.smag:  # eddy viscosity from the non-equilibrium momentum flux |Pi_neq| (Hou et al.)
            p3 = rho / F32(3.0)
            xx = _sum(f, [1, 2, 7, 8, 9, 10, 11, 12, 13, 14]) - p3 - u[0] * u[0]
            yy = _sum(f, [3, 4, 7, 8, 9, 10, 15, 16, 17, 18]) - p3 - u[1] * u[1]
            zz = _sum(f, [5, 6, 11, 12, 13, 14, 15, 16, 17, 18]) - p3 - u[2] * u[2]
            xy = f[7] + f[8] - f[9] - f[10] - u[0] * u[1]
            xz = f[11] + f[12] - f[13] - f[14] - u[0] * u[2]
            yz = f[15] + f[16] - f[17] - f[18] - u[1] * u[2]
            P = xx * xx + yy * yy + zz * zz + F32(2) * (xy * xy + xz * xz + yz * yz)
            t0 = F32(self.tau0)
            tau = F32(0.5) * (t0 + np.sqrt(t0 * t0 + F32(18.0 * math.sqrt(2.0) * CS_SMAG ** 2) * np.sqrt(F32(2) * P)))
            # regularised collision: the non-equilibrium part rebuilt from its momentum flux alone, so the
            # higher (ghost) moments that make plain BGK / TRT blow up at low viscosity are filtered out
            keep = F32(1) - F32(1) / tau  # (1 - omega)
            tr = (xx + yy + zz) / F32(3.0)
            f[0] = _W0 * base + keep * _W0 * F32(4.5) * (-tr)
            for i, w, terms in _PAIRS:
                s = _cu(u, terms)
                ep = w * (base + F32(4.5) * s * s)
                em = w * F32(3.0) * s
                if len(terms) == 1:  # axis directions: c.Pi.c = Pi_aa
                    q = (xx, yy, zz)[terms[0][0]]
                else:  # diagonals: Pi_aa + Pi_bb +- 2 Pi_ab
                    (a1, s1), (a2, s2) = terms
                    off = {(0, 1): xy, (0, 2): xz, (1, 2): yz}[(a1, a2)]
                    q = (xx, yy, zz)[a1] + (xx, yy, zz)[a2] + F32(2 * s1 * s2) * off
                neq = keep * w * F32(4.5) * (q - tr)
                f[i] = ep + em + neq
                f[i + 1] = ep - em + neq
            f *= self._fluid
            return
        else:
            wp = F32(1.0 / self.tau0)
            wm = F32(1.0 / (0.5 + MAGIC / (self.tau0 - 0.5)))
        f[0] -= wp * (f[0] - _W0 * base)
        for i, w, terms in _PAIRS:
            s = _cu(u, terms)
            ep = w * (base + F32(4.5) * s * s)  # even and odd parts of the pair's equilibrium
            em = w * F32(3.0) * s
            fi, fj = f[i], f[i + 1]
            dp = wp * (F32(0.5) * (fi + fj) - ep)
            dm = wm * (F32(0.5) * (fi - fj) - em)
            fi -= dp + dm
            fj -= dp - dm
        f *= self._fluid

    def stream(self):
        post, new = self.f, self._g
        for i in range(19):
            dst, src = self._moves[i]
            new[i][dst] = post[i][src]
        F = np.zeros(3)
        for i, cells in self.links:  # bounce back off the wall half-way along the link
            v = post[i][cells]
            new[OPP[i]][cells] = v
            F += 2.0 * float(v.sum()) * C[i]  # momentum given to the wall
        self.force = F
        self.f, self._g = new, post


def _rotation_to_x(d):
    """A rotation matrix taking unit vector d to +X."""
    d = np.asarray(d, float) / np.linalg.norm(d)
    x = np.array([1.0, 0, 0])
    v = np.cross(d, x)
    s, c = np.linalg.norm(v), float(d @ x)
    if s < 1e-12:
        return np.eye(3) if c > 0 else np.diag([-1.0, -1.0, 1.0])
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]) / s
    a = math.atan2(s, c)
    return np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * K @ K


def _rotated(body, R):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf
    from build123d import Shape
    t = gp_Trsf()
    t.SetValues(R[0, 0], R[0, 1], R[0, 2], 0, R[1, 0], R[1, 1], R[1, 2], 0, R[2, 0], R[2, 1], R[2, 2], 0)
    return Shape.cast(BRepBuilderAPI_Transform(body.wrapped, t, True).Shape())


def _lattice_scales(Re: float, cells: float, u_max: float = 0.08):
    """Lattice velocity and viscosity for Reynolds number Re over `cells`: the largest stable velocity, slower
    for viscous flows (keeps tau <= 1.5, where bounce-back stays accurate)."""
    u = u_max
    nu = u * cells / Re
    if nu > 1.0 / 3.0:
        nu = 1.0 / 3.0
        u = nu * Re / cells
    return u, nu


def _run(lat: Lattice, boundary, steps: int, check, budget: float, window: int, every: int = 25,
         tol: float = 2e-3):
    """Time-step until check() has settled: its mean over the last `window` steps within tol of its mean over
    the window before (a slow drift is not taken for convergence, and an unsteady wake is averaged), or the
    steps / time budget run out. Returns (steps run, readings every `every` steps, converged)."""
    t0 = time.monotonic()
    n, history = 0, []
    k = max(4, window // every)
    while n < steps:
        rho, u = _moments(lat.f)
        lat.collide(rho, u)
        lat.stream()
        boundary(lat, rho, u)
        n += 1
        if n % every == 0:
            from . import jobs
            jobs.progress(max(n / steps, min(0.99, (time.monotonic() - t0) / max(budget, 1e-9))), f"flow step {n}")
            history.append(check(lat))
            if not math.isfinite(history[-1]):
                raise ValueError("the flow went unstable: raise cells_across or lower the speed")
            if len(history) >= 2 * k:
                a, b = np.mean(history[-k:]), np.mean(history[-2 * k:-k])
                if abs(a - b) <= tol * max(abs(a), 1e-12):
                    return n, history, True
            if time.monotonic() - t0 > budget:
                break
    return n, history, False


def _method(smag: bool) -> str:
    return "lattice Boltzmann D3Q19, " + ("regularised + Smagorinsky LES" if smag else "TRT (laminar)")


def _warning(smag: bool, Re: float) -> str | None:
    if not smag:
        return None
    return (f"Re {Re:.3g} is beyond what this grid resolves: a large-eddy estimate without resolved boundary layers "
            "(bluff-body drag can be up to twice the real value). Use it for comparisons between designs.")


def _slice(field, solid, org, h, k):
    sp = np.round(field[:, :, k].T.astype(float), 5).tolist()  # rows along Y, the part's cells None
    for j, row in enumerate(solid[:, :, k].T):
        for i in np.nonzero(row)[0]:
            sp[j][i] = None
    return {"speed": sp,
            "extent": [float(org[0]), float(org[0] + field.shape[0] * h), float(org[1]), float(org[1] + field.shape[1] * h)]}


# ====================================================================== external flow
def external_flow(body, direction, speed: float, fluid: str = "air", cells_across: int = 16,
                  max_steps: int = 20000, budget: float = 600.0) -> dict:
    """The part in a free stream of `speed` m/s along `direction`: drag and lift (coefficients on the frontal
    area, and forces), the surface pressure coefficient and a mid-plane slice of the speed. cells_across:
    lattice cells over the part's size square to the flow."""
    from .fea import voxelize
    props = FLUIDS.get(fluid)
    if props is None:
        raise ValueError(f"unknown fluid {fluid!r}: air or water")
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    R = _rotation_to_x(d)
    rb = _rotated(body, R)  # the flow runs along +X in this frame
    bb = rb.bounding_box()
    cross = max(bb.size.Y, bb.size.Z, 1e-9)
    h = cross / max(10, int(cells_across))  # (under 10 cells the wall is too coarse to give a drag)
    frac, _flags, origin, n = voxelize(rb, h, sub=2)
    part = frac > 0.5
    if not part.any():
        raise ValueError("the part is too thin for this grid: raise cells_across")
    D = cross / h
    up, down, side = (max(int(m * D), k) for m, k in zip(MARGINS, (8, 16, 8)))
    N = (n[0] + up + down, n[1] + 2 * side, n[2] + 2 * side)
    solid = np.zeros(N, bool)
    solid[up:up + n[0], side:side + n[1], side:side + n[2]] = part
    org = np.asarray(origin, float) - np.array([up, side, side]) * h
    nu_phys = props["viscosity"] / props["density"]
    Re = speed * cross * 1e-3 / nu_phys
    u_lb, nu_lb = _lattice_scales(Re, D)
    smag = 3.0 * nu_lb + 0.5 < 0.52  # under-resolved: an eddy viscosity keeps the grid stable
    lat = Lattice(solid, nu_lb, smag)
    zero = np.zeros(N, np.float32)
    lat.f = _feq(np.ones(N, np.float32), np.stack([np.full(N, u_lb, np.float32), zero, zero]))
    free = _feq(np.float32(1.0), np.array([u_lb, 0.0, 0.0], np.float32))[:, None, None]  # (19, 1, 1)

    def boundary(lat, rho, u):
        f = lat.f
        f[:, -1] = f[:, -2]  # outlet: zero gradient
        f[:, 0] = free  # inlet
        if SIDES == "free":  # sides: the free stream, or open (zero gradient)
            f[:, :, 0] = free
            f[:, :, -1] = free
            f[:, :, :, 0] = free
            f[:, :, :, -1] = free
        else:
            f[:, :, 0] = f[:, :, 1]
            f[:, :, -1] = f[:, :, -2]
            f[:, :, :, 0] = f[:, :, :, 1]
            f[:, :, :, -1] = f[:, :, :, -2]

    proj = int(part.any(axis=0).sum())  # frontal area, cells
    qa = 0.5 * u_lb ** 2 * proj
    window = int(0.5 * N[0] / u_lb)  # half the time the flow takes through the domain
    steps = int(min(max_steps, max(6 * window, 20 * N[0] / u_lb)))
    forces = []

    def check(L):
        forces.append(L.force.copy())
        return L.force[0] / qa
    nsteps, hist, done = _run(lat, boundary, steps, check, budget, window)
    rho, u = _moments(lat.f)
    F = np.mean(forces[-max(4, window // 25):], axis=0)  # lattice units, averaged over the last window
    scale = props["density"] * (speed / u_lb) ** 2 * (h * 1e-3) ** 2  # lattice force -> N
    force = R.T @ (F * scale)  # in the part's frame
    near = np.zeros(N, bool)
    for a in range(3):
        for s in (-1, 1):
            near |= np.roll(solid, s, axis=a)
    near &= ~solid
    cells = np.argwhere(near)
    cp = ((rho[near] - 1.0) / 3.0) / (0.5 * u_lb ** 2)
    sp = np.linalg.norm(u, axis=0) / u_lb * speed
    return {"method": _method(smag), "warning": _warning(smag, Re),
            "speed": speed, "fluid": fluid, "direction": d.tolist(), "reynolds": Re, "cells": int(np.prod(N)),
            "cell_size": h, "cells_across": round(D, 1), "steps": nsteps, "converged": done,
            "drag_coefficient": float(F[0] / qa), "lift_coefficients": [float(F[1] / qa), float(F[2] / qa)],
            "frontal_area_mm2": proj * h * h, "drag_N": float(F[0] * scale), "force_N": force.tolist(),
            "lift_side_force_N": float(np.linalg.norm(force - d * (force @ d))),
            "dynamic_pressure_Pa": 0.5 * props["density"] * speed ** 2,
            "min_cp": float(cp.min()), "max_cp": float(cp.max()), "max_velocity": float(sp[~solid].max()),
            "history": [float(x) for x in hist],
            "_surface": {"points": (R.T @ (org + (cells + 0.5) * h).T).T, "cp": cp},
            "_slice": _slice(sp, solid, org, h, N[2] // 2)}


# ====================================================================== internal flow
def _inside_outline(solid2d: np.ndarray) -> np.ndarray:
    """Cells of a 2D section enclosed by material (not joined to the section's border through empty cells)."""
    from scipy.ndimage import label
    lab, _ = label(~solid2d)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    return (lab > 0) & ~np.isin(lab, list(edge))


def _reachable(fluid: np.ndarray) -> np.ndarray:
    """Fluid cells joined to the inlet plane (index 0 along the first axis)."""
    from scipy.ndimage import label
    lab, _ = label(fluid)
    keep = set(np.unique(lab[0])) - {0}
    return np.isin(lab, list(keep))


def internal_flow(body, axis: str, speed: float, fluid: str = "water", cells_across: int = 16,
                  max_steps: int = 40000, budget: float = 600.0) -> dict:
    """Flow through the part's internal passage along `axis` ('X', 'Y' or 'Z', in at the low end, out at the
    high end) with a mean inlet velocity of `speed` m/s: pressure drop (Pa) between the part's end faces, flow
    rate and Reynolds number. cells_across: lattice cells over the inlet's equivalent diameter."""
    from .fea import voxelize
    props = FLUIDS.get(fluid)
    if props is None:
        raise ValueError(f"unknown fluid {fluid!r}: air or water")
    ax = "XYZ".index(axis.upper())
    perm = [ax] + [a for a in range(3) if a != ax]
    bb = body.bounding_box()
    size = [bb.size.X, bb.size.Y, bb.size.Z]
    h0 = max(size[perm[1]], size[perm[2]]) / 40.0  # a first look: how wide is the opening?
    frac, _f, _o, _n = voxelize(body, h0, sub=2)
    s0 = np.transpose(frac > 0.5, perm)
    hole0 = _inside_outline(s0[0])
    if not hole0.any():
        raise ValueError(f"no passage opens at the part's low {axis.upper()} end")
    h = 2.0 * math.sqrt(hole0.sum() / math.pi) * h0 / max(8, int(cells_across))
    frac, _f, _o, _n = voxelize(body, h, sub=2)
    solid = np.transpose(frac > 0.5, perm)
    # (the end slices can lie mostly beyond the part's end faces: keep the slices that cut it properly)
    count = solid.reshape(solid.shape[0], -1).sum(axis=1)
    keep = np.nonzero(count >= 0.5 * np.median(count[count > 0]))[0]
    solid = solid[keep[0]:keep[-1] + 1]
    a_in, a_out = _inside_outline(solid[0]), _inside_outline(solid[-1])
    if not a_in.any() or not a_out.any():
        raise ValueError(f"the passage does not run through the part along {axis.upper()}")
    pad = 4  # straight extensions of the end openings (cells) beyond the part
    full = np.concatenate([np.repeat((~a_in)[None], pad, 0), solid, np.repeat((~a_out)[None], pad, 0)], axis=0)
    full = np.pad(full, ((0, 0), (1, 1), (1, 1)), constant_values=True)
    full = ~_reachable(~full)  # pockets the flow cannot reach are wall
    if not (~full[-1]).any():
        raise ValueError(f"the passage does not run through the part along {axis.upper()}")
    N = full.shape
    inlet, outlet = ~full[0], ~full[-1]
    area_cells = int(inlet.sum())
    D = 2.0 * math.sqrt(area_cells / math.pi)  # equivalent diameter, cells
    nu_phys = props["viscosity"] / props["density"]
    Re = speed * D * h * 1e-3 / nu_phys
    u_lb, nu_lb = _lattice_scales(Re, D, u_max=0.05)
    smag = 3.0 * nu_lb + 0.5 < 0.52
    lat = Lattice(full, nu_lb, smag)
    uin = np.zeros((3, area_cells), np.float32)
    uin[0] = u_lb

    def boundary(lat, rho, u):
        f = lat.f
        r = f[:, 1][:, inlet].sum(axis=0)  # inlet: the velocity imposed, density from the cells behind it
        f[:, 0][:, inlet] = _feq(r, uin)
        f1 = f[:, -2][:, outlet]  # outlet: density 1 with the velocity (and non-equilibrium part) of the cells before
        r1 = f1.sum(axis=0)
        u1 = np.stack([(f1 * C[:, a].reshape(19, 1)).sum(axis=0) for a in range(3)])
        f[:, -1][:, outlet] = _feq(np.ones_like(r1), u1) + (f1 - _feq(r1, u1))

    a_, b_ = pad, N[0] - 1 - pad  # the part's end sections

    def dp_lb(L):
        # (an equilibrium inlet passes less than its velocity times its area - the cells along the wall lose
        # some - so the inlet velocity is trimmed until the flow through the part's entry is the one asked for)
        flux = float((L.f[:, a_][:, ~full[a_]] * C[:, 0].reshape(19, 1)).sum()) / area_cells
        if flux > 0:
            uin[0] *= np.float32(min(1.5, max(0.67, u_lb / flux)))
        r = L.f.sum(axis=0)
        return float(r[a_][~full[a_]].mean() - r[b_][~full[b_]].mean()) / 3.0
    window = int(max(0.5 * N[0] / u_lb, 0.3 * D * D / nu_lb))  # a flow-through / viscous diffusion time
    steps = int(min(max_steps, 8 * window))
    nsteps, hist, done = _run(lat, boundary, steps, dp_lb, budget, window)
    p_scale = props["density"] * (speed / u_lb) ** 2  # lattice pressure -> Pa
    rho, u = _moments(lat.f)
    sp = np.linalg.norm(u, axis=0) / u_lb * speed
    A = area_cells * (h * 1e-3) ** 2
    return {"method": _method(smag), "warning": _warning(smag, Re),
            "axis": axis.upper(), "speed": speed, "fluid": fluid, "reynolds": Re, "cells": int(np.prod(N)),
            "cell_size": h, "cells_across": round(D, 1), "steps": nsteps, "converged": done,
            "pressure_drop_Pa": dp_lb(lat) * p_scale, "length_mm": size[ax], "inlet_area_mm2": A * 1e6,
            "flow_rate_m3_s": speed * A, "flow_rate_l_min": speed * A * 60000.0,
            "max_velocity": float(sp[~full].max()), "history": [float(x) * p_scale for x in hist],
            "_slice": _slice(sp, full, np.array([0.0, 0.0, 0.0]), h, N[2] // 2)}


def surface_values(result: dict, mesh: dict) -> dict:
    """Cp on each display vertex: that of the nearest fluid cell next to the wall."""
    from scipy.spatial import cKDTree
    s = result["_surface"]
    tree = cKDTree(s["points"])
    out = {}
    for f in mesh["faces"]:
        _, k = tree.query(np.asarray(f["v"], float).reshape(-1, 3))
        out[f["id"]] = np.round(s["cp"][k], 4).tolist()
    return out
