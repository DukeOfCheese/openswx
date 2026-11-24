"""The speed-ups give the same answers as the slow ways they replace (each checked against an independent
result: the per-item method, brute force, or an analytic volume)."""
import math

import numpy as np
import pytest
from build123d import Box, Cylinder, Pos, Sphere

from openswx import geom, render
from openswx.features import _MP_CACHE, mass_properties


def test_mass_properties_cache_hits():
    _MP_CACHE.clear()
    b = Box(10, 20, 30)
    first = mass_properties(b)
    assert mass_properties(b) == first and len(_MP_CACHE) == 1  # (the key used to change from call to call)
    assert first["volume"] == pytest.approx(6000)


def _mesh_volume(m):
    vol = 0.0
    for f in m["faces"]:
        v = np.array(f["v"]).reshape(-1, 3)
        t = np.array(f["t"]).reshape(-1, 3)
        vol += np.einsum("ij,ij->i", v[t[:, 0]], np.cross(v[t[:, 1]], v[t[:, 2]])).sum() / 6
    return vol


def test_tessellation_closed_outward_and_cached():
    """The whole-shape mesh encloses the body's volume with outward triangles (reversed faces flipped), and the
    cached result hands out copies a caller may change."""
    s = Box(100, 60, 20) - Pos(20, 0, 0) * Cylinder(8, 30) - Pos(-30, 0, 10) * Sphere(12)
    geom._TESS_CACHE.clear()
    m = geom.tessellate(s)
    assert len(m["faces"]) == len(s.faces()) and len(m["edges"]) == len(s.edges())
    assert _mesh_volume(m) == pytest.approx(s.volume, rel=5e-3)
    m["faces"][0]["color"] = "#ff0000"
    again = geom.tessellate(s)
    assert "color" not in again["faces"][0] and again["faces"][0]["v"] == m["faces"][0]["v"]


def test_triangle_fill_matches_one_at_a_time():
    """The scanline fill of many triangles gives the image of the old fill, one triangle at a time."""
    rng = np.random.default_rng(3)
    h, w = 90, 120
    S = np.empty((300, 3, 3))
    S[..., 0] = rng.uniform(-10, w + 10, (300, 3))
    S[..., 1] = rng.uniform(-10, h + 10, (300, 3))
    S[..., 2] = rng.uniform(0, 1, (300, 3))
    S[::7, :, 1] = S[::7, :1, 1] + rng.uniform(0, 0.8, (len(S[::7]), 3))  # (long thin slivers too)
    cols = rng.uniform(0, 255, (300, 3))
    img_a, z_a = np.zeros((h, w, 3)), np.full((h, w), -np.inf)
    for tri, c in zip(S, cols):
        render._tri(img_a, z_a, tuple(tri[0]), tuple(tri[1]), tuple(tri[2]), c)
    img_b, z_b = np.zeros((h, w, 3)), np.full((h, w), -np.inf)
    render._tris(img_b, z_b, S, cols)
    differ = np.any(np.abs(img_a - img_b) > 1e-6, axis=2)
    assert differ.mean() < 0.005  # (only pixels whose centre lies exactly on an edge may go either way)


def test_faces_of_edge_matches_brute_force():
    p = Box(80, 50, 10)
    for i in range(4):
        p = p - Pos(-30 + 20 * i, 0, 0) * Cylinder(4, 20)
    for e in p.edges():
        brute = [f for f in p.faces() if any(e.is_same(x) for x in f.edges())]
        fast = geom.faces_of_edge(p, e)
        assert len(fast) == len(brute) and all(any(a.is_same(b) for b in brute) for a in fast)


def test_sketch_regions_even_odd_nested():
    """Nested contours keep alternate regions (a plate, a hole in it, an island in the hole)."""
    from openswx import session as S
    from openswx import sketch as SK
    S.new_part("Nest")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-50, -50], [50, 50])
    S.sketch_circle(sk, [0, 0], 30)
    S.sketch_circle(sk, [0, 0], 10)
    s, plane = S.APP.doc.ctx.sketches[sk]
    SK._PROFILES.clear()
    faces = s.profiles(plane)
    assert sum(f.area for f in faces) == pytest.approx(100 * 100 - math.pi * 30 ** 2 + math.pi * 10 ** 2, rel=1e-6)
    assert len(faces) == 2


def test_many_profile_cut_in_one_boolean():
    from openswx import session as S
    S.new_part("Holes")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [120, 60])
    S.extrude(sk, 10)
    h = S.create_sketch("Top")["sketch"]
    for i in range(6):
        for j in range(3):
            S.sketch_circle(h, [10 + 20 * i, 10 + 20 * j], 4)
    S.cut_extrude(h, end_condition="ThroughAllBoth")
    assert S.get_mass_properties()["volume"] == pytest.approx(120 * 60 * 10 - 18 * math.pi * 16 * 10, rel=1e-9)


def test_mesh_is_compressed_on_the_wire():
    from fastapi.testclient import TestClient
    from openswx import session as S
    from openswx.web_server import create_app
    S.new_part("Wire")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 20)
    S.extrude(sk, 10)
    with TestClient(create_app(mount_mcp=False, token=None), base_url="http://127.0.0.1:8765") as c:
        r = c.get("/api/mesh", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip" and r.json()["faces"]
