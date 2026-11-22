"""Derived configurations and display states of parts."""
import pytest

from openswx import session as S


def part():
    S.new_part("Cfg")
    sk = S.create_sketch("Top")["sketch"]
    r = S.sketch_rectangle(sk, [0, 0], [40, 20])["result"]
    S.sketch_dimension(sk, "length", [r["entities"][0]], 40)
    S.sketch_dimension(sk, "length", [r["entities"][1]], 20)
    S.extrude(sk, 10)
    h = S.create_sketch("Top")["sketch"]
    S.sketch_circle(h, [10, 10], 3)
    S.cut_extrude(h, end_condition="ThroughAllBoth")
    dims = S.get_equations()["dimensions"]
    return next(k for k, v in dims.items() if k.endswith("@" + sk) and abs(v - 40) < 1e-9)


def vol():
    return S.get_mass_properties()["volume"]


def test_derived_configuration_follows_its_parent(tmp_path):
    import math
    width = part()
    hole = math.pi * 9 * 10
    S.add_configuration("Long")
    S.set_configuration_value("Long", width, 60)
    assert vol() == pytest.approx(60 * 20 * 10 - hole)
    S.add_configuration("Long, no hole", derived_from="Long")
    assert S.get_model()["derived_configurations"] == {"Long, no hole": "Long"}
    assert vol() == pytest.approx(60 * 20 * 10 - hole)  # nothing of its own yet: the parent's
    S.suppress_feature("Cut-Extrude1", True, configuration="Long, no hole")
    assert vol() == pytest.approx(60 * 20 * 10)
    S.set_configuration_value("Long", width, 80)  # the parent changes: the derived one follows
    S.activate_configuration("Long, no hole")
    assert vol() == pytest.approx(80 * 20 * 10)
    S.activate_configuration("Long")
    assert vol() == pytest.approx(80 * 20 * 10 - hole)  # ... and the parent keeps its hole
    S.activate_configuration("Default")
    assert vol() == pytest.approx(40 * 20 * 10 - hole)
    with pytest.raises(S.CommandError, match="derived configurations"):
        S.delete_configuration("Long")
    with pytest.raises(S.CommandError):
        S.add_configuration("Orphan", derived_from="Nope")
    p = S.save_part(str(tmp_path / "Cfg"))["saved"]
    S.close_document("Cfg")
    S.open_part(p)
    S.activate_configuration("Long, no hole")
    assert vol() == pytest.approx(80 * 20 * 10)
    S.delete_configuration("Long, no hole")
    S.delete_configuration("Long")
    assert S.get_model()["configurations"] == ["Default"]


def test_part_display_states():
    part()
    S.set_appearance(color="#cc2222")
    m = S.save_display_state("Red")
    assert m["display_state"] == "Red" and m["display_states"] == ["Red"]
    S.set_appearance(color="#2244cc")
    S.save_display_state("Blue")
    assert S.activate_display_state("Red")["appearance"]["part"] == "#cc2222"
    assert S.activate_display_state("Blue")["appearance"]["part"] == "#2244cc"
    S.delete_display_state("Blue")
    assert S.get_model()["display_states"] == ["Red"] and S.get_model()["display_state"] is None
    with pytest.raises(S.CommandError):
        S.activate_display_state("Blue")
