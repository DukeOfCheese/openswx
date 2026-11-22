"""Sketch blocks: make, insert (moved, turned, scaled), list, delete, saved with the part."""
import math

import pytest

from openswx import session as S


def test_make_and_insert_blocks(tmp_path):
    S.new_part("Blocks")
    sk = S.create_sketch("Top")["sketch"]
    r = S.sketch_rectangle(sk, [0, 0], [10, 4])["result"]  # a 10 x 4 tab ...
    c = S.sketch_circle(sk, [5, 2], 1)["result"]["entity"]  # ... with a hole
    made = S.sketch_make_block(sk, [*r["entities"], c], "Tab")
    assert made == {"block": "Tab", "entities": 5, "points": 5}  # the corners are shared: four, plus the centre
    assert S.list_sketch_blocks()["blocks"] == [{"name": "Tab", "entities": 5, "types": ["circle", "line"]}]
    S.extrude(sk, 2)
    v_tab = 10 * 4 * 2 - math.pi * 2
    assert S.get_mass_properties()["volume"] == pytest.approx(v_tab)

    # two more copies in a new sketch: one moved, one turned 90 degrees and twice the size
    s2 = S.create_sketch("Top")["sketch"]
    a = S.sketch_insert_block(s2, "Tab", position=[20, 0])["result"]
    assert len(a["entities"]) == 5 and len(a["points"]) == 5
    S.sketch_insert_block(s2, "Tab", position=[60, 0], angle=90, scale=2)
    pts = S.get_feature(s2)["params"]["sketch"]["points"]
    xs, ys = [p[0] for p in pts.values()], [p[1] for p in pts.values()]
    assert min(xs) == pytest.approx(20) and max(ys) == pytest.approx(20)  # the turned copy reaches y = 10 * 2
    assert min(x for x in xs if x > 40) == pytest.approx(60 - 8)  # ... and x = 60 - 4 * 2
    S.extrude(s2, 2)
    assert S.get_mass_properties()["volume"] == pytest.approx(v_tab * 2 + v_tab * 4)  # areas x1, x1 and x4

    with pytest.raises(S.CommandError, match="no block"):
        S.sketch_insert_block(s2, "Nope")
    with pytest.raises(S.CommandError):
        S.sketch_make_block(sk, ["Line999"], "Bad")
    with pytest.raises(S.CommandError):
        S.sketch_insert_block(s2, "Tab", scale=0)

    p = S.save_part(str(tmp_path / "Blocks"))["saved"]
    S.close_document("Blocks")
    S.open_part(p)
    assert [b["name"] for b in S.list_sketch_blocks()["blocks"]] == ["Tab"]
    assert S.delete_sketch_block("Tab")["blocks"] == []
    S.undo()
    assert len(S.list_sketch_blocks()["blocks"]) == 1


def test_block_with_arc_keeps_its_insertion_point():
    S.new_part("ArcBlock")
    sk = S.create_sketch("Top")["sketch"]
    arc = S.sketch_arc(sk, [10, 10], [15, 10], [5, 10])["result"]  # the upper half circle, r = 5
    line = S.sketch_line(sk, arc["points"]["end"], arc["points"]["start"])["result"]["entity"]
    S.sketch_make_block(sk, [arc["entity"], line], "Half", insertion_point=arc["points"]["center"])
    s2 = S.create_sketch("Top")["sketch"]
    S.sketch_insert_block(s2, "Half", position=[100, 0], angle=180)  # now the lower half, centred on (100, 0)
    S.extrude(s2, 1)
    mp = S.get_mass_properties()
    assert mp["volume"] == pytest.approx(math.pi * 25 / 2)
    assert mp["bounding_box"]["min"][0] == pytest.approx(95) and mp["bounding_box"]["max"][0] == pytest.approx(105)
