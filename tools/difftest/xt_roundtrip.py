"""Check the Parasolid writer against SOLIDWORKS: write test solids with openswx.parasolid_write, import each
.x_t into a licensed SOLIDWORKS through its public COM API, and compare SOLIDWORKS' mass properties.

    python tools/difftest/xt_roundtrip.py OUT_DIR
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def cases():
    from build123d import Axis, Box, Circle, Cone, Cylinder, Plane, Pos, Rectangle, Sphere, Torus, fillet, loft
    b = Box(100, 60, 20)
    out = {
        "box": b,
        "holed": b - Cylinder(10, 40),
        "cylinder": Cylinder(10, 30),
        "sphere": Sphere(15),
        "torus": Torus(30, 10),
        "cone": Cone(20, 8, 30),
        "filleted": fillet(Box(100, 60, 20).edges().filter_by(Axis.Z), 5),
        "cavity": Box(40, 40, 40) - Box(10, 10, 10),
        "two_lumps": Box(10, 10, 10) + Pos(30, 0, 0) * Box(10, 10, 10),
    }
    import difftest  # the difftest parts, built with openswx's own feature commands
    osw = difftest.OpenSwx()
    for case in difftest.CASES:
        osw.run(case)
        out["openswx_" + case["name"]] = osw.S.APP.doc.body
    try:  # a loft between a circle and a square gives B-spline faces
        out["loft"] = loft([Plane.XY * Circle(15), Plane.XY.offset(30) * Rectangle(20, 20)])
    except Exception:
        pass
    return out


def main():
    import difftest
    from openswx.features import mass_properties
    from openswx.parasolid_write import write_xt
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "xt_roundtrip")
    out.mkdir(parents=True, exist_ok=True)
    sw = difftest.SolidWorks()
    import pythoncom
    import win32com.client as w
    fails = 0
    for name, shape in cases().items():
        path = out / f"{name}.x_t"
        write_xt(shape, str(path))
        mine = mass_properties(shape)
        err = w.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        m = sw.app.LoadFile4(str(path), "r", sw._nothing(), err)
        if m is None:
            print(f"FAIL {name}: SOLIDWORKS could not import the file (error {err.value})")
            fails += 1
            continue
        try:
            mp = difftest.call0(m.Extension, "CreateMassProperty")
            vol, area = mp.Volume * 1e9, mp.SurfaceArea * 1e6
            ok = abs(vol - mine["volume"]) / mine["volume"] < 1e-5 and abs(area - mine["surface_area"]) / mine["surface_area"] < 1e-5
            print(f"{'PASS' if ok else 'FAIL'} {name}: SOLIDWORKS {vol:.4f} mm3 / {area:.4f} mm2, "
                  f"openswx {mine['volume']:.4f} / {mine['surface_area']:.4f}")
            fails += not ok
        finally:
            sw.app.CloseDoc(difftest.call0(m, "GetTitle"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
