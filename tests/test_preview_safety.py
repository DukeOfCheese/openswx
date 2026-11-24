import pytest

from openswx import session as S

# Every command the UI's PropertyManager previews (propman.js SPECS, more.js MORE_SPECS, the sketch panels and
# Edit Sketch Plane in app.js, Insert Components' placing): each must still preview.
UI_PREVIEWS = """split_body indent intersect cut_with_surface delete_face move_face replace_face projected_curve split_line
composite_curve curve_through_points rib hole_wizard thread dome structural_member base_flange edge_flange
vertex_chamfer face_fillet full_round_fillet wrap hem jog corner_relief forming_tool_dimple forming_tool_louver
forming_tool_lance forming_tool_extruded_flange forming_tool_rib miter_flange sketched_bend revolved_surface
swept_surface boundary_surface lofted_surface planar_surface filled_surface offset_surface knit_surface trim_surface
end_cap gusset extruded_surface thicken sweep cut_sweep loft cut_loft draft add_mate extrude cut_extrude revolve
cut_revolve fillet variable_fillet chamfer shell hole linear_pattern circular_pattern sketch_pattern curve_pattern
table_pattern fill_pattern mirror reference_plane reference_axis helix reference_point coordinate_system flex deform
freeform fillet_profile parting_line shut_off_surfaces parting_surface tooling_split cosmetic_thread ruled_surface
radiate_surface extend_surface untrim_surface flatten_surface filled_surface_edges connection_point mate_reference
redefine_mate edit_sketch_plane sketch_offset sketch_mirror sketch_linear_pattern sketch_circular_pattern
sketch_convert_entities sketch_transform redefine_feature edit_feature""".split()


def _block():
    S.call("new_part", {"name": "Previewed"})
    sk = S.call("create_sketch", {"plane": "Top"})["sketch"]
    S.call("sketch_rectangle", {"sketch": sk, "corner1": [0, 0], "corner2": [40, 20]})
    return sk, S.call("extrude", {"sketch": sk, "depth": 10})["feature"]


@pytest.fixture
def evil(tmp_path):
    marker = tmp_path / "ran.txt"
    macro = tmp_path / "evil.py"
    macro.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
    return macro, marker, tmp_path / "written.oswprt"


def test_a_macro_or_a_save_cannot_run_as_a_preview(evil):
    macro, marker, out = evil
    _block()
    for name, args in (("run_macro", {"path": str(macro)}), ("save_part", {"path": str(out)}),
                       ("batch", {"commands": [{"name": "run_macro", "args": {"path": str(macro)}}]}),
                       ("collab_sign_out", {}), ("close_document", {}), ("export_file", {"path": str(out)})):
        got = S.preview(name, args)
        assert got["ok"] is False and got.get("refused"), (name, got)
    assert not marker.exists() and not out.exists()


def test_nor_through_redefine_feature(evil):
    macro, marker, out = evil
    _, ext = _block()
    for inner, args in (("run_macro", {"path": str(macro)}), ("save_part", {"path": str(out)}),
                        ("insert_part", {"path": str(out)})):
        got = S.preview("redefine_feature", {"name": ext, "command_name": inner, "args": args})
        assert got["ok"] is False and got.get("refused"), (inner, got)
        if inner != "insert_part":  # (a real Edit Feature of a linked part may name its file; a preview may not)
            with pytest.raises(S.CommandError, match="cannot redefine a feature"):
                S.call("redefine_feature", {"name": ext, "command_name": inner, "args": args})
    assert not marker.exists() and not out.exists()


def test_feature_previews_still_work():
    sk, ext = _block()
    version = S.APP.version
    assert S.preview("edit_feature", {"name": ext, "params": {"depth": 30}})["ok"]
    assert S.preview("redefine_feature", {"name": ext, "command_name": "extrude", "args": {"sketch": sk, "depth": 25}})["ok"]
    assert S.preview("fillet", {"edges": ["Edge0"], "radius": 1})["ok"]
    assert S.APP.version == version and S.APP.doc.find(ext)["params"]["depth"] == 10


def test_every_panel_the_ui_previews_is_allowed():
    S.call("new_part", {"name": "Panels"})
    refused = {n: S.preview_refusal(n, {}) for n in UI_PREVIEWS if S.preview_refusal(n, {})}
    assert refused == {"redefine_feature": "None cannot be previewed (only commands that make or change a feature can)"}
    assert S.preview_refusal("insert_component", {"source": "Panels"}) is None  # (an open document)
    assert S.preview_refusal("insert_component", {"source": "C:/somewhere/part.oswprt"})  # (a file: no)
    allowed = [n for n in S.COMMANDS if S.preview_refusal(n, {"command_name": "extrude", "source": "Panels"}) is None]
    assert not [n for n in allowed if n in S.FILE_ARGS and n != "insert_component" or n.startswith("collab_")
                or not S.COMMANDS[n]["mutates"]]


def test_a_preview_can_give_way_to_a_running_command():
    _, ext = _block()
    import threading
    held, done = threading.Event(), threading.Event()

    def hold():
        with S.APP.lock:
            held.set()
            done.wait(5)
    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        assert S.preview("edit_feature", {"name": ext, "params": {"depth": 30}}, wait=False) == {
            "ok": False, "busy": True, "error": "openswx is busy with another command"}
    finally:
        done.set()
        t.join()
