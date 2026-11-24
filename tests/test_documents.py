"""Tests: documents (split out of the former test_core.py; shared helpers in core_helpers.py)."""
from core_helpers import *  # noqa: F401,F403


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


def test_save_without_a_path_on_a_never_saved_document(tmp_path):
    """save_part with no path on a document never saved to a file is an error that names the path argument
    (nothing is written); once saved with a path, later saves without one go to that file."""
    for kind in ("new_part", "new_assembly"):
        S.call(kind, {})
        name, files = S.APP.active, sorted(tmp_path.iterdir())
        with pytest.raises(S.CommandError, match=r"never been saved.*path argument"):
            S.call("save_part", {})
        assert S.APP.active_doc.path is None and sorted(tmp_path.iterdir()) == files
        saved = S.call("save_part", {"path": str(tmp_path / f"{kind}-doc")})["saved"]
        ext = ".oswasm" if kind == "new_assembly" else ".oswprt"
        assert saved == str(tmp_path / f"{kind}-doc{ext}") and Path(saved).is_file()
        assert S.APP.active == f"{kind}-doc" and S.APP.active_doc.path == saved and name not in S.APP.documents
        before = Path(saved).stat().st_mtime_ns
        os.utime(saved, ns=(before - 10**9, before - 10**9))
        assert S.call("save_part", {})["saved"] == saved  # (the same file, no path needed now)
        assert Path(saved).stat().st_mtime_ns > before - 10**9


def test_desktop_save_as_uses_the_native_dialog(tmp_path, monkeypatch):
    """The desktop app's Save As (what Save does for a document with no file yet): the native dialog's path is
    saved to; cancelling it saves nothing and is not an error; a folder that cannot be written is reported."""
    pytest.importorskip("webview", reason="pywebview (the desktop extra) is not installed")
    from openswx.desktop import DesktopApi
    api = DesktopApi()
    S.call("new_part", {"name": "Unsaved"})
    asked = []
    monkeypatch.setattr(api, "_dialog", lambda kind, name="", types=(): asked.append((kind, name)) or None)
    assert api.save_as() == {"cancelled": True}
    assert asked == [("save", "Unsaved.oswprt")] and S.APP.active_doc.path is None
    target = tmp_path / "Chosen.oswprt"
    monkeypatch.setattr(api, "_dialog", lambda kind, name="", types=(): str(target))
    r = api.save_as()
    assert r["ok"] and r["result"]["saved"] == str(target) and target.is_file()
    assert S.APP.active_doc.path == str(target)
    monkeypatch.setattr(api, "_dialog", lambda kind, name="", types=(): str(tmp_path / "missing" / "x.oswprt"))
    r = api.save_as()
    assert r["ok"] is False and r["error"]
