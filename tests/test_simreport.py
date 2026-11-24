"""Result probes and the static study report."""
import pytest

from openswx import session as S

pytestmark = pytest.mark.slow


def cantilever():
    S.new_part("Beam")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [10, 10])
    S.extrude(sk, 100)
    S.set_material("Plain Carbon Steel")
    faces = S.get_topology()["faces"]
    root = next(f for f in faces if f["normal"][2] < -0.99)["id"]
    tip = next(f for f in faces if f["normal"][2] > 0.99)["id"]
    S.simulation_add_fixture(root)
    S.simulation_add_force(tip, force=[0, -100, 0])
    return S.simulation_run(element_size=2.5)


def test_probe_follows_beam_theory():
    r = cantilever()
    E, inertia = S.APP.doc.material["elastic_modulus"], 10 ** 4 / 12
    tip_theory = 100 * 100 ** 3 / (3 * E * inertia)
    p = S.simulation_probe([[5, 5, 100], [5, 5, 50], [5, 5, 0], [5, 9.9, 2]])["probes"]
    assert p[0]["displacement_size"] == pytest.approx(tip_theory, rel=0.05)
    assert p[0]["displacement"][1] < 0  # it bends the way the load points
    # deflection at mid span of an end-loaded cantilever: 5/16 of the tip's
    assert p[1]["displacement_size"] == pytest.approx(tip_theory * 5 / 16, rel=0.06)
    assert p[2]["displacement_size"] < 0.01 * tip_theory  # held at the root
    assert p[3]["von_mises"] > 3 * p[1]["von_mises"]  # the outer fibre at the root against the neutral axis (coarse mesh)
    assert p[3]["factor_of_safety"] == pytest.approx(r["yield_strength"] / p[3]["von_mises"], rel=1e-3)
    with pytest.raises(S.CommandError):
        S.simulation_probe([])
    S.set_custom_property("Rev", "B")  # the model changed: the results are stale
    with pytest.raises(S.CommandError, match="run the static study"):
        S.simulation_probe([[5, 5, 50]])


def test_report(tmp_path):
    r = cantilever()
    out = S.simulation_report(str(tmp_path / "beam"), probes=[[5, 5, 100]])
    page = (tmp_path / "beam.html").read_text(encoding="utf-8")
    assert out["saved"].endswith("beam.html") and out["factor_of_safety"] == pytest.approx(r["factor_of_safety"])
    assert out["force_balance_pct"] < 0.01  # the reactions balance the load
    for text in ("Beam - static study", "Plain Carbon Steel", "Maximum von Mises stress", "Fixture 1", "Load 1",
                 "force [0, -100, 0] N", "Probes", "does not yield", "data:image/png;base64,", "finite-cell"):
        assert text in page
    S.new_part("NoStudy")
    with pytest.raises(S.CommandError):
        S.simulation_report(str(tmp_path / "none"))


def test_convergence_study():
    cantilever()
    E, inertia = S.APP.doc.material["elastic_modulus"], 10 ** 4 / 12
    tip = 100 * 100 ** 3 / (3 * E * inertia)
    c = S.simulation_convergence(element_sizes=[5, 2.5, 5 / 3])
    assert [r["element_size"] for r in c["runs"]] == [5, 2.5, pytest.approx(5 / 3, abs=1e-4)]
    assert c["runs"][0]["elements"] < c["runs"][1]["elements"] < c["runs"][2]["elements"]
    assert c["displacement_converged"] and c["displacement_change_pct"] < 5
    assert c["runs"][-1]["max_displacement"] == pytest.approx(tip, rel=0.03)  # beam theory
    # the built-in end of a cantilever is a singular corner: the peak stress keeps climbing
    assert c["runs"][-1]["max_von_mises"] > c["runs"][0]["max_von_mises"]
    assert S.simulation_probe([[5, 5, 100]])["probes"][0]["displacement_size"] == pytest.approx(tip, rel=0.03)  # the finest run is current
    auto = S.simulation_convergence(refinements=2)
    assert len(auto["runs"]) == 2 and auto["runs"][1]["element_size"] == pytest.approx(auto["runs"][0]["element_size"] * 2 / 3, rel=1e-3)
    with pytest.raises(S.CommandError):
        S.simulation_convergence(element_sizes=[2.5])
    with pytest.raises(S.CommandError):
        S.simulation_convergence(refinements=9)
