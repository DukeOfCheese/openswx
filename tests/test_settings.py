"""User settings (openswx/system/settings.py) and what reads them."""
import json

import pytest

from openswx.server import mcp_server
from openswx.system import recovery, settings
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


def test_view_mouse_and_keyboard_settings(home):
    r = S.set_settings({"view_body_color": "#AA2200", "mouse_preset": "onshape", "mouse_zoom_reverse": True,
                        "shortcuts": {"zoom_fit": "Shift+F", "shortcut_bar": ""}})["settings"]
    assert (r["view_body_color"], r["mouse_preset"], r["mouse_zoom_reverse"]) == ("#aa2200", "onshape", True)
    assert r["shortcuts"] == {"zoom_fit": "Shift+F", "shortcut_bar": ""}
    for bad in ({"view_body_color": "red"}, {"view_body_color": "#12345"}, {"mouse_preset": "trackball"},
                {"shortcuts": ["Ctrl+S"]}, {"shortcuts": {"save": "Shift+Ctrl+S"}}, {"shortcuts": {"save": "s"}},
                {"shortcuts": {"Save": "Ctrl+S"}}, {"shortcuts": {"save": 5}}):
        with pytest.raises(S.CommandError):
            S.set_settings(bad)
    assert S.set_settings(reset=True)["settings"]["shortcuts"] == {}


def test_web_ui_tables_match_the_settings():
    """keys.js's default keys are in the format settings.py accepts, and viewport.js has every mouse preset."""
    import re
    from pathlib import Path
    web = Path(settings.__file__).parent.parent / "web"
    keys_js = (web / "js" / "keys.js").read_text(encoding="utf-8")
    rows = re.findall(r'\["([a-z0-9_]+)", "[^"]+", "([^"]*)", "[^"]+"\]', keys_js)
    assert len(rows) >= 30 and len({a for a, _ in rows}) == len(rows)
    for action, combo in rows:
        assert settings._check("shortcuts", {action: combo}) == {action: combo}
    assert len({c for _, c in rows}) == len(rows), "two actions share default keys"
    vp = (web / "js" / "viewport.js").read_text(encoding="utf-8")
    block = vp[vp.index("export const MOUSE = {"):vp.index("};", vp.index("export const MOUSE = {"))]
    assert set(re.findall(r"^\s+(\w+): \(b, e\)", block, re.M)) == set(settings.MOUSE_PRESETS)
