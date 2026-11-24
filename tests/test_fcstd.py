import zipfile

import pytest

from openswx.exchange import fcstd
from openswx import session as S

DOCUMENT = """<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="4" ProgramVersion="0.21" FileVersion="1">
 <Objects Count="3">
  <Object type="Part::Box" name="Box" id="1"/>
  <Object type="Part::Cylinder" name="Cylinder" id="2"/>
  <Object type="Part::Cut" name="Cut" id="3"/>
 </Objects>
 <ObjectData Count="3">
  <Object name="Box"><Properties Count="1">
   <Property name="Shape" type="Part::PropertyPartShape"><Part file="PartShape.brp"/></Property>
  </Properties></Object>
  <Object name="Cylinder"><Properties Count="1">
   <Property name="Shape" type="Part::PropertyPartShape"><Part file="PartShape1.brp"/></Property>
  </Properties></Object>
  <Object name="Cut"><Properties Count="3">
   <Property name="Base" type="App::PropertyLink"><Link value="Box"/></Property>
   <Property name="Tool" type="App::PropertyLink"><Link value="Cylinder"/></Property>
   <Property name="Shape" type="Part::PropertyPartShape"><Part file="PartShape2.brp"/></Property>
  </Properties></Object>
 </ObjectData>
</Document>"""


def gui(visible):
    rows = "".join(f'<ViewProvider name="{n}" expanded="0"><Properties Count="1"><Property name="Visibility" '
                   f'type="App::PropertyBool"><Bool value="{"true" if n in visible else "false"}"/></Property></Properties></ViewProvider>'
                   for n in ("Box", "Cylinder", "Cut"))
    return f"<?xml version='1.0' encoding='utf-8'?><Document SchemaVersion=\"1\"><ViewProviderData Count=\"3\">{rows}</ViewProviderData></Document>"


def make(tmp_path, name="model.FCStd", with_gui=True, visible=("Cut",)):
    """A box, a cylinder and the box with the cylinder cut out of it: three shapes, as FreeCAD keeps them."""
    import math
    breps, volumes = {}, {}
    for key, build in (("PartShape.brp", lambda: block(30, 20, 10)), ("PartShape1.brp", lambda: rod()),
                       ("PartShape2.brp", lambda: block(30, 20, 10, hole=True))):
        build()
        f = tmp_path / key
        S.export_file(str(f), "brep")
        breps[key] = f.read_bytes()
        volumes[key] = S.get_mass_properties()["volume"]
        S.close_document(S.APP.active)
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Document.xml", DOCUMENT)
        if with_gui:
            z.writestr("GuiDocument.xml", gui(visible))
        for k, v in breps.items():
            z.writestr(k, v)
    assert volumes["PartShape2.brp"] == pytest.approx(30 * 20 * 10 - math.pi * 16 * 10)
    return str(p), volumes


def block(x, y, z, hole=False):
    S.new_part("b")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [x, y])
    if hole:
        S.sketch_circle(sk, [10, 10], 4)
    S.extrude(sk, z)


def rod():
    S.new_part("r")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [10, 10], 4)
    S.extrude(sk, 10)


def test_the_visible_result_is_imported(tmp_path):
    path, vol = make(tmp_path)
    assert fcstd.shape_files(path) == [("Cut", "PartShape2.brp")]
    r = S.open_part(path)
    assert S.get_mass_properties()["volume"] == pytest.approx(vol["PartShape2.brp"])  # the cut box, not all three
    assert S.APP.active == "model" and r
    notes = S.get_model()["features"][0]
    assert notes["type"] == "import"
    # more than one visible body: all of them
    path2, _ = make(tmp_path, "two.FCStd", visible=("Box", "Cylinder"))
    S.open_part(path2)
    assert S.get_mass_properties()["volume"] == pytest.approx(vol["PartShape.brp"] + vol["PartShape1.brp"])


def test_without_gui_data_the_end_results_are_taken(tmp_path):
    path, vol = make(tmp_path, "headless.FCStd", with_gui=False)
    assert fcstd.shape_files(path) == [("Cut", "PartShape2.brp")]  # Box and Cylinder are inputs of Cut
    S.new_part("Host")
    S.import_file(path)
    assert S.get_mass_properties()["volume"] == pytest.approx(vol["PartShape2.brp"])


def test_bad_files_are_refused(tmp_path):
    bad = tmp_path / "bad.FCStd"
    bad.write_text("not a zip")
    with pytest.raises(S.CommandError, match="not a FreeCAD document"):
        S.open_part(str(bad))
    empty = tmp_path / "empty.FCStd"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("Document.xml", "<Document><Objects Count=\"0\"/><ObjectData Count=\"0\"/></Document>")
    with pytest.raises(S.CommandError, match="no shapes"):
        S.open_part(str(empty))
