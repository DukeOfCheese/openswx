"""Tests: assembly (split out of the former test_core.py; shared helpers in core_helpers.py)."""
from core_helpers import *  # noqa: F401,F403


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


# ---------------------------------------------------------------- ecosystem: toolbox, PDM, eDrawings, macros
def test_toolbox_standards():
    cat = S.list_fasteners()
    assert {"button_head_screw", "countersunk_screw", "dowel_pin", "ansi_hex_bolt", "ansi_washer"} <= set(cat)
    assert cat["ansi_socket_cap_screw"]["family"] == "ANSI Inch" and "1/4-20" in cat["ansi_socket_cap_screw"]["sizes"]
    S.new_assembly()
    pin = S.insert_fastener("dowel_pin", "6", length=20)["component"]
    bolt = S.insert_fastener("ansi_hex_bolt", "1/4-20", length=1.0)["component"]
    asm = S.APP.asm
    from openswx.core.features import mass_properties
    assert mass_properties(asm._local_body(asm.component(pin)))["volume"] == pytest.approx(PI * 9 * 20, rel=1e-6)
    bb = asm._local_body(asm.component(bolt)).bounding_box()
    assert bb.min.Z == pytest.approx(-25.4) and bb.size.X == pytest.approx(0.4375 * 25.4 / math.cos(PI / 6), rel=1e-3)
    with pytest.raises(S.CommandError):
        S.insert_fastener("button_head_screw", "M24", length=30)  # not in ISO 7380


def test_piping_library_route_and_fittings():
    from openswx.manufacturing import piping
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


def test_cfd_pipe_flow_and_sphere_drag():
    from build123d import Cylinder, Rot, Sphere
    from openswx.analysis.cfd import external_flow, internal_flow
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


def test_mates_on_edges_vertices_and_assembly_planes():
    import numpy as np
    from openswx.core.assembly import ASSEMBLY, plane_ref
    from openswx.core.geom import edge_ref
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
