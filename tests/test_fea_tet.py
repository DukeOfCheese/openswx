import numpy as np
import pytest
from build123d import Box, Cylinder

from openswx.analysis import fea_tet
from openswx import session as S

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not fea_tet.available(), reason="the Netgen mesher is not installed")]

STEEL = {"elastic_modulus": 200000.0, "poisson": 0.3, "yield_strength": 250.0}


def _ends(body):
    fs = body.faces()
    return min(fs, key=lambda f: f.center().X), max(fs, key=lambda f: f.center().X)


def test_shape_functions_are_a_partition_of_unity():
    p = np.random.default_rng(1).random((5, 3)) / 3
    g = fea_tet.shape_grads(p)
    assert np.allclose(g.sum(axis=1), 0.0)  # sum of N is 1 everywhere: its gradient vanishes
    # a linear field u = x + 2y + 3z is reproduced exactly by the quadratic element
    X = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
    X = np.vstack([X, [(X[i] + X[j]) / 2 for i, j in fea_tet.TET_EDGES]])
    G, det = fea_tet._gradients(X[None] * 7.0, fea_tet.GAUSS)
    u = X @ np.array([1.0, 2.0, 3.0]) * 7.0
    assert np.allclose(np.einsum("eqna,n->eqa", G, u), [1, 2, 3]) and np.allclose(det, 7.0 ** 3)


def test_cantilever_deflection_matches_beam_theory():
    b = Box(100, 10, 10)
    root, tip = _ends(b)
    r = fea_tet.solve_static(b, STEEL, [root], [(tip, (0, 0, -100))])
    I = 10 * 10 ** 3 / 12
    bending = 100 * 100 ** 3 / (3 * 200000 * I)
    shear = 100 * 100 / ((5 / 6) * 100 * 200000 / 2.6)
    assert r["max_displacement"] == pytest.approx(bending + shear, rel=0.02)
    assert np.allclose(r["reaction"], [0, 0, 100], atol=1e-6) and r["mesh_volume"] == pytest.approx(10000, rel=1e-9)


def test_hole_in_a_plate_stress_concentration():
    """Peterson: a central hole d in a strip of width W under tension, d/W = 0.2: Kt (net section) = 2.51.
    The voxel mesh cannot resolve this (stair-stepped hole); the curvature-based mesh comes within a few %."""
    b = Box(200, 50, 5) - Cylinder(5, 5)
    root, end = _ends(b)
    r = fea_tet.solve_static(b, STEEL, [root], [(end, (10000, 0, 0))])
    kt = r["max_von_mises"] / (10000 / (40 * 5))
    assert kt == pytest.approx(2.506, rel=0.04)
    g = r["_grid"]
    x, y, _ = g["coords"][int(np.argmax(g["vm"]))]
    assert abs(x) < 1.5 and abs(abs(y) - 5) < 0.5  # at the edge of the hole, across the load


def test_static_study_uses_the_tetrahedral_mesh(tmp_path):
    S.new_part(name="TetStudy")
    S.create_sketch(plane="Top")
    S.sketch_rectangle(sketch="Sketch1", corner1=[0, 0], corner2=[80, 10])
    S.extrude(sketch="Sketch1", depth=10)
    S.set_material(name="Plain Carbon Steel")
    faces = S.get_topology()["faces"]
    root = min(faces, key=lambda f: f["point"][0])["id"]
    tip = max(faces, key=lambda f: f["point"][0])["id"]
    S.simulation_add_fixture(face=root)
    S.simulation_add_force(face=tip, force=[0, -50, 0])
    r = S.simulation_run()
    assert r["mesh"].startswith("tetrahedral") and r["max_displacement"] > 0
    v = S.simulation_run(mesh="voxel")
    assert v["mesh"].startswith("voxel") and v["max_displacement"] == pytest.approx(r["max_displacement"], rel=0.1)
    S.simulation_run(mesh="tetrahedral")
    plot = S.simulation_results()
    assert plot["faces"] and max(max(s) for s in plot["stress"].values()) > 0
    probe = S.simulation_probe(points=[[5, 5, 5]])
    assert probe["probes"][0]["von_mises"] > 0
    life = S.fatigue_run(cycles=1e6)
    assert life["study"] == "fatigue"


def test_quadrature_rules_are_exact():
    import math
    p, w = fea_tet.tet_rule(4)
    for a, b, c in [(0, 0, 0), (2, 1, 0), (1, 1, 2), (4, 0, 0), (2, 2, 1)]:
        exact = math.factorial(a) * math.factorial(b) * math.factorial(c) / math.factorial(a + b + c + 3)
        assert (w * p[:, 0] ** a * p[:, 1] ** b * p[:, 2] ** c).sum() == pytest.approx(exact, rel=1e-12)
    q, v = fea_tet.tri_rule(4)
    assert (v * q[:, 0] ** 2 * q[:, 1] ** 2).sum() == pytest.approx(4 / math.factorial(6), rel=1e-12)


def test_frequency_buckling_and_thermal_match_closed_forms():
    import math
    steel = dict(STEEL, density=7850.0)
    b = Box(200, 10, 10)
    root, _tip = _ends(b)
    r = fea_tet.solve_modal(b, steel, [root], modes=2)
    f1 = 1.875104 ** 2 / (2 * math.pi * 0.2 ** 2) * math.sqrt(200e9 * (0.01 ** 4 / 12) / (7850 * 1e-4))
    assert r["frequencies_hz"][0] == pytest.approx(f1, rel=0.01) and r["mass_kg"] == pytest.approx(0.157, rel=1e-6)
    col = Box(10, 10, 300)
    fs = col.faces()
    base, top = min(fs, key=lambda f: f.center().Z), max(fs, key=lambda f: f.center().Z)
    k = fea_tet.solve_buckling(col, steel, [base], [(top, (0, 0, -1000))], modes=1)
    euler = math.pi ** 2 * 200000 * (10 ** 4 / 12) / (4 * 300 ** 2) / 1000
    assert k["load_factors"][0] == pytest.approx(euler, rel=0.01)
    bar = Box(100, 10, 10)
    e0, e1 = _ends(bar)
    t = fea_tet.solve_thermal(bar, 50.0, [(e0, 100.0)], [], [(e1, 500.0, 20.0)])
    series = 80 / (0.1 / (50 * 1e-4) + 1 / (500 * 1e-4))  # conduction then convection, in series
    assert t["heat_in_per_temperature_boundary"][0] == pytest.approx(series, rel=1e-6)


def test_bonded_bodies_of_two_materials():
    from build123d import Compound, Pos
    s1, s2 = Box(50, 10, 10), Pos(50, 0, 0) * Box(50, 10, 10)
    body = Compound([s1, s2])
    e0 = min(body.faces(), key=lambda f: f.center().X)
    e1 = max(body.faces(), key=lambda f: f.center().X)
    r = fea_tet.solve_static(body, STEEL, [e0], [(e1, (1000, 0, 0))], regions=[(s1, 1.0), (s2, 0.5)])
    series = 1000 * 50 / (200000 * 100) + 1000 * 50 / (100000 * 100)  # the two halves in series
    assert r["_grid"]["U"][:, 0].max() == pytest.approx(series, rel=0.01)


def test_studies_choose_the_tetrahedral_mesh():
    S.new_part(name="TetStudies")
    S.create_sketch(plane="Top")
    S.sketch_rectangle(sketch="Sketch1", corner1=[0, 0], corner2=[80, 10])
    S.extrude(sketch="Sketch1", depth=10)
    S.set_material(name="Plain Carbon Steel")
    faces = S.get_topology()["faces"]
    root = min(faces, key=lambda f: f["point"][0])["id"]
    tip = max(faces, key=lambda f: f["point"][0])["id"]
    S.simulation_add_fixture(face=root)
    S.simulation_add_force(face=tip, force=[-100, 0, 0])
    fr = S.frequency_run(modes=2)
    assert fr["mesh"].startswith("tetrahedral") and S.frequency_mode_shape(mode=1)["displacement"]
    bk = S.buckling_run(modes=1)
    assert bk["mesh"].startswith("tetrahedral") and bk["load_factors"][0] > 0
    S.thermal_add_temperature(face=root, temperature=100.0)
    S.thermal_add_convection(face=tip, coefficient=50.0, ambient=20.0)
    th = S.thermal_run()
    assert th["mesh"].startswith("tetrahedral") and 20 < th["min_temperature"] < 100
    assert S.thermal_results()["temperature"]
    tr = S.thermal_run(transient=True, duration=30.0, steps=10)
    assert tr["mesh"].startswith("tetrahedral") and len(tr["history"]) == 11


def test_transient_cooling_matches_lumped_capacitance():
    """A small aluminium cube (Biot number ~5e-4) cooling by convection on every face:
    T(t) = T_amb + (T0 - T_amb) exp(-t / tau), tau = rho c V / (h A)."""
    import math
    b = Box(10, 10, 10)
    r = fea_tet.solve_thermal_transient(b, 167.0, 2700.0, 896.0, [], [], [(f, 50.0, 20.0) for f in b.faces()],
                                        initial=100.0, duration=60.0, steps=60)
    tau = 2700 * 896 * 1e-6 / (50 * 6e-4)
    assert r["history"][-1]["mean"] == pytest.approx(20 + 80 * math.exp(-60 / tau), abs=0.05)


def test_contact_and_springs_between_separate_bodies():
    """Bodies meshed apart: two stacked blocks in frictionless contact compress like one 40 mm bar; two blocks
    1 mm apart joined by a 1000 N/mm spring move 100 N / 1000 N/mm further than the bars alone."""
    from build123d import Compound, Pos
    lo, hi = Pos(0, 0, 0) * Box(10, 10, 20), Pos(0, 0, 20) * Box(10, 10, 20)
    body = Compound([lo, hi])
    base = min(body.faces(), key=lambda f: f.center().Z)
    top = max(body.faces(), key=lambda f: f.center().Z)
    a, b = max(lo.faces(), key=lambda f: f.center().Z), min(hi.faces(), key=lambda f: f.center().Z)
    r = fea_tet.solve_static(body, STEEL, [base], [(top, (0, 0, -1000))],
                             connectors=[{"a": a, "b": b, "contact": True, "axis": [0, 0, 1]}])
    assert -r["_grid"]["U"][:, 2].min() == pytest.approx(1000 * 40 / (200000 * 100), rel=0.05)
    gap = Pos(0, 0, 21) * Box(10, 10, 20)
    body = Compound([lo, gap])
    base = min(body.faces(), key=lambda f: f.center().Z)
    top = max(body.faces(), key=lambda f: f.center().Z)
    r = fea_tet.solve_static(body, STEEL, [base], [(top, (0, 0, 100))],
                             connectors=[{"a": a, "b": min(gap.faces(), key=lambda f: f.center().Z), "kn": 1000.0,
                                          "kt": 1000.0, "axis": [0, 0, 1]}])
    assert r["_grid"]["U"][:, 2].max() == pytest.approx(0.1 + 100 * 40 / (200000 * 100), rel=0.06)


def test_plastic_bar_and_drop_match_closed_forms():
    """A bar pulled past yield stretches by sy / E + (s - sy) / Et per unit length (bilinear material); a bar
    dropped onto its end takes up m g h as strain energy under its own inertia: peak deceleration
    G = sqrt(6 E h / (rho g L^2)) times g (energy method, uniaxial)."""
    import math
    steel = dict(STEEL, density=7850.0)
    bar = Box(100, 10, 10)
    root, tip = _ends(bar)
    r = fea_tet.solve_nonlinear(bar, steel, [root], [(tip, (30000, 0, 0))], steps=4, hardening=0.05)
    strain = 250 / 200000 + (300 - 250) / (0.05 * 200000)
    assert r["plastic_elements"] > 0 and r["max_displacement"] == pytest.approx(100 * strain, rel=0.05)
    d = fea_tet.solve_drop(bar, steel, [root], 100.0, (-1, 0, 0))
    g = 9810.0
    expect = math.sqrt(6 * 200000 * 100 / (7850e-12 * g * 100 ** 2))
    assert d["peak_acceleration_g"] == pytest.approx(expect, rel=0.05) and d["mass_kg"] == pytest.approx(0.0785, rel=1e-6)
