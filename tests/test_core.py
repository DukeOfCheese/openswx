import math
from pathlib import Path
import os

import pytest

from openswx import session as S
from openswx.geom import STD_PLANES
from openswx.sketch import Sketch

PI = math.pi


def vol():
    return S.get_mass_properties()["volume"]


def top_face(y=None):
    faces = [f for f in S.get_topology()["faces"] if f["normal"][1] > 0.99]
    if y is not None:
        faces = [f for f in faces if abs(f["point"][1] - y) < 1e-6]
    return max(faces, key=lambda f: f["point"][1])["id"]


def block(w=100, d=60, h=20):
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-w / 2, -d / 2], [w / 2, d / 2])
    S.extrude(sk, depth=h)
    return sk


# ---------------------------------------------------------------- sketch solver
def test_rectangle_becomes_fully_defined():
    s = Sketch()
    lines = s.add_rectangle([0, 0], [40, 20])
    assert s.solve()["dof"] == 4
    s.add_relation("coincident", ["ORIGIN", s.entities[lines[0]]["p1"]])
    s.add_dimension("length", [lines[0]], 50)
    s.add_dimension("length", [lines[1]], 30)
    st = s.solve()
    assert st["state"] == "fully_defined" and st["dof"] == 0
    assert s.points[s.entities[lines[2]]["p1"]] == pytest.approx([50, 30], abs=1e-9)


def test_redundant_dimension_becomes_driven():
    s = Sketch()
    lines = s.add_rectangle([0, 0], [40, 20])
    s.add_dimension("length", [lines[0]], 50)
    d = s.add_dimension("length", [lines[2]], 99)
    assert d["driven"] and d["value"] == pytest.approx(50)


def test_tangent_and_angle_constraints():
    s = Sketch()
    ln = s.add_line([0, 0], [50, 0])
    c = s.add_circle([20, 12], 5)
    s.add_relation("horizontal", [ln])
    s.add_relation("tangent", [ln, c])
    s.add_dimension("radius", [c], 8)
    s.solve()
    line_y = s.points[s.entities[ln]["p1"]][1]
    assert abs(s.points[s.entities[c]["c"]][1] - line_y) == pytest.approx(8, abs=1e-7)
    assert s.entities[c]["r"] == pytest.approx(8, abs=1e-7)
    s2 = Sketch()
    a = s2.add_line([0, 0], [10, 0])
    b = s2.add_line([0, 0], [10, 3])
    s2.add_relation("horizontal", [a])
    s2.add_dimension("angle", [a, b], 30)
    s2.solve()
    P = s2.points
    da = [P[s2.entities[a]["p2"]][i] - P[s2.entities[a]["p1"]][i] for i in (0, 1)]
    db = [P[s2.entities[b]["p2"]][i] - P[s2.entities[b]["p1"]][i] for i in (0, 1)]
    ang = math.degrees(math.atan2(db[1], db[0]) - math.atan2(da[1], da[0]))
    assert abs(ang) == pytest.approx(30, abs=1e-6)


def test_profile_with_hole():
    s = Sketch()
    s.add_rectangle([-50, -30], [50, 30])
    s.add_circle([0, 0], 10)
    faces = s.profiles(STD_PLANES["Top"])
    assert len(faces) == 1 and faces[0].area == pytest.approx(6000 - PI * 100)


# ---------------------------------------------------------------- features
def test_extrude_cut_fillet_volumes():
    block()
    assert vol() == pytest.approx(120000)
    sk = S.create_sketch(top_face())["sketch"]
    S.sketch_circle(sk, [0, 0], 10)
    S.cut_extrude(sk, end_condition="ThroughAll")
    assert vol() == pytest.approx(120000 - PI * 100 * 20)
    edges = [e["id"] for e in S.get_topology()["edges"] if e["type"] == "line" and abs(e["length"] - 20) < 1e-6]
    S.fillet(5, edges=edges)
    assert vol() == pytest.approx(120000 - PI * 2000 - 4 * (25 - 25 * PI / 4) * 20)


def test_sw_end_condition_integers_and_midplane():
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [10, 10])
    S.extrude(sk, depth=20, end_condition=6)  # swEndCondMidPlane
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["min"][2] == pytest.approx(-10) and bb["max"][2] == pytest.approx(10)


def test_draft_and_direction2():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [20, 20], centered=True)
    S.extrude(sk, depth=10, direction2_depth=5)
    assert vol() == pytest.approx(40 * 40 * 15)


def test_revolve_and_circular_pattern():
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_centerline(sk, [0, -5], [0, 50])
    S.sketch_rectangle(sk, [10, 0], [20, 40])
    S.revolve(sk)
    assert vol() == pytest.approx(PI * 300 * 40)
    s2 = S.create_sketch(top_face())["sketch"]
    S.sketch_circle(s2, [15, 0], 2)
    S.cut_extrude(s2, depth=5)
    S.circular_pattern(["Cut-Extrude1"], "Y", 6)
    assert vol() == pytest.approx(PI * 300 * 40 - 6 * PI * 4 * 5)


def test_linear_pattern_mirror_hole_chamfer_shell():
    block()
    S.hole(top_face(), [[-40, -20]], diameter=6, end_condition="ThroughAll")
    S.linear_pattern(["Hole1"], "X", 15, 3)  # x = -40, -25, -10 (mirror copies do not overlap)
    assert vol() == pytest.approx(120000 - 3 * PI * 9 * 20)
    S.mirror("Right", features=["LPattern1"])
    assert vol() == pytest.approx(120000 - 6 * PI * 9 * 20)
    S.hole(top_face(), [[0, 0]], diameter=10, depth=8, type="counterbore", cbore_diameter=16, cbore_depth=4)
    assert vol() == pytest.approx(120000 - 6 * PI * 9 * 20 - PI * 25 * 8 - PI * (64 - 25) * 4)
    before = vol()
    edges = [e["id"] for e in S.get_topology()["edges"] if e["type"] == "line" and abs(e["length"] - 100) < 1e-6]
    S.chamfer(2, edges=edges[:1])
    assert vol() == pytest.approx(before - 0.5 * 2 * 2 * 100)
    S.shell(2, faces_to_remove=[top_face()])
    # the wall around the blind counterbore only touched the removed top face, so it may float free
    assert S.get_mass_properties()["bodies"] >= 1 and vol() < before / 2


def test_shell_open_box():
    block()
    S.shell(2, faces_to_remove=[top_face()])
    assert vol() == pytest.approx(120000 - 96 * 56 * 18)
    assert S.get_mass_properties()["bodies"] == 1


def test_reference_plane_sketch():
    S.new_part()
    S.reference_plane("Top", offset=30)
    sk = S.create_sketch("Plane1")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, depth=10)
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["min"][1] == pytest.approx(30) and bb["max"][1] == pytest.approx(40)


# ---------------------------------------------------------------- design intent survives edits
def test_edit_keeps_downstream_references():
    block()
    sk = S.create_sketch(top_face())["sketch"]
    S.sketch_circle(sk, [0, 0], 10)
    S.cut_extrude(sk, end_condition="ThroughAll")
    S.hole(top_face(), [[-40, -20]], diameter=6, end_condition="ThroughAll")
    corners = [e["id"] for e in S.get_topology()["edges"] if e["type"] == "line" and abs(e["length"] - 20) < 1e-6
               and abs(abs(e["point"][0]) - 50) < 1e-6]
    S.fillet(5, edges=corners)
    S.set_dimension("D1@Sketch1", 0) if False else None
    S.edit_feature("Boss-Extrude1", {"depth": 30})
    m = S.get_model()
    assert not m["errors"]
    expected = 180000 - PI * 100 * 30 - PI * 9 * 30 - 4 * (25 - 25 * PI / 4) * 30
    assert vol() == pytest.approx(expected)


def test_width_change_keeps_all_fillets():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    lines = S.sketch_rectangle(sk, [0, 0], [60, 30], centered=True)["result"]["entities"]
    S.sketch_dimension(sk, "length", [lines[0]], 120)
    S.extrude(sk, depth=10)
    S.hole(top_face(), [[48, -18]], diameter=8, end_condition="ThroughAll")
    corners = [e["id"] for e in S.get_topology()["edges"] if e["type"] == "line" and abs(e["length"] - 10) < 1e-6
               and abs(abs(e["point"][0]) - 60) < 1e-6]
    S.fillet(6, edges=corners)
    S.set_dimension("D1@Sketch1", 150)
    assert not S.get_model()["errors"]
    assert vol() == pytest.approx(150 * 60 * 10 - PI * 16 * 10 - 4 * (36 - 9 * PI) * 10)


# ---------------------------------------------------------------- document behaviour
def test_undo_redo_rollback_suppress():
    block()
    sk = S.create_sketch(top_face())["sketch"]
    S.sketch_circle(sk, [0, 0], 10)
    S.cut_extrude(sk, end_condition="ThroughAll")
    v_cut = vol()
    S.suppress_feature("Cut-Extrude1", True)
    assert vol() == pytest.approx(120000)
    S.suppress_feature("Cut-Extrude1", False)
    S.rollback("Boss-Extrude1")
    assert vol() == pytest.approx(120000)
    assert any(f["rolled_back"] for f in S.get_model()["features"])
    S.rollback()
    assert vol() == pytest.approx(v_cut)
    S.undo()
    S.undo()
    S.redo()
    S.redo()
    assert vol() == pytest.approx(v_cut)


def test_failed_feature_is_rejected():
    block()
    with pytest.raises(S.CommandError):
        S.fillet(500, edges=["Edge0"])
    assert [f["name"] for f in S.get_model()["features"]] == ["Sketch1", "Boss-Extrude1"]


def test_preview_does_not_mutate():
    sk = block()
    s2 = S.create_sketch(top_face())["sketch"]
    S.sketch_circle(s2, [0, 0], 5)
    before = [f["name"] for f in S.get_model()["features"]]
    m = S.preview("cut_extrude", {"sketch": s2, "end_condition": "ThroughAll"})
    assert m["ok"] and m["faces"]
    assert [f["name"] for f in S.get_model()["features"]] == before
    assert vol() == pytest.approx(120000)
    assert sk == "Sketch1"


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


def test_view_image_is_png():
    block()
    import base64
    png = base64.b64decode(S.get_view_image("isometric", 200, 150)["png_base64"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- phase 2 features
def face_at_y(y):
    return [f["id"] for f in S.get_topology()["faces"] if f["normal"][1] > 0.99 and abs(f["point"][1] - y) < 1e-6][0]


def test_up_to_end_conditions():
    block()
    S.reference_plane("Top", offset=50)
    S.extrude(S.create_sketch("Plane1")["sketch"] and "Sketch2", depth=1) if False else None
    v0 = vol()
    s = S.create_sketch("Plane1")["sketch"]
    S.sketch_circle(s, [-30, 0], 5)
    S.extrude(s, end_condition="UpToSurface", up_to=face_at_y(20), reverse=True)
    assert vol() - v0 == pytest.approx(PI * 25 * 30)
    v1 = vol()
    s = S.create_sketch("Plane1")["sketch"]
    S.sketch_circle(s, [30, 0], 5)
    S.extrude(s, end_condition=5, up_to=face_at_y(20), up_to_offset=10, reverse=True)  # swEndCondOffsetFromSurface
    assert vol() - v1 == pytest.approx(PI * 25 * 20)
    v2 = vol()
    s = S.create_sketch("Plane1")["sketch"]
    S.sketch_circle(s, [0, 20], 5)
    S.extrude(s, end_condition="UpToVertex", up_to=[50, 20, 30], reverse=True)
    assert vol() - v2 == pytest.approx(PI * 25 * 30)
    v3 = vol()
    s = S.create_sketch("Plane1")["sketch"]
    S.sketch_circle(s, [0, -20], 4)
    S.extrude(s, end_condition="UpToNext", reverse=True)
    assert vol() - v3 == pytest.approx(PI * 16 * 30)


def test_up_to_curved_surface():
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_circle(sk, [0, 0], 50)
    S.extrude(sk, depth=40)  # cylinder along +Z, radius 50
    S.reference_plane("Top", offset=100)
    s2 = S.create_sketch("Plane1")["sketch"]
    S.sketch_circle(s2, [0, -20], 2)  # small pin straight above the cylinder (sketch y = -Z)
    v0 = vol()
    cyl = [f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder"][0]
    S.extrude(s2, end_condition="UpToSurface", up_to=cyl, reverse=True)
    # pin runs from y=100 down to the cylinder top surface y=50 (tiny cap curvature over r=2 is negligible)
    assert vol() - v0 == pytest.approx(PI * 4 * 50, rel=1e-3)


def test_sweep_loft_draft():
    S.new_part()
    path = S.create_sketch("Front")["sketch"]
    end = S.sketch_line(path, [0, 0], [0, 50])["result"]["points"][1]
    S.sketch_three_point_arc(path, end, [20, 70], [20 - 20 / math.sqrt(2), 50 + 20 / math.sqrt(2)])
    prof = S.create_sketch("Top")["sketch"]
    S.sketch_circle(prof, [0, 0], 3)
    S.sweep(prof, path)
    assert vol() == pytest.approx(PI * 9 * (50 + PI * 10), rel=1e-6)
    tree = {f["name"]: f["absorbed_by"] for f in S.get_model()["features"]}
    assert tree[path] == "Sweep1" and tree[prof] == "Sweep1"
    S.new_part()
    a = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(a, [-20, -20], [20, 20])
    S.reference_plane("Top", offset=30)
    b = S.create_sketch("Plane1")["sketch"]
    S.sketch_rectangle(b, [-10, -10], [10, 10])
    S.loft([a, b], ruled=True)
    assert vol() == pytest.approx(30 / 3 * (1600 + 400 + 800))
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-20, -20], [20, 20])
    S.extrude(sk, depth=20)
    sides = [f["id"] for f in S.get_topology()["faces"] if abs(f["normal"][1]) < 0.01]
    S.draft("Top", sides, 5)
    t = 40 - 2 * math.tan(math.radians(5)) * 20
    assert vol() == pytest.approx(20 / 3 * (1600 + t * t + 40 * t))


def test_sketch_offset_mirror_fillet():
    s = Sketch()
    lines = s.add_rectangle([0, 0], [40, 20])
    s.offset_entities(lines, 5)
    assert s.profiles(STD_PLANES["Front"])[0].area == pytest.approx(50 * 30 - 40 * 20)
    s = Sketch()
    lines = s.add_rectangle([0, 0], [40, 20])
    s.fillet_corner(s.entities[lines[0]]["p2"], 5)
    assert s.solve()["state"] == "under_defined"
    assert s.profiles(STD_PLANES["Front"])[0].area == pytest.approx(800 - (25 - PI * 25 / 4))
    s = Sketch()
    cl = s.add_centerline([0, -10], [0, 30])
    c = s.add_circle([10, 5], 3)
    m = s.mirror_entities([c], cl)
    s.solve()
    assert s.points[s.entities[m[0]]["c"]] == pytest.approx([-10, 5], abs=1e-7)


def test_sketch_trim_extend():
    s = Sketch()
    h = s.add_line([0, 0], [100, 0])
    s.add_line([30, -10], [30, 10])
    s.add_line([70, -10], [70, 10])
    pieces = s.trim(h, [50, 0])  # remove the middle part between the two crossings
    assert len(pieces) == 2
    lengths = sorted(round(abs(s.coords(s.entities[p]["p2"])[0] - s.coords(s.entities[p]["p1"])[0]), 6) for p in pieces)
    assert lengths == [30, 30]
    s2 = Sketch()
    c = s2.add_circle([0, 0], 10)
    s2.add_line([-20, 0], [20, 0])
    arc = s2.trim(c, [0, 10])[0]  # remove the upper half
    a = s2.entities[arc]
    assert s2.coords(a["s"])[1] == pytest.approx(0, abs=1e-9) and s2.coords(a["e"])[1] == pytest.approx(0, abs=1e-9)
    mid = s2.coords(a["c"])
    assert mid == pytest.approx([0, 0])
    s3 = Sketch()
    ln = s3.add_line([0, 0], [10, 0])
    s3.add_line([40, -5], [40, 5])
    s3.extend(ln, [9, 0])
    assert s3.coords(s3.entities[ln]["p2"]) == pytest.approx([40, 0])


def test_equations_and_configurations(tmp_path):
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    rect = S.sketch_rectangle(sk, [0, 0], [50, 30])["result"]
    lines = rect["entities"]
    S.sketch_relation(sk, "coincident", ["ORIGIN", rect["points"][0]])  # anchor the corner like a real part
    S.sketch_dimension(sk, "length", [lines[0]], 50)
    S.sketch_dimension(sk, "length", [lines[1]], 30)
    S.extrude(sk, depth=10)
    S.set_global_variable("width", 120)
    S.set_equation("D1@Sketch1", '"width"')
    S.set_equation("D2@Sketch1", '"width"/4')
    S.set_equation("D1@Boss-Extrude1", '"D2@Sketch1" / 3')
    assert vol() == pytest.approx(120 * 30 * 10)
    S.set_global_variable("width", 60)
    assert vol() == pytest.approx(60 * 15 * 5)
    assert {e["target"] for e in S.get_equations()["equations"]} == {"D1@Sketch1", "D2@Sketch1", "D1@Boss-Extrude1"}
    S.add_configuration("Long")
    S.set_configuration_value("Long", "D1@Boss-Extrude1", 40)
    assert vol() == pytest.approx(60 * 15 * 40)
    S.activate_configuration("Default")
    assert vol() == pytest.approx(60 * 15 * 5)
    top = [f["id"] for f in S.get_topology()["faces"] if f["normal"][1] > 0.99][0]
    s2 = S.create_sketch(top)["sketch"]
    S.sketch_circle(s2, [30, 7.5], 3)
    S.cut_extrude(s2, end_condition="ThroughAll")
    assert vol() == pytest.approx(60 * 15 * 5 - PI * 9 * 5)
    S.activate_configuration("Long")
    S.suppress_feature("Cut-Extrude1", True)
    assert vol() == pytest.approx(60 * 15 * 40)
    S.activate_configuration("Default")
    assert vol() == pytest.approx(60 * 15 * 5 - PI * 9 * 5)
    path = S.save_part(str(tmp_path / "eq.oswprt"))["saved"]
    S.open_part(path)
    S.activate_configuration("Long")
    assert vol() == pytest.approx(60 * 15 * 40)


def _cyl_part(name, r, depth, box=None):
    S.new_part(name)
    sk = S.create_sketch("Front")["sketch"]
    if box:
        S.sketch_rectangle(sk, [-box, -box], [box, box])
    S.sketch_circle(sk, [0, 0], r)
    S.extrude(sk, depth=depth)


def test_mechanical_mates():
    """Screw (advance = turns x pitch, counting past a full turn), rack and pinion (travel = turn x pitch
    radius), universal joint (Cardan: tan thB cos beta = tan thA), hinge (one rotation left)."""
    import numpy as np
    from scipy.spatial.transform import Rotation as Rot
    topo_cyl = lambda c: [f["id"] for f in S.get_topology()["faces"] if f["id"].startswith(c) and f["type"] == "cylinder"][0]  # noqa: E731

    _cyl_part("Nut", 5, 10, box=15)
    _cyl_part("Bolt", 5, 40)
    S.new_assembly("ScrewAsm")
    S.insert_component("Nut")
    S.insert_component("Bolt", position=[3, 5, 2])
    S.add_mate("screw", topo_cyl("Nut"), topo_cyl("Bolt"), value=2.0)
    z0 = S.APP.asm.component("Bolt-1").x[5]
    for k in range(1, 6):
        S.rotate_component("Bolt-1", [0, 0, 1], 90)
        assert S.APP.asm.component("Bolt-1").x[5] - z0 == pytest.approx(0.5 * k, abs=1e-6)

    S.new_part("Ground")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [-150, -30], [150, 0])
    S.extrude(sk, depth=40)
    _cyl_part("Pinion", 10, 10)
    S.new_part("Rack")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [-100, 10], [100, 20])
    S.extrude(sk, depth=10)
    S.new_assembly("RackAsm")
    S.insert_component("Ground")
    S.insert_component("Pinion", position=[0, 40, 0])
    S.fix_component("Pinion-1")
    S.insert_component("Rack", position=[0, 20, 0])
    t = S.get_topology()
    face = lambda c, ax, s: [f["id"] for f in t["faces"] if f["id"].startswith(c) and f["type"] == "plane" and f["normal"][ax] * s > 0.99][0]  # noqa: E731
    S.add_mate("distance", face("Ground", 1, 1), face("Rack", 1, -1), value=20)
    S.add_mate("coincident", face("Ground", 2, 1), face("Rack", 2, 1), aligned=True)
    edge = [e["id"] for e in S.get_topology()["edges"] if e["id"].startswith("Rack") and e["type"] == "line" and abs(e["length"] - 200) < 1e-6][0]
    S.add_mate("rack_pinion", edge, topo_cyl("Pinion"), value=20.0)
    x0 = S.APP.asm.component("Rack-1").x.copy()
    for ang, quarter_turns in ((90, 1), (180, 3), (-360, -1)):
        S.rotate_component("Pinion-1", [0, 0, 1], ang)
        x = S.APP.asm.component("Rack-1").x
        assert x[3] - x0[3] == pytest.approx(quarter_turns * PI / 2 * 10, abs=1e-5)
        assert Rot.from_rotvec(x[:3]).as_matrix() == pytest.approx(np.eye(3), abs=1e-6)

    _cyl_part("ShaftA", 4, 30)
    _cyl_part("Bearing", 4, 10, box=10)
    _cyl_part("ShaftB", 4, 30)
    S.new_assembly("UJAsm")
    S.insert_component("ShaftA")
    S.insert_component("Bearing", position=[0, 0, 60], rotation_deg=[30, 0, 0])
    S.fix_component("Bearing-1")
    S.insert_component("ShaftB", position=[0, 0, 60], rotation_deg=[30, 0, 0])
    S.add_mate("concentric", topo_cyl("Bearing"), topo_cyl("ShaftB"))
    S.add_mate("universal_joint", topo_cyl("ShaftA"), topo_cyl("ShaftB"))
    b0 = S.APP.asm.component("ShaftB-1").x.copy()
    axis_b = Rot.from_rotvec(b0[:3]).apply([0, 0, 1])
    total = 0
    for step in (45, 90, 100):
        total += step
        S.rotate_component("ShaftA-1", [0, 0, 1], step)
        b = S.APP.asm.component("ShaftB-1").x
        turned = math.degrees(np.dot((Rot.from_rotvec(b[:3]) * Rot.from_rotvec(b0[:3]).inv()).as_rotvec(), axis_b))
        want = math.degrees(math.atan2(math.sin(math.radians(total)), math.cos(math.radians(total)) * math.cos(math.radians(30))))
        assert turned == pytest.approx(want, abs=1e-3) and not S.APP.asm.status["failing"]

    S.new_part("Leaf")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, -10], [50, 10])
    S.sketch_circle(sk, [0, 0], 3)
    S.extrude(sk, depth=5)
    S.new_assembly("HingeAsm")
    S.insert_component("Leaf")
    S.insert_component("Leaf", position=[0, 0, 20], rotation_deg=[0, 0, 40])
    fs = S.get_topology()["faces"]
    cyl1, cyl2 = (next(f["id"] for f in fs if f["id"].startswith(c) and f["type"] == "cylinder") for c in ("Leaf-1", "Leaf-2"))
    top1 = next(f["id"] for f in fs if f["id"].startswith("Leaf-1") and f["type"] == "plane" and f["normal"][2] > 0.99)
    bot2 = next(f["id"] for f in fs if f["id"].startswith("Leaf-2") and f["type"] == "plane" and f["normal"][2] < -0.99)
    S.add_mate("hinge", cyl1, cyl2, entity3=top1, entity4=bot2)
    st = S.APP.asm.status
    assert not st["failing"] and st["dof"]["Leaf-2"] == 1
    assert S.APP.asm.component("Leaf-2").x[5] == pytest.approx(5)  # resting on the first leaf


def test_assembly_cuts_bom_and_edit_in_context(tmp_path):
    """Assembly hole / extruded cut through several parts (assembly only: the parts are unchanged) and a mate
    to the hole the cut made; BOM export; Edit Part in context shows the rest of the assembly and its
    changes update the assembly."""
    S.new_part("Plate")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=10)
    S.save_part(str(tmp_path / "plate.oswprt"))
    S.new_part("Block")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 40])
    S.extrude(sk, depth=20)
    S.set_custom_property("Description", "Spacer block")
    S.new_assembly("Fixture")
    S.insert_component(str(tmp_path / "plate.oswprt"))
    S.insert_component("Block", position=[10, 10, 0])  # stacked on the plate: x 10..50, y 10..30, z -40..0
    v0 = S.APP.asm.body.volume
    S.assembly_hole([30, 40, -20], [0, -1, 0], 8)  # down through the block (20) and the plate (10)
    assert v0 - S.APP.asm.body.volume == pytest.approx(PI * 16 * 30, rel=1e-6)
    assert S.APP.documents["Block"].body.volume == pytest.approx(40 * 40 * 20)  # the part itself is unchanged
    S.assembly_cut_extrude([[0, 40, -60], [5, 40, -60], [5, 40, -55], [0, 40, -55]], components=["Plate-1"])
    assert v0 - S.APP.asm.body.volume == pytest.approx(PI * 16 * 30 + 25 * 10, rel=1e-6)
    holes = [f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder"]
    assert sorted(h.split("/")[0] for h in holes) == ["Block-1", "Plate-1"]  # mates can use the cut's faces
    S.new_part("Pin")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 4)
    S.extrude(sk, depth=50)
    S.activate_document("Fixture")
    S.insert_component("Pin", position=[70, 30, -20])
    pin = next(f["id"] for f in S.get_topology()["faces"] if f["id"].startswith("Pin") and f["type"] == "cylinder")
    S.add_mate("concentric", holes[0], pin)
    x = S.APP.asm.component("Pin-1").x
    assert (x[3], x[5]) == pytest.approx((30, -20), abs=1e-5)

    bom = S.assembly_bom()["bom"]
    assert [(r["part_number"], r["description"], r["qty"]) for r in bom] == [("Plate", "", 1), ("Block", "Spacer block", 1), ("Pin", "", 1)]
    out = S.export_bom(str(tmp_path / "bom.csv"))
    assert out["rows"] == 3 and "Spacer block" in (tmp_path / "bom.csv").read_text(encoding="utf-8")

    v1 = S.APP.asm.body.volume
    S.edit_in_context("Plate-1")
    assert S.get_model()["in_context"]["component"] == "Plate-1"
    assert {f["component"] for f in S.get_mesh()["context"]} == {"Block-1", "Pin-1"}
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [80, 30], 5)
    S.cut_extrude(sk, end_condition="ThroughAllBoth")
    S.return_to_assembly()
    assert S.APP.active == "Fixture" and v1 - S.APP.asm.body.volume == pytest.approx(PI * 25 * 10, rel=1e-6)
    for name in ("Fixture", "Pin", "Block", "Plate"):  # later tests make parts with these names
        S.close_document(name)


class _FakeReg:
    """An in-memory stand-in for winreg (HKEY_CURRENT_USER only), to test file associations safely."""
    HKEY_CURRENT_USER, REG_SZ, KEY_ALL_ACCESS = "HKCU", 1, 0xF003F

    def __init__(self):
        self.keys = {}  # path -> {value name: data}

    class _K:
        def __init__(self, reg, path):
            self.reg, self.path = reg, path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def CreateKey(self, root, path):  # noqa: N802 (winreg names)
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):
            self.keys.setdefault("\\".join(parts[:i]), {})
        return self._K(self, path)

    def OpenKey(self, root, path, res=0, access=0):  # noqa: N802
        if path not in self.keys:
            raise OSError(path)
        return self._K(self, path)

    def SetValueEx(self, k, name, res, typ, data):  # noqa: N802
        self.keys[k.path][name] = data

    def QueryValueEx(self, k, name):  # noqa: N802
        if name not in self.keys[k.path]:
            raise OSError(name)
        return self.keys[k.path][name], self.REG_SZ

    def DeleteValue(self, k, name):  # noqa: N802
        if name not in self.keys[k.path]:
            raise OSError(name)
        del self.keys[k.path][name]

    def EnumKey(self, k, i):  # noqa: N802
        subs = sorted({p[len(k.path) + 1:].split("\\")[0] for p in self.keys if p.startswith(k.path + "\\")})
        if i >= len(subs):
            raise OSError("no more")
        return subs[i]

    def DeleteKey(self, root, path):  # noqa: N802
        if any(p.startswith(path + "\\") for p in self.keys):
            raise OSError("has subkeys")
        self.keys.pop(path)


def test_drawing_document(tmp_path):
    """A drawing saved as its own .oswdrw refers to its part by a relative path; opening it (the part closed)
    opens the part with the drawing; the pair can be moved together."""
    import json as _json
    import shutil
    S.new_part("DwgPart")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=20)
    with pytest.raises(S.CommandError):
        S.save_drawing_document(str(tmp_path / "a.oswdrw"))  # no drawing yet
    S.drawing_new(sheet="A3")
    with pytest.raises(S.CommandError):
        S.save_drawing_document(str(tmp_path / "a.oswdrw"))  # the part is not saved yet
    (tmp_path / "p").mkdir()
    S.save_part(str(tmp_path / "p" / "dwgpart.oswprt"))
    S.drawing_add_sheet()
    out = S.save_drawing_document()
    assert out["saved"].endswith("dwgpart.oswdrw")
    assert _json.loads((tmp_path / "p" / "dwgpart.oswdrw").read_text(encoding="utf-8"))["model"] == "dwgpart.oswprt"
    S.close_document("DwgPart")
    shutil.move(str(tmp_path / "p"), str(tmp_path / "moved"))
    r = S.open_part(str(tmp_path / "moved" / "dwgpart.oswdrw"))
    assert r["open_drawing"] and [s["name"] for s in S.drawing_info()["sheets"]] == ["Sheet1", "Sheet2"]
    assert S.get_model()["drawing_document"].endswith("dwgpart.oswdrw")
    S.close_document("DwgPart")


def test_open_solidworks_drawing(tmp_path, monkeypatch):
    """A SOLIDWORKS drawing (.slddrw): the referenced model is found beside the drawing and opened, and the
    sheets and views are recreated - model views in their orientations, projected views from them. (A
    synthetic Definition stream in the record layout observed in SOLIDWORKS files: no sample is committed.)"""
    from openswx import sw_drawing
    u = lambda s: b"\x00\x07" + s.encode("utf-16-le") + b"\x00\x00\x03"  # noqa: E731
    model = "C:\\Somewhere\\Else\\bracket.SLDPRT"
    definition = (u("Sheet1") + u("Drawing View1") + u("Drawing View2") + u("Drawing View3") + u(model)  # contents
                  + u("Sheet1") + u("Drawing View1") + u(model) + u("*Front") + u("Drawing View2")
                  + u("Drawing View3") + u("*Isometric") + u("Sheet2") + u("Drawing View4") + u("*Top"))
    names = b"\x01\x00" + u("Sheet1") + u("Sheet2")
    monkeypatch.setattr(sw_drawing, "read_streams", lambda p: {"Contents/Definition": definition, "SheetPreviews/SheetNames": names})
    drw = sw_drawing.read_drawing(str(tmp_path / "bracket.slddrw"))
    assert drw["models"] == [model] and drw["sheets"] == ["Sheet1", "Sheet2"]
    assert [(v["name"], v["orientation"], v["sheet"]) for v in drw["views"]] == [
        ("Drawing View1", "front", "Sheet1"), ("Drawing View2", None, "Sheet1"), ("Drawing View3", "isometric", "Sheet1"),
        ("Drawing View4", "top", "Sheet2")]
    S.new_part("bracket")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=20)
    S.save_part(str(tmp_path / "bracket.oswprt"))
    S.close_document("bracket")
    monkeypatch.setattr(sw_drawing, "find_model", lambda p, models: tmp_path / "bracket.oswprt")
    (tmp_path / "bracket.slddrw").write_bytes(b"")
    r = S.open_part(str(tmp_path / "bracket.slddrw"))
    assert r["open_drawing"] and r["sheets"] == ["Sheet1", "Sheet2"] and r["scale"] == 0.5
    info = S.drawing_info()
    kinds = [[(v["kind"], v.get("orientation") or v.get("side")) for v in sh["views"]] for sh in info["sheets"]]
    assert kinds == [[("model", "front"), ("projected", "top"), ("model", "isometric")], [("model", "top")]]
    assert S.get_model()["show_drawing"] and "show_drawing" not in S.get_model()  # once
    S.close_document("bracket")


def test_solidworks_drawing_placement(tmp_path, monkeypatch):
    """A .slddrw view record's 3x3 orientation, sheet position (m, from the lower left: where the model origin is)
    and scale, the sheet's (height, width) and the view's dimension texts: the recreated views sit where they
    did in SOLIDWORKS, at its scale, on its sheet size, dimensioned by value."""
    import struct
    from openswx import sw_drawing
    u = lambda s: b"\x00\x07" + s.encode("utf-16-le") + b"\x00\x00\x03"  # noqa: E731
    rec = lambda m, x, y, s: b"\x00" * 40 + struct.pack("<13d", *m, x, y, -0.025, s) + b"\x00" * 40  # noqa: E731
    front, top = (1, 0, 0, 0, 1, 0, 0, 0, 1), (1, 0, 0, 0, 0, 1, 0, -1, 0)
    definition = (struct.pack("<2d", 0.2159, 0.2794) + u("Sheet1") + u("Drawing View1") + u("*Front")
                  + rec(front, 0.06, 0.05, 0.5) + u("<Border type=14>100</Border>") + u("<Border type=14>20</Border>")
                  + u("<Border type=14>100</Border>") + u("Drawing View2") + rec(top, 0.06, 0.16, 0.5))
    monkeypatch.setattr(sw_drawing, "read_streams", lambda p: {"Contents/Definition": definition,
                                                                "SheetPreviews/SheetNames": b"\x01\x00" + u("Sheet1")})
    drw = sw_drawing.read_drawing(str(tmp_path / "plate.slddrw"))
    assert drw["sheet_size"] == "ANSI A"
    assert [(v["position"], v["scale"]) for v in drw["views"]] == [((60.0, 50.0), 0.5), ((60.0, 160.0), 0.5)]
    assert drw["views"][0]["dimensions"] == ["100", "20"]
    S.new_part("plate")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=20)
    S.save_part(str(tmp_path / "plate.oswprt"))
    S.close_document("plate")
    monkeypatch.setattr(sw_drawing, "find_model", lambda p, models: tmp_path / "plate.oswprt")
    (tmp_path / "plate.slddrw").write_bytes(b"")
    r = S.open_part(str(tmp_path / "plate.slddrw"))
    assert r["views"][0]["dimensions"] == {"placed": 2, "not_found": []}
    sh = S.APP.active_doc.drawing["sheets"][0]
    assert sh["size"] == "ANSI A" and sh["scale"] == 0.5
    views = [(v["kind"], v.get("orientation") or v.get("side"), round(v["x"], 1), round(v["y"], 1)) for v in sh["views"]]
    # the model origin (the plate's corner) at the stored points, y down on a 215.9 mm sheet
    assert views == [("model", "front", 85.0, 160.9), ("projected", "top", 85.0, 40.9)]
    assert sorted((d["kind"], round(d["b"][0] - d["a"][0] + d["b"][1] - d["a"][1], 6)) for d in sh["dims"]) == [
        ("horizontal", 100.0), ("vertical", 20.0)]
    with pytest.raises(S.CommandError):
        S.drawing_dimension_value("Drawing View1", "⌀12")  # no hole in the plate
    S.close_document("plate")


def test_dimension_by_value_concentric():
    """Dimension by value on concentric circles: each diameter stays on the circle of its size when the sheet
    redraws (not snapped to the innermost); a side-on diameter is the width across, its text kept."""
    S.new_part("Ring")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 20)
    S.sketch_circle(sk, [0, 0], 8)
    S.extrude(sk, depth=10)
    S.drawing_new(sheet="A3", scale=1.0, standard_views=False)
    top = S.drawing_add_view("top", 150, 150)["view"]["id"]
    front = S.drawing_add_view("front", 300, 150)["view"]["id"]
    assert S.drawing_dimension_value(top, "⌀40")["dimension"]["value"] == pytest.approx(40)
    assert S.drawing_dimension_value(top, "<MOD-DIAM>16")["dimension"]["value"] == pytest.approx(16)
    side = S.drawing_dimension_value(front, "⌀40")["dimension"]
    assert side["kind"] == "horizontal" and side["text"] == "⌀40"
    svg = S.create_drawing()["svg"]
    assert "⌀40<" in svg and "⌀16<" in svg
    S.close_document("Ring")


def test_drawing_dwg_export(tmp_path, monkeypatch):
    """DWG drawings go through the ODA File Converter: one DWG per sheet converted from the sheet's DXF; without
    the converter the error says what to install."""
    from openswx import dxf_io
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=10)
    S.drawing_new()
    S.drawing_add_sheet()
    converted = []

    def fake(dxf, dwg):  # stands in for the converter: the DXF it was given becomes the "DWG"
        assert open(dxf, encoding="utf-8", errors="ignore").read(200).strip()
        Path(dwg).write_bytes(Path(dxf).read_bytes())
        converted.append(dwg)
        return dwg
    monkeypatch.setattr(dxf_io, "dxf_to_dwg", fake)
    out = S.export_drawing(str(tmp_path / "d.dwg"))
    assert out["format"] == "dwg" and len(out["paths"]) == 2 and all(Path(q).exists() for q in out["paths"])
    monkeypatch.undo()
    from ezdxf.addons import odafc
    if not odafc.is_installed():
        with pytest.raises(S.CommandError, match="ODA File Converter"):
            S.export_drawing(str(tmp_path / "e.dwg"))


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


def test_file_associations():
    """register-file-types: .oswprt / .oswasm open in openswx with its icon, openswx is offered in Open with
    for SOLIDWORKS / STEP / Parasolid files without taking them over; unregister removes exactly that."""
    from openswx import fileassoc
    reg = _FakeReg()
    reg.CreateKey(reg.HKEY_CURRENT_USER, r"Software\Classes\.sldprt")  # SOLIDWORKS owns .sldprt
    with reg.OpenKey(reg.HKEY_CURRENT_USER, r"Software\Classes\.sldprt") as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, "SldWorks.Document.1")
    exe = r"C:\Program Files\openswx\openswx.exe"
    fileassoc.register(exe, reg=reg)
    K = reg.keys
    assert K[r"Software\Classes\.oswprt"][""] == "openswx.Part" and K[r"Software\Classes\.oswasm"][""] == "openswx.Assembly"
    assert K[r"Software\Classes\openswx.Part\shell\open\command"][""] == f'"{exe}" "%1"'
    assert K[r"Software\Classes\.sldprt"][""] == "SldWorks.Document.1"  # not taken over
    assert "openswx.File" in K[r"Software\Classes\.sldprt\OpenWithProgids"]
    fileassoc.unregister(reg=reg)
    assert not any("openswx" in p for p in K) and K[r"Software\Classes\.sldprt"][""] == "SldWorks.Document.1"
    assert "" not in K.get(r"Software\Classes\.oswprt", {}) and "openswx.File" not in K[r"Software\Classes\.sldprt\OpenWithProgids"]


def test_thickness_undercut_clearance_coupler_replace():
    """Thickness Analysis (a 2 mm shell), Undercut Detection (a side hole blocks a Y pull), Clearance
    Verification, the Linear Coupler mate (a dragged part leads) and Replace Components."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=30)
    top = [f["id"] for f in S.get_topology()["faces"] if f["type"] == "plane" and f["normal"][1] > 0.99]
    S.shell(2.0, top)
    r = S.thickness_analysis(3.0)
    assert r["min_thickness"] == pytest.approx(2.0, abs=1e-6) and r["thin_faces"]
    assert not S.thickness_analysis(1.5)["thin_faces"]

    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=30)
    s2 = S.create_sketch("Right")["sketch"]
    S.sketch_circle(s2, [30, 15], 5)
    S.cut_extrude(s2, end_condition="ThroughAllBoth")
    u = S.undercut_analysis("Y")
    hole = [f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder"]
    assert [k for k, v in u["faces"].items() if v["class"] == "undercut"] == hole
    assert u["counts"]["direction1"] == 1 and u["counts"]["direction2"] == 1

    S.new_part("CGround")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [-150, -30], [150, 0])
    S.extrude(sk, depth=40)
    S.new_part("CSlider")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [20, 10])
    S.extrude(sk, depth=10)
    S.new_part("CSlider2")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [30, 10])
    S.extrude(sk, depth=10)
    S.new_assembly("CAsm")
    S.insert_component("CGround")
    S.insert_component("CSlider", position=[0, 0.5, 0])
    S.insert_component("CSlider", position=[0, 20, 0])
    r = S.clearance_verification(1.0)
    assert [v["components"] for v in r["violations"]] == [["CGround-1", "CSlider-1"]] and r["closest"]["distance"] == pytest.approx(0.5)
    t = S.get_topology()
    face = lambda c: [f["id"] for f in t["faces"] if f["id"].startswith(c) and f["type"] == "plane" and f["normal"][0] > 0.99][0]  # noqa: E731
    S.add_mate("linear_coupler", face("CSlider-1"), face("CSlider-2"), value=2.0)
    x1, x2 = S.APP.asm.component("CSlider-1").x.copy(), S.APP.asm.component("CSlider-2").x.copy()
    S.move_component("CSlider-1", position=[10, 0.5, 0])
    assert S.APP.asm.component("CSlider-1").x[3] - x1[3] == pytest.approx(10)
    assert S.APP.asm.component("CSlider-2").x[3] - x2[3] == pytest.approx(20)
    v0 = S.APP.asm.body.volume
    r = S.replace_component("CSlider-2", "CSlider2")
    assert r["replaced"] == ["CSlider-2"] and S.APP.asm.body.volume - v0 == pytest.approx(10 * 10 * 10)
    for name in ("CAsm", "CSlider2", "CSlider", "CGround"):
        S.close_document(name)


def test_assembly_mates(tmp_path):
    block_part = block()  # Part with a 100x60x20 block
    del block_part
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    base = S.get_model()["name"]
    S.new_part("Pin")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, depth=40)
    S.new_assembly("Assem")
    S.insert_component(base)
    S.insert_component("Pin", position=[30, 50, 10], rotation_deg=[20, 0, 10])
    topo = S.get_topology()
    hole = [f["id"] for f in topo["faces"] if f["id"].startswith(base) and f["type"] == "cylinder"][0]
    pin_side = [f["id"] for f in topo["faces"] if f["id"].startswith("Pin") and f["type"] == "cylinder"][0]
    blk_top = [f["id"] for f in topo["faces"] if f["id"].startswith(base) and f["normal"][1] > 0.99][0]
    pin_bottom = [f["id"] for f in topo["faces"] if f["id"].startswith("Pin") and f["normal"][1] < -0.8][0]
    S.add_mate("concentric", hole, pin_side)
    r = S.add_mate("distance", blk_top, pin_bottom, value=5)  # faces point at each other (anti-aligned)
    model = r["assembly"]
    pin = [c for c in model["components"] if c["name"].startswith("Pin")][0]
    assert pin["position"][0] == pytest.approx(0, abs=1e-6) and pin["position"][2] == pytest.approx(0, abs=1e-6)
    assert pin["position"][1] == pytest.approx(25, abs=1e-6)  # pin bottom 5 mm above the block top (y=20)
    assert pin["dof"] == 1  # free to spin about its axis, as in SOLIDWORKS
    S.edit_mate("Distance2", value=-15)  # sink the pin 15 mm into the hole
    assert S.check_interference()["interferences"] == []
    pin = [c for c in S.get_model()["components"] if c["name"].startswith("Pin")][0]
    assert pin["position"][1] == pytest.approx(5, abs=1e-6)
    path = S.save_part(str(tmp_path / "asm.oswasm"))["saved"]
    S.open_part(path)
    assert S.get_model()["kind"] == "assembly" and len(S.get_model()["mates"]) == 2
    assert S.get_mass_properties()["volume"] == pytest.approx(120000 - PI * 25 * 20 + PI * 25 * 40)
    # editing the open part updates the assembly (linked component)
    S.activate_document("Pin")
    S.edit_feature("Boss-Extrude1", {"depth": 50})
    S.activate_document("Assem")
    assert S.get_mass_properties()["volume"] == pytest.approx(120000 - PI * 25 * 20 + PI * 25 * 50)


def test_tangent_lock_width_mates():
    block()  # Part1: 100 x 20 x 60 block, top face at y=20
    base = S.get_model()["name"]
    S.new_part("Rod")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, depth=30, end_condition="MidPlane")  # rod along X, radius 5
    S.new_part("Tab")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-20, -10], [20, 10])
    S.extrude(sk, depth=5)
    S.new_assembly("MateAsm")
    S.insert_component(base)
    S.insert_component("Rod", position=[10, 60, 5])
    S.insert_component("Tab", position=[37, 40, 3])
    topo = S.get_topology()["faces"]
    blk_top = [f["id"] for f in topo if f["id"].startswith(base) and f["normal"][1] > 0.99][0]
    rod_side = [f["id"] for f in topo if f["id"].startswith("Rod") and f["type"] == "cylinder"][0]
    S.add_mate("tangent", blk_top, rod_side)
    rod = [c for c in S.get_model()["components"] if c["name"].startswith("Rod")][0]
    assert rod["position"][1] == pytest.approx(25, abs=1e-6)  # axis 5 above the top face
    # width: centre the tab (x faces at +-20) between the block's x faces (+-50)
    bx = sorted((f for f in topo if f["id"].startswith(base) and abs(f["normal"][0]) > 0.99), key=lambda f: f["point"][0])
    tx = sorted((f for f in topo if f["id"].startswith("Tab") and abs(f["normal"][0]) > 0.99), key=lambda f: f["point"][0])
    S.add_mate("width", bx[0]["id"], tx[0]["id"], entity3=bx[1]["id"], entity4=tx[1]["id"])
    tab = [c for c in S.get_model()["components"] if c["name"].startswith("Tab")][0]
    assert tab["position"][0] == pytest.approx(0, abs=1e-6)
    # lock keeps a pose: moving the locked component is undone by the solver
    S.add_mate("lock", base + "-1", "Tab-1")
    before = [c for c in S.get_model()["components"] if c["name"] == "Tab-1"][0]["position"]
    S.move_component("Tab-1", position=[5, 90, 7])
    after = [c for c in S.get_model()["components"] if c["name"] == "Tab-1"][0]["position"]
    assert after == pytest.approx(before, abs=1e-6)


def test_sheet_metal_flange_and_flatten():
    S.new_part("Bracket")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 50])
    S.base_flange(sk, thickness=2, bend_radius=3, k_factor=0.5)
    assert vol() == pytest.approx(100 * 50 * 2)
    # top edge along X at the +X... pick the top (y=2) edge on the x=100 side (runs along Z)
    edge = [e["id"] for e in S.get_topology()["edges"] if e["type"] == "line" and abs(e["point"][0] - 100) < 1e-6
            and abs(e["point"][1] - 2) < 1e-6][0]
    S.edge_flange(edge, length=30, angle=90)
    t, r, L, w = 2, 3, 30, 50
    bend = PI / 2 / 2 * ((r + t) ** 2 - r ** 2) * w  # quarter annulus x width
    assert vol() == pytest.approx(100 * 50 * 2 + bend + L * t * w, rel=1e-9)
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["max"][0] == pytest.approx(100 + r + t) and bb["max"][1] == pytest.approx(2 + r + L)
    info = S.flatten(True)
    BA = PI / 2 * (r + 0.5 * t)
    assert info["bends"][0]["allowance"] == pytest.approx(BA)
    assert info["bounding_box"]["max"][0] == pytest.approx(100 + BA + L)  # developed length
    assert vol() == pytest.approx((100 + BA + L) * 50 * 2)
    S.flatten(False)
    assert vol() == pytest.approx(100 * 50 * 2 + bend + L * t * w, rel=1e-9)


def test_hem_and_sketched_bend():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 50])
    S.base_flange(sk, thickness=2, bend_radius=2, k_factor=0.5)
    edge = [e["id"] for e in S.get_topology()["edges"] if e["type"] == "line" and abs(e["point"][0] - 100) < 1e-6
            and abs(e["point"][1] - 2) < 1e-6][0]
    S.hem(edge, type="open", gap=4, length=15)
    t, r, w = 2, 2, 50
    assert vol() == pytest.approx(100 * 50 * 2 + PI / 2 * ((r + t) ** 2 - r ** 2) * w + 15 * t * w, rel=1e-9)
    # sketched bend conserves volume with K = 0.5 (BA = phi (r + t/2))
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 50])
    S.base_flange(sk, thickness=2, bend_radius=2, k_factor=0.5)
    S.reference_plane("Top", offset=2)
    bl = S.create_sketch("Plane1")["sketch"]
    S.sketch_line(bl, [50, -200], [50, 200])
    S.sketched_bend(bl, angle=90, fixed=[10, 1, 25])
    assert vol() == pytest.approx(100 * 50 * 2, rel=1e-6)
    BA = PI / 2 * (2 + 1)
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["max"][0] == pytest.approx(50 - BA / 2 + 2 + 2, abs=1e-6)
    assert bb["max"][1] == pytest.approx(2 + 2 + 50 - BA / 2, abs=1e-6)
    S.flatten(True)
    assert vol() == pytest.approx(100 * 50 * 2)


def test_more_surfaces():
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    ln = S.sketch_line(sk, [10, 0], [10, 20])
    rs = S.revolved_surface(sk, "Y")
    assert rs["info"]["surface_area"] == pytest.approx(2 * PI * 10 * 20, rel=1e-6)
    bot = S.create_sketch("Top")["sketch"]
    S.sketch_circle(bot, [0, 0], 10)
    S.planar_surface(bot)
    S.reference_plane("Top", offset=20)
    top = S.create_sketch("Plane1")["sketch"]
    S.sketch_circle(top, [0, 0], 10)
    S.planar_surface(top)
    S.knit_surface(["Surface-Revolve1", "Surface-Plane1", "Surface-Plane2"], form_solid=True)
    assert vol() == pytest.approx(PI * 100 * 20, rel=1e-6)
    S.new_part()
    r = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(r, [-5, 0], [5, 20])
    assert S.planar_surface(r)["info"]["surface_area"] == pytest.approx(200)
    assert S.offset_surface(5, surface="Surface-Plane1")["info"]["surface_area"] == pytest.approx(200)
    assert S.trim_surface("Surface-Plane1", "Right", keep=[1, 0, 0])["info"]["surface_area"] == pytest.approx(100)
    a = S.create_sketch("Front")["sketch"]
    S.sketch_circle(a, [0, 0], 10)
    S.reference_plane("Front", offset=30)
    b = S.create_sketch("Plane1")["sketch"]
    S.sketch_circle(b, [0, 0], 10)
    assert S.lofted_surface([a, b])["info"]["surface_area"] == pytest.approx(2 * PI * 10 * 30, rel=1e-4)
    S.create_3d_sketch([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], closed=True)
    assert S.filled_surface("3DSketch1")["info"]["surface_area"] == pytest.approx(100, rel=1e-4)


def test_boundary_surface():
    S.new_part()
    S.create_3d_sketch([[0, 0, 0], [10, 0, 0]])
    S.create_3d_sketch([[0, 10, 0], [10, 10, 0]])
    S.create_3d_sketch([[0, 0, 0], [0, 10, 0]])
    S.create_3d_sketch([[10, 0, 0], [10, 10, 0]])
    r = S.boundary_surface(["3DSketch1", "3DSketch2"], ["3DSketch3", "3DSketch4"])
    assert r["info"]["surface_area"] == pytest.approx(100, rel=1e-5)
    # a twisted (non-planar) quad: the patch still meets all four edges, and is larger than its projection
    S.create_3d_sketch([[0, 0, 0], [10, 0, 5]])
    S.create_3d_sketch([[0, 10, 5], [10, 10, 0]])
    S.create_3d_sketch([[0, 0, 0], [0, 10, 5]])
    S.create_3d_sketch([[10, 0, 5], [10, 10, 0]])
    r = S.boundary_surface(["3DSketch5", "3DSketch6"], ["3DSketch7", "3DSketch8"])
    assert r["info"]["surface_area"] > 100
    with pytest.raises(S.CommandError):
        S.boundary_surface(["3DSketch1"], ["3DSketch3"])  # not a closed loop


def test_structural_member_profile_sketch():
    """A structural member with a profile sketch (a 10 x 10 bar) on two of a square frame's sides, mitred, one
    body per member; the mitre keeps the volume of the sides' lengths."""
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    lines = [S.sketch_line(sk, a, b)["result"]["entity"] for a, b in (([0, 0], [100, 0]), ([100, 0], [100, 80]),
                                                            ([100, 80], [0, 80]), ([0, 80], [0, 0]))]
    prof = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(prof, [-5, -5], [5, 5])
    S.structural_member(sk, profile="sketch", profile_sketch=prof, segments=lines[:2], merge=False)
    assert vol() == pytest.approx(10 * 10 * (100 + 80), rel=1e-6)
    assert len(S.APP.doc.body.solids()) == 2


def test_structural_member_3d_path():
    """A structural member along a 3D sketch (a diagonal and a vertical line): one 10 x 10 bar per line."""
    import math
    S.new_part()
    S.create_3d_sketch([[0, 0, 0], [30, 40, 0], [30, 40, -60]], name="Path3D")
    prof = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(prof, [-5, -5], [5, 5])
    S.structural_member("Path3D", profile="sketch", profile_sketch=prof, corner="none", merge=False)
    assert vol() == pytest.approx(100 * (50 + 60), rel=1e-6)


def test_structural_member_trim():
    """A second member group trimmed back by the first: crossing 10 x 10 bars, the second cut where the
    first runs, so the bodies do not overlap."""
    S.new_part()
    a = S.create_sketch("Front")["sketch"]
    S.sketch_line(a, [-50, 0], [50, 0])
    S.structural_member(a, profile="solid_bar", width=10, merge=False, corner="none")
    b = S.create_sketch("Front")["sketch"]
    S.sketch_line(b, [0, -50], [0, 50])
    feat = S.structural_member(b, profile="solid_bar", width=10, merge=False, corner="none")["feature"]
    S.APP.doc.edit_feature(feat, {"trim": True})
    assert vol() == pytest.approx(100 * 100 + 100 * 90, rel=1e-6)


def test_end_cap_and_gusset():
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_line(sk, [0, 0], [100, 0])
    S.structural_member(sk, profile="square_tube", width=20, thickness=2)
    v0 = vol()
    S.end_cap("Structural Member1" if "Structural Member1" in [f["name"] for f in S.get_model()["features"]]
              else [f["name"] for f in S.get_model()["features"] if f["type"] == "structural_member"][0], thickness=3)
    assert vol() == pytest.approx(v0 + 2 * 16 * 16 * 3)
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    pts = [[0, 0], [60, 0], [60, 10], [10, 10], [10, 60], [0, 60]]
    for i in range(6):
        S.sketch_line(sk, pts[i], pts[(i + 1) % 6])
    S.extrude(sk, depth=40)
    v0 = vol()
    faces = S.get_topology()["faces"]
    f1 = [f["id"] for f in faces if f["normal"][1] > 0.99 and abs(f["point"][1] - 10) < 1e-6][0]
    f2 = [f["id"] for f in faces if f["normal"][0] > 0.99 and abs(f["point"][0] - 10) < 1e-6][0]
    S.gusset(f1, f2, d1=20, d2=20, thickness=3)
    assert vol() == pytest.approx(v0 + 0.5 * 20 * 20 * 3)


def test_evaluate_tools():
    block()  # 100 x 60 x 20, extruded +Y from the Top plane
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    da = S.draft_analysis("Y", angle=1)
    c = da["counts"]
    assert c["positive"] == 1 and c["negative"] == 1 and c["requires_draft"] == 5  # top, bottom, 4 sides + hole wall
    cv = S.curvature_analysis()
    hole_face = [f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder"][0]
    assert max(cv["faces"][hole_face]) == pytest.approx(1 / 5, rel=1e-4)  # 1 / radius
    assert cv["max"] == pytest.approx(0.2, rel=1e-4)
    sec = S.section_view("Front")  # z = 0 through the hole
    assert sec["area"] == pytest.approx(100 * 20 - 10 * 20)
    assert S.section_properties("Right", offset=30)["area"] == pytest.approx(60 * 20)
    chk = S.check_geometry()
    assert chk["valid"] and chk["solids"] == 1 and chk["min_radius"] == pytest.approx(5)
    S.new_part()
    s1 = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(s1, [0, 0], [10, 10])
    S.extrude(s1, depth=10)
    s2 = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(s2, [5, 5], [15, 15])
    S.extrude(s2, depth=10, merge=False)
    bi = S.body_interference()
    assert bi["bodies"] == 2 and bi["interferences"][0]["volume"] == pytest.approx(5 * 5 * 10)


def test_linear_static_simulation():
    S.new_part("Beam")
    sk = S.create_sketch("Right")["sketch"]  # cross-section in the YZ plane, extruded along +X
    S.sketch_rectangle(sk, [-5, 0], [5, 10])
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")  # E = 200 GPa, nu = 0.29, yield 351.6 MPa
    faces = S.get_topology()["faces"]
    root = [f["id"] for f in faces if f["normal"][0] < -0.99][0]
    tip = [f["id"] for f in faces if f["normal"][0] > 0.99][0]
    with pytest.raises(S.CommandError):
        S.simulation_run()  # no fixtures yet
    S.simulation_add_fixture(root)
    S.simulation_add_force(tip, force=[0, -100, 0])
    r = S.simulation_run(element_size=2.5)
    # Euler-Bernoulli + Timoshenko shear: F L^3 / (3 E I) + F L / (k G A)
    E, nu, I = 200e3, 0.29, 10 * 10 ** 3 / 12
    delta = 100 * 100 ** 3 / (3 * E * I) + 100 * 100 / (5 / 6 * E / (2 * (1 + nu)) * 100)
    assert r["max_displacement"] == pytest.approx(delta, rel=0.02)
    assert r["reaction"][1] == pytest.approx(100, rel=1e-6)
    assert 35 < r["max_von_mises"] < 70  # 60 MPa at the root fibre; element centres sit inside it
    assert r["factor_of_safety"] == pytest.approx(351.6 / r["max_von_mises"])
    res = S.simulation_results()
    assert set(res["stress"]) == {f["id"] for f in faces} and max(max(v) for v in res["stress"].values()) > 30
    # pressure on the top face: total force p * A
    S.simulation_clear(fixtures=False)
    top = [f["id"] for f in faces if f["normal"][1] > 0.99][0]
    S.simulation_add_pressure(top, 0.1)  # 0.1 MPa over 100 x 10 = 100 N downward
    r2 = S.simulation_run(element_size=2.5)
    assert r2["reaction"][1] == pytest.approx(100, rel=1e-6)
    assert r2["max_displacement"] < r["max_displacement"]  # distributed load bends less than a tip load


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


def test_vertex_chamfer_face_and_full_round_fillets_wrap():
    block()  # x -50..50, y 0..20, z -30..30
    S.vertex_chamfer([50, 20, 30], 5)
    assert vol() == pytest.approx(120000 - 5 ** 3 / 6)
    S.vertex_chamfer([-50, 20, 30], 4, 5, 6)
    assert vol() == pytest.approx(120000 - 5 ** 3 / 6 - 4 * 5 * 6 / 6)
    block()
    faces = S.get_topology()["faces"]
    top = [f["id"] for f in faces if f["normal"][1] > 0.99][0]
    xp = [f["id"] for f in faces if f["normal"][0] > 0.99][0]
    S.face_fillet([top], [xp], radius=4)
    assert vol() == pytest.approx(120000 - (16 - PI * 16 / 4) * 60)
    # full round on a 10 mm wide rib: top face replaced by a half cylinder of radius 5
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [10, 30])
    S.extrude(sk, depth=50)
    faces = S.get_topology()["faces"]
    top = [f["id"] for f in faces if f["normal"][1] > 0.99][0]
    left = [f["id"] for f in faces if f["normal"][0] < -0.99][0]
    right = [f["id"] for f in faces if f["normal"][0] > 0.99][0]
    S.full_round_fillet(left, top, right)
    assert vol() == pytest.approx(10 * 30 * 50 - 10 * 5 * 50 + PI * 25 / 2 * 50)
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["max"][1] == pytest.approx(30)
    # wrap: deboss a 10 x 10 square 1 mm into a R20 cylinder
    S.new_part()
    c = S.create_sketch("Top")["sketch"]
    S.sketch_circle(c, [0, 0], 20)
    S.extrude(c, depth=40)
    S.reference_plane("Front", offset=20)
    w = S.create_sketch("Plane1")["sketch"]
    S.sketch_rectangle(w, [-5, 15], [5, 25])
    cyl = [f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder"][0]
    v0 = vol()
    S.wrap(w, cyl, type="deboss", depth=1)
    assert vol() == pytest.approx(v0 - 0.5 * (20 ** 2 - 19 ** 2) * (10 / 20) * 10, rel=1e-4)
    S.wrap(w, cyl, type="emboss", depth=1)  # the second wrap fills the pocket back in and raises 1 mm
    assert vol() > v0


def test_symmetric_slot_limit_mates():
    S.new_part("Base")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [10, 10])
    S.extrude(sk, depth=10)
    S.new_part("Cube5")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [5, 5])
    S.extrude(sk, depth=5)
    S.new_part("Pin")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 2)
    S.extrude(sk, depth=8)
    S.new_assembly("Mates2")
    S.insert_component("Base")
    S.insert_component("Cube5", position=[30, 2, 3])
    topo = S.get_topology()["faces"]
    f = lambda c, ax, sgn: [x["id"] for x in topo if x["id"].startswith(c) and x["normal"][ax] * sgn > 0.99][0]  # noqa: E731
    # Base -z face (z = 0) mirrored in the Base +z face plane (z = 10) lands at z = 20: Cube5's +z face goes there
    S.add_mate("symmetric", f("Base", 2, -1), f("Cube5", 2, 1), entity3=f("Base", 2, 1))
    c5 = [c for c in S.get_model()["components"] if c["name"] == "Cube5-1"][0]
    assert c5["position"][2] == pytest.approx(15, abs=1e-6)
    # limit distance: Cube5's -x face kept 5..15 mm from Base's +x face (it starts 20 away)
    S.add_mate("limit_distance", f("Base", 0, 1), f("Cube5", 0, -1), value=5, value2=15)
    c5 = [c for c in S.get_model()["components"] if c["name"] == "Cube5-1"][0]
    assert 10 + 5 - 1e-6 <= c5["position"][0] <= 10 + 15 + 1e-6  # anywhere inside the limits
    # slot: the pin's axis centred between Base's two x faces (x = 5), still free along the slot
    S.insert_component("Pin", position=[30, 0, 30])
    topo = S.get_topology()["faces"]
    pin = [x["id"] for x in topo if x["id"].startswith("Pin") and x["type"] == "cylinder"][0]
    S.add_mate("slot", f("Base", 0, -1), pin, entity3=f("Base", 0, 1))
    p = [c for c in S.get_model()["components"] if c["name"] == "Pin-1"][0]
    assert p["position"][0] == pytest.approx(5, abs=1e-6) and p["dof"] >= 2


def test_cam_and_path_mates():
    S.new_part("Track")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 10])
    S.extrude(sk, depth=10)
    S.new_part("CamDisc")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_circle(sk, [0, 0], 20)
    S.extrude(sk, depth=5)
    S.new_part("Roller")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_circle(sk, [0, 0], 2)
    S.extrude(sk, depth=5)
    S.new_assembly("CamPath")
    S.insert_component("CamDisc")
    S.insert_component("Roller", position=[40, 3, 0.5])
    topo = S.get_topology()["faces"]
    cam = [f["id"] for f in topo if f["id"].startswith("CamDisc") and f["type"] == "cylinder"][0]
    rol = [f["id"] for f in topo if f["id"].startswith("Roller") and f["type"] == "cylinder"][0]
    S.add_mate("cam", cam, rol)
    p = [c for c in S.get_model()["components"] if c["name"] == "Roller-1"][0]["position"]
    assert math.hypot(p[0], p[1]) == pytest.approx(22, abs=1e-5)  # roller rides on the R20 cam: axes 20 + 2 apart
    # path: the roller's axis point follows the top-front edge of the track (y = 10, z = 10)
    S.new_assembly("PathAsm")
    S.insert_component("Track")
    S.insert_component("Roller", position=[20, 30, 30])
    edges = S.get_topology()["edges"]
    path = [e["id"] for e in edges if e["id"].startswith("Track") and e["type"] == "line"
            and abs(e["point"][1] - 10) < 1e-6 and abs(e["point"][2] - 10) < 1e-6][0]
    rol = [f["id"] for f in S.get_topology()["faces"] if f["id"].startswith("Roller") and f["type"] == "cylinder"][0]
    S.add_mate("path", path, rol)
    p = [c for c in S.get_model()["components"] if c["name"] == "Roller-1"][0]["position"]
    assert p[1] == pytest.approx(10, abs=1e-5) and p[2] in (pytest.approx(10, abs=1e-5), pytest.approx(5, abs=1e-5))


def test_jog():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 50])
    S.base_flange(sk, thickness=2, bend_radius=2, k_factor=0.5)
    S.reference_plane("Top", offset=2)
    bl = S.create_sketch("Plane1")["sketch"]
    S.sketch_line(bl, [40, -200], [40, 200])
    r = S.jog(bl, distance=20, fixed=[10, 1, 25])
    BA = PI / 2 * (2 + 1)
    assert vol() == pytest.approx(100 * 50 * 2, rel=1e-6)  # K = 0.5: material conserved
    rise = 20 - BA + 2 * 2 + 2  # centreline: two quarter arcs (r + t/2) and the straight between them
    assert r["info"]["offset"] == pytest.approx(rise, abs=1e-6)
    tops = sorted({round(f["point"][1], 6) for f in S.get_topology()["faces"] if f["normal"][1] > 0.99})
    assert tops[0] == pytest.approx(2) and tops[-1] == pytest.approx(2 + rise)
    S.flatten(True)
    assert vol() == pytest.approx(100 * 50 * 2)


def test_corner_relief_dimple_miter_flange():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 50])
    S.base_flange(sk, thickness=2, bend_radius=2)
    corner = [0, 1, 0]  # a plate corner (any point along the corner works: the cut spans the sheet)
    S.corner_relief(corner, 10)
    assert vol() == pytest.approx(10000 - 5 * 5 * 2)  # a quarter of the 10 x 10 relief overlaps the plate
    S.corner_relief([100, 1, 0], 6, type="circular")
    assert vol() == pytest.approx(10000 - 50 - PI * 9 / 4 * 2)
    top = [f["id"] for f in S.get_topology()["faces"] if f["normal"][1] > 0.99][0]
    S.forming_tool_dimple(top, [50, 2, -25], diameter=20, height=5)
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["max"][1] == pytest.approx(2 + 5, abs=1e-6) and S.check_geometry()["valid"]
    # miter flange: a 15 mm upward line profile at the x = 100 edge's end, run along the 50 mm edge
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 50])
    S.base_flange(sk, thickness=2)
    edge = [e for e in S.get_topology()["edges"] if e["type"] == "line" and abs(e["point"][0] - 100) < 1e-6
            and abs(e["point"][1] - 2) < 1e-6][0]
    z0 = min(edge["point"][2] - 25, edge["point"][2] + 25)
    S.reference_plane("Front", offset=0)
    prof = S.create_sketch("Plane1")["sketch"]
    S.sketch_line(prof, [100, 2], [100, 17])
    v0 = vol()
    S.miter_flange(prof, edge["id"], flip=True)
    assert vol() == pytest.approx(v0 + 15 * 2 * 50)


def test_multi_edge_miter_flange_forming_tools_auto_relief():
    def plate():
        S.new_part()
        sk = S.create_sketch("Top")["sketch"]
        S.sketch_rectangle(sk, [0, 0], [100, 50])
        S.base_flange(sk, thickness=2, bend_radius=2)
        return S.get_topology()["edges"]
    edges = plate()
    top = [e for e in edges if e["type"] == "line" and abs(e["point"][1] - 2) < 1e-6]
    e1 = [e for e in top if abs(e["point"][0] - 100) < 1e-6][0]  # runs along z at x = 100
    zs = sorted({round(e["point"][2], 6) for e in top if abs(e["point"][0] - 50) < 1e-6})
    z_far = min(zs, key=lambda z: abs(z - (e1["point"][2] + 25 if e1["point"][2] < 0 else e1["point"][2] - 25)))
    e2 = [e for e in top if abs(e["point"][0] - 50) < 1e-6 and abs(e["point"][2] - z_far) < 1e-6][0]
    z_near = e1["point"][2] * 2 - z_far  # the other end of edge 1
    S.reference_plane("Front", offset=z_near)
    prof = S.create_sketch("Plane1")["sketch"]
    S.sketch_line(prof, [100, 2], [100, 17])
    v0 = vol()
    r = S.miter_flange(prof, edges=[e1["id"], e2["id"]], flip=False)  # inside the plate outline
    assert vol() == pytest.approx(v0 + 15 * (2 * 50 + 2 * 100 - 2 * 2), rel=1e-6)  # mitred union of the two strips
    assert S.check_geometry()["valid"]
    S.edit_feature(r["feature"], {"flip": True})  # outside: the strips meet with the corner square filled
    assert vol() == pytest.approx(v0 + 15 * (2 * 50 + 2 * 100 + 2 * 2), rel=1e-6)
    # forming tools
    plate()
    topf = [f["id"] for f in S.get_topology()["faces"] if f["normal"][1] > 0.99][0]
    S.forming_tool_louver(topf, [30, 2, -25], length=30, width=6, height=4)
    assert S.get_mass_properties()["bounding_box"]["max"][1] == pytest.approx(2 + 4 + 2, abs=1e-6)
    topf = [f for f in S.get_topology()["faces"] if f["normal"][1] > 0.99 and abs(f["point"][1] - 2) < 1e-6][0]["id"]
    S.forming_tool_lance(topf, [75, 2, -25], length=20, width=6, height=3)
    assert S.check_geometry()["valid"]
    # automatic corner relief between two edge flanges sharing a corner
    edges = plate()
    top = [e for e in edges if e["type"] == "line" and abs(e["point"][1] - 2) < 1e-6]
    S.edge_flange([e for e in top if abs(e["point"][0] - 100) < 1e-6][0]["id"], length=20)
    edges = S.get_topology()["edges"]
    z_edge = [e for e in edges if e["type"] == "line" and abs(e["point"][1] - 2) < 1e-6 and abs(e["point"][0] - 50) < 1e-6][0]
    S.edge_flange(z_edge["id"], length=20)
    v1 = vol()
    r = S.corner_relief_auto()
    assert r["info"]["corners"] >= 1 and vol() < v1 and S.check_geometry()["solids"] == 1


def test_weldment_miter_corners():
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 100])
    S.structural_member(sk, profile="square_tube", width=20, thickness=2)
    # mitered closed frame: section area x centreline length, outer size 100 + 20
    assert vol() == pytest.approx((20 * 20 - 16 * 16) * 400, rel=1e-6)
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["max"][0] - bb["min"][0] == pytest.approx(120) and bb["max"][1] - bb["min"][1] == pytest.approx(120)
    assert S.check_geometry()["valid"]


def test_drawing_units():
    block()
    assert ">100<" in S.create_drawing()["svg"]
    S.set_units("in")
    svg = S.create_drawing()["svg"]
    assert ">3.937<" in svg and ">100<" not in svg and ">in<" in svg  # 100 mm overall width, units row
    S.set_units("mm")


def test_thermal_study():
    S.new_part("Bar")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(sk, [-5, 0], [5, 10])
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")  # k = 47 W/(m K)
    faces = S.get_topology()["faces"]
    a = [f["id"] for f in faces if f["normal"][0] < -0.99][0]
    b = [f["id"] for f in faces if f["normal"][0] > 0.99][0]
    S.thermal_add_temperature(a, 20)
    S.thermal_add_heat_power(b, 10)
    r = S.thermal_run(element_size=2.5)
    assert r["max_temperature"] == pytest.approx(20 + 10 * 100 / (0.047 * 100), rel=1e-6)  # 1D conduction
    assert r["heat_through_fixed_faces"] == pytest.approx(10, rel=1e-6)
    res = S.thermal_results()
    assert max(max(v) for v in res["temperature"].values()) == pytest.approx(r["max_temperature"], rel=1e-3)
    S.thermal_clear()
    S.thermal_add_temperature(a, 100)
    S.thermal_add_convection(b, 1000, ambient=20)
    r = S.thermal_run(element_size=2.5)
    R1, R2 = 100 / (0.047 * 100), 1 / (1000e-6 * 100)
    assert r["min_temperature"] == pytest.approx(100 - 80 / (R1 + R2) * R1, rel=1e-6)  # series resistances


def test_assembly_bonded_multimaterial_simulation():
    for name, mat in (("SteelBar", "AISI 1020"), ("AluBar", "6061-T6 (SS)")):
        S.new_part(name)
        sk = S.create_sketch("Right")["sketch"]
        S.sketch_rectangle(sk, [-5, 0], [5, 10])
        S.extrude(sk, depth=50)
        S.set_material(mat)
    S.new_assembly("Bonded")
    S.insert_component("SteelBar")
    S.insert_component("AluBar", position=[50, 0, 0], fixed=True)
    faces = S.get_topology()["faces"]
    root = [f["id"] for f in faces if f["id"].startswith("SteelBar") and f["normal"][0] < -0.99][0]
    tip = [f["id"] for f in faces if f["id"].startswith("AluBar") and f["normal"][0] > 0.99][0]
    S.simulation_add_fixture(root)
    S.simulation_add_force(tip, force=[1000, 0, 0])
    r = S.simulation_run(element_size=2.5)
    expect = 1000 * 50 / (200e3 * 100) + 1000 * 50 / (69e3 * 100)  # springs in series
    assert r["bonded_components"] == 2 and r["max_displacement"] == pytest.approx(expect, rel=0.03)
    assert r["reaction"][0] == pytest.approx(-1000, rel=1e-6)


def test_frequency_study():
    S.new_part("Beam")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(sk, [-5, 0], [5, 10])
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")  # E 200 GPa, rho 7900
    root = [f["id"] for f in S.get_topology()["faces"] if f["normal"][0] < -0.99][0]
    S.simulation_add_fixture(root)
    r = S.frequency_run(modes=3, element_size=2.5)
    EI, rA = 200e3 * 10 ** 4 / 12, 7900e-12 * 100
    f1 = 1.875 ** 2 / (2 * PI) * math.sqrt(EI / (rA * 100 ** 4))
    assert r["frequencies_hz"][0] == pytest.approx(f1, rel=0.01) and r["frequencies_hz"][1] == pytest.approx(f1, rel=0.01)
    assert r["mass_kg"] == pytest.approx(0.079, rel=1e-6)
    sh = S.frequency_mode_shape(1)
    assert max(abs(c) for v in sh["displacement"].values() for d in v for c in d) == pytest.approx(1, abs=0.05)


def test_buckling_study():
    S.new_part("Column")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(sk, [-5, 0], [5, 10])
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    S.simulation_add_fixture([f["id"] for f in faces if f["normal"][0] < -0.99][0])
    S.simulation_add_force([f["id"] for f in faces if f["normal"][0] > 0.99][0], force=[-1000, 0, 0])
    r = S.buckling_run(modes=2, element_size=2.5)
    euler = PI ** 2 * 200e3 * 10 ** 4 / 12 / (4 * 100 ** 2) / 1000  # fixed-free column, per 1 kN
    assert r["load_factors"][0] == pytest.approx(euler, rel=0.01)
    assert S.buckling_mode_shape(1)["load_factor"] == r["load_factors"][0]


def test_spring_and_bolt_connectors():
    S.new_part("Pair")
    s1 = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(s1, [-5, 0], [5, 10])
    S.extrude(s1, depth=10)
    S.reference_plane("Right", offset=20)
    s2 = S.create_sketch("Plane1")["sketch"]
    S.sketch_rectangle(s2, [-5, 0], [5, 10])
    S.extrude(s2, depth=10, merge=False)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    xs = lambda x, sgn: [f["id"] for f in faces if f["normal"][0] * sgn > 0.99 and abs(f["point"][0] - x) < 1e-6][0]  # noqa: E731
    S.simulation_add_fixture(xs(0, -1))
    S.simulation_add_force(xs(30, 1), force=[100, 0, 0])
    S.simulation_add_spring(xs(10, 1), xs(20, -1), normal_stiffness=1000)
    r = S.simulation_run(element_size=1.0)
    assert r["max_displacement"] == pytest.approx(100 / 1000 + 2 * 100 * 10 / (200e3 * 100), rel=0.01)
    # bolt: two plates 10 mm apart joined through coaxial 8 mm holes, pulled apart
    S.new_part("Plates")
    for off, merge in ((0, True), (15, False)):
        plane = S.reference_plane("Top", offset=off)["feature"] if off else "Top"
        sk = S.create_sketch(plane)["sketch"]
        S.sketch_rectangle(sk, [-20, -20], [20, 20])
        S.sketch_circle(sk, [0, 0], 4)
        S.extrude(sk, depth=5, merge=merge)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    holes = sorted((f for f in faces if f["type"] == "cylinder"), key=lambda f: f["point"][1])
    bottom = [f["id"] for f in faces if f["normal"][1] < -0.99 and abs(f["point"][1]) < 1e-6][0]
    top = [f["id"] for f in faces if f["normal"][1] > 0.99 and abs(f["point"][1] - 20) < 1e-6][0]
    S.simulation_add_fixture(bottom)
    S.simulation_add_force(top, force=[0, 1000, 0])
    S.simulation_add_bolt(holes[0]["id"], holes[-1]["id"])
    r = S.simulation_run(element_size=1.5)
    kn = 200e3 * PI * 16 / 15  # E A / L, grip between hole centres
    assert 1000 / kn < r["max_displacement"] < 50 * 1000 / kn  # bolt stretch plus plate bending


def test_no_penetration_contact():
    S.new_part("Gap")
    s1 = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(s1, [-5, 0], [5, 10])
    S.extrude(s1, depth=10)
    S.reference_plane("Right", offset=10.05)
    s2 = S.create_sketch("Plane1")["sketch"]
    S.sketch_rectangle(s2, [-5, 0], [5, 10])
    S.extrude(s2, depth=10, merge=False)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    xs = lambda x, sgn: [f["id"] for f in faces if f["normal"][0] * sgn > 0.99 and abs(f["point"][0] - x) < 1e-6][0]  # noqa: E731
    S.simulation_add_fixture(xs(0, -1))
    S.simulation_add_force(xs(20.05, 1), force=[-1000, 0, 0])  # push the free block onto the fixed one
    S.simulation_add_contact(xs(10, 1), xs(10.05, -1))
    r = S.simulation_run(element_size=1.0)
    assert r["contact_pairs_closed"] > 0 and r["reaction"][0] == pytest.approx(1000, rel=1e-6)
    ux = max(abs(u[0]) for u in S.APP.sim_result[1]["_grid"]["U"])
    assert ux == pytest.approx(0.05 + 2 * 1000 * 10 / (200e3 * 100), rel=0.01)  # close the gap, then compress both


def test_simulation_curved_boundary_accuracy():
    # round cantilever: the finite-cell method integrates cut voxels over their inside part only
    S.new_part("Rod")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    root = [f["id"] for f in faces if f["normal"][0] < -0.99][0]
    tip = [f["id"] for f in faces if f["normal"][0] > 0.99][0]
    S.simulation_add_fixture(root)
    S.simulation_add_force(tip, force=[0, -100, 0])
    r = S.simulation_run(element_size=2.0)
    E, nu = 200e3, 0.29
    I, A = PI * 10 ** 4 / 64, PI * 25
    delta = 100 * 100 ** 3 / (3 * E * I) + 100 * 100 / (0.9 * E / (2 * (1 + nu)) * A)
    assert r["max_displacement"] == pytest.approx(delta, rel=0.03)
    assert r["mesh_volume"] == pytest.approx(PI * 25 * 100, rel=0.02)


def test_smart_fasteners():
    block()  # 100 x 60 x 20 plate, top at y = 20
    S.hole(top_face(), [[-30, 0], [30, 0]], diameter=9, end_condition="ThroughAll")  # M8 clearance holes
    part = S.get_model()["name"]
    S.new_assembly("Bolted")
    plate = S.insert_component(part)["component"]
    r = S.smart_fasteners(plate)
    fs = r["fasteners"]
    assert len(fs) == 2 and {f["size"] for f in fs} == {"M8"} and {f["length"] for f in fs} == {30}
    comps = {c["name"]: c for c in r["assembly"]["components"]}
    xs = sorted(round(comps[f["component"]]["position"][0], 6) for f in fs)
    assert xs == pytest.approx([-30, 30], abs=1e-5)
    assert all(comps[f["component"]]["position"][1] == pytest.approx(20, abs=1e-5) for f in fs)  # heads seated on top
    assert r["assembly"]["state"] != "over_defined"


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


def test_surface_extrude_and_thicken():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    end = S.sketch_line(sk, [0, 0], [40, 0])["result"]["points"][1]
    S.sketch_line(sk, end, [40, 30])  # open L-shaped profile
    S.extruded_surface(sk, depth=20)
    assert vol() == 0  # a surface body has no volume
    S.thicken("Surface-Extrude1", 2)
    assert vol() == pytest.approx(40 * 2 * 20 + (30 - 2) * 2 * 20)  # wall thickened to the inside of the corner
    assert S.get_model()["features"][-1]["name"] == "Thicken1"


def test_weldment_and_gear_mate():
    S.new_part("Frame")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [200, 100])
    S.structural_member(sk, profile="square_tube", width=20, thickness=2)
    assert S.get_mass_properties()["bodies"] == 1 and vol() > 0
    cl = S.weldment_cut_list()
    assert cl["total_members"] == 4
    assert {(i["length"], i["qty"]) for i in cl["items"]} == {(200, 2), (100, 2)}
    assert cl["items"][0]["description"] == "SQ TUBE 20 x 20 x 2"
    # gears: two discs on fixed-position axes, ratio 2 (B has twice the teeth)
    S.new_part("GearA")
    s = S.create_sketch("Front")["sketch"]
    S.sketch_circle(s, [0, 0], 10)
    S.extrude(s, depth=5)
    S.new_part("GearB")
    s = S.create_sketch("Front")["sketch"]
    S.sketch_circle(s, [0, 0], 20)
    S.extrude(s, depth=5)
    S.new_assembly("Gears")
    S.insert_component("GearA")
    S.insert_component("GearB", position=[30, 0, 0])
    topo = S.get_topology()["faces"]
    ca = [f["id"] for f in topo if f["id"].startswith("GearA") and f["type"] == "cylinder"][0]
    cb = [f["id"] for f in topo if f["id"].startswith("GearB") and f["type"] == "cylinder"][0]
    S.add_mate("gear", ca, cb, value=2)
    S.fix_component("GearA-1", False)
    m = S.rotate_component("GearA-1", [0, 0, 1], 60)
    b = [c for c in m["components"] if c["name"] == "GearB-1"][0]
    assert b["rotation_deg"][2] == pytest.approx(-30, abs=1e-4)  # 60 deg on A -> -30 deg on B


def test_b1_rib_hole_wizard_thread_bodies():
    # rib: L-bracket (two plates) braced by a diagonal line in the Front plane
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [60, 5])
    S.sketch_rectangle(sk, [0, 5], [5, 60])
    S.extrude(sk, depth=20, end_condition="MidPlane")
    v0 = vol()
    rs = S.create_sketch("Front")["sketch"]
    S.sketch_line(rs, [20, 5], [5, 20])
    S.rib(rs, thickness=4)
    assert vol() - v0 == pytest.approx(0.5 * 15 * 15 * 4, rel=1e-6)  # triangular web, 4 thick
    # Hole Wizard ISO sizes
    block()
    S.hole_wizard(top_face(), [[0, 0]], size="M6", hole_type="counterbore")
    assert vol() == pytest.approx(120000 - PI * 3.3 ** 2 * 20 - PI * (5.5 ** 2 - 3.3 ** 2) * 6.4)
    # thread on a shaft
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 4)
    S.extrude(sk, depth=20)
    v_shaft = vol()
    cyl = [f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder"][0]
    S.thread(cyl, pitch=1.25, length=12)
    assert 0 < v_shaft - vol() < PI * (4 ** 2 - 3.2 ** 2) * 12  # material removed, bounded by the thread depth
    # bodies: copy apart (2 bodies), combine-subtract after moving the copy to overlap, delete, scale
    block()
    S.move_copy_body([0], translate=[200, 0, 0], copy=True)
    assert S.get_mass_properties()["bodies"] == 2 and vol() == pytest.approx(240000)
    S.move_copy_body([1], translate=[-150, 0, 0])  # copy now overlaps the first block by 50 x 60 x 20
    S.combine_bodies(0, [1], mode="subtract")
    assert S.get_mass_properties()["bodies"] == 1 and vol() == pytest.approx(120000 - 50 * 60 * 20)
    S.scale_body(2)
    assert vol() == pytest.approx((120000 - 50 * 60 * 20) * 8)


def test_b1_dome_and_ansi_holes():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 20)
    S.extrude(sk, depth=10)
    v0 = vol()
    top = [f["id"] for f in S.get_topology()["faces"] if f["normal"][1] > 0.99][0]
    S.dome(top, height=5)
    assert vol() - v0 == pytest.approx(PI * 5 * (3 * 20 ** 2 + 5 ** 2) / 6, rel=1e-6)  # spherical cap
    block()
    S.hole_wizard(top_face(), [[0, 0]], size="1/4-20", hole_type="clearance")
    assert vol() == pytest.approx(120000 - PI * (0.266 * 25.4 / 2) ** 2 * 20)


def test_sketch_and_curve_patterns():
    block()
    S.hole(top_face(), [[-40, 0]], diameter=4, end_condition="ThroughAll")
    pts = S.create_sketch(top_face())["sketch"]
    for x, y in ([-40, 0], [-20, 10], [0, -15], [30, 5]):
        S.sketch_point(pts, [x, y])
    S.sketch_pattern(["Hole1"], pts)
    assert vol() == pytest.approx(120000 - 4 * PI * 4 * 20)
    block()
    S.hole(top_face(), [[-40, -20]], diameter=4, end_condition="ThroughAll")
    path = S.create_sketch(top_face())["sketch"]
    S.sketch_line(path, [-40, -20], [40, -20])
    S.curve_pattern(["Hole1"], path, count=5)
    assert vol() == pytest.approx(120000 - 5 * PI * 4 * 20)


def test_table_and_fill_patterns():
    block()
    S.hole(top_face(), [[0, 0]], diameter=4, end_condition="ThroughAll")
    S.table_pattern(["Hole1"], [[10, 0, 0], [0, 0, 10], [-25, 0, 15]])
    assert vol() == pytest.approx(120000 - 4 * PI * 4 * 20)
    block()
    S.hole(top_face(), [[0, 0]], diameter=4, end_condition="ThroughAll")
    S.fill_pattern(["Hole1"], top_face(), spacing=10, margin=5)
    # |x| <= 45 -> 9 columns, |z| <= 25 -> 5 rows
    assert vol() == pytest.approx(120000 - 45 * PI * 4 * 20)


def test_reference_axis_point_csys():
    block()
    S.hole(top_face(), [[20, 0]], diameter=4, end_condition="ThroughAll")
    ax = S.reference_axis(points=[[0, 0, 0], [0, 1, 0]])
    assert ax["feature"] == "Axis1" and ax["axis"]["axis_direction"] == pytest.approx([0, 1, 0])
    S.circular_pattern(["Hole1"], "Axis1", count=4)
    assert vol() == pytest.approx(120000 - 4 * PI * 4 * 20)
    ax2 = S.reference_axis(planes=["Right", "Front"])["axis"]
    assert abs(ax2["axis_direction"][1]) == pytest.approx(1) and ax2["axis_origin"] == pytest.approx([0, 0, 0], abs=1e-9)
    pt = S.reference_point(reference=top_face())["point"]["point"]
    assert pt == pytest.approx([0, 20, 0])
    assert S.reference_point(reference="Point1", offset=[1, 2, 3])["point"]["point"] == pytest.approx([1, 22, 3])
    # csys with X along world Z, Y along world X -> Z along world Y
    cs = S.coordinate_system(origin="Point1", x_axis=[0, 0, 1], y_axis="X")["csys"]
    assert cs["csys_z"] == pytest.approx([0, 1, 0]) and cs["csys_origin"] == pytest.approx([0, 20, 0])
    block()
    S.hole(top_face(), [[0, 0]], diameter=4, end_condition="ThroughAll")
    S.coordinate_system(x_axis=[0, 0, 1], y_axis=[1, 0, 0])
    S.table_pattern(["Hole1"], [[10, 0, 0]], csys="Coordinate System1")  # world +Z 10: a second hole
    assert vol() == pytest.approx(120000 - 2 * PI * 4 * 20)
    sk = S.create_sketch("Coordinate System1")["sketch"]  # its XY plane is the world ZX plane
    assert sk


def test_sketch_ellipse_spline_text():
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_ellipse(sk, [0, 0], [20, 0], 10)
    S.extrude(sk, depth=5)
    assert vol() == pytest.approx(PI * 20 * 10 * 5, rel=1e-6)
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    sp = S.sketch_spline(sk, [[0, 0], [20, 10], [40, 0], [20, -10]], closed=True)
    assert len(sp["result"]["points"]) == 4
    S.extrude(sk, depth=5)
    assert 0 < vol() < 40 * 20 * 5
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_text(sk, [0, 0], "OB", height=20)
    S.extrude(sk, depth=2)
    v = vol()
    assert v > 0
    S.new_part()  # the counter of the O is a hole, not filled
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_text(sk, [0, 0], "O", height=20)
    S.extrude(sk, depth=2)
    o = S.get_topology()
    assert sum(1 for f in o["faces"] if abs(f["normal"][2]) < 0.5) >= 2  # outer and inner side walls


def test_sketch_patterns_and_convert():
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    c = S.sketch_circle(sk, [0, 0], 5)["result"]["entity"]
    S.sketch_linear_pattern(sk, [c], spacing=20, count=3)
    S.extrude(sk, depth=10)
    assert vol() == pytest.approx(3 * PI * 25 * 10)
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    c = S.sketch_circle(sk, [20, 0], 2)["result"]["entity"]
    S.sketch_circular_pattern(sk, [c], [0, 0], count=6)
    S.extrude(sk, depth=10)
    assert vol() == pytest.approx(6 * PI * 4 * 10)
    block()
    sk = S.create_sketch(top_face())["sketch"]
    S.sketch_convert_entities(sk, faces=[top_face()])
    S.extrude(sk, depth=10)
    assert vol() == pytest.approx(120000 + 100 * 60 * 10)
    block()
    sk = S.create_sketch("Front")["sketch"]
    r = S.sketch_intersection_curve(sk)
    assert len(r["result"]["entities"]) == 4 and r["status"]["state"] == "fully_defined"


def test_3d_sketch_and_helix_paths():
    S.new_part()
    prof = S.create_sketch("Front")["sketch"]
    S.sketch_circle(prof, [0, 0], 5)
    p3 = S.create_3d_sketch([[0, 0, 0], [0, 0, 50]])
    assert p3["sketch"] == "3DSketch1" and p3["info"]["length"] == pytest.approx(50)
    S.add_3d_sketch_segment("3DSketch1", [[0, 0, 50], [0, 30, 50]])
    assert S.get_model()  # rebuilt fine
    S.sweep(prof, "3DSketch1")
    assert vol() > PI * 25 * 50
    hx = S.helix(pitch=10, radius=20, revolutions=3)["info"]
    assert hx["length"] == pytest.approx(3 * math.hypot(2 * PI * 20, 10), rel=1e-4)
    assert hx["start"] == pytest.approx([20, 0, 0], abs=1e-6)


def test_materials_appearance_properties_units():
    block()
    S.set_material("6061-T6 (SS)")
    m = S.get_model()["material"]
    assert m["density"] == 2700 and m["elastic_modulus"] == 69e3 and m["yield_strength"] == 275
    assert S.get_mass_properties()["mass"] == pytest.approx(120000 * 2.7e-3)  # g
    with pytest.raises(S.CommandError):
        S.set_material("Unobtainium")
    S.set_material("Unobtainium", 1234)
    assert "AISI 1020" in S.list_materials("steel")
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    S.set_appearance("#ff0000", target="feature", feature="Hole1")
    S.set_appearance("#00ff00", target="face", face=top_face())
    S.set_appearance("#0000ff")
    faces = S.get_mesh()["faces"]
    cols = [f.get("color") for f in faces]
    assert cols.count("#ff0000") == 1  # the hole wall
    assert cols.count("#00ff00") == 1  # the top face
    assert cols.count("#0000ff") == len(faces) - 2
    S.set_custom_property("Description", "Mounting plate")
    S.set_custom_property("PartNo", "MP-001")
    svg = S.create_drawing()["svg"]
    assert "Mounting plate" in svg and "MP-001" in svg
    assert S.set_units("in")["units"] == "in"


def test_assembly_patterns_mirror_explode_motion_fasteners(tmp_path):
    S.new_part("Cube")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [10, 10])
    S.extrude(sk, depth=10)
    S.new_part("Slider")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [5, 5])
    S.extrude(sk, depth=5)
    S.new_assembly("Line")
    S.insert_component("Cube")
    S.insert_component("Slider", position=[50, 0, 0])
    r = S.component_pattern(["Slider-1"], "linear", count=3, spacing=20, direction=[0, 1, 0])
    comps = {c["name"]: c for c in r["assembly"]["components"]}
    assert comps["Slider-3"]["position"] == pytest.approx([50, 40, 0])
    S.move_component("Slider-1", position=[60, 0, 0])  # instances follow the seed
    comps = {c["name"]: c for c in S.get_model()["components"]}
    assert comps["Slider-2"]["position"] == pytest.approx([60, 20, 0])
    c = S.component_pattern(["Slider-1"], "circular", count=4, axis=[0, 0, 1])
    comps = {c["name"]: c for c in c["assembly"]["components"]}
    assert comps["Slider-4"]["position"] == pytest.approx([0, 60, 0], abs=1e-9)  # 90 deg about Z
    v0 = S.get_mass_properties()["volume"]
    S.mirror_components(["Cube-1"], plane="Right")  # opposite hand at x in [-10, 0]
    assert S.get_mass_properties()["volume"] == pytest.approx(v0 + 1000)
    bb = [f for f in S.get_topology()["faces"] if f["id"].startswith("Cube-2")]
    assert max(f["point"][0] for f in bb) <= 1e-9
    S.explode_step(["Cube-1"], [0, 0, 1], 30)
    pts = [f["point"][2] for f in S.get_topology()["faces"] if f["id"].startswith("Cube-1/")]
    assert min(pts) == pytest.approx(30)
    S.set_exploded(False)
    assert min(f["point"][2] for f in S.get_topology()["faces"] if f["id"].startswith("Cube-1/")) == pytest.approx(0)
    # motion: drive a distance mate between the slider and the cube
    S.new_assembly("Motion")
    S.insert_component("Cube")
    S.insert_component("Slider", position=[30, 0, 0])
    topo = S.get_topology()["faces"]
    cube_x = [f["id"] for f in topo if f["id"].startswith("Cube") and f["normal"][0] > 0.99][0]
    sl_x = [f["id"] for f in topo if f["id"].startswith("Slider") and f["normal"][0] < -0.99][0]
    cz = [f["id"] for f in topo if f["id"].startswith("Cube") and f["normal"][2] < -0.99][0]
    sz = [f["id"] for f in topo if f["id"].startswith("Slider") and f["normal"][2] < -0.99][0]
    cy = [f["id"] for f in topo if f["id"].startswith("Cube") and f["normal"][1] < -0.99][0]
    sy = [f["id"] for f in topo if f["id"].startswith("Slider") and f["normal"][1] < -0.99][0]
    S.add_mate("coincident", cz, sz, aligned=True)
    S.add_mate("coincident", cy, sy, aligned=True)
    m = S.add_mate("distance", cube_x, sl_x, value=5)["assembly"]["mates"][-1]["name"]
    fr = S.motion_study(m, 5, 25, frames=5)["frames"]
    assert [round(f["poses"]["Slider-1"][3], 6) for f in fr] == pytest.approx([15, 20, 25, 30, 35])
    assert [c for c in S.get_model()["components"] if c["name"] == "Slider-1"][0]["position"][0] == pytest.approx(15)
    gif = S.motion_export(m, 5, 25, str(tmp_path / "motion"), frames=4, width=160, height=120)
    from PIL import Image
    im = Image.open(gif["path"])
    assert gif["frames"] == 4 and im.n_frames == 4 and im.size == (160, 120)
    avi = S.motion_export(m, 5, 25, str(tmp_path / "motion.avi"), frames=4, width=160, height=120)
    data = Path(avi["path"]).read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"AVI " and data.count(b"00dc") == 8 and b"idx1" in data
    # hardware + subassembly
    S.insert_fastener("hex_bolt", "M8", length=30, position=[0, 0, 50])
    bolt = [c["name"] for c in S.get_model()["components"] if c["name"].startswith("ISO_4017")][0]
    assert bolt
    assert "M8" in S.list_fasteners()["hex_bolt"]["sizes"]
    S.new_assembly("Top")
    S.insert_component("Motion")
    sub = S.get_model()["components"][0]
    assert sub["subassembly"] and S.get_mass_properties()["volume"] > 1000 + 125


def test_drawing_editor_annotations_and_export(tmp_path):
    sk = block()
    S.sketch_dimension(sk, "length", ["Line5"], 100)
    S.sketch_dimension(sk, "length", ["Line6"], 60)
    S.hole(top_face(), [[20, 0]], diameter=10, end_condition="ThroughAll")
    base = S.create_drawing()["svg"]
    S.drawing_move_view("front", 10, -5)
    moved = S.create_drawing()["svg"]
    assert moved != base and 'data-view="front"' in moved
    n = S.drawing_add_note("BREAK ALL EDGES\nDEBURR", 30, 40, leader=[60, 60])["note"]
    S.drawing_add_datum("a", 50, 50, leader=[70, 80])
    g = S.drawing_add_gdt("position", 0.1, 100, 40, datums=["A", "B"], diameter=True, modifier="MMC")["gdt"]
    before = S.create_drawing()["svg"].count("<text")
    S.drawing_settings(model_items=True)
    svg = S.create_drawing()["svg"]
    assert svg.count("<text") == before + 2  # the two driving sketch dimensions appear as model items
    assert "BREAK ALL EDGES" in svg and "DEBURR" in svg and "⌖" in svg and "⌀0.1 Ⓜ" in svg and ">A<" in svg
    S.drawing_edit_annotation(n["id"], text="NOTE 2")
    S.drawing_delete_annotation(g["id"])
    svg = S.create_drawing()["svg"]
    assert "NOTE 2" in svg and "⌖" not in svg
    pdf = S.export_drawing(str(tmp_path / "sheet.pdf"))
    data = (tmp_path / "sheet.pdf").read_bytes()
    assert pdf["bytes"] > 1000 and data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    dxf = S.export_drawing(str(tmp_path / "sheet"), format="dxf")
    import ezdxf
    msp = ezdxf.readfile(dxf["path"]).modelspace()
    assert len(msp.query("LWPOLYLINE")) > 10 and any("NOTE 2" in t.dxf.text for t in msp.query("TEXT"))
    path = S.save_part(str(tmp_path / "d.oswprt"))["saved"]
    S.open_part(path)
    assert "NOTE 2" in S.create_drawing()["svg"]  # drawing state is saved with the part


def test_variable_fillet():
    block()
    e = [x["id"] for x in S.get_topology()["edges"] if x["type"] == "line" and abs(x["length"] - 100) < 1e-6][0]
    v0 = vol()
    S.variable_fillet([e], 2, 6)
    removed = v0 - vol()
    # between constant fillets of r=2 and r=6 (removed area (1 - pi/4) r^2 per unit length)
    assert (1 - PI / 4) * 4 * 100 < removed < (1 - PI / 4) * 36 * 100


def _poly_area(pts):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def test_drawing_sheets_and_views(tmp_path):
    """Drawing documents: New Drawing lays out Front / Top / Right / Isometric (projected views aligned with
    Front); section, detail and auxiliary views; first-angle projection; several sheets exported as a
    multi-page PDF; deleting a view deletes the views derived from it; revision table."""
    from openswx import drawing_sheets as DS
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_line(sk, [0, 0], [100, 0])
    S.sketch_line(sk, [100, 0], [100, 20])
    S.sketch_line(sk, [100, 20], [60, 40])  # an inclined face (rises 20 over 40)
    S.sketch_line(sk, [60, 40], [0, 40])
    S.sketch_line(sk, [0, 40], [0, 0])
    S.extrude(sk, depth=30)
    S.drawing_new(sheet="A3", scale=1)
    st = S.APP.doc.drawing
    front, top, right, iso = st["sheets"][0]["views"]
    assert (front["kind"], top["side"], right["side"], iso["orientation"]) == ("model", "top", "right", "isometric")
    assert top["x"] == pytest.approx(front["x"]) and right["y"] == pytest.approx(front["y"])
    assert tuple(DS.frame(st, right)[0]) == pytest.approx((1, 0, 0))  # third angle: placed right = right side
    S.drawing_move_view(front["id"], 10, 0)
    S.create_drawing()
    assert top["x"] == pytest.approx(front["x"])  # projected views follow their parent

    # section through the middle of the front view, looking down: the cut face is the 100 x 30 bottom slab
    sec = S.drawing_section_view(front["id"], [front["x"] - 70, front["y"] + 5], [front["x"] + 70, front["y"] + 5])["view"]
    g = DS.view_geometry(S.APP.doc, st, sec)
    a, b = sec["a"], sec["b"]
    assert a[1] == pytest.approx(b[1])
    y_cut = a[1]
    assert sum(_poly_area(h) for h in g["hatch"]) == pytest.approx(100 * 30 if y_cut <= 20 else (100 - 2 * (y_cut - 20)) * 30, rel=1e-3)

    det = S.drawing_detail_view(front["id"], [front["x"] + 30, front["y"] - 10], 8, 3)["view"]
    assert DS.view_scale(st, det) == pytest.approx(3.0)
    assert det["letter"] == "B" and sec["letter"] == "A"

    # auxiliary view square to the inclined edge: the inclined face shows its true length sqrt(40^2 + 20^2)
    # picked on the sheet: Front's model (100, 20) -> (60, 40); its 2D frame is model x, y around the box centre
    t = DS.placement(st, front, DS.view_geometry(S.APP.doc, st, front))
    aux = S.drawing_auxiliary_view(front["id"], list(t(100, 20)), list(t(60, 40)))["view"]
    ga = DS.view_geometry(S.APP.doc, st, aux)
    assert tuple(DS.frame(st, aux)[0]) == pytest.approx((1 / math.sqrt(5), 2 / math.sqrt(5), 0))  # the face's normal
    lengths = {round(math.dist(p[0], p[-1]), 3) for p in ga["vis"] if len(p) == 2}
    assert round(math.hypot(40, 20), 3) in lengths  # the inclined edge at its true length

    # Smart Dimension: picks near (not on) the corners snap to them; values are model sizes
    dh = S.drawing_add_dimension(front["id"], "horizontal", list(t(1, 1)), list(t(99, -1)))["dimension"]
    dv = S.drawing_add_dimension(front["id"], "vertical", list(t(0, 0)), list(t(0, 40)), offset=12)["dimension"]
    dl = S.drawing_add_dimension(front["id"], "linear", list(t(100, 20)), list(t(60, 40)))["dimension"]
    assert (dh["value"], dv["value"], dl["value"]) == pytest.approx((100, 40, math.hypot(40, 20)))
    with pytest.raises(S.CommandError):
        S.drawing_add_dimension(front["id"], "diameter", list(t(50, 20)))  # no circle in this part
    S.drawing_edit_dimension(dh["id"], text="2X 100")
    S.drawing_delete_dimension(dl["id"])
    svg = S.create_drawing()["svg"]
    assert svg.count("data-dim") == 2 and "2X 100" in svg

    S.drawing_add_revision("A", "First issue", date="2026-10-24")
    S.drawing_add_sheet(size="A4")
    S.drawing_add_view("isometric", 150, 100)
    info = S.drawing_info()
    assert [x["name"] for x in info["sheets"]] == ["Sheet1", "Sheet2"] and info["active"] == "Sheet2"
    out = S.export_drawing(str(tmp_path / "d.pdf"))
    assert out["pages"] == 2 and (tmp_path / "d.pdf").read_bytes().count(b"/Type /Page ") == 2
    S.drawing_activate_sheet("Sheet1")
    svg = S.create_drawing()["svg"]
    assert "SECTION A-A" in svg and "DETAIL B" in svg and "First issue" in svg and "url(#hatch)" in svg
    S.drawing_delete_view(front["id"])  # takes its projected, section, detail and auxiliary views with it
    assert [v["id"] for v in st["sheets"][0]["views"]] == [iso["id"]]
    assert st["sheets"][0]["dims"] == []  # and its dimensions

    # first angle: the view placed to the right of Front shows the left side
    S.drawing_new(sheet="A3", projection="first", scale=1)
    st = S.APP.doc.drawing
    side = st["sheets"][0]["views"][2]
    assert tuple(DS.frame(st, side)[0]) == pytest.approx((-1, 0, 0))


def _plate_with_hole():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=20)
    s2 = S.create_sketch("Top")["sketch"]
    S.sketch_circle(s2, [50, 30], 5)
    S.cut_extrude(s2, end_condition="ThroughAllBoth")


def _faces(kind=None, normal=None):
    out = []
    for f in S.get_topology()["faces"]:
        if kind and f["type"] != kind:
            continue
        if normal and not (f.get("normal") and all(abs(a - b) < 1e-6 for a, b in zip(f["normal"], normal))):
            continue
        out.append(f["id"])
    return out


def test_split_delete_move_replace_face():
    """Split (all pieces / one side), Delete Face (patch heals a hole; delete leaves a surface), Move Face
    (offset with the neighbours extended), Replace Face (up to and down to a surface), Cut With Surface."""
    _plate_with_hole()
    vol = lambda: S.APP.doc.body.volume if S.APP.doc.body else 0.0  # noqa: E731
    full = 100 * 60 * 20 - PI * 25 * 20
    assert vol() == pytest.approx(full)
    plane = S.reference_plane("Right", offset=30)["feature"]
    assert S.split_body(plane)["bodies"] == 2
    assert sorted(s.volume for s in S.APP.doc.body.solids()) == pytest.approx(sorted([30 * 60 * 20, full - 30 * 60 * 20]))
    S.undo()
    S.split_body(plane, keep="above")
    assert vol() == pytest.approx(full - 30 * 60 * 20)
    S.undo()

    S.delete_face(_faces("cylinder"))
    assert vol() == pytest.approx(100 * 60 * 20)
    f = S.APP.doc.features[-1]  # Edit Feature: its faces resolve on the model before it, where they still exist
    S.redefine_feature(f["name"], "delete_face", {"faces": f["params"]["faces"], "mode": "patch"})
    assert [x["name"] for x in S.APP.doc.features][-1] == f["name"] and vol() == pytest.approx(100 * 60 * 20)
    S.undo()
    S.undo()
    S.delete_face(_faces("cylinder"), mode="delete")
    assert S.APP.doc.body is None and "DeleteFace1" in S.APP.doc.ctx.surfaces
    S.undo()

    S.move_face(_faces("plane", [0, 1, 0]), 5)
    assert vol() == pytest.approx(100 * 60 * 25 - PI * 25 * 25)
    S.undo()
    S.move_face(_faces("cylinder"), 2)  # the hole wall moves out of the material: radius 5 -> 3
    assert vol() == pytest.approx(100 * 60 * 20 - PI * 9 * 20)
    S.undo()

    side = S.reference_plane("Front", offset=-150)["feature"]
    surfs = {}
    for y in (30, 12, 8):  # flat surfaces across the part at these heights
        ss = S.create_sketch(side)["sketch"]
        S.sketch_line(ss, [-20, y], [120, y])
        surfs[y] = S.extruded_surface(ss, depth=300)["feature"]
    for y in (30, 12):  # the top face pushed up to / pulled down to the surface
        S.replace_face(_faces("plane", [0, 1, 0]), surfs[y])
        assert vol() == pytest.approx(100 * 60 * y - PI * 25 * y)
        S.undo()
    slab = lambda h: 100 * 60 * h - PI * 25 * h  # noqa: E731
    S.cut_with_surface(surfs[8])
    v1 = vol()
    S.undo()
    S.cut_with_surface(surfs[8], flip=True)
    assert sorted([v1, vol()]) == pytest.approx([slab(8), slab(12)])  # one side each way
    S.undo()
    with pytest.raises(S.CommandError):
        S.cut_with_surface(surfs[30], flip=v1 < vol())  # all the material is on one side: nothing would be left


def test_sketch_tangent_arc_circle_3pt_split_transform():
    """Tangent Arc leaves a line tangentially (centre on the normal, tangent relation); Perimeter Circle through
    3 points; Split Entities; Move/Copy, Rotate and Scale Entities (radius scales too); a profile closed with a
    tangent arc extrudes to the expected area."""
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    X = lambda: S.APP.doc.ctx.sketches[sk][0]  # noqa: E731 (the sketch is rebuilt after each edit)
    r = S.sketch_line(sk, [0, 0], [40, 0])["result"]
    arc = S.sketch_tangent_arc(sk, r["points"][1], [40, 20])["result"]["entity"]
    assert X().coords(X().entities[arc]["c"]) == pytest.approx([40, 10])
    assert any(rel["type"] == "tangent" and arc in rel["refs"] for rel in X().relations)
    assert X().coords(X().entities[arc]["e"]) == pytest.approx([40, 20])  # counterclockwise round the right
    S.sketch_line(sk, X().entities[arc]["e"], [0, 20])
    S.sketch_line(sk, [0, 20], r["points"][0])
    S.extrude(sk, depth=1)
    assert S.APP.doc.body.volume == pytest.approx(40 * 20 + PI * 100 / 2)  # rectangle + half disc

    sk = S.create_sketch("Front")["sketch"]
    c = S.sketch_circle_3pt(sk, [100, 0], [120, 20], [100, 40])["result"]["entity"]
    assert X().coords(X().entities[c]["c"]) == pytest.approx([100, 20]) and X().entities[c]["r"] == pytest.approx(20)
    ln = S.sketch_line(sk, [0, -50], [40, -50])["result"]["entity"]
    assert S.sketch_split(sk, ln, [10, -50])["result"]["entities"][0] == ln
    assert X().coords(X().entities[ln]["p2"]) == pytest.approx([10, -50])
    cp = S.sketch_transform(sk, [c], move=[0, 50], copy=True)["result"]["entities"][0]
    assert X().coords(X().entities[cp]["c"]) == pytest.approx([100, 70])
    S.sketch_transform(sk, [c], rotate=90, center=[0, 0])
    assert X().coords(X().entities[c]["c"]) == pytest.approx([-20, 100])
    S.sketch_transform(sk, [c], scale=2, center=[0, 0])
    assert X().coords(X().entities[c]["c"]) == pytest.approx([-40, 200]) and X().entities[c]["r"] == pytest.approx(40)


def test_indent():
    """Indent: cut mode removes the tool plus clearance from the target; indent mode adds a wall `thickness`
    thick round the part of the tool sticking out (its top at tool + clearance + thickness); the tool stays.
    A tool poking out on both sides is wrapped on the side region_point picks."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-50, -50], [50, 50])
    S.extrude(sk, depth=2)
    s2 = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(s2, [-10, -10], [10, 10])
    S.extrude(s2, depth=12, merge=False)
    S.indent(0, 1, clearance=0.5, cut=True)
    hole = 21 * 21 - (4 - PI) * 0.5 ** 2  # the clearance offset rounds the hole's corners (radius 0.5)
    assert sorted(s.volume for s in S.APP.doc.body.solids()) == pytest.approx([20 * 20 * 12, 100 * 100 * 2 - hole * 2])
    S.undo()
    S.indent(0, 1, thickness=1.5, clearance=0.5)
    tool, target = sorted(S.APP.doc.body.solids(), key=lambda s: s.volume)
    assert tool.volume == pytest.approx(4800) and target.is_valid
    assert target.bounding_box().max.Y == pytest.approx(12 + 0.5 + 1.5)
    assert target.volume > 100 * 100 * 2 - 21 * 21 * 2
    with pytest.raises(S.CommandError):
        S.indent(0, 0)
    S.undo()
    s3 = S.reference_plane("Top", offset=-6)["feature"]
    s4 = S.create_sketch(s3)["sketch"]
    S.sketch_rectangle(s4, [30, 30], [40, 40])
    S.extrude(s4, depth=14, merge=False)  # pokes out below (y -6) and above (y 8)
    target_i = max(range(3), key=lambda i: S.APP.doc.body.solids()[i].volume)
    tool_i = next(i for i in range(3) if abs(S.APP.doc.body.solids()[i].bounding_box().min.Y + 6) < 1e-6)
    S.indent(target_i, tool_i, thickness=1, region_point=[35, 20, -35])
    bb = max(S.APP.doc.body.solids(), key=lambda s: s.volume).bounding_box()
    assert bb.max.Y == pytest.approx(9) and bb.min.Y == pytest.approx(0)  # wrapped above only


def test_loft_guide_curves():
    """Loft with guide curves: the side passes through every guide (square profiles with guides at the
    corners, circles with four guides), closes into a valid solid, and asks for two or more guides."""
    from build123d import Vertex
    for shape in ("square", "circle"):
        S.new_part()
        top = S.reference_plane("Top", offset=50)["feature"]
        a = S.create_sketch("Top")["sketch"]
        b = S.create_sketch(top)["sketch"]
        if shape == "square":
            S.sketch_rectangle(a, [-10, -10], [10, 10])
            S.sketch_rectangle(b, [-10, -10], [10, 10])
            ends = [(10, -10), (-10, 10), (10, 10), (-10, -10)]  # (x, z) of the corners
        else:
            S.sketch_circle(a, [0, 0], 10)
            S.sketch_circle(b, [0, 0], 10)
            ends = [(10, 0), (-10, 0), (0, 10), (0, -10)]
        guides = [S.curve_through_points([[x, 0, z], [1.4 * x, 25, 1.4 * z], [x, 50, z]])["feature"] for x, z in ends]
        with pytest.raises(S.CommandError):
            S.loft([a, b], guides=guides[:1])
        S.loft([a, b], guides=guides)
        body = S.APP.doc.body
        assert body.is_valid and body.volume > (400 if shape == "square" else PI * 100) * 50  # bulges out to the guides
        for g in guides:
            mid = S.APP.doc.ctx.curves[g][0].position_at(0.5)
            assert min(f.distance_to(Vertex(mid)) for f in body.faces()) < 1e-3


def test_intersect_regions():
    """Intersect: a body and a plane give two regions (exclude one, or keep both unmerged); a tube surface and
    two caps enclose a cylinder with no solid body at all."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=20)
    pl = S.reference_plane("Top", offset=5)["feature"]
    r = S.intersect([pl])
    assert sorted(x["volume"] for x in r["regions"]) == pytest.approx([30000, 90000])
    assert len(S.APP.doc.body.solids()) == 1 and S.APP.doc.body.volume == pytest.approx(120000)
    S.undo()
    low = min(r["regions"], key=lambda x: x["center"][1])["index"]
    S.intersect([pl], exclude=[low])
    assert S.APP.doc.body.volume == pytest.approx(90000)
    S.undo()
    S.intersect([pl], merge=False)
    assert len(S.APP.doc.body.solids()) == 2

    S.new_part()
    a = S.create_sketch("Front")["sketch"]
    S.sketch_circle(a, [0, 0], 10)
    tube = S.extruded_surface(a, depth=30)["feature"]
    c1 = S.create_sketch("Front")["sketch"]
    S.sketch_circle(c1, [0, 0], 10)
    cap1 = S.planar_surface(c1)["feature"]
    top = S.reference_plane("Front", offset=30)["feature"]
    c2 = S.create_sketch(top)["sketch"]
    S.sketch_circle(c2, [0, 0], 10)
    cap2 = S.planar_surface(c2)["feature"]
    r = S.intersect([tube, cap1, cap2])
    assert [x["volume"] for x in r["regions"]] == pytest.approx([PI * 100 * 30])
    assert S.APP.doc.body.volume == pytest.approx(PI * 100 * 30)


def test_curves_sweep_twist_guide_loft_centerline_sketch_chamfer_conic():
    """Projected curve, composite curve and curve through XYZ points; a twisted sweep keeps its volume and
    turns the end; a guide curve scales the profile (a cone frustum); a loft along a centreline; sketch
    chamfer; a conic (parabola) bounds the exact area."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=20)
    above = S.reference_plane("Top", offset=50)["feature"]
    s3 = S.create_sketch(above)["sketch"]
    S.sketch_circle(s3, [50, 30], 10)
    pc = S.projected_curve(s3)
    assert pc["length"] == pytest.approx(2 * PI * 10)
    assert all(abs(e.position_at(0.3).Y - 20) < 1e-6 for e in S.APP.doc.ctx.curves[pc["feature"]])  # on the top face
    nfaces = len(S.APP.doc.body.faces())
    assert S.split_line(s3)["new_faces"] == 1  # the top face split in two along the circle
    assert len(S.APP.doc.body.faces()) == nfaces + 1 and S.APP.doc.body.volume == pytest.approx(100 * 60 * 20)
    xyz = S.curve_through_points([[0, 0, 0], [10, 5, 0], [20, 0, 5], [30, -5, 10]])
    a = S.create_sketch("Front")["sketch"]
    S.sketch_line(a, [30, 0], [60, 0])
    xyz_end = S.APP.doc.ctx.curves[xyz["feature"]][0].position_at(1)
    assert tuple(xyz_end) == pytest.approx((30, -5, 10))
    l1 = S.curve_through_points([[0, 0, 0], [10, 0, 0]])["feature"]
    l2 = S.curve_through_points([[10, 0, 0], [10, 10, 0]])["feature"]
    assert S.composite_curve([l1, l2])["length"] == pytest.approx(20)
    with pytest.raises(S.CommandError):
        S.composite_curve([l1, S.curve_through_points([[50, 0, 0], [60, 0, 0]])["feature"]])

    S.new_part()
    path = S.create_sketch("Front")["sketch"]
    S.sketch_line(path, [0, 0], [0, 100])
    prof = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(prof, [-5, -5], [5, 5])
    S.sweep(prof, path, twist=45)
    b = S.APP.doc.body
    assert b.volume == pytest.approx(10000, rel=1e-6) and b.is_valid
    top = [f for f in b.faces() if abs(f.center().Y - 100) < 1e-6][0]
    assert sorted(round(abs(c), 3) for v in top.vertices() for c in (v.X, v.Z)) == pytest.approx([0, 0, 0, 0, 7.071, 7.071, 7.071, 7.071])

    S.new_part()
    path = S.create_sketch("Front")["sketch"]
    S.sketch_line(path, [0, 0], [0, 100])
    guide = S.create_sketch("Front")["sketch"]
    S.sketch_line(guide, [5, 0], [15, 100])
    prof = S.create_sketch("Top")["sketch"]
    S.sketch_circle(prof, [0, 0], 5)
    S.sweep(prof, path, guide=guide)
    assert S.APP.doc.body.volume == pytest.approx(PI * 100 / 3 * (25 + 225 + 75), rel=1e-5)

    S.new_part()
    p2 = S.reference_plane("Top", offset=80)["feature"]
    sq = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sq, [-10, -10], [10, 10])
    ci = S.create_sketch(p2)["sketch"]
    S.sketch_circle(ci, [0, 0], 8)
    cl = S.create_sketch("Front")["sketch"]
    S.sketch_line(cl, [0, 0], [0, 80])
    S.loft([sq, ci], centerline=cl)
    b = S.APP.doc.body
    assert b.is_valid and 80 * PI * 64 < b.volume < 80 * 400

    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 20])
    pts = S.APP.doc.ctx.sketches[sk][0].points
    corner = next(k for k, v in pts.items() if tuple(v) == (40.0, 20.0))
    S.sketch_chamfer(sk, corner, 5)
    S.extrude(sk, depth=10)
    assert S.APP.doc.body.volume == pytest.approx((800 - 12.5) * 10)

    S.new_part()
    s = S.create_sketch("Front")["sketch"]
    S.sketch_line(s, [0, 0], [40, 0])
    S.sketch_conic(s, [40, 0], [0, 40], [40, 40], rho=0.5)
    S.sketch_line(s, [0, 40], [0, 0])
    S.extrude(s, depth=1)
    assert S.APP.doc.body.volume == pytest.approx(800 + 2 / 3 * 800)  # triangle + 2/3 of the parabola's chord triangle


def test_drawing_hole_table_bom_title_block(tmp_path):
    """Hole Table tags holes by size (blind holes give their depth, bosses are left out) and locates them from
    a picked origin; the BOM's part number, description and order can be edited; the title block's fields."""
    from openswx import drawing_sheets as DS
    S.new_part("Plate")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.sketch_circle(sk, [20, 15], 4)
    S.sketch_circle(sk, [80, 15], 4)
    S.extrude(sk, depth=10)
    s2 = S.create_sketch("Top")["sketch"]
    S.sketch_circle(s2, [50, 40], 3)
    S.cut_extrude(s2, depth=4, reverse=True)  # blind, from the bottom
    s3 = S.create_sketch("Top")["sketch"]
    S.sketch_circle(s3, [50, 10], 5)
    S.extrude(s3, depth=5, reverse=True)  # a boss: not a hole
    S.drawing_new(sheet="A3", scale=1)
    st = S.APP.doc.drawing
    top = st["sheets"][0]["views"][1]
    g = DS.view_geometry(S.APP.doc, st, top)
    t = DS.placement(st, top, g)
    ht = S.drawing_hole_table(top["id"], list(t(g["box"][0] + 0.5, g["box"][1] + 0.5)))
    rows = {h["tag"]: (h["diameter"], h["depth"]) for h in ht["holes"]}
    assert rows == {"A1": (pytest.approx(6), pytest.approx(4)), "B1": (pytest.approx(8), None), "B2": (pytest.approx(8), None)}
    assert sorted((round(h["x"], 6), round(h["y"], 6)) for h in ht["holes"] if h["tag"].startswith("B"))[0][0] == pytest.approx(20)
    S.drawing_title_block(company="ACME", drawn="SJ", checked="AB")
    svg = S.create_drawing()["svg"]
    assert "THRU" in svg and "↧4" in svg and "ACME" in svg and "CHK AB" in svg
    out = S.export_drawing(str(tmp_path / "h.pdf"))
    assert out["pages"] == 1 and b"DIA 6 DEPTH 4" in (tmp_path / "h.pdf").read_bytes()

    S.new_part("Pin")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 4)
    S.extrude(sk, depth=30)
    S.new_assembly("Fixture")
    S.insert_component("Plate")
    S.insert_component("Pin", position=[20, 0, 15])
    S.insert_component("Pin", position=[80, 0, 15])
    S.drawing_new(sheet="A3")
    bom = S.drawing_info()["sheets"][0]["tables"][0]["rows"]
    assert [(r["part_number"], r["qty"]) for r in bom] == [("Plate", 1), ("Pin", 2)]
    pin = bom[1]["part"]
    rows = S.drawing_edit_bom(pin, part_number="PN-0042", description="Dowel pin", item=1)["bom"]
    assert [(r["item"], r["part_number"], r["description"]) for r in rows][0] == (1, "PN-0042", "Dowel pin")
    assert "PN-0042" in S.create_drawing()["svg"]


def test_drawing_annotations():
    """Angle between edges (the quadrant the picks are in), distance between parallel edges, Hole Callout,
    center marks / centerlines, and balloons numbered from the BOM (auto and manual, moving with the view)."""
    from openswx import drawing_sheets as DS
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    for a, b in (([0, 0], [100, 0]), ([100, 0], [100, 20]), ([100, 20], [60, 40]), ([60, 40], [0, 40]), ([0, 40], [0, 0])):
        S.sketch_line(sk, a, b)
    S.sketch_circle(sk, [25, 20], 6)
    S.extrude(sk, depth=30)
    S.drawing_new(sheet="A3", scale=1)
    st = S.APP.doc.drawing
    front, top = st["sheets"][0]["views"][:2]
    t = DS.placement(st, front, DS.view_geometry(S.APP.doc, st, front))
    slope = math.degrees(math.atan2(20, 40))
    assert S.drawing_add_dimension(front["id"], "angle", list(t(80, 30)), list(t(30, 40)))["dimension"]["value"] == pytest.approx(180 - slope)
    d = S.drawing_add_dimension(front["id"], "auto", list(t(100, 10)), list(t(80, 30)))["dimension"]
    assert d["kind"] == "angle" and d["value"] == pytest.approx(90 + slope)
    d = S.drawing_add_dimension(front["id"], "auto", list(t(50, 0)), list(t(30, 40)))["dimension"]
    assert d["kind"] == "line_distance" and d["value"] == pytest.approx(40)
    with pytest.raises(S.CommandError):
        S.drawing_add_dimension(front["id"], "angle", list(t(50, 0)), list(t(30, 40)))  # parallel
    # placed in the sector between the edges (not merely on their sides): inside the part's corner, or the other way
    for at, want in (((62, 30), 180 - slope), ((55, 35), 180 - slope), ((40, 45), slope)):
        d = S.drawing_add_dimension(front["id"], "angle", list(t(80, 30)), list(t(30, 40)), at=list(t(*at)))["dimension"]
        assert d["value"] == pytest.approx(want)
        S.drawing_delete_dimension(d["id"])
    assert S.drawing_add_dimension(front["id"], "callout", list(t(31, 20)))["dimension"]["callout"] == "⌀12 THRU"
    S.drawing_view_properties(front["id"], center_marks=True)
    S.drawing_view_properties(top["id"], centerlines=True)
    svg = S.create_drawing()["svg"]
    assert f"{DS.D._fmt(180 - slope)}°" in svg and "⌀12 THRU" in svg
    assert svg.count('stroke-dasharray="4 1 0.8 1"') == 2 and svg.count('stroke-dasharray="6 1.2 1 1.2"') == 1

    S.new_part("BPlate")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=10)
    S.new_part("BBlock")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 40])
    S.extrude(sk, depth=20)
    S.new_part("BPin")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 4)
    S.extrude(sk, depth=30)
    S.new_assembly("BAsm")
    S.insert_component("BPlate")
    S.insert_component("BBlock", position=[10, 10, 0])
    S.insert_component("BPin", position=[80, 10, -30])
    S.insert_component("BPin", position=[80, 10, -50])
    S.drawing_new(sheet="A3")
    st = S.APP.active_doc.drawing
    front, iso = st["sheets"][0]["views"][0], st["sheets"][0]["views"][3]
    balloons = S.drawing_auto_balloon(iso["id"])["balloons"]
    assert [b["component"] for b in balloons] == ["BPlate-1", "BBlock-1", "BPin-1"]  # one per BOM item
    t = DS.placement(st, front, DS.view_geometry(S.APP.active_doc, st, front))
    b = S.drawing_add_balloon(front["id"], list(t(30, 25)))["balloon"]
    assert (b["component"], b["item"]) == ("BBlock-1", 2)
    svg = S.create_drawing()["svg"]
    assert all(f">{i}</text></g>" in svg for i in (1, 2, 3))
    x0 = balloons[0]["x"]
    S.drawing_move_view(iso["id"], 5, 5)
    assert st["sheets"][0]["balloons"][0]["x"] == pytest.approx(x0 + 5)
    for name in ("BAsm", "BPin", "BBlock", "BPlate"):
        S.close_document(name)


def test_drawing_break_crop_broken_out():
    """Break View shortens a long part but dimensions across the break keep the true length; Crop View keeps
    a rectangle; a broken-out section cuts to a depth and hatches the floor; each can be removed."""
    from openswx import drawing_sheets as DS
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [300, 20])
    S.sketch_circle(sk, [20, 10], 4)
    S.sketch_circle(sk, [280, 10], 4)
    S.extrude(sk, depth=10)
    S.drawing_new(sheet="A3", scale=1)
    st = S.APP.doc.drawing
    front, top = st["sheets"][0]["views"][:2]
    t = DS.placement(st, front, DS.view_geometry(S.APP.doc, st, front))
    S.drawing_break_view(front["id"], list(t(80, 10)), list(t(220, 10)))
    g = DS.view_geometry(S.APP.doc, st, front)
    assert DS.half_size(st, front, g)[0] * 2 == pytest.approx(300 - 140 + DS.BREAK_GAP)
    assert all(not 80 + 1e-6 < q[0] < 220 - 1e-6 for p in g["vis"] for q in p)  # nothing drawn in the break
    t = DS.placement(st, front, g)
    assert S.drawing_add_dimension(front["id"], "horizontal", list(t(0, 0)), list(t(300, 0)))["dimension"]["value"] == pytest.approx(300)
    assert S.drawing_add_dimension(front["id"], "diameter", list(t(284, 10)))["dimension"]["value"] == pytest.approx(8)
    svg = S.create_drawing()["svg"]
    assert svg.count('stroke="#000" stroke-width="0.25"/>') >= 2  # the two break lines

    S.drawing_crop_view(front["id"], list(t(-5, -5)), list(t(40, 25)))
    assert DS.view_geometry(S.APP.doc, st, front)["box"] == pytest.approx((0, 0, 40, 20))
    S.drawing_remove_view_effect(front["id"], "crop")
    S.drawing_remove_view_effect(front["id"], "breaks")
    assert DS.view_geometry(S.APP.doc, st, front)["box"] == pytest.approx((0, 0, 300, 20))

    # broken-out in the top view (looking down on the 300 x 10 top face): a circle wholly on the face, 5 deep
    gt = DS.view_geometry(S.APP.doc, st, top)
    tt = DS.placement(st, top, gt)
    cy = (gt["box"][1] + gt["box"][3]) / 2
    S.drawing_broken_out_section(top["id"], 5, center=list(tt(150, cy)), radius=4)
    gt = DS.view_geometry(S.APP.doc, st, top)
    area = sum(abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(h, h[1:] + h[:1]))) / 2 for h in gt["hatch"])
    assert area == pytest.approx(math.pi * 16, rel=0.01)
    S.drawing_remove_view_effect(top["id"], "broken_out")
    assert DS.view_geometry(S.APP.doc, st, top)["hatch"] == []


def test_drawing(tmp_path):

    block()
    r = S.create_drawing(path=str(tmp_path / "blk.svg"), sheet="A3")
    svg = (tmp_path / "blk.svg").read_text(encoding="utf-8")
    assert r["views"] == ["front", "top", "right", "isometric"] and r["scale_label"] == "1:1"
    assert svg.startswith("<svg") and ">100<" in svg and ">20<" in svg and ">60<" in svg  # overall dimensions
    assert S.create_drawing(projection="first")["views"][2] == "left"
    assert "SECTION A-A" in svg and "DETAIL B" in svg
    part = S.get_model()["name"]
    S.new_assembly("BomAsm")
    S.insert_component(part)
    S.insert_component(part, position=[0, 50, 0])
    bom = S.create_drawing()["svg"]
    assert "PART NUMBER" in bom and f">{part}<" in bom and ">2<" in bom  # one line, quantity 2


SAMPLE = r"C:\Users\Public\Documents\SOLIDWORKS\SOLIDWORKS 2025\samples\tutorial\advdrawings\98blade extension.sldprt"


@pytest.mark.skipif(not os.path.exists(SAMPLE), reason="SOLIDWORKS sample files not installed")
def test_sldprt_reader(tmp_path):
    from openswx.sldprt import extract, read_info
    info = read_info(SAMPLE)
    assert info["document_type"] == "PART"
    assert "Contents/Config-0-Partition" in info["streams"]
    assert any(b["kind"] == "partition" for b in info["parasolid"])
    files = extract(SAMPLE, str(tmp_path))
    assert any(f.endswith(".x_b") for f in files)


XT_DATA = Path(__file__).parent / "data" / "xt"


@pytest.mark.parametrize("name", ["block.x_t", "block.x_b", "drafted_cylinder.x_t", "torus.x_b", "sphere.x_t",
                                  "spline_extrude.x_b", "spline_revolve.x_t", "spline_drafted.x_t", "filleted_block.x_t",
                                  "shelled_box.x_b"])
def test_parasolid_reader(name):
    """Clean-room XT reader against SOLIDWORKS 2025 exports (Parasolid 36.1): volume and area match
    SOLIDWORKS' own mass properties."""
    import json
    from openswx.features import mass_properties
    from openswx.parasolid import read_xt_shape
    truth = json.loads((XT_DATA / "truth.json").read_text())[name.split(".")[0]]
    shape, notes = read_xt_shape(str(XT_DATA / name))
    mp = mass_properties(shape)
    assert shape.is_valid and not notes
    assert abs(mp["volume"] - truth["volume"]) / truth["volume"] < 1e-4
    assert abs(mp["surface_area"] - truth["area"]) / truth["area"] < 1e-4


def test_sldprt_native_geometry(tmp_path):
    """read_sldprt: container record -> Config-0-Partition stream -> zlib -> Parasolid partition -> B-rep.
    The container is synthesised around a partition taken from a SOLIDWORKS 2025 part."""
    import json
    import struct
    import zlib
    from openswx.sldprt import read_sldprt
    xt = (XT_DATA / "drafted_cylinder_partition.xtp").read_bytes()
    stream = struct.pack("<I", 0) + bytes(16) + zlib.compress(xt)
    name = bytes(((c & 0x0F) << 4) | (c >> 4) for c in b"Contents/Config-0-Partition")
    payload = zlib.compressobj(9, zlib.DEFLATED, -15)
    payload = payload.compress(stream) + payload.flush()
    rec = (b"\0\0\0\0" + b"\x14\x00\x06\x00\x08\x00" + struct.pack("<5I", 0, zlib.crc32(stream), len(payload), len(stream),
                                                                   len(name)) + name + payload)
    f = tmp_path / "cyl.sldprt"
    f.write_bytes(rec)
    shape, _ = read_sldprt(str(f))
    truth = json.loads((XT_DATA / "truth.json").read_text())["drafted_cylinder"]
    assert abs(shape.volume - truth["volume"]) / truth["volume"] < 1e-6
    S.new_part()
    r = S.open_part(str(f))
    assert "natively" in r["geometry"] and abs(vol() - truth["volume"]) / truth["volume"] < 1e-6
    S.new_part()
    S.import_file(str(XT_DATA / "torus.x_b"))
    assert abs(vol() - 2 * PI ** 2 * 30 * 10 ** 2) < 1e-3


def test_transient_thermal_lumped_cooling():
    """Small copper cube cooling by convection (Biot << 1) follows the lumped-capacitance curve
    T = Tamb + (T0 - Tamb) exp(-h A t / (rho c V))."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-5, -5], [5, 5])
    S.extrude(sk, depth=10)
    S.set_material("Copper")
    for f in S.get_topology()["faces"]:
        S.thermal_add_convection(f["id"], 50.0, 20.0)
    r = S.thermal_run(transient=True, duration=150, steps=50, initial_temperature=100.0, element_size=2.0)
    tau = 8900e-9 * 385.0 * 1000.0 / (50e-6 * 600.0)
    assert r["history"][-1]["mean"] - 20 == pytest.approx(80 * math.exp(-150 / tau), rel=2e-3)
    assert r["history"][0]["mean"] == pytest.approx(100.0)
    assert len(S.thermal_results()["temperature"]) == 6


def test_forming_extruded_flange_and_rib():
    def plate():
        S.new_part()
        sk = S.create_sketch("Top")["sketch"]
        S.sketch_rectangle(sk, [0, 0], [100, 50])
        S.base_flange(sk, thickness=2)
        return [f["id"] for f in S.get_topology()["faces"] if f["normal"][1] > 0.99][0]
    top = plate()
    S.forming_tool_extruded_flange(top, [50, 2, -25], diameter=10, height=6)
    # pierced hole r=5 through t=2, collar (r 5..7) 6 mm tall above the face
    assert vol() == pytest.approx(10000 - PI * 25 * 2 + PI * (49 - 25) * 6)
    assert S.get_mass_properties()["bounding_box"]["max"][1] == pytest.approx(8) and S.check_geometry()["valid"]
    top = plate()
    S.forming_tool_rib(top, [50, 2, -25], length=40, width=8, height=2.5, angle=0)
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["max"][1] == pytest.approx(4.5, abs=1e-6) and S.check_geometry()["valid"]
    # a pressed bead keeps the sheet thickness: the material gained is small compared with the bead volume
    assert 10000 < vol() < 10000 + 40 * 8 * 2.5
    S.edit_feature(S.get_model()["features"][-1]["name"], {"reverse": True})
    assert S.get_mass_properties()["bounding_box"]["min"][1] == pytest.approx(-2.5, abs=1e-6)


def test_miter_flange_profile_bend_radius():
    """An L profile (15 up, 10 across) gets the sheet bend radius at its corner: inner radius r, outer r + t."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 50])
    S.base_flange(sk, thickness=2, bend_radius=2)
    edge = [e for e in S.get_topology()["edges"] if e["type"] == "line" and abs(e["point"][0] - 100) < 1e-6
            and abs(e["point"][1] - 2) < 1e-6][0]
    z0 = min(edge["point"][2] - 25, edge["point"][2] + 25)
    S.reference_plane("Front", offset=z0)
    prof = S.create_sketch("Plane1")["sketch"]
    S.sketch_line(prof, [100, 2], [100, 17])
    S.sketch_line(prof, [100, 17], [110, 17])
    v0 = vol()
    t, r, L1, L2 = 2.0, 2.0, 15.0, 10.0
    arc = PI / 4 * ((r + t) ** 2 - r ** 2)
    outer = t * (L1 + L2 - 2 * (r + t)) + arc  # sketch lines on the outside of the bend
    inner = t * (L1 + L2 - 2 * r) + arc  # sketch lines on the inside
    f = S.miter_flange(prof, edge["id"], flip=True)["feature"]
    a = (vol() - v0) / 50
    assert S.check_geometry()["valid"]
    assert a == pytest.approx(outer, rel=1e-6) or a == pytest.approx(inner, rel=1e-6)
    S.edit_feature(f, {"flip": False})
    b = (vol() - v0) / 50
    assert {round(a, 6), round(b, 6)} == {round(outer, 6), round(inner, 6)}
    # flat pattern: with K = 0.5 the developed strip carries exactly the folded material, in the sheet plane
    S.edit_feature(f, {"flip": True})
    folded = vol()
    info = S.flatten(True)
    assert vol() == pytest.approx(folded, rel=1e-9) and info["bounding_box"]["max"][1] == pytest.approx(2)
    assert any(b.get("miter") for b in info["bends"])
    S.flatten(False)
    assert vol() == pytest.approx(folded, rel=1e-9)


def test_parasolid_writer_roundtrip(tmp_path):
    """openswx -> .x_t -> clean-room reader gives the same solid (SOLIDWORKS import of the same files is
    checked by tools/difftest/xt_roundtrip.py)."""
    from openswx.features import mass_properties
    from openswx.parasolid import read_xt_shape
    from openswx.parasolid_write import write_xt
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
    from openswx.features import mass_properties
    from openswx.parasolid import read_xt_shape
    truth = json.loads((XT_DATA / "truth.json").read_text())["spline_fillet"]
    shape, notes = read_xt_shape(str(XT_DATA / "spline_fillet.x_t"))
    mp = mass_properties(shape)
    assert shape.is_valid and not notes
    assert abs(mp["volume"] - truth["volume"]) / truth["volume"] < 1e-4
    assert abs(mp["surface_area"] - truth["area"]) / truth["area"] < 3e-4


def _sw_package(streams: dict) -> bytes:
    """A minimal SOLIDWORKS 2015+ style package: deflated records with nibble-swapped names."""
    import struct
    import zlib
    out = b""
    for name, data in streams.items():
        nm = bytes(((c & 0x0F) << 4) | (c >> 4) for c in name.encode())
        z = zlib.compressobj(9, zlib.DEFLATED, -15)
        payload = z.compress(data) + z.flush()
        out += b"\0\0\0\0\x14\x00\x06\x00\x08\x00" + struct.pack("<5I", 0, zlib.crc32(data), len(payload), len(data), len(nm)) + nm + payload
    return out


def test_sldasm_native(tmp_path):
    """Component tree XML -> parts read natively -> placed with the row-major swTransform."""
    import struct
    import zlib
    xt = (XT_DATA / "drafted_cylinder_partition.xtp").read_bytes()
    (tmp_path / "cyl.SLDPRT").write_bytes(_sw_package({"Contents/Config-0-Partition": struct.pack("<I", 0) + bytes(16) + zlib.compress(xt)}))
    ns = "http://www.solidworks.com/sw2003/schema"
    rz = "0 1 0 0 -1 0 0 0 0 0 1 0"  # rows = component axes in the parent: x -> +y, y -> -x (90 deg about Z)
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><swSolidWorks xmlns="{ns}"><swHeader>'
           r'<swFile id="3" swDocType="ASSEMBLY" swPath="D:\elsewhere\asm.SLDASM"/>'
           r'<swFile id="6" swDocType="PART" swPath="D:\elsewhere\cyl.SLDPRT"/></swHeader><swModelList>'
           '<swModel id="5" swName="cyl" swConfigurationId="0" swFileRef="6"/>'
           '<swModel id="2" swName="asm" swFileRef="3">'
           '<swReference id="4" swName="cyl" swReferenceNumber="1" swComponentName="cyl" swSuppressed="NO" swHidden="NO" '
           'swModelRef="5" swConfigurationId="0" swTransform="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>'
           '<swReference id="7" swName="cyl" swReferenceNumber="2" swComponentName="cyl" swSuppressed="NO" swHidden="NO" '
           f'swModelRef="5" swConfigurationId="0" swTransform="{rz} 0.1 0 0 1"/></swModel></swModelList></swSolidWorks>')
    (tmp_path / "asm.SLDASM").write_bytes(_sw_package({"swXmlContents/COMPINSTANCETREE": xml.encode()}))
    from openswx.sldprt import assembly_tree
    tree = assembly_tree(str(tmp_path / "asm.SLDASM"))
    assert [c["name"] for c in tree["components"]] == ["cyl-1", "cyl-2"]
    assert tree["components"][1]["rotation"][1][0] == pytest.approx(1.0)  # component x maps to parent +y
    r = S.open_part(str(tmp_path / "asm.SLDASM"))
    assert "natively" in r["geometry"] and len(r["model"]["components"]) == 2
    assert r["model"]["components"][1]["position"] == pytest.approx([100, 0, 0])
    assert r["model"]["components"][1]["rotation_deg"][2] == pytest.approx(90)
    # mates: Config-0-MatesList names faces by SOLIDWORKS face ids (FACE_ID_2001 attributes in the part)
    stack = xml.replace(f'swTransform="{rz} 0.1 0 0 1"', 'swTransform="1 0 0 0 0 1 0 0 0 0 1 0 0 0.03 0 1"')
    tag = lambda name: b"\xff\xff\x01\x00" + struct.pack("<H", len(name)) + name  # noqa: E731
    u16 = lambda text: b"\x05" + text.encode("utf-16-le") + b"\x01"  # noqa: E731
    mates = (tag(b"moMateCoincident") + u16("Coincident1") + tag(b"moPlaneRef_w") + u16("moEndFaceSurfIdRep_c,32,1,0, ")
             + u16("cyl-1@asm") + u16("moEndFaceSurfIdRep_c,32,0,0, ") + u16("cyl-2@asm")
             + u16("Coincident2") + u16("cyl-1@asm") + u16("Front@cyl-1@asm"))  # untagged: plane to assembly
    (tmp_path / "stack.SLDASM").write_bytes(_sw_package({"swXmlContents/COMPINSTANCETREE": stack.encode(),
                                                         "Contents/Config-0-MatesList": mates}))
    r = S.open_part(str(tmp_path / "stack.SLDASM"))
    assert r.get("notes", [r])[0] == "2 of 2 mates imported"
    m = S.get_model()
    # top cap onto bottom cap; cyl-1's Front plane on the assembly's own (the "(assembly)" frame, fixed)
    assert [(x["type"], x["aligned"]) for x in m["mates"]] == [("coincident", False), ("coincident", True)]
    assert m["mates"][1]["b"]["component"] == "(assembly)"
    assert [c["fixed"] for c in m["components"]] == [True, False]  # the mated component is free again
    assert m["components"][1]["position"] == pytest.approx([0, 30, 0])  # and solving keeps it in place


SW_DATA = Path(__file__).parent / "data" / "sw"


def _sw_part(tmp_path, name: str) -> str:
    """A .sldprt holding a SOLIDWORKS 2025 part's feature-tree and Parasolid streams (tests/data/sw)."""
    f = tmp_path / f"{name}.SLDPRT"
    f.write_bytes(_sw_package({"Contents/Config-0-ResolvedFeatures": (SW_DATA / f"{name}.features").read_bytes(),
                               "Contents/Config-0-Partition": (SW_DATA / f"{name}.partition").read_bytes()}))
    return str(f)


def test_sw_feature_tree(tmp_path):
    """Feature history from ResolvedFeatures: tree order, classes, dimensions, end conditions, feature ids."""
    from openswx.sw_tree import read_tree
    tree = {f["name"]: f for f in read_tree(_sw_part(tmp_path, "cut_thru"))}
    assert list(tree)[3:] == ["Sketch1", "Boss-Extrude1", "Sketch2", "Cut-Extrude1"]
    assert tree["Boss-Extrude1"]["cls"] == "moExtrusion_c" and tree["Cut-Extrude1"]["cls"] == "moICE_c"
    assert tree["Boss-Extrude1"]["params"] == [{"name": "D1", "value": pytest.approx(0.02), "kind": "length"}]
    assert (tree["Boss-Extrude1"]["end_condition"], tree["Cut-Extrude1"]["end_condition"]) == (0, 1)  # Blind, Through All
    assert (tree["Boss-Extrude1"]["id"], tree["Cut-Extrude1"]["id"]) == (32, 40)  # FACE_ID_2001 feature ids
    ext = {f["name"]: f for f in read_tree(_sw_part(tmp_path, "ext_mid"))}["Boss-Extrude1"]
    assert ext["end_condition"] == 6  # Mid Plane
    rev = {f["name"]: f for f in read_tree(_sw_part(tmp_path, "ext_rev"))}["Boss-Extrude1"]
    assert (ext["reverse"], rev["reverse"]) == (False, True)  # the end spec's direction flag
    draft = {f["name"]: f for f in read_tree(_sw_part(tmp_path, "ext_draft5"))}["Boss-Extrude1"]["params"]
    assert [p["kind"] for p in draft] == ["length", "angle"] and math.degrees(draft[1]["value"]) == pytest.approx(5)
    pat = {f["name"]: f for f in read_tree(_sw_part(tmp_path, "patterned_holes"))}["LPattern1"]
    assert [(p["kind"], p["value"]) for p in pat["params"][:3]] == [("integer", 2), ("length", pytest.approx(0.02)),
                                                                     ("integer", 1)]


def test_sw_sketch_decode(tmp_path):
    """Sketch entities and frames: points (mm), arc centre/start/end, circles, a sketch on a face."""
    from openswx.sw_sketch import read_sketches
    sk = read_sketches(_sw_part(tmp_path, "sk_arc"))["Sketch1"]
    (c, s, e), = sk["arcs"]
    assert [sk["points"][i] for i in (c, s, e)] == [pytest.approx((13, 17)), pytest.approx((22, 17)), pytest.approx((13, 26))]
    assert sk["R"].tolist() == [[1, 0, 0], [0, 0, 1], [0, -1, 0]]  # Top Plane: sketch x -> +X, y -> -Z, normal +Y
    circ = read_sketches(_sw_part(tmp_path, "sk_circle"))["Sketch1"]
    (c, s, e), = circ["arcs"]
    assert s == e and math.dist(circ["points"][c], circ["points"][s]) == pytest.approx(9)
    face = read_sketches(_sw_part(tmp_path, "cut_thru"))["Sketch2"]  # drawn on the block's top face
    assert face["t"].tolist() == pytest.approx([0, 20, 0]) and face["R"][:, 2].tolist() == pytest.approx([0, 1, 0])
    ell = read_sketches(_sw_part(tmp_path, "ell_ext"))["Sketch1"]  # an ellipse: centre, major end, minor radius
    (c, m, minor), = ell["ellipses"]
    assert ell["points"][c] == pytest.approx((5, 3)) and ell["points"][m] == pytest.approx((25, 3)) and minor == pytest.approx(8)
    # segment records: construction flags (a revolve centerline, a centre rectangle's diagonals)
    assert frozenset((1, 2)) in read_sketches(_sw_part(tmp_path, "rev_360"))["Sketch1"]["construction"]
    rect = read_sketches(_sw_part(tmp_path, "cut_thru"))["Sketch1"]
    assert rect["construction"] == {frozenset((3, 2)), frozenset((4, 5))}
    assert {frozenset(ln) for ln in rect["lines"]} - rect["construction"] == {
        frozenset((3, 5)), frozenset((3, 4)), frozenset((2, 4)), frozenset((2, 5))}  # the rectangle itself
    spl = read_sketches(_sw_part(tmp_path, "spline_extrude"))["Sketch1"]["splines"][0]["bspline"]
    assert spl["degree"] == 3 and len(spl["poles"]) == 7 and spl["poles"][0] == pytest.approx([-40, 0])


@pytest.mark.parametrize("name,features", [
    ("cut_thru", ["Sketch1", "Boss-Extrude1", "Sketch2", "Cut-Extrude1"]),
    ("cut_blind8", ["Sketch1", "Boss-Extrude1", "Sketch2", "Cut-Extrude1"]),
    ("ext_mid", ["Sketch1", "Boss-Extrude1"]), ("ext_rev", ["Sketch1", "Boss-Extrude1"]), ("ext_draft5", ["Sketch1", "Boss-Extrude1"]),
    ("fil_r5", ["Sketch1", "Boss-Extrude1", "Fillet1"]), ("shell_t2", ["Sketch1", "Boss-Extrude1", "Shell1"]),
    ("rev_360", ["Sketch1", "Revolve1"]), ("torus", ["Sketch1", "Revolve1"]),
    ("chamfered_block", ["Sketch1", "Boss-Extrude1", "Chamfer1"]),
    ("vertex_chamfer", ["Sketch1", "Boss-Extrude1", "Chamfer1"]),
    ("patterned_holes", ["Sketch1", "Boss-Extrude1", "Sketch2", "Cut-Extrude1", "LPattern1"]),
    ("spline_extrude", ["Sketch1", "Boss-Extrude1"]),
    ("ell_ext", ["Sketch1", "Boss-Extrude1"]),
])
def test_sw_rebuild(tmp_path, name, features):
    """The decoded history rebuilt with openswx features reproduces the Parasolid body stored in the file."""
    from openswx.document import PartDocument
    from openswx.sw_rebuild import rebuild_part
    doc = PartDocument("t")
    r = rebuild_part(_sw_part(tmp_path, name), doc)
    assert r["features"] == features and [f["name"] for f in doc.features] == features
    assert abs(r["volume"] - r["stored_volume"]) <= 2e-3 * r["stored_volume"]
    assert abs(r["area"] - r["stored_area"]) <= 1e-3 * r["stored_area"]


def test_sw_rebuild_imports_undecoded_feature(tmp_path, monkeypatch):
    """A feature type that is not decoded comes in as its exact contribution, read off the stored body next to
    the faces SOLIDWORKS tagged with its id; the features around it stay parametric."""
    import openswx.sw_tree as sw_tree
    from openswx.document import PartDocument
    from openswx.sw_rebuild import rebuild_part
    real = sw_tree.read_tree

    def tree(path, config=None):
        return [{**f, "cls": "moSomethingNew_c"} if f["name"] == "Fillet1" else f for f in real(path, config)]
    monkeypatch.setattr(sw_tree, "read_tree", tree)
    doc = PartDocument("t")
    r = rebuild_part(_sw_part(tmp_path, "fil_r5"), doc)
    assert r["imported"] == ["Fillet1"] and [f["type"] for f in doc.features] == ["sketch", "extrude", "import"]
    assert abs(r["volume"] - r["stored_volume"]) <= 1e-3 * r["stored_volume"]
    # but the feature that makes the first solid must be editable, else the part opens as its stored body
    from openswx.sw_rebuild import RebuildError
    monkeypatch.setattr(sw_tree, "read_tree", lambda path, config=None: [
        {**f, "cls": "moSomethingNew_c"} if f["name"] == "Boss-Extrude1" else f for f in real(path, config)])
    with pytest.raises(RebuildError, match="base feature"):
        rebuild_part(_sw_part(tmp_path, "fil_r5"), PartDocument("t"))


def test_revolve_half_section():
    """A revolve whose sketch is not decoded takes its profile from the stored body: the half-section on one
    side of the axis, closed along it, turned about the axis to where later cuts (holes in the sketch plane)
    leave the most - revolving it gives the body before the cuts."""
    import numpy as np
    from build123d import Cylinder, Pos, Rot
    from openswx import sw_rebuild as R
    turned = Rot(0, 90, 0) * Cylinder(5, 20)  # along X, x -10..10
    holes = [Pos(10, 0, z) * Rot(0, 90, 0) * Cylinder(0.5, 4) for z in (3, -3)]  # off the axis, in the XZ plane
    body = turned - holes[0] - holes[1]
    oracle = R._Oracle(body)
    sk = {"R": np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]]), "t": np.zeros(3)}  # the XZ plane
    R._REVOLVE_AXES[0] = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    try:
        sketches = R._section_sketches(oracle, sk)
    finally:
        R._REVOLVE_AXES[0] = None
    S.new_part("HalfSec")
    best = 0.0
    for data in sketches:
        S.APP.doc.add_feature("sketch", {"plane": R._plane(sk["R"], sk["t"]), "sketch": data}, name="P")
        try:
            S.APP.doc.add_feature("revolve", {"sketch": "P", "axis": "X", "angle": 360.0}, name="Rev")
            best = max(best, S.APP.doc.body.volume)
        except Exception:
            pass
        for nm in ("Rev", "P"):
            if any(f["name"] == nm for f in S.APP.doc.features):
                S.APP.doc.delete_feature(nm, with_children=False)
    assert best == pytest.approx(turned.volume, rel=1e-6)
    S.close_document("HalfSec")


@pytest.mark.parametrize("htype,extra", [("countersink", {"csk_diameter": 9.0, "csk_angle": 90.0}),
                                         ("counterbore", {"cbore_diameter": 10.0, "cbore_depth": 3.0}),
                                         ("simple", {"drill_point": True})])
def test_hole_from_faces(htype, extra):
    """Hole Wizard features are rebuilt as openswx holes read off the faces they made (cylinders, cone): a
    cone as wide as the hole is a blind hole's drill point (entered from the other end), not a countersink."""
    from openswx.sw_rebuild import _hole_variants
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-20, -20], [20, 20])
    S.extrude(sk, depth=10)
    top = max(S.get_topology()["faces"], key=lambda f: f["point"][1])["id"]
    S.hole(top, [[0, 0]], diameter=5.0, depth=6.0, type=htype, **extra)
    body = S.APP.doc.body
    faces = [f for f in body.faces() if f.geom_type.name in ("CYLINDER", "CONE")]
    holes = _hole_variants(faces)
    assert len(holes) == 1  # one hole: its variants
    v = next(v for v in holes[0] if v["face"]["normal"][1] > 0 and "end_condition" not in v)
    assert v["type"] == htype and v["diameter"] == pytest.approx(5.0) and v["depth"] == pytest.approx(6.0)
    assert v["face"]["origin"] == pytest.approx([0, 10, 0])
    if htype == "countersink":
        assert (v["csk_diameter"], v["csk_angle"]) == (pytest.approx(9.0), pytest.approx(90.0))
    elif extra.get("drill_point"):
        assert v["drill_point"] and all(w["face"]["normal"][1] > 0 for w in holes[0])  # only from the top
    else:
        assert (v["cbore_diameter"], v["cbore_depth"]) == (pytest.approx(10.0), pytest.approx(3.0))
    S.new_part()


def test_solidworks_drawing_notes(tmp_path, monkeypatch):
    """A .slddrw sheet's notes (DetailItem records after the views: lines, font, then x, y, 0 in metres from
    the lower left) come in as openswx notes, symbol tags as characters; sheet-format labels stay out."""
    import struct
    from openswx import sw_drawing
    u = lambda s: b"\x00\x07" + s.encode("utf-16-le") + b"\x00\x00\x03"  # noqa: E731
    at = lambda x, y: b"\x00" * 24 + struct.pack("<3d", x / 1000, y / 1000, -0.0)  # noqa: E731
    model = "C:\\Somewhere\\Else\\plate.SLDPRT"
    definition = (u("Sheet1") + u("Drawing View1") + u(model) + u("*Front")
                  + u("DetailItem7") + u("<FONT size=20PTS>90<MOD-DEG> ROTATION") + u("FOR FULL OPEN")
                  + u("Century Gothic") + at(60.0, 103.5)
                  + u("DetailItem8") + u("NEXT ASSY") + u("Century Gothic") + at(150.0, 20.0))
    names = b"\x01\x00" + u("Sheet1")
    monkeypatch.setattr(sw_drawing, "read_streams", lambda p: {"Contents/Definition": definition, "SheetPreviews/SheetNames": names})
    drw = sw_drawing.read_drawing(str(tmp_path / "plate.slddrw"))
    assert [(n["text"], n["position"], n["sheet"]) for n in drw["notes"]] == [
        ("90\u00b0 ROTATION\nFOR FULL OPEN", (60.0, 103.5), "Sheet1")]
    assert drw["notes"][0]["size"] == pytest.approx(20 * 25.4 / 72, abs=0.01)
    S.new_part("plate")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [100, 60])
    S.extrude(sk, depth=10)
    S.save_part(str(tmp_path / "plate.oswprt"))
    S.close_document("plate")
    monkeypatch.setattr(sw_drawing, "find_model", lambda p, models: tmp_path / "plate.oswprt")
    (tmp_path / "plate.slddrw").write_bytes(b"")
    r = S.open_part(str(tmp_path / "plate.slddrw"))
    assert r["notes"] == 1
    note = S.APP.active_doc.drawing["sheets"][0]["notes"][0]
    assert note["text"].startswith("90\u00b0 ROTATION") and note["x"] == pytest.approx(60.0)
    S.close_document(S.APP.active_doc.name)


def test_parasolid_extent_counts_edge_circles():
    """The runaway-geometry check's reach: a pipe's full-circle edges have no vertices (5.5 m of pipe was
    rejected), so the edges' circles count; a construction circle no edge uses (500 m, on a sample bat)
    does not."""
    from types import SimpleNamespace
    from openswx.parasolid import _Builder
    nodes = {1: (31, {"centre": [0.0, 0.0, 5.4864], "radius": 0.05715}), 2: (16, {"curve": 1}),
             3: (31, {"centre": [0.0, 0.0, 0.0], "radius": 500.0}), 4: (29, {"pvec": [0.01, 0.0, 0.0]})}
    reach = _Builder(SimpleNamespace(nodes=nodes)).extent()
    assert reach == pytest.approx(2 * (5.4864 + 0.05715) * 1000 + 10)


def test_tagged_faces_extended_by_mirror():
    """A feature's tagged face that a later mirror merged with its copy (a pipe run, mirrored to full length)
    still matches the half the feature made; a candidate that falls short of a tagged end cap does not."""
    from build123d import Box, Pos
    from openswx.features import _valid
    from openswx.sw_rebuild import _faces_on_body
    full = Box(40, 10, 10)  # the stored body: x -20 .. 20
    side = max(full.faces(), key=lambda f: f.center().Y)  # one face across both halves
    cap = max(full.faces(), key=lambda f: f.center().X)  # the end at x = 20
    half = Pos(10, 0, 0) * Box(20, 10, 10)  # the feature itself: x 0 .. 20
    short = Pos(5, 0, 0) * Box(10, 10, 10)  # too short: x 0 .. 10
    assert _faces_on_body(half, [side, cap])
    assert not _faces_on_body(short, [side, cap])
    assert _valid(half) is half  # valid shapes pass through untouched


def test_sw_sketch_dimensions_drive(tmp_path):
    """Sketch dimensions are decoded and drive the rebuilt sketch: editing the SOLIDWORKS width resizes the part."""
    S.new_part()
    r = S.open_part(_sw_part(tmp_path, "dim_rect"))
    assert r["geometry"].startswith("feature history rebuilt") and vol() == pytest.approx(60 * 40 * 10)
    dims = S.APP.doc.find("Sketch1")["params"]["sketch"]["dimensions"]
    assert sorted(round(d["value"], 6) for d in dims) == [40.0, 60.0]
    width = next(d["id"] for d in dims if abs(d["value"] - 60) < 1e-6)
    S.set_dimension(width, 80, sketch="Sketch1")
    assert vol() == pytest.approx(80 * 40 * 10)
    S.new_part()


def test_open_sldprt_history_editable(tmp_path):
    """open_part rebuilds the history; the decoded dimensions then drive the model like any openswx part."""
    S.new_part()
    r = S.open_part(_sw_part(tmp_path, "ext_mid"))
    assert r["geometry"].startswith("feature history rebuilt") and r["feature_history"]["features"] == ["Sketch1", "Boss-Extrude1"]
    assert vol() == pytest.approx(100 * 60 * 20)
    S.edit_feature("Boss-Extrude1", {"depth": 30})
    assert vol() == pytest.approx(100 * 60 * 30)
    S.new_part()
    r = S.open_part(_sw_part(tmp_path, "ext_mid"), history=False)
    assert "natively" in r["geometry"] and [f["type"] for f in S.APP.doc.features] == ["import"]
    S.new_part()


@pytest.mark.parametrize("closed,side,volume", [
    (False, "one", 1360.0), (False, "other", 1440.0), (False, "mid", 1400.0),  # L of 40 + 30 mm, 2 mm wall, 10 deep
    (True, "one", 2960.0), (True, "other", 2640.0), (True, "mid", 2800.0),  # 40 x 30 rectangle
])
def test_thin_extrude(closed, side, volume):
    """Thin feature: walls along open or closed sketch curves, sharp corners, on either side or centred."""
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    if closed:
        S.sketch_rectangle(sk, [0, 0], [40, 30])
    else:
        S.sketch_line(sk, [0, 0], [40, 0])
        S.sketch_line(sk, [40, 0], [40, 30])
    S.extrude(sk, depth=10, thin_thickness=2, thin_side=side)
    assert vol() == pytest.approx(volume)


def test_thin_extrude_auto_fillet():
    """Thin feature with auto-fillet corners: the open L is rounded (radius 5) before it is thickened."""
    import math
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_line(sk, [0, 0], [40, 0])
    S.sketch_line(sk, [40, 0], [40, 30])
    S.extrude(sk, depth=10, thin_thickness=2, thin_side="mid", thin_fillet=5)
    # centre line length 35 + 25 + quarter circle of radius 5; area = length x 2 (offset centred on it)
    assert vol() == pytest.approx((35 + 25 + math.pi * 5 / 2) * 2 * 10, rel=1e-6)


def test_sw_rebuild_draft():
    """A draft (not decoded: neutral plane and faces) rebuilt by trying the body's planes, scored against
    the stored body: a 10 degree draft on the sides of a block."""
    from openswx import sw_rebuild as rb
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 30])
    S.extrude(sk, depth=10)
    doc = S.APP.doc
    sides = [f for f in doc.body.faces() if abs(f.normal_at().Y) < 1e-6]
    from openswx.geom import face_ref
    top = max(doc.body.faces(), key=lambda f: f.center().Y)
    c, n = top.center(), top.normal_at()
    neutral = {"origin": [c.X, c.Y, c.Z], "normal": [n.X, n.Y, n.Z], "x_dir": [1, 0, 0]}
    doc.add_feature("draft", {"neutral": neutral, "faces": [face_ref(f) for f in sides], "angle": 10.0}, name="D")
    stored = doc.body
    doc.delete_feature("D", with_children=False)
    oracle = rb._Oracle(stored)
    rb._try(doc, oracle, "draft", "Draft1", rb._draft_variants(doc, oracle, [10.0]))
    assert doc.body.volume == pytest.approx(stored.volume, rel=1e-6)


def test_sw_rebuild_sketch_helpers():
    """Rebuild helpers on decoded sketches: a rectangle's unflagged diagonal is a chord (construction); a
    section's loops around the origin are a member's profile, the member beside it is left out."""
    from openswx import sw_rebuild as rb
    sk = {"points": {1: (0, 0), 2: (10, 0), 3: (10, 5), 4: (0, 5)},
          "lines": [(1, 2), (2, 3), (3, 4), (4, 1), (1, 3)], "arcs": []}
    assert rb._chords(sk) == {frozenset((1, 3))}
    assert rb._chords({**sk, "lines": sk["lines"][:4]}) == set()

    def square(x0, y0, w, k):
        pts = {f"P{k}{i}": p for i, p in enumerate([(x0, y0), (x0 + w, y0), (x0 + w, y0 + w), (x0, y0 + w)])}
        ids = list(pts)
        ents = {f"L{k}{i}": {"type": "line", "p1": ids[i], "p2": ids[(i + 1) % 4]} for i in range(4)}
        return pts, ents
    p1, e1 = square(-1, -1, 2, "a")
    p2, e2 = square(5, -1, 2, "b")
    out = rb._around_origin({"points": {**p1, **p2}, "entities": {**e1, **e2}})
    assert set(out["entities"]) == set(e1)


def test_sw_sketch_relations_decoded(tmp_path):
    """Sketch relations read from the stream (swConstraintType_e code + line references): perpendicular
    (8) and parallel (7) between two lines, carried into the rebuilt openswx sketch."""
    from openswx.sw_rebuild import _sketch_dict
    from openswx.sw_sketch import read_sketches
    for case, code, kind in (("rel_perp", 8, "perpendicular"), ("rel_par", 7, "parallel")):
        sk = read_sketches(_sw_part(tmp_path, case))["Sketch1"]
        assert [r["type"] for r in sk["relations"]] == [code]
        assert sorted(sk["relations"][0]["refs"]) == [("sgLineHandle", 0), ("sgLineHandle", 1)]
        data, _ = _sketch_dict(sk)
        assert [r["type"] for r in data["relations"]] == [kind]


def test_sw_rebuild_inferred_relations():
    """Relations read off decoded geometry: perpendicular slanted lines, arcs tangent at a shared end,
    concentric circles with separate centre points; the sketch still solves without moving."""
    from openswx import sw_rebuild as rb
    from openswx.sketch import Sketch
    sk = {"points": {1: (0, 0), 2: (3, 4), 3: (-4, 3),       # two perpendicular slanted lines from 1
                     10: (20, 0), 11: (25, 0), 12: (30, 0),   # arc A: centre 10 from 11 ... ; arc B: centre 13
                     13: (35, 0), 14: (40, 0),
                     20: (50, 50), 21: (52, 50), 22: (50, 50), 23: (55, 50)},
          "lines": [(1, 2), (1, 3)], "arcs": [(10, 12, 11), (13, 12, 14), (20, 21, 21), (22, 23, 23)],
          "splines": [], "ellipses": []}
    data, _ = rb._sketch_dict(sk)
    types = sorted(r["type"] for r in data["relations"])
    assert types.count("perpendicular") == 1 and types.count("concentric") == 1 and types.count("tangent") == 1
    s = Sketch(data)
    before = dict(s.points)
    s.solve()
    assert all(abs(a - b) < 1e-6 for k in before for a, b in zip(before[k], s.points[k]))


def test_no_solidworks_dependency():
    """openswx must work without SOLIDWORKS: no COM automation anywhere in the package, and opening a
    SOLIDWORKS part loads no COM module (tools/difftest alone talks to SOLIDWORKS, as a development oracle)."""
    import subprocess
    import sys
    pkg = Path(__file__).parent.parent / "openswx"
    for f in pkg.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert not any(k in text for k in ("win32com", "pythoncom", "comtypes", "SldWorks.Application")), f
    code = ("import sys; from openswx import session as S; S.open_part(sys.argv[1]); "
            "print(any(m.split('.')[0] in ('win32com', 'pythoncom', 'comtypes') for m in sys.modules))")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = _sw_part(Path(d), "ext_mid")
        out = subprocess.run([sys.executable, "-c", code, f], capture_output=True, text=True, timeout=300,
                             cwd=str(pkg.parent))
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_mate_to_component_reference_planes():
    """'Comp/Front Plane' (Top / Right) mates a component's own reference plane, as in SOLIDWORKS."""
    block()
    part = S.get_model()["name"]
    S.new_assembly("PlaneMates")
    a = S.insert_component(part)["component"]
    b = S.insert_component(part, position=[0, 0, 37])["component"]
    S.add_mate("coincident", f"{a}/Front Plane", f"{b}/Front Plane", aligned=True)
    comps = {c["name"]: c for c in S.get_model()["components"]}
    assert comps[b]["position"][2] == pytest.approx(0, abs=1e-6)
    S.add_mate("distance", f"{a}/Right Plane", f"{b}/Right Plane", value=120, aligned=True)
    comps = {c["name"]: c for c in S.get_model()["components"]}
    assert comps[b]["position"][0] == pytest.approx(120, abs=1e-6)


def test_thermal_heat_per_boundary():
    """Two fixed temperatures: the heat in through one equals the heat out through the other, k A dT / L."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-50, -5], [50, 5])
    S.extrude(sk, depth=10)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    xmin = min(faces, key=lambda f: f["point"][0])["id"]
    xmax = max(faces, key=lambda f: f["point"][0])["id"]
    S.thermal_add_temperature(xmin, 100.0)
    S.thermal_add_temperature(xmax, 20.0)
    q_in, q_out = S.thermal_run()["heat_in_per_temperature_boundary"]
    assert q_in == pytest.approx(-q_out, rel=1e-9)
    assert q_in == pytest.approx(47.0 / 1000 * 100 * 80 / 100, rel=0.03)


def test_planes_shown_unless_hidden():
    """Planes are shown (translucent) unless hidden: the standard planes by name, a plane feature by its flag;
    the hidden standard planes are saved with the part and undo restores them."""
    S.new_part("Planes")
    flags = lambda: {p["name"]: p["hidden"] for p in S.APP.doc.plane_overlays()}  # noqa: E731
    assert flags() == {"Front": False, "Top": False, "Right": False}
    S.set_feature_visibility("Top Plane", True)
    assert flags()["Top"] is True and flags()["Front"] is False
    assert S.APP.doc.to_dict()["appearance"]["hidden"] == ["Top"]
    S.undo()
    assert flags()["Top"] is False
    S.close_document("Planes")


def test_smart_dimension_placement_and_supplementary_angle():
    """Smart Dimension as placed in the UI: the label position is kept (and can be moved), an angle placed on
    the other side of the lines measures 180 - their angle and drives them that way, and a sloped line's
    horizontal extent is a horizontal distance between its end points."""
    import math
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    a = S.sketch_line(sk, [0, 0], [40, 0])["result"]
    b = S.sketch_line(sk, "P2", [30, 30])["result"]
    S.sketch_relation(sk, "fix", [a["entity"]])
    d = S.sketch_dimension(sk, "angle", [a["entity"], b["entity"]], position=[-10, 5], supplement=True)["result"]
    assert d["value"] == pytest.approx(135) and not d["driven"]
    S.set_dimension(d["dimension"], 120)
    pts = S.get_feature(sk)["params"]["sketch"]["points"]
    end = pts[b["points"][1]]
    assert math.degrees(math.atan2(end[1], end[0])) == pytest.approx(60, abs=1e-4)
    S.sketch_move_dimension(sk, d["dimension"], [-20, 8])
    dims = S.get_feature(sk)["params"]["sketch"]["dimensions"]
    assert dims[0]["pos"] == [-20, 8] and dims[0]["supplement"]
    c = S.sketch_line(sk, [50, 0], [80, 20])["result"]
    h = S.sketch_dimension(sk, "hdistance", c["points"], position=[65, 30])["result"]
    assert h["value"] == pytest.approx(30)
    S.set_dimension(h["dimension"], 45)
    pts = S.get_feature(sk)["params"]["sketch"]["points"]
    assert pts[c["points"][1]][0] - pts[c["points"][0]][0] == pytest.approx(45, abs=1e-6)


def test_feature_reorder_folders_and_entity_drag():
    """FeatureManager drag-reorder (refused when it breaks a reference), folders, and dragging sketch
    entities (a circle grabbed on its edge resizes, a line moves)."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 20])
    S.extrude(sk, depth=10)
    S.create_sketch("Front")
    names = [f["name"] for f in S.get_model()["features"]]
    with pytest.raises(Exception):  # the extrude cannot come before its sketch
        S.reorder_feature(sk, "Boss-Extrude1")
    assert [f["name"] for f in S.get_model()["features"]] == names
    S.reorder_feature("Sketch2", None)  # an independent sketch can go to the top
    assert S.get_model()["features"][0]["name"] == "Sketch2"
    S.add_folder("Folder1", ["Boss-Extrude1"])
    assert S.get_model()["folders"] == [{"name": "Folder1", "features": ["Boss-Extrude1"]}]
    S.rename_feature("Boss-Extrude1", "Base")
    assert S.get_model()["folders"][0]["features"] == ["Base"]
    S.remove_folder("Folder1")
    assert S.get_model()["folders"] == []
    sk3 = S.create_sketch("Right")["sketch"]
    c = S.sketch_circle(sk3, [0, 0], 5)["result"]["entity"]
    line = S.sketch_line(sk3, [20, 0], [30, 0])["result"]
    S.sketch_drag_entity(sk3, c, [5, 0], [8, 0])
    sketch = S.get_feature(sk3)["params"]["sketch"]
    assert sketch["entities"][c]["r"] == pytest.approx(8)
    S.sketch_drag_entity(sk3, line["entity"], [25, 0], [25, 4])
    pts = S.get_feature(sk3)["params"]["sketch"]["points"]
    assert pts[line["points"][0]] == pytest.approx([20, 4]) and pts[line["points"][1]] == pytest.approx([30, 4])


def test_measure_items():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 20])
    S.extrude(sk, depth=10)
    S.hole(top_face(), [[10, 10]], diameter=6, end_condition="ThroughAll")
    topo = S.get_topology()
    cyl = next(f["id"] for f in topo["faces"] if f["type"] == "cylinder")
    one = S.measure_items([cyl])["items"][0]
    assert one["diameter"] == pytest.approx(6)
    planes = [f for f in topo["faces"] if f["type"] == "plane"]
    top = max(planes, key=lambda f: f["point"][1])["id"]
    bottom = min(planes, key=lambda f: f["point"][1])["id"]
    two = S.measure_items([top, bottom])
    assert two["angle"] == pytest.approx(0) and two["normal_distance"] == pytest.approx(10)
    pts = S.measure_items([[0, 0, 0], [3, 4, 0]])
    assert pts["point_distance"] == pytest.approx(5) and pts["distance"] == pytest.approx(5)


def test_parent_child_and_edit_sketch_plane():
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 20])
    S.extrude(sk, depth=10)
    assert S.parent_child("Boss-Extrude1") == {"feature": "Boss-Extrude1", "parents": [sk], "children": []}
    assert S.parent_child(sk)["children"] == ["Boss-Extrude1"]
    S.edit_sketch_plane(sk, "Front")  # the block now stands on the Front plane
    bb = S.get_mass_properties()["bounding_box"]
    assert not S.get_model()["errors"]
    assert bb["max"][2] - bb["min"][2] == pytest.approx(10)


def test_delete_view_of_automatic_drawing():
    block()
    from openswx.drawing import make_drawing
    S.drawing_delete_view("top")
    assert S.drawing_info()["settings"]["hidden_views"] == ["top"]
    views = make_drawing(S.APP.doc)["views"]
    assert "top" not in views and "front" in views
    with pytest.raises(Exception):
        S.drawing_delete_view("nothing")


def test_converted_entity_dangles_when_its_edge_goes():
    """Convert Entities keeps a reference to the model edge; when a change removes the edge the sketch entity
    is dangling (SOLIDWORKS shows it brown)."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 20])
    S.extrude(sk, depth=10)
    hole = S.hole(top_face(), [[10, 10]], diameter=6, end_condition="ThroughAll")
    hole_name = hole.get("feature") if isinstance(hole, dict) and "feature" in hole else S.get_model()["features"][-1]["name"]
    circle = next(e["id"] for e in S.get_topology()["edges"] if e["type"] == "circle")
    sk2 = S.create_sketch("Front")["sketch"]
    made = S.sketch_convert_entities(sk2, edges=[circle])["result"]["entities"]
    st = S.get_feature(sk2)["params"]["sketch"]["status"]
    assert not st.get("dangling")
    S.suppress_feature(hole_name, True)
    st = S.get_feature(sk2)["params"]["sketch"]["status"]
    assert st["dangling"] == made and st["entities"][made[0]] == "dangling"


def test_design_table():
    """Design Table: rows make configurations, columns drive dimensions and feature suppression."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    lines = S.sketch_rectangle(sk, [0, 0], [40, 20])["result"]["entities"]
    S.sketch_dimension(sk, "length", [lines[0]], 40)
    S.extrude(sk, depth=10)
    S.hole(top_face(), [[10, 10]], diameter=6, end_condition="ThroughAll")
    hole = S.get_model()["features"][-1]["name"]
    out = S.apply_design_table(["D1@" + sk, "Boss-Extrude1.depth", "$STATE@" + hole],
                               [{"name": "Small", "values": [30, 5, "S"]}, {"name": "Big", "values": [60, "", "U"]}])
    assert out["created"] == ["Small", "Big"]
    S.activate_configuration("Small")
    assert vol() == pytest.approx(30 * 20 * 5)
    S.activate_configuration("Big")
    assert vol() == pytest.approx(60 * 20 * 10 - PI * 9 * 10)
    table = S.get_design_table()
    assert table["columns"][0] == "D1@" + sk and table["rows"][1]["name"] == "Big"
    S.delete_design_table()
    assert "Small" in S.get_model()["configurations"]


def test_custom_materials(tmp_path, monkeypatch):
    """Custom Materials library (saved apart from the documents) and edited material properties."""
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    S.save_custom_material("Unobtainium", {"density": 1234, "elastic_modulus": 5e4, "poisson": 0.3, "yield_strength": 99})
    lib = S.list_materials()
    assert lib["Unobtainium"]["category"] == "Custom Materials" and lib["Unobtainium"]["density"] == 1234
    block()
    S.set_material("Unobtainium")
    m = S.get_model()["material"]
    assert m["density"] == 1234 and m["elastic_modulus"] == 5e4
    S.set_material("AISI 1020", properties={"yield_strength": 400})
    m = S.get_model()["material"]
    assert m["yield_strength"] == 400 and m["density"] == 7900
    S.delete_custom_material("Unobtainium")
    assert "Unobtainium" not in S.list_materials()


def test_redefine_mate():
    """Edit a mate in full (type and selections): it keeps its name and place."""
    block()
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    base = S.get_model()["name"]
    S.new_part("Pin2")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, depth=40)
    S.new_assembly("AsmR")
    S.insert_component(base)
    S.insert_component("Pin2", position=[30, 50, 10])
    topo = S.get_topology()
    hole = [f["id"] for f in topo["faces"] if f["id"].startswith(base) and f["type"] == "cylinder"][0]
    pin_side = [f["id"] for f in topo["faces"] if f["id"].startswith("Pin2") and f["type"] == "cylinder"][0]
    blk_top = [f["id"] for f in topo["faces"] if f["id"].startswith(base) and f["normal"][1] > 0.99][0]
    pin_bottom = [f["id"] for f in topo["faces"] if f["id"].startswith("Pin2") and f["normal"][1] < -0.8][0]
    S.add_mate("concentric", hole, pin_side)
    S.add_mate("distance", blk_top, pin_bottom, value=5)
    mates = S.get_model()["mates"]
    dist = mates[1]
    S.redefine_mate(dist["name"], "coincident", dist["a"], dist["b"])
    mates = S.get_model()["mates"]
    assert [m["name"] for m in mates] == [mates[0]["name"], dist["name"]] and mates[1]["type"] == "coincident"
    pin = [c for c in S.get_model()["components"] if c["name"].startswith("Pin2")][0]
    assert pin["position"][1] == pytest.approx(20, abs=1e-6)
    S.undo()
    assert S.get_model()["mates"][1]["type"] == "distance"


def test_dimension_configuration_scope():
    """The Modify box's scope: this configuration, all configurations, or chosen ones."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    lines = S.sketch_rectangle(sk, [0, 0], [40, 20])["result"]["entities"]
    S.sketch_dimension(sk, "length", [lines[0]], 40)
    S.extrude(sk, depth=10)
    S.add_configuration("Long")  # (active now)
    S.set_dimension(f"D1@{sk}", 80, configurations="this")
    assert vol() == pytest.approx(80 * 20 * 10)
    S.activate_configuration("Default")
    assert vol() == pytest.approx(40 * 20 * 10)
    S.set_dimension(f"D1@{sk}", 50, configurations="all")
    S.activate_configuration("Long")
    assert vol() == pytest.approx(50 * 20 * 10)  # the override went
    S.set_dimension(f"D1@{sk}", 30, configurations=["Default"])
    assert vol() == pytest.approx(50 * 20 * 10)
    S.activate_configuration("Default")
    assert vol() == pytest.approx(30 * 20 * 10)


def test_face_features():
    """Each face is credited to the feature that made it (FeatureManager / model cross-highlighting)."""
    block()  # Boss-Extrude1: 6 faces
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    hole = S.get_model()["features"][-1]["name"]
    owners = S.face_features()["faces"]
    topo = S.get_topology()["faces"]
    assert len(owners) == len(topo)
    cyl = next(f["id"] for f in topo if f["type"] == "cylinder")
    assert owners[cyl] == hole
    assert sum(1 for v in owners.values() if v == "Boss-Extrude1") == 6


def test_named_views(tmp_path):
    """Orientation dialog New View: saved with the document; standard names are reserved."""
    block()
    S.save_named_view("Corner", [0.1, 0.2, 0.3, 0.927], [1, 2, 3], 150, perspective=True)
    with pytest.raises(Exception):
        S.save_named_view("Front", [0, 0, 0, 1], [0, 0, 0], 100)
    path = S.save_part(str(tmp_path / "v.oswprt"))["saved"]
    S.open_part(path)
    v = S.get_model()["named_views"]["Corner"]
    assert v["t"] == [1, 2, 3] and v["h"] == 150 and v["perspective"] is True
    S.delete_named_view("Corner")
    assert S.get_model()["named_views"] == {}


def test_flex_deform_freeform():
    """Flex (twist / bend keep the volume, taper and stretch scale it exactly), Deform and Freeform."""
    block()
    v0 = vol()
    S.flex("taper", axis="X", factor=0.5)
    assert vol() == pytest.approx(v0 * (1 - 0.5 + 0.25 / 3), rel=1e-6)  # cross-section scales by s^2
    block(); S.flex("stretch", axis="X", distance=20)
    assert vol() == pytest.approx(v0 * 120 / 100, rel=1e-6)
    block(); S.flex("twist", axis="X", angle=45)
    assert vol() == pytest.approx(v0, rel=3e-3)
    block(); S.flex("bend", axis="X", angle=60)
    assert vol() == pytest.approx(v0, rel=3e-3) and not S.get_model()["errors"]
    block(); S.deform([[0, 20, 0]], [[0, 5, 0]], radius=30)
    assert vol() > v0 + 1000
    block(); S.freeform(top_face(), 4)
    assert vol() > v0


def test_profile_fillets_and_fillet_xpert():
    block()
    v0 = vol()
    long_top = lambda: next(x["id"] for x in S.get_topology()["edges"]  # noqa: E731
                            if x["type"] == "line" and abs(x["length"] - 100) < 1e-6 and x["point"][1] > 19)
    S.fillet_profile([long_top()], 5, "conic", 0.5)  # a parabola leaves 1/3 of the corner triangle
    assert v0 - vol() == pytest.approx(100 * 12.5 / 3, rel=1e-6)
    block(); S.fillet_profile([long_top()], 5, "curvature_continuous")
    assert 0 < v0 - vol() < 100 * 12.5
    block()
    edges = [x["id"] for x in S.get_topology()["edges"] if x["point"][1] > 19]
    r = S.fillet_xpert(edges, 12)
    assert r["features"] and not S.get_model()["errors"]


def test_mold_tools():
    block()
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    part = vol()
    info = S.parting_line("Y")["info"]
    assert info["level"] == pytest.approx(0) and info["edges"] == 5  # the bottom outline and the hole's rim
    assert S.shut_off_surfaces("Y")["info"]["surface_area"] == pytest.approx(PI * 25, rel=1e-6)
    S.parting_surface("Y", 10)
    out = S.tooling_split("Y", 10)["info"]
    block_vol = 120 * 80 * 40
    assert out["core_volume"] + out["cavity_volume"] + part == pytest.approx(block_vol, rel=1e-9)
    assert S.get_model()["bodies"] == 3


def test_insert_mirror_part_and_cosmetic_thread(tmp_path):
    block(40, 30, 10)
    src = S.save_part(str(tmp_path / "src.oswprt"))["saved"]
    one = vol()
    S.new_part()
    S.insert_part(src, position=[100, 0, 0])
    assert vol() == pytest.approx(one)
    S.mirror_part(src, "Right")
    assert vol() == pytest.approx(2 * one) and S.get_model()["bodies"] == 2
    block()
    S.hole(top_face(), [[0, 0]], diameter=8.5, end_condition="ThroughAll")
    cyl = next(f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder")
    t = S.cosmetic_thread(cyl, "M10x1.5")["info"]["cosmetic_thread"]
    assert t["internal"] and t["thread_diameter"] == pytest.approx(10.0) and t["length"] == pytest.approx(20)


def test_more_surface_tools():
    block()
    top = [x["id"] for x in S.get_topology()["edges"] if x["point"][1] > 19 and abs(x["length"] - 60) < 1e-6]
    assert S.ruled_surface(top[:1], 10, "normal")["info"]["surface_area"] == pytest.approx(600)
    assert S.radiate_surface(top[:1], "Top", 10)["info"]["surface_area"] == pytest.approx(600)
    assert S.extend_surface(5, faces=[top_face()])["info"]["surface_area"] == pytest.approx(110 * 70)
    block()
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    assert S.untrim_surface(faces=[top_face()])["info"]["surface_area"] == pytest.approx(100 * 60)
    cyl = next(f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder")
    assert S.flatten_surface(faces=[cyl])["info"]["surface_area"] == pytest.approx(PI * 10 * 20, rel=1e-6)
    rim = [x["id"] for x in S.get_topology()["edges"] if x["type"] == "circle" and x["point"][1] > 19]
    assert S.filled_surface_edges(rim, "tangent")["info"]["surface_area"] == pytest.approx(PI * 25, rel=1e-4)


def test_library_features(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    block()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.cut_extrude(sk, end_condition="ThroughAll", reverse=True)
    cut = S.get_model()["features"][-1]["name"]
    with_hole = vol()
    S.save_library_feature("Hole10", [cut])
    assert S.list_design_library()["features"][0]["features"] == [sk, cut]
    block()
    S.insert_library_feature("Hole10", "Top", [20, 0])
    assert vol() == pytest.approx(with_hole)
    S.add_part_to_design_library()
    assert S.list_design_library()["parts"]



def _base_and_pin():
    S.new_part("BaseM")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-50, -30], [50, 30])
    S.extrude(sk, depth=20)
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    S.connection_point([0, 20, 0], "Y", name="Socket")
    hole = max((f for f in S.get_topology()["faces"] if f["type"] == "cylinder"), key=lambda f: f["area"])["id"]
    S.mate_reference(hole, "concentric", reference="PinFit")
    S.new_part("PinM")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, depth=40)
    S.connection_point([0, 0, 0], "-Y", name="Plug")
    cyl = next(f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder")
    S.mate_reference(cyl, "concentric", reference="PinFit")
    S.new_assembly("AsmM")
    S.insert_component("BaseM")
    S.insert_component("PinM", position=[60, 50, 30])


def test_mate_references_magnetic_mates_and_assembly_display_modes(tmp_path):
    _base_and_pin()
    assert S.apply_mate_references()["mates"] == ["Concentric1"]
    assert S.magnetic_snap("PinM-1", 5)["snapped"] is False  # too far away to snap
    S.magnetic_mate("BaseM-1/Socket", "PinM-1/Plug")
    pin = next(c for c in S.get_model()["components"] if c["name"] == "PinM-1")
    assert pin["position"] == pytest.approx([0, 20, 0], abs=1e-6)
    S.save_alternate_position("Seated")
    assert S.APP.asm.to_dict()["alternate_positions"]["Seated"]["PinM-1"][3:] == pytest.approx([0, 20, 0], abs=1e-6)
    S.set_envelope("PinM-1", True)
    assert [r["part"] for r in S.assembly_bom()["bom"]] == ["BaseM"]
    assert S.get_mass_properties()["volume"] == pytest.approx(120000 - PI * 25 * 20)
    S.set_envelope("PinM-1", False)
    S.large_assembly_mode(True)
    assert S.APP.asm.mesh()["edges"] == []
    S.large_assembly_mode(False)
    S.set_speedpak("BaseM-1", True)  # shown without its hole
    assert len([f for f in S.APP.asm.mesh()["faces"] if f["id"].startswith("BaseM")]) == 6
    S.set_speedpak("BaseM-1", False)
    files = S.pack_and_go(str(tmp_path / "pack"), prefix="X-", zip_file=str(tmp_path / "pack.zip"))
    assert sorted(Path(f).name for f in files["files"]) == ["X-AsmM.oswasm", "X-BaseM.oswprt", "X-PinM.oswprt"]
    assert Path(files["zip"]).exists()


def test_smart_mate_routing_hole_series_weld_bead_defeature():
    _base_and_pin()
    t = S.get_topology()
    hole = max((f for f in t["faces"] if f["id"].startswith("BaseM") and f["type"] == "cylinder"), key=lambda f: f["area"])["id"]
    pin = next(f["id"] for f in t["faces"] if f["id"].startswith("PinM") and f["type"] == "cylinder")
    assert S.smart_mate(hole, pin)["type"] == "concentric"
    pipe = S.route_pipe([[0, 30, 0], [0, 80, 0], [80, 80, 0]], 12, 1.5, 15)
    straight, arc = (50 - 15) + (80 - 15), 15 * PI / 2
    assert pipe["length"] == pytest.approx(straight + arc, rel=1e-9)
    assert pipe["volume"] == pytest.approx(PI * (36 - 20.25) * (straight + arc), rel=1e-4)
    cable = S.route_cable([[0, 30, 40], [30, 60, 50], [60, 40, 80]], 4)
    assert cable["volume"] == pytest.approx(PI * 4 * cable["length"], rel=1e-3)
    holes = S.hole_series([30, 0, 0], [0, 1, 0], [6.6])["holes"]
    assert [h["component"] for h in holes] == ["BaseM-1", "Pipe1-1"]  # (the line runs through the pipe too)
    assert S.get_mass_properties()["volume"] < 120000 - PI * 25 * 20 + PI * 25 * 40 + pipe["volume"] + cable["volume"]
    S.new_part("Tee")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-50, -30], [50, 30])
    S.extrude(sk, depth=10)
    sk2 = S.create_sketch(top_face())["sketch"]
    S.sketch_rectangle(sk2, [-5, -30], [5, 30])
    S.extrude(sk2, depth=40)
    t = S.get_topology()["faces"]
    base_top = next(f["id"] for f in t if f["normal"][1] > 0.99 and abs(f["point"][1] - 10) < 1e-6 and f["point"][0] > 5)
    side = next(f["id"] for f in t if f["normal"][0] > 0.99 and f["point"][1] > 10)
    v0 = vol()
    S.weld_bead(base_top, side, 4)
    assert vol() - v0 == pytest.approx(0.5 * 4 * 4 * 60, rel=1e-9)
    block()
    S.hole(top_face(), [[0, 0]], diameter=6, end_condition="ThroughAll")
    out = S.defeature(10, 1)
    assert out["faces_after"] == 6 and vol() == pytest.approx(100 * 60 * 20)



def test_drawing_symbols_threads_layers_formats(tmp_path, monkeypatch):
    """Surface finish and weld symbols, cosmetic threads in views, layers, sheet formats, stacked balloons and
    alternate position views."""
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    block()
    S.hole(top_face(), [[0, 0]], diameter=8.5, end_condition="ThroughAll")
    cyl = next(f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder")
    S.cosmetic_thread(cyl, "M10x1.5")
    S.drawing_new(sheet="A3", scale=1)
    svg = S.create_drawing()["svg"]
    assert ' 0 1 1 ' in svg  # the thread's three-quarter circle in the top view
    S.drawing_add_surface_finish(200, 100, 1.6, "required", lay="=")
    S.drawing_add_weld_symbol(150, 60, [140, 80], "fillet", "both", size=5, length=50, all_around=True, field=True, process="MIG")
    svg = S.create_drawing()["svg"]
    assert ">Ra 1.6<" in svg and 'data-ann="Weld1"' in svg and ">MIG<" in svg
    S.drawing_add_layer("Hidden", "#ff0000", visible=False)
    S.drawing_set_layer(["Weld1"], "Hidden")
    assert 'data-ann="Weld1"' not in S.create_drawing()["svg"]
    S.drawing_add_layer("Red", "#ff0000")
    S.drawing_set_layer(["Finish1"], "Red")
    svg = S.create_drawing()["svg"]
    assert 'data-ann="Weld1"' not in svg and "#ff0000" in svg
    S.drawing_delete_annotation("Finish1")
    assert ">Ra 1.6<" not in S.create_drawing()["svg"]
    S.drawing_set_sheet_format("ISO 7200")
    svg = S.create_drawing()["svg"]
    assert "Identification number" in svg and ">TITLE<" not in svg
    S.drawing_save_sheet_format("Mine")
    assert "Mine" in S.list_sheet_formats()["saved"]
    S.drawing_set_sheet_format()
    assert ">TITLE<" in S.create_drawing()["svg"]
    # assembly: stacked balloon and an alternate position view
    _base_and_pin()
    S.save_alternate_position("Out")
    S.magnetic_mate("BaseM-1/Socket", "PinM-1/Plug")
    S.drawing_new(sheet="A3", scale=0.5)
    st = S.APP.asm.drawing
    front = st["sheets"][0]["views"][0]
    S.drawing_view_alternate_position(front["id"], "Out")
    assert 'stroke-dasharray="6 1.2 1.2 1.2 1.2 1.2"' in S.create_drawing()["svg"]
    from openswx import drawing_sheets as DS
    t = DS.placement(st, front, DS.view_geometry(S.APP.asm, st, front))
    b = S.drawing_add_stacked_balloon(front["id"], list(t(0, 10)), ["PinM-1", "SPARE"])["balloon"]
    assert b["stack"] == ["PinM-1", "SPARE"]
    assert ">SPARE<" in S.create_drawing()["svg"]



# ---------------------------------------------------------------- analysis: nonlinear, dynamics, drop, fatigue, flow
def _cantilever():
    S.new_part("Beam")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(sk, [-5, 0], [5, 10])
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    root = [f["id"] for f in faces if f["normal"][0] < -0.99][0]
    tip = [f["id"] for f in faces if f["normal"][0] > 0.99][0]
    S.simulation_add_fixture(root)
    return root, tip


def test_nonlinear_dynamic_drop_and_fatigue_studies():
    root, tip = _cantilever()
    S.simulation_add_force(tip, force=[0, -100, 0])
    lin = S.simulation_run(element_size=2.5)
    nl = S.nonlinear_run(steps=2, element_size=2.5)  # elastic: the same as the linear study
    assert nl["plastic_elements"] == 0 and nl["max_displacement"] == pytest.approx(lin["max_displacement"], rel=1e-3)
    assert set(S.simulation_results()["stress"])
    # fatigue: 100 N gives ~50 MPa, under the endurance limit 0.5 Su; ten times that does not
    f = S.fatigue_run(cycles=1e6)
    assert f["infinite_life"] and f["endurance_limit"] == pytest.approx(0.5 * 420.5)
    f = S.fatigue_run(cycles=1e6, scale=10)
    assert not f["infinite_life"] and f["damage"] > 0 and f["elements_failing"] > 0
    assert min(min(v) for v in S.fatigue_results()["log_life"].values()) < 6
    # dynamics: a suddenly applied load overshoots to about twice the static deflection; resonance at f1
    st = S.step_response_run(duration=0.01, points=400, damping=0.02, modes=4, element_size=2.5)
    assert 1.85 < st["dynamic_amplification"] < 2.0
    f1 = st["frequencies_hz"][0]
    h = S.harmonic_run(f1 * 0.7, f1 * 1.3, points=121, modes=4, element_size=2.5)
    assert h["peak_frequency"] == pytest.approx(f1, rel=0.01)
    # yielding: plastic elements, and more deflection than the linear extrapolation
    S.simulation_clear(fixtures=False)
    S.simulation_add_force(tip, force=[0, -2000, 0])
    nl = S.nonlinear_run(steps=5, element_size=2.5)
    assert nl["plastic_elements"] > 0 and nl["max_displacement"] > 20 * lin["max_displacement"] * 1.05
    assert nl["max_von_mises"] < 20 * lin["max_von_mises"]  # beyond the plastic moment: hardening carries it
    # drop test: a bar landing on its end, G^2 = 6 E h / (rho g L^2)
    d = S.drop_test_run(100, [root], direction=[-1, 0, 0], element_size=2.5)
    assert d["peak_acceleration_g"] == pytest.approx(math.sqrt(6 * 200e3 * 100 / (7.9e-9 * 9810 * 100 ** 2)), rel=0.05)
    assert d["impact_velocity_m_s"] == pytest.approx(math.sqrt(2 * 9.81 * 0.1), rel=1e-6)


def test_flow_costing_sustainability():
    block()
    with pytest.raises(S.CommandError):
        S.costing("sheet_metal")  # not a sheet metal part
    S.set_material("AISI 1020")
    r = S.flow_simulation(speed=10, direction=[1, 0, 0], cells_across=16)
    assert r["converged"] and r["min_cp"] < -0.2 and r["max_cp"] > 0.5 and r["skin_friction_drag_N"] > 0
    assert abs(r["pressure_force_N"][0]) < 0.05 * r["dynamic_pressure_Pa"] * 60 * 20 * 1e-6  # ideal flow: no drag
    assert set(S.flow_results()["cp"])
    one, many = S.costing(quantity=1), S.costing(quantity=100)
    assert many["cost_each"] < one["cost_each"] and one["removed_cm3"] == pytest.approx(104 * 64 * 24 / 1000 - 120)
    for m in ("additive", "casting"):
        assert S.costing(m)["cost_each"] > 0
    s = S.sustainability_report(region="Europe")
    assert s["mass_kg"] == pytest.approx(0.948) and s["total"]["co2"] == pytest.approx(
        sum(p["co2"] for p in s["phases"].values()), abs=1e-3)
    assert S.sustainability_report(recycled_fraction=1.0)["phases"]["material"]["co2"] < s["phases"]["material"]["co2"]


def test_dimxpert_and_render_scene(tmp_path):
    block()
    sk = S.create_sketch(top_face())["sketch"]
    S.sketch_circle(sk, [30, 0], 4)
    S.sketch_circle(sk, [-30, 0], 4)
    S.cut_extrude(sk, end_condition="ThroughAll")
    pmi = S.dimxpert_auto()["pmi"]
    assert [p["label"] for p in pmi if p["type"] == "datum"] == ["A", "B", "C"]
    assert sorted(p["value"] for p in pmi if p["type"] == "size") == [20, 60, 100]
    holes = [p for p in pmi if p["type"] == "hole"]
    assert len(holes) == 1 and holes[0]["count"] == 2 and holes[0]["text"].startswith("2X ⌀8 H7")
    S.set_render_scene("outdoor", exposure=1.4)
    png = tmp_path / "logo.png"
    from PIL import Image
    Image.new("RGB", (8, 4), "red").save(png)
    dec = S.add_decal(top_face(), str(png), width=30)["decal"]
    assert dec["name"] == "Decal1" and dec["normal"][1] == pytest.approx(1)
    p = tmp_path / "m.oswprt"
    S.save_part(str(p))
    S.open_part(str(p))
    summ = S.APP.doc.summary()
    assert len(summ["pmi"]) == len(pmi) and summ["render"]["scene"] == "outdoor"
    assert summ["render"]["decals"][0]["image"].startswith("data:image/png;base64,")
    S.delete_decal("Decal1")
    S.dimxpert_clear()
    assert S.APP.doc.summary()["pmi"] == [] and S.APP.doc.render["decals"] == []
    S.undo()
    assert len(S.APP.doc.pmi) == len(pmi)



# ---------------------------------------------------------------- ecosystem: toolbox, PDM, eDrawings, macros
def test_toolbox_standards():
    cat = S.list_fasteners()
    assert {"button_head_screw", "countersunk_screw", "dowel_pin", "ansi_hex_bolt", "ansi_washer"} <= set(cat)
    assert cat["ansi_socket_cap_screw"]["family"] == "ANSI Inch" and "1/4-20" in cat["ansi_socket_cap_screw"]["sizes"]
    S.new_assembly()
    pin = S.insert_fastener("dowel_pin", "6", length=20)["component"]
    bolt = S.insert_fastener("ansi_hex_bolt", "1/4-20", length=1.0)["component"]
    asm = S.APP.asm
    from openswx.features import mass_properties
    assert mass_properties(asm._local_body(asm.component(pin)))["volume"] == pytest.approx(PI * 9 * 20, rel=1e-6)
    bb = asm._local_body(asm.component(bolt)).bounding_box()
    assert bb.min.Z == pytest.approx(-25.4) and bb.size.X == pytest.approx(0.4375 * 25.4 / math.cos(PI / 6), rel=1e-3)
    with pytest.raises(S.CommandError):
        S.insert_fastener("button_head_screw", "M24", length=30)  # not in ISO 7380


def test_pdm_vault_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path / "home"))
    block()
    S.APP.doc.properties["Project"] = "Gearbox"
    p = tmp_path / "work" / "Plate.oswprt"
    p.parent.mkdir()
    S.save_part(str(p))
    r = S.pdm_check_in(comment="first")
    assert r["version"] == 1 and S.pdm_list()["files"][0]["state"] == "Work in Progress"
    with pytest.raises(S.CommandError):
        S.pdm_check_in(str(p))  # not checked out
    co = S.pdm_check_out("Plate.oswprt")
    assert Path(co["path"]).is_file() and S.APP.doc.path and Path(S.APP.doc.path).resolve() == Path(co["path"])
    sk = S.create_sketch(top_face())["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.cut_extrude(sk, end_condition="ThroughAll")
    S.save_part()
    assert S.pdm_check_in(comment="hole")["version"] == 2
    with pytest.raises(S.CommandError):
        S.pdm_change_state("Plate.oswprt", "Released")  # must go through approval
    S.pdm_change_state("Plate.oswprt", "Pending Approval")
    assert S.pdm_change_state("Plate.oswprt", "Released")["revision"] == "A"
    with pytest.raises(S.CommandError):
        S.pdm_check_out("Plate.oswprt")  # released: locked
    h = S.pdm_history("Plate.oswprt")
    assert [v["comment"] for v in h["versions"]] == ["first", "hole"] and h["versions"][-1]["revision"] == "A"
    old = S.pdm_get_version("Plate.oswprt", 1, str(tmp_path / "old"))["path"]
    import json
    nfeat = lambda f: len(json.loads(Path(f).read_text(encoding="utf-8"))["features"])  # noqa: E731
    assert nfeat(old) + 2 == nfeat(co["path"])  # version 1: before the sketch and cut
    assert [f["name"] for f in S.pdm_search("gearbox")["files"]] == ["Plate.oswprt"]
    assert S.pdm_search("nothing-like-this")["files"] == []
    S.pdm_change_state("Plate.oswprt", "Work in Progress")
    S.pdm_check_out("Plate.oswprt", open=False)
    S.pdm_undo_check_out("Plate.oswprt")
    assert S.pdm_history("Plate.oswprt")["checked_out"] is None


def test_edrawing_export_and_macros(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    S.macro_start()
    block()
    S.set_material("AISI 1020")
    S.dimxpert_auto()
    m = S.macro_stop(str(tmp_path / "mk.py"))
    assert S.macro_status()["recording"] is False
    src = Path(m["path"]).read_text(encoding="utf-8")
    assert "S.new_part(" in src and "S.extrude(" in src and "S.set_material(name='AISI 1020'" in src
    assert "S.dimxpert_auto(" in src and "macro_" not in src.split('"""')[-1]
    vol_before = S.get_mass_properties()["volume"]
    S.new_part("Other")
    S.run_macro(m["path"])
    assert S.get_mass_properties()["volume"] == pytest.approx(vol_before) and S.APP.doc.pmi
    out = S.export_edrawing(str(tmp_path / "plate"))
    html = Path(out["path"]).read_text(encoding="utf-8")
    assert out["path"].endswith(".html") and '"Material":"AISI 1020"' in html and "Datum" not in html.split("three-src")[0]
    assert html.count("<script") == 3 and '"pmi":[{' in html



def test_holes_do_not_give_spurious_modes_or_runaway_drop_nodes():
    # cut cells around holes with only a few inside points are under-integrated: they must not carry mass
    from build123d import Box, Cylinder, Pos, Rot
    from openswx.fea import solve_modal
    from openswx.fea_more import solve_drop
    from openswx.materials import lookup
    mat = lookup("AISI 1020")
    solid = Pos(0, 10, 0) * Box(100, 20, 60)
    holed = solid - Pos(30, 10, 0) * Rot(90, 0, 0) * Cylinder(4, 40) - Pos(-30, 10, 0) * Rot(90, 0, 0) * Cylinder(4, 40)
    face = lambda b, ax, s: [f for f in b.faces() if s * list(f.normal_at(f.center()))[ax] > 0.99][0]  # noqa: E731
    f = solve_modal(holed, mat, [face(holed, 0, -1)], 3, None)["frequencies_hz"]
    beam = 1.875 ** 2 / (2 * PI) * math.sqrt(200e3 * 20 ** 2 / 12 / (7.9e-9 * 100 ** 4))  # Euler-Bernoulli, no holes
    assert 0.85 * beam < f[0] < 1.02 * beam and f[1] > 1.5 * f[0]
    plain = solve_drop(solid, mat, [face(solid, 1, -1)], 1000, (0, -1, 0))
    r = solve_drop(holed, mat, [face(holed, 1, -1)], 1000, (0, -1, 0))
    assert r["peak_acceleration_g"] == pytest.approx(plain["peak_acceleration_g"], rel=0.05)
    assert r["max_displacement"] < 1.5 * plain["max_displacement"] and r["max_von_mises"] < 3 * plain["max_von_mises"]


def test_piping_library_route_and_fittings():
    from openswx import piping
    p = piping.pipe("2", "40")
    assert (p["od"], p["wall"]) == (60.3, 3.91)
    el, R, t = piping.elbow_shape("2", "40", 90)
    assert (R, t) == (76.0, pytest.approx(76.0))  # B16.9 long radius: centre-to-end 1.5 x NPS
    assert el.volume == pytest.approx(PI / 4 * (p["od"] ** 2 - p["id"] ** 2) * R * PI / 2, rel=1e-6)
    bb = el.bounding_box()  # enters along +Z at the origin, leaves along +X at (R, 0, R)
    assert (bb.min.Z, bb.max.X) == (pytest.approx(0, abs=1e-6), pytest.approx(R, abs=1e-6))
    for shape, _ in (piping.tee_shape("2", "40"), piping.reducer_shape("4", "2", "40"), piping.cap_shape("2", "40"),
                     piping.flange_shape("2", "40")):
        assert shape.is_valid and shape.volume > 0
    S.new_assembly("Skid")
    route = [[0, 0, 0], [500, 0, 0], [500, 400, 0], [500, 400, 300]]
    r = S.route_pipe_standard(route, "2")
    # each run loses an elbow's centre-to-end (76) per corner and a flange's length (62) per flanged end
    assert r["cut_list"] == [pytest.approx(500 - 62 - 76), pytest.approx(400 - 2 * 76), pytest.approx(300 - 76 - 62)]
    assert (r["elbows"], r["flanges"]) == (2, 2)
    assert [x["kind"] for x in r["parts"]] == ["flange", "pipe", "elbow", "pipe", "elbow", "pipe", "flange"]
    parts = sum(q["shape"].volume for q in piping.layout(route, "2"))
    assert S.get_mass_properties()["volume"] == pytest.approx(parts, rel=1e-6)  # parts only touch: no overlap
    with pytest.raises(Exception, match="shorter than its fittings"):
        S.route_pipe_standard([[0, 0, 0], [100, 0, 0], [100, 50, 0]], "2")
    tee = S.insert_fitting("tee", "2", [0, 0, 600], [0, 0, 1], [1, 0, 0])
    assert tee["component"].startswith("Tee")
    assert len(S.list_pipe_standards()["sizes"]) == 16


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
    from openswx.parasolid import _Builder
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


def test_cfd_pipe_flow_and_sphere_drag():
    from build123d import Cylinder, Rot, Sphere
    from openswx.cfd import external_flow, internal_flow
    # laminar pipe flow (Re 10): Hagen-Poiseuille plus the entrance loss (K ~ 1.25), parabolic profile
    pipe = Rot(0, 90, 0) * (Cylinder(8, 60) - Cylinder(5, 62))
    r = internal_flow(pipe, "X", 0.001, "water", cells_across=10)
    D = 2 * math.sqrt(r["inlet_area_mm2"] / PI) * 1e-3
    expected = 32 * 1e-3 * 0.060 * 0.001 / D ** 2 + 1.25 * 998 * 0.001 ** 2 / 2
    assert r["converged"] and r["pressure_drop_Pa"] == pytest.approx(expected, rel=0.05)
    assert r["max_velocity"] == pytest.approx(2 * 0.001, rel=0.03)
    # sphere at Re 20: the standard drag curve (Clift et al.) gives Cd = 2.73
    nu = 1.81e-5 / 1.204
    s = external_flow(Sphere(5), (1, 0, 0), 20 * nu / 0.010, "air", cells_across=12)
    assert s["converged"] and s["warning"] is None
    assert s["drag_coefficient"] == pytest.approx(24 / 20 * (1 + 0.1935 * 20 ** 0.6305), rel=0.12)
    assert abs(s["lift_coefficients"][0]) < 0.05 and s["max_cp"] > 0.9  # (viscous stagnation: over 1 at Re 20)
    S.new_part("Duct")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_circle(sk, [0, 0], 8)
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, depth=60)
    with pytest.raises(Exception, match="passage"):
        S.cfd_internal(axis="Y", speed=0.001)


def _pocket_block():
    S.new_part("Block")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [60, 40])
    S.extrude(sk, depth=20)
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [15, 10], [45, 30])
    S.cut_extrude(sk, depth=8, reverse=True)
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_circle(sk, [6, 20], 3)
    S.sketch_circle(sk, [54, 20], 3)
    S.cut_extrude(sk, end_condition="Through All", reverse=True)


def test_cam_toolpaths_gcode_and_stock_check(tmp_path):
    _pocket_block()
    r = S.cam_generate(setup="-Z", tool_diameter=6, leave=0.3)
    assert [h["diameter"] for h in r["holes"]] == [6.0, 6.0] and all(h["through"] for h in r["holes"])
    assert [o["kind"] for o in r["operations"]] == ["rough", "finish", "drill"]
    v = r["verification"]
    assert v["gouge_mm"] <= r["precision_mm"]  # the tool never enters the part
    assert v["area_over_0.1mm_pct"] < 1.0  # only the pocket's inside corners keep material (the tool radius)
    assert -9.0 in r["levels"]  # the pocket floor is a cutting level (stock top 1 mm above the part)
    g = S.cam_gcode(post="fanuc", path=str(tmp_path / "block"))
    text = Path(g["path"]).read_text()
    assert g["path"].endswith(".nc") and "T1 M6" in text and "T2 M6" in text and "G43" in text
    assert "G83 X7 Y21 Z-23.8 R0 Q6" in text and "X55 Y21" in text  # peck drilling through, both holes
    grbl = S.cam_gcode(post="grbl")["gcode"]
    assert "G83" not in grbl and "M0" in grbl and "M6" not in grbl
    tp = S.cam_toolpaths()
    assert len(tp["paths"]) == 3 and tp["paths"][2]["feed"][0][:3] == pytest.approx([6, 20, -1])  # R plane, part coords
    with pytest.raises(Exception, match="stepover"):
        S.cam_generate(tool_diameter=6, stepover=6)


def test_plastics_fill_matches_slit_flow_and_finds_weld_lines():
    from build123d import Align, Box, Cylinder, Pos
    from openswx.plastics import fill, material
    plate = Box(100, 20, 2, align=(Align.MIN, Align.MIN, Align.MIN))
    gate = [f for f in plate.faces() if abs(f.center().X) < 1e-6][0]
    # a line gate along the end: 1-D slit flow, dp = 12 eta Q L / (W t^3) (Newtonian) or the power-law form
    r = fill(plate, [gate], material("ABS", newtonian=True), fill_time=1.0)
    assert r["nominal_wall_mm"] == pytest.approx(2.0, abs=0.02)
    assert r["max_injection_pressure_MPa"] == pytest.approx(12 * 200 * 4e-6 * 0.1 / (0.02 * 0.002 ** 3) / 1e6, rel=0.02)
    assert r["fill_time_s"] == pytest.approx(1.0, abs=0.03) and r["weld_line_cells"] == 0 and not r["air_traps"]
    m = material("ABS")
    n = m["n"]
    expected = m["consistency"] / 0.001 * ((2 * n + 1) / n * 0.1 / 0.001) ** n * 0.1 / 1e6
    assert fill(plate, [gate], m, fill_time=1.0)["max_injection_pressure_MPa"] == pytest.approx(expected, rel=0.02)
    holed = plate - Pos(40, 10, 1) * Cylinder(4, 4)
    w = fill(holed, [gate], m, fill_time=1.0)
    assert w["weld_line_cells"] > 0 and all(p[0] > 44 for p in w["weld_lines"])  # behind the hole
    assert not fill(plate, [(0, 10, 1)], m, fill_time=1.0)["air_traps"]  # ends at the outline: the parting line vents it
    # race-tracking: the melt runs round a thick rim and closes in the thin panel, trapping air inside it
    frame = Box(60, 60, 3, align=(Align.MIN,) * 3) - Pos(6, 6, 1) * Box(48, 48, 3, align=(Align.MIN,) * 3)
    traps = fill(frame, [(0, 30, 1.5)], m, fill_time=1.0)["air_traps"]
    assert len(traps) == 1 and 40 < traps[0][0] < 54 and traps[0][2] < 1
    short = fill(plate, [gate], material("PC"), fill_time=0.05, max_pressure_MPa=50)
    assert short["short_shot"] and short["filled_pct"] < 100 and "short shot" in short["warnings"][0]
    S.new_part("Lid")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [60, 40])
    S.extrude(sk, depth=2)
    p = S.plastics_fill(gates=[[0, 20, 1]], material="PP")
    assert p["filled_pct"] == 100 and p["clamp_force_tonne"] > 0 and p["cooling_time_s"] > 0


def test_inspection_characteristics_results_and_report(tmp_path):
    _pocket_block()
    v = S.drawing_new(sheet="A3")["views"][0]
    S.drawing_dimension_value(v, "60")
    S.drawing_dimension_value(v, "40")
    S.drawing_dimension_value(v, "⌀6")
    S.drawing_add_gdt("flatness", 0.05, 200, 60)
    S.dimxpert_auto()
    chars = S.inspection_characteristics()["characteristics"]
    req = {c["source"]: c["requirement"] for c in chars}
    assert [c["number"] for c in chars] == list(range(1, len(chars) + 1))
    assert req["Dim1"].startswith("60 ±0.3") and "ISO 2768-m" in req["Dim1"]  # 30-120 mm, class m
    assert req["Dim3"].startswith("⌀6 ±0.1") and req["GTol1"] == "Flatness 0.05"
    assert any(c["designator"] == "Position" for c in chars)
    S.inspection_set(1, measured=[60.05, 59.9])
    S.inspection_set("Dim2", measured=[40.6])
    S.inspection_settings(general_tolerance="f", part_number="P-100")
    out = S.inspection_characteristics()
    c1 = out["characteristics"][0]
    assert c1["source"] == "Dim1" and c1["max"] == pytest.approx(60.15) and c1["result"] == "pass"
    assert out["summary"]["failed"] == 1 and out["summary"]["status"] == "fail"
    S.drawing_delete_dimension("Dim1")  # numbers stay with their characteristics
    assert S.inspection_characteristics()["characteristics"][0]["source"] == "Dim2"
    html = Path(S.inspection_report(str(tmp_path / "fai"))["path"]).read_text(encoding="utf-8")
    assert "Form 3" in html and "P-100" in html and "FAIL" in html
    csv_text = Path(S.inspection_report(str(tmp_path / "fai"), format="csv")["path"]).read_text(encoding="utf-8")
    assert csv_text.splitlines()[0].startswith("Char No.")
    svg = S.export_drawing(str(tmp_path / "d.svg"))["path"]
    assert Path(svg).read_text(encoding="utf-8").count("insp-balloon") >= 3


def test_electrical_harness_and_composer(tmp_path):
    def box(name, w, h, d):
        S.new_part(name)
        sk = S.create_sketch("Front")["sketch"]
        S.sketch_rectangle(sk, [0, 0], [w, h])
        S.extrude(sk, depth=d)
    box("Plate", 300, 200, 5)
    for n in ("ConnA", "ConnB", "ConnC"):
        box(n, 20, 10, 10)
    S.new_assembly("Rig")
    S.insert_component("Plate", [0, 0, 0])
    S.insert_component("ConnA", [20, 20, 5])
    S.insert_component("ConnB", [260, 20, 5])
    S.insert_component("ConnC", [140, 170, 5])
    S.electrical_connector("J1", "ConnA-1", pins=4)  # at the centre (30, 25, 10)
    S.electrical_connector("J2", "ConnB-1", pins=4)
    S.electrical_connector("J3", "ConnC-1", pins=2)
    S.electrical_route_segment([[30, 40, 20], [150, 40, 20], [270, 40, 20]])
    S.electrical_route_segment([[150, 40, 20], [150, 160, 20]])
    S.electrical_wires([{"id": "W1", "from": "J1-1", "to": "J2-1", "gauge": "20 AWG", "color": "RD"},
                        {"id": "W2", "from": "J1-2", "to": "J2-2", "gauge": "20 AWG", "color": "BK"},
                        {"id": "W3", "from": "J1-3", "to": "J3-1", "gauge": "24 AWG", "color": "BU"}])
    h = S.electrical_harness(current=2.0)
    w1 = next(w for w in h["wires"] if w["id"] == "W1")
    breakout = math.sqrt(15 ** 2 + 10 ** 2)
    assert w1["length"] == pytest.approx((240 + 2 * breakout) * 1.05 + 50, abs=0.1)
    assert w1["voltage_drop_V"] == pytest.approx(2.0 * 33.3 * w1["length"] / 1e6, rel=1e-3)
    trunk = [s for s in h["segments"] if set(s["wires"]) == {"W1", "W2", "W3"}]
    assert trunk and trunk[0]["diameter"] == pytest.approx(1.15 * math.sqrt(2 * 1.5 ** 2 + 1.1 ** 2), abs=1e-3)
    assert h["component"].startswith("Harness") and not h["warnings"]
    svg = Path(S.electrical_formboard(str(tmp_path / "harness"))["path"]).read_text(encoding="utf-8")
    assert "formboard" in svg and "J3" in svg and "W3" in svg
    S.electrical_wires([{"id": "W9", "from": "J3-3", "to": "J1-4"}])
    with pytest.raises(Exception, match="pins 1-2"):
        S.electrical_harness()
    S.explode_step(["ConnA-1"], [0, 0, 1], 60)
    S.explode_step(["ConnB-1", "ConnC-1"], [0, 0, 1], 100)
    assert S.composer_auto("assembly")["steps"] == ["Parts", "Install ConnB-1, ConnC-1", "Install ConnA-1"]
    steps = S.composer_steps()["steps"]
    assert steps[0]["offsets"]["ConnB-1"] == [0.0, 0.0, 100.0] and steps[2]["offsets"] == {}
    S.composer_add_step("See through", explode_steps=1, transparent=["Plate-1"])
    html = Path(S.composer_export(str(tmp_path / "rig"))["path"]).read_text(encoding="utf-8")
    assert '"steps":' in html and "See through" in html and "splay" in html
    saved = S.APP.asm.to_dict()
    assert saved["electrical"]["connectors"]["J1"]["pins"] == 4 and len(saved["composer"]["steps"]) == 4


def test_persistent_names_follow_moved_edges():
    from build123d import Vector
    from openswx.geom import edge_ref
    from openswx.naming import labels_of
    S.new_part("Step")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 10])
    e1 = S.extrude(sk, depth=20)["feature"]
    sk2 = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk2, [40, 0], [50, 10])
    e2 = S.extrude(sk2, depth=40)["feature"]
    doc = S.APP.active_doc
    names = set(labels_of(doc.body).values())
    assert {"Boss-Extrude1:cap0", "Boss-Extrude2:cap+"} <= names
    assert "Boss-Extrude2:Sketch2.Line7" not in names  # the high step's top lies on the low step's top plane: it keeps that name
    edges = doc.body.edges()
    i = min(range(len(edges)), key=lambda k: (edges[k].center() - Vector(45, 10, 40)).length)
    assert edge_ref(edges[i], doc.body)["faces"] == ["Boss-Extrude1:Sketch1.Line7", "Boss-Extrude2:cap+"]
    fil = S.fillet(3, edges=[f"Edge{i}"])["feature"]
    # the steps swap heights: the stored edge position (z 40) is now nearest the low step's edge (z 38)
    S.edit_feature(e1, {"depth": 38})
    S.edit_feature(e2, {"depth": 22})
    assert doc.state[fil]["status"] == "ok"
    (blend,) = [f for f in doc.body.faces() if f.geom_type.name == "CYLINDER"]
    assert blend.center().X == pytest.approx(45, abs=0.1) and blend.center().Z == pytest.approx(21.1, abs=0.1)


def test_mates_on_edges_vertices_and_assembly_planes():
    import numpy as np
    from openswx.assembly import ASSEMBLY, plane_ref
    from openswx.geom import edge_ref
    def block(name):
        S.new_part(name)
        sk = S.create_sketch("Front")["sketch"]
        S.sketch_rectangle(sk, [0, 0], [40, 20])
        S.extrude(sk, depth=10)
    block("A")
    block("B")
    S.new_assembly("EdgeAsm")
    S.insert_component("A")
    S.insert_component("B", position=[100, 50, 30])
    asm = S.APP.asm
    body = asm._local_body(asm.component("B-1"))
    edge = next(e for e in body.edges() if e.geom_type.name == "LINE" and abs(e.length - 40) < 1e-9
                and abs(e.center().Y - 20) < 1e-9 and abs(e.center().Z - 10) < 1e-9)
    line = {"component": "B-1", "line": edge_ref(edge, body)}
    # B's top front edge lies in the assembly's Top plane shifted 0 (coincident), its corner vertex on A's right face
    asm.add_mate("coincident", line, {"component": ASSEMBLY, "plane": plane_ref("Top")})
    a_right = {"component": "A-1", "face": S.APP.asm.face_ref_for("A-1/Face" + str(next(
        i for i, f in enumerate(asm._local_body(asm.component("A-1")).faces())
        if f.geom_type.name == "PLANE" and abs(f.center().X - 40) < 1e-9)))["face"]}
    asm.add_mate("coincident", {"component": "B-1", "vertex": [0.0, 20.0, 10.0]}, a_right)
    asm.add_mate("distance", {"component": "B-1", "vertex": [0.0, 20.0, 0.0]},
                 {"component": ASSEMBLY, "plane": plane_ref("Front")}, value=5.0)
    assert not asm.status["failing"]
    R, t = asm.component("B-1").matrix()
    p_edge = R @ np.array(tuple(edge.center())) + t
    assert p_edge[1] == pytest.approx(0, abs=1e-6)  # the edge is in the Top plane
    corner = R @ np.array([0.0, 20.0, 10.0]) + t
    assert corner[0] == pytest.approx(40, abs=1e-6)  # the vertex on A's right face
    assert (R @ np.array([0.0, 20.0, 0.0]) + t)[2] == pytest.approx(5, abs=1e-6)  # 5 mm in front of the Front plane
    with pytest.raises(Exception, match="not defined"):
        asm.add_mate("tangent", line, a_right)


def test_solidworks_drawing_gdt(tmp_path, monkeypatch):
    """A .slddrw feature control frame: the characteristic tag, its texts up to <Gtol>, then the frame's
    position (the first sheet point after the texts that the view record before it does not also hold)."""
    import struct
    from openswx import sw_drawing
    u = lambda s: b"\x00\x07" + s.encode("utf-16-le") + b"\x00\x00\x03"  # noqa: E731
    at = lambda x, y: b"\x00" * 24 + struct.pack("<3d", x / 1000, y / 1000, -0.0)  # noqa: E731
    definition = (u("Sheet1") + u("Drawing View1") + u("C:/x/plate.SLDPRT") + u("*Front") + at(120.0, 40.0)
                  + u("DetailItem40") + u("<IGTOL-PARA>") + u("<MOD-DIAM>0.05") + u("<Gtol>")
                  + at(120.0, 40.0) + at(300.0, 210.0)
                  + u("DetailItem41") + u("<IGTOL-FLAT>") + u("<Gtol>") + at(50.0, 50.0))  # (no tolerance: left out)
    names = b"\x01\x00" + u("Sheet1")
    monkeypatch.setattr(sw_drawing, "read_streams", lambda p: {"Contents/Definition": definition, "SheetPreviews/SheetNames": names})
    drw = sw_drawing.read_drawing(str(tmp_path / "plate.slddrw"))
    assert drw["gdt"] == [{"characteristic": "parallelism", "tolerance": 0.05, "diameter": True, "datums": [],
                           "position": (300.0, 210.0), "sheet": "Sheet1"}]


def test_disk_cache_for_files(tmp_path, monkeypatch):
    from build123d import Box
    from openswx import cache
    src = tmp_path / "part.bin"
    src.write_bytes(b"one")
    monkeypatch.setenv("OPENSWX_CACHE", "off")
    assert cache.key(str(src), "x") is None  # (off: nothing is kept)
    monkeypatch.setenv("OPENSWX_CACHE", str(tmp_path / "cache"))
    k = cache.key(str(src), "x")
    assert k and k != cache.key(str(src), "y") and cache.get_json("history", k) is None
    cache.put_json("history", k, {"ok": True, "n": 3})
    assert cache.get_json("history", k) == {"ok": True, "n": 3}
    cache.put_shape("body", k, Box(10, 20, 30), {"notes": ["a"]})
    shape, meta = cache.get_shape("body", k)
    assert shape.volume == pytest.approx(6000) and meta == {"notes": ["a"]}
    src.write_bytes(b"two")  # the file changed: a new key, nothing cached for it
    assert cache.key(str(src), "x") != k and cache.get_json("history", cache.key(str(src), "x")) is None
    assert cache.clear() == 3
