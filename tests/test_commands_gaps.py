"""Commands that had no test of their own: cut variants, sketch editing tools, delete commands, queries.

Volumes are checked against closed-form values; deletes are followed by a save / open round trip, so a
command that leaves a broken document fails here.
"""
import math

import pytest

from openswx import session as S


def vol():
    return S.get_mass_properties()["volume"]


def block(name, x=40.0, y=20.0, z=10.0):
    S.new_part(name)
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [x, y])
    S.extrude(sk, z)
    return sk


def roundtrip(tmp_path):
    """Save, close, reopen: the document must come back with the same volume and no feature in error."""
    d = S.APP.active_doc
    v = d.body.volume if getattr(d, "body", None) is not None else None
    p = S.save_part(str(tmp_path / d.name))["saved"]
    S.close_document(d.name)
    S.open_part(p)
    assert not [f for f in S.get_model().get("features", []) if f.get("status") == "error"]
    if v is not None:
        assert abs(vol() - v) < 1e-6 * max(1.0, v)


def sketch(sk):
    """The sketch's geometry with point ids resolved: {entity id: {type, p1, p2, center, radius, ...}}."""
    d = S.get_feature(sk)["params"]["sketch"]
    pts = d["points"]
    out = {}
    for eid, e in d["entities"].items():
        r = dict(e)
        for k in ("p1", "p2", "center", "start", "end", "p"):
            if isinstance(e.get(k), str) and e[k] in pts:
                r[k] = pts[e[k]]
        out[eid] = r
    return out


def of_type(sk, kind):
    return {k: e for k, e in sketch(sk).items() if e["type"] == kind}


# ------------------------------------------------------------------ cut variants
def test_cut_revolve_removes_a_ring():
    S.new_part("CutRev")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 20)
    S.extrude(sk, 10)
    v0 = vol()
    assert abs(v0 - math.pi * 400 * 10) < 1e-6
    # a 2 x 3 groove section in the Front plane, revolved about the part's axis (the sketch centerline)
    g = S.create_sketch("Front")["sketch"]
    S.sketch_centerline(g, [0, 0], [0, 10])
    S.sketch_rectangle(g, [10, 7], [12, 10])
    S.cut_revolve(g)
    ring = math.pi * (12 ** 2 - 10 ** 2) * 3
    assert abs(v0 - vol() - ring) < 1e-4
    S.edit_feature(S.get_model()["features"][-1]["name"], {"angle": 180})
    assert abs(v0 - vol() - ring / 2) < 1e-4


def test_cut_sweep_removes_a_cylinder():
    S.new_part("CutSweep")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-20, -10], [20, 10])
    S.extrude(sk, 10)
    v0 = vol()
    prof = S.create_sketch("Top")["sketch"]  # a circle at the origin, swept up through the block
    S.sketch_circle(prof, [0, 0], 3)
    path = S.create_sketch("Front")["sketch"]
    S.sketch_line(path, [0, 0], [0, 10])
    S.cut_sweep(prof, path)
    assert abs(v0 - vol() - math.pi * 9 * 10) < 1e-4


def test_cut_loft_between_two_squares():
    block("CutLoft", 40, 40, 20)
    v0 = vol()
    a = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(a, [10, 10], [30, 30])
    plane = S.reference_plane("Top", offset=20)["feature"]
    b = S.create_sketch(plane)["sketch"]
    S.sketch_rectangle(b, [15, 15], [25, 25])
    S.cut_loft([a, b], ruled=True)
    removed = 20 / 3 * (400 + 100 + math.sqrt(400 * 100))  # frustum of a square pyramid
    assert abs(v0 - vol() - removed) < 1e-3


# ------------------------------------------------------------------ sketch tools
def test_sketch_arc_polygon_slot_areas():
    S.new_part("SkPolygon")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_polygon(sk, [0, 0], 10, sides=6)
    S.extrude(sk, 1)
    assert abs(vol() - 6 * 0.5 * 100 * math.sin(math.radians(60))) < 1e-6  # hexagon inscribed in r = 10

    S.new_part("SkSlot")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_slot(sk, [0, 0], [20, 0], 8)
    S.extrude(sk, 2)
    assert abs(vol() - (20 * 8 + math.pi * 16) * 2) < 1e-6

    S.new_part("SkArc")
    sk = S.create_sketch("Top")["sketch"]
    arc = S.sketch_arc(sk, [0, 0], [10, 0], [-10, 0])["result"]  # counter-clockwise: a half circle
    S.sketch_line(sk, arc["points"]["end"], arc["points"]["start"])  # close it across the diameter
    S.extrude(sk, 3)
    assert abs(vol() - math.pi * 100 / 2 * 3) < 1e-6


def test_sketch_fillet_and_delete():
    S.new_part("SkFillet")
    sk = S.create_sketch("Top")["sketch"]
    r = S.sketch_rectangle(sk, [0, 0], [30, 20])["result"]
    S.sketch_fillet(sk, r["points"][0], 5)
    arcs = of_type(sk, "arc")
    assert len(arcs) == 1
    S.extrude(sk, 1)
    assert abs(vol() - (600 - (25 - math.pi * 25 / 4))) < 1e-6
    S.undo()  # the extrude
    S.sketch_delete(sk, next(iter(arcs)))
    assert not of_type(sk, "arc")


def test_sketch_offset_and_mirror():
    S.new_part("SkOffset")
    sk = S.create_sketch("Top")["sketch"]
    c = S.sketch_circle(sk, [0, 0], 10)["result"]["entity"]
    S.sketch_offset(sk, [c], 2)
    S.extrude(sk, 1)  # the ring between the two circles
    ring = vol()
    assert any(abs(ring - math.pi * (r2 - 100)) < 1e-6 or abs(ring - math.pi * (100 - r2)) < 1e-6 for r2 in (144, 64))

    S.new_part("SkMirror")
    sk = S.create_sketch("Top")["sketch"]
    axis = S.sketch_centerline(sk, [0, -20], [0, 20])["result"]["entity"]
    c = S.sketch_circle(sk, [15, 0], 4)["result"]["entity"]
    S.sketch_mirror(sk, [c], axis)
    assert len(of_type(sk, "circle")) == 2
    S.extrude(sk, 2)
    assert abs(vol() - 2 * math.pi * 16 * 2) < 1e-6
    bb = S.get_mass_properties()["bounding_box"]
    assert "-19" in str(bb) or abs(min(v for v in _flat(bb)) + 19) < 1e-6  # the copy is at x = -15


def _flat(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _flat(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _flat(v)
    elif isinstance(x, (int, float)):
        yield x


def test_sketch_trim_and_extend():
    S.new_part("SkTrim")
    sk = S.create_sketch("Top")["sketch"]
    h = S.sketch_line(sk, [-10, 0], [10, 0])["result"]["entity"]
    S.sketch_line(sk, [0, -10], [0, 10])
    S.sketch_trim(sk, h, [5, 0])  # the part right of the crossing goes
    xs = [p[0] for e in of_type(sk, "line").values() for p in (e["p1"], e["p2"])]
    assert abs(max(xs)) < 1e-6 and abs(min(xs) + 10) < 1e-6

    S.new_part("SkExtend")
    sk = S.create_sketch("Top")["sketch"]
    short = S.sketch_line(sk, [0, 0], [5, 0])["result"]["entity"]
    S.sketch_line(sk, [20, -10], [20, 10])
    S.sketch_extend(sk, short, [5, 0])
    line = sketch(sk)[short]
    assert abs(max(line["p1"][0], line["p2"][0]) - 20.0) < 1e-6


def test_sketch_move_point_construction_and_visibility():
    S.new_part("SkMove")
    sk = S.create_sketch("Top")["sketch"]
    r = S.sketch_line(sk, [0, 0], [10, 0])["result"]
    S.sketch_move_point(sk, r["points"][1], [10, 7])
    line = sketch(sk)[r["entity"]]
    assert abs(line["p2"][1] - 7) < 1e-6
    S.sketch_set_construction(sk, [r["entity"]], True)
    assert sketch(sk)[r["entity"]]["construction"]
    S.set_sketch_visibility(sk, True)
    assert sk not in [s["name"] for s in S.get_mesh()["sketches"] if not s.get("hidden")]
    S.set_sketch_visibility(sk, False)
    assert sk in [s["name"] for s in S.get_mesh()["sketches"] if not s.get("hidden")]


# ------------------------------------------------------------------ deletes leave a document that still works
def test_delete_configuration_variable_equation(tmp_path):
    block("DelCfg")
    S.set_global_variable("depth", 12)
    S.set_equation("D1@Boss-Extrude1", '"depth"')
    assert abs(vol() - 40 * 20 * 12) < 1e-6
    S.clear_equation("D1@Boss-Extrude1")
    assert not S.get_equations()["equations"]
    S.delete_global_variable("depth")
    assert not S.get_equations()["global_variables"]
    assert abs(vol() - 40 * 20 * 12) < 1e-6  # the dimension keeps its value
    S.add_configuration("Short")
    S.activate_configuration("Default")
    S.delete_configuration("Short")
    with pytest.raises(S.CommandError):
        S.delete_configuration("Default")
    roundtrip(tmp_path)


def test_delete_body(tmp_path):
    S.new_part("TwoBodies")
    a = S.create_sketch("Top")["sketch"]
    S.sketch_circle(a, [0, 0], 5)
    S.extrude(a, 4)
    b = S.create_sketch("Top")["sketch"]
    S.sketch_circle(b, [30, 0], 3)
    S.extrude(b, 4, merge=False)
    assert abs(vol() - math.pi * (25 + 9) * 4) < 1e-6
    S.delete_body([1])
    assert any(abs(vol() - math.pi * r2 * 4) < 1e-6 for r2 in (25, 9))  # one of the two is left
    roundtrip(tmp_path)


def test_assembly_deletes(tmp_path):
    block("AsmPart", 10, 10, 10)
    S.save_part(str(tmp_path / "AsmPart.oswprt"))
    S.new_assembly("DelAsm")
    a = S.insert_component("AsmPart")["component"]
    b = S.insert_component("AsmPart", position=[30, 0, 0])["component"]
    c = S.insert_component("AsmPart", position=[60, 0, 0])["component"]
    S.add_mate("parallel", f"{a}/Face0", f"{b}/Face0")
    S.delete_mate(S.get_model()["mates"][0]["name"])
    assert not S.get_model()["mates"]
    S.add_mate("parallel", f"{a}/Face0", f"{b}/Face0")
    S.delete_component(b)  # takes its mate with it
    m = S.get_model()
    assert [x["name"] for x in m["components"]] == [a, c] and not m["mates"]

    S.component_pattern([c], "linear", 3, spacing=20, direction=[0, 1, 0])
    m = S.get_model()
    assert len(m["components"]) == 4
    S.delete_assembly_feature(m["assembly_features"][-1]["name"])
    assert len(S.get_model()["components"]) == 2

    S.save_alternate_position("Open")
    S.move_component(c, position=[90, 0, 0])
    S.show_alternate_position("Open")
    comp = S.APP.asm.component(c)
    assert abs(float(comp.x[3]) - 60) < 1e-6  # (x = rotation vector + translation)
    S.delete_alternate_position("Open")
    with pytest.raises(S.CommandError):
        S.show_alternate_position("Open")
    p = S.save_part(str(tmp_path / "DelAsm"))["saved"]
    S.close_document("DelAsm")
    S.open_part(p)
    assert len(S.get_model()["components"]) == 2


def test_drawing_sheet_and_table_commands():
    block("DrwPart", 60, 40, 10)
    h = S.create_sketch("Top")["sketch"]
    S.sketch_circle(h, [15, 20], 4)
    S.cut_extrude(h, end_condition="ThroughAllBoth")
    S.drawing_new()
    S.drawing_sheet_properties(name="Main", scale=2.0)
    info = S.drawing_info()
    assert info["active"] == "Main" and info["sheets"][0]["scale"] == 2
    second = S.drawing_add_sheet(name="Second")["sheet"]
    view = S.drawing_add_view("top", 100, 150)["view"]["id"]
    side = S.drawing_projected_view(view, "right")["view"]
    assert side["kind"] == "projected" and side["parent"] == view
    assert isinstance(S.drawing_pick(view, [100, 150]), dict)

    # a hole table: move it, then delete it
    from openswx.drawing import drawing_sheets as DS
    st = S.APP.doc.drawing
    v = next(x for s in st["sheets"] for x in s["views"] if x["id"] == view)
    g = DS.view_geometry(S.APP.doc, st, v)
    t = DS.placement(st, v, g)
    ht = S.drawing_hole_table(view, list(t(g["box"][0] + 0.5, g["box"][1] + 0.5)))
    assert len(ht["holes"]) == 1 and abs(ht["holes"][0]["diameter"] - 8) < 1e-6
    tid = ht["table"]

    def tables():
        return [x for s in S.APP.doc.drawing["sheets"] for x in s.get("tables", []) if x.get("id") == tid]
    S.drawing_move_table(tid, 20, 250)
    assert [(x["x"], x["y"]) for x in tables()] == [(20, 250)]
    S.drawing_delete_table(tid)
    assert not tables()

    S.drawing_delete_sheet(second)
    assert [s["name"] for s in S.drawing_info()["sheets"]] == ["Main"]
    with pytest.raises(S.CommandError):
        S.drawing_delete_sheet("Main")  # a drawing keeps at least one sheet


# ------------------------------------------------------------------ queries and smaller modules
def test_query_commands():
    block("Queries", 40, 20, 10)
    docs = S.list_documents()
    assert docs["active"] == "Queries" and any(d["name"] == "Queries" and d["dirty"] for d in docs["documents"])
    ec = S.list_end_conditions()
    assert ec["Blind"] == {"sw_value": 0, "supported": True} and ec["ThroughAllBoth"]["sw_value"] == 9
    assert len(S.get_activity(limit=5)["log"]) == 5
    faces = S.get_topology()["faces"]
    f0 = faces[0]
    opposite = next(f for f in faces if abs(sum(a * b for a, b in zip(f["normal"], f0["normal"])) + 1) < 1e-9)
    d = S.measure(f0["id"], opposite["id"])
    assert any(abs(d["distance"] - x) < 1e-6 for x in (10, 20, 40))
    assert len(S.plastics_materials()["materials"]) >= 8
    assert S.get_mesh()["faces"]
    v = vol()
    S.rebuild()  # a forced full rebuild gives the same body
    assert abs(vol() - v) < 1e-9


def test_sheet_metal_info_and_swept_surface():
    S.new_part("SM")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [50, 30])
    S.base_flange(sk, thickness=2)
    assert abs(S.sheet_metal_info()["thickness"] - 2) < 1e-9

    S.new_part("SweptSurf")
    prof = S.create_sketch("Top")["sketch"]
    S.sketch_line(prof, [0, 0], [10, 0])
    path = S.create_sketch("Front")["sketch"]
    S.sketch_line(path, [0, 0], [0, 20])
    S.swept_surface(prof, path)
    surfaces = S.APP.doc.ctx.surfaces
    assert len(surfaces) == 1 and abs(next(iter(surfaces.values())).area - 200) < 1e-6


def test_plastics_results_and_cfd_commands():
    S.new_part("Lid")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [60, 40])
    S.extrude(sk, depth=2)
    S.plastics_fill(gates=[[0, 20, 1]], material="PP")
    r = S.plastics_results("fill_time")
    assert r and max(_flat(r)) > 0

    S.new_part("Ball")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_centerline(sk, [0, -8], [0, 8])
    arc = S.sketch_arc(sk, [0, 0], [0, -5], [0, 5])["result"]["points"]  # the right half circle
    S.sketch_line(sk, arc["end"], arc["start"])
    S.revolve(sk)
    assert abs(vol() - 4 / 3 * math.pi * 125) < 1e-3
    nu = 1.81e-5 / 1.204
    s = S.cfd_external(speed=20 * nu / 0.010, cells_across=8, max_seconds=60)  # Re 20: laminar, coarse grid
    assert s["drag_coefficient"] > 1.5  # (2.73 on a fine grid; a coarse one over-reads)
    assert S.cfd_results()


def test_electrical_composer_library_pdm_leftovers(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path / "home"))
    for n, size in (("Plate", (120, 80, 5)), ("ConnA", (20, 10, 10)), ("ConnB", (20, 10, 10))):
        block(n, *size)
    S.save_part(str(tmp_path / "ConnB.oswprt"))
    S.new_assembly("Rig")
    S.insert_component("Plate", [0, 0, 0])
    S.insert_component("ConnA", [10, 10, 5])
    S.insert_component("ConnB", [90, 10, 5])
    S.electrical_connector("J1", "ConnA-1", pins=2)
    S.electrical_connector("J2", "ConnB-1", pins=2)
    S.electrical_route_segment([[20, 15, 20], [100, 15, 20]])
    csv = tmp_path / "wires.csv"
    csv.write_text("Wire,From,To,Gauge,Color\nW1,J1-1,J2-1,20 AWG,RD\nW2,J1-2,J2-2,20 AWG,BK\n", encoding="utf-8")
    S.electrical_import_wires(str(csv))
    info = S.electrical_info()
    assert [w["id"] for w in info["wires"]] == ["W1", "W2"] and len(info["connectors"]) == 2
    S.electrical_clear("wires")
    info = S.electrical_info()
    assert not info["wires"] and len(info["connectors"]) == 2
    S.electrical_clear()
    assert not S.electrical_info()["connectors"]

    S.composer_add_step("Start", note="everything in place")
    assert S.composer_steps()["steps"]
    S.composer_clear()
    assert not S.composer_steps()["steps"]

    # PDM: where used, in a vault chosen with pdm_set_vault
    vault = S.pdm_set_vault(str(tmp_path / "vault"))["vault"]
    assert (tmp_path / "vault").is_dir() and vault
    S.activate_document("ConnB")
    S.pdm_check_in(comment="part")
    S.activate_document("Rig")
    S.save_part(str(tmp_path / "Rig.oswasm"))
    S.pdm_check_in(comment="rig")
    assert "Rig.oswasm" in str(S.pdm_where_used("ConnB.oswprt"))

    # Design Library: delete an item
    S.activate_document("ConnA")
    S.add_part_to_design_library()
    parts = S.list_design_library()["parts"]
    assert parts
    item = parts[0]
    S.delete_library_item(item["path"] if isinstance(item, dict) else item)
    assert not S.list_design_library()["parts"]


# ------------------------------------------------------------------ mesh import, equation-driven curves
def test_stl_and_3mf_import_as_solids(tmp_path):
    block("MeshSrc", 30, 20, 5)
    h = S.create_sketch("Top")["sketch"]
    S.sketch_circle(h, [10, 10], 4)
    S.cut_extrude(h, end_condition="ThroughAllBoth")
    v = vol()
    for ext in ("stl", "3mf"):
        S.activate_document("MeshSrc")
        out = tmp_path / f"m.{ext}"
        S.export_file(str(out))
        r = S.open_part(str(out))
        assert r["name"] == "m" or r.get("model", {}).get("name") == "m" or S.APP.active == "m"
        assert vol() == pytest.approx(v, rel=2e-3)  # the hole is faceted: slightly more material
        faces = S.get_topology()["faces"]
        assert sum(1 for f in faces if f["type"] == "plane" and f["area"] > 100) >= 2  # top and bottom merged back
        S.close_document(S.APP.active)
    # an open mesh is refused with a reason
    bad = tmp_path / "open.stl"
    bad.write_text("solid o\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid o\n")
    with pytest.raises(S.CommandError, match="not a closed volume|cannot read"):
        S.open_part(str(bad))


def test_equation_driven_curve():
    S.new_part("EqCurve")
    S.set_global_variable("a", 20)
    sk = S.create_sketch("Top")["sketch"]
    # a parabola y = x^2 / a from x = -10 to 10, closed by a chord: area = (2/3) * width * height
    r = S.sketch_equation_curve(sk, "t", 't^2/"a"', -10, 10, points=41)["result"]
    pts = S.get_feature(sk)["params"]["sketch"]["points"]
    first, last = pts[r["points"][0]], pts[r["points"][-1]]
    assert first == pytest.approx([-10, 5]) and last == pytest.approx([10, 5])
    S.sketch_line(sk, r["points"][-1], r["points"][0])
    S.extrude(sk, 3)
    assert vol() == pytest.approx(2 / 3 * 20 * 5 * 3, rel=1e-3)
    # a full circle from sin / cos (degrees), closed
    S.new_part("EqCircle")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_equation_curve(sk, "10*cos(t)", "10*sin(t)", 0, 360, points=60, closed=True)
    S.extrude(sk, 1)
    assert vol() == pytest.approx(math.pi * 100, rel=2e-3)
    with pytest.raises(S.CommandError, match="cannot evaluate"):
        S.sketch_equation_curve(sk, "1/(t-5)", "t", 0, 10, points=11)
    with pytest.raises(S.CommandError):
        S.sketch_equation_curve(sk, "t", "t", 1, 1)
