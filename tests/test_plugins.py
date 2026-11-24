"""Plugins: installed packages that add commands (openswx/system/plugins.py)."""
import asyncio
import importlib.metadata

from openswx.server import mcp_server
from openswx.system import plugins
from openswx import session as S


class FakeEntryPoint:
    def __init__(self, name, value, load):
        self.name, self.value, self._load = name, value, load

    def load(self):
        return self._load()


def test_plugin_commands_are_registered_and_reach_agents(monkeypatch):
    def good():
        @S.command(category="gears")
        def demo_plugin_block(size: float = 10.0) -> dict:
            """A cube of the given size (a command added by a test plugin)."""
            S.new_part.__wrapped__("PluginBlock")
            sk = S.create_sketch.__wrapped__("Top")["sketch"]
            S.sketch_rectangle.__wrapped__(sk, [0, 0], [size, size])
            S.extrude.__wrapped__(sk, size)
            return {"volume": S.APP.doc.body.volume}

    def bad():
        raise ImportError("no module named openswx_broken")

    monkeypatch.setattr(plugins, "loaded", [])
    monkeypatch.delenv("OPENSWX_PLUGINS", raising=False)
    monkeypatch.setattr(importlib.metadata, "entry_points",
                        lambda group=None: [FakeEntryPoint("gears", "openswx_gears", good), FakeEntryPoint("broken", "openswx_broken", bad)])
    try:
        got = {p["name"]: p for p in S.list_plugins()["plugins"]}
        assert got["gears"]["ok"] and got["gears"]["commands"] == ["demo_plugin_block"]
        assert not got["broken"]["ok"] and "openswx_broken" in got["broken"]["error"]
        assert abs(S.call("demo_plugin_block", {"size": 4})["volume"] - 64) < 1e-9
        assert mcp_server.toolset_commands("plugins") == ["demo_plugin_block"]
        tools = {t.name for t in asyncio.run(mcp_server.build_mcp("core,plugins").list_tools())}
        assert "demo_plugin_block" in tools
        assert "demo_plugin_block" in {t.name for t in asyncio.run(mcp_server.build_mcp("all").list_tools())}
    finally:
        S.COMMANDS.pop("demo_plugin_block", None)


def test_plugins_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(plugins, "loaded", [])
    monkeypatch.setenv("OPENSWX_PLUGINS", "off")
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group=None: 1 / 0)  # (must not even be asked)
    assert plugins.load_all() == []
