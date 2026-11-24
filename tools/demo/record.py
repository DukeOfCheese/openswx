"""Record the README demo (docs/media/demo.mp4, 4K): an MCP client builds a mounting plate in a running openswx while
a chat panel illustrates the conversation. Every feature is made by a real MCP tool call; the chat text is scripted.
Then the part goes to the cloud and a second person, in a second openswx, joins it: their pointer, a draft of
their fillet and the fillet itself arrive live, and the History pane shows their change and a named version.

    pip install -e .[collab] playwright openswx-collab-server && playwright install chromium   (plus ffmpeg on PATH)
    python tools/demo/record.py OUT_DIR    -> OUT_DIR/demo.mp4 (3840x2160, under 10 MB), demo-1080p.mp4 and poster.png

The collaboration part runs a throwaway openswx-collab server (SQLite, in a temporary folder) with two made-up
accounts, Alex (the recorded window) and Sam (a second openswx with its own home and token). The server runs on
this Python, or on OPENSWX_COLLAB_SERVER_PYTHON if it is installed in another environment.

Frames come from the browser's screencast (1920x1080 CSS pixels at device scale 2), timestamped, then encoded
at a constant 30 fps by ffmpeg.

Uses the GPU when the browser can (headless Chromium with ANGLE). An optional claude-logo.svg next to this file,
if you have the right to use it, replaces the spinner glyph in the chat header.
"""

import asyncio
import base64
import json
import math
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
CAP = 9_500_000  # bytes: the 4K demo.mp4 stays under GitHub's 10 MB for an attached video
# the collaboration part: made-up people on a throwaway server
TOKEN_SAM = "demo-recording-token-sam-0123456789abcdefghijk"
PASSWORD = "demo password"  # (both accounts live only in the temporary server)
ALEX = {"email": "alex@example.com", "password": PASSWORD, "name": "Alex"}
SAM = {"email": "sam@example.com", "password": PASSWORD, "name": "Sam"}
SHARE = "Share it with Sam in Brackets."


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(url: str, tries: int = 240) -> None:
    for _ in range(tries):
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not come up")


def post(url: str, body: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json",
                                                                  **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read() or b"null")


def command(base: str, token: str, name: str, /, **args):
    """A command in an openswx, as its UI sends it (POST /api/command)."""
    r = post(base + "/api/command", {"name": name, "args": args}, token)
    if not r.get("ok"):
        raise RuntimeError(f"{name}: {r.get('error')}")
    return r["result"]


def openswx(home_prefix: str, token: str) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = dict(os.environ, OPENSWX_HOME=tempfile.mkdtemp(prefix=home_prefix), OPENSWX_TOKEN=token, OPENSWX_AUTOSAVE="off",
               OPENSWX_LOG="off", OPENSWX_PLUGINS="off", OPENSWX_TOOLSETS="all", OPENSWX_SHOW_HOME="0")
    proc = subprocess.Popen([sys.executable, "-m", "openswx", "serve", "--port", str(port)], env=env, cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, f"http://127.0.0.1:{port}"


def collab_server() -> tuple[subprocess.Popen, str]:
    """A throwaway openswx-collab server: SQLite and file storage in a temporary folder."""
    work = Path(tempfile.mkdtemp(prefix="oswx-demo-collab-"))
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = dict(os.environ, OSWX_COLLAB_DATABASE_URL=f"sqlite+aiosqlite:///{(work / 'collab.db').as_posix()}",
               OSWX_COLLAB_STORAGE_URL=str(work / "storage"), OSWX_COLLAB_PUBLIC_URL=url)
    env.pop("PYTHONPATH", None)
    py = os.environ.get("OPENSWX_COLLAB_SERVER_PYTHON") or sys.executable
    proc = subprocess.Popen([py, "-m", "openswx_collab_server", "serve", "--port", str(port)], env=env, cwd=work,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, url


def team(server: str, alex: tuple[str, str], sam: tuple[str, str]) -> None:
    """Alex (the first account: the server's admin) makes an organisation and the Brackets project and invites
    Sam as an editor; both openswx instances sign in."""
    a = post(server + "/api/auth/register", ALEX)["token"]
    org = post(server + "/api/orgs", {"name": "Demo Works"}, a)["id"]
    command(*alex, "collab_sign_in", server=server, email=ALEX["email"], password=PASSWORD)
    project = command(*alex, "collab_create_project", name="Brackets")
    inv = post(server + f"/api/orgs/{org}/invites", {"email": SAM["email"], "projects": [
        {"project_id": project["id"], "role": "editor"}]}, a)["token"]
    post(server + "/api/auth/register", {**SAM, "invite": inv})
    command(*sam, "collab_sign_in", server=server, email=SAM["email"], password=PASSWORD)


def glide(points: list[list[float]], n: int) -> list[list[float]]:
    """n points along a polyline, eased in and out, evenly spaced by length."""
    seg = [math.dist(p, q) for p, q in zip(points, points[1:])]
    total, out = sum(seg), []
    for i in range(n):
        t = i / (n - 1)
        d = total * (t * t * (3 - 2 * t))
        for p, q, L in zip(points, points[1:], seg):
            if d <= L or q is points[-1]:
                k = min(d / L, 1.0) if L else 1.0
                out.append([p[j] + (q[j] - p[j]) * k for j in range(3)])
                break
            d -= L
    return out


async def main():
    procs = []
    try:
        proc, base = openswx("oswx-demo-", TOKEN)
        procs.append(proc)
        sam_proc, sam_base = openswx("oswx-demo-sam-", TOKEN_SAM)
        procs.append(sam_proc)
        server_proc, server = collab_server()
        procs.append(server_proc)
        for u in (base, sam_base, server):
            wait_up(u + "/api/health")
        alex, sam = (base, TOKEN), (sam_base, TOKEN_SAM)
        team(server, alex, sam)

        async def sam_do(name, /, **args):  # Sam's openswx, off camera
            return await asyncio.to_thread(command, *sam, name, **args)
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
                    await page.evaluate("""(t) => { CHAT.say(t); DEMO.orbitSpeed = 24; DEMO.cc('A parametric part you can keep editing.'); }""",
                                        f"Done. <b>MountingPlate</b>: 6061 aluminium, <b>{mp['mass']:.0f} g</b>, 9 features, all of them editable in the design tree.")
                    await page.wait_for_timeout(2000)

                    # ---------------------------------------------------- working together: Sam joins
                    await page.evaluate("DEMO.orbitSpeed = 6; DEMO.cc('Share it, and work on it together, live.'); CHAT.focus()")
                    for i in range(0, len(SHARE) + 1, 3):
                        await page.evaluate("(t) => CHAT.type(t)", SHARE[:i])
                        await page.wait_for_timeout(16)
                    await page.evaluate("(t) => CHAT.type(t)", SHARE)
                    await page.wait_for_timeout(250)
                    await page.evaluate("(t) => { CHAT.send(t); CHAT.think('Moving it to the cloud...'); }", SHARE)
                    await page.wait_for_timeout(350)
                    shared = await call("collab_enable", project="Brackets", pause=250, show="project = Brackets")
                    await page.evaluate("(t) => CHAT.say(t)", "Shared: <b>MountingPlate</b> is now a cloud part in "
                                        "<b>Brackets</b>. Sam can open it from Home › Cloud.")
                    await page.wait_for_timeout(500)
            await sam_do("collab_open", document_id=shared["document_id"])  # Sam's avatar comes up in the bar
            await page.wait_for_timeout(700)
            await page.evaluate("DEMO.cc('Sam joins: their pointer and their edits arrive as they make them.')")
            topo = await sam_do("get_topology")
            rim = max((e for e in topo["edges"] if e["type"] == "circle" and abs(e.get("radius", 0) - 18) < 1e-6),
                      key=lambda e: e["point"][1])
            rim = rim.get("name") or rim["id"]
            # Sam's pointer: across the plate towards the camera, up the boss and onto its rim (on the side in view)
            cam = await page.evaluate("window.app.vp.camera.position.toArray()")
            th = math.atan2(cam[2], cam[0])
            at = lambda r, a, y: [r * math.cos(th + a), y, r * math.sin(th + a)]  # noqa: E731
            start = [40 * math.cos(th + 0.9), 10.0, 22 * math.sin(th + 0.9)]  # (on the plate, clear of the holes)
            path = glide([start, at(30, 0.35, 10), at(19, 0.18, 10), at(18.05, 0.1, 22), at(18, 0.0, 35)], 46)
            for i, pt in enumerate(path):
                await sam_do("collab_presence", pointer={"point": [round(v, 3) for v in pt]},
                             tool="Fillet" if i > len(path) // 2 else "Select")
                await page.wait_for_timeout(35)
            # Sam picks the rim and opens Fillet: the draft shows here, translucent in Sam's colour (each presence
            # message is Sam's whole presence: the pointer goes with it every time)
            on_rim = {"point": [round(v, 3) for v in path[-1]]}
            await sam_do("collab_presence", pointer=on_rim, selection=[rim], tool="Fillet")
            await page.wait_for_timeout(500)
            poster_at = time.time()
            await sam_do("collab_presence", pointer=on_rim, selection=[rim], tool="Fillet",
                         draft={"kind": "feature", "command": "fillet", "args": {"radius": 3, "edges": [rim]}})
            await page.wait_for_timeout(1400)
            await sam_do("fillet", radius=3, edges=[rim])
            await sam_do("collab_presence", pointer=on_rim, selection=[], tool="Select", clear_draft=True)
            await page.wait_for_timeout(700)
            await sam_do("collab_presence", pointer={"point": [round(v, 3) for v in at(28, 0.5, 10)]}, tool="Select")
            await page.wait_for_timeout(800)
            await sam_do("collab_name_version", name="Rev A", note="Boss edge filleted")
            await page.evaluate("DEMO.cc('Feature locks, undo of your own edits, history and named versions.'); window.app.collabHistoryDialog()")
            await page.wait_for_timeout(2600)
            build_to = time.time()
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
            poster_s = video_s = 0.0
            with open(lst, "w", encoding="utf-8") as fh:
                for (t, f), (t2, _) in zip(frames, frames[1:] + [(frames[-1][0] + 1 / FPS, None)]):
                    d = t2 - t
                    if build_from <= t <= build_to:  # a stall while a new model loads: one frame, not a freeze
                        d = min(d, 1.5 / FPS)  # (the orbit pauses through a stall, so the motion stays continuous)
                    if t <= poster_at:
                        poster_s = video_s
                    video_s += max(d, 0.001)
                    fh.write(f"file '{f.as_posix()}'\nduration {max(d, 0.001):.4f}\n")
                fh.write(f"file '{frames[-1][1].as_posix()}'\n")
            print(f"{len(frames)} frames over {frames[-1][0] - frames[0][0]:.1f} s; video {video_s:.1f} s, poster at {poster_s:.1f} s")
            gaps = sorted(((t2 - t, t - frames[0][0]) for (t, _), (t2, _) in zip(frames, frames[1:])), reverse=True)
            print("frame gaps over 50 ms:", sum(1 for g, _ in gaps if g > 0.05),
                  "| longest:", ", ".join(f"{g * 1000:.0f} ms at {at:.1f} s" for g, at in gaps[:8]))
            master = OUT / "demo-master.mp4"
            enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "slow", "-movflags", "+faststart"]
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf",
                            f"fps={FPS},scale={2 * W}:{2 * H}:flags=lanczos", *enc, "-crf", "18", "-tune", "animation",
                            str(master)], check=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(master), "-vf", "scale=1920:1080:flags=lanczos",
                            *enc, "-crf", "23", str(OUT / "demo-1080p.mp4")], check=True)
            # GitHub takes videos up to 10 MB in comments and release notes: the 4K file is fitted under that
            if master.stat().st_size <= CAP:
                master.replace(OUT / "demo.mp4")
            else:
                kbps = int(CAP * 8 / 1000 / video_s * 0.97)
                two = ["ffmpeg", "-v", "error", "-y", "-i", str(master), "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kbps}k",
                       "-pix_fmt", "yuv420p", "-tune", "animation", "-passlogfile", str(OUT / "x264pass")]
                subprocess.run([*two, "-pass", "1", "-an", "-f", "mp4", os.devnull], check=True)
                subprocess.run([*two, "-pass", "2", "-movflags", "+faststart", str(OUT / "demo.mp4")], check=True)
                master.unlink()
                for f in OUT.glob("x264pass*"):
                    f.unlink()
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{poster_s:.2f}", "-i", str(OUT / "demo.mp4"), "-frames:v", "1",
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
        for proc in procs:
            proc.kill()


asyncio.run(main())
