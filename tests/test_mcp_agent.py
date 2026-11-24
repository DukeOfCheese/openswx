"""What agents see: toolsets, tool annotations, persistent names, batch, resources."""
import asyncio
import json
import math

import pytest

from openswx.server import mcp_server as M
from openswx import session as S


def run(coro):
    return asyncio.run(coro)


def test_default_connection_lists_core_and_can_grow():
    server = M.build_mcp()

    async def go():
        tools = {t.name: t for t in await server.list_tools()}
        assert len(tools) < 50  # few enough for any client; the full set is over 350
        assert {"create_sketch", "extrude", "fillet", "get_topology", "batch", "list_toolsets", "enable_toolset",
                "get_view_image"} <= set(tools)
        assert "base_flange" not in tools and "add_mate" not in tools
        # annotations come from the registry
        assert tools["get_topology"].annotations.read_only_hint is True
        assert tools["extrude"].annotations.read_only_hint is False
        assert tools["delete_feature"].annotations.destructive_hint is True
        assert tools["extrude"].annotations.destructive_hint is False
        r = await server.call_tool("list_toolsets", {})
        sets = json.loads(r.content[0].text)["toolsets"]
        assert sets["core"]["enabled"] and not sets["sheet_metal"]["enabled"] and sets["assembly"]["tools"] > 40
        return len(tools)
    n = run(go())

    server = M.build_mcp("core,sheet_metal")
    names = {t.name for t in run(server.list_tools())}
    assert "base_flange" in names and len(names) > n
    everything = {t.name for t in run(M.build_mcp("all").list_tools())}
    assert len(everything) > 350 and {n for n, m in S.COMMANDS.items() if m["mcp"]} <= everything
    with pytest.raises(ValueError, match="unknown toolset"):
        M.build_mcp("core,nonsense")


def test_every_mcp_command_is_in_a_toolset_and_core_exists():
    assert all(n in S.COMMANDS for n in M.CORE)
    covered = set(M.toolset_commands("core"))
    for ts in M.TOOLSETS:
        covered |= set(M.toolset_commands(ts))
    covered |= set(M.ALWAYS)
    missing = {n for n, m in S.COMMANDS.items() if m["mcp"]} - covered
    assert not missing, f"commands no toolset reaches (add their category to TOOLSETS): {sorted(missing)}"


def test_persistent_names_survive_an_edit_that_renumbers_faces():
    S.new_part("Named")
    sk = S.create_sketch("Top")["sketch"]
    S.sketch_rectangle(sk, [0, 0], [40, 20])
    S.extrude(sk, 10)
    topo = S.get_topology()
    assert all("name" in f for f in topo["faces"]) and all("name" in e for e in topo["edges"])
    top = next(f for f in topo["faces"] if f["normal"][1] > 0.99)  # the cap away from the sketch plane
    edge = next(e for e in topo["edges"] if top["name"] in e["name"].split("|"))
    v0 = S.get_mass_properties()["volume"]
    # an earlier feature changes the body's face order: a boss sketched on the top face, picked by name
    s2 = S.create_sketch(top["name"])["sketch"]
    S.sketch_circle(s2, [10, -10], 3)
    S.extrude(s2, 5)
    v1 = S.get_mass_properties()["volume"]
    assert abs(v1 - v0 - math.pi * 9 * 5) < 1e-6
    S.fillet(2.0, [edge["name"]])  # the edge named before the boss existed
    f = S.get_model()["features"][-1]
    assert f.get("status") != "error" and S.get_mass_properties()["volume"] < v1
    with pytest.raises(S.CommandError, match="unknown face"):
        S.create_sketch("NoSuch:face")
    with pytest.raises(S.CommandError, match="unknown edge"):
        S.fillet(1.0, ["a|b"])


def test_batch_is_atomic_and_one_undo_step():
    S.new_part("Batch")
    r = S.batch([{"name": "create_sketch", "args": {"plane": "Top"}},
                 {"name": "sketch_circle", "args": {"sketch": "Sketch1", "center": [0, 0], "radius": 10}},
                 {"name": "extrude", "args": {"sketch": "Sketch1", "depth": 5}}])
    assert r["count"] == 3 and r["results"][0]["sketch"] == "Sketch1"
    v = S.get_mass_properties()["volume"]
    assert abs(v - math.pi * 100 * 5) < 1e-6
    S.undo()  # the whole batch
    assert not S.get_model()["features"]
    S.redo()
    assert abs(S.get_mass_properties()["volume"] - v) < 1e-9

    names = [f["name"] for f in S.get_model()["features"]]
    with pytest.raises(S.CommandError, match=r"command 3 \(extrude\) failed.*nothing was changed"):
        S.batch([{"name": "create_sketch", "args": {"plane": "Front"}},
                 {"name": "sketch_circle", "args": {"sketch": "Sketch2", "center": [0, 0], "radius": 2}},
                 {"name": "extrude", "args": {"sketch": "NoSuchSketch", "depth": 5}}])
    assert [f["name"] for f in S.get_model()["features"]] == names  # the two that worked were taken back
    assert abs(S.get_mass_properties()["volume"] - v) < 1e-9

    with pytest.raises(S.CommandError, match="kept"):
        S.batch([{"name": "create_sketch", "args": {"plane": "Front"}},
                 {"name": "extrude", "args": {"sketch": "NoSuchSketch", "depth": 5}}], atomic=False)
    assert len(S.get_model()["features"]) == len(names) + 1
    with pytest.raises(S.CommandError, match="unknown command"):
        S.batch([{"name": "no_such_command"}])
    with pytest.raises(S.CommandError, match="cannot be used inside"):
        S.batch([{"name": "undo"}])


def test_resources():
    S.new_part("Res")
    S.create_sketch("Top")
    server = M.build_mcp()

    async def go():
        model = await server.read_resource("openswx://model")
        feat = await server.read_resource("openswx://feature/Sketch1")
        cmds = await server.read_resource("openswx://commands")
        return [next(iter(x)).content for x in (model, feat, cmds)]
    model, feat, cmds = (json.loads(x) for x in run(go()))
    assert model["name"] == "Res" and feat["name"] == "Sketch1"
    assert "extrude" in cmds["core"] and "base_flange" in cmds["sheet_metal"]
