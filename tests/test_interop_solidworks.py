"""Tests: interop solidworks (split out of the former test_core.py; shared helpers in core_helpers.py)."""
from core_helpers import *  # noqa: F401,F403


def test_sw_end_condition_integers_and_midplane():
    S.new_part()
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [10, 10])
    S.extrude(sk, depth=20, end_condition=6)  # swEndCondMidPlane
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["min"][2] == pytest.approx(-10) and bb["max"][2] == pytest.approx(10)


@pytest.mark.skipif(not os.path.exists(SAMPLE), reason="SOLIDWORKS sample files not installed")
def test_sldprt_reader(tmp_path):
    from openswx.exchange.sldprt import extract, read_info
    info = read_info(SAMPLE)
    assert info["document_type"] == "PART"
    assert "Contents/Config-0-Partition" in info["streams"]
    assert any(b["kind"] == "partition" for b in info["parasolid"])
    files = extract(SAMPLE, str(tmp_path))
    assert any(f.endswith(".x_b") for f in files)


def test_sldprt_native_geometry(tmp_path):
    """read_sldprt: container record -> Config-0-Partition stream -> zlib -> Parasolid partition -> B-rep.
    The container is synthesised around a partition taken from a SOLIDWORKS 2025 part."""
    import json
    import struct
    import zlib
    from openswx.exchange.sldprt import read_sldprt
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
    from openswx.exchange.sldprt import assembly_tree
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


def test_sw_feature_tree(tmp_path):
    """Feature history from ResolvedFeatures: tree order, classes, dimensions, end conditions, feature ids."""
    from openswx.exchange.sw_tree import read_tree
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
    from openswx.exchange.sw_sketch import read_sketches
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
    from openswx.core.document import PartDocument
    from openswx.exchange.sw_rebuild import rebuild_part
    doc = PartDocument("t")
    r = rebuild_part(_sw_part(tmp_path, name), doc)
    assert r["features"] == features and [f["name"] for f in doc.features] == features
    assert abs(r["volume"] - r["stored_volume"]) <= 2e-3 * r["stored_volume"]
    assert abs(r["area"] - r["stored_area"]) <= 1e-3 * r["stored_area"]


def test_sw_rebuild_imports_undecoded_feature(tmp_path, monkeypatch):
    """A feature type that is not decoded comes in as its exact contribution, read off the stored body next to
    the faces SOLIDWORKS tagged with its id; the features around it stay parametric."""
    import openswx.exchange.sw_tree as sw_tree
    from openswx.core.document import PartDocument
    from openswx.exchange.sw_rebuild import rebuild_part
    real = sw_tree.read_tree

    def tree(path, config=None):
        return [{**f, "cls": "moSomethingNew_c"} if f["name"] == "Fillet1" else f for f in real(path, config)]
    monkeypatch.setattr(sw_tree, "read_tree", tree)
    doc = PartDocument("t")
    r = rebuild_part(_sw_part(tmp_path, "fil_r5"), doc)
    assert r["imported"] == ["Fillet1"] and [f["type"] for f in doc.features] == ["sketch", "extrude", "import"]
    assert abs(r["volume"] - r["stored_volume"]) <= 1e-3 * r["stored_volume"]
    # but the feature that makes the first solid must be editable, else the part opens as its stored body
    from openswx.exchange.sw_rebuild import RebuildError
    monkeypatch.setattr(sw_tree, "read_tree", lambda path, config=None: [
        {**f, "cls": "moSomethingNew_c"} if f["name"] == "Boss-Extrude1" else f for f in real(path, config)])
    with pytest.raises(RebuildError, match="base feature"):
        rebuild_part(_sw_part(tmp_path, "fil_r5"), PartDocument("t"))


@pytest.mark.parametrize("name, cls", [("Flat-Pattern1", "moFlatPattern_c"),
                                       ("Flat-Pattern1", "moTemplateFlatPattern_c"),
                                       ("Flatten-<EdgeBend1>1", "moUiBend_c")])
def test_sw_rebuild_skips_flat_pattern(tmp_path, monkeypatch, name, cls):
    """The Flat-Pattern feature at the end of a sheet-metal tree (written as moTemplateFlatPattern_c by
    SOLIDWORKS 2018 and later) and its per-bend Flatten entries (moUiBend_c) do not change the folded body:
    they are skipped, not imported or refused."""
    import openswx.exchange.sw_tree as sw_tree
    from openswx.core.document import PartDocument
    from openswx.exchange.sw_rebuild import rebuild_part
    real = sw_tree.read_tree
    monkeypatch.setattr(sw_tree, "read_tree", lambda path, config=None: [
        *real(path, config), {"name": name, "cls": cls, "id": None, "params": []}])
    doc = PartDocument("t")
    r = rebuild_part(_sw_part(tmp_path, "fil_r5"), doc)
    assert not r["imported"] and [f["type"] for f in doc.features] == ["sketch", "extrude", "fillet"]
    assert abs(r["volume"] - r["stored_volume"]) <= 1e-3 * r["stored_volume"]


def test_tagged_faces_extended_by_mirror():
    """A feature's tagged face that a later mirror merged with its copy (a pipe run, mirrored to full length)
    still matches the half the feature made; a candidate that falls short of a tagged end cap does not."""
    from build123d import Box, Pos
    from openswx.core.features import _valid
    from openswx.exchange.sw_rebuild import _faces_on_body
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


def test_sw_rebuild_draft():
    """A draft (not decoded: neutral plane and faces) rebuilt by trying the body's planes, scored against
    the stored body: a 10 degree draft on the sides of a block."""
    from openswx.exchange import sw_rebuild as rb
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 30])
    S.extrude(sk, depth=10)
    doc = S.APP.doc
    sides = [f for f in doc.body.faces() if abs(f.normal_at().Y) < 1e-6]
    from openswx.core.geom import face_ref
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
    from openswx.exchange import sw_rebuild as rb
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
    from openswx.exchange.sw_rebuild import _sketch_dict
    from openswx.exchange.sw_sketch import read_sketches
    for case, code, kind in (("rel_perp", 8, "perpendicular"), ("rel_par", 7, "parallel")):
        sk = read_sketches(_sw_part(tmp_path, case))["Sketch1"]
        assert [r["type"] for r in sk["relations"]] == [code]
        assert sorted(sk["relations"][0]["refs"]) == [("sgLineHandle", 0), ("sgLineHandle", 1)]
        data, _ = _sketch_dict(sk)
        assert [r["type"] for r in data["relations"]] == [kind]


def test_sw_rebuild_inferred_relations():
    """Relations read off decoded geometry: perpendicular slanted lines, arcs tangent at a shared end,
    concentric circles with separate centre points; the sketch still solves without moving."""
    from openswx.exchange import sw_rebuild as rb
    from openswx.core.sketch import Sketch
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


def test_weld_member_matches_a_stored_body():
    """A weldment stores each member as a body of its own: a trial member that is one of those bodies exactly
    (volume and centroid) is accepted without relying on the sampled score, which undercounts thin-walled
    tubes; a member that is shifted or longer is not one of them."""
    from build123d import Box, Compound, Pos
    from openswx.exchange.sw_rebuild._base import _Oracle
    from openswx.exchange.sw_rebuild.features_weld import _stored_solid

    def tube(length):  # a 20 x 20 x 2 square tube along X, from x = 0
        return Pos(length / 2, 0, 0) * (Box(length, 20, 20) - Box(length, 16, 16))
    stored = Compound([tube(200).solid(), (Pos(0, 50, 0) * tube(100)).solid()])
    oracle = _Oracle(stored)
    assert _stored_solid(oracle, (Pos(0, 50, 0) * tube(100)).solid())
    assert _stored_solid(oracle, tube(200).solid())
    assert not _stored_solid(oracle, (Pos(0, 51, 0) * tube(100)).solid())
    assert not _stored_solid(oracle, (Pos(0, 50, 0) * tube(101)).solid())
