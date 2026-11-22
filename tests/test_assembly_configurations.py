"""Assembly configurations (suppressed components, mate values) and display states."""
import pytest

from openswx import session as S


def rig():
    S.new_part("Cube")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [10, 10])
    S.extrude(sk, 10)
    S.new_assembly("Rig")
    a = S.insert_component("Cube")["component"]
    b = S.insert_component("Cube", position=[30, 0, 0])["component"]
    c = S.insert_component("Cube", position=[60, 0, 0])["component"]
    return a, b, c


def names():
    return [c["name"] for c in S.get_model()["components"]]


def test_suppressed_components_leave_the_configuration(tmp_path):
    a, b, c = rig()
    S.add_mate("parallel", f"{a}/Face0", f"{b}/Face0")
    S.add_mate("parallel", f"{a}/Face0", f"{c}/Face0")
    assert S.get_model()["volume_mm3"] == pytest.approx(3000)
    S.add_configuration("Short")  # a copy of Default, now active
    m = S.suppress_component(b)
    assert names() == [a, c] and m["suppressed_components"] == [b]
    assert len(m["mates"]) == 1 and m["volume_mm3"] == pytest.approx(2000)  # its mate went with it
    rows = next(v for v in S.assembly_bom().values() if isinstance(v, list))
    assert len(rows) == 1 and 2 in rows[0].values()  # two cubes left in the BOM
    with pytest.raises(S.CommandError):
        S.add_mate("parallel", f"{a}/Face0", f"{b}/Face0")  # not there in this configuration

    S.activate_configuration("Default")
    m = S.get_model()
    assert names() == [a, b, c] and len(m["mates"]) == 2 and m["volume_mm3"] == pytest.approx(3000)
    assert m["configurations"] == ["Default", "Short"] and m["active_config"] == "Default"

    # saved with the file, in the configuration that was active
    S.activate_configuration("Short")
    p = S.save_part(str(tmp_path / "Rig"))["saved"]
    S.close_document("Rig")
    S.open_part(p)
    assert names() == [a, c] and S.get_model()["active_config"] == "Short"
    S.activate_configuration("Default")
    assert names() == [a, b, c] and len(S.get_model()["mates"]) == 2

    # bring it back in Short; undo; delete the configuration
    S.suppress_component(b, False, configuration="Short")
    S.activate_configuration("Short")
    assert names() == [a, b, c]
    S.suppress_component(c)
    assert names() == [a, b]
    S.undo()
    assert names() == [a, b, c]
    S.delete_configuration("Short")
    assert S.get_model()["configurations"] == ["Default"] and S.get_model()["active_config"] == "Default"
    with pytest.raises(S.CommandError):
        S.delete_configuration("Default")
    with pytest.raises(S.CommandError):
        S.suppress_component("Nope-1")


def test_a_suppressed_seed_takes_its_pattern_with_it():
    a, b, c = rig()
    S.component_pattern([c], "linear", 3, spacing=20, direction=[0, 1, 0])
    assert len(names()) == 5
    S.add_configuration("NoRow")
    S.suppress_component(c)
    assert names() == [a, b]
    S.activate_configuration("Default")
    assert len(names()) == 5


def test_mate_values_per_configuration():
    a, b, _c = rig()
    S.fix_component(b, False) if "fix_component" in S.COMMANDS else None
    mate = S.add_mate("distance", f"{a}/Face0", f"{b}/Face0", value=30)
    name = S.get_model()["mates"][0]["name"]
    assert mate
    S.add_configuration("Wide")
    S.edit_mate(name, value=50)
    assert S.get_model()["mates"][0]["value"] == 50
    S.activate_configuration("Default")
    assert S.get_model()["mates"][0]["value"] == 30
    S.activate_configuration("Wide")
    assert S.get_model()["mates"][0]["value"] == 50
    S.activate_configuration("Default")
    S.edit_mate(name, value=35)  # the Default value; Wide keeps its own
    S.activate_configuration("Wide")
    assert S.get_model()["mates"][0]["value"] == 50
    S.activate_configuration("Default")
    assert S.get_model()["mates"][0]["value"] == 35


def test_display_states(tmp_path):
    a, b, c = rig()
    m = S.save_display_state("Insides", hidden=[b, c])
    assert m["display_state"] == "Insides" and m["hidden_components"] == [b, c]
    assert names() == [a, b, c] and m["volume_mm3"] == pytest.approx(3000)  # hidden is not suppressed
    S.save_display_state("Only A hidden", hidden=[a])
    assert S.activate_display_state("Insides")["hidden_components"] == [b, c]
    assert S.activate_display_state(None)["hidden_components"] == []
    with pytest.raises(S.CommandError):
        S.save_display_state("Bad", hidden=["Nope-1"])
    with pytest.raises(S.CommandError):
        S.activate_display_state("Nope")
    S.activate_display_state("Insides")
    p = S.save_part(str(tmp_path / "Rig"))["saved"]
    S.close_document("Rig")
    S.open_part(p)
    m = S.get_model()
    assert m["display_states"] == ["Insides", "Only A hidden"] and m["hidden_components"] == [b, c]
    S.delete_display_state("Insides")
    assert S.get_model()["display_state"] is None and S.get_model()["display_states"] == ["Only A hidden"]
