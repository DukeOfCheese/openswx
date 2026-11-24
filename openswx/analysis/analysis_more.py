from __future__ import annotations

import math

import numpy as np

FLUIDS = {"air": {"density": 1.204, "viscosity": 1.81e-5}, "water": {"density": 998.0, "viscosity": 1.0e-3}}


def flow_study(body, direction, speed: float, fluid: str = "air", cells_across: int = 20) -> dict:
    """Steady potential (inviscid, incompressible) flow past a part in a free stream of `speed` m/s along
    `direction`: velocity potential on a voxel grid around the part (zero flux into the part, the free stream
    at the domain's boundary), the surface pressure coefficient Cp = 1 - (v / U)^2, the net pressure force
    (lift / side force; ideal flow gives no pressure drag) and a flat-plate skin-friction drag estimate on the
    wetted area. An estimate for a first look, not Navier-Stokes CFD."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import cg
    from openswx.analysis.fea import voxelize
    props = FLUIDS.get(fluid)
    if props is None:
        raise ValueError(f"unknown fluid {fluid!r}: air or water")
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    bb = body.bounding_box()
    L = max(bb.size.X, bb.size.Y, bb.size.Z)
    h = L / max(8, int(cells_across))
    frac, _flags, origin, n = voxelize(body, h, sub=2)
    pad = int(math.ceil(L / h))  # one body length of fluid all round
    N = np.array(n) + 2 * pad
    solid = np.zeros(N, bool)
    solid[pad:pad + n[0], pad:pad + n[1], pad:pad + n[2]] = frac > 0.5
    org = np.asarray(origin, float) - pad * h
    idx = -np.ones(N, int)
    fluid_cells = np.argwhere(~solid)
    idx[tuple(fluid_cells.T)] = np.arange(len(fluid_cells))
    centre = lambda ijk: org + (np.asarray(ijk) + 0.5) * h  # noqa: E731
    free = lambda ijk: centre(ijk) @ d * speed  # noqa: E731  (free-stream potential, mm * m/s)
    me = np.arange(len(fluid_cells))
    rows, cols, vals = [], [], []
    rhs = np.zeros(len(fluid_cells))
    for ax in range(3):
        for sgn in (-1, 1):
            nb = fluid_cells.copy()
            nb[:, ax] += sgn
            inside = (nb[:, ax] >= 0) & (nb[:, ax] < N[ax])
            ok = inside.copy()
            ok[inside] = ~solid[tuple(nb[inside].T)]  # fluid neighbour: coupling; solid: no flux (no term)
            k = int(ok.sum())
            rows += [me[ok], me[ok]]
            cols += [me[ok], idx[tuple(nb[ok].T)]]
            vals += [np.ones(k), -np.ones(k)]
            out = ~inside  # beyond the domain: the free stream (Dirichlet ghost)
            rows.append(me[out])
            cols.append(me[out])
            vals.append(np.ones(int(out.sum())))
            rhs[out] += free(nb[out])
    A = coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                   shape=(len(fluid_cells),) * 2).tocsr()
    phi, info = cg(A, rhs, x0=free(fluid_cells), rtol=1e-8, maxiter=5000)
    full = np.zeros(N)
    full[tuple(fluid_cells.T)] = phi
    vel = np.zeros((len(fluid_cells), 3))
    near = np.zeros(len(fluid_cells), bool)  # fluid cells touching the part
    for ax in range(3):
        side = []
        for sgn in (-1, 1):
            nb = fluid_cells.copy()
            nb[:, ax] += sgn
            ins = (nb[:, ax] >= 0) & (nb[:, ax] < N[ax])
            v = np.empty(len(nb))
            v[~ins] = free(nb[~ins])
            sol = np.zeros(len(nb), bool)
            sol[ins] = solid[tuple(nb[ins].T)]
            ok = ins & ~sol
            v[ok] = full[tuple(nb[ok].T)]
            v[sol] = phi[sol]  # a wall mirrors the cell: no flow through it
            near |= sol
            side.append(v)
        vel[:, ax] = (side[1] - side[0]) / (2 * h)
    speed_cells = np.linalg.norm(vel, axis=1)
    q = 0.5 * props["density"] * speed ** 2  # dynamic pressure, Pa
    cp = 1 - (speed_cells / speed) ** 2
    force = np.zeros(3)  # pressure on the part's faces, from each wall-touching fluid cell
    for ax in range(3):
        for sgn in (-1, 1):
            nb = fluid_cells.copy()
            nb[:, ax] += sgn
            ins = (nb[:, ax] >= 0) & (nb[:, ax] < N[ax])
            hit = np.zeros(len(fluid_cells), bool)
            hit[ins] = solid[tuple(nb[ins].T)]
            force[ax] += sgn * float((cp[hit] * q).sum()) * (h * 1e-3) ** 2
    nu = props["viscosity"] / props["density"]
    Re = speed * (L * 1e-3) / nu
    cf = 0.074 * Re ** -0.2 if Re > 5e5 else 1.328 / math.sqrt(max(Re, 1.0))
    drag_f = cf * q * body.area * 1e-6
    lift = force - d * (force @ d)
    cpn = cp[near] if near.any() else np.zeros(1)
    return {"speed": speed, "fluid": fluid, "direction": d.tolist(), "reynolds": Re, "cells": int(len(fluid_cells)),
            "cell_size": h, "dynamic_pressure_Pa": q,
            "max_velocity": float(speed_cells[near].max()) if near.any() else speed,
            "min_cp": float(cpn.min()), "max_cp": float(cpn.max()), "pressure_force_N": force.tolist(),
            "lift_side_force_N": float(np.linalg.norm(lift)), "skin_friction_drag_N": drag_f,
            "skin_friction_coefficient": cf, "converged": info == 0,
            "_surface": {"origin": org, "h": h, "cells": fluid_cells[near], "cp": cp[near]}}


def flow_surface_values(result: dict, mesh: dict) -> dict:
    """Cp on each display vertex: that of the nearest fluid cell next to the part."""
    from scipy.spatial import cKDTree
    s = result["_surface"]
    tree = cKDTree(s["origin"] + (s["cells"] + 0.5) * s["h"])
    out = {}
    for f in mesh["faces"]:
        _, k = tree.query(np.asarray(f["v"], float).reshape(-1, 3))
        out[f["id"]] = np.round(s["cp"][k], 4).tolist()
    return out


PRICE_PER_KG = {"Steel": 1.2, "Stainless Steel": 4.5, "Iron": 0.9, "Aluminium Alloys": 3.5, "Copper Alloys": 8.5,
                "Titanium Alloys": 35.0, "Plastics": 3.0, "Rubber": 4.0, "Woods": 1.5, "Other": 2.0}  # generic, USD/kg
REMOVAL_CM3_MIN = {"Steel": 12.0, "Stainless Steel": 8.0, "Iron": 15.0, "Aluminium Alloys": 50.0, "Copper Alloys": 25.0,
                   "Titanium Alloys": 5.0, "Plastics": 60.0, "Rubber": 30.0, "Woods": 80.0, "Other": 20.0}
BEND_FEATURES = ("edge_flange", "hem", "sketched_bend", "miter_flange", "jog")


def _material(doc):
    mat = doc.material or {}
    return mat, mat.get("category") or "Other", float(mat.get("density") or 1000.0)


def cost_estimate(doc, method: str = "machining", quantity: int = 1, rate_per_hour: float = 80.0,
                  setup_hours: float = 1.0, price_per_kg: float | None = None) -> dict:
    """Should-cost estimate for one part (USD, generic rates; override the material price and shop rate):
    machining (stock = bounding box + 2 mm a side, removal rate by material, holes and faces add time),
    sheet_metal (blank from the volume and thickness, cutting length, bends), additive (volume and print time)
    or casting (metal and pattern / tooling shared over the quantity)."""
    from openswx.core.features import mass_properties
    body = doc.body
    if body is None:
        raise ValueError("the part has no body")
    mat, cat, density = _material(doc)
    price = float(price_per_kg if price_per_kg is not None else PRICE_PER_KG.get(cat, 2.0))
    mp = mass_properties(body, density)
    vol_cm3, mass_kg = mp["volume"] / 1000, mp["mass"] / 1000
    rate_min = rate_per_hour / 60
    batch = max(1, int(quantity))
    if method == "machining":
        bb = body.bounding_box()
        stock_cm3 = (bb.size.X + 4) * (bb.size.Y + 4) * (bb.size.Z + 4) / 1000
        removed = max(0.0, stock_cm3 - vol_cm3)
        holes = sum(1 for f in body.faces() if f.geom_type.name.lower() == "cylinder")
        minutes = removed / REMOVAL_CM3_MIN.get(cat, 20.0) + 0.5 * holes + 0.1 * len(body.faces())
        lines = [("Material (stock)", stock_cm3 * density / 1e6 * price), ("Machining", minutes * rate_min),
                 ("Setup (shared over the batch)", setup_hours * rate_per_hour / batch)]
        detail = {"stock_cm3": stock_cm3, "removed_cm3": removed, "machining_minutes": minutes, "holes": holes}
    elif method == "sheet_metal":
        sheet = getattr(doc.ctx, "sheet", None) if getattr(doc, "ctx", None) else None
        if not sheet:
            raise ValueError("sheet metal costing needs a sheet metal part (a Base Flange)")
        t = float(sheet["thickness"])
        area = mp["volume"] / t
        cut_m = (4 * math.sqrt(area) + sum(e.length for e in body.edges()) / 6) / 1000  # outline + openings, roughly
        bends = sum(1 for f in doc.features if f["type"] in BEND_FEATURES and not f.get("suppressed"))
        lines = [("Material (blank + 10% scrap)", area * 1.1 * t * density / 1e9 * price), ("Laser cutting", cut_m / 5.0 * rate_min),
                 ("Bending", bends * 0.5 * rate_min), ("Setup (shared over the batch)", setup_hours * rate_per_hour / batch)]
        detail = {"thickness": t, "blank_area_mm2": area, "cut_length_m": cut_m, "bends": bends}
    elif method == "additive":
        hours = vol_cm3 / 15.0 + 0.5
        lines = [("Material", vol_cm3 * 0.25), ("Machine time", hours * 5.0), ("Post-processing", 10.0 / batch + 2.0)]
        detail = {"print_hours": hours}
    elif method == "casting":
        tooling = 2000.0 + 40.0 * len(body.faces())
        lines = [("Metal (+50% gating and scrap)", mass_kg * 1.5 * price), ("Casting labour", 0.1 * rate_per_hour),
                 ("Pattern / tooling (shared over the batch)", tooling / batch), ("Fettling and machining", 0.25 * rate_per_hour)]
        detail = {"tooling": tooling}
    else:
        raise ValueError(f"unknown method {method!r}: machining, sheet_metal, additive or casting")
    each = sum(v for _, v in lines)
    return {"method": method, "material": mat.get("name"), "category": cat, "quantity": batch, "currency": "USD",
            "breakdown": [{"item": k, "cost": round(v, 2)} for k, v in lines], "cost_each": round(each, 2),
            "cost_batch": round(each * batch, 2), "mass_kg": mass_kg, **detail}


IMPACT = {  # per kg of material: kg CO2e, MJ, litres of water (generic primary-production figures)
    "Steel": (2.0, 25.0, 45.0), "Stainless Steel": (6.2, 80.0, 120.0), "Iron": (1.9, 20.0, 40.0),
    "Aluminium Alloys": (12.0, 200.0, 1100.0), "Copper Alloys": (4.0, 60.0, 300.0), "Titanium Alloys": (35.0, 600.0, 500.0),
    "Plastics": (3.0, 85.0, 180.0), "Rubber": (3.2, 100.0, 250.0), "Woods": (0.5, 10.0, 600.0), "Other": (1.4, 15.0, 20.0)}
GRID_KG_CO2_PER_KWH = {"Asia": 0.6, "Europe": 0.3, "North America": 0.4, "Oceania": 0.55, "South America": 0.2}
PROCESS_KWH_PER_KG = {"casting": 2.5, "sheet_metal": 0.8, "additive": 20.0}


def sustainability(doc, region: str = "Oceania", process: str = "machining", transport_km: float = 1000.0,
                   recycled_fraction: float = 0.0) -> dict:
    """Sustainability estimate (generic data): carbon (kg CO2e), energy (MJ) and water (L) by life-cycle phase:
    material (recycled content at 30% of the virgin impact), manufacturing (machining: the removed stock's
    energy; others: process energy per kg) on the region's grid, truck transport, and end of life (half
    recycled, credited)."""
    from openswx.core.features import mass_properties
    body = doc.body
    if body is None:
        raise ValueError("the part has no body")
    if process not in ("machining", *PROCESS_KWH_PER_KG):
        raise ValueError(f"unknown process {process!r}: machining, casting, sheet_metal or additive")
    mat, cat, density = _material(doc)
    mass = mass_properties(body, density)["mass"] / 1000
    co2, mj, water = IMPACT.get(cat, IMPACT["Other"])
    grid = GRID_KG_CO2_PER_KWH.get(region, 0.45)
    r = max(0.0, min(1.0, recycled_fraction))
    share = (1 - r) + 0.3 * r
    material = {"co2": mass * co2 * share, "energy": mass * mj * share, "water": mass * water * share}
    if process == "machining":
        bb = body.bounding_box()
        removed = max(0.0, (bb.size.X + 4) * (bb.size.Y + 4) * (bb.size.Z + 4) * density / 1e9 - mass)
        kwh = 3.0 * removed + 0.2 * mass
    else:
        kwh = PROCESS_KWH_PER_KG[process] * mass
    phases = {"material": material,
              "manufacturing": {"co2": kwh * grid, "energy": kwh * 3.6, "water": kwh * 2.0},
              "transportation": {"co2": mass / 1000 * transport_km * 0.1, "energy": mass / 1000 * transport_km * 1.4, "water": 0.0},
              "end_of_life": {"co2": -0.25 * mass * co2, "energy": -0.2 * mass * mj, "water": 0.0}}
    total = {k: sum(p[k] for p in phases.values()) for k in ("co2", "energy", "water")}
    return {"material": mat.get("name"), "category": cat, "mass_kg": mass, "region": region, "process": process,
            "phases": {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in phases.items()},
            "total": {k: round(v, 4) for k, v in total.items()},
            "units": {"co2": "kg CO2e", "energy": "MJ", "water": "L"}}


def _hole_tol(dia: float) -> str:  # ISO 286 H7
    for top, tol in ((3, "+0.010"), (6, "+0.012"), (10, "+0.015"), (18, "+0.018"), (30, "+0.021"), (50, "+0.025"),
                     (80, "+0.030"), (120, "+0.035")):
        if dia <= top:
            return f"{tol}/0"
    return "+0.040/0"


def dimxpert_scheme(body, general_tolerance: float = 0.1, position_tolerance: float = 0.1) -> list[dict]:
    """An automatic dimension and tolerance scheme in 3D (PMI): datums A (largest planar face), B and C (the
    largest planar faces at right angles to it and to each other), the overall sizes with a general
    tolerance, hole patterns (count x diameter, H7) with a position tolerance to the datums."""
    from build123d import Vector
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from openswx.core.geom import face_normal, geom_name, interior_point
    planes = sorted((f for f in body.faces() if geom_name(f) == "plane"), key=lambda f: -f.area)
    if not planes:
        raise ValueError("DimXpert needs planar faces for datums")
    pmi, datums, normals = [], [], []
    for f in planes:
        nf = face_normal(f, interior_point(f))
        if all(abs(nf.dot(m)) < 1e-6 for m in normals):
            datums.append(f)
            normals.append(nf)
        if len(datums) == 3:
            break
    for letter, f, nf in zip("ABC", datums, normals):
        pmi.append({"type": "datum", "label": letter, "text": letter, "anchor": list(interior_point(f)), "normal": list(nf)})
    bb = body.bounding_box()
    lo = (bb.min.X, bb.min.Y, bb.min.Z)
    for i, (axis, size) in enumerate((("X", bb.size.X), ("Y", bb.size.Y), ("Z", bb.size.Z))):
        anchor = list(lo)
        anchor[i] += size / 2
        pmi.append({"type": "size", "axis": axis, "value": round(size, 4), "text": f"{size:.2f} ±{general_tolerance:g}",
                    "anchor": anchor})
    groups: dict = {}
    for f in body.faces():
        if geom_name(f) != "cylinder":
            continue
        ad = BRepAdaptor_Surface(f.wrapped)
        if ad.LastUParameter() - ad.FirstUParameter() < 2 * math.pi - 1e-3:
            continue  # fillets and partial cylinders are not holes
        cyl = ad.Cylinder()
        q = interior_point(f)
        o, dd = cyl.Axis().Location(), cyl.Axis().Direction()
        a = Vector(dd.X(), dd.Y(), dd.Z())
        to_axis = Vector(o.X(), o.Y(), o.Z()) - q
        to_axis = to_axis - a * to_axis.dot(a)
        if face_normal(f, q).dot(to_axis) <= 0:
            continue  # a boss, not a hole
        key = (round(2 * cyl.Radius(), 4), tuple(round(abs(v), 3) for v in (a.X, a.Y, a.Z)))
        groups.setdefault(key, []).append(q + to_axis)
    refs = " | ".join("ABC"[:len(datums)])
    for (dia, _ax), centres in sorted(groups.items()):
        n = len(centres)
        pmi.append({"type": "hole", "diameter": dia, "count": n,
                    "text": f"{n}X ⌀{dia:g} H7 ({_hole_tol(dia)})" if n > 1 else f"⌀{dia:g} H7 ({_hole_tol(dia)})",
                    "anchor": list(centres[0]), "position": f"⌖ ⌀{position_tolerance:g} | {refs}"})
    for i, p in enumerate(pmi, 1):
        p["id"] = f"PMI{i}"
    return pmi
