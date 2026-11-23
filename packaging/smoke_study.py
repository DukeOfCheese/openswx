"""Smoke test of a built openswx: start its command-line program as a server, build a bar through the REST
API and run a static study, checking the deflection against beam theory and, unless --voxel-ok, that the
study meshed with tetrahedra (the bundled Netgen mesher works in the frozen app).

    python packaging/smoke_study.py dist/openswx/openswx-cli.exe          (Windows)
    python packaging/smoke_study.py /Applications/openswx.app/Contents/MacOS/openswx-cli
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

TOKEN = "smoke-study-token-0123456789abcdefghijklmnopqrstuv"


def main() -> int:
    exe = os.path.abspath(sys.argv[1])
    voxel_ok = "--voxel-ok" in sys.argv
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, OPENSWX_HOME=tempfile.mkdtemp(), OPENSWX_TOKEN=TOKEN, OPENSWX_AUTOSAVE="off",
               OPENSWX_PLUGINS="off")
    proc = subprocess.Popen([exe, "serve", "--port", str(port)], env=env)
    base = f"http://127.0.0.1:{port}"

    def call(name, args=None):
        req = urllib.request.Request(base + "/api/command", data=json.dumps({"name": name, "args": args or {}}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=600))
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{name}: {exc.read().decode()[:400]}") from exc
        if not r.get("ok"):
            raise SystemExit(f"{name}: {r.get('error')}")
        return r["result"]
    try:
        for _ in range(240):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1).read()
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise SystemExit("the server did not start")
        call("new_part", {"name": "Bar"})
        call("create_sketch", {"plane": "Top"})
        call("sketch_rectangle", {"sketch": "Sketch1", "corner1": [0, 0], "corner2": [100, 10]})
        call("extrude", {"sketch": "Sketch1", "depth": 10})
        call("set_material", {"name": "Plain Carbon Steel"})
        faces = call("get_topology")["faces"]
        root = min(faces, key=lambda f: f["point"][0])["id"]
        tip = max(faces, key=lambda f: f["point"][0])["id"]
        call("simulation_add_fixture", {"face": root})
        call("simulation_add_force", {"face": tip, "force": [0, -100, 0]})
        r = call("simulation_run")
        expect = 100 * 100 ** 3 / (3 * 210000 * 10 ** 4 / 12)  # beam theory, E 210 GPa
        print(f"mesh: {r.get('mesh')}; max displacement {r['max_displacement']:.4f} mm (beam theory {expect:.4f})")
        if not voxel_ok and not str(r.get("mesh", "")).startswith("tetrahedral"):
            raise SystemExit("the study did not use the tetrahedral mesher")
        if abs(r["max_displacement"] - expect) > 0.05 * expect:
            raise SystemExit("the deflection is off")
        return 0
    finally:
        proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
