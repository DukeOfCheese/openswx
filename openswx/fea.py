"""Linear static stress analysis (Simulation > Static) on a voxel hexahedral mesh.

The solid is voxelised on a uniform grid (columns ray-cast through the B-rep), meshed with 8-node
hexahedra enhanced with Wilson-Taylor incompatible modes (no shear locking in bending), assembled with
scipy.sparse and solved directly. On a uniform grid every element has the same 24x24 stiffness, so the
assembly is fully vectorised. Boundary faces are stair-stepped at the voxel size: results converge as
the element size shrinks (report element_size with any number you rely on).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve

_CORNERS = np.array([(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                     (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)], float)


def _D(E: float, nu: float) -> np.ndarray:
    lam = E * nu / ((1 + nu) * (1 - 2 * nu))
    mu = E / (2 * (1 + nu))
    D = np.zeros((6, 6))
    D[:3, :3] = lam
    D[np.arange(3), np.arange(3)] += 2 * mu
    D[3, 3] = D[4, 4] = D[5, 5] = mu
    return D


def _B_from_grads(dN: np.ndarray) -> np.ndarray:
    """dN: (n, 3) shape-function gradients -> strain-displacement matrix (6, 3n), Voigt xx yy zz xy yz zx."""
    n = dN.shape[0]
    B = np.zeros((6, 3 * n))
    for a in range(n):
        dx, dy, dz = dN[a]
        c = 3 * a
        B[0, c], B[1, c + 1], B[2, c + 2] = dx, dy, dz
        B[3, c], B[3, c + 1] = dy, dx
        B[4, c + 1], B[4, c + 2] = dz, dy
        B[5, c], B[5, c + 2] = dz, dx
    return B


def _grads(xi, eta, zeta, h):
    """Hex8 gradients (8, 3) and incompatible-mode gradients (3, 3) at a natural point, box element size h."""
    s = 2.0 / np.asarray(h, float)  # d(natural)/d(physical)
    dN = np.empty((8, 3))
    for a, (xa, ya, za) in enumerate(_CORNERS):
        dN[a] = [xa * (1 + eta * ya) * (1 + zeta * za) / 8 * s[0],
                 ya * (1 + xi * xa) * (1 + zeta * za) / 8 * s[1],
                 za * (1 + xi * xa) * (1 + eta * ya) / 8 * s[2]]
    dP = np.diag([-2 * xi * s[0], -2 * eta * s[1], -2 * zeta * s[2]])  # P_k = 1 - xi_k^2
    return dN, dP


def element_stiffness(h, E: float, nu: float) -> np.ndarray:
    """24x24 stiffness of a box hex with incompatible modes (statically condensed)."""
    D = _D(E, nu)
    g = 1 / math.sqrt(3)
    vol = np.prod(h) / 8  # det J
    Kcc, Kci, Kii = np.zeros((24, 24)), np.zeros((24, 9)), np.zeros((9, 9))
    for xi in (-g, g):
        for eta in (-g, g):
            for zeta in (-g, g):
                dN, dP = _grads(xi, eta, zeta, h)
                B, G = _B_from_grads(dN), _B_from_grads(dP)
                Kcc += B.T @ D @ B * vol
                Kci += B.T @ D @ G * vol
                Kii += G.T @ D @ G * vol
    return Kcc - Kci @ np.linalg.solve(Kii, Kci.T)


def voxelize(body, h: float, sub: int = 4):
    """Voxel grid over the part for the finite-cell method. Each column is ray-cast sub x sub times through
    the B-rep. Returns (inside fraction (nx, ny, nz), inside flags at sub^3 midpoints per cell
    (nx, ny, nz, sub^3) ordered x-major, grid origin, n)."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    bb = body.bounding_box()
    lo = np.array([bb.min.X, bb.min.Y, bb.min.Z]) - 1e-6
    size = np.array([bb.max.X, bb.max.Y, bb.max.Z]) + 1e-6 - lo
    n = np.maximum(1, np.ceil(size / h - 1e-3).astype(int))  # 1e-3 cell slack: exact multiples stay exact
    origin = lo + (size - n * h) / 2  # centre the grid on the part
    inter = IntCurvesFace_ShapeIntersector()
    inter.Load(body.wrapped, 1e-7)
    offs = (np.arange(sub) + 0.5) / sub
    zs = origin[2] + (np.arange(n[2])[:, None] + offs[None, :]) * h  # (nz, sub) sample heights
    flags = np.zeros((*n, sub, sub, sub), bool)
    for i in range(n[0]):
        for j in range(n[1]):
            for a_, oa in enumerate(offs):
                for b_, ob in enumerate(offs):
                    x = origin[0] + (i + oa) * h + 1.3e-7
                    y = origin[1] + (j + ob) * h + 1.7e-7
                    inter.Perform(gp_Lin(gp_Pnt(x, y, origin[2] - 1), gp_Dir(0, 0, 1)), -1e9, 1e9)
                    if not inter.IsDone() or inter.NbPnt() < 2:
                        continue
                    w = sorted(inter.WParameter(k) + origin[2] - 1 for k in range(1, inter.NbPnt() + 1))
                    inside = np.zeros_like(zs, bool)
                    for a, b in zip(w[0::2], w[1::2]):
                        inside |= (zs > a) & (zs < b)
                    flags[i, j, :, a_, b_, :] = inside
    flags = flags.reshape(*n, sub ** 3)
    return flags.mean(axis=3), flags, origin, n


def _cell_point_matrices(h, E, nu, sub):
    """Per midpoint (x-major, sub^3): B'DB (24x24), B'DG (24x9), G'DG (9x9) times the point weight."""
    D = _D(E, nu)
    w = np.prod(h) / sub ** 3
    xi = -1 + (2 * np.arange(sub) + 1) / sub
    cc, ci, ii = [], [], []
    for a in xi:
        for b in xi:
            for c in xi:
                dN, dP = _grads(a, b, c, h)
                B, G = _B_from_grads(dN), _B_from_grads(dP)
                cc.append(B.T @ D @ B * w)
                ci.append(B.T @ D @ G * w)
                ii.append(G.T @ D @ G * w)
    return np.array(cc), np.array(ci), np.array(ii)


def _build_structural_separate(body, E: float, nu: float, element_size, target_elements, regions):
    """Mesh every solid on its own grid (no shared nodes, so bodies interact only through connectors or
    contact) and merge the systems block-diagonally."""
    from scipy.sparse import block_diag
    h = float(element_size) if element_size else (body.volume / target_elements) ** (1 / 3)
    subs = [_build_structural(sol, E, nu, h, target_elements, regions) for sol in body.solids()]
    node0 = np.cumsum([0] + [m["nn"] for m in subs])
    e0 = np.cumsum([0] + [len(m["fr"]) for m in subs])
    coords = np.vstack([m["coords"] for m in subs])
    conn = np.vstack([m["conn"] + node0[k] for k, m in enumerate(subs)])
    edofs = np.vstack([m["edofs"] + 3 * node0[k] for k, m in enumerate(subs)]).astype(np.int32)

    solids = list(body.solids())

    def nodes_near(face):  # a face belongs to exactly one body: search only that body's grid
        c = face.center()
        k = min(range(len(solids)), key=lambda q: solids[q].distance_to(c))
        return [n_ + int(node0[k]) for n_ in subs[k]["nodes_near"](face)]

    parts = [{"origin": m["origin"], "n": m["n"], "elem_ijk": np.stack([m["I"], m["J"], m["K"]], 1), "used": m["used"],
              "node0": int(node0[k]), "nn": m["nn"], "e0": int(e0[k]), "ne": len(m["fr"])} for k, m in enumerate(subs)]

    def cat(key):
        return np.concatenate([m[key] for m in subs])

    return dict(h=h, hh=subs[0]["hh"], frac=None, mask=np.ones(int(e0[-1]), bool), I=cat("I"), J=cat("J"), K=cat("K"),
                used=None, conn=conn, nn=int(node0[-1]), coords=coords, edofs=edofs, fr=cat("fr"), scale=cat("scale"),
                rho_scale=cat("rho_scale"), Kg=block_diag([m["Kg"] for m in subs]).tocsr(), origin=subs[0]["origin"],
                n=subs[0]["n"], nodes_near=nodes_near, parts=parts)


def _build_structural(body, E: float, nu: float, element_size, target_elements, regions):
    """Finite-cell voxel mesh + assembled stiffness. regions: [(solid, E factor[, density factor])]."""
    h = float(element_size) if element_size else (body.volume / target_elements) ** (1 / 3)
    frac, flags, origin, n = voxelize(body, h)
    mask = frac > 0.0  # finite-cell method: cut cells are integrated over their inside part only
    if mask.sum() > 60000:
        raise ValueError(f"{int(mask.sum())} elements is too many; increase element_size")
    if mask.sum() < 8:
        raise ValueError("element size too large for this part")
    hh = np.array([h, h, h])
    Ke = element_stiffness(hh, E, nu)
    # element -> node indices on the full (nx+1)(ny+1)(nz+1) grid
    I, J, K = np.nonzero(mask)
    off = (_CORNERS + 1) / 2
    nid = lambda i, j, k: (i * (n[1] + 1) + j) * (n[2] + 1) + k  # noqa: E731
    conn = np.stack([nid(I + int(o[0]), J + int(o[1]), K + int(o[2])) for o in off], axis=1)  # (E, 8)
    used, conn = np.unique(conn, return_inverse=True)
    conn = conn.reshape(-1, 8)
    nn = len(used)
    gi = used // ((n[1] + 1) * (n[2] + 1))
    gj = (used // (n[2] + 1)) % (n[1] + 1)
    gk = used % (n[2] + 1)
    coords = origin + np.stack([gi, gj, gk], axis=1) * h
    edofs = (3 * conn[:, :, None] + np.arange(3)).reshape(-1, 24).astype(np.int32)
    rows = np.repeat(edofs, 24, axis=1).ravel()
    cols = np.tile(edofs, (1, 24)).ravel()
    fr = frac[I, J, K]
    scale = np.ones(len(I))  # per-element modulus factor (multi-material assemblies, bonded)
    rho_scale = np.ones(len(I))
    if regions:
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON
        from OCP.gp import gp_Pnt
        cen = origin + (np.stack([I, J, K], 1) + 0.5) * h
        for reg in regions:
            solid, factor = reg[0], reg[1]
            cls = BRepClass3d_SolidClassifier(solid.wrapped)
            for e, c in enumerate(cen):
                cls.Perform(gp_Pnt(*map(float, c)), 1e-6)
                if cls.State() in (TopAbs_IN, TopAbs_ON):
                    scale[e] = factor
                    rho_scale[e] = reg[2] if len(reg) > 2 else factor
    Kall = np.broadcast_to(Ke, (len(I), 24, 24)).copy()
    cut = np.nonzero(fr < 1.0)[0]
    if len(cut):
        cc, ci, ii = _cell_point_matrices(hh, E, nu, round(flags.shape[3] ** (1 / 3)))
        fl = flags[I[cut], J[cut], K[cut]].astype(float)  # (Nc, q)
        Kcc = np.einsum("eq,qij->eij", fl, cc)
        Kci = np.einsum("eq,qij->eij", fl, ci)
        Kii = np.einsum("eq,qij->eij", fl, ii)
        Kii += np.eye(9)[None] * (1e-9 * np.trace(ii.sum(0)) + 1e-30)  # tiny cut cells: keep Kii invertible
        Kall[cut] = Kcc - Kci @ np.linalg.solve(Kii, np.transpose(Kci, (0, 2, 1)))
        Kall[cut] += np.eye(24)[None] * (1e-8 * np.trace(Ke) / 24) * (fr[cut] < 0.05)[:, None, None]  # stabilise slivers
    Kall *= scale[:, None, None]
    data = Kall.reshape(-1)
    Kg = coo_matrix((data, (rows, cols)), shape=(3 * nn, 3 * nn)).tocsr()
    del rows, cols, data

    def nodes_near(face):
        from build123d import Vector
        fb = face.bounding_box()
        lo = np.array([fb.min.X, fb.min.Y, fb.min.Z]) - 0.75 * h
        hi = np.array([fb.max.X, fb.max.Y, fb.max.Z]) + 0.75 * h
        cand = np.nonzero(np.all((coords >= lo) & (coords <= hi), axis=1))[0]
        return [int(c) for c in cand if face.distance_to(Vector(*coords[c])) <= 0.75 * h]

    return dict(h=h, hh=hh, frac=frac, mask=mask, I=I, J=J, K=K, used=used, conn=conn, nn=nn, coords=coords,
                edofs=edofs, fr=fr, scale=scale, rho_scale=rho_scale, Kg=Kg, origin=origin, n=n, nodes_near=nodes_near)


def solve_static(body, material: dict, fixtures: list, loads: list, element_size: float | None = None,
                 target_elements: int = 12000, regions: list | None = None, connectors: list | None = None) -> dict:
    """fixtures: list of Faces (fixed geometry). loads: list of (Face, force vector [N]) - pressure is
    pre-converted to a force. connectors: [{"a": Face, "b": Face, "kn": N/mm, "kt": N/mm, "axis": vec|None,
    "preload": N}] - each node of face a is tied to its nearest node of face b by a share of the stiffness
    (kn along the axis, kt across it), so the connection also carries moments. Units: mm, N, MPa."""
    E, nu = material.get("elastic_modulus"), material.get("poisson")
    if not E or nu is None:
        raise ValueError("assign a library material (with elastic modulus and Poisson ratio) first")
    if not fixtures:
        raise ValueError("add at least one fixture (fixed face)")
    if not loads:
        raise ValueError("add at least one load")
    separate = any(c.get("contact") for c in connectors or []) or bool(connectors and len(body.solids()) > 1)
    m = (_build_structural_separate if separate else _build_structural)(body, E, nu, element_size, target_elements, regions)
    h, hh, frac, mask, I, J, K = m["h"], m["hh"], m["frac"], m["mask"], m["I"], m["J"], m["K"]
    used, conn, nn, coords, edofs, fr, scale, Kg = (m[k] for k in ("used", "conn", "nn", "coords", "edofs", "fr", "scale", "Kg"))
    origin, n, nodes_near = m["origin"], m["n"], m["nodes_near"]
    fixed = set()
    for f in fixtures:
        fixed.update(nodes_near(f))
    if not fixed:
        raise ValueError("no mesh nodes found on the fixed faces; reduce element_size")
    F = np.zeros(3 * nn)
    adj = np.bincount(conn.ravel(), minlength=nn).astype(float)  # tributary weight: elements sharing the node
    for face, force in loads:
        nodes = nodes_near(face)
        if not nodes:
            raise ValueError("no mesh nodes found on a loaded face; reduce element_size")
        w = adj[nodes] / adj[nodes].sum()
        for c, wc in zip(nodes, w):
            F[3 * c: 3 * c + 3] += np.asarray(force, float) * wc
    contact_pairs = []  # (i, j, normal, gap): compression-only, handled by the active-set loop below
    for con in connectors or []:  # spring / bolt connectors between faces of separate bodies
        na, nb = nodes_near(con["a"]), nodes_near(con["b"])
        if con.get("contact"):
            ax = np.asarray(con["axis"], float)
            ax /= np.linalg.norm(ax)
            Pp = np.eye(3) - np.outer(ax, ax)
            cb = coords[nb]
            for i in na:
                dvec = cb - coords[i]
                j = nb[int(np.argmin(np.linalg.norm(dvec @ Pp, axis=1) + 1e-6 * np.linalg.norm(dvec, axis=1)))]
                contact_pairs.append((i, j, ax, float(np.dot(coords[j] - coords[i], ax))))
            continue
        if not na or not nb:
            raise ValueError("no mesh nodes on a connector face; reduce element_size")
        ax = np.asarray(con.get("axis") if con.get("axis") is not None else [1.0, 0.0, 0.0], float)
        ax = ax / np.linalg.norm(ax)
        Pp = np.eye(3) - np.outer(ax, ax)
        cb = coords[nb]
        rows_c, cols_c, vals_c = [], [], []
        for i in na:  # pair with the nearest node of b measured across the axis (bolts: same radial position)
            dvec = cb - coords[i]
            j = nb[int(np.argmin(np.linalg.norm(dvec @ Pp, axis=1) + 1e-6 * np.linalg.norm(dvec, axis=1)))]
            kc = (con["kn"] * np.outer(ax, ax) + con["kt"] * Pp) / len(na)
            for (r_, c_, sg) in ((i, i, 1), (j, j, 1), (i, j, -1), (j, i, -1)):
                for d1 in range(3):
                    for d2 in range(3):
                        rows_c.append(3 * r_ + d1)
                        cols_c.append(3 * c_ + d2)
                        vals_c.append(sg * kc[d1, d2])
        Kg = Kg + coo_matrix((vals_c, (rows_c, cols_c)), shape=Kg.shape).tocsr()
        if con.get("preload"):  # bolt pretension: equal and opposite clamping forces along the axis
            wa, wb = adj[na] / adj[na].sum(), adj[nb] / adj[nb].sum()
            sgn = np.sign(np.dot(coords[nb].mean(0) - coords[na].mean(0), ax)) or 1.0
            for c, wc in zip(na, wa):
                F[3 * c: 3 * c + 3] += ax * sgn * con["preload"] * wc
            for c, wc in zip(nb, wb):
                F[3 * c: 3 * c + 3] -= ax * sgn * con["preload"] * wc
    fdofs = np.array(sorted(3 * c + d for c in fixed for d in range(3)))
    free = np.setdiff1d(np.arange(3 * nn), fdofs)
    U = np.zeros(3 * nn)
    if contact_pairs:
        return _solve_contact(m, Kg, F, free, fdofs, contact_pairs, E, nu, material, fixed)
    Kff = Kg[free][:, free]
    if len(free) <= 90000:  # direct: symmetric minimum-degree ordering is ~10x faster than the default here
        from scipy.sparse.linalg import splu
        A = Kff.tocsc()
        A = (A + A.T) * 0.5  # exact symmetry (condensed cut cells carry round-off asymmetry)
        lu = splu(A.tocsc(), permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0, options={"SymmetricMode": True})
        U[free] = lu.solve(F[free])
    else:  # large models: Jacobi-preconditioned CG keeps memory bounded
        from scipy.sparse import diags
        from scipy.sparse.linalg import cg
        sol, info = cg(Kff.tocsr(), F[free], M=diags(1 / Kff.diagonal()), rtol=1e-9, maxiter=50000)
        if info != 0:
            raise ValueError("the iterative solver did not converge; check fixtures (rigid-body motion?)")
        U[free] = sol
    if not np.all(np.isfinite(U)):
        raise ValueError("the model is not fully restrained (rigid-body motion): add fixtures")
    reaction = (Kg @ U - F)[fdofs].reshape(-1, 3).sum(axis=0)
    # element stresses at the centre (incompatible modes vanish there)
    D = _D(E, nu)
    B0 = _B_from_grads(_grads(0, 0, 0, hh)[0])
    sig = (D @ B0 @ U[edofs].T).T * scale[:, None]  # (E, 6)
    sx, sy, sz, txy, tyz, tzx = sig.T
    vm = np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2) + 3 * (txy ** 2 + tyz ** 2 + tzx ** 2))
    disp = np.linalg.norm(U.reshape(-1, 3), axis=1)
    ys = material.get("yield_strength") or material.get("tensile_strength") or 0.0
    return {"element_size": h, "elements": int(mask.sum()), "nodes": int(nn), "dofs": int(len(free)),
            "mesh_volume": float(fr.sum() * h ** 3),
            "max_displacement": float(disp.max()), "max_von_mises": float(vm.max()),
            "factor_of_safety": float(ys / vm.max()) if ys and vm.max() > 0 else None,
            "yield_strength": ys, "reaction": reaction.tolist(), "applied": F.reshape(-1, 3).sum(axis=0).tolist(),
            "_grid": {"origin": origin, "h": h, "n": n, "mask": mask, "elem_ijk": np.stack([I, J, K], 1),
                      "vm": vm, "U": U.reshape(-1, 3), "used": used, "sig": sig, "sys": m, "free": free,
                      "parts": m.get("parts")}}


def _pair_matrix(pairs, k_each, nn):
    rows, cols, vals = [], [], []
    for (i, j, ax, _g), k in zip(pairs, k_each):
        if k == 0:
            continue
        kc = k * np.outer(ax, ax)
        for r_, c_, sg in ((i, i, 1), (j, j, 1), (i, j, -1), (j, i, -1)):
            for d1 in range(3):
                for d2 in range(3):
                    rows.append(3 * r_ + d1)
                    cols.append(3 * c_ + d2)
                    vals.append(sg * kc[d1, d2])
    return coo_matrix((vals, (rows, cols)), shape=(3 * nn, 3 * nn)).tocsr()


def _solve_contact(m, Kg, F, free, fdofs, pairs, E, nu, material, fixed, max_iter: int = 30) -> dict:
    """Frictionless no-penetration contact by node pairs: penalty springs active only while closed
    (separation along the normal <= 0); weak springs keep separated bodies from drifting."""
    from scipy.sparse.linalg import splu
    nn, h = m["nn"], m["h"]
    k_pen = 10 * E * h / max(1, len(pairs) ** 0.5)
    k_soft = 1e-7 * E * h
    active = np.zeros(len(pairs), bool)
    U = np.zeros(3 * nn)
    for it in range(max_iter):
        k_each = np.where(active, k_pen, k_soft)
        Kt = Kg + _pair_matrix(pairs, k_each, nn)
        # gap offset: active pairs push with k_pen * gap so contact closes at the actual gap, not at zero
        Fc = F.copy()
        for (i, j, ax, g), act in zip(pairs, active):
            if act:
                Fc[3 * i: 3 * i + 3] += k_pen * g * ax  # spring force -k (g + (uj - ui).ax) on j: constant part
                Fc[3 * j: 3 * j + 3] -= k_pen * g * ax
        A = Kt[free][:, free].tocsc()
        A = (A + A.T) * 0.5
        lu = splu(A, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0, options={"SymmetricMode": True})
        U = np.zeros(3 * nn)
        U[free] = lu.solve(Fc[free])
        Uv = U.reshape(-1, 3)
        sep = np.array([g + np.dot(Uv[j] - Uv[i], ax) for (i, j, ax, g) in pairs])
        new = sep <= 1e-9
        if np.array_equal(new, active):
            break
        active = new
    Kt = Kg + _pair_matrix(pairs, np.where(active, k_pen, k_soft), nn)
    reaction = (Kt @ U - F)[fdofs].reshape(-1, 3).sum(axis=0)
    D = _D(E, nu)
    B0 = _B_from_grads(_grads(0, 0, 0, m["hh"])[0])
    sig = (D @ B0 @ U[m["edofs"]].T).T * m["scale"][:, None]
    sx, sy, sz, txy, tyz, tzx = sig.T
    vm = np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2) + 3 * (txy ** 2 + tyz ** 2 + tzx ** 2))
    disp = np.linalg.norm(U.reshape(-1, 3), axis=1)
    ys = material.get("yield_strength") or material.get("tensile_strength") or 0.0
    return {"element_size": h, "elements": int(m["mask"].sum()), "nodes": int(nn), "dofs": int(len(free)),
            "mesh_volume": float(m["fr"].sum() * h ** 3), "max_displacement": float(disp.max()),
            "max_von_mises": float(vm.max()), "factor_of_safety": float(ys / vm.max()) if ys and vm.max() > 0 else None,
            "yield_strength": ys, "reaction": reaction.tolist(), "contact_pairs_closed": int(active.sum()),
            "contact_iterations": it + 1,
            "_grid": {"origin": m["origin"], "h": h, "n": m["n"], "mask": m["mask"],
                      "elem_ijk": np.stack([m["I"], m["J"], m["K"]], 1), "vm": vm, "U": U.reshape(-1, 3),
                      "used": m["used"], "sig": sig, "sys": m, "free": free, "parts": m.get("parts")}}


def _geometric_basis(h):
    """Q[p] (6 x 24 x 24): geometric stiffness of a full box cell per unit stress component, Voigt order
    xx yy zz xy yz zx; K_sigma = sum_p sigma_p Q[p] for a constant element stress."""
    g = 1 / math.sqrt(3)
    w = np.prod(h) / 8
    pairs = [(0, 0), (1, 1), (2, 2), (0, 1), (1, 2), (2, 0)]
    Q = np.zeros((6, 24, 24))
    for a in (-g, g):
        for b in (-g, g):
            for c in (-g, g):
                dN, _ = _grads(a, b, c, h)  # (8, 3)
                for pi, (i, j) in enumerate(pairs):
                    blk = np.outer(dN[:, i], dN[:, j])  # (8, 8): dN_a/dx_i dN_b/dx_j
                    if i != j:
                        blk = blk + blk.T  # symmetric stress tensor: S_ij = S_ji
                    Q[pi] += np.kron(blk, np.eye(3)) * w
    return Q


def solve_buckling(body, material: dict, fixtures: list, loads: list, modes: int = 3,
                   element_size: float | None = None, target_elements: int = 12000) -> dict:
    """Linear buckling: load factors lambda (critical load = lambda x applied loads) and mode shapes from
    K phi = lambda (-K_sigma) phi, K_sigma built from the static stresses of the applied loads."""
    from scipy.sparse.linalg import eigsh
    st = solve_static(body, material, fixtures, loads, element_size, target_elements)
    g = st["_grid"]
    m, sig, free = g["sys"], g["sig"], g["free"]
    Q = _geometric_basis(m["hh"])
    Ks = np.einsum("ep,pab->eab", sig * m["fr"][:, None], Q)  # cut cells: scaled by their inside fraction
    edofs = m["edofs"]
    rows = np.repeat(edofs, 24, axis=1).ravel()
    cols = np.tile(edofs, (1, 24)).ravel()
    nn = m["nn"]
    KS = coo_matrix((Ks.reshape(-1), (rows, cols)), shape=(3 * nn, 3 * nn)).tocsr()
    A = m["Kg"][free][:, free].tocsc()
    A = (A + A.T) * 0.5
    B = -KS[free][:, free].tocsc()
    B = (B + B.T) * 0.5
    k = max(1, min(int(modes), len(free) - 2))
    vals, vecs = eigsh(B, k=k, M=A, which="LA")  # mu = 1 / lambda; A is SPD
    order = np.argsort(-vals)
    lam = [float(1 / v) for v in vals[order] if v > 0]
    if not lam:
        raise ValueError("no buckling under these loads (the structure is not in compression)")
    shapes = []
    for c in order[: len(lam)]:
        U = np.zeros(3 * nn)
        U[free] = vecs[:, c]
        U = U.reshape(-1, 3)
        shapes.append(U / (np.abs(U).max() or 1.0))
    return {"element_size": st["element_size"], "elements": st["elements"], "load_factors": lam,
            "_grid": {**{k2: v for k2, v in g.items() if k2 != "sys"}, "shapes": shapes}}


def solve_modal(body, material: dict, fixtures: list, modes: int = 5, element_size: float | None = None,
                target_elements: int = 12000, regions: list | None = None) -> dict:
    """Frequency study: natural frequencies (Hz) and mode shapes, K phi = w^2 M phi with a lumped mass matrix
    (each cell's inside mass shared by its 8 nodes). Units: mm, N, tonne (density kg/m^3 * 1e-12)."""
    from scipy.sparse import diags
    from scipy.sparse.linalg import eigsh
    E, nu, rho = material.get("elastic_modulus"), material.get("poisson"), material.get("density")
    if not E or nu is None or not rho:
        raise ValueError("assign a library material (elastic modulus, Poisson ratio, density) first")
    if not fixtures:
        raise ValueError("add at least one fixture (unrestrained modal analysis is not supported)")
    m = _build_structural(body, E, nu, element_size, target_elements, regions)
    h, nn, conn, fr, Kg = m["h"], m["nn"], m["conn"], m["fr"], m["Kg"]
    me = rho * 1e-12 * fr * m["rho_scale"] * h ** 3 / 8  # tonne per node per element
    mass = np.bincount(conn.ravel(), weights=np.repeat(me, 8), minlength=nn)
    Mdiag = np.repeat(mass, 3)
    fixed = set()
    for f in fixtures:
        fixed.update(m["nodes_near"](f))
    if not fixed:
        raise ValueError("no mesh nodes found on the fixed faces; reduce element_size")
    fdofs = np.array(sorted(3 * c + d for c in fixed for d in range(3)))
    free = np.setdiff1d(np.arange(3 * nn), fdofs)
    A = Kg[free][:, free].tocsc()
    A = (A + A.T) * 0.5
    k = max(1, min(int(modes), len(free) - 2))
    vals, vecs = eigsh(A, k=k, M=diags(Mdiag[free]).tocsc(), sigma=0.0, which="LM")
    order = np.argsort(vals)
    vals, vecs = vals[order], vecs[:, order]
    freqs = np.sqrt(np.clip(vals, 0, None)) / (2 * math.pi)
    shapes = []
    for c in range(k):
        U = np.zeros(3 * nn)
        U[free] = vecs[:, c]
        U = U.reshape(-1, 3)
        U /= np.abs(U).max() or 1.0
        shapes.append(U)
    return {"element_size": h, "elements": int(m["mask"].sum()), "frequencies_hz": [float(f) for f in freqs],
            "mass_kg": float(mass.sum() * 1000),
            "_grid": {"origin": m["origin"], "h": h, "n": m["n"], "mask": m["mask"],
                      "elem_ijk": np.stack([m["I"], m["J"], m["K"]], 1), "used": m["used"], "shapes": shapes,
                      "vm": np.zeros(len(m["I"]))}}


def map_to_mesh(result: dict, mesh: dict) -> dict:
    """Per tessellation vertex: von Mises stress of the containing (or nearest active) element and the
    trilinearly interpolated displacement vector (vectorised; boundary vertices that fall in an empty voxel
    of the stair-stepped mesh use the nearest active neighbour). Separately meshed bodies (contact) map each
    vertex to the body grid it lies in."""
    g = result["_grid"]
    if not g.get("parts"):
        st, du, _ = _map_grid(g, mesh)
        return {"stress": st, "displacement": du}
    best = None
    for part in g["parts"]:
        sub = {"origin": part["origin"], "h": g["h"], "n": part["n"], "elem_ijk": part["elem_ijk"], "used": part["used"],
               "U": g["U"][part["node0"]: part["node0"] + part["nn"]], "vm": g["vm"][part["e0"]: part["e0"] + part["ne"]]}
        st, du, dist = _map_grid(sub, mesh)
        if best is None:
            best = (st, du, dist)
            continue
        for fid in st:
            for q in range(len(st[fid])):
                if dist[fid][q] < best[2][fid][q]:
                    best[0][fid][q], best[1][fid][q], best[2][fid][q] = st[fid][q], du[fid][q], dist[fid][q]
    return {"stress": best[0], "displacement": best[1]}


def _map_grid(g: dict, mesh: dict):
    origin, h, n = g["origin"], g["h"], g["n"]
    eidx = -np.ones(n, int)
    eidx[tuple(g["elem_ijk"].T)] = np.arange(len(g["elem_ijk"]))
    used = g["used"]  # sorted (np.unique)
    U, vm = g["U"], g["vm"]
    corners = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1)])
    shifts = np.array([(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)])
    out_s, out_u, out_d = {}, {}, {}
    for f in mesh["faces"]:
        v = np.asarray(f["v"], float).reshape(-1, 3)
        rel = (v - origin) / h
        ijk = np.clip(np.floor(rel).astype(int), 0, n - 1)
        e = eidx[ijk[:, 0], ijk[:, 1], ijk[:, 2]]
        for q in np.nonzero(e < 0)[0]:  # few boundary vertices: search the 3x3x3 neighbourhood
            cand = np.clip(ijk[q] + shifts, 0, n - 1)
            ce = eidx[cand[:, 0], cand[:, 1], cand[:, 2]]
            ok = ce >= 0
            if ok.any():
                d = ((cand[ok] + 0.5 - rel[q]) ** 2).sum(axis=1)
                k = int(np.argmin(d))
                ijk[q], e[q] = cand[ok][k], ce[ok][k]
        good = e >= 0
        t = np.clip(rel - ijk, 0, 1)
        disp = np.zeros((len(v), 3))
        for oa, ob, oc in corners:
            w = np.where(oa, t[:, 0], 1 - t[:, 0]) * np.where(ob, t[:, 1], 1 - t[:, 1]) * np.where(oc, t[:, 2], 1 - t[:, 2])
            node = ((ijk[:, 0] + oa) * (n[1] + 1) + ijk[:, 1] + ob) * (n[2] + 1) + ijk[:, 2] + oc
            pos = np.clip(np.searchsorted(used, node), 0, len(used) - 1)
            disp += (w * good)[:, None] * U[pos]
        out_s[f["id"]] = np.round(np.where(good, vm[np.maximum(e, 0)], 0.0), 4).tolist()
        out_u[f["id"]] = np.round(disp, 7).tolist()
        out_d[f["id"]] = np.where(good, np.linalg.norm(rel - (ijk + 0.5), axis=1), 1e9).tolist()
    return out_s, out_u, out_d


def refine_mesh(mesh: dict, h: float, max_tris: int = 400000) -> dict:
    """Split display triangles (midpoint 1->4) until no edge is longer than h, so result colours follow the
    analysis mesh instead of being smeared across large planar triangles."""
    faces = []
    total = 0
    for f in mesh["faces"]:
        v = [tuple(f["v"][i:i + 3]) for i in range(0, len(f["v"]), 3)]
        tris = [tuple(f["t"][i:i + 3]) for i in range(0, len(f["t"]), 3)]
        index = {p: i for i, p in enumerate(v)}

        def mid(a, b):
            p = tuple((x + y) / 2 for x, y in zip(v[a], v[b]))
            if p not in index:
                index[p] = len(v)
                v.append(p)
            return index[p]

        for _ in range(12):
            longest = max((max(math.dist(v[a], v[b]), math.dist(v[b], v[c]), math.dist(v[c], v[a])) for a, b, c in tris), default=0)
            if longest <= h or total + len(tris) * 4 > max_tris:
                break
            nxt = []
            for a, b, c in tris:
                if max(math.dist(v[a], v[b]), math.dist(v[b], v[c]), math.dist(v[c], v[a])) <= h:
                    nxt.append((a, b, c))
                    continue
                ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
                nxt += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
            tris = nxt
        total += len(tris)
        faces.append({"id": f["id"], "v": [c for p in v for c in p], "t": [i for t in tris for i in t]})
    return {"faces": faces}


# ---------------------------------------------------------------------- steady-state thermal
def _thermal_point_matrices(h, sub):
    """Per midpoint (x-major, sub^3): grad(N)' grad(N) (8x8) times the point weight; and exact Gauss Ke."""
    w = np.prod(h) / sub ** 3
    xi = -1 + (2 * np.arange(sub) + 1) / sub
    pts = []
    for a in xi:
        for b in xi:
            for c in xi:
                dN, _ = _grads(a, b, c, h)
                pts.append(dN @ dN.T * w)
    g = 1 / math.sqrt(3)
    Ke = np.zeros((8, 8))
    for a in (-g, g):
        for b in (-g, g):
            for c in (-g, g):
                dN, _ = _grads(a, b, c, h)
                Ke += dN @ dN.T * np.prod(h) / 8
    return np.array(pts), Ke


def _thermal_system(body, conductivity, temperatures, heat, convection, element_size, target_elements):
    """Conduction matrix (W/K), load vector (W), convection diagonal and node data on the voxel mesh."""
    if not conductivity:
        raise ValueError("assign a library material (with thermal conductivity) first")
    k = conductivity / 1000.0  # W/(mm K)
    h = float(element_size) if element_size else (body.volume / target_elements) ** (1 / 3)
    frac, flags, origin, n = voxelize(body, h)
    mask = frac > 0.0
    if mask.sum() > 80000:
        raise ValueError(f"{int(mask.sum())} elements is too many; increase element_size")
    hh = np.array([h, h, h])
    pts, Ke = _thermal_point_matrices(hh, round(flags.shape[3] ** (1 / 3)))
    I, J, K = np.nonzero(mask)
    off = (_CORNERS + 1) / 2
    nid = lambda i, j, kk: (i * (n[1] + 1) + j) * (n[2] + 1) + kk  # noqa: E731
    conn = np.stack([nid(I + int(o[0]), J + int(o[1]), K + int(o[2])) for o in off], axis=1)
    used, conn = np.unique(conn, return_inverse=True)
    conn = conn.reshape(-1, 8)
    nn = len(used)
    gi, gj, gk = used // ((n[1] + 1) * (n[2] + 1)), (used // (n[2] + 1)) % (n[1] + 1), used % (n[2] + 1)
    coords = origin + np.stack([gi, gj, gk], axis=1) * h
    fr = frac[I, J, K]
    Kall = np.broadcast_to(Ke, (len(I), 8, 8)).copy()
    cut = np.nonzero(fr < 1.0)[0]
    if len(cut):
        Kall[cut] = np.einsum("eq,qij->eij", flags[I[cut], J[cut], K[cut]].astype(float), pts)
    Kall *= k
    rows = np.repeat(conn, 8, axis=1).ravel()
    cols = np.tile(conn, (1, 8)).ravel()
    Kg = coo_matrix((Kall.reshape(-1), (rows, cols)), shape=(nn, nn)).tocsr()
    rhs = np.zeros(nn)

    def nodes_near(face):
        from build123d import Vector
        fb = face.bounding_box()
        lo = np.array([fb.min.X, fb.min.Y, fb.min.Z]) - 0.75 * h
        hi = np.array([fb.max.X, fb.max.Y, fb.max.Z]) + 0.75 * h
        cand = np.nonzero(np.all((coords >= lo) & (coords <= hi), axis=1))[0]
        sel = [int(c) for c in cand if face.distance_to(Vector(*coords[c])) <= 0.75 * h]
        if not sel:
            raise ValueError("no mesh nodes on a boundary face; reduce element_size")
        return sel

    from scipy.sparse import diags
    adj = np.bincount(conn.ravel(), minlength=nn).astype(float)  # tributary weight: elements sharing the node
    conv_diag = np.zeros(nn)
    for face, hc, tamb in convection:  # Robin: h A (T - Tamb), area shared by tributary weight
        sel = nodes_near(face)
        ha = hc * 1e-6 * face.area * adj[sel] / adj[sel].sum()  # W/(m^2 K) -> W/(mm^2 K)
        conv_diag[sel] += ha
        rhs[sel] += ha * tamb
    for face, q in heat:
        sel = nodes_near(face)
        rhs[sel] += q * adj[sel] / adj[sel].sum()
    fixed, groups = {}, []
    for face, tv in temperatures:
        sel = nodes_near(face)
        groups.append(sel)
        for c in sel:
            fixed[c] = tv
    # lumped volume per node (mm^3): each element's material volume shared equally by its 8 corners
    vnode = np.bincount(conn.ravel(), weights=np.repeat(fr * h ** 3 / 8.0, 8), minlength=nn)
    return {"K": Kg + diags(conv_diag), "rhs": rhs, "fixed": fixed, "groups": groups, "vnode": vnode, "h": h, "n": n, "nn": nn,
            "origin": origin, "IJK": np.stack([I, J, K], 1), "used": used, "elements": int(mask.sum())}


def _sym_lu(A):
    from scipy.sparse.linalg import splu
    return splu(((A + A.T) * 0.5).tocsc(), permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0,
                options={"SymmetricMode": True})


def solve_thermal(body, conductivity: float, temperatures: list, heat: list, convection: list,
                  element_size: float | None = None, target_elements: int = 12000) -> dict:
    """Steady-state conduction. conductivity in W/(m K); temperatures: [(Face, T degC)]; heat: [(Face, W)] into
    the part; convection: [(Face, h W/(m^2 K), T_ambient)]. Units: mm, W, degC."""
    if not temperatures and not convection:
        raise ValueError("add a temperature or a convection boundary (otherwise the temperature is undefined)")
    sy = _thermal_system(body, conductivity, temperatures, heat, convection, element_size, target_elements)
    Kg, rhs, fixed, nn = sy["K"], sy["rhs"], sy["fixed"], sy["nn"]
    T = np.zeros(nn)
    fd = np.array(sorted(fixed), int)
    T[fd] = [fixed[c] for c in fd]
    free = np.setdiff1d(np.arange(nn), fd)
    A = Kg[free][:, free].tocsc()
    b = rhs[free] - Kg[free][:, fd] @ T[fd] if len(fd) else rhs[free]
    T[free] = _sym_lu(A).solve(b)
    if not np.all(np.isfinite(T)):
        raise ValueError("thermal model is not constrained")
    react = Kg @ T - rhs  # at a fixed node: the heat (W) the boundary must supply to hold its temperature
    flow = react[fd].sum() if len(fd) else 0.0
    # heat into the part through each temperature boundary (positive in, negative out); a node shared by two
    # boundaries is counted with the one listed first
    seen, per = set(), []
    for sel in sy["groups"]:
        own = [c for c in sel if c not in seen]
        seen.update(own)
        per.append(float(react[own].sum()))
    return {"element_size": sy["h"], "elements": sy["elements"], "nodes": int(nn), "max_temperature": float(T.max()),
            "min_temperature": float(T.min()), "heat_through_fixed_faces": float(-flow),
            "heat_in_per_temperature_boundary": per,
            "_grid": {"origin": sy["origin"], "h": sy["h"], "n": sy["n"], "elem_ijk": sy["IJK"], "T": T,
                      "used": sy["used"]}}


def solve_thermal_transient(body, conductivity: float, density: float, specific_heat: float, temperatures: list,
                            heat: list, convection: list, initial: float, duration: float, steps: int = 50,
                            element_size: float | None = None, target_elements: int = 12000) -> dict:
    """Transient conduction from a uniform initial temperature (degC) over `duration` seconds in `steps`
    implicit steps (BDF2 after one backward-Euler step: second order, unconditionally stable), lumped heat capacity rho*c per node. density kg/m^3,
    specific_heat J/(kg K); loads as in solve_thermal. Returns the history of min/max/mean temperature
    and the final field."""
    if not specific_heat or not density:
        raise ValueError("the material needs a density and a specific heat for a transient study")
    if duration <= 0 or steps < 1:
        raise ValueError("duration must be positive and steps at least 1")
    sy = _thermal_system(body, conductivity, temperatures, heat, convection, element_size, target_elements)
    Kg, rhs, fixed, nn = sy["K"], sy["rhs"], sy["fixed"], sy["nn"]
    from scipy.sparse import diags
    C = density * 1e-9 * specific_heat * sy["vnode"]  # J/K per node
    dt = duration / steps
    T = np.full(nn, float(initial))
    fd = np.array(sorted(fixed), int)
    T[fd] = [fixed[c] for c in fd]
    free = np.setdiff1d(np.arange(nn), fd)
    # first step backward Euler, then BDF2 (second order, also unconditionally stable)
    lu1 = _sym_lu((diags(C / dt) + Kg)[free][:, free].tocsc())
    lu2 = _sym_lu((diags(1.5 * C / dt) + Kg)[free][:, free].tocsc()) if steps > 1 else None
    Kfd = Kg[free][:, fd] if len(fd) else None
    w = sy["vnode"] / sy["vnode"].sum()
    hist = [{"time": 0.0, "max": float(T.max()), "min": float(T.min()), "mean": float(w @ T)}]
    Tprev = None
    for i in range(1, steps + 1):
        cf = C[free] / dt
        b = (cf * T[free] if Tprev is None else cf * (2.0 * T[free] - 0.5 * Tprev)) + rhs[free]
        if Kfd is not None:
            b -= Kfd @ T[fd]
        Tprev = T[free].copy()
        T[free] = (lu1 if i == 1 else lu2).solve(b)
        hist.append({"time": i * dt, "max": float(T.max()), "min": float(T.min()), "mean": float(w @ T)})
    if not np.all(np.isfinite(T)):
        raise ValueError("transient thermal solution diverged")
    return {"element_size": sy["h"], "elements": sy["elements"], "nodes": int(nn), "time_step": dt,
            "max_temperature": float(T.max()), "min_temperature": float(T.min()), "history": hist,
            "_grid": {"origin": sy["origin"], "h": sy["h"], "n": sy["n"], "elem_ijk": sy["IJK"], "T": T,
                      "used": sy["used"]}}


def map_temperature(result: dict, mesh: dict) -> dict:
    """Trilinearly interpolated temperature at every display-mesh vertex."""
    g = result["_grid"]
    origin, h, n, used, T = g["origin"], g["h"], g["n"], g["used"], g["T"]
    eidx = -np.ones(n, int)
    eidx[tuple(g["elem_ijk"].T)] = np.arange(len(g["elem_ijk"]))
    shifts = np.array([(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)])
    corners = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1)])
    out = {}
    for f in mesh["faces"]:
        v = np.asarray(f["v"], float).reshape(-1, 3)
        rel = (v - origin) / h
        ijk = np.clip(np.floor(rel).astype(int), 0, n - 1)
        e = eidx[ijk[:, 0], ijk[:, 1], ijk[:, 2]]
        for q in np.nonzero(e < 0)[0]:
            cand = np.clip(ijk[q] + shifts, 0, n - 1)
            ce = eidx[cand[:, 0], cand[:, 1], cand[:, 2]]
            ok = ce >= 0
            if ok.any():
                kq = int(np.argmin(((cand[ok] + 0.5 - rel[q]) ** 2).sum(axis=1)))
                ijk[q], e[q] = cand[ok][kq], ce[ok][kq]
        t = np.clip(rel - ijk, 0, 1)
        val = np.zeros(len(v))
        for oa, ob, oc in corners:
            w = np.where(oa, t[:, 0], 1 - t[:, 0]) * np.where(ob, t[:, 1], 1 - t[:, 1]) * np.where(oc, t[:, 2], 1 - t[:, 2])
            node = ((ijk[:, 0] + oa) * (n[1] + 1) + ijk[:, 1] + ob) * (n[2] + 1) + ijk[:, 2] + oc
            val += w * T[np.clip(np.searchsorted(used, node), 0, len(used) - 1)]
        out[f["id"]] = np.round(np.where(e >= 0, val, np.nan if False else val), 4).tolist()
    return {"temperature": out}
