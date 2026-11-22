"""Tests: drawing (split out of the former test_core.py; shared helpers in core_helpers.py)."""
from core_helpers import *  # noqa: F401,F403


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


def test_drawing_units():
    block()
    assert ">100<" in S.create_drawing()["svg"]
    S.set_units("in")
    svg = S.create_drawing()["svg"]
    assert ">3.937<" in svg and ">100<" not in svg and ">in<" in svg  # 100 mm overall width, units row
    S.set_units("mm")


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


def test_delete_view_of_automatic_drawing():
    block()
    from openswx.drawing import make_drawing
    S.drawing_delete_view("top")
    assert S.drawing_info()["settings"]["hidden_views"] == ["top"]
    views = make_drawing(S.APP.doc)["views"]
    assert "top" not in views and "front" in views
    with pytest.raises(Exception):
        S.drawing_delete_view("nothing")


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
