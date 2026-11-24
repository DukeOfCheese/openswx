import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
TOKEN = "demo-recording-token-0123456789abcdefghijklmnop"
PROMPT = ("Make a 120×60×10 mm mounting plate with four Ø8 holes 12 mm from the edges and a Ø36 boss "
          "with a counterbored hole. Fillet the corners R6. Material 6061.")
W, H = 1920, 1080  # CSS pixels; captured at 2x = 3840x2160
FPS = 30


async def main():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    home = tempfile.mkdtemp(prefix="oswx-demo-")
    env = dict(os.environ, OPENSWX_HOME=home, OPENSWX_TOKEN=TOKEN, OPENSWX_AUTOSAVE="off", OPENSWX_LOG="off",
               OPENSWX_PLUGINS="off", OPENSWX_TOOLSETS="all", OPENSWX_SHOW_HOME="0")
    proc = subprocess.Popen([sys.executable, "-m", "openswx", "serve", "--port", str(port)], env=env, cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(240):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1).read()
                break
            except OSError:
                time.sleep(0.5)
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist"])
            ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=2)
            page = await ctx.new_page()
            frames_dir = OUT / "frames"
            frames_dir.mkdir(exist_ok=True)
            frames: list[tuple[float, Path]] = []
            cdp = await ctx.new_cdp_session(page)

            def on_frame(ev):
                f = frames_dir / f"{len(frames):05d}.jpg"
                f.write_bytes(base64.b64decode(ev["data"]))
                frames.append((ev["metadata"]["timestamp"], f))
                asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]}))
            cdp.on("Page.screencastFrame", on_frame)
            await page.goto(f"{base}/?token={TOKEN}")
            await page.wait_for_function("window.app && window.app.version >= 0")
            here = Path(__file__).parent
            await page.add_script_tag(content=(here / "overlays.js").read_text(encoding="utf-8"))
            await page.add_script_tag(content=(here / "chat.js").read_text(encoding="utf-8"))
            await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 2 * W, "maxHeight": 2 * H})
            logo = here / "claude-logo.svg"
            logo_url = ("data:image/svg+xml;base64," + base64.b64encode(logo.read_bytes()).decode()) if logo.exists() else ""
            # behind a first title card: lay out the stage and start the model view, so nothing heavy happens on screen
            await page.evaluate("(u) => { DEMO.css(); CHAT.css(); if (u) CHAT.logo(u); DEMO.card(u); CHAT.stage(); window.app.vp.togglePlanes(); }", logo_url)
            await page.wait_for_timeout(600)
            await page.evaluate("window.app.vp.resize(); window.app.vp.zoomFit(false); DEMO.orbit(9)")
            await page.wait_for_timeout(900)
            await page.evaluate("(u) => DEMO.card(u)", logo_url)  # again: its entrance animation from the start
            mark = time.time()
            await page.evaluate("DEMO.cc('Open-source parametric CAD, with an MCP server built in.')")
            await page.wait_for_timeout(2300)
            await page.evaluate("DEMO.lockup(); DEMO.cc('Now Claude can design parts in it.')")
            await page.wait_for_timeout(2100)
            await page.evaluate("DEMO.cardOut(); CHAT.showChat(); DEMO.cc('Describe the part.')")  # the chat alone, centred
            await page.wait_for_timeout(900)
            await page.evaluate("CHAT.focus()")
            for i in range(0, len(PROMPT) + 1, 3):
                await page.evaluate("(t) => CHAT.type(t)", PROMPT[:i])
                await page.wait_for_timeout(16)
            await page.evaluate("(t) => CHAT.type(t)", PROMPT)
            send = await page.evaluate("(() => { const r = document.querySelector('#chat .send').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()")
            await page.evaluate("([x, y]) => { DEMO.cursor(x + 260, y + 160, false); }", send)
            await page.wait_for_timeout(60)
            await page.evaluate("([x, y]) => DEMO.cursor(x - 4, y - 4)", send)
            await page.wait_for_timeout(1050)
            await page.evaluate("(t) => { DEMO.click(); CHAT.sent(); CHAT.send(t); }", PROMPT)
            await page.wait_for_timeout(150)
            await page.evaluate("DEMO.cursor(1500, 1000, false); DEMO.cc('Claude builds it in openswx, step by step, over MCP.')")
            await page.wait_for_timeout(80)
            build_from = time.time()  # from here to the end of the build, page stalls are cut (see below)
            await page.evaluate("CHAT.split()")  # side by side: the chat glides left, openswx floats in
            await page.wait_for_timeout(650)
            await page.evaluate("CHAT.think('Planning the part...')")
            await page.wait_for_timeout(700)

            async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=300) as http,                     streamable_http_client(base + "/mcp", http_client=http) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()

                    async def call(_tool, pause=450, fit=False, show=None, think=None, **args):
                        if think:
                            await page.evaluate("(t) => CHAT.thinkText(t)", think)
                        if show is not False:
                            label = show or ", ".join(f"{k}={v}" for k, v in args.items() if not isinstance(v, (list, dict)))
                            await page.evaluate("([n, a]) => CHAT.chip(n, a)", [_tool, label])
                            await page.wait_for_timeout(400)  # the pulse reaches the model view
                        r = await s.call_tool(_tool, args)
                        if getattr(r, "is_error", None) or getattr(r, "isError", None):
                            raise RuntimeError(f"{_tool}: {r.content[0].text}")
                        await page.evaluate("CHAT.done()")
                        if fit:
                            await page.wait_for_function("window.app.vp.bbox && !window.app.vp.bbox.isEmpty()")
                            await page.wait_for_timeout(350)
                            await page.evaluate("CHAT.fit(0.86)")
                        await page.wait_for_timeout(pause)
                        c = r.content[0]
                        return json.loads(c.text) if getattr(c, "type", "") == "text" else c

                    await call("new_part", name="MountingPlate", pause=150, think="Sketching the base plate...")
                    sk = (await call("create_sketch", plane="Top", pause=100))["sketch"]
                    rect = (await call("sketch_rectangle", sketch=sk, corner1=[0, 0], corner2=[80, 50], centered=True, pause=250, show="centered rectangle"))["result"]
                    lines = rect["entities"]
                    await call("sketch_dimension", sketch=sk, kind="length", refs=[lines[0]], value=120, pause=150, show="width = 120 mm")
                    await call("sketch_dimension", sketch=sk, kind="length", refs=[lines[1]], value=60, pause=150, show="height = 60 mm")
                    await call("extrude", sketch=sk, depth=10, pause=750, fit=True, show="depth = 10 mm")
                    topo = await call("get_topology", pause=0, show=False, think="Adding the boss...")
                    top = max((f for f in topo["faces"] if f["normal"][1] > 0.99), key=lambda f: f["point"][1])
                    boss = (await call("create_sketch", plane=top["id"], pause=100, show="on the top face"))["sketch"]
                    await call("sketch_circle", sketch=boss, center=[0, 0], radius=18, pause=150, show="Ø36")
                    await call("extrude", sketch=boss, depth=25, pause=750, fit=True, show="depth = 25 mm")
                    topo = await call("get_topology", pause=0, show=False, think="Drilling the holes...")
                    cap = max((f for f in topo["faces"] if f["normal"][1] > 0.99), key=lambda f: f["point"][1])
                    await call("hole", face=cap["id"], points=[[0, 0]], diameter=16, end_condition="ThroughAll",
                               type="counterbore", cbore_diameter=26, cbore_depth=6, pause=600, show="counterbore Ø16 / Ø26")
                    topo = await call("get_topology", pause=0, show=False)
                    plate_top = [f for f in topo["faces"] if f["normal"][1] > 0.99 and abs(f["point"][1] - 10) < 1e-6][0]
                    await call("hole", face=plate_top["id"], points=[[-48, -18]], diameter=8, end_condition="ThroughAll", pause=500, show="Ø8 through all")
                    await call("linear_pattern", features=["Hole2"], direction="X", spacing=96, count=2,
                               direction2="Z", spacing2=36, count2=2, reverse2=True, pause=600, show="2 × 2, 96 × 36 mm")
                    topo = await call("get_topology", pause=0, show=False, think="Rounding the corners...")
                    await page.evaluate("DEMO.cc('Every feature is real, and editable afterwards.')")
                    vertical = [e["id"] for e in topo["edges"] if e["type"] == "line" and abs(e["length"] - 10) < 1e-6]
                    await call("fillet", radius=6, edges=vertical, pause=700, fit=True, show="R6, 4 edges")
                    await call("set_material", name="6061 Alloy", density_kg_m3=2700, pause=300, show="6061 aluminium", think="Checking the result...")
                    mp = await call("get_mass_properties", pause=200, show="")
            build_to = time.time()
            await page.evaluate("""(t) => { CHAT.say(t); DEMO.orbitSpeed = 40; DEMO.cc('A parametric part you can keep editing.'); }""",
                                f"Done. <b>MountingPlate</b>: 6061 aluminium, <b>{mp['mass']:.0f} g</b>, 9 features, all of them editable in the design tree.")
            await page.wait_for_timeout(3000)
            await page.evaluate("DEMO.cc(''); DEMO.endCard('Free and open source&nbsp;&nbsp;·&nbsp;&nbsp;Windows, macOS, Linux', 'github.com/DukeOfCheese/openswx')")
            await page.wait_for_timeout(4200)
            end_mark = time.time()
            print(f"MARK {end_mark - mark:.2f} mass {mp['mass']:.1f}")
            await cdp.send("Page.stopScreencast")
            await asyncio.sleep(0.5)
            await ctx.close()
            # constant frame rate from the timestamped frames, starting at the title card
            frames = [(t, f) for t, f in frames if t >= mark - 0.05]
            lst = OUT / "frames.txt"
            with open(lst, "w", encoding="utf-8") as fh:
                for (t, f), (t2, _) in zip(frames, frames[1:] + [(frames[-1][0] + 1 / FPS, None)]):
                    d = t2 - t
                    if build_from <= t <= build_to:  # a stall while a new model loads: one frame, not a freeze
                        d = min(d, 1.5 / FPS)  # (the orbit pauses through a stall, so the motion stays continuous)
                    fh.write(f"file '{f.as_posix()}'\nduration {max(d, 0.001):.4f}\n")
                fh.write(f"file '{frames[-1][1].as_posix()}'\n")
            print(f"{len(frames)} frames over {frames[-1][0] - frames[0][0]:.1f} s")
            gaps = sorted(((t2 - t, t - frames[0][0]) for (t, _), (t2, _) in zip(frames, frames[1:])), reverse=True)
            print("frame gaps over 50 ms:", sum(1 for g, _ in gaps if g > 0.05),
                  "| longest:", ", ".join(f"{g * 1000:.0f} ms at {at:.1f} s" for g, at in gaps[:8]))
            enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "slow", "-movflags", "+faststart"]
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf",
                            f"fps={FPS},scale={2 * W}:{2 * H}:flags=lanczos", *enc, "-crf", "18", "-tune", "animation",
                            str(OUT / "demo.mp4")], check=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(OUT / "demo.mp4"), "-vf", "scale=1920:1080:flags=lanczos",
                            *enc, "-crf", "23", str(OUT / "demo-1080p.mp4")], check=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "24", "-i", str(OUT / "demo.mp4"), "-frames:v", "1",
                            "-vf", "scale=1920:1080:flags=lanczos", str(OUT / "poster.png")], check=True)
            for _, f in frames:
                f.unlink(missing_ok=True)
            for f in frames_dir.glob("*.jpg"):
                f.unlink()
            frames_dir.rmdir()
            lst.unlink()
            print("wrote", OUT / "demo.mp4", OUT / "demo-1080p.mp4", OUT / "poster.png")
            await browser.close()
    finally:
        proc.kill()


asyncio.run(main())
