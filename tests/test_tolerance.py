"""Dimension tolerances and ISO 286 fits (openswx/tolerance.py, commands_tol.py)."""
import pytest

from openswx import session as S
from openswx import tolerance as T


def um(pair):
    return tuple(round(v * 1000, 3) for v in pair)


def test_iso286_table_values():
    # published ISO 286-2 limit deviations (micrometres)
    assert um(T.fit_deviations(25, "H7")) == (0, 21)
    assert um(T.fit_deviations(25, "g6")) == (-20, -7)
    assert um(T.fit_deviations(40, "f7")) == (-50, -25)
    assert um(T.fit_deviations(25, "k6")) == (2, 15)
    assert um(T.fit_deviations(25, "n6")) == (15, 28)
    assert um(T.fit_deviations(25, "p6")) == (22, 35)
    assert um(T.fit_deviations(25, "h9")) == (-52, 0)
    assert um(T.fit_deviations(25, "js6")) == (-6.5, 6.5)
    assert um(T.fit_deviations(10, "H8")) == (0, 22)
    assert um(T.fit_deviations(100, "e8")) == (-126, -72)
    assert um(T.fit_deviations(3, "H7")) == (0, 10)  # range ends are inclusive
    assert um(T.fit_deviations(400, "H11")) == (0, 360)
    # holes K, M, N, P take the delta rule
    assert um(T.fit_deviations(25, "K7")) == (-15, 6)
    assert um(T.fit_deviations(25, "M7")) == (-21, 0)
    assert um(T.fit_deviations(25, "N7")) == (-28, -7)
    assert um(T.fit_deviations(25, "P7")) == (-35, -14)
    assert um(T.fit_deviations(2, "N7")) == (-14, -4)  # no delta up to 3 mm
    assert um(T.fit_deviations(25, "F8")) == (20, 53)
    assert um(T.fit_deviations(25, "G7")) == (7, 28)
    for bad in ("H4", "Z7", "7H", "", "P9", "Hh7"):
        with pytest.raises(T.ToleranceError):
            T.fit_deviations(25, bad)
    with pytest.raises(T.ToleranceError):
        T.fit_deviations(600, "H7")


def test_fit_summary_kinds():
    assert T.fit_summary(25, "H7", "g6")["fit"] == "clearance"
    r = T.fit_summary(25, "H7", "k6")
    assert r["fit"] == "transition" and r["min_clearance"] == pytest.approx(-0.015) and r["max_clearance"] == pytest.approx(0.019)
    assert T.fit_summary(25, "H7", "p6")["fit"] == "interference"
    assert S.fit_calculator(25, "H7", "g6")["min_clearance"] == pytest.approx(0.007)
    with pytest.raises(S.CommandError):
        S.fit_calculator(25, "h7", "g6")


def test_text_and_limits():
    assert T.text("25", 25, {"type": "symmetric", "plus": 0.1}) == "25 ±0.1"
    assert T.text("25", 25, {"type": "bilateral", "plus": 0.1, "minus": 0.05}) == "25 +0.1/-0.05"
    assert T.text("25", 25, {"type": "bilateral", "plus": 0.2, "minus": 0}) == "25 +0.2/0"
    assert T.text("25", 25, {"type": "limit", "plus": 0.1, "minus": 0.05}) == "25.1 / 24.95"
    assert T.text("⌀25", 25, {"type": "fit", "fit": "H7"}) == "⌀25 H7 (+0.021/0)"
    assert T.text("25", 25, None) == "25" and T.text("25", 25, {"type": "none"}) == "25"
    assert T.limits(25, {"type": "fit", "fit": "g6"}) == pytest.approx((24.98, 24.993))
    with pytest.raises(T.ToleranceError):
        T.normalize({"type": "symmetric", "plus": 0})
    with pytest.raises(T.ToleranceError):
        T.normalize({"type": "wide"})


def plate():
    S.new_part("TolPlate")
    sk = S.create_sketch("Top")["sketch"]
    r = S.sketch_rectangle(sk, [0, 0], [60, 40])["result"]
    S.sketch_dimension(sk, "length", [r["entities"][0]], 60)
    c = S.sketch_circle(sk, [20, 20], 12.5)["result"]["entity"]
    S.sketch_dimension(sk, "diameter", [c], 25)
    S.extrude(sk, 8)
    return sk


def test_model_dimension_tolerances_are_saved_and_shown(tmp_path):
    sk = plate()
    dims = S.get_equations()["dimensions"]
    width = next(k for k, v in dims.items() if k.endswith("@" + sk) and abs(v - 60) < 1e-9)
    bore = next(k for k, v in dims.items() if k.endswith("@" + sk) and abs(v - 25) < 1e-9)
    r = S.set_dimension_tolerance(width, "bilateral", plus=0.1, minus=0.05)
    assert (r["min"], r["max"]) == (59.95, 60.1)
    r = S.set_dimension_tolerance(bore, "fit", fit="H7")
    assert (r["lower"], r["upper"], r["max"]) == (0.0, 0.021, 25.021)
    S.set_dimension_tolerance("D1@Boss-Extrude1", "symmetric", plus=0.2)
    got = {d["dimension"]: d for d in S.get_dimension_tolerances()["dimensions"]}
    assert set(got) == {width, bore, "D1@Boss-Extrude1"} and got[bore]["text"] == "25 H7 (+0.021/0)"
    assert len(S.get_dimension_tolerances(all_dimensions=True)["dimensions"]) >= 3

    with pytest.raises(S.CommandError, match="no dimension"):
        S.set_dimension_tolerance("D9@Nope", "symmetric", plus=0.1)
    with pytest.raises(S.CommandError):
        S.set_dimension_tolerance(width, "fit", fit="Q7")

    # drawings: Model Items show the tolerance with the value
    S.drawing_settings(model_items=True)
    svg = S.create_drawing()["svg"]
    assert "60 +0.1/-0.05" in svg and "⌀25 H7 (+0.021/0)" in svg

    # saved with the part; undo takes one back; "none" removes it
    p = S.save_part(str(tmp_path / "TolPlate"))["saved"]
    S.close_document("TolPlate")
    S.open_part(p)
    assert len(S.get_dimension_tolerances()["dimensions"]) == 3
    S.set_dimension_tolerance(width, "none")
    assert len(S.get_dimension_tolerances()["dimensions"]) == 2
    S.undo()
    assert len(S.get_dimension_tolerances()["dimensions"]) == 3


def test_drawing_dimension_tolerance_reaches_the_inspection_report():
    plate()
    S.drawing_new(sheet="A3", scale=1)
    from openswx import drawing_sheets as DS
    st = S.APP.doc.drawing
    view = next(v for v in st["sheets"][0]["views"] if v.get("orientation") == "top" or v.get("side") == "top")
    g = DS.view_geometry(S.APP.doc, st, view)
    t = DS.placement(st, view, g)
    x0, y0, x1, y1 = g["box"][:4]
    on_circle = None  # a click on the bore: scan the view until a pick lands on a circle
    for i in range(1, 40):
        for j in range(1, 40):
            q = list(t(x0 + (x1 - x0) * i / 40, y0 + (y1 - y0) * j / 40))
            if S.drawing_pick(view["id"], q)["circle"]:
                on_circle = q
                break
        if on_circle:
            break
    assert on_circle is not None
    S.drawing_add_dimension(view["id"], "diameter", on_circle)
    did = S.APP.doc.drawing["sheets"][0]["dims"][-1]["id"]
    S.drawing_edit_dimension(did, tolerance={"type": "fit", "fit": "H7"})
    svg = S.create_drawing()["svg"]
    assert "H7 (+0.021/0)" in svg
    items = S.inspection_characteristics()["characteristics"]
    bore = next(i for i in items if i["type"] == "diameter")
    assert (bore["lower"], bore["upper"], bore["general"]) == (0.0, 0.021, False)
    S.drawing_edit_dimension(did, tolerance={"type": "none"})
    bore = next(i for i in S.inspection_characteristics()["characteristics"] if i["type"] == "diameter")
    assert bore["general"] is True
    with pytest.raises(S.CommandError):
        S.drawing_edit_dimension(did, tolerance={"type": "fit", "fit": "X1"})
