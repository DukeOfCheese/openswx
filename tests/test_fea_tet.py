"""Static stress on the curvature-based tetrahedral mesh (fea_tet), checked against closed-form results.
Needs the optional Netgen mesher (installed with the dev extra); skipped without it."""
import numpy as np
import pytest
from build123d import Box, Cylinder

from openswx import fea_tet
from openswx import session as S

pytestmark = pytest.mark.skipif(not fea_tet.available(), reason="the Netgen mesher is not installed")

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
