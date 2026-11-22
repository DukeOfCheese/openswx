"""Scripts, compare, requirements, review, hand-off pack and the headless CLI."""
import json
import math
import runpy
import zipfile

import pytest

from openswx import session as S
from openswx.__main__ import main


def bracket(name="Bracket", depth=6.0, hole=3.0):
    S.new_part(name)
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 20])
    S.extrude(sk, depth)
    h = S.create_sketch("Top")["sketch"]
    S.sketch_circle(h, [10, 10], hole)
    S.cut_extrude(h, end_condition="ThroughAllBoth")
    return 40 * 20 * depth - math.pi * hole ** 2 * depth


def test_export_script_rebuilds_the_same_part(tmp_path):
    v = bracket()
    S.set_material("Plain Carbon Steel")
    S.undo()  # an undone command is not part of the script
    S.fillet(1.0, ["Edge0"])
    v_final = S.get_mass_properties()["volume"]
    assert v_final < v
    r = S.export_script(str(tmp_path / "bracket"))
    assert "S.set_material" not in r["script"] and "S.fillet(radius=1.0, edges=['Edge0'])" in r["script"]
    assert r["script"].splitlines()[4] == "S.new_part('Bracket')"
    S.save_part(str(tmp_path / "Bracket.oswprt"))
    S.close_document("Bracket")
    runpy.run_path(r["path"])  # the script, as `python bracket.py` would run it
    assert S.get_mass_properties()["volume"] == pytest.approx(v_final, rel=1e-12)
    # the journal travels with the file
    S.close_document(S.APP.active)
    S.open_part(str(tmp_path / "Bracket.oswprt"))
    assert S.export_script()["commands"] == r["commands"]
    # an imported document has no recorded history
    S.export_file(str(tmp_path / "b.step"))
    S.open_part(str(tmp_path / "b.step"))
    S.APP.active_doc.journal = None
    with pytest.raises(S.CommandError, match="no recorded history"):
        S.export_script()


def test_compare_documents(tmp_path):
    bracket("Rev")
    old = S.save_part(str(tmp_path / "RevA.oswprt"))["saved"]
    assert S.compare_documents(old)["identical"]
    S.edit_feature("Boss-Extrude1", {"depth": 8})
    S.fillet(1.0, ["Edge0"])
    d = S.compare_documents(old)
    assert not d["identical"]
    assert d["features"]["added"] == ["Fillet1"] and d["features"]["removed"] == []
    assert [c["parameter"] for c in d["features"]["changed"]["Boss-Extrude1"]] == ["params.depth"]
    assert d["features"]["changed"]["Boss-Extrude1"][0]["b"] == 8
    assert d["measures"]["volume"]["change"] > 0 and d["material_added_mm3"] > d["material_removed_mm3"] > 0
    with pytest.raises(S.CommandError):
        S.compare_documents("nothing-here.oswprt")


def test_requirements_and_review(tmp_path):
    bracket("Req")
    S.set_material("Plain Carbon Steel")
    mass = S.get_mass_properties()["mass"]
    assert S.add_requirement("Light", "mass", "<", mass + 1)["passes"]
    S.add_requirement("Fits", "longest_side", "<=", 40)
    S.add_requirement("Thick enough", "dimension", ">=", 5, target="D1@Boss-Extrude1")
    S.add_requirement("Rebuilds", "errors", "==", 0)
    assert S.check_requirements()["ok"]
    S.edit_feature("Boss-Extrude1", {"depth": 4})
    r = S.check_requirements()
    assert not r["ok"] and r["failed"] == ["Thick enough"]
    rev = S.review_model(min_wall=1.0)
    assert not rev["ok"] and any("Thick enough" in f["subject"] for f in rev["findings"] if f["severity"] == "error")
    assert any(f["severity"] == "warning" and "under-defined" in f["message"] for f in rev["findings"])
    S.add_requirement("Thick enough", "dimension", ">=", 3, target="D1@Boss-Extrude1")  # same name: replaced
    assert S.check_requirements()["ok"] and len(S.check_requirements()["requirements"]) == 4
    S.delete_requirement("Fits")
    assert len(S.check_requirements()["requirements"]) == 3
    with pytest.raises(S.CommandError):
        S.add_requirement("Bad", "dimension", "==", 1, target="D7@Nope")
    with pytest.raises(S.CommandError):
        S.add_requirement("Asm only", "components", "==", 1)
    # thin wall found by the review
    S.new_part("Thin")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [30, 30])
    S.extrude(sk, 0.4)
    rev = S.review_model(min_wall=1.0)
    assert rev["ok"] and any(f["subject"] == "wall thickness" for f in rev["findings"])
    # requirements are saved with the file
    S.activate_document("Req")
    p = S.save_part(str(tmp_path / "Req"))["saved"]
    S.close_document("Req")
    S.open_part(p)
    assert len(S.check_requirements()["requirements"]) == 3


def test_handoff_pack(tmp_path):
    bracket("Pack")
    S.add_requirement("Rebuilds", "errors", "==", 0)
    S.drawing_new()
    r = S.export_handoff_pack(str(tmp_path / "pack"), formats=["step", "stl"])
    names = {f["file"] for f in r["files"]}
    assert {"Pack.step", "Pack.stl", "Pack.pdf", "Pack.png", "Pack-properties.json", "Pack-requirements.json"} <= names
    assert r["requirements_ok"] is True
    with zipfile.ZipFile(r["zip"]) as z:
        assert set(z.namelist()) == names | {"manifest.json"}
    manifest = json.loads((tmp_path / "pack" / "manifest.json").read_text())
    assert manifest["document"] == "Pack" and not manifest["not_included"]


def test_cli(tmp_path, capsys):
    v = bracket("Cli")
    S.add_requirement("Volume", "volume", ">", 1000)
    src = S.save_part(str(tmp_path / "Cli"))["saved"]
    S.close_document("Cli")

    assert main(["convert", src, str(tmp_path / "out.step")]) == 0
    assert (tmp_path / "out.step").stat().st_size > 1000
    assert main(["convert", str(tmp_path / "out.step"), str(tmp_path / "back.oswprt")]) == 0
    assert main(["convert", src, str(tmp_path / "out.xyz")]) == 2
    assert main(["render", src, "-o", str(tmp_path / "pic.png"), "--size", "200x150"]) == 0
    assert (tmp_path / "pic.png").read_bytes()[:4] == b"\x89PNG"
    capsys.readouterr()
    assert main(["massprops", src]) == 0
    assert json.loads(capsys.readouterr().out)["volume"] == pytest.approx(v)
    assert main(["check", src, "--review"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["drawing", src, "-o", str(tmp_path / "d.pdf")]) == 0
    assert (tmp_path / "d.pdf").read_bytes()[:4] == b"%PDF"
    assert main(["script", src, "-o", str(tmp_path / "s.py")]) == 0
    assert "S.cut_extrude" in (tmp_path / "s.py").read_text()

    # a failing requirement makes `check` exit 1; a changed file makes `diff` exit 1
    S.open_part(src)
    S.add_requirement("Volume", "volume", ">", 1e9)
    S.edit_feature("Boss-Extrude1", {"depth": 9})
    bad = S.save_part(str(tmp_path / "Cli2"))["saved"]
    capsys.readouterr()
    assert main(["check", bad]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert main(["diff", src, src]) == 0
    capsys.readouterr()
    assert main(["diff", src, bad]) == 1
    assert "Boss-Extrude1" in json.loads(capsys.readouterr().out)["features"]["changed"]
    assert main(["massprops", str(tmp_path / "missing.oswprt")]) == 2


def test_tolerance_stack():
    S.new_part("Stack")
    sk = S.create_sketch("Top")["sketch"]
    r = S.sketch_rectangle(sk, [0, 0], [50, 20])["result"]
    S.sketch_dimension(sk, "length", [r["entities"][0]], 50)
    S.sketch_dimension(sk, "length", [r["entities"][1]], 20)
    S.extrude(sk, 12)
    dims = S.get_equations()["dimensions"]
    long_ = next(k for k, v in dims.items() if k.endswith("@" + sk) and abs(v - 50) < 1e-9)
    short = next(k for k, v in dims.items() if k.endswith("@" + sk) and abs(v - 20) < 1e-9)
    S.set_dimension_tolerance(long_, "symmetric", plus=0.1)
    S.set_dimension_tolerance(short, "bilateral", plus=0.05, minus=0.15)
    r = S.tolerance_stack([{"dimension": long_, "sign": 1}, {"dimension": short, "sign": -1}])
    assert r["nominal"] == 30
    # worst case: 50.1 - 19.85 and 49.9 - 20.05
    assert (r["worst_case"]["min"], r["worst_case"]["max"]) == (pytest.approx(29.85), pytest.approx(30.25))
    # statistical: mean shift +0.05 (the short side's band is off-centre), half bands 0.1 and 0.1
    assert r["statistical"]["min"] == pytest.approx(30.05 - math.sqrt(0.02))
    assert [c["share_pct"] for c in r["contributors"]] == [50.0, 50.0]
    # a dimension without a tolerance takes the general one (ISO 2768-m: +/- 0.2 for 6-30 mm)
    g = S.tolerance_stack([{"dimension": "D1@Boss-Extrude1"}], general_class="m")
    assert g["contributors"][0]["from"] == "ISO 2768-m" and g["worst_case"] == {"min": 11.8, "max": 12.2}
    with pytest.raises(S.CommandError):
        S.tolerance_stack([{"dimension": "D9@Nope"}])


def test_design_study_sweeps_and_restores():
    S.new_part("Study")
    S.set_global_variable("w", 20)
    sk = S.create_sketch("Top")["sketch"]
    r = S.sketch_rectangle(sk, [0, 0], [20, 10])["result"]
    S.sketch_dimension(sk, "length", [r["entities"][0]], 20)
    S.sketch_dimension(sk, "length", [r["entities"][1]], 10)
    S.extrude(sk, 5)
    dims = S.get_equations()["dimensions"]
    wdim = next(k for k, v in dims.items() if k.endswith("@" + sk) and abs(v - 20) < 1e-9)
    hdim = next(k for k, v in dims.items() if k.endswith("@" + sk) and abs(v - 10) < 1e-9)
    S.set_equation(wdim, '"w"')
    S.add_requirement("Stiff enough", "volume", ">=", 1500)
    v0, undo0 = S.get_mass_properties()["volume"], len(S.APP.doc._undo)
    st = S.design_study({"w": [20, 30, 40], hdim: [10, 12]}, measures=["volume", "longest_side"],
                        objective={"quantity": "volume", "goal": "min"})
    assert st["count"] == 6
    table = {(r["values"]["w"], r["values"][hdim]): r for r in st["runs"]}
    assert table[(30.0, 12.0)]["volume"] == pytest.approx(30 * 12 * 5) and table[(40.0, 10.0)]["longest_side"] == 40
    assert not table[(20.0, 10.0)]["feasible"] and table[(20.0, 10.0)]["requirements_failed"] == ["Stiff enough"]
    assert st["best"]["values"] == {"w": 30.0, hdim: 10.0}  # the lightest that still meets the requirement
    assert S.get_mass_properties()["volume"] == pytest.approx(v0) and len(S.APP.doc._undo) == undo0 + 1
    kept = S.design_study({"w": [30, 40]}, objective={"quantity": "volume", "goal": "max"}, mode="paired", keep_best=True)
    assert kept["kept_best"] and S.get_mass_properties()["volume"] == pytest.approx(40 * 10 * 5)
    S.undo()
    assert S.get_mass_properties()["volume"] == pytest.approx(v0)
    with pytest.raises(S.CommandError):
        S.design_study({"nope": [1]})
    with pytest.raises(S.CommandError, match="max_runs"):
        S.design_study({"w": list(range(20, 60))}, max_runs=10)
