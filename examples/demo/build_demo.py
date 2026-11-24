"""Build the demo bracket, pin and assembly in examples/demo/out."""
import os
from pathlib import Path

os.environ.setdefault("OPENSWX_LOG", "off")
os.environ.setdefault("OPENSWX_AUTOSAVE", "off")
os.environ.setdefault("OPENSWX_PLUGINS", "off")

import openswx.session as S


def bracket(path: Path) -> None:
    S.call("new_part", {"name": "Demo Bracket"})
    sketch = S.call("create_sketch", {"plane": "Top"})["sketch"]
    S.call("sketch_rectangle", {"sketch": sketch, "corner1": [-60, -40], "corner2": [60, 40]})
    S.call("extrude", {"sketch": sketch, "depth": 12})

    topo = S.call("get_topology", {})
    corners = [e["id"] for e in topo["edges"]
               if e["type"] == "line" and abs(e["length"] - 12) < 1e-6
               and abs(abs(e["point"][0]) - 60) < 1e-6 and abs(abs(e["point"][2]) - 40) < 1e-6]
    S.call("fillet", {"radius": 12, "edges": corners})

    S.call("reference_plane", {"reference": "Top", "offset": 12})
    boss = S.call("create_sketch", {"plane": "Plane1"})["sketch"]
    S.call("sketch_circle", {"sketch": boss, "center": [0, 0], "radius": 22})
    S.call("extrude", {"sketch": boss, "depth": 18})

    holes = S.call("create_sketch", {"plane": "Top"})["sketch"]
    S.call("sketch_circle", {"sketch": holes, "center": [0, 0], "radius": 10.5})
    for x, z in ((-45, -25), (45, -25), (-45, 25), (45, 25)):
        S.call("sketch_circle", {"sketch": holes, "center": [x, z], "radius": 4.5})
    S.call("cut_extrude", {"sketch": holes, "depth": 100, "end_condition": "ThroughAll", "reverse": True})

    S.call("set_material", {"name": "Plain Carbon Steel"})
    S.call("set_appearance", {"color": "#9fb4cc", "target": "part"})
    S.call("save_part", {"path": str(path)})


def pin(path: Path) -> None:
    S.call("new_part", {"name": "Demo Pin"})
    shaft = S.call("create_sketch", {"plane": "Top"})["sketch"]
    S.call("sketch_circle", {"sketch": shaft, "center": [0, 0], "radius": 10})
    S.call("extrude", {"sketch": shaft, "depth": 55})
    head = S.call("create_sketch", {"plane": "Top"})["sketch"]
    S.call("sketch_circle", {"sketch": head, "center": [0, 0], "radius": 16})
    S.call("extrude", {"sketch": head, "depth": 10})
    S.call("set_material", {"name": "Brass"})
    S.call("set_appearance", {"color": "#c9a227", "target": "part"})
    S.call("save_part", {"path": str(path)})


def assembly(bracket_path: Path, pin_path: Path, path: Path) -> None:
    S.call("new_assembly", {"name": "Demo Assembly"})
    S.call("insert_component", {"source": str(bracket_path), "position": [0, 0, 0]})
    S.call("insert_component", {"source": str(pin_path), "position": [0, 40, 0], "rotation_deg": [180, 0, 0]})
    S.call("save_part", {"path": str(path)})


def main() -> None:
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    bracket_path, pin_path, assembly_path = out / "demo-bracket.oswprt", out / "demo-pin.oswprt", out / "demo-assembly.oswasm"
    bracket(bracket_path)
    pin(pin_path)
    assembly(bracket_path, pin_path, assembly_path)
    for p in (bracket_path, pin_path, assembly_path):
        print(p)


if __name__ == "__main__":
    main()
