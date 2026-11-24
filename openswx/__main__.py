from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="openswx")
    sub = ap.add_subparsers(dest="cmd")
    d = sub.add_parser("desktop", help="run openswx as a desktop application (default)")
    d.add_argument("file", nargs="?", help="document to open")
    d.add_argument("--port", type=int, default=8765)
    s = sub.add_parser("serve", help="run the web UI (and MCP over HTTP at /mcp)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--open", action="store_true", help="open the browser")
    s.add_argument("--file", help="part to open on start (.oswprt/.step)")
    s.add_argument("--no-token", action="store_true", help="do not ask for the token (this computer only)")
    s.add_argument("--share", action="store_true", help="also print a view-only link: whoever opens it sees the model live but cannot change it")
    s.add_argument("--workspace", help="folder agents (MCP) may read and write files in; elsewhere is refused")
    s.add_argument("--toolsets", help="MCP tools listed at first: core (default), all, or e.g. core,assembly,drawing")
    s.add_argument("--allow-remote", action="store_true", help="allow --host to be an address other computers can reach")
    sub.add_parser("token", help="print the token for the web UI link and for agents (MCP over HTTP)")
    m = sub.add_parser("mcp", help="run the MCP server over stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8765)
    m.add_argument("--no-ui", action="store_true", help="do not start the web UI alongside")
    m.add_argument("--standalone", action="store_true",
                   help="always serve MCP from this process (by default it relays to an openswx already running on --port)")
    m.add_argument("--workspace", help="folder agents (MCP) may read and write files in; elsewhere is refused")
    m.add_argument("--toolsets", help="MCP tools listed at first: core (default), all, or e.g. core,assembly,drawing")
    i = sub.add_parser("sldinfo", help="inspect a SOLIDWORKS file container")
    i.add_argument("file")
    i.add_argument("--extract", help="directory to extract streams and Parasolid data into")
    r = sub.add_parser("register-file-types", help="open .oswprt/.oswasm in openswx and add it to Open with (this user)")
    r.add_argument("--exe", help="program to register (default: openswx.exe next to this program)")
    sub.add_parser("unregister-file-types", help="remove the file associations register-file-types added")
    from openswx.shell import cli  # (headless one-shot tools: convert, render, massprops, check, drawing, script, diff)
    cli.add_parsers(sub)
    argv = [x for x in (sys.argv[1:] if argv is None else argv) if not x.startswith("-psn_")]  # (macOS Finder launch)
    if not argv or (argv[0] not in ("desktop", "serve", "mcp", "token", "sldinfo", "register-file-types", "unregister-file-types", "-h", "--help", *cli.COMMANDS)):
        argv = ["desktop", *argv]  # double-clicking openswx.exe (or opening a file with it) starts the app
    a = ap.parse_args(argv)
    if getattr(a, "workspace", None):
        os.environ["OPENSWX_WORKSPACE"] = str(Path(a.workspace).resolve())
    if getattr(a, "toolsets", None):
        os.environ["OPENSWX_TOOLSETS"] = a.toolsets

    if a.cmd in cli.COMMANDS:
        return cli.run(a)

    if a.cmd == "desktop":
        from openswx.shell.desktop import run as run_desktop
        return run_desktop(a.port, a.file)

    if a.cmd == "serve":
        from openswx.server import security
        remote = not security.is_loopback(a.host)
        if remote and not a.allow_remote:
            print(f"openswx: --host {a.host} lets other computers reach openswx; add --allow-remote if that is intended",
                  file=sys.stderr)
            return 2
        if remote and a.no_token:
            print("openswx: --no-token is only for this computer (not with --allow-remote)", file=sys.stderr)
            return 2
        token = None if a.no_token else security.user_token()
        from openswx import session as S
        from openswx.server.web_server import run
        if a.file:
            S.open_part(a.file)
        url = f"http://{a.host}:{a.port}/"
        link = url + (f"?token={token}" if token else "")
        if a.open:
            threading.Timer(1.0, lambda: webbrowser.open(link)).start()
        print(f"openswx UI: {link}\nMCP (streamable HTTP): {url}mcp"
              + ("   header  Authorization: Bearer <token>  (the token: `openswx token`)" if token else ""), file=sys.stderr)
        view = security.view_token() if a.share and token else None
        if a.share and not token:
            print("openswx: --share needs the token (not with --no-token)", file=sys.stderr)
            return 2
        if view:
            print(f"view-only link: {url}?view={view}" + ("" if remote else "   (this computer only; for others add --host and --allow-remote)"),
                  file=sys.stderr)
        run(a.host, a.port, token=token, view_token=view)
        return 0

    if a.cmd == "token":
        from openswx.server import security
        print(security.user_token())
        return 0

    if a.cmd == "mcp":
        from openswx.server import mcp_bridge
        running = None if a.standalone or a.workspace or a.toolsets else mcp_bridge.find_running(a.port)
        if running:  # an openswx window is open: the agent works in it (its edits show there, live)
            from openswx.server import security
            print(f"openswx: relaying MCP to the running openswx at {running}", file=sys.stderr)
            mcp_bridge.run(running + "/mcp", security.user_token())
            return 0
        from openswx.server.mcp_server import build_mcp
        if not a.no_ui:
            from openswx.server import security
            if not security.is_loopback(a.host):
                print("openswx: the live UI of `mcp` is for this computer only (use `serve --allow-remote`)", file=sys.stderr)
                return 2
            token = security.user_token()

            def ui():
                try:
                    from openswx.server.web_server import run
                    run(a.host, a.port, mount_mcp=False, log_level="error", token=token)
                except (OSError, SystemExit) as exc:  # port busy: agents still work headless
                    print(f"openswx: web UI not started ({exc})", file=sys.stderr)
            threading.Thread(target=ui, daemon=True).start()
            print(f"openswx: live UI at http://{a.host}:{a.port}/?token={token}", file=sys.stderr)
        build_mcp().run("stdio")
        return 0

    if a.cmd == "sldinfo":
        from openswx.exchange.sldprt import extract, read_info
        info = read_info(a.file)
        if a.extract:
            info["extracted"] = extract(a.file, a.extract)
        print(json.dumps(info, indent=1))
        return 0
    if a.cmd in ("register-file-types", "unregister-file-types"):
        from openswx.system import fileassoc
        if a.cmd == "unregister-file-types":
            print(json.dumps({"removed": fileassoc.unregister()}, indent=1))
            return 0
        exe = a.exe
        if not exe and getattr(sys, "frozen", False):  # the windowed app beside openswx-cli.exe (or its launcher)
            launcher = os.environ.get("OPENSWX_LAUNCHER_DIR")
            gui = Path(launcher) / "openswx.exe" if launcher else Path(sys.executable).with_name("openswx.exe")
            exe = str(gui if gui.exists() else Path(sys.executable))
        if not exe:
            print("pass --exe (the program files should open with) when running from source", file=sys.stderr)
            return 2
        print(json.dumps({"registered": fileassoc.register(exe)}, indent=1))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
