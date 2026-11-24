"""The example plugin is loaded in a subprocess so it cannot change this process's command registry."""
import json
import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "openswx-gears"

# Runs in a subprocess. The dist-info stands in for `pip install`: importlib.metadata reads the entry point
# from it, and openswx.system.plugins loads the module exactly as it would for an installed plugin.
SCRIPT = textwrap.dedent("""
    import json
    import math
    import sys
    from pathlib import Path

    repo, example, tmp = (Path(p) for p in sys.argv[1:4])
    info = tmp / "openswx_gears-0.1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\\nName: openswx-gears\\nVersion: 0.1.0\\n", encoding="utf-8")
    (info / "entry_points.txt").write_text("[openswx.plugins]\\ngears = openswx_gears\\n", encoding="utf-8")
    sys.path[:0] = [str(repo), str(tmp), str(example)]

    from openswx import session as S
    from openswx.system import plugins
    from openswx.server import mcp_server

    loaded = {p["name"]: p for p in plugins.loaded}
    gears = [p for p in plugins.loaded if p["name"] == "gears"]
    result = S.call("spur_gear", {"module": 2.0, "teeth": 12, "width": 8.0, "bore": 10.0})

    # Independent bounds: the blank lies between the root cylinder and the outside cylinder, less the bore.
    module, teeth, width, bore = 2.0, 12, 8.0, 10.0
    pitch_r = module * teeth / 2
    outer_r = pitch_r + module
    root_r = pitch_r - 1.25 * module
    lower = math.pi * (root_r ** 2 - (bore / 2) ** 2) * width
    upper = math.pi * (outer_r ** 2 - (bore / 2) ** 2) * width
    box = S.APP.doc.body.bounding_box()
    rejected = None
    try:
        S.call("spur_gear", {"module": 2.0, "teeth": 3, "width": 8.0})
    except S.CommandError as exc:
        rejected = str(exc)

    print(json.dumps({
        "loaded": loaded,
        "gears": {"ok": bool(gears) and all(p["ok"] for p in gears),
                  "commands": sorted({c for p in gears for c in p["commands"]})},
        "commands": mcp_server.toolset_commands("plugins"),
        "result": result,
        "lower": lower,
        "upper": upper,
        "bbox": [list(box.min), list(box.max)],
        "rejected": rejected,
    }))
""")


def test_example_declares_the_plugin_entry_point():
    cfg = tomllib.loads((EXAMPLE / "pyproject.toml").read_text(encoding="utf-8"))
    assert cfg["project"]["entry-points"]["openswx.plugins"] == {"gears": "openswx_gears"}
    assert (EXAMPLE / "openswx_gears" / "__init__.py").is_file()


def test_example_plugin_loads_and_adds_a_gear_command(tmp_path):
    script = tmp_path / "load_example_plugin.py"
    script.write_text(SCRIPT, encoding="utf-8")
    env = {**os.environ, "OPENSWX_PLUGINS": "on", "OPENSWX_ISOLATE": "off", "OPENSWX_AUTOSAVE": "off",
           "OPENSWX_LOG": "off"}
    r = subprocess.run([sys.executable, str(script), str(ROOT), str(EXAMPLE), str(tmp_path / "meta")],
                       capture_output=True, text=True, timeout=300, env=env, cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])

    assert out["gears"]["ok"] is True
    assert out["gears"]["commands"] == ["spur_gear"]
    assert "spur_gear" in out["commands"]

    result = out["result"]
    assert result["teeth"] == 12 and result["pitch_diameter"] == 24.0
    assert out["lower"] < result["volume_mm3"] < out["upper"]
    xmin, ymin, zmin = out["bbox"][0]
    xmax, ymax, zmax = out["bbox"][1]
    assert abs((ymax - ymin) - 8.0) < 1e-6  # face width along the Top plane's normal
    assert max(abs(xmin), abs(xmax), abs(zmin), abs(zmax)) <= 14.0 * 1.01  # outside radius, spline overshoot
    assert out["rejected"] and "teeth" in out["rejected"]
