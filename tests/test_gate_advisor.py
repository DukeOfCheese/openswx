"""Plastics gate advisor: the middle of a long strip beats its end."""
import pytest

from openswx import session as S


def test_centre_gate_needs_less_pressure_than_an_end_gate():
    S.new_part("Strip")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [120, 20])
    S.extrude(sk, 2)
    # candidates: the middle of the big faces, and two points we give - at one end and at the centre
    r = S.plastics_gate_advisor(candidates=2, points=[[1, 1, -10], [60, 1, -10]], material="PP")
    rows = {tuple(c["gate"]): c for c in r["candidates"]}
    end, centre = rows[(1.0, 1.0, -10.0)], rows[(60.0, 1.0, -10.0)]
    # filling 120 mm from one end takes about twice the flow length of filling from the middle
    assert end["max_injection_pressure_MPa"] > 1.5 * centre["max_injection_pressure_MPa"]
    assert centre["rank"] < end["rank"] and all(c["filled_pct"] == 100 for c in r["candidates"])
    best = rows[tuple(r["best"])]
    assert best["rank"] == 1 and abs(r["best"][0] - 60) < 15  # the best gate is near the middle of the length
    assert [c["rank"] for c in r["candidates"]] == sorted(c["rank"] for c in r["candidates"])
    with pytest.raises(S.CommandError):
        S.plastics_gate_advisor(candidates=0)
    with pytest.raises(S.CommandError):
        S.plastics_gate_advisor(material="Unobtainium")
