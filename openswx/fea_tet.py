"""Structural and thermal analysis on a curvature-based tetrahedral mesh (quadratic, 10-node tetrahedra).

The voxel mesh of fea.py stair-steps curved boundaries, so peak stresses there (fillets, holes) are only
approximate. Here the part is meshed with Netgen (LGPL; the optional `netgen-mesher` package): elements are
graded to the curvature of the faces and their mid-side nodes lie on the true surfaces, which is what
stress concentrations need. Meshing runs in a child process (Netgen carries its own OpenCASCADE build, which
must not share a process with OCP's); everything else here is numpy / scipy.

Studies: linear static (with bonded multi-body assemblies, spring and bolt connectors), frequency
(consistent mass), linear buckling (geometric stiffness from the static stresses) and steady-state thermal
(conduction, convection, heat power, fixed temperatures). Several bodies are glued before meshing, so the
mesh is conforming across their shared faces and each body keeps its own material.

Elements: 10-node tetrahedra; stiffness by the 4-point Gauss rule (exact for straight-sided elements), mass
and convection by collapsed Gauss-Legendre rules exact for their degree. Stresses are recovered at the corner
nodes of each element and averaged at the nodes ("nodal" stresses). Units: mm, N, MPa, tonne, W, degC.
"""
from __future__ import annotations

import importlib.util
import math
import os
import tempfile

import numpy as np

# Netgen's node order: tetrahedron corners 0-3, then the edges (0,1) (0,2) (0,3) (1,2) (1,3) (2,3);
# 6-node triangles: corners 0-2, then the edges (1,2) (0,2) (0,1).
TET_EDGES = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
TRI_EDGES = ((1, 2), (0, 2), (0, 1))

MAX_ELEMENTS = 60000
CURVATURE_SAFETY = 4.0  # Netgen curvaturesafety: elements per radius of curvature (4: hole Kt within 1%)


def available() -> bool:
    """Whether the tetrahedral mesher is installed (pip install netgen-mesher, or openswx[mesh])."""
    return importlib.util.find_spec("netgen") is not None


# ------------------------------------------------------------------------------------------------ meshing
def _mesh_worker(step_path: str, maxh: float, out_path: str, conn, curvature: float = 4.0) -> None:
    try:
        from netgen.occ import Glue, OCCGeometry
        geo = OCCGeometry(step_path)
        solids = list(geo.shape.solids)
        if len(solids) > 1:  # bodies that touch share their faces: one conforming mesh, a domain per body
            geo = OCCGeometry(Glue(solids))
        m = geo.GenerateMesh(maxh=maxh, curvaturesafety=curvature, segmentsperedge=1.0, grading=0.3)
        m.SecondOrder()
        pts = np.array([p.p for p in m.Points()], float)
        els = list(m.Elements3D())
        tets = np.array([[p.nr - 1 for p in e.points] for e in els], np.int64)
        dom = np.array([e.index for e in els], np.int64)
        tris = np.array([[p.nr - 1 for p in e.points] for e in m.Elements2D()], np.int64)
        np.savez(out_path, points=pts, tets=tets, tris=tris, domains=dom)
        conn.send(("ok", ""))
    except BaseException as exc:  # noqa: BLE001 - whatever went wrong is the answer
        try:
            conn.send(("error", f"{type(exc).__name__}: {exc}"))
        except Exception:  # noqa: BLE001
            pass
    finally:
        conn.close()


def _run_mesher(step_path: str, maxh: float, timeout: float) -> dict:
    import multiprocessing as mp
    import time
    from multiprocessing.connection import wait
    out = step_path[:-5] + ".npz"
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_mesh_worker, args=(step_path, maxh, out, child, CURVATURE_SAFETY), daemon=True)
    proc.start()
    deadline = time.monotonic() + timeout
    result = None
    try:
        while result is None:
            left = deadline - time.monotonic()
            ready = wait([parent, proc.sentinel], max(left, 0)) if left > 0 else []
            if not ready:
                raise ValueError(f"meshing took longer than {timeout:g} s; increase element_size")
            if parent in ready or parent.poll():
                result = parent.recv()
            elif not proc.is_alive():
                raise ValueError("the mesher stopped unexpectedly; try a different element_size")
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
    if result[0] != "ok":
        raise ValueError(f"the part could not be meshed: {result[1]}")
    with np.load(out) as z:
        return {k: z[k] for k in ("points", "tets", "tris", "domains")}


def mesh_body(body, element_size: float | None = None, target_elements: int = 12000, timeout: float = 300.0) -> dict:
    """Quadratic tetrahedral mesh of a solid (or of several, glued): points (N, 3), tets (E, 10), boundary
    triangles (T, 6), domains (E,) numbering the bodies from 1, h."""
    if not available():
        raise ValueError("the tetrahedral mesher is not installed (pip install netgen-mesher)")
    from build123d import export_step
    vol = float(body.volume)
    # a regular tetrahedron of edge h has volume h^3 / (6 sqrt 2); Netgen's graded meshes run about twice as fine
    h = float(element_size) if element_size else (vol * 2.0 / (target_elements * 0.1179)) ** (1 / 3)
    with tempfile.TemporaryDirectory(prefix="openswx-mesh-") as tmp:
        step = os.path.join(tmp, "part.step")
        export_step(body, step)
        m = _run_mesher(step, h, timeout)
        if not element_size and len(m["tets"]) > 2.5 * target_elements:  # the curvature grading added a lot: coarsen once
            h *= (len(m["tets"]) / (1.5 * target_elements)) ** (1 / 3)
            m = _run_mesher(step, h, timeout)
    if len(m["tets"]) > MAX_ELEMENTS:
        raise ValueError(f"{len(m['tets'])} elements is too many; increase element_size")
    if len(m["tets"]) == 0:
        raise ValueError("the mesher produced no elements (is the body a closed solid?)")
    m["h"] = h
    return m


# ------------------------------------------------------------------------------------------------ elements
_A, _B = 0.5854101966249685, 0.1381966011250105
GAUSS = np.array([(_B, _B, _B), (_A, _B, _B), (_B, _A, _B), (_B, _B, _A)])  # (xi, eta, zeta); weights 1/24
CORNERS = np.array([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)])


def tet_rule(n: int = 4):
    """Collapsed (Duffy) Gauss-Legendre rule on the unit tetrahedron with n points per direction: exact for
    polynomials of degree 2n - 3 and more (n = 4: the consistent mass of quadratic elements, degree 4)."""
    g, w = np.polynomial.legendre.leggauss(n)
    g, w = (g + 1) / 2, w / 2
    u, v, s = np.meshgrid(g, g, g, indexing="ij")
    wu, wv, ws = np.meshgrid(w, w, w, indexing="ij")
    x = u
    y = v * (1 - u)
    z = s * (1 - u) * (1 - v)
    wt = wu * wv * ws * (1 - u) ** 2 * (1 - v)
    return np.column_stack([x.ravel(), y.ravel(), z.ravel()]), wt.ravel()


def tri_rule(n: int = 4):
    """Collapsed Gauss-Legendre rule on the unit triangle (area 1/2)."""
    g, w = np.polynomial.legendre.leggauss(n)
    g, w = (g + 1) / 2, w / 2
    u, v = np.meshgrid(g, g, indexing="ij")
    wu, wv = np.meshgrid(w, w, indexing="ij")
    return np.column_stack([u.ravel(), (v * (1 - u)).ravel()]), (wu * wv * (1 - u)).ravel()


def shape_values(p: np.ndarray) -> np.ndarray:
    """The 10 quadratic shape functions at natural points p (Q, 3): (Q, 10)."""
    p = np.atleast_2d(p)
    L = np.column_stack([1 - p.sum(1), p[:, 0], p[:, 1], p[:, 2]])
    out = np.zeros((len(p), 10))
    out[:, :4] = L * (2 * L - 1)
    for k, (i, j) in enumerate(TET_EDGES):
        out[:, 4 + k] = 4 * L[:, i] * L[:, j]
    return out


def shape_grads(p: np.ndarray) -> np.ndarray:
    """dN/d(xi, eta, zeta) of the 10 quadratic shape functions at natural points p (Q, 3): (Q, 10, 3).
    L0 = 1 - xi - eta - zeta, L1 = xi, L2 = eta, L3 = zeta; corners Li (2 Li - 1), edges 4 Li Lj."""
    p = np.atleast_2d(p)
    L = np.column_stack([1 - p.sum(1), p[:, 0], p[:, 1], p[:, 2]])  # (Q, 4)
    dL = np.array([[-1, -1, -1], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)  # dLi/dxi_a
    out = np.zeros((len(p), 10, 3))
    for i in range(4):
        out[:, i, :] = (4 * L[:, i] - 1)[:, None] * dL[i]
    for k, (i, j) in enumerate(TET_EDGES):
        out[:, 4 + k, :] = 4 * (L[:, i][:, None] * dL[j] + L[:, j][:, None] * dL[i])
    return out


def tri_shape_values(p: np.ndarray) -> np.ndarray:
    """The 6 quadratic shape functions of Netgen's triangle (corners, then edges (1,2) (0,2) (0,1))."""
    L = np.column_stack([1 - p[:, 0] - p[:, 1], p[:, 0], p[:, 1]])
    out = np.zeros((len(p), 6))
    out[:, :3] = L * (2 * L - 1)
    for k, (i, j) in enumerate(TRI_EDGES):
        out[:, 3 + k] = 4 * L[:, i] * L[:, j]
    return out


def _D(E: float, nu: float) -> np.ndarray:
    lam, mu = E * nu / ((1 + nu) * (1 - 2 * nu)), E / (2 * (1 + nu))
    D = np.zeros((6, 6))
    D[:3, :3] = lam
    D[np.arange(3), np.arange(3)] += 2 * mu
    D[3:, 3:] = np.eye(3) * mu
    return D


def _B(G: np.ndarray) -> np.ndarray:
    """Strain-displacement matrices from physical gradients G (..., 10, 3): (..., 6, 30); strains
    (exx, eyy, ezz, gxy, gyz, gzx)."""
    B = np.zeros(G.shape[:-2] + (6, 30))
    gx, gy, gz = G[..., 0], G[..., 1], G[..., 2]
    B[..., 0, 0::3], B[..., 1, 1::3], B[..., 2, 2::3] = gx, gy, gz
    B[..., 3, 0::3], B[..., 3, 1::3] = gy, gx
    B[..., 4, 1::3], B[..., 4, 2::3] = gz, gy
    B[..., 5, 0::3], B[..., 5, 2::3] = gz, gx
    return B


def _gradients(X: np.ndarray, p: np.ndarray):
    """Physical shape gradients (E, Q, 10, 3) and |det J| (E, Q) at natural points p for elements X (E, 10, 3)."""
    dN = shape_grads(p)  # (Q, 10, 3)
    J = np.einsum("qna,enb->eqab", dN, X)  # J[a, b] = d x_b / d xi_a
    det = np.linalg.det(J)
    G = np.einsum("eqab,qnb->eqna", np.linalg.inv(J), dN)
    return G, np.abs(det)


def _assemble(Ke: np.ndarray, conn: np.ndarray, n: int):
    from scipy.sparse import coo_matrix
    k = conn.shape[1]
    rows = np.repeat(conn, k, axis=1).ravel()
    cols = np.tile(conn, (1, k)).ravel()
    return coo_matrix((Ke.ravel(), (rows, cols)), shape=(n, n)).tocsc()


def _sym_lu(A):
    from scipy.sparse.linalg import splu
    return splu(((A + A.T) * 0.5).tocsc(), permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0,
                options={"SymmetricMode": True})


# ------------------------------------------------------------------------------------------------ the model
class Model:
    """A meshed body with per-element material factors and the face lookups the studies need."""

    def __init__(self, body, element_size=None, target_elements=12000, regions=None):
        m = mesh_body(body, element_size, target_elements)
        self.P, self.T, self.tris, self.h, self.dom = m["points"], m["tets"], m["tris"], m["h"], m["domains"]
        self.nn, self.ne = len(self.P), len(self.T)
        self.X = self.P[self.T]
        self.boundary = np.unique(self.tris)
        self.tol = max(1e-3 * self.h, 1e-5)
        self.scale = np.ones(self.ne)  # elastic modulus factor per element (bonded multi-material)
        self.rho_scale = np.ones(self.ne)
        if regions:
            self._regions(regions)

    def _regions(self, regions):
        """Each mesh domain (body) takes the factors of the region solid its elements lie in."""
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON
        from OCP.gp import gp_Pnt
        cen = self.X[:, :4].mean(axis=1)
        for d in np.unique(self.dom):
            e = int(np.nonzero(self.dom == d)[0][0])
            for reg in regions:
                cls = BRepClass3d_SolidClassifier(reg[0].wrapped)
                cls.Perform(gp_Pnt(*map(float, cen[e])), 1e-6)
                if cls.State() in (TopAbs_IN, TopAbs_ON):
                    self.scale[self.dom == d] = reg[1]
                    self.rho_scale[self.dom == d] = reg[2] if len(reg) > 2 else reg[1]
                    break

    def nodes_on(self, face) -> np.ndarray:
        from build123d import Vector
        fb = face.bounding_box()
        lo = np.array([fb.min.X, fb.min.Y, fb.min.Z]) - self.tol
        hi = np.array([fb.max.X, fb.max.Y, fb.max.Z]) + self.tol
        c = self.boundary[np.all((self.P[self.boundary] >= lo) & (self.P[self.boundary] <= hi), axis=1)]
        out = np.array([int(i) for i in c if face.distance_to(Vector(*self.P[i])) <= self.tol], int)
        if len(out) == 0:
            raise ValueError("no mesh nodes found on a boundary face; reduce element_size")
        return out

    def face_tris(self, face) -> np.ndarray:
        on = set(self.nodes_on(face).tolist())
        sel = [t for t in self.tris if t[0] in on and t[1] in on and t[2] in on]
        if not sel:
            raise ValueError("no mesh found on a boundary face; reduce element_size")
        return np.array(sel)

    def face_shares(self, face) -> tuple[np.ndarray, np.ndarray]:
        """(nodes, weights summing to 1): a uniform load on the face as consistent nodal shares - on a 6-node
        triangle zero at the corners and a third of the triangle's share at each mid-side node."""
        tr = self.face_tris(face)
        a = 0.5 * np.linalg.norm(np.cross(self.P[tr[:, 1]] - self.P[tr[:, 0]], self.P[tr[:, 2]] - self.P[tr[:, 0]]), axis=1)
        w = np.repeat(a / a.sum() / 3.0, 3)
        nodes = tr[:, 3:].ravel()
        u, inv = np.unique(nodes, return_inverse=True)
        return u, np.bincount(inv, weights=w)

    def stiffness(self, E: float, nu: float):
        G, det = _gradients(self.X, GAUSS)
        B = _B(G)
        w = det / 24.0 * self.scale[:, None]
        Ke = np.einsum("eq,eqki,kl,eqlj->eij", w, B, _D(E, nu), B, optimize=True)
        self.edofs = (3 * self.T[:, :, None] + np.arange(3)).reshape(-1, 30).astype(np.int64)
        self.volume = float((det / 24.0).sum())
        return _assemble(Ke, self.edofs, 3 * self.nn)

    def mass(self, rho: float):
        """Consistent mass matrix (tonne) for density rho in tonne/mm^3."""
        p, wq = tet_rule(4)
        N = shape_values(p)  # (Q, 10)
        _, det = _gradients(self.X, p)
        Me = np.einsum("eq,qi,qj->eij", det * wq[None] * rho * self.rho_scale[:, None], N, N, optimize=True)
        M3 = np.zeros((self.ne, 30, 30))
        for d in range(3):
            M3[:, d::3, d::3] = Me
        return _assemble(M3, self.edofs, 3 * self.nn)

    def fixed_dofs(self, fixtures) -> np.ndarray:
        nodes = set()
        for f in fixtures:
            nodes.update(self.nodes_on(f).tolist())
        return np.array(sorted(3 * c + d for c in nodes for d in range(3)), int)

    def stresses(self, U: np.ndarray, E: float, nu: float):
        """Nodal stresses (nn, 6) and von Mises (nn,) averaged from each element's corners."""
        Gc, _ = _gradients(self.X, CORNERS)
        sig_c = np.einsum("kl,eqlj,ej->eqk", _D(E, nu), _B(Gc), U[self.edofs], optimize=True) * self.scale[:, None, None]
        corner = self.T[:, :4].ravel()
        acc = np.zeros((self.nn, 6))
        np.add.at(acc, corner, sig_c.reshape(-1, 6))
        sig = acc / np.maximum(np.bincount(corner, minlength=self.nn), 1)[:, None]
        for k, (i, j) in enumerate(TET_EDGES):  # mid-side nodes: the mean of their two corners
            sig[self.T[:, 4 + k]] = 0.5 * (sig[self.T[:, i]] + sig[self.T[:, j]])
        sx, sy, sz, txy, tyz, tzx = sig.T
        vm = np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2) + 3 * (txy ** 2 + tyz ** 2 + tzx ** 2))
        return sig, vm

    def grid(self, **kw) -> dict:
        return {"kind": "tet", "coords": self.P, "tets": self.T, **kw}


def _connector_matrix(model: Model, connectors: list, F: np.ndarray):
    """Spring / bolt connectors between faces: each node of face a tied to its nearest node of face b (measured
    across the axis) by a share of the stiffness (kn along the axis, kt across it); bolt preloads as equal and
    opposite clamping forces."""
    from scipy.sparse import coo_matrix
    rows, cols, vals = [], [], []
    for con in connectors:
        na, nb = model.nodes_on(con["a"]), model.nodes_on(con["b"])
        ax = np.asarray(con.get("axis") if con.get("axis") is not None else [1.0, 0.0, 0.0], float)
        ax = ax / np.linalg.norm(ax)
        Pp = np.eye(3) - np.outer(ax, ax)
        cb = model.P[nb]
        kc = (con["kn"] * np.outer(ax, ax) + con["kt"] * Pp) / len(na)
        for i in na:
            dvec = cb - model.P[i]
            j = nb[int(np.argmin(np.linalg.norm(dvec @ Pp, axis=1) + 1e-6 * np.linalg.norm(dvec, axis=1)))]
            for (r_, c_, sg) in ((i, i, 1), (j, j, 1), (i, j, -1), (j, i, -1)):
                for d1 in range(3):
                    for d2 in range(3):
                        rows.append(3 * r_ + d1)
                        cols.append(3 * c_ + d2)
                        vals.append(sg * kc[d1, d2])
        if con.get("preload"):
            sgn = np.sign(np.dot(model.P[nb].mean(0) - model.P[na].mean(0), ax)) or 1.0
            F_a = ax * sgn * con["preload"] / len(na)
            for c in na:
                F[3 * c: 3 * c + 3] += F_a
            for c in nb:
                F[3 * c: 3 * c + 3] -= ax * sgn * con["preload"] / len(nb)
    n = 3 * model.nn
    return coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsc()


# ------------------------------------------------------------------------------------------------ studies
def solve_static(body, material: dict, fixtures: list, loads: list, element_size: float | None = None,
                 target_elements: int = 12000, regions: list | None = None, connectors: list | None = None,
                 _keep: bool = False) -> dict:
    """fixtures: Faces (fixed). loads: (Face, total force vector [N]). regions: [(solid, E factor[, density
    factor])] for bonded bodies of different materials. connectors: springs / bolts as in fea.solve_static
    (contact is not supported here). Same result keys as fea.solve_static, with the mesh in `_grid` (kind "tet":
    nodal von Mises `vm` and displacements `U` at `coords`)."""
    E, nu = material.get("elastic_modulus"), material.get("poisson")
    if not E or nu is None:
        raise ValueError("assign a library material (with elastic modulus and Poisson ratio) first")
    if not fixtures:
        raise ValueError("add at least one fixture (fixed face)")
    if not loads:
        raise ValueError("add at least one load")
    if any(c.get("contact") for c in connectors or []):
        raise ValueError("contact needs the voxel mesh")
    model = Model(body, element_size, target_elements, regions)
    K = model.stiffness(E, nu)
    F = np.zeros(3 * model.nn)
    for face, force in loads:
        nodes, w = model.face_shares(face)
        F.reshape(-1, 3)[nodes] += np.outer(w, np.asarray(force, float))
    if connectors:
        K = K + _connector_matrix(model, connectors, F)
    fdofs = model.fixed_dofs(fixtures)
    free = np.setdiff1d(np.arange(3 * model.nn), fdofs)
    U = np.zeros(3 * model.nn)
    U[free] = _sym_lu(K[free][:, free]).solve(F[free])
    if not np.all(np.isfinite(U)):
        raise ValueError("the model is not fully restrained (rigid-body motion): add fixtures")
    reaction = (K @ U - F)[fdofs].reshape(-1, 3).sum(axis=0)
    sig, vm = model.stresses(U, E, nu)
    disp = np.linalg.norm(U.reshape(-1, 3), axis=1)
    ys = material.get("yield_strength") or material.get("tensile_strength") or 0.0
    out = {"element_size": model.h, "elements": int(model.ne), "nodes": int(model.nn), "dofs": int(len(free)),
           "mesh": "tetrahedral (quadratic, curvature-based)", "mesh_volume": model.volume,
           "max_displacement": float(disp.max()), "max_von_mises": float(vm.max()),
           "factor_of_safety": float(ys / vm.max()) if ys and vm.max() > 0 else None,
           "yield_strength": ys, "reaction": reaction.tolist(), "applied": F.reshape(-1, 3).sum(axis=0).tolist(),
           "_grid": model.grid(vm=vm, U=U.reshape(-1, 3), sig=sig)}
    if _keep:
        out["_model"] = (model, K, free, U)
    return out


def solve_modal(body, material: dict, fixtures: list, modes: int = 5, element_size: float | None = None,
                target_elements: int = 12000, regions: list | None = None) -> dict:
    """Frequency study: K phi = w^2 M phi with the consistent mass matrix. Units: mm, N, tonne."""
    from scipy.sparse.linalg import eigsh
    E, nu, rho = material.get("elastic_modulus"), material.get("poisson"), material.get("density")
    if not E or nu is None or not rho:
        raise ValueError("assign a library material (elastic modulus, Poisson ratio, density) first")
    if not fixtures:
        raise ValueError("add at least one fixture (unrestrained modal analysis is not supported)")
    model = Model(body, element_size, target_elements, regions)
    K = model.stiffness(E, nu)
    M = model.mass(rho * 1e-12)
    fdofs = model.fixed_dofs(fixtures)
    free = np.setdiff1d(np.arange(3 * model.nn), fdofs)
    A = K[free][:, free]
    k = max(1, min(int(modes), len(free) - 2))
    vals, vecs = eigsh((A + A.T) * 0.5, k=k, M=M[free][:, free], sigma=0.0, which="LM")
    order = np.argsort(vals)
    vals, vecs = vals[order], vecs[:, order]
    shapes = []
    for c in range(k):
        U = np.zeros(3 * model.nn)
        U[free] = vecs[:, c]
        U = U.reshape(-1, 3)
        U /= np.abs(U).max() or 1.0
        shapes.append(U)
    total = float(M.sum() / 3 * 1000)  # (rigid translation: the mass, in kg)
    return {"element_size": model.h, "elements": int(model.ne), "mesh": "tetrahedral (quadratic, curvature-based)",
            "frequencies_hz": [float(f) for f in np.sqrt(np.clip(vals, 0, None)) / (2 * math.pi)],
            "mass_kg": total, "_grid": model.grid(shapes=shapes, vm=np.zeros(model.nn), U=shapes[0])}


def solve_buckling(body, material: dict, fixtures: list, loads: list, modes: int = 3,
                   element_size: float | None = None, target_elements: int = 12000) -> dict:
    """Linear buckling: (K + lambda Kg) phi = 0, Kg from the stresses of the static solution under the loads;
    lambda is the load factor (the loads times lambda buckle the part)."""
    from scipy.sparse.linalg import eigsh
    st = solve_static(body, material, fixtures, loads, element_size, target_elements, _keep=True)
    model, K, free, U = st.pop("_model")
    E, nu = material["elastic_modulus"], material["poisson"]
    G, det = _gradients(model.X, GAUSS)
    sig = np.einsum("kl,eqlj,ej->eqk", _D(E, nu), _B(G), U[model.edofs], optimize=True) * model.scale[:, None, None]
    S = np.zeros(sig.shape[:2] + (3, 3))
    S[..., 0, 0], S[..., 1, 1], S[..., 2, 2] = sig[..., 0], sig[..., 1], sig[..., 2]
    S[..., 0, 1] = S[..., 1, 0] = sig[..., 3]
    S[..., 1, 2] = S[..., 2, 1] = sig[..., 4]
    S[..., 0, 2] = S[..., 2, 0] = sig[..., 5]
    H = np.einsum("eq,eqia,eqab,eqjb->eij", det / 24.0, G, S, G, optimize=True)  # (E, 10, 10)
    Kg_e = np.zeros((model.ne, 30, 30))
    for d in range(3):
        Kg_e[:, d::3, d::3] = H
    Kg = _assemble(Kg_e, model.edofs, 3 * model.nn)
    A = K[free][:, free]
    B = Kg[free][:, free]
    k = max(1, min(int(modes), len(free) - 2))
    # K phi = -lambda Kg phi: the eigenvalues mu of Kg phi = mu K phi give lambda = -1 / mu
    mu, vecs = eigsh((B + B.T) * 0.5, k=k, M=(A + A.T) * 0.5, which="SA")
    lam = -1.0 / mu
    keep = np.argsort(np.where(lam > 0, lam, np.inf))[:k]
    shapes = []
    for c in keep:
        V = np.zeros(3 * model.nn)
        V[free] = vecs[:, c]
        V = V.reshape(-1, 3)
        V /= np.abs(V).max() or 1.0
        shapes.append(V)
    factors = [float(lam[c]) for c in keep if lam[c] > 0]
    return {"element_size": model.h, "elements": int(model.ne), "mesh": st["mesh"], "load_factors": factors,
            "max_von_mises": st["max_von_mises"], "_grid": model.grid(shapes=shapes, vm=st["_grid"]["vm"], U=shapes[0])}


def solve_thermal(body, conductivity: float, temperatures: list, heat: list, convection: list,
                  element_size: float | None = None, target_elements: int = 12000) -> dict:
    """Steady-state conduction. conductivity in W/(m K); temperatures: [(Face, T degC)]; heat: [(Face, W)] into
    the part; convection: [(Face, h W/(m^2 K), T_ambient)]. Units: mm, W, degC."""
    from scipy.sparse import coo_matrix
    if not conductivity:
        raise ValueError("assign a library material (with thermal conductivity) first")
    if not temperatures and not convection:
        raise ValueError("add a temperature or a convection boundary (otherwise the temperature is undefined)")
    model = Model(body, element_size, target_elements)
    nn = model.nn
    G, det = _gradients(model.X, GAUSS)
    Ke = np.einsum("eq,eqia,eqja->eij", det / 24.0 * conductivity / 1000.0, G, G, optimize=True)  # W/K
    K = _assemble(Ke, model.T, nn)
    rhs = np.zeros(nn)
    p, wq = tri_rule(4)
    N = tri_shape_values(p)  # (Q, 6)
    rows, cols, vals = [], [], []
    for face, hc, tamb in convection:  # Robin: h (T - Tamb) over the face, integrated exactly on each triangle
        tr = model.face_tris(face)
        a2 = np.linalg.norm(np.cross(model.P[tr[:, 1]] - model.P[tr[:, 0]], model.P[tr[:, 2]] - model.P[tr[:, 0]]), axis=1)
        hmm = hc * 1e-6  # W/(m^2 K) -> W/(mm^2 K)
        Ce = np.einsum("t,q,qi,qj->tij", a2 * hmm, wq, N, N)
        fe = np.einsum("t,q,qi->ti", a2 * hmm * tamb, wq, N)
        rows.append(np.repeat(tr, 6, axis=1).ravel())
        cols.append(np.tile(tr, (1, 6)).ravel())
        vals.append(Ce.ravel())
        np.add.at(rhs, tr.ravel(), fe.ravel())
    if rows:
        K = K + coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(nn, nn)).tocsc()
    for face, q in heat:
        nodes, w = model.face_shares(face)
        rhs[nodes] += q * w
    fixed, groups = {}, []
    for face, tv in temperatures:
        sel = model.nodes_on(face)
        groups.append(sel)
        for c in sel:
            fixed[int(c)] = tv
    T = np.zeros(nn)
    fd = np.array(sorted(fixed), int)
    T[fd] = [fixed[c] for c in fd]
    free = np.setdiff1d(np.arange(nn), fd)
    b = rhs[free] - K[free][:, fd] @ T[fd] if len(fd) else rhs[free]
    T[free] = _sym_lu(K[free][:, free]).solve(b)
    if not np.all(np.isfinite(T)):
        raise ValueError("thermal model is not constrained")
    react = K @ T - rhs
    seen, per = set(), []
    for sel in groups:
        own = [int(c) for c in sel if int(c) not in seen]
        seen.update(own)
        per.append(float(react[own].sum()))
    return {"element_size": model.h, "elements": int(model.ne), "nodes": int(nn),
            "mesh": "tetrahedral (quadratic, curvature-based)",
            "max_temperature": float(T.max()), "min_temperature": float(T.min()),
            "heat_through_fixed_faces": float(-react[fd].sum()) if len(fd) else 0.0,
            "heat_in_per_temperature_boundary": per, "_grid": model.grid(T=T)}


def map_to_mesh(g: dict, mesh: dict, key: str = "vm") -> dict:
    """Per display-mesh vertex: the stress and displacement of the nearest mesh node (the tetrahedral mesh's
    boundary nodes lie on the faces, so the nearest one is next to the vertex)."""
    from scipy.spatial import cKDTree
    tree = cKDTree(g["coords"])
    out_s, out_u = {}, {}
    for f in mesh["faces"]:
        v = np.asarray(f["v"], float).reshape(-1, 3)
        _, idx = tree.query(v)
        out_s[f["id"]] = np.round(np.asarray(g[key])[idx], 4).tolist()
        if "U" in g:
            out_u[f["id"]] = np.round(np.asarray(g["U"])[idx], 7).tolist()
    return {"stress": out_s, "displacement": out_u}
