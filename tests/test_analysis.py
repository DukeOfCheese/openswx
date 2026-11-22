"""Tests: analysis (split out of the former test_core.py; shared helpers in core_helpers.py)."""
from core_helpers import *  # noqa: F401,F403


def test_failed_feature_is_rejected():
    block()
    with pytest.raises(S.CommandError):
        S.fillet(500, edges=["Edge0"])
    assert [f["name"] for f in S.get_model()["features"]] == ["Sketch1", "Boss-Extrude1"]


def test_evaluate_tools():
    block()  # 100 x 60 x 20, extruded +Y from the Top plane
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    da = S.draft_analysis("Y", angle=1)
    c = da["counts"]
    assert c["positive"] == 1 and c["negative"] == 1 and c["requires_draft"] == 5  # top, bottom, 4 sides + hole wall
    cv = S.curvature_analysis()
    hole_face = [f["id"] for f in S.get_topology()["faces"] if f["type"] == "cylinder"][0]
    assert max(cv["faces"][hole_face]) == pytest.approx(1 / 5, rel=1e-4)  # 1 / radius
    assert cv["max"] == pytest.approx(0.2, rel=1e-4)
    sec = S.section_view("Front")  # z = 0 through the hole
    assert sec["area"] == pytest.approx(100 * 20 - 10 * 20)
    assert S.section_properties("Right", offset=30)["area"] == pytest.approx(60 * 20)
    chk = S.check_geometry()
    assert chk["valid"] and chk["solids"] == 1 and chk["min_radius"] == pytest.approx(5)
    S.new_part()
    s1 = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(s1, [0, 0], [10, 10])
    S.extrude(s1, depth=10)
    s2 = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(s2, [5, 5], [15, 15])
    S.extrude(s2, depth=10, merge=False)
    bi = S.body_interference()
    assert bi["bodies"] == 2 and bi["interferences"][0]["volume"] == pytest.approx(5 * 5 * 10)


def test_linear_static_simulation():
    S.new_part("Beam")
    sk = S.create_sketch("Right")["sketch"]  # cross-section in the YZ plane, extruded along +X
    S.sketch_rectangle(sk, [-5, 0], [5, 10])
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")  # E = 200 GPa, nu = 0.29, yield 351.6 MPa
    faces = S.get_topology()["faces"]
    root = [f["id"] for f in faces if f["normal"][0] < -0.99][0]
    tip = [f["id"] for f in faces if f["normal"][0] > 0.99][0]
    with pytest.raises(S.CommandError):
        S.simulation_run()  # no fixtures yet
    S.simulation_add_fixture(root)
    S.simulation_add_force(tip, force=[0, -100, 0])
    r = S.simulation_run(element_size=2.5)
    # Euler-Bernoulli + Timoshenko shear: F L^3 / (3 E I) + F L / (k G A)
    E, nu, I = 200e3, 0.29, 10 * 10 ** 3 / 12
    delta = 100 * 100 ** 3 / (3 * E * I) + 100 * 100 / (5 / 6 * E / (2 * (1 + nu)) * 100)
    assert r["max_displacement"] == pytest.approx(delta, rel=0.02)
    assert r["reaction"][1] == pytest.approx(100, rel=1e-6)
    assert 35 < r["max_von_mises"] < 70  # 60 MPa at the root fibre; element centres sit inside it
    assert r["factor_of_safety"] == pytest.approx(351.6 / r["max_von_mises"])
    res = S.simulation_results()
    assert set(res["stress"]) == {f["id"] for f in faces} and max(max(v) for v in res["stress"].values()) > 30
    # pressure on the top face: total force p * A
    S.simulation_clear(fixtures=False)
    top = [f["id"] for f in faces if f["normal"][1] > 0.99][0]
    S.simulation_add_pressure(top, 0.1)  # 0.1 MPa over 100 x 10 = 100 N downward
    r2 = S.simulation_run(element_size=2.5)
    assert r2["reaction"][1] == pytest.approx(100, rel=1e-6)
    assert r2["max_displacement"] < r["max_displacement"]  # distributed load bends less than a tip load


def test_thermal_study():
    S.new_part("Bar")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(sk, [-5, 0], [5, 10])
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")  # k = 47 W/(m K)
    faces = S.get_topology()["faces"]
    a = [f["id"] for f in faces if f["normal"][0] < -0.99][0]
    b = [f["id"] for f in faces if f["normal"][0] > 0.99][0]
    S.thermal_add_temperature(a, 20)
    S.thermal_add_heat_power(b, 10)
    r = S.thermal_run(element_size=2.5)
    assert r["max_temperature"] == pytest.approx(20 + 10 * 100 / (0.047 * 100), rel=1e-6)  # 1D conduction
    assert r["heat_through_fixed_faces"] == pytest.approx(10, rel=1e-6)
    res = S.thermal_results()
    assert max(max(v) for v in res["temperature"].values()) == pytest.approx(r["max_temperature"], rel=1e-3)
    S.thermal_clear()
    S.thermal_add_temperature(a, 100)
    S.thermal_add_convection(b, 1000, ambient=20)
    r = S.thermal_run(element_size=2.5)
    R1, R2 = 100 / (0.047 * 100), 1 / (1000e-6 * 100)
    assert r["min_temperature"] == pytest.approx(100 - 80 / (R1 + R2) * R1, rel=1e-6)  # series resistances


def test_frequency_study():
    S.new_part("Beam")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(sk, [-5, 0], [5, 10])
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")  # E 200 GPa, rho 7900
    root = [f["id"] for f in S.get_topology()["faces"] if f["normal"][0] < -0.99][0]
    S.simulation_add_fixture(root)
    r = S.frequency_run(modes=3, element_size=2.5)
    EI, rA = 200e3 * 10 ** 4 / 12, 7900e-12 * 100
    f1 = 1.875 ** 2 / (2 * PI) * math.sqrt(EI / (rA * 100 ** 4))
    assert r["frequencies_hz"][0] == pytest.approx(f1, rel=0.01) and r["frequencies_hz"][1] == pytest.approx(f1, rel=0.01)
    assert r["mass_kg"] == pytest.approx(0.079, rel=1e-6)
    sh = S.frequency_mode_shape(1)
    assert max(abs(c) for v in sh["displacement"].values() for d in v for c in d) == pytest.approx(1, abs=0.05)


def test_buckling_study():
    S.new_part("Column")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(sk, [-5, 0], [5, 10])
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    S.simulation_add_fixture([f["id"] for f in faces if f["normal"][0] < -0.99][0])
    S.simulation_add_force([f["id"] for f in faces if f["normal"][0] > 0.99][0], force=[-1000, 0, 0])
    r = S.buckling_run(modes=2, element_size=2.5)
    euler = PI ** 2 * 200e3 * 10 ** 4 / 12 / (4 * 100 ** 2) / 1000  # fixed-free column, per 1 kN
    assert r["load_factors"][0] == pytest.approx(euler, rel=0.01)
    assert S.buckling_mode_shape(1)["load_factor"] == r["load_factors"][0]


def test_spring_and_bolt_connectors():
    S.new_part("Pair")
    s1 = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(s1, [-5, 0], [5, 10])
    S.extrude(s1, depth=10)
    S.reference_plane("Right", offset=20)
    s2 = S.create_sketch("Plane1")["sketch"]
    S.sketch_rectangle(s2, [-5, 0], [5, 10])
    S.extrude(s2, depth=10, merge=False)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    xs = lambda x, sgn: [f["id"] for f in faces if f["normal"][0] * sgn > 0.99 and abs(f["point"][0] - x) < 1e-6][0]  # noqa: E731
    S.simulation_add_fixture(xs(0, -1))
    S.simulation_add_force(xs(30, 1), force=[100, 0, 0])
    S.simulation_add_spring(xs(10, 1), xs(20, -1), normal_stiffness=1000)
    r = S.simulation_run(element_size=1.0)
    assert r["max_displacement"] == pytest.approx(100 / 1000 + 2 * 100 * 10 / (200e3 * 100), rel=0.01)
    # bolt: two plates 10 mm apart joined through coaxial 8 mm holes, pulled apart
    S.new_part("Plates")
    for off, merge in ((0, True), (15, False)):
        plane = S.reference_plane("Top", offset=off)["feature"] if off else "Top"
        sk = S.create_sketch(plane)["sketch"]
        S.sketch_rectangle(sk, [-20, -20], [20, 20])
        S.sketch_circle(sk, [0, 0], 4)
        S.extrude(sk, depth=5, merge=merge)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    holes = sorted((f for f in faces if f["type"] == "cylinder"), key=lambda f: f["point"][1])
    bottom = [f["id"] for f in faces if f["normal"][1] < -0.99 and abs(f["point"][1]) < 1e-6][0]
    top = [f["id"] for f in faces if f["normal"][1] > 0.99 and abs(f["point"][1] - 20) < 1e-6][0]
    S.simulation_add_fixture(bottom)
    S.simulation_add_force(top, force=[0, 1000, 0])
    S.simulation_add_bolt(holes[0]["id"], holes[-1]["id"])
    r = S.simulation_run(element_size=1.5)
    kn = 200e3 * PI * 16 / 15  # E A / L, grip between hole centres
    assert 1000 / kn < r["max_displacement"] < 50 * 1000 / kn  # bolt stretch plus plate bending


def test_no_penetration_contact():
    S.new_part("Gap")
    s1 = S.create_sketch("Right")["sketch"]
    S.sketch_rectangle(s1, [-5, 0], [5, 10])
    S.extrude(s1, depth=10)
    S.reference_plane("Right", offset=10.05)
    s2 = S.create_sketch("Plane1")["sketch"]
    S.sketch_rectangle(s2, [-5, 0], [5, 10])
    S.extrude(s2, depth=10, merge=False)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    xs = lambda x, sgn: [f["id"] for f in faces if f["normal"][0] * sgn > 0.99 and abs(f["point"][0] - x) < 1e-6][0]  # noqa: E731
    S.simulation_add_fixture(xs(0, -1))
    S.simulation_add_force(xs(20.05, 1), force=[-1000, 0, 0])  # push the free block onto the fixed one
    S.simulation_add_contact(xs(10, 1), xs(10.05, -1))
    r = S.simulation_run(element_size=1.0)
    assert r["contact_pairs_closed"] > 0 and r["reaction"][0] == pytest.approx(1000, rel=1e-6)
    ux = max(abs(u[0]) for u in S.APP.sim_result[1]["_grid"]["U"])
    assert ux == pytest.approx(0.05 + 2 * 1000 * 10 / (200e3 * 100), rel=0.01)  # close the gap, then compress both


def test_simulation_curved_boundary_accuracy():
    # round cantilever: the finite-cell method integrates cut voxels over their inside part only
    S.new_part("Rod")
    sk = S.create_sketch("Right")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, depth=100)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    root = [f["id"] for f in faces if f["normal"][0] < -0.99][0]
    tip = [f["id"] for f in faces if f["normal"][0] > 0.99][0]
    S.simulation_add_fixture(root)
    S.simulation_add_force(tip, force=[0, -100, 0])
    r = S.simulation_run(element_size=2.0)
    E, nu = 200e3, 0.29
    I, A = PI * 10 ** 4 / 64, PI * 25
    delta = 100 * 100 ** 3 / (3 * E * I) + 100 * 100 / (0.9 * E / (2 * (1 + nu)) * A)
    assert r["max_displacement"] == pytest.approx(delta, rel=0.03)
    assert r["mesh_volume"] == pytest.approx(PI * 25 * 100, rel=0.02)


def test_transient_thermal_lumped_cooling():
    """Small copper cube cooling by convection (Biot << 1) follows the lumped-capacitance curve
    T = Tamb + (T0 - Tamb) exp(-h A t / (rho c V))."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-5, -5], [5, 5])
    S.extrude(sk, depth=10)
    S.set_material("Copper")
    for f in S.get_topology()["faces"]:
        S.thermal_add_convection(f["id"], 50.0, 20.0)
    r = S.thermal_run(transient=True, duration=150, steps=50, initial_temperature=100.0, element_size=2.0)
    tau = 8900e-9 * 385.0 * 1000.0 / (50e-6 * 600.0)
    assert r["history"][-1]["mean"] - 20 == pytest.approx(80 * math.exp(-150 / tau), rel=2e-3)
    assert r["history"][0]["mean"] == pytest.approx(100.0)
    assert len(S.thermal_results()["temperature"]) == 6


def test_thermal_heat_per_boundary():
    """Two fixed temperatures: the heat in through one equals the heat out through the other, k A dT / L."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [-50, -5], [50, 5])
    S.extrude(sk, depth=10)
    S.set_material("AISI 1020")
    faces = S.get_topology()["faces"]
    xmin = min(faces, key=lambda f: f["point"][0])["id"]
    xmax = max(faces, key=lambda f: f["point"][0])["id"]
    S.thermal_add_temperature(xmin, 100.0)
    S.thermal_add_temperature(xmax, 20.0)
    q_in, q_out = S.thermal_run()["heat_in_per_temperature_boundary"]
    assert q_in == pytest.approx(-q_out, rel=1e-9)
    assert q_in == pytest.approx(47.0 / 1000 * 100 * 80 / 100, rel=0.03)


def test_feature_reorder_folders_and_entity_drag():
    """FeatureManager drag-reorder (refused when it breaks a reference), folders, and dragging sketch
    entities (a circle grabbed on its edge resizes, a line moves)."""
    S.new_part()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 20])
    S.extrude(sk, depth=10)
    S.create_sketch("Front")
    names = [f["name"] for f in S.get_model()["features"]]
    with pytest.raises(Exception):  # the extrude cannot come before its sketch
        S.reorder_feature(sk, "Boss-Extrude1")
    assert [f["name"] for f in S.get_model()["features"]] == names
    S.reorder_feature("Sketch2", None)  # an independent sketch can go to the top
    assert S.get_model()["features"][0]["name"] == "Sketch2"
    S.add_folder("Folder1", ["Boss-Extrude1"])
    assert S.get_model()["folders"] == [{"name": "Folder1", "features": ["Boss-Extrude1"]}]
    S.rename_feature("Boss-Extrude1", "Base")
    assert S.get_model()["folders"][0]["features"] == ["Base"]
    S.remove_folder("Folder1")
    assert S.get_model()["folders"] == []
    sk3 = S.create_sketch("Right")["sketch"]
    c = S.sketch_circle(sk3, [0, 0], 5)["result"]["entity"]
    line = S.sketch_line(sk3, [20, 0], [30, 0])["result"]
    S.sketch_drag_entity(sk3, c, [5, 0], [8, 0])
    sketch = S.get_feature(sk3)["params"]["sketch"]
    assert sketch["entities"][c]["r"] == pytest.approx(8)
    S.sketch_drag_entity(sk3, line["entity"], [25, 0], [25, 4])
    pts = S.get_feature(sk3)["params"]["sketch"]["points"]
    assert pts[line["points"][0]] == pytest.approx([20, 4]) and pts[line["points"][1]] == pytest.approx([30, 4])


def test_face_features():
    """Each face is credited to the feature that made it (FeatureManager / model cross-highlighting)."""
    block()  # Boss-Extrude1: 6 faces
    S.hole(top_face(), [[0, 0]], diameter=10, end_condition="ThroughAll")
    hole = S.get_model()["features"][-1]["name"]
    owners = S.face_features()["faces"]
    topo = S.get_topology()["faces"]
    assert len(owners) == len(topo)
    cyl = next(f["id"] for f in topo if f["type"] == "cylinder")
    assert owners[cyl] == hole
    assert sum(1 for v in owners.values() if v == "Boss-Extrude1") == 6


def test_library_features(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    block()
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.cut_extrude(sk, end_condition="ThroughAll", reverse=True)
    cut = S.get_model()["features"][-1]["name"]
    with_hole = vol()
    S.save_library_feature("Hole10", [cut])
    assert S.list_design_library()["features"][0]["features"] == [sk, cut]
    block()
    S.insert_library_feature("Hole10", "Top", [20, 0])
    assert vol() == pytest.approx(with_hole)
    S.add_part_to_design_library()
    assert S.list_design_library()["parts"]


def test_nonlinear_dynamic_drop_and_fatigue_studies():
    root, tip = _cantilever()
    S.simulation_add_force(tip, force=[0, -100, 0])
    lin = S.simulation_run(element_size=2.5)
    nl = S.nonlinear_run(steps=2, element_size=2.5)  # elastic: the same as the linear study
    assert nl["plastic_elements"] == 0 and nl["max_displacement"] == pytest.approx(lin["max_displacement"], rel=1e-3)
    assert set(S.simulation_results()["stress"])
    # fatigue: 100 N gives ~50 MPa, under the endurance limit 0.5 Su; ten times that does not
    f = S.fatigue_run(cycles=1e6)
    assert f["infinite_life"] and f["endurance_limit"] == pytest.approx(0.5 * 420.5)
    f = S.fatigue_run(cycles=1e6, scale=10)
    assert not f["infinite_life"] and f["damage"] > 0 and f["elements_failing"] > 0
    assert min(min(v) for v in S.fatigue_results()["log_life"].values()) < 6
    # dynamics: a suddenly applied load overshoots to about twice the static deflection; resonance at f1
    st = S.step_response_run(duration=0.01, points=400, damping=0.02, modes=4, element_size=2.5)
    assert 1.85 < st["dynamic_amplification"] < 2.0
    f1 = st["frequencies_hz"][0]
    h = S.harmonic_run(f1 * 0.7, f1 * 1.3, points=121, modes=4, element_size=2.5)
    assert h["peak_frequency"] == pytest.approx(f1, rel=0.01)
    # yielding: plastic elements, and more deflection than the linear extrapolation
    S.simulation_clear(fixtures=False)
    S.simulation_add_force(tip, force=[0, -2000, 0])
    nl = S.nonlinear_run(steps=5, element_size=2.5)
    assert nl["plastic_elements"] > 0 and nl["max_displacement"] > 20 * lin["max_displacement"] * 1.05
    assert nl["max_von_mises"] < 20 * lin["max_von_mises"]  # beyond the plastic moment: hardening carries it
    # drop test: a bar landing on its end, G^2 = 6 E h / (rho g L^2)
    d = S.drop_test_run(100, [root], direction=[-1, 0, 0], element_size=2.5)
    assert d["peak_acceleration_g"] == pytest.approx(math.sqrt(6 * 200e3 * 100 / (7.9e-9 * 9810 * 100 ** 2)), rel=0.05)
    assert d["impact_velocity_m_s"] == pytest.approx(math.sqrt(2 * 9.81 * 0.1), rel=1e-6)


def test_flow_costing_sustainability():
    block()
    with pytest.raises(S.CommandError):
        S.costing("sheet_metal")  # not a sheet metal part
    S.set_material("AISI 1020")
    r = S.flow_simulation(speed=10, direction=[1, 0, 0], cells_across=16)
    assert r["converged"] and r["min_cp"] < -0.2 and r["max_cp"] > 0.5 and r["skin_friction_drag_N"] > 0
    assert abs(r["pressure_force_N"][0]) < 0.05 * r["dynamic_pressure_Pa"] * 60 * 20 * 1e-6  # ideal flow: no drag
    assert set(S.flow_results()["cp"])
    one, many = S.costing(quantity=1), S.costing(quantity=100)
    assert many["cost_each"] < one["cost_each"] and one["removed_cm3"] == pytest.approx(104 * 64 * 24 / 1000 - 120)
    for m in ("additive", "casting"):
        assert S.costing(m)["cost_each"] > 0
    s = S.sustainability_report(region="Europe")
    assert s["mass_kg"] == pytest.approx(0.948) and s["total"]["co2"] == pytest.approx(
        sum(p["co2"] for p in s["phases"].values()), abs=1e-3)
    assert S.sustainability_report(recycled_fraction=1.0)["phases"]["material"]["co2"] < s["phases"]["material"]["co2"]


def test_pdm_vault_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path / "home"))
    block()
    S.APP.doc.properties["Project"] = "Gearbox"
    p = tmp_path / "work" / "Plate.oswprt"
    p.parent.mkdir()
    S.save_part(str(p))
    r = S.pdm_check_in(comment="first")
    assert r["version"] == 1 and S.pdm_list()["files"][0]["state"] == "Work in Progress"
    with pytest.raises(S.CommandError):
        S.pdm_check_in(str(p))  # not checked out
    co = S.pdm_check_out("Plate.oswprt")
    assert Path(co["path"]).is_file() and S.APP.doc.path and Path(S.APP.doc.path).resolve() == Path(co["path"])
    sk = S.create_sketch(top_face())["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.cut_extrude(sk, end_condition="ThroughAll")
    S.save_part()
    assert S.pdm_check_in(comment="hole")["version"] == 2
    with pytest.raises(S.CommandError):
        S.pdm_change_state("Plate.oswprt", "Released")  # must go through approval
    S.pdm_change_state("Plate.oswprt", "Pending Approval")
    assert S.pdm_change_state("Plate.oswprt", "Released")["revision"] == "A"
    with pytest.raises(S.CommandError):
        S.pdm_check_out("Plate.oswprt")  # released: locked
    h = S.pdm_history("Plate.oswprt")
    assert [v["comment"] for v in h["versions"]] == ["first", "hole"] and h["versions"][-1]["revision"] == "A"
    old = S.pdm_get_version("Plate.oswprt", 1, str(tmp_path / "old"))["path"]
    import json
    nfeat = lambda f: len(json.loads(Path(f).read_text(encoding="utf-8"))["features"])  # noqa: E731
    assert nfeat(old) + 2 == nfeat(co["path"])  # version 1: before the sketch and cut
    assert [f["name"] for f in S.pdm_search("gearbox")["files"]] == ["Plate.oswprt"]
    assert S.pdm_search("nothing-like-this")["files"] == []
    S.pdm_change_state("Plate.oswprt", "Work in Progress")
    S.pdm_check_out("Plate.oswprt", open=False)
    S.pdm_undo_check_out("Plate.oswprt")
    assert S.pdm_history("Plate.oswprt")["checked_out"] is None


def test_holes_do_not_give_spurious_modes_or_runaway_drop_nodes():
    # cut cells around holes with only a few inside points are under-integrated: they must not carry mass
    from build123d import Box, Cylinder, Pos, Rot
    from openswx.fea import solve_modal
    from openswx.fea_more import solve_drop
    from openswx.materials import lookup
    mat = lookup("AISI 1020")
    solid = Pos(0, 10, 0) * Box(100, 20, 60)
    holed = solid - Pos(30, 10, 0) * Rot(90, 0, 0) * Cylinder(4, 40) - Pos(-30, 10, 0) * Rot(90, 0, 0) * Cylinder(4, 40)
    face = lambda b, ax, s: [f for f in b.faces() if s * list(f.normal_at(f.center()))[ax] > 0.99][0]  # noqa: E731
    f = solve_modal(holed, mat, [face(holed, 0, -1)], 3, None)["frequencies_hz"]
    beam = 1.875 ** 2 / (2 * PI) * math.sqrt(200e3 * 20 ** 2 / 12 / (7.9e-9 * 100 ** 4))  # Euler-Bernoulli, no holes
    assert 0.85 * beam < f[0] < 1.02 * beam and f[1] > 1.5 * f[0]
    plain = solve_drop(solid, mat, [face(solid, 1, -1)], 1000, (0, -1, 0))
    r = solve_drop(holed, mat, [face(holed, 1, -1)], 1000, (0, -1, 0))
    assert r["peak_acceleration_g"] == pytest.approx(plain["peak_acceleration_g"], rel=0.05)
    assert r["max_displacement"] < 1.5 * plain["max_displacement"] and r["max_von_mises"] < 3 * plain["max_von_mises"]


def test_cam_toolpaths_gcode_and_stock_check(tmp_path):
    _pocket_block()
    r = S.cam_generate(setup="-Z", tool_diameter=6, leave=0.3)
    assert [h["diameter"] for h in r["holes"]] == [6.0, 6.0] and all(h["through"] for h in r["holes"])
    assert [o["kind"] for o in r["operations"]] == ["rough", "finish", "drill"]
    v = r["verification"]
    assert v["gouge_mm"] <= r["precision_mm"]  # the tool never enters the part
    assert v["area_over_0.1mm_pct"] < 1.0  # only the pocket's inside corners keep material (the tool radius)
    assert -9.0 in r["levels"]  # the pocket floor is a cutting level (stock top 1 mm above the part)
    g = S.cam_gcode(post="fanuc", path=str(tmp_path / "block"))
    text = Path(g["path"]).read_text()
    assert g["path"].endswith(".nc") and "T1 M6" in text and "T2 M6" in text and "G43" in text
    assert "G83 X7 Y21 Z-23.8 R0 Q6" in text and "X55 Y21" in text  # peck drilling through, both holes
    grbl = S.cam_gcode(post="grbl")["gcode"]
    assert "G83" not in grbl and "M0" in grbl and "M6" not in grbl
    tp = S.cam_toolpaths()
    assert len(tp["paths"]) == 3 and tp["paths"][2]["feed"][0][:3] == pytest.approx([6, 20, -1])  # R plane, part coords
    with pytest.raises(Exception, match="stepover"):
        S.cam_generate(tool_diameter=6, stepover=6)


def test_plastics_fill_matches_slit_flow_and_finds_weld_lines():
    from build123d import Align, Box, Cylinder, Pos
    from openswx.plastics import fill, material
    plate = Box(100, 20, 2, align=(Align.MIN, Align.MIN, Align.MIN))
    gate = [f for f in plate.faces() if abs(f.center().X) < 1e-6][0]
    # a line gate along the end: 1-D slit flow, dp = 12 eta Q L / (W t^3) (Newtonian) or the power-law form
    r = fill(plate, [gate], material("ABS", newtonian=True), fill_time=1.0)
    assert r["nominal_wall_mm"] == pytest.approx(2.0, abs=0.02)
    assert r["max_injection_pressure_MPa"] == pytest.approx(12 * 200 * 4e-6 * 0.1 / (0.02 * 0.002 ** 3) / 1e6, rel=0.02)
    assert r["fill_time_s"] == pytest.approx(1.0, abs=0.03) and r["weld_line_cells"] == 0 and not r["air_traps"]
    m = material("ABS")
    n = m["n"]
    expected = m["consistency"] / 0.001 * ((2 * n + 1) / n * 0.1 / 0.001) ** n * 0.1 / 1e6
    assert fill(plate, [gate], m, fill_time=1.0)["max_injection_pressure_MPa"] == pytest.approx(expected, rel=0.02)
    holed = plate - Pos(40, 10, 1) * Cylinder(4, 4)
    w = fill(holed, [gate], m, fill_time=1.0)
    assert w["weld_line_cells"] > 0 and all(p[0] > 44 for p in w["weld_lines"])  # behind the hole
    assert not fill(plate, [(0, 10, 1)], m, fill_time=1.0)["air_traps"]  # ends at the outline: the parting line vents it
    # race-tracking: the melt runs round a thick rim and closes in the thin panel, trapping air inside it
    frame = Box(60, 60, 3, align=(Align.MIN,) * 3) - Pos(6, 6, 1) * Box(48, 48, 3, align=(Align.MIN,) * 3)
    traps = fill(frame, [(0, 30, 1.5)], m, fill_time=1.0)["air_traps"]
    assert len(traps) == 1 and 40 < traps[0][0] < 54 and traps[0][2] < 1
    short = fill(plate, [gate], material("PC"), fill_time=0.05, max_pressure_MPa=50)
    assert short["short_shot"] and short["filled_pct"] < 100 and "short shot" in short["warnings"][0]
    S.new_part("Lid")
    sk = S.create_sketch("Front")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [60, 40])
    S.extrude(sk, depth=2)
    p = S.plastics_fill(gates=[[0, 20, 1]], material="PP")
    assert p["filled_pct"] == 100 and p["clamp_force_tonne"] > 0 and p["cooling_time_s"] > 0
