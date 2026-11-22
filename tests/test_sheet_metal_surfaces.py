"""Tests: sheet metal surfaces (split out of the former test_core.py; shared helpers in core_helpers.py)."""
from core_helpers import *  # noqa: F401,F403


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
