"""User settings (openswx/settings.py) and what reads them."""
import json

import pytest

from openswx import mcp_server, recovery, settings
from openswx import session as S


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSWX_HOME", str(tmp_path))
    return tmp_path


def test_defaults_update_and_reset(home):
    assert S.get_settings()["settings"]["units"] == "mm"
    r = S.set_settings({"units": "in", "autosave_seconds": 120})["settings"]
    assert (r["units"], r["autosave_seconds"]) == ("in", 120)
    assert json.loads((home / "settings.json").read_text())["units"] == "in"
    for bad in ({"units": "furlong"}, {"autosave_seconds": -5}, {"confirm_close": "yes"}, {"nope": 1}, {"image_width": True}):
        with pytest.raises(S.CommandError):
            S.set_settings(bad)
    with pytest.raises(S.CommandError):
        S.set_settings({"units": "cm", "autosave_seconds": 99999})  # one bad value: nothing saved
    assert settings.get("units") == "in"
    assert S.set_settings(reset=True)["settings"]["units"] == "mm" and not (home / "settings.json").exists()
    # a damaged file or value falls back to the defaults; keys of a newer version are kept
    (home / "settings.json").write_text(json.dumps({"units": 7, "future_key": [1]}))
    assert settings.get("units") == "mm"
    S.set_settings({"units": "cm"})
    assert json.loads((home / "settings.json").read_text()) == {"units": "cm", "future_key": [1]}
    (home / "settings.json").write_text("{not json")
    assert settings.all_settings()["units"] == "mm"


def test_settings_are_used(monkeypatch):
    S.set_settings({"units": "in", "autosave_seconds": 30, "mcp_toolsets": "core,sheet_metal"})
    S.new_part("Inch")
    assert S.get_model()["units"] == "in"
    monkeypatch.delenv("OPENSWX_AUTOSAVE", raising=False)
    assert recovery.interval() == 30
    monkeypatch.setenv("OPENSWX_AUTOSAVE", "off")  # the environment wins
    assert recovery.interval() == 0
    monkeypatch.delenv("OPENSWX_TOOLSETS", raising=False)
    assert mcp_server.starting_toolsets() == ["core", "sheet_metal"]
    monkeypatch.setenv("OPENSWX_TOOLSETS", "all")
    assert mcp_server.starting_toolsets() == ["all"]
    monkeypatch.delenv("OPENSWX_TOOLSETS")
    S.set_settings({"mcp_toolsets": "nonsense"})
    assert mcp_server.starting_toolsets() == ["core"]  # a mistyped setting does not stop agents connecting
