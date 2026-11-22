"""Standard parts: envelope dimensions from the standards, volumes in closed form."""
import math

import pytest

from openswx import session as S


def test_ball_bearing():
    r = S.standard_part("ball_bearing", "6204")
    assert r["size"] == {"bore": 20, "outside_diameter": 47, "width": 14}
    assert r["volume"] == pytest.approx(math.pi * (23.5 ** 2 - 10 ** 2) * 14)
    bb = S.get_mass_properties()["bounding_box"]
    assert bb["max"][2] - bb["min"][2] == pytest.approx(14) and bb["max"][2] == pytest.approx(7)  # centred on its mid plane
    assert S.APP.doc.properties["PartNo"] == "6204"
    assert S.list_standard_parts()["ball_bearing"]["608"] == {"bore": 8, "outside_diameter": 22, "width": 7}
    with pytest.raises(S.CommandError, match="no bearing"):
        S.standard_part("ball_bearing", "9999")


def test_key_o_ring_and_extrusion():
    key = S.standard_part("parallel_key", shaft_diameter=25, length=40)  # over 22 to 30 mm: 8 x 7
    assert (key["size"]["width"], key["size"]["height"]) == (8, 7)
    assert key["volume"] == pytest.approx(((40 - 8) * 8 + math.pi * 16) * 7)
    assert S.standard_part("parallel_key", designation="5x5", length=20)["size"]["width"] == 5
    with pytest.raises(S.CommandError):
        S.standard_part("parallel_key", shaft_diameter=200, length=40)
    with pytest.raises(S.CommandError):
        S.standard_part("parallel_key", shaft_diameter=25)

    ring = S.standard_part("o_ring", inside_diameter=20, cross_section=2.5)
    assert ring["volume"] == pytest.approx(2 * math.pi ** 2 * (10 + 1.25) * 1.25 ** 2, rel=1e-6)  # torus: 2 pi2 R r2

    bar = S.standard_part("t_slot_extrusion", "2020", length=300)
    section = 20 * 20 - 4 * 6 * 6 - math.pi * 2.1 ** 2
    assert bar["volume"] == pytest.approx(section * 300, rel=1e-9)
    with pytest.raises(S.CommandError):
        S.standard_part("t_slot_extrusion", "1234", length=100)


def test_standard_parts_go_into_an_assembly_and_its_bom():
    S.standard_part("ball_bearing", "608")
    S.standard_part("t_slot_extrusion", "3030", length=200)
    S.new_assembly("Frame")
    S.insert_component("Bearing 608")
    S.insert_component("Bearing 608", position=[40, 0, 0])
    S.insert_component("Extrusion 3030 L200", position=[0, 50, 0])
    text = str(S.assembly_bom())
    assert "Deep groove ball bearing 608" in text and "T-slot extrusion 3030" in text
