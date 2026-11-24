"""Tests: interop formats (split out of the former test_core.py; shared helpers in core_helpers.py)."""
from core_helpers import *  # noqa: F401,F403


def test_save_open_and_step_roundtrip(tmp_path):
    block()
    path = S.save_part(str(tmp_path / "blk.oswprt"))["saved"]
    S.open_part(path)
    assert vol() == pytest.approx(120000)
    step = tmp_path / "blk.step"
    S.export_file(str(step))
    S.open_part(str(step))
    assert vol() == pytest.approx(120000, rel=1e-6)
    assert S.get_model()["features"][0]["type"] == "import"
    S.export_file(str(tmp_path / "blk.stl"))
    assert (tmp_path / "blk.stl").stat().st_size > 0


def test_iges_3mf_gltf(tmp_path):
    """IGES out and back in (solids exact; surface-only IGES is sewn into a solid), 3MF and glTF exports."""
    from OCP.IGESControl import IGESControl_Writer
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.sketch_circle(sk, [50, 30], 10)
    S.extrude(sk, depth=20)
    v = 100 * 60 * 20 - PI * 100 * 20
    assert S.export_file(str(tmp_path / "p.igs"))["format"] == "iges"
    w = IGESControl_Writer("MM", 0)  # faces only, as most CAD writes IGES
    w.AddShape(S.APP.doc.body.wrapped)
    w.ComputeModel()
    w.Write(str(tmp_path / "faces.iges"))
    for ext in ("3mf", "glb"):
        S.export_file(str(tmp_path / f"p.{ext}"))
        assert (tmp_path / f"p.{ext}").stat().st_size > 1000
    S.open_part(str(tmp_path / "p.igs"))
    assert S.APP.doc.body.volume == pytest.approx(v, rel=1e-6)
    S.open_part(str(tmp_path / "faces.iges"))
    assert len(S.APP.doc.body.solids()) == 1 and S.APP.doc.body.volume == pytest.approx(v, rel=1e-5)


def test_dxf_import_export(tmp_path):
    import ezdxf
    doc = ezdxf.new()
    msp = doc.modelspace()
    # 40 x 20 slot-ish outline: straight sides + two semicircular ends via bulges, and a 5 mm hole
    msp.add_lwpolyline([(0, 0, 0), (40, 0, 1), (40, 20, 0), (0, 20, 1)], format="xyb", close=True)
    msp.add_circle((20, 10), 5)
    src = tmp_path / "in.dxf"
    doc.saveas(src)
    S.new_part()
    r = S.import_dxf(str(src), plane="Front")
    sk = r["result"]["sketch"]
    S.extrude(sk, depth=2)
    area = 40 * 20 + PI * 10 ** 2 - PI * 5 ** 2
    assert vol() == pytest.approx(area * 2, rel=1e-6)
    out = S.export_sketch_dxf(sk, str(tmp_path / "out.dxf"))["path"]
    S.new_part()
    sk2 = S.import_dxf(out, plane="Front")["result"]["sketch"]
    S.extrude(sk2, depth=2)
    assert vol() == pytest.approx(area * 2, rel=1e-6)  # round trip
    # flat pattern DXF of a bent bracket = developed length
    S.new_part()
    s = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(s, [0, 0], [100, 50])
    S.base_flange(s, thickness=2, bend_radius=3, k_factor=0.5)
    edge = [e["id"] for e in S.get_topology()["edges"] if e["type"] == "line" and abs(e["point"][0] - 100) < 1e-6
            and abs(e["point"][1] - 2) < 1e-6][0]
    S.edge_flange(edge, length=30, angle=90)
    fp = S.export_flat_pattern_dxf(str(tmp_path / "flat"))
    BA = PI / 2 * (3 + 1)
    xs = [v for ln in ezdxf.readfile(fp["path"]).modelspace().query("LINE") for v in (ln.dxf.start.x, ln.dxf.end.x, ln.dxf.start.y, ln.dxf.end.y)]
    assert max(xs) - min(xs) == pytest.approx(100 + BA + 30, abs=1e-6)
    assert not S.get_model()["flattened"]  # back to the formed state


def test_dwg_needs_oda_converter(tmp_path):
    from ezdxf.addons import odafc
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    if odafc.is_installed():
        out = S.export_sketch_dxf(sk, str(tmp_path / "c.dwg"))["path"]
        assert out.endswith(".dwg")
    else:
        with pytest.raises(S.CommandError, match="ODA File Converter"):
            S.export_sketch_dxf(sk, str(tmp_path / "c.dwg"))


@pytest.mark.parametrize("name", ["block.x_t", "block.x_b", "drafted_cylinder.x_t", "torus.x_b", "sphere.x_t",
                                  "spline_extrude.x_b", "spline_revolve.x_t", "spline_drafted.x_t", "filleted_block.x_t",
                                  "shelled_box.x_b"])
def test_parasolid_reader(name):
    """Clean-room XT reader against SOLIDWORKS 2025 exports (Parasolid 36.1): volume and area match
    SOLIDWORKS' own mass properties."""
    import json
    from openswx.core.features import mass_properties
    from openswx.exchange.parasolid import read_xt_shape
    truth = json.loads((XT_DATA / "truth.json").read_text())[name.split(".")[0]]
    shape, notes = read_xt_shape(str(XT_DATA / name))
    mp = mass_properties(shape)
    assert shape.is_valid and not notes
    assert abs(mp["volume"] - truth["volume"]) / truth["volume"] < 1e-4
    assert abs(mp["surface_area"] - truth["area"]) / truth["area"] < 1e-4


def test_parasolid_writer_roundtrip(tmp_path):
    """openswx -> .x_t -> clean-room reader gives the same solid (SOLIDWORKS import of the same files is
    checked by tools/difftest/xt_roundtrip.py)."""
    from openswx.core.features import mass_properties
    from openswx.exchange.parasolid import read_xt_shape
    from openswx.exchange.parasolid_write import write_xt
    from build123d import Box, Cone, Cylinder, Sphere, Torus
    for shape in (Box(40, 30, 20) - Cylinder(5, 40), Sphere(10), Torus(20, 5), Cone(10, 4, 15),
                  Box(40, 40, 40) - Box(10, 10, 10)):
        write_xt(shape, str(tmp_path / "s.x_t"))
        back, _ = read_xt_shape(str(tmp_path / "s.x_t"))
        a, b = mass_properties(shape), mass_properties(back)
        assert back.is_valid and b["volume"] == pytest.approx(a["volume"], rel=1e-9)
        assert b["surface_area"] == pytest.approx(a["surface_area"], rel=1e-9)
    block()
    r = S.export_file(str(tmp_path / "blk.x_t"))
    assert r["format"] == "x_t"
    S.new_part()
    S.import_file(str(tmp_path / "blk.x_t"))
    assert vol() == pytest.approx(100 * 60 * 20)


def test_parasolid_rolling_ball_blend():
    """SOLIDWORKS fillet between a plane and a B-surface: BLENDED_EDGE (exact tube around the spine, the
    intersection of the offset supports), BLEND_BOUND contact curves (spine feet on the supports)."""
    import json
    from openswx.core.features import mass_properties
    from openswx.exchange.parasolid import read_xt_shape
    truth = json.loads((XT_DATA / "truth.json").read_text())["spline_fillet"]
    shape, notes = read_xt_shape(str(XT_DATA / "spline_fillet.x_t"))
    mp = mass_properties(shape)
    assert shape.is_valid and not notes
    assert abs(mp["volume"] - truth["volume"]) / truth["volume"] < 1e-4
    assert abs(mp["surface_area"] - truth["area"]) / truth["area"] < 3e-4


def test_parasolid_extent_counts_edge_circles():
    """The runaway-geometry check's reach: a pipe's full-circle edges have no vertices (5.5 m of pipe was
    rejected), so the edges' circles count; a construction circle no edge uses (500 m, on a sample bat)
    does not."""
    from types import SimpleNamespace
    from openswx.exchange.parasolid import _Builder
    nodes = {1: (31, {"centre": [0.0, 0.0, 5.4864], "radius": 0.05715}), 2: (16, {"curve": 1}),
             3: (31, {"centre": [0.0, 0.0, 0.0], "radius": 500.0}), 4: (29, {"pvec": [0.01, 0.0, 0.0]})}
    reach = _Builder(SimpleNamespace(nodes=nodes)).extent()
    assert reach == pytest.approx(2 * (5.4864 + 0.05715) * 1000 + 10)


def test_parasolid_band_of_ring_loops_on_closed_bspline():
    # an XT face between two vertex-less loops on a closed (not periodic) B-spline surface needs no seam in
    # Parasolid; OCC does, and ShapeFix only adds one on a periodic surface (foot_board.sldprt's 0.38 mm wall)
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GeomConvert import GeomConvert
    from OCP.Geom import Geom_Circle
    from OCP.ShapeFix import ShapeFix_Face
    from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Wire
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec
    from build123d import Shape
    from openswx.exchange.parasolid import _Builder
    circ = GeomConvert.CurveToBSplineCurve_s(Geom_Circle(gp_Circ(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 10.0)))
    circ.SetNotPeriodic()  # closed, not periodic: as XT stores it
    surf = _Builder._bspline_extrusion(circ, (0, 0, 1), 100.0)
    assert surf.IsUClosed() and not surf.IsUPeriodic()
    b = BRep_Builder()
    wires = []
    for z, rev in ((0.0, False), (5.0, True)):
        e = BRepBuilderAPI_MakeEdge(circ.Translated(gp_Vec(0, 0, z))).Edge()
        w = TopoDS_Wire()
        b.MakeWire(w)
        b.Add(w, TopoDS.Edge(e.Reversed()) if rev else e)
        wires.append(w)

    def band(s):
        f = TopoDS_Face()
        b.MakeFace(f, s, 1e-7)
        for w in wires:
            b.Add(f, w)
        fx = ShapeFix_Face(f)
        fx.Perform()
        return abs(Shape.cast(fx.Face()).area)
    assert all(_Builder._ring(w) for w in wires)
    assert band(surf) < 1e-6  # (as read before: one loop dropped, a zero-area face)
    # (OCC's default area integration is ~0.3 % off on rational B-spline faces)
    assert band(_Builder._periodic(surf)) == pytest.approx(2 * PI * 10 * 5, rel=1e-2)
