"""Differential test: build the same parametric parts in openswx and in SOLIDWORKS, compare results.

    python tools/difftest/difftest.py                 # openswx vs analytic values
    python tools/difftest/difftest.py --solidworks    # also drive a licensed SOLIDWORKS via its public COM API

SOLIDWORKS is driven only through its documented automation interface (SldWorks.Application); the
openswx side uses the same command layer as the MCP server. Results go to docs/difftest_report.md.
Cases are written in a neutral form: faces/edges are selected by a point on them (model mm).
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PI = math.pi
CASES = [
    {"name": "block", "expect": 100 * 60 * 20,
     "ops": [("sketch", "Top"), ("rect", 0, 0, 100, 60), ("extrude", 20, "Blind")]},
    {"name": "block_through_hole", "expect": 100 * 60 * 20 - PI * 100 * 20,
     "ops": [("sketch", "Top"), ("rect", 0, 0, 100, 60), ("extrude", 20, "Blind"),
             ("sketch_face", (10, 20, 10)), ("circle", 0, 0, 10), ("cut", 0, "ThroughAll")]},
    {"name": "midplane_square", "expect": 10 * 10 * 20,
     "ops": [("sketch", "Front"), ("rect", 0, 0, 10, 10), ("extrude", 20, "MidPlane")]},
    {"name": "revolved_tube", "expect": PI * (20 ** 2 - 10 ** 2) * 40,
     "ops": [("sketch", "Front"), ("centerline", 0, -5, 0, 50), ("rect", 15, 20, 10, 40), ("revolve", 360)]},
    {"name": "drafted_cylinder", "expect": None,
     "ops": [("sketch", "Top"), ("circle", 0, 0, 20), ("extrude", 30, "Blind", 5)]},
    {"name": "filleted_block", "expect": 100 * 60 * 20 - 4 * (25 - 25 * PI / 4) * 20,
     "ops": [("sketch", "Top"), ("rect", 0, 0, 100, 60), ("extrude", 20, "Blind"),
             ("fillet", 5, [(50, 10, 30), (50, 10, -30), (-50, 10, 30), (-50, 10, -30)])]},
    {"name": "blind_pocket", "expect": 100 * 60 * 20 - 40 * 20 * 8,
     "ops": [("sketch", "Top"), ("rect", 0, 0, 100, 60), ("extrude", 20, "Blind"),
             ("sketch_face", (30, 20, 20)), ("rect", 0, 0, 40, 20), ("cut", 8, "Blind")]},
    {"name": "chamfered_block", "expect": 100 * 60 * 20 - 2 * 0.5 * 4 * 4 * 100,
     "ops": [("sketch", "Top"), ("rect", 0, 0, 100, 60), ("extrude", 20, "Blind"),
             ("chamfer", 4, [(0, 20, 30), (0, 20, -30)])]},
    {"name": "vertex_chamfer", "expect": 100 * 60 * 20 - 6 ** 3 / 6,
     "ops": [("sketch", "Top"), ("rect", 0, 0, 100, 60), ("extrude", 20, "Blind"), ("vertex_chamfer", 6, (50, 20, 30))]},
    {"name": "patterned_holes", "expect": 100 * 60 * 20 - 2 * PI * 25 * 20,
     "ops": [("sketch", "Top"), ("rect", 0, 0, 100, 60), ("extrude", 20, "Blind"),
             ("sketch_face", (10, 20, 10)), ("circle", 0, 0, 5), ("cut", 0, "ThroughAll"), ("pattern", 20, 2, (0, 20, 30))]},
    {"name": "shelled_box", "expect": 100 * 60 * 20 - 96 * 56 * 18,
     "ops": [("sketch", "Top"), ("rect", 0, 0, 100, 60), ("extrude", 20, "Blind"), ("shell", 2, (10, 20, 10))]},
    {"name": "torus", "expect": 2 * PI ** 2 * 30 * 10 ** 2,
     "ops": [("sketch", "Front"), ("centerline", 0, -30, 0, 30), ("circle", 30, 0, 10), ("revolve", 360)]},
    {"name": "sphere", "expect": 4 / 3 * PI * 20 ** 3,
     "ops": [("sketch", "Front"), ("centerline", 0, -30, 0, 30), ("arc", 0, 0, 0, -20, 0, 20), ("line", 0, 20, 0, -20),
             ("revolve", 360)]},
]


# ---------------------------------------------------------------------------- openswx backend
class OpenSwx:
    name = "openswx"

    def __init__(self):
        from openswx import session as S
        self.S = S

    def run(self, case):
        S = self.S
        S.new_part(case["name"])
        sk = None
        for op in case["ops"]:
            k = op[0]
            if k == "sketch":
                sk = S.create_sketch(op[1])["sketch"]
            elif k == "sketch_face":
                sk = S.create_sketch(self._face_at(op[1]))["sketch"]
            elif k == "rect":
                _, cx, cy, w, h = op
                S.sketch_rectangle(sk, [cx, cy], [cx + w / 2, cy + h / 2], centered=True)
            elif k == "circle":
                S.sketch_circle(sk, [op[1], op[2]], op[3])
            elif k == "centerline":
                S.sketch_centerline(sk, [op[1], op[2]], [op[3], op[4]])
            elif k == "line":
                S.sketch_line(sk, [op[1], op[2]], [op[3], op[4]])
            elif k == "arc":  # centre, start, end (counter-clockwise)
                S.sketch_arc(sk, [op[1], op[2]], [op[3], op[4]], [op[5], op[6]])
            elif k == "spline":
                S.sketch_spline(sk, [list(q) for q in op[1]])
            elif k == "extrude":
                S.extrude(sk, depth=op[1], end_condition=op[2], draft=op[3] if len(op) > 3 else 0)
            elif k == "cut":
                S.cut_extrude(sk, depth=op[1] or 10, end_condition=op[2])
            elif k == "revolve":
                S.revolve(sk, angle=op[1])
            elif k == "fillet":
                S.fillet(op[1], edges=[self._edge_at(p) for p in op[2]])
            elif k == "fillet_ray":  # ("fillet_ray", r, [(point_on_edge, ray_direction)])
                S.fillet(op[1], edges=[self._edge_at(pt) for pt, _ in op[2]])
            elif k == "chamfer":
                S.chamfer(op[1], edges=[self._edge_at(p) for p in op[2]], type="equal_distance")
            elif k == "vertex_chamfer":
                S.vertex_chamfer(list(op[2]), op[1])
            elif k == "shell":
                S.shell(op[1], faces_to_remove=[self._face_at(op[2])])
            elif k == "pattern":
                last = [f["name"] for f in S.get_model()["features"] if f["type"] != "sketch"][-1]
                S.linear_pattern([last], self._edge_at(op[3]), spacing=op[1], count=op[2])
        mp = S.get_mass_properties()
        return {"volume": mp["volume"], "area": mp["surface_area"], "com": mp["center_of_mass"]}

    def _face_at(self, p):
        best = min(self.S.get_topology()["faces"], key=lambda f: sum((a - b) ** 2 for a, b in zip(f["point"], p))
                   if abs(f["normal"][1]) > 0.99 else 1e18)
        return best["id"]

    def _edge_at(self, p):
        return min(self.S.get_topology()["edges"], key=lambda e: sum((a - b) ** 2 for a, b in zip(e["point"], p)))["id"]


# ---------------------------------------------------------------------------- SOLIDWORKS backend (COM)
class SolidWorks:
    """Uses only the public SOLIDWORKS API (same calls a VBA macro would make). Units: metres."""
    name = "SOLIDWORKS"
    END = {"Blind": 0, "ThroughAll": 1, "MidPlane": 6}  # swEndConditions_e

    def __init__(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        self.VT = (pythoncom, win32com.client)
        self.app = win32com.client.Dispatch("SldWorks.Application")
        self.app.Visible = True
        self.template = self.app.GetUserPreferenceStringValue(8)  # swDefaultTemplatePart
        self.app.SetUserPreferenceToggle(10, False)  # swInputDimValOnCreate: no Modify box for new dimensions

    def _nothing(self):
        pythoncom, w = self.VT
        return w.VARIANT(pythoncom.VT_DISPATCH, None)

    def run(self, case):
        m = self.app.NewDocument(self.template, 0, 0, 0)
        if m is None:
            raise RuntimeError("SOLIDWORKS could not create a part from the default template")
        ext, skm, fm = m.Extension, m.SketchManager, m.FeatureManager
        mm = 0.001
        sel = lambda name, typ, x=0, y=0, z=0, append=False, mark=0: ext.SelectByID2(name, typ, x * mm, y * mm, z * mm, append, mark, self._nothing(), 0)  # noqa: E731
        for op in case["ops"]:
            k = op[0]
            if k == "sketch":
                m.ClearSelection2(True)
                sel(f"{op[1]} Plane", "PLANE")
                skm.InsertSketch(True)
            elif k == "sketch_face":
                m.ClearSelection2(True)
                sel("", "FACE", *op[1])
                skm.InsertSketch(True)
            elif k == "rect":
                _, cx, cy, w, h = op
                skm.CreateCenterRectangle(cx * mm, cy * mm, 0, (cx + w / 2) * mm, (cy + h / 2) * mm, 0)
            elif k == "circle":
                skm.CreateCircleByRadius(op[1] * mm, op[2] * mm, 0, op[3] * mm)
            elif k == "centerline":
                skm.CreateCenterLine(op[1] * mm, op[2] * mm, 0, op[3] * mm, op[4] * mm, 0)
            elif k == "line":
                skm.CreateLine(op[1] * mm, op[2] * mm, 0, op[3] * mm, op[4] * mm, 0)
            elif k == "arc":
                skm.CreateArc(op[1] * mm, op[2] * mm, 0, op[3] * mm, op[4] * mm, 0, op[5] * mm, op[6] * mm, 0, 1)
            elif k == "ellipse":  # centre, major-axis end, minor-axis end
                skm.CreateEllipse(op[1] * mm, op[2] * mm, 0, op[3] * mm, op[4] * mm, 0, op[5] * mm, op[6] * mm, 0)
            elif k == "addtodb":  # True: sketch entities go in as given (no snapping, no automatic relations)
                skm.AddToDB = bool(op[1])
            elif k == "rel":  # add a relation (swConstraintType string, e.g. "sgHORIZONTAL2D") to the segments at points
                m.ClearSelection2(True)
                for i, (x, y) in enumerate(op[2]):
                    sel("", "SKETCHSEGMENT", x, y, 0, i > 0)
                m.SketchAddConstraints(op[1])
                m.ClearSelection2(True)
            elif k == "dim":  # dimension the sketch segment at (x, y) (Front-plane sketches: model = sketch), text at (tx, ty)
                m.ClearSelection2(True)
                sel("", "SKETCHSEGMENT", op[1], op[2], 0)
                m.AddDimension2(op[3] * mm, op[4] * mm, 0)
                m.ClearSelection2(True)
            elif k == "spline":
                pythoncom, w = self.VT
                pts = [c * mm for q in op[1] for c in (q[0], q[1], 0.0)]
                skm.CreateSpline2(w.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, pts), True)
            elif k == "extrude":
                draft = op[3] if len(op) > 3 else 0
                rev = bool(op[4]) if len(op) > 4 else False  # (Sd, Flip, Dir: reverse the direction, ...)
                fm.FeatureExtrusion3(True, False, rev, self.END[op[2]], 0, op[1] * mm, 0, bool(draft), False, False, False,
                                     math.radians(draft), 0, False, False, False, False, True, True, True, 0, 0, False)
            elif k == "cut":
                fm.FeatureCut4(True, False, False, self.END[op[2]], 0, (op[1] or 10) * mm, 0, False, False, False, False, 0, 0,
                               False, False, False, False, False, True, True, True, True, False, 0, 0, False, False)
            elif k == "revolve":
                fm.FeatureRevolve2(True, True, False, False, False, False, 0, 0, math.radians(op[1]), 0, False, False, 0, 0,
                                   0, 0, 0, True, True, True)
            elif k == "fillet":
                m.ClearSelection2(True)
                for i, p in enumerate(op[2]):
                    # SelectByID2 only finds entities visible in the current view; cast a ray at the edge
                    # from just outside the part instead (works for hidden edges too)
                    d = [p[0], 0.0, p[2]]
                    n = math.hypot(d[0], d[2]) or 1.0
                    d = [d[0] / n, 0.0, d[2] / n]
                    start = [(p[j] + 5 * d[j]) * mm for j in range(3)]
                    ext.SelectByRay(*start, -d[0], -d[1], -d[2], 0.5 * mm, 1, i > 0, 1, 0)  # swSelEDGES
                fm.FeatureFillet3(195, op[1] * mm, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
            elif k == "fillet_ray":  # select each edge with a ray that ends on it
                m.ClearSelection2(True)
                for i, (pt, d) in enumerate(op[2]):
                    start = [(pt[j] - 5 * d[j]) * mm for j in range(3)]
                    ext.SelectByRay(*start, *d, 0.5 * mm, 1, i > 0, 1, 0)  # swSelEDGES
                fm.FeatureFillet3(195, op[1] * mm, 0, 0, 0, 0, 0, None, None, None, None, None, None, None)
            elif k == "chamfer":
                m.ClearSelection2(True)
                for i, p in enumerate(op[2]):
                    d = [0.0, 1.0, p[2] / (abs(p[2]) or 1.0)]
                    n = math.hypot(*d)
                    d = [c / n for c in d]
                    start = [(p[j] + 5 * d[j]) * mm for j in range(3)]
                    ext.SelectByRay(*start, -d[0], -d[1], -d[2], 0.5 * mm, 1, i > 0, 1, 0)  # swSelEDGES
                fm.InsertFeatureChamfer(4, 1, op[1] * mm, math.radians(45), 0, 0, 0, 0)  # swChamferAngleDistance = 1
            elif k == "vertex_chamfer":
                m.ClearSelection2(True)
                p = op[2]
                d = [1 / math.sqrt(3)] * 3
                d = [d[0] * (1 if p[0] > 0 else -1), d[1], d[2] * (1 if p[2] > 0 else -1)]
                start = [(p[j] + 5 * d[j]) * mm for j in range(3)]
                ext.SelectByRay(*start, -d[0], -d[1], -d[2], 0.5 * mm, 3, False, 1, 0)  # swSelVERTICES
                fm.InsertFeatureChamfer(4, 3, 0, 0, 0, op[1] * mm, op[1] * mm, op[1] * mm)  # swChamferVertex = 3
            elif k == "pattern":
                m.ClearSelection2(True)
                p = op[3]
                d = [0.0, 1 / math.sqrt(2), (1 if p[2] > 0 else -1) / math.sqrt(2)]
                ext.SelectByRay(*[(p[j] + 5 * d[j]) * mm for j in range(3)], -d[0], -d[1], -d[2], 0.5 * mm, 1, False, 1, 0)
                m.FeatureByPositionReverse(0).Select2(True, 4)  # the seed feature (last in the tree)
                fm.FeatureLinearPattern5(op[2], op[1] * mm, 1, 0, False, False, "NULL", "NULL", False, False, False, False,
                                         False, False, False, False, 0, 0, False, False, False, False)
            elif k == "shell":
                m.ClearSelection2(True)
                p = op[2]
                ext.SelectByRay(p[0] * mm, (p[1] + 5) * mm, p[2] * mm, 0, -1, 0, 0.5 * mm, 2, False, 1, 0)  # swSelFACES
                m.InsertFeatureShell(op[1] * mm, False)
            m.ClearSelection2(True)
        call0(m, "EditRebuild3")
        mp = call0(ext, "CreateMassProperty")
        out = {"volume": mp.Volume * 1e9, "area": mp.SurfaceArea * 1e6, "com": [c * 1e3 for c in mp.CenterOfMass]}
        if getattr(self, "before_close", None):  # e.g. export the part (tools/difftest/xt_samples.py)
            self.before_close(m, case)
        self.app.CloseDoc(call0(m, "GetTitle"))
        return out


def call0(obj, name):
    """Late-bound COM exposes zero-argument methods as property reads; handle both forms."""
    import inspect
    v = getattr(obj, name)
    return v() if inspect.ismethod(v) else v  # COM objects are callable too, so test for a bound method


# ---------------------------------------------------------------------------- report
def rel(a, b):
    return abs(a - b) / max(abs(b), 1e-12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solidworks", action="store_true", help="also run the cases in SOLIDWORKS via COM")
    ap.add_argument("--report", default=str(ROOT / "docs" / "difftest_report.md"))
    a = ap.parse_args()
    backends = [OpenSwx()]
    sw_error = None
    if a.solidworks:
        try:
            t0 = time.time()
            backends.append(SolidWorks())
            print(f"SOLIDWORKS connected in {time.time() - t0:.1f}s")
        except Exception as exc:  # no licence / not installed
            sw_error = str(exc)
            print("SOLIDWORKS unavailable:", exc)
    rows, fails = [], 0
    for case in CASES:
        res = {}
        for b in backends:
            try:
                res[b.name] = b.run(case)
            except Exception as exc:
                res[b.name] = {"error": str(exc)}
        o = res["openswx"]
        line = {"case": case["name"], "openswx": o, "sw": res.get("SOLIDWORKS"), "expect": case["expect"]}
        ok = "error" not in o
        if ok and case["expect"] is not None:
            ok = rel(o["volume"], case["expect"]) < 1e-6
        if ok and line["sw"]:
            sw = line["sw"]
            ok = "error" not in sw and rel(o["volume"], sw["volume"]) < 1e-4 and rel(o["area"], sw["area"]) < 1e-3
        line["ok"] = ok
        fails += not ok
        rows.append(line)
        print(("PASS " if ok else "FAIL ") + case["name"], {k: v for k, v in line.items() if k in ("openswx", "sw")})

    md = ["# Differential test report", "",
          f"Backends: {', '.join(b.name for b in backends)}" + (f" (SOLIDWORKS unavailable: {sw_error})" if sw_error else ""),
          f"Generated: {time.strftime('%Y-%m-%d %H:%M')}", "",
          "| Case | openswx volume (mm³) | expected | SOLIDWORKS volume | openswx area (mm²) | SOLIDWORKS area | result |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        o, sw = r["openswx"], r["sw"] or {}
        fmt = lambda d, k: "error: " + d["error"][:60] if "error" in d else (f"{d[k]:.3f}" if k in d else "—")  # noqa: E731
        md.append(f"| {r['case']} | {fmt(o, 'volume')} | {r['expect']:.3f} | {fmt(sw, 'volume') if sw else '—'} | "
                  f"{fmt(o, 'area')} | {fmt(sw, 'area') if sw else '—'} | {'PASS' if r['ok'] else 'FAIL'} |"
                  if r["expect"] is not None else
                  f"| {r['case']} | {fmt(o, 'volume')} | — | {fmt(sw, 'volume') if sw else '—'} | {fmt(o, 'area')} | "
                  f"{fmt(sw, 'area') if sw else '—'} | {'PASS' if r['ok'] else 'FAIL'} |")
    Path(a.report).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"report: {a.report}; {len(rows) - fails}/{len(rows)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
