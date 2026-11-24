"""Tests: features (split out of the former test_core.py; shared helpers in core_helpers.py)."""
from core_helpers import *  # noqa: F401,F403


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


def test_view_image_is_png():
    block()
    import base64
    png = base64.b64decode(S.get_view_image("isometric", 200, 150)["png_base64"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


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


def test_variable_fillet():
    block()
    e = [x["id"] for x in S.get_topology()["edges"] if x["type"] == "line" and abs(x["length"] - 100) < 1e-6][0]
    v0 = vol()
    S.variable_fillet([e], 2, 6)
    removed = v0 - vol()
    # between constant fillets of r=2 and r=6 (removed area (1 - pi/4) r^2 per unit length)
    assert (1 - PI / 4) * 4 * 100 < removed < (1 - PI / 4) * 36 * 100


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


def test_revolve_half_section():
    """A revolve whose sketch is not decoded takes its profile from the stored body: the half-section on one
    side of the axis, closed along it, turned about the axis to where later cuts (holes in the sketch plane)
    leave the most - revolving it gives the body before the cuts."""
    import numpy as np
    from build123d import Cylinder, Pos, Rot
    from openswx.exchange import sw_rebuild as R
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
    from openswx.exchange.sw_rebuild import _hole_variants
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
