"""Tests: sketch (split out of the former test_core.py; shared helpers in core_helpers.py)."""
from core_helpers import *  # noqa: F401,F403


def test_reference_plane_sketch():
    S.new_part()
    S.reference_plane("Top", offset=30)
    sk = S.create_sketch("Plane1")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, depth=10)
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["min"][1] == pytest.approx(30) and bb["max"][1] == pytest.approx(40)


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
