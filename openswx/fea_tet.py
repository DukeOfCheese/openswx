"""Linear static stress analysis on a curvature-based tetrahedral mesh (quadratic, 10-node tetrahedra).

The voxel mesh of fea.py stair-steps curved boundaries, so peak stresses there (fillets, holes) are only
approximate. Here the part is meshed with Netgen (LGPL; the optional `netgen-mesher` package): elements are
graded to the curvature of the faces and their mid-side nodes lie on the true surfaces, which is what
stress concentrations need. Meshing runs in a child process (Netgen carries its own OpenCASCADE build, which
must not share a process with OCP's); everything else here is numpy / scipy.

Elements: 10-node tetrahedra, 4-point Gauss rule (exact for the stiffness of straight-sided elements).
Stresses are recovered at the corner nodes of each element and averaged at the nodes ("nodal" stresses).
Units: mm, N, MPa.
"""
from __future__ import annotations

import importlib.util
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
def _mesh_worker(step_path: str, maxh: float, out_path: str, conn, curvature: float = 2.0) -> None:
    try:
        from netgen.occ import OCCGeometry
        geo = OCCGeometry(step_path)
        m = geo.GenerateMesh(maxh=maxh, curvaturesafety=curvature, segmentsperedge=1.0, grading=0.3)
        m.SecondOrder()
        pts = np.array([p.p for p in m.Points()], float)
        tets = np.array([[p.nr - 1 for p in e.points] for e in m.Elements3D()], np.int64)
        tris = np.array([[p.nr - 1 for p in e.points] for e in m.Elements2D()], np.int64)
        np.savez(out_path, points=pts, tets=tets, tris=tris)
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
        return {k: z[k] for k in ("points", "tets", "tris")}


def mesh_body(body, element_size: float | None = None, target_elements: int = 12000, timeout: float = 300.0) -> dict:
    """Quadratic tetrahedral mesh of a solid: points (N, 3), tets (E, 10), boundary triangles (T, 6), h."""
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


# ------------------------------------------------------------------------------------------------ solver
def _nodes_on(face, P: np.ndarray, cand: np.ndarray, tol: float) -> np.ndarray:
    from build123d import Vector
    fb = face.bounding_box()
    lo = np.array([fb.min.X, fb.min.Y, fb.min.Z]) - tol
    hi = np.array([fb.max.X, fb.max.Y, fb.max.Z]) + tol
    c = cand[np.all((P[cand] >= lo) & (P[cand] <= hi), axis=1)]
    return np.array([int(i) for i in c if face.distance_to(Vector(*P[i])) <= tol], int)


def _face_loads(face, force, P, tris, on_face: set, F) -> None:
    """Distribute a total force over a face's boundary triangles by area: for a uniform traction on a
    6-node triangle the consistent loads are zero at the corners and a third at each mid-side node."""
    sel = [t for t in tris if t[0] in on_face and t[1] in on_face and t[2] in on_face]
    if not sel:
        raise ValueError("no mesh found on a loaded face; reduce element_size")
    sel = np.array(sel)
    a = 0.5 * np.linalg.norm(np.cross(P[sel[:, 1]] - P[sel[:, 0]], P[sel[:, 2]] - P[sel[:, 0]]), axis=1)
    w = a / a.sum()
    force = np.asarray(force, float)
    for tri, wt in zip(sel, w):
        for node in tri[3:]:
            F[3 * node: 3 * node + 3] += force * wt / 3.0


def solve_static(body, material: dict, fixtures: list, loads: list, element_size: float | None = None,
                 target_elements: int = 12000) -> dict:
    """fixtures: Faces (fixed). loads: (Face, total force vector [N]). Same result keys as fea.solve_static,
    with the mesh in `_grid` (kind "tet": nodal von Mises `vm` and displacements `U` at `coords`)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import splu
    E, nu = material.get("elastic_modulus"), material.get("poisson")
    if not E or nu is None:
        raise ValueError("assign a library material (with elastic modulus and Poisson ratio) first")
    if not fixtures:
        raise ValueError("add at least one fixture (fixed face)")
    if not loads:
        raise ValueError("add at least one load")
    m = mesh_body(body, element_size, target_elements)
    P, T, tris, h = m["points"], m["tets"], m["tris"], m["h"]
    nn, ne = len(P), len(T)
    X = P[T]  # (E, 10, 3)
    G, det = _gradients(X, GAUSS)  # (E, 4, 10, 3), (E, 4)
    B = _B(G)  # (E, 4, 6, 30)
    D = _D(E, nu)
    w = det / 24.0  # 4-point rule, weights 1/24 on the unit tetrahedron
    Ke = np.einsum("eq,eqki,kl,eqlj->eij", w, B, D, B, optimize=True)  # (E, 30, 30)
    edofs = (3 * T[:, :, None] + np.arange(3)).reshape(-1, 30).astype(np.int64)
    rows = np.repeat(edofs, 30, axis=1).ravel()
    cols = np.tile(edofs, (1, 30)).ravel()
    K = coo_matrix((Ke.ravel(), (rows, cols)), shape=(3 * nn, 3 * nn)).tocsc()
    del Ke, rows, cols
    tol = max(1e-3 * h, 1e-5)
    boundary = np.unique(tris)
    fixed = set()
    for f in fixtures:
        fixed.update(_nodes_on(f, P, boundary, tol).tolist())
    if not fixed:
        raise ValueError("no mesh nodes found on the fixed faces")
    F = np.zeros(3 * nn)
    for face, force in loads:
        _face_loads(face, force, P, tris, set(_nodes_on(face, P, boundary, tol).tolist()), F)
    fdofs = np.array(sorted(3 * c + d for c in fixed for d in range(3)))
    free = np.setdiff1d(np.arange(3 * nn), fdofs)
    U = np.zeros(3 * nn)
    Kff = K[free][:, free]
    lu = splu(((Kff + Kff.T) * 0.5).tocsc(), permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0,
              options={"SymmetricMode": True})
    U[free] = lu.solve(F[free])
    if not np.all(np.isfinite(U)):
        raise ValueError("the model is not fully restrained (rigid-body motion): add fixtures")
    reaction = (K @ U - F)[fdofs].reshape(-1, 3).sum(axis=0)
    # stresses at each element's corner nodes, averaged over the elements sharing the node
    Gc, _ = _gradients(X, CORNERS)  # (E, 4, 10, 3)
    sig_c = np.einsum("kl,eqlj,ej->eqk", D, _B(Gc), U[edofs], optimize=True)  # (E, 4, 6)
    corner_nodes = T[:, :4].ravel()
    acc = np.zeros((nn, 6))
    np.add.at(acc, corner_nodes, sig_c.reshape(-1, 6))
    cnt = np.bincount(corner_nodes, minlength=nn).astype(float)
    sig = acc / np.maximum(cnt, 1)[:, None]
    for k, (i, j) in enumerate(TET_EDGES):  # mid-side nodes: the mean of their two corners
        mid = T[:, 4 + k]
        sig[mid] = 0.5 * (sig[T[:, i]] + sig[T[:, j]])
    sx, sy, sz, txy, tyz, tzx = sig.T
    vm = np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2) + 3 * (txy ** 2 + tyz ** 2 + tzx ** 2))
    disp = np.linalg.norm(U.reshape(-1, 3), axis=1)
    ys = material.get("yield_strength") or material.get("tensile_strength") or 0.0
    return {"element_size": h, "elements": int(ne), "nodes": int(nn), "dofs": int(len(free)),
            "mesh": "tetrahedral (quadratic, curvature-based)", "mesh_volume": float(w.sum()),
            "max_displacement": float(disp.max()), "max_von_mises": float(vm.max()),
            "factor_of_safety": float(ys / vm.max()) if ys and vm.max() > 0 else None,
            "yield_strength": ys, "reaction": reaction.tolist(), "applied": F.reshape(-1, 3).sum(axis=0).tolist(),
            "_grid": {"kind": "tet", "coords": P, "vm": vm, "U": U.reshape(-1, 3), "sig": sig, "tets": T}}


def map_to_mesh(g: dict, mesh: dict) -> dict:
    """Per display-mesh vertex: the stress and displacement of the nearest mesh node (the tetrahedral mesh's
    boundary nodes lie on the faces, so the nearest one is next to the vertex)."""
    from scipy.spatial import cKDTree
    tree = cKDTree(g["coords"])
    out_s, out_u = {}, {}
    for f in mesh["faces"]:
        v = np.asarray(f["v"], float).reshape(-1, 3)
        _, idx = tree.query(v)
        out_s[f["id"]] = np.round(np.asarray(g["vm"])[idx], 4).tolist()
        out_u[f["id"]] = np.round(np.asarray(g["U"])[idx], 7).tolist()
    return {"stress": out_s, "displacement": out_u}
