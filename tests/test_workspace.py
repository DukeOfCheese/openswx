"""The agent workspace (OPENSWX_WORKSPACE / --workspace): agents may only name files inside it."""
import pytest

from openswx import session as S


def test_agent_workspace_confines_file_commands(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("OPENSWX_WORKSPACE", str(ws))
    S.new_part("WsPart")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, 2)
    for name, args in (("save_part", {"path": str(tmp_path / "out.oswprt")}),
                       ("export_file", {"path": str(ws / ".." / "x.step")}),
                       ("open_part", {"path": str(tmp_path / "nope.oswprt")}),
                       ("macro_stop", {"path": str(tmp_path / "m.py")})):
        with pytest.raises(S.CommandError, match="outside the agent workspace"):
            S.call(name, args)  # (origin: agent)
    assert not (tmp_path / "out.oswprt").exists()
    saved = S.call("save_part", {"path": "inside.oswprt"})["saved"]  # relative: taken from the workspace
    assert (ws / "inside.oswprt").exists() and str(ws) in saved
    S.new_assembly("WsAsm")
    assert S.call("insert_component", {"source": "inside"})["component"]  # an open document's name is not a file
    S.call("activate_document", {"name": "inside"})
    S.call("export_file", {"path": str(tmp_path / "user.step")}, "ui")  # the user's own actions are not limited
    assert (tmp_path / "user.step").exists()


def test_no_workspace_means_no_limit(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENSWX_WORKSPACE", raising=False)
    S.new_part("Free")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_circle(sk, [0, 0], 5)
    S.extrude(sk, 2)
    S.call("save_part", {"path": str(tmp_path / "anywhere.oswprt")})
    assert (tmp_path / "anywhere.oswprt").exists()
