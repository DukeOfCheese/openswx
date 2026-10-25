"""openswx command line.

  openswx [FILE]                               desktop application (native window; default)
  python -m openswx serve [--port 8765]        web UI + REST + MCP (streamable HTTP at /mcp)
  python -m openswx mcp [--port 8765] [--no-ui] MCP over stdio for agents, plus the live web UI
  python -m openswx sldinfo FILE.SLDPRT [--extract DIR]
  openswx-cli register-file-types [--exe EXE]  .oswprt/.oswasm open in openswx; Open with for CAD files
  openswx-cli unregister-file-types           remove those file associations again
"""
from __future__ import annotations

import argparse
import json
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
    m = sub.add_parser("mcp", help="run the MCP server over stdio")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8765)
    m.add_argument("--no-ui", action="store_true", help="do not start the web UI alongside")
    i = sub.add_parser("sldinfo", help="inspect a SOLIDWORKS file container")
    i.add_argument("file")
    i.add_argument("--extract", help="directory to extract streams and Parasolid data into")
    r = sub.add_parser("register-file-types", help="open .oswprt/.oswasm in openswx and add it to Open with (this user)")
    r.add_argument("--exe", help="program to register (default: openswx.exe next to this program)")
    sub.add_parser("unregister-file-types", help="remove the file associations register-file-types added")
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in ("desktop", "serve", "mcp", "sldinfo", "register-file-types", "unregister-file-types", "-h", "--help")):
        argv = ["desktop", *argv]  # double-clicking openswx.exe (or opening a file with it) starts the app
    a = ap.parse_args(argv)

    if a.cmd == "desktop":
        from .desktop import run as run_desktop
        return run_desktop(a.port, a.file)

    if a.cmd == "serve":
        from . import session as S
        from .web_server import run
        if a.file:
            S.open_part(a.file)
        if a.open:
            threading.Timer(1.0, lambda: webbrowser.open(f"http://{a.host}:{a.port}/")).start()
        print(f"openswx UI: http://{a.host}:{a.port}/   MCP (streamable HTTP): http://{a.host}:{a.port}/mcp", file=sys.stderr)
        run(a.host, a.port)
        return 0

    if a.cmd == "mcp":
        from .mcp_server import build_mcp
        if not a.no_ui:
            def ui():
                try:
                    from .web_server import run
                    run(a.host, a.port, mount_mcp=False, log_level="error")
                except (OSError, SystemExit) as exc:  # port busy: agents still work headless
                    print(f"openswx: web UI not started ({exc})", file=sys.stderr)
            threading.Thread(target=ui, daemon=True).start()
            print(f"openswx: live UI at http://{a.host}:{a.port}/", file=sys.stderr)
        build_mcp().run("stdio")
        return 0

    if a.cmd == "sldinfo":
        from .sldprt import extract, read_info
        info = read_info(a.file)
        if a.extract:
            info["extracted"] = extract(a.file, a.extract)
        print(json.dumps(info, indent=1))
        return 0
    if a.cmd in ("register-file-types", "unregister-file-types"):
        from . import fileassoc
        if a.cmd == "unregister-file-types":
            print(json.dumps({"removed": fileassoc.unregister()}, indent=1))
            return 0
        exe = a.exe
        if not exe and getattr(sys, "frozen", False):  # the windowed app beside openswx-cli.exe
            gui = Path(sys.executable).with_name("openswx.exe")
            exe = str(gui if gui.exists() else Path(sys.executable))
        if not exe:
            print("pass --exe (the program files should open with) when running from source", file=sys.stderr)
            return 2
        print(json.dumps({"registered": fileassoc.register(exe)}, indent=1))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
