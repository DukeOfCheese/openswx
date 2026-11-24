"""`openswx mcp` relays to an openswx already running on this computer (mcp_bridge): a chat app that only
starts local stdio servers (Claude Desktop) then works in the open window. Real processes on both sides."""
import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from openswx import mcp_bridge

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "bridge-test-token-0123456789abcdefghijklmnopqrstuv"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, OPENSWX_HOME=str(tmp_path_factory.mktemp("home")), OPENSWX_TOKEN=TOKEN,
               OPENSWX_AUTOSAVE="off", OPENSWX_LOG="off", OPENSWX_PLUGINS="off")
    proc = subprocess.Popen([sys.executable, "-m", "openswx", "serve", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(240):
            if mcp_bridge.find_running(port):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("the server did not start")
        yield port, env
    finally:
        proc.kill()
        proc.wait(10)


def test_find_running_recognises_openswx_only():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert mcp_bridge.find_running(free) is None  # nothing listening


def test_sse_parsing():
    lines = ["event: message", 'data: {"jsonrpc": "2.0", "id": 1,', 'data: "result": {}}', "", 'data: {"a": 2}']
    assert list(mcp_bridge._sse_messages(lines)) == [{"jsonrpc": "2.0", "id": 1, "result": {}}, {"a": 2}]


def test_stdio_client_works_in_the_running_openswx(server):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    port, env = server

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "openswx", "mcp", "--port", str(port)],
                                       env=env, cwd=str(ROOT))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                r = await s.call_tool("new_part", {"name": "ViaBridge"})
                await s.call_tool("enable_toolset", {"name": "assembly"})
                more = {t.name for t in (await s.list_tools()).tools}
                return tools, r, more
    tools, r, more = asyncio.run(go())
    assert "extrude" in tools and not getattr(r, "isError", False)
    assert "add_mate" in more  # enable_toolset reached the running server's session
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/command",
                                 data=json.dumps({"name": "list_documents", "args": {}}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
    docs = json.load(urllib.request.urlopen(req, timeout=30))["result"]
    assert "ViaBridge" in json.dumps(docs)  # the part was made in the running openswx, not a separate one


def test_a_failed_relay_answers_the_request():
    b = mcp_bridge.Bridge("http://127.0.0.1:9/mcp", "x", out=io.StringIO())  # (port 9: nothing there)
    b.send({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    reply = json.loads(b.out.getvalue())
    assert reply["id"] == 7 and "openswx relay" in reply["error"]["message"]


def test_agent_connection_info_names_this_installation():
    from openswx import session as S
    info = S.agent_connection_info(port=9100)
    assert info["stdio"]["args"][-3:] == ["mcp", "--port", "9100"] and Path(info["stdio"]["command"]).exists()
    assert info["http"] == "http://127.0.0.1:9100/mcp" and info["claude_desktop_config"].endswith("claude_desktop_config.json")
    # the window shows the token and the full command that prints it (an installed openswx-cli need not be on PATH)
    from openswx import security
    assert info["token"] == security.user_token() and info["token_command"].endswith("token")
    import subprocess, shlex  # noqa: E401
    out = subprocess.run(shlex.split(info["token_command"], posix=False) if "\\" in info["token_command"] else shlex.split(info["token_command"]),
                         capture_output=True, text=True, timeout=120,
                         env={**__import__("os").environ, "OPENSWX_LOG": "off", "OPENSWX_PLUGINS": "off"})
    assert out.stdout.strip() == info["token"]
    # neither an MCP tool nor a query a view-only link may run
    assert S.COMMANDS["agent_connection_info"]["mcp"] is False and S.COMMANDS["agent_connection_info"]["category"] != "query"


def test_agents_are_told_this_is_openswx_not_solidworks():
    """Agents name the program after what the MCP server tells them: the instructions say openswx and that it
    is not SOLIDWORKS, and no tool description compares itself with a SOLIDWORKS tool (file formats and the
    equation syntax may still name it)."""
    import re
    from openswx import mcp_server
    from openswx import session as S
    ins = mcp_server.INSTRUCTIONS
    assert "call it openswx" in ins and "It is not SOLIDWORKS" in ins
    assert "same as SOLIDWORKS" not in ins and "SOLIDWORKS-style" not in ins
    likeness = re.compile(r"\b(like|as in|as)\s+(the\s+)?SOLIDWORKS\b", re.I)
    assert [k for k, m in S.COMMANDS.items() if likeness.search(m["doc"] or "")] == []
    # clients that ignore the instructions still read it on the first tools an agent uses
    import asyncio
    tools = {t.name: t.description for t in asyncio.run(mcp_server.build_mcp().list_tools())}
    for name in mcp_server.NOTED:
        assert "It is not SOLIDWORKS" in tools[name] and "call it openswx" in tools[name], name
        assert not likeness.search(tools[name])
    assert "It is not SOLIDWORKS" not in tools["extrude"]  # (only on those few)
